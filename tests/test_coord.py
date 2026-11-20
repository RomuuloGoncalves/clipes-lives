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


# ---- controle-discord T2: colecao alvos e heartbeat estendido ----


def _alvo_doc(
    alvo_id: str,
    *,
    tipo: str = "live",
    plataforma: str = "twitch",
    entrada: str = "gaules",
    estado: str = "ativo",
    pc_fixo: str | None = None,
    owner: str | None = None,
    lease_until=None,
    fase: str = "sem vaga",
    criado_em: float = 1.0,
    url: str | None = None,
) -> dict:
    return {
        "_id": alvo_id,
        "tipo": tipo,
        "plataforma": plataforma,
        "entrada": entrada,
        "url": url,
        "estado": estado,
        "pc_fixo": pc_fixo,
        "owner": owner,
        "lease_until": lease_until,
        "fase": fase,
        "criado_em": criado_em,
        "erro": None,
    }


def test_ensure_indexes_cria_indice_alvos(env):
    _, db, a, _ = env
    a.ensure_indexes()
    assert "estado_1_tipo_1_plataforma_1" in db.alvos.index_information()


def test_claim_alvo_respeita_plataforma_estado_pc_fixo_e_exclude_ids(env):
    clock, db, a, b = env
    db.alvos.insert_many([
        _alvo_doc("gaules", plataforma="twitch", entrada="gaules"),
        _alvo_doc("alanzoka", plataforma="twitch", entrada="alanzoka", pc_fixo="PC-2"),
        _alvo_doc("yt_1", plataforma="youtube", entrada="https://youtu.be/1"),
        _alvo_doc("removido", plataforma="twitch", entrada="removido", estado="removido"),
        _alvo_doc("vod:11111111", tipo="vod", plataforma="twitch", entrada="", url="https://twitch.tv/videos/1"),
    ])

    doc_a = a.claim_alvo("twitch", "PC-1")
    assert doc_a is not None
    assert doc_a["_id"] == "gaules"
    assert doc_a["owner"] == "w-a"
    assert doc_a["lease_until"] == a._dt(90)
    assert "previous_owner" not in doc_a

    # PC-1 nao pode pegar alanzoka (pc_fixo=PC-2) nem re-pegar gaules quando excluido
    assert a.claim_alvo("twitch", "PC-1", exclude_ids={"gaules"}) is None

    # PC-2 pega alanzoka (pc_fixo=PC-2) e nao pega gaules (owner=w-a com lease valida)
    doc_b = b.claim_alvo("twitch", "PC-2")
    assert doc_b is not None and doc_b["_id"] == "alanzoka" and doc_b["owner"] == "w-b"

    # YouTube continua separado da Twitch
    doc_yt = a.claim_alvo("youtube", "PC-1")
    assert doc_yt is not None and doc_yt["_id"] == "yt_1"


def test_claim_alvo_failover_apos_lease_90s_e_own_alvos(env):
    clock, db, a, b = env
    db.alvos.insert_one(_alvo_doc("gaules", plataforma="twitch", entrada="gaules"))

    assert a.claim_alvo("twitch", "PC-1")["_id"] == "gaules"
    assert [d["_id"] for d in a.own_alvos()] == ["gaules"]
    assert b.claim_alvo("twitch", "PC-2") is None

    clock.t += 91
    assert a.own_alvos() == []  # lease vencida nao volta em own_alvos
    taken = b.claim_alvo("twitch", "PC-2")
    assert taken is not None
    assert taken["_id"] == "gaules"
    assert taken["owner"] == "w-b"
    assert taken["previous_owner"] == "w-a"
    assert db.alvos.find_one({"_id": "gaules"})["owner"] == "w-b"


def test_claim_alvo_corrida_atomica_entre_dois_coords(env):
    _, db, a, b = env
    ids = [f"canal_{i}" for i in range(20)]
    for i, aid in enumerate(ids):
        db.alvos.insert_one(_alvo_doc(aid, plataforma="twitch", entrada=aid, criado_em=float(i)))

    barrier = threading.Barrier(2)
    wins: dict[str, set[str]] = {"w-a": set(), "w-b": set()}
    errors: list[BaseException] = []

    def racer(coord: Coord, alias: str) -> None:
        try:
            for _ in range(len(ids)):
                barrier.wait(timeout=5)
                doc = coord.claim_alvo("twitch", alias, exclude_ids=wins[coord.worker_id])
                if doc is not None:
                    wins[coord.worker_id].add(doc["_id"])
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=racer, args=(a, "PC-1"))
    t2 = threading.Thread(target=racer, args=(b, "PC-2"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors
    assert wins["w-a"].isdisjoint(wins["w-b"])
    assert wins["w-a"] | wins["w-b"] == set(ids)
    for aid in ids:
        expected = "w-a" if aid in wins["w-a"] else "w-b"
        assert db.alvos.find_one({"_id": aid})["owner"] == expected


def test_renew_alvos_e_release_alvo(env):
    clock, db, a, b = env
    db.alvos.insert_many([
        _alvo_doc("c1", owner="w-a", lease_until=a._dt(90), fase="sem vaga"),
        _alvo_doc("c2", owner="w-a", lease_until=a._dt(90), estado="removido", fase="gravando"),
        _alvo_doc("c3", owner="w-b", lease_until=b._dt(90), fase="gravando"),
    ])

    clock.t += 30
    renewed = a.renew_alvos({"c1": "gravando", "c2": "gravando", "c3": "aguardando live"})
    assert renewed == {"c1"}
    d1 = db.alvos.find_one({"_id": "c1"})
    assert d1["fase"] == "gravando" and d1["lease_until"] == a._dt(90)
    # c2 (removido) e c3 (outro dono) nao sao renovados nem alterados
    assert db.alvos.find_one({"_id": "c2"})["lease_until"] != a._dt(90)
    assert db.alvos.find_one({"_id": "c3"})["fase"] == "gravando"

    # release_alvo limpa owner/lease e volta fase para "sem vaga" apenas se ativo
    a.release_alvo("c1")
    d1_rel = db.alvos.find_one({"_id": "c1"})
    assert (d1_rel["owner"], d1_rel["lease_until"], d1_rel["fase"]) == (None, None, "sem vaga")

    a.release_alvo("c2")
    d2_rel = db.alvos.find_one({"_id": "c2"})
    assert (d2_rel["owner"], d2_rel["lease_until"], d2_rel["fase"]) == (None, None, "gravando")

    # Nao-dono nao solta c3
    a.release_alvo("c3")
    assert db.alvos.find_one({"_id": "c3"})["owner"] == "w-b"


def test_alvos_ativos_retorna_chaves_de_lives_ativas_de_qualquer_pc(env):
    _, db, a, _ = env
    db.alvos.insert_many([
        _alvo_doc("gaules", tipo="live", estado="ativo", owner="w-a"),
        _alvo_doc("twitch:alanzoka", tipo="live", estado="ativo", owner="w-b"),
        _alvo_doc("yt_AbC123", tipo="live", plataforma="youtube", estado="ativo", owner=None),
        _alvo_doc("parado", tipo="live", estado="removido", owner=None),
        _alvo_doc("vod:12345678", tipo="vod", estado="ativo", url="https://twitch.tv/videos/1"),
    ])
    ativos = a.alvos_ativos()
    assert {"gaules", "twitch:alanzoka", "alanzoka", "yt_AbC123"} <= ativos
    assert "parado" not in ativos
    assert "vod:12345678" not in ativos


def test_pegar_vod_mais_antigo_e_fim_vod_sanitizado(env, monkeypatch):
    clock, db, a, b = env
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok_super_secreto_12345")
    # VOD sem campos opcionais pc_fixo/owner (como na spec CTRL-02 AC1)
    db.alvos.insert_many([
        {"_id": "vod:novo0002", "tipo": "vod", "url": "https://twitch.tv/videos/2", "estado": "ativo", "criado_em": 20},
        {"_id": "vod:velh0001", "tipo": "vod", "url": "https://twitch.tv/videos/1", "estado": "ativo", "criado_em": 10},
        _alvo_doc("gaules", tipo="live", estado="ativo", criado_em=1),
    ])

    v1 = a.pegar_vod("PC-1")
    assert v1 is not None and v1["_id"] == "vod:velh0001" and v1["owner"] == "w-a"
    v2 = b.pegar_vod("PC-2")
    assert v2 is not None and v2["_id"] == "vod:novo0002" and v2["owner"] == "w-b"
    c = Coord(db, "w-c", now=clock)
    assert c.pegar_vod("PC-3") is None

    # Nao-dono nao finaliza VOD de outro PC
    assert b.fim_vod("vod:velh0001", "feito") is False
    assert a.fim_vod("vod:velh0001", "feito") is True
    assert a.pegar_vod("PC-1") is None  # nao ha mais VOD livre
    d1 = db.alvos.find_one({"_id": "vod:velh0001"})
    assert (d1["estado"], d1["erro"], d1["owner"]) == ("feito", None, None)

    assert b.fim_vod(
        "vod:novo0002",
        "falhou",
        "erro em mongodb+srv://user:senha@cluster.local/db com tok_super_secreto_12345 e https://u:p@host/x",
    ) is True
    d2 = db.alvos.find_one({"_id": "vod:novo0002"})
    assert d2["estado"] == "falhou"
    assert "senha" not in d2["erro"]
    assert "tok_super_secreto_12345" not in d2["erro"]
    assert "u:p@" not in d2["erro"]
    assert "mongodb+srv://***@***" in d2["erro"]

    with pytest.raises(ValueError):
        a.fim_vod("vod:velh0001", "ativo")


def test_beat_estendido_grava_slots_ocupados_e_fila(env):
    _, db, a, _ = env
    slots = {"twitch": 1, "kick": 1, "youtube": 1, "busca": 1}
    ocupados = {"twitch": 1, "kick": 0, "youtube": 1, "busca": 0}
    fila = {"janelas": 2, "busca": 1, "vods": 0}
    a.beat("PC-1", CAP, "job-1", 3, 0.4, beat_s=20, slots=slots, ocupados=ocupados, fila=fila)
    doc = db.workers.find_one({"_id": "w-a"})
    assert doc["slots"] == slots
    assert doc["ocupados"] == ocupados
    assert doc["fila"] == fila
    assert doc["beat_s"] == 20.0


def test_reconcile_alias_migra_config_e_remove_duplicata(env):
    from datetime import datetime

    _, db, a, _ = env
    # Simula documento antigo e pre-cadastro do painel para o mesmo alias PC-1
    db.workers.insert_one({
        "_id": "w-antigo",
        "alias": "PC-1",
        "config": {"CLIPES_LAYOUT": "full"},
        "config_updated_at": datetime(2026, 1, 1, 10, 0),
        "heartbeat_at": datetime(2026, 1, 1, 10, 0),
    })
    db.workers.insert_one({
        "_id": "PC-1",
        "alias": "PC-1",
        "config": {"CLIPES_LAYOUT": "split-barra", "CLIPES_WHISPER_MODEL": "large-v3-turbo"},
        "config_updated_at": datetime(2026, 1, 2, 10, 0),
        "heartbeat_at": None,
    })

    cfg = a.reconcile_alias("PC-1")
    assert cfg == {"CLIPES_WHISPER_MODEL": "large-v3-turbo"}  # CLIPES_LAYOUT por PC e ignorado (global)
    # Documentos antigos/duplicados removidos; apenas w-a permanece
    docs = list(db.workers.find({"alias": "PC-1"}))
    assert len(docs) == 1
    assert docs[0]["_id"] == "w-a"
    assert docs[0]["config"] == {**cfg, "CLIPES_LAYOUT": "split-barra"}  # doc intacto; filtro so na leitura


def test_beat_retorna_config_salvo_em_unica_operacao(env):
    _, db, a, _ = env
    db.workers.insert_one({"_id": "w-a", "alias": "PC-1", "config": {"CLIPES_SLOTS": "twitch=2,busca=0"}})
    cfg = a.beat("PC-1", CAP, None, 3, 0.4, beat_s=20)
    assert cfg == {"CLIPES_SLOTS": "twitch=2,busca=0"}
    assert db.workers.find_one({"_id": "w-a"})["heartbeat_at"] is not None


# ---- Onda 10 T1: pegar_vod marca fase=processando, fim_vod atualiza fase e nao sobrescreve removido, promover_para_vod ----

def test_pegar_vod_marca_fase_processando_e_pc_fixo(env):
    clock, db, a, b = env
    # Limpa alvos existentes do env
    db.alvos.delete_many({})
    db.alvos.insert_many([
        {"_id": "vod:1", "tipo": "vod", "url": "https://twitch.tv/videos/1", "estado": "ativo", "criado_em": 10, "pc_fixo": "PC-1"},
        {"_id": "vod:2", "tipo": "vod", "url": "https://twitch.tv/videos/2", "estado": "ativo", "criado_em": 20, "pc_fixo": "PC-2"},
        {"_id": "vod:3", "tipo": "vod", "url": "https://twitch.tv/videos/3", "estado": "ativo", "criado_em": 30},  # sem pc_fixo
    ])

    v1 = a.pegar_vod("PC-1")
    assert v1 is not None and v1["_id"] == "vod:1"
    assert v1["fase"] == "processando"
    assert v1["owner"] == "w-a"

    # PC-2 nao pode pegar vod:1 (pc_fixo=PC-1) - deve pular para vod:2
    v2 = b.pegar_vod("PC-2")
    assert v2 is not None and v2["_id"] == "vod:2" and v2["fase"] == "processando"
    assert v2["owner"] == "w-b"

    # PC-3 pega vod:3 (sem pc_fixo)
    c = Coord(db, "w-c", now=clock)
    v3 = c.pegar_vod("PC-3")
    assert v3 is not None and v3["_id"] == "vod:3" and v3["fase"] == "processando"

    # PC-1 tentando de novo imediatamente: vod:1 ainda tem owner=w-a (lease valida) -> re-reivindica
    v1_again = a.pegar_vod("PC-1")
    assert v1_again is not None and v1_again["_id"] == "vod:1"

    # PC-2 tentando pegar vod:1 (pc_fixo=PC-1) deve falhar e pegar vod:2 (seu proprio, lease valida)
    v2_again = b.pegar_vod("PC-2")
    assert v2_again is not None and v2_again["_id"] == "vod:2"

    # Teste que pc_fixo e respeitado: cria VOD sem pc_fixo e PC-1 pega (mas vod:1 vem antes no sort)
    # Entao testa com VOD de criado_em maior que so PC-1 pode pegar
    db.alvos.insert_one({"_id": "vod:livre", "tipo": "vod", "url": "https://twitch.tv/videos/99", "estado": "ativo", "criado_em": 5, "pc_fixo": "PC-1"})
    v_livre = a.pegar_vod("PC-1")
    assert v_livre is not None and v_livre["_id"] == "vod:livre"


def test_fim_vod_atualiza_fase_e_nao_sobrescreve_removido(env, monkeypatch):
    clock, db, a, _ = env
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok_super_secreto_12345")
    db.alvos.insert_one({"_id": "vod:ok", "tipo": "vod", "url": "https://twitch.tv/videos/1", "estado": "ativo", "criado_em": 10})
    db.alvos.insert_one({"_id": "vod:removed", "tipo": "vod", "url": "https://twitch.tv/videos/2", "estado": "ativo", "criado_em": 20})

    # Pega e finaliza com sucesso
    a.pegar_vod("PC-1")
    assert a.fim_vod("vod:ok", "feito") is True
    d1 = db.alvos.find_one({"_id": "vod:ok"})
    assert (d1["estado"], d1["fase"], d1["owner"], d1["lease_until"]) == ("feito", "concluido", None, None)
    assert d1["finalizado_em"] is not None

    # Remove manualmente enquanto outro PC processa
    b = Coord(db, "w-b", now=clock)
    b.pegar_vod("PC-2")
    # Operador remove no painel -> estado=removido, fase=cancelado
    db.alvos.update_one({"_id": "vod:removed"}, {"$set": {"estado": "removido", "fase": "cancelado", "owner": None, "lease_until": None}})
    # fim_vod do worker original NAO deve sobrescrever removido
    assert b.fim_vod("vod:removed", "feito") is False
    d2 = db.alvos.find_one({"_id": "vod:removed"})
    assert (d2["estado"], d2["fase"]) == ("removido", "cancelado")

    # Erro sanitizado
    assert a.fim_vod(
        "vod:ok",  # ja finalizado, mas testa sanitizacao
        "falhou",
        "erro em mongodb+srv://user:senha@cluster.local/db com tok_super_secreto_12345",
    ) is False  # matched_count = 0 pois estado != "ativo"


def test_promover_para_vod_in_place_e_duplicado(env):
    clock, db, a, _ = env
    # Cria alvo live do YouTube com auto_vod=True
    db.alvos.insert_one({
        "_id": "yt_AbC123", "tipo": "live", "plataforma": "youtube",
        "entrada": "https://www.youtube.com/watch?v=AbC123",
        "url": "https://www.youtube.com/watch?v=AbC123",
        "estado": "ativo", "owner": "w-a", "lease_until": a._dt(90),
        "fase": "aguardando live", "pc_fixo": "PC-1",
        "criado_por": "painel", "criado_em": 100.0, "auto_vod": True,
    })

    # Promove in-place
    res = a.promover_para_vod("yt_AbC123", "https://www.youtube.com/watch?v=AbC123")
    assert res == "promovido"
    doc = db.alvos.find_one({"_id": "yt_AbC123"})
    assert doc["tipo"] == "vod"
    assert doc["fase"] == "na fila"
    assert doc["owner"] is None
    assert doc["lease_until"] is None
    assert doc["auto_vod"] is False
    # Preservados
    assert doc["pc_fixo"] == "PC-1"
    assert doc["criado_por"] == "painel"
    assert doc["criado_em"] == 100.0
    assert doc["url"] == "https://www.youtube.com/watch?v=AbC123"
    assert doc["entrada"] == "https://www.youtube.com/watch?v=AbC123"

    # Testa duplicado: cria outro VOD ativo com a mesma URL
    db.alvos.insert_one({
        "_id": "vod:dup1111", "tipo": "vod", "url": "https://www.youtube.com/watch?v=AbC123",
        "estado": "ativo", "criado_em": 200.0,
    })
    # Cria outro alvo live para testar duplicado
    db.alvos.insert_one({
        "_id": "yt_XyZ789", "tipo": "live", "plataforma": "youtube",
        "entrada": "https://www.youtube.com/watch?v=XyZ789",
        "url": "https://www.youtube.com/watch?v=XyZ789",
        "estado": "ativo", "owner": "w-a", "lease_until": a._dt(90),
        "fase": "aguardando live", "pc_fixo": "PC-1",
        "criado_por": "painel", "criado_em": 300.0, "auto_vod": True,
    })
    res2 = a.promover_para_vod("yt_XyZ789", "https://www.youtube.com/watch?v=AbC123")
    assert res2 == "duplicado"
    doc2 = db.alvos.find_one({"_id": "yt_XyZ789"})
    assert (doc2["estado"], doc2["fase"]) == ("removido", "cancelado")

    # Ignorado: alvo nao pertence a este worker (outro worker) ou nao esta ativo
    # Cria alvo live de outro worker
    db.alvos.insert_one({
        "_id": "yt_Outro999", "tipo": "live", "plataforma": "youtube",
        "entrada": "https://www.youtube.com/watch?v=Outro999",
        "url": "https://www.youtube.com/watch?v=Outro999",
        "estado": "ativo", "owner": "w-b", "lease_until": a._dt(90),
        "fase": "aguardando live", "pc_fixo": "PC-2",
        "criado_por": "painel", "criado_em": 400.0, "auto_vod": True,
    })
    res3 = a.promover_para_vod("yt_Outro999", "https://www.youtube.com/watch?v=Outro999")
    assert res3 == "ignorado"
    doc3 = db.alvos.find_one({"_id": "yt_Outro999"})
    assert doc3["tipo"] == "live"  # nao alterado


def test_layout_global_vence_config_por_pc(env, capsys):
    _, db, a, _ = env
    db.workers.update_one({"_id": "w-a"}, {"$set": {"config": {"CLIPES_LAYOUT": "full", "CLIPES_BAR_H": "400"}}}, upsert=True)
    beat = lambda: a.beat("PC-1", {}, None, 1, 1.0)
    # sem global: chave por PC ignorada (env/padrao valem), aviso unico
    assert beat() == {"CLIPES_BAR_H": "400"}
    beat()
    assert capsys.readouterr().out.count("CLIPES_LAYOUT por PC ignorado") == 1
    db.config_global.insert_one({"_id": "global", "config": {"CLIPES_LAYOUT": "split-cheio"}})
    assert beat() == {"CLIPES_BAR_H": "400", "CLIPES_LAYOUT": "split-cheio"}
    assert a.reconcile_alias("PC-1")["CLIPES_LAYOUT"] == "split-cheio"


def test_global_vence_pc_em_chave_do_schema(env):
    _, db, a, _ = env
    db.workers.update_one({"_id": "w-a"}, {"$set": {"config": {"CLIPES_DUR_MAX": "90"}}}, upsert=True)
    db.config_global.insert_one({"_id": "global", "config": {"CLIPES_DUR_MAX": "30"}})
    assert a.reconcile_alias("PC-1") == {"CLIPES_DUR_MAX": "30"}


def test_chave_global_por_pc_e_ignorada_com_um_aviso_por_chave(env, capsys):
    _, db, a, _ = env
    db.workers.update_one(
        {"_id": "w-a"}, {"$set": {"config": {"CLIPES_DUR_MAX": "90", "CLIPES_DUR_MIN": "20"}}}, upsert=True
    )
    assert a.reconcile_alias("PC-1") == {}
    a.reconcile_alias("PC-1")
    saida = capsys.readouterr().out
    assert saida.count("CLIPES_DUR_MAX por PC ignorado") == 1
    assert saida.count("CLIPES_DUR_MIN por PC ignorado") == 1


def test_chave_nao_global_por_pc_continua_valendo(env):
    _, db, a, _ = env
    db.workers.update_one(
        {"_id": "w-a"}, {"$set": {"config": {"CLIPES_DUR_MAX": "90", "CLIPES_BAR_H": "400"}}}, upsert=True
    )
    db.config_global.insert_one({"_id": "global", "config": {"CLIPES_DUR_MAX": "30"}})
    assert a.reconcile_alias("PC-1") == {"CLIPES_DUR_MAX": "30", "CLIPES_BAR_H": "400"}
