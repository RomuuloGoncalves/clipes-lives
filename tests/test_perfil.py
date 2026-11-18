"""Unit tests for clipes.perfil (derived from PERF-01 and PERF-02)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipes import __main__ as entry, monitor, perfil


def _make_job_dir(tmp_path: Path, name: str = "live_win") -> Path:
    job_dir = tmp_path / name
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "source.mkv").write_bytes(b"fake-mkv")
    return job_dir


def test_medir_writes_perfil_json_and_calculates_max_lives_one_for_100s_300s(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    job_dir = _make_job_dir(tmp_path)

    clock_t = [10.0]

    def fake_clock() -> float:
        return clock_t[0]

    def fake_pipeline(jdir: Path, oroot: Path) -> dict:
        assert jdir == job_dir
        assert oroot == out_dir.resolve()
        clock_t[0] += 100.0
        return {"clips": []}

    monkeypatch.setattr(perfil, "probe_duration", lambda p: 300.0)
    monkeypatch.setattr(perfil, "cpu_threads", lambda: 3)

    res = perfil.medir(job_dir, fake_pipeline, clock=fake_clock, now=lambda: 1_763_416_800.0)
    assert res["segundos_live"] == 300.0
    assert res["segundos_proc"] == 100.0
    assert res["razao"] == pytest.approx(100.0 / 300.0, rel=1e-5)
    assert res["cpus"] == 3
    assert "medido_em" in res and res["medido_em"]

    saved = json.loads((out_dir / ".perfil.json").read_text(encoding="utf-8"))
    assert saved == res
    assert perfil.max_lives(out_dir, cpus=3) == 1


def test_medir_30s_for_300s_gives_max_lives_six(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    job_dir = _make_job_dir(tmp_path)

    clock_t = [0.0]

    def fake_pipeline(jdir: Path, oroot: Path) -> dict:
        clock_t[0] += 30.0
        return {"clips": []}

    monkeypatch.setattr(perfil, "probe_duration", lambda p: 300.0)
    monkeypatch.setattr(perfil, "cpu_threads", lambda: 8)

    res = perfil.medir(job_dir, fake_pipeline, clock=lambda: clock_t[0])
    assert res["razao"] == pytest.approx(0.1, rel=1e-6)
    assert perfil.max_lives(out_dir, cpus=8) == 6


def test_max_lives_returns_one_when_missing_or_cpus_changed(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    assert perfil.max_lives(out_dir, cpus=4) == 1

    (out_dir / ".perfil.json").write_text(
        json.dumps({"razao": 0.1, "segundos_live": 300.0, "segundos_proc": 30.0, "medido_em": "2026-11-17T00:00:00+00:00", "cpus": 8}),
        encoding="utf-8",
    )
    assert perfil.max_lives(out_dir, cpus=8) == 6
    assert perfil.max_lives(out_dir, cpus=4) == 1


def test_medir_rejects_missing_or_short_source(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    monkeypatch.setenv("OUT_DIR", str(out_dir))

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="source.mkv"):
        perfil.medir(empty_dir, lambda *_: {})

    short_dir = _make_job_dir(tmp_path, "short")
    monkeypatch.setattr(perfil, "probe_duration", lambda p: 200.0)
    with pytest.raises(ValueError, match="300"):
        perfil.medir(short_dir, lambda *_: {})
    assert not (out_dir / ".perfil.json").exists()


def test_bench_live_short_source_exits_2_with_message(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "out"
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    short_dir = _make_job_dir(tmp_path, "short_cli")
    monkeypatch.setattr(perfil, "probe_duration", lambda p: 200.0)

    rc = entry.main(["bench-live", str(short_dir)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "300" in (captured.err + captured.out)
    assert not (out_dir / ".perfil.json").exists()


def test_bench_live_routes_to_perfil_medir_and_returns_zero(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "out"
    monkeypatch.setenv("OUT_DIR", str(out_dir))
    job_dir = _make_job_dir(tmp_path, "valid_cli")

    clock_t = [0.0]

    def fake_pipeline(jdir: Path, oroot: Path) -> dict:
        clock_t[0] += 30.0
        return {"clips": []}

    monkeypatch.setattr(perfil, "probe_duration", lambda p: 300.0)
    monkeypatch.setattr(perfil, "cpu_threads", lambda: 4)
    monkeypatch.setattr(monitor, "run_local_pipeline", fake_pipeline)
    monkeypatch.setattr(perfil.time, "perf_counter", lambda: clock_t[0])

    rc = entry.main(["bench-live", str(job_dir)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "max_lives=6" in captured.out
    saved = json.loads((out_dir / ".perfil.json").read_text(encoding="utf-8"))
    assert saved["cpus"] == 4
    assert saved["razao"] == pytest.approx(0.1, rel=1e-6)

