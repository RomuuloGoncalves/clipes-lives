"""Synthetic tests for deteccao-por-cota: prosody triage, 2-pass ASR, CheapScore, and quota selection."""

import json
import sys
import types
import wave
from pathlib import Path

import numpy as np

from clipes.candidates import pick_quota_candidates
from clipes.asr import transcribe_candidates, transcribe_fast_blocks
from clipes.models import CandidatePeak
from clipes.models import TopicSegment
from clipes.signals import FPS_RMS, SILENCE_DB, compute_signals, signal_prosody, triage_audio_blocks
from clipes.topics import ScoredSegment, dedup_segments_text, score_topic_segments


def _compressed_speech(n_s: int, seed: int = 0) -> np.ndarray:
    """Conversational speech with a limiter: ±2 dB syllabic jitter around -20 dB plus short pauses."""
    rng = np.random.default_rng(seed)
    db = (-20.0 + rng.uniform(-2.0, 2.0, n_s * FPS_RMS)).astype(np.float32)
    db[:: 7 * FPS_RMS] = -65.0
    return db


def test_prosody_high_on_compressed_speech_zero_on_silence_and_tone():
    n = 120
    speech = signal_prosody(_compressed_speech(n), n)
    assert float(np.median(speech)) >= 0.5

    silence = np.full(n * FPS_RMS, -70.0, dtype=np.float32)
    assert float(signal_prosody(silence, n).max()) == 0.0

    tone = np.full(n * FPS_RMS, -18.0, dtype=np.float32)
    assert float(signal_prosody(tone, n).max()) == 0.0


def test_prosody_is_scale_invariant_to_compression():
    n = 120
    rng = np.random.default_rng(1)
    jitter = rng.uniform(-1.0, 1.0, n * FPS_RMS)
    soft = signal_prosody((-20.0 + 2.0 * jitter).astype(np.float32), n)
    loud = signal_prosody((-20.0 + 8.0 * jitter).astype(np.float32), n)
    assert abs(float(np.median(soft)) - float(np.median(loud))) < 0.1


def _bundle(rms_db: np.ndarray):
    return compute_signals(duration=len(rms_db) / FPS_RMS, rms_db=rms_db)


def test_triage_respects_budget_and_covers_all_thirds():
    n = 3600
    bundle = _bundle(_compressed_speech(n, seed=2))
    blocks = triage_audio_blocks(bundle, budget_ratio=0.30)
    assert blocks
    assert sum(e - s for s, e, _ in blocks) <= 0.30 * n + 1e-6
    thirds = {min(2, int(((s + e) / 2) // (n / 3))) for s, e, _ in blocks}
    assert thirds == {0, 1, 2}
    # No overlapping blocks, so the budget is real audio seconds
    for (s1, e1, _), (s2, _, _) in zip(blocks, blocks[1:]):
        assert s2 >= e1 - 1e-6


def test_triage_drops_silent_and_music_blocks():
    n = 1800
    db = _compressed_speech(n, seed=3)
    # Middle third: steady music bed (flat energy) + silence
    db[600 * FPS_RMS : 900 * FPS_RMS] = -16.0
    db[900 * FPS_RMS : 1200 * FPS_RMS] = SILENCE_DB - 10.0
    bundle = _bundle(db)
    blocks = triage_audio_blocks(bundle, budget_ratio=0.5)
    assert blocks
    for s, e, _ in blocks:
        assert e <= 600.0 + 15.0 or s >= 1200.0 - 15.0, (s, e)


def test_triage_returns_empty_without_speech():
    n = 1200
    rng = np.random.default_rng(4)
    music = (-15.0 + rng.uniform(-0.1, 0.1, n * FPS_RMS)).astype(np.float32)
    assert triage_audio_blocks(_bundle(music)) == []
    silence = np.full(n * FPS_RMS, -80.0, dtype=np.float32)
    assert triage_audio_blocks(_bundle(silence)) == []


def test_triage_leftover_budget_flows_to_other_thirds():
    n = 1800
    db = _compressed_speech(n, seed=5)
    db[600 * FPS_RMS :] = -80.0  # only the first third has speech
    blocks = triage_audio_blocks(_bundle(db), budget_ratio=0.30)
    used = sum(e - s for s, e, _ in blocks)
    assert used > 0.30 * n / 3.0  # more than one third's share
    assert used <= 0.30 * n + 1e-6
    assert all(e <= 600.0 + 15.0 for _, e, _ in blocks)


def _write_wav(path: Path, seconds: float, sr: int = 16000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.zeros(int(seconds * sr), dtype=np.int16).tobytes())


def _fake_whisper(monkeypatch) -> dict:
    """Install a fake faster_whisper recording model loads and transcribe() kwargs."""
    calls: dict = {"loads": 0, "transcribe": []}

    class FakeWord:
        def __init__(self, s, e, w):
            self.start, self.end, self.word = s, e, w

    class FakeSegment:
        def __init__(self, s, e, text):
            self.start, self.end, self.text = s, e, text
            self.words = [FakeWord(s, s + 0.4, " Olha"), FakeWord(s + 0.5, e, " isso!")]

    class FakeInfo:
        language = "pt"
        language_probability = 0.99

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            calls["loads"] += 1

        def transcribe(self, audio_slice, **kwargs):
            calls["transcribe"].append({"n_samples": len(audio_slice), **kwargs})
            return [FakeSegment(1.0, 4.0, " Olha isso!"), FakeSegment(5.0, 9.0, " E aí, cara.")], FakeInfo()

    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)
    return calls


def test_fast_blocks_merges_neighbours_uses_beam1_and_caches(tmp_path: Path, monkeypatch):
    wav = tmp_path / "audio.wav"
    _write_wav(wav, 200.0)
    calls = _fake_whisper(monkeypatch)
    blocks = [(0.0, 30.0, 0.9), (15.0, 45.0, 0.8), (100.0, 130.0, 0.5)]

    segs = transcribe_fast_blocks(wav, blocks, tmp_path, model_size="small", cpu_threads=2)

    assert calls["loads"] == 1
    assert len(calls["transcribe"]) == 2  # (0-45) merged + (100-130)
    assert [c["n_samples"] for c in calls["transcribe"]] == [45 * 16000, 30 * 16000]
    for c in calls["transcribe"]:
        assert c["beam_size"] == 1
        assert c["word_timestamps"] is False
        assert c["vad_filter"] is True
    # Segment-level output shifted to absolute time
    assert [(s.start, s.end) for s in segs] == [(1.0, 4.0), (5.0, 9.0), (101.0, 104.0), (105.0, 109.0)]
    assert segs[0].clean == "Olha isso!"

    cache = json.loads((tmp_path / "pass1_segments.json").read_text(encoding="utf-8"))
    assert cache["windows"] == [[0.0, 45.0], [100.0, 130.0]]
    assert len(cache["segments"]) == 4

    again = transcribe_fast_blocks(wav, blocks, tmp_path, model_size="small", cpu_threads=2)
    assert calls["loads"] == 1  # cache hit, model never reloaded
    assert [(s.start, s.end, s.text) for s in again] == [(s.start, s.end, s.text) for s in segs]


def test_fast_blocks_empty_does_not_load_model(tmp_path: Path, monkeypatch):
    calls = _fake_whisper(monkeypatch)
    assert transcribe_fast_blocks(tmp_path / "missing.wav", [], tmp_path) == []
    assert calls["loads"] == 0


def test_pass2_word_timestamps_only_on_candidate_windows(tmp_path: Path, monkeypatch):
    wav = tmp_path / "audio.wav"
    _write_wav(wav, 600.0)
    calls = _fake_whisper(monkeypatch)
    cands = [
        CandidatePeak(peak=150.0, raw_score=0.8, start_win=120.0, end_win=180.0),
        CandidatePeak(peak=450.0, raw_score=0.7, start_win=420.0, end_win=500.0),
    ]
    words, lang = transcribe_candidates(wav, cands, tmp_path, model_size="small", cpu_threads=2, beam_size=1)

    assert lang == "pt"
    assert [c["n_samples"] for c in calls["transcribe"]] == [60 * 16000, 80 * 16000]
    for c in calls["transcribe"]:
        assert c["word_timestamps"] is True
        assert c["beam_size"] == 1
    assert all(120.0 <= w.start and w.end <= 180.0 or 420.0 <= w.start and w.end <= 500.0 for w in words)

    cache = json.loads((tmp_path / "words.json").read_text(encoding="utf-8"))
    assert cache["beam_size"] == 1
    # A different beam size must not reuse the beam=1 transcript
    transcribe_candidates(wav, cands, tmp_path, model_size="small", cpu_threads=2, beam_size=5)
    assert calls["loads"] == 2


FILLER = "a gente estava conversando sobre isso ontem lá em casa com o pessoal todo reunido"


def _seg(start: float, end: float, texts: list[str]) -> TopicSegment:
    step = (end - start) / len(texts)
    sents = [(start + i * step, start + (i + 1) * step, t) for i, t in enumerate(texts)]
    return TopicSegment(start=start, end=end, peak=(start + end) / 2, sentences=sents)


def test_cheapscore_ranks_hook_story_pace_above_monotone_and_donate_reading():
    n = 1200
    db = _compressed_speech(n, seed=6)
    db[600 * FPS_RMS : 900 * FPS_RMS] = -20.0  # monotone, flat delivery
    bundle = _bundle(db)

    lively = " ".join([FILLER] * 5)
    hooked = _seg(100.0, 140.0, ["Mano, você não vai acreditar no que aconteceu?", "Caralho, do nada o cara apareceu!", lively])
    story = _seg(300.0, 340.0, ["Sério, quando eu tava lá, nossa!", "Aí imagina a história kkkk", lively])
    monotone = _seg(650.0, 690.0, [" ".join(FILLER.split()[:10]) for _ in range(4)])
    donate = _seg(1000.0, 1040.0, [
        "Obrigado pelo sub fulano!",
        "Valeu pelos bits beltrano, obrigado pelo pix ciclano",
        lively,
    ])

    scored = score_topic_segments([hooked, story, monotone, donate], bundle)
    assert all(isinstance(s, ScoredSegment) for s in scored)
    assert all(0.0 <= s.score <= 1.0 for s in scored)
    by = {round(s.start): s for s in scored}
    for good in (100, 300):
        assert by[good].score > by[650].score
        assert by[good].score > by[1000].score
    assert by[100].word_count >= 30


def test_cheapscore_uses_external_signals_when_present():
    n = 1200
    db = _compressed_speech(n, seed=7)
    db[700 * FPS_RMS : 760 * FPS_RMS] = db[200 * FPS_RMS : 260 * FPS_RMS]  # same delivery, only heatmap differs
    heatmap = [{"start_time": float(i * 12), "end_time": float((i + 1) * 12), "value": 0.1} for i in range(100)]
    for i in range(int(700 // 12), int(760 // 12) + 1):
        heatmap[i]["value"] = 0.95
    bundle = compute_signals(duration=float(n), rms_db=db, heatmap=heatmap)
    texts = ["E aí, tudo bem?", " ".join([FILLER] * 5)]
    a = _seg(200.0, 260.0, texts)
    b = _seg(700.0, 760.0, texts)
    sa, sb = score_topic_segments([a, b], bundle)
    assert sb.score > sa.score


def test_dedup_segments_text_keeps_highest_score():
    base = "então a gente foi pro show e o cara subiu no palco e começou a cantar errado a música inteira"
    s_hi = ScoredSegment(start=0.0, end=40.0, peak=20.0, sentences=[(0.0, 40.0, base)], score=0.8, word_count=40)
    s_dup = ScoredSegment(start=500.0, end=540.0, peak=520.0, sentences=[(500.0, 540.0, base + " de novo")], score=0.6, word_count=40)
    s_other = ScoredSegment(
        start=900.0, end=940.0, peak=920.0,
        sentences=[(900.0, 940.0, "depois disso fomos comer pizza e discutir futebol até de madrugada")],
        score=0.5, word_count=40,
    )
    kept = dedup_segments_text([s_dup, s_other, s_hi], max_jaccard=0.60)
    assert [k.score for k in kept] == [0.8, 0.5]


WORDS40 = "palavra " * 40


def _scored(start: float, score: float, text: str | None = None, dur: float = 40.0, words: int = 40) -> ScoredSegment:
    body = text if text is not None else f"assunto numero {int(start)} " + "fala " * (words - 3)
    return ScoredSegment(
        start=start, end=start + dur, peak=start + dur / 2,
        sentences=[(start, start + dur, body)], score=score, word_count=words,
    )


def _flat_bundle(duration: float):
    """Conversational loudness with no strong peaks and no heatmap/chat."""
    return _bundle(np.full(int(duration) * FPS_RMS, -30.0, dtype=np.float32))


def _third(t: float, duration: float) -> int:
    return min(2, int(t // (duration / 3)))


def test_quota_2h_no_peaks_returns_6_spread_over_thirds():
    duration = 7200.0
    bundle = _flat_bundle(duration)
    # Best material clustered in the first third; decent talk elsewhere
    segs = [_scored(10.0 + i * 300.0, 0.95) for i in range(8)]
    segs += [_scored(2410.0 + i * 400.0, 0.80) for i in range(12)]

    cands, floor_mode = pick_quota_candidates(bundle, segs, max_clips=8, min_per_hour=3.0)

    assert len(cands) == 6
    peaks = sorted(c.peak for c in cands)
    assert all(b - a >= 120.0 for a, b in zip(peaks, peaks[1:]))
    per_third = [sum(1 for p in peaks if _third(p, duration) == k) for k in range(3)]
    assert max(per_third) <= 3
    assert per_third[0] == 3
    assert floor_mode is True  # P75 alone only had first-third material
    for c in cands:
        assert c.topic_start is not None and c.topic_end is not None
        assert c.start_win <= c.topic_start and c.end_win >= c.topic_end


def test_quota_fills_at_p75_without_floor_mode():
    duration = 7200.0
    bundle = _flat_bundle(duration)
    segs = [_scored(10.0 + i * 235.0, 0.4 + 0.5 * ((i * 7) % 30) / 30) for i in range(30)]
    cands, floor_mode = pick_quota_candidates(bundle, segs, max_clips=8, min_per_hour=3.0)
    assert len(cands) == 6
    assert floor_mode is False
    p75 = float(np.percentile([s.score for s in segs], 75))
    assert all(c.raw_score >= p75 for c in cands)


def test_quota_floor_mode_never_goes_below_p55_or_abs_floor():
    duration = 7200.0
    bundle = _flat_bundle(duration)
    scores = [0.9, 0.85, 0.3, 0.3, 0.3, 0.2, 0.2, 0.2, 0.1, 0.1]
    segs = [_scored(10.0 + i * 700.0, s) for i, s in enumerate(scores)]
    cands, floor_mode = pick_quota_candidates(bundle, segs, max_clips=8, min_per_hour=3.0)
    assert floor_mode is True
    p55 = float(np.percentile(scores, 55))
    assert 2 <= len(cands) < 6
    assert all(c.raw_score >= max(p55, 0.25) for c in cands)


def test_quota_no_speech_returns_empty_without_floor_mode():
    bundle = _flat_bundle(3600.0)
    assert pick_quota_candidates(bundle, []) == ([], False)
    low = [_scored(10.0 + i * 300.0, 0.2) for i in range(10)]
    assert pick_quota_candidates(bundle, low) == ([], False)
    short = [_scored(10.0 + i * 300.0, 0.9, words=12) for i in range(10)]
    assert pick_quota_candidates(bundle, short) == ([], False)


def test_quota_dedups_repeated_text_and_short_vod_gets_one():
    bundle = _flat_bundle(900.0)  # 15 min -> K = ceil(0.75) = 1
    repeated = "então o cara chegou e falou que ia resolver tudo mas não resolveu nada " * 3
    segs = [_scored(10.0, 0.9, repeated), _scored(400.0, 0.85, repeated), _scored(700.0, 0.5)]
    cands, _ = pick_quota_candidates(bundle, segs, max_clips=8, min_per_hour=3.0)
    assert len(cands) == 1
    assert cands[0].topic_start == 10.0

    bundle_2h = _flat_bundle(7200.0)
    segs = [_scored(10.0, 0.9, repeated), _scored(3000.0, 0.85, repeated)]
    segs += [_scored(1000.0 + i * 1200.0, 0.6) for i in range(5)]
    cands, _ = pick_quota_candidates(bundle_2h, segs, max_clips=8, min_per_hour=3.0)
    assert 3000.0 not in [c.topic_start for c in cands]


def test_quota_keeps_strong_peaks_first():
    duration = 3600.0
    db = np.full(int(duration) * FPS_RMS, -30.0, dtype=np.float32)
    db[1800 * FPS_RMS : 1803 * FPS_RMS] = -5.0
    bundle = _bundle(db)
    segs = [_scored(10.0 + i * 400.0, 0.8) for i in range(9)]
    cands, _ = pick_quota_candidates(bundle, segs, max_clips=8, min_per_hour=3.0)
    assert len(cands) == 3
    assert abs(cands[0].peak - 1801.0) <= 5.0 and cands[0].topic_start is None
    assert all(abs(c.peak - cands[0].peak) >= 120.0 for c in cands[1:])


import json as _json

from clipes.eval import evaluate_quota_calibration

FIXTURE = Path(__file__).parent / "fixtures" / "calib_labels.json"


def test_calib_fixture_keeps_only_documented_positives():
    videos = _json.loads(FIXTURE.read_text(encoding="utf-8"))
    by_id = {v["video_id"]: v["moments"] for v in videos}
    assert set(by_id) == {"UBv07VGeD24", "cStJTQf9XUk"}
    assert len(by_id["UBv07VGeD24"]) == 8
    assert len(by_id["cStJTQf9XUk"]) == 2
    moments = [m for ms in by_id.values() for m in ms]
    assert all(m["label"] == 1 and m["end"] > m["start"] for m in moments)
    # The 8 published clips, taken as the selection, recover every positive of that VOD.
    labels = by_id["UBv07VGeD24"]
    metrics = evaluate_quota_calibration(labels, labels, k=8)
    assert metrics == {"precision_at_k": 1.0, "recall_at_k": 1.0}


def test_calibration_counts_center_or_iou_and_ignores_negatives():
    labels = [
        {"start": 100.0, "end": 140.0, "label": 1},
        {"start": 300.0, "end": 340.0, "label": 1},
        {"start": 500.0, "end": 540.0, "label": 1},
        {"start": 700.0, "end": 740.0, "label": 1},
        {"start": 900.0, "end": 940.0, "label": 1},
        {"start": 1100.0, "end": 1140.0, "label": 0},
    ]
    selected = [
        {"start": 100.0, "end": 140.0},   # exact
        {"start": 305.0, "end": 335.0},   # label center inside
        {"start": 490.0, "end": 530.0},   # IoU 0.6, center 520 outside
        {"start": 680.0, "end": 710.0},   # IoU < 0.5 and center outside: miss
        {"start": 905.0, "end": 935.0},   # center inside
        {"start": 1100.0, "end": 1140.0}, # overlaps only the negative label
        {"start": 2000.0, "end": 2040.0},
        {"start": 3000.0, "end": 3040.0},
        {"start": 100.0, "end": 140.0},   # past k
    ]
    metrics = evaluate_quota_calibration(selected, labels, k=8)
    assert metrics["precision_at_k"] == 0.5
    assert metrics["recall_at_k"] == 0.8

    wide = CandidatePeak(
        peak=120.0, raw_score=0.5, start_win=0.0, end_win=400.0,
        topic_start=100.0, topic_end=140.0,
    )
    assert evaluate_quota_calibration([wide], labels, k=8)["recall_at_k"] == 0.2


def test_calibration_empty_selection():
    assert evaluate_quota_calibration([], [{"start": 0.0, "end": 10.0, "label": 1}]) == {
        "precision_at_k": 0.0,
        "recall_at_k": 0.0,
    }
