"""Detecção de capacidade do PC (threads da cota, RAM, disco, temperatura, GPU e worker_id)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from clipes import config


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


# ponytail: 2,5 GB por job e um teto empirico (yt-dlp + ffmpeg + whisper); ajustavel via CLIPES_RAM_POR_JOB
RAM_POR_JOB_PADRAO_MB = 2560


def ram_por_job_mb() -> int:
    """RAM (MB) reservada por job: CLIPES_RAM_POR_JOB (formato mem, 1m a 128g) ou o padrao."""
    m = config._MEM_RE.fullmatch(os.environ.get("CLIPES_RAM_POR_JOB", "").strip().lower())
    if m is None:
        return RAM_POR_JOB_PADRAO_MB
    mb = int(m.group(1)) * (1024 if m.group(2) == "g" else 1)
    return mb if 1 <= mb <= 131072 else RAM_POR_JOB_PADRAO_MB


def _mem_total_bytes(proc: Path) -> int:
    try:
        for line in (proc / "meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def jobs_efetivos(sysfs: str | Path = "/sys", proc: str | Path = "/proc") -> int:
    """Jobs paralelos = max(1, min(CLIPES_JOBS, CPUs, RAM / ram_por_job)); pedido invalido = 1."""
    try:
        pedido = int(os.environ.get("CLIPES_JOBS", "").strip())
    except ValueError:
        return 1
    mem, cpus = config._cgroup_limites(Path(sysfs) / "fs" / "cgroup")
    cpus_n = cpus if cpus and cpus > 0 else (os.cpu_count() or 1)
    ram = mem if mem and mem > 0 else _mem_total_bytes(Path(proc))
    por_ram = int(ram / (1024 * 1024) // ram_por_job_mb())
    return max(1, min(pedido, int(cpus_n), por_ram))


def threads_por_job() -> int:
    """Threads de CPU por job: cpu_threads() dividido entre os jobs efetivos."""
    return max(1, cpu_threads() // jobs_efetivos())


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
        if time.time() - temp_file.stat().st_mtime > 30:
            return None
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
    mem_limite, _cpus_limite = config._cgroup_limites(sysfs_path / "fs" / "cgroup")
    mem_livre = _read_mem_available_bytes(proc_path)
    mem_candidates = [v for v in (mem_limite or None, mem_livre) if v is not None]
    ram_gb = round(min(mem_candidates) / (1024**3), 2) if mem_candidates else 0.0
    if mem_limite is None:
        ram_limite_estado = "ilegivel"
        ram_limite_gb = None
    elif mem_limite == 0:
        ram_limite_estado = "sem_limite"
        ram_limite_gb = None
    else:
        ram_limite_estado = "limite"
        ram_limite_gb = round(mem_limite / (1024**3), 2)
    ram_livre_gb = round(mem_livre / (1024**3), 2) if mem_livre is not None else None
    disco_livre_gb = round(shutil.disk_usage(out_path).free / (1024**3), 2)
    temp_c = _read_temp_c(out_path)
    gpu = _probe_gpu(run)
    score = cpus * (2 if gpu is not None else 1)

    return {
        "cpus": cpus,
        "cpu_pct": cpu_pct,
        "ram_gb": ram_gb,
        "ram_livre_gb": ram_livre_gb,
        "ram_limite_gb": ram_limite_gb,
        "ram_limite_estado": ram_limite_estado,
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
