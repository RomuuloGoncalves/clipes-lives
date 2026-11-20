"""Painel (PAINEL-02, CMD-01): nucleo + handlers com mongomock e servidor local em porta efemera."""
import base64
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer

import mongomock
import pytest

from painel.api import _nucleo, comando, status as status_fn

NOW = datetime(2026, 1, 1, 12, 0, 0)


def basic(senha, user="x"):
    return "Basic " + base64.b64encode(f"{user}:{senha}".encode()).decode()


@pytest.fixture
def db():
    d = mongomock.MongoClient()["clipes_lives"]
    d.workers.insert_one({"_id": "w-1", "alias": "PC-1", "heartbeat_at": NOW - timedelta(seconds=61),
                          "capacity": {"cpus": 3}, "max_lives": 1})
    d.workers.insert_one({"_id": "w-2", "alias": "PC-2", "heartbeat_at": NOW - timedelta(seconds=10)})
    return d


# ---- T17: nucleo
def test_online_pela_batida(db):
    ws = {w["id"]: w for w in _nucleo.status(db, NOW, beat_s=20)["workers"]}
    assert ws["w-1"]["online"] is False and ws["w-2"]["online"] is True


def test_status_lives_e_jobs_24h(db):
    db.lives.insert_many([{"_id": "a", "status": "live", "owner": "w-1", "channel": "c",
                           "lease_until": NOW + timedelta(seconds=60)},
                          {"_id": "b", "status": "live", "owner": None},
                          {"_id": "c", "status": "ended", "owner": None}])
    db.jobs.insert_many([{"_id": "1", "status": "done", "updated_at": NOW - timedelta(hours=1)},
                         {"_id": "2", "status": "done", "updated_at": NOW - timedelta(hours=25)},
                         {"_id": "3", "status": "lost", "updated_at": NOW}])
    r = _nucleo.status(db, NOW)
    assert [l["id"] for l in r["lives"]] == ["a"]
    assert r["jobs_24h"] == {"done": 1, "lost": 1}


def test_comando_codigos(db):
    assert _nucleo.criar_comando(db, "w-2", "", NOW)[0] == 400
    assert _nucleo.criar_comando(db, "w-9", "LIBERAR", NOW)[0] == 404
    assert _nucleo.criar_comando(db, "w-2", "LIBERAR", NOW)[0] == 202
    assert _nucleo.criar_comando(db, "w-2", "LIBERAR", NOW)[0] == 409
    docs = list(db.commands.find())
    assert len(docs) == 1 and docs[0]["acao"] == "liberar" and docs[0]["status"] == "pending"
    # Após concluir o comando pendente, outro pode ser criado.
    db.commands.update_one({"_id": docs[0]["_id"]}, {"$set": {"status": "feito"}})
    assert _nucleo.criar_comando(db, "w-2", "LIBERAR", NOW)[0] == 202


def test_comando_409_atomico_em_corrida_e_duplicate_key(db, monkeypatch):
    from pymongo.errors import DuplicateKeyError

    db.commands.create_index([("worker_id", 1)], unique=True, partialFilterExpression={"status": "pending"})
    barrier = threading.Barrier(4)
    codes: list[int] = []
    lock = threading.Lock()

    def worker():
        barrier.wait(timeout=5)
        code, _ = _nucleo.criar_comando(db, "w-2", "LIBERAR", NOW)
        with lock:
            codes.append(code)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(codes) == [202, 409, 409, 409]
    assert db.commands.count_documents({"worker_id": "w-2", "status": "pending"}) == 1

    # DuplicateKeyError em corrida real no Mongo devolve 409 sem erro 500.
    def boom(*args, **kwargs):
        raise DuplicateKeyError("dup pending")

    monkeypatch.setattr(db.commands, "update_one", boom)
    assert _nucleo.criar_comando(db, "w-2", "LIBERAR", NOW) == (409, {"erro": "comando ja pendente"})


def test_status_sem_uri_nem_host(db):
    txt = json.dumps(_nucleo.status(db, NOW))
    assert "mongodb" not in txt and "://" not in txt


# ---- T18/T19: handlers
def chamar(mod, db, metodo, path_auth=None, corpo=None, headers=None, raw_body=None):
    # o handler usa o relogio real: mantem os workers online para o comando
    db.workers.update_many({}, {"$set": {"heartbeat_at": datetime.now(timezone.utc).replace(tzinfo=None)}})
    h = type("H", (mod.handler,), {"client": {"clipes_lives": db}})
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
    monkeypatch.delenv("PAINEL_SENHA", raising=False)
    monkeypatch.setenv("PAINEL_MONGO_URI", "mongodb://exemplo.invalid")


def test_status_acesso_livre_sem_auth(db, env):
    code, _, corpo = chamar(status_fn, db, "GET")
    assert code == 200 and len(json.loads(corpo)["workers"]) == 2
    assert b"exemplo.invalid" not in corpo


def test_status_e_comando_500_sem_uri(db, monkeypatch):
    monkeypatch.delenv("PAINEL_SENHA", raising=False)
    monkeypatch.delenv("PAINEL_MONGO_URI", raising=False)
    code, _, corpo = chamar(status_fn, db, "GET")
    assert code == 500 and json.loads(corpo) == {"erro": "painel nao configurado"}

    code_c, _, corpo_c = chamar(comando, db, "POST", None, {"worker_id": "w-1", "confirmar": "LIBERAR"})
    assert code_c == 500 and json.loads(corpo_c) == {"erro": "painel nao configurado"}


def test_comando_405_400_202_409(db, env):
    assert chamar(comando, db, "GET")[0] == 405
    assert chamar(comando, db, "POST", None, {"worker_id": "w-1"})[0] == 400
    assert chamar(comando, db, "POST", None, {"worker_id": "w-1", "confirmar": "LIBERAR"})[0] == 202
    assert chamar(comando, db, "POST", None, {"worker_id": "w-1", "confirmar": "LIBERAR"})[0] == 409
    assert db.commands.count_documents({}) == 1


def test_comando_exige_content_type_json_e_confere_origin(db, env):
    # Content-Type ausente ou form -> 415
    assert chamar(comando, db, "POST", raw_body=b"worker_id=w-1")[0] == 415
    assert chamar(
        comando, db, "POST", None, {"worker_id": "w-1", "confirmar": "LIBERAR"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )[0] == 415
    # charset junto de application/json é aceito
    assert chamar(
        comando, db, "POST", None, {"worker_id": "w-2", "confirmar": "LIBERAR"},
        headers={"Content-Type": "application/json; charset=utf-8"},
    )[0] == 202
    # Origin cruzado ou null -> 403
    assert chamar(
        comando, db, "POST", None, {"worker_id": "w-1", "confirmar": "LIBERAR"},
        headers={"Origin": "https://atacante.invalid"},
    )[0] == 403
    assert chamar(
        comando, db, "POST", None, {"worker_id": "w-1", "confirmar": "LIBERAR"},
        headers={"Origin": "null"},
    )[0] == 403
    # Origin compatível com Host -> 202
    assert chamar(
        comando, db, "POST", None, {"worker_id": "w-1", "confirmar": "LIBERAR"},
        headers={"Host": "painel.local", "Origin": "https://painel.local"},
    )[0] == 202


def test_abrir_reutiliza_client_lazy_com_max_pool_size_1(monkeypatch):
    import pymongo

    criados = []

    class FakeClient:
        def __init__(self, uri, **kwargs):
            self.uri = uri
            self.kwargs = kwargs
            self.closed = False
            criados.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(pymongo, "MongoClient", FakeClient)
    monkeypatch.setattr(_nucleo, "_CLIENT", None)
    monkeypatch.setattr(_nucleo, "_CLIENT_URI", None)

    assert _nucleo._CLIENT is None
    c1 = _nucleo._abrir("mongodb://um.invalid")
    c2 = _nucleo._abrir("mongodb://um.invalid")
    assert c1 is c2
    assert len(criados) == 1
    assert c1.kwargs["maxPoolSize"] == 1
    assert not c1.closed

    c3 = _nucleo._abrir("mongodb://dois.invalid")
    assert c3 is not c1
    assert c1.closed is True
    assert len(criados) == 2
    assert c3.kwargs["maxPoolSize"] == 1


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
    assert "auth-modal" not in txt
    assert "pedirSenha" not in txt
    js = "\n".join(p.scripts)
    assert "/api/status" in js
    assert "/api/comando" in js
    assert "Basic " not in js
    assert "Authorization" not in js
    assert "10000" in js
    assert "confirm(" in js
    assert "LIBERAR" in js
    assert "liberar recursos" in txt.lower()
    assert "n/d" in js
    for campo in ("alias", "online", "cpus", "cpu_pct", "ram_gb", "disco_livre_gb", "temp_c", "gpu", "job_atual", "max_lives"):
        assert campo in js
    # atualizar() chama /api/status diretamente sem Authorization/pedirSenha
    atualizar_js = js.split("function atualizar()", 1)[1]
    assert "Authorization" not in atualizar_js and "pedirSenha" not in atualizar_js and "basicHeader" not in atualizar_js


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


def test_status_serializa_history_com_datetime_em_json(db):
    db.workers.update_one({"_id": "w-2"}, {"$set": {"history": [{"t": NOW, "cpu_pct": 12.5, "temp_c": 55}]}})
    r = _nucleo.status(db, NOW, beat_s=20)
    ws = {w["id"]: w for w in r["workers"]}
    assert ws["w-2"]["history"] == [{"t": NOW.isoformat() + "Z", "cpu_pct": 12.5, "temp_c": 55}]
    json.dumps(r)  # nao pode levantar TypeError (500 no /api/status)


# ---- T17: slots, fila e alvos
def test_status_inclui_slots_fila_e_alvos(db):
    db.workers.update_one({"_id": "w-2"}, {"$set": {
        "slots": {"twitch": 2, "kick": 0}, "ocupados": {"twitch": 1}, "fila": {"janelas": 1, "busca": 0}}})
    assert _nucleo.criar_alvo(db, "fulano", "PC-2", NOW) == (201, {"ok": True, "_id": "fulano", "tipo": "live"})
    assert _nucleo.criar_alvo(db, "twitch.tv/outro", "", NOW + timedelta(seconds=1))[0] == 201
    assert _nucleo.criar_alvo(db, "kick.com/terceiro", "", NOW + timedelta(seconds=2))[0] == 201
    assert _nucleo.remover_alvo(db, "kick_terceiro")[0] == 200

    r = _nucleo.status(db, NOW)
    ws = {w["id"]: w for w in r["workers"]}
    assert ws["w-2"]["slots"] == {"twitch": 2, "kick": 0}
    assert ws["w-2"]["ocupados"] == {"twitch": 1}
    assert ws["w-2"]["fila"] == {"janelas": 1, "busca": 0}
    assert ws["w-1"]["slots"] is None and ws["w-1"]["fila"] is None
    assert [a["_id"] for a in r["alvos"]] == ["fulano", "outro"]  # removido nao aparece
    assert r["alvos"][0] == {"_id": "fulano", "tipo": "live", "plataforma": "twitch", "entrada": "fulano",
                             "owner": None, "pc_fixo": "PC-2", "fase": "sem vaga", "url": None}
    json.dumps(r)


PARIDADE_ALVOS = [
    "twitch.tv/Fulano", "https://www.twitch.tv/fulano_1", "fulano", "@Fulano", "twitch.tv/a/b",
    "twitch.tv/", "https://m.twitch.tv/x-y", "youtube.com/watch?v=abcd1234",
    "https://www.youtube.com/watch?v=abcd1234&t=1", "youtube.com/watch", "youtu.be/abcd1234",
    "youtu.be/ab", "https://www.youtube.com/live/abcd1234", "youtube.com/@Canal.X/live",
    "youtube.com/@canal", "https://kick.com/Slug-x", "kick.com/ab", "kick.com/a", "kick.com:443/slug",
    "https://user:pw@kick.com/slug", "ftp://kick.com/slug", "kick.com/slug;x", "kick.com/categories",
    "https://m.kick.com/xqc", "kick.com:443/slug", "x y", "", "@", "a.b",
    "https://example.com/abc", "https://twitch.tv/nome com",
]


@pytest.mark.parametrize("entrada", PARIDADE_ALVOS)
def test_parse_alvo_paridade_com_o_bot(entrada):
    # O painel nao importa clipes (Vercel); aqui conferimos que o espelho aceita/recusa e normaliza igual ao bot.
    from clipes import bot

    try:
        esperado = bot._parse_live_alvo(entrada)
    except ValueError:
        esperado = None
    got = _nucleo._parse_alvo(entrada)
    if esperado is None:
        assert got is None
    else:
        assert got == {"plataforma": esperado.plataforma, "chave": esperado.chave,
                       "canal": esperado.canal, "url": esperado.url}


def test_criar_alvo_valida_duplicado_e_grava_doc_como_o_bot(db):
    assert _nucleo.criar_alvo(db, "https://x.invalid/nope", "", NOW) == (400, {"erro": "alvo invalido"})
    assert _nucleo.criar_alvo(db, "  ", "", NOW) == (400, {"erro": "alvo invalido"})
    assert _nucleo.criar_alvo(db, "twitch.tv/Fulano", " PC-1 ", NOW) == (201, {"ok": True, "_id": "fulano", "tipo": "live"})
    doc = db.alvos.find_one({"_id": "fulano"})
    assert doc == {"_id": "fulano", "tipo": "live", "plataforma": "twitch", "entrada": "fulano",
                   "canal": "fulano", "url": None, "estado": "ativo", "pc_fixo": "PC-1", "owner": None,
                   "lease_until": None, "fase": "sem vaga", "criado_por": "painel", "criado_em": NOW,
                   "erro": None, "auto_vod": False}
    assert _nucleo.criar_alvo(db, "fulano", "", NOW) == (409, {"erro": "alvo ja acompanhado"})

    assert _nucleo.criar_alvo(db, "youtu.be/abcd1234", "", NOW)[0] == 201
    yt = db.alvos.find_one({"_id": "yt_abcd1234"})
    assert yt["entrada"] == "https://www.youtube.com/watch?v=abcd1234" and yt["pc_fixo"] is None


def test_criar_alvo_limite_30_ativos_e_remover(db):
    db.alvos.insert_many([{"_id": f"t{i}", "tipo": "live", "estado": "ativo"} for i in range(30)])
    assert _nucleo.criar_alvo(db, "fulano", "", NOW) == (409, {"erro": "limite de 30 alvos ativos atingido"})
    db.alvos.update_one({"_id": "t0"}, {"$set": {"estado": "removido"}})
    assert _nucleo.criar_alvo(db, "fulano", "", NOW)[0] == 201

    assert _nucleo.remover_alvo(db, "fulano") == (200, {"ok": True})
    assert db.alvos.find_one({"_id": "fulano"})["estado"] == "removido"
    assert _nucleo.remover_alvo(db, "fulano") == (404, {"erro": "alvo nao encontrado"})
    assert _nucleo.remover_alvo(db, "") == (400, {"erro": "alvo invalido"})
    assert _nucleo.gerir_alvo(db, {"acao": "xyz"}, NOW) == (400, {"erro": "acao invalida"})


def test_alvos_handler_origem_json_e_codigos(db, env):
    from painel.api import alvos as alvos_fn

    body = {"acao": "adicionar", "alvo": "fulano", "pc": ""}
    assert chamar(alvos_fn, db, "GET")[0] == 405
    assert chamar(alvos_fn, db, "POST", raw_body=b"alvo=fulano")[0] == 415
    assert chamar(alvos_fn, db, "POST", None, body, headers={"Origin": "https://atacante.invalid"})[0] == 403
    code, _, corpo = chamar(alvos_fn, db, "POST", None, body)
    assert code == 201 and json.loads(corpo) == {"ok": True, "_id": "fulano", "tipo": "live"}
    assert chamar(alvos_fn, db, "POST", None, body)[0] == 409
    assert chamar(alvos_fn, db, "POST", None, {"acao": "remover", "id": "fulano"})[0] == 200
    assert chamar(alvos_fn, db, "POST", None, {"acao": "xyz"})[0] == 400


def test_index_html_slots_fila_e_alvos():
    from html.parser import HTMLParser
    from pathlib import Path

    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.scripts, self._in = [], False

        def handle_starttag(self, tag, attrs):
            self._in = self._in or tag == "script"

        def handle_endtag(self, tag):
            self._in = False if tag == "script" else self._in

        def handle_data(self, data):
            if self._in:
                self.scripts.append(data)

    html = Path(__file__).resolve().parents[1] / "painel" / "index.html"
    raw = html.read_text(encoding="utf-8")
    p = Scripts()
    p.feed(raw)
    js = "\n".join(p.scripts)
    assert 'fetch("/api/alvos"' in js and "Authorization" not in js
    assert 'id="alvo-form"' in raw and 'id="alvos"' in raw
    for campo in ("slots", "ocupados", "fila", "pc_fixo", "owner", "fase", "entrada", "_id"):
        assert campo in js
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js


# ---- Onda 9 (T4/T5): config por PC, paridade de schema e /api/config

def test_config_schema_paridade_com_clipes():
    from clipes import config as clipes_config

    assert len(_nucleo.CONFIG_SCHEMA) == 54
    assert _nucleo.CONFIG_SCHEMA == clipes_config.CONFIG_SCHEMA
    assert _nucleo.FORBIDDEN_ENV_KEYS == clipes_config.FORBIDDEN_ENV_KEYS


def test_validate_config_payload_do_painel_rejeita_min_maior_ou_igual_max():
    assert _nucleo.validate_config_payload({"CLIPES_TEMP_MIN": "80", "CLIPES_TEMP_MAX": "80"})[1] is not None
    assert _nucleo.validate_config_payload({"CLIPES_TEMP_MAX": "70"})[1] is not None
    assert _nucleo.validate_config_payload({"CLIPES_TEMP_MAX": "80"}) == ({"CLIPES_TEMP_MAX": "80"}, None)
    assert _nucleo.validate_config_payload({"CLIPES_TEMP_MAX": "101"})[1] is not None
    assert _nucleo.validate_config_payload({"CLIPES_TEMP_MIN": "39"})[1] is not None
    assert _nucleo.CONFIG_SCHEMA["CLIPES_DISCORD_MAX_MB"]["default"] == "10"
    assert _nucleo.CONFIG_SCHEMA["CLIPES_VIGIA"]["grupo"] == "temperatura"


def test_status_retorna_pcs_online_offline_ordenados_com_config_e_schema(db):
    db.workers.update_one({"_id": "w-1"}, {"$set": {"config": {"CLIPES_WHISPER_MODEL": "large-v3-turbo"}}})
    db.workers.insert_one({"_id": "PC-0", "alias": "PC-0", "heartbeat_at": None, "config": {"CLIPES_PORTAO": "on"}})

    r = _nucleo.status(db, NOW, beat_s=20)
    assert [w["alias"] for w in r["workers"]] == ["PC-0", "PC-1", "PC-2"]
    assert [w["online"] for w in r["workers"]] == [False, False, True]
    assert r["workers"][0]["config"] == {"CLIPES_PORTAO": "on"}
    assert r["workers"][1]["config"] == {"CLIPES_WHISPER_MODEL": "large-v3-turbo"}
    assert r["workers"][2]["config"] == {}
    assert r["config_schema"] == _nucleo.CONFIG_SCHEMA
    json.dumps(r)


def test_salvar_config_persiste_limpa_e_faz_upsert_offline(db):
    code, body = _nucleo.salvar_config(db, {
        "pc_alias": "PC-1",
        "config": {"CLIPES_SLOTS": "twitch=2,kick=0", "CLIPES_WHISPER_MODEL": "large-v3-turbo", "CLIPES_BAR_H": "400"},
    }, NOW)
    assert code == 200
    assert body == {
        "ok": True,
        "pc_alias": "PC-1",
        "config": {"CLIPES_SLOTS": "twitch=2,kick=0", "CLIPES_WHISPER_MODEL": "large-v3-turbo", "CLIPES_BAR_H": "400"},
    }
    doc = db.workers.find_one({"_id": "w-1"})
    assert doc["config"] == body["config"]
    assert doc["config_updated_at"] == NOW

    # Campo vazio "" ou None remove a chave para voltar ao padrao
    code2, body2 = _nucleo.salvar_config(db, {
        "pc_alias": "PC-1",
        "config": {"CLIPES_SLOTS": "twitch=2,kick=0", "CLIPES_WHISPER_MODEL": "", "CLIPES_BAR_H": None},
    }, NOW)
    assert code2 == 200
    assert body2["config"] == {"CLIPES_SLOTS": "twitch=2,kick=0"}
    assert db.workers.find_one({"_id": "w-1"})["config"] == {"CLIPES_SLOTS": "twitch=2,kick=0"}

    # Upsert de PC novo/offline por pc_alias
    code3, body3 = _nucleo.salvar_config(db, {
        "pc_alias": "PC-NOVO",
        "config": {"CLIPES_LAYOUT": "full"},
    }, NOW)
    assert code3 == 200
    novo = db.workers.find_one({"_id": "PC-NOVO"})
    assert novo is not None and novo["alias"] == "PC-NOVO" and novo["heartbeat_at"] is None
    assert novo["config"] == {"CLIPES_LAYOUT": "full"}


@pytest.mark.parametrize("chave", [
    "CLIPES_MONGO_URI", "PAINEL_MONGO_URI", "PAINEL_SENHA", "DISCORD_BOT_TOKEN",
    "DISCORD_CHANNEL_ID", "GEMINI_API_KEYS", "YOUTUBE_CLIENT_SECRET", "TIKTOK_CLIENT_SECRET",
    "TWITCH_CLIENT_SECRET", "GROQ_API_KEY", "MINHA_CHAVE_SECRETA", "VARIAVEL_DESCONHECIDA",
])
def test_salvar_config_rejeita_segredos_e_desconhecidas(db, chave):
    code, body = _nucleo.salvar_config(db, {"pc_alias": "PC-1", "config": {chave: "abc"}}, NOW)
    assert code == 400
    assert body == {"erro": f"variavel nao permitida: {chave}"}
    assert db.workers.find_one({"_id": "w-1"}).get("config") is None


@pytest.mark.parametrize("cfg", [
    {"CLIPES_BAR_H": "999"},
    {"CLIPES_SPLIT_BAR_H": "101"},
    {"CLIPES_LAYOUT": "inexistente"},
    {"CLIPES_SLOTS": "twitch=abc"},
    {"CLIPES_AGENDA_HORARIOS": "25:99"},
    {"CLIPES_TZ": "Mars/Phobos"},
])
def test_salvar_config_rejeita_valores_invalidos(db, cfg):
    code, body = _nucleo.salvar_config(db, {"pc_alias": "PC-1", "config": cfg}, NOW)
    assert code == 400
    assert body["erro"].startswith("valor invalido para ")


def test_config_handler_http_codigos_e_csrf(db, env):
    from painel.api import config as config_fn

    assert chamar(config_fn, db, "GET")[0] == 405
    code_get, hdr_get, _ = chamar(config_fn, db, "GET")
    assert code_get == 405 and hdr_get.get("Allow") == "POST"
    assert chamar(config_fn, db, "POST", raw_body=b"pc_alias=PC-1")[0] == 415
    assert chamar(
        config_fn, db, "POST", None,
        {"pc_alias": "PC-1", "config": {"CLIPES_PORTAO": "on"}},
        headers={"Origin": "https://atacante.invalid"},
    )[0] == 403

    code_ok, _, body_ok = chamar(
        config_fn, db, "POST", None,
        {"pc_alias": "PC-1", "config": {"CLIPES_PORTAO": "on", "CLIPES_TZ": "UTC"}},
    )
    assert code_ok == 200
    assert json.loads(body_ok) == {
        "ok": True,
        "pc_alias": "PC-1",
        "config": {"CLIPES_PORTAO": "on", "CLIPES_TZ": "UTC"},
    }


def test_index_html_config_modal_e_sem_segredos():
    from pathlib import Path

    raw = (Path(__file__).resolve().parents[1] / "painel" / "index.html").read_text(encoding="utf-8")
    assert 'id="config-modal"' in raw
    assert 'id="novo-pc-btn"' in raw
    assert 'fetch("/api/config"' in raw
    assert '"configurar"' in raw
    assert "Restaurar padroes" in raw
    assert "config_schema" in raw
    for segredo in _nucleo.FORBIDDEN_ENV_KEYS:
        assert segredo not in raw, f"segredo {segredo} nao pode aparecer em index.html"


def test_dev_server_roteia_api_config(db, env):
    from painel import dev_server

    h = type("H", (dev_server.PainelDevHandler,), {"client": {"clipes_lives": db}})
    srv = HTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    req_get = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/api/config", method="GET")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req_get, timeout=5)
    assert exc.value.code == 405

    threading.Thread(target=srv.handle_request, daemon=True).start()
    req_post = urllib.request.Request(
        f"http://127.0.0.1:{srv.server_port}/api/config",
        method="POST",
        data=json.dumps({"pc_alias": "PC-1", "config": {"CLIPES_PORTAO": "on"}}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_post, timeout=5) as resp:
        assert resp.status == 200
        assert json.loads(resp.read()) == {"ok": True, "pc_alias": "PC-1", "config": {"CLIPES_PORTAO": "on"}}
    srv.server_close()


def test_salvar_config_global(db):
    assert _nucleo.salvar_config_global(db, {"config": {"CLIPES_LAYOUT": "inexistente"}}, NOW)[0] == 400
    assert _nucleo.salvar_config_global(db, {"config": {"CLIPES_BAR_H": "400"}}, NOW)[0] == 400
    assert _nucleo.salvar_config_global(db, {"config": {"PAINEL_SENHA": "x"}}, NOW)[0] == 400
    code, body = _nucleo.salvar_config_global(db, {"config": {"CLIPES_LAYOUT": "split-cheio"}}, NOW)
    assert code == 200 and body["config"] == {"CLIPES_LAYOUT": "split-cheio"}
    assert db.config_global.find_one({"_id": "global"})["config"] == {"CLIPES_LAYOUT": "split-cheio"}
    assert _nucleo.status(db, NOW)["config_global"] == {"CLIPES_LAYOUT": "split-cheio"}
    # vazio = remove o global (volta ao .env/padrao)
    assert _nucleo.salvar_config_global(db, {"config": {"CLIPES_LAYOUT": ""}}, NOW)[1]["config"] == {}
    assert _nucleo.status(db, NOW)["config_global"] == {}


# ---- ajustes-nov20 US-R: alvos VOD (AC 1-5)
@pytest.mark.parametrize("entrada", ["youtube.com/watch?v=abcd1234", "youtu.be/abcd1234", "youtube.com/live/abcd1234"])
def test_vod_forcado_aceita_video_youtube(db, entrada):
    code, body = _nucleo.criar_alvo(db, entrada, "", NOW, "vod")
    assert code == 201 and body["tipo"] == "vod"
    doc = db.alvos.find_one({"_id": body["_id"]})
    assert doc["tipo"] == "vod" and doc["url"] == "https://www.youtube.com/watch?v=abcd1234" and doc["fase"] == "na fila"


@pytest.mark.parametrize("entrada", ["gaules", "twitch.tv/x", "kick.com/x", "youtube.com/@x"])
def test_vod_forcado_rejeita_login_e_canal(db, entrada):
    assert _nucleo.criar_alvo(db, entrada, "", NOW, "vod")[0] == 400
    assert db.alvos.count_documents({}) == 0


def test_auto_vod_so_para_id_de_video(db):
    for entrada in ("youtube.com/@handle", "youtube.com/@handle/live"):
        assert _nucleo._parse_alvo_painel(entrada)["auto_vod"] is False
    assert _nucleo._parse_alvo_painel("youtube.com/watch?v=abcd1234")["auto_vod"] is True


@pytest.mark.parametrize("estado", ["feito", "falhou", "removido"])
def test_vod_finalizado_reativa(db, estado):
    url = "https://www.youtube.com/watch?v=abcd1234"
    _, b = _nucleo.criar_alvo(db, url, "", NOW, "vod")
    db.alvos.update_one({"_id": b["_id"]}, {"$set": {"estado": estado, "fase": "x", "erro": "boom"}})
    later = NOW + timedelta(hours=1)
    assert _nucleo.criar_alvo(db, url, "", later, "vod")[0] == 201
    d = db.alvos.find_one({"_id": b["_id"]})
    assert (d["estado"], d["fase"], d["erro"], d["criado_em"]) == ("ativo", "na fila", None, later)


def test_vod_ativo_duplicado_409(db):
    url = "https://www.youtube.com/watch?v=abcd1234"
    assert _nucleo.criar_alvo(db, url, "", NOW, "vod")[0] == 201
    assert _nucleo.criar_alvo(db, url, "", NOW, "vod")[0] == 409


def test_canonicalizacao_kick_clip_twitch_clip_e_plataforma_generica():
    p = _nucleo._parse_alvo_painel("https://www.kick.com/canal/clips/clip_01AB")
    assert p["url"] == "https://kick.com/canal/clips/clip_01AB" and p["plataforma"] == "kick"
    p = _nucleo._parse_alvo_painel("https://clips.twitch.tv/SlugBonito")
    assert p["plataforma"] == "twitch" and p["tipo"] == "vod"
    assert _nucleo._parse_alvo_painel("https://www.mywww.example.com/v/1")["plataforma"] == "mywww.example.com"
    assert _nucleo._parse_alvo_painel("https://m.vimeo.com/123")["plataforma"] == "vimeo.com"


# ---- Ajustes nov20 (T4): secao #vods-recentes escondida e em bloco

def test_index_html_vods_recentes_hidden_e_em_bloco():
    import re
    from pathlib import Path

    raw = (Path(__file__).resolve().parents[1] / "painel" / "index.html").read_text(encoding="utf-8")
    tag = re.search(r'<section id="vods-recentes"([^>]*)>', raw)
    assert tag, "vods-recentes deve ser <section id=\"vods-recentes\">"
    assert re.search(r"\shidden(\s|$)", " " + tag.group(1) + " "), "secao deve nascer hidden"
    assert "sec-head" not in tag.group(0), "o proprio section nao pode ter classe sec-head"
    bloco = raw[tag.end():]
    assert re.match(r'\s*<div class="sec-head">\s*<h2>[^<]+</h2>\s*</div>', bloco), "cabecalho em sec-head/h2 dentro da secao"
    compacto = re.sub(r"\s+", "", raw)
    assert re.search(r"#vods-recentes\[hidden\]\{display:none;?\}", compacto), "falta regra CSS [hidden]"


# ---- ajustes-nov20 T16: aba Temperatura e cor pelo limite do PC (VIG-08)
def test_index_html_aba_temperatura_e_cor_pelo_limite_do_pc():
    import re
    from pathlib import Path

    raw = (Path(__file__).resolve().parents[1] / "painel" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'\{\s*id:\s*"temperatura",\s*label:\s*"Temperatura"\s*\}', raw), "falta aba Temperatura em GRUPOS_CFG"
    m = re.search(r"var tempCls =(.*?);", raw, flags=re.S)
    assert m, "regra de cor da temperatura nao encontrada"
    regra = m.group(0)
    assert ">= 85" not in regra and ">= 75" not in regra, "limites nao podem ser literais"
    assert "CLIPES_TEMP_MAX" in raw and "CLIPES_TEMP_MIN" in raw
    assert "CLIPES_TEMP_MAX" in raw[raw.index("var tempCls") - 400:raw.index("var tempCls")]
    assert re.search(r'CLIPES_TEMP_MAX\s*\|\|\s*85', raw) and re.search(r'CLIPES_TEMP_MIN\s*\|\|\s*75', raw), "padrao 85/75 ausente"
    assert '"temp-off"' in raw and re.search(r'CLIPES_VIGIA\)\s*===\s*"0"', raw), "cinza com CLIPES_VIGIA=0 ausente"
    assert re.search(r"\.temp-off\s*\{[^}]*color", raw), "classe .temp-off sem cor"


def test_salvar_config_global_aceita_chaves_globais_antiflop_e_recusa_nao_globais(db):
    code, body = _nucleo.salvar_config_global(db, {"config": {"CLIPES_DUR_MAX": "60"}}, NOW)
    assert code == 200 and body["config"] == {"CLIPES_DUR_MAX": "60"}
    assert db.config_global.find_one({"_id": "global"})["config"] == {"CLIPES_DUR_MAX": "60"}
    assert _nucleo.salvar_config_global(db, {"config": {"CLIPES_CPU_THREADS": "4"}}, NOW)[0] == 400
    assert _nucleo.salvar_config_global(db, {"config": {"CLIPES_DUR_MAX": "12"}}, NOW)[0] == 400
    assert _nucleo.salvar_config_global(db, {"config": {"CLIPES_DUR_MIN": "30", "CLIPES_DUR_MAX": "30"}}, NOW)[0] == 400


# ---- T17: clipes recentes e visibilidade efetiva no status
def test_status_clips_recentes_campos_e_ordem(db):
    db.clips.insert_many([
        {"_id": "antigo", "titulo": "Velho", "canal": "c1", "gate": "ok", "criado_em": NOW - timedelta(days=1),
         "publicacoes": {}, "metricas": None, "arquivo": "/segredo/local.mp4"},
        {"_id": "novo", "titulo": "Novo", "canal": "c2", "gate": "bloqueado", "criado_em": NOW,
         "publicacoes": {"youtube": {"privacidade": "public", "url": "https://yt.invalid/x", "id": "9", "em": NOW}},
         "metricas": {"views": 10}},
    ])
    r = _nucleo.status(db, NOW)
    assert [c["_id"] for c in r["clips_recentes"]] == ["novo", "antigo"]
    assert r["clips_recentes"][0] == {
        "_id": "novo", "titulo": "Novo", "canal": "c2", "gate": "bloqueado",
        "publicacoes": {"youtube": {"privacidade": "public", "url": "https://yt.invalid/x"}},
        "metricas": {"views": 10},
    }
    assert "arquivo" not in json.dumps(r["clips_recentes"][1])
    json.dumps(r)


def test_status_clips_recentes_limite_50_e_vazio(db):
    assert _nucleo.status(db, NOW)["clips_recentes"] == []
    db.clips.insert_many([{"_id": f"c{i}", "criado_em": NOW + timedelta(seconds=i)} for i in range(60)])
    r = _nucleo.status(db, NOW)
    assert len(r["clips_recentes"]) == 50
    assert r["clips_recentes"][0]["_id"] == "c59"


def test_status_visibilidade_efetiva_padrao_e_global(db):
    assert _nucleo.status(db, NOW)["visibilidade_efetiva"] == "padrao"
    _nucleo.salvar_config_global(db, {"config": {"CLIPES_VISIBILIDADE": "private"}}, NOW)
    assert _nucleo.status(db, NOW)["visibilidade_efetiva"] == "private"


# ---- T19: config global, clipes e aviso de privado
def test_index_html_clipes_aviso_e_config_global_por_escopo():
    from pathlib import Path

    txt = (Path(__file__).resolve().parents[1] / "painel" / "index.html").read_text(encoding="utf-8")
    assert 'id="clipes"' in txt
    assert 'id="aviso-privado"' in txt
    assert "visibilidade_efetiva" in txt and '!== "public"' in txt
    assert "/api/config_global" in txt and "clips_recentes" in txt
    assert 'escopo === "global"' in txt and 'escopo !== "global"' in txt
    assert "CLIPES_LAYOUT" not in txt


def test_html_selects_seguem_tema():
    import re
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "painel" / "index.html").read_text(encoding="utf-8")
    m = re.search(r"\n\s*select \{(.*?)\n\s*\}", html, re.S)
    assert m, "regra CSS 'select' global ausente"
    regra = m.group(1)
    assert "appearance: none" in regra
    for v in ("--bg", "--border-strong", "--text", "--radius-sm"):
        assert f"var({v})" in regra
    assert "select:focus-visible" in html and "select:disabled" in html
    assert not re.search(r"<select[^>]*\sstyle=", html)


def test_html_config_global_em_modal():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "painel" / "index.html").read_text(encoding="utf-8")
    assert 'id="cfg-global-open"' in html
    assert 'id="config-global" class="cfg-modal-backdrop"' in html
    assert 'id="cfg-global-btn"' in html and 'id="cfg-global-fields"' in html
    assert "Layout dos clipes (global)" not in html
    assert '<section id="config-global"' not in html
    assert 'id="aviso-privado"' in html
