"""Integration tests for clipes.acompanhar with MongoDB (mongomock) — T8."""

from __future__ import annotations

import time
from types import SimpleNamespace

import mongomock
import pymongo.errors
import pytest

from clipes import acompanhar, monitor
from clipes.coord import Coord
from test_acompanhar import B, Clock, FakeMultiPool


def test_supervisor_coord_lease_ok_twitch_e_youtube_renova_e_libera(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-sup1", now=clock.now, lease_s=90)
    pool = FakeMultiPool(clock, {"gaules": B + 320, "yt_ytV1": B + 320})

    renews: list[str] = []
    orig_renew = coord.renew
    coord.renew = lambda lid: (renews.append(lid), orig_renew(lid))[1]

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules"), acompanhar.parse_alvo("https://www.youtube.com/@CanalYT/live")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=lambda u: clock.t < B + 320,
        probe=lambda u: (
            {"id": "ytV1", "channel_id": "UC1", "uploader_id": "@CanalYT", "is_live": True}
            if "youtube" in u
            else {"id": "twS1", "is_live": True}
        ),
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert "twitch:gaules:twS1" in renews
    assert "youtube:UC1:ytV1" in renews
    assert db.lives.find_one({"_id": "twitch:gaules:twS1"})["status"] == "ended"
    assert db.lives.find_one({"_id": "youtube:UC1:ytV1"})["status"] == "ended"
    assert db.jobs.find_one({"_id": f"twitch:gaules:twS1:{B + 300}"})["status"] == "rejected"
    assert db.jobs.find_one({"_id": f"youtube:UC1:ytV1:{B + 300}"})["status"] == "rejected"


def test_supervisor_coord_lease_negada_fica_aguardando_e_avisa(tmp_path, monkeypatch, capsys):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

    db = mongomock.MongoClient().db
    other = Coord(db, "w-outro", now=clock.now, lease_s=5000)
    coord = Coord(db, "w-eu", now=clock.now, lease_s=5000)
    other.ensure_live("twitch:c1:101", channel="c1", platform="twitch")
    assert other.claim_live("twitch:c1:101") is True

    avisos: list[str] = []
    pool = FakeMultiPool(clock, {"c1": B + 620, "c2": B + 320})

    # While c2 runs, other releases c1 at B+200 -> when c2 ends at B+320, c1 is promoted!
    orig_tick = acompanhar.LiveSession.tick

    def tick_and_release(self, t):
        if t >= B + 200 and db.lives.find_one({"_id": "twitch:c1:101"})["owner"] == "w-outro":
            other.release("twitch:c1:101", False)
        return orig_tick(self, t)

    monkeypatch.setattr(acompanhar.LiveSession, "tick", tick_and_release)

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("c1"), acompanhar.parse_alvo("c2")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=lambda u: clock.t < (B + 620 if "c1" in u else B + 320),
        probe=lambda u: {"id": "101" if "c1" in u else "202", "is_live": True},
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        aviso_fn=avisos.append,
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert avisos == ["[acompanhar] recusada c1: lease com outro PC"]
    assert pool.started_order == ["c2", "c1"]


def test_supervisor_mongo_cai_segue_local_e_reconecta_apos_60s(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-drop", now=clock.now, lease_s=500)
    pool = FakeMultiPool(clock, {"gaules": B + 620})

    orig_add_job = coord.add_job
    fail_until = [B + 310]

    def flaky_add_job(job_id, live_id, window, priority):
        if clock.t <= fail_until[0]:
            raise pymongo.errors.AutoReconnect("network blip")
        return orig_add_job(job_id, live_id, window, priority)

    coord.add_job = flaky_add_job

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="555",
        is_live=lambda u: clock.t < B + 620,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert res["jobs"] == [f"gaules_{B + 300}", f"gaules_{B + 600}"]
    # First job ran locally while Mongo was down and was replayed after reconnect (B-01); second synced directly
    j1 = db.jobs.find_one({"_id": f"twitch:gaules:555:{B + 300}"})
    assert j1 is not None and j1["status"] == "rejected"
    assert db.jobs.find_one({"_id": f"twitch:gaules:555:{B + 600}"}) is not None


def test_supervisor_ctrl_c_libera_lease_sem_marcar_ended(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-cc", now=clock.now, lease_s=300)

    class InterruptPool(FakeMultiPool):
        def segments(self, ch):
            if self.clock.t >= B + 30:
                raise KeyboardInterrupt()
            return super().segments(ch)

    pool = InterruptPool(clock, {"c1": B + 600, "c2": B + 600})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("c1"), acompanhar.parse_alvo("c2")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=lambda u: True,
        probe=lambda u: {"id": "1" if "c1" in u else "2", "is_live": True},
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    with pytest.raises(KeyboardInterrupt):
        sup.run()

    assert pool.active == set()
    for lid in ("twitch:c1:1", "twitch:c2:2"):
        doc = db.lives.find_one({"_id": lid})
        assert doc["status"] == "live"
        assert doc["owner"] is None
        assert doc["lease_until"] is None


def test_supervisor_keepalive_renova_beat_e_lease_durante_pipeline_lento(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_BEAT_S", "1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-keep", now=clock.now, lease_s=90)
    pool = FakeMultiPool(clock, {"gaules": B + 320})

    in_pipeline = [False]
    beat_during_pipe: list[bool] = []
    renew_during_pipe: list[str] = []
    orig_beat = coord.beat
    orig_renew = coord.renew

    def tracked_beat(*args, **kwargs):
        if in_pipeline[0]:
            beat_during_pipe.append(True)
        return orig_beat(*args, **kwargs)

    def tracked_renew(lid):
        if in_pipeline[0]:
            renew_during_pipe.append(lid)
        return orig_renew(lid)

    coord.beat = tracked_beat
    coord.renew = tracked_renew

    ran_once = [False]

    def slow_pipe(job_dir, out_root):
        if not ran_once[0]:
            ran_once[0] = True
            in_pipeline[0] = True
            try:
                time.sleep(1.25)
            finally:
                in_pipeline[0] = False
        return {"clips": []}

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=slow_pipe,
        coord=coord,
        stream_id="99",
        is_live=lambda u: clock.t < B + 320,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert beat_during_pipe
    assert "twitch:gaules:99" in renew_during_pipe


def test_supervisor_coord_kick_abertura_platform_e_stream_id_override(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-kick", now=clock.now, lease_s=90)
    ensure_calls: list[tuple[str, str | None, str | None]] = []
    orig_ensure = coord.ensure_live

    def tracked_ensure(live_id, channel=None, platform=None, stream_id=None, **kw):
        ensure_calls.append((live_id, platform, stream_id))
        return orig_ensure(live_id, channel=channel, platform=platform, stream_id=stream_id, **kw)

    coord.ensure_live = tracked_ensure

    pool = FakeMultiPool(clock, {"kick_foo-bar": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("https://www.kick.com/Foo-Bar/")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=lambda u: clock.t < B + 320,
        probe=lambda u: {"is_live": True, "id": 123, "channel_id": "9"},
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert ensure_calls == [("kick:foo-bar:123", "kick", "123")]
    doc = db.lives.find_one({"_id": "kick:foo-bar:123"})
    assert doc is not None and doc["platform"] == "kick" and doc["status"] == "ended"
    assert db.jobs.find_one({"_id": f"kick:foo-bar:123:{B + 300}"}) is not None

    # stream_id explicito prevalece sobre alvo.video_id
    ensure_calls.clear()
    clock2 = Clock(B + 10)
    pool2 = FakeMultiPool(clock2, {"kick_foo-bar": B + 320})
    sup2 = acompanhar.Supervisor(
        [acompanhar.parse_alvo("https://kick.com/foo-bar")],
        tmp_path / "out2",
        pool=pool2,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="override-99",
        is_live=lambda u: clock2.t < B + 320,
        probe=lambda u: {"is_live": True, "id": 123},
        now=clock2.now,
        sleep=clock2.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    assert sup2.run()["status"] == "ended"
    assert ensure_calls == [("kick:foo-bar:override-99", "kick", "override-99")]


def test_supervisor_coord_kick_sem_sid_recusa_como_aguardando(tmp_path, monkeypatch, capsys):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-kick-nosid", now=clock.now, lease_s=90)
    avisos: list[str] = []
    pool = FakeMultiPool(clock, {"kick_foo-bar": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("https://kick.com/foo-bar")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=lambda u: True,
        probe=lambda u: {"is_live": True},
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        aviso_fn=avisos.append,
    )
    res = sup.run()

    assert res["status"] == "not_claimed"
    assert res["reason"] == "stream_id indisponivel"
    assert pool.started_order == []
    assert avisos == ["[acompanhar] recusada https://kick.com/foo-bar: stream_id indisponivel"]
    assert "stream_id indisponivel" in capsys.readouterr().out


def test_supervisor_coord_kick_backoff_e_reconexao(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-kick-backoff", now=clock.now, lease_s=500)
    ensure_calls: list[tuple[str, str | None, str | None]] = []
    orig_ensure = coord.ensure_live

    def tracked_ensure(live_id, channel=None, platform=None, stream_id=None, **kw):
        ensure_calls.append((live_id, platform, stream_id))
        return orig_ensure(live_id, channel=channel, platform=platform, stream_id=stream_id, **kw)

    coord.ensure_live = tracked_ensure

    # Falha na inicializacao coloca Mongo em backoff antes de _try_start_alvo (cobre pending_live_id em backoff)
    orig_rel = coord.release_expired_own
    failed_once = [False]

    def fail_first_release():
        if not failed_once[0]:
            failed_once[0] = True
            raise pymongo.errors.AutoReconnect("mongo down at startup")
        return orig_rel()

    coord.release_expired_own = fail_first_release

    pool = FakeMultiPool(clock, {"kick_foo-bar": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("https://kick.com/foo-bar")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=lambda u: clock.t < B + 320,
        probe=lambda u: {"is_live": True, "id": "777"},
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert ensure_calls == [("kick:foo-bar:777", "kick", "777")]
    doc = db.lives.find_one({"_id": "kick:foo-bar:777"})
    assert doc is not None and doc["platform"] == "kick" and doc["status"] == "ended"

    # Tambem verifica _try_claim_pending quando pending_live_id esta None na reconexao
    ensure_calls.clear()
    alvo_reconn = acompanhar.parse_alvo("https://kick.com/baz-qux")
    alvo_reconn.video_id = "888"
    sess_stub = SimpleNamespace(
        alvo=alvo_reconn,
        has_coord=True,
        live_id=None,
        pending_live_id=None,
        pending_stream_id=None,
    )
    claimed = sup._try_claim_pending(coord, alvo_reconn.chave, sess_stub, pool, lambda _u: {})
    assert claimed is True
    assert sess_stub.live_id == "kick:baz-qux:888"
    assert ensure_calls == [("kick:baz-qux:888", "kick", "888")]
