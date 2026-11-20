"""Auditoria (onda 4-B: painel): M-17, M-18, M-22 e B-15."""
from __future__ import annotations

import base64
import importlib.util
import inspect
import json
import re
import threading
import typing
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path

import mongomock
import pymongo
import pytest

from clipes.coord import Coord
from painel.api import _nucleo, comando, status as status_fn

NOW = datetime(2026, 1, 1, 12, 0, 0)
PAINEL_DIR = Path(__file__).resolve().parents[1] / "painel"


def basic(senha: str, user: str = "painel") -> str:
    return "Basic " + base64.b64encode(f"{user}:{senha}".encode()).decode()


def _chamar_client(mod, client, metodo: str, auth: str | None = None, corpo=None, headers=None):
    h = type("H", (mod.handler,), {"client": client})
    srv = HTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    data = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/", method=metodo, data=data)
    if auth:
        req.add_header("Authorization", auth)
    if corpo is not None and (not headers or "Content-Type" not in headers):
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        if v is not None:
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    finally:
        srv.server_close()


# ---- M-17: banco da URI tem prioridade; fallback PAINEL_MONGO_DB ou clipes_lives


def test_m17_banco_da_uri_tem_prioridade_sobre_env(monkeypatch):
    monkeypatch.delenv("PAINEL_SENHA", raising=False)
    monkeypatch.setenv("PAINEL_MONGO_URI", "mongodb://exemplo.invalid/banco_da_uri")
    monkeypatch.setenv("PAINEL_MONGO_DB", "banco_do_env")

    client = mongomock.MongoClient("mongodb://exemplo.invalid/banco_da_uri")
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    client["banco_da_uri"].workers.insert_one({"_id": "w-uri", "alias": "PC-URI", "heartbeat_at": agora})
    client["banco_do_env"].workers.insert_one({"_id": "w-env", "alias": "PC-ENV", "heartbeat_at": agora})

    code, _, corpo = _chamar_client(status_fn, client, "GET")
    assert code == 200
    ids = [w["id"] for w in json.loads(corpo)["workers"]]
    assert ids == ["w-uri"]

    code_c, _, _ = _chamar_client(comando, client, "POST", None, {"worker_id": "w-uri", "confirmar": "LIBERAR"})
    assert code_c == 202
    assert client["banco_da_uri"].commands.count_documents({"worker_id": "w-uri"}) == 1
    assert client["banco_do_env"].commands.count_documents({}) == 0


def test_m17_sem_banco_na_uri_usa_env_ou_clipes_lives(monkeypatch):
    monkeypatch.delenv("PAINEL_SENHA", raising=False)
    monkeypatch.setenv("PAINEL_MONGO_URI", "mongodb://exemplo.invalid")
    agora = datetime.now(timezone.utc).replace(tzinfo=None)

    client = mongomock.MongoClient("mongodb://exemplo.invalid")
    client["meu_db"].workers.insert_one({"_id": "w-env", "heartbeat_at": agora})
    client["clipes_lives"].workers.insert_one({"_id": "w-def", "heartbeat_at": agora})

    monkeypatch.setenv("PAINEL_MONGO_DB", "meu_db")
    code, _, corpo = _chamar_client(status_fn, client, "GET")
    assert code == 200
    assert [w["id"] for w in json.loads(corpo)["workers"]] == ["w-env"]

    monkeypatch.setenv("PAINEL_MONGO_DB", "")
    code2, _, corpo2 = _chamar_client(status_fn, client, "GET")
    assert code2 == 200
    assert [w["id"] for w in json.loads(corpo2)["workers"]] == ["w-def"]

    monkeypatch.delenv("PAINEL_MONGO_DB", raising=False)
    code3, _, corpo3 = _chamar_client(status_fn, client, "GET")
    assert code3 == 200
    assert [w["id"] for w in json.loads(corpo3)["workers"]] == ["w-def"]


def test_m17_chama_get_default_database_com_pymongo_real(monkeypatch):
    chamadas: list[str] = []
    db_mock = mongomock.MongoClient()["qualquer"]

    class SomenteGetDefault:
        def get_default_database(self, default: str | None = None):
            chamadas.append(str(default))
            return db_mock

    monkeypatch.setenv("PAINEL_MONGO_DB", "db_custom")
    assert _nucleo._obter_db(SomenteGetDefault()) is db_mock
    assert chamadas == ["db_custom"]

    # Mesmo contrato com pymongo.MongoClient real (sem abrir conexao)
    c_com_uri = pymongo.MongoClient("mongodb://exemplo.invalid/db_uri", connect=False)
    c_sem_uri = pymongo.MongoClient("mongodb://exemplo.invalid", connect=False)
    assert _nucleo._obter_db(c_com_uri).name == "db_uri"
    assert _nucleo._obter_db(c_sem_uri).name == "db_custom"
    monkeypatch.delenv("PAINEL_MONGO_DB", raising=False)
    assert _nucleo._obter_db(c_sem_uri).name == "clipes_lives"
    c_com_uri.close()
    c_sem_uri.close()


# ---- Painel livre: /api/status e /api/comando funcionam sem Basic Auth nem PAINEL_SENHA


def test_m18_status_e_comando_livres_sem_auth(monkeypatch):
    monkeypatch.delenv("PAINEL_SENHA", raising=False)
    monkeypatch.setenv("PAINEL_MONGO_URI", "mongodb://exemplo.invalid")
    client = mongomock.MongoClient("mongodb://exemplo.invalid")
    client["clipes_lives"].workers.insert_one(
        {"_id": "w-1", "alias": "PC-1", "heartbeat_at": datetime.now(timezone.utc).replace(tzinfo=None)}
    )

    code_ok, _, corpo_ok = _chamar_client(status_fn, client, "GET")
    assert code_ok == 200
    assert json.loads(corpo_ok)["workers"][0]["id"] == "w-1"

    code_c, _, _ = _chamar_client(comando, client, "POST", None, {"worker_id": "w-1", "confirmar": "LIBERAR"})
    assert code_c == 202


def test_m18_html_status_acesso_livre_e_polling():
    txt = (PAINEL_DIR / "index.html").read_text(encoding="utf-8")
    m = re.search(r"<script>(.*?)</script>", txt, re.S)
    assert m, "bloco <script> nao encontrado"
    js = m.group(1)

    atualizar_js = js.split("function atualizar()", 1)[1]
    assert 'fetch("/api/status"' in atualizar_js
    assert "Authorization" not in js
    assert "pedirSenha" not in js
    assert "basicHeader" not in js
    assert re.search(r"setInterval\s*\(\s*atualizar\s*,\s*10000\s*\)", atualizar_js)


# ---- M-22: parse tolerante de CLIPES_BEAT_S e online/comando via beat_s do worker


def _importar_nucleo_isolado():
    spec = importlib.util.spec_from_file_location("_nucleo_isolado_m22", _nucleo.__file__)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("valor", ["20s", "", "abc", "0", "-5", "nan", "inf"])
def test_m22_importacao_tolerante_com_beat_s_invalido(monkeypatch, valor):
    monkeypatch.setenv("CLIPES_BEAT_S", valor)
    mod = _importar_nucleo_isolado()
    assert mod.BEAT_S == 20.0


def test_m22_importacao_aceita_beat_s_valido(monkeypatch):
    monkeypatch.setenv("CLIPES_BEAT_S", "12.5")
    mod = _importar_nucleo_isolado()
    assert mod.BEAT_S == 12.5


def test_m22_online_e_comando_respeitam_beat_s_do_worker():
    db = mongomock.MongoClient()["clipes_lives"]
    # Worker lento gravado via Coord.beat (beat_s=60 -> limite 180s), ultimo beat ha 120s
    t_base = NOW.replace(tzinfo=timezone.utc).timestamp()
    Coord(db, "w-lento", now=lambda: t_base - 120).beat("PC-Lento", {}, None, 1, 0.5, beat_s=60)
    # Worker rapido gravado via Coord.beat (beat_s=5 -> limite 15s), ultimo beat ha 20s
    Coord(db, "w-rapido", now=lambda: t_base - 20).beat("PC-Rapido", {}, None, 1, 0.5, beat_s=5)
    # Worker legado (sem beat_s) e worker com beat_s invalido caem no beat_s do painel (20 -> limite 60s)
    db.workers.insert_many([
        {"_id": "w-legado-on", "heartbeat_at": NOW - timedelta(seconds=50)},
        {"_id": "w-legado-off", "heartbeat_at": NOW - timedelta(seconds=61)},
        {"_id": "w-invalido-on", "heartbeat_at": NOW - timedelta(seconds=50), "beat_s": "ruim"},
    ])

    st = {w["id"]: w["online"] for w in _nucleo.status(db, NOW, beat_s=20)["workers"]}
    assert st == {
        "w-lento": True,
        "w-rapido": False,
        "w-legado-on": True,
        "w-legado-off": False,
        "w-invalido-on": True,
    }

    # Mesmo criterio no check de worker online de criar_comando (M-03)
    assert _nucleo.criar_comando(db, "w-lento", "LIBERAR", NOW, beat_s=20)[0] == 202
    assert _nucleo.criar_comando(db, "w-rapido", "LIBERAR", NOW, beat_s=20) == (409, {"erro": "worker offline"})
    assert _nucleo.criar_comando(db, "w-legado-on", "LIBERAR", NOW, beat_s=20)[0] == 202
    assert _nucleo.criar_comando(db, "w-legado-off", "LIBERAR", NOW, beat_s=20) == (409, {"erro": "worker offline"})
    assert _nucleo.criar_comando(db, "w-invalido-on", "LIBERAR", NOW, beat_s=20)[0] == 202


# ---- B-15 e isolamento da Vercel


def test_b15_funcoes_publicas_anotadas():
    for fn in (
        _nucleo.status,
        _nucleo.criar_comando,
        _nucleo.gerir_alvo,
        _nucleo.validate_config_payload,
        _nucleo.salvar_config,
        _nucleo.tratar,
    ):
        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn)
        assert "return" in hints, f"{fn.__name__} sem anotacao de retorno"
        for nome in sig.parameters:
            assert nome in hints, f"{fn.__name__}({nome}) sem anotacao de tipo"


def test_painel_nao_importa_clipes():
    for arq in (PAINEL_DIR / "api").glob("*.py"):
        txt = arq.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(?:from|import)\s+clipes\b", txt, re.M), f"{arq.name} importa clipes"


# ---- T17: /api/alvos aceita POST direto sem Basic Auth, mantendo validacao de origem e metodo


def test_t17_api_alvos_acesso_livre_sem_auth(monkeypatch):
    from painel.api import alvos as alvos_api

    monkeypatch.delenv("PAINEL_SENHA", raising=False)
    monkeypatch.setenv("PAINEL_MONGO_URI", "mongodb://exemplo.invalid")
    client = mongomock.MongoClient("mongodb://exemplo.invalid")
    corpo = {"acao": "adicionar", "alvo": "twitch.tv/fulano", "pc": ""}

    code, _, body = _chamar_client(alvos_api, client, "POST", None, corpo)
    assert code == 201 and json.loads(body) == {"ok": True, "_id": "fulano", "tipo": "live"}
    assert client["clipes_lives"].alvos.count_documents({}) == 1


def test_t17_api_alvos_so_aceita_post(monkeypatch):
    from painel.api import alvos as alvos_api

    monkeypatch.delenv("PAINEL_SENHA", raising=False)
    monkeypatch.setenv("PAINEL_MONGO_URI", "mongodb://exemplo.invalid")
    client = mongomock.MongoClient("mongodb://exemplo.invalid")
    code, hdr, _ = _chamar_client(alvos_api, client, "GET")
    assert code == 405 and hdr.get("Allow") == "POST"
