"""CLI entrypoint for clipes-lives."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .asr import transcribe_candidates
from .candidates import pick_candidate_peaks
from .eval import evaluate_clips
from .ingest import ingest
from .render import generate_title, render_clip
from .signals import compute_rms_db, compute_signals
from .snap import select_clips


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m clipes",
        description="Gera clipes verticais (1080x1920) com legendas de lives e VODs em CPU.",
    )
    p.add_argument("url", help="URL do vídeo/VOD/live (YouTube ou Twitch)")
    p.add_argument("--max-clips", type=int, default=8, help="Máximo de clipes a gerar (padrão: 8)")
    p.add_argument("--min-dur", type=float, default=25.0, help="Duração mínima do clipe em s (padrão: 25)")
    p.add_argument("--max-dur", type=float, default=55.0, help="Duração máxima do clipe em s (padrão: 55)")
    p.add_argument("--live-minutes", type=float, default=20.0,
                   help="Minutos a gravar se a URL for uma live ativa (padrão: 20)")
    p.add_argument("--chat", action=argparse.BooleanOptionalAction, default=True,
                   help="Usar sinal de chat replay quando disponível (padrão: --chat)")
    p.add_argument("--whisper-model", default=None,
                   help="Modelo faster-whisper (padrão: CLIPES_WHISPER_MODEL ou 'small')")
    p.add_argument("--out-dir", default=None, help="Diretório raiz de saída (padrão: OUT_DIR ou ./out)")
    p.add_argument("--skip-render", action="store_true",
                   help="Executar análise e snapping sem baixar/renderizar MP4s")
    return p


def _write_run_readme(
    out_dir: Path,
    video_id: str,
    title: str,
    url: str,
    clips_data: list[dict[str, Any]],
    timings: dict[str, float],
) -> None:
    lines = [
        f"# {title}",
        "",
        f"- **Video ID:** `{video_id}`",
        f"- **URL:** {url}",
        f"- **Clipes gerados:** {len(clips_data)}",
        "",
        "## Clipes",
        "",
        "| Arquivo | Início | Fim | Duração | Score | Sinais | Título |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for c in clips_data:
        sigs = ", ".join(c["signals"])
        lines.append(
            f"| `{c['file']}` | {c['start']:.1f}s | {c['end']:.1f}s | {c['duration']:.1f}s | "
            f"{c['score']:.3f} | {sigs} | {c['title']} |"
        )
    lines.extend([
        "",
        "## Tempos de CPU por etapa (s)",
        "",
        *[f"- `{k}`: {v:.2f}s" for k, v in timings.items()],
        "",
    ])
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def run_pipeline(args: argparse.Namespace) -> int:
    if args.min_dur <= 0 or args.max_dur <= args.min_dur:
        print("Erro: --min-dur deve ser > 0 e menor que --max-dur.", file=sys.stderr)
        return 2

    out_root = Path(args.out_dir or os.environ.get("OUT_DIR") or "out").expanduser().resolve()
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    ing = ingest(
        url=args.url,
        out_root=out_root,
        live_minutes=args.live_minutes,
        enable_chat=bool(args.chat),
    )
    timings["ingest_s"] = round(time.perf_counter() - t0, 2)

    t1 = time.perf_counter()
    rms_db = compute_rms_db(ing.audio_path, duration=ing.duration)
    bundle = compute_signals(
        duration=ing.duration,
        rms_db=rms_db,
        chat_messages=ing.chat_messages,
        heatmap=ing.heatmap,
        cache_path=ing.cache_dir / "signals.npz",
    )
    timings["signals_s"] = round(time.perf_counter() - t1, 2)
    print(f"[signals] Active signals: {list(bundle.scores.keys())} (weights={bundle.weights})", flush=True)

    t2 = time.perf_counter()
    candidates = pick_candidate_peaks(bundle, max_clips=args.max_clips, min_separation=90)
    timings["candidates_s"] = round(time.perf_counter() - t2, 2)
    print(f"[candidates] Selected {len(candidates)} candidate peaks", flush=True)

    if not candidates:
        print("[clipes] Nenhum pico candidato encontrado no vídeo.", flush=True)
        return 1

    t3 = time.perf_counter()
    words, lang = transcribe_candidates(
        audio_path=ing.audio_path,
        candidates=candidates,
        cache_dir=ing.cache_dir,
        model_size=args.whisper_model,
    )
    timings["asr_s"] = round(time.perf_counter() - t3, 2)

    t4 = time.perf_counter()
    clips = select_clips(
        candidates=candidates,
        words=words,
        rms_db=rms_db,
        duration=ing.duration,
        max_clips=args.max_clips,
        min_dur=args.min_dur,
        max_dur=args.max_dur,
        bundle=bundle,
    )
    timings["snap_s"] = round(time.perf_counter() - t4, 2)
    print(f"[snap] Final valid clips: {len(clips)}/{len(candidates)}", flush=True)

    if not clips:
        print("[clipes] Nenhum candidato formou clipe válido dentro das restrições de borda/duração.", flush=True)
        return 1

    t5 = time.perf_counter()
    clips_payload: list[dict[str, Any]] = []
    for idx, clip in enumerate(clips, 1):
        clip.title = generate_title(clip, words)
        if args.skip_render:
            clip.output_file = f"clip_{idx:02d}.mp4"
        else:
            print(
                f"[render] Clip {idx}/{len(clips)}: {clip.start:.1f}s-{clip.end:.1f}s "
                f"({clip.duration:.1f}s, score={clip.score:.3f}) -> {clip.title}",
                flush=True,
            )
            out_path = render_clip(
                clip=clip,
                idx=idx,
                words=words,
                url=ing.url,
                out_dir=ing.out_dir,
                cache_dir=ing.cache_dir,
                live_video_path=ing.video_path,
            )
            clip.output_file = out_path.name

        clips_payload.append({
            "index": idx,
            "file": clip.output_file,
            "title": clip.title,
            "score": clip.score,
            "signals": clip.active_signals,
            "signal_scores": clip.signal_scores,
            "start": clip.start,
            "end": clip.end,
            "duration": clip.duration,
            "peak": clip.peak,
            "peak_ratio": round(clip.peak_ratio, 3),
            "edge_mode": clip.edge_mode,
            "start_edge_type": clip.start_edge_type,
            "end_edge_type": clip.end_edge_type,
            "transcript": clip.text,
        })
    timings["render_s"] = round(time.perf_counter() - t5, 2)
    timings["total_s"] = round(sum(timings.values()), 2)

    eval_metrics = evaluate_clips(clips, words, min_dur=args.min_dur, max_dur=args.max_dur)

    manifest = {
        "video_id": ing.video_id,
        "title": ing.title,
        "url": ing.url,
        "is_live": ing.is_live,
        "duration": round(ing.duration, 2),
        "language": lang,
        "active_signals": list(bundle.scores.keys()),
        "timings": timings,
        "eval": eval_metrics,
        "clips": clips_payload,
    }
    (ing.out_dir / "clips.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_run_readme(ing.out_dir, ing.video_id, ing.title, ing.url, clips_payload, timings)
    print(f"[done] Output written to {ing.out_dir} in {timings['total_s']:.1f}s", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_pipeline(args)
