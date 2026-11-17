"""Synthetic tests for deterministic word/silence snapping."""

import numpy as np

from clipes.eval import cuts_mid_word, evaluate_clips
from clipes.models import CandidatePeak, Word
from clipes.signals import FPS_RMS
from clipes.snap import select_clips, snap_candidate


def _make_speech_timeline(
    total_s: float = 300.0,
    sentence_period: float = 8.0,
) -> tuple[list[Word], np.ndarray]:
    """Generate realistic synthetic words with sentences, pauses, and baseline RMS."""
    words: list[Word] = []
    t = 2.0
    sent_idx = 0
    while t + 6.5 < total_s:
        tokens = ["Olha", "só", "o", "que", "acabou", "de", "acontecer", "ali", "agora!"]
        for k, tok in enumerate(tokens):
            w_start = round(t, 3)
            w_end = round(t + 0.38, 3)
            words.append(Word(start=w_start, end=w_end, text=(" " if k else " ") + tok))
            t = w_end + 0.12
        # Pause >= 0.9s after each sentence
        t += max(0.95, sentence_period - len(tokens) * 0.5)
        sent_idx += 1

    rms = np.full(int(total_s * FPS_RMS), -32.0, dtype=np.float32)
    return words, rms


def test_snap_synthetic_speech_metrics():
    duration = 320.0
    words, rms = _make_speech_timeline(duration, sentence_period=7.5)
    peaks = [65.0, 160.0, 255.0]
    for p in peaks:
        # Plant a loud reaction around each peak
        i0 = int((p - 1.0) * FPS_RMS)
        i1 = int((p + 2.0) * FPS_RMS)
        rms[i0:i1] = -14.0

    cands = [
        CandidatePeak(peak=p, raw_score=0.9 - i * 0.1, start_win=p - 45, end_win=p + 45, active_signals=["loudness"])
        for i, p in enumerate(peaks)
    ]
    clips = select_clips(cands, words, rms, duration, max_clips=5, min_dur=25.0, max_dur=55.0)
    assert len(clips) == 3

    metrics = evaluate_clips(clips, words, min_dur=25.0, max_dur=55.0, ground_truth_peaks=peaks, k=5)
    assert metrics["mid_word_cut_rate"] == 0.0
    assert metrics["sentence_edge_rate"] >= 0.85
    assert metrics["duration_ok_rate"] == 1.0
    assert metrics["peak_position_ok_rate"] == 1.0
    assert metrics["recall_at_5"] == 1.0


def test_snap_protects_post_speech_laughter():
    """When elevated RMS (laughter/scream) follows the final word before a long pause, end extends."""
    words = [
        Word(10.0, 10.4, " Olha"),
        Word(10.5, 11.0, " isso"),
        Word(11.1, 11.6, " aqui!"),
        # pause 1.2s
        Word(12.8, 13.2, " Cara,"),
        Word(13.3, 13.8, " espera"),
        Word(13.9, 14.4, " um"),
        Word(14.5, 15.0, " pouco."),
        # climax sentence around t=25
        Word(23.5, 24.0, " Meu"),
        Word(24.1, 24.5, " Deus"),
        Word(24.6, 25.2, " do"),
        Word(25.3, 25.8, " céu!"),
        # 5s laughter/scream gap from 25.8 to 33.5 before next sentence at 34.0
        Word(34.0, 34.4, " Bom,"),
        Word(34.5, 35.0, " vamos"),
        Word(35.1, 35.7, " seguir."),
        # long extra sentence for range
        Word(38.0, 38.4, " Tudo"),
        Word(38.5, 39.0, " certo."),
    ]
    duration = 60.0
    rms = np.full(int(duration * FPS_RMS), -35.0, dtype=np.float32)
    # Laughing/screaming elevated energy from 25.0s through 31.0s
    rms[int(25.0 * FPS_RMS):int(31.0 * FPS_RMS)] = -15.0

    cand = CandidatePeak(peak=25.5, raw_score=0.95, start_win=0.0, end_win=60.0, active_signals=["loudness"])
    clip = snap_candidate(cand, words, rms, duration, min_dur=20.0, max_dur=55.0)
    assert clip is not None
    assert not cuts_mid_word(clip.start, words)
    assert not cuts_mid_word(clip.end, words)
    # Must protect the laugh through ~31.0s rather than cutting at 26.15s
    assert clip.end >= 31.0
    assert 20.0 <= clip.duration <= 55.0
    assert 0.35 <= clip.peak_ratio <= 0.75


def test_snap_sound_only_fallback_uses_silence():
    """Window with no speech falls back to audio silence boundaries."""
    duration = 120.0
    rms = np.full(int(duration * FPS_RMS), -30.0, dtype=np.float32)
    # Quiet dips around 40s and 72s; scream peak at 55s
    rms[int(39.0 * FPS_RMS):int(41.0 * FPS_RMS)] = -52.0
    rms[int(54.0 * FPS_RMS):int(57.0 * FPS_RMS)] = -10.0
    rms[int(71.0 * FPS_RMS):int(73.0 * FPS_RMS)] = -52.0

    cand = CandidatePeak(peak=55.0, raw_score=0.9, start_win=10.0, end_win=100.0, active_signals=["loudness"])
    clip = snap_candidate(cand, words=[], rms_db=rms, duration=duration, min_dur=25.0, max_dur=55.0)
    assert clip is not None
    assert clip.edge_mode == "silence"
    assert 25.0 <= clip.duration <= 55.0
    assert 0.35 <= clip.peak_ratio <= 0.75
    assert 38.5 <= clip.start <= 42.0
    assert 70.5 <= clip.end <= 73.5


def test_snap_discards_impossible_candidate():
    """Candidates that cannot meet duration/peak-ratio/non-mid-word constraints are discarded."""
    # Video shorter than min_dur
    rms = np.full(40, -30.0, dtype=np.float32)
    cand = CandidatePeak(peak=10.0, raw_score=0.9, start_win=0.0, end_win=20.0)
    assert snap_candidate(cand, words=[], rms_db=rms, duration=20.0, min_dur=25.0, max_dur=55.0) is None

    # One single giant unbreakable word spanning the whole possible boundary range
    words = [Word(0.0, 90.0, "unbreakable")]
    rms_long = np.full(200, -30.0, dtype=np.float32)
    cand2 = CandidatePeak(peak=45.0, raw_score=0.9, start_win=0.0, end_win=90.0)
    assert snap_candidate(cand2, words=words, rms_db=rms_long, duration=100.0, min_dur=25.0, max_dur=55.0) is None
