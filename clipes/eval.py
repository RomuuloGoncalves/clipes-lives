"""Objective evaluation metrics for boundary quality and peak recall."""

from __future__ import annotations

from typing import Any

from .models import SnappedClip, Word


def cuts_mid_word(t: float, words: list[Word], tol: float = 0.005) -> bool:
    return any(w.start + tol < t < w.end - tol for w in words)


def evaluate_clips(
    clips: list[SnappedClip],
    words: list[Word],
    min_dur: float = 25.0,
    max_dur: float = 55.0,
    ground_truth_peaks: list[float] | None = None,
    k: int = 5,
) -> dict[str, Any]:
    """Compute MidWordCutRate, SentenceEdgeRate, duration/peak compliance, and Recall@K."""
    if not clips:
        out: dict[str, Any] = {
            "count": 0,
            "mid_word_cut_rate": 0.0,
            "sentence_edge_rate": 0.0,
            "duration_ok_rate": 0.0,
            "peak_position_ok_rate": 0.0,
        }
        if ground_truth_peaks is not None:
            out[f"recall_at_{k}"] = 0.0 if ground_truth_peaks else 1.0
        return out

    mid_word_cuts = 0
    edge_hits = 0
    edge_total = 0
    dur_ok = 0
    peak_ok = 0

    for c in clips:
        if cuts_mid_word(c.start, words) or cuts_mid_word(c.end, words):
            mid_word_cuts += 1

        if c.edge_mode == "speech":
            edge_total += 2
            if c.start_edge_type in ("sentence", "pause"):
                edge_hits += 1
            if c.end_edge_type in ("sentence", "pause"):
                edge_hits += 1

        d = c.end - c.start
        if min_dur - 1e-6 <= d <= max_dur + 1e-6:
            dur_ok += 1

        r = (c.peak - c.start) / d if d > 0 else 0.0
        if 0.35 - 1e-6 <= r <= 0.75 + 1e-6:
            peak_ok += 1

    n = len(clips)
    metrics: dict[str, Any] = {
        "count": n,
        "mid_word_cut_rate": round(mid_word_cuts / n, 4),
        "sentence_edge_rate": round(edge_hits / edge_total, 4) if edge_total else 1.0,
        "duration_ok_rate": round(dur_ok / n, 4),
        "peak_position_ok_rate": round(peak_ok / n, 4),
    }

    if ground_truth_peaks is not None:
        if not ground_truth_peaks:
            metrics[f"recall_at_{k}"] = 1.0
        else:
            top_clips = clips[:k]
            hits = sum(
                1 for gt in ground_truth_peaks
                if any(c.start <= gt <= c.end for c in top_clips)
            )
            metrics[f"recall_at_{k}"] = round(hits / len(ground_truth_peaks), 4)

    return metrics
