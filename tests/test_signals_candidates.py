"""Synthetic tests for 1 Hz multimodal signals, chat parser, and candidate picking."""

from pathlib import Path
import wave
import numpy as np

from clipes.candidates import pick_candidate_peaks
from clipes.ingest import parse_ytdlp_live_chat
from clipes.signals import FPS_RMS, compute_signals, pre_score


def _write_wav(path: Path, samples: np.ndarray, sr: int = 16000) -> Path:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return path


def test_pre_score_silence_then_loud_tone_is_high(tmp_path: Path):
    sr = 16000
    silence = np.zeros(10 * sr)
    t = np.arange(2 * sr) / sr
    tone = 0.5 * np.sin(2 * np.pi * 440.0 * t)
    wav = _write_wav(tmp_path / "pico.wav", np.concatenate([silence, tone]), sr)
    assert pre_score(wav) > 20


def test_pre_score_constant_signal_is_near_zero(tmp_path: Path):
    sr = 16000
    t = np.arange(5 * sr) / sr
    wav = _write_wav(tmp_path / "const.wav", 0.3 * np.sin(2 * np.pi * 440.0 * t), sr)
    assert abs(pre_score(wav)) <= 0.5


def test_pre_score_empty_wav_is_zero(tmp_path: Path):
    wav = _write_wav(tmp_path / "vazio.wav", np.zeros(0))
    assert pre_score(wav) == 0.0


def test_planted_multimodal_peaks_and_cache(tmp_path: Path):
    duration = 600.0
    n = int(duration)
    rms_db = np.full(n * FPS_RMS, -36.0, dtype=np.float32)
    # Plant silence gaps so masking works too
    rms_db[0:20] = -65.0

    planted = [100.0, 260.0, 440.0]
    for p in planted:
        i0 = int((p - 1.5) * FPS_RMS)
        i1 = int((p + 2.0) * FPS_RMS)
        rms_db[i0:i1] = -12.0

    # Chat messages: steady background + bursts 5s AFTER planted peaks (testing -5s shift)
    msgs = []
    for t in range(5, n, 4):
        msgs.append([float(t), "normal message", t % 20])
    for p in planted:
        # Reaction burst at p + 5s
        for k in range(25):
            msgs.append([p + 5.0 + (k % 3) * 0.4, "KKKKKK caralho clipa", 100 + k])

    heatmap = [
        {"start_time": float(i * 6), "end_time": float((i + 1) * 6), "value": 0.1}
        for i in range(100)
    ]
    for p in planted:
        seg_idx = int(p // 6)
        heatmap[seg_idx]["value"] = 0.95

    npz_path = tmp_path / "signals.npz"
    bundle = compute_signals(
        duration=duration,
        rms_db=rms_db,
        chat_messages=msgs,
        heatmap=heatmap,
        cache_path=npz_path,
    )

    assert npz_path.is_file()
    with np.load(npz_path) as data:
        assert "smoothed" in data
        assert "sig_loudness" in data
        assert "sig_chat" in data
        assert "sig_heatmap" in data

    # Agreement bonus must activate at planted peaks
    for p in planted:
        assert bundle.agreeing[int(p) - 2 : int(p) + 3].max() >= 2

    cands = pick_candidate_peaks(bundle, max_clips=3, min_separation=90)
    assert len(cands) >= 3
    found_peaks = [c.peak for c in cands]
    for p in planted:
        assert any(abs(fp - p) <= 5.0 for fp in found_peaks), f"Missed planted peak {p} in {found_peaks}"

    # Verify >= 90s separation between all selected candidates
    for i in range(len(found_peaks)):
        for j in range(i + 1, len(found_peaks)):
            assert abs(found_peaks[i] - found_peaks[j]) >= 90.0


def test_missing_chat_and_heatmap_degrade_cleanly():
    duration = 400.0
    rms_db = np.full(int(duration) * FPS_RMS, -35.0, dtype=np.float32)
    rms_db[200 * FPS_RMS : 203 * FPS_RMS] = -10.0

    bundle = compute_signals(duration=duration, rms_db=rms_db, chat_messages=None, heatmap=None)
    assert list(bundle.scores.keys()) == ["loudness"]
    assert abs(bundle.weights["loudness"] - 1.0) < 1e-6

    cands = pick_candidate_peaks(bundle, max_clips=4, min_separation=90)
    assert len(cands) >= 1
    assert abs(cands[0].peak - 201.0) <= 5.0


def test_agreement_bonus_beats_single_signal():
    duration = 400.0
    n = int(duration)
    rms_db = np.full(n * FPS_RMS, -35.0, dtype=np.float32)
    # Peak A at 100s (loudness + chat) vs Peak B at 260s (loudness only, same intensity)
    rms_db[100 * FPS_RMS : 102 * FPS_RMS] = -12.0
    rms_db[260 * FPS_RMS : 262 * FPS_RMS] = -12.0

    msgs = [[float(t), "ok", t % 10] for t in range(5, n, 4)]
    for k in range(25):
        msgs.append([105.0 + (k % 2) * 0.5, "LUL omg", 200 + k])

    bundle = compute_signals(duration=duration, rms_db=rms_db, chat_messages=msgs, heatmap=None)
    score_a = bundle.smoothed[98:104].max()
    score_b = bundle.smoothed[258:264].max()
    assert score_a > score_b


def test_parse_ytdlp_live_chat():
    sample_lines = [
        '{"replayChatItemAction":{"videoOffsetTimeMsec":"12500","actions":[{"addChatItemAction":{"item":{"liveChatTextMessageRenderer":{"message":{"runs":[{"text":"KKKKKK"}]},"authorName":{"simpleText":"Joao"}}}}}]}}',
        '{"replayChatItemAction":{"videoOffsetTimeMsec":"-500","actions":[]}}',
        'not json',
    ]
    rows = parse_ytdlp_live_chat(sample_lines)
    assert len(rows) == 1
    assert rows[0] == (12.5, "KKKKKK", "Joao")


def test_asymmetric_window_on_top_candidates_and_symmetric_reserves():
    duration = 1200.0
    n = int(duration)
    rms_db = np.full(n * FPS_RMS, -36.0, dtype=np.float32)
    # 5 peaks: first near start (60s) to test 0.0 clamping, last near end (1170s) to test duration clamping
    planted = [(60.0, -8.0), (300.0, -10.0), (550.0, -12.0), (850.0, -14.0), (1170.0, -16.0)]
    for p, level in planted:
        rms_db[int((p - 1.0) * FPS_RMS) : int((p + 1.0) * FPS_RMS)] = level

    bundle = compute_signals(duration=duration, rms_db=rms_db, chat_messages=None, heatmap=None)
    cands = pick_candidate_peaks(bundle, max_clips=3, min_separation=90)
    assert len(cands) == 5

    # Top max_clips (indices 0..2) use [peak - 150, peak + 60]
    for c in cands[:3]:
        assert c.start_win == round(max(0.0, c.peak - 150.0), 2)
        assert c.end_win == round(min(duration, c.peak + 60.0), 2)

    # Reserves (index >= max_clips) keep [peak - 45, peak + 45]
    for c in cands[3:]:
        assert c.start_win == round(max(0.0, c.peak - 45.0), 2)
        assert c.end_win == round(min(duration, c.peak + 45.0), 2)
