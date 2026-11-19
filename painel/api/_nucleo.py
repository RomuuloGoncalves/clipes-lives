"""Nucleo do painel: autorizacao, status e comando. Sem I/O de rede alem do `db` recebido."""
from __future__ import annotations

import base64
import json
import math
import os
import secrets
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol, TypedDict
from urllib.parse import urlsplit

MetodoPainel = Literal["status", "comando"]


class WorkerStatus(TypedDict):
    id: Any
    alias: Any
    online: bool
    heartbeat_at: str | None
    capacity: Any
    job_atual: Any
    max_lives: Any
    razao: Any
    history: list[dict[str, Any]]


class LiveStatus(TypedDict):
    id: Any
    channel: Any
    owner: Any
    lease_until: str | None


class StatusPayload(TypedDict):
    workers: list[WorkerStatus]
    lives: list[LiveStatus]
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
MAX_TENTATIVAS_SENHA: int = 5
JANELA_BLOQUEIO_S: float = 60.0
_FALHAS_AUTH: dict[str, list[float]] = {}
_AUTH_LOCK = threading.Lock()
_CLIENT: Any = None
_CLIENT_URI: str | None = None
_CLIENT_LOCK = threading.Lock()


def autorizado(header: str | None, senha: str | None) -> bool:
    """Basic Auth: so a senha conta (usuario ignorado), comparada em bytes UTF-8."""
    if not header or not senha or not header.startswith("Basic "):
        return False
    try:
        bruto = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except ValueError:
        return False
    _, sep, dada = bruto.partition(":")
    return bool(sep) and secrets.compare_digest(dada.encode(), senha.encode())


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


def status(db: Any, now: datetime, beat_s: float = BEAT_S) -> StatusPayload:
    workers: list[WorkerStatus] = []
    for w in db.workers.find():
        hb = w.get("heartbeat_at")
        workers.append({
            "id": w["_id"], "alias": w.get("alias"),
            "online": _worker_online(w, now, beat_s),
            "heartbeat_at": _iso(hb), "capacity": w.get("capacity"),
            "job_atual": w.get("job_atual"), "max_lives": w.get("max_lives"),
            "razao": w.get("razao"), "history": _historico(w.get("history")),
        })
    lives: list[LiveStatus] = [{"id": l["_id"], "channel": l.get("channel"), "owner": l["owner"],
              "lease_until": _iso(l.get("lease_until"))}
             for l in db.lives.find({"status": "live", "owner": {"$ne": None},
                                     "lease_until": {"$gte": now}})]
    jobs: dict[str, int] = {}
    for j in db.jobs.find({"updated_at": {"$gte": now - timedelta(hours=24)}}):
        jobs[j["status"]] = jobs.get(j["status"], 0) + 1
    return {"workers": workers, "lives": lives, "jobs_24h": jobs}


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


def _chave_cliente(h: _HttpHandler) -> str:
    for hdr in ("X-Real-IP", "X-Forwarded-For"):
        val = (h.headers.get(hdr) or "").split(",")[0].strip()
        if val:
            return val
    addr = getattr(h, "client_address", None)
    return str(addr[0]) if addr else "global"


def _auth_bloqueada(chave: str, agora_mono: float) -> bool:
    corte = agora_mono - JANELA_BLOQUEIO_S
    with _AUTH_LOCK:
        recentes = [t for t in _FALHAS_AUTH.get(chave, ()) if t > corte]
        if recentes:
            _FALHAS_AUTH[chave] = recentes
        else:
            _FALHAS_AUTH.pop(chave, None)
        return len(recentes) >= MAX_TENTATIVAS_SENHA


def _registrar_falha_auth(chave: str, agora_mono: float) -> None:
    corte = agora_mono - JANELA_BLOQUEIO_S
    with _AUTH_LOCK:
        recentes = [t for t in _FALHAS_AUTH.get(chave, ()) if t > corte]
        recentes.append(agora_mono)
        _FALHAS_AUTH[chave] = recentes


def _limpar_falhas_auth(chave: str) -> None:
    with _AUTH_LOCK:
        _FALHAS_AUTH.pop(chave, None)


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
    senha = os.environ.get("PAINEL_SENHA")
    if not senha:
        return _enviar(h, *SEM_CONFIG)
    chave = _chave_cliente(h)
    agora_mono = time.monotonic()
    if _auth_bloqueada(chave, agora_mono):
        return _enviar(
            h,
            429,
            {"erro": "muitas tentativas"},
            {"Retry-After": str(int(JANELA_BLOQUEIO_S))},
        )
    if not autorizado(h.headers.get("Authorization"), senha):
        _registrar_falha_auth(chave, agora_mono)
        return _enviar(h, 401, {"erro": "nao autorizado"}, {"WWW-Authenticate": 'Basic realm="painel"'})
    _limpar_falhas_auth(chave)
    if metodo == "comando":
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
