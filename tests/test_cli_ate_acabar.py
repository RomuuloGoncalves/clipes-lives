"""Tests for removed --ate-acabar CLI option and intact VOD / --live-minutes flows (UNI-01 AC 4)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from clipes import cli
from clipes.models import IngestResult

REMOVED_MSG = "removido: use ./clipar servico (e /live adicionar no Discord)"


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


def test_parser_esconde_ate_acabar_na_ajuda_e_preserva_live_minutes():
    parser = cli.build_parser()
    assert "--ate-acabar" not in parser.format_help()

    default_args = parser.parse_args(["https://www.twitch.tv/gaules"])
    assert default_args.ate_acabar is False
    assert default_args.live_minutes == 20.0

    args = parser.parse_args(["https://www.twitch.tv/gaules", "--live-minutes", "15"])
    assert args.live_minutes == 15.0


def test_ate_acabar_sai_codigo_2_sem_chamar_follow_live_nem_ingest(tmp_path: Path, monkeypatch, capsys):
    def fail_probe(url: str):
        raise AssertionError("probe_metadata must not be called when --ate-acabar is removed")

    def fail_follow(*args, **kwargs):
        raise AssertionError("follow_live must not be called when --ate-acabar is removed")

    def fail_ingest(**kwargs):
        raise AssertionError("ingest must not be called when --ate-acabar is removed")

    monkeypatch.setattr(cli, "probe_metadata", fail_probe)
    monkeypatch.setattr(cli, "follow_live", fail_follow)
    monkeypatch.setattr(cli, "ingest", fail_ingest)

    rc = cli.main([
        "https://www.twitch.tv/gaules",
        "--ate-acabar",
        "--out-dir", str(tmp_path / "out"),
    ])

    cap = capsys.readouterr()
    assert rc == 2
    assert REMOVED_MSG in (cap.err + cap.out)


def test_vod_sem_ate_acabar_roda_pipeline_normal(tmp_path: Path, monkeypatch):
    ing = _make_ing(tmp_path)
    ingest_calls: list[dict] = []

    def fail_follow(*args, **kwargs):
        raise AssertionError("follow_live must not be called for VOD")

    monkeypatch.setattr(cli, "follow_live", fail_follow)
    monkeypatch.setattr(cli, "ingest", lambda **kwargs: (ingest_calls.append(kwargs), ing)[1])
    monkeypatch.setattr(cli, "compute_rms_db", lambda *a, **k: np.zeros(4, np.float32))
    monkeypatch.setattr(cli, "pick_candidate_peaks", lambda *a, **k: [])

    rc = cli.main([
        "https://www.twitch.tv/videos/123",
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


def test_ate_acabar_com_min_dur_invalido_sai_2_sem_probing(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "probe_metadata",
        lambda url: (_ for _ in ()).throw(AssertionError("probe_metadata must not run")),
    )
    monkeypatch.setattr(
        cli, "follow_live",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("follow_live must not run")),
    )

    rc = cli.main(["https://www.twitch.tv/gaules", "--ate-acabar", "--min-dur", "0"])
    cap = capsys.readouterr()
    assert rc == 2
    assert REMOVED_MSG in (cap.err + cap.out)
