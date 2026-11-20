"""Unit tests for clipes.capacidade (derived from CAP-02, CAP-03 and Edge Cases)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import types
import wave
from pathlib import Path

import numpy as np
import pytest

import clipes.capacidade as capacidade_mod
from clipes.asr import transcribe_candidates, transcribe_fast_blocks
from clipes.capacidade import capacity, cpu_threads, worker_id
from clipes.models import CandidatePeak


def _make_sysfs(root: Path, cpu_max: str | None = "300000 100000", mem_max: str | None = "max") -> Path:
    cgroup = root / "fs" / "cgroup"
    cgroup.mkdir(parents=True, exist_ok=True)
    if cpu_max is not None:
        (cgroup / "cpu.max").write_text(f"{cpu_max}\n", encoding="utf-8")
    if mem_max is not None:
        (cgroup / "memory.max").write_text(f"{mem_max}\n", encoding="utf-8")
    return root


def _make_proc(root: Path, mem_available_kb: int = 4 * 1024 * 1024, load1: float | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "meminfo").write_text(
        f"MemTotal:       16777216 kB\nMemAvailable:   {mem_available_kb} kB\n",
        encoding="utf-8",
    )
    if load1 is not None:
        (root / "loadavg").write_text(f"{load1:.2f} 0.50 0.40 1/200 1234\n", encoding="utf-8")
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
    proc = _make_proc(tmp_path / "proc1", mem_available_kb=6 * 1024 * 1024, load1=1.5)

    def missing_smi(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    cap = capacity(out, sysfs=sysfs, proc=proc, run=missing_smi)
    assert cap["cpus"] == 3
    assert cap["cpu_pct"] == 50.0
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
    assert cap_missing["cpu_pct"] is None
    assert cap_missing["gpu"] is None

    # load1 acima da cota de CPUs é limitado a 100.0%
    proc_cheio = _make_proc(tmp_path / "proc_cheio", load1=9.0)
    cap_cheio = capacity(out, sysfs=sysfs, proc=proc_cheio, run=fail_smi)
    assert cap_cheio["cpu_pct"] == 100.0

    (out / ".temp").write_text("sem-sensor\n", encoding="utf-8")
    cap_sem = capacity(out, sysfs=sysfs, proc=proc, run=fail_smi)
    assert cap_sem["temp_c"] is None


def test_capacity_temp_stale_file_returns_none_and_fresh_returns_int(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    temp_file = out / ".temp"
    temp_file.write_text("78\n", encoding="utf-8")

    old = time.time() - 31
    os.utime(temp_file, (old, old))
    assert capacidade_mod._read_temp_c(out) is None

    now = time.time()
    os.utime(temp_file, (now, now))
    assert capacidade_mod._read_temp_c(out) == 78


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


def _write_wav(path: Path, seconds: float = 2.0, sr: int = 16000) -> None:
    samples = np.zeros(int(seconds * sr), dtype=np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())


def test_asr_uses_cpu_threads_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_CPU_THREADS", raising=False)
    wav_path = tmp_path / "audio.wav"
    _write_wav(wav_path, seconds=2.0)

    received_threads: list[int] = []

    class FakeInfo:
        language = "pt"
        language_probability = 0.99

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            received_threads.append(kwargs["cpu_threads"])

        def transcribe(self, audio_slice, **kwargs):
            return [], FakeInfo()

    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)

    expected_real = cpu_threads()
    cands = [CandidatePeak(peak=1.0, raw_score=0.9, start_win=0.0, end_win=2.0)]
    (tmp_path / "c1").mkdir()
    (tmp_path / "c3").mkdir()
    transcribe_candidates(wav_path, cands, tmp_path / "c1", model_size="small")
    transcribe_fast_blocks(wav_path, [(0.0, 2.0, 1.0)], tmp_path / "c2", model_size="small")
    assert received_threads == [expected_real, expected_real]

    # Sentinel override proves ASR delegates default threads to capacidade.cpu_threads()
    received_threads.clear()
    monkeypatch.setattr(capacidade_mod, "cpu_threads", lambda: 5)
    transcribe_candidates(wav_path, cands, tmp_path / "c3", model_size="small")
    transcribe_fast_blocks(wav_path, [(0.0, 2.0, 1.0)], tmp_path / "c4", model_size="small")
    assert received_threads == [5, 5]
