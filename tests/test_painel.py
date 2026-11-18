"""Painel (PAINEL-02, CMD-01): nucleo + handlers com mongomock e servidor local em porta efemera."""
import base64
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import HTTPServer

import mongomock
import pytest

from painel.api import _nucleo, comando, status as status_fn

NOW = datetime(2026, 1, 1, 12, 0, 0)


def basic(senha, user="x"):
    return "Basic " + base64.b64encode(f"{user}:{senha}".encode()).decode()


@pytest.fixture
def db():
    d = mongomock.MongoClient()["clipes"]
    d.workers.insert_one({"_id": "w-1", "alias": "PC-1", "heartbeat_at": NOW - timedelta(seconds=61),
                          "capacity": {"cpus": 3}, "max_lives": 1})
    d.workers.insert_one({"_id": "w-2", "alias": "PC-2", "heartbeat_at": NOW - timedelta(seconds=10)})
    return d


# ---- T17: nucleo
def test_online_pela_batida(db):
    ws = {w["id"]: w for w in _nucleo.status(db, NOW, beat_s=20)["workers"]}
    assert ws["w-1"]["online"] is False and ws["w-2"]["online"] is True


def test_status_lives_e_jobs_24h(db):
    db.lives.insert_many([{"_id": "a", "status": "live", "owner": "w-1", "channel": "c"},
                          {"_id": "b", "status": "live", "owner": None},
                          {"_id": "c", "status": "ended", "owner": None}])
    db.jobs.insert_many([{"_id": "1", "status": "done", "updated_at": NOW - timedelta(hours=1)},
                         {"_id": "2", "status": "done", "updated_at": NOW - timedelta(hours=25)},
                         {"_id": "3", "status": "lost", "updated_at": NOW}])
    r = _nucleo.status(db, NOW)
    assert [l["id"] for l in r["lives"]] == ["a"]
    assert r["jobs_24h"] == {"done": 1, "lost": 1}


def test_comando_codigos(db):
    assert _nucleo.criar_comando(db, "w-1", "", NOW)[0] == 400
    assert _nucleo.criar_comando(db, "w-9", "LIBERAR", NOW)[0] == 404
    assert _nucleo.criar_comando(db, "w-1", "LIBERAR", NOW)[0] == 202
    assert _nucleo.criar_comando(db, "w-1", "LIBERAR", NOW)[0] == 409
    docs = list(db.commands.find())
    assert len(docs) == 1 and docs[0]["acao"] == "liberar" and docs[0]["status"] == "pending"
    # Após concluir o comando pendente, outro pode ser criado.
    db.commands.update_one({"_id": docs[0]["_id"]}, {"$set": {"status": "feito"}})
    assert _nucleo.criar_comando(db, "w-1", "LIBERAR", NOW)[0] == 202


def test_comando_409_atomico_em_corrida_e_duplicate_key(db, monkeypatch):
    from pymongo.errors import DuplicateKeyError

    db.commands.create_index([("worker_id", 1)], unique=True, partialFilterExpression={"status": "pending"})
    barrier = threading.Barrier(4)
    codes: list[int] = []
    lock = threading.Lock()

    def worker():
        barrier.wait(timeout=5)
        code, _ = _nucleo.criar_comando(db, "w-1", "LIBERAR", NOW)
        with lock:
            codes.append(code)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(codes) == [202, 409, 409, 409]
    assert db.commands.count_documents({"worker_id": "w-1", "status": "pending"}) == 1

    # DuplicateKeyError em corrida real no Mongo devolve 409 sem erro 500.
    def boom(*args, **kwargs):
        raise DuplicateKeyError("dup pending")

    monkeypatch.setattr(db.commands, "update_one", boom)
    assert _nucleo.criar_comando(db, "w-2", "LIBERAR", NOW) == (409, {"erro": "comando ja pendente"})


def test_autorizado():
    assert _nucleo.autorizado(basic("s3"), "s3")
    assert not _nucleo.autorizado(basic("errada"), "s3")
    assert not _nucleo.autorizado(None, "s3")
    assert not _nucleo.autorizado("Basic !!!", "s3")
    assert not _nucleo.autorizado(basic("s3"), "")
    assert _nucleo.autorizado(basic("sénha"), "sénha")


def test_status_sem_uri_nem_host(db):
    txt = json.dumps(_nucleo.status(db, NOW))
    assert "mongodb" not in txt and "://" not in txt


@pytest.fixture(autouse=True)
def _limpar_falhas_auth():
    _nucleo._FALHAS_AUTH.clear()
    yield
    _nucleo._FALHAS_AUTH.clear()


# ---- T18/T19: handlers
def chamar(mod, db, metodo, path_auth=None, corpo=None, headers=None, raw_body=None):
    h = type("H", (mod.handler,), {"client": {"clipes": db}})
    srv = HTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    data = raw_body if raw_body is not None else (json.dumps(corpo).encode() if corpo is not None else None)
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/", method=metodo, data=data)
    if path_auth:
        req.add_header("Authorization", path_auth)
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


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PAINEL_SENHA", "s3")
    monkeypatch.setenv("PAINEL_MONGO_URI", "mongodb://exemplo.invalid")


def test_status_401_sem_auth(db, env):
    code, hdr, corpo = chamar(status_fn, db, "GET")
    assert code == 401 and "Basic" in hdr["WWW-Authenticate"] and b"workers" not in corpo


def test_status_500_sem_env(db, monkeypatch):
    monkeypatch.delenv("PAINEL_SENHA", raising=False)
    monkeypatch.delenv("PAINEL_MONGO_URI", raising=False)
    code, _, corpo = chamar(status_fn, db, "GET", basic("s3"))
    assert code == 500 and json.loads(corpo) == {"erro": "painel nao configurado"}


def test_status_200(db, env):
    code, _, corpo = chamar(status_fn, db, "GET", basic("s3"))
    assert code == 200 and len(json.loads(corpo)["workers"]) == 2
    assert b"exemplo.invalid" not in corpo


def test_comando_405_401_202_409(db, env):
    assert chamar(comando, db, "GET", basic("s3"))[0] == 405
    assert chamar(comando, db, "POST", None, {"worker_id": "w-1", "confirmar": "LIBERAR"})[0] == 401
    assert chamar(comando, db, "POST", basic("s3"), {"worker_id": "w-1"})[0] == 400
    assert chamar(comando, db, "POST", basic("s3"), {"worker_id": "w-1", "confirmar": "LIBERAR"})[0] == 202
    assert chamar(comando, db, "POST", basic("s3"), {"worker_id": "w-1", "confirmar": "LIBERAR"})[0] == 409
    assert db.commands.count_documents({}) == 1


def test_comando_exige_content_type_json_e_confere_origin(db, env):
    # Content-Type ausente ou form -> 415
    assert chamar(comando, db, "POST", basic("s3"), raw_body=b"worker_id=w-1")[0] == 415
    assert chamar(
        comando, db, "POST", basic("s3"), {"worker_id": "w-1", "confirmar": "LIBERAR"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )[0] == 415
    # charset junto de application/json é aceito
    assert chamar(
        comando, db, "POST", basic("s3"), {"worker_id": "w-2", "confirmar": "LIBERAR"},
        headers={"Content-Type": "application/json; charset=utf-8"},
    )[0] == 202
    # Origin cruzado ou null -> 403
    assert chamar(
        comando, db, "POST", basic("s3"), {"worker_id": "w-1", "confirmar": "LIBERAR"},
        headers={"Origin": "https://atacante.invalid"},
    )[0] == 403
    assert chamar(
        comando, db, "POST", basic("s3"), {"worker_id": "w-1", "confirmar": "LIBERAR"},
        headers={"Origin": "null"},
    )[0] == 403
    # Origin compatível com Host -> 202
    assert chamar(
        comando, db, "POST", basic("s3"), {"worker_id": "w-1", "confirmar": "LIBERAR"},
        headers={"Host": "painel.local", "Origin": "https://painel.local"},
    )[0] == 202


def test_limite_tentativas_senha_429(db, env, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(_nucleo.time, "monotonic", lambda: t[0])

    for _ in range(_nucleo.MAX_TENTATIVAS_SENHA):
        code, _, _ = chamar(status_fn, db, "GET", basic("errada"), headers={"X-Real-IP": "198.51.100.10"})
        assert code == 401

    # Mesmo com a senha certa, o cliente bloqueado recebe 429 até expirar a janela.
    code, hdr, _ = chamar(status_fn, db, "GET", basic("s3"), headers={"X-Real-IP": "198.51.100.10"})
    assert code == 429 and int(hdr["Retry-After"]) > 0

    # Outro IP continua normal.
    assert chamar(status_fn, db, "GET", basic("s3"), headers={"X-Real-IP": "198.51.100.11"})[0] == 200

    # Passada a janela de bloqueio, volta a aceitar.
    t[0] += _nucleo.JANELA_BLOQUEIO_S + 1
    assert chamar(status_fn, db, "GET", basic("s3"), headers={"X-Real-IP": "198.51.100.10"})[0] == 200


# ---- T20: pagina do painel
def test_index_html_sem_innerhtml_e_sem_segredo():
    from pathlib import Path
    import re

    html = Path(__file__).resolve().parents[1] / "painel" / "index.html"
    assert html.is_file()
    txt = html.read_text(encoding="utf-8")
    low = txt.lower()
    assert "innerhtml" not in low
    assert "outerhtml" not in low
    assert "insertadjacenthtml" not in low
    assert "document.write" not in low
    assert "textContent" in txt
    assert not re.search(r"mongodb(\+srv)?://", txt, re.I)
    assert "http://" not in low and "https://" not in low  # sem CDN ou URL externa


def test_index_html_estrutura_e_fluxos():
    from html.parser import HTMLParser
    from pathlib import Path

    class Checker(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []
            self.scripts = []
            self._in_script = False

        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
            if tag == "script":
                self._in_script = True

        def handle_endtag(self, tag):
            if tag == "script":
                self._in_script = False

        def handle_data(self, data):
            if self._in_script:
                self.scripts.append(data)

    txt = (Path(__file__).resolve().parents[1] / "painel" / "index.html").read_text(encoding="utf-8")
    p = Checker()
    p.feed(txt)
    p.close()
    assert "table" in p.tags
    js = "\n".join(p.scripts)
    assert "/api/status" in js
    assert "/api/comando" in js
    assert "10000" in js
    assert "confirm(" in js
    assert "LIBERAR" in js
    assert "liberar recursos" in txt.lower()
    assert "n/d" in js
    for campo in ("alias", "online", "cpus", "ram_gb", "disco_livre_gb", "temp_c", "gpu", "job_atual", "max_lives"):
        assert campo in js


# ---- T21: configuracao da Vercel
def test_vercel_json_valido_e_sem_segredos():
    from pathlib import Path
    import re

    p = Path(__file__).resolve().parents[1] / "painel" / "vercel.json"
    assert p.is_file()
    txt = p.read_text(encoding="utf-8")
    dados = json.loads(txt)
    assert isinstance(dados, dict)
    assert not re.search(r"mongodb(\+srv)?://", txt, re.I)
    assert "PAINEL_SENHA" not in str(dados.get("env", {}))
    assert "PAINEL_MONGO_URI" not in str(dados.get("env", {}))


def test_painel_requirements_pymongo_sem_segredos():
    from pathlib import Path
    import re

    p = Path(__file__).resolve().parents[1] / "painel" / "requirements.txt"
    assert p.is_file()
    txt = p.read_text(encoding="utf-8")
    linhas = [ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert "pymongo" in linhas
    assert not re.search(r"mongodb(\+srv)?://", txt, re.I)
