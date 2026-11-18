"""Unit tests for clipes.capacidade (derived from CAP-02, CAP-03 and Edge Cases)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from clipes.capacidade import capacity, cpu_threads, worker_id


def _make_sysfs(root: Path, cpu_max: str | None = "300000 100000", mem_max: str | None = "max") -> Path:
    cgroup = root / "fs" / "cgroup"
    cgroup.mkdir(parents=True, exist_ok=True)
    if cpu_max is not None:
        (cgroup / "cpu.max").write_text(f"{cpu_max}\n", encoding="utf-8")
    if mem_max is not None:
        (cgroup / "memory.max").write_text(f"{mem_max}\n", encoding="utf-8")
    return root


def _make_proc(root: Path, mem_available_kb: int = 4 * 1024 * 1024) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "meminfo").write_text(
        f"MemTotal:       16777216 kB\nMemAvailable:   {mem_available_kb} kB\n",
        encoding="utf-8",
    )
    return root


def test_cpu_threads_reads_cgroup_quota(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_CPU_THREADS", raising=False)
    sysfs = _make_sysfs(tmp_path / "sys", cpu_max="300000 100000")
    assert cpu_threads(sysfs=sysfs) == 3


def test_cpu_threads_minimum_is_one_for_fractional_quota(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_CPU_THREADS", raising=False)
    sysfs = _make_sysfs(tmp_path / "sys", cpu_max="50000 100000")
    assert cpu_threads(sysfs=sysfs) == 1


def test_cpu_threads_falls_back_to_os_cpu_count_on_max_or_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_CPU_THREADS", raising=False)
    sysfs = _make_sysfs(tmp_path / "sys", cpu_max="max 100000")
    assert cpu_threads(sysfs=sysfs) == (os.cpu_count() or 1)
    assert cpu_threads(sysfs=tmp_path / "missing") == (os.cpu_count() or 1)


def test_cpu_threads_respects_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_CPU_THREADS", "2")
    sysfs = _make_sysfs(tmp_path / "sys", cpu_max="300000 100000")
    assert cpu_threads(sysfs=sysfs) == 2


def test_capacity_picks_min_of_cgroup_and_memavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_CPU_THREADS", raising=False)
    out = tmp_path / "out"
    out.mkdir()
    (out / ".temp").write_text("78\n", encoding="utf-8")

    # cgroup limit = 2 GiB, host MemAvailable = 6 GiB -> 2.0 GiB
    sysfs = _make_sysfs(tmp_path / "sys1", cpu_max="300000 100000", mem_max=str(2 * 1024**3))
    proc = _make_proc(tmp_path / "proc1", mem_available_kb=6 * 1024 * 1024)

    def missing_smi(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    cap = capacity(out, sysfs=sysfs, proc=proc, run=missing_smi)
    assert cap["cpus"] == 3
    assert cap["ram_gb"] == pytest.approx(2.0, rel=1e-2)
    assert cap["temp_c"] == 78
    assert cap["gpu"] is None
    assert cap["score"] == 3
    expected_free = round(shutil.disk_usage(out).free / (1024**3), 2)
    assert cap["disco_livre_gb"] == pytest.approx(expected_free, abs=0.05)

    # cgroup limit = 8 GiB, host MemAvailable = 2.5 GiB -> 2.5 GiB
    sysfs2 = _make_sysfs(tmp_path / "sys2", cpu_max="400000 100000", mem_max=str(8 * 1024**3))
    proc2 = _make_proc(tmp_path / "proc2", mem_available_kb=int(2.5 * 1024 * 1024))
    cap2 = capacity(out, sysfs=sysfs2, proc=proc2, run=missing_smi)
    assert cap2["ram_gb"] == pytest.approx(2.5, rel=1e-2)


def test_capacity_temp_sem_sensor_or_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_CPU_THREADS", raising=False)
    out = tmp_path / "out"
    out.mkdir()
    sysfs = _make_sysfs(tmp_path / "sys")
    proc = _make_proc(tmp_path / "proc")

    def fail_smi(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], returncode=1, stdout="", stderr="no gpu")

    cap_missing = capacity(out, sysfs=sysfs, proc=proc, run=fail_smi)
    assert cap_missing["temp_c"] is None
    assert cap_missing["gpu"] is None

    (out / ".temp").write_text("sem-sensor\n", encoding="utf-8")
    cap_sem = capacity(out, sysfs=sysfs, proc=proc, run=fail_smi)
    assert cap_sem["temp_c"] is None


def test_capacity_with_fake_gpu_doubles_score(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_CPU_THREADS", raising=False)
    out = tmp_path / "out"
    out.mkdir()
    sysfs = _make_sysfs(tmp_path / "sys", cpu_max="400000 100000")
    proc = _make_proc(tmp_path / "proc")

    def fake_smi(cmd, *args, **kwargs):
        assert "nvidia-smi" in cmd[0]
        return subprocess.CompletedProcess(
            cmd,
            returncode=0,
            stdout="NVIDIA GeForce GTX 1660 SUPER, 6144, 15, 52\n",
            stderr="",
        )

    cap = capacity(out, sysfs=sysfs, proc=proc, run=fake_smi)
    assert cap["cpus"] == 4
    assert cap["gpu"] == {
        "nome": "NVIDIA GeForce GTX 1660 SUPER",
        "vram_mb": 6144,
        "uso_pct": 15,
        "temp_c": 52,
    }
    assert cap["score"] == 8


def test_worker_id_creates_once_and_reuses(tmp_path):
    out = tmp_path / "out"
    first = worker_id(out)
    assert re.fullmatch(r"w-[0-9a-f]{12}", first)
    assert (out / ".worker_id").read_text(encoding="utf-8").strip() == first
    second = worker_id(out)
    assert second == first
