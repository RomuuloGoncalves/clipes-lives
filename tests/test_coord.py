"""Coordenação entre PCs (mongomock em memória; sem rede nem banco real)."""

import threading

import mongomock
import pytest
from pymongo.errors import DuplicateKeyError

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
    cmd_idx = db.commands.index_information()
    assert "worker_id_1_status_1" in cmd_idx
    assert cmd_idx["worker_id_1"]["unique"] is True
    assert cmd_idx["worker_id_1"]["partialFilterExpression"] == {"status": "pending"}
    db.commands.insert_one({"worker_id": "w-a", "acao": "liberar", "status": "pending"})
    with pytest.raises(DuplicateKeyError):
        db.commands.insert_one({"worker_id": "w-a", "acao": "liberar", "status": "pending"})
    db.commands.insert_one({"worker_id": "w-a", "acao": "liberar", "status": "feito"})
    db.commands.insert_one({"worker_id": "w-a", "acao": "liberar", "status": "feito"})


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

CAP = {"cpus": 3, "cpu_pct": 42.5, "ram_gb": 2.6, "disco_livre_gb": 15.1, "temp_c": 78, "gpu": None, "score": 3}


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
    assert doc["history"][0]["cpu_pct"] == 42.5
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


# ---- #10: corrida concorrente em threads ----


def test_corrida_dois_workers_em_threads_so_um_reivindica_cada_live(env):
    """Corrida com 2 threads sincronizadas por barreira em N lives.

    Nota: mongomock roda em memória e não prova atomicidade de rede/wiredtiger;
    a atomicidade real vem do `find_one_and_update` único no MongoDB.
    """
    _, db, a, b = env
    live_ids = [f"twitch:canal_{i}:100{i}" for i in range(20)]
    for lid in live_ids:
        a.ensure_live(lid, channel="canal", platform="twitch")

    barrier = threading.Barrier(2)
    wins: dict[str, set[str]] = {"w-a": set(), "w-b": set()}
    errors: list[BaseException] = []

    def worker(coord: Coord) -> None:
        try:
            for lid in live_ids:
                barrier.wait(timeout=5)
                if coord.claim_live(lid):
                    wins[coord.worker_id].add(lid)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(a,)), threading.Thread(target=worker, args=(b,))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    assert wins["w-a"].isdisjoint(wins["w-b"])
    assert wins["w-a"] | wins["w-b"] == set(live_ids)
    for lid in live_ids:
        expected_owner = "w-a" if lid in wins["w-a"] else "w-b"
        assert db.lives.find_one({"_id": lid})["owner"] == expected_owner


def test_corrida_dois_workers_em_threads_so_um_inicia_e_cria_cada_job(env):
    """Corrida com 2 threads em `add_job` e `start_job`: só um vence cada job."""
    _, db, a, b = env
    job_ids = [f"{LIVE}:{1763416800 + i * 300}" for i in range(20)]
    barrier_add = threading.Barrier(2)
    barrier_start = threading.Barrier(2)
    add_wins: dict[str, set[str]] = {"w-a": set(), "w-b": set()}
    start_wins: dict[str, set[str]] = {"w-a": set(), "w-b": set()}
    errors: list[BaseException] = []

    def racer_add(coord: Coord) -> None:
        try:
            for i, jid in enumerate(job_ids):
                end = 1763416800 + i * 300
                barrier_add.wait(timeout=5)
                if coord.add_job(jid, LIVE, (end - 600, end), 5.0):
                    add_wins[coord.worker_id].add(jid)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads_add = [threading.Thread(target=racer_add, args=(a,)), threading.Thread(target=racer_add, args=(b,))]
    for t in threads_add:
        t.start()
    for t in threads_add:
        t.join(timeout=10)

    assert not errors
    assert add_wins["w-a"].isdisjoint(add_wins["w-b"])
    assert add_wins["w-a"] | add_wins["w-b"] == set(job_ids)
    assert db.jobs.count_documents({}) == len(job_ids)

    # Libera owner para que A e B possam disputar start_job em igualdade.
    db.jobs.update_many({}, {"$set": {"owner": None}})

    def racer_start(coord: Coord) -> None:
        try:
            for jid in job_ids:
                barrier_start.wait(timeout=5)
                if coord.start_job(jid):
                    start_wins[coord.worker_id].add(jid)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads_start = [threading.Thread(target=racer_start, args=(a,)), threading.Thread(target=racer_start, args=(b,))]
    for t in threads_start:
        t.start()
    for t in threads_start:
        t.join(timeout=10)

    assert not errors
    assert start_wins["w-a"].isdisjoint(start_wins["w-b"])
    assert start_wins["w-a"] | start_wins["w-b"] == set(job_ids)
    for jid in job_ids:
        doc = db.jobs.find_one({"_id": jid})
        expected_owner = "w-a" if jid in start_wins["w-a"] else "w-b"
        assert (doc["status"], doc["attempts"], doc["owner"]) == ("processing", 1, expected_owner)


# ---- auditoria A-02 / M-03 / M-22: API nova do Coord ----

def test_count_owned_live_ignora_lease_vencida_e_excluidos(env):
    clock, db, a, _ = env
    a.claim_live(LIVE)
    a.ensure_live("twitch:outro:1")
    a.claim_live("twitch:outro:1")
    assert a.count_owned_live() == 2
    assert a.count_owned_live(exclude={LIVE}) == 1
    clock.t += 91  # crash: ninguem renovou
    assert a.count_owned_live() == 0


def test_release_expired_own_solta_so_as_vencidas_do_proprio_pc(env):
    clock, db, a, b = env
    a.claim_live(LIVE)
    b.ensure_live("twitch:b:1")
    b.claim_live("twitch:b:1")
    clock.t += 91
    a.ensure_live("twitch:viva:1")
    a.claim_live("twitch:viva:1")
    assert a.release_expired_own() == 1
    assert db.lives.find_one({"_id": LIVE})["owner"] is None
    assert db.lives.find_one({"_id": LIVE})["status"] == "live"  # nao marca ended
    assert db.lives.find_one({"_id": "twitch:b:1"})["owner"] == "w-b"
    assert db.lives.find_one({"_id": "twitch:viva:1"})["owner"] == "w-a"


def test_beat_grava_beat_s_quando_informado(env):
    _, db, a, _ = env
    a.beat("PC-1", CAP, None, 1, 0.5, beat_s=30)
    assert db.workers.find_one({"_id": "w-a"})["beat_s"] == 30.0


def test_comando_expirado_vira_expirado_e_nao_e_devolvido(env):
    from datetime import timedelta

    _, db, a, _ = env
    old = db.commands.insert_one({"worker_id": "w-a", "acao": "liberar", "status": "pending", "criado_em": 1,
                                  "expira_em": a._dt() - timedelta(seconds=1)}).inserted_id
    assert a.pending_command() is None
    assert db.commands.find_one({"_id": old})["status"] == "expirado"
    novo = db.commands.insert_one({"worker_id": "w-a", "acao": "liberar", "status": "pending", "criado_em": 2,
                                   "expira_em": a._dt() + timedelta(minutes=5)}).inserted_id
    assert a.pending_command()["_id"] == novo


def test_run_pending_command_liberar_marca_executando_chama_e_marca_feito(env):
    _, db, a, _ = env
    cid = cmd(db)
    vistos = []

    def liberar():
        vistos.append(db.commands.find_one({"_id": cid})["status"])

    assert a.run_pending_command(liberar) == "liberar"
    assert vistos == ["executando"]
    assert db.commands.find_one({"_id": cid})["status"] == "feito"
    assert a.run_pending_command(liberar) is None
