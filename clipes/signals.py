"""1 Hz multimodal highlight signals (loudness, chat, heatmap) with agreement bonus."""

from __future__ import annotations

import math
import re
import wave
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d

from .models import SignalBundle

FPS_RMS = 2
SILENCE_DB = -55.0
LOUDNESS_BASELINE_S = 60
JUMP_LOOKBACK_S = 3

CHAT_WINDOW_S = 5
CHAT_BASELINE_S = 121
CHAT_MIN_MESSAGES = 20
CHAT_SHIFT_S = 5.0
AUTHOR_COOLDOWN_S = 3.0

BASE_WEIGHTS = {
    "loudness": 0.40,
    "chat": 0.35,
    "heatmap": 0.25,
}

# Reaction patterns covering pt-BR + EN + universal Twitch/YouTube slang.
REACTION_WHOLE_RE = re.compile(
    r"^(?:w+|l+|f|gg+|ez+|\?{2,}|!{2,}|\.{3,}|o+|x+d+|\+1+|k{2,}|rs+)$",
    re.IGNORECASE,
)
REACTION_CONTAINS_RE = re.compile(
    r"(?:"
    r"k{3,}|(?:ha){2,}|(?:he){2,}|(?:ah){2,}|rsrs+|ahuahua+|"
    r"lulw?|kekw|omegalul|pepelaugh|lmf?ao+|\brofl\b|\blo+l\b|"
    r"pog(?:gers|champ)?|monkas|sadge|\bomg\b|\bwtf\b|\bbruh\b|"
    r"caralho|carai|mano+|meu deus|\bmds\b|\bpqp\b|nem fudendo|"
    r"clipa|clip ?it|\bgta\b|rockstar|no way|insane|holy|"
    r"\?{3,}|!{3,}|😂|🤣|😭|💀|😱|🔥|👀|😳|🤯|😮|👏"
    r")",
    re.IGNORECASE,
)


def _norm_text(text: str) -> str:
    return re.sub(r"(.)\1{2,}", r"\1\1", text.casefold().strip())


def is_reaction_message(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    norm = _norm_text(t)
    return bool(REACTION_WHOLE_RE.match(norm) or REACTION_CONTAINS_RE.search(norm))


def robust_percentile_norm(
    x: np.ndarray,
    low_q: float = 50.0,
    high_q: float = 99.0,
    min_scale: float = 1e-3,
) -> np.ndarray:
    """Normalize array to [0, 1] using robust percentiles and a floor scale."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.size == 0:
        return arr
    p_low = float(np.percentile(arr, low_q))
    p_high = float(np.percentile(arr, high_q))
    scale = max(p_high - p_low, min_scale)
    return np.clip((arr - p_low) / scale, 0.0, 1.0)


def _frames_to_seconds(frames: np.ndarray, fps: int, n: int) -> np.ndarray:
    need = n * fps
    if len(frames) < need:
        frames = np.pad(frames, (0, need - len(frames)))
    return frames[:need].reshape(n, fps).max(axis=1)


def compute_rms_db(audio_path: Path, duration: float | None = None, fps: int = FPS_RMS) -> np.ndarray:
    """Stream 16 kHz mono WAV in chunks and compute RMS dBFS at `fps` Hz."""
    with wave.open(str(audio_path), "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        n_frames = wf.getnframes()
        if duration is None or duration <= 0:
            duration = n_frames / float(sr)
        hop = max(1, int(sr / fps))
        chunk_frames = hop * fps * 120  # 2 minutes per read
        out: list[np.ndarray] = []
        while True:
            raw = wf.readframes(chunk_frames)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if n_channels > 1:
                samples = samples.reshape(-1, n_channels).mean(axis=1)
            m = len(samples) // hop
            if m > 0:
                blk = samples[: m * hop].reshape(m, hop)
                out.append(10.0 * np.log10(np.mean(blk * blk, axis=1) + 1e-10))
            rem = len(samples) % hop
            if rem > 0:
                tail = samples[m * hop:]
                out.append(np.array([10.0 * np.log10(np.mean(tail * tail) + 1e-10)], dtype=np.float32))
    if not out:
        return np.full(max(1, math.ceil(duration or 1.0) * fps), SILENCE_DB, dtype=np.float32)
    return np.concatenate(out).astype(np.float32)


def signal_loudness(rms_db: np.ndarray, n: int) -> np.ndarray:
    """1 Hz loudness score: excess over 60s rolling median (silence masked) + 3s abrupt jump."""
    db = np.asarray(rms_db, dtype=np.float64)
    if db.size == 0:
        return np.zeros(n, dtype=np.float64)

    voiced = np.where(db > SILENCE_DB, db, np.nan)
    fill = float(np.nanmedian(voiced)) if np.isfinite(voiced).any() else -40.0
    win = min(len(db), LOUDNESS_BASELINE_S * FPS_RMS + 1)
    if win % 2 == 0:
        win = max(1, win - 1)
    baseline = median_filter(np.where(np.isfinite(voiced), voiced, fill), size=win, mode="nearest")
    excess = np.maximum(0.0, db - baseline - 3.0)

    k = JUMP_LOOKBACK_S * FPS_RMS
    csum = np.concatenate([[0.0], np.cumsum(db)])
    idx = np.arange(len(db))
    a = np.maximum(0, idx - k)
    cnt = idx - a
    prev_mean = np.where(cnt > 0, (csum[idx] - csum[a]) / np.maximum(cnt, 1), db)
    jump = np.maximum(0.0, db - prev_mean - 4.0)

    raw_frame = 0.6 * excess + 0.4 * jump
    raw_frame[db <= SILENCE_DB] = 0.0
    raw_1hz = _frames_to_seconds(raw_frame, FPS_RMS, n)
    scores = robust_percentile_norm(raw_1hz, low_q=50.0, high_q=99.0, min_scale=8.0)
    # Keep truly silent seconds at 0
    sec_db = _frames_to_seconds(db, FPS_RMS, n)
    scores[sec_db <= SILENCE_DB] = 0.0
    return scores


def signal_chat(
    messages: list[list[Any]] | None,
    n: int,
    shift_s: float = CHAT_SHIFT_S,
) -> np.ndarray | None:
    """1 Hz chat hype score with -5s shift, anti-flood, Poisson cap, and pt-BR/en regex."""
    if not messages or len(messages) < CHAT_MIN_MESSAGES:
        return None

    rate = np.zeros(n, dtype=np.float64)
    react = np.zeros(n, dtype=np.float64)

    # Copy-paste wave detection: same normalized short message from >=3 distinct authors within 5s
    by_text: dict[str, list[tuple[float, Any]]] = {}
    for msg in messages:
        if len(msg) < 2:
            continue
        t = float(msg[0])
        text = str(msg[1])
        author = msg[2] if len(msg) > 2 else 0
        key = _norm_text(text)[:40]
        if key:
            by_text.setdefault(key, []).append((t, author))

    wave_keys: set[str] = set()
    for key, items in by_text.items():
        if len(items) < 3 or (key.isdigit() and len(key) <= 2):
            continue
        items_sorted = sorted(items, key=lambda x: x[0])
        for i in range(len(items_sorted) - 2):
            if items_sorted[i + 2][0] - items_sorted[i][0] <= CHAT_WINDOW_S:
                if len({items_sorted[j][1] for j in range(i, i + 3)}) >= 3:
                    wave_keys.add(key)
                    break

    last_author_t: dict[Any, float] = {}
    sorted_msgs = sorted(messages, key=lambda m: float(m[0]))
    for msg in sorted_msgs:
        if len(msg) < 2:
            continue
        t = float(msg[0])
        text = str(msg[1])
        author = msg[2] if len(msg) > 2 else 0
        prev_t = last_author_t.get(author)
        if prev_t is not None and (t - prev_t) < AUTHOR_COOLDOWN_S:
            continue
        last_author_t[author] = t

        sec = int(t - shift_s)
        if not (0 <= sec < n):
            continue
        rate[sec] += 1.0
        key = _norm_text(text)[:40]
        if is_reaction_message(text) or key in wave_keys:
            react[sec] += 1.0

    if rate.sum() < CHAT_MIN_MESSAGES:
        return None

    rate5 = uniform_filter1d(rate, CHAT_WINDOW_S, mode="constant")
    react5 = uniform_filter1d(react, CHAT_WINDOW_S, mode="constant")
    med_win = min(CHAT_BASELINE_S, max(3, (n // 2) * 2 - 1))
    baseline = median_filter(rate5, size=med_win, mode="nearest")
    baseline = np.maximum(baseline, max(float(rate.mean()) * 0.4, 1e-3))

    ratio = rate5 / baseline
    count10 = uniform_filter1d(rate, 10, mode="constant") * 10.0
    expected10 = baseline * 10.0
    z = (count10 - expected10) / np.sqrt(expected10 + 1.0)

    spike_raw = np.minimum(
        np.clip(np.log2(np.maximum(ratio, 1e-6)) / np.log2(6.0), 0.0, 2.0),
        np.clip(z / 5.0, 0.0, 2.0),
    )
    burst_raw = np.minimum(
        np.clip(react5 / (baseline * 1.5), 0.0, 2.0),
        np.clip(react5 * CHAT_WINDOW_S / 4.0, 0.0, 2.0),
    )
    raw = 0.65 * spike_raw + 0.35 * burst_raw
    return robust_percentile_norm(raw, low_q=50.0, high_q=99.0, min_scale=0.5)


def signal_heatmap(
    heatmap: list[dict[str, Any]] | None,
    duration: float,
    n: int,
) -> np.ndarray | None:
    """Interpolate YouTube 'Most Replayed' heatmap onto a 1 Hz grid and normalize."""
    if not heatmap or len(heatmap) < 2:
        return None
    pts: list[tuple[float, float]] = []
    for item in heatmap:
        try:
            s = float(item["start_time"])
            e = float(item["end_time"])
            v = float(item["value"])
            pts.append(((s + e) * 0.5, v))
        except (KeyError, TypeError, ValueError):
            continue
    if len(pts) < 2:
        return None
    pts.sort(key=lambda p: p[0])
    xs = np.array([p[0] for p in pts], dtype=np.float64)
    ys = np.array([p[1] for p in pts], dtype=np.float64)
    grid = np.arange(n, dtype=np.float64) + 0.5
    interp = np.interp(grid, xs, ys)
    return robust_percentile_norm(interp, low_q=25.0, high_q=99.0, min_scale=0.35)


def compute_signals(
    duration: float,
    rms_db: np.ndarray,
    chat_messages: list[list[Any]] | None = None,
    heatmap: list[dict[str, Any]] | None = None,
    cache_path: Path | None = None,
) -> SignalBundle:
    """Compute, normalize, and fuse all available 1 Hz highlight signals."""
    n = max(1, math.ceil(duration))
    scores: dict[str, np.ndarray] = {
        "loudness": signal_loudness(rms_db, n),
    }

    chat_vec = signal_chat(chat_messages, n)
    if chat_vec is not None:
        scores["chat"] = chat_vec

    heat_vec = signal_heatmap(heatmap, duration, n)
    if heat_vec is not None:
        scores["heatmap"] = heat_vec

    active_total = sum(BASE_WEIGHTS[k] for k in scores)
    shares = {k: BASE_WEIGHTS[k] / active_total for k in scores}

    combined = np.zeros(n, dtype=np.float64)
    agreeing = np.zeros(n, dtype=np.int32)
    for k, vec in scores.items():
        combined += shares[k] * vec
        agreeing += (vec >= 0.5).astype(np.int32)

    if len(scores) >= 2:
        bonus = 1.0 + 0.15 * np.maximum(0, agreeing - 1)
        combined = combined * bonus

    combined = np.clip(combined, 0.0, 1.0)
    smoothed = gaussian_filter1d(combined, sigma=2.0) if n > 3 else combined.copy()
    smoothed = np.clip(smoothed, 0.0, 1.0)

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "combined": combined.astype(np.float32),
            "smoothed": smoothed.astype(np.float32),
            "agreeing": agreeing.astype(np.int16),
            "rms_db": np.asarray(rms_db, dtype=np.float32),
        }
        for k, vec in scores.items():
            payload[f"sig_{k}"] = vec.astype(np.float32)
        tmp = cache_path.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, **payload)
        tmp.replace(cache_path)

    return SignalBundle(
        duration=duration,
        n=n,
        scores=scores,
        combined=combined,
        smoothed=smoothed,
        agreeing=agreeing,
        weights=shares,
        rms_db=np.asarray(rms_db, dtype=np.float32),
    )
