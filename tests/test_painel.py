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

from painel.api import _nucleo

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


