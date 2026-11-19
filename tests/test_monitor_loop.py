"""Tests for the monitor main loop (run_monitor) and the `python -m clipes monitor` entrypoint (all mocked)."""

import asyncio
import json
import time

import pytest

from clipes import __main__ as entry, cli, monitor
from test_monitor_queue import stage_job
from test_monitor_recorder import FakePopen

T0 = 1_763_380_000.0


class Clock:
    """Simulated wall clock: every sleep advances it (at most 1s per call) and yields to the event loop."""

    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.t += min(seconds, 1.0)
        await asyncio.sleep(0.001)


def burst_irc(clock, channel="canal", at=T0 + 70, authors=6, messages=12):
    """Fake IRC: silence until `at`, then a legit burst on `channel`, then idle until stopped."""
    seen = {}

    async def irc(channels, on_message, stop):
        seen["channels"] = channels
        while clock() < at:
            await clock.sleep(1)
        for i in range(messages):
            on_message(channel, clock(), f"user{i % authors}", "KKKKKK")
        await stop.wait()

    return irc, seen


async def run_until(condition, **kwargs):
    stop = asyncio.Event()
    task = asyncio.create_task(monitor.run_monitor(stop=stop, **kwargs))
    for _ in range(3000):
        if condition() or task.done():
            break
        await asyncio.sleep(0.002)
    stop.set()
    await task


def test_dry_run_only_detects_spikes_without_recording_or_queue(tmp_path, capsys):
    clock = Clock()
    irc, _ = burst_irc(clock)
    popen = FakePopen()

    asyncio.run(run_until(
        lambda: "spike canal" in capsys.readouterr().out,
        channels=["canal"], out_root=tmp_path, policy=monitor.ChannelPolicy(),
        dry_run=True, irc=irc, popen=popen, now=clock, sleep=clock.sleep,
    ))

    assert clock() >= T0 + 70  # the burst actually happened
    assert popen.procs == []
    assert not (tmp_path / "queue").exists()


def test_spike_is_captured_enqueued_and_processed_by_worker(tmp_path, monkeypatch):
    clock = Clock()
    irc, _ = burst_irc(clock)
    popen = FakePopen()
    queue_root = tmp_path / "q"
    captured = []

    async def fake_capture(pool, detector, event, staging_root, *, now, sleep):
        captured.append(event)
        assert pool.max_hot == 2  # --max-recorders 2
        job_dir = staging_root / f"{event.channel}_{int(event.peak_ts)}"
        job_dir.mkdir()
        (job_dir / "job.json").write_text(json.dumps({
            "job_id": job_dir.name, "channel": event.channel, "z_score": event.z_score,
            "created_at": now(), "status": "pending",
        }), encoding="utf-8")
        return job_dir

    processed = []

    def pipeline(job_dir, out_root):
        processed.append(job_dir.name)
        return {"clips": [{"score": 0.8}], "output_dir": str(out_root / job_dir.name)}

    monkeypatch.setattr(monitor, "capture_job", fake_capture)
    done = queue_root / "done"

    asyncio.run(run_until(
        lambda: done.is_dir() and len(list(done.iterdir())) == 1 and popen.pipelines(),
        channels=["canal"], out_root=tmp_path, policy=monitor.ChannelPolicy(delay_h={"canal": 24}),
        queue_root=queue_root, max_recorders=2, irc=irc, popen=popen, pipeline=pipeline,
        now=clock, sleep=clock.sleep,
    ))

    assert len(captured) == 1 and captured[0].channel == "canal"
    (job,) = [json.loads((d / "job.json").read_text()) for d in done.iterdir()]
    assert job["status"] == "done" and job["score"] == 0.8
    assert job["ready_at"] == pytest.approx(job["created_at"] + 86400)
    assert processed == [job["job_id"]]
    assert [p.cmd[4] for p in popen.pipelines()] == ["twitch.tv/canal"]  # rolling recorder on the chatty channel
    assert all(p.returncode is not None for p in popen.procs)  # recorders stopped on shutdown


def test_orphans_recovered_and_blocked_channels_never_watched(tmp_path):
    clock = Clock()
    irc, seen = burst_irc(clock, at=float("inf"))
    queue_root = tmp_path / "q"
    queue = monitor.DiskQueue(queue_root)
    queue.enqueue(stage_job(tmp_path))
    orphan = queue.claim_next()  # left in processing/ as if the previous run crashed
    processed = []

    def pipeline(job_dir, out_root):
        processed.append(job_dir.name)
        return {"clips": []}

    asyncio.run(run_until(
        lambda: bool(processed) and not list((queue_root / "processing").iterdir()),
        channels=["gaules", "proibido"], out_root=tmp_path,
        policy=monitor.ChannelPolicy(blocked={"proibido"}),
        queue_root=queue_root, irc=irc, popen=FakePopen(), pipeline=pipeline, now=clock, sleep=clock.sleep,
    ))

    assert seen["channels"] == ["gaules"]
    assert processed == [orphan.name]
    assert [p.name for p in (queue_root / "rejected").iterdir()] == [orphan.name]  # 0 clips


def test_startup_clears_stale_buffer_and_worker_runs_off_the_event_loop(tmp_path):
    clock = Clock()
    irc_sleeps = []

    async def irc(channels, on_message, stop):
        while not stop.is_set():
            irc_sleeps.append(clock())
            await asyncio.sleep(0.01)  # real time: counts how often the event loop gets to run

    queue_root = tmp_path / "q"
    stale = queue_root / ".buffer" / "velho" / "1700000000.ts"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    queue = monitor.DiskQueue(queue_root)
    queue.enqueue(stage_job(tmp_path))
    finished = []

    def whisper_like_pipeline(job_dir, out_root):
        before = len(irc_sleeps)
        time.sleep(0.5)  # blocking, CPU-bound in real life
        finished.append(len(irc_sleeps) - before)
        return {"clips": [{"score": 0.5}]}

    asyncio.run(run_until(
        lambda: bool(finished),
        channels=["canal"], out_root=tmp_path, policy=monitor.ChannelPolicy(),
        queue_root=queue_root, irc=irc, popen=FakePopen(), pipeline=whisper_like_pipeline,
        now=clock, sleep=clock.sleep,
    ))

    assert not stale.parent.exists()
    assert finished[0] >= 20  # IRC kept reading while the pipeline blocked for 0.5s


def test_second_monitor_on_same_queue_fails_fast(tmp_path):
    queue_root = tmp_path / "q"
    queue_root.mkdir()
    lock = monitor._lock_worker(queue_root)
    try:
        with pytest.raises(RuntimeError, match="another monitor worker"):
            asyncio.run(monitor.run_monitor(["canal"], tmp_path, monitor.ChannelPolicy(), queue_root=queue_root))
    finally:
        lock.close()


@pytest.fixture
def fake_run_monitor(monkeypatch):
    calls = []

    async def fake(channels, out_root, policy, **kwargs):
        calls.append({"channels": channels, "out_root": out_root, "policy": policy, **kwargs})

    monkeypatch.setattr(monitor, "run_monitor", fake)
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    return calls


def test_python_m_clipes_monitor_dispatches_flags(tmp_path, fake_run_monitor):
    channels = tmp_path / "channels.txt"
    channels.write_text("gaules\nproibido\nalanzoka\n", encoding="utf-8")
    (tmp_path / "policy.json").write_text(json.dumps({"blocked": ["proibido"]}), encoding="utf-8")

    code = monitor.main([
        "--channels-file", str(channels), "--max-recorders", "2",
        "--queue-dir", str(tmp_path / "fila"), "--out-dir", str(tmp_path / "out"), "--dry-run",
    ])

    assert code == 0
    (call,) = fake_run_monitor
    assert call["channels"] == ["gaules", "alanzoka"]
    assert call["out_root"] == (tmp_path / "out").resolve()
    assert call["queue_root"] == (tmp_path / "fila").resolve()
    assert call["max_recorders"] == 2 and call["dry_run"] is True


def test_monitor_without_channels_exits_2(tmp_path, fake_run_monitor):
    assert monitor.main(["--channels-file", str(tmp_path / "nao_existe.txt")]) == 2
    assert fake_run_monitor == []


def test_other_args_still_go_to_cli(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv: seen.append(argv) or 0)
    assert entry.main(["https://www.twitch.tv/videos/1"]) == 0
    assert seen == [["https://www.twitch.tv/videos/1"]]
