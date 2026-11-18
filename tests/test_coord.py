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
