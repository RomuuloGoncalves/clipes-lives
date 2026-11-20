"""Testes do servidor local de desenvolvimento do painel (`painel/dev_server.py`)."""
from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mongomock
import pytest

from painel import dev_server


def _basic(senha: str, user: str = "x") -> str:
    return "Basic " + base64.b64encode(f"{user}:{senha}".encode()).decode()


def test_carregar_env_sem_sobrescrever_e_fallback_clipes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comentario\n"
        "CLIPES_MONGO_URI=mongodb://user:pass@localhost:27017/clipes  # inline comment\n"
        "CLIPES_MONGO_DB='meu_db'\n"
        "PAINEL_SENHA=\"segredo123\"\n"
        "export PAINEL_PORT=9090\n",
        encoding="utf-8",
    )

    environ = {"PAINEL_SENHA": "ja-definida"}
    dev_server.configurar_ambiente(repo_root=tmp_path, environ=environ)

    assert environ["PAINEL_SENHA"] == "ja-definida"
    assert environ["CLIPES_MONGO_URI"] == "mongodb://user:pass@localhost:27017/clipes"
    assert environ["PAINEL_MONGO_URI"] == "mongodb://user:pass@localhost:27017/clipes"
    assert environ["PAINEL_MONGO_DB"] == "meu_db"
    assert environ["PAINEL_PORT"] == "9090"


def test_configurar_ambiente_nao_le_dotenv_se_painel_mongo_uri_ja_definida(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PAINEL_MONGO_URI=mongodb://do-arquivo\n"
        "PAINEL_SENHA=do-arquivo\n",
        encoding="utf-8",
    )

    environ = {"PAINEL_MONGO_URI": "mongodb://do-ambiente"}
    dev_server.configurar_ambiente(repo_root=tmp_path, environ=environ)

    assert environ["PAINEL_MONGO_URI"] == "mongodb://do-ambiente"
    assert "PAINEL_SENHA" not in environ


def test_resolver_porta_prioridade() -> None:
    assert dev_server.resolver_porta(None, {}) == 8787
    assert dev_server.resolver_porta(None, {"PAINEL_PORT": "9123"}) == 9123
    assert dev_server.resolver_porta(7777, {"PAINEL_PORT": "9123"}) == 7777


@pytest.fixture
def servidor_local(monkeypatch: pytest.MonkeyPatch):
    client = mongomock.MongoClient()
    db = client["clipes_lives"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.workers.insert_one({
        "_id": "w-1",
        "alias": "PC-1",
        "heartbeat_at": now - timedelta(seconds=5),
        "capacity": {"cpus": 4},
        "max_lives": 1,
    })

    monkeypatch.setenv("PAINEL_MONGO_URI", "mongodb://mock-local/clipes_lives")
    monkeypatch.setenv("PAINEL_SENHA", "segredo")
    monkeypatch.delenv("PAINEL_MONGO_DB", raising=False)

    class HandlerTeste(dev_server.PainelDevHandler):
        pass

    HandlerTeste.client = client
    srv = dev_server.criar_servidor(port=0, handler_cls=HandlerTeste)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", db
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def test_rotas_index_status_alvos_e_comando(servidor_local) -> None:
    base_url, db = servidor_local

    # GET / e GET /index.html servem o HTML do painel
    for rota in ("/", "/index.html"):
        with urllib.request.urlopen(f"{base_url}{rota}") as resp:
            assert resp.status == 200
            assert "text/html" in resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
            assert "<html" in body.lower()

    # GET /api/status sem auth -> 200 (painel aberto)
    with urllib.request.urlopen(f"{base_url}/api/status") as resp:
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
        assert [w["id"] for w in payload["workers"]] == ["w-1"]

    # POST /api/alvos autenticado -> 201 e persiste no mongomock
    req_alvo = urllib.request.Request(
        f"{base_url}/api/alvos",
        data=json.dumps({"acao": "adicionar", "alvo": "canal_teste"}).encode("utf-8"),
        headers={
            "Authorization": _basic("segredo"),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req_alvo) as resp:
        assert resp.status == 201
        res_alvo = json.loads(resp.read().decode("utf-8"))
        assert res_alvo == {"ok": True, "_id": "canal_teste", "tipo": "live"}
    assert db.alvos.find_one({"_id": "canal_teste"}) is not None

    # POST /api/comando autenticado -> 202
    req_cmd = urllib.request.Request(
        f"{base_url}/api/comando",
        data=json.dumps({"worker_id": "w-1", "confirmar": "LIBERAR"}).encode("utf-8"),
        headers={
            "Authorization": _basic("segredo"),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req_cmd) as resp:
        assert resp.status == 202
        assert json.loads(resp.read().decode("utf-8")) == {"ok": True}
