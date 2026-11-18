"""Tests for monitor MongoDB coordination fallback, live claim, heartbeat, and liberar command."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import mongomock
import pytest
from pymongo.errors import ServerSelectionTimeoutError

from clipes import monitor
from clipes.coord import Coord

T0 = 1_763_380_000.0
SECRET_URI = "mongodb+srv://worker:supersecret@cluster0.example.mongodb.net/clipes_lives"


class Clock:
    """Simulated clock advancing by the requested duration (up to 1s per tick for loop responsiveness)."""

    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += min(seconds, 1.0)
        await asyncio.sleep(0.001)


class FakeProc:
    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.stdout = io.BytesIO() if kwargs.get("stdout") else None
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class FakePopen:
    def __init__(self):
        self.procs: list[FakeProc] = []

    def __call__(self, cmd, **kwargs):
        proc = FakeProc(cmd, **kwargs)
        self.procs.append(proc)
        return proc

    def pipelines(self):
        return [p for p in self.procs if p.cmd[0] == "streamlink"]


def make_staged_job(root: Path, channel: str = "canal", peak: int = int(T0) + 70, **extra) -> Path:
    job_id = f"{channel}_{peak}"
    job_dir = root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(
        json.dumps({"job_id": job_id, "channel": channel, "z_score": 5.0, "created_at": float(peak), "status": "pending", **extra}),
        encoding="utf-8",
    )
    return job_dir


async def run_until(condition, **kwargs):
    stop = asyncio.Event()
    task = asyncio.create_task(monitor.run_monitor(stop=stop, **kwargs))
    for _ in range(3000):
        if condition() or task.done():
            break
        await asyncio.sleep(0.002)
    stop.set()
    await task


# ---- T22: Mongo backend vs local DiskQueue fallback -----------------------------------------------


def test_sem_uri_nao_importa_pymongo(tmp_path):
    """FILA-03 AC 10: without CLIPES_MONGO_URI, pymongo is never imported into sys.modules."""
    script = (
        "import asyncio, json, sys, tempfile\n"
        "from pathlib import Path\n"
        "from clipes import monitor\n"
        "assert 'pymongo' not in sys.modules\n"
        "async def fake_irc(channels, on_message, stop):\n"
        "    await stop.wait()\n"
        "async def run():\n"
        "    stop = asyncio.Event()\n"
        "    with tempfile.TemporaryDirectory() as td:\n"
        "        root = Path(td)\n"
        "        q = monitor.DiskQueue(root / 'q')\n"
        "        st = root / 'stage' / 'canal_100'\n"
        "        st.mkdir(parents=True)\n"
        "        (st / 'job.json').write_text(json.dumps({'job_id': 'canal_100', 'channel': 'canal', 'z_score': 5.0, 'created_at': 100.0, 'status': 'pending'}))\n"
        "        q.enqueue(st)\n"
        "        done = []\n"
        "        def pipe(j, o):\n"
        "            done.append(j.name)\n"
        "            stop.set()\n"
        "            return {'clips': []}\n"
        "        await monitor.run_monitor(['canal'], root / 'out', monitor.ChannelPolicy(), queue_root=root / 'q', irc=fake_irc, pipeline=pipe, stop=stop)\n"
        "        assert done == ['canal_100']\n"
        "asyncio.run(run())\n"
        "assert 'pymongo' not in sys.modules\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "CLIPES_MONGO_URI"}
    res = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr + res.stdout


def test_com_coord_liga_add_start_e_finish_job_ao_redor_do_worker(tmp_path, monkeypatch):
    """FILA-02 AC 6-7: run_monitor enqueues to Coord and worker marks job done with relative output_dir."""
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-test", now=clock)
    coord.ensure_indexes()

    async def irc(channels, on_message, stop):
        while clock() < T0 + 70:
            await clock.sleep(1)
        for i in range(12):
            on_message("canal", clock(), f"u{i % 6}", "KKKK")
        await stop.wait()

    async def fake_capture(pool, detector, event, staging_root, *, now, sleep):
        return make_staged_job(staging_root, channel=event.channel, peak=int(event.peak_ts))

    monkeypatch.setattr(monitor, "capture_job", fake_capture)
    out_root = tmp_path / "out"
    queue_root = tmp_path / "q"

    def pipeline(job_dir, root):
        return {"clips": [{"score": 0.85}, {"score": 0.6}], "output_dir": str(root / "canal [1]")}

    done_dir = queue_root / "done"
    asyncio.run(run_until(
        lambda: done_dir.is_dir() and len(list(done_dir.iterdir())) == 1,
        channels=["canal"], out_root=out_root, policy=monitor.ChannelPolicy(),
        queue_root=queue_root, irc=irc, popen=FakePopen(), pipeline=pipeline,
        now=clock, sleep=clock.sleep, coord=coord,
        stream_ids={"canal": "999"}, capacity_fn=lambda _: {"cpus": 4, "score": 30},
    ))

    jobs = list(db.jobs.find())
    assert len(jobs) == 1
    doc = jobs[0]
    assert doc["status"] == "done"
    assert doc["owner"] == "w-test"
    assert doc["attempts"] == 1
    assert doc["result"] == {"clips": 2, "score": 0.85, "output_dir": "canal [1]"}


def test_mongo_fora_continua_local_mascara_uri_e_reconecta_em_60s(tmp_path, monkeypatch, capsys):
    """FILA-03 AC 11-12: ServerSelectionTimeoutError falls back to DiskQueue, masks URI in logs, retries after 60s."""
    clock = Clock()
    monkeypatch.setenv("CLIPES_MONGO_URI", SECRET_URI)
    out_root = tmp_path / "out"
    queue_root = tmp_path / "q"

    class FailingThenRecoveringCoord:
        def __init__(self):
            self.worker_id = "w-fail"
            self.online = False
            self.ensure_calls = 0
            self.started: list[str] = []
            self.finished: list[tuple[str, str]] = []

        def ensure_indexes(self):
            self.ensure_calls += 1
            if not self.online:
                raise ServerSelectionTimeoutError(f"cannot reach {SECRET_URI}")

        def ensure_live(self, live_id, **meta):
            if not self.online:
                raise ServerSelectionTimeoutError(f"cannot reach {SECRET_URI}")

        def claim_live(self, live_id):
            return True

        def renew(self, live_id):
            return True

        def release(self, live_id, ended):
            pass

        def add_job(self, job_id, live_id, window, priority):
            if not self.online:
                raise ServerSelectionTimeoutError(f"cannot reach {SECRET_URI}")
            return True

        def start_job(self, job_id):
            if not self.online:
                raise ServerSelectionTimeoutError(f"cannot reach {SECRET_URI}")
            self.started.append(job_id)
            return True

        def finish_job(self, job_id, status, result):
            self.finished.append((job_id, status))
            return True

        def beat(self, alias, capacity, job_atual, max_lives, razao):
            pass

        def pending_command(self):
            return None

    coord = FailingThenRecoveringCoord()
    queue = monitor.DiskQueue(queue_root)
    queue.enqueue(make_staged_job(tmp_path / "stage1", channel="canal", peak=int(T0)))

    processed: list[str] = []

    def pipeline(job_dir, root):
        processed.append(job_dir.name)
        return {"clips": [{"score": 0.7}], "output_dir": str(root / job_dir.name)}

    async def irc(channels, on_message, stop):
        await stop.wait()

    # Phase 1: Mongo is down at startup; job must still be processed locally and URI must never leak
    asyncio.run(run_until(
        lambda: len(processed) == 1 and clock() >= T0 + 20,
        channels=["canal"], out_root=out_root, policy=monitor.ChannelPolicy(),
        queue_root=queue_root, irc=irc, popen=FakePopen(), pipeline=pipeline,
        now=clock, sleep=clock.sleep, coord=coord,
    ))

    out_log = capsys.readouterr().out
    assert len(list((queue_root / "done").iterdir())) == 1
    assert "supersecret" not in out_log
    assert "cluster0.example.mongodb.net" not in out_log
    assert "mongodb+srv://***@***" in out_log
    assert coord.ensure_calls == 1  # failed once at startup, no retry before 60s

    # Phase 2: simulate Mongo staying down, advancing 60s to trigger reconnect, then recovering
    queue.enqueue(make_staged_job(tmp_path / "stage2", channel="canal", peak=int(T0) + 100))

    async def run_reconnect():
        stop = asyncio.Event()
        coord.online = False
        task = asyncio.create_task(monitor.run_monitor(
            channels=["canal"], out_root=out_root, policy=monitor.ChannelPolicy(),
            queue_root=queue_root, irc=irc, popen=FakePopen(), pipeline=pipeline,
            now=clock, sleep=clock.sleep, stop=stop, coord=coord,
        ))
        t_start = clock()
        while clock() < t_start + 120:
            if clock() >= t_start + 55:
                coord.online = True
            if len(processed) >= 2 and coord.ensure_calls >= 3:
                break
            await asyncio.sleep(0.002)
        stop.set()
        await task

    asyncio.run(run_reconnect())
    assert coord.ensure_calls >= 3
    assert len(processed) == 2


def test_run_worker_step_coord_start_job_false_nao_roda_pipeline(tmp_path):
    """FILA-02 AC 8: when Coord denies start_job (e.g. attempts >= 3), worker skips pipeline."""
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-a")
    coord.add_job("canal_100", "twitch:canal:1", (0, 100), 5.0)
    db.jobs.update_one({"_id": "canal_100"}, {"$set": {"attempts": 3, "status": "failed"}})

    queue = monitor.DiskQueue(tmp_path / "q")
    queue.enqueue(make_staged_job(tmp_path / "stage", channel="canal", peak=100))

    ran = []
    assert monitor.run_worker_step(
        queue, tmp_path / "out", monitor.ChannelPolicy(),
        pipeline=lambda *_: ran.append(1) or {"clips": []},
        coord=coord,
    ) is True
    assert ran == []
    assert [p.name for p in (queue.root / "rejected").iterdir()] == ["canal_100"]


def test_run_worker_step_coord_timeout_segue_local_sem_vazar_uri(tmp_path, monkeypatch, capsys):
    """FILA-03 AC 11-12: start_job raising ServerSelectionTimeoutError still executes job locally and masks URI."""
    monkeypatch.setenv("CLIPES_MONGO_URI", SECRET_URI)
    queue = monitor.DiskQueue(tmp_path / "q")
    queue.enqueue(make_staged_job(tmp_path / "stage", channel="canal", peak=100))

    class BrokenCoord:
        def start_job(self, job_id):
            raise ServerSelectionTimeoutError(f"timeout {SECRET_URI}")

        def finish_job(self, job_id, status, result):
            raise AssertionError("finish_job must not be called after start_job timed out")

    errors = []
    assert monitor.run_worker_step(
        queue, tmp_path / "out", monitor.ChannelPolicy(),
        pipeline=lambda d, o: {"clips": [{"score": 0.9}], "output_dir": str(o / d.name)},
        coord=BrokenCoord(), on_mongo_error=errors.append,
    ) is True
    assert len(errors) == 1
    assert [p.name for p in (queue.root / "done").iterdir()] == ["canal_100"]
    out = capsys.readouterr().out
    assert "supersecret" not in out
    assert "cluster0.example.mongodb.net" not in out


# ---- T23: live claim, stream_id, 30/score wait, max_lives ----------------------------------------


def test_helix_guarda_stream_id_e_sem_helix_usa_probe_metadata(tmp_path, monkeypatch):
    """FILA-01 AC 1 & edge case: stream_id comes from Helix `id` or falls back to yt-dlp probe_metadata."""
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "csec")
    monkeypatch.setattr(monitor, "_helix_cache", {})
    monkeypatch.setattr(monitor, "_helix_stream_ids", {})

    def fake_http(req):
        if req.full_url == monitor.HELIX_TOKEN_URL:
            return {"access_token": "tok"}, {}
        return {"data": [{"id": "777888", "user_login": "canal_helix"}], "pagination": {}}, {}

    monkeypatch.setattr(monitor, "_http_json", fake_http)
    ch_file = tmp_path / "channels.txt"
    ch_file.write_text("canal_local\n", encoding="utf-8")
    discovered = monitor.discover_channels(ch_file, monitor.ChannelPolicy())
    assert discovered == ["canal_local", "canal_helix"]
    assert monitor._helix_stream_ids["canal_helix"] == "777888"

    probe_calls = []

    def fake_probe(url):
        probe_calls.append(url)
        return {"id": "555444", "is_live": True}

    assert monitor._resolve_stream_id("canal_helix", probe=fake_probe) == "777888"
    assert probe_calls == []
    assert monitor._resolve_stream_id("canal_local", probe=fake_probe) == "555444"
    assert probe_calls == ["https://www.twitch.tv/canal_local"]


def test_coord_que_nega_claim_nunca_inicia_gravador(tmp_path, monkeypatch):
    """FILA-01 AC 2-3: when claim_live returns False, streamlink/ffmpeg recorder is never started."""
    clock = Clock()
    db = mongomock.MongoClient().db
    coord_a = Coord(db, "w-dono", now=clock, lease_s=300)
    coord_b = Coord(db, "w-eu", now=clock, lease_s=300)
    coord_a.ensure_live("twitch:canal:123", channel="canal", platform="twitch")
    assert coord_a.claim_live("twitch:canal:123") is True  # another PC owns it

    popen = FakePopen()

    async def irc(channels, on_message, stop):
        while clock() < T0 + 70:
            await clock.sleep(1)
        for i in range(12):
            on_message("canal", clock(), f"u{i % 6}", "KKKK")
        while clock() < T0 + 120:
            await clock.sleep(1)
        stop.set()

    asyncio.run(monitor.run_monitor(
        ["canal"], tmp_path / "out", monitor.ChannelPolicy(),
        queue_root=tmp_path / "q", irc=irc, popen=popen,
        pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
        coord=coord_b, stream_ids={"canal": "123"},
        capacity_fn=lambda _: {"cpus": 6, "score": 30},
    ))
    assert popen.procs == []

    # Also verify follow_live does not start recorder when claim is denied
    assert coord_a.renew("twitch:canal:123") is True
    pool = monitor.RollingRecorderPool(tmp_path / "buf2", max_hot=0, max_on_demand=1, popen=popen)
    res = monitor.follow_live(
        "canal", tmp_path / "out2", pool=pool, coord=coord_b, stream_id="123",
        is_live=lambda _: True, now=clock, sleep=lambda d: setattr(clock, "t", clock.t + d),
        capacity_fn=lambda _: {"cpus": 6, "score": 30},
    )
    assert res["status"] == "not_claimed"
    assert popen.procs == []


def test_max_lives_1_com_uma_live_propria_nao_reivindica_outra(tmp_path):
    """PERF-02 AC 3: with max_lives=1 (from .perfil.json) and 1 owned live, a second live is not claimed."""
    clock = Clock()
    out_root = tmp_path / "out"
    out_root.mkdir()
    (out_root / ".perfil.json").write_text(
        json.dumps({"razao": 0.4, "cpus": 3, "segundos_live": 300.0, "segundos_proc": 120.0, "medido_em": "x"}),
        encoding="utf-8",
    )

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-1", now=clock)
    claimed_calls: list[str] = []
    orig_claim = coord.claim_live

    def spy_claim(live_id):
        claimed_calls.append(live_id)
        return orig_claim(live_id)

    coord.claim_live = spy_claim
    popen = FakePopen()

    async def irc(channels, on_message, stop):
        while clock() < T0 + 70:
            await clock.sleep(1)
        for i in range(12):
            on_message("canal1", clock(), f"u{i % 6}", "KKKK")
        while clock() < T0 + 85:
            await clock.sleep(1)
        for i in range(12):
            on_message("canal2", clock(), f"u{i % 6}", "KKKK")
        while clock() < T0 + 130:
            await clock.sleep(1)
        stop.set()

    asyncio.run(monitor.run_monitor(
        ["canal1", "canal2"], out_root, monitor.ChannelPolicy(),
        queue_root=tmp_path / "q", irc=irc, popen=popen,
        pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
        coord=coord, stream_ids={"canal1": "111", "canal2": "222"},
        capacity_fn=lambda _: {"cpus": 3, "score": 15},
    ))

    assert claimed_calls == ["twitch:canal1:111"]
    assert db.lives.find_one({"_id": "twitch:canal1:111"})["status"] == "live"
    assert db.lives.find_one({"_id": "twitch:canal2:222"}) is None
    assert [p.cmd[3] for p in popen.pipelines()] == ["twitch.tv/canal1"]


def test_espera_proporcional_a_1_sobre_score_antes_do_claim(tmp_path):
    """FILA-01 AC 9: wait before claim_live is 30s / capacity().score."""
    waits: dict[int, float] = {}
    sleep_args: dict[int, list[float]] = {}
    for score in (3, 10):
        clock = Clock()
        recorded: list[float] = []

        async def rec_sleep(seconds: float) -> None:
            recorded.append(seconds)
            await clock.sleep(seconds)

        db = mongomock.MongoClient().db
        coord = Coord(db, f"w-{score}", now=clock)
        ensure_t: list[float] = []
        claim_t: list[float] = []
        orig_ensure = coord.ensure_live
        orig_claim = coord.claim_live

        def spy_ensure(live_id, **meta):
            ensure_t.append(clock())
            return orig_ensure(live_id, **meta)

        def spy_claim(live_id):
            claim_t.append(clock())
            return orig_claim(live_id)

        coord.ensure_live = spy_ensure
        coord.claim_live = spy_claim

        async def irc(channels, on_message, stop):
            while clock() < T0 + 70:
                await clock.sleep(1)
            for i in range(12):
                on_message("canal", clock(), f"u{i % 6}", "KKKK")
            await stop.wait()

        asyncio.run(run_until(
            lambda: bool(claim_t),
            channels=["canal"], out_root=tmp_path / f"out-{score}", policy=monitor.ChannelPolicy(),
            queue_root=tmp_path / f"q-{score}", irc=irc, popen=FakePopen(),
            pipeline=lambda *_: {"clips": []}, now=clock, sleep=rec_sleep,
            coord=coord, stream_ids={"canal": f"s{score}"},
            capacity_fn=lambda _: {"cpus": score, "score": score},
        ))
        waits[score] = claim_t[0] - ensure_t[0]
        sleep_args[score] = recorded

    assert 10.0 in sleep_args[3]
    assert 3.0 in sleep_args[10]
    assert waits[3] >= 10.0 and waits[10] >= 3.0
    assert waits[3] > waits[10]

    # Exact elapsed time in follow_live (no concurrent tick/worker loops advancing the fake clock)
    exact_waits: dict[int, float] = {}
    for score in (3, 6):
        clock = Clock()
        db = mongomock.MongoClient().db
        coord = Coord(db, f"wf-{score}", now=clock)
        times: list[float] = []
        orig_ensure = coord.ensure_live

        def spy_e(live_id, **meta):
            times.append(clock())
            return orig_ensure(live_id, **meta)

        coord.ensure_live = spy_e
        coord.claim_live = lambda lid: times.append(clock()) or False  # stop before starting ffmpeg

        pool = monitor.RollingRecorderPool(tmp_path / f"buf-{score}", max_hot=0, max_on_demand=1, popen=FakePopen())
        monitor.follow_live(
            "canal", tmp_path / f"outf-{score}", pool=pool, coord=coord, stream_id=f"sf{score}",
            is_live=lambda _: True, now=clock, sleep=lambda d: setattr(clock, "t", clock.t + d),
            capacity_fn=lambda _: {"cpus": score, "score": score},
        )
        exact_waits[score] = times[1] - times[0]

    assert exact_waits[3] == pytest.approx(10.0)
    assert exact_waits[6] == pytest.approx(5.0)
    assert exact_waits[3] == pytest.approx(2.0 * exact_waits[6])


# ---- T24: heartbeat, renew lease, and liberar command -------------------------------------------


def test_heartbeat_3_ciclos_chama_3_beat_e_3_renew_e_renew_falso_para_gravador(tmp_path, monkeypatch, capsys):
    """PAINEL-01 AC 1, FILA-01 AC 4-5: 3 cycles -> 3 beats + 3 renews; failed renew stops recorder and blocks finish_job."""
    clock = Clock()
    monkeypatch.setenv("CLIPES_BEAT_S", "20")
    monkeypatch.setenv("CLIPES_ALIAS", "PC-TESTE")
    out_root = tmp_path / "out"
    out_root.mkdir()
    (out_root / ".perfil.json").write_text(
        json.dumps({"razao": 0.25, "cpus": 4, "segundos_live": 300.0, "segundos_proc": 75.0, "medido_em": "x"}),
        encoding="utf-8",
    )

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-hb", now=clock, lease_s=90)
    other = Coord(db, "w-outro", now=clock, lease_s=90)

    beats: list[tuple] = []
    renews: list[tuple[str, bool]] = []
    orig_beat = coord.beat
    orig_renew = coord.renew

    def spy_beat(alias, cap, job_atual, max_lives, razao):
        beats.append((alias, cap, job_atual, max_lives, razao))
        return orig_beat(alias, cap, job_atual, max_lives, razao)

    def spy_renew(live_id):
        ok = orig_renew(live_id)
        renews.append((live_id, ok))
        return ok

    coord.beat = spy_beat
    coord.renew = spy_renew

    popen = FakePopen()
    queue_root = tmp_path / "q"

    async def irc(channels, on_message, stop):
        while clock() < T0 + 70:
            await clock.sleep(1)
        for i in range(12):
            on_message("canal", clock(), f"u{i % 6}", "KKKK")
        while not stop.is_set():
            await clock.sleep(1)
            on_message("canal", clock(), "viewer_keepalive", "pog")

    # Run until live is owned and 3 renew cycles succeed; then steal lease before the 4th renew
    stolen = [False]

    async def driver():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_monitor(
            ["canal"], out_root, monitor.ChannelPolicy(),
            queue_root=queue_root, irc=irc, popen=popen,
            pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            stop=stop, coord=coord, stream_ids={"canal": "501"},
            capacity_fn=lambda _: {"cpus": 4, "ram_gb": 8.0, "disco_livre_gb": 20.0, "temp_c": 65, "gpu": None, "score": 30},
        ))
        for _ in range(4000):
            if len(renews) == 3 and not stolen[0]:
                # Force another PC to steal the lease
                db.lives.update_one({"_id": "twitch:canal:501"}, {"$set": {"owner": "w-outro"}})
                stolen[0] = True
            if len(renews) >= 4 and not renews[-1][1]:
                break
            await asyncio.sleep(0.002)
        assert popen.pipelines() and any(p.returncode is None for p in popen.procs) is False  # already stopped by renew=False!
        stop.set()
        await task

    asyncio.run(driver())

    assert len(renews) == 4
    assert [ok for _, ok in renews[:3]] == [True, True, True]
    assert renews[3] == ("twitch:canal:501", False)
    assert len(beats) >= 3
    worker_doc = db.workers.find_one({"_id": "w-hb"})
    assert worker_doc["alias"] == "PC-TESTE"
    assert worker_doc["max_lives"] == 2  # floor(0.6 / 0.25) == 2
    assert worker_doc["razao"] == 0.25
    assert len(worker_doc["history"]) == len(beats)
    assert all(p.returncode is not None for p in popen.procs)
    assert "[fila] lease perdida twitch:canal:501" in capsys.readouterr().out


def test_comando_liberar_para_pool_mata_filhos_marca_feito_e_sai_75(tmp_path, capsys):
    """CMD-02 AC 8: pending `liberar` -> executando -> stop_all + kill children -> feito -> log -> SystemExit(75)."""
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-cmd", now=clock)
    cmd_id = db.commands.insert_one({
        "worker_id": "w-cmd", "acao": "liberar", "status": "pending", "criado_em": coord._dt(), "executado_em": None,
    }).inserted_id

    statuses: list[str] = []
    orig_mark = coord.mark_command

    def spy_mark(cid, status):
        statuses.append(status)
        return orig_mark(cid, status)

    coord.mark_command = spy_mark

    popen = FakePopen()
    killed_children: list[bool] = []

    async def irc(channels, on_message, stop):
        while clock() < T0 + 70:
            await clock.sleep(1)
        await stop.wait()

    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(),
            queue_root=tmp_path / "q", irc=irc, popen=popen,
            pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            coord=coord, capacity_fn=lambda _: {"cpus": 2, "score": 2},
            kill_children=lambda: killed_children.append(True),
        ))

    assert exc_info.value.code == 75
    assert statuses == ["executando", "feito"]
    assert killed_children == [True]
    doc = db.commands.find_one({"_id": cmd_id})
    assert doc["status"] == "feito" and doc["executado_em"] is not None
    assert "[comando] liberar recursos" in capsys.readouterr().out


def test_liberar_chama_stop_all_antes_de_marcar_feito(tmp_path, monkeypatch):
    """CMD-02 AC 8: pool.stop_all() runs between `executando` and `feito` when recorders are active."""
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-cmd2", now=clock)
    events: list[str] = []
    orig_mark = coord.mark_command

    def spy_mark(cid, status):
        events.append(f"mark:{status}")
        return orig_mark(cid, status)

    coord.mark_command = spy_mark

    orig_stop_all = monitor.RollingRecorderPool.stop_all

    def spy_stop_all(self):
        events.append("stop_all")
        return orig_stop_all(self)

    monkeypatch.setattr(monitor.RollingRecorderPool, "stop_all", spy_stop_all)

    cmd_inserted = [False]

    async def irc(channels, on_message, stop):
        while clock() < T0 + 70:
            await clock.sleep(1)
        for i in range(12):
            on_message("canal", clock(), f"u{i % 6}", "KKKK")
        while len(events) == 0 and not cmd_inserted[0]:
            if db.lives.find_one({"_id": "twitch:canal:88", "owner": "w-cmd2"}):
                db.commands.insert_one({
                    "worker_id": "w-cmd2", "acao": "liberar", "status": "pending",
                    "criado_em": coord._dt(), "executado_em": None,
                })
                cmd_inserted[0] = True
            await clock.sleep(1)
        await stop.wait()

    popen = FakePopen()
    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(),
            queue_root=tmp_path / "q", irc=irc, popen=popen,
            pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            coord=coord, stream_ids={"canal": "88"},
            capacity_fn=lambda _: {"cpus": 4, "score": 30},
            kill_children=lambda: events.append("kill_children"),
        ))

    assert exc_info.value.code == 75
    assert events[:4] == ["mark:executando", "stop_all", "kill_children", "mark:feito"]
    assert len(popen.pipelines()) >= 1
    assert all(p.returncode is not None for p in popen.procs)


def test_terminate_child_processes_atinge_apenas_descendentes(tmp_path, monkeypatch):
    """CMD-02 AC 8: _terminate_child_processes terminates only descendants of current PID."""
    proc = tmp_path / "proc"
    for pid, ppid, comm in [(100, 1, "init"), (200, 100, "python"), (201, 200, "ffmpeg"), (202, 201, "llama-server"), (300, 100, "other")]:
        d = proc / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} 0 0 0 0 0", encoding="utf-8")

    monkeypatch.setattr(os, "getpid", lambda: 200)
    sent: list[tuple[int, int]] = []
    monitor._terminate_child_processes(proc_root=proc, kill_fn=lambda pid, sig: sent.append((pid, sig)))
    assert sorted(pid for pid, _ in sent) == [201, 202]

