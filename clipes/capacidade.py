"""Detecção de capacidade do PC (threads da cota, RAM, disco, temperatura, GPU e worker_id)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable


def cpu_threads(sysfs: str | Path = "/sys") -> int:
    """Return CLIPES_CPU_THREADS if set, else floor(quota/period) from cgroup cpu.max, else os.cpu_count()."""
    env = os.environ.get("CLIPES_CPU_THREADS", "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass

    cpu_max = Path(sysfs) / "fs" / "cgroup" / "cpu.max"
    try:
        parts = cpu_max.read_text(encoding="utf-8").strip().split()
    except OSError:
        parts = []

    if len(parts) >= 2 and parts[0] != "max":
        try:
            quota = int(parts[0])
            period = int(parts[1])
            if period > 0:
                return max(1, quota // period)
        except ValueError:
            pass

    return os.cpu_count() or 1


def _read_cgroup_memory_bytes(sysfs: Path) -> int | None:
    mem_max = sysfs / "fs" / "cgroup" / "memory.max"
    try:
        raw = mem_max.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or raw == "max":
        return None
    try:
        val = int(raw)
        return val if val >= 0 else None
    except ValueError:
        return None


def _read_mem_available_bytes(proc: Path) -> int | None:
    meminfo = proc / "meminfo"
    try:
        lines = meminfo.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1]) * 1024
                except ValueError:
                    return None
    return None


def _read_cpu_pct(proc: Path, cpus: int) -> float | None:
    load1: float | None = None
    try:
        parts = (proc / "loadavg").read_text(encoding="utf-8").strip().split()
        if parts:
            load1 = float(parts[0])
    except (OSError, ValueError):
        load1 = None
    if load1 is None and proc == Path("/proc"):
        try:
            load1 = os.getloadavg()[0]
        except OSError:
            load1 = None
    if load1 is None or load1 < 0:
        return None
    return round(min(100.0, (load1 / max(1, cpus)) * 100.0), 1)


def _read_temp_c(out_dir: Path) -> int | None:
    temp_file = out_dir / ".temp"
    try:
        raw = temp_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or raw == "sem-sensor":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_num(token: str) -> int | float | None:
    s = token.strip()
    if not s or s.upper() in ("[N/A]", "N/A", "[NOT SUPPORTED]"):
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return int(val) if val.is_integer() else val


def _probe_gpu(run: Callable[..., Any]) -> dict[str, Any] | None:
    cmd = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,utilization.gpu,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        res = run(cmd, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(res, "returncode", 1) != 0:
        return None
    lines = [ln.strip() for ln in (getattr(res, "stdout", "") or "").splitlines() if ln.strip()]
    if not lines:
        return None
    parts = [p.strip() for p in lines[0].split(",")]
    if len(parts) < 4 or not parts[0]:
        return None
    return {
        "nome": parts[0],
        "vram_mb": _parse_num(parts[1]),
        "uso_pct": _parse_num(parts[2]),
        "temp_c": _parse_num(parts[3]),
    }


def capacity(
    out_dir: str | Path,
    *,
    sysfs: str | Path = "/sys",
    proc: str | Path = "/proc",
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Return host/container capacity snapshot for heartbeat and worker scoring."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    sysfs_path = Path(sysfs)
    proc_path = Path(proc)

    cpus = cpu_threads(sysfs=sysfs_path)
    cpu_pct = _read_cpu_pct(proc_path, cpus)
    mem_candidates = [
        v for v in (_read_cgroup_memory_bytes(sysfs_path), _read_mem_available_bytes(proc_path))
        if v is not None
    ]
    ram_gb = round(min(mem_candidates) / (1024**3), 2) if mem_candidates else 0.0
    disco_livre_gb = round(shutil.disk_usage(out_path).free / (1024**3), 2)
    temp_c = _read_temp_c(out_path)
    gpu = _probe_gpu(run)
    score = cpus * (2 if gpu is not None else 1)

    return {
        "cpus": cpus,
        "cpu_pct": cpu_pct,
        "ram_gb": ram_gb,
        "disco_livre_gb": disco_livre_gb,
        "temp_c": temp_c,
        "gpu": gpu,
        "score": score,
    }


def worker_id(out_dir: str | Path) -> str:
    """Read or generate once the persistent worker ID (`w-<12 hex>`) in out_dir/.worker_id."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    id_file = out_path / ".worker_id"
    try:
        existing = id_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass

    wid = f"w-{uuid.uuid4().hex[:12]}"
    # criacao atomica: se outro processo criou primeiro, usa o id dele
    try:
        fd = os.open(id_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        for _ in range(20):
            try:
                other = id_file.read_text(encoding="utf-8").strip()
            except OSError:
                other = ""
            if other:
                return other
            time.sleep(0.05)
        # arquivo vazio (crash entre O_EXCL e write): grava um id novo de forma atomica
        tmp_file = out_path / f".worker_id.tmp.{os.getpid()}"
        tmp_file.write_text(wid, encoding="utf-8")
        os.replace(tmp_file, id_file)
        return wid
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(wid)
    return wid
