"""Nucleo do painel: autorizacao, status e comando. Sem I/O de rede alem do `db` recebido."""
import base64
import json
import os
import secrets
import threading
import time
from datetime import timedelta
from urllib.parse import urlsplit

BEAT_S = int(os.environ.get("CLIPES_BEAT_S", "20"))
SEM_CONFIG = (500, {"erro": "painel nao configurado"})
MAX_TENTATIVAS_SENHA = 5
JANELA_BLOQUEIO_S = 60.0
_FALHAS_AUTH: dict[str, list[float]] = {}
_AUTH_LOCK = threading.Lock()


def autorizado(header, senha):
    """Basic Auth: so a senha conta (usuario ignorado), comparada em bytes UTF-8."""
    if not header or not senha or not header.startswith("Basic "):
        return False
    try:
        bruto = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except ValueError:
        return False
    _, sep, dada = bruto.partition(":")
    return bool(sep) and secrets.compare_digest(dada.encode(), senha.encode())


def _iso(d):
    return d.isoformat() + "Z" if d else None


def status(db, now, beat_s=BEAT_S):
    workers = []
    for w in db.workers.find():
        hb = w.get("heartbeat_at")
        workers.append({
            "id": w["_id"], "alias": w.get("alias"),
            "online": bool(hb) and (now - hb).total_seconds() <= 3 * beat_s,
            "heartbeat_at": _iso(hb), "capacity": w.get("capacity"),
            "job_atual": w.get("job_atual"), "max_lives": w.get("max_lives"),
            "razao": w.get("razao"), "history": (w.get("history") or [])[-30:],
        })
    lives = [{"id": l["_id"], "channel": l.get("channel"), "owner": l["owner"],
              "lease_until": _iso(l.get("lease_until"))}
             for l in db.lives.find({"status": "live", "owner": {"$ne": None}})]
    jobs = {}
    for j in db.jobs.find({"updated_at": {"$gte": now - timedelta(hours=24)}}):
        jobs[j["status"]] = jobs.get(j["status"], 0) + 1
    return {"workers": workers, "lives": lives, "jobs_24h": jobs}


def criar_comando(db, worker_id, confirmar, now):
    """Devolve (codigo_http, corpo)."""
    if confirmar != "LIBERAR":
        return 400, {"erro": "confirmacao invalida"}
    if not worker_id or not db.workers.find_one({"_id": worker_id}):
        return 404, {"erro": "worker inexistente"}
    if db.commands.find_one({"worker_id": worker_id, "status": "pending"}):
        return 409, {"erro": "comando ja pendente"}
    db.commands.insert_one({"worker_id": worker_id, "acao": "liberar", "status": "pending",
                            "criado_em": now, "executado_em": None})
    return 202, {"ok": True}


def _chave_cliente(h):
    for hdr in ("X-Real-IP", "X-Forwarded-For"):
        val = (h.headers.get(hdr) or "").split(",")[0].strip()
        if val:
            return val
    addr = getattr(h, "client_address", None)
    return str(addr[0]) if addr else "global"


def _auth_bloqueada(chave, agora_mono):
    corte = agora_mono - JANELA_BLOQUEIO_S
    with _AUTH_LOCK:
        recentes = [t for t in _FALHAS_AUTH.get(chave, ()) if t > corte]
        if recentes:
            _FALHAS_AUTH[chave] = recentes
        else:
            _FALHAS_AUTH.pop(chave, None)
        return len(recentes) >= MAX_TENTATIVAS_SENHA


def _registrar_falha_auth(chave, agora_mono):
    corte = agora_mono - JANELA_BLOQUEIO_S
    with _AUTH_LOCK:
        recentes = [t for t in _FALHAS_AUTH.get(chave, ()) if t > corte]
        recentes.append(agora_mono)
        _FALHAS_AUTH[chave] = recentes


def _limpar_falhas_auth(chave):
    with _AUTH_LOCK:
        _FALHAS_AUTH.pop(chave, None)


def _origem_valida(h):
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


def tratar(h, metodo, now=None):
    """Logica comum dos handlers (h = BaseHTTPRequestHandler). Mantida aqui para status/comando."""
    from datetime import datetime, timezone
    senha, uri = os.environ.get("PAINEL_SENHA"), os.environ.get("PAINEL_MONGO_URI")
    if not senha or not uri:
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
        db = client[os.environ.get("PAINEL_MONGO_DB", "clipes")]
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


def _abrir(uri):
    from pymongo import MongoClient
    return MongoClient(uri, maxPoolSize=1, serverSelectionTimeoutMS=5000)


def _enviar(h, codigo, corpo, extra=None):
    dados = json.dumps(corpo).encode()
    h.send_response(codigo)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(dados)))
    for k, v in (extra or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(dados)
