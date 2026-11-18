"""Orquestracao de pedidos de publicacao (fila local/Mongo, permissao, politica, cota, idempotencia)."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .monitor import ChannelPolicy, load_policy

PLATFORMS: tuple[str, ...] = ("youtube", "tiktok")
DEFAULT_DAILY_QUOTAS: dict[str, int] = {"youtube": 6, "tiktok": 5}
QUOTA_ENV_KEYS: dict[str, str] = {"youtube": "CLIPES_YT_MAX_DIA", "tiktok": "CLIPES_TIKTOK_MAX_DIA"}
DEFAULT_TOKENS_PATH = Path("/config/tokens.json")
WORKER_STALE_S = 180.0
_UPLOAD_URL_RE = re.compile(r"https?://[^\s'\"<>]*upload[^\s'\"<>]*", re.IGNORECASE)


def _now_ts(now: Callable[[], float] | float | None) -> float:
    if now is None:
        return time.time()
    return float(now() if callable(now) else now)


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _json_default(obj: Any) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _atomic_write_json(path: Path, payload: Any, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _sanitize_text(text: str, secrets: Iterable[str] = ()) -> str:
    cleaned = _UPLOAD_URL_RE.sub("[upload-url-redacted]", text or "")
    for secret in secrets:
        s = str(secret or "")
        if len(s) >= 4:
            cleaned = cleaned.replace(s, "***")
    return cleaned


def _allowed_user_set(allowed_ids: Iterable[str | int] | None) -> set[str]:
    if allowed_ids is not None:
        return {str(uid).strip() for uid in allowed_ids if str(uid).strip()}
    raw = os.environ.get("DISCORD_ALLOWED_USER_IDS", "")
    return {tok.strip() for tok in re.split(r"[,\s]+", raw) if tok.strip()}


def _resolve_tokens_path(tokens_path: Path | str | None) -> Path:
    if tokens_path is not None:
        return Path(tokens_path)
    env_path = os.environ.get("CLIPES_TOKENS_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_TOKENS_PATH


def _load_tokens(tokens: dict[str, Any] | None, tokens_path: Path | str | None) -> dict[str, Any]:
    if tokens is not None:
        return tokens
    path = _resolve_tokens_path(tokens_path)
    try:
        from .oauth import load_tokens as oauth_load_tokens  # lazy: module from T4

        return oauth_load_tokens(path)
    except ImportError:
        data = _read_json(path, {})
        return data if isinstance(data, dict) else {}


def _save_tokens(tokens_data: dict[str, Any], tokens_path: Path | str | None) -> None:
    path = _resolve_tokens_path(tokens_path)
    try:
        from .oauth import save_tokens as oauth_save_tokens  # lazy: module from T4

        oauth_save_tokens(tokens_data, path)
    except ImportError:
        _atomic_write_json(path, tokens_data, mode=0o600)


def _resolve_quota_path(quota_path: Path | str | None, out_root: Path | str | None) -> Path:
    if quota_path is not None:
        return Path(quota_path)
    env_path = os.environ.get("CLIPES_QUOTA_PATH", "").strip()
    if env_path:
        return Path(env_path)
    base = Path(out_root) if out_root is not None else Path(os.environ.get("OUT_DIR") or "out")
    return base / ".cotas_publicacao.json"


def _daily_limit(plataforma: str, limit: int | None = None) -> int:
    if limit is not None:
        return int(limit)
    env_key = QUOTA_ENV_KEYS.get(plataforma, "")
    raw = os.environ.get(env_key, "").strip() if env_key else ""
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_DAILY_QUOTAS.get(plataforma, 0)


def quota_ok(
    plataforma: str,
    now: Callable[[], float] | float | None = None,
    *,
    quota_path: Path | str | None = None,
    out_root: Path | str | None = None,
    limit: int | None = None,
) -> bool:
    """Return True if today's local publication count for `plataforma` is below the daily cap."""
    ts = _now_ts(now)
    day = _dt(ts).strftime("%Y-%m-%d")
    path = _resolve_quota_path(quota_path, out_root)
    data = _read_json(path, {})
    used = int((data.get(day) or {}).get(plataforma, 0)) if isinstance(data, dict) else 0
    return used < _daily_limit(plataforma, limit)


def _record_quota(
    plataforma: str,
    now_ts: float,
    *,
    quota_path: Path | str | None = None,
    out_root: Path | str | None = None,
) -> None:
    day = _dt(now_ts).strftime("%Y-%m-%d")
    path = _resolve_quota_path(quota_path, out_root)
    data = _read_json(path, {})
    if not isinstance(data, dict):
        data = {}
    day_counts = dict(data.get(day) or {})
    day_counts[plataforma] = int(day_counts.get(plataforma, 0)) + 1
    data[day] = day_counts
    _atomic_write_json(path, data)


def _extract_channel(manifest: dict[str, Any], clip: dict[str, Any], job: dict[str, Any] | None) -> str:
    raw = (
        (job or {}).get("channel")
        or clip.get("channel")
        or manifest.get("channel")
        or clip.get("channel_credit")
        or manifest.get("channel_credit")
        or ""
    )
    text = str(raw).strip().rstrip("/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.lstrip("@").lower()


def _parse_ts(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            try:
                dt = datetime.fromisoformat(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except ValueError:
                return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_done_jobs_by_output_dir(root: Path) -> dict[str, dict[str, Any]]:
    jobs_by_dir: dict[str, dict[str, Any]] = {}
    done_dir = root / "queue" / "done"
    if not done_dir.is_dir():
        return jobs_by_dir
    for job_dir in sorted(done_dir.iterdir()):
        job = _read_json(job_dir / "job.json", None)
        if not isinstance(job, dict):
            continue
        out_dir = job.get("output_dir")
        if out_dir:
            try:
                key = str(Path(out_dir).resolve())
            except OSError:
                key = str(out_dir)
            jobs_by_dir[key] = job
    return jobs_by_dir


def _local_worker_id(root: Path) -> str | None:
    wid_file = root / ".worker_id"
    if wid_file.is_file():
        wid = wid_file.read_text(encoding="utf-8").strip()
        if wid:
            return wid
    return None


class LocalStore:
    """JSON-backed publication request store in `OUT_DIR/.publicacoes.json`."""

    def __init__(self, path: Path | str):
        p = Path(path)
        self.path = p / ".publicacoes.json" if (p.is_dir() or p.suffix != ".json") else p
        self.out_root = self.path.parent

    def _load(self) -> dict[str, Any]:
        data = _read_json(self.path, {"requests": {}})
        if not isinstance(data, dict) or not isinstance(data.get("requests"), dict):
            return {"requests": {}}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        _atomic_write_json(self.path, data)

    def ensure_indexes(self) -> None:
        pass

    def get_request(self, clip_id: str, plataforma: str) -> dict[str, Any] | None:
        req_id = f"{clip_id}:{plataforma}"
        return self._load()["requests"].get(req_id)

    def enqueue_request(self, doc: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        data = self._load()
        req_id = doc["_id"]
        existing = data["requests"].get(req_id)
        if existing is not None and existing.get("status") in ("ok", "pending"):
            return existing, False
        stored = dict(doc)
        data["requests"][req_id] = stored
        self._save(data)
        return stored, True

    def find_pending(self, worker_id: str | None = None) -> list[dict[str, Any]]:
        reqs = self._load()["requests"].values()
        out = [
            dict(r)
            for r in reqs
            if r.get("status") == "pending" and (worker_id is None or r.get("owner") == worker_id)
        ]
        return sorted(out, key=lambda r: str(r.get("criado_em") or ""))

    def update_request(self, req_id: str, fields: dict[str, Any]) -> None:
        data = self._load()
        existing = data["requests"].get(req_id)
        if existing is None:
            return
        existing.update(fields)
        self._save(data)

    def worker_status(self, owner: str | None, now_ts: float, stale_s: float = WORKER_STALE_S) -> tuple[bool, str]:
        return True, (owner or "local")

    def find_clip_doc(self, clip_id: str) -> dict[str, Any] | None:
        return None


class MongoStore:
    """MongoDB-backed publication request store wrapping `Coord` or a `Database`."""

    def __init__(self, db_or_coord: Any):
        self.db = db_or_coord if hasattr(db_or_coord, "list_collection_names") else db_or_coord.db

    def ensure_indexes(self) -> None:
        self.db.publish_requests.create_index([("owner", 1), ("status", 1)])

    def get_request(self, clip_id: str, plataforma: str) -> dict[str, Any] | None:
        return self.db.publish_requests.find_one({"_id": f"{clip_id}:{plataforma}"})

    def enqueue_request(self, doc: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        req_id = doc["_id"]
        existing = self.db.publish_requests.find_one({"_id": req_id})
        if existing is not None and existing.get("status") in ("ok", "pending"):
            return existing, False
        if existing is not None:
            self.db.publish_requests.update_one({"_id": req_id}, {"$set": doc})
            return dict(doc), True
        try:
            self.db.publish_requests.insert_one(dict(doc))
            return dict(doc), True
        except Exception as exc:
            if type(exc).__name__ == "DuplicateKeyError":
                found = self.db.publish_requests.find_one({"_id": req_id})
                return (found or dict(doc)), False
            raise

    def find_pending(self, worker_id: str | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"status": "pending"}
        if worker_id is not None:
            query["owner"] = worker_id
        return list(self.db.publish_requests.find(query).sort("criado_em", 1))

    def update_request(self, req_id: str, fields: dict[str, Any]) -> None:
        self.db.publish_requests.update_one({"_id": req_id}, {"$set": fields})

    def worker_status(self, owner: str | None, now_ts: float, stale_s: float = WORKER_STALE_S) -> tuple[bool, str]:
        if not owner:
            return True, "local"
        doc = self.db.workers.find_one({"_id": owner})
        if not doc:
            return False, owner
        alias = str(doc.get("alias") or owner)
        hb = doc.get("heartbeat_at")
        hb_ts = _parse_ts(hb)
        if hb_ts is None or (now_ts - hb_ts) > stale_s:
            return False, alias
        return True, alias

    def find_clip_doc(self, clip_id: str) -> dict[str, Any] | None:
        clip = self.db.clips.find_one({"$or": [{"_id": clip_id}, {"clip_id": clip_id}]})
        if clip:
            return {
                "clip_id": clip_id,
                "clip": clip,
                "manifest": clip.get("manifest") or {},
                "clips_json_path": None,
                "mp4_path": None,
                "channel": str(clip.get("channel") or "").lower(),
                "created_at": _parse_ts(clip.get("created_at")),
                "ready_at": _parse_ts(clip.get("ready_at")),
                "owner": clip.get("owner"),
                "thread_id": (clip.get("discord") or {}).get("thread_id") or clip.get("thread_id"),
            }
        return None


def _adapt_store(store: Any) -> LocalStore | MongoStore:
    if isinstance(store, (LocalStore, MongoStore)):
        return store
    if hasattr(store, "enqueue_request") and hasattr(store, "find_pending"):
        return store
    return MongoStore(store)


def find_clip(out_root: Path | str | None, clip_id: str, *, store: Any = None) -> dict[str, Any] | None:
    """Locate `clip_id` across `clips.json` manifests under `out_root` (and optional Mongo store)."""
    cid = (clip_id or "").strip()
    if not cid:
        return None

    if out_root is not None:
        root = Path(out_root)
        if root.is_dir():
            jobs_by_dir = _load_done_jobs_by_output_dir(root)
            default_owner = _local_worker_id(root)
            for clips_path in sorted(root.rglob("clips.json")):
                manifest = _read_json(clips_path, None)
                if not isinstance(manifest, dict):
                    continue
                clips = manifest.get("clips")
                if not isinstance(clips, list):
                    continue
                try:
                    dir_key = str(clips_path.parent.resolve())
                except OSError:
                    dir_key = str(clips_path.parent)
                job = jobs_by_dir.get(dir_key)
                for entry in clips:
                    if not isinstance(entry, dict):
                        continue
                    entry_id = entry.get("clip_id") or (
                        f"{manifest.get('video_id')}_c{int(entry['index']):02d}"
                        if manifest.get("video_id") and entry.get("index") is not None
                        else None
                    )
                    if entry_id != cid:
                        continue
                    mp4_rel = entry.get("file")
                    mp4_path = (clips_path.parent / mp4_rel) if mp4_rel else None
                    discord_meta = entry.get("discord") if isinstance(entry.get("discord"), dict) else {}
                    manifest_discord = manifest.get("discord") if isinstance(manifest.get("discord"), dict) else {}
                    return {
                        "clip_id": cid,
                        "clip": entry,
                        "manifest": manifest,
                        "clips_json_path": clips_path,
                        "mp4_path": mp4_path,
                        "channel": _extract_channel(manifest, entry, job),
                        "created_at": _parse_ts((job or {}).get("created_at") or manifest.get("created_at")),
                        "ready_at": _parse_ts(
                            entry.get("ready_at")
                            if entry.get("ready_at") is not None
                            else (job or {}).get("ready_at", manifest.get("ready_at"))
                        ),
                        "owner": (job or {}).get("owner") or entry.get("owner") or manifest.get("owner") or default_owner,
                        "thread_id": discord_meta.get("thread_id") or manifest_discord.get("thread_id"),
                    }

    if store is not None:
        return _adapt_store(store).find_clip_doc(cid)
    return None


def _resolve_policy(policy: ChannelPolicy | None, policy_path: Path | str | None, out_root: Path | None) -> ChannelPolicy:
    if policy is not None:
        return policy
    if policy_path is not None:
        return load_policy(Path(policy_path))
    if out_root is not None and (out_root / "policy.json").is_file():
        return load_policy(out_root / "policy.json")
    return ChannelPolicy()


def _effective_ready_at(found: dict[str, Any], policy: ChannelPolicy) -> float | None:
    if found.get("ready_at") is not None:
        return float(found["ready_at"])
    created_at = found.get("created_at")
    channel = found.get("channel") or ""
    delay_h = policy.delay_h.get(channel, 0.0) if channel else 0.0
    if created_at is not None and delay_h > 0:
        return float(created_at) + float(delay_h) * 3600.0
    return None


def _format_existing_link(plataforma: str, resultado: dict[str, Any] | None) -> str:
    res = resultado or {}
    if res.get("url"):
        return str(res["url"])
    if plataforma == "youtube" and res.get("video_id"):
        return f"https://youtube.com/shorts/{res['video_id']}"
    if res.get("publish_id"):
        return f"publish_id={res['publish_id']}"
    if res.get("id"):
        return str(res["id"])
    return "publicado"


def _has_platform_token(tokens: dict[str, Any], plataforma: str) -> bool:
    entry = tokens.get(plataforma)
    return isinstance(entry, dict) and bool(entry.get("refresh_token") or entry.get("access_token"))


def request(
    store: Any,
    clip_id: str,
    user_id: str | int,
    now: Callable[[], float] | float | None = None,
    *,
    out_root: Path | str | None = None,
    allowed_ids: Iterable[str | int] | None = None,
    policy: ChannelPolicy | None = None,
    policy_path: Path | str | None = None,
    tokens: dict[str, Any] | None = None,
    tokens_path: Path | str | None = None,
    quota_path: Path | str | None = None,
    worker_id: str | None = None,
    thread_id: str | None = None,
    platforms: tuple[str, ...] = PLATFORMS,
    worker_stale_s: float = WORKER_STALE_S,
) -> str:
    """Validate permission, policy, idempotency, and quota, then enqueue `publish_requests`."""
    if str(user_id).strip() not in _allowed_user_set(allowed_ids):
        return "sem permissao"

    adapted = _adapt_store(store)
    root = Path(out_root) if out_root is not None else getattr(adapted, "out_root", None)
    found = find_clip(root, clip_id, store=adapted)
    if found is None:
        return f"clipe {clip_id} nao encontrado"

    now_ts = _now_ts(now)
    pol = _resolve_policy(policy, policy_path, root)
    channel = found.get("channel") or ""
    if channel and not pol.permits(channel):
        return "bloqueado pela politica"

    ready_at = _effective_ready_at(found, pol)
    if ready_at is not None and now_ts < ready_at:
        ready_str = _dt(ready_at).strftime("%Y-%m-%d %H:%M UTC")
        return f"aguardar ate {ready_str}"

    tok_map = _load_tokens(tokens, tokens_path)
    owner = worker_id or found.get("owner") or (_local_worker_id(root) if root is not None else None) or "local"
    eff_thread_id = thread_id or found.get("thread_id")
    messages: list[str] = []

    for plat in platforms:
        existing = adapted.get_request(clip_id, plat)
        if existing is not None and existing.get("status") == "ok":
            link = _format_existing_link(plat, existing.get("resultado"))
            messages.append(f"{plat}: ja publicado ({link})")
            continue

        if not _has_platform_token(tok_map, plat):
            messages.append(f"{plat}: desligada")
            continue

        if not quota_ok(plat, now_ts, quota_path=quota_path, out_root=root):
            adapted.enqueue_request({
                "_id": f"{clip_id}:{plat}",
                "clip_id": clip_id,
                "plataforma": plat,
                "owner": owner,
                "pedido_por": str(user_id),
                "status": "adiado",
                "resultado": {"erro": "cota_diaria"},
                "thread_id": eff_thread_id,
                "criado_em": _dt(now_ts),
                "concluido_em": _dt(now_ts),
            })
            messages.append(f"cota diaria atingida ({plat})")
            continue

        adapted.enqueue_request({
            "_id": f"{clip_id}:{plat}",
            "clip_id": clip_id,
            "plataforma": plat,
            "owner": owner,
            "pedido_por": str(user_id),
            "status": "pending",
            "resultado": None,
            "thread_id": eff_thread_id,
            "criado_em": _dt(now_ts),
            "concluido_em": None,
        })
        online, host_label = adapted.worker_status(owner, now_ts, stale_s=worker_stale_s)
        if not online:
            messages.append(f"{plat}: PC {host_label} offline; pedido fica na fila")
        else:
            messages.append(f"{plat}: publicacao enfileirada")

    return "\n".join(messages) if messages else "nenhuma plataforma configurada"


def _default_refresh(
    plataforma: str,
    plat_tokens: dict[str, Any],
    *,
    tokens_path: Path | str | None = None,
) -> dict[str, Any]:
    refresh_tok = plat_tokens.get("refresh_token")
    if not refresh_tok:
        raise RuntimeError(f"missing refresh_token for {plataforma}")
    from . import oauth  # lazy: module from T4

    resolved_tokens = Path(tokens_path) if tokens_path is not None else None
    if plataforma == "youtube":
        access_token = oauth.refresh_google(refresh_tok, tokens_path=resolved_tokens)
        return {"access_token": access_token}
    if plataforma == "tiktok":
        access_token = oauth.refresh_tiktok(refresh_tok, tokens_path=resolved_tokens)
        return {"access_token": access_token}
    raise ValueError(f"unknown platform: {plataforma}")


def _default_publish(plataforma: str, mp4_path: Path, clip: dict[str, Any], access_token: str) -> dict[str, Any]:
    if plataforma == "youtube":
        from . import publicar_youtube  # lazy: module from T5

        title = str(clip.get("title") or clip.get("clip_id") or mp4_path.stem)
        desc = str(clip.get("description_long") or clip.get("caption") or title)
        return publicar_youtube.publish(mp4_path, title, desc, access_token)
    if plataforma == "tiktok":
        from . import publicar_tiktok  # lazy: module from T6

        caption = str(clip.get("caption") or clip.get("title") or mp4_path.stem)
        return publicar_tiktok.publish(mp4_path, caption, access_token)
    raise ValueError(f"unknown platform: {plataforma}")


def _call_publisher(
    fn: Callable[..., dict[str, Any]],
    plataforma: str,
    mp4_path: Path,
    clip: dict[str, Any],
    access_token: str,
) -> dict[str, Any]:
    if plataforma == "youtube":
        title = str(clip.get("title") or clip.get("clip_id") or mp4_path.stem)
        desc = str(clip.get("description_long") or clip.get("caption") or title)
        return fn(mp4_path, title, desc, access_token)
    caption = str(clip.get("caption") or clip.get("title") or mp4_path.stem)
    return fn(mp4_path, caption, access_token)


def _is_401_error(exc: BaseException) -> bool:
    if getattr(exc, "code", None) == 401 or getattr(exc, "status", None) == 401:
        return True
    if getattr(exc, "kind", None) == "erro_auth":
        return True
    text = str(exc).lower()
    return "401" in text or "unauthorized" in text or "invalid_token" in text


def _is_account_error(exc: BaseException) -> bool:
    if getattr(exc, "kind", None) == "erro_conta":
        return True
    text = str(exc).lower()
    return "unaudited_client_can_only_post_to_private_accounts" in text or "erro_conta" in text


def _record_clip_publication(clips_json_path: Path | None, clip_id: str, pub_entry: dict[str, Any]) -> None:
    if clips_json_path is None or not clips_json_path.is_file():
        return
    manifest = _read_json(clips_json_path, None)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("clips"), list):
        return
    updated = False
    for entry in manifest["clips"]:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("clip_id") or (
            f"{manifest.get('video_id')}_c{int(entry['index']):02d}"
            if manifest.get("video_id") and entry.get("index") is not None
            else None
        )
        if eid != clip_id:
            continue
        pubs = list(entry.get("publicacoes") or [])
        pubs = [p for p in pubs if p.get("plataforma") != pub_entry["plataforma"]]
        pubs.append(pub_entry)
        entry["publicacoes"] = pubs
        updated = True
        break
    if updated:
        _atomic_write_json(clips_json_path, manifest)


def _send_thread_reply(
    discord_post: Callable[[str, str], Any] | None,
    thread_id: str | None,
    message: str,
) -> None:
    if not thread_id:
        return
    if discord_post is not None:
        try:
            discord_post(str(thread_id), message)
        except Exception:
            pass
        return
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not bot_token:
        return
    try:
        from .discord_rest import DiscordRest  # lazy: module from T3

        DiscordRest(bot_token).post(str(thread_id), message)
    except Exception:
        pass


def _secret_values(tokens_map: dict[str, Any], extra: Iterable[str] = ()) -> list[str]:
    vals: list[str] = [str(x) for x in extra if x]
    for entry in tokens_map.values():
        if isinstance(entry, dict):
            for k in ("access_token", "refresh_token"):
                if entry.get(k):
                    vals.append(str(entry[k]))
    bot_tok = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if bot_tok:
        vals.append(bot_tok)
    return vals


def run_pending(
    store: Any,
    worker_id: str,
    *,
    out_root: Path | str | None = None,
    now: Callable[[], float] | float | None = None,
    policy: ChannelPolicy | None = None,
    policy_path: Path | str | None = None,
    tokens: dict[str, Any] | None = None,
    tokens_path: Path | str | None = None,
    quota_path: Path | str | None = None,
    publishers: dict[str, Callable[..., dict[str, Any]]] | None = None,
    refreshers: dict[str, Callable[..., dict[str, Any]]] | None = None,
    save_tokens_fn: Callable[[dict[str, Any], Path | str | None], None] | None = None,
    discord_post: Callable[[str, str], Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute pending publish requests owned by `worker_id` and reply in the clip thread."""
    adapted = _adapt_store(store)
    root = Path(out_root) if out_root is not None else getattr(adapted, "out_root", None)
    pol = _resolve_policy(policy, policy_path, root)
    save_fn = save_tokens_fn or _save_tokens
    outcomes: list[dict[str, Any]] = []

    for req in adapted.find_pending(worker_id=worker_id):
        now_ts = _now_ts(now)
        req_id = req["_id"]
        clip_id = req["clip_id"]
        plat = req["plataforma"]
        thread_id = req.get("thread_id")

        def finish(status: str, resultado: dict[str, Any], reply: str) -> None:
            adapted.update_request(req_id, {
                "status": status,
                "resultado": resultado,
                "concluido_em": _dt(now_ts),
            })
            _send_thread_reply(discord_post, eff_thread_id, reply)
            outcomes.append({
                "_id": req_id,
                "clip_id": clip_id,
                "plataforma": plat,
                "status": status,
                "resultado": resultado,
                "message": reply,
            })

        found = find_clip(root, clip_id, store=adapted)
        eff_thread_id = thread_id or (found.get("thread_id") if found else None)

        if not quota_ok(plat, now_ts, quota_path=quota_path, out_root=root):
            finish("adiado", {"erro": "cota_diaria"}, f"[{plat}] adiado: cota diaria atingida ({plat})")
            continue

        mp4_path = found.get("mp4_path") if found else None
        if found is None or mp4_path is None or not Path(mp4_path).is_file():
            finish("erro_arquivo", {"erro": "arquivo_ausente"}, f"[{plat}] erro_arquivo: .mp4 ausente para {clip_id}")
            continue

        channel = found.get("channel") or ""
        if channel and not pol.permits(channel):
            finish("erro", {"erro": "bloqueado pela politica"}, f"[{plat}] erro: bloqueado pela politica")
            continue

        ready_at = _effective_ready_at(found, pol)
        if ready_at is not None and now_ts < ready_at:
            continue

        tok_map = _load_tokens(tokens, tokens_path)
        plat_tok = dict(tok_map.get(plat) or {})
        secrets = _secret_values(tok_map)

        def do_refresh() -> str:
            ref_fn = (refreshers or {}).get(plat) or (
                lambda p, t: _default_refresh(p, t, tokens_path=tokens_path)
            )
            refreshed = ref_fn(plat, plat_tok)
            if isinstance(refreshed, str):
                new_access = refreshed
                new_refresh = ""
            elif isinstance(refreshed, dict):
                new_access = str(refreshed.get("access_token") or "")
                new_refresh = str(refreshed.get("refresh_token") or "")
            else:
                new_access = ""
                new_refresh = ""
            if not new_access:
                raise RuntimeError("refresh returned no access_token")
            plat_tok["access_token"] = new_access
            if new_refresh:
                plat_tok["refresh_token"] = new_refresh
                tok_map[plat] = plat_tok
                save_fn(tok_map, tokens_path)
            else:
                tok_map[plat] = plat_tok
            secrets.extend(_secret_values(tok_map))
            return str(new_access)

        access_token = plat_tok.get("access_token")
        if not access_token:
            if not plat_tok.get("refresh_token"):
                finish("erro_auth", {"erro": "sem_token"}, f"[{plat}] erro_auth: refaca clipes auth {plat}")
                continue
            try:
                access_token = do_refresh()
            except Exception as exc:
                safe_err = _sanitize_text(str(exc), secrets)
                finish("erro_auth", {"erro": safe_err}, f"[{plat}] erro_auth: refaca clipes auth {plat}")
                continue

        pub_fn = (publishers or {}).get(plat)
        clip_entry = found["clip"]

        def invoke(tok: str) -> dict[str, Any]:
            if pub_fn is not None:
                return _call_publisher(pub_fn, plat, Path(mp4_path), clip_entry, tok)
            return _default_publish(plat, Path(mp4_path), clip_entry, tok)

        try:
            res = invoke(str(access_token))
        except Exception as exc:
            if _is_401_error(exc):
                try:
                    access_token = do_refresh()
                    res = invoke(access_token)
                except Exception as exc2:
                    safe_err = _sanitize_text(str(exc2), secrets)
                    if _is_account_error(exc2):
                        finish("erro_conta", {"erro": safe_err}, f"[{plat}] erro_conta: deixe a conta do TikTok como privada")
                    else:
                        finish("erro_auth", {"erro": safe_err}, f"[{plat}] erro_auth: refaca clipes auth {plat}")
                    continue
            elif _is_account_error(exc):
                safe_err = _sanitize_text(str(exc), secrets)
                finish("erro_conta", {"erro": safe_err}, f"[{plat}] erro_conta: deixe a conta do TikTok como privada")
                continue
            else:
                safe_err = _sanitize_text(str(exc), secrets)
                finish("erro", {"erro": safe_err}, f"[{plat}] erro: {safe_err}")
                continue

        if isinstance(res, dict) and res.get("status") in ("erro_conta", "erro_auth", "erro"):
            st = str(res["status"])
            safe_err = _sanitize_text(str(res.get("error") or res.get("erro") or st), secrets)
            if st == "erro_conta":
                msg = f"[{plat}] erro_conta: deixe a conta do TikTok como privada"
            elif st == "erro_auth":
                msg = f"[{plat}] erro_auth: refaca clipes auth {plat}"
            else:
                msg = f"[{plat}] erro: {safe_err}"
            finish(st, {"erro": safe_err}, msg)
            continue

        _record_quota(plat, now_ts, quota_path=quota_path, out_root=root)
        pub_id = str(res.get("video_id") or res.get("publish_id") or res.get("id") or "")
        if plat == "youtube":
            priv = "private"
            url = str(res.get("url") or (f"https://youtube.com/shorts/{pub_id}" if pub_id else ""))
            resultado: dict[str, Any] = {"video_id": pub_id, "privacidade": priv}
            if url:
                resultado["url"] = url
        else:
            priv = "SELF_ONLY"
            url = str(res.get("url") or "")
            resultado = {"publish_id": pub_id, "privacidade": priv}
            if url:
                resultado["url"] = url

        _record_clip_publication(
            found.get("clips_json_path"),
            clip_id,
            {"plataforma": plat, "id": pub_id, "privacidade": priv, **({"url": url} if url else {})},
        )
        link_or_id = url or pub_id
        reply = _sanitize_text(f"[{plat}] ok: {link_or_id}", secrets)
        finish("ok", resultado, reply)

    return outcomes
