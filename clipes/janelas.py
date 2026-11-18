"""Janelas alinhadas da live contínua: região dona e dedup por tempo absoluto."""
from __future__ import annotations

import json
import os
from pathlib import Path


def window_end(t: float, step: int) -> int:
    """Fim da janela que contém t: floor(t/step)*step (alinhado ao relógio)."""
    return int(t // step) * step


def owns(center: float, s: float, e: float, step: int, pad: float, last: bool) -> bool:
    """A janela [s, e] é dona do centro em [e - step - pad, e - pad); a última, até e."""
    lo = e - step - pad
    return lo <= center <= e if last else lo <= center < e - pad


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def filter_emitted(clips_abs: list[tuple[float, float]], emitted_path: Path) -> list[bool]:
    """True = aceito. Descarta clipe com sobreposição >= 50% (da sua duração) com um já emitido."""
    emitted_path = Path(emitted_path)
    corrupted = False
    try:
        emitted = [(float(a), float(b)) for a, b in json.loads(emitted_path.read_text())]
    except FileNotFoundError:
        emitted = []
    except (ValueError, TypeError, OSError):
        print(f"[live] emitted.json corrompido, recomeçando: {emitted_path}", flush=True)
        emitted, corrupted = [], True
    keep: list[bool] = []
    for a, b in clips_abs:
        dur = b - a
        ok = not any(_overlap((a, b), e) >= 0.5 * dur for e in emitted)
        keep.append(ok)
        if ok:
            emitted.append((a, b))
    if corrupted or any(keep):
        tmp = emitted_path.with_name(emitted_path.name + ".tmp")
        tmp.write_text(json.dumps(emitted))
        os.replace(tmp, emitted_path)
    return keep
