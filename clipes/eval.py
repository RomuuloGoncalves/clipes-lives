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
    max_dur: float = 65.0,
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

        if c.edge_mode in ("speech", "topic"):
            edge_total += 2
            if c.start_edge_type in ("sentence", "pause"):
                edge_hits += 1
            if c.end_edge_type in ("sentence", "pause"):
                edge_hits += 1

        d = c.end - c.start
        dur_limit = 58.0 if c.part_total > 1 else max_dur
        if min_dur - 1e-6 <= d <= dur_limit + 1e-6:
            dur_ok += 1

        r = (c.peak - c.start) / d if d > 0 else 0.0
        if c.edge_mode == "topic" or (0.35 - 1e-6 <= r <= 0.75 + 1e-6):
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


def _span(item: Any) -> tuple[float, float]:
    if isinstance(item, dict):
        return float(item["start"]), float(item["end"])
    start = getattr(item, "topic_start", None)
    end = getattr(item, "topic_end", None)
    if start is None or end is None:
        start = getattr(item, "start", None)
        end = getattr(item, "end", None)
    if start is None or end is None:
        start, end = item.start_win, item.end_win
    return float(start), float(end)


def _iou(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = (a1 - a0) + (b1 - b0) - inter
    return inter / union if union > 0 else 0.0


def _matches(item: Any, label: dict[str, Any], iou_min: float) -> bool:
    s, e = _span(item)
    ls, le = float(label["start"]), float(label["end"])
    return (s <= (ls + le) / 2.0 <= e) or _iou(s, e, ls, le) >= iou_min


def evaluate_quota_calibration(
    selected: list[Any],
    labels: list[dict[str, Any]],
    k: int = 8,
    iou_min: float = 0.5,
) -> dict[str, float]:
    """precision@k / recall@k: a pick hits a positive label by center inclusion or IoU >= iou_min."""
    positives = [lb for lb in labels if int(lb.get("label", 0)) == 1]
    top = list(selected)[:k]
    hit_sel = sum(1 for item in top if any(_matches(item, lb, iou_min) for lb in positives))
    hit_lbl = sum(1 for lb in positives if any(_matches(item, lb, iou_min) for item in top))
    precision = hit_sel / len(top) if top else 0.0
    recall = hit_lbl / len(positives) if positives else 0.0
    return {"precision_at_k": round(precision, 4), "recall_at_k": round(recall, 4)}
