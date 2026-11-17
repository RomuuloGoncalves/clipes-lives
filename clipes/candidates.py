"""Peak selection on the 1 Hz smoothed hype curve, plus quota/floor selection over scored topics."""

from __future__ import annotations

import math

from scipy.signal import find_peaks
import numpy as np

from .models import CandidatePeak, SignalBundle, TopicSegment
from .topics import dedup_segments_text


def pick_candidate_peaks(
    bundle: SignalBundle,
    max_clips: int = 8,
    min_separation: int = 90,
    window_half: float = 45.0,
    pre_window: float = 150.0,
    post_window: float = 60.0,
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

        if len(chosen) < max_clips:
            start_win = max(0.0, float(p) - pre_window)
            end_win = min(bundle.duration, float(p) + post_window)
        else:
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


def pick_quota_candidates(
    bundle: SignalBundle,
    topic_segments: list[TopicSegment],
    max_clips: int = 8,
    min_per_hour: float = 3.0,
    min_separation: float = 120.0,
    floor_percentile: float = 75.0,
    min_abs_score: float = 0.25,
    signal_fire_threshold: float = 0.35,
) -> tuple[list[CandidatePeak], bool]:
    """Fill K = clamp(ceil(min_per_hour * hours), 1, max_clips) with strong peaks + scored topics.

    Topics need word_count >= 30 and score >= P75 of the video (relaxed P65 -> P55 in floor mode,
    never below min_abs_score). The first pass caps each video third at ceil(K / 2).
    Returns (candidates, floor_mode).
    """
    duration = float(bundle.duration)
    k = max(1, min(max_clips, math.ceil(min_per_hour * duration / 3600.0)))
    third_cap = math.ceil(k / 2)
    third = duration / 3.0

    p90 = float(np.percentile(bundle.smoothed, 90.0)) if len(bundle.smoothed) else 0.0
    strong = [
        c for c in pick_candidate_peaks(bundle, max_clips=max_clips)
        if c.raw_score >= max(p90, min_abs_score)
    ]
    all_scores = [float(getattr(s, "score", 0.0)) for s in topic_segments]
    eligible = [
        s for s in topic_segments
        if getattr(s, "word_count", 0) >= 30 and float(getattr(s, "score", 0.0)) >= min_abs_score
    ]
    if not strong and not eligible:
        return [], False
    eligible = dedup_segments_text(eligible)

    def _to_candidate(seg: TopicSegment) -> CandidatePeak:
        i0 = max(0, int(seg.start))
        i1 = min(bundle.n, max(i0 + 1, int(math.ceil(seg.end))))
        sig_peaks = {name: round(float(vec[i0:i1].max()), 3) for name, vec in bundle.scores.items()}
        active = [n for n, v in sorted(sig_peaks.items(), key=lambda kv: -kv[1]) if v >= signal_fire_threshold]
        if not active and sig_peaks:
            active = [max(sig_peaks, key=lambda n: sig_peaks[n])]
        return CandidatePeak(
            peak=round((seg.start + seg.end) * 0.5, 2),
            raw_score=round(float(getattr(seg, "score", 0.0)), 4),
            start_win=round(max(0.0, seg.start - 2.0), 2),
            end_win=round(min(duration, seg.end + 2.0), 2),
            active_signals=active,
            signal_peaks=sig_peaks,
            topic_start=seg.start,
            topic_end=seg.end,
        )

    def _third(t: float) -> int:
        return min(2, int(t // third)) if third > 0 else 0

    chosen: list[CandidatePeak] = []

    def _fill(pool: list[CandidatePeak], capped: bool) -> None:
        for c in pool:
            if len(chosen) >= k:
                return
            if any(c is x or abs(c.peak - x.peak) < min_separation for x in chosen):
                continue
            if capped and sum(1 for x in chosen if _third(x.peak) == _third(c.peak)) >= third_cap:
                continue
            chosen.append(c)

    seg_cands = [(float(getattr(s, "score", 0.0)), _to_candidate(s)) for s in eligible]
    levels = [floor_percentile] + [p for p in (65.0, 55.0) if p < floor_percentile]
    floor_mode = False
    pool: list[CandidatePeak] = []
    for i, level in enumerate(levels):
        if i > 0:
            floor_mode = True
        thr = max(float(np.percentile(all_scores, level)), min_abs_score) if all_scores else math.inf
        pool = strong + [c for score, c in seg_cands if score >= thr]
        _fill(pool, capped=True)
        if len(chosen) >= k:
            break
    else:
        # Third cap only binds the first pass; fill what is left at the loosest level.
        _fill(pool, capped=False)

    return chosen, floor_mode
