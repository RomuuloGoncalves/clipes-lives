"""Tests for --ate-acabar CLI option (T26 / LIVE-02)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from clipes import cli
from clipes.models import IngestResult


def _make_ing(tmp_path: Path, video_id: str = "vod1") -> IngestResult:
    out_dir = tmp_path / "out" / video_id
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(parents=True)
    wav_path = cache_dir / "audio.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    return IngestResult(
        video_id=video_id,
        title="VOD teste",
        url="https://www.twitch.tv/videos/123",
        extractor="twitch:vod",
        is_live=False,
        duration=120.0,
        audio_path=wav_path,
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=out_dir,
        cache_dir=cache_dir,
        channel="twitch.tv/gaules",
    )


def test_parser_has_ate_acabar_and_preserves_live_minutes():
    default_args = cli.build_parser().parse_args(["https://www.twitch.tv/gaules"])
    assert default_args.ate_acabar is False
    assert default_args.live_minutes == 20.0

    args = cli.build_parser().parse_args([
        "https://www.twitch.tv/gaules", "--ate-acabar", "--live-minutes", "15",
    ])
    assert args.ate_acabar is True
    assert args.live_minutes == 15.0


def test_ate_acabar_with_active_live_delegates_to_follow_live(tmp_path: Path, monkeypatch):
    out_root = tmp_path / "out"
    probed: list[str] = []
    followed: list[dict] = []

    def fake_probe(url: str):
        probed.append(url)
        return {"id": "live1", "is_live": True}

    def fake_follow(url: str, root: Path, **kwargs):
        followed.append({"url": url, "out_root": root, "kwargs": kwargs})
        return {"status": "ended", "jobs": ["gaules_100"]}

    def fail_ingest(**kwargs):
        raise AssertionError("ingest must not be called when --ate-acabar handles an active live")

    monkeypatch.setattr(cli, "probe_metadata", fake_probe)
    monkeypatch.setattr(cli, "follow_live", fake_follow)
    monkeypatch.setattr(cli, "ingest", fail_ingest)

    rc = cli.main([
        "https://www.twitch.tv/gaules",
        "--ate-acabar",
        "--out-dir", str(out_root),
        "--max-dur", "55",
    ])

    assert rc == 0
    assert probed == ["https://www.twitch.tv/gaules"]
    assert len(followed) == 1
    assert followed[0]["url"] == "https://www.twitch.tv/gaules"
    assert followed[0]["out_root"] == out_root.resolve()
    assert followed[0]["kwargs"].get("max_dur") == 55.0


def test_ate_acabar_without_active_live_runs_normal_pipeline(tmp_path: Path, monkeypatch):
    ing = _make_ing(tmp_path)
    ingest_calls: list[dict] = []

    monkeypatch.setattr(cli, "probe_metadata", lambda url: {"id": "vod1", "is_live": False})

    def fail_follow(*args, **kwargs):
        raise AssertionError("follow_live must not be called when stream is not live")

    monkeypatch.setattr(cli, "follow_live", fail_follow)
    monkeypatch.setattr(cli, "ingest", lambda **kwargs: (ingest_calls.append(kwargs), ing)[1])
    monkeypatch.setattr(cli, "compute_rms_db", lambda *a, **k: np.zeros(4, np.float32))
    monkeypatch.setattr(cli, "pick_candidate_peaks", lambda *a, **k: [])

    rc = cli.main([
        "https://www.twitch.tv/videos/123",
        "--ate-acabar",
        "--out-dir", str(tmp_path / "out"),
        "--no-llm",
        "--skip-render",
    ])

    assert rc == 0
    assert len(ingest_calls) == 1
    assert (ing.out_dir / "clips.json").is_file()


def test_without_ate_acabar_keeps_live_minutes_flow_intact(tmp_path: Path, monkeypatch):
    ing = _make_ing(tmp_path, video_id="live_fixed")
    captured_live_minutes: list[float] = []

    def fail_probe(url: str):
        raise AssertionError("cli must not call probe_metadata directly without --ate-acabar")

    def fail_follow(*args, **kwargs):
        raise AssertionError("follow_live must not be called without --ate-acabar")

    def fake_ingest(**kwargs):
        captured_live_minutes.append(kwargs["live_minutes"])
        return ing

    monkeypatch.setattr(cli, "probe_metadata", fail_probe)
    monkeypatch.setattr(cli, "follow_live", fail_follow)
    monkeypatch.setattr(cli, "ingest", fake_ingest)
    monkeypatch.setattr(cli, "compute_rms_db", lambda *a, **k: np.zeros(4, np.float32))
    monkeypatch.setattr(cli, "pick_candidate_peaks", lambda *a, **k: [])

    rc = cli.main([
        "https://www.twitch.tv/gaules",
        "--live-minutes", "12",
        "--out-dir", str(tmp_path / "out"),
        "--no-llm",
        "--skip-render",
    ])

    assert rc == 0
    assert captured_live_minutes == [12.0]
    assert (ing.out_dir / "clips.json").is_file()


def test_ate_acabar_invalid_duration_exits_2_before_probing(monkeypatch):
    monkeypatch.setattr(
        cli, "probe_metadata",
        lambda url: (_ for _ in ()).throw(AssertionError("probe_metadata must not run on invalid args")),
    )
    monkeypatch.setattr(
        cli, "follow_live",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("follow_live must not run on invalid args")),
    )

    rc = cli.main(["https://www.twitch.tv/gaules", "--ate-acabar", "--min-dur", "0"])
    assert rc == 2


def test_ate_acabar_com_uri_passa_coord_para_follow_live_e_sem_uri_segue_local(tmp_path: Path, monkeypatch):
    """Achado 1: `--ate-acabar` builds Coord when CLIPES_MONGO_URI is set, and never imports pymongo without URI."""
    import os
    import subprocess
    import sys

    import mongomock
    import pymongo

    from clipes.coord import Coord

    client = mongomock.MongoClient()
    monkeypatch.setenv("CLIPES_MONGO_URI", "mongodb+srv://user:secret@cluster0.example.mongodb.net/clipes_lives")
    monkeypatch.setattr(pymongo, "MongoClient", lambda *a, **kw: client)
    monkeypatch.setattr(cli, "probe_metadata", lambda url: {"id": "live777", "is_live": True})

    seen: list[dict] = []

    def fake_follow(url: str, root: Path, **kwargs):
        seen.append(kwargs)
        return {"status": "ended", "jobs": []}

    monkeypatch.setattr(cli, "follow_live", fake_follow)

    rc = cli.main(["https://www.twitch.tv/gaules", "--ate-acabar", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    assert len(seen) == 1
    assert isinstance(seen[0].get("coord"), Coord)
    assert seen[0].get("stream_id") == "live777"

    script = (
        "import sys, tempfile\n"
        "from clipes import cli\n"
        "seen = []\n"
        "cli.probe_metadata = lambda url: {'id': 'live1', 'is_live': True}\n"
        "cli.follow_live = lambda url, out, **kw: (seen.append(kw), {'status': 'ended', 'jobs': []})[1]\n"
        "with tempfile.TemporaryDirectory() as td:\n"
        "    rc = cli.main(['https://www.twitch.tv/gaules', '--ate-acabar', '--out-dir', td])\n"
        "assert rc == 0 and len(seen) == 1 and seen[0].get('coord') is None\n"
        "assert 'pymongo' not in sys.modules\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "CLIPES_MONGO_URI"}
    res = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr + res.stdout
