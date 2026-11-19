"""Auditoria (onda 1-C): leases/fila de publicacao, espelho do registro, painel, worker_id."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mongomock
import pytest

from clipes import capacidade, publicacao
from clipes.publicacao import LocalStore, MongoStore, run_pending
from clipes.registro import Registro
from painel.api import _nucleo

T0 = 1_763_400_000.0


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _req(cid: str, plat: str = "youtube", **extra) -> dict:
    d = {"_id": f"{cid}:{plat}", "clip_id": cid, "plataforma": plat, "owner": "w-1",
         "status": "pending", "criado_em": _dt(T0)}
    d.update(extra)
    return d


@pytest.fixture
def db():
    return mongomock.MongoClient().clipes_lives


# ---- A-01
def test_excecao_inesperada_marca_erro_e_segue(db, tmp_path, monkeypatch):
    db.publish_requests.insert_many([_req("a"), _req("b")])
    chamados = []

    def fake_find(root, clip_id, store=None):
        chamados.append(clip_id)
        if clip_id == "a":
            raise ValueError("boom inesperado")
        return None

    monkeypatch.setattr(publicacao, "find_clip", fake_find)
    out = run_pending(MongoStore(db), "w-1", out_root=tmp_path, now=T0)
    assert chamados == ["a", "b"]
    assert db.publish_requests.find_one({"_id": "a:youtube"})["status"] == "erro"
    assert db.publish_requests.find_one({"_id": "b:youtube"})["status"] == "erro_arquivo"
    assert [o["status"] for o in out] == ["erro", "erro_arquivo"]


def test_erro_de_mongo_continua_subindo(db, tmp_path, monkeypatch):
    from pymongo.errors import AutoReconnect

    db.publish_requests.insert_one(_req("a"))

    def fake_find(root, clip_id, store=None):
        raise AutoReconnect("caiu")

    monkeypatch.setattr(publicacao, "find_clip", fake_find)
    with pytest.raises(AutoReconnect):
        run_pending(MongoStore(db), "w-1", out_root=tmp_path, now=T0)


def test_claim_grava_lease_mongo(db):
    db.publish_requests.insert_one(_req("a"))
    doc = MongoStore(db).claim_request("a:youtube", worker_id="w-1", now=T0)
    assert doc["status"] == "running"
    assert doc["iniciado_em"] == _dt(T0)
    assert doc["lease_until"] == _dt(T0 + 1800)


def test_claim_grava_lease_local_e_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_PUB_LEASE_S", "60")
    st = LocalStore(tmp_path)
    st.enqueue_request(_req("a"))
    doc = st.claim_request("a:youtube", worker_id="w-1", now=T0)
    assert doc["lease_until"] == _dt(T0 + 60)


def test_running_com_lease_vencida_volta_a_pending_mongo(db, tmp_path, monkeypatch):
    db.publish_requests.insert_many([
        _req("vencido", status="running", lease_until=_dt(T0 - 10)),
        _req("legado", status="running"),
        _req("vivo", status="running", lease_until=_dt(T0 + 500)),
    ])
    monkeypatch.setattr(publicacao, "find_clip", lambda *a, **k: None)
    run_pending(MongoStore(db), "w-1", out_root=tmp_path, now=T0)
    st = {d["_id"]: d["status"] for d in db.publish_requests.find()}
    assert st["vencido:youtube"] == "erro_arquivo"  # voltou a pending e foi reprocessado
    assert st["legado:youtube"] == "erro_arquivo"
    assert st["vivo:youtube"] == "running"


def test_running_com_lease_vencida_volta_a_pending_local(tmp_path):
    st = LocalStore(tmp_path)
    st.enqueue_request(_req("a"))
    st.claim_request("a:youtube", worker_id="w-1", now=T0)
    assert st.release_expired("w-1", now=T0 + 10) == 0
    assert st.release_expired("w-1", now=T0 + 4000) == 1
    assert st.get_request("a", "youtube")["status"] == "pending"


def test_retomada_nao_reenvia_video_ja_publicado(db, tmp_path, monkeypatch):
    db.publish_requests.insert_one(_req(
        "a", status="running", lease_until=_dt(T0 - 1),
        resultado={"video_id": "yt123", "privacidade": "private"}))
    chamou = []
    monkeypatch.setattr(publicacao, "find_clip", lambda *a, **k: chamou.append(1) or None)
    out = run_pending(MongoStore(db), "w-1", out_root=tmp_path, now=T0,
                      publishers={"youtube": lambda *a: chamou.append("pub") or {"video_id": "novo"}})
    d = db.publish_requests.find_one({"_id": "a:youtube"})
    assert d["status"] == "ok" and d["resultado"]["video_id"] == "yt123"
    assert "pub" not in chamou
    assert out[0]["status"] == "ok"


def test_limit_processa_no_maximo_n(db, tmp_path, monkeypatch):
    db.publish_requests.insert_many([_req(f"c{i}") for i in range(5)])
    monkeypatch.setattr(publicacao, "find_clip", lambda *a, **k: None)
    out = run_pending(MongoStore(db), "w-1", out_root=tmp_path, now=T0, limit=2)
    assert len(out) == 2
    assert db.publish_requests.count_documents({"status": "pending"}) == 3
    # padrao (env) = 3 por chamada
    out = run_pending(MongoStore(db), "w-1", out_root=tmp_path, now=T0)
    assert len(out) == 3


# ---- M-06
def _rec(cid="c1", **kw):
    r = {"clip_id": cid, "arquivo": "x.mp4", "sha256": "h", "tamanho": 1}
    r.update(kw)
    return r


def test_espelho_nao_grava_versao_antiga(db, tmp_path):
    reg = Registro(tmp_path, mongo=db)
    reg.upsert(_rec())
    reg.marcar("c1", titulo="novo")  # rev 2 no Mongo
    docs = Registro(tmp_path).get("c1")
    assert docs["rev"] == 2
    # snapshot antigo (rev 1) chegando fora de ordem
    antigo = {"clip_id": "c1", "titulo": "velho", "rev": 1, "mongo_synced": False}
    assert reg._mirror("c1", antigo) is False
    assert db.clips.find_one({"_id": "c1"})["titulo"] == "novo"
    # mesmo rev (reenvio) conta como sincronizado
    assert reg._mirror("c1", dict(docs)) is True


def test_espelho_atualiza_doc_antigo_sem_rev(db, tmp_path):
    db.clips.insert_one({"_id": "c1", "clip_id": "c1", "titulo": "legado"})
    reg = Registro(tmp_path, mongo=db)
    reg.upsert(_rec(titulo="atual"))
    assert db.clips.find_one({"_id": "c1"})["titulo"] == "atual"
    assert db.clips.find_one({"_id": "c1"})["rev"] == 1


def test_rev_incrementa_e_sync_marca(db, tmp_path):
    reg = Registro(tmp_path, mongo=db)
    reg.upsert(_rec())
    reg.marcar("c1", status="so_discord")
    rec = reg.get("c1")
    assert rec["rev"] == 2 and rec["mongo_synced"] is True


# ---- A-02 / M-03
NOW = datetime(2026, 1, 1, 12, 0, 0)


def test_painel_lives_fantasma_nao_aparecem(db):
    db.lives.insert_many([
        {"_id": "ok", "status": "live", "owner": "w-1", "lease_until": NOW + timedelta(seconds=30)},
        {"_id": "fantasma", "status": "live", "owner": "w-1", "lease_until": NOW - timedelta(seconds=30)},
    ])
    assert [l["id"] for l in _nucleo.status(db, NOW)["lives"]] == ["ok"]


def test_comando_grava_expira_em_e_recusa_worker_offline(db):
    db.workers.insert_one({"_id": "w-on", "heartbeat_at": NOW - timedelta(seconds=10)})
    db.workers.insert_one({"_id": "w-off", "heartbeat_at": NOW - timedelta(seconds=600)})
    assert _nucleo.criar_comando(db, "w-on", "LIBERAR", NOW)[0] == 202
    cmd = db.commands.find_one({"worker_id": "w-on"})
    assert cmd["expira_em"] == NOW + timedelta(minutes=10)
    code, _ = _nucleo.criar_comando(db, "w-off", "LIBERAR", NOW)
    assert code == 409
    assert db.commands.count_documents({"worker_id": "w-off"}) == 0


def test_comando_vencido_nao_trava_novo(db):
    db.workers.insert_one({"_id": "w-on", "heartbeat_at": NOW - timedelta(seconds=10)})
    db.commands.insert_one({"worker_id": "w-on", "acao": "liberar", "status": "pending",
                            "criado_em": NOW - timedelta(hours=2), "expira_em": NOW - timedelta(hours=1)})
    assert _nucleo.criar_comando(db, "w-on", "LIBERAR", NOW)[0] == 202
    st = sorted(c["status"] for c in db.commands.find({"worker_id": "w-on"}))
    assert st == ["expirado", "pending"]


# ---- B-04
def test_worker_id_criacao_atomica(tmp_path):
    ids = []

    def go():
        ids.append(capacidade.worker_id(tmp_path))

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(ids)) == 1
    assert (tmp_path / ".worker_id").read_text(encoding="utf-8").strip() == ids[0]


def test_worker_id_usa_o_do_outro_processo_se_ja_existe(tmp_path, monkeypatch):
    # simula corrida: leitura inicial vazia, mas o arquivo aparece antes do O_EXCL
    (tmp_path / ".worker_id").write_text("w-aaaaaaaaaaaa", encoding="utf-8")
    real_read = Path.read_text
    calls = {"n": 0}

    def fake_read(self, *a, **k):
        if self.name == ".worker_id" and calls["n"] == 0:
            calls["n"] += 1
            return ""
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", fake_read)
    assert capacidade.worker_id(tmp_path) == "w-aaaaaaaaaaaa"


# ---- revisao
def test_pedido_nao_vencido_nao_consome_limit_e_limpa_lease(db, tmp_path, monkeypatch):
    db.publish_requests.insert_many([_req(f"c{i}") for i in range(3)])
    futuro = {"clips_json_path": None, "mp4_path": None, "channel": "x", "created_at": T0}

    def fake_find(root, clip_id, store=None):
        return futuro if clip_id in ("c0", "c1") else None

    monkeypatch.setattr(publicacao, "find_clip", fake_find)
    monkeypatch.setattr(publicacao, "_effective_ready_at",
                        lambda found, pol: T0 + 999 if found is futuro else None)
    out = run_pending(MongoStore(db), "w-1", out_root=tmp_path, now=T0, limit=1)
    assert [o["clip_id"] for o in out] == ["c2"]  # c0/c1 (adiados) nao consumiram o limit
    d = db.publish_requests.find_one({"_id": "c0:youtube"})
    assert d["status"] == "pending" and not d.get("lease_until") and not d.get("iniciado_em")


def test_erro_inesperado_sanitiza_e_preserva_parcial(db, tmp_path, monkeypatch):
    db.publish_requests.insert_one(_req("a", status="running", lease_until=_dt(T0 - 1),
                                        resultado={"video_id": "yt1"}))

    def boom(*a, **k):
        raise ValueError("falha https://www.googleapis.com/upload/youtube/v3/videos?upload_id=seg")

    monkeypatch.setattr(publicacao, "find_clip", boom)
    run_pending(MongoStore(db), "w-1", out_root=tmp_path, now=T0)
    d = db.publish_requests.find_one({"_id": "a:youtube"})
    assert d["status"] == "erro"
    assert d["resultado"]["video_id"] == "yt1"
    assert "upload_id=seg" not in d["resultado"]["erro"]


def test_retomada_faz_contabilidade_sem_reenviar(db, tmp_path, monkeypatch):
    db.publish_requests.insert_one(_req("a", status="running", lease_until=_dt(T0 - 1),
                                        resultado={"video_id": "yt1", "url": "https://y/1"}))
    gravou = []
    monkeypatch.setattr(publicacao, "find_clip", lambda *a, **k: {"clips_json_path": None, "thread_id": "t"})
    monkeypatch.setattr(publicacao, "_record_clip_publication", lambda *a, **k: gravou.append("clips"))
    monkeypatch.setattr(Registro, "registrar_publicacao", lambda self, *a, **k: gravou.append("reg"))
    posts = []
    out = run_pending(MongoStore(db), "w-1", out_root=tmp_path, now=T0,
                      discord_post=lambda t, m: posts.append((t, m)),
                      publishers={"youtube": lambda *a: pytest.fail("reenviou")})
    assert gravou == ["clips", "reg"] and posts and out[0]["status"] == "ok"


def test_worker_id_arquivo_vazio_grava_id_estavel(tmp_path, monkeypatch):
    monkeypatch.setattr(capacidade.time, "sleep", lambda s: None)
    (tmp_path / ".worker_id").write_text("", encoding="utf-8")
    a = capacidade.worker_id(tmp_path)
    assert a.startswith("w-") and capacidade.worker_id(tmp_path) == a


def test_espelho_duplicate_key_com_rev_invalido(db, tmp_path):
    reg = Registro(tmp_path, mongo=db)
    db.clips.insert_one({"_id": "c1", "rev": "abc"})
    assert reg._mirror("c1", {"clip_id": "c1", "rev": 1}) is False
