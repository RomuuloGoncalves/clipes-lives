"""CLI entrypoint for clipes-lives."""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import cloud_llm
from .asr import transcribe_candidates, transcribe_fast_blocks
from .candidates import pick_candidate_peaks, pick_quota_candidates
from .eval import evaluate_clips
from .ingest import format_channel_credit, ingest, probe_metadata, sanitize_id
from .llm import (
    enrich_clips_with_llm,
    format_long_description,
    format_three_line_caption,
    long_paragraph_fallback,
    pick_hook_top,
    sanitize_hashtags,
)
from .models import CandidatePeak, SignalBundle, TopicSegment, Word
from .monitor import follow_live
from .notificar import _write_json_atomic, notify_discord
from .render import OUTRO_DURATION, clip_stem, generate_title, is_blur_bg_enabled, render_clip
from .signals import compute_rms_db, compute_signals, triage_audio_blocks
from .snap import select_clips
from .topics import score_topic_segments


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m clipes",
        description="Gera clipes verticais (1080x1920) com legendas de lives e VODs em CPU.",
    )
    p.add_argument("url", help="URL do vídeo/VOD/live (YouTube ou Twitch)")
    p.add_argument("--max-clips", type=int, default=8, help="Máximo de clipes a gerar (padrão: 8)")
    p.add_argument("--min-dur", type=float, default=25.0, help="Duração mínima do clipe em s (padrão: 25)")
    p.add_argument("--max-dur", type=float, default=65.0, help="Duração máxima do clipe em s (padrão: 65)")
    p.add_argument("--live-minutes", type=float, default=20.0,
                   help="Minutos a gravar se a URL for uma live ativa (padrão: 20)")
    p.add_argument("--ate-acabar", action="store_true",
                   help="Se a URL for uma live ativa, clipa em janelas contínuas até ela acabar")
    p.add_argument("--chat", action=argparse.BooleanOptionalAction, default=True,
                   help="Usar sinal de chat replay quando disponível (padrão: --chat)")
    p.add_argument("--whisper-model", default=None,
                   help="Modelo faster-whisper (padrão: CLIPES_WHISPER_MODEL ou 'small')")
    p.add_argument("--out-dir", default=None, help="Diretório raiz de saída (padrão: OUT_DIR ou ./out)")
    p.add_argument("--channel", default=None,
                   help="Canal para crédito na tela (sobrescreve o canal inferido)")
    p.add_argument("--reframe", action="store_true",
                   help="Ativa reenquadramento vertical opcional focado no rosto via YuNet (P2)")
    p.add_argument("--mode", choices=["peaks", "quota", "auto"], default="auto",
                   help="Seleção: picos, cota por hora, ou auto (padrão: auto)")
    p.add_argument("--min-per-hour", type=float, default=3.0,
                   help="Cota mínima de clipes por hora no modo quota/auto (padrão: 3)")
    p.add_argument("--asr-budget-ratio", type=float, default=0.30,
                   help="Fração máxima da duração transcrita na Passada 1 (padrão: 0.30)")
    p.add_argument("--no-llm", action="store_true",
                   help="Desativa o LLM local e usa apenas título, legenda e hashtags determinísticos")
    p.add_argument("--skip-render", action="store_true",
                   help="Executar análise e snapping sem baixar/renderizar MP4s")
    p.add_argument("--discord", action=argparse.BooleanOptionalAction, default=True,
                   help="Enviar prévias dos clipes no Discord ao concluir (padrão: --discord)")
    return p


def _write_run_readme(
    out_dir: Path,
    video_id: str,
    title: str,
    url: str,
    clips_data: list[dict[str, Any]],
    timings: dict[str, float],
    cloud_providers: list[str] | None = None,
) -> None:
    lines = [
        f"# {title}",
        "",
        f"- **Video ID:** `{video_id}`",
        f"- **URL:** {url}",
        f"- **Clipes gerados:** {len(clips_data)}",
    ]
    if cloud_providers:
        lines.extend(["", f"Texto da transcrição enviado a: {', '.join(cloud_providers)}"])
    lines.extend([
        "",
        "## Clipes",
        "",
        "| Arquivo | Parte | Início | Fim | Duração | Score | Sinais | Título |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ])
    for c in clips_data:
        sigs = ", ".join(c["signals"])
        p_idx = c.get("part_index", 1)
        p_tot = c.get("part_total", 1)
        gid = c.get("group_id", "")
        parte_col = f"{p_idx}/{p_tot}" + (f" (`{gid}`)" if gid else "")
        lines.append(
            f"| `{c['file']}` | {parte_col} | {c['start']:.1f}s | {c['end']:.1f}s | {c['duration']:.1f}s | "
            f"{c['score']:.3f} | {sigs} | {c['title']} |"
        )
    lines.extend([
        "",
        "## Tempos de CPU por etapa (s)",
        "",
        *[f"- `{k}`: {v:.2f}s" for k, v in timings.items()],
        "",
    ])
    if clips_data:
        lines.extend(["## Legendas e Hashtags", ""])
        for c in clips_data:
            nota = f"{c['llm_score']:.1f}/10" if c.get("llm_score") is not None else "n/a"
            src = c.get("title_source") or "-"
            tags = " ".join(c.get("hashtags") or [])
            lines.append(f"### `{c['file']}` — {c['title']}")
            lines.append(f"- **Fonte do título:** `{src}` | **Nota LLM:** {nota}")
            if c.get("llm_reason"):
                lines.append(f"- **Motivo LLM:** {c['llm_reason']}")
            if tags:
                lines.append(f"- **Hashtags:** {tags}")
            if c.get("caption"):
                lines.append("")
                lines.append("```text")
                lines.append(c["caption"])
                lines.append("```")
            lines.append("")
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def _merge_discord(old_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Preserve manifest and per-clip Discord IDs when rewriting clips.json."""
    if not old_path.is_file():
        return manifest
    try:
        old = json.loads(old_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        print("[discord] clips.json antigo ilegivel; ids nao preservados", flush=True)
        return manifest
    if not isinstance(old, dict):
        print("[discord] clips.json antigo ilegivel; ids nao preservados", flush=True)
        return manifest
    if isinstance(old.get("discord"), dict) and "discord" not in manifest:
        manifest["discord"] = dict(old["discord"])
    old_by_id = {
        str(c["clip_id"]): dict(c["discord"])
        for c in (old.get("clips") or [])
        if isinstance(c, dict) and c.get("clip_id") and isinstance(c.get("discord"), dict)
    }
    if old_by_id:
        for clip in manifest.get("clips") or []:
            if isinstance(clip, dict) and clip.get("clip_id") in old_by_id and "discord" not in clip:
                clip["discord"] = dict(old_by_id[str(clip["clip_id"])])
    return manifest


def _write_clips_manifest(out_dir: Path, manifest: dict[str, Any]) -> None:
    clips_path = out_dir / "clips.json"
    _merge_discord(clips_path, manifest)
    _write_json_atomic(clips_path, manifest)


def _timing_total(timings: dict[str, float]) -> float:
    skip = {"topics_s", "asr_pass1_s", "asr_pass2_s", "total_s"}
    return round(sum(v for k, v in timings.items() if k not in skip), 2)


def _resolve_layout_key(channel: str, video_id: str) -> str:
    """Derive a filesystem-safe facecam cache/override key from channel or video_id."""
    raw = (channel or "").strip()
    for prefix in ("https://", "http://", "www.twitch.tv/", "twitch.tv/"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
    raw = raw.lstrip("@").strip("/ ")
    if re.search(r"[A-Za-z0-9-]", raw):
        return sanitize_id(raw)
    return sanitize_id(video_id)


def _topics_from_pass1(segments: list[Word]) -> list[TopicSegment]:
    """Group Passada 1 segments into 25–180s topic units."""
    ordered = sorted((w for w in segments if w.clean), key=lambda w: (w.start, w.end))
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in ordered:
        if cur and (w.start - cur[-1].end > 2.0 or w.end - cur[0].start > 180.0):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    topics: list[TopicSegment] = []
    for group in groups:
        if group[-1].end - group[0].start < 25.0:
            continue
        topics.append(TopicSegment(
            start=group[0].start,
            end=group[-1].end,
            peak=round((group[0].start + group[-1].end) / 2.0, 2),
            sentences=[(w.start, w.end, w.clean) for w in group],
        ))
    return topics


def _select_candidates(
    args: argparse.Namespace,
    bundle: SignalBundle,
    audio_path: Path,
    cache_dir: Path,
    timings: dict[str, float],
) -> tuple[list[CandidatePeak], bool, int, bool]:
    """Peaks, quota, or auto. Auto adds the quota pass when strong peaks do not fill the quota.

    The fourth value is True when the quota pass (Passada 1) ran.
    """
    mode = args.mode
    quota_target = max(1, min(args.max_clips, math.ceil(args.min_per_hour * bundle.duration / 3600.0)))
    peaks = pick_candidate_peaks(bundle, max_clips=args.max_clips, min_separation=90)
    p90 = float(np.percentile(bundle.smoothed, 90.0)) if len(bundle.smoothed) else 0.0
    strong = [c for c in peaks if c.raw_score >= max(p90, 0.25)]  # absolute floor: a flat curve has no strong peaks
    if mode == "peaks" or (mode == "auto" and len(strong) >= quota_target):
        timings["asr_pass1_s"] = 0.0
        return peaks, False, quota_target, False

    blocks = triage_audio_blocks(bundle, budget_ratio=args.asr_budget_ratio)
    t_pass1 = time.perf_counter()
    fast_segments = transcribe_fast_blocks(
        audio_path, blocks, cache_dir, model_size=args.whisper_model,
    ) if blocks else []
    timings["asr_pass1_s"] = round(time.perf_counter() - t_pass1, 2)
    if sum(len(w.clean.split()) for w in fast_segments) < 30:  # music/silence: no speech, no clips
        return [], False, quota_target, True
    scored = score_topic_segments(_topics_from_pass1(fast_segments), bundle)
    chosen, floor_mode = pick_quota_candidates(
        bundle,
        scored,
        max_clips=args.max_clips,
        min_per_hour=args.min_per_hour,
    )
    return chosen, floor_mode, quota_target, True


def run_pipeline(args: argparse.Namespace) -> int:
    if args.min_dur <= 0 or args.max_dur <= args.min_dur:
        print("Erro: --min-dur deve ser > 0 e menor que --max-dur.", file=sys.stderr)
        return 2
    if args.min_per_hour < 0.0 or not (0.05 < args.asr_budget_ratio <= 1.0):
        print("Erro: --min-per-hour >= 0 e --asr-budget-ratio em (0.05, 1.0].", file=sys.stderr)
        return 2

    out_root = Path(args.out_dir or os.environ.get("OUT_DIR") or "out").expanduser().resolve()
    cloud_llm.set_out_dir(out_root)
    if getattr(args, "ate_acabar", False):
        meta = probe_metadata(args.url)
        if bool(meta.get("is_live")):
            out_root.mkdir(parents=True, exist_ok=True)
            print(f"[live] Live ativa detectada; acompanhando até acabar: {args.url}", flush=True)
            follow_kwargs: dict[str, Any] = {}
            sig_params = inspect.signature(follow_live).parameters
            has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig_params.values())
            if has_varkw or "max_dur" in sig_params:
                follow_kwargs["max_dur"] = args.max_dur
            if (has_varkw or "stream_id" in sig_params) and isinstance(meta, dict) and meta.get("id"):
                follow_kwargs["stream_id"] = str(meta["id"])
            if has_varkw or "coord" in sig_params:
                if os.environ.get("CLIPES_MONGO_URI", "").strip():
                    from .monitor import _connect_coord_for_follow

                    follow_kwargs["coord"] = _connect_coord_for_follow(out_root)
                else:
                    follow_kwargs["coord"] = None
            follow_live(args.url, out_root, **follow_kwargs)
            args._followed_live = True
            return 0

    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    ing = ingest(
        url=args.url,
        out_root=out_root,
        live_minutes=args.live_minutes,
        enable_chat=bool(args.chat),
    )
    args.output_dir = ing.out_dir
    timings["ingest_s"] = round(time.perf_counter() - t0, 2)

    raw_channel_arg = getattr(args, "channel", None)
    if raw_channel_arg:
        channel_credit = (
            format_channel_credit(str(raw_channel_arg), ing.url, ing.extractor)
            or str(raw_channel_arg).strip()
        )
    else:
        channel_credit = (
            str(getattr(ing, "channel", "") or "").strip()
            or format_channel_credit("", ing.url, ing.extractor)
        )
    reframe = bool(getattr(args, "reframe", False))

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
    candidates, floor_mode, quota_target, used_quota = _select_candidates(
        args, bundle, ing.audio_path, ing.cache_dir, timings,
    )
    timings["candidates_s"] = round(time.perf_counter() - t2 - timings.get("asr_pass1_s", 0.0), 2)  # pass 1 is counted in asr_s
    print(
        f"[candidates] mode={args.mode} selected {len(candidates)} "
        f"(quota_target={quota_target}, floor_mode={floor_mode})",
        flush=True,
    )
    quota_note = ""
    if used_quota and len(candidates) < quota_target:
        quota_note = (
            f"Cota {quota_target} não preenchida: {len(candidates)} candidatos acima do piso."
        )

    if not candidates:
        note = "Nenhum pico candidato encontrado no vídeo (ex.: áudio contínuo sem variação)."
        if quota_note:
            note = f"{note} {quota_note}"
        print(f"[clipes] {note}", flush=True)
        timings["total_s"] = _timing_total(timings)
        manifest = {
            "video_id": ing.video_id,
            "title": ing.title,
            "url": ing.url,
            "is_live": ing.is_live,
            "duration": round(ing.duration, 2),
            "language": None,
            "active_signals": list(bundle.scores.keys()),
            "detection_mode": args.mode,
            "floor_mode": floor_mode,
            "quota_target": quota_target,
            "timings": timings,
            "eval": evaluate_clips([], [], min_dur=args.min_dur, max_dur=args.max_dur),
            "note": note,
            "clips": [],
        }
        _write_clips_manifest(ing.out_dir, manifest)
        _write_run_readme(ing.out_dir, ing.video_id, ing.title, ing.url, [], timings)
        return 0

    t3 = time.perf_counter()
    words, lang = transcribe_candidates(
        audio_path=ing.audio_path,
        candidates=candidates,
        cache_dir=ing.cache_dir,
        model_size=args.whisper_model,
        beam_size=1 if used_quota else 5,
    )
    timings["asr_pass2_s"] = round(time.perf_counter() - t3, 2)
    timings["asr_s"] = round(timings.get("asr_pass1_s", 0.0) + timings["asr_pass2_s"], 2)

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
    timings["topics_s"] = timings["snap_s"]
    print(f"[snap] Final valid clips: {len(clips)}/{len(candidates)}", flush=True)

    if not clips:
        note = "Nenhum candidato formou clipe válido dentro das restrições de borda/duração."
        if quota_note:
            note = f"{note} {quota_note}"
        print(f"[clipes] {note}", flush=True)
        timings["total_s"] = _timing_total(timings)
        manifest = {
            "video_id": ing.video_id,
            "title": ing.title,
            "url": ing.url,
            "is_live": ing.is_live,
            "duration": round(ing.duration, 2),
            "language": lang,
            "active_signals": list(bundle.scores.keys()),
            "detection_mode": args.mode,
            "floor_mode": floor_mode,
            "quota_target": quota_target,
            "timings": timings,
            "eval": evaluate_clips([], words, min_dur=args.min_dur, max_dur=args.max_dur),
            "note": note,
            "clips": [],
        }
        _write_clips_manifest(ing.out_dir, manifest)
        _write_run_readme(ing.out_dir, ing.video_id, ing.title, ing.url, [], timings)
        return 0

    t_llm = time.perf_counter()
    cloud_llm.reset_round()
    try:
        enrich_clips_with_llm(clips, words, ing, enabled=not getattr(args, "no_llm", False))
    except Exception as exc:
        print(f"[llm] Falha inesperada ({exc}); usando fallback determinístico.", flush=True)
    timings["llm_s"] = round(time.perf_counter() - t_llm, 2)

    t5 = time.perf_counter()
    default_layout = "blurred_16x9" if is_blur_bg_enabled() else "black_bars_16x9"
    safe_video_id = sanitize_id(ing.video_id)
    effective_channel = channel_credit or getattr(ing, "channel", "") or ing.title
    layout_key = _resolve_layout_key(
        channel_credit or str(getattr(ing, "channel", "") or "").strip(),
        ing.video_id,
    )
    group_totals: dict[str, float] = {}
    for c in clips:
        if c.group_id:
            group_totals[c.group_id] = round(group_totals.get(c.group_id, 0.0) + c.duration, 2)
    clips_payload: list[dict[str, Any]] = []
    for idx, clip in enumerate(clips, 1):
        part_index = int(getattr(clip, "part_index", 1) or 1)
        part_total = int(getattr(clip, "part_total", 1) or 1)
        if not clip.title:
            clip.title = generate_title(clip, words)
            if not getattr(clip, "title_source", ""):
                clip.title_source = "heuristic_fallback"
        if not getattr(clip, "hashtags", None):
            clip.hashtags = sanitize_hashtags([], channel=channel_credit, title=f"{ing.title} {clip.title}")
        if not getattr(clip, "caption", ""):
            clip.caption = format_three_line_caption(
                clip.title,
                effective_channel,
                ing.url,
                clip.hashtags,
                part_index=part_index,
                part_total=part_total,
            )
        if not getattr(clip, "hook_top", "") and not getattr(clip, "description_source", ""):
            clip.hook_top = pick_hook_top(
                "", clip.title, clip.text, f"{ing.title} {effective_channel}".strip()
            )
        if not getattr(clip, "description_long", ""):
            paragraph = long_paragraph_fallback(clip)
            if paragraph:
                clip.description_long = format_long_description(
                    clip.title,
                    paragraph,
                    effective_channel,
                    ing.url,
                    clip.hashtags,
                    part_index=part_index,
                    part_total=part_total,
                )
                if not getattr(clip, "description_source", ""):
                    clip.description_source = "fallback"
            else:
                clip.description_source = ""
        if args.skip_render:
            clip.output_file = f"{clip_stem(idx, clip)}.mp4"
            clip_layout = default_layout
            clip_layout_fallback = False
            clip_facecam = None
        else:
            print(
                f"[render] Clip {idx}/{len(clips)}: {clip.start:.1f}s-{clip.end:.1f}s "
                f"({clip.duration:.1f}s, score={clip.score:.3f}) -> {clip.title}",
                flush=True,
            )
            render_kwargs: dict[str, Any] = {}
            sig_params = inspect.signature(render_clip).parameters
            has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig_params.values())
            if has_varkw or "channel_credit" in sig_params:
                render_kwargs["channel_credit"] = channel_credit
            if has_varkw or "reframe" in sig_params:
                render_kwargs["reframe"] = reframe
            if has_varkw or "layout_key" in sig_params:
                render_kwargs["layout_key"] = layout_key
            out_path = render_clip(
                clip=clip,
                idx=idx,
                words=words,
                url=ing.url,
                out_dir=ing.out_dir,
                cache_dir=ing.cache_dir,
                live_video_path=ing.video_path,
                **render_kwargs,
            )
            clip.output_file = out_path.name
            clip_layout = str(getattr(clip, "layout", default_layout))
            clip_layout_fallback = bool(getattr(clip, "layout_fallback", False))
            clip_facecam = getattr(clip, "facecam", None)

        if clip.caption:
            txt_path = ing.out_dir / Path(clip.output_file).with_suffix(".txt")
            txt_path.write_text(clip.caption, encoding="utf-8")
        if getattr(clip, "description_long", ""):
            desc_path = ing.out_dir / Path(clip.output_file).with_suffix(".descricao.txt")
            desc_path.write_text(clip.description_long, encoding="utf-8")

        clip_entry: dict[str, Any] = {
            "index": idx,
            "clip_id": f"{safe_video_id}_c{idx:02d}",
            "file": clip.output_file,
            "title": clip.title,
            "title_source": str(getattr(clip, "title_source", "") or "heuristic_fallback"),
            "hook_top": str(getattr(clip, "hook_top", "") or ""),
            "caption": clip.caption,
            "description_long": str(getattr(clip, "description_long", "") or ""),
            "description_source": str(getattr(clip, "description_source", "") or ""),
            "hashtags": list(clip.hashtags),
            "llm_score": getattr(clip, "llm_score", None),
            "llm_reason": str(getattr(clip, "llm_reason", "") or ""),
            "score": clip.score,
            "signals": clip.active_signals,
            "signal_scores": clip.signal_scores,
            "start": clip.start,
            "end": clip.end,
            "duration": clip.duration,
            "rendered_duration": round(clip.duration + OUTRO_DURATION, 3),
            "channel_credit": channel_credit,
            "layout": clip_layout,
            "layout_fallback": clip_layout_fallback,
            "facecam": clip_facecam,
            "peak": clip.peak,
            "peak_ratio": round(clip.peak_ratio, 3),
            "edge_mode": clip.edge_mode,
            "start_edge_type": clip.start_edge_type,
            "end_edge_type": clip.end_edge_type,
            "part_index": clip.part_index,
            "part_total": clip.part_total,
            "group_id": clip.group_id,
            "topic_duration": group_totals.get(clip.group_id, clip.duration),
            "transcript": clip.text,
            "cabe_discord": bool(getattr(clip, "cabe_discord", True)),
        }
        clips_payload.append(clip_entry)
    timings["render_s"] = round(time.perf_counter() - t5, 2)
    timings["total_s"] = _timing_total(timings)

    eval_metrics = evaluate_clips(clips, words, min_dur=args.min_dur, max_dur=args.max_dur)

    manifest = {
        "video_id": ing.video_id,
        "title": ing.title,
        "url": ing.url,
        "channel_credit": channel_credit,
        "is_live": ing.is_live,
        "duration": round(ing.duration, 2),
        "language": lang,
        "active_signals": list(bundle.scores.keys()),
        "detection_mode": args.mode,
        "floor_mode": floor_mode,
        "quota_target": quota_target,
        "timings": timings,
        "eval": eval_metrics,
        "clips": clips_payload,
    }
    if quota_note:
        manifest["note"] = quota_note
    _write_clips_manifest(ing.out_dir, manifest)
    used_providers = [] if getattr(args, "no_llm", False) else cloud_llm.providers_used()
    _write_run_readme(
        ing.out_dir, ing.video_id, ing.title, ing.url, clips_payload, timings,
        cloud_providers=used_providers,
    )
    print(f"[done] Output written to {ing.out_dir} in {timings['total_s']:.1f}s", flush=True)
    return 0


def _notify_cli_discord(args: argparse.Namespace) -> None:
    from .publicacao import _resolve_policy

    out_root = Path(args.out_dir or os.environ.get("OUT_DIR") or "out").expanduser().resolve()
    policy_file = (os.environ.get("CLIPES_POLICY_FILE") or "").strip() or None
    policy = _resolve_policy(None, policy_file, out_root)
    notify_discord(Path(args.output_dir), policy=policy)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.output_dir = None
    args._followed_live = False
    rc = run_pipeline(args)
    if (
        rc == 0
        and getattr(args, "discord", True)
        and not getattr(args, "_followed_live", False)
        and getattr(args, "output_dir", None) is not None
        and Path(args.output_dir).is_dir()
    ):
        _notify_cli_discord(args)
    return rc
