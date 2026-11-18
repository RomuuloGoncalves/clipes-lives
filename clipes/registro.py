"""Registro local (registro.json) e espelho best-effort no MongoDB para clipes."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import sys
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .ingest import sanitize_id

VERSAO = 1
SCHEMA_REGISTRO = 1
_CHUNK_BYTES = 1024 * 1024
_DISCORD_INFO_KEYS = ("thread_id", "message_id", "attachment_id", "filename", "size")
_MONGO_ERR_NAMES = frozenset({
    "ServerSelectionTimeoutError",
    "PyMongoError",
    "ConnectionFailure",
    "NetworkTimeout",
    "AutoReconnect",
    "ExecutionTimeout",
})

_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.Lock] = {}


def _is_mongo_error(exc: BaseException) -> bool:
    pymongo_errors = sys.modules.get("pymongo.errors")
    if pymongo_errors is not None and isinstance(exc, pymongo_errors.PyMongoError):
        return True
    return type(exc).__name__ in _MONGO_ERR_NAMES


def _json_default(obj: Any) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


@contextmanager
def _locked_path(path: Path) -> Iterator[None]:
    """Serialize read-modify-write across threads and processes for `path`."""
    resolved = path.resolve()
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.setdefault(resolved, threading.Lock())
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(f".{path.name}.lock")
        with open(lock_path, "a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, payload: Any, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", suffix=".json", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        if mode is not None:
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sha256_arquivo(path: Path | str) -> str:
    """Compute hex SHA-256 of a file using 1 MiB streaming reads."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _norm_busca(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").lower()


def _normalize_channel(raw: Any) -> str:
    text = str(raw or "").strip().rstrip("/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.lstrip("@").lower()


def _sanitize_discord_info(info: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(info, dict):
        return None
    out: dict[str, Any] = {}
    for key in _DISCORD_INFO_KEYS:
        if key in info and info[key] is not None:
            out[key] = info[key]
    return out or None


def _to_mongo_doc(entry: dict[str, Any]) -> dict[str, Any]:
    doc = dict(entry)
    doc["_id"] = entry["clip_id"]
    doc["channel"] = entry.get("canal", "")
    doc["created_at"] = entry.get("criado_em")
    doc["title"] = entry.get("titulo", "")
    doc["caption"] = entry.get("legenda", "")
    doc["description_long"] = entry.get("descricao_longa", "")
    doc["mongo_synced"] = True
    return doc


def _doc_matches_tokens(doc: dict[str, Any], tokens: list[str]) -> bool:
    if not tokens:
        return False
    titulo = str(doc.get("titulo") or doc.get("title") or "")
    hashtags = doc.get("hashtags") or []
    tags_str = " ".join(str(t) for t in hashtags) if isinstance(hashtags, list) else str(hashtags)
    canal = str(doc.get("canal") or doc.get("channel") or "")
    legenda = str(doc.get("legenda") or doc.get("caption") or "")
    haystack = _norm_busca(f"{titulo} {tags_str} {canal} {legenda}")
    return all(tok in haystack for tok in tokens)


def _doc_ts(doc: dict[str, Any]) -> float:
    raw = doc.get("criado_em") if doc.get("criado_em") is not None else doc.get("created_at")
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _sync_fingerprint(rec: dict[str, Any]) -> str:
    cmp = {k: v for k, v in rec.items() if k != "mongo_synced"}
    return json.dumps(cmp, sort_keys=True, default=_json_default)


class Registro:
    """Persistent index of generated clips in `OUT_DIR/registro.json` with optional Mongo mirror."""

    def __init__(self, out_root: Path | str, mongo: Any | None = None) -> None:
        self.out_root = Path(out_root)
        self.path = self.out_root / "registro.json"
        if mongo is None or hasattr(mongo, "clips"):
            self.db = mongo
        else:
            self.db = getattr(mongo, "db", mongo)
        self.last_mongo_error = False

    def _load(self) -> tuple[dict[str, Any], bool]:
        if not self.path.is_file():
            return {"version": VERSAO, "clips": {}}, False
        data = _read_json(self.path, None)
        if not isinstance(data, dict):
            return {"version": VERSAO, "clips": {}}, False
        ver = data.get("version")
        if isinstance(ver, int) and ver > VERSAO:
            print(f"[registro] versao {ver} desconhecida; registro nao alterado", flush=True)
            return data, True
        clips = data.get("clips")
        if not isinstance(clips, dict):
            clips = {}
        return {"version": VERSAO, "clips": dict(clips)}, False

    def _rel_arquivo(self, arquivo: str | Path) -> str:
        raw = str(arquivo or "")
        if not raw:
            return ""
        p = Path(raw)
        if p.is_absolute():
            try:
                return p.resolve().relative_to(self.out_root.resolve()).as_posix()
            except ValueError:
                return str(p)
        return p.as_posix()

    @staticmethod
    def _pending_snapshots(data: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
        return [
            (cid, copy.deepcopy(rec), _sync_fingerprint(rec))
            for cid, rec in data["clips"].items()
            if isinstance(rec, dict) and rec.get("mongo_synced") is False
        ]

    def _sync_mongo(self, pending: list[tuple[str, dict[str, Any], str]]) -> set[str]:
        """Push pending snapshots to Mongo outside the file lock, then mark unchanged ones synced."""
        if self.db is None or not pending:
            self.last_mongo_error = False
            return set()

        synced: list[tuple[str, str]] = []
        try:
            for cid, rec, fp in pending:
                self.db.clips.replace_one({"_id": cid}, _to_mongo_doc(rec), upsert=True)
                synced.append((cid, fp))
        except Exception as exc:
            if not (_is_mongo_error(exc) or isinstance(exc, OSError)):
                raise
            self.last_mongo_error = True
        else:
            self.last_mongo_error = False

        marked: set[str] = set()
        if synced:
            with _locked_path(self.path):
                fresh, unknown = self._load()
                if not unknown:
                    changed = False
                    for cid, fp in synced:
                        cur = fresh["clips"].get(cid)
                        if isinstance(cur, dict) and _sync_fingerprint(cur) == fp:
                            if cur.get("mongo_synced") is not True:
                                cur["mongo_synced"] = True
                                changed = True
                            marked.add(cid)
                    if changed:
                        _atomic_write_json(self.path, fresh)
        return marked

    def upsert(self, rec: dict[str, Any]) -> dict[str, Any] | None:
        """Insert or update a clip record atomically; preserve ACK/discord/publicacoes only when file is unchanged."""
        clip_id = str(rec.get("clip_id") or "").strip()
        if not clip_id:
            raise ValueError("clip_id is required")

        sha256 = str(rec.get("sha256") or "")
        tamanho = int(rec.get("tamanho") or 0)
        hashtags_raw = rec.get("hashtags") or []
        hashtags = [str(t) for t in hashtags_raw] if isinstance(hashtags_raw, list) else []

        pending: list[tuple[str, dict[str, Any], str]] = []
        with _locked_path(self.path):
            data, unknown = self._load()
            if unknown:
                return None
            existing = data["clips"].get(clip_id)
            same_file = (
                isinstance(existing, dict)
                and existing.get("sha256") == sha256
                and int(existing.get("tamanho") or 0) == tamanho
            )

            if same_file:
                status = str(existing.get("status") or "local")
                ack_em = existing.get("ack_em")
                discord_info = _sanitize_discord_info(existing.get("discord"))
                publicacoes = dict(existing.get("publicacoes") or {})
            else:
                status = "local"
                ack_em = None
                discord_info = None
                publicacoes = {}

            entry: dict[str, Any] = {
                "schema": SCHEMA_REGISTRO,
                "clip_id": clip_id,
                "titulo": str(rec.get("titulo") if rec.get("titulo") is not None else (rec.get("title") or "")),
                "hashtags": hashtags,
                "legenda": str(rec.get("legenda") if rec.get("legenda") is not None else (rec.get("caption") or "")),
                "descricao_longa": str(
                    rec.get("descricao_longa")
                    if rec.get("descricao_longa") is not None
                    else (rec.get("description_long") or "")
                ),
                "canal": _normalize_channel(rec.get("canal") if rec.get("canal") is not None else rec.get("channel")),
                "origem_url": str(rec.get("origem_url") or ""),
                "video_id": str(rec.get("video_id") or ""),
                "criado_em": rec.get("criado_em") if rec.get("criado_em") is not None else rec.get("created_at"),
                "ready_at": rec.get("ready_at"),
                "owner": rec.get("owner"),
                "arquivo": self._rel_arquivo(rec.get("arquivo") or ""),
                "tamanho": tamanho,
                "sha256": sha256,
                "cabe_discord": bool(rec.get("cabe_discord", True)),
                "status": status,
                "ack_em": ack_em,
                "discord": discord_info,
                "publicacoes": publicacoes,
                "mongo_synced": False,
            }
            data["clips"][clip_id] = entry
            _atomic_write_json(self.path, data)
            pending = self._pending_snapshots(data)

        if clip_id in self._sync_mongo(pending):
            entry["mongo_synced"] = True
        return dict(entry)

    def set_discord(self, clip_id: str, info: dict[str, Any]) -> dict[str, Any] | None:
        """Persist Discord message/attachment IDs (never signed URLs) for `clip_id`."""
        sanitized = _sanitize_discord_info(info)
        pending: list[tuple[str, dict[str, Any], str]] = []
        with _locked_path(self.path):
            data, unknown = self._load()
            if unknown:
                return None
            rec = data["clips"].get(clip_id)
            if not isinstance(rec, dict):
                return None
            rec["discord"] = sanitized
            rec["mongo_synced"] = False
            _atomic_write_json(self.path, data)
            pending = self._pending_snapshots(data)

        if clip_id in self._sync_mongo(pending):
            rec["mongo_synced"] = True
        return dict(rec)

    def marcar(self, clip_id: str, **campos: Any) -> dict[str, Any] | None:
        """Update arbitrary fields on `clip_id`; returns None if missing or version unknown."""
        if "discord" in campos:
            campos["discord"] = _sanitize_discord_info(campos["discord"])
        pending: list[tuple[str, dict[str, Any], str]] = []
        with _locked_path(self.path):
            data, unknown = self._load()
            if unknown:
                return None
            rec = data["clips"].get(clip_id)
            if not isinstance(rec, dict):
                return None
            rec.update(campos)
            rec["mongo_synced"] = False
            _atomic_write_json(self.path, data)
            pending = self._pending_snapshots(data)

        if clip_id in self._sync_mongo(pending):
            rec["mongo_synced"] = True
        return dict(rec)

    def registrar_publicacao(self, clip_id: str, plataforma: str, info: dict[str, Any]) -> dict[str, Any] | None:
        """Record `publicacoes.<plataforma> = {id, url, em}` without failing on pre-F4 clips."""
        pending: list[tuple[str, dict[str, Any], str]] = []
        with _locked_path(self.path):
            data, unknown = self._load()
            if unknown:
                return None
            rec = data["clips"].get(clip_id)
            if not isinstance(rec, dict):
                return None
            pubs = dict(rec.get("publicacoes") or {})
            pubs[plataforma] = {
                "id": str(info.get("id") or ""),
                "url": str(info.get("url") or ""),
                "em": info.get("em"),
            }
            rec["publicacoes"] = pubs
            rec["mongo_synced"] = False
            _atomic_write_json(self.path, data)
            pending = self._pending_snapshots(data)

        if clip_id in self._sync_mongo(pending):
            rec["mongo_synced"] = True
        return dict(rec)

    def get(self, clip_id: str) -> dict[str, Any] | None:
        """Return record for `clip_id` from local registro.json (falling back to Mongo if configured)."""
        data = _read_json(self.path, None)
        if isinstance(data, dict):
            clips = data.get("clips")
            if isinstance(clips, dict) and isinstance(clips.get(clip_id), dict):
                return dict(clips[clip_id])
        if self.db is not None:
            try:
                doc = self.db.clips.find_one({"$or": [{"_id": clip_id}, {"clip_id": clip_id}]})
                if isinstance(doc, dict):
                    return dict(doc)
            except Exception as exc:
                if not (_is_mongo_error(exc) or isinstance(exc, OSError)):
                    raise
        return None

    def buscar(self, texto: str, limite: int = 10) -> list[dict[str, Any]]:
        """Search clips where all normalized tokens appear in titulo+hashtags+canal+legenda, newest first."""
        tokens = _norm_busca(texto).split()
        if not tokens or limite <= 0:
            return []

        merged: dict[str, dict[str, Any]] = {}
        local_data = _read_json(self.path, None)
        local_clips = local_data.get("clips") if isinstance(local_data, dict) else None
        if isinstance(local_clips, dict):
            for cid, rec in local_clips.items():
                if isinstance(rec, dict):
                    merged[cid] = dict(rec)

        if self.db is not None:
            try:
                mongo_docs: dict[str, dict[str, Any]] = {}
                for doc in self.db.clips.find({}):
                    if not isinstance(doc, dict):
                        continue
                    cid = str(doc.get("clip_id") or doc.get("_id") or "").strip()
                    if cid:
                        mongo_docs[cid] = dict(doc)
                # Prefer local unsynced entries over stale Mongo copies
                for cid, rec in merged.items():
                    if rec.get("mongo_synced") is False or cid not in mongo_docs:
                        mongo_docs[cid] = rec
                merged = mongo_docs
            except Exception as exc:
                if not (_is_mongo_error(exc) or isinstance(exc, OSError)):
                    raise

        matches = [doc for doc in merged.values() if _doc_matches_tokens(doc, tokens)]
        matches.sort(key=lambda d: (_doc_ts(d), str(d.get("clip_id") or d.get("_id") or "")), reverse=True)
        return matches[:limite]

    def liberar(
        self,
        retencao_h: Any,
        ocupados: Iterable[str] = (),
        *,
        now: Callable[[], float] | float | None = None,
    ) -> list[str]:
        """Delete local .mp4 and cached preview for confirmed Discord clips older than `retencao_h`."""
        if retencao_h is None:
            return []
        if isinstance(retencao_h, str):
            raw = retencao_h.strip()
            if not raw:
                return []
            try:
                hours = float(raw)
            except ValueError:
                return []
        elif isinstance(retencao_h, bool):
            return []
        else:
            try:
                hours = float(retencao_h)
            except (TypeError, ValueError):
                return []

        if math.isnan(hours) or math.isinf(hours) or hours < 0:
            return []

        now_ts = float(now() if callable(now) else (time.time() if now is None else now))
        cutoff = now_ts - hours * 3600.0
        busy = {str(x) for x in ocupados}
        resolved_root = self.out_root.resolve()
        freed: list[str] = []
        pending: list[tuple[str, dict[str, Any], str]] = []

        with _locked_path(self.path):
            data, unknown = self._load()
            if unknown:
                return []

            for clip_id, rec in data["clips"].items():
                if not isinstance(rec, dict):
                    continue
                if rec.get("status") != "no_discord":
                    continue
                if clip_id in busy:
                    continue
                ack_em = rec.get("ack_em")
                if ack_em is None:
                    continue
                try:
                    ack_ts = float(ack_em)
                except (TypeError, ValueError):
                    continue
                if ack_ts > cutoff:
                    continue

                rel_arquivo = str(rec.get("arquivo") or "").strip()
                if not rel_arquivo:
                    continue
                mp4_path = (resolved_root / rel_arquivo).resolve()
                if mp4_path == resolved_root or not mp4_path.is_relative_to(resolved_root) or mp4_path.suffix != ".mp4":
                    continue

                freed_bytes = 0
                if mp4_path.is_file():
                    try:
                        freed_bytes += mp4_path.stat().st_size
                    except OSError:
                        pass
                    try:
                        mp4_path.unlink(missing_ok=True)
                    except OSError as exc:
                        print(f"[registro] erro ao remover {clip_id}: {exc}", flush=True)
                        continue

                stem = sanitize_id(mp4_path.stem)
                preview_path = (mp4_path.parent / ".cache" / f"discord_{stem}.mp4").resolve()
                if preview_path.is_relative_to(resolved_root) and preview_path.is_file():
                    try:
                        freed_bytes += preview_path.stat().st_size
                    except OSError:
                        pass
                    try:
                        preview_path.unlink(missing_ok=True)
                    except OSError as exc:
                        print(f"[registro] erro ao remover previa {clip_id}: {exc}", flush=True)

                rec["status"] = "so_discord"
                rec["mongo_synced"] = False
                freed.append(clip_id)
                mb = freed_bytes / (1024 * 1024)
                print(f"[registro] liberado {clip_id} ({mb:.1f} MB)", flush=True)

            if freed:
                _atomic_write_json(self.path, data)
                pending = self._pending_snapshots(data)

        if pending:
            self._sync_mongo(pending)

        return freed
