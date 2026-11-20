"""Nucleo do painel: status, comando, alvos e config. Sem I/O de rede alem do `db` recebido.

Sem imports de `clipes` (a Vercel so instala pymongo): o parser de alvos espelha
`acompanhar.parse_alvo` + `bot._parse_live_alvo` e `CONFIG_SCHEMA` espelha
`clipes.config.CONFIG_SCHEMA`; `tests/test_painel.py` confere a paridade.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import threading
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol, TypedDict
from urllib.parse import parse_qs, urlparse, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MetodoPainel = Literal["status", "comando", "alvos", "config", "config_global"]


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
    config: dict[str, str]
    history: list[dict[str, Any]]


class LiveStatus(TypedDict):
    id: Any
    channel: Any
    owner: Any
    lease_until: str | None


class AlvoStatus(TypedDict):
    _id: Any
    tipo: str          # "live" | "vod"
    plataforma: Any
    entrada: Any       # entrada or url or _id
    url: Any           # URL canonica para VODs (ou None para lives sem url)
    owner: Any
    pc_fixo: Any
    fase: Any


class VodRecenteStatus(TypedDict):
    _id: Any
    plataforma: Any
    entrada: Any
    url: Any
    pc_fixo: Any
    estado: str        # "feito" | "falhou"
    fase: Any          # "concluido" | "falhou"
    erro: Any          # mensagem sanitizada ou None
    finalizado_em: str | None


class StatusPayload(TypedDict):
    workers: list[WorkerStatus]
    lives: list[LiveStatus]
    alvos: list[AlvoStatus]
    vods_recentes: list[VodRecenteStatus]
    jobs_24h: dict[str, int]
    config_schema: dict[str, dict[str, Any]]
    config_global: dict[str, str]
    clips_recentes: list[dict[str, Any]]
    visibilidade_efetiva: str


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


def _config_str_dict(valor: Any) -> dict[str, str]:
    if not isinstance(valor, Mapping):
        return {}
    return {
        str(k): str(v)
        for k, v in valor.items()
        if str(k) in CONFIG_SCHEMA and v is not None and str(v).strip() != ""
    }


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
            "fila": _mapa_ou_none(w.get("fila")),
            "config": _config_str_dict(w.get("config")),
            "history": _historico(w.get("history")),
        })
    workers.sort(key=lambda w: (str(w.get("alias") or w.get("id") or "").lower(), str(w.get("id") or "")))
    lives: list[LiveStatus] = [{"id": l["_id"], "channel": l.get("channel"), "owner": l["owner"],
              "lease_until": _iso(l.get("lease_until"))}
             for l in db.lives.find({"status": "live", "owner": {"$ne": None},
                                     "lease_until": {"$gte": now}})]
    # Alvos ativos: live + VOD, ordenados por criado_em
    alvos: list[AlvoStatus] = []
    for a in db.alvos.find({"tipo": {"$in": ["live", "vod"]}, "estado": "ativo"}).sort("criado_em", 1):
        entrada = a.get("entrada") or a.get("url") or a["_id"]
        alvos.append({
            "_id": a["_id"],
            "tipo": a.get("tipo", "live"),
            "plataforma": a.get("plataforma"),
            "entrada": entrada,
            "url": a.get("url"),
            "owner": a.get("owner"),
            "pc_fixo": a.get("pc_fixo"),
            "fase": a.get("fase"),
        })
    # VODs recentes (ultimas 24h, ate 10)
    vods_recentes: list[VodRecenteStatus] = []
    cutoff = now - timedelta(hours=24)
    for v in db.alvos.find({
        "tipo": "vod",
        "estado": {"$in": ["feito", "falhou"]},
        "finalizado_em": {"$gte": cutoff}
    }).sort("finalizado_em", -1).limit(10):
        vods_recentes.append({
            "_id": v["_id"],
            "plataforma": v.get("plataforma"),
            "entrada": v.get("entrada") or v.get("url") or v["_id"],
            "url": v.get("url"),
            "pc_fixo": v.get("pc_fixo"),
            "estado": v.get("estado"),
            "fase": v.get("fase"),
            "erro": v.get("erro"),
            "finalizado_em": _iso(v.get("finalizado_em")),
        })
    jobs: dict[str, int] = {}
    for j in db.jobs.find({"updated_at": {"$gte": now - timedelta(hours=24)}}):
        jobs[j["status"]] = jobs.get(j["status"], 0) + 1
    config_global = _config_str_dict((db.config_global.find_one({"_id": "global"}) or {}).get("config"))
    # Clipes recentes: so campos de exibicao (sem caminhos locais nem segredos)
    clips_recentes: list[dict[str, Any]] = []
    for c in db.clips.find().sort("criado_em", -1).limit(50):
        pubs = c.get("publicacoes") if isinstance(c.get("publicacoes"), dict) else {}
        clips_recentes.append({
            "_id": c["_id"],
            "titulo": c.get("titulo"),
            "canal": c.get("canal"),
            "gate": c.get("gate"),
            "publicacoes": {
                plat: {"privacidade": p.get("privacidade"), "url": p.get("url")}
                for plat, p in pubs.items() if isinstance(p, dict)
            },
            "metricas": c.get("metricas"),
        })
    return {
        "workers": workers,
        "lives": lives,
        "alvos": alvos,
        "vods_recentes": vods_recentes,
        "jobs_24h": jobs,
        "config_schema": CONFIG_SCHEMA,
        "config_global": config_global,
        "clips_recentes": clips_recentes,
        "visibilidade_efetiva": config_global.get("CLIPES_VISIBILIDADE", "padrao"),
    }


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
MAX_VODS_ATIVOS: int = 20
_CANAL_RE = re.compile(r"[a-z0-9_]{1,25}")
_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{4,32}")
_YT_HANDLE_RE = re.compile(r"@([A-Za-z0-9_.-]{1,64})")
_KICK_SLUG_RE = re.compile(r"[a-z0-9_-]{1,25}")  # espelho de acompanhar._KICK_SLUG_RE (aplicado em minusculas)
_KICK_RESERVED = frozenset({"video", "videos", "categories", "search", "auth", "clips"})
_TWITCH_HOSTS = frozenset({"twitch.tv", "www.twitch.tv", "m.twitch.tv"})
_YT_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
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
    if host == "youtu.be":
        return _alvo_youtube(path) if path and "/" not in path and _YT_ID_RE.fullmatch(path) else None
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

    # Kick: o bot usa urlsplit (sem `;params`) e aceita so http(s) sem credenciais
    sk = urlsplit(url)
    slug = sk.path.strip("/").lower()
    if (sk.scheme.lower() in ("http", "https") and not sk.username and not sk.password
            and sk.netloc.lower() in _KICK_HOSTS and slug and "/" not in slug
            and slug not in _KICK_RESERVED and _KICK_SLUG_RE.fullmatch(slug)):
        return _alvo_dict("kick", f"kick_{slug}", slug, f"https://kick.com/{slug}")
    return None


# ---- VOD parsing ----

_TWITCH_VOD_RE = re.compile(r"(?:www\.|m\.)?twitch\.tv/(?:videos?/|[^/]+/videos?/|clips?/|[^/]+/clip/)(\d+|[a-zA-Z0-9_-]+)")
_KICK_VOD_RE = re.compile(r"(?:www\.|m\.)?kick\.com/(?:video/|[^/]+/videos?/)(\d+|[a-zA-Z0-9_-]+)")
_KICK_CLIP_RE = re.compile(r"(?:www\.|m\.)?kick\.com/([^/?#]+)/clips/([A-Za-z0-9_-]+)")
_YT_SHORTS_RE = re.compile(r"(?:www\.|m\.)?youtube\.com/shorts/([A-Za-z0-9_-]{4,32})")


def _is_ssrf_host(host: str) -> bool:
    """Verifica se o host e perigoso (SSRF): localhost, IPs literais, .local, .internal."""
    if not host:
        return True
    host_lower = host.lower()
    if host_lower in ("localhost", "localhost.localdomain"):
        return True
    if host_lower.endswith(".local") or host_lower.endswith(".internal") or host_lower.endswith(".localhost"):
        return True
    # IP literal (IPv4 ou IPv6)
    try:
        ipaddress.ip_address(host_lower.strip("[]"))
        return True
    except ValueError:
        return False


def _canonicalize_vod_url(url: str) -> str:
    """Canonicaliza URL de VOD removendo fragmento e normalizando."""
    p = urlparse(url)
    # Remove fragmento
    clean = p._replace(fragment="").geturl()
    # Remove barra final supérflua
    if clean.endswith("/") and urlparse(clean).path == "/":
        clean = clean[:-1]
    return clean


def _vod_hash(url: str) -> str:
    """Gera hash curto para _id do VOD."""
    return f"vod:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]}"


def _parse_vod_alvo(raw: str) -> dict[str, str] | None:
    """Parseia URL de VOD explicita (Twitch, Kick, YouTube Shorts, generica).
    Retorna {plataforma, chave, entrada, url} ou None se invalido."""
    clean = raw.strip()
    if not clean:
        return None
    # SSRF: rejeita credenciais embutidas
    if "@" in clean and "://" in clean:
        sp = urlsplit(clean)
        if sp.username or sp.password:
            return None
    # Adiciona https:// se necessario
    if "://" not in clean:
        clean = f"https://{clean}"
    sp = urlsplit(clean)
    if sp.scheme.lower() not in ("http", "https"):
        return None
    host = sp.netloc.lower()
    path = sp.path.strip("/")
    if _is_ssrf_host(sp.hostname or ""):
        return None

    # Twitch VOD/Clip
    m = _TWITCH_VOD_RE.search(clean)
    if m and host in _TWITCH_HOSTS:
        vod_id = m.group(1)
        canonical = f"https://www.twitch.tv/videos/{vod_id}" if vod_id.isdigit() else f"https://clips.twitch.tv/{vod_id}"
        return {
            "plataforma": "twitch",
            "chave": _vod_hash(canonical),
            "entrada": canonical,
            "url": canonical,
        }

    # Twitch clip em clips.twitch.tv/<slug>
    if host == "clips.twitch.tv" and path and "/" not in path and _YT_ID_RE.fullmatch(path):
        canonical = f"https://clips.twitch.tv/{path}"
        return {"plataforma": "twitch", "chave": _vod_hash(canonical), "entrada": canonical, "url": canonical}

    # Kick clip: mantem o caminho original /<canal>/clips/<id>
    m = _KICK_CLIP_RE.search(clean)
    if m and host in _KICK_HOSTS:
        canonical = f"https://kick.com/{m.group(1)}/clips/{m.group(2)}"
        return {"plataforma": "kick", "chave": _vod_hash(canonical), "entrada": canonical, "url": canonical}

    # Kick VOD
    m = _KICK_VOD_RE.search(clean)
    if m and host in _KICK_HOSTS:
        vod_id = m.group(1)
        canonical = f"https://kick.com/video/{vod_id}"
        return {
            "plataforma": "kick",
            "chave": _vod_hash(canonical),
            "entrada": canonical,
            "url": canonical,
        }

    # YouTube Shorts
    m = _YT_SHORTS_RE.search(clean)
    if m and host in _YT_HOSTS:
        shorts_id = m.group(1)
        canonical = f"https://www.youtube.com/shorts/{shorts_id}"
        return {
            "plataforma": "youtube",
            "chave": _vod_hash(canonical),
            "entrada": canonical,
            "url": canonical,
        }

    # Plataforma generica: URL publica HTTP(S) com dominio valido e path nao vazio
    # So aceita generico para dominios NAO conhecidos como Twitch/Kick/YouTube
    # (para canais conhecidos, so VODs com padroes especificos sao aceitos acima)
    _KNOWN_LIVE_HOSTS = _TWITCH_HOSTS | _YT_HOSTS | _KICK_HOSTS
    _RESERVED_TLDS = frozenset({"invalid", "test", "localhost", "example", "local", "internal"})
    host_parts = host.split(".")
    if (
        path
        and host not in _KNOWN_LIVE_HOSTS
        and len(host_parts) >= 2
        and host_parts[-1] not in _RESERVED_TLDS
        and not _is_ssrf_host(sp.hostname or "")
    ):
        canonical = _canonicalize_vod_url(clean)
        # Extrai host limpo para plataforma
        plat_host = re.sub(r"^(?:www\.|m\.)", "", host)[:32]
        return {
            "plataforma": plat_host,
            "chave": _vod_hash(canonical),
            "entrada": canonical,
            "url": canonical,
        }

    return None


def _yt_video_url(live: dict[str, str]) -> bool:
    return live["plataforma"] == "youtube" and live["url"].startswith("https://www.youtube.com/watch?v=")


def _parse_alvo_painel(raw: Any, tipo: str = "auto") -> dict[str, Any] | None:
    """Parser unificado para painel: aceita live, VOD ou auto.
    Retorna dict com tipo, plataforma, chave, entrada, url, auto_vod, ou None se invalido."""
    if tipo not in ("auto", "live", "vod"):
        return None
    clean = raw.strip() if isinstance(raw, str) else ""
    if not clean or any(ch.isspace() for ch in clean):
        return None

    # Tenta parsear como VOD primeiro (se tipo vod ou auto)
    if tipo in ("auto", "vod"):
        vod_parsed = _parse_vod_alvo(clean)
        if vod_parsed:
            return {**vod_parsed, "tipo": "vod", "auto_vod": False}
        if tipo == "vod":
            # video do YouTube (watch/youtu.be/live) vira VOD canonico; login/canal/@handle nao
            live = _parse_alvo(clean)
            if live and _yt_video_url(live):
                return {"plataforma": "youtube", "chave": _vod_hash(live["url"]), "entrada": live["url"],
                        "url": live["url"], "tipo": "vod", "auto_vod": False}
            return None

    # Tenta parsear como live
    live_parsed = _parse_alvo(clean)
    if live_parsed:
        if tipo == "live":
            return {**live_parsed, "tipo": "live", "auto_vod": False}
        return {**live_parsed, "tipo": "live", "auto_vod": _yt_video_url(live_parsed)}

    return None


def criar_alvo(db: Any, alvo: Any, pc: Any, now: datetime, tipo: str = "auto") -> tuple[int, dict[str, Any]]:
    """Adiciona alvo (live ou VOD) com suporte a tipo auto/live/vod e pc_fixo."""
    parsed = _parse_alvo_painel(alvo, tipo)
    if parsed is None:
        return 400, {"erro": "alvo invalido"}
    if parsed.get("tipo") == "vod":
        # VOD: usa chave hash, verifica limite de VODs
        chave = parsed["chave"]
        # Duplicata: por _id OU por url
        if db.alvos.find_one({"$or": [{"_id": chave}, {"tipo": "vod", "url": parsed["url"]}], "estado": "ativo"}) is not None:
            return 409, {"erro": "alvo ja acompanhado"}
        if db.alvos.count_documents({"tipo": "vod", "estado": "ativo"}) >= MAX_VODS_ATIVOS:
            return 409, {"erro": f"limite de {MAX_VODS_ATIVOS} VODs ativos atingido"}
        pc_fixo = pc.strip() if isinstance(pc, str) else ""
        doc = {
            "_id": chave, "tipo": "vod", "plataforma": parsed["plataforma"],
            "entrada": parsed["entrada"], "url": parsed["url"], "estado": "ativo",
            "pc_fixo": pc_fixo or None, "owner": None, "lease_until": None, "fase": "na fila",
            "criado_por": "painel", "criado_em": now, "erro": None, "auto_vod": False,
        }
        db.alvos.replace_one({"_id": chave}, doc, upsert=True)
        return 201, {"ok": True, "_id": chave, "tipo": "vod"}

    # Live: usa chave do parser original
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
        "criado_por": "painel", "criado_em": now, "erro": None, "auto_vod": parsed.get("auto_vod", False),
    }, upsert=True)
    return 201, {"ok": True, "_id": chave, "tipo": "live"}


def remover_alvo(db: Any, alvo_id: Any) -> tuple[int, dict[str, Any]]:
    """Marca `estado=removido` e `fase=cancelado` para live ou VOD ativo."""
    if not isinstance(alvo_id, str) or not alvo_id:
        return 400, {"erro": "alvo invalido"}
    res = db.alvos.update_one(
        {"_id": alvo_id, "tipo": {"$in": ["live", "vod"]}, "estado": "ativo"},
        {"$set": {"estado": "removido", "fase": "cancelado"}}
    )
    if res.matched_count == 0:
        return 404, {"erro": "alvo nao encontrado"}
    return 200, {"ok": True}


def gerir_alvo(db: Any, corpo: Mapping[str, Any], now: datetime) -> tuple[int, dict[str, Any]]:
    acao = corpo.get("acao")
    if acao == "adicionar":
        return criar_alvo(db, corpo.get("alvo"), corpo.get("pc"), now, corpo.get("tipo", "auto"))
    if acao == "remover":
        return remover_alvo(db, corpo.get("id"))
    return 400, {"erro": "acao invalida"}


# ---- config por PC (espelho de clipes.config.CONFIG_SCHEMA; paridade em tests/test_painel.py)

_TRUE = ("1", "true", "yes", "on", "sim")
_FALSE = ("0", "false", "no", "off", "nao")
_SLOTS_DEFAULT = {"twitch": 1, "kick": 1, "youtube": 1, "busca": 1}
_SLOTS_INT_RE = re.compile(r"[0-9]+")
_SCHEDULE_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_INT_STR_RE = re.compile(r"^-?[0-9]+$")
_PC_ALIAS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
_FORBIDDEN_SUBSTRINGS = ("SECRET", "TOKEN", "KEY", "PASS", "URI")

FORBIDDEN_ENV_KEYS: frozenset[str] = frozenset({
    "CLIPES_MONGO_URI",
    "PAINEL_MONGO_URI",
    "PAINEL_MONGO_DB",
    "PAINEL_SENHA",
    "CLIPES_SECRETS",
    "CLIPES_CONFIG_DIR",
    "DISCORD_BOT_TOKEN",
    "DISCORD_CHANNEL_ID",
    "DISCORD_GUILD_ID",
    "DISCORD_ALLOWED_USER_IDS",
    "DISCORD_LOG_CHANNEL_ID",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "TIKTOK_CLIENT_KEY",
    "TIKTOK_CLIENT_ID",
    "TIKTOK_CLIENT_SECRET",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "GEMINI_API_KEYS",
    "GROQ_API_KEY",
})

CONFIG_SCHEMA: dict[str, dict[str, Any]] = {
    # --- lives (7) ---
    "CLIPES_SLOTS": {
        "grupo": "lives",
        "tipo": "slots",
        "default": "twitch=1,kick=1,youtube=1,busca=1",
        "descricao": "Vagas simultaneas por plataforma e busca (twitch=N,kick=N,youtube=N,busca=N)",
    },
    "CLIPES_CPU_THREADS": {
        "grupo": "lives",
        "tipo": "int",
        "default": "",
        "min": 1,
        "max": 64,
        "descricao": "Threads de CPU para Whisper, llama-server e FFmpeg (vazio = auto cgroup)",
    },
    "CLIPES_MIN_FREE_GB": {
        "grupo": "lives",
        "tipo": "float",
        "default": "5",
        "min": 0.0,
        "max": 1000.0,
        "descricao": "Espaco livre minimo em disco (GB) antes de pausar captura",
    },
    "CLIPES_BUFFER_CAP_GB": {
        "grupo": "lives",
        "tipo": "float",
        "default": "1.5",
        "min": 0.2,
        "max": 50.0,
        "descricao": "Limite maximo (GB) do buffer circular de lives",
    },
    "CLIPES_LIVE_CAPTURA_MORTA_S": {
        "grupo": "lives",
        "tipo": "float",
        "default": "900",
        "min": 60.0,
        "max": 7200.0,
        "descricao": "Tempo sem novos bytes (s) para reiniciar captura travada",
    },
    "CLIPES_BEAT_S": {
        "grupo": "lives",
        "tipo": "float",
        "default": "20",
        "min": 5.0,
        "max": 300.0,
        "descricao": "Intervalo de heartbeat (s) com o MongoDB",
    },
    "CLIPES_LEASE_S": {
        "grupo": "lives",
        "tipo": "float",
        "default": "90",
        "min": 30.0,
        "max": 600.0,
        "descricao": "Duracao do lease (s) de alvos e locks no MongoDB",
    },
    # --- render (12) ---
    "CLIPES_LAYOUT": {
        "grupo": "render",
        "escopo": "global",
        "tipo": "enum",
        "default": "split-starcuts",
        "opcoes": ["split-starcuts", "split-barra", "split-cheio", "split-fina", "full"],
        "descricao": "Modo de composicao vertical do clipe",
    },
    "CLIPES_SPLIT_BAR_H": {
        "grupo": "render",
        "tipo": "int",
        "default": "",
        "min": 0,
        "max": 600,
        "even": True,
        "descricao": "Altura (px, par) da barra no layout split",
    },
    "CLIPES_SPLIT_CAM_H": {
        "grupo": "render",
        "tipo": "int",
        "default": "",
        "min": 400,
        "max": 1020,
        "even": True,
        "descricao": "Altura (px, par) da facecam no layout split",
    },
    "CLIPES_FACECAM_TTL_H": {
        "grupo": "render",
        "tipo": "float",
        "default": "12",
        "min": 0.0,
        "max": 168.0,
        "descricao": "Validade (h) do cache de deteccao de facecam",
    },
    "CLIPES_BLUR_BG": {
        "grupo": "render",
        "tipo": "bool",
        "default": "1",
        "opcoes": ["1", "0"],
        "descricao": "Fundo desfocado no modo full (1=sim, 0=nao)",
    },
    "CLIPES_HOOK_TOP": {
        "grupo": "render",
        "tipo": "bool",
        "default": "1",
        "opcoes": ["1", "0"],
        "descricao": "Exibir gancho no topo do clipe (1=sim, 0=nao)",
    },
    "CLIPES_BAR": {
        "grupo": "render",
        "tipo": "bool",
        "default": "",
        "opcoes": ["1", "0"],
        "descricao": "Forcar barra de marca (vazio=automatico, 1=sim, 0=nao)",
    },
    "CLIPES_BRAND_NAME": {
        "grupo": "render",
        "tipo": "str",
        "default": "",
        "max_len": 40,
        "descricao": "Nome principal da marca na barra",
    },
    "CLIPES_BRAND_DISPLAY": {
        "grupo": "render",
        "tipo": "str",
        "default": "",
        "max_len": 40,
        "descricao": "Texto secundario/arroba na barra",
    },
    "CLIPES_BRAND_LOGO": {
        "grupo": "render",
        "tipo": "str",
        "default": "",
        "max_len": 120,
        "no_dotdot": True,
        "descricao": "Caminho do arquivo de logo da barra",
    },
    "CLIPES_BAR_STYLE": {
        "grupo": "render",
        "tipo": "enum",
        "default": "claro",
        "opcoes": ["claro", "escuro"],
        "descricao": "Tema visual da barra de marca",
    },
    "CLIPES_BAR_H": {
        "grupo": "render",
        "tipo": "int",
        "default": "420",
        "min": 300,
        "max": 600,
        "descricao": "Altura (px) da barra de marca",
    },
    "CLIPES_LAYOUT_FALLBACK": {
        "grupo": "render",
        "tipo": "enum",
        "default": "blur",
        "opcoes": ["blur", "recorte"],
        "escopo": "global",
        "descricao": "Fundo quando a facecam nao e detectada (blur ou recorte)",
    },
    # --- ia (11) ---
    "CLIPES_WHISPER_MODEL": {
        "grupo": "ia",
        "tipo": "enum",
        "default": "small",
        "opcoes": ["small", "large-v3-turbo"],
        "descricao": "Modelo Whisper para transcricao ASR",
    },
    "CLIPES_LLM_BACKEND": {
        "grupo": "ia",
        "tipo": "enum",
        "default": "auto",
        "opcoes": ["auto", "local"],
        "descricao": "Backend de LLM para roteiro/titulos (auto=Gemini+local, local=apenas local)",
    },
    "CLIPES_GEMINI_MODELS": {
        "grupo": "ia",
        "tipo": "str",
        "default": "gemini-2.5-flash,gemini-2.5-flash-lite",
        "max_len": 120,
        "descricao": "Lista CSV de modelos Gemini em ordem de prioridade",
    },
    "CLIPES_GEMINI_MIN_INTERVAL_S": {
        "grupo": "ia",
        "tipo": "float",
        "default": "4",
        "min": 0.0,
        "max": 60.0,
        "descricao": "Intervalo minimo (s) entre chamadas Gemini",
    },
    "CLIPES_CLOUD_TIMEOUT": {
        "grupo": "ia",
        "tipo": "float",
        "default": "20",
        "min": 5.0,
        "max": 120.0,
        "descricao": "Timeout (s) para chamadas Gemini na nuvem",
    },
    "CLIPES_LLM_TIMEOUT": {
        "grupo": "ia",
        "tipo": "float",
        "default": "45",
        "min": 10.0,
        "max": 300.0,
        "descricao": "Timeout (s) para chamadas ao llama-server local",
    },
    "CLIPES_PORTAO": {
        "grupo": "ia",
        "tipo": "enum",
        "default": "log",
        "opcoes": ["log", "on", "off"],
        "descricao": "Modo do portao de fala (log, on ou off)",
    },
    "CLIPES_MIN_WPS": {
        "grupo": "ia",
        "tipo": "float",
        "default": "0.4",
        "min": 0.0,
        "max": 5.0,
        "descricao": "Taxa minima de palavras por segundo no portao de fala",
    },
    "CLIPES_MIN_PALAVRAS": {
        "grupo": "ia",
        "tipo": "int",
        "default": "15",
        "min": 0,
        "max": 200,
        "descricao": "Minimo de palavras no trecho para passar no portao de fala",
    },
    "CLIPES_LEGENDA_IDIOMA": {
        "grupo": "ia",
        "tipo": "enum",
        "default": "pt",
        "opcoes": ["pt", "off"],
        "descricao": "Idioma alvo da traducao de legendas (pt ou off)",
    },
    "CLIPES_LEGENDA_ORIGINAL": {
        "grupo": "ia",
        "tipo": "bool",
        "default": "0",
        "opcoes": ["1", "0"],
        "descricao": "Exibir legenda original junto com a traduzida (1=sim, 0=nao)",
    },
    # --- publicacao (12) ---
    "CLIPES_MASTER_MODE": {
        "grupo": "publicacao",
        "tipo": "enum",
        "default": "livre",
        "opcoes": ["livre", "discord"],
        "descricao": "Modo de aprovacao antes da publicacao (livre ou discord)",
    },
    "CLIPES_DISCORD_MAX_MB": {
        "grupo": "publicacao",
        "tipo": "float",
        "default": "10",
        "min": 1.0,
        "max": 100.0,
        "descricao": "Tamanho maximo (MB) de video enviado como previa no Discord",
    },
    "CLIPES_REGISTRO_VERIFICAR": {
        "grupo": "publicacao",
        "tipo": "bool",
        "default": "1",
        "opcoes": ["1", "0"],
        "descricao": "Verificar ACK de anexo no registro do Discord (1=sim, 0=nao)",
    },
    "CLIPES_RETENCAO_H": {
        "grupo": "publicacao",
        "tipo": "float",
        "default": "",
        "min": 0.0,
        "max": 720.0,
        "descricao": "Horas para limpar arquivos locais apos envio ao Discord (vazio=manter)",
    },
    "CLIPES_YT_MAX_DIA": {
        "grupo": "publicacao",
        "tipo": "int",
        "default": "6",
        "min": 0,
        "max": 100,
        "descricao": "Limite diario de publicacoes no YouTube",
    },
    "CLIPES_TIKTOK_MAX_DIA": {
        "grupo": "publicacao",
        "tipo": "int",
        "default": "5",
        "min": 0,
        "max": 100,
        "descricao": "Limite diario de publicacoes no TikTok",
    },
    "CLIPES_YT_PRIVACY": {
        "grupo": "publicacao",
        "tipo": "enum",
        "default": "private",
        "opcoes": ["private", "unlisted", "public"],
        "descricao": "Visibilidade padrao no YouTube",
    },
    "CLIPES_TIKTOK_PRIVACY": {
        "grupo": "publicacao",
        "tipo": "enum",
        "default": "SELF_ONLY",
        "opcoes": ["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE"],
        "descricao": "Visibilidade padrao no TikTok",
    },
    "CLIPES_PUB_LEASE_S": {
        "grupo": "publicacao",
        "tipo": "float",
        "default": "1800",
        "min": 60.0,
        "max": 7200.0,
        "descricao": "Lease (s) de pedido de publicacao no MongoDB",
    },
    "CLIPES_PUB_MAX_POR_CICLO": {
        "grupo": "publicacao",
        "tipo": "int",
        "default": "3",
        "min": 1,
        "max": 20,
        "descricao": "Maximo de publicacoes por ciclo de run_pending",
    },
    "CLIPES_AGENDA_HORARIOS": {
        "grupo": "publicacao",
        "tipo": "schedule",
        "default": "11:30,19:00",
        "descricao": "Horarios agendados HH:MM separados por virgula",
    },
    "CLIPES_TZ": {
        "grupo": "publicacao",
        "tipo": "tz",
        "default": "America/Sao_Paulo",
        "descricao": "Fuso horario IANA para agendamento e cotas",
    },
    # --- temperatura (3) ---
    "CLIPES_VIGIA": {
        "grupo": "temperatura",
        "tipo": "bool",
        "default": "1",
        "opcoes": ["1", "0"],
        "descricao": "Vigia de temperatura: pausa o processamento quando o PC esquenta (1=sim, 0=nao)",
    },
    "CLIPES_TEMP_MAX": {
        "grupo": "temperatura",
        "tipo": "int",
        "default": "85",
        "min": 60,
        "max": 100,
        "descricao": "Temperatura (C) em que o vigia pausa o processamento",
    },
    "CLIPES_TEMP_MIN": {
        "grupo": "temperatura",
        "tipo": "int",
        "default": "75",
        "min": 40,
        "max": 95,
        "descricao": "Temperatura (C) em que o vigia retoma o processamento (menor que CLIPES_TEMP_MAX)",
    },
    "CLIPES_VISIBILIDADE": {
        "grupo": "publicacao",
        "tipo": "enum",
        "default": "padrao",
        "opcoes": ["padrao", "private", "public"],
        "escopo": "global",
        "descricao": "Visibilidade da publicacao (padrao = a de cada plataforma)",
    },
    # --- antiflop (7) ---
    "CLIPES_GATE": {
        "grupo": "antiflop",
        "tipo": "enum",
        "default": "bloquear",
        "opcoes": ["bloquear", "avisar", "off"],
        "escopo": "global",
        "descricao": "Gate de qualidade antes de publicar (bloquear, avisar ou off)",
    },
    "CLIPES_HOOK_MAX_ATRASO": {
        "grupo": "antiflop",
        "tipo": "float",
        "default": "1.0",
        "min": 0.3,
        "max": 3.0,
        "escopo": "global",
        "descricao": "Atraso maximo (s) do gancho no inicio do clipe",
    },
    "CLIPES_DUR_MIN": {
        "grupo": "antiflop",
        "tipo": "int",
        "default": "15",
        "min": 8,
        "max": 60,
        "escopo": "global",
        "descricao": "Duracao minima (s) do clipe",
    },
    "CLIPES_DUR_MAX": {
        "grupo": "antiflop",
        "tipo": "int",
        "default": "45",
        "min": 15,
        "max": 120,
        "escopo": "global",
        "descricao": "Duracao maxima (s) do clipe",
    },
    "CLIPES_CARTAO_FINAL": {
        "grupo": "antiflop",
        "tipo": "bool",
        "default": "0",
        "opcoes": ["1", "0"],
        "escopo": "global",
        "descricao": "Cartao final no clipe (1=sim, 0=nao)",
    },
    "CLIPES_HASHTAGS": {
        "grupo": "antiflop",
        "tipo": "int",
        "default": "3",
        "min": 1,
        "max": 5,
        "escopo": "global",
        "descricao": "Quantidade de hashtags por publicacao",
    },
    "CLIPES_DEDUP_PHASH": {
        "grupo": "antiflop",
        "tipo": "int",
        "default": "6",
        "min": 0,
        "max": 32,
        "escopo": "global",
        "descricao": "Distancia pHash maxima para considerar clipe duplicado (0 desliga)",
    },
}


def _is_forbidden_key(key: str) -> bool:
    upper = key.upper()
    if key in FORBIDDEN_ENV_KEYS or upper in FORBIDDEN_ENV_KEYS:
        return True
    if any(sub in upper for sub in _FORBIDDEN_SUBSTRINGS):
        return True
    return key not in CONFIG_SCHEMA


def _validate_single(key: str, raw_val: Any) -> tuple[str | None, str | None]:
    if _is_forbidden_key(key):
        return None, f"variavel nao permitida: {key}"
    if raw_val is None:
        return None, None
    meta = CONFIG_SCHEMA[key]
    tipo = meta["tipo"]

    if isinstance(raw_val, bool):
        if tipo != "bool":
            return None, f"valor invalido para {key}: tipo booleano nao aceito"
        val_str = "1" if raw_val else "0"
    elif isinstance(raw_val, (dict, list)):
        return None, f"valor invalido para {key}: tipo composto nao aceito"
    else:
        val_str = str(raw_val).strip()

    if val_str == "":
        return None, None

    if tipo == "int":
        if not _INT_STR_RE.fullmatch(val_str):
            return None, f"valor invalido para {key}: esperado inteiro"
        val_i = int(val_str)
        if "min" in meta and val_i < meta["min"]:
            return None, f"valor invalido para {key}: minimo {meta['min']}"
        if "max" in meta and val_i > meta["max"]:
            return None, f"valor invalido para {key}: maximo {meta['max']}"
        if meta.get("even") and val_i % 2 != 0:
            return None, f"valor invalido para {key}: deve ser par"
        return str(val_i), None

    if tipo == "float":
        try:
            val_f = float(val_str)
        except ValueError:
            return None, f"valor invalido para {key}: esperado numero"
        if not math.isfinite(val_f):
            return None, f"valor invalido para {key}: numero nao finito"
        if "min" in meta and val_f < meta["min"]:
            return None, f"valor invalido para {key}: minimo {meta['min']}"
        if "max" in meta and val_f > meta["max"]:
            return None, f"valor invalido para {key}: maximo {meta['max']}"
        return val_str, None

    if tipo == "bool":
        low = val_str.lower()
        if low in _TRUE:
            return "1", None
        if low in _FALSE:
            return "0", None
        return None, f"valor invalido para {key}: esperado 1 ou 0"

    if tipo == "enum":
        if val_str not in meta["opcoes"]:
            return None, f"valor invalido para {key}: opcoes {', '.join(meta['opcoes'])}"
        return val_str, None

    if tipo == "slots":
        seen: set[str] = set()
        for parte in val_str.split(","):
            trecho = parte.strip()
            if not trecho or "=" not in trecho:
                return None, f"valor invalido para {key}: formato plataforma=N"
            plat_raw, num_raw = trecho.split("=", 1)
            plat = plat_raw.strip().lower()
            num_str = num_raw.strip()
            if plat not in _SLOTS_DEFAULT or not _SLOTS_INT_RE.fullmatch(num_str) or plat in seen:
                return None, f"valor invalido para {key}: trecho {trecho!r}"
            seen.add(plat)
        return val_str, None

    if tipo == "schedule":
        for parte in val_str.split(","):
            hora = parte.strip()
            if not _SCHEDULE_RE.fullmatch(hora):
                return None, f"valor invalido para {key}: horario {hora!r} (use HH:MM)"
        return val_str, None

    if tipo == "tz":
        try:
            ZoneInfo(val_str)
        except (ZoneInfoNotFoundError, ValueError):
            return None, f"valor invalido para {key}: fuso horario desconhecido"
        return val_str, None

    if tipo == "str":
        if "\n" in val_str or "\r" in val_str or "\x00" in val_str:
            return None, f"valor invalido para {key}: quebras de linha nao permitidas"
        max_len = int(meta.get("max_len", 120))
        if len(val_str) > max_len:
            return None, f"valor invalido para {key}: maximo {max_len} caracteres"
        if meta.get("no_dotdot") and ".." in val_str:
            return None, f"valor invalido para {key}: '..' nao permitido"
        return val_str, None

    return None, f"valor invalido para {key}"


def validate_config_payload(raw_cfg: Mapping[str, Any]) -> tuple[dict[str, str], str | None]:
    """Valida um dicionario de configuracoes contra `CONFIG_SCHEMA`."""
    if not isinstance(raw_cfg, Mapping):
        return {}, "config deve ser um objeto"
    clean: dict[str, str] = {}
    for raw_key, raw_val in raw_cfg.items():
        key = str(raw_key).strip()
        norm, err = _validate_single(key, raw_val)
        if err is not None:
            return {}, err
        if norm is not None:
            clean[key] = norm
    temp_min = int(clean.get("CLIPES_TEMP_MIN", CONFIG_SCHEMA["CLIPES_TEMP_MIN"]["default"]))
    temp_max = int(clean.get("CLIPES_TEMP_MAX", CONFIG_SCHEMA["CLIPES_TEMP_MAX"]["default"]))
    if temp_min >= temp_max:
        return {}, "CLIPES_TEMP_MIN deve ser menor que CLIPES_TEMP_MAX"
    lo = int(clean.get("CLIPES_DUR_MIN") or CONFIG_SCHEMA["CLIPES_DUR_MIN"]["default"])
    hi = int(clean.get("CLIPES_DUR_MAX") or CONFIG_SCHEMA["CLIPES_DUR_MAX"]["default"])
    if lo >= hi:
        return {}, f"CLIPES_DUR_MIN ({lo}) deve ser menor que CLIPES_DUR_MAX ({hi})"
    return clean, None


def salvar_config(db: Any, corpo: Mapping[str, Any], now: datetime) -> tuple[int, dict[str, Any]]:
    """Persiste overrides nao vazios em `db.workers.config` para um `pc_alias`."""
    raw_alias = corpo.get("pc_alias") if corpo.get("pc_alias") is not None else corpo.get("alias")
    pc_alias = raw_alias.strip() if isinstance(raw_alias, str) else ""
    if not _PC_ALIAS_RE.fullmatch(pc_alias):
        return 400, {"erro": "pc_alias invalido"}

    raw_cfg = corpo.get("config")
    if not isinstance(raw_cfg, Mapping):
        return 400, {"erro": "config deve ser um objeto"}
    clean_cfg, err = validate_config_payload(raw_cfg)
    if err is not None:
        return 400, {"erro": err}

    existente = db.workers.find_one({"$or": [{"alias": pc_alias}, {"_id": pc_alias}]})
    if existente is not None:
        db.workers.update_one(
            {"_id": existente["_id"]},
            {"$set": {"alias": pc_alias, "config": clean_cfg, "config_updated_at": now}},
        )
    else:
        db.workers.update_one(
            {"_id": pc_alias},
            {
                "$set": {"alias": pc_alias, "config": clean_cfg, "config_updated_at": now},
                "$setOnInsert": {"heartbeat_at": None},
            },
            upsert=True,
        )
    return 200, {"ok": True, "pc_alias": pc_alias, "config": clean_cfg}


def salvar_config_global(db: Any, corpo: Mapping[str, Any], now: datetime) -> tuple[int, dict[str, Any]]:
    """Persiste a config global em `db.config_global` (_id=global); so chaves de `chaves_globais()`."""
    raw_cfg = corpo.get("config")
    if not isinstance(raw_cfg, Mapping):
        return 400, {"erro": "config deve ser um objeto"}
    globais = {k for k, m in CONFIG_SCHEMA.items() if m.get("escopo") == "global"}
    for raw_key in raw_cfg:
        if str(raw_key).strip() not in globais:
            return 400, {"erro": f"chave nao global: {raw_key}"}
    clean_cfg, err = validate_config_payload(raw_cfg)
    if err is not None:
        return 400, {"erro": err}
    db.config_global.update_one(
        {"_id": "global"}, {"$set": {"config": clean_cfg, "updated_at": now}}, upsert=True
    )
    return 200, {"ok": True, "config": clean_cfg}


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
    if metodo in ("comando", "alvos", "config", "config_global"):
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
        if metodo == "config":
            return _enviar(h, *salvar_config(db, corpo, now))
        if metodo == "config_global":
            return _enviar(h, *salvar_config_global(db, corpo, now))
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
