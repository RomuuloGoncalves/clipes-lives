"""Discord upload preview generation within size limits."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from .ingest import sanitize_id

DEFAULT_DISCORD_MAX_MB = 20.0
_TARGET_RATIO = 0.90
_ACCEPT_RATIO = 0.98
_AUDIO_KBPS = 96
_MIN_VIDEO_KBPS = 400
_MAX_VIDEO_KBPS = 2500
_MB_BYTES = 1024 * 1024


def _resolve_limit_mb(limit_mb: float | None) -> float:
    if limit_mb is not None:
        val = float(limit_mb)
    else:
        raw = os.environ.get("CLIPES_DISCORD_MAX_MB", "").strip()
        try:
            val = float(raw) if raw else DEFAULT_DISCORD_MAX_MB
        except ValueError:
            val = DEFAULT_DISCORD_MAX_MB
    if val <= 0:
        raise ValueError("limit_mb must be positive")
    return val


def bitrate_alvo(
    duracao: float,
    limite_mb: float | None = None,
    *,
    teto: int = _MAX_VIDEO_KBPS,
    audio_kbps: int = _AUDIO_KBPS,
) -> int:
    """Compute target video bitrate (kbps) aiming at 90% of `limite_mb` capped at `teto`."""
    eff_limit_mb = _resolve_limit_mb(limite_mb)
    limit_bytes = int(eff_limit_mb * _MB_BYTES)
    eff_duration = max(float(duracao), 1.0)
    total_kbps = int((limit_bytes * _TARGET_RATIO * 8) / (1000.0 * eff_duration))
    raw_video_kbps = total_kbps - int(audio_kbps)
    return max(0, min(int(teto), raw_video_kbps))


def preview_for_discord(
    src: Path,
    duration: float,
    cache_dir: Path,
    limit_mb: float | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> Path | None:
    """Return src if <= 98% of limit_mb, a 720x1280 preview in cache_dir, or None if < 400 kbps."""
    if not src.is_file():
        raise FileNotFoundError(src)

    eff_limit_mb = _resolve_limit_mb(limit_mb)
    limit_bytes = int(eff_limit_mb * _MB_BYTES)
    accept_bytes = int(limit_bytes * _ACCEPT_RATIO)

    if src.stat().st_size <= accept_bytes:
        return src

    video_kbps = bitrate_alvo(duration, eff_limit_mb, teto=_MAX_VIDEO_KBPS, audio_kbps=_AUDIO_KBPS)
    if video_kbps < _MIN_VIDEO_KBPS:
        return None
    maxrate_kbps = round(video_kbps * 1.2)
    bufsize_kbps = video_kbps * 2

    cache_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_id(src.stem)
    out_path = cache_dir / f"discord_{stem}.mp4"
    tmp_out = cache_dir / f".tmp_discord_{stem}.mp4"

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(src),
        "-vf", "scale=720:1280",
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k",
        "-maxrate", f"{maxrate_kbps}k",
        "-bufsize", f"{bufsize_kbps}k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", f"{_AUDIO_KBPS}k",
        "-movflags", "+faststart",
        str(tmp_out),
    ]
    try:
        res = run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if res.returncode != 0:
            detail = (getattr(res, "stderr", "") or getattr(res, "stdout", "") or "")[-500:]
            raise RuntimeError(f"ffmpeg preview failed: {detail}")
        if not tmp_out.is_file():
            raise RuntimeError("ffmpeg preview did not create output file")
        if tmp_out.stat().st_size > accept_bytes:
            return None
        tmp_out.replace(out_path)
    finally:
        tmp_out.unlink(missing_ok=True)

    return out_path
