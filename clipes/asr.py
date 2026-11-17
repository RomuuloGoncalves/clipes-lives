"""Surgical CPU transcription via faster-whisper INT8 on candidate windows only."""

from __future__ import annotations

import json
import os
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .models import CandidatePeak, Word


def merge_windows(windows: list[tuple[float, float]], gap: float = 2.0) -> list[tuple[float, float]]:
    """Merge overlapping or near-adjacent (start, end) windows."""
    if not windows:
        return []
    ordered = sorted((float(s), float(e)) for s, e in windows if e > s)
    merged: list[tuple[float, float]] = [ordered[0]]
    for s, e in ordered[1:]:
        ps, pe = merged[-1]
        if s <= pe + gap:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def load_audio_slice(audio_path: Path, start: float, end: float) -> tuple[np.ndarray, int]:
    """Read a [start, end] slice from a 16 kHz mono PCM WAV as float32 in [-1, 1]."""
    with wave.open(str(audio_path), "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        n_ch = wf.getnchannels()
        i0 = max(0, int(round(start * sr)))
        i1 = min(n_frames, int(round(end * sr)))
        if i1 <= i0:
            return np.zeros(0, dtype=np.float32), sr
        wf.setpos(i0)
        raw = wf.readframes(i1 - i0)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch > 1:
        samples = samples.reshape(-1, n_ch).mean(axis=1)
    return samples, sr


def transcribe_candidates(
    audio_path: Path,
    candidates: list[CandidatePeak],
    cache_dir: Path,
    model_size: str | None = None,
    cpu_threads: int | None = None,
) -> tuple[list[Word], str | None]:
    """Transcribe ~90s windows around candidates using faster-whisper INT8 + Silero VAD."""
    if not candidates:
        return [], None

    model_name = model_size or os.environ.get("CLIPES_WHISPER_MODEL", "small")
    threads = cpu_threads or int(os.environ.get("CLIPES_CPU_THREADS", "6"))
    windows = merge_windows([(c.start_win, c.end_win) for c in candidates])

    words_path = cache_dir / "words.json"
    if words_path.is_file():
        try:
            cached = json.loads(words_path.read_text(encoding="utf-8"))
            cached_wins = [tuple(w) for w in cached.get("windows", [])]
            if cached.get("model") == model_name and cached_wins == [list(w) for w in windows]:
                words = [Word(float(w["start"]), float(w["end"]), str(w["word"])) for w in cached.get("words", [])]
                print(f"[asr] Reusing cached transcript ({len(words)} words)", flush=True)
                return words, cached.get("language")
        except Exception:
            pass

    from faster_whisper import WhisperModel

    t0 = time.perf_counter()
    print(f"[asr] Loading faster-whisper '{model_name}' (int8, threads={threads})...", flush=True)
    model = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=threads,
    )

    all_words: list[Word] = []
    detected_lang: str | None = None
    best_lang_prob = 0.0

    for idx, (w_start, w_end) in enumerate(windows, 1):
        audio_slice, sr = load_audio_slice(audio_path, w_start, w_end)
        if len(audio_slice) < sr // 2:
            continue
        lang_arg = detected_lang if best_lang_prob >= 0.75 else None
        segments, info = model.transcribe(
            audio_slice,
            language=lang_arg,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": 600,
                "speech_pad_ms": 400,
            },
        )
        if getattr(info, "language", None) and getattr(info, "language_probability", 0.0) > best_lang_prob:
            detected_lang = str(info.language)
            best_lang_prob = float(info.language_probability)

        win_words = 0
        for seg in segments:
            for w in (getattr(seg, "words", None) or []):
                gs = round(w_start + float(w.start), 3)
                ge = round(w_start + float(w.end), 3)
                if ge > gs and str(w.word).strip():
                    all_words.append(Word(start=gs, end=ge, text=str(w.word)))
                    win_words += 1
        print(
            f"[asr] Window {idx}/{len(windows)} [{w_start:.1f}s-{w_end:.1f}s]: "
            f"{win_words} words (lang={detected_lang})",
            flush=True,
        )

    all_words.sort(key=lambda w: (w.start, w.end))
    # Deduplicate any accidental overlap from adjacent windows
    deduped: list[Word] = []
    for w in all_words:
        if deduped and w.start < deduped[-1].end - 0.01:
            if w.end <= deduped[-1].end + 0.01:
                continue
        deduped.append(w)

    payload: dict[str, Any] = {
        "model": model_name,
        "language": detected_lang,
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "windows": [[round(s, 2), round(e, 2)] for s, e in windows],
        "words": [{"start": w.start, "end": w.end, "word": w.text} for w in deduped],
    }
    words_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return deduped, detected_lang
