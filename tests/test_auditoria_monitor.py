"""Auditoria (onda 1, W1-A): C-02, M-01..M-05, B-01, B-02, A-02 no monitor."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import mongomock
import pytest
from pymongo.errors import ServerSelectionTimeoutError

from clipes import monitor
from clipes.coord import Coord
from clipes.executor_publicacao import PublishLoop
from test_monitor_coord import T0, Clock, FakePopen, make_staged_job

CAP = {"cpus": 6, "score": 30}


class FlakyCoord:
    """Proxy de um Coord que levanta erro de Mongo enquanto `down` estiver ligado."""

    def __init__(self, inner: Coord, down: bool = False):
        self.inner = inner
        self.down = down
        self.worker_id = inner.worker_id
        self.calls: list[str] = []

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            self.calls.append(name)
            if self.down:
                raise ServerSelectionTimeoutError("mongo fora")
            return attr(*args, **kwargs)

        return call


# ---- C-02: PublishLoop + heartbeat sem upload -------------------------------------------------------


def test_publish_loop_run_now_nao_bloqueia_e_recusa_rodada_concorrente():
    started, release = threading.Event(), threading.Event()
    calls: list[int] = []

    def run_once():
        calls.append(1)
        started.set()
        release.wait(5)

    loop = PublishLoop(run_once, 0)
    t0 = time.monotonic()
    assert loop.run_now() is True
    assert time.monotonic() - t0 < 1.0  # nao esperou o run_once
    assert started.wait(2)
    assert loop.running
    assert loop.run_now() is False  # ja rodando
    release.set()
    assert loop.stop(timeout=5) is True
    assert calls == [1]
    assert loop.run_now() is False  # parado


def test_publish_loop_engole_excecao_e_roda_de_novo(capsys):
    calls: list[int] = []

    def run_once():
        calls.append(1)
        raise RuntimeError("boom")

    loop = PublishLoop(run_once, 0.01, name="pub-teste")
    loop.start()
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert loop.stop(timeout=5)
    assert len(calls) >= 3  # a thread sobreviveu as excecoes
    assert "[pub-teste] erro na rodada: RuntimeError: boom" in capsys.readouterr().out


def test_heartbeat_segue_batendo_enquanto_o_upload_esta_travado(tmp_path):
    """C-02: run_pending roda na thread do PublishLoop; beat/renew nao esperam o upload."""
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-c02", now=clock, lease_s=90)
    beats: list[float | None] = []
    orig_beat = coord.beat

    def spy_beat(alias, cap, job_atual, max_lives, razao, beat_s=None):
        beats.append(beat_s)
        return orig_beat(alias, cap, job_atual, max_lives, razao, beat_s=beat_s)

    coord.beat = spy_beat
    started, release = threading.Event(), threading.Event()
    pending_calls: list[int] = []
    calls_while_blocked: list[int] = []

    def slow_run_pending(*args, **kwargs):
        pending_calls.append(1)
        started.set()
        release.wait(10)  # upload "lento"
        return []

    async def irc(channels, on_message, stop):
        while not stop.is_set():
            await clock.sleep(1)

    async def driver():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(), queue_root=tmp_path / "q",
            irc=irc, popen=FakePopen(), pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            stop=stop, coord=coord, capacity_fn=lambda _: CAP, beat_s=20, run_pending_fn=slow_run_pending,
        ))
        for _ in range(15000):  # folga para maquina carregada
            if len(beats) >= 4 or task.done():
                break
            await asyncio.sleep(0.002)
        blocked_during_beats = started.is_set() and not release.is_set()
        calls_while_blocked.append(len(pending_calls))  # antes de soltar: depois disso o heartbeat pode abrir outra rodada
        release.set()
        stop.set()
        await task
        return blocked_during_beats

    assert asyncio.run(driver()) is True
    assert len(beats) >= 4  # antes: 1 beat e o heartbeat ficava preso no run_pending
    assert calls_while_blocked == [1]  # trava "ja rodando": rodadas seguintes nao empilharam
    assert set(beats) == {20.0}  # beat_s vai para o doc do worker
    assert db.workers.find_one({"_id": "w-c02"})["beat_s"] == 20.0


def test_lease_e_no_minimo_3x_o_beat(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CLIPES_LEASE_S", "30")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")
    assert monitor._effective_lease_s() == 60.0
    assert "CLIPES_LEASE_S=30 < 3*beat" in capsys.readouterr().out
    monkeypatch.setenv("CLIPES_LEASE_S", "90")
    assert monitor._effective_lease_s() == 90.0
    assert monitor._effective_lease_s(40) == 120.0

    # onde o Coord e criado
    monkeypatch.setenv("CLIPES_LEASE_S", "30")
    monkeypatch.setenv("CLIPES_MONGO_URI", "mongodb://localhost/clipes_lives")
    import pymongo

    monkeypatch.setattr(pymongo, "MongoClient", lambda *a, **k: mongomock.MongoClient())
    session = monitor._CoordSession(tmp_path, beat_s=20)
    assert session.connect() is True
    assert session.coord.lease_s == 60.0


# ---- A-02: release_expired_own ao conectar ------------------------------------------------------------


def test_connect_solta_lives_proprias_com_lease_vencida(tmp_path):
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-a02", now=clock, lease_s=90)
    coord.ensure_live("twitch:velha:1", channel="velha", platform="twitch")
    assert coord.claim_live("twitch:velha:1")
    clock.t += 1000  # lease vencida (crash)
    session = monitor._CoordSession(tmp_path, coord=coord, now=clock)
    assert session.connect() is True
    doc = db.lives.find_one({"_id": "twitch:velha:1"})
    assert doc["owner"] is None and doc["lease_until"] is None


def test_connect_com_mongo_fora_na_limpeza_vira_offline(tmp_path):
    db = mongomock.MongoClient().db
    flaky = FlakyCoord(Coord(db, "w-x"), down=True)
    session = monitor._CoordSession(tmp_path, coord=flaky)
    assert session.connect() is False
    assert session.online is False


# ---- M-01: Mongo fora -> grava; reconecta -> claim; claim negado -> sai do hot -------------------------


def _m01_run(tmp_path, *, other_owns: bool):
    clock = Clock()
    db = mongomock.MongoClient().db
    flaky = FlakyCoord(Coord(db, "w-me", now=clock, lease_s=100_000), down=True)
    if other_owns:
        other = Coord(db, "w-other", now=clock, lease_s=100_000)
        other.ensure_live("twitch:canal:123", channel="canal", platform="twitch")
        assert other.claim_live("twitch:canal:123")
    popen = FakePopen()
    seen: dict[str, object] = {}

    async def irc(channels, on_message, stop):
        i = 0
        while not stop.is_set():
            await clock.sleep(1)
            if clock() >= T0 + 20:
                i += 1
                on_message("canal", clock(), f"u{i % 3}", "oi")

    async def driver():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(), queue_root=tmp_path / "q",
            irc=irc, popen=popen, pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            stop=stop, coord=flaky, stream_ids={"canal": "123"}, capacity_fn=lambda _: CAP,
            max_lives_fn=lambda *_: 1,
        ))
        for _ in range(6000):
            if popen.pipelines() and "gravou_offline" not in seen:
                seen["gravou_offline"] = any(p.returncode is None for p in popen.pipelines())
                seen["lives_offline"] = db.lives.count_documents({})
            if clock() >= T0 + 70 and flaky.down:
                flaky.down = False  # Mongo volta antes do retry de 60s vencer
            if "gravou_offline" in seen and not flaky.down and clock() >= T0 + 200:
                break
            await asyncio.sleep(0.002)
        seen["recorders_vivos_no_fim"] = [p for p in popen.pipelines() if p.returncode is None]
        seen["owner_antes_do_stop"] = (db.lives.find_one({"_id": "twitch:canal:123"}) or {}).get("owner")
        stop.set()
        await task

    asyncio.run(driver())
    return db, seen, popen


def test_mongo_fora_grava_e_ao_voltar_reivindica_a_lease(tmp_path):
    db, seen, popen = _m01_run(tmp_path, other_owns=False)
    assert seen["gravou_offline"] is True  # captura segue sem Mongo (inevitavel)
    assert seen["lives_offline"] == 0
    assert seen["owner_antes_do_stop"] == "w-me"  # reivindicou depois de reconectar
    assert len(seen["recorders_vivos_no_fim"]) >= 1  # claim ok: segue gravando


def test_mongo_volta_e_claim_negado_para_de_gravar(tmp_path):
    db, seen, popen = _m01_run(tmp_path, other_owns=True)
    assert seen["gravou_offline"] is True
    assert seen["owner_antes_do_stop"] == "w-other"
    assert seen["recorders_vivos_no_fim"] == []  # canal saiu do hot: gravador parado
    assert all(p.returncode is not None for p in popen.procs)


# ---- M-02: max_lives sem corrida entre tasks de claim ---------------------------------------------


def test_max_lives_1_com_claims_concorrentes_reivindica_uma_so(tmp_path):
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-m02", now=clock, lease_s=100_000)
    claims: list[str] = []
    orig_claim = coord.claim_live

    def spy_claim(live_id):
        claims.append(live_id)
        return orig_claim(live_id)

    coord.claim_live = spy_claim
    channels = ["ca", "cb", "cc"]

    async def irc(channels_, on_message, stop):
        i = 0
        while not stop.is_set():
            await clock.sleep(1)
            if clock() >= T0 + 20:
                i += 1
                for ch in channels:
                    on_message(ch, clock(), f"u{i % 3}", "oi")

    async def driver():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_monitor(
            channels, tmp_path / "out", monitor.ChannelPolicy(), queue_root=tmp_path / "q",
            irc=irc, popen=FakePopen(), pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            stop=stop, coord=coord, stream_ids={"ca": "1", "cb": "2", "cc": "3"},
            capacity_fn=lambda _: CAP, max_lives_fn=lambda *_: 1,
        ))
        for _ in range(6000):
            if clock() >= T0 + 120:
                break
            await asyncio.sleep(0.002)
        stop.set()
        await task

    asyncio.run(driver())
    assert db.lives.count_documents({"owner": "w-m02", "status": "live"}) <= 1
    assert len(claims) == 1


# ---- M-03: o monitor delega o comando ao Coord.run_pending_command -----------------------------------


def test_heartbeat_delega_liberar_para_run_pending_command(tmp_path):
    clock = Clock()
    seen: list[str] = []

    class StubCoord:
        worker_id = "w-stub"

        def ensure_indexes(self):
            pass

        def release_expired_own(self):
            return 0

        def beat(self, *args, **kwargs):
            seen.append("beat")

        def renew(self, live_id):
            return True

        def run_pending_command(self, liberar):
            seen.append("cmd")
            liberar()
            return "liberar"

    killed: list[bool] = []

    async def irc(channels, on_message, stop):
        while not stop.is_set():
            await clock.sleep(1)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(), queue_root=tmp_path / "q",
            irc=irc, popen=FakePopen(), pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            coord=StubCoord(), capacity_fn=lambda _: CAP, kill_children=lambda: killed.append(True),
        ))
    assert exc.value.code == 75
    assert seen[:2] == ["beat", "cmd"] and killed == [True]


# ---- M-04: cancelamento devolve o job a pending; lock so fecha depois da thread -----------------------


def _queue_with_job(tmp_path, **extra):
    queue = monitor.DiskQueue(tmp_path / "q")
    queue.enqueue(make_staged_job(tmp_path / "stage", channel="canal", peak=int(T0), **extra))
    return queue


def test_pipeline_falha_com_cancelamento_devolve_job_a_pending_e_nao_marca_rejected(tmp_path):
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-m04", now=clock)
    queue = _queue_with_job(tmp_path)
    coord.ensure_live("twitch:canal:1", channel="canal", platform="twitch")
    coord.add_job("canal_job", "twitch:canal:1", (0, 10), 5.0)
    job_dir = queue.root / "pending" / f"canal_{int(T0)}"
    job = json.loads((job_dir / "job.json").read_text())
    job.update(mongo_job_id="canal_job", live_id="twitch:canal:1", coord_synced=True)
    (job_dir / "job.json").write_text(json.dumps(job))
    cancel = threading.Event()

    def pipeline(job_dir_, out_root):
        cancel.set()  # liberar: filhos mortos, o pipeline estoura
        raise RuntimeError("ffmpeg morto")

    assert monitor.run_worker_step(queue, tmp_path / "out", monitor.ChannelPolicy(), pipeline, coord=coord,
                                   cancel=cancel) is True
    assert [d.name for d in (queue.root / "pending").iterdir()] == [f"canal_{int(T0)}"]
    assert list((queue.root / "rejected").iterdir()) == []
    assert json.loads((queue.root / "pending" / f"canal_{int(T0)}" / "job.json").read_text())["status"] == "pending"
    assert db.jobs.find_one({"_id": "canal_job"})["status"] == "processing"  # nem rejected nem failed


def test_pipeline_falha_sem_cancelamento_segue_rejected(tmp_path):
    queue = _queue_with_job(tmp_path)

    def pipeline(*_):
        raise RuntimeError("quebrou")

    monitor.run_worker_step(queue, tmp_path / "out", monitor.ChannelPolicy(), pipeline, cancel=threading.Event())
    assert len(list((queue.root / "rejected").iterdir())) == 1


def test_finally_do_monitor_so_fecha_o_lock_depois_do_worker_terminar(tmp_path):
    clock = Clock()
    queue_root = tmp_path / "q"
    queue = _queue_with_job(tmp_path)
    started, release = threading.Event(), threading.Event()
    observed: list[str] = []

    def pipeline(job_dir, out_root):
        started.set()
        release.wait(10)
        raise RuntimeError("morto no encerramento")

    def probe_then_release():
        try:
            monitor._lock_worker(queue_root)
        except RuntimeError:
            observed.append("lock_ainda_preso")
        else:
            observed.append("lock_solto_cedo")
        release.set()

    async def irc(channels, on_message, stop):
        while not stop.is_set():
            await clock.sleep(1)

    async def driver():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(), queue_root=queue_root,
            irc=irc, popen=FakePopen(), pipeline=pipeline, now=clock, sleep=clock.sleep, stop=stop,
        ))
        for _ in range(3000):
            if started.is_set():
                break
            await asyncio.sleep(0.002)
        assert started.is_set()
        threading.Timer(0.3, probe_then_release).start()
        stop.set()
        await task

    asyncio.run(driver())
    assert observed == ["lock_ainda_preso"]
    monitor._lock_worker(queue_root).close()  # agora liberado
    # o pipeline falhou durante o encerramento: job volta a pending, nao rejected
    assert len(list((queue.root / "pending").iterdir())) == 1
    assert list((queue.root / "rejected").iterdir()) == []


# ---- M-05: complete() apaga o source so depois do move; recover conclui move de job final ---------------


def test_complete_com_crash_no_move_preserva_o_source(tmp_path, monkeypatch):
    queue = _queue_with_job(tmp_path)
    claimed = queue.claim_next()
    (claimed / "source.mkv").write_bytes(b"x")

    def boom(self, job_dir, state):
        raise OSError("crash no move")

    monkeypatch.setattr(monitor.DiskQueue, "_move", boom)
    with pytest.raises(OSError):
        queue.complete(claimed, "done", {"score": 0.9})
    assert (claimed / "source.mkv").exists()  # antes: ja tinha sido apagado


def test_recover_orphans_conclui_move_de_job_com_status_final(tmp_path):
    queue = _queue_with_job(tmp_path)
    claimed = queue.claim_next()
    (claimed / "source.mkv").write_bytes(b"x")
    job = json.loads((claimed / "job.json").read_text())
    job.update(status="done", score=0.9)  # crash entre a gravacao do status e o move
    (claimed / "job.json").write_text(json.dumps(job))
    other = queue.root / "processing" / "outro_1"
    other.mkdir()
    (other / "job.json").write_text(json.dumps({"job_id": "outro_1", "channel": "canal", "status": "processing"}))

    restarted = monitor.DiskQueue(queue.root)
    assert restarted.recover_orphans() == 2
    done = queue.root / "done" / claimed.name
    assert json.loads((done / "job.json").read_text())["status"] == "done"
    assert not (done / "source.mkv").exists()
    assert [d.name for d in (queue.root / "pending").iterdir()] == ["outro_1"]  # o nao concluido volta a pending


# ---- B-01: replay_unsynced_jobs ---------------------------------------------------------------------


def _unsynced(queue, tmp_path, name, state="pending", **extra):
    job_dir = make_staged_job(tmp_path / f"st-{name}", channel="canal", peak=int(T0) + extra.pop("off", 0),
                              coord_synced=False, live_id="twitch:canal:9", **extra)
    queue.enqueue(job_dir)
    return queue.root / "pending" / job_dir.name


def test_replay_sobe_job_pending_marca_synced_e_e_idempotente(tmp_path):
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-b01", now=clock)
    queue = monitor.DiskQueue(tmp_path / "q")
    job_dir = _unsynced(queue, tmp_path, "a", window_end=int(T0) + 10)
    assert monitor.replay_unsynced_jobs(queue, coord) == 1
    doc = db.jobs.find_one({"_id": f"twitch:canal:9:{int(T0) + 10}"})
    assert doc is not None and doc["status"] == "pending" and doc["live_id"] == "twitch:canal:9"
    job = json.loads((job_dir / "job.json").read_text())
    assert job["coord_synced"] is True and job["mongo_job_id"] == doc["_id"]
    assert monitor.replay_unsynced_jobs(queue, coord) == 0  # nada a fazer
    assert db.jobs.count_documents({}) == 1


def test_replay_sincroniza_status_final_de_job_ja_concluido_localmente(tmp_path):
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-b01f", now=clock)
    queue = monitor.DiskQueue(tmp_path / "q")
    _unsynced(queue, tmp_path, "b", window_end=int(T0) + 20)
    claimed = queue.claim_next()
    queue.complete(claimed, "done", {"score": 0.8, "clips": 2, "output_dir": "x"})
    assert monitor.replay_unsynced_jobs(queue, coord) == 1
    doc = db.jobs.find_one({"_id": f"twitch:canal:9:{int(T0) + 20}"})
    assert doc["status"] == "done" and doc["result"]["clips"] == 2
    done_job = json.loads((queue.root / "done" / claimed.name / "job.json").read_text())
    assert done_job["coord_synced"] is True


def test_replay_erro_de_mongo_sobe_e_mantem_o_job_unsynced(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "q")
    job_dir = _unsynced(queue, tmp_path, "c", window_end=int(T0) + 30)
    flaky = FlakyCoord(Coord(mongomock.MongoClient().db, "w-x"), down=True)
    with pytest.raises(ServerSelectionTimeoutError):
        monitor.replay_unsynced_jobs(queue, flaky)
    assert json.loads((job_dir / "job.json").read_text())["coord_synced"] is False


def test_replay_pula_lives_perdidas(tmp_path):
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-b01s")
    queue = monitor.DiskQueue(tmp_path / "q")
    _unsynced(queue, tmp_path, "d", window_end=int(T0) + 40)
    assert monitor.replay_unsynced_jobs(queue, coord, skip_lives={"twitch:canal:9"}) == 0
    assert db.jobs.count_documents({}) == 0


def test_reconexao_do_monitor_dispara_o_replay(tmp_path):
    """Job criado offline (coord_synced=False) sobe para o Mongo quando a sessao reconecta."""
    clock = Clock()
    db = mongomock.MongoClient().db
    flaky = FlakyCoord(Coord(db, "w-recon", now=clock), down=True)
    queue = monitor.DiskQueue(tmp_path / "q")
    first = make_staged_job(tmp_path / "s1", channel="canal", peak=int(T0), z_score=9.0)
    queue.enqueue(first)  # este fica "em processamento" (pipeline bloqueado)
    second = make_staged_job(tmp_path / "s2", channel="canal", peak=int(T0) + 50,
                             coord_synced=False, live_id="twitch:canal:9", window_end=int(T0) + 50)
    job = json.loads((second / "job.json").read_text())
    job["z_score"] = 1.0
    (second / "job.json").write_text(json.dumps(job))
    queue.enqueue(second)
    release = threading.Event()

    def pipeline(job_dir, out_root):
        release.wait(10)
        return {"clips": []}

    async def irc(channels, on_message, stop):
        while not stop.is_set():
            await clock.sleep(1)

    async def driver():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(), queue_root=queue.root,
            irc=irc, popen=FakePopen(), pipeline=pipeline, now=clock, sleep=clock.sleep, stop=stop,
            coord=flaky, capacity_fn=lambda _: CAP,
        ))
        for _ in range(6000):
            if clock() >= T0 + 55:
                flaky.down = False
            if db.jobs.find_one({"_id": f"twitch:canal:9:{int(T0) + 50}"}):
                break
            await asyncio.sleep(0.002)
        release.set()
        stop.set()
        await task

    asyncio.run(driver())
    assert db.jobs.find_one({"_id": f"twitch:canal:9:{int(T0) + 50}"}) is not None


# ---- B-02: fsync antes do rename ----------------------------------------------------------------------


def test_write_job_faz_fsync_antes_do_replace(tmp_path, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(monitor.os, "fsync", lambda fd: events.append(f"fsync tmp_existe={(tmp_path / 'job.json.tmp').exists()}"))
    orig_replace = Path.replace

    def spy_replace(self, target):
        events.append("replace")
        return orig_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy_replace)
    monitor._write_job(tmp_path, {"a": 1})
    assert events == ["fsync tmp_existe=True", "replace"]
    assert json.loads((tmp_path / "job.json").read_text()) == {"a": 1}


# ---- Revisao: B-01 (status final), A-02 (so na 1a conexao), M-04 (cancel antes do pipeline), M-02, C-02 ----


def _synced_job(queue, tmp_path, name, **extra):
    job_dir = make_staged_job(tmp_path / f"sy-{name}", channel="canal", peak=int(T0) + extra.pop("off", 0),
                              coord_synced=True, live_id="twitch:canal:9", **extra)
    queue.enqueue(job_dir)


def test_job_processado_sem_mongo_vira_unsynced_e_o_replay_sobe_o_status_final(tmp_path):
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-rev1")
    queue = monitor.DiskQueue(tmp_path / "q")
    _synced_job(queue, tmp_path, "a", window_end=int(T0) + 10)
    # Mongo fora: o worker roda sem coord (coord=None) um job que ja estava sincronizado
    assert monitor.run_worker_step(queue, tmp_path / "out", monitor.ChannelPolicy(),
                                   lambda *_: {"clips": [{"score": 0.5}]}, coord=None)
    done = next((queue.root / "done").iterdir())
    assert json.loads((done / "job.json").read_text())["coord_synced"] is False
    assert monitor.replay_unsynced_jobs(queue, coord) == 1
    assert db.jobs.find_one({"_id": f"twitch:canal:9:{int(T0) + 10}"})["status"] == "done"


def test_falha_de_finish_job_por_mongo_marca_unsynced(tmp_path):
    db = mongomock.MongoClient().db
    inner = Coord(db, "w-rev1b")
    queue = monitor.DiskQueue(tmp_path / "q")
    inner.ensure_live("twitch:canal:9", channel="canal", platform="twitch")
    inner.add_job("twitch:canal:9:5", "twitch:canal:9", (0, 5), 1.0)
    _synced_job(queue, tmp_path, "b", window_end=5, mongo_job_id="twitch:canal:9:5")

    class FinishDown:
        worker_id = "w-rev1b"

        def start_job(self, job_id):
            return inner.start_job(job_id)

        def finish_job(self, *a):
            raise ServerSelectionTimeoutError("caiu")

    errors: list[BaseException] = []
    monitor.run_worker_step(queue, tmp_path / "out", monitor.ChannelPolicy(), lambda *_: {"clips": [{"score": 1}]},
                            coord=FinishDown(), on_mongo_error=errors.append)
    assert errors
    done = next((queue.root / "done").iterdir())
    assert json.loads((done / "job.json").read_text())["coord_synced"] is False
    assert monitor.replay_unsynced_jobs(queue, inner) == 1
    assert db.jobs.find_one({"_id": "twitch:canal:9:5"})["status"] == "done"


def test_replay_e_claim_next_nao_se_atropelam_no_job(tmp_path):
    """O replay segura o mutex da fila durante o envio: claim_next espera e nao perde a gravacao."""
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-rev1c")
    queue = monitor.DiskQueue(tmp_path / "q")
    job_dir = _unsynced(queue, tmp_path, "r", window_end=int(T0) + 10)
    claimed: list[Path | None] = []
    blocked: list[bool] = []
    orig_add = coord.add_job

    def add_job(*args):
        t = threading.Thread(target=lambda: claimed.append(queue.claim_next()))
        t.start()
        t.join(0.3)
        blocked.append(t.is_alive())  # claim_next esta esperando o mutex
        return orig_add(*args)

    coord.add_job = add_job
    assert monitor.replay_unsynced_jobs(queue, coord) == 1
    deadline = time.monotonic() + 5
    while not claimed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert blocked == [True]
    moved = json.loads((claimed[0] / "job.json").read_text())
    assert moved["coord_synced"] is True and moved["status"] == "processing"
    assert job_dir.name == claimed[0].name


def test_replay_remap_troca_live_de_fallback_pela_real(tmp_path):
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-rev3")
    queue = monitor.DiskQueue(tmp_path / "q")
    job_dir = make_staged_job(tmp_path / "fb", channel="canal", peak=int(T0), coord_synced=False,
                              live_id="twitch:canal", window_end=int(T0) + 7)
    queue.enqueue(job_dir)
    assert monitor.replay_unsynced_jobs(queue, coord, skip_lives={"twitch:canal"}) == 0  # claim ainda nao decidido
    assert monitor.replay_unsynced_jobs(queue, coord, live_remap={"twitch:canal": "twitch:canal:77"}) == 1
    assert db.jobs.find_one({"_id": f"twitch:canal:77:{int(T0) + 7}"})["live_id"] == "twitch:canal:77"


def test_release_expired_own_so_na_primeira_conexao(tmp_path):
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-rev2", now=clock, lease_s=90)
    session = monitor._CoordSession(tmp_path, coord=coord, now=clock)
    assert session.connect() is True
    coord.ensure_live("twitch:viva:1", channel="viva", platform="twitch")
    assert coord.claim_live("twitch:viva:1")
    clock.t += 1000  # queda longa do Mongo: a lease desta live (ainda gravando) venceu
    session.mark_offline(RuntimeError("x"))
    assert session.connect() is True  # reconexao
    assert db.lives.find_one({"_id": "twitch:viva:1"})["owner"] == "w-rev2"  # nao foi solta
    assert coord.renew("twitch:viva:1") is True


def test_cancel_ligado_durante_start_job_nao_roda_o_pipeline(tmp_path):
    db = mongomock.MongoClient().db
    inner = Coord(db, "w-rev4")
    cancel = threading.Event()
    inner.ensure_live("twitch:canal:9", channel="canal", platform="twitch")
    inner.add_job("twitch:canal:9:5", "twitch:canal:9", (0, 5), 1.0)
    queue = monitor.DiskQueue(tmp_path / "q")
    _synced_job(queue, tmp_path, "c", window_end=5, mongo_job_id="twitch:canal:9:5")

    class Cancelling:
        worker_id = "w-rev4"

        def start_job(self, job_id):
            ok = inner.start_job(job_id)
            cancel.set()  # liberar chegou enquanto falavamos com o Mongo
            return ok

    ran: list[int] = []
    monitor.run_worker_step(queue, tmp_path / "out", monitor.ChannelPolicy(), lambda *_: ran.append(1) or {"clips": []},
                            coord=Cancelling(), cancel=cancel)
    assert ran == []
    assert len(list((queue.root / "pending").iterdir())) == 1
    assert list((queue.root / "rejected").iterdir()) == []


def test_max_lives_2_com_3_canais_reivindica_exatamente_duas(tmp_path):
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-rev5", now=clock, lease_s=100_000)
    claims: list[str] = []
    orig_claim = coord.claim_live
    coord.claim_live = lambda lid: claims.append(lid) or orig_claim(lid)
    chans = ["ca", "cb", "cc"]

    async def irc(channels_, on_message, stop):
        i = 0
        while not stop.is_set():
            await clock.sleep(1)
            if clock() >= T0 + 20:
                i += 1
                for ch in chans:
                    on_message(ch, clock(), f"u{i % 3}", "oi")

    async def driver():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_monitor(
            chans, tmp_path / "out", monitor.ChannelPolicy(), queue_root=tmp_path / "q",
            irc=irc, popen=FakePopen(), pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            stop=stop, coord=coord, stream_ids={"ca": "1", "cb": "2", "cc": "3"},
            capacity_fn=lambda _: CAP, max_lives_fn=lambda *_: 2,
        ))
        for _ in range(6000):
            if clock() >= T0 + 120:
                break
            await asyncio.sleep(0.002)
        stop.set()
        await task

    asyncio.run(driver())
    assert len(claims) == 2  # a reserva ordenada nao nega todo mundo nem deixa passar 3


def test_publish_loop_run_now_nao_inicia_rodada_depois_de_stop():
    runs: list[int] = []
    loop = PublishLoop(lambda: runs.append(1), 0)
    stop_flag = threading.Event()
    results: list[bool] = []

    def spam():
        while not stop_flag.is_set():
            results.append(loop.run_now())

    threads = [threading.Thread(target=spam) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    assert loop.stop(timeout=5) is True
    after_stop = len(runs)
    time.sleep(0.1)
    stop_flag.set()
    for t in threads:
        t.join()
    assert len(runs) == after_stop  # nenhuma thread de rodada nasceu depois de stop() retornar
    assert after_stop >= 1


def test_liberar_lento_ainda_sai_com_75_mesmo_com_o_worker_cancelado(tmp_path):
    """liberar liga o cancel; a task do worker nao pode terminar sozinha e encerrar o monitor com codigo 0."""
    clock = Clock()

    class SlowLiberarCoord:
        worker_id = "w-slow"

        def ensure_indexes(self):
            pass

        def release_expired_own(self):
            return 0

        def beat(self, *args, **kwargs):
            pass

        def renew(self, live_id):
            return True

        def run_pending_command(self, liberar):
            liberar()
            time.sleep(0.3)  # o comando demora a ser marcado `feito`; o worker ja viu o cancel
            return "liberar"

    async def irc(channels, on_message, stop):
        while not stop.is_set():
            await clock.sleep(1)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(), queue_root=tmp_path / "q",
            irc=irc, popen=FakePopen(), pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            coord=SlowLiberarCoord(), capacity_fn=lambda _: CAP, kill_children=lambda: None,
        ))
    assert exc.value.code == 75


# ---- Re-revisao: fallback lost_lives, feito com erro de Mongo, stop tolerante, replay periodico ----


def test_claim_aceito_depois_de_negado_libera_o_id_de_fallback_e_o_replay_sobe_o_job(tmp_path):
    clock = Clock()
    db = mongomock.MongoClient().db
    flaky = FlakyCoord(Coord(db, "w-r3a", now=clock, lease_s=100_000), down=True)
    other = Coord(db, "w-outro", now=clock, lease_s=100_000)
    other.ensure_live("twitch:canal:123", channel="canal", platform="twitch")
    assert other.claim_live("twitch:canal:123")
    queue = monitor.DiskQueue(tmp_path / "q")
    queue.enqueue(make_staged_job(tmp_path / "j1", channel="canal", peak=int(T0), z_score=9.0))  # fica rodando
    j2 = make_staged_job(tmp_path / "j2", channel="canal", peak=int(T0) + 50, coord_synced=False,
                         live_id="twitch:canal", window_end=int(T0) + 50)
    job = json.loads((j2 / "job.json").read_text())
    job["z_score"] = 1.0
    (j2 / "job.json").write_text(json.dumps(job))
    queue.enqueue(j2)
    release = threading.Event()
    target = f"twitch:canal:123:{int(T0) + 50}"

    def pipeline(job_dir, out_root):
        release.wait(30)
        return {"clips": []}

    async def irc(channels, on_message, stop):
        i = 0
        while not stop.is_set():
            await clock.sleep(1)
            if clock() >= T0 + 20:
                i += 1
                on_message("canal", clock(), f"u{i % 3}", "oi")

    async def driver():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(), queue_root=queue.root,
            irc=irc, popen=FakePopen(), pipeline=pipeline, now=clock, sleep=clock.sleep, stop=stop,
            coord=flaky, stream_ids={"canal": "123"}, capacity_fn=lambda _: CAP, max_lives_fn=lambda *_: 1,
        ))
        for _ in range(15000):
            if clock() >= T0 + 70:
                flaky.down = False  # volta e o claim e negado (outro PC e o dono)
            if clock() >= T0 + 160:  # o outro PC solta a live: o proximo retry do claim vence
                db.lives.update_one({"_id": "twitch:canal:123"}, {"$set": {"owner": None, "lease_until": None}})
            if db.jobs.find_one({"_id": target}) or task.done():
                break
            await asyncio.sleep(0.002)
        release.set()
        stop.set()
        await task

    asyncio.run(driver())
    doc = db.jobs.find_one({"_id": target})
    assert doc is not None and doc["live_id"] == "twitch:canal:123"


def test_liberar_com_erro_de_mongo_no_feito_ainda_sai_com_75(tmp_path):
    clock = Clock()

    class FeitoFalha:
        worker_id = "w-r3b"

        def ensure_indexes(self):
            pass

        def release_expired_own(self):
            return 0

        def beat(self, *args, **kwargs):
            pass

        def renew(self, live_id):
            return True

        def run_pending_command(self, liberar):
            liberar()
            raise ServerSelectionTimeoutError("caiu ao marcar feito")

    async def irc(channels, on_message, stop):
        while not stop.is_set():
            await clock.sleep(1)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(), queue_root=tmp_path / "q",
            irc=irc, popen=FakePopen(), pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            coord=FeitoFalha(), capacity_fn=lambda _: CAP, kill_children=lambda: None,
        ))
    assert exc.value.code == 75


def test_pool_stop_de_canal_ja_parado_nao_levanta(tmp_path):
    pool = monitor.RollingRecorderPool(tmp_path / "buf", max_hot=1, popen=FakePopen())
    pool._stop("inexistente", remove_buffer=True)  # antes: KeyError
    pool.stop_channel("inexistente")


def test_tick_loop_nao_religa_gravadores_durante_o_liberar(tmp_path):
    clock = Clock()
    db = mongomock.MongoClient().db
    inner = Coord(db, "w-r3c", now=clock, lease_s=100_000)
    popen = FakePopen()
    seen: dict[str, int] = {}

    class SlowLiberar(FlakyCoord):
        def run_pending_command(self, liberar):
            if not popen.pipelines():
                return None
            liberar()
            seen["no_liberar"] = len(popen.pipelines())
            time.sleep(0.4)  # o tick_loop roda varias vezes (relogio simulado) antes do SystemExit
            seen["depois"] = len(popen.pipelines())
            return "liberar"

    coord = SlowLiberar(inner)

    async def irc(channels, on_message, stop):
        i = 0
        while not stop.is_set():
            await clock.sleep(1)
            if clock() >= T0 + 20:
                i += 1
                on_message("canal", clock(), f"u{i % 3}", "oi")

    with pytest.raises(SystemExit) as exc:
        asyncio.run(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(), queue_root=tmp_path / "q",
            irc=irc, popen=popen, pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            coord=coord, stream_ids={"canal": "5"}, capacity_fn=lambda _: CAP, kill_children=lambda: None,
        ))
    assert exc.value.code == 75
    assert seen["no_liberar"] >= 1 and seen["depois"] == seen["no_liberar"]  # nada religado
    assert all(p.returncode is not None for p in popen.procs)


def test_cancel_antes_do_start_job_nao_gasta_attempt(tmp_path):
    db = mongomock.MongoClient().db
    inner = Coord(db, "w-r3e")
    inner.ensure_live("twitch:canal:9", channel="canal", platform="twitch")
    inner.add_job("twitch:canal:9:5", "twitch:canal:9", (0, 5), 1.0)
    queue = monitor.DiskQueue(tmp_path / "q")
    _synced_job(queue, tmp_path, "e", window_end=5, mongo_job_id="twitch:canal:9:5")
    cancel = threading.Event()
    cancel.set()
    monitor.run_worker_step(queue, tmp_path / "out", monitor.ChannelPolicy(), lambda *_: {"clips": []},
                            coord=inner, cancel=cancel)
    assert db.jobs.find_one({"_id": "twitch:canal:9:5"})["attempts"] == 0
    assert len(list((queue.root / "pending").iterdir())) == 1


def test_heartbeat_reenvia_periodicamente_job_final_unsynced_com_mongo_online(tmp_path):
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-r3f", now=clock)
    queue = monitor.DiskQueue(tmp_path / "q")
    target = f"twitch:canal:9:{int(T0) + 10}"

    async def irc(channels, on_message, stop):
        while not stop.is_set():
            await clock.sleep(1)

    async def driver():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_monitor(
            ["canal"], tmp_path / "out", monitor.ChannelPolicy(), queue_root=queue.root,
            irc=irc, popen=FakePopen(), pipeline=lambda *_: {"clips": []}, now=clock, sleep=clock.sleep,
            stop=stop, coord=coord, capacity_fn=lambda _: CAP, beat_s=1,
        ))
        await asyncio.sleep(0.05)
        # job terminou localmente sem sincronizar, com o Mongo ja online (nenhuma reconexao vai acontecer)
        make_staged_job(queue.root / "done", channel="canal", peak=int(T0), coord_synced=False,
                        live_id="twitch:canal:9", window_end=int(T0) + 10, status="done", score=0.8, clips=1)
        for _ in range(15000):
            if db.jobs.find_one({"_id": target}) or task.done():
                break
            await asyncio.sleep(0.002)
        stop.set()
        await task

    asyncio.run(driver())
    assert db.jobs.find_one({"_id": target})["status"] == "done"


def test_ajuda_do_policy_file_cita_clipes_policy_file(capsys):
    with pytest.raises(SystemExit):
        monitor.main(["--help"])
    assert "CLIPES_POLICY_FILE" in capsys.readouterr().out
