"""Peak selection on the 1 Hz smoothed hype curve."""

from __future__ import annotations

from scipy.signal import find_peaks
import numpy as np

from .models import CandidatePeak, SignalBundle


def pick_candidate_peaks(
    bundle: SignalBundle,
    max_clips: int = 8,
    min_separation: int = 90,
    window_half: float = 45.0,
    signal_fire_threshold: float = 0.35,
) -> list[CandidatePeak]:
    """Pick top N*2 separated peaks from the smoothed score curve."""
    curve = bundle.smoothed
    n = len(curve)
    if n < 3 or float(curve.max()) <= 0.0:
        return []

    floor = float(np.median(curve))
    peak_max = float(curve.max())
    threshold = floor + 0.08 * (peak_max - floor)

    raw_peaks, _ = find_peaks(
        np.concatenate([[0.0], curve, [0.0]]),
        height=threshold,
        distance=max(1, min_separation),
        prominence=0.01,
    )
    peak_indices = sorted((int(p) - 1 for p in raw_peaks), key=lambda idx: -float(curve[idx]))

    limit = max(1, max_clips * 2)
    chosen: list[CandidatePeak] = []
    for p in peak_indices:
        if len(chosen) >= limit:
            break
        if any(abs(p - c.peak) < min_separation for c in chosen):
            continue

        lo = max(0, p - 5)
        hi = min(n, p + 6)
        sig_peaks = {
            name: round(float(vec[lo:hi].max()), 3)
            for name, vec in bundle.scores.items()
        }
        active = [
            name for name, val in sorted(sig_peaks.items(), key=lambda kv: -kv[1])
            if val >= signal_fire_threshold
        ]
        if not active and sig_peaks:
            active = [max(sig_peaks, key=lambda k: sig_peaks[k])]

        start_win = max(0.0, float(p) - window_half)
        end_win = min(bundle.duration, float(p) + window_half)
        chosen.append(CandidatePeak(
            peak=float(p),
            raw_score=round(float(curve[p]), 4),
            start_win=round(start_win, 2),
            end_win=round(end_win, 2),
            active_signals=active,
            signal_peaks=sig_peaks,
        ))

    return chosen
