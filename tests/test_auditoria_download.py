"""Testes de auditoria para download_clip_window (M-15) e record_live_stream (B-05)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from clipes import ingest as ingest_mod
from clipes import render as render_mod


def _cp(cmd: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=cmd, returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# M-15: render.download_clip_window
# ---------------------------------------------------------------------------


def test_download_clip_window_attempt3_uses_force_keyframes_at_cuts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if len(calls) < 3:
            return _cp(cmd, returncode=1, stderr=f"falha {len(calls)}")
        out = tmp_path / "seg_01.mp4"
        out.write_bytes(b"mp4data")
        return _cp(cmd, returncode=0)

    monkeypatch.setattr(render_mod, "js_runtime_args", lambda: [])
    monkeypatch.setattr(render_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)

    got = render_mod.download_clip_window("https://youtu.be/abc", 10.0, 40.0, tmp_path, idx=1)

    assert got == tmp_path / "seg_01.mp4"
    assert len(calls) == 3
    for cmd in calls:
        assert "--force-keyframes-at-cuts" in cmd
    # Mantem demais parametros da tentativa 3
    attempt3 = calls[2]
    assert "--download-sections" in attempt3
    assert "b[height<=1080]/best" in attempt3
    assert "https://youtu.be/abc" in attempt3


def test_download_clip_window_timeout_cleans_leftovers_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    leftovers_before_attempt2: list[Path] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if len(calls) == 1:
            (tmp_path / "seg_02.mp4.part").write_bytes(b"parcial")
            (tmp_path / "seg_02.webm").write_bytes(b"sobra")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=600, stderr="stalled")
        leftovers_before_attempt2.extend(tmp_path.glob("seg_02.*"))
        out = tmp_path / "seg_02.mp4"
        out.write_bytes(b"ok")
        return _cp(cmd, returncode=0)

    monkeypatch.setattr(render_mod, "js_runtime_args", lambda: [])
    monkeypatch.setattr(render_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)

    got = render_mod.download_clip_window("https://youtu.be/abc", 5.0, 25.0, tmp_path, idx=2)

    assert got == tmp_path / "seg_02.mp4"
    assert len(calls) == 2
    assert leftovers_before_attempt2 == []


def test_download_clip_window_all_timeouts_raises_runtime_error_and_cleans_leftovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        (tmp_path / "seg_03.mp4.part").write_bytes(b"parcial")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=600, stderr=b"rede travada")

    monkeypatch.setattr(render_mod, "js_runtime_args", lambda: [])
    monkeypatch.setattr(render_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match=r"timeout.*600"):
        render_mod.download_clip_window("https://youtu.be/abc", 0.0, 30.0, tmp_path, idx=3)

    assert len(calls) == 3
    assert list(tmp_path.glob("seg_03.*")) == []


# ---------------------------------------------------------------------------
# B-05: ingest.record_live_stream
# ---------------------------------------------------------------------------


def _touch_wav_stub(src: Path, wav: Path) -> Path:
    wav.write_bytes(b"RIFF" + b"\x00" * 2048)
    return wav


def test_record_live_stream_ffmpeg_timeout_falls_back_to_ytdlp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if cmd[0] == "yt-dlp" and "-g" in cmd:
            return _cp(cmd, 0, stdout="https://example.com/v\nhttps://example.com/a\n")
        if cmd[0] == "ffmpeg":
            (tmp_path / "live_source.tmp.mkv").write_bytes(b"x" * 20000)
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout or 240)
        if cmd[0] == "yt-dlp" and "--downloader" in cmd:
            (tmp_path / "live_source.mkv").write_bytes(b"y" * 20000)
            return _cp(cmd, 0)
        if cmd[0] == "ffprobe":
            return _cp(cmd, 0, stdout="55.0\n")
        raise AssertionError(f"Comando inesperado: {cmd}")

    monkeypatch.setattr(ingest_mod, "js_runtime_args", lambda: [])
    monkeypatch.setattr(ingest_mod, "_run", fake_run)
    monkeypatch.setattr(ingest_mod, "extract_wav", _touch_wav_stub)

    wav, vid = ingest_mod.record_live_stream("https://youtu.be/live1", 1.0, tmp_path)

    assert vid == tmp_path / "live_source.mkv"
    assert wav == tmp_path / "audio.wav"
    assert not (tmp_path / "live_source.tmp.mkv").exists()
    assert any(c[0] == "yt-dlp" and "--downloader" in c for c in calls)


def test_record_live_stream_fallback_timeout_raises_runtime_error_and_cleans_leftovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd: list[str], timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "yt-dlp" and "-g" in cmd:
            return _cp(cmd, 1, stdout="", stderr="no urls")
        if cmd[0] == "yt-dlp" and "--downloader" in cmd:
            (tmp_path / "live_source.mkv").write_bytes(b"z" * 20000)
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout or 240)
        raise AssertionError(f"Comando inesperado: {cmd}")

    monkeypatch.setattr(ingest_mod, "js_runtime_args", lambda: [])
    monkeypatch.setattr(ingest_mod, "_run", fake_run)
    monkeypatch.setattr(ingest_mod, "extract_wav", _touch_wav_stub)

    with pytest.raises(RuntimeError, match=r"timeout"):
        ingest_mod.record_live_stream("https://youtu.be/live2", 1.0, tmp_path)

    assert list(tmp_path.glob("live_source.*")) == []


def test_record_live_stream_rejects_short_recording_on_ffmpeg_and_accepts_valid_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe_durations = iter(["10.0\n", "50.0\n"])
    ffmpeg_ran = False
    fallback_ran = False

    def fake_run(cmd: list[str], timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        nonlocal ffmpeg_ran, fallback_ran
        if cmd[0] == "yt-dlp" and "-g" in cmd:
            return _cp(cmd, 0, stdout="https://example.com/av\n")
        if cmd[0] == "ffmpeg":
            ffmpeg_ran = True
            # > 10 KB mas so 10s (para seconds=60, minimo e 30s)
            (tmp_path / "live_source.tmp.mkv").write_bytes(b"a" * 25000)
            return _cp(cmd, 0)
        if cmd[0] == "yt-dlp" and "--downloader" in cmd:
            fallback_ran = True
            (tmp_path / "live_source.mkv").write_bytes(b"b" * 25000)
            return _cp(cmd, 0)
        if cmd[0] == "ffprobe":
            return _cp(cmd, 0, stdout=next(probe_durations))
        raise AssertionError(f"Comando inesperado: {cmd}")

    monkeypatch.setattr(ingest_mod, "js_runtime_args", lambda: [])
    monkeypatch.setattr(ingest_mod, "_run", fake_run)
    monkeypatch.setattr(ingest_mod, "extract_wav", _touch_wav_stub)

    _, vid = ingest_mod.record_live_stream("https://youtu.be/live3", 1.0, tmp_path)

    assert ffmpeg_ran
    assert fallback_ran
    assert vid == tmp_path / "live_source.mkv"
    assert not (tmp_path / "live_source.tmp.mkv").exists()


def test_record_live_stream_rejects_short_recording_in_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd: list[str], timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "yt-dlp" and "-g" in cmd:
            return _cp(cmd, 1, stdout="")
        if cmd[0] == "yt-dlp" and "--downloader" in cmd:
            # > 10 KB, mas duracao < 50% de 60s
            (tmp_path / "live_source.mkv").write_bytes(b"c" * 30000)
            return _cp(cmd, 0)
        if cmd[0] == "ffprobe":
            return _cp(cmd, 0, stdout="12.0\n")
        raise AssertionError(f"Comando inesperado: {cmd}")

    monkeypatch.setattr(ingest_mod, "js_runtime_args", lambda: [])
    monkeypatch.setattr(ingest_mod, "_run", fake_run)
    monkeypatch.setattr(ingest_mod, "extract_wav", _touch_wav_stub)

    with pytest.raises(RuntimeError, match=r"curta"):
        ingest_mod.record_live_stream("https://youtu.be/live4", 1.0, tmp_path)

    assert list(tmp_path.glob("live_source.*")) == []


def test_record_live_stream_removes_stale_live_source_before_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_mp4 = tmp_path / "live_source.mp4"
    stale_mp4.write_bytes(b"velho" * 5000)
    stale_short_mkv = tmp_path / "live_source.mkv"
    stale_short_mkv.write_bytes(b"curto" * 5000)

    seen_before_fallback: list[Path] = []

    def fake_run(cmd: list[str], timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "ffprobe":
            # live_source.mkv existente e curto demais para reuso (< 90%)
            return _cp(cmd, 0, stdout="5.0\n")
        if cmd[0] == "yt-dlp" and "-g" in cmd:
            return _cp(cmd, 1, stdout="")
        if cmd[0] == "yt-dlp" and "--downloader" in cmd:
            seen_before_fallback.extend(tmp_path.glob("live_source.*"))
            return _cp(cmd, 1, stderr="erro de rede")
        raise AssertionError(f"Comando inesperado: {cmd}")

    monkeypatch.setattr(ingest_mod, "js_runtime_args", lambda: [])
    monkeypatch.setattr(ingest_mod, "_run", fake_run)
    monkeypatch.setattr(ingest_mod, "extract_wav", _touch_wav_stub)

    with pytest.raises(RuntimeError, match=r"erro de rede"):
        ingest_mod.record_live_stream("https://youtu.be/live5", 1.0, tmp_path)

    assert seen_before_fallback == []
    assert not stale_mp4.exists()
    assert not stale_short_mkv.exists()


@pytest.mark.parametrize(
    ("urls_stdout", "expected_maps", "forbidden_map"),
    [
        (
            "https://example.com/v\nhttps://example.com/a\n",
            ["-map", "0:v:0", "-map", "1:a:0"],
            None,
        ),
        (
            "https://example.com/av\n",
            ["-map", "0"],
            "1:a:0",
        ),
    ],
)
def test_record_live_stream_ffmpeg_explicit_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    urls_stdout: str,
    expected_maps: list[str],
    forbidden_map: str | None,
) -> None:
    ff_cmds: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "yt-dlp" and "-g" in cmd:
            return _cp(cmd, 0, stdout=urls_stdout)
        if cmd[0] == "ffmpeg":
            ff_cmds.append(list(cmd))
            (tmp_path / "live_source.tmp.mkv").write_bytes(b"v" * 20000)
            return _cp(cmd, 0)
        if cmd[0] == "ffprobe":
            return _cp(cmd, 0, stdout="58.0\n")
        raise AssertionError(f"Comando inesperado: {cmd}")

    monkeypatch.setattr(ingest_mod, "js_runtime_args", lambda: [])
    monkeypatch.setattr(ingest_mod, "_run", fake_run)
    monkeypatch.setattr(ingest_mod, "extract_wav", _touch_wav_stub)

    _, vid = ingest_mod.record_live_stream("https://youtu.be/live6", 1.0, tmp_path)

    assert vid == tmp_path / "live_source.mkv"
    assert len(ff_cmds) == 1
    cmd = ff_cmds[0]
    # Verifica sequencia exata de flags -map no comando ffmpeg
    found_seq = any(
        cmd[i : i + len(expected_maps)] == expected_maps
        for i in range(len(cmd) - len(expected_maps) + 1)
    )
    assert found_seq, f"Esperado {expected_maps} em {cmd}"
    if forbidden_map is not None:
        assert forbidden_map not in cmd


def test_record_live_stream_reuses_valid_cached_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = tmp_path / "live_source.mkv"
    cached.write_bytes(b"ok" * 10000)
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"w" * 2048)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if cmd[0] == "ffprobe":
            return _cp(cmd, 0, stdout="58.0\n")
        raise AssertionError(f"Nao deveria gravar de novo: {cmd}")

    monkeypatch.setattr(ingest_mod, "_run", fake_run)

    got_wav, got_vid = ingest_mod.record_live_stream("https://youtu.be/live7", 1.0, tmp_path)

    assert got_wav == wav
    assert got_vid == cached
    assert len(calls) == 1 and calls[0][0] == "ffprobe"
