"""Tests for monitor-twitch: ingest_local, discovery/policy, IRC parser and spike detector."""

import asyncio
import json
import re
import socket
import subprocess
import urllib.error
import urllib.parse

import pytest

from clipes import ingest as ingest_mod
from clipes import monitor
from clipes.ingest import ingest_local

CHAT_ROWS = [[1.0, "kkkk", 0], [2.5, "que isso", 1], [3.0, "GG", 0]]


def make_source_mkv(path, seconds=3):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=10:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


@pytest.fixture
def no_network(monkeypatch):
    """Fails the test if ingest_local tries yt-dlp or any socket connection."""
    real_run = ingest_mod._run

    def guarded_run(cmd, *args, **kwargs):
        assert cmd[0] != "yt-dlp", "ingest_local must not call yt-dlp"
        return real_run(cmd, *args, **kwargs)

    def no_connect(*args, **kwargs):
        raise AssertionError("ingest_local must not open network connections")

    monkeypatch.setattr(ingest_mod, "_run", guarded_run)
    monkeypatch.setattr(socket.socket, "connect", no_connect)


@pytest.fixture
def job_dir(tmp_path):
    job = tmp_path / "jobs" / "gaules_1763380000"
    job.mkdir(parents=True)
    make_source_mkv(job / "source.mkv")
    (job / "chat.json").write_text(json.dumps(CHAT_ROWS), encoding="utf-8")
    (job / "meta.json").write_text(json.dumps({
        "id": "gaules_1763380000",
        "title": "gaules - pico de chat",
        "url": "https://www.twitch.tv/gaules",
        "extractor": "twitch",
    }), encoding="utf-8")
    return job


def test_ingest_local_builds_result_from_disk(job_dir, tmp_path, no_network):
    res = ingest_local(job_dir, tmp_path / "out")

    assert res.video_path == job_dir / "source.mkv"
    assert res.audio_path.name == "audio.wav" and res.audio_path.stat().st_size > 1024
    assert res.video_id == "gaules_1763380000"
    assert res.title == "gaules - pico de chat"
    assert res.url == "https://www.twitch.tv/gaules"
    assert res.chat_messages == CHAT_ROWS
    assert res.duration == pytest.approx(3.0, abs=0.5)
    assert res.out_dir == tmp_path / "out" / "gaules - pico de chat [gaules_1763380000]"
    assert res.cache_dir == res.out_dir / ".cache"
    assert res.heatmap is None


def test_ingest_local_without_chat_flag_ignores_chat(job_dir, tmp_path, no_network):
    assert ingest_local(job_dir, tmp_path / "out", enable_chat=False).chat_messages is None


def test_ingest_local_without_chat_file_or_meta(job_dir, tmp_path, no_network):
    (job_dir / "chat.json").unlink()
    (job_dir / "meta.json").unlink()
    res = ingest_local(job_dir, tmp_path / "out")
    assert res.chat_messages is None
    assert res.video_id == "gaules_1763380000"  # falls back to directory name


def test_ingest_local_accepts_cached_chat_payload(job_dir, tmp_path, no_network):
    (job_dir / "chat.json").write_text(
        json.dumps({"status": "ok", "messages": CHAT_ROWS}), encoding="utf-8"
    )
    assert ingest_local(job_dir, tmp_path / "out").chat_messages == CHAT_ROWS


def test_ingest_local_requires_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_local(tmp_path, tmp_path / "out")


# --- T2: discovery and policy -------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(monitor, "_helix_cache", {})
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_policy(tmp_path, **policy):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def test_discover_reads_channels_txt_and_drops_blocked(clean_env):
    channels_file = clean_env / "channels.txt"
    channels_file.write_text("Gaules\n# comentario\nalanzoka  # inline\n\nloud_coringa\nGaules\n", encoding="utf-8")
    policy = monitor.load_policy(write_policy(clean_env, blocked=["Alanzoka"]))

    assert monitor.discover_channels(channels_file, policy) == ["gaules", "loud_coringa"]


def test_discover_allowed_list_restricts_channels(clean_env):
    channels_file = clean_env / "channels.txt"
    channels_file.write_text("a1\nb2\nc3\n", encoding="utf-8")
    policy = monitor.load_policy(write_policy(clean_env, allowed=["b2"], blocked=["b2", "c3"]))
    assert monitor.discover_channels(channels_file, policy) == []
    policy = monitor.load_policy(write_policy(clean_env, allowed=["b2", "c3"], blocked=["c3"]))
    assert monitor.discover_channels(channels_file, policy) == ["b2"]


def test_discover_missing_file_and_invalid_names(clean_env):
    policy = monitor.ChannelPolicy()
    assert monitor.discover_channels(clean_env / "nope.txt", policy) == []
    channels_file = clean_env / "channels.txt"
    channels_file.write_text("ok_name\nbad name\n" + "x" * 26 + "\n", encoding="utf-8")
    assert monitor.discover_channels(channels_file, policy) == ["ok_name"]


def test_load_policy_defaults_and_delay(clean_env):
    assert monitor.load_policy(None) == monitor.ChannelPolicy()
    assert monitor.load_policy(clean_env / "missing.json") == monitor.ChannelPolicy()
    policy = monitor.load_policy(write_policy(clean_env, delay_h={"Gaules": 24}))
    assert policy.delay_h == {"gaules": 24.0}


def test_discover_with_helix_paginates_dedups_and_caps_at_80(clean_env, monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret-value")
    calls = []

    def fake_http(req):
        calls.append(req)
        if req.full_url == monitor.HELIX_TOKEN_URL:
            assert req.get_method() == "POST"
            return {"access_token": "tok"}, {}
        assert req.get_header("Authorization") == "Bearer tok"
        query = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        assert int(query["first"][0]) <= 80
        if "after" not in query:
            logins = [f"user{i}" for i in range(50)]
            return {"data": [{"user_login": n} for n in logins], "pagination": {"cursor": "c1"}}, {}
        logins = [f"user{i}" for i in range(40, 100)]  # overlaps with page 1
        return {"data": [{"user_login": n} for n in logins], "pagination": {}}, {}

    monkeypatch.setattr(monitor, "_http_json", fake_http)
    channels_file = clean_env / "channels.txt"
    channels_file.write_text("gaules\nuser3\n", encoding="utf-8")

    result = monitor.discover_channels(channels_file, monitor.ChannelPolicy(), game_id="32982", limit=200)

    assert result[0] == "gaules"
    assert len(result) == 80 and len(set(result)) == 80
    assert "game_id=32982" in calls[1].full_url
    assert not any("secret-value" in p.read_text(errors="ignore") for p in clean_env.iterdir() if p.is_file())


def test_discover_helix_failure_falls_back_to_channels_txt(clean_env, monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")

    def boom(req):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(monitor, "_http_json", boom)
    channels_file = clean_env / "channels.txt"
    channels_file.write_text("gaules\n", encoding="utf-8")
    assert monitor.discover_channels(channels_file, monitor.ChannelPolicy()) == ["gaules"]


def test_discover_helix_polls_at_most_once_per_minute(clean_env, monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")
    calls = []

    def fake_http(req):
        calls.append(req.full_url)
        if req.full_url == monitor.HELIX_TOKEN_URL:
            return {"access_token": "tok"}, {}
        return {"data": [{"user_login": "streamer1"}], "pagination": {}}, {}

    monkeypatch.setattr(monitor, "_http_json", fake_http)
    channels_file = clean_env / "channels.txt"
    channels_file.write_text("", encoding="utf-8")
    policy = monitor.ChannelPolicy()
    assert monitor.discover_channels(channels_file, policy) == ["streamer1"]
    n_calls = len(calls)
    assert monitor.discover_channels(channels_file, policy) == ["streamer1"]  # served from cache
    assert len(calls) == n_calls


def test_helix_cache_is_per_game_and_limit(clean_env, monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")
    queries = []

    def fake_http(req):
        if req.full_url == monitor.HELIX_TOKEN_URL:
            return {"access_token": "tok"}, {}
        query = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        queries.append((query.get("game_id", [None])[0], query["first"][0]))
        game = query.get("game_id", ["all"])[0]
        return {"data": [{"user_login": f"s{game}{i}"} for i in range(int(query["first"][0]))], "pagination": {}}, {}

    monkeypatch.setattr(monitor, "_http_json", fake_http)
    channels_file = clean_env / "channels.txt"
    channels_file.write_text("", encoding="utf-8")
    policy = monitor.ChannelPolicy()
    assert monitor.discover_channels(channels_file, policy, game_id="21779", limit=2) == ["s217790", "s217791"]
    assert monitor.discover_channels(channels_file, policy, game_id="33214", limit=2) == ["s332140", "s332141"]
    assert len(monitor.discover_channels(channels_file, policy, game_id="33214", limit=3)) == 3
    assert monitor.discover_channels(channels_file, policy, game_id="21779", limit=2) == ["s217790", "s217791"]
    assert queries == [("21779", "2"), ("33214", "2"), ("33214", "3")]  # last call came from its own cache


def test_channels_file_hash_prefixed_names_are_channels_not_comments(clean_env):
    channels_file = clean_env / "channels.txt"
    channels_file.write_text("#gaules\n# comentario\n#\n#alanzoka  # nota\ntab\t# nota\n", encoding="utf-8")
    assert monitor.discover_channels(channels_file, monitor.ChannelPolicy()) == ["gaules", "alanzoka", "tab"]


# --- T3: IRC parser, detector, client -----------------------------------------------------------


def irc(text, author="viewer1", channel="gaules", tags=""):
    prefix = f"@{tags} " if tags else ""
    return f"{prefix}:{author}!{author}@{author}.tmi.twitch.tv PRIVMSG #{channel} :{text}"


def test_parse_irc_line_extracts_tags_channel_author_text():
    tags, channel, author, text = monitor.parse_irc_line(
        irc("que jogada kkkk", "Viewer1", "Gaules", "room-id=1;display-name=Viewer1") + "\r\n"
    )
    assert (channel, author, text) == ("gaules", "viewer1", "que jogada kkkk")
    assert tags["room-id"] == "1"


def test_parse_irc_line_ignores_shared_chat_copies():
    assert monitor.parse_irc_line(irc("oi", tags="room-id=1;source-room-id=2")) is None
    assert monitor.parse_irc_line(irc("oi", tags="room-id=1;source-room-id=1")) is not None
    assert monitor.parse_irc_line(irc("oi", tags="room-id=1")) is not None


def test_parse_irc_line_ignores_bots_commands_and_non_privmsg():
    assert monitor.parse_irc_line(irc("bem vindo", author="Nightbot")) is None
    assert monitor.parse_irc_line(irc("!sr musica")) is None
    assert monitor.parse_irc_line("PING :tmi.twitch.tv") is None
    assert monitor.parse_irc_line(":tmi.twitch.tv 001 justinfan1 :Welcome, GLHF!") is None
    assert monitor.parse_irc_line(":x!x@x.tmi.twitch.tv JOIN #gaules") is None


def feed(det, start, end, per_sec, authors):
    """per_sec messages per second from t=start to t=end, rotating over `authors` names."""
    n = 0
    for sec in range(start, end):
        for k in range(per_sec):
            det.record_message(sec + k / per_sec, f"user{n % authors}", f"msg {n}")
            n += 1


def small_channel(t0=0):
    det = monitor.ChannelSpikeDetector("pequeno", t0=t0)
    for sec in range(0, 100, 4):  # ~2.5 msgs per 10s
        det.record_message(sec + 0.5, f"user{sec % 5}", f"papo {sec}")
    return det


def burst(det, start, n_msgs, authors, prefix="user"):
    for k in range(n_msgs):
        det.record_message(start + k * (4.9 / n_msgs), f"{prefix}{k % authors}", f"pico {k}")


def test_small_channel_spikes_on_legit_burst():
    det = small_channel()
    burst(det, 95.0, 12, authors=6)
    event = det.check_spike(100.0)
    assert event is not None
    assert (event.channel, event.peak_ts) == ("pequeno", 100.0)
    assert event.count10 >= 12 and event.unique_authors >= 6
    assert event.expected == pytest.approx(2.5, abs=1.0)
    assert event.z_score >= 3.5 and event.count10 / event.expected >= 3.0


def test_no_spike_during_warmup():
    det = monitor.ChannelSpikeDetector("pequeno", t0=0)
    burst(det, 35.0, 12, authors=6)
    assert det.check_spike(40.0) is None


def test_warmup_counts_from_each_channels_join():
    """A channel joined 16s into the run is still warming up at global t=60s (the rakin false spike)."""
    early = monitor.ChannelSpikeDetector("cedo", float("inf"))
    late = monitor.ChannelSpikeDetector("tarde", float("inf"))
    early.start_watching(0.0)
    late.start_watching(16.0)
    late.start_watching(500.0)  # reconnect re-JOINs; warmup stays anchored at the first JOIN
    assert late.t0 == 16.0
    burst(early, 50.0, 12, authors=6)
    burst(late, 50.0, 12, authors=6)
    assert early.check_spike(60.0) is not None  # 60s since its own JOIN
    assert late.check_spike(61.0) is None  # only 45s since its JOIN, same wall clock
    burst(late, 70.0, 12, authors=6)
    assert late.check_spike(76.0) is not None

    before_join = monitor.ChannelSpikeDetector("novo", float("inf"))
    before_join.record_message(16.0, "ana", "oi")  # message with no JOIN callback yet
    assert before_join.t0 == 16.0

    joined = []
    run_irc_loop(["a", "b"], [], on_join=lambda ch, _t: joined.append(ch))
    assert joined == ["a", "b"]  # one callback per JOIN, in order


def test_single_author_flood_does_not_spike():
    det = small_channel()
    burst(det, 95.0, 20, authors=1)
    assert det.check_spike(100.0) is None
    burst(det, 96.0, 20, authors=3)  # 3 authors total is still below 4
    assert det.check_spike(101.0) is None


def test_bots_and_commands_are_not_counted():
    det = small_channel()
    before = det.count10(100.0)
    for k in range(20):
        det.record_message(95.0 + k * 0.2, "nightbot", f"spam {k}")
        det.record_message(95.0 + k * 0.2, f"user{k}", "!comando")
    assert det.count10(100.0) == before
    assert det.check_spike(100.0) is None


def test_shared_chat_duplicates_do_not_spike_via_parser():
    det = small_channel()
    for k in range(30):
        parsed = monitor.parse_irc_line(irc(f"gg {k}", f"user{k}", "pequeno", "room-id=1;source-room-id=9"))
        if parsed:
            det.record_message(95.0 + k * 0.15, parsed[2], parsed[3])
    assert det.check_spike(100.0) is None


def test_cooldown_blocks_second_spike_for_180s():
    det = small_channel()
    burst(det, 95.0, 12, authors=6)
    assert det.check_spike(100.0) is not None
    burst(det, 145.0, 12, authors=6)
    assert det.check_spike(150.0) is None  # inside cooldown
    burst(det, 285.0, 12, authors=6)
    assert det.check_spike(290.0) is not None  # cooldown over


def test_big_channel_needs_3x_baseline():
    det = monitor.ChannelSpikeDetector("grande", t0=0)
    feed(det, 0, 120, per_sec=4, authors=30)  # baseline 40 msgs / 10s
    feed(det, 120, 125, per_sec=8, authors=30)  # 80 in last 10s: ratio 2.0 even though z is high
    assert det.check_spike(125.0) is None
    feed(det, 125, 130, per_sec=22, authors=30)  # 5s*22 + 5s*8 -> ratio > 3
    event = det.check_spike(130.0)
    assert event is not None and event.expected == pytest.approx(40.0, abs=6.0)


def test_expected_baseline_uses_bounded_per_second_counts():
    det = monitor.ChannelSpikeDetector("grande", t0=0)
    feed(det, 0, 2000, per_sec=4, authors=30)  # long stream at a steady 40 msgs / 10s
    assert len(det.buckets) <= monitor.CHAT_KEEP_S + 1  # memory stays bounded by the chat window
    assert det.expected(2000.0) == pytest.approx(40.0)
    det.record_message(1990.5, "atrasado", "fora de ordem")  # late (out-of-order) message is still counted
    assert sum(n for s, n in det.buckets if s == 1990) == 5


def test_check_spike_evaluates_at_most_once_per_second():
    det = small_channel()
    assert det.check_spike(99.5) is None  # calm
    burst(det, 95.6, 12, authors=6)
    assert det.check_spike(100.0) is None  # same second as the last evaluation
    assert det.check_spike(100.5) is not None


def test_chat_rows_returns_window_as_text_author_rows():
    det = monitor.ChannelSpikeDetector("x", t0=0)
    det.record_message(10.0, "Ana", "oi")
    det.record_message(20.0, "bob", "ola")
    assert det.chat_rows(15.0, 25.0) == [(20.0, "ola", "bob")]


class FakeWriter:
    def __init__(self):
        self.lines = []
        self.closed = False

    def write(self, data):
        self.lines.append(data.decode())

    async def drain(self):
        pass

    def close(self):
        self.closed = True


def run_irc_loop(channels, server_lines, on_message=None, on_join=None):
    """Run irc_chat_loop against a scripted connection: one session, then a failed reconnect, then stop."""
    stop = asyncio.Event()
    writer = FakeWriter()
    received, sleeps, connects = [], [], []

    async def open_connection(host, port):
        connects.append((host, port))
        if len(connects) == 1:
            reader = asyncio.StreamReader()
            reader.feed_data(("\r\n".join(server_lines) + "\r\n").encode())
            reader.feed_eof()
            return reader, writer
        stop.set()
        raise OSError("network down")

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    asyncio.run(monitor.irc_chat_loop(
        channels, on_message or (lambda *msg: received.append(msg)),
        open_connection=open_connection, sleep=fake_sleep, stop=stop, on_join=on_join,
    ))
    return writer, received, sleeps, connects


def test_irc_loop_anonymous_login_paced_joins_and_filters():
    server = [
        "PING :tmi.twitch.tv",
        irc("que lance", "ana", "a", "room-id=1"),
        irc("spam", "nightbot", "a", "room-id=1"),
        irc("dup", "bia", "a", "room-id=1;source-room-id=7"),
    ]
    writer, received, sleeps, connects = run_irc_loop(["a", "b", "c"], server)
    sent = "".join(writer.lines)

    assert connects[0] == (monitor.IRC_HOST, monitor.IRC_PORT)
    assert re.search(r"NICK justinfan\d+\r\n", sent) and "PASS SCHMOOPIIE" in sent
    assert "CAP REQ :twitch.tv/tags" in sent
    assert [l.strip() for l in writer.lines if l.startswith("JOIN")] == ["JOIN #a", "JOIN #b", "JOIN #c"]
    assert "PONG :tmi.twitch.tv" in sent
    assert [(c, a, t) for c, _, a, t in received] == [("a", "ana", "que lance")]
    assert sleeps[:2] == [monitor.JOIN_INTERVAL_S] * 2 and monitor.JOIN_INTERVAL_S >= 0.6
    assert writer.closed


def test_irc_loop_reconnects_with_exponential_backoff():
    _, _, sleeps, connects = run_irc_loop(["a"], [irc("oi", "ana", "a")])
    assert len(connects) == 2
    assert sleeps == [1.0, 2.0]  # EOF after a good session, then a failed connect


def test_irc_loop_joins_at_most_80_channels():
    channels = [f"canal{i}" for i in range(100)]
    writer, _, sleeps, _ = run_irc_loop(channels, [])
    assert sum(1 for l in writer.lines if l.startswith("JOIN")) == 80
    assert all(s >= 0.6 for s in sleeps[:79])


def test_irc_loop_survives_callback_error_and_reconnects_on_overlong_line():
    received = []

    def on_message(channel, t, author, text):
        if text == "boom":
            raise RuntimeError("detector bug")
        received.append(text)

    server = [irc("boom", "ana", "a"), irc("depois", "bia", "a"), "x" * 70_000, irc("nunca", "cid", "a")]
    _, _, sleeps, connects = run_irc_loop(["a"], server, on_message)
    assert received == ["depois"]  # the failing callback did not kill the session
    assert len(connects) == 2 and sleeps == [1.0, 2.0]  # overlong line -> reconnect, not a crash


def test_irc_loop_never_joins_invalid_channel_names():
    writer, _, _, _ = run_irc_loop(["ok_1", "bad\r\nPRIVMSG #x :spam", "UPPER", "a" * 26], [])
    assert [l.strip() for l in writer.lines if l.startswith("JOIN")] == ["JOIN #ok_1"]


def test_run_local_pipeline_usa_min_per_hour_zero_e_mantem_duracoes(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from clipes import cli

    seen_args = []

    def fake_run_pipeline(args):
        seen_args.append(args)
        ing = cli.ingest(args.url, Path(args.out_dir), args.live_minutes, args.chat)
        (ing.out_dir / "clips.json").write_text(json.dumps({"video_id": ing.video_id, "clips": []}), encoding="utf-8")
        return 0

    def fake_ingest_local(job_dir: Path, out_root: Path, enable_chat: bool):
        od = out_root / job_dir.name
        od.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(video_id=job_dir.name, out_dir=od)

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr("clipes.ingest.ingest_local", fake_ingest_local)

    out_root = tmp_path / "out"
    j1 = tmp_path / "live_1"
    j1.mkdir()
    (j1 / "job.json").write_text(json.dumps({"job_id": "live_1", "max_dur": 50.0, "min_dur": 20.0}), encoding="utf-8")
    monitor.run_local_pipeline(j1, out_root)
    assert seen_args[-1].min_per_hour == 0.0
    assert seen_args[-1].max_dur == 50.0
    assert seen_args[-1].min_dur == 20.0

    j2 = tmp_path / "spike_2"
    j2.mkdir()
    monitor.run_local_pipeline(j2, out_root)
    assert seen_args[-1].min_per_hour == 0.0
    assert seen_args[-1].max_dur == 45.0
    assert seen_args[-1].min_dur == 15.0


@pytest.fixture
def fake_wav(monkeypatch):
    """Replaces extract_wav/probe_duration with fakes; records extract_wav calls."""
    calls = []

    def fake_extract(src, wav):
        calls.append((src, wav))
        wav.write_bytes(b"x" * 2048)
        return wav

    monkeypatch.setattr(ingest_mod, "extract_wav", fake_extract)
    monkeypatch.setattr(ingest_mod, "probe_duration", lambda p: 3.0)
    return calls


def test_ingest_local_reuses_job_audio_wav(job_dir, tmp_path, no_network, fake_wav):
    (job_dir / "audio.wav").write_bytes(b"w" * 4096)
    res = ingest_local(job_dir, tmp_path / "out")
    assert fake_wav == []
    assert res.audio_path.is_file()
    assert res.audio_path.read_bytes() == b"w" * 4096
    assert not (job_dir / "audio.wav").exists()


def test_ingest_local_extracts_wav_when_job_has_none(job_dir, tmp_path, no_network, fake_wav):
    res = ingest_local(job_dir, tmp_path / "out")
    assert len(fake_wav) == 1
    assert res.audio_path.is_file()


def test_ingest_local_extracts_wav_when_job_audio_is_tiny(job_dir, tmp_path, no_network, fake_wav):
    (job_dir / "audio.wav").write_bytes(b"w" * 512)
    res = ingest_local(job_dir, tmp_path / "out")
    assert len(fake_wav) == 1
    assert res.audio_path.is_file()
