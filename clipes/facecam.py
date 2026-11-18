"""Local YuNet + Canny/Hough webcam overlay detection with TTL cache and manual override."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any

import cv2
import numpy as np

from . import reframe

DEFAULT_CONFIG_PATH = Path("/config/facecam.json")
DEFAULT_TTL_H = 12.0
SAMPLE_FRAMES = 12
MAX_SAMPLE_SPAN_S = 120.0
REVALIDATE_FRAMES = 3
MIN_FACE_SCORE = 0.72
MIN_PRESENCE_RATIO = 0.50
MAX_POS_STD = 0.03
MIN_FACE_AREA_FRAC = 0.0008
MAX_OVERLAY_FACE_FRAC = 0.04
MIN_CAM_AREA_FRAC = 0.01
MAX_CAM_AREA_FRAC = 0.40
BORDER_SNAP_FRAC = 0.15
EDGE_SEARCH_MARGIN = 0.30
BORDER_REGION_FRAC = 0.28
DUO_MAX_DX = 0.25
DUO_MAX_DY = 0.20


@dataclass(frozen=True)
class Facecam:
    """Detected or configured webcam classification and normalized [0..1] rectangle."""

    tipo: str  # "overlay" | "camera_cheia" | "nenhuma"
    rect: tuple[float, float, float, float] | None
    metodo: str  # "yunet" | "yunet+bordas" | "override" | ""


def _safe_key(chave: str) -> str:
    cleaned = re.sub(r"[^\w.-]", "_", (chave or "").strip())
    cleaned = re.sub(r"\.{2,}", "_", cleaned).strip(".-")
    if not cleaned or not cleaned.strip("_"):
        return "default"
    return cleaned


def _ttl_seconds() -> float:
    raw = os.environ.get("CLIPES_FACECAM_TTL_H", "").strip()
    if not raw:
        return DEFAULT_TTL_H * 3600.0
    try:
        hours = float(raw)
    except ValueError:
        return DEFAULT_TTL_H * 3600.0
    if hours <= 0:
        return 0.0
    return hours * 3600.0


def _pick_uniform(frames: list[np.ndarray], count: int) -> list[np.ndarray]:
    if len(frames) <= count:
        return list(frames)
    idxs = np.linspace(0, len(frames) - 1, count, dtype=int)
    return [frames[int(i)] for i in idxs]


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _valid_rect(rect: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        return None
    try:
        x, y, w, h = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    if w <= 0.0 or h <= 0.0 or x < 0.0 or y < 0.0 or x >= 1.0 or y >= 1.0:
        return None
    w = min(w, 1.0 - x)
    h = min(h, 1.0 - y)
    if w <= 0.0 or h <= 0.0:
        return None
    return (round(x, 4), round(y, 4), round(w, 4), round(h, 4))


def _load_override(chave: str, config_path: Path | None = None) -> Facecam | None:
    path = config_path or Path(os.environ.get("CLIPES_FACECAM_CONFIG", "").strip() or DEFAULT_CONFIG_PATH)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[facecam] override invalido ({path}): {exc}", flush=True)
        return None
    if not isinstance(data, dict):
        return None

    safe_chave = _safe_key(chave)
    val = None
    if safe_chave in data:
        val = data[safe_chave]
    else:
        low = safe_chave.lower()
        for k, v in data.items():
            if isinstance(k, str) and k.strip().lower() == low:
                val = v
                break
    if val is None:
        return None
    if isinstance(val, str) and val.strip().lower() == "nenhuma":
        return Facecam(tipo="nenhuma", rect=None, metodo="override")
    rect = _valid_rect(val)
    if rect is not None:
        return Facecam(tipo="overlay", rect=rect, metodo="override")
    return None


@dataclass
class _Cluster:
    obs: list[tuple[int, float, float, float, float]]  # (frame_idx, cx_n, cy_n, bw_n, bh_n)

    @property
    def frame_count(self) -> int:
        return len({o[0] for o in self.obs})

    @property
    def med_cx(self) -> float:
        return float(np.median([o[1] for o in self.obs]))

    @property
    def med_cy(self) -> float:
        return float(np.median([o[2] for o in self.obs]))

    @property
    def med_bw(self) -> float:
        return float(np.median([o[3] for o in self.obs]))

    @property
    def med_bh(self) -> float:
        return float(np.median([o[4] for o in self.obs]))

    @property
    def area_frac(self) -> float:
        return self.med_bw * self.med_bh

    @property
    def std_x(self) -> float:
        return float(np.std([o[1] for o in self.obs]))

    @property
    def std_y(self) -> float:
        return float(np.std([o[2] for o in self.obs]))

    @property
    def is_border(self) -> bool:
        return (
            min(self.med_cx, 1.0 - self.med_cx) <= BORDER_REGION_FRAC
            or min(self.med_cy, 1.0 - self.med_cy) <= BORDER_REGION_FRAC
        )


def _cluster_faces(
    frame_faces: list[list[tuple[float, float, float, float, float]]],
) -> list[_Cluster]:
    """Group normalized face detections across frames by spatial proximity."""
    clusters: list[_Cluster] = []
    for f_idx, dets in enumerate(frame_faces):
        used_clusters: set[int] = set()
        for cx, cy, bw, bh, score in dets:
            if score < MIN_FACE_SCORE:
                continue
            cx_n = cx / reframe.SAMPLE_W
            cy_n = cy / reframe.SAMPLE_H
            bw_n = bw / reframe.SAMPLE_W
            bh_n = bh / reframe.SAMPLE_H
            if bw_n * bh_n < MIN_FACE_AREA_FRAC:
                continue

            best_ci = -1
            best_dist = 0.08
            for ci, cl in enumerate(clusters):
                if ci in used_clusters:
                    continue
                d = max(abs(cx_n - cl.med_cx), abs(cy_n - cl.med_cy))
                if d <= best_dist:
                    best_dist = d
                    best_ci = ci
            if best_ci >= 0:
                clusters[best_ci].obs.append((f_idx, cx_n, cy_n, bw_n, bh_n))
                used_clusters.add(best_ci)
            else:
                clusters.append(_Cluster(obs=[(f_idx, cx_n, cy_n, bw_n, bh_n)]))
                used_clusters.add(len(clusters) - 1)
    return clusters


def _estimate_webcam_rect(
    cx: float,
    cy: float,
    bw: float,
    bh: float,
) -> tuple[float, float, float, float]:
    """Estimate normalized webcam [x0, y0, x1, y1] from face box and snap to nearby borders."""
    est_w = 3.2 * bw
    est_h = 3.0 * bh
    est_cx = cx
    est_cy = cy + 0.25 * bh

    x0 = est_cx - est_w / 2.0
    x1 = est_cx + est_w / 2.0
    y0 = est_cy - est_h / 2.0
    y1 = est_cy + est_h / 2.0

    if cx <= BORDER_SNAP_FRAC:
        x0 = 0.0
    elif (1.0 - cx) <= BORDER_SNAP_FRAC:
        x1 = 1.0

    if cy <= BORDER_SNAP_FRAC:
        y0 = 0.0
    elif (1.0 - cy) <= BORDER_SNAP_FRAC:
        y1 = 1.0

    x0, x1 = _clamp01(x0), _clamp01(x1)
    y0, y1 = _clamp01(y0), _clamp01(y1)
    return x0, y0, x1, y1


def _refine_with_edges(
    frames: list[np.ndarray],
    box: tuple[float, float, float, float],
    face_box: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float, float], bool]:
    """Refine estimated (x0, y0, x1, y1) using Canny + Hough lines on the temporal mean frame."""
    h, w = frames[0].shape[:2]
    mean_img = np.mean(np.stack(frames, axis=0), axis=0).astype(np.uint8)
    gray = cv2.cvtColor(mean_img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    min_len = max(20, int(min(w, h) * 0.08))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180.0, threshold=25, minLineLength=min_len, maxLineGap=10)
    if lines is None:
        return box, False

    h_lines: list[tuple[float, float, float]] = []
    v_lines: list[tuple[float, float, float]] = []
    for x1_px, y1_px, x2_px, y2_px in np.asarray(lines).reshape(-1, 4):
        dx_px = abs(int(x2_px) - int(x1_px))
        dy_px = abs(int(y2_px) - int(y1_px))
        if dy_px <= 2 and dx_px >= min_len:
            h_lines.append((min(x1_px, x2_px) / w, max(x1_px, x2_px) / w, (y1_px + y2_px) / (2.0 * h)))
        elif dx_px <= 2 and dy_px >= min_len:
            v_lines.append((min(y1_px, y2_px) / h, max(y1_px, y2_px) / h, (x1_px + x2_px) / (2.0 * w)))

    x0, y0, x1, y1 = box
    fcx, fcy, fbw, fbh = face_box
    bw_box = max(0.05, x1 - x0)
    bh_box = max(0.05, y1 - y0)
    mx = EDGE_SEARCH_MARGIN * bw_box
    my = EDGE_SEARCH_MARGIN * bh_box

    refined = False

    def _pick_line(
        candidates: list[tuple[float, float, float]],
        target: float,
        margin: float,
        span_lo: float,
        span_hi: float,
        guard_fn,
    ) -> float | None:
        span = max(1e-6, span_hi - span_lo)
        best_val = None
        best_score = -1.0
        for s0, s1, pos in candidates:
            if abs(pos - target) > margin or not guard_fn(pos):
                continue
            overlap = min(s1, span_hi) - max(s0, span_lo)
            if overlap < 0.35 * span:
                continue
            score = overlap - 0.5 * abs(pos - target)
            if score > best_score:
                best_score = score
                best_val = pos
        return best_val

    lx = _pick_line(v_lines, x0, mx, y0, y1, lambda p: p <= fcx - fbw * 0.25)
    if lx is not None:
        x0 = _clamp01(lx)
        refined = True

    rx = _pick_line(v_lines, x1, mx, y0, y1, lambda p: p >= fcx + fbw * 0.25)
    if rx is not None:
        x1 = _clamp01(rx)
        refined = True

    ty = _pick_line(h_lines, y0, my, x0, x1, lambda p: p <= fcy - fbh * 0.15)
    if ty is not None:
        y0 = _clamp01(ty)
        refined = True

    by = _pick_line(h_lines, y1, my, x0, x1, lambda p: p >= fcy + fbh * 0.15)
    if by is not None:
        y1 = _clamp01(by)
        refined = True

    return (x0, y0, x1, y1), refined


def _select_facecam_from_detections(
    frames: list[np.ndarray],
    frame_faces: list[list[tuple[float, float, float, float, float]]],
) -> Facecam:
    """Classify frames as overlay, camera_cheia, or nenhuma and refine webcam rect."""
    n_frames = len(frames)
    if n_frames == 0:
        return Facecam(tipo="nenhuma", rect=None, metodo="")

    min_count = math.ceil(n_frames * MIN_PRESENCE_RATIO)
    clusters = _cluster_faces(frame_faces)
    stable = [
        cl for cl in clusters
        if cl.frame_count >= min_count and cl.std_x <= MAX_POS_STD and cl.std_y <= MAX_POS_STD
    ]
    overlay_clusters = [cl for cl in stable if cl.area_frac <= MAX_OVERLAY_FACE_FRAC]

    if not overlay_clusters:
        large_frames = sum(
            1
            for dets in frame_faces
            if any(
                sc >= MIN_FACE_SCORE
                and (bw * bh) / (reframe.SAMPLE_W * reframe.SAMPLE_H) > MAX_OVERLAY_FACE_FRAC
                for _, _, bw, bh, sc in dets
            )
        )
        if large_frames >= min_count:
            return Facecam(tipo="camera_cheia", rect=None, metodo="yunet")
        return Facecam(tipo="nenhuma", rect=None, metodo="")

    # Prefer border/corner clusters over interior gameplay faces, then more frequent & more stable
    overlay_clusters.sort(
        key=lambda cl: (cl.is_border, cl.frame_count, -(cl.std_x + cl.std_y)),
        reverse=True,
    )
    primary = overlay_clusters[0]

    # Check for a second stable face in the same webcam (duo) and enclose both before expanding
    group = [primary]
    for other in overlay_clusters[1:]:
        if abs(other.med_cx - primary.med_cx) <= DUO_MAX_DX and abs(other.med_cy - primary.med_cy) <= DUO_MAX_DY:
            group.append(other)

    fx0 = min(cl.med_cx - cl.med_bw / 2.0 for cl in group)
    fy0 = min(cl.med_cy - cl.med_bh / 2.0 for cl in group)
    fx1 = max(cl.med_cx + cl.med_bw / 2.0 for cl in group)
    fy1 = max(cl.med_cy + cl.med_bh / 2.0 for cl in group)
    fcx, fcy = (fx0 + fx1) / 2.0, (fy0 + fy1) / 2.0
    fbw, fbh = max(1e-4, fx1 - fx0), max(1e-4, fy1 - fy0)

    est_box = _estimate_webcam_rect(fcx, fcy, fbw, fbh)
    refined_box, used_edges = _refine_with_edges(frames, est_box, (fcx, fcy, fbw, fbh))

    x0, y0, x1, y1 = refined_box
    rw, rh = x1 - x0, y1 - y0
    area = rw * rh
    if rw <= 0.0 or rh <= 0.0 or area < MIN_CAM_AREA_FRAC or area > MAX_CAM_AREA_FRAC:
        # Try unrefined estimate if edge refinement collapsed or blew up the box
        x0, y0, x1, y1 = est_box
        rw, rh = x1 - x0, y1 - y0
        area = rw * rh
        used_edges = False
        if rw <= 0.0 or rh <= 0.0 or area < MIN_CAM_AREA_FRAC or area > MAX_CAM_AREA_FRAC:
            return Facecam(tipo="nenhuma", rect=None, metodo="")

    rect = (round(x0, 4), round(y0, 4), round(rw, 4), round(rh, 4))
    return Facecam(tipo="overlay", rect=rect, metodo="yunet+bordas" if used_edges else "yunet")


def _open_yunet(yunet_onnx: str | None = None):
    model_path = Path(yunet_onnx or os.environ.get("CLIPES_YUNET_ONNX", reframe.DEFAULT_YUNET_ONNX))
    if not model_path.is_file():
        raise FileNotFoundError(f"modelo YuNet nao encontrado em {model_path}")
    import onnxruntime as ort

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    return session, input_name


def detectar(
    src: Path,
    start: float = 0.0,
    dur: float = MAX_SAMPLE_SPAN_S,
    yunet_onnx: str | None = None,
) -> Facecam:
    """Detect webcam overlay locally using YuNet on up to 12 frames + Canny/Hough refinement."""
    try:
        session, input_name = _open_yunet(yunet_onnx)
        span = min(max(1.0, dur), MAX_SAMPLE_SPAN_S)
        frames = reframe._sample_frames_bgr(src, start, span)
        if not frames:
            return Facecam(tipo="nenhuma", rect=None, metodo="")
        sample = _pick_uniform(frames, SAMPLE_FRAMES)
        frame_faces = [reframe._detect_faces(session, input_name, f) for f in sample]
        return _select_facecam_from_detections(sample, frame_faces)
    except Exception as exc:
        print(f"[facecam] deteccao falhou: {exc}", flush=True)
        return Facecam(tipo="nenhuma", rect=None, metodo="")


def revalidar(
    src: Path,
    start: float,
    dur: float,
    rect: tuple[float, float, float, float],
    yunet_onnx: str | None = None,
) -> bool:
    """Return True if cached webcam rect still contains a face in > 1 of 3 sampled clip frames."""
    rx, ry, rw, rh = rect
    try:
        session, input_name = _open_yunet(yunet_onnx)
        frames = reframe._sample_frames_bgr(src, start, max(1.0, dur))
        if not frames:
            return False
        sample = _pick_uniform(frames, REVALIDATE_FRAMES)
        outside_count = 0
        for frame in sample:
            faces = reframe._detect_faces(session, input_name, frame)
            inside = False
            for cx, cy, bw, bh, sc in faces:
                if sc < MIN_FACE_SCORE or (bw * bh) / (reframe.SAMPLE_W * reframe.SAMPLE_H) < MIN_FACE_AREA_FRAC:
                    continue
                cx_n = cx / reframe.SAMPLE_W
                cy_n = cy / reframe.SAMPLE_H
                if rx <= cx_n <= rx + rw and ry <= cy_n <= ry + rh:
                    inside = True
                    break
            if not inside:
                outside_count += 1
        return outside_count < 2
    except Exception as exc:
        print(f"[facecam] deteccao falhou: {exc}", flush=True)
        return False


def _parse_ts(em: Any) -> float | None:
    if isinstance(em, (int, float)):
        return float(em)
    if isinstance(em, str):
        try:
            return datetime.fromisoformat(em).timestamp()
        except ValueError:
            return None
    return None


def _read_cache(cache_path: Path, src_w: int | None, src_h: int | None) -> Facecam | None:
    if not cache_path.is_file():
        return None
    ttl_s = _ttl_seconds()
    if ttl_s <= 0:
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    ts = _parse_ts(data.get("em"))
    if ts is None or (time.time() - ts) >= ttl_s:
        return None
    if src_w is not None and src_h is not None:
        if data.get("src_w") != src_w or data.get("src_h") != src_h:
            return None

    tipo = data.get("tipo")
    if tipo not in ("overlay", "camera_cheia", "nenhuma"):
        return None
    metodo = str(data.get("metodo") or "")
    raw_rect = data.get("rect")
    if tipo == "overlay":
        rect = _valid_rect(raw_rect)
        if rect is None:
            return None
        return Facecam(tipo="overlay", rect=rect, metodo=metodo)
    return Facecam(tipo=tipo, rect=None, metodo=metodo)


def _write_cache(
    cache_path: Path,
    fc: Facecam,
    src_w: int | None,
    src_h: int | None,
) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tipo": fc.tipo,
        "rect": list(fc.rect) if fc.rect is not None else None,
        "src_w": src_w,
        "src_h": src_h,
        "metodo": fc.metodo,
        "em": time.time(),
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def obter(
    chave: str,
    src: Path,
    start: float,
    dur: float,
    out_root: Path,
    yunet_onnx: str | None = None,
    config_path: Path | None = None,
) -> Facecam:
    """Resolve webcam for key: manual override -> valid cache (+3-frame revalidation) -> local detect."""
    size = reframe._probe_video_size(src)
    src_w, src_h = size if size is not None else (None, None)
    safe_chave = _safe_key(chave)
    cache_path = out_root / ".facecam" / f"{safe_chave}.json"

    override = _load_override(safe_chave, config_path=config_path)
    if override is not None:
        _write_cache(cache_path, override, src_w, src_h)
        return override

    cached = _read_cache(cache_path, src_w, src_h)
    if cached is not None:
        if cached.tipo != "overlay" or (
            cached.rect is not None and revalidar(src, start, dur, cached.rect, yunet_onnx=yunet_onnx)
        ):
            return cached

    detected = detectar(src, start, dur, yunet_onnx=yunet_onnx)
    _write_cache(cache_path, detected, src_w, src_h)
    return detected
