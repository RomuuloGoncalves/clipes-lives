"""Pure 9:16 split-screen geometry (bar + webcam + gameplay) with variant height budgets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Any

CANVAS_W = 1080
CANVAS_H = 1920
MIN_CAM_H = 400
MIN_GAME_H = 900
MIN_GAME_FRACTION = 0.5
DEFAULT_VARIANTE = "split-starcuts"


@dataclass(frozen=True)
class Rect:
    """Pixel crop rectangle in source coordinates."""

    x: int
    y: int
    w: int
    h: int

    def crop(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"


@dataclass(frozen=True)
class Variante:
    """Height budget and brand geometry for a split layout variant."""

    name: str
    bar_h: int
    cam_h: int
    bar_bg: str | None
    fg: str
    handle_fg: str
    logo_size: int
    logo_x: int
    logo_y: int
    name_x: int
    name_y: int
    handle_y: int
    title_in_bar: bool
    hook_on_cam: bool

    @property
    def game_h(self) -> int:
        return CANVAS_H - self.bar_h - self.cam_h


# B-14: tabela usada pelo calculo de recorte (split_layout/alturas). render._SPLIT_STYLES guarda a geometria de
# texto/barra do filter_complex e tem valores proprios (name_y etc.); nao unificadas para nao mudar o visual (adiado).
VARIANTES: dict[str, Variante] = {
    "split-starcuts": Variante(
        name="split-starcuts",
        bar_h=0,
        cam_h=600,
        bar_bg=None,
        fg="#101010",
        handle_fg="#707070",
        logo_size=0,
        logo_x=0,
        logo_y=0,
        name_x=0,
        name_y=0,
        handle_y=0,
        title_in_bar=True,
        hook_on_cam=False,
    ),
    "split-cheio": Variante(
        name="split-cheio",
        bar_h=0,
        cam_h=768,
        bar_bg=None,
        fg="#FFFFFF",
        handle_fg="#E0E0E0",
        logo_size=140,
        logo_x=40,
        logo_y=30,
        name_x=205,
        name_y=70,
        handle_y=122,
        title_in_bar=False,
        hook_on_cam=True,
    ),
    "split-barra": Variante(
        name="split-barra",
        bar_h=340,
        cam_h=520,
        bar_bg="#FFFFFF",
        fg="#101010",
        handle_fg="#707070",
        logo_size=150,
        logo_x=50,
        logo_y=30,
        name_x=235,
        name_y=70,
        handle_y=122,
        title_in_bar=True,
        hook_on_cam=False,
    ),
    "split-fina": Variante(
        name="split-fina",
        bar_h=170,
        cam_h=700,
        bar_bg="#14141C",
        fg="#FFFFFF",
        handle_fg="#E0E0E0",
        logo_size=120,
        logo_x=40,
        logo_y=25,
        name_x=190,
        name_y=62,
        handle_y=112,
        title_in_bar=False,
        hook_on_cam=True,
    ),
}


@dataclass(frozen=True)
class Layout:
    """Resolved split layout crops and vertical band coordinates."""

    cam: Rect
    game: Rect
    bar_h: int
    cam_h: int
    game_h: int
    emenda_y: int
    variante: str = DEFAULT_VARIANTE


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))


def _fc_box(fc: Any) -> tuple[float, float, float, float]:
    """Extract normalized (x, y, w, h) from a tuple/list, Rect-like object, or Facecam."""
    if hasattr(fc, "rect") and fc.rect is not None:
        fc = fc.rect
    if isinstance(fc, (tuple, list)):
        return float(fc[0]), float(fc[1]), float(fc[2]), float(fc[3])
    return float(fc.x), float(fc.y), float(fc.w), float(fc.h)


def resolve_variante(variante: str | None) -> str:
    """Normalize layout variant name; empty or unknown defaults to split-starcuts."""
    key = (variante or "").strip().lower()
    return key if key in VARIANTES else DEFAULT_VARIANTE


def alturas(
    variante: str = DEFAULT_VARIANTE,
    env: Mapping[str, str] | None = None,
) -> tuple[int, int, int]:
    """Return (bar_h, cam_h, game_h) summing to 1920, honoring valid env overrides."""
    v_name = resolve_variante(variante)
    spec = VARIANTES[v_name]
    cfg = os.environ if env is None else env

    raw_bar = cfg.get("CLIPES_SPLIT_BAR_H", "").strip()
    raw_cam = cfg.get("CLIPES_SPLIT_CAM_H", "").strip()
    if not raw_bar and not raw_cam:
        return spec.bar_h, spec.cam_h, spec.game_h

    try:
        bar_h = int(raw_bar) if raw_bar else spec.bar_h
        cam_h = int(raw_cam) if raw_cam else spec.cam_h
    except ValueError:
        print(f"[layout] alturas invalidas; usando {v_name}", flush=True)
        return spec.bar_h, spec.cam_h, spec.game_h

    game_h = CANVAS_H - bar_h - cam_h
    if bar_h < 0 or cam_h < MIN_CAM_H or game_h < MIN_GAME_H:
        print(f"[layout] alturas invalidas; usando {v_name}", flush=True)
        return spec.bar_h, spec.cam_h, spec.game_h

    return bar_h, cam_h, game_h


def fit(
    region: tuple[float, float, float, float],
    aspect: float,
    cx: float,
    cy: float,
) -> Rect:
    """Largest even-sized Rect with ratio `aspect` (w/h) inside `region`, closest to (cx, cy)."""
    rx, ry, rw, rh = region
    if rw <= 0 or rh <= 0 or aspect <= 0:
        return Rect(int(rx), int(ry), 2, 2)
    if rw / rh > aspect:
        h = rh
        w = rh * aspect
    else:
        w = rw
        h = rw / aspect
    w, h = _even(min(w, rw)), _even(min(h, rh))
    x = _clamp(cx - w / 2, rx, max(rx, rx + rw - w))
    y = _clamp(cy - h / 2, ry, max(ry, ry + rh - h))
    return Rect(int(x), int(y), w, h)


def cam_crop(fc: Any, src_w: int, src_h: int, cam_h: int) -> Rect:
    """Largest crop with 1080:cam_h ratio inside the webcam rectangle, centered on it."""
    fx, fy, fw, fh = _fc_box(fc)
    px = max(0.0, min(fx * src_w, float(src_w)))
    py = max(0.0, min(fy * src_h, float(src_h)))
    pw = max(2.0, min(fw * src_w, src_w - px))
    ph = max(2.0, min(fh * src_h, src_h - py))
    return fit((px, py, pw, ph), CANVAS_W / cam_h, px + pw / 2, py + ph / 2)


def game_crop(src_w: int, src_h: int, fc: Any | None, game_h: int) -> Rect:
    """Largest crop with 1080:game_h ratio in the 4 free regions around webcam, or centered fallback."""
    aspect = CANVAS_W / game_h
    cx, cy = src_w / 2.0, src_h / 2.0
    center = fit((0.0, 0.0, float(src_w), float(src_h)), aspect, cx, cy)
    if fc is None:
        return center

    fx, fy, fw, fh = _fc_box(fc)
    px, py, pw, ph = fx * src_w, fy * src_h, fw * src_w, fh * src_h
    free = [
        (0.0, 0.0, px, float(src_h)),
        (px + pw, 0.0, src_w - px - pw, float(src_h)),
        (0.0, 0.0, float(src_w), py),
        (0.0, py + ph, float(src_w), src_h - py - ph),
    ]
    options = [fit(r, aspect, cx, cy) for r in free if r[2] >= 16 and r[3] >= 16]
    if not options:
        return center
    best = max(
        options,
        key=lambda r: (
            r.w * r.h,
            -abs(r.x + r.w / 2.0 - cx) - abs(r.y + r.h / 2.0 - cy),
        ),
    )
    return best if best.h >= center.h * MIN_GAME_FRACTION else center


def split_layout(
    src_w: int,
    src_h: int,
    fc: Any,
    variante: str = DEFAULT_VARIANTE,
    env: Mapping[str, str] | None = None,
) -> Layout:
    """Compute complete split-screen Layout for given source dimensions, facecam, and variant."""
    v_name = resolve_variante(variante)
    bar_h, cam_h, game_h = alturas(v_name, env)
    return Layout(
        cam=cam_crop(fc, src_w, src_h, cam_h),
        game=game_crop(src_w, src_h, fc, game_h),
        bar_h=bar_h,
        cam_h=cam_h,
        game_h=game_h,
        emenda_y=bar_h + cam_h,
        variante=v_name,
    )
