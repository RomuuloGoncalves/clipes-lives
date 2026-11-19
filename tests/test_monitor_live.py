"""follow_live: janelas alinhadas, fim por 2 sinais, disco, backlog (gravador, relógio e pipeline falsos)."""

import json
from types import SimpleNamespace

from clipes import monitor

B = 1_700_000_100  # múltiplo de 300
STEP = 300


class Clock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, d):
        self.t += d


class FakePool:
    """Grava segmentos de 10 s até `stop_at`; `alive` cai quando o gravador "sai"."""

    def __init__(self, clock, stop_at):
        self.clock, self.stop_at, self.first = clock, stop_at, None
        self.started = False

    def start_on_demand(self, ch):
        self.started, self.first = True, int(self.clock.t) // 10 * 10
        return True

    def release_on_demand(self, ch):
        self.started = False

    def is_recording(self, ch):
        return self.clock.t < self.stop_at

    def segments(self, ch):
        last = min(self.clock.t, self.stop_at) - 10
        return [(e, None) for e in range(self.first, int(last) + 1, 10)]

    def video_start_ts(self, ch, s, e):
        return float(max(self.first, int(s) // 10 * 10))

    def freeze_and_concat(self, ch, s, e, dst):
        dst.write_bytes(b"x")

    def hold(self, ch, ts): pass
    def release_hold(self, ch): pass
    def cleanup(self, now): pass


def run(tmp_path, monkeypatch, *, stop_after, pipeline, free_gb=100, jump=None):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    pool = FakePool(clock, B + stop_after)
    res = monitor.follow_live(
        "https://www.twitch.tv/Canal", tmp_path / "out", pool=pool, pipeline=pipeline(clock),
        is_live=lambda u: clock.t < B + stop_after, now=clock.now, sleep=clock.sleep, min_free_gb=5,
        disk_usage=lambda p: SimpleNamespace(free=free_gb * 1e9),
    )
    return res, tmp_path / "out" / "queue"


def jobs_in(q, state):
    return sorted(p.name for p in (q / state).iterdir())


def test_tres_janelas_e_depois_gravador_parado_encerra_a_live(tmp_path, monkeypatch):
    # boundaries B+300, B+600, B+900; stream stops at B+920 (tail 20 s < 25 s: no 4th job)
    res, q = run(tmp_path, monkeypatch, stop_after=920, pipeline=lambda c: lambda d, o: {"clips": []})
    assert res["status"] == "ended"
    assert res["jobs"] == [f"canal_{B + 300 * i}" for i in (1, 2, 3)]
    assert len(jobs_in(q, "pending")) == 0 and len(jobs_in(q, "rejected")) == 3  # no clips -> rejected
    assert not list(q.glob("*/*/source.mkv"))


def test_disco_abaixo_do_piso_pula_a_janela(tmp_path, monkeypatch, capsys):
    res, q = run(tmp_path, monkeypatch, stop_after=620, pipeline=lambda c: lambda d, o: {"clips": []}, free_gb=4)
    assert res["jobs"] == []
    assert capsys.readouterr().out.count("[live] janela pulada: disco") == 2  # B+300 and B+600


def test_backlog_maior_que_2_descarta_a_mais_antiga_como_lost(tmp_path, monkeypatch):
    def slow(clock):
        calls = []

        def pipe(d, o):
            if not calls:
                clock.t += 1000  # three boundaries pass while the first window is processed
            calls.append(d.name)
            return {"clips": []}
        return pipe
    res, q = run(tmp_path, monkeypatch, stop_after=2500, pipeline=slow)
    lost = [n for n in jobs_in(q, "rejected")
            if json.loads((q / "rejected" / n / "job.json").read_text()).get("reject_reason") == "lost"]
    assert lost == [f"canal_{B + 600}"]
    assert not (q / "rejected" / lost[0] / "source.mkv").exists()


def test_so_clipes_da_regiao_dona_e_sem_repeticao_sobrevivem(tmp_path, monkeypatch):
    def make(clock):
        def pipe(job_dir, out):
            job = json.loads((job_dir / "job.json").read_text())
            vs, e = job["video_start"], job["window_end"]
            def clip(center):
                return {"file": "c.mp4", "score": 1.0, "start": center - 20 - vs, "end": center + 20 - vs}
            if e == B + 300:
                return {"clips": [clip(e - 100), clip(e - 10)]}  # 2nd is in the NEXT window's region
            if e == B + 600:
                return {"clips": [clip(B + 290), clip(B + 290)]}  # same absolute interval twice
            return {"clips": []}
        return pipe
    _, q = run(tmp_path, monkeypatch, stop_after=620, pipeline=make)
    counts = {n: json.loads((q / "done" / n / "job.json").read_text())["clips"] for n in jobs_in(q, "done")}
    assert counts == {f"canal_{B + 300}": 1, f"canal_{B + 600}": 1}


def test_monitor_follow_dispatches_follow_live_with_policy_and_dirs(tmp_path, monkeypatch):
    from clipes import __main__ as entry

    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"delay_h": {"gaules": 12.0}}), encoding="utf-8")
    out_root = tmp_path / "out"
    queue_root = tmp_path / "fila"
    calls = []

    def fake_follow(url_or_channel, root, **kwargs):
        calls.append({"target": url_or_channel, "out_root": root, **kwargs})
        return {"status": "ended", "jobs": []}

    async def fail_run_monitor(*args, **kwargs):
        raise AssertionError("run_monitor must not be called in --follow mode")

    monkeypatch.setattr(monitor, "follow_live", fake_follow)
    monkeypatch.setattr(monitor, "run_monitor", fail_run_monitor)

    rc = monitor.main([
        "--follow", "https://www.twitch.tv/Gaules",
        "--policy-file", str(policy_path),
        "--out-dir", str(out_root),
        "--queue-dir", str(queue_root),
    ])

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["target"] == "gaules"
    assert calls[0]["out_root"] == out_root.resolve()
    assert calls[0]["queue_root"] == queue_root.resolve()
    assert calls[0]["policy"] == monitor.ChannelPolicy(delay_h={"gaules": 12.0})


def test_monitor_follow_blocked_in_policy_exits_2_without_recording(tmp_path, monkeypatch):
    channels_file = tmp_path / "channels.txt"  # does not need to exist; policy.json sits beside it
    (tmp_path / "policy.json").write_text(json.dumps({"blocked": ["Proibido"]}), encoding="utf-8")

    def fail_follow(*args, **kwargs):
        raise AssertionError("follow_live must not run when channel is blocked")

    monkeypatch.setattr(monitor, "follow_live", fail_follow)

    rc_blocked = monitor.main(["--follow", "proibido", "--channels-file", str(channels_file)])
    assert rc_blocked == 2

    (tmp_path / "policy.json").write_text(json.dumps({"allowed": ["outro"]}), encoding="utf-8")
    rc_not_allowed = monitor.main(["--follow", "proibido", "--channels-file", str(channels_file)])
    assert rc_not_allowed == 2


def test_monitor_follow_invalid_channel_exits_2_without_recording(tmp_path, monkeypatch):
    monkeypatch.setattr(
        monitor,
        "follow_live",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("follow_live must not run for invalid channel")),
    )

    rc = monitor.main(["--follow", "canal invalido!", "--out-dir", str(tmp_path / "out")])
    assert rc == 2


def test_follow_live_respects_policy_blocked_and_delay(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    pool = FakePool(clock, B + 320)

    blocked_res = monitor.follow_live(
        "canal", tmp_path / "blocked_out", pool=pool,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: True, now=clock.now, sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        policy=monitor.ChannelPolicy(blocked={"canal"}),
    )
    assert blocked_res == {"status": "blocked", "jobs": []}
    assert pool.started is False

    def pipe_one(job_dir, out):
        job = json.loads((job_dir / "job.json").read_text())
        vs, e = job["video_start"], job["window_end"]
        center = e - 100
        return {"clips": [{"file": "c.mp4", "score": 1.0, "start": center - 20 - vs, "end": center + 20 - vs}]}

    res = monitor.follow_live(
        "canal", tmp_path / "out", pool=pool, pipeline=pipe_one,
        is_live=lambda u: clock.t < B + 320, now=clock.now, sleep=clock.sleep, min_free_gb=5,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        policy=monitor.ChannelPolicy(delay_h={"canal": 6.0}),
    )
    assert res["status"] == "ended"
    done_jobs = list((tmp_path / "out" / "queue" / "done").iterdir())
    assert len(done_jobs) == 1
    job = json.loads((done_jobs[0] / "job.json").read_text())
    assert job["ready_at"] == job["created_at"] + 6.0 * 3600
