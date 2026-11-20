"""Nucleo do painel: status, comando e alvos. Sem I/O de rede alem do `db` recebido.

Sem imports de `clipes` (a Vercel so instala pymongo): o parser de alvos espelha
`acompanhar.parse_alvo` + `bot._parse_live_alvo`, e `tests/test_painel.py` confere a paridade.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol, TypedDict
from urllib.parse import parse_qs, urlparse, urlsplit

MetodoPainel = Literal["status", "comando", "alvos"]


class WorkerStatus(TypedDict):
    id: Any
    alias: Any
    online: bool
    heartbeat_at: str | None
    capacity: Any
    job_atual: Any
    max_lives: Any
    razao: Any
    slots: dict[str, Any] | None
    ocupados: dict[str, Any] | None
    fila: dict[str, Any] | None
    history: list[dict[str, Any]]


class LiveStatus(TypedDict):
    id: Any
    channel: Any
    owner: Any
    lease_until: str | None


class AlvoStatus(TypedDict):
    _id: Any
    plataforma: Any
    entrada: Any
    owner: Any
    pc_fixo: Any
    fase: Any


class StatusPayload(TypedDict):
    workers: list[WorkerStatus]
    lives: list[LiveStatus]
    alvos: list[AlvoStatus]
    jobs_24h: dict[str, int]


class _Headers(Protocol):
    def get(self, name: str, /) -> Any: ...


class _HttpHandler(Protocol):
    @property
    def headers(self) -> _Headers: ...
    rfile: Any
    wfile: Any
    client: Any

    def send_response(self, code: int, message: str | None = None) -> None: ...
    def send_header(self, keyword: str, value: str) -> None: ...
    def end_headers(self) -> None: ...


def _parse_beat_s(valor: Any, padrao: float = 20.0) -> float:
    """Converte `beat_s` positivo (env ou doc do worker), caindo no padrao se invalido."""
    if valor is None or isinstance(valor, bool):
        return padrao
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return padrao
    if not math.isfinite(num) or num <= 0:
        return padrao
    return num


BEAT_S: float = _parse_beat_s(os.environ.get("CLIPES_BEAT_S"), 20.0)
SEM_CONFIG: tuple[int, dict[str, str]] = (500, {"erro": "painel nao configurado"})
_CLIENT: Any = None
_CLIENT_URI: str | None = None
_CLIENT_LOCK = threading.Lock()


def _iso(d: Any) -> str | None:
    return d.isoformat() + "Z" if hasattr(d, "isoformat") else (d or None)


def _historico(itens: Any) -> list[dict[str, Any]]:
    """Ultimos 30 pontos do historico com `t` (datetime do Mongo) em ISO, serializavel em JSON."""
    out: list[dict[str, Any]] = []
    for p in (itens or [])[-30:]:
        if isinstance(p, dict):
            out.append({k: (_iso(v) if hasattr(v, "isoformat") else v) for k, v in p.items()})
    return out


def _worker_beat_s(worker: Mapping[str, Any], padrao: float) -> float:
    """Usa `beat_s` gravado pelo proprio worker quando valido; senao o padrao do painel."""
    return _parse_beat_s(worker.get("beat_s"), _parse_beat_s(padrao, 20.0))


def _worker_online(worker: Mapping[str, Any], now: datetime, beat_s: float) -> bool:
    hb = worker.get("heartbeat_at")
    if not isinstance(hb, datetime):
        return False
    return (now - hb).total_seconds() <= 3 * _worker_beat_s(worker, beat_s)


def _mapa_ou_none(valor: Any) -> dict[str, Any] | None:
    return dict(valor) if isinstance(valor, dict) else None


def status(db: Any, now: datetime, beat_s: float = BEAT_S) -> StatusPayload:
    workers: list[WorkerStatus] = []
    for w in db.workers.find():
        hb = w.get("heartbeat_at")
        workers.append({
            "id": w["_id"], "alias": w.get("alias"),
            "online": _worker_online(w, now, beat_s),
            "heartbeat_at": _iso(hb), "capacity": w.get("capacity"),
            "job_atual": w.get("job_atual"), "max_lives": w.get("max_lives"),
            "razao": w.get("razao"),
            "slots": _mapa_ou_none(w.get("slots")), "ocupados": _mapa_ou_none(w.get("ocupados")),
            "fila": _mapa_ou_none(w.get("fila")), "history": _historico(w.get("history")),
        })
    lives: list[LiveStatus] = [{"id": l["_id"], "channel": l.get("channel"), "owner": l["owner"],
              "lease_until": _iso(l.get("lease_until"))}
             for l in db.lives.find({"status": "live", "owner": {"$ne": None},
                                     "lease_until": {"$gte": now}})]
    alvos: list[AlvoStatus] = [{"_id": a["_id"], "plataforma": a.get("plataforma"),
                                "entrada": a.get("entrada"), "owner": a.get("owner"),
                                "pc_fixo": a.get("pc_fixo"), "fase": a.get("fase")}
                               for a in db.alvos.find({"tipo": "live", "estado": "ativo"}).sort("criado_em", 1)]
    jobs: dict[str, int] = {}
    for j in db.jobs.find({"updated_at": {"$gte": now - timedelta(hours=24)}}):
        jobs[j["status"]] = jobs.get(j["status"], 0) + 1
    return {"workers": workers, "lives": lives, "alvos": alvos, "jobs_24h": jobs}


def criar_comando(
    db: Any,
    worker_id: str | None,
    confirmar: str | None,
    now: datetime,
    beat_s: float = BEAT_S,
) -> tuple[int, dict[str, Any]]:
    """Devolve (codigo_http, corpo)."""
    from pymongo.errors import DuplicateKeyError

    if confirmar != "LIBERAR":
        return 400, {"erro": "confirmacao invalida"}
    w = db.workers.find_one({"_id": worker_id}) if worker_id else None
    if not w:
        return 404, {"erro": "worker inexistente"}
    if not _worker_online(w, now, beat_s):
        return 409, {"erro": "worker offline"}
    # comandos pendentes vencidos nao devem travar novos pedidos em 409
    db.commands.update_many(
        {"worker_id": worker_id, "status": "pending", "expira_em": {"$lt": now}},
        {"$set": {"status": "expirado"}},
    )
    try:
        res = db.commands.update_one(
            {"worker_id": worker_id, "status": "pending"},
            {"$setOnInsert": {
                "worker_id": worker_id,
                "acao": "liberar",
                "status": "pending",
                "criado_em": now,
                "expira_em": now + timedelta(minutes=10),
                "executado_em": None,
            }},
            upsert=True,
        )
    except DuplicateKeyError:
        return 409, {"erro": "comando ja pendente"}
    if res.upserted_id is None:
        return 409, {"erro": "comando ja pendente"}
    return 202, {"ok": True}


# ---- alvos (espelho de acompanhar.parse_alvo + bot._parse_live_alvo; paridade em tests/test_painel.py)

MAX_ALVOS_ATIVOS: int = 30
_CANAL_RE = re.compile(r"[a-z0-9_]{1,25}")
_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{4,32}")
_YT_HANDLE_RE = re.compile(r"@([A-Za-z0-9_.-]{1,64})")
_KICK_SLUG_RE = re.compile(r"[a-z0-9_-]{1,25}")  # espelho de acompanhar._KICK_SLUG_RE (aplicado em minusculas)
_KICK_RESERVED = frozenset({"video", "videos", "categories", "search", "auth", "clips"})
_TWITCH_HOSTS = frozenset({"twitch.tv", "www.twitch.tv", "m.twitch.tv"})
_YT_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_KICK_HOSTS = frozenset({"kick.com", "www.kick.com", "m.kick.com"})


def _alvo_dict(plataforma: str, chave: str, canal: str, url: str) -> dict[str, str]:
    return {"plataforma": plataforma, "chave": chave, "canal": canal, "url": url}


def _alvo_youtube(vid: str) -> dict[str, str]:
    return _alvo_dict("youtube", f"yt_{vid}", vid.lower(), f"https://www.youtube.com/watch?v={vid}")


def _parse_alvo(raw: Any) -> dict[str, str] | None:
    """Alvo de live (Twitch, YouTube ou Kick) -> {plataforma, chave, canal, url}; None se invalido."""
    clean = raw.strip() if isinstance(raw, str) else ""
    if not clean or any(ch.isspace() for ch in clean):
        return None
    if "://" in clean:
        sp = urlsplit(clean)
        if sp.scheme.lower() not in ("http", "https") or sp.username or sp.password:
            return None
    if "://" not in clean and "/" not in clean and "." not in clean:
        canal = clean.lstrip("@").lower()
        if not _CANAL_RE.fullmatch(canal):
            return None
        return _alvo_dict("twitch", canal, canal, f"https://www.twitch.tv/{canal}")

    url = clean if "://" in clean else f"https://{clean}"
    p = urlparse(url)
    host = p.netloc.lower()
    path = p.path.strip("/")
    if host in _TWITCH_HOSTS:
        if not path or "/" in path or not _CANAL_RE.fullmatch(path.lower()):
            return None
        return _alvo_dict("twitch", path.lower(), path.lower(), f"https://www.twitch.tv/{path.lower()}")
    if host in _YT_HOSTS:
        parts = [x for x in path.split("/") if x]
        if parts == ["watch"]:
            vid = (parse_qs(p.query).get("v") or [""])[0]
            return _alvo_youtube(vid) if _YT_ID_RE.fullmatch(vid) else None
        if len(parts) == 2 and parts[0] == "live" and _YT_ID_RE.fullmatch(parts[1]):
            return _alvo_youtube(parts[1])
        if (len(parts) == 1 or (len(parts) == 2 and parts[1] == "live")) and (m := _YT_HANDLE_RE.fullmatch(parts[0])):
            handle = m.group(1)
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", handle.lower())
            return _alvo_dict("youtube", f"yt_{safe}", handle.lower(), f"https://www.youtube.com/@{handle}/live")
        return None
    if host == "youtu.be":
        return _alvo_youtube(path) if path and "/" not in path and _YT_ID_RE.fullmatch(path) else None

    # Kick: o bot usa urlsplit (sem `;params`) e aceita so http(s) sem credenciais
    sk = urlsplit(url)
    slug = sk.path.strip("/").lower()
    if (sk.scheme.lower() in ("http", "https") and not sk.username and not sk.password
            and sk.netloc.lower() in _KICK_HOSTS and slug and "/" not in slug
            and slug not in _KICK_RESERVED and _KICK_SLUG_RE.fullmatch(slug)):
        return _alvo_dict("kick", f"kick_{slug}", slug, f"https://kick.com/{slug}")
    return None


def criar_alvo(db: Any, alvo: Any, pc: Any, now: datetime) -> tuple[int, dict[str, Any]]:
    """Adiciona alvo de live como o bot (`_live_adicionar`): mesmo doc, limite e recusa de duplicado."""
    parsed = _parse_alvo(alvo)
    if parsed is None:
        return 400, {"erro": "alvo invalido"}
    chave = parsed["chave"]
    if db.alvos.find_one({"_id": chave, "estado": "ativo"}) is not None:
        return 409, {"erro": "alvo ja acompanhado"}
    if db.alvos.count_documents({"tipo": "live", "estado": "ativo"}) >= MAX_ALVOS_ATIVOS:
        return 409, {"erro": f"limite de {MAX_ALVOS_ATIVOS} alvos ativos atingido"}
    pc_fixo = pc.strip() if isinstance(pc, str) else ""
    db.alvos.replace_one({"_id": chave}, {
        "_id": chave, "tipo": "live", "plataforma": parsed["plataforma"],
        "entrada": parsed["canal"] if parsed["plataforma"] == "twitch" else parsed["url"],
        "canal": parsed["canal"], "url": None, "estado": "ativo",
        "pc_fixo": pc_fixo or None, "owner": None, "lease_until": None, "fase": "sem vaga",
        "criado_por": "painel", "criado_em": now, "erro": None,
    }, upsert=True)
    return 201, {"ok": True, "_id": chave}


def remover_alvo(db: Any, alvo_id: Any) -> tuple[int, dict[str, Any]]:
    """Marca `estado=removido` como o bot (`_live_remover`); o Supervisor para o alvo no proximo ciclo."""
    if not isinstance(alvo_id, str) or not alvo_id:
        return 400, {"erro": "alvo invalido"}
    res = db.alvos.update_one({"_id": alvo_id, "tipo": "live", "estado": "ativo"},
                              {"$set": {"estado": "removido"}})
    if res.matched_count == 0:
        return 404, {"erro": "alvo nao encontrado"}
    return 200, {"ok": True}


def gerir_alvo(db: Any, corpo: Mapping[str, Any], now: datetime) -> tuple[int, dict[str, Any]]:
    acao = corpo.get("acao")
    if acao == "adicionar":
        return criar_alvo(db, corpo.get("alvo"), corpo.get("pc"), now)
    if acao == "remover":
        return remover_alvo(db, corpo.get("id"))
    return 400, {"erro": "acao invalida"}


def _origem_valida(h: _HttpHandler) -> bool:
    origin = (h.headers.get("Origin") or "").strip()
    alvo = origin or (h.headers.get("Referer") or "").strip()
    if not alvo:
        return True
    parsed = urlsplit(alvo)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    hosts = {
        v.strip().lower()
        for v in (h.headers.get("Host"), h.headers.get("X-Forwarded-Host"))
        if v and v.strip()
    }
    return bool(hosts) and parsed.netloc.lower() in hosts


def _obter_db(client: Any) -> Any:
    """Mesma regra dos workers: banco da URI tem prioridade; senao PAINEL_MONGO_DB ou clipes_lives."""
    default = (os.environ.get("PAINEL_MONGO_DB") or "").strip() or "clipes_lives"
    get_default = getattr(client, "get_default_database", None)
    if callable(get_default):
        return get_default(default=default)
    return client[default]


def tratar(h: _HttpHandler, metodo: MetodoPainel, now: datetime | None = None) -> None:
    """Logica comum dos handlers (h = BaseHTTPRequestHandler). Mantida aqui para status/comando."""
    uri = os.environ.get("PAINEL_MONGO_URI")
    if not uri:
        return _enviar(h, *SEM_CONFIG)
    if metodo in ("comando", "alvos"):
        if not _origem_valida(h):
            return _enviar(h, 403, {"erro": "origem nao permitida"})
        ctype = (h.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return _enviar(h, 415, {"erro": "Content-Type deve ser application/json"})
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        client = h.client or _abrir(uri)
        db = _obter_db(client)
        if metodo == "status":
            return _enviar(h, 200, status(db, now))
        n = int(h.headers.get("Content-Length") or 0)
        try:
            corpo = json.loads(h.rfile.read(n) or b"{}")
        except ValueError:
            corpo = {}
        corpo = corpo if isinstance(corpo, dict) else {}
        if metodo == "alvos":
            return _enviar(h, *gerir_alvo(db, corpo, now))
        return _enviar(h, *criar_comando(db, corpo.get("worker_id"), corpo.get("confirmar"), now))
    except Exception:  # nunca vazar detalhe (URI/host) na resposta
        return _enviar(h, 500, {"erro": "erro interno"})


def _abrir(uri: str) -> Any:
    global _CLIENT, _CLIENT_URI
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT_URI != uri:
            if _CLIENT is not None:
                try:
                    _CLIENT.close()
                except Exception:
                    pass
                _CLIENT = None
            from pymongo import MongoClient
            _CLIENT = MongoClient(uri, maxPoolSize=1, serverSelectionTimeoutMS=5000)
            _CLIENT_URI = uri
        return _CLIENT


def _enviar(
    h: _HttpHandler,
    codigo: int,
    corpo: Mapping[str, Any],
    extra: Mapping[str, str] | None = None,
) -> None:
    dados = json.dumps(corpo).encode()
    h.send_response(codigo)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(dados)))
    for k, v in (extra or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(dados)
