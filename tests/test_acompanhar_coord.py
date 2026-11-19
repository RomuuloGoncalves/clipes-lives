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
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=2")

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


def test_supervisor_beat_envia_slots_ocupados_e_fila_no_loop_e_keepalive(tmp_path, monkeypatch):
    import json

    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=2,kick=0,youtube=1,busca=1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-slots-beat", now=clock.now, lease_s=90)
    db.alvos.insert_one({
        "_id": "vod:abc12345",
        "tipo": "vod",
        "url": "https://twitch.tv/videos/1",
        "estado": "ativo",
        "criado_em": 1,
    })

    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    queue = monitor.DiskQueue(queue_root)
    # Semeia 1 job da busca pendente e 1 janela pendente
    for jid, extra in (
        ("busca_1", {"channel": "outro", "origem": "busca", "z_score": 5.0}),
        ("jan_1", {"channel": "outro", "session_key": "outro", "window_start": 0, "window_end": 600, "video_start": 0, "last": False}),
    ):
        d = queue.root / ".staging" / jid
        d.mkdir(parents=True, exist_ok=True)
        (d / "source.mkv").write_bytes(b"v")
        (d / "meta.json").write_text(json.dumps({"id": jid, "duration": 60.0}), encoding="utf-8")
        monitor._write_job(d, {"job_id": jid, "status": "pending", **extra})
        queue.enqueue(d)

    seen_beats: list[dict] = []
    orig_beat = coord.beat

    def tracked_beat(*args, **kwargs):
        seen_beats.append(dict(kwargs))
        return orig_beat(*args, **kwargs)

    coord.beat = tracked_beat

    ran_slow = [False]

    def slow_pipe(job_dir, root):
        if not ran_slow[0]:
            ran_slow[0] = True
            time.sleep(0.12)
        return {"clips": []}

    pool = FakeMultiPool(clock, {"gaules": B + 320, "yt_ytV9": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules"), acompanhar.parse_alvo("https://www.youtube.com/watch?v=ytV9")],
        out_root,
        queue_root=queue_root,
        pool=pool,
        pipeline=slow_pipe,
        coord=coord,
        is_live=lambda u: clock.t < B + 320,
        probe=lambda u: {"id": "ytV9", "channel_id": "UC9", "is_live": True} if "youtube" in u else {"id": "tw9", "is_live": True},
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=0.04,
    )
    res = sup.run()
    assert res["status"] == "ended"
    assert len(seen_beats) >= 2
    expected_slots = {"twitch": 2, "kick": 0, "youtube": 1, "busca": 1}
    assert any(
        b.get("slots") == expected_slots
        and b.get("ocupados") == {"twitch": 1, "kick": 0, "youtube": 1, "busca": 0}
        and b.get("fila", {}).get("busca") == 1
        and b.get("fila", {}).get("vods") == 1
        and b.get("fila", {}).get("janelas", 0) >= 1
        for b in seen_beats
    )
    worker_doc = db.workers.find_one({"_id": "w-slots-beat"})
    assert worker_doc["slots"] == expected_slots
    assert set(worker_doc["ocupados"]) == {"twitch", "kick", "youtube", "busca"}
    assert set(worker_doc["fila"]) == {"janelas", "busca", "vods"}


# ---- controle-discord T4: ciclo de alvos no Mongo (claim, renew, remover, Q4, failover) ----------


def _seed_alvo(
    db,
    alvo_id: str,
    *,
    plataforma: str = "twitch",
    entrada: str | None = None,
    estado: str = "ativo",
    pc_fixo: str | None = None,
    owner: str | None = None,
    lease_until=None,
    fase: str = "sem vaga",
) -> None:
    doc = {
        "_id": alvo_id,
        "tipo": "live",
        "plataforma": plataforma,
        "estado": estado,
        "pc_fixo": pc_fixo,
        "owner": owner,
        "lease_until": lease_until,
        "fase": fase,
        "criado_em": 1.0,
        "erro": None,
    }
    if entrada is not None:
        doc["entrada"] = entrada
    db.alvos.insert_one(doc)


def test_t4_modo_servico_sem_argv_reage_em_1_beat_e_dois_pcs_com_pc_fixo(tmp_path, monkeypatch):
    import threading

    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=1,kick=0,youtube=0,busca=0")
    monkeypatch.setenv("CLIPES_ALIAS", "PC-1")

    db = mongomock.MongoClient().db
    c1 = Coord(db, "w-pc1", now=clock.now, lease_s=90)
    c2 = Coord(db, "w-pc2", now=clock.now, lease_s=90)

    stop1 = threading.Event()
    inserted = [False]
    seen_pc1_owner = [None]
    seen_pc2_owner = [None]

    def sleep1(d: float) -> None:
        clock.sleep(d)
        # Sem alvos no inicio -> Supervisor nao sai; apos 25s inserimos 3 alvos
        if not inserted[0] and clock.t >= B + 35:
            inserted[0] = True
            _seed_alvo(db, "gaules", entrada="gaules")
            _seed_alvo(db, "alanzoka", entrada="alanzoka", pc_fixo="PC-2")
            _seed_alvo(db, "nobru", entrada="nobru")
        # Quando PC-1 ja pegou gaules, PC-2 faz seu claim (alanzoka por pc_fixo)
        if inserted[0] and seen_pc1_owner[0] is None and db.alvos.find_one({"_id": "gaules"})["owner"] == "w-pc1":
            seen_pc1_owner[0] = "w-pc1"
            doc2 = c2.claim_alvo("twitch", "PC-2")
            if doc2:
                seen_pc2_owner[0] = doc2["_id"]
        if clock.t >= B + 80:
            stop1.set()

    pool1 = FakeMultiPool(clock, {"gaules": B + 900, "alanzoka": B + 900, "nobru": B + 900})
    sup1 = acompanhar.Supervisor(
        [],
        tmp_path / "out1",
        pool=pool1,
        pipeline=lambda d, o: {"clips": []},
        coord=c1,
        is_live=lambda u: True,
        probe=lambda u: {"id": "s1", "is_live": True},
        now=clock.now,
        sleep=sleep1,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=20,
        stop_event=stop1,
    )
    res = sup1.run()

    assert res["status"] == "ended"
    assert pool1.started_order == ["gaules"]
    assert seen_pc1_owner[0] == "w-pc1"
    assert seen_pc2_owner[0] == "alanzoka"  # PC-2 pegou o alvo com pc_fixo=PC-2
    # nobru ficou sem vaga (ambos PCs com twitch=1 cheios)
    assert db.alvos.find_one({"_id": "nobru"})["owner"] is None
    assert db.alvos.find_one({"_id": "nobru"})["fase"] == "sem vaga"


def test_t4_retomada_pos_reinicio_e_failover_90s_publica_aviso(tmp_path, monkeypatch):
    import threading

    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=1,kick=0,youtube=0,busca=0")
    monkeypatch.setenv("CLIPES_ALIAS", "PC-1")

    db = mongomock.MongoClient().db
    c1 = Coord(db, "w-pc1", now=clock.now, lease_s=90)
    # 1) Retomada pos-reinicio: c_proprio ainda tem lease valida de w-pc1; c_novo esta livre
    _seed_alvo(db, "c_proprio", entrada="c_proprio", owner="w-pc1", lease_until=c1._dt(+60), fase="gravando")
    _seed_alvo(db, "c_novo", entrada="c_novo", owner=None, lease_until=None)

    stop1 = threading.Event()
    checked_resume = [False]

    def sleep1(d: float) -> None:
        clock.sleep(d)
        if clock.t >= B + 45:
            checked_resume[0] = (
                db.alvos.find_one({"_id": "c_proprio"})["owner"] == "w-pc1"
                and db.alvos.find_one({"_id": "c_novo"})["owner"] is None
            )
            stop1.set()

    pool1 = FakeMultiPool(clock, {"c_proprio": B + 900, "c_novo": B + 900})
    sup1 = acompanhar.Supervisor(
        [],
        tmp_path / "out_r",
        pool=pool1,
        pipeline=lambda d, o: {"clips": []},
        coord=c1,
        is_live=lambda u: True,
        probe=lambda u: {"id": "s_prop", "is_live": True},
        now=clock.now,
        sleep=sleep1,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=20,
        stop_event=stop1,
    )
    sup1.run()
    assert checked_resume[0] is True
    assert pool1.started_order == ["c_proprio"]

    # 2) Failover apos 90s: alvo de w-morto com lease vencida e tomado por w-pc2 com aviso
    clock2 = Clock(B + 10)
    db2 = mongomock.MongoClient().db
    c2 = Coord(db2, "w-pc2", now=clock2.now, lease_s=90)
    _seed_alvo(db2, "c_morto", entrada="c_morto", owner="w-morto", lease_until=c2._dt(-10), fase="gravando")

    monkeypatch.setenv("CLIPES_ALIAS", "PC-2")
    avisos: list[str] = []
    stop2 = threading.Event()

    def sleep2(d: float) -> None:
        clock2.sleep(d)
        if clock2.t >= B + 45:
            stop2.set()

    pool2 = FakeMultiPool(clock2, {"c_morto": B + 900})
    sup2 = acompanhar.Supervisor(
        [],
        tmp_path / "out_f",
        pool=pool2,
        pipeline=lambda d, o: {"clips": []},
        coord=c2,
        is_live=lambda u: True,
        probe=lambda u: {"id": "s_morto", "is_live": True},
        now=clock2.now,
        sleep=sleep2,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        aviso_fn=avisos.append,
        beat_s=20,
        stop_event=stop2,
    )
    sup2.run()
    assert pool2.started_order == ["c_morto"]
    assert any("c_morto" in a and "w-morto" in a for a in avisos)


def test_t4_remocao_fecha_janela_drena_jobs_e_libera_em_1_beat(tmp_path, monkeypatch):
    import threading

    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=1,kick=0,youtube=0,busca=0")
    monkeypatch.setenv("CLIPES_ALIAS", "PC-1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-rem", now=clock.now, lease_s=90)
    _seed_alvo(db, "gaules", entrada="gaules")

    removed_at = [None]
    stopped_at = [None]
    processed: list[str] = []
    stop = threading.Event()

    def fake_pipe(job_dir, root):
        processed.append(job_dir.name)
        return {"clips": []}

    pool = FakeMultiPool(clock, {"gaules": B + 900})

    def sleep_fn(d: float) -> None:
        clock.sleep(d)
        if removed_at[0] is None and clock.t >= B + 350:
            removed_at[0] = clock.t
            db.alvos.update_one({"_id": "gaules"}, {"$set": {"estado": "removido"}})
        if removed_at[0] is not None and stopped_at[0] is None and "gaules" not in pool.active:
            stopped_at[0] = clock.t
        if removed_at[0] is not None and clock.t >= B + 390:
            stop.set()

    sup = acompanhar.Supervisor(
        [],
        tmp_path / "out",
        pool=pool,
        pipeline=fake_pipe,
        coord=coord,
        stream_id="s_rem",
        is_live=lambda u: True,
        now=clock.now,
        sleep=sleep_fn,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=20,
        stop_event=stop,
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert stopped_at[0] is not None
    assert stopped_at[0] - removed_at[0] <= 30.0
    # Janela cheia (B+300) + janela parcial encerrada na remocao foram drenadas
    assert len(processed) >= 2
    assert f"gaules_{B + 300}" in processed
    alvo_doc = db.alvos.find_one({"_id": "gaules"})
    assert alvo_doc["estado"] == "removido"
    assert alvo_doc["owner"] is None
    live_doc = db.lives.find_one({"_id": "twitch:gaules:s_rem"})
    assert live_doc["status"] == "live" and live_doc["owner"] is None


def test_t4_q4_alvo_continua_ativo_apos_live_e_pega_proxima_em_120s(tmp_path, monkeypatch):
    import threading

    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=1,kick=0,youtube=0,busca=0")
    monkeypatch.setenv("CLIPES_ALIAS", "PC-1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-q4", now=clock.now, lease_s=90)
    _seed_alvo(db, "gaules", entrada="gaules")

    # Live 1: B+10..B+320 (stream s1); offline: B+320..B+450; Live 2: B+450..B+800 (stream s2)
    def is_live_q4(u: str) -> bool:
        return clock.t < B + 320 or clock.t >= B + 450

    def probe_q4(u: str) -> dict:
        return {"id": "s1" if clock.t < B + 400 else "s2", "is_live": is_live_q4(u)}

    class Q4Pool(FakeMultiPool):
        def is_recording(self, ch: str) -> bool:
            if ch not in self.active:
                return False
            return is_live_q4(ch)

        def segments(self, ch: str):
            if ch not in self.first:
                return []
            end = min(self.clock.t, B + 320 if self.first[ch] < B + 400 else B + 800) - 10
            return [(e, None) for e in range(self.first[ch], int(end) + 1, 10)]

    pool = Q4Pool(clock, {})
    ended_at = [None]
    restarted_at = [None]
    fase_entre_lives = [None]
    owner_entre_lives = [None]
    stop = threading.Event()

    def sleep_fn(d: float) -> None:
        clock.sleep(d)
        s1 = db.lives.find_one({"_id": "twitch:gaules:s1"})
        if ended_at[0] is None and s1 is not None and s1["status"] == "ended":
            ended_at[0] = clock.t
            doc = db.alvos.find_one({"_id": "gaules"})
            fase_entre_lives[0] = doc["fase"]
            owner_entre_lives[0] = doc["owner"]
        if ended_at[0] is not None and restarted_at[0] is None and len(pool.started_order) >= 2:
            restarted_at[0] = clock.t
        if clock.t >= B + 620:
            stop.set()

    sup = acompanhar.Supervisor(
        [],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=is_live_q4,
        probe=probe_q4,
        now=clock.now,
        sleep=sleep_fn,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=20,
        stop_event=stop,
    )
    sup.run()

    # Live 1 encerrada, alvo permaneceu ativo com o mesmo dono em "aguardando live"
    assert db.lives.find_one({"_id": "twitch:gaules:s1"})["status"] == "ended"
    assert ended_at[0] is not None
    assert owner_entre_lives[0] == "w-q4"
    assert fase_entre_lives[0] == "aguardando live"
    # Live 2 foi pega automaticamente somente apos reconsulta de 120 s (Q4)
    assert pool.started_order == ["gaules", "gaules"]
    assert restarted_at[0] is not None and restarted_at[0] - ended_at[0] >= 120.0
    assert db.lives.find_one({"_id": "twitch:gaules:s2"}) is not None


def test_t4_dup01_ac3_aguarda_lease_da_busca_liberar_e_inicia(tmp_path, monkeypatch):
    import threading

    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=1,kick=0,youtube=0,busca=0")
    monkeypatch.setenv("CLIPES_ALIAS", "PC-1")

    db = mongomock.MongoClient().db
    busca = Coord(db, "w-busca", now=clock.now, lease_s=90)
    coord = Coord(db, "w-sup", now=clock.now, lease_s=90)
    busca.ensure_live("twitch:gaules:s_dup", channel="gaules", platform="twitch")
    assert busca.claim_live("twitch:gaules:s_dup") is True
    _seed_alvo(db, "gaules", entrada="gaules")

    fase_ocupada = [None]
    liberada = [False]
    started_at = [None]
    stop = threading.Event()
    pool = FakeMultiPool(clock, {"gaules": B + 900})

    def sleep_fn(d: float) -> None:
        clock.sleep(d)
        if not liberada[0] and clock.t >= B + 20:
            doc = db.alvos.find_one({"_id": "gaules"})
            if doc["owner"] == "w-sup":
                fase_ocupada[0] = doc["fase"]
            assert "gaules" not in pool.active
            busca.release("twitch:gaules:s_dup", False)
            liberada[0] = True
        if liberada[0] and started_at[0] is None and "gaules" in pool.active:
            started_at[0] = clock.t
        if clock.t >= B + 60:
            stop.set()

    sup = acompanhar.Supervisor(
        [],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="s_dup",
        is_live=lambda u: True,
        now=clock.now,
        sleep=sleep_fn,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=20,
        stop_event=stop,
    )
    sup.run()

    assert fase_ocupada[0] == "aguardando live"
    assert started_at[0] is not None
    assert started_at[0] - (B + 20) <= 30.0
    assert pool.started_order == ["gaules"]


def test_t4_doc_invalido_ignorado_com_log_e_queda_mongo_mantem_sessoes(tmp_path, monkeypatch, capsys):
    import threading

    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=2,kick=0,youtube=0,busca=0")
    monkeypatch.setenv("CLIPES_ALIAS", "PC-1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-edge", now=clock.now, lease_s=90)
    _seed_alvo(db, "ruim_1", entrada="canal invalido!")
    _seed_alvo(db, "gaules", entrada="gaules")

    orig_claim = coord.claim_alvo
    mongo_down = [False]

    def flaky_claim(plat, alias=None, *, exclude_ids=()):
        if mongo_down[0]:
            raise pymongo.errors.AutoReconnect("mongo fora")
        return orig_claim(plat, alias, exclude_ids=exclude_ids)

    coord.claim_alvo = flaky_claim

    stop = threading.Event()
    added_second = [False]
    pool = FakeMultiPool(clock, {"gaules": B + 900, "alanzoka": B + 900})

    def sleep_fn(d: float) -> None:
        clock.sleep(d)
        if not added_second[0] and "gaules" in pool.active and clock.t >= B + 35:
            added_second[0] = True
            _seed_alvo(db, "alanzoka", entrada="alanzoka")
            mongo_down[0] = True
        if clock.t >= B + 320:
            stop.set()

    sup = acompanhar.Supervisor(
        [],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=lambda u: True,
        probe=lambda u: {"id": "s_ok", "is_live": True},
        now=clock.now,
        sleep=sleep_fn,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=20,
        stop_event=stop,
    )
    res = sup.run()
    out = capsys.readouterr().out

    assert "alvo invalido no Mongo" in out
    assert pool.started_order == ["gaules"]
    # Sessao ativa continuou gravando e gerando janela mesmo com Mongo caido
    assert f"gaules_{B + 300}" in res["jobs"]
    # Nenhum novo claim durante a queda do Mongo
    assert db.alvos.find_one({"_id": "alanzoka"})["owner"] is None


# ---- controle-discord T6: exclusao local busca x acompanhamento e liberar parando busca ----------


def test_t6_exclusao_local_mesmo_pc_impede_gravacao_simultanea_e_liberar_para_busca(tmp_path, monkeypatch):
    import threading
    from test_acompanhar import FakeProc

    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=1,kick=0,youtube=0,busca=1")
    monkeypatch.setenv("CLIPES_ALIAS", "PC-1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-local", now=clock.now, lease_s=90)
    _seed_alvo(db, "gaules", entrada="gaules")

    irc_started = threading.Event()
    irc_stopped = threading.Event()

    async def fake_irc(chans, on_msg, stop):
        irc_started.set()
        await stop.wait()
        irc_stopped.set()

    pool = FakeMultiPool(clock, {"gaules": B + 900})
    sup = acompanhar.Supervisor(
        [],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="s_loc",
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=20,
        busca_channels=["gaules"],
        busca_irc=fake_irc,
        busca_popen=lambda *a, **k: FakeProc(*a, **k),
    )

    # 1) Exclusao em memoria no mesmo PC: mesmo com claim_live aceitando self,
    #    _try_start_alvo recusa enquanto busca local segura gaules
    coord.ensure_live("twitch:gaules:s_loc", channel="gaules", platform="twitch")
    assert coord.claim_live("twitch:gaules:s_loc") is True
    sup._busca_owned_lives["gaules"] = "twitch:gaules:s_loc"
    q_local = monitor.DiskQueue(tmp_path / "out" / "queue")
    status_busy = sup._try_start_alvo(
        acompanhar.parse_alvo("gaules"),
        1,
        q_local,
        pool,
        lambda d, o: {"clips": []},
        lambda u: True,
        lambda u: {"id": "s_loc", "is_live": True},
        managed=True,
    )
    assert status_busy == "busy"
    assert "gaules" not in pool.active

    # 2) Ao iniciar run(), busca solta gaules ao ve-lo em _excluir_para_busca(), Supervisor inicia e liberar para busca
    excl_saw_gaules = [False]
    liberar_sent = [False]

    def sleep_fn(d: float) -> None:
        clock.sleep(d)
        if not excl_saw_gaules[0] and "gaules" in sup._excluir_para_busca():
            excl_saw_gaules[0] = True
            sup._busca_owned_lives.pop("gaules", None)
        if not liberar_sent[0] and "gaules" in pool.active and irc_started.is_set() and clock.t >= B + 55:
            liberar_sent[0] = True
            db.commands.insert_one({
                "_id": "cmd-lib-t6",
                "worker_id": "w-local",
                "acao": "liberar",
                "status": "pending",
                "criado_em": coord._dt(),
                "expira_em": coord._dt(+300),
                "executado_em": None,
            })

    sup.sleep = sleep_fn
    with pytest.raises(SystemExit) as exc:
        sup.run()

    assert exc.value.code == 75
    assert excl_saw_gaules[0] is True
    assert pool.started_order == ["gaules"]
    assert irc_stopped.is_set()


# ---- controle-discord T7: worker de VOD com fila vazia, prioridade, feito e falhou sanitizado ---


def test_t7_vod_so_roda_com_fila_vazia_atualiza_job_atual_e_trata_feito_falha_policy(tmp_path, monkeypatch):
    import json
    import threading

    clock = Clock(B + 10)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=0,kick=0,youtube=0,busca=0")
    monkeypatch.setenv("CLIPES_ALIAS", "PC-1")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok_vod_secreto_99999")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-vod", now=clock.now, lease_s=90)
    db.alvos.insert_many([
        {"_id": "vod:ok000001", "tipo": "vod", "url": "https://twitch.tv/videos/1", "estado": "ativo", "criado_em": 1},
        {"_id": "vod:blk00002", "tipo": "vod", "url": "https://twitch.tv/videos/2", "estado": "ativo", "criado_em": 2, "canal": "proibido"},
        {"_id": "vod:err00003", "tipo": "vod", "url": "https://twitch.tv/videos/3", "estado": "ativo", "criado_em": 3},
    ])

    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    queue = monitor.DiskQueue(queue_root)
    # Semeia 1 job da busca em pending/ (e em db.jobs) — deve rodar ANTES de qualquer VOD
    coord.add_job("busca_prioritaria", "twitch:c1:1", (0, 60), 5.0)
    d = queue.root / ".staging" / "busca_prioritaria"
    d.mkdir(parents=True, exist_ok=True)
    (d / "source.mkv").write_bytes(b"v")
    (d / "meta.json").write_text(json.dumps({"id": "busca_prioritaria", "duration": 60.0}), encoding="utf-8")
    monitor._write_job(d, {
        "job_id": "busca_prioritaria", "mongo_job_id": "busca_prioritaria", "live_id": "twitch:c1:1",
        "coord_synced": True, "channel": "c1", "origem": "busca", "z_score": 5.0,
        "created_at": float(B), "status": "pending",
    })
    queue.enqueue(d)

    ordem: list[str] = []
    beats_job_atual: list[str | None] = []
    orig_beat = coord.beat
    coord.beat = lambda alias, cap, job_atual, ml, razao, **kw: (
        beats_job_atual.append(job_atual),
        orig_beat(alias, cap, job_atual, ml, razao, **kw),
    )[1]

    avisos: list[str] = []
    stop = threading.Event()

    def fake_busca_pipe(job_dir, root):
        ordem.append(f"busca:{job_dir.name}")
        return {"clips": []}

    def fake_vod_pipe(url: str, root):
        ordem.append(f"vod:{url}")
        if url.endswith("/1"):
            time.sleep(0.12)  # deixa o keepalive registrar job_atual no heartbeat
            return 0
        if url.endswith("/3"):
            raise RuntimeError("falha em mongodb+srv://u:segredo@host/db com tok_vod_secreto_99999")
        return 0

    def sleep_fn(d_s: float) -> None:
        clock.sleep(d_s)
        if db.alvos.count_documents({"tipo": "vod", "estado": "ativo"}) == 0:
            stop.set()

    sup = acompanhar.Supervisor(
        [],
        out_root,
        queue_root=queue_root,
        pool=FakeMultiPool(clock, {}),
        pipeline=fake_busca_pipe,
        coord=coord,
        now=clock.now,
        sleep=sleep_fn,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        policy=monitor.ChannelPolicy(blocked={"proibido"}),
        aviso_fn=avisos.append,
        beat_s=0.04,
        stop_event=stop,
        vod_pipeline=fake_vod_pipe,
    )
    sup.run()

    # Prioridade: busca rodou antes dos VODs; bloqueado pela policy nao executou vod_pipeline
    assert ordem == [
        "busca:busca_prioritaria",
        "vod:https://twitch.tv/videos/1",
        "vod:https://twitch.tv/videos/3",
    ]
    assert "vod:ok000001" in beats_job_atual

    d1 = db.alvos.find_one({"_id": "vod:ok000001"})
    assert d1["estado"] == "feito" and d1["erro"] is None

    d2 = db.alvos.find_one({"_id": "vod:blk00002"})
    assert d2["estado"] == "falhou" and "policy" in d2["erro"]

    d3 = db.alvos.find_one({"_id": "vod:err00003"})
    assert d3["estado"] == "falhou"
    assert "segredo" not in d3["erro"]
    assert "tok_vod_secreto_99999" not in d3["erro"]
    assert "mongodb+srv://***@***" in d3["erro"]
    assert any("vod:err00003" in a and "segredo" not in a and "tok_vod_secreto_99999" not in a for a in avisos)
    assert any("vod:blk00002" in a for a in avisos)


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
