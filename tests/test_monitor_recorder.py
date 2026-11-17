"""Tests for the rolling recorder pool and job capture (fake subprocesses + real ffmpeg on tiny media)."""

import asyncio
import io
import json
import os
import shutil
import subprocess
import time

import pytest

from clipes import monitor
from clipes.ingest import probe_duration

BASE = 1_700_000_000  # arbitrary epoch used to name fake segments


class FakeProc:
    def __init__(self, cmd, **kwargs):
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


class FakePopen:
    def __init__(self):
        self.procs = []

    def __call__(self, cmd, **kwargs):
        proc = FakeProc(cmd, **kwargs)
        self.procs.append(proc)
        return proc

    def pipelines(self):
        return [p for p in self.procs if p.cmd[0] == "streamlink"]


@pytest.fixture(scope="session")
def ts_sample(tmp_path_factory):
    """One real 10s MPEG-TS segment (h264 + aac), copied under epoch names by the tests."""
    path = tmp_path_factory.mktemp("sample") / "sample.ts"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "10", "-c:a", "aac", "-shortest",
         "-f", "mpegts", str(path)],
        check=True,
    )
    return path


def add_segments(pool, channel, ts_sample, starts):
    seg_dir = pool.buf_root / channel
    seg_dir.mkdir(parents=True, exist_ok=True)
    for start in starts:
        shutil.copy(ts_sample, seg_dir / f"{start}.ts")


def make_pool(tmp_path, popen=None):
    return monitor.RollingRecorderPool(tmp_path / "buf", popen=popen or FakePopen())


def test_pool_never_exceeds_three_hot_plus_one_on_demand(tmp_path):
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    pool.set_hot(["a", "b", "c", "d", "e"])
    assert sorted(pool.recordings) == ["a", "b", "c"]
    assert pool.start_on_demand("d") is True
    assert pool.start_on_demand("e") is False  # only 1 on-demand slot
    assert len(pool.recordings) == 4 and len(popen.pipelines()) == 4
    assert pool.start_on_demand("a") is True  # already recording: no extra pipeline
    assert len(popen.pipelines()) == 4


def test_pool_drops_channels_leaving_top3_and_deletes_their_buffer(tmp_path, ts_sample):
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    pool.set_hot(["a", "b", "c"])
    add_segments(pool, "c", ts_sample, [BASE])
    pool.set_hot(["a", "b", "x"])
    assert sorted(pool.recordings) == ["a", "b", "x"]
    assert not (pool.buf_root / "c").exists()
    assert len(popen.pipelines()) == 4  # a, b, c, then x (a and b were kept)


def test_pool_restarts_dead_hot_pipeline_and_promotes_on_demand(tmp_path):
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    pool.set_hot(["a"])
    popen.procs[0].returncode = 1  # streamlink exited (stream offline)
    pool.set_hot(["a"])
    assert len(popen.pipelines()) == 2 and pool.is_recording("a")
    pool.start_on_demand("d")
    pool.set_hot(["a", "d"])
    assert not pool.recordings["d"].on_demand
    assert pool.start_on_demand("e") is True  # on-demand slot is free again


def test_pipeline_is_streamlink_piped_into_ffmpeg_copy_segments(tmp_path):
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    pool.set_hot(["gaules"])
    source, sink = popen.procs
    assert source.cmd[:2] == ["streamlink", "--quiet"] and "--stdout" in source.cmd
    assert "twitch.tv/gaules" in source.cmd and "720p60,720p,480p,best" in source.cmd
    assert "stdin" in sink.kwargs  # ffmpeg reads the streamlink pipe
    joined = " ".join(sink.cmd)
    assert "-c copy" in joined and "-f segment" in joined and "-segment_time 10" in joined
    assert "-reset_timestamps 1" in joined and sink.cmd[-1].endswith("buf/gaules/%s.ts")


def test_real_ffmpeg_segmenter_writes_epoch_named_copy_segments(tmp_path):
    seg_dir = tmp_path / "seg"
    seg_dir.mkdir()
    producer = subprocess.Popen(
        ["ffmpeg", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=3",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "10", "-f", "mpegts", "-"],
        stdout=subprocess.PIPE,
    )
    consumer = subprocess.run(monitor.segment_cmd(seg_dir, segment_s=2), stdin=producer.stdout, check=False)
    producer.wait()
    names = sorted(p.name for p in seg_dir.glob("*.ts"))
    assert consumer.returncode == 0 and names
    # offline faster-than-realtime input lands in one wall-clock second, so only the naming is checked
    assert all(n[:-3].isdigit() and abs(int(n[:-3]) - time.time()) < 60 for n in names)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(seg_dir / names[0])],
        capture_output=True, text=True, check=True,
    )
    assert "h264" in probe.stdout  # copied, not re-encoded to something else


def test_cleanup_removes_segments_older_than_300s(tmp_path, ts_sample):
    pool = make_pool(tmp_path)
    pool.set_hot(["a"])
    add_segments(pool, "a", ts_sample, [BASE, BASE + 10])
    now = BASE + 1000
    os.utime(pool.buf_root / "a" / f"{BASE}.ts", (now - 301, now - 301))
    os.utime(pool.buf_root / "a" / f"{BASE + 10}.ts", (now - 299, now - 299))
    pool.cleanup(now)
    assert [s for s, _ in pool.segments("a")] == [BASE + 10]


def test_cleanup_keeps_held_preroll(tmp_path, ts_sample):
    pool = make_pool(tmp_path)
    pool.set_hot(["a"])
    add_segments(pool, "a", ts_sample, [BASE, BASE + 10, BASE + 20])
    now = BASE + 1000
    for _, path in pool.segments("a"):
        os.utime(path, (now - 400, now - 400))
    pool.hold("a", BASE + 10)  # job needs everything from BASE+10 on
    pool.cleanup(now)
    assert [s for s, _ in pool.segments("a")] == [BASE + 10, BASE + 20]
    pool.release_hold("a")
    pool.cleanup(now)
    assert pool.segments("a") == []


def test_set_hot_keeps_hot_channel_with_capture_in_flight(tmp_path, ts_sample):
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    pool.set_hot(["a", "b", "c"])
    add_segments(pool, "c", ts_sample, [BASE])
    pool.hold("c", BASE)  # c is mid-capture
    pool.start_on_demand("x")
    pool.set_hot(["x", "a", "b"])  # c dropped out of the ranking
    assert pool.is_recording("c") and pool.segments("c")
    assert len(pool.recordings) <= 4 and sum(not r.on_demand for r in pool.recordings.values()) == 3


def test_enforce_cap_deletes_oldest_unheld_segments_across_channels(tmp_path, ts_sample):
    pool = make_pool(tmp_path)
    add_segments(pool, "a", ts_sample, [BASE, BASE + 20])
    add_segments(pool, "b", ts_sample, [BASE + 10, BASE + 30])
    size = ts_sample.stat().st_size
    pool.hold("a", BASE)  # a's pre-roll must survive even though it is the oldest
    pool.enforce_cap(2 * size)
    assert [s for s, _ in pool.segments("a")] == [BASE, BASE + 20]
    assert pool.segments("b") == []
    assert monitor.BUFFER_CAP_BYTES == 1_500_000_000


def test_freeze_and_concat_makes_valid_mkv_from_overlapping_segments(tmp_path, ts_sample):
    pool = make_pool(tmp_path)
    add_segments(pool, "a", ts_sample, [BASE, BASE + 10, BASE + 20, BASE + 30])
    dst = tmp_path / "source.mkv"
    out = pool.freeze_and_concat("a", BASE + 12, BASE + 25, dst)
    assert out == dst and dst.is_file()
    assert probe_duration(dst) == pytest.approx(20.0, abs=1.0)  # segments BASE+10 and BASE+20 only
    assert pool.video_start_ts("a", BASE + 12, BASE + 25) == BASE + 10
    assert not list(tmp_path.glob("*.txt")) and not list(tmp_path.glob("*.tmp.mkv"))


def test_freeze_without_segments_raises(tmp_path):
    pool = make_pool(tmp_path)
    with pytest.raises(ValueError):
        pool.freeze_and_concat("a", BASE, BASE + 10, tmp_path / "source.mkv")


def test_write_chat_json_aligns_to_video_start(tmp_path):
    rows = [(BASE - 5, "antes", "ana"), (BASE + 2.0, "oi", "ana"), (BASE + 3.456, "gg", "bia"),
            (BASE + 4.0, "spam", "nightbot"), (BASE + 5.0, "kkk", "ana")]
    dst = tmp_path / "chat.json"
    monitor.write_chat_json(rows, BASE, dst)
    assert json.loads(dst.read_text()) == [[2.0, "oi", 0], [3.46, "gg", 1], [5.0, "kkk", 0]]


class Clock:
    def __init__(self, start):
        self.t = start
        self.on_tick = lambda: None

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.t += seconds
        self.on_tick()


def spike_at(peak):
    return monitor.SpikeEvent("gaules", peak, count10=12, unique_authors=6, expected=2.5, z_score=5.0)


def run_capture(pool, detector, event, staging, clock):
    return asyncio.run(monitor.capture_job(pool, detector, event, staging, now=clock.now, sleep=clock.sleep))


def test_capture_job_builds_job_with_partial_preroll_and_early_postroll_end(tmp_path, ts_sample):
    pool = make_pool(tmp_path)
    add_segments(pool, "gaules", ts_sample, [BASE, BASE + 10, BASE + 20])
    detector = monitor.ChannelSpikeDetector("gaules", t0=BASE)
    detector.record_message(BASE + 12, "ana", "que lance")
    detector.record_message(BASE + 14, "bia", "gg")
    peak = BASE + 25
    clock = Clock(peak)

    job_dir = run_capture(pool, detector, spike_at(peak), tmp_path / "staging", clock)

    job = json.loads((job_dir / "job.json").read_text())
    assert job_dir.name == f"gaules_{peak}" and job["status"] == "pending"
    assert job["partial_preroll"] is True and job["peak_rel_s"] == 25.0
    assert job["z_score"] == 5.0 and job["channel"] == "gaules"
    assert clock.now() - peak == monitor.POST_ROLL_MIN_S  # chat calmed down: stop at the 60s minimum
    assert probe_duration(job_dir / "source.mkv") == pytest.approx(30.0, abs=1.0)
    assert json.loads((job_dir / "chat.json").read_text()) == [[12.0, "que lance", 0], [14.0, "gg", 1]]
    meta = json.loads((job_dir / "meta.json").read_text())
    assert meta["id"] == job_dir.name and meta["extractor"] == "twitch"
    assert not pool.holds and "gaules" not in pool.recordings  # on-demand slot released


def test_capture_job_postroll_runs_to_120s_while_chat_stays_hot(tmp_path, ts_sample):
    pool = make_pool(tmp_path)
    add_segments(pool, "gaules", ts_sample, [BASE, BASE + 10, BASE + 20])
    detector = monitor.ChannelSpikeDetector("gaules", t0=BASE)
    peak = BASE + 25
    clock = Clock(peak)
    clock.on_tick = lambda: [detector.record_message(clock.t, f"u{k}", f"hype {k}") for k in range(5)]

    job_dir = run_capture(pool, detector, spike_at(peak), tmp_path / "staging", clock)

    assert clock.now() - peak == monitor.POST_ROLL_MAX_S
    assert json.loads((job_dir / "job.json").read_text())["status"] == "pending"


def test_capture_job_stops_when_stream_ends_and_rejects_short_source(tmp_path, ts_sample):
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    add_segments(pool, "gaules", ts_sample, [BASE + 20])  # only 10s buffered
    detector = monitor.ChannelSpikeDetector("gaules", t0=BASE)
    peak = BASE + 25
    clock = Clock(peak)
    clock.on_tick = lambda: setattr(popen.procs[0], "returncode", 1)  # live ended during post-roll

    job_dir = run_capture(pool, detector, spike_at(peak), tmp_path / "staging", clock)

    job = json.loads((job_dir / "job.json").read_text())
    assert clock.now() - peak < monitor.POST_ROLL_MIN_S
    assert job["status"] == "rejected" and job["reject_reason"] == "too_short"


def test_capture_job_runs_ffmpeg_and_ffprobe_off_the_event_loop(tmp_path, monkeypatch):
    pool = make_pool(tmp_path)
    add_segments(pool, "gaules", tmp_path / "unused", [])
    (pool.buf_root / "gaules" / f"{BASE}.ts").write_bytes(b"x")

    def slow_concat(channel, start, end, dst):
        time.sleep(0.3)
        dst.write_bytes(b"mkv")
        return dst

    monkeypatch.setattr(pool, "freeze_and_concat", slow_concat)
    monkeypatch.setattr(monitor, "probe_duration", lambda path: time.sleep(0.2) or 60.0)
    detector = monitor.ChannelSpikeDetector("gaules", t0=BASE)
    clock = Clock(BASE + 25 + monitor.POST_ROLL_MAX_S)  # post-roll already over
    event = spike_at(BASE + 25)

    async def scenario():
        ticks = 0
        task = asyncio.create_task(monitor.capture_job(pool, detector, event, tmp_path / "st",
                                                       now=clock.now, sleep=clock.sleep))
        while not task.done():
            ticks += 1
            await asyncio.sleep(0.01)
        return ticks, task.result()

    ticks, job_dir = asyncio.run(scenario())
    assert ticks >= 20  # the loop kept running during ~0.5s of blocking subprocess work
    assert json.loads((job_dir / "job.json").read_text())["status"] == "pending"


def test_capture_job_removes_half_built_job_dir_on_error(tmp_path, monkeypatch):
    pool = make_pool(tmp_path)
    add_segments(pool, "gaules", tmp_path / "unused", [])
    (pool.buf_root / "gaules" / f"{BASE}.ts").write_bytes(b"x")

    def broken_concat(channel, start, end, dst):
        dst.write_bytes(b"partial")
        raise RuntimeError("ffmpeg concat failed")

    monkeypatch.setattr(pool, "freeze_and_concat", broken_concat)
    detector = monitor.ChannelSpikeDetector("gaules", t0=BASE)
    clock = Clock(BASE + 25 + monitor.POST_ROLL_MAX_S)
    with pytest.raises(RuntimeError):
        run_capture(pool, detector, spike_at(BASE + 25), tmp_path / "st", clock)
    assert list((tmp_path / "st").iterdir()) == []
    assert not pool.holds and "gaules" not in pool.recordings


class FailingSinkPopen(FakePopen):
    def __call__(self, cmd, **kwargs):
        if cmd[0] == "ffmpeg":
            raise OSError("ffmpeg not found")
        return super().__call__(cmd, **kwargs)


def test_start_failure_kills_streamlink_and_cleans_its_folder(tmp_path):
    popen = FailingSinkPopen()
    pool = make_pool(tmp_path, popen)
    pool.set_hot(["a"])  # must not raise into the monitor loop
    assert "a" not in pool.recordings and not (pool.buf_root / "a").exists()
    (source,) = popen.procs
    assert source.returncode == -9 and source.stdout.closed
    assert pool.start_on_demand("b") is False


def test_recorders_run_in_their_own_session(tmp_path):
    popen = FakePopen()
    make_pool(tmp_path, popen).set_hot(["a"])
    assert all(p.kwargs.get("start_new_session") is True for p in popen.procs)


class StuckProc(FakeProc):
    def __init__(self, cmd, **kwargs):
        super().__init__(cmd, **kwargs)
        self.reaped = False

    def terminate(self):
        pass  # ignores SIGTERM

    def wait(self, timeout=None):
        if timeout is not None and self.returncode is None:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        self.reaped = True
        return self.returncode


def test_stop_kills_and_reaps_process_that_ignores_terminate(tmp_path):
    procs = []
    pool = make_pool(tmp_path, lambda cmd, **kw: procs.append(StuckProc(cmd, **kw)) or procs[-1])
    pool.set_hot(["a"])
    pool.stop_all()
    assert all(p.returncode == -9 and p.reaped for p in procs)


def test_pool_refuses_invalid_channel_names(tmp_path):
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    assert pool.start_on_demand("../etc") is False
    pool.set_hot(["Bad Name"])
    assert popen.procs == [] and pool.recordings == {}


def test_cleanup_ignores_segment_that_vanished_meanwhile(tmp_path, ts_sample, monkeypatch):
    pool = make_pool(tmp_path)
    pool.set_hot(["a"])
    add_segments(pool, "a", ts_sample, [BASE + 10])
    real = pool.segments
    monkeypatch.setattr(pool, "segments", lambda c: [(BASE, pool.buf_root / c / f"{BASE}.ts")] + real(c))
    now = BASE + 1000
    os.utime(pool.buf_root / "a" / f"{BASE + 10}.ts", (now - 400, now - 400))
    pool.cleanup(now)  # BASE.ts does not exist: no crash, and the old one is still removed
    pool.enforce_cap(0)
    assert real("a") == []


def test_capture_job_without_free_slot_returns_none(tmp_path):
    pool = make_pool(tmp_path)
    assert pool.start_on_demand("other") is True
    detector = monitor.ChannelSpikeDetector("gaules", t0=BASE)
    assert run_capture(pool, detector, spike_at(BASE + 25), tmp_path / "staging", Clock(BASE + 25)) is None
