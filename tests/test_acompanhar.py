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
        is_live=lambda u: True,
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
    assert cmds[1][0] == "yt-dlp" and cmds[1][-1] == "https://www.youtube.com/@CanalYT/live"


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
        is_live=lambda u: True,
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
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        aviso_fn=avisos.append,
    )
    sup.run()
    assert f"janela perdida gaules (fila cheia)" in avisos


# ---- T7: Limite, aguardando, recusa visível, offline, policy, recorder error ----------------------


def test_tres_alvos_limite_2_recusa_com_1_aviso_e_promove_ao_encerrar(tmp_path, monkeypatch, capsys):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

    avisos: list[str] = []
    pool = FakeMultiPool(clock, {"c1": B + 320, "c2": B + 620, "c3": B + 920})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("c1"), acompanhar.parse_alvo("c2"), acompanhar.parse_alvo("c3")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: True,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        aviso_fn=avisos.append,
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert pool.started_order == ["c1", "c2", "c3"]  # c3 started only after c1 ended
    assert len(avisos) == 1
    expected = "[acompanhar] recusada c3: limite max_lives=2 (rode bench-live ou defina CLIPES_MAX_LIVES)"
    assert avisos[0] == expected
    assert expected in capsys.readouterr().out


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
        is_live=lambda u: "off" not in u,
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
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

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
            "ensure_indexes": lambda *a: None,
            "ensure_live": lambda *a, **k: None,
            "claim_live": lambda *a: False,
            "beat": lambda *a: None,
            "renew": lambda *a: True,
            "pending_command": lambda *a: None,
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
    assert entry.main(["acompanhar", "gaules", "--max-dur", "45"]) == 0
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
        "    rc = entry.main(['acompanhar', 'invalido!', '--out-dir', td])\n"
        "assert rc == 2 and 'pymongo' not in sys.modules\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "CLIPES_MONGO_URI"}
    res = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr + res.stdout


def test_erro_tick_ou_encerrar_remove_so_sessao_com_falha_e_mantem_outra_viva(tmp_path, monkeypatch, capsys):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "2")

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
        is_live=lambda u: True,
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
        is_live=lambda u: True,
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
