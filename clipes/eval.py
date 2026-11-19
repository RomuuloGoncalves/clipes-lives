"""Objective evaluation metrics for boundary quality and peak recall."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
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


def speech_gate(
    n_words: int,
    duration: float,
    min_wps: float = 0.4,
    min_words: int = 15,
) -> tuple[bool, float]:
    """Evaluate speech gate: passes iff duration > 0, n_words >= min_words and wps >= min_wps."""
    if duration <= 0:
        return False, 0.0
    wps = n_words / float(duration)
    return (n_words >= min_words and wps >= min_wps), wps


def count_speech_words(
    words: list[Word],
    start: float,
    end: float,
    nsp_max: float = 0.6,
) -> int:
    """Count words whose center lies in [start, end] and no_speech_prob <= nsp_max."""
    total = 0
    for w in words:
        center = (float(w.start) + float(w.end)) * 0.5
        if start <= center <= end:
            nsp = float(getattr(w, "no_speech_prob", 0.0) or 0.0)
            if nsp <= nsp_max:
                if hasattr(w, "text") and not str(w.text).strip():
                    continue
                total += 1
    return total


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


def evaluate_speech_gate(
    out_dir: Path | str,
    min_wps: float = 0.4,
    min_words: int = 15,
) -> dict[str, Any]:
    """Reaplica o portao de fala offline (somente leitura) sobre */clips.json sob out_dir."""
    root = Path(out_dir)
    total = 0
    discarded: list[dict[str, Any]] = []
    discarded_wps_ge_1 = 0
    lives_total = 0
    lives_zeroed = 0
    c01_discarded = 0
    skipped = 0

    hours_by_origin = {"live": 0.0, "vod": 0.0}
    before_by_origin = {"live": 0, "vod": 0}
    after_by_origin = {"live": 0, "vod": 0}

    entries = sorted(root.iterdir(), key=lambda p: p.name) if root.is_dir() else []
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        clips_path = entry / "clips.json"
        if not clips_path.is_file():
            continue
        try:
            data = json.loads(clips_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            print(f"[eval] aviso: {clips_path} ilegivel ({exc}); pulando", file=sys.stderr, flush=True)
            skipped += 1
            continue
        if not isinstance(data, dict):
            print(f"[eval] aviso: {clips_path} formato invalido; pulando", file=sys.stderr, flush=True)
            skipped += 1
            continue

        lives_total += 1
        origin = "live" if bool(data.get("is_live")) else "vod"
        manifest_dur = float(data.get("duration") or 0.0)
        if manifest_dur > 0:
            hours_by_origin[origin] += manifest_dur / 3600.0

        raw_clips = data.get("clips") or []
        valid_in_live = 0
        kept_in_live = 0
        for clip in raw_clips:
            if not isinstance(clip, dict):
                continue
            valid_in_live += 1
            total += 1
            before_by_origin[origin] += 1

            transcript = str(clip.get("transcript") or "")
            n_words = len(transcript.split())
            dur = float(clip.get("duration") or 0.0)
            ok, wps = speech_gate(n_words, dur, min_wps=min_wps, min_words=min_words)
            if ok:
                kept_in_live += 1
                after_by_origin[origin] += 1
            else:
                clip_id = str(clip.get("clip_id") or clip.get("file") or f"{entry.name}_c{valid_in_live:02d}")
                is_c01 = clip_id.endswith("_c01") or int(clip.get("index") or 0) == 1
                if is_c01:
                    c01_discarded += 1
                if wps >= 1.0:
                    discarded_wps_ge_1 += 1
                discarded.append({
                    "clip_id": clip_id,
                    "wps": round(wps, 3),
                    "palavras": n_words,
                    "duration": round(dur, 2),
                    "is_c01": is_c01,
                })

        if valid_in_live > 0 and kept_in_live == 0:
            lives_zeroed += 1

    by_origin: dict[str, dict[str, Any]] = {}
    for origin in ("live", "vod"):
        hrs = hours_by_origin[origin]
        bef = before_by_origin[origin]
        aft = after_by_origin[origin]
        by_origin[origin] = {
            "hours": round(hrs, 3),
            "before": bef,
            "after": aft,
            "clips_per_hour_before": round(bef / hrs, 2) if hrs > 0 else 0.0,
            "clips_per_hour_after": round(aft / hrs, 2) if hrs > 0 else 0.0,
        }

    pct = round(100.0 * len(discarded) / total, 1) if total else 0.0
    return {
        "out_dir": str(root),
        "min_wps": min_wps,
        "min_words": min_words,
        "total": total,
        "kept": total - len(discarded),
        "discarded_count": len(discarded),
        "discarded_pct": pct,
        "discarded_wps_ge_1": discarded_wps_ge_1,
        "lives_total": lives_total,
        "lives_zeroed": lives_zeroed,
        "c01_discarded": c01_discarded,
        "skipped_files": skipped,
        "by_origin": by_origin,
        "discarded": discarded,
    }


def _format_speech_gate_report(report: dict[str, Any]) -> str:
    lines = [
        f"Portao de fala (min_wps={report['min_wps']}, min_palavras={report['min_words']}): {report['out_dir']}",
        f"Total de clipes: {report['total']}",
        f"Descartados: {report['discarded_count']}/{report['total']} ({report['discarded_pct']:.1f}%)",
        f"Descartados com wps >= 1: {report['discarded_wps_ge_1']}",
        f"Lives com 0 clipes (zeradas): {report['lives_zeroed']}/{report['lives_total']}",
        f"c01 descartados: {report['c01_discarded']}",
        "Clipes/h por origem:",
    ]
    for origin, stats in report["by_origin"].items():
        if stats["before"] or stats["hours"]:
            lines.append(
                f"  - {origin}: {stats['before']} -> {stats['after']} clipes em {stats['hours']:.2f}h "
                f"({stats['clips_per_hour_before']:.2f}/h -> {stats['clips_per_hour_after']:.2f}/h)"
            )
    if report["discarded"]:
        lines.append("Descartados (id, wps, palavras):")
        for item in report["discarded"]:
            lines.append(f"  - {item['clip_id']}: wps={item['wps']:.3f}, palavras={item['palavras']}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m clipes.eval")
    sub = p.add_subparsers(dest="cmd", required=True)
    gp = sub.add_parser("portao", help="Avalia o portao de fala offline sobre */clips.json")
    gp.add_argument("out_dir", help="Diretorio raiz contendo subpastas com clips.json")
    gp.add_argument("--min-wps", type=float, default=0.4, help="WPS minimo (padrao: 0.4)")
    gp.add_argument("--min-palavras", type=int, default=15, help="Minimo de palavras (padrao: 15)")
    gp.add_argument("--json", action="store_true", help="Imprime o relatorio em JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "portao":
        report = evaluate_speech_gate(args.out_dir, min_wps=args.min_wps, min_words=args.min_palavras)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(_format_speech_gate_report(report))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
