"""Testes da auditoria para clipes/acompanhar.py (A-02, A-03, M-01, M-03, M-07)."""

from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import mongomock
import pymongo.errors
import pytest

from clipes import acompanhar, monitor
from clipes.coord import Coord
from test_acompanhar import B, Clock, FakeMultiPool


def _seed_pending_job(queue: monitor.DiskQueue, job_id: str, job: dict) -> Path:
    d = queue.root / ".staging" / job_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "source.mkv").write_bytes(b"video")
    (d / "meta.json").write_text(json.dumps({"id": job_id, "duration": 600.0}), encoding="utf-8")
    monitor._write_job(d, {"job_id": job_id, "status": "pending", **job})
    return queue.enqueue(d)


# ---- A-03: keepalive continuo + beat_s + lease perdida sem race ----------------------------------


def test_a03_keepalive_renova_durante_is_live_e_fechar_janela_e_para_no_finally(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-a03", now=clock.now, lease_s=90)

    phase = ["idle"]
    beats_by_phase: dict[str, int] = {"is_live": 0, "fechar_janela": 0, "after": 0}
    renews_by_phase: dict[str, int] = {"is_live": 0, "fechar_janela": 0, "after": 0}
    orig_beat = coord.beat
    orig_renew = coord.renew

    def tracked_beat(*args, **kwargs):
        if phase[0] in beats_by_phase:
            beats_by_phase[phase[0]] += 1
        return orig_beat(*args, **kwargs)

    def tracked_renew(lid):
        if phase[0] in renews_by_phase:
            renews_by_phase[phase[0]] += 1
        return orig_renew(lid)

    coord.beat = tracked_beat
    coord.renew = tracked_renew

    slow_live_done = [False]

    def slow_is_live(url: str) -> bool:
        if not slow_live_done[0] and clock.t >= B + 70:
            slow_live_done[0] = True
            phase[0] = "is_live"
            try:
                time.sleep(0.18)
            finally:
                phase[0] = "idle"
        return True

    class SlowConcatPool(FakeMultiPool):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.concat_slept = False

        def freeze_and_concat(self, ch: str, s: float, e: float, dst: Path) -> Path:
            if not self.concat_slept:
                self.concat_slept = True
                phase[0] = "fechar_janela"
                try:
                    time.sleep(0.18)
                finally:
                    phase[0] = "idle"
            return super().freeze_and_concat(ch, s, e, dst)

    pool = SlowConcatPool(clock, {"gaules": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="301",
        is_live=slow_is_live,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=0.05,
    )
    res = sup.run()

    # Apos run(), a thread de keepalive deve estar encerrada
    phase[0] = "after"
    time.sleep(0.12)

    assert res["status"] == "ended"
    assert beats_by_phase["is_live"] >= 1
    assert renews_by_phase["is_live"] >= 1
    assert beats_by_phase["fechar_janela"] >= 1
    assert renews_by_phase["fechar_janela"] >= 1
    assert beats_by_phase["after"] == 0
    assert renews_by_phase["after"] == 0

    worker_doc = db.workers.find_one({"_id": "w-a03"})
    assert worker_doc is not None
    assert worker_doc["beat_s"] == pytest.approx(0.05)


def test_a03_beat_s_do_env_e_gravado_no_documento_workers(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_BEAT_S", "15")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-beats", now=clock.now, lease_s=90)
    pool = FakeMultiPool(clock, {"gaules": B + 320})

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="302",
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()
    assert res["status"] == "ended"
    worker_doc = db.workers.find_one({"_id": "w-beats"})
    assert worker_doc is not None
    assert worker_doc["beat_s"] == 15.0


def test_a03_keepalive_registra_perda_e_loop_principal_para_gravador(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-a03-lost", now=clock.now, lease_s=90)

    stop_threads: list[threading.Thread] = []

    class ThreadTrackPool(FakeMultiPool):
        def stop_channel(self, ch: str) -> None:
            stop_threads.append(threading.current_thread())
            super().stop_channel(ch)

    stole = [False]

    def slow_is_live_and_steal(url: str) -> bool:
        if not stole[0] and clock.t >= B + 70:
            stole[0] = True
            # Outro PC toma a live enquanto is_live bloqueia o loop principal
            db.lives.update_one({"_id": "twitch:gaules:303"}, {"$set": {"owner": "w-outro"}})
            time.sleep(0.18)
        return True

    pool = ThreadTrackPool(clock, {"gaules": B + 620})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="303",
        is_live=slow_is_live_and_steal,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=0.05,
    )
    res = sup.run()

    assert res["status"] == "lease_lost"
    assert stop_threads == [threading.main_thread()]
    doc = db.lives.find_one({"_id": "twitch:gaules:303"})
    assert doc["status"] == "live"
    assert doc["owner"] == "w-outro"


# ---- A-02: release_expired_own + count_owned_live ------------------------------------------------


def test_a02_live_orfa_vencida_e_liberada_no_start_e_nao_bloqueia_max_lives(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-a02", now=clock.now, lease_s=90)
    # Simula live orfa deixada por crash anterior (lease vencida)
    db.lives.insert_one({
        "_id": "twitch:antiga:1",
        "status": "live",
        "owner": "w-a02",
        "lease_until": coord._dt(-300),
    })

    pool = FakeMultiPool(clock, {"nova": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("nova")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="2",
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert pool.started_order == ["nova"]
    orfa = db.lives.find_one({"_id": "twitch:antiga:1"})
    assert orfa["status"] == "live"
    assert orfa["owner"] is None
    assert orfa["lease_until"] is None


def test_a02_live_propria_com_lease_valida_continua_contando_no_max_lives(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-a02-v", now=clock.now, lease_s=90)
    db.lives.insert_one({
        "_id": "twitch:ativa:1",
        "status": "live",
        "owner": "w-a02-v",
        "lease_until": coord._dt(+300),
    })

    pool = FakeMultiPool(clock, {"nova": B + 320})
    avisos: list[str] = []
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("nova")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="2",
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        aviso_fn=avisos.append,
    )
    res = sup.run()

    assert res["status"] == "not_claimed"
    assert pool.started_order == []
    assert db.lives.find_one({"_id": "twitch:ativa:1"})["owner"] == "w-a02-v"


def test_a02_erro_mongo_em_release_expired_own_e_fake_coord_minimo(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    # 1. Erro Mongo em release_expired_own nao derruba o Supervisor
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-a02-err", now=clock.now, lease_s=90)
    called_rel = [0]

    def fail_rel():
        called_rel[0] += 1
        raise pymongo.errors.AutoReconnect("down at start")

    coord.release_expired_own = fail_rel
    pool = FakeMultiPool(clock, {"gaules": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out1",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="10",
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()
    assert res["status"] == "ended"
    assert called_rel[0] == 1

    # 2. Fake coord sem count_owned_live / release_expired_own / run_pending_command
    class MinimalCoord:
        def ensure_live(self, *a, **k):
            pass

        def claim_live(self, lid):
            return True

        def renew(self, lid):
            return True

        def release(self, lid, ended):
            pass

        def add_job(self, *a, **k):
            return True

        def start_job(self, jid):
            return True

        def finish_job(self, *a, **k):
            return True

        def beat(self, alias, cap, job_atual, ml, razao):
            pass

    clock2 = Clock(B + 10)
    pool2 = FakeMultiPool(clock2, {"gaules": B + 320})
    sup2 = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out2",
        pool=pool2,
        pipeline=lambda d, o: {"clips": []},
        coord=MinimalCoord(),
        stream_id="11",
        is_live=lambda u: True,
        now=clock2.now,
        sleep=clock2.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    assert sup2.run()["status"] == "ended"


# ---- M-01: retentativa de claim apos reconexao do Mongo ------------------------------------------


def test_m01_claim_falha_por_mongo_reivindica_ao_reconectar(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-m01-ok", now=clock.now, lease_s=120)

    orig_claim = coord.claim_live
    fail_first = [True]
    claims: list[str] = []

    def flaky_claim(lid):
        claims.append(lid)
        if fail_first[0]:
            fail_first[0] = False
            raise pymongo.errors.AutoReconnect("mongo offline at start")
        return orig_claim(lid)

    coord.claim_live = flaky_claim

    pool = FakeMultiPool(clock, {"gaules": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="501",
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert claims.count("twitch:gaules:501") >= 2
    live_doc = db.lives.find_one({"_id": "twitch:gaules:501"})
    assert live_doc is not None
    assert live_doc["status"] == "ended"
    assert db.jobs.find_one({"_id": f"twitch:gaules:501:{B + 300}"}) is not None


def test_m01_claim_negado_apos_reconexao_encerra_sessao_sem_marcar_ended(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-m01-eu", now=clock.now, lease_s=120)
    other = Coord(db, "w-m01-outro", now=clock.now, lease_s=5000)

    orig_claim = coord.claim_live
    fail_first = [True]

    def flaky_claim(lid):
        if fail_first[0]:
            fail_first[0] = False
            # Outro PC assume enquanto este PC fica sem Mongo
            other.ensure_live(lid, channel="gaules", platform="twitch", stream_id="502")
            assert other.claim_live(lid) is True
            raise pymongo.errors.AutoReconnect("mongo offline at start")
        return orig_claim(lid)

    coord.claim_live = flaky_claim

    pool = FakeMultiPool(clock, {"gaules": B + 620})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="502",
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "lease_lost"
    assert "gaules" in pool.stopped
    assert "gaules" not in pool.active
    doc = db.lives.find_one({"_id": "twitch:gaules:502"})
    assert doc["status"] == "live"
    assert doc["owner"] == "w-m01-outro"


# ---- M-03: comando liberar consumido no beat do loop principal -----------------------------------


def test_m03_comando_liberar_para_pool_mata_filhos_solta_leases_e_sai_75(tmp_path, monkeypatch, capsys):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-m03", now=clock.now, lease_s=120)
    db.commands.insert_one({
        "_id": "cmd-lib-1",
        "worker_id": "w-m03",
        "acao": "liberar",
        "status": "pending",
        "criado_em": coord._dt(),
        "expira_em": coord._dt(+300),
        "executado_em": None,
    })

    events: list[str] = []
    cmd_threads: list[threading.Thread] = []

    class EventPool(FakeMultiPool):
        def stop_all(self) -> None:
            events.append("stop_all")
            super().stop_all()

    orig_mark = coord.mark_command

    def tracked_mark(cid, status):
        events.append(f"mark:{status}")
        return orig_mark(cid, status)

    orig_release = coord.release

    def tracked_release(lid, ended):
        events.append(f"release:{lid}:{ended}")
        return orig_release(lid, ended)

    coord.mark_command = tracked_mark
    coord.release = tracked_release

    def fake_kill_children() -> None:
        cmd_threads.append(threading.current_thread())
        events.append("kill_children")

    pool = EventPool(clock, {"c1": B + 620, "c2": B + 620})
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
        kill_children=fake_kill_children,
    )

    with pytest.raises(SystemExit) as exc_info:
        sup.run()

    assert exc_info.value.code == 75
    assert cmd_threads == [threading.main_thread()]
    assert "[comando] liberar recursos" in capsys.readouterr().out
    assert events.index("mark:executando") < events.index("stop_all") < events.index("kill_children")
    assert events.index("kill_children") < events.index("mark:feito")

    cmd_doc = db.commands.find_one({"_id": "cmd-lib-1"})
    assert cmd_doc["status"] == "feito"
    assert cmd_doc["executado_em"] is not None

    for lid in ("twitch:c1:1", "twitch:c2:2"):
        doc = db.lives.find_one({"_id": lid})
        assert doc["status"] == "live"
        assert doc["owner"] is None
        assert doc["lease_until"] is None


def test_m03_comando_liberar_expirado_e_ignorado_e_marcado_expirado(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-m03-exp", now=clock.now, lease_s=120)
    db.commands.insert_one({
        "_id": "cmd-exp-1",
        "worker_id": "w-m03-exp",
        "acao": "liberar",
        "status": "pending",
        "criado_em": coord._dt() - timedelta(hours=2),
        "expira_em": coord._dt() - timedelta(hours=1),
        "executado_em": None,
    })

    killed = [False]
    pool = FakeMultiPool(clock, {"c1": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("c1")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="9",
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        kill_children=lambda: killed.__setitem__(0, True),
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert killed[0] is False
    assert db.commands.find_one({"_id": "cmd-exp-1"})["status"] == "expirado"


# ---- M-07: jobs alheios (sobras/monitor) nao caem no pipeline_fn cru -----------------------------


def test_m07_nao_processa_jobs_alheios_nem_picos_do_monitor(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    queue = monitor.DiskQueue(queue_root)

    # 1) Pico de chat do monitor com z_score alto no MESMO canal (sem window_start/session_key)
    _seed_pending_job(queue, "gaules_pico_monitor", {
        "channel": "gaules",
        "url": "https://www.twitch.tv/gaules",
        "peak_ts": B - 100,
        "z_score": 9.5,
        "created_at": B - 100,
    })
    # 2) Pico de chat do monitor de outro canal
    _seed_pending_job(queue, "outro_pico_monitor", {
        "channel": "outro",
        "url": "https://www.twitch.tv/outro",
        "peak_ts": B - 100,
        "z_score": 8.0,
        "created_at": B - 100,
    })
    # 3) Sobra de janela de uma execucao anterior (session_key sem sessao neste Supervisor)
    _seed_pending_job(queue, "canal_antigo_1700000000", {
        "channel": "canal_antigo",
        "session_key": "canal_antigo",
        "url": "https://www.twitch.tv/canal_antigo",
        "peak_ts": B - 300,
        "z_score": 0.0,
        "created_at": B - 300,
        "window_start": B - 900,
        "window_end": B - 300,
        "video_start": B - 900,
        "last": False,
    })

    processed_ids: list[str] = []

    def fake_pipe(job_dir: Path, root: Path):
        processed_ids.append(job_dir.name)
        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        vs, e = job["video_start"], job["window_end"]
        # Retorna 1 clipe dentro da regiao dona e 1 fora (so o dono deve sobreviver)
        return {
            "clips": [
                {"file": "dono.mp4", "score": 1.0, "start": (e - 100) - 20 - vs, "end": (e - 100) + 20 - vs},
                {"file": "fora.mp4", "score": 0.9, "start": (e - 5) - 2 - vs, "end": (e - 5) + 2 - vs},
            ],
        }

    pool = FakeMultiPool(clock, {"gaules": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        out_root,
        queue_root=queue_root,
        pool=pool,
        pipeline=fake_pipe,
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert processed_ids == [f"gaules_{B + 300}"]
    # Os 3 jobs alheios permanecem intactos em pending/ (nao foram rodados nem destruidos)
    remaining = sorted(d.name for d in (queue_root / "pending").iterdir())
    assert remaining == ["canal_antigo_1700000000", "gaules_pico_monitor", "outro_pico_monitor"]
    for name in remaining:
        assert (queue_root / "pending" / name / "source.mkv").is_file()
    # O job da sessao passou pelo filtro owns (so 1 clipe sobreviveu)
    done_job = monitor._read_job(queue_root / "done" / f"gaules_{B + 300}")
    assert done_job["clips"] == 1


def test_m07_process_one_direto_retorna_false_quando_so_ha_jobs_alheios(tmp_path):
    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    queue = monitor.DiskQueue(queue_root)
    _seed_pending_job(queue, "alheio_100", {
        "channel": "alheio",
        "session_key": "alheio",
        "z_score": 0.0,
        "created_at": B,
        "window_start": B - 600,
        "window_end": B,
        "video_start": B - 600,
        "last": False,
    })
    called = [False]
    sup = acompanhar.Supervisor([acompanhar.parse_alvo("gaules")], out_root, queue_root=queue_root)
    assert sup._process_one(queue, lambda d, r: (called.__setitem__(0, True), {"clips": []})[1]) is False
    assert called[0] is False
    assert (queue_root / "pending" / "alheio_100").is_dir()


# ---- Revisao W1-B: race fechar_janela/keepalive e skip_lost no loop ativo ------------------------


def test_a03_perda_de_lease_durante_fechamento_de_janela_aborta_add_job_e_enqueue(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-race-fj", now=clock.now, lease_s=90)

    renew_failed = threading.Event()
    orig_renew = coord.renew

    def tracked_renew(lid):
        ok = orig_renew(lid)
        if not ok:
            renew_failed.set()
        return ok

    add_job_calls: list[str] = []
    orig_add_job = coord.add_job

    def tracked_add_job(*args, **kwargs):
        add_job_calls.append(args[0])
        return orig_add_job(*args, **kwargs)

    coord.renew = tracked_renew
    coord.add_job = tracked_add_job

    class StealDuringConcatPool(FakeMultiPool):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.stole = False

        def freeze_and_concat(self, ch: str, s: float, e: float, dst: Path) -> Path:
            if not self.stole:
                self.stole = True
                db.lives.update_one({"_id": "twitch:gaules:304"}, {"$set": {"owner": "w-outro"}})
                assert renew_failed.wait(timeout=2.0), "keepalive nao detectou perda durante freeze_and_concat"
            return super().freeze_and_concat(ch, s, e, dst)

    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    pool = StealDuringConcatPool(clock, {"gaules": B + 620})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        out_root,
        queue_root=queue_root,
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="304",
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=0.02,
    )
    res = sup.run()

    assert res["status"] == "lease_lost"
    assert res["jobs"] == []
    assert add_job_calls == []
    assert db.jobs.count_documents({}) == 0
    for state in ("pending", "processing", "done", "rejected"):
        assert list((queue_root / state).iterdir()) == []
    staging = queue_root / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_m07_a03_multi_live_com_uma_lease_perdida_nao_processa_pending_da_perdida(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-multi-lost", now=clock.now, lease_s=120)

    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    pool = FakeMultiPool(clock, {"a_ativa": B + 620, "z_perdida": B + 620})
    processed: list[str] = []
    stole = [False]

    def fake_pipe(job_dir: Path, root: Path):
        processed.append(job_dir.name)
        if job_dir.name.startswith("a_ativa_") and not stole[0]:
            stole[0] = True
            # Outro PC assume z_perdida enquanto a_ativa ainda esta ativa e z_perdida ja tem janela em pending/
            db.lives.update_one({"_id": "twitch:z_perdida:2"}, {"$set": {"owner": "w-outro"}})
            clock.t += 20  # avanca relogio para o proximo beat detectar a perda antes de pegar z_perdida
        return {"clips": []}

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("a_ativa"), acompanhar.parse_alvo("z_perdida")],
        out_root,
        queue_root=queue_root,
        pool=pool,
        pipeline=fake_pipe,
        coord=coord,
        is_live=lambda u: True,
        probe=lambda u: {"id": "1" if "a_ativa" in u else "2", "is_live": True},
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert processed == [f"a_ativa_{B + 300}", f"a_ativa_{B + 600}"]
    assert not any(name.startswith("z_perdida_") for name in processed)
    # Job pendente da sessao com lease perdida fica sem processar em pending/
    assert (queue_root / "pending" / f"z_perdida_{B + 300}").is_dir()
    assert sup._process_one(monitor.DiskQueue(queue_root), fake_pipe) is False

