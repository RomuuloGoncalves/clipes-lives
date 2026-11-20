"""Discord preview notification, clip registry ACK, and disk cleanup (monitor and CLI)."""

from __future__ import annotations

import inspect
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from . import config
from .registro import Registro, _is_mongo_error, sha256_arquivo

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


_STATUS_EMOJIS = ("🟡", "🗓️", "✅", "🗑️")


def set_clip_status_line(
    content: str,
    new_status: str,
    *,
    prepend_if_missing: bool = False,
) -> str:
    """Atualiza o prefixo de estado na primeira linha da mensagem de clipe."""
    text = content or ""
    if not text:
        return new_status[:2000]
    first, sep, rest = text.partition("\n")
    has_emoji = any(first.startswith(em) for em in _STATUS_EMOJIS)
    if has_emoji:
        if " — " in first:
            _, _, title_part = first.partition(" — ")
            new_first = f"{new_status} — {title_part}"
        else:
            new_first = new_status
    elif prepend_if_missing:
        new_first = f"{new_status} — {first}"
    else:
        return text
    return f"{new_first}{sep}{rest}"[:2000]


def _gate_bloqueio(clip: dict[str, Any]) -> list[str] | None:
    """Motivos do gate quando reprovado (ok=false); None quando aprovado ou sem gate."""
    gate = clip.get("gate")
    if not isinstance(gate, dict) or gate.get("ok") is not False:
        return None
    return [str(m) for m in (gate.get("motivos") or [])] or ["gate reprovado"]


def _format_clip_discord_message(
    clip: dict[str, Any],
    clip_id: str,
    policy_status: str,
    too_large_path: Path | None = None,
    *,
    estado: str = "🟡 pendente",
) -> str:
    title = str(clip.get("title") or clip_id)
    hook = str(clip.get("hook_top") or "-")
    score_val = clip.get("llm_score") if clip.get("llm_score") is not None else clip.get("score")
    score_txt = f"{score_val}" if score_val is not None else "-"
    ai_src = str(clip.get("title_source") or clip.get("description_source") or clip.get("llm_reason") or "-")
    desc_long = str(clip.get("description_long") or "-")

    head = (
        f"{estado} — **{title}**\n"
        f"ID: `{clip_id}`\n"
        f"Gancho: {hook}\n"
        f"Nota: {score_txt} ({ai_src})\n"
    )
    tail_lines: list[str] = []
    bloqueio = _gate_bloqueio(clip)
    if bloqueio is not None:
        tail_lines.append(f"Bloqueado: {', '.join(bloqueio)}")
    if too_large_path is not None:
        tail_lines.append(f"arquivo grande demais para o Discord: `{too_large_path}`")
    if policy_status and policy_status != "liberado":
        tail_lines.append(f"Policy: {policy_status}")
    tail = "\n".join(tail_lines)

    desc_prefix = "Descricao: "
    extra_sep = 1 if tail else 0
    budget = 2000 - len(head) - len(tail) - extra_sep - len(desc_prefix)
    if budget < 20:
        desc_part = ""
    elif len(desc_long) > budget:
        desc_part = f"{desc_prefix}{desc_long[: max(0, budget - 3)]}..."
    else:
        desc_part = f"{desc_prefix}{desc_long}"

    body = head + desc_part
    if tail:
        body = f"{body}\n{tail}" if desc_part else f"{head}{tail}"
    return body[:2000]


def _normalize_policy_channel(raw: str) -> str:
    text = raw.strip().rstrip("/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.lstrip("@").lower()


def _master_mode() -> str:
    raw = os.environ.get("CLIPES_MASTER_MODE", "").strip().lower()
    return "livre" if raw == "livre" else "discord"


def _verificar_sha_enabled() -> bool:
    return config.env_flag("CLIPES_REGISTRO_VERIFICAR", True)


def _resolve_registro_root(out_path: Path | None, out_root: Path | str | None) -> Path | None:
    if out_root is not None:
        return Path(out_root).expanduser().resolve()
    if out_path is None:
        return None
    if os.environ.get("OUT_DIR", "").strip():
        cand = config.resolve_out_dir()
        try:
            out_path.resolve().relative_to(cand)
            return cand
        except ValueError:
            pass
    return out_path.resolve().parent


def _format_safe_err(exc: BaseException, *secrets: str) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text[-200:]


def _is_413_error(exc: BaseException) -> bool:
    if getattr(exc, "status", None) == 413 or getattr(exc, "code", None) == 40005:
        return True
    text = str(exc)
    return "HTTP 413" in text or "code=40005" in text


def _is_attachment_error(exc: BaseException) -> bool:
    if _is_413_error(exc) or isinstance(exc, OSError):
        return True
    return isinstance(getattr(exc, "__cause__", None), OSError)


_threads_vivas: set[str] = set()  # threads confirmadas neste processo (evita 1 GET por ciclo)


def _is_404_error(exc: BaseException) -> bool:
    if getattr(exc, "status", None) == 404 or getattr(exc, "code", None) in (10003, 10008):
        return True
    text = str(exc)
    return "HTTP 404" in text or "code=10008" in text or "code=10003" in text


def _format_live_header(
    channel_name: str,
    live_title: str,
    clip_count: int,
    live_url: str,
    *,
    encerrada: bool = False,
) -> str:
    header_title = f"**{channel_name}** — {live_title}" if channel_name else f"**{live_title}**"
    estado = "✅ encerrada" if encerrada else "🔴 ao vivo"
    lines = [header_title, f"Clipes: {int(clip_count)}", estado]
    if live_url:
        lines.append(live_url)
    return "\n".join(lines)[:2000]


def _format_live_thread_name(channel_name: str, live_title: str, created_at: Any) -> str:
    try:
        date_str = datetime.fromtimestamp(float(created_at), timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    short_title = (live_title or channel_name or "live")[:60]
    if channel_name:
        return f"{channel_name} · {date_str} · {short_title}"[:100]
    return f"{date_str} · {short_title}"[:100]


def _approve_components(clip_id: str) -> list[dict[str, Any]]:
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 4,
                    "label": "YouTube",
                    "custom_id": f"publicar:youtube:{clip_id}",
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "TikTok",
                    "custom_id": f"publicar:tiktok:{clip_id}",
                },
                {
                    "type": 2,
                    "style": 3,
                    "label": "Ambos",
                    "custom_id": f"publicar:ambos:{clip_id}",
                },
            ],
        },
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 2,
                    "label": "Agendar: nao",
                    "custom_id": f"agendar:off:{clip_id}",
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "Descartar",
                    "custom_id": f"descartar:{clip_id}",
                },
            ],
        },
    ]


def _post_supports_components(client: Any) -> bool:
    try:
        sig = inspect.signature(client.post)
    except (TypeError, ValueError):
        return True
    params = sig.parameters
    return "components" in params or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def _verify_ack(
    reg: Registro,
    clip_id: str,
    local_size: int,
    local_sha: str,
    att: dict[str, Any],
    client: Any,
    token: str,
) -> None:
    raw_size = att.get("size")
    att_size = int(raw_size) if raw_size is not None else -1
    if att_size != local_size:
        reg.marcar(clip_id, status="ack_falhou")
        return

    if _verificar_sha_enabled():
        att_url = att.get("url")
        if not att_url or not hasattr(client, "download"):
            return
        try:
            dl_size, dl_sha = client.download(str(att_url), None, max_bytes=local_size + 1024)
        except Exception as exc:
            err_txt = f"{type(exc).__name__}: {exc}".replace(token, "***")
            print(f"[registro] falha ao verificar sha256 de {clip_id} ({err_txt[-160:]})", flush=True)
            return
        if dl_size != local_size or dl_sha != local_sha:
            reg.marcar(clip_id, status="ack_falhou")
            return

    reg.marcar(clip_id, status="no_discord", ack_em=time.time())


def _local_worker_id(root: Path | None) -> str | None:
    if root is None:
        return None
    wid_file = root / ".worker_id"
    if wid_file.is_file():
        wid = wid_file.read_text(encoding="utf-8").strip()
        if wid:
            return wid
    return None


def _run_liberar(
    reg: Registro,
    reg_root: Path,
    mongo_db: Any | None,
    mongo_configured: bool,
    mongo_unreachable: bool,
    pub_store: Any | None,
) -> None:
    try:
        retencao_raw = os.environ.get("CLIPES_RETENCAO_H", "").strip()
        if not retencao_raw:
            return
        if mongo_configured and mongo_unreachable:
            return

        from .publicacao import LocalStore, MongoStore

        ocupados: set[str] = set()
        try:
            if pub_store is not None and hasattr(pub_store, "active_clip_ids"):
                ocupados.update(pub_store.active_clip_ids())
            else:
                ocupados.update(LocalStore(reg_root).active_clip_ids())
                if mongo_db is not None:
                    ocupados.update(MongoStore(mongo_db).active_clip_ids())
        except Exception as exc:
            if _is_mongo_error(exc) or isinstance(exc, OSError):
                return
            raise

        reg.liberar(retencao_raw, ocupados=ocupados)
    except Exception as exc:
        print(f"[notificar] falha ao liberar arquivos ({_format_safe_err(exc)})", flush=True)


def _clip_match_key(clip: dict[str, Any]) -> tuple[Any, ...] | None:
    """Chave estavel para cruzar clipes de result['clips'] com os do clips.json (A-04)."""
    cid = clip.get("clip_id")
    if cid:
        return ("clip_id", str(cid))
    idx = clip.get("index")
    if idx is not None and not isinstance(idx, bool):
        try:
            return ("index", int(idx))
        except (TypeError, ValueError):
            pass
    if "start" in clip and "end" in clip:
        return ("span", clip.get("file"), clip["start"], clip["end"])
    file_rel = clip.get("file")
    if file_rel:
        return ("file", str(file_rel))
    return None


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
    out_root: Path | str | None = None,
    registro: Registro | None = None,
    mongo_db: Any | None = None,
    pub_store: Any | None = None,
) -> None:
    """Register clips, post Discord thread/previews with ACK verification, and sweep expired local files."""
    out_path = Path(out_dir) if out_dir is not None else None
    clips_json_path = (out_path / "clips.json") if out_path is not None else None

    manifest: dict[str, Any] | None = None
    manifest_clips: list[dict[str, Any]] | None = None
    if clips_json_path is not None and clips_json_path.is_file():
        try:
            loaded = json.loads(clips_json_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
                manifest_clips = list(manifest.get("clips") or [])
            else:
                print(f"[notificar] clips.json invalido em {clips_json_path}", flush=True)
        except Exception as exc:
            print(f"[notificar] falha ao ler clips.json ({_format_safe_err(exc)})", flush=True)

    result_clips = result.get("clips") if isinstance(result, dict) else None
    if manifest_clips is not None:
        if isinstance(result_clips, list):
            # A-04: prioriza result["clips"], preservando os dicts do manifest (estado discord ja gravado)
            by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
            for mc in manifest_clips:
                if isinstance(mc, dict):
                    mk = _clip_match_key(mc)
                    if mk is not None and mk not in by_key:
                        by_key[mk] = mc
            clips = []
            for rc in result_clips:
                if not isinstance(rc, dict):
                    continue
                rk = _clip_match_key(rc)
                clips.append(by_key[rk] if (rk is not None and rk in by_key) else rc)
        else:
            clips = manifest_clips
    else:
        clips = list(result_clips or [])

    if not clips:
        return

    reg_root = _resolve_registro_root(out_path, out_root)
    mongo_uri = os.environ.get("CLIPES_MONGO_URI", "").strip()
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    mongo_configured = (mongo_db is not None) or bool(mongo_uri)
    mongo_unreachable = False
    mclient: Any | None = None

    try:
        if mongo_db is None and mongo_uri and registro is None:
            try:
                import pymongo

                mclient = pymongo.MongoClient(mongo_uri, maxPoolSize=5, serverSelectionTimeoutMS=5000)
                mongo_db = mclient.get_default_database(default="clipes_lives")
            except Exception as exc:
                mongo_db = None
                mongo_unreachable = True
                if not (_is_mongo_error(exc) or isinstance(exc, OSError)):
                    print(f"[notificar] falha ao conectar Mongo ({_format_safe_err(exc, mongo_uri, token)})", flush=True)

        reg = registro
        if reg is None and reg_root is not None:
            try:
                reg = Registro(reg_root, mongo=mongo_db)
            except Exception as exc:
                print(f"[notificar] falha ao iniciar registro ({_format_safe_err(exc, mongo_uri, token)})", flush=True)
                reg = None

        job_path = Path(job_dir) if job_dir is not None else None
        job_dict = dict(job or {})
        completed_job = dict(job_dict)
        if job_path is not None and (job_path / "job.json").is_file():
            try:
                completed_job = _read_job(job_path)
            except Exception as exc:
                print(f"[notificar] falha ao ler job.json ({_format_safe_err(exc, mongo_uri, token)})", flush=True)

        channel_name = str(
            job_dict.get("channel")
            or (manifest or {}).get("channel")
            or (manifest or {}).get("channel_credit")
            or ""
        )
        live_title = str((manifest or {}).get("title") or job_dict.get("title") or job_dict.get("job_id") or channel_name)
        live_url = str((manifest or {}).get("url") or job_dict.get("url") or "")
        video_id = str((manifest or {}).get("video_id") or (result or {}).get("video_id") or job_dict.get("job_id") or "clip")
        master_mode = _master_mode()
        default_owner = (
            job_dict.get("owner")
            or (manifest or {}).get("owner")
            or _local_worker_id(reg_root)
        )
        created_at = job_dict.get("created_at") or (manifest or {}).get("created_at") or time.time()
        job_ready_at = (
            completed_job.get("ready_at")
            if completed_job.get("ready_at") is not None
            else (manifest or {}).get("ready_at")
        )

        rerendered_ids: set[str] = set()
        file_hashes: dict[str, tuple[Path, int, str]] = {}
        if reg is not None:
            for idx, clip in enumerate(clips, start=1):
                clip_id = str(clip.get("clip_id") or f"{video_id}_c{int(clip.get('index', idx)):02d}")
                try:
                    file_rel = clip.get("file")
                    mp4_path = (out_path / file_rel) if (out_path is not None and file_rel) else None
                    cabe_discord = bool(clip.get("cabe_discord", True)) and (master_mode == "discord")
                    ready_at = clip.get("ready_at") if clip.get("ready_at") is not None else job_ready_at
                    owner = clip.get("owner") or default_owner

                    prev_rec = reg.get(clip_id)
                    if mp4_path is not None and mp4_path.is_file():
                        local_size = mp4_path.stat().st_size
                        local_sha = sha256_arquivo(mp4_path)
                        file_hashes[clip_id] = (mp4_path, local_size, local_sha)
                        if prev_rec is not None and (
                            prev_rec.get("sha256") != local_sha
                            or int(prev_rec.get("tamanho") or 0) != local_size
                        ):
                            rerendered_ids.add(clip_id)
                            clip.pop("discord", None)
                        reg.upsert({
                            "clip_id": clip_id,
                            "titulo": str(clip.get("title") or clip_id),
                            "hashtags": list(clip.get("hashtags") or []),
                            "legenda": str(clip.get("caption") or ""),
                            "descricao_longa": str(clip.get("description_long") or ""),
                            "canal": channel_name,
                            "origem_url": live_url,
                            "video_id": video_id,
                            "criado_em": created_at,
                            "ready_at": ready_at,
                            "owner": owner,
                            "arquivo": mp4_path,
                            "tamanho": local_size,
                            "sha256": local_sha,
                            "cabe_discord": cabe_discord,
                        })
                    elif prev_rec is None:
                        reg.upsert({
                            "clip_id": clip_id,
                            "titulo": str(clip.get("title") or clip_id),
                            "hashtags": list(clip.get("hashtags") or []),
                            "legenda": str(clip.get("caption") or ""),
                            "descricao_longa": str(clip.get("description_long") or ""),
                            "canal": channel_name,
                            "origem_url": live_url,
                            "video_id": video_id,
                            "criado_em": created_at,
                            "ready_at": ready_at,
                            "owner": owner,
                            "arquivo": file_rel or "",
                            "tamanho": 0,
                            "sha256": "",
                            "cabe_discord": cabe_discord,
                        })
                    if reg.last_mongo_error:
                        mongo_unreachable = True
                except Exception as exc:
                    print(
                        f"[notificar] falha ao registrar {clip_id} ({_format_safe_err(exc, mongo_uri, token)})",
                        flush=True,
                    )

        target_channel = (discord_channel_id or os.environ.get("DISCORD_CHANNEL_ID", "")).strip()
        if not token or not target_channel:
            return

        if policy is None:
            from .monitor import ChannelPolicy

            policy = ChannelPolicy()
        policy_status = _policy_status_text(policy, _normalize_policy_channel(channel_name), completed_job.get("ready_at"))

        def _needs_ack_retry(clip: dict[str, Any], cid: str) -> bool:
            if reg is None or master_mode != "discord" or cid not in file_hashes:
                return False
            if not bool(clip.get("cabe_discord", True)):
                return False
            try:
                rec = reg.get(cid)
            except Exception as exc:
                print(
                    f"[notificar] falha ao consultar {cid} ({_format_safe_err(exc, mongo_uri, token)})",
                    flush=True,
                )
                return False
            if not isinstance(rec, dict) or rec.get("status") != "local":
                return False
            d_info = rec.get("discord")
            return isinstance(d_info, dict) and bool(d_info.get("message_id")) and bool(d_info.get("attachment_id"))

        needs_work = False
        for idx, clip in enumerate(clips, start=1):
            cid = str(clip.get("clip_id") or f"{video_id}_c{int(clip.get('index', idx)):02d}")
            d_clip = clip.get("discord")
            has_msg = isinstance(d_clip, dict) and bool(d_clip.get("message_id"))
            # ponytail: thread ainda nao confirmada neste processo segue ao loop, que a sonda 1x (pode ter sido apagada)
            if not has_msg or _needs_ack_retry(clip, cid) or d_clip.get("thread_id") not in _threads_vivas:
                needs_work = True
                break

        if not needs_work:
            if reg is not None and reg_root is not None:
                _run_liberar(reg, reg_root, mongo_db, mongo_configured, mongo_unreachable, pub_store)
            return

        client = discord_client
        if client is None:
            from .discord_rest import DiscordRest

            client = DiscordRest(token)
        supports_components = _post_supports_components(client)

        live_id = (
            str(
                completed_job.get("live_id")
                or job_dict.get("live_id")
                or (manifest or {}).get("live_id")
                or ""
            ).strip()
            or None
        )
        live_discord: dict[str, Any] = {}
        live_ended = False  # live encerrada (coord.release): o cartao nao volta a "ao vivo" ao reusar
        if live_id and mongo_db is not None and hasattr(mongo_db, "lives"):
            try:
                live_doc = mongo_db.lives.find_one({"_id": live_id})
                if isinstance(live_doc, dict):
                    live_ended = live_doc.get("status") == "ended"
                if isinstance(live_doc, dict) and isinstance(live_doc.get("discord"), dict):
                    live_discord = dict(live_doc["discord"])
            except Exception as exc:
                if _is_mongo_error(exc) or isinstance(exc, OSError):
                    mongo_unreachable = True
                else:
                    print(
                        f"[notificar] falha ao consultar live {live_id} ({_format_safe_err(exc, mongo_uri, token)})",
                        flush=True,
                    )

        thread_id = (
            (live_discord.get("thread_id") if live_id else None)
            or ((manifest or {}).get("discord") or {}).get("thread_id")
            or ((completed_job.get("discord") or {}).get("thread_id"))
            or next(
                (c["discord"]["thread_id"] for c in clips if isinstance(c.get("discord"), dict) and c["discord"].get("thread_id")),
                None,
            )
        )
        header_msg_id = (
            (live_discord.get("header_message_id") if live_id else None)
            or ((manifest or {}).get("discord") or {}).get("header_message_id")
            or ((completed_job.get("discord") or {}).get("header_message_id"))
        )
        created_live_header = False
        # ponytail: thread legada (manifest/job sem doc em lives) tambem conta como reuso: o cartao vai para lives na 1a edicao
        reused_live_header = bool(live_id and thread_id)

        def _record_error(exc: Exception) -> None:
            err_text = f"{type(exc).__name__}: {exc}".replace(token, "***")
            if job_path is not None:
                try:
                    completed_job["discord_error"] = err_text[-500:]
                    _write_job(job_path, completed_job)
                except Exception as wexc:
                    print(
                        f"[notificar] falha ao gravar job.json ({_format_safe_err(wexc, mongo_uri, token)})",
                        flush=True,
                    )
            print(f"[discord] falha ao enviar previa ({err_text[-200:]})", flush=True)

        def _save_live_discord(info: dict[str, Any]) -> None:
            nonlocal mongo_unreachable
            if not (live_id and mongo_db is not None and hasattr(mongo_db, "lives")):
                return
            try:
                mongo_db.lives.update_one({"_id": live_id}, {"$set": {"discord": info}}, upsert=True)
            except Exception as mexc:
                if _is_mongo_error(mexc) or isinstance(mexc, OSError):
                    mongo_unreachable = True
                else:
                    print(
                        f"[notificar] falha ao gravar lives.discord ({_format_safe_err(mexc, mongo_uri, token)})",
                        flush=True,
                    )

        def _new_thread() -> None:
            nonlocal thread_id, header_msg_id, live_discord, created_live_header
            if live_id:
                header = _format_live_header(channel_name, live_title, len(clips), live_url, encerrada=live_ended)
                thread_name = _format_live_thread_name(channel_name, live_title, created_at)
            else:
                header_title = f"**{channel_name}** — {live_title}" if channel_name else f"**{live_title}**"
                header = f"{header_title}\nClipes: {len(clips)}"
                if live_url:
                    header += f"\n{live_url}"
                thread_name = f"{channel_name} · {live_title[:70]}" if channel_name else live_title[:70]
            header_resp = client.post(target_channel, header[:2000])
            header_msg_id = str(header_resp["id"])
            thread_id = str(client.thread_from(target_channel, header_msg_id, thread_name, auto_archive_duration=1440))

            if live_id:
                created_live_header = True
                live_discord = {
                    "thread_id": thread_id,
                    "header_message_id": header_msg_id,
                    "header_channel_id": target_channel,
                    "channel_id": target_channel,
                    "clipes": len(clips),
                    "channel": channel_name,
                    "title": live_title,
                    "url": live_url,
                }
                _save_live_discord(live_discord)

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

        recovered = False  # fallback de thread apagada: no maximo 1 vez por chamada

        def _recover_dead_thread() -> None:
            nonlocal thread_id, header_msg_id, recovered, reused_live_header
            recovered = True
            dead = thread_id
            gone = [
                str(c.get("clip_id") or f"{video_id}_c{int(c.get('index', i)):02d}")
                for i, c in enumerate(clips, start=1)
                if isinstance(c.get("discord"), dict) and str(c["discord"].get("thread_id") or "") == dead
            ]
            for c in clips:
                if isinstance(c.get("discord"), dict) and str(c["discord"].get("thread_id") or "") == dead:
                    c.pop("discord")
            if job_path is not None:
                job_discord = dict(completed_job.get("discord") or {})
                job_discord["clips"] = {k: v for k, v in (job_discord.get("clips") or {}).items() if k not in gone}
                completed_job["discord"] = job_discord
            thread_id = header_msg_id = None
            reused_live_header = False
            _new_thread()
            print(f"[discord] thread {dead} inexistente; criada nova {thread_id}", flush=True)

        probed: set[str] = set()
        try:
            if not thread_id:
                _new_thread()

            posted_now = 0
            todo = deque(enumerate(clips, start=1))
            while todo:
                idx, clip = todo.popleft()
                try:
                    clip_id = str(clip.get("clip_id") or f"{video_id}_c{int(clip.get('index', idx)):02d}")
                    has_msg = isinstance(clip.get("discord"), dict) and bool(clip["discord"].get("message_id"))
                    if has_msg:
                        if _needs_ack_retry(clip, clip_id) and hasattr(client, "get_message"):
                            rec = reg.get(clip_id)  # type: ignore[union-attr]
                            d_info = rec["discord"]  # type: ignore[index]
                            msg = client.get_message(d_info["thread_id"], d_info["message_id"])
                            atts = msg.get("attachments") or []
                            att = next(
                                (a for a in atts if str(a.get("id") or "") == str(d_info["attachment_id"])),
                                atts[0] if atts else None,
                            )
                            if att is not None:
                                _, local_size, local_sha = file_hashes[clip_id]
                                _verify_ack(reg, clip_id, local_size, local_sha, att, client, token)  # type: ignore[arg-type]
                                if reg.last_mongo_error:  # type: ignore[union-attr]
                                    mongo_unreachable = True
                        elif (
                            hasattr(client, "get_message")
                            and clip["discord"].get("thread_id") not in probed
                            and clip["discord"].get("thread_id") not in _threads_vivas
                        ):
                            # ponytail: 1 GET por thread; confirma que o clipe "ja postado" ainda existe
                            probed.add(clip["discord"].get("thread_id"))
                            try:
                                client.get_message(clip["discord"]["thread_id"], clip["discord"]["message_id"])
                                _threads_vivas.add(clip["discord"]["thread_id"])
                            except Exception as pexc:
                                if _is_404_error(pexc):
                                    if clip["discord"]["thread_id"] == thread_id and not recovered:
                                        raise
                                    clip.pop("discord")
                                    todo.appendleft((idx, clip))
                        continue

                    file_rel = clip.get("file")
                    mp4_path = (out_path / file_rel) if (out_path is not None and file_rel) else None
                    clip_fits_discord = bool(clip.get("cabe_discord", True))
                    cabe_discord = clip_fits_discord and (master_mode == "discord")

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

                    post_kw: dict[str, Any] = (
                        {"components": _approve_components(clip_id)}
                        if supports_components and _gate_bloqueio(clip) is None
                        else {}
                    )
                    msg_text = _format_clip_discord_message(clip, clip_id, policy_status, too_large_path=too_large_path)

                    attachment_rejected = False
                    msg_resp: dict[str, Any] | None = None
                    try:
                        msg_resp = client.post(thread_id, msg_text, file=attach_path, **post_kw)
                    except Exception as exc:
                        if attach_path is not None and _is_attachment_error(exc):
                            attachment_rejected = True
                        else:
                            raise

                    if not attachment_rejected and attach_path is not None and msg_resp is not None:
                        if "attachments" in msg_resp and not msg_resp.get("attachments"):
                            attachment_rejected = True

                    if attachment_rejected:
                        clip["cabe_discord"] = False
                        if reg is not None:
                            try:
                                reg.marcar(clip_id, cabe_discord=False)
                                if reg.last_mongo_error:
                                    mongo_unreachable = True
                            except Exception as rexc:
                                print(
                                    f"[notificar] falha ao marcar cabe_discord=False para {clip_id} ({_format_safe_err(rexc, mongo_uri, token)})",
                                    flush=True,
                                )
                        if manifest is not None and clips_json_path is not None:
                            manifest["clips"] = clips
                            _write_json_atomic(clips_json_path, manifest)
                        fallback_text = _format_clip_discord_message(clip, clip_id, policy_status, too_large_path=mp4_path)
                        msg_resp = client.post(thread_id, fallback_text, file=None, **post_kw)
                        attach_path = None

                    if mp4_path is not None and mp4_path.is_file() and attach_path is None:
                        from .previa import _resolve_limit_mb

                        post_aviso(
                            f"prévia não coube no Discord: {clip_id} "
                            f"({mp4_path.stat().st_size / (1024 * 1024):.1f} MB, limite {_resolve_limit_mb(None):g} MB)",
                            discord_client=client,
                        )

                    msg_id = str(msg_resp["id"])  # type: ignore[index]
                    _threads_vivas.add(str(thread_id))
                    posted_now += 1
                    clip["discord"] = {"thread_id": thread_id, "message_id": msg_id}
                    if manifest is not None and clips_json_path is not None:
                        manifest["clips"] = clips
                        _write_json_atomic(clips_json_path, manifest)

                    if job_path is not None:
                        job_discord = dict(completed_job.get("discord") or {})
                        job_clips = dict(job_discord.get("clips") or {})
                        job_clips[clip_id] = msg_id
                        job_discord["thread_id"] = thread_id
                        if header_msg_id:
                            job_discord["header_message_id"] = header_msg_id
                        job_discord["clips"] = job_clips
                        completed_job["discord"] = job_discord
                        _write_job(job_path, completed_job)

                    atts = msg_resp.get("attachments") if isinstance(msg_resp, dict) else None
                    if reg is not None and attach_path is not None and isinstance(atts, list) and atts:
                        att = atts[0]
                        try:
                            reg.set_discord(clip_id, {
                                "thread_id": thread_id,
                                "message_id": msg_id,
                                "attachment_id": str(att.get("id") or ""),
                                "filename": str(att.get("filename") or attach_path.name),
                                "size": int(att.get("size") or 0),
                            })
                            if (
                                master_mode == "discord"
                                and cabe_discord
                                and mp4_path is not None
                                and attach_path == mp4_path
                                and clip_id in file_hashes
                            ):
                                _, local_size, local_sha = file_hashes[clip_id]
                                _verify_ack(reg, clip_id, local_size, local_sha, att, client, token)
                            if reg.last_mongo_error:
                                mongo_unreachable = True
                        except Exception as rexc:
                            print(
                                f"[notificar] falha ao atualizar ACK de {clip_id} ({_format_safe_err(rexc, mongo_uri, token)})",
                                flush=True,
                            )
                except Exception as exc:
                    if _is_404_error(exc) and not recovered and thread_id:
                        try:
                            _recover_dead_thread()
                            todo.appendleft((idx, clip))
                            continue
                        except Exception as rexc:
                            exc = rexc
                    _record_error(exc)

            if live_id and posted_now > 0 and reused_live_header:
                prev_count = int(live_discord.get("clipes") or 0)
                new_total = prev_count + posted_now
                card_ch = str(live_discord.get("header_channel_id") or live_discord.get("channel_id") or target_channel)
                card_channel = str(live_discord.get("channel") or channel_name)
                card_title = str(live_discord.get("title") or live_title)
                card_url = str(live_discord.get("url") or live_url)
                updated_header = _format_live_header(card_channel, card_title, new_total, card_url, encerrada=live_ended)
                new_header_id = str(header_msg_id or "")
                new_header_ch = card_ch
                if new_header_id and hasattr(client, "edit"):
                    try:
                        client.edit(card_ch, new_header_id, updated_header)
                    except Exception as eexc:
                        if _is_404_error(eexc):
                            resp_new = client.post(thread_id, updated_header)
                            new_header_id = str(resp_new["id"])
                            new_header_ch = thread_id
                        else:
                            _record_error(eexc)
                elif not new_header_id:
                    resp_new = client.post(thread_id, updated_header)
                    new_header_id = str(resp_new["id"])
                    new_header_ch = thread_id

                live_discord.update({
                    "thread_id": thread_id,
                    "header_message_id": new_header_id,
                    "header_channel_id": new_header_ch,
                    "channel_id": live_discord.get("channel_id") or target_channel,
                    "clipes": new_total,
                    "channel": card_channel,
                    "title": card_title,
                    "url": card_url,
                })
                _save_live_discord(live_discord)
                if manifest is not None and clips_json_path is not None:
                    manifest_discord = dict(manifest.get("discord") or {})
                    manifest_discord.update(thread_id=thread_id, header_message_id=new_header_id)
                    manifest["discord"] = manifest_discord
                    _write_json_atomic(clips_json_path, manifest)
            elif live_id and created_live_header and posted_now != len(clips):
                live_discord["clipes"] = posted_now
                _save_live_discord(live_discord)
                if header_msg_id and hasattr(client, "edit"):
                    try:
                        client.edit(
                            target_channel,
                            header_msg_id,
                            _format_live_header(channel_name, live_title, posted_now, live_url, encerrada=False),
                        )
                    except Exception as eexc:
                        _record_error(eexc)
        except Exception as exc:
            _record_error(exc)

        if reg is not None and reg_root is not None:
            _run_liberar(reg, reg_root, mongo_db, mongo_configured, mongo_unreachable, pub_store)
    finally:
        if mclient is not None:
            try:
                mclient.close()
            except Exception:
                pass


def encerrar_cartao_live(
    mongo_db: Any | None,
    live_id: str | None,
    *,
    discord_client: Any | None = None,
    discord_channel_id: str | None = None,
) -> bool:
    """Edita o cartao da live no Discord para '✅ encerrada' ao encerrar a live."""
    if mongo_db is None or not live_id or not hasattr(mongo_db, "lives"):
        return False
    try:
        doc = mongo_db.lives.find_one({"_id": live_id})
    except Exception as exc:
        print(f"[notificar] falha ao ler live {live_id} ({_format_safe_err(exc)})", flush=True)
        return False
    if not isinstance(doc, dict):
        return False
    d_info = doc.get("discord")
    if not isinstance(d_info, dict) or not d_info.get("header_message_id"):
        return False

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    client = discord_client
    if client is None:
        if not token:
            return False
        from .discord_rest import DiscordRest

        client = DiscordRest(token)
    if not hasattr(client, "edit"):
        return False

    target_ch = (
        str(d_info.get("header_channel_id") or "").strip()
        or str(d_info.get("channel_id") or "").strip()
        or (discord_channel_id or "").strip()
        or os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    )
    if not target_ch:
        return False

    header_msg_id = str(d_info["header_message_id"])
    thread_id = str(d_info.get("thread_id") or "")
    channel_name = str(d_info.get("channel") or doc.get("channel") or "")
    live_title = str(d_info.get("title") or doc.get("title") or live_id)
    live_url = str(d_info.get("url") or doc.get("url") or "")
    clipes = int(d_info.get("clipes") or 0)
    text = _format_live_header(channel_name, live_title, clipes, live_url, encerrada=True)

    try:
        client.edit(target_ch, header_msg_id, text)
        return True
    except Exception as exc:
        if _is_404_error(exc) and thread_id:
            try:
                resp = client.post(thread_id, text)
                new_id = str(resp["id"])
                mongo_db.lives.update_one(
                    {"_id": live_id},
                    {"$set": {
                        "discord.header_message_id": new_id,
                        "discord.header_channel_id": thread_id,
                    }},
                )
                return True
            except Exception as exc2:
                if _is_404_error(exc2):
                    print("[discord] cartao encerrado inexistente; ignorado", flush=True)
                    return False
                print(f"[discord] falha ao recriar cartao encerrado ({_format_safe_err(exc2, token)})", flush=True)
                return False
        print(f"[discord] falha ao encerrar cartao ({_format_safe_err(exc, token)})", flush=True)
        return False


_AVISO_JANELA_S = 60.0
_aviso_recente: dict[str, list[float]] = {}  # texto -> [ultimo envio, suprimidos]


def _canal_log(client: Any) -> str | None:
    """Canal de avisos: env -> cache `<OUT_DIR>/.discord_log_channel` -> `bot-log` existente -> cria."""
    env = os.environ.get("DISCORD_LOG_CHANNEL_ID", "").strip()
    if env:
        return env
    cache = config.resolve_out_dir() / ".discord_log_channel"
    try:
        cached = cache.read_text(encoding="utf-8").strip()
    except OSError:
        cached = ""
    if cached:
        return cached
    guild = os.environ.get("DISCORD_GUILD_ID", "").strip()
    if not guild:
        return None
    try:
        canal = next((c for c in client.list_guild_channels(guild) if c.get("name") == "bot-log"), None)
        if canal is None:
            canal = client.create_text_channel(guild, "bot-log")  # 403 sem Manage Channels
        cid = str(canal["id"])
    except Exception:
        return None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(cid, encoding="utf-8")
    except OSError:
        pass
    return cid


def post_aviso(
    texto: str,
    *,
    discord_client: Any | None = None,
    discord_channel_id: str | None = None,
) -> None:
    """Post an operational notice only to the log channel; falls back to stdout (never DISCORD_CHANNEL_ID)."""
    now = time.monotonic()
    rec = _aviso_recente.get(texto)
    if rec and now - rec[0] <= _AVISO_JANELA_S:
        rec[1] += 1
        return
    suprimidos = int(rec[1]) if rec else 0
    _aviso_recente[texto] = [now, 0]
    if suprimidos:
        texto = f"{texto} (+{suprimidos} repetidos)"
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        print(f"[aviso] {texto}", flush=True)
        return
    client = discord_client
    if client is None:
        from .discord_rest import DiscordRest

        client = DiscordRest(token)
    target_channel = (discord_channel_id or "").strip() or _canal_log(client)
    if not target_channel:
        print(f"[aviso] {texto}", flush=True)
        return
    try:
        client.post(target_channel, texto[:2000])
    except Exception as exc:
        err_text = f"{type(exc).__name__}: {exc}".replace(token, "***")
        print(f"[discord] falha ao enviar aviso ({err_text[-200:]})", flush=True)
