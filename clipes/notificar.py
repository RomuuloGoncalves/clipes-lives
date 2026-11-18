"""Discord preview notification for generated clips (monitor and CLI)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .monitor import ChannelPolicy


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_job(job_dir: Path) -> dict[str, Any]:
    return json.loads((job_dir / "job.json").read_text(encoding="utf-8"))


def _write_job(job_dir: Path, job: dict[str, Any]) -> None:
    tmp = job_dir / "job.json.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(job_dir / "job.json")


def _policy_status_text(policy: ChannelPolicy, channel: str, ready_at: Any) -> str:
    if channel and not policy.permits(channel):
        return "bloqueado pela policy"
    delay_h = policy.delay_h.get(channel, 0.0)
    if delay_h > 0 and ready_at is not None:
        ready_str = datetime.fromtimestamp(float(ready_at), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"aguardar ate {ready_str} (delay {delay_h:g}h)"
    return "liberado"


def _format_clip_discord_message(
    clip: dict[str, Any],
    clip_id: str,
    policy_status: str,
    too_large_path: Path | None = None,
) -> str:
    title = str(clip.get("title") or clip_id)
    hook = str(clip.get("hook_top") or "-")
    score_val = clip.get("llm_score") if clip.get("llm_score") is not None else clip.get("score")
    score_txt = f"{score_val}" if score_val is not None else "-"
    ai_src = str(clip.get("title_source") or clip.get("description_source") or clip.get("llm_reason") or "-")
    caption = str(clip.get("caption") or "-")
    desc_long = str(clip.get("description_long") or "-")

    head = (
        f"**{title}** (`{clip_id}`)\n"
        f"Gancho: {hook}\n"
        f"Nota/IA: {score_txt} ({ai_src})\n"
        f"Legenda: {caption}\n"
    )
    tail = f"Policy: {policy_status}\nPara publicar: `publicar {clip_id}`"
    if too_large_path is not None:
        tail = f"arquivo grande demais para o Discord: `{too_large_path}`\n" + tail

    budget = 2000 - len(head) - len(tail) - len("Descricao: \n")
    if budget < 20:
        desc_part = ""
    elif len(desc_long) > budget:
        desc_part = f"Descricao: {desc_long[: max(0, budget - 3)]}...\n"
    else:
        desc_part = f"Descricao: {desc_long}\n"
    return (head + desc_part + tail)[:2000]


def _normalize_policy_channel(raw: str) -> str:
    text = raw.strip().rstrip("/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.lstrip("@").lower()


def notify_discord(
    out_dir: Path | str | None,
    *,
    policy: ChannelPolicy | None = None,
    job_dir: Path | None = None,
    job: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    discord_client: Any | None = None,
    discord_channel_id: str | None = None,
    preview_fn: Callable[..., Path | None] | None = None,
) -> None:
    """Create a thread in Discord for the output directory and post each clip once."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        return
    target_channel = (discord_channel_id or os.environ.get("DISCORD_CHANNEL_ID", "")).strip()
    if not target_channel:
        return

    if policy is None:
        from .monitor import ChannelPolicy

        policy = ChannelPolicy()

    out_path = Path(out_dir) if out_dir is not None else None
    clips_json_path = (out_path / "clips.json") if out_path is not None else None

    manifest: dict[str, Any] | None = None
    if clips_json_path is not None and clips_json_path.is_file():
        manifest = json.loads(clips_json_path.read_text(encoding="utf-8"))
        clips = list(manifest.get("clips") or [])
    else:
        clips = list((result or {}).get("clips") or [])

    if not clips or all(isinstance(c.get("discord"), dict) and c["discord"].get("message_id") for c in clips):
        return

    client = discord_client
    if client is None:
        from .discord_rest import DiscordRest

        client = DiscordRest(token)

    job_path = Path(job_dir) if job_dir is not None else None
    job_dict = dict(job or {})
    completed_job = _read_job(job_path) if (job_path is not None and (job_path / "job.json").is_file()) else job_dict

    channel_name = str(
        job_dict.get("channel")
        or (manifest or {}).get("channel")
        or (manifest or {}).get("channel_credit")
        or ""
    )
    live_title = str((manifest or {}).get("title") or job_dict.get("title") or job_dict.get("job_id") or channel_name)
    live_url = str((manifest or {}).get("url") or job_dict.get("url") or "")
    video_id = str((manifest or {}).get("video_id") or (result or {}).get("video_id") or job_dict.get("job_id") or "clip")
    policy_status = _policy_status_text(policy, _normalize_policy_channel(channel_name), completed_job.get("ready_at"))

    thread_id = (
        ((manifest or {}).get("discord") or {}).get("thread_id")
        or ((completed_job.get("discord") or {}).get("thread_id"))
        or next(
            (c["discord"]["thread_id"] for c in clips if isinstance(c.get("discord"), dict) and c["discord"].get("thread_id")),
            None,
        )
    )

    def _record_error(exc: Exception) -> None:
        err_text = f"{type(exc).__name__}: {exc}".replace(token, "***")
        if job_path is not None:
            completed_job["discord_error"] = err_text[-500:]
            _write_job(job_path, completed_job)
        print(f"[discord] falha ao enviar previa ({err_text[-200:]})", flush=True)

    try:
        if not thread_id:
            header_title = f"**{channel_name}** — {live_title}" if channel_name else f"**{live_title}**"
            header = f"{header_title}\nClipes: {len(clips)}"
            if live_url:
                header += f"\n{live_url}"
            header_resp = client.post(target_channel, header[:2000])
            header_msg_id = str(header_resp["id"])
            thread_name = f"{channel_name} · {live_title[:70]}" if channel_name else live_title[:70]
            thread_id = str(client.thread_from(target_channel, header_msg_id, thread_name, auto_archive_duration=1440))

            if manifest is not None and clips_json_path is not None:
                manifest_discord = dict(manifest.get("discord") or {})
                manifest_discord.update(thread_id=thread_id, header_message_id=header_msg_id)
                manifest["discord"] = manifest_discord
                _write_json_atomic(clips_json_path, manifest)

            if job_path is not None:
                job_discord = dict(completed_job.get("discord") or {})
                job_discord.update(thread_id=thread_id, header_message_id=header_msg_id)
                completed_job["discord"] = job_discord
                _write_job(job_path, completed_job)

        for idx, clip in enumerate(clips, start=1):
            try:
                if isinstance(clip.get("discord"), dict) and clip["discord"].get("message_id"):
                    continue
                clip_id = str(clip.get("clip_id") or f"{video_id}_c{int(clip.get('index', idx)):02d}")
                file_rel = clip.get("file")
                mp4_path = (out_path / file_rel) if (out_path is not None and file_rel) else None

                attach_path: Path | None = None
                too_large_path: Path | None = None
                if mp4_path is not None and mp4_path.is_file():
                    preview_impl = preview_fn
                    if preview_impl is None:
                        from .previa import preview_for_discord as preview_impl
                    duration = float(clip.get("rendered_duration") or clip.get("duration") or 60.0)
                    attach_path = preview_impl(mp4_path, duration, out_path / ".cache")
                    if attach_path is None:
                        too_large_path = mp4_path

                msg_text = _format_clip_discord_message(clip, clip_id, policy_status, too_large_path=too_large_path)
                msg_resp = client.post(thread_id, msg_text, file=attach_path)
                msg_id = str(msg_resp["id"])

                clip["discord"] = {"thread_id": thread_id, "message_id": msg_id}
                if manifest is not None and clips_json_path is not None:
                    manifest["clips"] = clips
                    _write_json_atomic(clips_json_path, manifest)

                if job_path is not None:
                    job_discord = dict(completed_job.get("discord") or {})
                    job_clips = dict(job_discord.get("clips") or {})
                    job_clips[clip_id] = msg_id
                    job_discord["thread_id"] = thread_id
                    job_discord["clips"] = job_clips
                    completed_job["discord"] = job_discord
                    _write_job(job_path, completed_job)
            except Exception as exc:
                _record_error(exc)
    except Exception as exc:
        _record_error(exc)


def post_aviso(
    texto: str,
    *,
    discord_client: Any | None = None,
    discord_channel_id: str | None = None,
) -> None:
    """Post a short operational notice to DISCORD_CHANNEL_ID; no-op without env, logs errors safely."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        return
    target_channel = (discord_channel_id or os.environ.get("DISCORD_CHANNEL_ID", "")).strip()
    if not target_channel:
        return
    client = discord_client
    if client is None:
        from .discord_rest import DiscordRest

        client = DiscordRest(token)
    try:
        client.post(target_channel, texto[:2000])
    except Exception as exc:
        err_text = f"{type(exc).__name__}: {exc}".replace(token, "***")
        print(f"[discord] falha ao enviar aviso ({err_text[-200:]})", flush=True)
