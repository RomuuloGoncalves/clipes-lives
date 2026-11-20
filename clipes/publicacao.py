"""Orquestracao de pedidos de publicacao (fila local/Mongo, permissao, politica, cota, idempotencia)."""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, time as dt_time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config
from .monitor import ChannelPolicy, load_policy
from .registro import Registro, _atomic_write_json, _locked_path, _read_json

PLATFORMS: tuple[str, ...] = ("youtube", "tiktok")
DEFAULT_DAILY_QUOTAS: dict[str, int] = {"youtube": 6, "tiktok": 5}
QUOTA_ENV_KEYS: dict[str, str] = {"youtube": "CLIPES_YT_MAX_DIA", "tiktok": "CLIPES_TIKTOK_MAX_DIA"}
DEFAULT_TOKENS_PATH = Path("/config/tokens.json")
DEFAULT_AGENDA_TZ = "America/Sao_Paulo"
DEFAULT_AGENDA_FOLGA_MIN = 30
WORKER_STALE_S = 180.0
_UPLOAD_URL_RE = re.compile(r"https?://[^\s'\"<>]*upload[^\s'\"<>]*", re.IGNORECASE)
_HTTP_401_RE = re.compile(r"(?:^|\b(?:http(?:/[\d.]+)?|status|code|erro|error)\s*[:=]?\s*|\()401(?:\b|\))")
_ACTIVE_REQUEST_STATUSES: tuple[str, ...] = ("ok", "pending", "running")
_AGENDA_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_UNSET = object()
DEFAULT_PUB_LEASE_S = 1800.0
DEFAULT_PUB_MAX_POR_CICLO = 3
YT_QUOTA_TZ = "America/Los_Angeles"  # a cota da YouTube Data API zera a meia-noite do Pacifico


def _pub_lease_s() -> float:
    try:
        v = float(os.environ.get("CLIPES_PUB_LEASE_S", ""))
    except ValueError:
        return DEFAULT_PUB_LEASE_S
    return v if v > 0 else DEFAULT_PUB_LEASE_S


def _pub_max_por_ciclo() -> int:
    try:
        v = int(os.environ.get("CLIPES_PUB_MAX_POR_CICLO", ""))
    except ValueError:
        return DEFAULT_PUB_MAX_POR_CICLO
    return v if v > 0 else DEFAULT_PUB_MAX_POR_CICLO


def _now_ts(now: Callable[[], float] | float | None) -> float:
    if now is None:
        return time.time()
    return float(now() if callable(now) else now)


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _resolve_tz(tz: tzinfo | str | None = None) -> tuple[tzinfo, str]:
    if isinstance(tz, tzinfo):
        name = getattr(tz, "key", None) or str(tz)
        return tz, name
    raw = (tz if isinstance(tz, str) else os.environ.get("CLIPES_TZ", "")).strip()
    name = raw or DEFAULT_AGENDA_TZ
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        print(f"[agenda] fuso invalido: {name}", flush=True)
        return ZoneInfo(DEFAULT_AGENDA_TZ), DEFAULT_AGENDA_TZ


def _to_aware_tz(value: Any, tz_obj: tzinfo) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc).astimezone(tz_obj)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz_obj)
        return value.astimezone(tz_obj)
    if isinstance(value, str):
        ts = _parse_ts(value)
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz_obj)
    return None


def ler_horarios(env: Any = _UNSET) -> list[dt_time]:
    """Parse comma-separated HH:MM slots from `env` (or `CLIPES_AGENDA_HORARIOS`), logging invalid tokens."""
    raw = os.environ.get("CLIPES_AGENDA_HORARIOS", "") if env is _UNSET else (env or "")
    if not isinstance(raw, str):
        raw = str(raw)
    slots: list[dt_time] = []
    for part in raw.split(","):
        tok = part.strip()
        if not tok:
            continue
        m = _AGENDA_TIME_RE.fullmatch(tok)
        if not m:
            print(f"[agenda] horario invalido: {tok}", flush=True)
            continue
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            print(f"[agenda] horario invalido: {tok}", flush=True)
            continue
        slots.append(dt_time(hour, minute))
    return sorted(set(slots))


def proximo_horario(
    agora: datetime | float | int,
    horarios: Iterable[dt_time],
    ocupados: Iterable[Any] = (),
    tz: tzinfo | str | None = None,
    folga_min: int = DEFAULT_AGENDA_FOLGA_MIN,
) -> datetime:
    """Return the next free scheduled datetime in `tz` >= `agora + folga_min` minutes."""
    ordered = sorted(set(horarios))
    if not ordered:
        raise ValueError("horarios nao pode ser vazio")

    tz_obj, _ = _resolve_tz(tz)
    agora_tz = _to_aware_tz(agora, tz_obj)
    if agora_tz is None:
        raise ValueError("agora invalido")

    min_dt = agora_tz + timedelta(minutes=folga_min)
    occupied_keys: set[datetime] = set()
    for occ in ocupados:
        occ_dt = _to_aware_tz(occ, tz_obj)
        if occ_dt is not None:
            occupied_keys.add(occ_dt.astimezone(timezone.utc).replace(second=0, microsecond=0))

    start_date = min_dt.date()
    day_offset = 0
    while True:
        day = start_date + timedelta(days=day_offset)
        for slot in ordered:
            cand = datetime.combine(day, slot, tzinfo=tz_obj)
            if cand < min_dt:
                continue
            cand_key = cand.astimezone(timezone.utc).replace(second=0, microsecond=0)
            if cand_key in occupied_keys:
                continue
            return cand
        day_offset += 1


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


def user_allowed(user_id: str | int, allowed_ids: Iterable[str | int] | None = None) -> bool:
    """True se o ID esta na lista ou se a lista contem `*` (qualquer usuario que alcance o canal)."""
    allowed = _allowed_user_set(allowed_ids)
    return "*" in allowed or str(user_id).strip() in allowed


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
    base = config.resolve_out_dir(out_root)
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


def _quota_tz(plataforma: str) -> tzinfo:
    """Fuso que define o dia de cota: Pacifico para o YouTube (cota zera la), UTC para o TikTok."""
    return ZoneInfo(YT_QUOTA_TZ) if plataforma == "youtube" else timezone.utc


def _quota_day(plataforma: str, ts: float) -> str:
    # contadores antigos gravados com chave UTC: aceitos, a transicao dura no maximo um dia
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(_quota_tz(plataforma)).strftime("%Y-%m-%d")


def _next_quota_day_start(plataforma: str, ts: float) -> float:
    tz = _quota_tz(plataforma)
    d = datetime.fromtimestamp(ts, timezone.utc).astimezone(tz).date() + timedelta(days=1)
    return datetime.combine(d, dt_time(0, 0), tzinfo=tz).timestamp()


def _quota_db(adapted: Any, quota_path: Path | str | None = None) -> Any:
    """Mongo do contador de cota (compartilhado entre PCs) quando o store e Mongo; senao None (arquivo local).

    Com Mongo ativo todos os caminhos usam o Mongo e espelham no arquivo local; a leitura usa max(mongo, arquivo).
    """
    return adapted.db if isinstance(adapted, MongoStore) else None


def _file_used(plataforma: str, day: str, quota_path: Path | str | None, out_root: Path | str | None) -> int:
    data = _read_json(_resolve_quota_path(quota_path, out_root), {})
    return int((data.get(day) or {}).get(plataforma, 0)) if isinstance(data, dict) else 0


def _file_update(
    plataforma: str,
    day: str,
    quota_path: Path | str | None,
    out_root: Path | str | None,
    fn: Callable[[int], int],
) -> None:
    path = _resolve_quota_path(quota_path, out_root)
    with _locked_path(path):
        data = _read_json(path, {})
        if not isinstance(data, dict):
            data = {}
        day_counts = dict(data.get(day) or {})
        day_counts[plataforma] = fn(int(day_counts.get(plataforma, 0)))
        data[day] = day_counts
        _atomic_write_json(path, data)


def _mirror_file(
    plataforma: str,
    day: str,
    quota_path: Path | str | None,
    out_root: Path | str | None,
    fn: Callable[[int], int],
) -> None:
    """Espelho best-effort no arquivo local quando o contador oficial e o Mongo."""
    try:
        _file_update(plataforma, day, quota_path, out_root, fn)
    except Exception as exc:
        print(f"[publicacao] falha ao espelhar cota no arquivo local ({type(exc).__name__})", flush=True)


def quota_ok(
    plataforma: str,
    now: Callable[[], float] | float | None = None,
    *,
    quota_path: Path | str | None = None,
    out_root: Path | str | None = None,
    limit: int | None = None,
    db: Any = None,
    extra_used: int = 0,
) -> bool:
    """Return True if today's publication count (+ `extra_used` pedidos ja comprometidos) is below the daily cap.

    O dia e o do Pacifico para o YouTube e UTC para o TikTok. Com `db` (Mongo) o contador oficial fica na
    colecao `cotas_publicacao` e o uso e max(mongo, arquivo local); sem ele, so o arquivo local.
    """
    ts = _now_ts(now)
    day = _quota_day(plataforma, ts)
    used = _file_used(plataforma, day, quota_path, out_root)
    if db is not None:
        doc = db.cotas_publicacao.find_one({"_id": f"{plataforma}:{day}"}) or {}
        used = max(used, int(doc.get("usado", 0)))
    return used + max(0, int(extra_used)) < _daily_limit(plataforma, limit)


def _reserve_quota(plataforma: str, now_ts: float, *, db: Any, limit: int | None = None) -> bool:
    """Reserva atomica de 1 vaga no Mongo ($inc com filtro $lt limite); False se o dia ja esta cheio."""
    cap = _daily_limit(plataforma, limit)
    if cap <= 0:
        return False
    doc_id = f"{plataforma}:{_quota_day(plataforma, now_ts)}"
    try:
        db.cotas_publicacao.update_one({"_id": doc_id, "usado": {"$lt": cap}}, {"$inc": {"usado": 1}}, upsert=True)
    except Exception as exc:
        if type(exc).__name__ == "DuplicateKeyError":  # doc existe e o filtro $lt falhou: cheio
            return False
        raise
    return True


def _release_quota(plataforma: str, now_ts: float, *, db: Any) -> None:
    """Estorna uma reserva (upload que falhou sem publicar)."""
    doc_id = f"{plataforma}:{_quota_day(plataforma, now_ts)}"
    db.cotas_publicacao.update_one({"_id": doc_id, "usado": {"$gt": 0}}, {"$inc": {"usado": -1}})


def _record_quota(
    plataforma: str,
    now_ts: float,
    *,
    quota_path: Path | str | None = None,
    out_root: Path | str | None = None,
    db: Any = None,
    mongo_counted: bool = False,
) -> None:
    """Conta 1 publicacao. Com `db`: $inc no Mongo (salvo `mongo_counted`, ja reservado) + espelho no arquivo."""
    day = _quota_day(plataforma, now_ts)
    if db is not None:
        if not mongo_counted:
            db.cotas_publicacao.update_one({"_id": f"{plataforma}:{day}"}, {"$inc": {"usado": 1}}, upsert=True)
        _mirror_file(plataforma, day, quota_path, out_root, lambda n: n + 1)
        return
    _file_update(plataforma, day, quota_path, out_root, lambda n: n + 1)


def _mark_quota_exhausted(
    plataforma: str,
    now_ts: float,
    *,
    quota_path: Path | str | None = None,
    out_root: Path | str | None = None,
    limit: int | None = None,
    db: Any = None,
) -> None:
    """Marca o dia de cota como esgotado (a API respondeu quotaExceeded), no Mongo e/ou no arquivo local."""
    day = _quota_day(plataforma, now_ts)
    cap = _daily_limit(plataforma, limit)
    if db is not None:
        db.cotas_publicacao.update_one({"_id": f"{plataforma}:{day}"}, {"$max": {"usado": cap}}, upsert=True)
        _mirror_file(plataforma, day, quota_path, out_root, lambda n: max(n, cap))
        return
    _file_update(plataforma, day, quota_path, out_root, lambda n: max(n, cap))


def _is_quota_exhausted(res_or_text: Any) -> bool:
    """Detecta o sinal de cota esgotada do publicador (flag `quota_esgotada` ou texto do erro)."""
    if isinstance(res_or_text, dict):
        if res_or_text.get("quota_esgotada"):
            return True
        text = str(res_or_text.get("erro") or res_or_text.get("error") or "")
    else:
        text = str(res_or_text or "")
    return "quotaExceeded" in text or "uploadLimitExceeded" in text


def _committed_count(adapted: Any, plataforma: str, day: str, now_ts: float, exclude_id: str | None = None) -> int:
    """Pedidos `pending`/`running` da plataforma cujo dia de cota e `day` (sem agendamento = hoje)."""
    fn = getattr(adapted, "active_requests", None)
    if not callable(fn):
        return 0
    try:
        docs = list(fn(plataforma))
    except TypeError:
        return 0
    n = 0
    for d in docs:
        if not isinstance(d, dict) or (exclude_id is not None and d.get("_id") == exclude_id):
            continue
        ts = _parse_ts(d.get("agendar_para"))
        # atrasado/sem agendamento vai rodar agora: conta para hoje, nao para um dia passado
        eff = max(ts, now_ts) if ts is not None else now_ts
        if _quota_day(plataforma, eff) == day:
            n += 1
    return n


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
        req_id = doc["_id"]
        with _locked_path(self.path):
            data = self._load()
            existing = data["requests"].get(req_id)
            if existing is not None and existing.get("status") in _ACTIVE_REQUEST_STATUSES:
                return existing, False
            stored = dict(doc)
            data["requests"][req_id] = stored
            self._save(data)
            return stored, True

    def find_pending(
        self,
        worker_id: str | None = None,
        now: Callable[[], float] | float | None = None,
    ) -> list[dict[str, Any]]:
        now_ts = _now_ts(now)
        reqs = self._load()["requests"].values()
        out = []
        for r in reqs:
            if r.get("status") != "pending":
                continue
            if worker_id is not None and r.get("owner") != worker_id:
                continue
            sched_ts = _parse_ts(r.get("agendar_para"))
            if sched_ts is not None and sched_ts > now_ts:
                continue
            out.append(dict(r))
        return sorted(out, key=lambda r: str(r.get("criado_em") or ""))

    def scheduled_times(self, plataforma: str) -> list[datetime]:
        reqs = self._load()["requests"].values()
        out: list[datetime] = []
        for r in reqs:
            if r.get("status") != "pending" or r.get("plataforma") != plataforma:
                continue
            ts = _parse_ts(r.get("agendar_para"))
            if ts is not None:
                out.append(datetime.fromtimestamp(ts, timezone.utc))
        return out

    def active_requests(self, plataforma: str) -> list[dict[str, Any]]:
        """Pedidos `pending`/`running` da plataforma (para contar a cota ja comprometida)."""
        return [
            dict(r) for r in self._load()["requests"].values()
            if r.get("status") in ("pending", "running") and r.get("plataforma") == plataforma
        ]

    def active_clip_ids(self) -> set[str]:
        reqs = self._load()["requests"].values()
        return {
            str(r["clip_id"])
            for r in reqs
            if r.get("status") in ("pending", "running") and r.get("clip_id")
        }

    def claim_request(
        self,
        req_id: str,
        worker_id: str | None = None,
        now: Callable[[], float] | float | None = None,
    ) -> dict[str, Any] | None:
        """Atomically transition a `pending` request to `running` (with lease), returning it or None."""
        now_ts = _now_ts(now)
        with _locked_path(self.path):
            data = self._load()
            existing = data["requests"].get(req_id)
            if existing is None or existing.get("status") != "pending":
                return None
            if worker_id is not None and existing.get("owner") != worker_id:
                return None
            existing["status"] = "running"
            existing["iniciado_em"] = _dt(now_ts)
            existing["lease_until"] = _dt(now_ts + _pub_lease_s())
            self._save(data)
            return dict(existing)

    def release_expired(self, worker_id: str | None, now: Callable[[], float] | float | None = None) -> int:
        """Devolve a `pending` pedidos `running` com lease vencida (ou legados sem lease)."""
        now_ts = _now_ts(now)
        n = 0
        with _locked_path(self.path):
            data = self._load()
            for r in data["requests"].values():
                if r.get("status") != "running":
                    continue
                if worker_id is not None and r.get("owner") != worker_id:
                    continue
                lease_ts = _parse_ts(r.get("lease_until"))
                if lease_ts is not None and lease_ts >= now_ts:
                    continue
                r["status"] = "pending"
                r.pop("lease_until", None)
                n += 1
            if n:
                self._save(data)
        return n

    def update_request(self, req_id: str, fields: dict[str, Any]) -> None:
        with _locked_path(self.path):
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
        from pymongo import ReturnDocument

        req_id = doc["_id"]
        existing = self.db.publish_requests.find_one({"_id": req_id})
        if existing is not None and existing.get("status") in _ACTIVE_REQUEST_STATUSES:
            return existing, False
        if existing is not None:
            updated = self.db.publish_requests.find_one_and_update(
                {"_id": req_id, "status": {"$nin": list(_ACTIVE_REQUEST_STATUSES)}},
                {"$set": doc},
                return_document=ReturnDocument.AFTER,
            )
            if updated is not None:
                return updated, True
            found = self.db.publish_requests.find_one({"_id": req_id})
            return (found or dict(doc)), False
        try:
            self.db.publish_requests.insert_one(dict(doc))
            return dict(doc), True
        except Exception as exc:
            if type(exc).__name__ == "DuplicateKeyError":
                found = self.db.publish_requests.find_one({"_id": req_id})
                return (found or dict(doc)), False
            raise

    def find_pending(
        self,
        worker_id: str | None = None,
        now: Callable[[], float] | float | None = None,
    ) -> list[dict[str, Any]]:
        now_dt = _dt(_now_ts(now))
        query: dict[str, Any] = {
            "status": "pending",
            "$or": [
                {"agendar_para": {"$exists": False}},
                {"agendar_para": None},
                {"agendar_para": {"$lte": now_dt}},
            ],
        }
        if worker_id is not None:
            query["owner"] = worker_id
        return list(self.db.publish_requests.find(query).sort("criado_em", 1))

    def scheduled_times(self, plataforma: str) -> list[datetime]:
        docs = self.db.publish_requests.find(
            {"status": "pending", "plataforma": plataforma, "agendar_para": {"$ne": None}},
            {"agendar_para": 1},
        )
        out: list[datetime] = []
        for d in docs:
            ts = _parse_ts(d.get("agendar_para"))
            if ts is not None:
                out.append(datetime.fromtimestamp(ts, timezone.utc))
        return out

    def active_requests(self, plataforma: str) -> list[dict[str, Any]]:
        """Pedidos `pending`/`running` da plataforma (para contar a cota ja comprometida)."""
        return list(self.db.publish_requests.find(
            {"status": {"$in": ["pending", "running"]}, "plataforma": plataforma},
            {"agendar_para": 1},
        ))

    def active_clip_ids(self) -> set[str]:
        docs = self.db.publish_requests.find(
            {"status": {"$in": ["pending", "running"]}},
            {"clip_id": 1},
        )
        return {str(d["clip_id"]) for d in docs if d.get("clip_id")}

    def claim_request(
        self,
        req_id: str,
        worker_id: str | None = None,
        now: Callable[[], float] | float | None = None,
    ) -> dict[str, Any] | None:
        """Atomically transition a `pending` request to `running` (with lease), returning it or None."""
        from pymongo import ReturnDocument

        now_ts = _now_ts(now)
        query: dict[str, Any] = {"_id": req_id, "status": "pending"}
        if worker_id is not None:
            query["owner"] = worker_id
        return self.db.publish_requests.find_one_and_update(
            query,
            {"$set": {
                "status": "running",
                "iniciado_em": _dt(now_ts),
                "lease_until": _dt(now_ts + _pub_lease_s()),
            }},
            return_document=ReturnDocument.AFTER,
        )

    def release_expired(self, worker_id: str | None, now: Callable[[], float] | float | None = None) -> int:
        """Devolve a `pending` pedidos `running` com lease vencida (ou legados sem lease)."""
        now_dt = _dt(_now_ts(now))
        query: dict[str, Any] = {
            "status": "running",
            "$or": [
                {"lease_until": {"$exists": False}},
                {"lease_until": None},
                {"lease_until": {"$lt": now_dt}},
            ],
        }
        if worker_id is not None:
            query["owner"] = worker_id
        res = self.db.publish_requests.update_many(
            query,
            {"$set": {"status": "pending"}, "$unset": {"lease_until": ""}},
        )
        return int(getattr(res, "modified_count", 0) or 0)

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
            discord_meta = clip.get("discord") if isinstance(clip.get("discord"), dict) else {}
            clip_view = dict(clip)
            clip_view.setdefault("clip_id", clip_id)
            if not clip_view.get("title") and clip_view.get("titulo"):
                clip_view["title"] = clip_view["titulo"]
            if not clip_view.get("caption") and clip_view.get("legenda"):
                clip_view["caption"] = clip_view["legenda"]
            if not clip_view.get("description_long") and clip_view.get("descricao_longa"):
                clip_view["description_long"] = clip_view["descricao_longa"]
            return {
                "clip_id": clip_id,
                "clip": clip_view,
                "manifest": clip.get("manifest") or {},
                "clips_json_path": None,
                "mp4_path": None,
                "channel": str(clip.get("channel") or clip.get("canal") or "").lower(),
                "created_at": _parse_ts(
                    clip.get("created_at") if clip.get("created_at") is not None else clip.get("criado_em")
                ),
                "ready_at": _parse_ts(clip.get("ready_at")),
                "owner": clip.get("owner"),
                "thread_id": discord_meta.get("thread_id") or clip.get("thread_id"),
                "discord": discord_meta,
                "sha256": clip.get("sha256"),
                "tamanho": clip.get("tamanho"),
                "registro": clip,
            }
        return None


def _adapt_store(store: Any) -> LocalStore | MongoStore:
    if isinstance(store, (LocalStore, MongoStore)):
        return store
    if hasattr(store, "enqueue_request") and hasattr(store, "find_pending"):
        return store
    return MongoStore(store)


def _candidate_clips_paths(root: Path, reg_rec: dict[str, Any] | None) -> Iterator[Path]:
    """clips.json ao lado do mp4 apontado pelo registro primeiro; rglob (lazy) so se nao achar (A-07)."""
    hinted: Path | None = None
    arquivo = str((reg_rec or {}).get("arquivo") or "").strip()
    if arquivo:
        cand = (root / arquivo).parent / "clips.json"
        try:
            cand_res = cand.resolve()
            dentro = cand_res.is_relative_to(root.resolve())
        except OSError:
            dentro = False
        if dentro and cand.is_file():  # arquivo absoluto ou com '..' fora de out_root: ignora
            hinted = cand_res
            yield cand
    for path in sorted(root.rglob("clips.json")):
        if hinted is not None and path.resolve() == hinted:
            continue
        yield path


def find_clip(out_root: Path | str | None, clip_id: str, *, store: Any = None) -> dict[str, Any] | None:
    """Locate `clip_id` across `clips.json`, `registro.json`, and optional Mongo store."""
    cid = (clip_id or "").strip()
    if not cid:
        return None

    reg_rec: dict[str, Any] | None = None
    root = Path(out_root) if out_root is not None else None
    if root is not None and (root / "registro.json").is_file():
        reg_rec = Registro(root).get(cid)
    reg_discord = reg_rec.get("discord") if isinstance((reg_rec or {}).get("discord"), dict) else {}

    if root is not None and root.is_dir():
        jobs_by_dir = _load_done_jobs_by_output_dir(root)
        default_owner = _local_worker_id(root)
        for clips_path in _candidate_clips_paths(root, reg_rec):
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
                merged_discord = {**reg_discord, **discord_meta}
                # fallback no registro (M-20): o manifest do CLI nao tem ready_at/created_at e o embargo
                # delay_h seria contornado
                rec = reg_rec or {}
                ready_raw = (
                    entry.get("ready_at")
                    if entry.get("ready_at") is not None
                    else (job or {}).get("ready_at", manifest.get("ready_at"))
                )
                if ready_raw is None:
                    ready_raw = rec.get("ready_at")
                return {
                    "clip_id": cid,
                    "clip": entry,
                    "manifest": manifest,
                    "clips_json_path": clips_path,
                    "mp4_path": mp4_path,
                    "channel": _extract_channel(manifest, entry, job),
                    "created_at": _parse_ts(
                        (job or {}).get("created_at")
                        or manifest.get("created_at")
                        or rec.get("criado_em")
                        or rec.get("created_at")
                    ),
                    "ready_at": _parse_ts(ready_raw),
                    "owner": (job or {}).get("owner") or entry.get("owner") or manifest.get("owner") or default_owner,
                    "thread_id": discord_meta.get("thread_id") or reg_discord.get("thread_id") or manifest_discord.get("thread_id"),
                    "discord": merged_discord,
                    "sha256": (reg_rec or {}).get("sha256") or entry.get("sha256"),
                    "tamanho": (reg_rec or {}).get("tamanho") or entry.get("tamanho"),
                    "registro": reg_rec,
                }

    if reg_rec is not None:
        mp4_rel = reg_rec.get("arquivo")
        mp4_path = (root / mp4_rel) if (root is not None and mp4_rel) else None
        clip_view = {
            **reg_rec,
            "clip_id": cid,
            "title": reg_rec.get("titulo") or reg_rec.get("title") or cid,
            "caption": reg_rec.get("legenda") or reg_rec.get("caption") or "",
            "description_long": reg_rec.get("descricao_longa") or reg_rec.get("description_long") or "",
            "discord": reg_discord,
        }
        return {
            "clip_id": cid,
            "clip": clip_view,
            "manifest": {},
            "clips_json_path": None,
            "mp4_path": mp4_path,
            "channel": str(reg_rec.get("canal") or reg_rec.get("channel") or "").lower(),
            "created_at": _parse_ts(
                reg_rec.get("criado_em") if reg_rec.get("criado_em") is not None else reg_rec.get("created_at")
            ),
            "ready_at": _parse_ts(reg_rec.get("ready_at")),
            "owner": reg_rec.get("owner") or (_local_worker_id(root) if root is not None else None),
            "thread_id": reg_discord.get("thread_id"),
            "discord": reg_discord,
            "sha256": reg_rec.get("sha256"),
            "tamanho": reg_rec.get("tamanho"),
            "registro": reg_rec,
        }

    if store is not None:
        return _adapt_store(store).find_clip_doc(cid)
    return None


def _resolve_policy(policy: ChannelPolicy | None, policy_path: Path | str | None, out_root: Path | None) -> ChannelPolicy:
    if policy is not None:
        return policy
    if policy_path is not None:
        return load_policy(Path(policy_path))
    path = config.resolve_policy_path(out_root, legacy=Path("policy.json"))  # legado: cwd (como o acompanhar)
    if path.is_file():
        return load_policy(path)
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


def _occupied_slots_for_platform(adapted: Any, plat: str) -> list[datetime]:
    fn = getattr(adapted, "scheduled_times", None)
    if callable(fn):
        return list(fn(plat))
    db = getattr(adapted, "db", None)
    if db is not None and hasattr(db, "publish_requests"):
        docs = db.publish_requests.find(
            {"status": "pending", "plataforma": plat, "agendar_para": {"$ne": None}},
            {"agendar_para": 1},
        )
        out: list[datetime] = []
        for d in docs:
            ts = _parse_ts(d.get("agendar_para"))
            if ts is not None:
                out.append(datetime.fromtimestamp(ts, timezone.utc))
        return out
    return []


def _next_free_quota_slot(
    adapted: Any,
    plat: str,
    now_ts: float,
    *,
    req_id: str | None = None,
    quota_path: Path | str | None = None,
    out_root: Path | str | None = None,
    tz: tzinfo | str | None = None,
    start_ts: float | None = None,
) -> float:
    """Primeiro slot (ou inicio do proximo dia de cota) com cota livre, contando pedidos ja comprometidos.

    `start_ts` (padrao: agora) e o instante a partir do qual procurar.
    """
    tz_obj, _ = _resolve_tz(tz)
    horarios = ler_horarios()
    db = _quota_db(adapted)
    after = now_ts if start_ts is None else start_ts
    for _ in range(120):
        if horarios:
            ocupados = _occupied_slots_for_platform(adapted, plat)
            cand = proximo_horario(after, horarios, ocupados, tz_obj, folga_min=0).timestamp()
        else:
            cand = after
        extra = _committed_count(adapted, plat, _quota_day(plat, cand), now_ts, exclude_id=req_id)
        if quota_ok(plat, cand, quota_path=quota_path, out_root=out_root, db=db, extra_used=extra):
            return cand
        after = _next_quota_day_start(plat, cand)
    return after


def _is_clip_discarded(found: dict[str, Any], adapted: Any, clip_id: str) -> bool:
    reg_rec = found.get("registro")
    if isinstance(reg_rec, dict) and reg_rec.get("status") == "descartado":
        return True
    clip_entry = found.get("clip")
    if isinstance(clip_entry, dict) and clip_entry.get("status") == "descartado":
        return True
    db = getattr(adapted, "db", None)
    if db is not None and hasattr(db, "clips"):
        try:
            doc = db.clips.find_one({"$or": [{"_id": clip_id}, {"clip_id": clip_id}]})
            if isinstance(doc, dict) and doc.get("status") == "descartado":
                return True
        except Exception:
            pass
    return False


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
    agendar: bool = True,
    tz: tzinfo | str | None = None,
) -> str:
    """Validate permission, policy, idempotency, and quota, then enqueue `publish_requests`."""
    if not user_allowed(user_id, allowed_ids):
        return "sem permissao"

    adapted = _adapt_store(store)
    root = Path(out_root) if out_root is not None else getattr(adapted, "out_root", None)
    found = find_clip(root, clip_id, store=adapted)
    if found is None:
        return f"clipe {clip_id} nao encontrado"
    if _is_clip_discarded(found, adapted, clip_id):
        return "clipe descartado"

    now_ts = _now_ts(now)
    pol = _resolve_policy(policy, policy_path, root)
    channel = found.get("channel") or ""
    if not pol.permits(channel):
        return "bloqueado pela politica"

    ready_at = _effective_ready_at(found, pol)
    if ready_at is not None and now_ts < ready_at:
        ready_str = _dt(ready_at).strftime("%Y-%m-%d %H:%M UTC")
        return f"aguardar ate {ready_str}"

    recusa, aviso_gate = _gate_e_dup(found, clip_id, root, getattr(adapted, "db", None))
    if recusa:
        return recusa

    tok_map = _load_tokens(tokens, tokens_path)
    owner = worker_id or found.get("owner") or (_local_worker_id(root) if root is not None else None) or "local"
    eff_thread_id = thread_id or found.get("thread_id")
    horarios = ler_horarios() if agendar else []
    tz_obj, tz_name = _resolve_tz(tz)
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

        slot_dt: datetime | None = None
        if horarios:
            if existing is not None and existing.get("status") in ("pending", "running"):
                existing_ts = _parse_ts(existing.get("agendar_para"))
                if existing_ts is not None:
                    slot_dt = datetime.fromtimestamp(existing_ts, timezone.utc).astimezone(tz_obj)
            else:
                agora_dt = datetime.fromtimestamp(now_ts, timezone.utc).astimezone(tz_obj)
                ocupados = _occupied_slots_for_platform(adapted, plat)
                slot_dt = proximo_horario(agora_dt, horarios, ocupados, tz_obj)

        quota_ts = slot_dt.timestamp() if slot_dt is not None else now_ts
        # conta tambem os pedidos pending/running do mesmo dia de cota (M-11)
        committed = _committed_count(
            adapted, plat, _quota_day(plat, quota_ts), now_ts, exclude_id=f"{clip_id}:{plat}"
        )
        cota_cheia = not quota_ok(
            plat, quota_ts, quota_path=quota_path, out_root=root,
            db=_quota_db(adapted), extra_used=committed,
        )
        if cota_cheia and not (existing is not None and existing.get("status") in ("pending", "running")):
            # nao cria mais `adiado` terminal: agenda no proximo slot/dia com cota livre.
            # (pedidos `adiado` legados continuam ignorados por find_pending/run_pending; um novo request os recria)
            slot_ts = _next_free_quota_slot(
                adapted, plat, now_ts, req_id=f"{clip_id}:{plat}", quota_path=quota_path, out_root=root, tz=tz_obj
            )
            slot_dt = datetime.fromtimestamp(slot_ts, timezone.utc).astimezone(tz_obj)

        doc: dict[str, Any] = {
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
        }
        if slot_dt is not None:
            doc["agendar_para"] = _dt(slot_dt.timestamp())

        stored, _ = adapted.enqueue_request(doc)
        stored_sched_ts = _parse_ts(stored.get("agendar_para"))
        sched_suffix = ""
        if stored_sched_ts is not None:
            stored_dt = datetime.fromtimestamp(stored_sched_ts, timezone.utc).astimezone(tz_obj)
            sched_suffix = f"; agendado para {stored_dt.strftime('%Y-%m-%d %H:%M')} ({tz_name})"

        online, host_label = adapted.worker_status(owner, now_ts, stale_s=worker_stale_s)
        if cota_cheia:
            messages.append(f"cota diaria atingida ({plat}){sched_suffix}")
        elif not online:
            messages.append(f"{plat}: PC {host_label} offline; pedido fica na fila{sched_suffix}")
        else:
            messages.append(f"{plat}: publicacao enfileirada{sched_suffix}")

    if messages and aviso_gate:
        messages.append(aviso_gate)
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
    # devolve o dict (access_token + refresh_token rotacionado, se vier) para quem persiste os tokens
    if plataforma == "youtube":
        return dict(oauth.refresh_google_tokens(refresh_tok, tokens_path=resolved_tokens))
    if plataforma == "tiktok":
        return dict(oauth.refresh_tiktok_tokens(refresh_tok, tokens_path=resolved_tokens))
    raise ValueError(f"unknown platform: {plataforma}")


def _default_publish(
    plataforma: str,
    mp4_path: Path,
    clip: dict[str, Any],
    access_token: str,
    refresh: Callable[[], str] | None = None,
    privacy: str | None = None,
) -> dict[str, Any]:
    if plataforma == "youtube":
        from . import publicar_youtube  # lazy: module from T5

        title = str(clip.get("title") or clip.get("clip_id") or mp4_path.stem)
        desc = str(clip.get("description_long") or clip.get("caption") or title)
        return publicar_youtube.publish(mp4_path, title, desc, access_token, refresh=refresh, privacy=privacy)
    if plataforma == "tiktok":
        from . import publicar_tiktok  # lazy: module from T6

        caption = str(clip.get("caption") or clip.get("title") or mp4_path.stem)
        return publicar_tiktok.publish(mp4_path, caption, access_token, refresh=refresh, privacy=privacy)
    raise ValueError(f"unknown platform: {plataforma}")


def _call_publisher(
    fn: Callable[..., dict[str, Any]],
    plataforma: str,
    mp4_path: Path,
    clip: dict[str, Any],
    access_token: str,
    privacy: str | None = None,
) -> dict[str, Any]:
    extra = {"privacy": privacy} if privacy is not None else {}
    if plataforma == "youtube":
        title = str(clip.get("title") or clip.get("clip_id") or mp4_path.stem)
        desc = str(clip.get("description_long") or clip.get("caption") or title)
        return fn(mp4_path, title, desc, access_token, **extra)
    caption = str(clip.get("caption") or clip.get("title") or mp4_path.stem)
    return fn(mp4_path, caption, access_token, **extra)


_VISIBILIDADE: dict[str, dict[str, str]] = {
    "public": {"youtube": "public", "tiktok": "PUBLIC_TO_EVERYONE"},
    "private": {"youtube": "private", "tiktok": "SELF_ONLY"},
}


def _cfg_val(g: dict[str, str], key: str, default: str) -> str:
    """Config global do Mongo vence o env; sem nenhum dos dois, `default`."""
    return (g.get(key) or os.environ.get(key) or default).strip()


def _privacy_for(visibilidade: str, plat: str) -> str | None:
    """`padrao` (ou desconhecido) -> None: o publisher mantem o comportamento atual."""
    return _VISIBILIDADE.get(visibilidade, {}).get(plat)


def _gate_e_dup(found: dict[str, Any], clip_id: str, root: Path | None, db: Any) -> tuple[str | None, str]:
    """(recusa, aviso) para o `request`: gate `bloquear`, duplicado ou gate `avisar`."""
    g = config.config_global(db)
    clip = found.get("clip") or {}
    rec = found.get("registro") or {}
    modo = _cfg_val(g, "CLIPES_GATE", "bloquear")
    gate = clip.get("gate") or rec.get("gate") or {}
    aviso = ""
    if modo != "off" and gate.get("ok") is False:
        motivos = ", ".join(gate.get("motivos") or ["?"])
        if modo == "bloquear":
            return f"bloqueado pelo gate: {motivos}", ""
        aviso = f"aviso do gate: {motivos}"
    if root is not None and (root / "registro.json").is_file():
        def pick(k: str) -> Any:
            return clip.get(k) if clip.get(k) is not None else rec.get(k)

        try:
            ini, fim = float(pick("inicio_abs") or 0), float(pick("fim_abs") or 0)
            phash_max = int(_cfg_val(g, "CLIPES_DEDUP_PHASH", "6"))
        except (TypeError, ValueError):
            ini = fim = 0.0
            phash_max = 6
        dups = Registro(root).similares(
            found.get("channel") or "", ini, fim, str(pick("texto_hash") or ""), pick("phash"),
            phash_max, excluir=clip_id,
        )
        if dups:
            return f"duplicado de {dups[0][0]}", ""
    return None, aviso


def _is_401_error(exc: BaseException) -> bool:
    if getattr(exc, "code", None) == 401 or getattr(exc, "status", None) == 401:
        return True
    if getattr(exc, "kind", None) == "erro_auth":
        return True
    text = str(exc).lower()
    return bool(_HTTP_401_RE.search(text)) or "unauthorized" in text or "invalid_token" in text


def _is_account_error(exc: BaseException) -> bool:
    if getattr(exc, "kind", None) == "erro_conta":
        return True
    text = str(exc).lower()
    return "unaudited_client_can_only_post_to_private_accounts" in text or "erro_conta" in text


def _record_clip_publication(clips_json_path: Path | None, clip_id: str, pub_entry: dict[str, Any]) -> None:
    if clips_json_path is None or not clips_json_path.is_file():
        return
    with _locked_path(clips_json_path):
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


def _edit_published_status_message(
    *,
    found: dict[str, Any],
    adapted: Any,
    clip_id: str,
    plat: str,
    discord_client: Any | None,
    reg: Registro | None,
) -> None:
    discord_info = (
        found.get("discord")
        if isinstance(found.get("discord"), dict)
        else (found.get("registro") or {}).get("discord")
        or (found.get("clip") or {}).get("discord")
        or {}
    )
    if not isinstance(discord_info, dict):
        return
    d_thread = discord_info.get("thread_id") or found.get("thread_id")
    d_msg = discord_info.get("message_id")
    if not (d_thread and d_msg):
        return

    client = discord_client
    if client is None:
        bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        if not bot_token:
            return
        from .discord_rest import DiscordRest

        client = DiscordRest(bot_token)
    if not (hasattr(client, "get_message") and hasattr(client, "edit")):
        return

    labels_map = {"youtube": "YouTube", "tiktok": "TikTok"}
    published: list[str] = []
    reg_rec = reg.get(clip_id) if reg is not None else None
    pubs_dict = (reg_rec.get("publicacoes") if isinstance(reg_rec, dict) else None) or {}
    for p in PLATFORMS:
        if p == plat or (isinstance(pubs_dict, dict) and p in pubs_dict):
            published.append(labels_map.get(p, p))
        else:
            existing = adapted.get_request(clip_id, p)
            if existing is not None and existing.get("status") == "ok":
                published.append(labels_map.get(p, p))

    new_status = f"✅ publicado ({', '.join(published)})"
    try:
        msg = client.get_message(str(d_thread), str(d_msg))
        orig_content = str((msg or {}).get("content") or "")
        if not orig_content:
            return
        from .notificar import set_clip_status_line

        new_content = set_clip_status_line(orig_content, new_status, prepend_if_missing=True)
        if new_content != orig_content:
            client.edit(str(d_thread), str(d_msg), new_content)
    except Exception:
        pass


def _edit_status_safe(
    found: dict[str, Any] | None,
    adapted: Any,
    clip_id: str,
    plat: str,
    discord_client: Any | None,
    reg: Registro | None,
) -> None:
    """Edita o estado no Discord sem nunca falhar o pedido: erro aqui nao pode virar 'erro' do publicado."""
    if not found:
        return
    try:
        _edit_published_status_message(
            found=found, adapted=adapted, clip_id=clip_id, plat=plat, discord_client=discord_client, reg=reg,
        )
    except Exception as exc:
        print(f"[publicacao] falha ao editar estado da mensagem de {clip_id} ({type(exc).__name__})", flush=True)


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


def _download_from_discord(
    *,
    found: dict[str, Any],
    root: Path | None,
    clip_id: str,
    plat: str,
    discord_client: Any | None,
    reg: Registro | None,
    secrets: Iterable[str] = (),
) -> tuple[Path | None, str | None, str | None]:
    discord_info = (
        found.get("discord")
        if isinstance(found.get("discord"), dict)
        else (found.get("registro") or {}).get("discord")
        or (found.get("clip") or {}).get("discord")
        or {}
    )
    if not isinstance(discord_info, dict):
        discord_info = {}
    d_thread = discord_info.get("thread_id") or found.get("thread_id")
    d_msg = discord_info.get("message_id")
    d_att = discord_info.get("attachment_id")
    if not (d_thread and d_msg and d_att):
        return None, "arquivo_ausente", f"[{plat}] erro_arquivo: .mp4 ausente para {clip_id}"

    client = discord_client
    if client is None:
        bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        if not bot_token:
            return None, "arquivo_ausente", f"[{plat}] erro_arquivo: .mp4 ausente para {clip_id}"
        from .discord_rest import DiscordRest

        client = DiscordRest(bot_token)

    try:
        msg = client.get_message(d_thread, d_msg)
    except Exception as exc:
        if getattr(exc, "status", None) == 404 or "HTTP 404" in str(exc):
            if reg is not None:
                reg.marcar(clip_id, status="perdido")
            return None, "clipe_perdido", "clipe perdido: mensagem do Discord apagada"
        return None, "erro_download", _sanitize_text(str(exc), secrets)

    atts = msg.get("attachments") or []
    att = next((a for a in atts if str(a.get("id") or "") == str(d_att)), None)
    if att is None or not att.get("url"):
        if reg is not None:
            reg.marcar(clip_id, status="perdido")
        return None, "clipe_perdido", "clipe perdido: mensagem do Discord apagada"

    base_out = config.resolve_out_dir(root)
    tmp_dl = base_out / ".tmp_publicar" / f"{clip_id}_{plat}_{uuid.uuid4().hex[:8]}.mp4"
    expected_size = int(found.get("tamanho") or discord_info.get("size") or att.get("size") or 0)
    max_bytes = (expected_size + 1024) if expected_size > 0 else 25 * 1024 * 1024

    try:
        _, dl_sha = client.download(str(att["url"]), tmp_dl, max_bytes=max_bytes)
    except Exception as exc:
        tmp_dl.unlink(missing_ok=True)
        if getattr(exc, "status", None) == 404 or "HTTP 404" in str(exc):
            if reg is not None:
                reg.marcar(clip_id, status="perdido")
            return None, "clipe_perdido", "clipe perdido: mensagem do Discord apagada"
        return None, "erro_download", _sanitize_text(str(exc), secrets)

    expected_sha = (
        found.get("sha256")
        or (found.get("registro") or {}).get("sha256")
        or (found.get("clip") or {}).get("sha256")
    )
    if expected_sha and dl_sha != expected_sha:
        tmp_dl.unlink(missing_ok=True)
        return None, "sha256_divergente", "arquivo do Discord diferente do registrado"

    return tmp_dl, None, None


def run_pending(
    store: Any,
    worker_id: str | None,
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
    discord_client: Any | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Execute pending publish requests owned by `worker_id` and reply in the clip thread.

    Processa no maximo `limit` pedidos por chamada (padrao `CLIPES_PUB_MAX_POR_CICLO`, 3).
    """
    adapted = _adapt_store(store)
    root = Path(out_root) if out_root is not None else getattr(adapted, "out_root", None)
    pol = _resolve_policy(policy, policy_path, root)
    save_fn = save_tokens_fn or _save_tokens
    mongo_db = getattr(adapted, "db", None)
    reg = Registro(root, mongo=mongo_db) if root is not None else None
    outcomes: list[dict[str, Any]] = []
    # config global lida a cada ciclo; sem Mongo vale o env/padrao
    visibilidade = _cfg_val(config.config_global(mongo_db), "CLIPES_VISIBILIDADE", "padrao")

    now_fn_ts = _now_ts(now)
    max_n = limit if limit is not None and limit > 0 else _pub_max_por_ciclo()
    release_fn = getattr(adapted, "release_expired", None)
    if callable(release_fn):
        release_fn(worker_id, now=now_fn_ts)
    if isinstance(adapted, (LocalStore, MongoStore)):
        pending_reqs = adapted.find_pending(worker_id=worker_id, now=now_fn_ts)
    else:
        try:
            pending_reqs = adapted.find_pending(worker_id=worker_id, now=now_fn_ts)
        except TypeError:
            pending_reqs = adapted.find_pending(worker_id=worker_id)

    processed = 0
    for req in pending_reqs:
        if processed >= max_n:
            break
        now_ts = _now_ts(now)
        sched_ts = _parse_ts(req.get("agendar_para"))
        if sched_ts is not None and sched_ts > now_ts:
            continue
        req_id = req["_id"]
        clip_id = req["clip_id"]
        plat = req["plataforma"]
        thread_id = req.get("thread_id")

        claim_fn = getattr(adapted, "claim_request", None)
        if callable(claim_fn):
            if isinstance(adapted, (LocalStore, MongoStore)):
                claimed = claim_fn(req_id, worker_id=worker_id, now=now_ts)
            else:
                claimed = claim_fn(req_id, worker_id=worker_id)
            if claimed is None:
                continue
        processed += 1

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

        def defer_quota(motivo: str, *, proximo_dia: bool = False) -> None:
            """Cota do dia esgotada: devolve o pedido a `pending` no proximo slot com cota livre (M-11).

            `proximo_dia`: o dia atual esta esgotado por certo (API); comeca do proximo dia de cota.
            """
            nonlocal processed
            slot_ts = _next_free_quota_slot(
                adapted, plat, now_ts, req_id=req_id, quota_path=quota_path, out_root=root,
                start_ts=_next_quota_day_start(plat, now_ts) if proximo_dia else None,
            )
            adapted.update_request(req_id, {
                "status": "pending",
                "agendar_para": _dt(slot_ts),
                "lease_until": None,
                "iniciado_em": None,
            })
            processed -= 1  # nao publicou de fato: nao consome o limit
            tz_obj_q, tz_name_q = _resolve_tz(None)
            slot_str = datetime.fromtimestamp(slot_ts, timezone.utc).astimezone(tz_obj_q).strftime("%Y-%m-%d %H:%M")
            reply = f"[{plat}] cota diaria atingida ({motivo}); reagendado para {slot_str} ({tz_name_q})"
            _send_thread_reply(discord_post, eff_thread_id, reply)
            outcomes.append({
                "_id": req_id,
                "clip_id": clip_id,
                "plataforma": plat,
                "status": "pending",
                "resultado": {"erro": "cota_diaria", "agendar_para": slot_str},
                "message": reply,
            })

        quota_db = _quota_db(adapted)
        quota_reserved = False  # vaga reservada no Mongo antes do upload (M-12)
        quota_consumed = False  # upload feito (ou dia esgotado): nao estornar

        def mark_quota_exhausted() -> None:
            """API respondeu quotaExceeded: esgota o dia (M-14) e reagenda sem subir o video de novo."""
            nonlocal quota_consumed
            quota_consumed = True  # a reserva fica (dia esgotado), nao estorna
            try:
                _mark_quota_exhausted(plat, now_ts, quota_path=quota_path, out_root=root, db=quota_db)
            except Exception as exc:
                print(f"[publicacao] falha ao marcar cota esgotada ({type(exc).__name__})", flush=True)
            defer_quota(f"{plat}, API", proximo_dia=True)

        eff_thread_id = thread_id
        secrets: list[str] = []
        raw_prev = req.get("resultado")
        prev: dict[str, Any] = raw_prev if isinstance(raw_prev, dict) else {}
        partial: dict[str, Any] = dict(prev) if (prev.get("video_id") or prev.get("publish_id")) else {}
        try:
            found = find_clip(root, clip_id, store=adapted)
            eff_thread_id = thread_id or (found.get("thread_id") if found else None)
            if partial:
                # ja subiu numa tentativa anterior (crash antes do finish): nao reenviar, so a contabilidade
                prev_id = str(partial.get("video_id") or partial.get("publish_id"))
                prev_url = str(partial.get("url") or "")
                priv = str(partial.get("privacidade") or ("private" if plat == "youtube" else "SELF_ONLY"))
                try:
                    _record_clip_publication(
                        (found or {}).get("clips_json_path"),
                        clip_id,
                        {"plataforma": plat, "id": prev_id, "privacidade": priv, **({"url": prev_url} if prev_url else {})},
                    )
                except Exception as exc:
                    print(f"[publicacao] falha ao atualizar clips.json de {clip_id} ({type(exc).__name__})", flush=True)
                if reg is not None:
                    try:
                        reg.registrar_publicacao(clip_id, plat, {"id": prev_id, "url": prev_url, "em": now_ts})
                    except Exception as exc:
                        print(f"[publicacao] falha ao registrar publicacao de {clip_id} ({type(exc).__name__})", flush=True)
                finish("ok", dict(partial), f"[{plat}] ok: {prev_url or prev_id}")
                _edit_status_safe(found, adapted, clip_id, plat, discord_client, reg)
                continue

            if found is not None and _is_clip_discarded(found, adapted, clip_id):
                # pedido aceito antes do Descartar: nao publica (request() so recusa na entrada)
                finish("erro", {"erro": "clipe descartado"}, f"[{plat}] erro: clipe descartado")
                continue

            if not quota_ok(plat, now_ts, quota_path=quota_path, out_root=root, db=quota_db):
                defer_quota(plat)
                continue

            if found is None:
                finish("erro_arquivo", {"erro": "arquivo_ausente"}, f"[{plat}] erro_arquivo: .mp4 ausente para {clip_id}")
                continue

            channel = found.get("channel") or ""
            if not pol.permits(channel):
                finish("erro", {"erro": "bloqueado pela politica"}, f"[{plat}] erro: bloqueado pela politica")
                continue

            ready_at = _effective_ready_at(found, pol)
            if ready_at is not None and now_ts < ready_at:
                adapted.update_request(
                    req_id, {"status": "pending", "lease_until": None, "iniciado_em": None}
                )
                processed -= 1  # nao tomado de fato: nao consome o limit
                continue

            mp4_raw = found.get("mp4_path")
            mp4_path = Path(mp4_raw) if mp4_raw is not None else None
            tmp_dl: Path | None = None
            if mp4_path is None or not mp4_path.is_file():
                tmp_dl, dl_err, dl_reply = _download_from_discord(
                    found=found,
                    root=root,
                    clip_id=clip_id,
                    plat=plat,
                    discord_client=discord_client,
                    reg=reg,
                )
                if tmp_dl is None:
                    finish("erro_arquivo", {"erro": dl_err or "arquivo_ausente"}, dl_reply or f"[{plat}] erro_arquivo: .mp4 ausente para {clip_id}")
                    continue
                mp4_path = tmp_dl

            try:
                tok_map = _load_tokens(tokens, tokens_path)
                plat_tok = dict(tok_map.get(plat) or {})
                secrets = _secret_values(tok_map)

                def persist_refresh(new_refresh: str) -> None:
                    """Grava so o refresh_token rotacionado da plataforma, sobre o arquivo atual (sem snapshot velho)."""
                    if save_tokens_fn is None:
                        from . import oauth

                        oauth._update_platform_tokens(
                            _resolve_tokens_path(tokens_path), plat, {"refresh_token": new_refresh}
                        )
                        return
                    fresh = dict(_load_tokens(None, tokens_path))
                    fresh[plat] = {**dict(fresh.get(plat) or {}), "refresh_token": new_refresh}
                    save_fn(fresh, tokens_path)

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
                    tok_map[plat] = plat_tok
                    if new_refresh:
                        plat_tok["refresh_token"] = new_refresh
                        persist_refresh(new_refresh)
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

                if quota_db is not None:
                    if not _reserve_quota(plat, now_ts, db=quota_db):
                        defer_quota(plat)
                        continue
                    quota_reserved = True

                pub_fn = (publishers or {}).get(plat)
                clip_entry = found["clip"]
                privacy = _privacy_for(visibilidade, plat)

                def invoke(tok: str) -> dict[str, Any]:
                    if pub_fn is not None:
                        return _call_publisher(pub_fn, plat, mp4_path, clip_entry, tok, privacy)
                    return _default_publish(plat, mp4_path, clip_entry, tok, refresh=do_refresh, privacy=privacy)

                try:
                    res = invoke(str(access_token))
                except FileNotFoundError:
                    if tmp_dl is not None:
                        finish("erro_arquivo", {"erro": "arquivo_ausente"}, f"[{plat}] erro_arquivo: .mp4 ausente para {clip_id}")
                        continue
                    tmp_dl, dl_err, dl_reply = _download_from_discord(
                        found=found,
                        root=root,
                        clip_id=clip_id,
                        plat=plat,
                        discord_client=discord_client,
                        reg=reg,
                        secrets=secrets,
                    )
                    if tmp_dl is None:
                        finish("erro_arquivo", {"erro": dl_err or "arquivo_ausente"}, dl_reply or f"[{plat}] erro_arquivo: .mp4 ausente para {clip_id}")
                        continue
                    mp4_path = tmp_dl
                    try:
                        res = invoke(str(access_token))
                    except Exception as exc_retry:
                        if _is_401_error(exc_retry):
                            try:
                                access_token = do_refresh()
                                res = invoke(access_token)
                            except Exception as exc2:
                                safe_err = _sanitize_text(str(exc2), secrets)
                                if _is_quota_exhausted(safe_err):
                                    mark_quota_exhausted()
                                    continue
                                if _is_account_error(exc2):
                                    finish("erro_conta", {"erro": safe_err}, f"[{plat}] erro_conta: deixe a conta do TikTok como privada")
                                else:
                                    finish("erro_auth", {"erro": safe_err}, f"[{plat}] erro_auth: refaca clipes auth {plat}")
                                continue
                        elif _is_account_error(exc_retry):
                            safe_err = _sanitize_text(str(exc_retry), secrets)
                            finish("erro_conta", {"erro": safe_err}, f"[{plat}] erro_conta: deixe a conta do TikTok como privada")
                            continue
                        else:
                            safe_err = _sanitize_text(str(exc_retry), secrets)
                            if _is_quota_exhausted(safe_err):
                                mark_quota_exhausted()
                                continue
                            finish("erro", {"erro": safe_err}, f"[{plat}] erro: {safe_err}")
                            continue
                except Exception as exc:
                    if _is_401_error(exc):
                        try:
                            access_token = do_refresh()
                            res = invoke(access_token)
                        except Exception as exc2:
                            safe_err = _sanitize_text(str(exc2), secrets)
                            if _is_quota_exhausted(safe_err):
                                mark_quota_exhausted()
                                continue
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
                        if _is_quota_exhausted(safe_err):
                            mark_quota_exhausted()
                            continue
                        finish("erro", {"erro": safe_err}, f"[{plat}] erro: {safe_err}")
                        continue

                if isinstance(res, dict) and res.get("status") in ("erro_conta", "erro_auth", "erro"):
                    st = str(res["status"])
                    if _is_quota_exhausted(res):
                        mark_quota_exhausted()
                        continue
                    safe_err = _sanitize_text(str(res.get("error") or res.get("erro") or st), secrets)
                    if st == "erro_conta":
                        msg = f"[{plat}] erro_conta: deixe a conta do TikTok como privada"
                    elif st == "erro_auth":
                        msg = f"[{plat}] erro_auth: refaca clipes auth {plat}"
                    else:
                        msg = f"[{plat}] erro: {safe_err}"
                    finish(st, {"erro": safe_err}, msg)
                    continue

                quota_consumed = True
                aviso_cota: str | None = None
                try:
                    _record_quota(
                        plat, now_ts, quota_path=quota_path, out_root=root, db=quota_db,
                        mongo_counted=quota_reserved,
                    )
                except Exception as exc:
                    aviso_cota = _sanitize_text(f"{type(exc).__name__}: {exc}", secrets)
                    print(f"[publicacao] falha ao registrar cota de {clip_id} ({aviso_cota})", flush=True)
                pub_id = str(res.get("video_id") or res.get("publish_id") or res.get("id") or "")
                if plat == "youtube":
                    priv = privacy or "private"
                    url = str(res.get("url") or (f"https://youtube.com/shorts/{pub_id}" if pub_id else ""))
                    resultado: dict[str, Any] = {"video_id": pub_id, "privacidade": priv}
                    if url:
                        resultado["url"] = url
                else:
                    priv = privacy or "SELF_ONLY"
                    url = str(res.get("url") or "")
                    resultado = {"publish_id": pub_id, "privacidade": priv}
                    if url:
                        resultado["url"] = url
                if aviso_cota:
                    resultado["aviso_cota"] = aviso_cota  # upload ok, mas a cota nao foi contada

                if pub_id:
                    # grava o id ja publicado: se cair antes do finish, a retomada nao reenvia
                    try:
                        adapted.update_request(req_id, {"resultado": dict(resultado)})
                        partial = dict(resultado)
                    except Exception as exc:
                        if type(exc).__module__.split(".")[0] in ("pymongo", "mongomock"):
                            raise
                        print(f"[publicacao] falha ao gravar resultado parcial de {clip_id}", flush=True)
                try:
                    _record_clip_publication(
                        found.get("clips_json_path"),
                        clip_id,
                        {"plataforma": plat, "id": pub_id, "privacidade": priv, **({"url": url} if url else {})},
                    )
                except Exception as exc:
                    safe_err = _sanitize_text(f"{type(exc).__name__}: {exc}", secrets)
                    print(f"[publicacao] falha ao atualizar clips.json de {clip_id} ({safe_err})", flush=True)
                if reg is not None:
                    try:
                        reg.registrar_publicacao(
                            clip_id,
                            plat,
                            {"id": pub_id, "url": url, "em": now_ts},
                        )
                        # registrar_publicacao nao guarda a privacidade: acrescenta em publicacoes.<plat>
                        rec_pub = reg.get(clip_id) or {}
                        pubs = dict(rec_pub.get("publicacoes") or {})
                        pubs[plat] = {**dict(pubs.get(plat) or {}), "privacidade": priv}
                        reg.marcar(clip_id, publicacoes=pubs)
                    except Exception as exc:
                        safe_err = _sanitize_text(f"{type(exc).__name__}: {exc}", secrets)
                        print(f"[publicacao] falha ao registrar publicacao de {clip_id} ({safe_err})", flush=True)
                link_or_id = url or pub_id
                reply = _sanitize_text(f"[{plat}] ok: {link_or_id}", secrets)
                finish("ok", resultado, reply)
                _edit_status_safe(found, adapted, clip_id, plat, discord_client, reg)
            finally:
                if tmp_dl is not None:
                    tmp_dl.unlink(missing_ok=True)

        except Exception as exc:
            if type(exc).__module__.split(".")[0] in ("pymongo", "mongomock"):
                raise
            safe_err = _sanitize_text(f"{type(exc).__name__}: {exc}", secrets)
            print(f"[publicacao] falha inesperada em {req_id} ({safe_err})", flush=True)
            finish("erro", {**partial, "erro": safe_err}, f"[{plat}] erro: {safe_err}")
        finally:
            if quota_reserved and not quota_consumed and quota_db is not None:
                try:
                    _release_quota(plat, now_ts, db=quota_db)  # upload falhou sem publicar: devolve a vaga
                except Exception as exc:
                    print(f"[publicacao] falha ao estornar cota ({type(exc).__name__})", flush=True)

    return outcomes
