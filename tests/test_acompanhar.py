"""Unit tests for clipes.acompanhar (T4..T7, T9, T10)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import mongomock
import pytest

from clipes import __main__ as entry, acompanhar, monitor
from clipes.coord import Coord

B = 1_700_000_100  # multiple of 300


class Clock:
    def __init__(self, t: float):
        self.t = float(t)

    def now(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.t += float(d)


class FakeProc:
    def __init__(self, cmd, **kwargs):
        import io

        self.cmd = cmd
        self.kwargs = kwargs
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


class FakeMultiPool:
    """Fake RollingRecorderPool tracking started/stopped channels and segments up to stop_at[ch]."""

    def __init__(self, clock: Clock, stop_at: dict[str, float], *, fail_on: set[str] | None = None):
        self.clock = clock
        self.stop_at = stop_at
        self.fail_on = fail_on or set()
        self.started_order: list[str] = []
        self.active: set[str] = set()
        self.stopped: set[str] = set()
        self.first: dict[str, int] = {}
        self.enforce_caps: list[int] = []
        self.stop_all_calls = 0

    def start_on_demand(self, ch: str) -> bool:
        if ch in self.fail_on:
            return False
        self.started_order.append(ch)
        self.active.add(ch)
        self.first[ch] = int(self.clock.t) // 10 * 10
        return True

    def release_on_demand(self, ch: str) -> None:
        self.active.discard(ch)
        self.stopped.add(ch)

    def stop_channel(self, ch: str) -> None:
        self.release_on_demand(ch)

    def is_recording(self, ch: str) -> bool:
        return ch in self.active and self.clock.t < self.stop_at[ch]

    def segments(self, ch: str):
        if ch not in self.first:
            return []
        last = min(self.clock.t, self.stop_at[ch]) - 10
        return [(e, None) for e in range(self.first[ch], int(last) + 1, 10)]

    def video_start_ts(self, ch: str, s: float, e: float) -> float:
        return float(max(self.first[ch], int(s) // 10 * 10))

    def freeze_and_concat(self, ch: str, s: float, e: float, dst: Path) -> Path:
        dst.write_bytes(b"x")
        return dst

    def hold(self, ch: str, ts: float) -> None:
        pass

    def release_hold(self, ch: str) -> None:
        pass

    def cleanup(self, now: float) -> None:
        pass

    def enforce_cap(self, max_bytes: int) -> None:
        self.enforce_caps.append(max_bytes)

    def stop_all(self) -> None:
        self.stop_all_calls += 1
        for ch in list(self.active):
            self.release_on_demand(ch)


# ---- DUR-02 (live): CLIPES_DUR_MAX / CLIPES_DUR_MIN lidos ao abrir a sessao -----------------------


def _roda_live_e_le_jobs(tmp_path, monkeypatch) -> list[dict]:
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    jobs: list[dict] = []

    def fake_pipe(job_dir: Path, root: Path):
        jobs.append(json.loads((job_dir / "job.json").read_text()))
        return {"clips": []}

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=FakeMultiPool(clock, {"gaules": B + 620}),
        pipeline=fake_pipe,
        is_live=lambda u: clock.t < B + 620,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    sup.run()
    return jobs


def test_live_usa_clipes_dur_max_do_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_DUR_MAX", "40")
    monkeypatch.delenv("CLIPES_DUR_MIN", raising=False)
    jobs = _roda_live_e_le_jobs(tmp_path, monkeypatch)
    assert jobs
    assert all(j["max_dur"] == 40 for j in jobs)
    assert all(j["min_dur"] == 15 for j in jobs)


def test_live_sem_env_usa_max_45_e_min_15(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_DUR_MAX", raising=False)
    monkeypatch.delenv("CLIPES_DUR_MIN", raising=False)
    jobs = _roda_live_e_le_jobs(tmp_path, monkeypatch)
    assert jobs
    assert all(j["max_dur"] == 45 and j["min_dur"] == 15 for j in jobs)


# ---- T4: parse_alvo & dedup ----------------------------------------------------------------------


def test_parse_alvo_aceita_cinco_formatos_rejeita_invalido_e_deduplica():
    a1 = acompanhar.parse_alvo("Gaules")
    a2 = acompanhar.parse_alvo("twitch.tv/gaules")
    a3 = acompanhar.parse_alvo("https://www.youtube.com/@MeuCanal/live")
    a4 = acompanhar.parse_alvo("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    a5 = acompanhar.parse_alvo("https://youtu.be/AbC123_xyz")

    assert a1.plataforma == "twitch" and a1.chave == "gaules"
    assert a2.plataforma == "twitch" and a2.chave == "gaules"
    assert a3.plataforma == "youtube" and a3.chave == "yt_meucanal" and a3.canal == "meucanal"
    assert a4.plataforma == "youtube" and a4.chave == "yt_dQw4w9WgXcQ" and a4.video_id == "dQw4w9WgXcQ"
    assert a5.plataforma == "youtube" and a5.chave == "yt_AbC123_xyz" and a5.video_id == "AbC123_xyz"

    assert len(acompanhar.dedup_alvos([a1, a2, a4, a5])) == 3

    for bad in ["", "   ", "canal invalido!", "https://example.com/live", "https://twitch.tv/videos/123", "../etc", "youtube.com/watch"]:
        with pytest.raises(ValueError):
            acompanhar.parse_alvo(bad)


# ---- T5 + T6: Supervisor multi-live + 1 worker + Twitch & YouTube --------------------------------


def test_supervisor_duas_lives_twitch_e_youtube_nunca_dois_em_processing(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

    popen_procs: list[FakeProc] = []

    def fake_popen(cmd, **kwargs):
        p = FakeProc(cmd, **kwargs)
        popen_procs.append(p)
        return p

    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    alvos = [
        acompanhar.parse_alvo("gaules"),
        acompanhar.parse_alvo("https://www.youtube.com/@CanalYT/live"),
    ]

    # Use a real RollingRecorderPool wired to FakePopen, plus segments written on disk per channel
    # by testing both command generation and full Supervisor execution.
    probe_calls: list[str] = []

    def fake_probe(url: str):
        probe_calls.append(url)
        return {"id": "ytVid99", "channel_id": "UC99", "uploader_id": "@CanalYT", "is_live": True}

    pool = FakeMultiPool(clock, {"gaules": B + 620, "yt_ytVid99": B + 920})
    in_flight_max = [0]
    processed: list[tuple[str, bool]] = []

    def fake_pipe(job_dir: Path, root: Path):
        in_flight = len(list((queue_root / "processing").iterdir()))
        in_flight_max[0] = max(in_flight_max[0], in_flight)
        processed.append((job_dir.name, (job_dir / "chat.json").exists()))
        job = json.loads((job_dir / "job.json").read_text())
        vs, e = job["video_start"], job["window_end"]
        center = e - 100
        return {"clips": [{"file": "c.mp4", "score": 1.0, "start": center - 20 - vs, "end": center + 20 - vs}]}

    sup = acompanhar.Supervisor(
        alvos,
        out_root,
        queue_root=queue_root,
        pool=pool,
        pipeline=fake_pipe,
        is_live=lambda u: clock.t < (B + 620 if "gaules" in u else B + 920),
        probe=fake_probe,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert in_flight_max[0] == 1  # never 2 jobs in processing at the same time
    assert any(name.startswith("gaules_") for name, _ in processed)
    assert any(name.startswith("yt_ytVid99_") and not has_chat for name, has_chat in processed)
    assert "https://www.youtube.com/@CanalYT/live" in probe_calls

    # Also verify real RollingRecorderPool source_cmd selection for Twitch vs YouTube
    cmd_map = {a.chave: a for a in alvos}
    real_pool = monitor.RollingRecorderPool(
        tmp_path / "buf_cmd",
        max_hot=0,
        max_on_demand=2,
        popen=fake_popen,
        source_cmd=lambda k: monitor.ytdlp_live_cmd(cmd_map[k].url) if cmd_map[k].plataforma == "youtube" else monitor.streamlink_cmd(k),
    )
    assert real_pool.start_on_demand("gaules")
    assert real_pool.start_on_demand("yt_ytVid99")
    cmds = [p.cmd for p in popen_procs if p.cmd[0] != "ffmpeg"]
    assert cmds[0][0] == "streamlink"
    assert cmds[1][0] == "yt-dlp" and cmds[1][-1] == "https://www.youtube.com/watch?v=ytVid99"


def test_supervisor_youtube_policy_por_handle_e_channel_id(tmp_path, monkeypatch, capsys):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

    pool = FakeMultiPool(clock, {"yt_vid_one": B + 320, "yt_vid_two": B + 320})
    sup = acompanhar.Supervisor(
        [
            acompanhar.parse_alvo("https://www.youtube.com/watch?v=vid_one"),
            acompanhar.parse_alvo("https://youtu.be/vid_two"),
        ],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: clock.t < B + 320,
        probe=lambda u: (
            {"id": "vid_one", "channel_id": "UC1", "uploader_id": "@BloqueadoYT", "is_live": True}
            if "vid_one" in u
            else {"id": "vid_two", "channel_id": "UC_Perm", "is_live": True}
        ),
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        policy=monitor.ChannelPolicy(blocked={"bloqueadoyt"}),
    )
    res = sup.run()
    out = capsys.readouterr().out
    assert res["status"] == "ended"
    assert "bloqueado pela policy" in out
    assert pool.started_order == ["yt_vid_two"]


def test_live_session_backlog_lost_avisa_discord(tmp_path, monkeypatch, capsys):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_MAX_BACKLOG", "2")  # limite antigo: força a perda com poucas janelas
    avisos: list[str] = []
    pool = FakeMultiPool(clock, {"gaules": B + 2500})
    calls: list[str] = []

    def slow_pipe(d, o):
        if not calls:
            clock.t += 1000
        calls.append(d.name)
        return {"clips": []}

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=slow_pipe,
        is_live=lambda u: clock.t < B + 2500,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        aviso_fn=avisos.append,
    )
    sup.run()
    assert any(a.startswith("janela perdida gaules (fila cheia, score ") for a in avisos)


# ---- T7: Limite, aguardando, recusa visível, offline, policy, recorder error ----------------------


def test_tres_alvos_limite_2_recusa_com_1_aviso_e_promove_ao_encerrar(tmp_path, monkeypatch, capsys):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=2")

    avisos: list[str] = []
    stops = {"c1": B + 320, "c2": B + 620, "c3": B + 920}
    pool = FakeMultiPool(clock, stops)
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("c1"), acompanhar.parse_alvo("c2"), acompanhar.parse_alvo("c3")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: clock.t < stops[u.rsplit("/", 1)[-1]],
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        aviso_fn=avisos.append,
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert pool.started_order == ["c1", "c2", "c3"]  # c3 started only after c1 ended
    assert len(avisos) == 1
    expected = "[acompanhar] recusada c3: limite slots twitch=2 (defina CLIPES_SLOTS)"
    assert avisos[0] == expected
    assert expected in capsys.readouterr().out


def test_slots_por_plataforma_permite_twitch_kick_youtube_e_bloqueia_zero(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.delenv("CLIPES_SLOTS", raising=False)

    peak_active = [0]

    class PeakPool(FakeMultiPool):
        def start_on_demand(self, ch: str) -> bool:
            ok = super().start_on_demand(ch)
            peak_active[0] = max(peak_active[0], len(self.active))
            return ok

    alvos = [
        acompanhar.parse_alvo("tw1"),
        acompanhar.Alvo(plataforma="kick", chave="kick_k1", url="https://kick.com/k1", canal="k1", raw="kick.com/k1"),
        acompanhar.parse_alvo("https://www.youtube.com/watch?v=ytVid01"),
        acompanhar.parse_alvo("tw2"),
    ]
    stops = {"tw1": B + 320, "kick_k1": B + 320, "yt_ytVid01": B + 320, "tw2": B + 620}
    pool = PeakPool(clock, stops)
    avisos: list[str] = []
    sup = acompanhar.Supervisor(
        alvos,
        tmp_path / "out_sim",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: clock.t < (B + 620 if "tw2" in u else B + 320),
        probe=lambda u: (
            {"is_live": True, "id": "123"}
            if "kick.com" in u
            else {"id": "ytVid01", "channel_id": "UC1", "is_live": True}
        ),
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        aviso_fn=avisos.append,
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert peak_active[0] == 3  # 1 Twitch + 1 Kick + 1 YouTube simultaneas
    assert set(pool.started_order[:3]) == {"tw1", "kick_k1", "yt_ytVid01"}
    assert pool.started_order[3] == "tw2"
    assert avisos == ["[acompanhar] recusada tw2: limite slots twitch=1 (defina CLIPES_SLOTS)"]

    # Plataforma com slot=0 e bloqueada sem impedir outra plataforma
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=0,kick=1,youtube=0,busca=0")
    clock2 = Clock(B + 10)
    pool2 = FakeMultiPool(clock2, {"tw1": B + 320, "kick_k1": B + 320})
    def fake_probe2(url: str):
        if "kick.com" in url:
            return {"is_live": True, "id": "123"}
        return {"is_live": True, "id": "tw9"}
    sup2 = acompanhar.Supervisor(
        [
            acompanhar.parse_alvo("tw1"),
            acompanhar.Alvo(plataforma="kick", chave="kick_k1", url="https://kick.com/k1", canal="k1"),
        ],
        tmp_path / "out_zero",
        pool=pool2,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: clock2.t < B + 320,
        probe=fake_probe2,
        now=clock2.now,
        sleep=clock2.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        aviso_fn=lambda _: None,
    )
    res2 = sup2.run()
    assert res2["status"] == "ended"
    assert pool2.started_order == ["kick_k1"]

    # Fallback de limite_fn injetada em testes
    sup3 = acompanhar.Supervisor([], tmp_path / "out_fn", limite_fn=lambda: 5)
    assert sup3._limite("twitch") == 5


def test_clipes_max_lives_invalido_cai_em_perfil_max_lives(tmp_path, monkeypatch):
    out_root = tmp_path / "out"
    out_root.mkdir()
    (out_root / ".perfil.json").write_text(json.dumps({"razao": 0.25, "cpus": 4}), encoding="utf-8")

    for bad in ["0", "-2", "abc", ""]:
        monkeypatch.setenv("CLIPES_MAX_LIVES", bad)
        assert acompanhar.resolve_max_lives(out_root, cpus=4) == 2

    monkeypatch.setenv("CLIPES_MAX_LIVES", "3")
    assert acompanhar.resolve_max_lives(out_root, cpus=4) == 3


def test_alvos_offline_bloqueado_e_erro_ao_gravar(tmp_path, monkeypatch, capsys):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "4")

    avisos: list[str] = []
    pool = FakeMultiPool(clock, {"ok": B + 320, "failstart": B + 320, "off": B + 320, "blk": B + 320}, fail_on={"failstart"})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("off"), acompanhar.parse_alvo("blk"), acompanhar.parse_alvo("failstart"), acompanhar.parse_alvo("ok")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: "off" not in u and clock.t < B + 320,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        policy=monitor.ChannelPolicy(blocked={"blk"}),
        aviso_fn=avisos.append,
    )
    res = sup.run()
    out = capsys.readouterr().out

    assert res["status"] == "ended"
    assert pool.started_order == ["ok"]
    assert "[acompanhar] off offline; ignorado" in out
    assert "[acompanhar] blk bloqueado pela policy" in out
    assert "[acompanhar] erro ao gravar failstart" in out
    assert any("failstart" in a for a in avisos)


def test_ctrl_c_para_gravadores_e_nao_deixa_staging(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=2")

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
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    with pytest.raises(KeyboardInterrupt):
        sup.run()

    assert pool.active == set()
    staging = tmp_path / "out" / "queue" / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


# ---- T9: log de not_claimed ----------------------------------------------------------------------


def test_follow_live_e_monitor_logam_not_claimed(tmp_path, capsys):
    clock = Clock(B + 10)
    pool = FakeMultiPool(clock, {"canal": B + 320})
    res = monitor.follow_live(
        "canal",
        tmp_path / "out_fl",
        pool=pool,
        coord=object(),
        stream_id="1",
        is_live=lambda _: True,
        now=clock.now,
        sleep=clock.sleep,
        capacity_fn=lambda _: {"cpus": 2, "score": 2},
        max_lives_fn=lambda *_: 0,
    )
    assert res == {"status": "not_claimed", "jobs": []}
    assert "[live] nao acompanhada canal:" in capsys.readouterr().out

    # Also verify monitor.run_monitor._claim_live logs `[live] nao acompanhada` when sid is missing
    clock_m = Clock(B)

    async def irc(channels, on_message, stop):
        for i in range(12):
            on_message("canal", clock_m.t + 70, f"u{i % 6}", "KKKK")
        clock_m.t += 70
        await asyncio.sleep(0.05)
        stop.set()

    fake_coord = type(
        "C",
        (),
        {
            "worker_id": "w-test",
            "ensure_indexes": lambda *a: None,
            "ensure_live": lambda *a, **k: None,
            "claim_live": lambda *a: False,
            "count_owned_live": lambda *a, **k: 0,
            "release_expired_own": lambda *a: 0,
            "beat": lambda *a, **k: None,
            "renew": lambda *a: True,
            "pending_command": lambda *a: None,
            "run_pending_command": lambda *a, **k: None,
        },
    )()
    asyncio.run(monitor.run_monitor(
        ["canal"],
        tmp_path / "out_mon",
        monitor.ChannelPolicy(),
        irc=irc,
        popen=lambda *a, **kw: FakeProc(*a, **kw),
        now=clock_m.now,
        sleep=lambda d: asyncio.sleep(0),
        coord=fake_coord,
        probe=lambda u: {},
        capacity_fn=lambda _: {"cpus": 2, "score": 30},
        max_lives_fn=lambda *_: 1,
    ))
    assert "[live] nao acompanhada canal: stream_id indisponivel" in capsys.readouterr().out


# ---- T10: CLI subcommand, invalid -> 2, busy lock -> 1, no pymongo without URI -------------------


def test_main_acompanhar_rota_alvo_invalido_e_lock_ocupado(tmp_path, monkeypatch, capsys):
    called: list[list[str]] = []
    monkeypatch.setattr(acompanhar, "main", lambda argv: (called.append(argv), 0)[1])
    assert entry.main(["servico", "gaules", "--max-dur", "45"]) == 0
    assert called == [["gaules", "--max-dur", "45"]]

    monkeypatch.undo()

    # Invalid target exits 2 before touching queue/recorders
    rc_bad = acompanhar.main(["canal invalido!", "--out-dir", str(tmp_path / "bad")])
    assert rc_bad == 2
    assert "[acompanhar] alvo invalido: canal invalido!" in capsys.readouterr().out
    assert not (tmp_path / "bad" / "queue").exists()

    # Busy .worker.lock exits 1 with _lock_worker message
    queue_dir = tmp_path / "q_busy"
    queue_dir.mkdir(parents=True)
    lock = monitor._lock_worker(queue_dir)
    try:
        rc_lock = acompanhar.main(["gaules", "--out-dir", str(tmp_path / "o"), "--queue-dir", str(queue_dir)])
    finally:
        lock.close()
    assert rc_lock == 1
    assert "another monitor worker already holds" in capsys.readouterr().out

    # Without CLIPES_MONGO_URI, pymongo is never imported
    import os

    script = (
        "import sys, tempfile\n"
        "from clipes import __main__ as entry\n"
        "with tempfile.TemporaryDirectory() as td:\n"
        "    rc = entry.main(['servico', 'invalido!', '--out-dir', td])\n"
        "assert rc == 2 and 'pymongo' not in sys.modules\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "CLIPES_MONGO_URI"}
    res = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr + res.stdout


def test_erro_tick_ou_encerrar_remove_so_sessao_com_falha_e_mantem_outra_viva(tmp_path, monkeypatch, capsys):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=2")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-isol", now=clock.now, lease_s=300)

    class FailingPool(FakeMultiPool):
        def freeze_and_concat(self, ch: str, s: float, e: float, dst: Path) -> Path:
            if ch == "canal_a":
                raise RuntimeError("ffmpeg concat failed")
            return super().freeze_and_concat(ch, s, e, dst)

    pool = FailingPool(clock, {"canal_a": B + 620, "canal_b": B + 620})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("canal_a"), acompanhar.parse_alvo("canal_b")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=lambda u: clock.t < B + 620,
        probe=lambda u: {"id": "a1" if "canal_a" in u else "b1", "is_live": True},
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()
    out = capsys.readouterr().out

    assert res["status"] == "ended"
    assert "[acompanhar]" in out and "canal_a" in out
    assert "canal_a" in pool.stopped
    assert "canal_a" not in pool.active
    assert db.lives.find_one({"_id": "twitch:canal_a:a1"})["owner"] is None
    assert db.lives.find_one({"_id": "twitch:canal_b:b1"})["status"] == "ended"
    assert any(j.startswith("canal_b_") for j in res["jobs"])


def test_sigterm_sighup_para_gravadores_e_enforce_cap_no_ciclo(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

    # 1. Normal run invokes enforce_cap(BUFFER_CAP_BYTES) and stop_all()
    pool_ok = FakeMultiPool(clock, {"c1": B + 320})
    sup_ok = acompanhar.Supervisor(
        [acompanhar.parse_alvo("c1")],
        tmp_path / "out_ok",
        pool=pool_ok,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: clock.t < B + 320,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    assert sup_ok.run()["status"] == "ended"
    assert monitor.BUFFER_CAP_BYTES in pool_ok.enforce_caps
    assert pool_ok.stop_all_calls >= 1

    # 2. SIGTERM raises KeyboardInterrupt, stops pool, restores previous handler
    prev_term = signal.getsignal(signal.SIGTERM)
    clock_sig = Clock(B + 10)

    class SigPool(FakeMultiPool):
        def segments(self, ch):
            if self.clock.t >= B + 30:
                os.kill(os.getpid(), signal.SIGTERM)
            return super().segments(ch)

    pool_sig = SigPool(clock_sig, {"c1": B + 600})
    sup_sig = acompanhar.Supervisor(
        [acompanhar.parse_alvo("c1")],
        tmp_path / "out_sig",
        pool=pool_sig,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: True,
        now=clock_sig.now,
        sleep=clock_sig.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    with pytest.raises(KeyboardInterrupt):
        sup_sig.run()

    assert pool_sig.stop_all_calls >= 1
    assert pool_sig.active == set()
    assert signal.getsignal(signal.SIGTERM) is prev_term

    # 3. SIGHUP in main() returns 0 cleanly and restores previous handler
    if hasattr(signal, "SIGHUP"):
        prev_hup = signal.getsignal(signal.SIGHUP)

        class HupSup:
            def __init__(self, *a, **kw):
                pass

            def run(self):
                os.kill(os.getpid(), signal.SIGHUP)
                return {"status": "ended"}

        monkeypatch.setattr(acompanhar, "Supervisor", HupSup)
        rc = acompanhar.main(["c1", "--out-dir", str(tmp_path / "out_hup")])
        assert rc == 0
        assert signal.getsignal(signal.SIGHUP) is prev_hup


def test_main_alvos_vazio_exige_mongo_e_com_mongo_roda_modo_servico(tmp_path, monkeypatch, capsys):
    # Sem CLIPES_MONGO_URI, 1+ alvos obrigatorio (fallback)
    monkeypatch.delenv("CLIPES_MONGO_URI", raising=False)
    rc_no_mongo = acompanhar.main(["--out-dir", str(tmp_path / "o1")])
    assert rc_no_mongo == 2
    assert "CLIPES_MONGO_URI" in capsys.readouterr().err

    # Com CLIPES_MONGO_URI, alvos vazio e aceito e passado ao Supervisor com coord
    captured: list[tuple[list, object]] = []

    class FakeSup:
        def __init__(self, alvos, out_root, **kw):
            captured.append((alvos, kw.get("coord")))

        def run(self):
            return {"status": "ended"}

    fake_coord = object()
    monkeypatch.setenv("CLIPES_MONGO_URI", "mongodb://fake/db")
    monkeypatch.setattr(monitor, "_connect_coord_for_follow", lambda out_root: fake_coord)
    monkeypatch.setattr(acompanhar, "Supervisor", FakeSup)
    rc_mongo = acompanhar.main(["--out-dir", str(tmp_path / "o2")])
    assert rc_mongo == 0
    assert captured == [([], fake_coord)]


def test_main_servico_avisa_inicio_e_parada_no_log(tmp_path, monkeypatch):
    avisos: list[str] = []

    class FakeSup:
        def __init__(self, *a, **kw):
            pass

        def run(self):
            avisos.append("run")
            return {"status": "ended"}

    monkeypatch.setattr(acompanhar, "Supervisor", FakeSup)
    monkeypatch.setattr(acompanhar, "post_aviso", lambda texto: avisos.append(texto))
    rc = acompanhar.main(["c1", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    assert avisos == ["servico iniciado", "run", "servico parado"]


def test_main_servico_falha_ao_postar_aviso_nao_derruba_servico(tmp_path, monkeypatch, capsys):
    rodou: list[bool] = []

    class FakeSup:
        def __init__(self, *a, **kw):
            pass

        def run(self):
            rodou.append(True)
            return {"status": "ended"}

    def aviso_quebrado(texto):
        raise RuntimeError("discord fora")

    monkeypatch.setattr(acompanhar, "Supervisor", FakeSup)
    monkeypatch.setattr(acompanhar, "post_aviso", aviso_quebrado)
    rc = acompanhar.main(["c1", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    assert rodou == [True]
    assert "falha ao postar aviso (RuntimeError)" in capsys.readouterr().out


def test_main_servico_avisa_parada_mesmo_com_erro_no_run(tmp_path, monkeypatch):
    avisos: list[str] = []

    class FakeSup:
        def __init__(self, *a, **kw):
            pass

        def run(self):
            raise RuntimeError("falha no supervisor")

    monkeypatch.setattr(acompanhar, "Supervisor", FakeSup)
    monkeypatch.setattr(acompanhar, "post_aviso", lambda texto: avisos.append(texto))
    rc = acompanhar.main(["c1", "--out-dir", str(tmp_path / "out")])
    assert rc == 1
    assert avisos == ["servico iniciado", "servico parado"]


# ---- controle-discord T6: fila compartilhada (janela + busca) e thread da busca geral -----------


def test_t6_fila_compartilhada_ordena_busca_e_janela_por_z_score_e_preserva_lease_perdida(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_SLOTS", raising=False)
    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    queue = monitor.DiskQueue(queue_root)

    def seed(jid: str, data: dict) -> None:
        d = queue.root / ".staging" / jid
        d.mkdir(parents=True, exist_ok=True)
        (d / "source.mkv").write_bytes(b"v")
        (d / "meta.json").write_text(json.dumps({"id": jid, "duration": 60.0}), encoding="utf-8")
        monitor._write_job(d, {"job_id": jid, "status": "pending", **data})
        queue.enqueue(d)

    # Janela de sessao ativa (z_score=0), 2 jobs da busca (z_score 3.2 e 7.5), e janela de sessao perdida
    seed("ativa_100", {
        "channel": "ativa", "session_key": "ativa", "z_score": 0.0, "created_at": 1.0,
        "window_start": 0.0, "window_end": 600.0, "video_start": 0.0, "last": False,
    })
    seed("busca_low", {"channel": "c_low", "origem": "busca", "z_score": 3.2, "created_at": 2.0})
    seed("busca_high", {"channel": "c_high", "origem": "busca", "z_score": 7.5, "created_at": 3.0})
    seed("perdida_100", {
        "channel": "perdida", "session_key": "perdida", "z_score": 9.9, "created_at": 0.5,
        "window_start": 0.0, "window_end": 600.0, "video_start": 0.0, "last": False,
    })

    sup = acompanhar.Supervisor([], out_root, queue_root=queue_root)
    sess_ok = acompanhar.LiveSession(
        acompanhar.parse_alvo("ativa"), out_root, queue, None, lambda d, r: {"clips": []},
        is_live=lambda u: True, now=lambda: 0.0, disk_usage=lambda p: SimpleNamespace(free=1e12),
    )
    sess_lost = acompanhar.LiveSession(
        acompanhar.parse_alvo("perdida"), out_root, queue, None, lambda d, r: {"clips": []},
        is_live=lambda u: True, now=lambda: 0.0, disk_usage=lambda p: SimpleNamespace(free=1e12),
    )
    sess_lost.lease_lost = True
    sup._all_sessions["ativa"] = sess_ok
    sup._all_sessions["perdida"] = sess_lost

    ran: list[str] = []

    def pipe(job_dir: Path, root: Path):
        ran.append(job_dir.name)
        return {"clips": []}

    sess_ok.pipeline_fn = pipe

    assert sup._has_own_pending(queue, skip_lost=True) is True
    while sup._has_own_pending(queue, skip_lost=True):
        assert sup._process_one(queue, pipe, skip_lost=True) is True

    assert ran == ["busca_high", "busca_low", "ativa_100"]
    assert (queue_root / "pending" / "perdida_100").is_dir()


def test_t6_busca_zero_nao_abre_irc_e_busca_positiva_inicia_e_encerra_limpo(tmp_path, monkeypatch):
    import threading

    channels_file = tmp_path / "channels.txt"
    channels_file.write_text("gaules\n", encoding="utf-8")

    # 1) busca=0 nao inicia thread IRC
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=1,kick=0,youtube=0,busca=0")
    irc_calls: list[list[str]] = []

    async def fake_irc(chans, on_msg, stop):
        irc_calls.append(list(chans))
        await stop.wait()

    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    pool = FakeMultiPool(clock, {"alanzoka": B + 320})
    sup0 = acompanhar.Supervisor(
        [acompanhar.parse_alvo("alanzoka")],
        tmp_path / "out0",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: clock.t < B + 320,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        channels_file=channels_file,
        busca_irc=fake_irc,
        busca_popen=lambda *a, **k: FakeProc(*a, **k),
    )
    assert sup0.run()["status"] == "ended"
    assert irc_calls == []

    # 2) busca=1 inicia thread da busca e encerra limpo ao parar o Supervisor
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=1,kick=0,youtube=0,busca=1")
    irc_started = threading.Event()
    irc_stopped = threading.Event()

    async def fake_irc_track(chans, on_msg, stop):
        irc_calls.append(list(chans))
        irc_started.set()
        await stop.wait()
        irc_stopped.set()

    stop_sup = threading.Event()
    clock2 = Clock(B + 10)

    def sleep2(d: float) -> None:
        clock2.sleep(d)
        if irc_started.wait(timeout=0.05):
            stop_sup.set()

    sup1 = acompanhar.Supervisor(
        [],
        tmp_path / "out1",
        pool=FakeMultiPool(clock2, {}),
        pipeline=lambda d, o: {"clips": []},
        now=clock2.now,
        sleep=sleep2,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        channels_file=channels_file,
        busca_irc=fake_irc_track,
        busca_popen=lambda *a, **k: FakeProc(*a, **k),
        stop_event=stop_sup,
    )
    sup1.run()
    assert irc_calls == [["gaules"]]
    assert irc_stopped.is_set()


def test_parse_alvo_kick_aceita_canal_normalizado_e_rejeita_invalidos():
    a = acompanhar.parse_alvo("https://www.kick.com/Foo-Bar/")
    assert a == acompanhar.Alvo(
        plataforma="kick",
        chave="kick_foo-bar",
        url="https://kick.com/foo-bar",
        canal="foo-bar",
        raw="https://www.kick.com/Foo-Bar/",
    )

    b = acompanhar.parse_alvo("http://kick.com/foo-bar")
    assert b.plataforma == "kick" and b.chave == "kick_foo-bar" and b.url == "https://kick.com/foo-bar"
    assert len(acompanhar.dedup_alvos([a, b])) == 1

    # Mesmo slug em Twitch e Kick nao colide no dedup; palavra solta continua Twitch
    tw = acompanhar.parse_alvo("gaules")
    kk = acompanhar.parse_alvo("https://kick.com/gaules")
    assert tw.plataforma == "twitch" and tw.chave == "gaules"
    assert kk.plataforma == "kick" and kk.chave == "kick_gaules"
    assert len(acompanhar.dedup_alvos([tw, kk])) == 2

    invalidos = [
        "https://kick.com/a/videos/01a1f270-2818-7960-a98a-0ceb2ff97b57",
        "https://kick.com/a/b",
        "https://kick.com/video/01a1f270-2818-7960-a98a-0ceb2ff97b57",
        "https://kick.com/search",
        "https://kick.com/clips",
        "https://kick.com/videos",
        "https://kick.com/categories",
        "https://kick.com/auth",
        "https://kick.com/video",
        "https://kick.com/",
        "https://kick.com/" + "a" * 26,
        "https://kick.com/foo.bar",
    ]
    for bad in invalidos:
        with pytest.raises(ValueError) as exc_info:
            acompanhar.parse_alvo(bad)
        assert bad in str(exc_info.value)


# ---- T5 + T6: Supervisor multi-live + 1 worker + Twitch & YouTube --------------------------------


@pytest.mark.parametrize(
    "probe_behavior",
    [
        lambda _u: (_ for _ in ()).throw(RuntimeError("UserNotLive")),
        lambda _u: None,
        lambda _u: "nao-dict",
        lambda _u: {"is_live": False, "id": 123},
        lambda _u: {"is_live": None, "id": 123},
        lambda _u: {"is_live": 1, "id": 123},
        lambda _u: {"id": 123},
    ],
)
def test_supervisor_kick_probe_offline_nao_grava(tmp_path, monkeypatch, capsys, probe_behavior):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

    pool = FakeMultiPool(clock, {"kick_foo-bar": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("https://kick.com/foo-bar")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        probe=probe_behavior,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    res = sup.run()
    out = capsys.readouterr().out

    assert res["status"] == "ended"
    assert pool.started_order == []
    assert "offline; ignorado" in out


def test_supervisor_kick_probe_popula_ids_e_promocao_nao_reprobra(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    kick_alvo = acompanhar.parse_alvo("https://www.kick.com/Foo-Bar/")
    kick_probe_calls: list[str] = []

    def fake_probe(url: str):
        kick_probe_calls.append(url)
        return {"is_live": True, "id": 123, "channel_id": "9"}

    stops = {"c1": B + 320, "kick_foo-bar": B + 620}
    pool = FakeMultiPool(clock, stops)
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("c1"), kick_alvo],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: clock.t < (B + 320 if "twitch" in u else B + 620),
        probe=fake_probe,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert kick_alvo.video_id == "123" and isinstance(kick_alvo.video_id, str)
    assert kick_alvo.channel_id == "9"
    assert kick_alvo.url == "https://kick.com/foo-bar"
    assert kick_alvo.chave == "kick_foo-bar"
    assert pool.started_order == ["c1", "kick_foo-bar"]
    assert kick_probe_calls == ["https://kick.com/foo-bar"]


def test_supervisor_kick_grava_via_ytdlp_e_twitch_segue_em_streamlink(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

    spawned: list[FakeProc] = []

    def fake_popen(cmd, **kwargs):
        proc = FakeProc(cmd, **kwargs)
        spawned.append(proc)
        return proc

    orig_pool_cls = monitor.RollingRecorderPool
    monkeypatch.setattr(
        monitor,
        "RollingRecorderPool",
        lambda *a, **kw: orig_pool_cls(*a, popen=fake_popen, **kw),
    )

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-kick-cmd", now=clock.now, lease_s=90)

    def fake_probe(url: str):
        if "kick.com" in url:
            return {"is_live": True, "id": "123"}
        return {"is_live": True, "id": "tw9"}

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("https://kick.com/foo-bar"), acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=lambda _u: clock.t < B + 100,
        probe=fake_probe,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "ended"
    sources = [p.cmd for p in spawned if p.cmd[0] != "ffmpeg"]
    kick_cmds = [c for c in sources if "https://kick.com/foo-bar" in c]
    twitch_cmds = [c for c in sources if "twitch.tv/gaules" in c]
    assert len(kick_cmds) == 1
    assert kick_cmds[0][0] == "yt-dlp" and "--hls-use-mpegts" in kick_cmds[0]
    assert len(twitch_cmds) == 1
    assert twitch_cmds[0][0] == "streamlink"
    assert db.lives.find_one({"_id": "kick:foo-bar:123"})["status"] == "ended"
    assert db.lives.find_one({"_id": "twitch:gaules:tw9"})["status"] == "ended"


# ---- ajustes-nov20 T3: uma sonda por tick (REV-06) e limpeza de _cancelled_vod_ids (REV-08) ----


class _CoordStub:
    def __init__(self):
        self.promovidos: list[tuple[str, str]] = []

    def promover_para_vod(self, alvo_id, url):
        self.promovidos.append((alvo_id, url))
        return "promovido"

    def renew_alvos(self, fases):
        return set(fases)

    def ensure_live(self, *a, **kw):
        pass

    def claim_live(self, *a, **kw):
        return False


def _tick_gerenciado(tmp_path, meta, coord=None):
    coord = coord or _CoordStub()
    calls: list[str] = []

    def probe(url):
        calls.append(url)
        return meta

    sup = acompanhar.Supervisor(
        [],
        tmp_path / "out",
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        is_live=lambda _u: True,
        probe=probe,
        now=lambda: B,
        sleep=lambda _s: None,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    entrada = "https://www.youtube.com/watch?v=AbC123"
    alvo = acompanhar.parse_alvo(entrada)
    sup._managed_alvos["a1"] = acompanhar._ManagedAlvo(
        alvo_id="a1", alvo=alvo, entrada=entrada, plataforma="youtube", auto_vod=True
    )
    queue = monitor.DiskQueue(tmp_path / "q")
    sup._tick_managed_alvos(B, 5.0, queue, None, lambda d, o: {}, lambda _u: True, probe)
    return sup, coord, calls


def test_tick_gerenciado_live_sonda_uma_vez(tmp_path):
    sup, coord, calls = _tick_gerenciado(tmp_path, {"id": "AbC123", "is_live": True, "live_status": "is_live"})
    assert len(calls) == 1
    assert coord.promovidos == []


def test_tick_gerenciado_offline_sonda_uma_vez(tmp_path):
    sup, coord, calls = _tick_gerenciado(tmp_path, {"id": "AbC123", "is_live": False, "live_status": "is_upcoming"})
    assert len(calls) == 1
    assert coord.promovidos == []


def test_tick_gerenciado_gravado_sonda_uma_vez_e_promove(tmp_path):
    sup, coord, calls = _tick_gerenciado(tmp_path, {"id": "AbC123", "is_live": False, "live_status": "was_live"})
    assert len(calls) == 1
    assert coord.promovidos == [("a1", "https://www.youtube.com/watch?v=AbC123")]
    assert "a1" not in sup._managed_alvos


def test_process_one_vod_limpa_cancelled_vod_ids_no_sucesso(tmp_path):
    class C(_CoordStub):
        def pegar_vod(self, alias):
            return {"_id": "v1", "url": "https://www.youtube.com/watch?v=AbC123"}

        def fim_vod(self, *a, **kw):
            pass

    sup = acompanhar.Supervisor(
        [],
        tmp_path / "out",
        pipeline=lambda d, o: {"clips": []},
        coord=C(),
        now=lambda: B,
        sleep=lambda _s: None,
        vod_pipeline=lambda url, out: sup._cancelled_vod_ids.add("v1") or 0,
    )
    sup._check_vod_policy = lambda vod, url, probe: None
    assert sup._process_one_vod("w", lambda u: {}) is True
    assert sup._cancelled_vod_ids == set()


def _vod_ssrf(tmp_path, monkeypatch, ip):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 0))])
    fins, runs = [], []

    class C(_CoordStub):
        def pegar_vod(self, alias):
            return {"_id": "v1", "url": "https://exemplo.test/v"}

        def fim_vod(self, *a, **kw):
            fins.append((a, kw))

    sup = acompanhar.Supervisor(
        [], tmp_path / "out", pipeline=lambda d, o: {"clips": []}, coord=C(),
        now=lambda: B, sleep=lambda _s: None,
        vod_pipeline=lambda url, out: runs.append(url) or 0,
    )
    sup._process_one_vod("w", lambda u: {})
    return fins, runs


@pytest.mark.parametrize("ip", ["10.0.0.5", "127.0.0.1", "169.254.1.1", "224.0.0.1"])
def test_vod_dns_ip_nao_publico_bloqueia(tmp_path, monkeypatch, ip):
    fins, runs = _vod_ssrf(tmp_path, monkeypatch, ip)
    assert runs == []
    assert "falhou" in repr(fins) and "host nao permitido" in repr(fins)


def test_vod_dns_ip_publico_roda_pipeline(tmp_path, monkeypatch):
    fins, runs = _vod_ssrf(tmp_path, monkeypatch, "8.8.8.8")
    assert runs == ["https://exemplo.test/v"]


def test_vod_dns_falha_bloqueia(tmp_path, monkeypatch):
    import socket

    def boom(*a, **k):
        raise socket.gaierror("x")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    sup = acompanhar.Supervisor([], tmp_path / "o", pipeline=lambda d, o: {}, now=lambda: B, sleep=lambda _s: None)
    assert sup._check_vod_policy({}, "https://exemplo.test/v", lambda u: {}) == "host nao permitido"


# ---- T20: backlog configuravel (CLIPES_MAX_BACKLOG) e descarte antecipado por disco ------------


class _PoolBacklog:
    def video_start_ts(self, ch, s, e):
        return float(s)

    def freeze_and_concat(self, ch, s, e, dst):
        dst.write_bytes(b"x")
        return dst

    def hold(self, ch, ts):
        pass


def _sessao_backlog(tmp_path, monkeypatch, *, disco_livre_gb, env=None):
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.delenv("CLIPES_MAX_BACKLOG", raising=False)
    monkeypatch.delenv("CLIPES_MIN_FREE_GB", raising=False)
    if env is not None:
        monkeypatch.setenv("CLIPES_MAX_BACKLOG", env)
    out = tmp_path / "out"
    out.mkdir()
    queue = monitor.DiskQueue(tmp_path / "queue")
    avisos: list[str] = []
    sess = acompanhar.LiveSession(
        acompanhar.parse_alvo("gaules"), out, queue, _PoolBacklog(), lambda d, r: {"clips": []},
        is_live=lambda u: True, now=lambda: 0.0,
        disk_usage=lambda p: SimpleNamespace(free=disco_livre_gb * 1e9),
        on_backlog_lost=lambda a, nome, score: avisos.append(nome),
    )
    return sess, queue, avisos


def _fechar_n_janelas(sess, n, inicio=0):
    for i in range(inicio, inicio + n):
        sess.fechar_janela(1000.0 + i * 300, last=False)


def _contagem(queue):
    return (
        len(list((queue.root / "pending").iterdir())),
        sorted(p.name for p in (queue.root / "rejected").iterdir()),
    )


def test_backlog_padrao_6_nao_perde_com_6_pendentes(tmp_path, monkeypatch):
    sess, queue, avisos = _sessao_backlog(tmp_path, monkeypatch, disco_livre_gb=100)
    _fechar_n_janelas(sess, 6)
    assert _contagem(queue) == (6, [])
    assert avisos == []


def test_backlog_padrao_6_perde_1_com_7_pendentes(tmp_path, monkeypatch):
    sess, queue, avisos = _sessao_backlog(tmp_path, monkeypatch, disco_livre_gb=100)
    _fechar_n_janelas(sess, 7)
    pendentes, rejeitados = _contagem(queue)
    assert pendentes == 6
    assert rejeitados == ["gaules_1000"]  # a mais antiga e a perdida
    assert avisos == ["gaules_1000"]


def test_backlog_env_2_mantem_comportamento_antigo(tmp_path, monkeypatch):
    sess, queue, avisos = _sessao_backlog(tmp_path, monkeypatch, disco_livre_gb=100, env="2")
    _fechar_n_janelas(sess, 2)
    assert _contagem(queue) == (2, [])
    _fechar_n_janelas(sess, 1, inicio=2)  # 3a janela: 3 > 2 -> perde a mais antiga
    pendentes, rejeitados = _contagem(queue)
    assert pendentes == 2
    assert len(rejeitados) == 1


def test_disco_baixo_descarta_antes_do_limite(tmp_path, monkeypatch):
    sess, queue, avisos = _sessao_backlog(tmp_path, monkeypatch, disco_livre_gb=1)
    _fechar_n_janelas(sess, 2)  # 2 < limite 6, mas disco livre 1 GB < 5 GB
    pendentes, rejeitados = _contagem(queue)
    assert pendentes == 1
    assert rejeitados == ["gaules_1000"]
    assert avisos == ["gaules_1000"]


def _sessao_scores(tmp_path, monkeypatch, scores, *, agora=None, max_backlog="2"):
    """Sessao com pre_score falso: o i-esimo fechar_janela recebe scores[i] (None -> extract_wav falha)."""
    sess, queue, avisos = _sessao_backlog(tmp_path, monkeypatch, disco_livre_gb=100, env=max_backlog)
    it = iter(scores)
    atual = {}

    def fake_wav(src, wav):
        atual["v"] = next(it)
        if atual["v"] is None:
            raise RuntimeError("ffmpeg falhou")
        return wav

    monkeypatch.setattr(acompanhar, "extract_wav", fake_wav)
    monkeypatch.setattr(acompanhar.sinais, "pre_score", lambda wav: atual["v"])
    msgs: list[str] = []
    sess.on_backlog_lost = lambda a, nome, score: msgs.append(f"{nome}:{score:.1f}")
    if agora is not None:
        sess.now = agora
    return sess, queue, msgs


def test_descarta_menor_pre_score(tmp_path, monkeypatch):
    sess, queue, msgs = _sessao_scores(tmp_path, monkeypatch, [5.0, 1.0, 9.0])
    _fechar_n_janelas(sess, 3)
    assert _contagem(queue)[1] == ["gaules_1300"]
    assert msgs == ["gaules_1300:1.0"]


def test_empate_de_score_descarta_a_mais_antiga(tmp_path, monkeypatch):
    t = iter([30.0, 10.0, 20.0])
    sess, queue, msgs = _sessao_scores(tmp_path, monkeypatch, [4.0, 4.0, 4.0], agora=lambda: next(t))
    _fechar_n_janelas(sess, 3)
    # created_at: 1000->30, 1300->10 (mais antiga), 1600->20
    assert _contagem(queue)[1] == ["gaules_1300"]


def test_extract_wav_falha_enfileira_com_pre_score_zero(tmp_path, monkeypatch):
    sess, queue, _ = _sessao_scores(tmp_path, monkeypatch, [None], max_backlog="6")
    _fechar_n_janelas(sess, 1)
    (job_dir,) = (queue.root / "pending").iterdir()
    assert monitor._read_job(job_dir)["pre_score"] == 0.0


def test_claim_next_janelas_por_pre_score_e_busca_por_z_score(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")

    def add(name, **kw):
        d = queue.root / "pending" / name
        d.mkdir(parents=True)
        base = {"channel": "gaules", "session_key": "gaules", "status": "pending"}
        monitor._write_job(d, {**base, **kw})

    janela = {"window_start": 0, "window_end": 1, "video_start": 0, "last": False}
    add("a", pre_score=3.0, created_at=1.0, **janela)
    add("b", pre_score=8.0, created_at=2.0, **janela)
    add("c", pre_score=8.0, created_at=3.0, **janela)
    view = acompanhar._SessionQueueView(queue, lambda: {"gaules"})
    ordem = []
    for _ in range(3):
        d = view.claim_next()
        ordem.append(d.name)
    assert ordem == ["b", "c", "a"]


def test_modulo_acompanhar_sem_discord_rest_direto():
    fonte = Path(acompanhar.__file__).read_text(encoding="utf-8")
    assert "DiscordRest(" not in fonte
    assert "client.post" not in fonte


def _sup_recursos(monkeypatch, tmp_path, divergente, avisos):
    t = [0.0]
    monkeypatch.setattr(acompanhar.config, "recursos_divergentes", lambda: divergente[0])
    sup = acompanhar.Supervisor(
        [], tmp_path / "out", now=lambda: t[0], aviso_fn=avisos.append,
    )
    coord = SimpleNamespace(beat=lambda *a, **k: None)

    def beat(at):
        t[0] = at
        cap: dict = {}
        sup._call_beat(coord, "pc1", cap, None, 1, 1.0, 20.0)
        return cap

    return beat


def test_recursos_divergentes_aviso_unico_apos_600s(monkeypatch, tmp_path):
    avisos: list[str] = []
    div = [True]
    beat = _sup_recursos(monkeypatch, tmp_path, div, avisos)
    assert beat(0)["recursos_divergentes"] is True
    assert beat(599)["recursos_divergentes"] is True
    assert avisos == []
    beat(600)
    assert len(avisos) == 1 and "PC pc1" in avisos[0] and "./clipar" in avisos[0]
    beat(1200)
    assert len(avisos) == 1


def test_recursos_voltam_a_bater_permite_novo_aviso(monkeypatch, tmp_path):
    avisos: list[str] = []
    div = [True]
    beat = _sup_recursos(monkeypatch, tmp_path, div, avisos)
    beat(0)
    beat(600)
    assert len(avisos) == 1
    div[0] = False
    assert beat(700)["recursos_divergentes"] is False
    div[0] = True
    beat(800)
    beat(1399)
    assert len(avisos) == 1
    beat(1400)
    assert len(avisos) == 2


def test_recursos_divergentes_sem_gerido_nao_sai_com_75(monkeypatch, tmp_path):
    monkeypatch.delenv("CLIPES_RECURSOS_GERIDO", raising=False)
    avisos: list[str] = []
    beat = _sup_recursos(monkeypatch, tmp_path, [True], avisos)
    for t in (0, 600, 1200):
        beat(t)  # nao levanta SystemExit


def _add_janela(queue, name, skey, score):
    d = queue.root / "pending" / name
    d.mkdir(parents=True)
    monitor._write_job(d, {
        "job_id": name, "channel": skey, "session_key": skey, "status": "pending", "pre_score": score,
        "created_at": 1.0, "window_start": 0, "window_end": 1, "video_start": 0, "last": False,
    })


def test_claim_next_duas_views_um_job_so_um_recebe(tmp_path):
    import threading

    for i in range(50):
        queue = monitor.DiskQueue(tmp_path / f"q{i}")
        _add_janela(queue, "j", "gaules", 1.0)
        ocup: set[str] = set()
        lock = threading.Lock()
        views = [acompanhar._SessionQueueView(queue, lambda: {"gaules"}, ocup, lock) for _ in range(2)]
        res: list = []
        barrier = threading.Barrier(2)

        def run(v):
            barrier.wait()
            res.append(v.claim_next())

        ts = [threading.Thread(target=run, args=(v,)) for v in views]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sum(r is not None for r in res) == 1


def test_claim_next_pula_live_ocupada_e_volta_apos_discard(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "q")
    _add_janela(queue, "a1", "A", 9.0)
    _add_janela(queue, "b1", "B", 1.0)
    ocup = {"A"}
    view = acompanhar._SessionQueueView(queue, lambda: {"A", "B"}, ocup)
    assert view.claim_next().name == "b1"
    assert view.claimed_key == "B" and ocup == {"A", "B"}
    ocup.discard("A")
    assert acompanhar._SessionQueueView(queue, lambda: {"A", "B"}, ocup).claim_next().name == "a1"


def test_process_one_cancelado_devolve_job_e_libera_live(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "q")
    _add_janela(queue, "a1", "A", 1.0)
    sup = acompanhar.Supervisor([], tmp_path / "out", queue_root=queue.root)
    sess = acompanhar.LiveSession(
        acompanhar.parse_alvo("A"), tmp_path / "out", queue, None, lambda d, r: {"clips": []},
        is_live=lambda u: True, now=lambda: 0.0, disk_usage=lambda p: SimpleNamespace(free=1e12),
    )
    sup._all_sessions["A"] = sess
    visto: list = []

    def pipe(d, r):
        visto.append(sup._jobs_atuais())
        sup._cancel.set()
        raise RuntimeError("morto")

    sess.pipeline = pipe
    assert sup._process_one(queue, pipe, skip_lost=True, idx=0) is True
    assert visto == [["a1"]]
    assert sup._jobs_atuais() == []
    assert (queue.root / "pending" / "a1").is_dir()
    assert sup._lives_ocupadas == set()


# ---- jobs-paralelos T20: pool de consumidores no Supervisor ---------------------------------------


def _sup_pool(tmp_path, monkeypatch, pipe, jobs, *, n=3, hook=None, coord=None, **kw):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", str(n))
    monkeypatch.setenv("CLIPES_SLOTS", f"twitch={n}")
    monkeypatch.setattr(acompanhar.capacidade, "jobs_efetivos", jobs)
    names = [f"c{i}" for i in range(n)]
    stops = {c: B + 620 for c in names}
    pool = FakeMultiPool(clock, stops)

    def sleep(d):
        clock.sleep(d)
        if hook is not None:
            hook()

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo(c) for c in names],
        tmp_path / "out",
        pool=pool,
        pipeline=pipe,
        is_live=lambda u: clock.t < stops[u.rsplit("/", 1)[-1]],
        now=clock.now,
        sleep=sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        coord=coord,
        **kw,
    )
    return sup, tmp_path / "out" / "queue"


def _ok(job_dir: Path) -> dict:
    j = json.loads((job_dir / "job.json").read_text())
    c = j["window_end"] - 100 - j["video_start"]
    return {"clips": [{"file": "c.mp4", "score": 1.0, "start": c - 20, "end": c + 20}]}


def _sess(job_dir: Path) -> str:
    j = json.loads((job_dir / "job.json").read_text())
    return j.get("session_key") or j["channel"]


def test_pool_tres_consumidores_paralelizam_sessoes_sem_duplicar_job(tmp_path, monkeypatch):
    import threading
    import time

    monkeypatch.setattr(acompanhar, "_CONSUMIDOR_OCIOSO_S", 0.1)  # so acelera o 1o ciclo ocioso; ha trabalho na fila
    lock = threading.Lock()
    spans: list[tuple[str, float, float, str]] = []

    def pipe(job_dir, root):
        t0 = time.monotonic()
        time.sleep(0.3)
        with lock:
            spans.append((_sess(job_dir), t0, time.monotonic(), job_dir.name))
        return _ok(job_dir)

    sup, qroot = _sup_pool(tmp_path, monkeypatch, pipe, lambda: 3)
    t0 = time.monotonic()
    assert sup.run()["status"] == "ended"
    assert time.monotonic() - t0 < 1.5
    assert len(spans) == 6
    assert sorted(p.name for p in (qroot / "done").iterdir()) == sorted(s[3] for s in spans)
    for a in spans:
        for b in spans:
            if a is not b and a[0] == b[0]:
                assert a[2] <= b[1] or b[2] <= a[1]  # mesma sessao nunca em paralelo


def test_pool_jobs_1_nao_cria_threads_consumidor(tmp_path, monkeypatch):
    import threading

    vistos: list[str] = []

    def pipe(job_dir, root):
        vistos.extend(t.name for t in threading.enumerate() if t.name.startswith("consumidor-"))
        return _ok(job_dir)

    sup, qroot = _sup_pool(tmp_path, monkeypatch, pipe, lambda: 1)
    sup.run()
    assert vistos == [] and sup._consumidores == {}
    assert len(list((qroot / "done").iterdir())) == 6


def test_pool_trocar_3_para_1_termina_os_3_e_depois_so_inline(tmp_path, monkeypatch):
    import threading
    import time

    val = [3]
    go = threading.Event()
    lock = threading.Lock()
    quem: list[str] = []
    bloqueados: list[str] = []

    def pipe(job_dir, root):
        with lock:
            quem.append(threading.current_thread().name)
            primeiros = len(quem) <= 3
        if primeiros:
            bloqueados.append(job_dir.name)
            go.wait(10)
        return _ok(job_dir)

    sup, qroot = _sup_pool(tmp_path, monkeypatch, pipe, lambda: val[0], hook=lambda: time.sleep(0.005))
    th = threading.Thread(target=sup.run, name="runner")
    th.start()
    for _ in range(600):
        if len(bloqueados) == 3:
            break
        time.sleep(0.01)
    assert len(bloqueados) == 3 and all(n.startswith("consumidor-") for n in quem)
    val[0] = 1
    for _ in range(600):
        if sup._jobs_alvo == 0:
            break
        time.sleep(0.01)
    go.set()
    th.join(20)
    assert not th.is_alive()
    assert len(list((qroot / "done").iterdir())) == 6
    assert all(not n.startswith("consumidor-") for n in quem[3:])


def test_pool_trocar_1_para_2_sobe_duas_threads(tmp_path, monkeypatch):
    import threading
    import time

    val = [1]
    vistos: set[str] = set()

    def hook():
        val[0] = 2
        time.sleep(0.005)
        vistos.update(t.name for t in threading.enumerate() if t.name.startswith("consumidor-"))

    sup, _ = _sup_pool(tmp_path, monkeypatch, lambda d, o: {"clips": []}, lambda: val[0], hook=hook)
    sup.run()
    assert vistos == {"consumidor-0", "consumidor-1"}


def test_pool_laco_continua_ticando_sessoes_durante_pipeline(tmp_path, monkeypatch):
    import threading
    import time

    ativo = threading.Event()
    ticks = [0]
    orig = acompanhar.LiveSession.tick

    def tick(self, t):
        if ativo.is_set():
            ticks[0] += 1
        return orig(self, t)

    monkeypatch.setattr(acompanhar.LiveSession, "tick", tick)
    medido = [False]

    def pipe(job_dir, root):
        if not medido[0]:
            medido[0] = True
            ativo.set()
            time.sleep(0.5)
            ativo.clear()
        return {"clips": []}

    sup, _ = _sup_pool(tmp_path, monkeypatch, pipe, lambda: 2, n=2, hook=lambda: time.sleep(0.005))
    sup.run()
    assert medido[0] and ticks[0] >= 2


def test_pool_dois_consumidores_nunca_dois_pegar_vod_ao_mesmo_tempo(tmp_path):
    import threading
    import time

    em_andamento = [0]
    pico = [0]
    lock = threading.Lock()
    fila = [{"_id": "v1", "url": "https://x.test/1"}, {"_id": "v2", "url": "https://x.test/2"}]
    fins: list[str] = []

    class C(_CoordStub):
        def pegar_vod(self, alias):
            with lock:
                em_andamento[0] += 1
                pico[0] = max(pico[0], em_andamento[0])
            try:
                time.sleep(0.1)
                return fila.pop(0) if fila else None
            finally:
                with lock:
                    em_andamento[0] -= 1

        def fim_vod(self, vod_id, *a, **kw):
            fins.append(vod_id)

    sup = acompanhar.Supervisor(
        [], tmp_path / "out", pipeline=lambda d, o: {"clips": []}, coord=C(), now=lambda: B, sleep=lambda _s: None,
        vod_pipeline=lambda url, out: time.sleep(0.1) or 0,
    )
    sup._check_vod_policy = lambda vod, url, probe: None
    queue = monitor.DiskQueue(tmp_path / "out" / "queue")
    sup._jobs_alvo = 2
    ths = [
        threading.Thread(target=sup._consumidor, args=(i, queue, sup.pipeline, "w", lambda u: {}, True)) for i in range(2)
    ]
    [t.start() for t in ths]
    for _ in range(300):
        if len(fins) == 2:
            break
        time.sleep(0.01)
    sup._parar_consumidores.set()
    [t.join(5) for t in ths]
    assert sorted(fins) == ["v1", "v2"]
    assert pico[0] == 1


def test_pool_liberar_com_dois_jobs_devolve_ambos_a_pending_e_encerra_threads(tmp_path, monkeypatch):
    import threading
    import time

    db = mongomock.MongoClient().db
    clock_ref: list = []
    coord = Coord(db, "w-pool", now=lambda: clock_ref[0].now(), lease_s=90)
    rodando: list[str] = []
    solto = threading.Event()
    enviado = [False]

    def pipe(job_dir, root):
        rodando.append(job_dir.name)
        solto.wait(10)
        raise RuntimeError("morto pelo liberar")

    def hook():
        time.sleep(0.01)
        if not enviado[0] and len(rodando) >= 2:
            enviado[0] = True
            db.commands.insert_one({
                "_id": "cmd-lib-pool", "worker_id": "w-pool", "acao": "liberar", "status": "pending",
                "criado_em": coord._dt(), "expira_em": coord._dt(+300), "executado_em": None,
            })

    sup, qroot = _sup_pool(
        tmp_path, monkeypatch, pipe, lambda: 2, n=2, hook=hook, coord=coord,
        kill_children=solto.set, stream_id="s_pool", capacity_fn=lambda _: {"cpus": 4, "score": 30}, beat_s=0.01,
    )
    clock_ref.append(SimpleNamespace(now=sup.now))
    t0 = time.monotonic()
    with pytest.raises(SystemExit) as exc:
        sup.run()
    assert exc.value.code == 75
    assert time.monotonic() - t0 <= 10
    assert len(list((qroot / "pending").iterdir())) >= 2
    assert list((qroot / "processing").iterdir()) == []
    assert not any(t.name.startswith("consumidor-") and t.is_alive() for t in threading.enumerate())


def test_pool_log_de_corte_aparece_uma_vez_por_mudanca(tmp_path, monkeypatch, capsys):
    import time

    monkeypatch.setenv("CLIPES_JOBS", "4")
    val = [2]
    passos = [0]

    def hook():
        passos[0] += 1
        if passos[0] == 50:
            val[0] = 3
        time.sleep(0.002)

    sup, _ = _sup_pool(tmp_path, monkeypatch, lambda d, o: {"clips": []}, lambda: val[0], hook=hook)
    sup.run()
    out = capsys.readouterr().out
    assert out.count("[jobs] CLIPES_JOBS=4 limitado a 2 ") == 1
    assert out.count("[jobs] CLIPES_JOBS=4 limitado a 3 ") == 1


def _beat_kwargs(monkeypatch, tmp_path, coord_beat, amostras=(), jobs_em_andamento=None, efetivos=2):
    monkeypatch.setattr(acompanhar.capacidade, "jobs_efetivos", lambda: efetivos)
    sup = acompanhar.Supervisor([], tmp_path / "out", now=lambda: 0.0)
    sup._worker_s.extend(amostras)
    sup._jobs_em_andamento.update(jobs_em_andamento or {})
    sup._call_beat(SimpleNamespace(beat=coord_beat), "pc1", {}, None, 1, 1.0, 20.0)


def test_beat_jobs_media_e_lives_sustentaveis(monkeypatch, tmp_path):
    got = {}
    _beat_kwargs(monkeypatch, tmp_path, lambda *a, **k: got.update(k), amostras=[100, 200])
    j = got["jobs"]
    assert (j["job_medio_s"], j["amostras"], j["efetivos"]) == (150.0, 2, 2)
    assert j["lives_sustentaveis"] == round(2 * acompanhar.monitor.LIVE_STEP_S / 150, 1)


def test_beat_jobs_sem_amostras(monkeypatch, tmp_path):
    got = {}
    _beat_kwargs(monkeypatch, tmp_path, lambda *a, **k: got.update(k))
    j = got["jobs"]
    assert (j["job_medio_s"], j["amostras"], j["lives_sustentaveis"]) == (None, 0, None)


def test_beat_media_das_20_ultimas(monkeypatch, tmp_path):
    got = {}
    _beat_kwargs(monkeypatch, tmp_path, lambda *a, **k: got.update(k), amostras=[1000] + [10] * 20)
    assert got["jobs"]["amostras"] == 20 and got["jobs"]["job_medio_s"] == 10.0


def test_beat_coord_antigo_sem_kwargs_recebe(monkeypatch, tmp_path):
    chamadas = []

    def beat(alias, cap, job_atual, ml, razao):
        chamadas.append(alias)

    _beat_kwargs(monkeypatch, tmp_path, beat)
    assert chamadas == ["pc1"]


def test_beat_dois_jobs_job_atual_e_o_primeiro(monkeypatch, tmp_path):
    got = {}

    def beat(alias, cap, job_atual, ml, razao, **k):
        got.update(k, job_atual=job_atual)

    _beat_kwargs(monkeypatch, tmp_path, beat, jobs_em_andamento={0: "a", 1: "b"})
    assert got["job_atual"] == "a" and got["jobs_atuais"] == ["a", "b"]


# ---- revisao CLIPES_JOBS: corridas e carga -------------------------------------------------------


def test_descarte_backlog_com_job_claimado_no_meio_nao_derruba_a_sessao(tmp_path, monkeypatch):
    import os

    sess, queue, msgs = _sessao_scores(tmp_path, monkeypatch, [5.0, 1.0, 9.0])
    _fechar_n_janelas(sess, 2)
    orig = sess.pending

    def pending_com_claim():
        lista = orig()
        # consumidor faz claim do menor score entre a listagem e o complete
        os.replace(queue.root / "pending" / "gaules_1300", queue.root / "processing" / "gaules_1300")
        return lista

    sess.pending = pending_com_claim
    _fechar_n_janelas(sess, 1, inicio=2)  # nao levanta
    assert (queue.root / "processing" / "gaules_1300").is_dir()
    assert "gaules_1300" not in _contagem(queue)[1]
    assert not any(m.startswith("gaules_1300") for m in msgs)


def test_consumidor_ocioso_nao_martela_pegar_vod(tmp_path):
    chamadas = [0]

    class C(_CoordStub):
        def pegar_vod(self, alias):
            chamadas[0] += 1

        def fim_vod(self, *a, **kw):
            pass

    class Rel:
        def __init__(self):
            self.t = 0.0

        def is_set(self):
            return self.t >= 3.0

        def wait(self, d):
            self.t += d

    sup = acompanhar.Supervisor([], tmp_path / "out", pipeline=lambda d, o: {"clips": []}, coord=C(), now=lambda: B)
    queue = monitor.DiskQueue(tmp_path / "out" / "queue")
    sup._jobs_alvo = 3
    for i in range(3):
        sup._parar_consumidores = Rel()
        sup._consumidor(i, queue, sup.pipeline, "w", lambda u: {}, True)
    assert chamadas[0] <= 3 * 4


def test_parada_nao_trava_com_consumidor_preso(tmp_path, monkeypatch):
    import threading
    import time

    monkeypatch.setattr(acompanhar, "_JOIN_CONSUMIDORES_S", 0.3)
    go = threading.Event()

    def pipe(job_dir, root):
        go.wait(15)
        return _ok(job_dir)

    sup, _ = _sup_pool(tmp_path, monkeypatch, pipe, lambda: 2, n=2, hook=lambda: time.sleep(0.005))
    th = threading.Thread(target=sup.run, daemon=True)
    th.start()
    th.join(8)
    travou = th.is_alive()
    go.set()
    th.join(5)
    assert not travou


def test_recursos_pendentes_devolve_job_em_andamento_a_pending(tmp_path, monkeypatch):
    import threading

    queue = monitor.DiskQueue(tmp_path / "q")
    _add_janela(queue, "a1", "A", 1.0)
    sup = acompanhar.Supervisor([], tmp_path / "out", queue_root=queue.root, kill_children=lambda: None)
    sess = acompanhar.LiveSession(
        acompanhar.parse_alvo("A"), tmp_path / "out", queue, None, lambda d, r: {"clips": []},
        is_live=lambda u: True, now=lambda: 0.0, disk_usage=lambda p: SimpleNamespace(free=1e12),
    )
    sup._all_sessions["A"] = sess
    iniciou, go = threading.Event(), threading.Event()

    def pipe(d, r):
        iniciou.set()
        go.wait(10)
        raise RuntimeError("morto")

    sess.pipeline = pipe
    th = threading.Thread(target=sup._process_one, args=(queue, pipe), kwargs={"skip_lost": True, "idx": 0})
    th.start()
    assert iniciou.wait(5)
    monkeypatch.setattr(acompanhar.config, "apply_remote_config", lambda *a, **k: {})
    monkeypatch.setattr(acompanhar.config, "recursos_pendentes", lambda: True)
    with pytest.raises(SystemExit) as ei:
        sup._apply_runtime_config({}, alias="pc")
    assert ei.value.code == 75
    go.set()
    th.join(5)
    assert (queue.root / "pending" / "a1").is_dir()


def test_claim_next_falha_no_move_nao_deixa_pending_marcado_processing(tmp_path, monkeypatch):
    queue = monitor.DiskQueue(tmp_path / "q")
    _add_janela(queue, "a1", "A", 1.0)
    real = monitor.os.replace

    def replace(src, dst):
        if Path(dst).parent.name == "processing":
            raise OSError("boom")
        return real(src, dst)

    monkeypatch.setattr(monitor.os, "replace", replace)
    view = acompanhar._SessionQueueView(queue, lambda: {"A"}, set())
    assert view.claim_next() is None
    assert monitor._read_job(queue.root / "pending" / "a1")["status"] == "pending"
