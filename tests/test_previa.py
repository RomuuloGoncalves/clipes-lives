"""Tests for Discord preview recompression (DISC-02)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from clipes.previa import DEFAULT_DISCORD_MAX_MB, bitrate_alvo, preview_for_discord


def _make_sparse_file(path: Path, size_bytes: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.truncate(size_bytes)
    return path


def test_returns_original_when_under_90_percent_limit(tmp_path: Path):
    src = _make_sparse_file(tmp_path / "clip_01_teste.mp4", 5 * 1024 * 1024)
    cache_dir = tmp_path / "cache"
    called: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        called.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    res = preview_for_discord(src, duration=30.0, cache_dir=cache_dir, limit_mb=10.0, run=fake_run)
    assert res == src
    assert called == []


def test_recompresses_when_above_limit_with_formula_bitrate(tmp_path: Path):
    src = _make_sparse_file(tmp_path / "clip_02_grande.mp4", 25 * 1024 * 1024)
    cache_dir = tmp_path / "cache"
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        Path(cmd[-1]).write_bytes(b"preview-mp4")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    duration = 60.0
    limit_mb = 10.0
    res = preview_for_discord(src, duration=duration, cache_dir=cache_dir, limit_mb=limit_mb, run=fake_run)

    expected_path = cache_dir / "discord_clip_02_grande.mp4"
    assert res == expected_path
    assert expected_path.is_file()
    assert src.stat().st_size == 25 * 1024 * 1024  # original preserved untouched
    assert len(captured) == 1

    cmd = captured[0]
    assert cmd[cmd.index("-vf") + 1] == "scale=720:1280"
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-preset") + 1] == "veryfast"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "96k"
    assert cmd[cmd.index("-map_metadata") + 1] == "-1"
    assert cmd[cmd.index("-map_metadata:s:v") + 1] == "-1"
    assert cmd[cmd.index("-map_metadata:s:a") + 1] == "-1"
    assert cmd[cmd.index("-map_chapters") + 1] == "-1"
    assert cmd[cmd.index("-fflags") + 1] == "+bitexact"
    assert cmd[cmd.index("-flags:v") + 1] == "+bitexact"
    assert cmd[cmd.index("-flags:a") + 1] == "+bitexact"

    expected_total_kbps = int((limit_mb * 1024 * 1024 * 0.90 * 8) / (1000 * duration))
    expected_video_kbps = max(400, min(2500, expected_total_kbps - 96))
    assert cmd[cmd.index("-b:v") + 1] == f"{expected_video_kbps}k"
    assert cmd[cmd.index("-maxrate") + 1] == f"{round(expected_video_kbps * 1.2)}k"
    assert cmd[cmd.index("-bufsize") + 1] == f"{expected_video_kbps * 2}k"


def test_clamps_bitrate_at_2500k_and_uses_env_default(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLIPES_DISCORD_MAX_MB", "10")
    src = _make_sparse_file(tmp_path / "clip_03_curto.mp4", 10 * 1024 * 1024)
    cache_dir = tmp_path / "cache"
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        Path(cmd[-1]).write_bytes(b"preview-mp4")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    # 10s with 10 MB limit -> total ~7549 kbps -> clamped to 2500k
    res = preview_for_discord(src, duration=10.0, cache_dir=cache_dir, run=fake_run)
    assert res == cache_dir / "discord_clip_03_curto.mp4"
    assert captured[0][captured[0].index("-b:v") + 1] == "2500k"


def test_returns_none_when_bitrate_below_400kbps(tmp_path: Path):
    src = _make_sparse_file(tmp_path / "clip_04_longo.mp4", 25 * 1024 * 1024)
    cache_dir = tmp_path / "cache"
    called: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        called.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    # 180s with 10 MB limit -> total ~419 kbps -> video ~323 kbps < 400 kbps -> None
    res = preview_for_discord(src, duration=180.0, cache_dir=cache_dir, limit_mb=10.0, run=fake_run)
    assert res is None
    assert called == []


def test_real_ffmpeg_3s_synthetic_preview_fits_target(tmp_path: Path):
    src = tmp_path / "synth_3s.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=3",
            "-f", "lavfi", "-i", "sine=f=440:d=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            str(src),
        ],
        check=True,
    )
    orig_size = src.stat().st_size
    limit_mb = 0.5
    target_bytes = int(limit_mb * 1024 * 1024 * 0.90)
    assert orig_size > target_bytes

    cache_dir = tmp_path / "cache"
    res = preview_for_discord(src, duration=3.0, cache_dir=cache_dir, limit_mb=limit_mb)
    assert res is not None
    assert res == cache_dir / "discord_synth_3s.mp4"
    assert res.is_file()
    assert res.stat().st_size <= target_bytes
    assert src.stat().st_size == orig_size


def test_default_limit_is_10mb_without_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIPES_DISCORD_MAX_MB", raising=False)
    assert DEFAULT_DISCORD_MAX_MB == 10.0

    # 9.5 MB is under 98% of 10 MB (9.8 MB): returned as-is, no ffmpeg
    src_95m = _make_sparse_file(tmp_path / "clip_95m.mp4", int(9.5 * 1024 * 1024))
    called: list[list[str]] = []
    res = preview_for_discord(
        src_95m,
        duration=60.0,
        cache_dir=tmp_path / "cache",
        run=lambda cmd, **kw: called.append(cmd),
    )
    assert res == src_95m
    assert called == []

    # bitrate_alvo respects custom teto and audio_kbps and can drop below 400
    v_master = bitrate_alvo(60.0, 20.0, teto=6000, audio_kbps=160)
    expected = int((20 * 1024 * 1024 * 0.90 * 8) / (1000 * 60)) - 160
    assert v_master == expected
    assert bitrate_alvo(10.0, 20.0, teto=6000, audio_kbps=160) == 6000
    assert bitrate_alvo(500.0, 20.0, teto=6000, audio_kbps=160) < 400


def test_retries_at_80_percent_when_first_output_over_98_percent(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIPES_DISCORD_MAX_MB", raising=False)
    src = _make_sparse_file(tmp_path / "clip_retry.mp4", 25 * 1024 * 1024)
    cache_dir = tmp_path / "cache"
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        size = 10_500_000 if len(captured) == 1 else 1_000_000  # 1st too big, 2nd fits
        _make_sparse_file(Path(cmd[-1]), size)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    res = preview_for_discord(src, duration=60.0, cache_dir=cache_dir, run=fake_run)

    assert res == cache_dir / "discord_clip_retry.mp4"
    assert res.is_file()
    assert len(captured) == 2
    v1 = int(captured[0][captured[0].index("-b:v") + 1].rstrip("k"))
    v2 = int(captured[1][captured[1].index("-b:v") + 1].rstrip("k"))
    assert v2 == round(v1 * 0.8)


def test_returns_none_when_retry_also_over_98_percent(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIPES_DISCORD_MAX_MB", raising=False)
    src = _make_sparse_file(tmp_path / "clip_big.mp4", 25 * 1024 * 1024)
    cache_dir = tmp_path / "cache"
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        _make_sparse_file(Path(cmd[-1]), 10_500_000)  # always too big
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    res = preview_for_discord(src, duration=60.0, cache_dir=cache_dir, run=fake_run)

    assert res is None
    assert len(captured) == 2
    assert not any(p.name.startswith(".tmp_discord_") for p in cache_dir.iterdir())
    assert not (cache_dir / "discord_clip_big.mp4").exists()


def test_no_retry_when_80_percent_bitrate_below_400(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIPES_DISCORD_MAX_MB", raising=False)
    src = _make_sparse_file(tmp_path / "clip_edge.mp4", 25 * 1024 * 1024)
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        _make_sparse_file(Path(cmd[-1]), 10_500_000)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    # 140 s with 10 MB -> video ~443 kbps (>=400), 80% of it ~354 kbps (<400)
    res = preview_for_discord(src, duration=140.0, cache_dir=tmp_path / "cache", run=fake_run)

    assert res is None
    assert len(captured) == 1
