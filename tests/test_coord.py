"""Coordenação entre PCs (mongomock em memória; sem rede nem banco real)."""

import mongomock
import pytest
from pymongo.errors import DuplicateKeyError

from clipes import coord as coord_mod
from clipes.coord import Coord, mask_uri

LIVE = "twitch:canal_exemplo:123"


class Clock:
    def __init__(self, t=1_763_380_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    clock = Clock()
    db = mongomock.MongoClient().db
    a = Coord(db, "w-a", now=clock)
    b = Coord(db, "w-b", now=clock)
    a.ensure_live(LIVE, channel="canal_exemplo", platform="twitch")
    return clock, db, a, b


def test_mongomock_aceita_or_e_lt_no_find_one_and_update():
    # Done-when 1: se falhar, trocar por fake mínimo e registrar SPEC_DEVIATION.
    col = mongomock.MongoClient().db.c
    col.insert_one({"_id": 1, "owner": "x", "t": 1})
    got = col.find_one_and_update(
        {"_id": 1, "$or": [{"owner": None}, {"t": {"$lt": 5}}]}, {"$set": {"owner": "y"}}
    )
    assert got["owner"] == "x"
    assert col.find_one({"_id": 1})["owner"] == "y"


def test_a_reivindica_b_recebe_false(env):
    _, _, a, b = env
    assert a.claim_live(LIVE) is True
    assert b.claim_live(LIVE) is False


def test_lease_vencida_b_reivindica_e_renew_de_a_falha(env):
    clock, _, a, b = env
    assert a.claim_live(LIVE)
    clock.t += 91
    assert b.claim_live(LIVE) is True
    assert a.renew(LIVE) is False


def test_lease_viva_nao_e_roubada_e_renew_do_dono_estende(env):
    clock, db, a, b = env
    a.claim_live(LIVE)
    clock.t += 60
    assert a.renew(LIVE) is True
    clock.t += 60  # 120 s desde o claim, mas só 60 desde a renovação
    assert b.claim_live(LIVE) is False


def test_dono_reivindica_de_novo_e_live_encerrada_nao_e_reivindicada(env):
    _, db, a, b = env
    assert a.claim_live(LIVE) and a.claim_live(LIVE)
    a.release(LIVE, ended=True)
    doc = db.lives.find_one({"_id": LIVE})
    assert doc["status"] == "ended" and doc["owner"] is None
    assert b.claim_live(LIVE) is False


def test_release_sem_ended_libera_para_outro(env):
    _, db, a, b = env
    a.claim_live(LIVE)
    a.release(LIVE, ended=False)
    assert db.lives.find_one({"_id": LIVE})["status"] == "live"
    assert b.claim_live(LIVE) is True


def test_release_de_nao_dono_nao_altera(env):
    _, db, a, b = env
    a.claim_live(LIVE)
    b.release(LIVE, ended=True)
    assert db.lives.find_one({"_id": LIVE})["owner"] == "w-a"


def test_ensure_live_idempotente_e_duplicate_key_nao_levanta(env, monkeypatch):
    _, db, a, _ = env
    a.ensure_live(LIVE, channel="outro")
    assert db.lives.count_documents({}) == 1
    assert db.lives.find_one({"_id": LIVE})["channel"] == "canal_exemplo"  # setOnInsert

    def boom(*args, **kw):
        raise DuplicateKeyError("dup")

    monkeypatch.setattr(db.lives, "update_one", boom)
    a.ensure_live(LIVE)  # não levanta


def test_ensure_indexes_cria_os_indices_do_design(env):
    _, db, a, _ = env
    a.ensure_indexes()
    assert "status_1_lease_until_1" in db.lives.index_information()
    assert "status_1_priority_-1" in db.jobs.index_information()
    assert "worker_id_1_status_1" in db.commands.index_information()


def test_mask_uri():
    assert mask_uri("mongodb+srv://u:p@h/x") == "mongodb+srv://***@***"
    assert mask_uri("lixo-sem-esquema") == "***"


# ---- T7: jobs idempotentes por janela e janelas perdidas ----

JOB = f"{LIVE}:1763416800"


def add(c, job_id=JOB, end=1763416800, priority=4.2):
    return c.add_job(job_id, LIVE, (end - 600, end), priority)


def test_add_job_insere_e_nao_recria_id_existente(env):
    _, db, a, b = env
    assert add(a) is True
    doc = db.jobs.find_one({"_id": JOB})
    assert (doc["status"], doc["live_id"], doc["window_start"], doc["window_end"], doc["priority"]) == (
        "pending", LIVE, 1763416200, 1763416800, 4.2)
    db.jobs.update_one({"_id": JOB}, {"$set": {"status": "done"}})
    assert add(b, priority=9.9) is False
    assert db.jobs.count_documents({}) == 1
    assert db.jobs.find_one({"_id": JOB})["status"] == "done"


def test_concluir_duas_vezes_deixa_um_done_e_devolve_false_na_segunda(env):
    _, db, a, _ = env
    add(a)
    assert a.start_job(JOB) is True
    assert a.finish_job(JOB, "done", {"clips": 3}) is True
    assert a.finish_job(JOB, "done", {"clips": 99}) is False
    doc = db.jobs.find_one({"_id": JOB})
    assert (doc["status"], doc["result"]) == ("done", {"clips": 3})
    assert db.jobs.count_documents({"status": "done"}) == 1


def test_outro_owner_nao_conclui(env):
    _, db, a, b = env
    add(a)
    a.start_job(JOB)
    assert b.finish_job(JOB, "done", {}) is False
    assert db.jobs.find_one({"_id": JOB})["status"] == "processing"


def test_job_pendente_so_e_iniciado_por_um_pc(env):
    _, _, a, b = env
    add(a)
    assert a.start_job(JOB) is True
    assert b.start_job(JOB) is False


def test_ate_tres_tentativas_permite_terceira_e_marca_failed_apos_falha(env):
    clock, db, a, b = env
    add(a)
    assert a.start_job(JOB) is True  # attempts 1
    assert a.start_job(JOB) is True  # attempts 2 (retomada pelo mesmo dono)
    assert a.start_job(JOB) is True  # attempts 3 (3ª execução real permitida)
    # Enquanto a 3ª execução está no lease de A, outro worker não pode derrubá-la para failed.
    assert b.start_job(JOB) is False
    assert db.jobs.find_one({"_id": JOB})["status"] == "processing"
    # Falhou a 3ª (não concluiu): próxima tentativa marca failed e recusa execução.
    assert a.start_job(JOB) is False
    doc = db.jobs.find_one({"_id": JOB})
    assert (doc["status"], doc["attempts"]) == ("failed", 3)
    assert a.finish_job(JOB, "done", {}) is False


def test_terceira_tentativa_pode_concluir_done(env):
    _, db, a, _ = env
    add(a)
    assert a.start_job(JOB) is True
    assert a.start_job(JOB) is True
    assert a.start_job(JOB) is True
    assert a.finish_job(JOB, "done", {"clips": 1}) is True
    doc = db.jobs.find_one({"_id": JOB})
    assert (doc["status"], doc["attempts"]) == ("done", 3)


def test_start_job_de_id_inexistente_devolve_false(env):
    assert env[2].start_job("nao-existe") is False


def test_claim_de_dono_vencido_marca_lost_pending_e_processing_dele(env):
    clock, db, a, b = env
    a.claim_live(LIVE)
    add(a, f"{LIVE}:1", 1)
    add(a, f"{LIVE}:2", 2)
    add(a, f"{LIVE}:3", 3)
    a.start_job(f"{LIVE}:2")
    a.start_job(f"{LIVE}:3")
    a.finish_job(f"{LIVE}:3", "done", {})
    add(b, f"{LIVE}:4", 4)  # job de outro dono/sem dono não é tocado
    db.jobs.update_one({"_id": f"{LIVE}:4"}, {"$set": {"owner": "w-b"}})
    clock.t += 91
    assert b.claim_live(LIVE) is True
    st = {d["_id"]: d["status"] for d in db.jobs.find()}
    assert st == {f"{LIVE}:1": "lost", f"{LIVE}:2": "lost", f"{LIVE}:3": "done", f"{LIVE}:4": "pending"}


def test_claim_sem_dono_anterior_ou_do_proprio_dono_nao_marca_lost(env):
    clock, db, a, b = env
    add(a)
    db.jobs.update_one({"_id": JOB}, {"$set": {"owner": "w-a"}})
    a.claim_live(LIVE)
    clock.t += 91
    assert a.claim_live(LIVE) is True  # o próprio dono volta com a lease vencida
    assert db.jobs.find_one({"_id": JOB})["status"] == "pending"


# ---- T8: heartbeat do PC ----

CAP = {"cpus": 3, "ram_gb": 2.6, "disco_livre_gb": 15.1, "temp_c": 78, "gpu": None, "score": 3}


def test_181_batidas_deixam_180_no_historico(env):
    clock, db, a, _ = env
    for i in range(181):
        clock.t += 20
        a.beat("PC-1", {**CAP, "temp_c": i}, None, 1, 0.33)
    doc = db.workers.find_one({"_id": "w-a"})
    assert len(doc["history"]) == 180
    assert doc["history"][0]["temp_c"] == 1 and doc["history"][-1]["temp_c"] == 180  # a mais antiga saiu
    assert db.workers.count_documents({}) == 1  # upsert por worker_id


def test_beat_grava_campos_do_design_e_nada_alem_deles(env):
    clock, db, a, _ = env
    a.beat("PC-1", CAP, "job-x", 2, 0.5)
    doc = db.workers.find_one({"_id": "w-a"})
    assert set(doc) == {"_id", "alias", "capacity", "job_atual", "max_lives", "razao", "heartbeat_at", "history"}
    assert (doc["alias"], doc["capacity"], doc["job_atual"], doc["max_lives"], doc["razao"]) == (
        "PC-1", CAP, "job-x", 2, 0.5)
    assert doc["heartbeat_at"] == a._dt()
    assert set(doc["history"][0]) == {"t", "cpu_pct", "temp_c"}
    assert doc["history"][0]["temp_c"] == 78


def test_beat_atualiza_sem_duplicar_e_aceita_job_nulo(env):
    clock, db, a, _ = env
    a.beat("PC-1", CAP, "job-x", 1, 0.33)
    clock.t += 20
    a.beat("PC-1", CAP, None, 1, 0.33)
    doc = db.workers.find_one({"_id": "w-a"})
    assert doc["job_atual"] is None and len(doc["history"]) == 2
    assert doc["heartbeat_at"] == a._dt()


# ---- T9: comandos do painel por PC ----

def cmd(db, worker_id="w-a", acao="liberar", t=1, status="pending"):
    return db.commands.insert_one(
        {"worker_id": worker_id, "acao": acao, "status": status, "criado_em": t, "executado_em": None}
    ).inserted_id


def test_comando_de_outro_worker_nao_e_lido(env):
    _, db, a, _ = env
    cmd(db, "w-b")
    assert a.pending_command() is None


def test_devolve_o_pending_mais_antigo_do_proprio_worker(env):
    _, db, a, _ = env
    cmd(db, t=5)
    velho = cmd(db, t=2)
    cmd(db, t=1, status="feito")
    assert a.pending_command()["_id"] == velho


def test_acao_fora_da_lista_branca_vira_rejeitado_e_nao_e_devolvida(env):
    _, db, a, _ = env
    ruim = cmd(db, acao="apagar", t=1)
    ok = cmd(db, acao="liberar", t=2)
    got = a.pending_command()
    assert got["_id"] == ok and got["acao"] == "liberar"
    assert db.commands.find_one({"_id": ruim})["status"] == "rejeitado"
    assert a.pending_command()["_id"] == ok


def test_so_acao_apagar_nunca_devolve_nada(env):
    _, db, a, _ = env
    ruim = cmd(db, acao="apagar")
    assert a.pending_command() is None
    assert db.commands.find_one({"_id": ruim})["status"] == "rejeitado"


def test_mark_command_executando_e_feito_gravam_status_e_executado_em(env):
    clock, db, a, _ = env
    i = cmd(db)
    a.mark_command(i, "executando")
    assert db.commands.find_one({"_id": i})["status"] == "executando"
    assert a.pending_command() is None
    a.mark_command(i, "feito")
    doc = db.commands.find_one({"_id": i})
    assert doc["status"] == "feito" and doc["executado_em"] == a._dt()


def test_mark_command_nao_mexe_em_comando_de_outro_worker(env):
    _, db, a, _ = env
    i = cmd(db, "w-b")
    a.mark_command(i, "feito")
    assert db.commands.find_one({"_id": i})["status"] == "pending"
