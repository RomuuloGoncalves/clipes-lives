"""Auditoria W2-A: gravador (C-01 monitor, M-08, M-09, B-07, B-08)."""

import subprocess

from clipes import monitor
from tests.test_monitor_recorder import BASE, FakePopen, FakeProc, add_segments, make_pool, ts_sample  # noqa: F401


def test_ytdlp_cmd_retries_infinitely_with_socket_timeout():
    cmd = monitor.ytdlp_live_cmd("https://youtu.be/x")
    for flag, value in (("--retries", "infinite"), ("--fragment-retries", "infinite"), ("--socket-timeout", "30")):
        assert cmd[cmd.index(flag) + 1] == value
    assert "--no-progress" in cmd


def test_restart_on_demand_keeps_buffer_and_slot_kind(tmp_path, ts_sample):  # noqa: F811
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    assert pool.start_on_demand("a") is True
    add_segments(pool, "a", ts_sample, [BASE])
    popen.procs[0].returncode = 1  # source died
    assert not pool.is_recording("a")
    assert pool.restart_on_demand("a") is True
    assert pool.is_recording("a") and pool.recordings["a"].on_demand
    assert [s for s, _ in pool.segments("a")] == [BASE]  # buffer preserved
    assert len(popen.pipelines()) == 2


def test_restart_on_demand_without_recording_acts_as_start(tmp_path):
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    assert pool.restart_on_demand("a") is True
    assert pool.recordings["a"].on_demand and len(popen.pipelines()) == 1


def test_restart_on_demand_returns_false_when_start_fails(tmp_path, ts_sample):  # noqa: F811
    class FailAfterFirst(FakePopen):
        def __call__(self, cmd, **kwargs):
            if len(self.procs) >= 2:
                raise OSError("boom")
            return super().__call__(cmd, **kwargs)

    pool = make_pool(tmp_path, FailAfterFirst())
    pool.start_on_demand("a")
    add_segments(pool, "a", ts_sample, [BASE])
    assert pool.restart_on_demand("a") is False
    assert "a" not in pool.recordings and pool.segments("a")  # buffer survives a failed restart


def test_select_drops_segments_before_a_gap(tmp_path, ts_sample):  # noqa: F811
    pool = make_pool(tmp_path)
    add_segments(pool, "a", ts_sample, [BASE, BASE + 10, BASE + 20, BASE + 60, BASE + 70])
    chosen = pool._select("a", BASE, BASE + 75)
    assert [s for s, _ in chosen] == [BASE + 60, BASE + 70]
    assert pool.video_start_ts("a", BASE, BASE + 75) == BASE + 60


def test_select_tolerates_small_keyframe_jitter(tmp_path, ts_sample):  # noqa: F811
    pool = make_pool(tmp_path)
    add_segments(pool, "a", ts_sample, [BASE, BASE + 12, BASE + 23])
    assert [s for s, _ in pool._select("a", BASE, BASE + 30)] == [BASE, BASE + 12, BASE + 23]


def test_buffer_cap_env(monkeypatch, capsys):
    monkeypatch.delenv("CLIPES_BUFFER_CAP_GB", raising=False)
    assert monitor.buffer_cap_bytes() == 1_500_000_000
    monkeypatch.setenv("CLIPES_BUFFER_CAP_GB", "3")
    assert monitor.buffer_cap_bytes() == 3_000_000_000
    for bad in ("abc", "-1", "0"):
        monkeypatch.setenv("CLIPES_BUFFER_CAP_GB", bad)
        assert monitor.buffer_cap_bytes() == 1_500_000_000
    assert "invalido" in capsys.readouterr().out


def test_stderr_goes_to_channel_log_and_is_closed_on_stop(tmp_path):
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    pool.start_on_demand("a")
    source, sink = popen.procs
    log = source.kwargs["stderr"]
    assert sink.kwargs["stderr"] is log and log is not subprocess.DEVNULL
    assert log.name == str(pool.buf_root / "a.log")
    pool.stop_all()
    assert log.closed


def test_log_rotates_when_too_big(tmp_path):
    pool = make_pool(tmp_path)
    pool.buf_root.mkdir(parents=True)
    (pool.buf_root / "a.log").write_bytes(b"x" * (monitor.LOG_MAX_BYTES + 1))
    pool.start_on_demand("a")
    assert (pool.buf_root / "a.log.1").stat().st_size == monitor.LOG_MAX_BYTES + 1
    assert (pool.buf_root / "a.log").stat().st_size == 0
    pool.stop_all()


def test_stop_logs_returncode_of_dead_recorder(tmp_path, capsys):
    popen = FakePopen()
    pool = make_pool(tmp_path, popen)
    pool.start_on_demand("a")
    popen.procs[0].returncode = 3
    pool.stop_all()
    assert "returncode=3" in capsys.readouterr().out


def test_stop_terminates_source_and_waits_before_waiting_on_ffmpeg(tmp_path):
    events = []

    class Ordered(FakeProc):
        def terminate(self):
            events.append(("terminate", self.cmd[0]))
            super().terminate()

        def wait(self, timeout=None):
            events.append(("wait", self.cmd[0]))
            return super().wait(timeout)

    procs = []
    pool = make_pool(tmp_path, lambda cmd, **kw: procs.append(Ordered(cmd, **kw)) or procs[-1])
    pool.start_on_demand("a")
    pool.stop_all()
    assert events[:3] == [("terminate", "streamlink"), ("wait", "streamlink"), ("wait", "ffmpeg")]
    assert ("terminate", "ffmpeg") not in events  # ffmpeg closed by itself on EOF


def test_stop_terminates_ffmpeg_if_it_does_not_close_in_time(tmp_path):
    class StubbornSink(FakeProc):
        def wait(self, timeout=None):
            if timeout is not None and self.returncode is None:
                raise subprocess.TimeoutExpired(self.cmd, timeout)
            return self.returncode

    procs = []

    def popen(cmd, **kw):
        procs.append((StubbornSink if cmd[0] == "ffmpeg" else FakeProc)(cmd, **kw))
        return procs[-1]

    pool = make_pool(tmp_path, popen)
    pool.start_on_demand("a")
    pool.stop_all()
    assert procs[1].returncode == -15  # terminate worked after the first timeout
