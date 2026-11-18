"""Medição da razão de processamento por PC e cálculo de max_lives."""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .capacidade import cpu_threads
from .ingest import probe_duration

MIN_BENCH_LIVE_S = 300.0


def _default_out_dir() -> Path:
    return Path(os.environ.get("OUT_DIR") or "out").expanduser().resolve()


def medir(
    job_dir: Path | str,
    pipeline: Callable[[Path, Path], Any] | None = None,
    *,
    out_dir: Path | str | None = None,
    cpus: int | None = None,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], float] = time.time,
    probe: Callable[[Path], float] | None = None,
    min_live_s: float = MIN_BENCH_LIVE_S,
) -> dict[str, Any]:
    """Run the pipeline on a recorded live window (>= 300s) and write OUT_DIR/.perfil.json."""
    job_path = Path(job_dir).expanduser().resolve()
    source_path = job_path / "source.mkv"
    if not source_path.is_file():
        raise ValueError(f"source.mkv ausente em {job_path}")

    probe_fn = probe or probe_duration
    segundos_live = float(probe_fn(source_path))
    if segundos_live < min_live_s:
        raise ValueError(
            f"source.mkv tem {segundos_live:.1f}s (minimo {min_live_s:.0f}s para bench-live)"
        )

    out_path = Path(out_dir).expanduser().resolve() if out_dir is not None else _default_out_dir()
    out_path.mkdir(parents=True, exist_ok=True)

    if pipeline is None:
        from .monitor import run_local_pipeline

        pipeline_fn: Callable[[Path, Path], Any] = run_local_pipeline
    else:
        pipeline_fn = pipeline

    cpus_count = int(cpus if cpus is not None else cpu_threads())

    t0 = clock()
    pipeline_fn(job_path, out_path)
    segundos_proc = max(0.0, float(clock() - t0))

    razao = round(segundos_proc / segundos_live, 6)
    payload: dict[str, Any] = {
        "razao": razao,
        "segundos_live": round(segundos_live, 3),
        "segundos_proc": round(segundos_proc, 3),
        "medido_em": datetime.fromtimestamp(now(), tz=timezone.utc).isoformat(),
        "cpus": cpus_count,
    }

    perfil_path = out_path / ".perfil.json"
    tmp_path = out_path / f".perfil.json.tmp.{os.getpid()}"
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(perfil_path)
    return payload


def max_lives(out_dir: Path | str, cpus: int | None = None) -> int:
    """Compute max_lives = max(1, floor(0.6 / razao)) from .perfil.json; 1 if missing or cpus changed."""
    perfil_path = Path(out_dir) / ".perfil.json"
    try:
        data = json.loads(perfil_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return 1
    if not isinstance(data, dict):
        return 1

    expected_cpus = int(cpus if cpus is not None else cpu_threads())
    if data.get("cpus") != expected_cpus:
        return 1

    razao = data.get("razao")
    if not isinstance(razao, (int, float)) or isinstance(razao, bool) or razao <= 0:
        return 1

    return max(1, math.floor(0.6 / float(razao) + 1e-9))
