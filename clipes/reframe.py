"""Optional 1 fps YuNet ONNX speaker reframing (P2)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np

DEFAULT_YUNET_ONNX = "/opt/models/yunet/face_detection_yunet_2023mar.onnx"
SAMPLE_W = 640
SAMPLE_H = 360
NET_SIZE = 640
SCORE_THRESHOLD = 0.6
MIN_DETECTION_RATIO = 0.30


def _probe_video_size(src_path: Path) -> tuple[int, int] | None:
    res = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
            str(src_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if res.returncode != 0:
        return None
    raw = res.stdout.strip().splitlines()
    if not raw or "x" not in raw[0]:
        return None
    w_s, h_s = raw[0].split("x", 1)
    return int(w_s), int(h_s)


def _sample_frames_bgr(src_path: Path, start: float, duration: float) -> list[np.ndarray]:
    frame_bytes = SAMPLE_W * SAMPLE_H * 3
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", f"{max(0.0, start):.3f}",
        "-t", f"{max(0.1, duration):.3f}",
        "-i", str(src_path),
        "-vf", f"fps=1,scale={SAMPLE_W}:{SAMPLE_H}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    if res.returncode != 0 or len(res.stdout) < frame_bytes:
        return []
    n_frames = len(res.stdout) // frame_bytes
    buf = np.frombuffer(res.stdout[: n_frames * frame_bytes], dtype=np.uint8)
    return [buf[i * frame_bytes : (i + 1) * frame_bytes].reshape((SAMPLE_H, SAMPLE_W, 3)) for i in range(n_frames)]


def _detect_best_face(
    session,
    input_name: str,
    frame_bgr: np.ndarray,
    score_threshold: float = SCORE_THRESHOLD,
) -> tuple[float, float, float, float] | None:
    """Run YuNet ONNX on a 640x360 BGR frame and return best (cx, cy, w, h) or None."""
    padded = np.zeros((NET_SIZE, NET_SIZE, 3), dtype=np.float32)
    h, w = frame_bgr.shape[:2]
    padded[: min(h, NET_SIZE), : min(w, NET_SIZE)] = frame_bgr[: min(h, NET_SIZE), : min(w, NET_SIZE)]
    inp = np.transpose(padded, (2, 0, 1))[None, ...]
    outs = session.run(None, {input_name: inp})

    best_score = score_threshold
    best_box: tuple[float, float, float, float] | None = None
    for idx, stride in enumerate((8, 16, 32)):
        cls_v = outs[idx][0, :, 0]
        obj_v = outs[idx + 3][0, :, 0]
        bbox_v = outs[idx + 6][0]
        scores = np.sqrt(np.clip(cls_v, 0.0, 1.0) * np.clip(obj_v, 0.0, 1.0))
        valid = np.where(scores >= best_score)[0]
        if valid.size == 0:
            continue
        cols = NET_SIZE // stride
        for k in valid:
            sc = float(scores[k])
            if sc < best_score:
                continue
            r, c = divmod(int(k), cols)
            dx, dy, dw, dh = bbox_v[k]
            cx = float((c + dx) * stride)
            cy = float((r + dy) * stride)
            if cy > SAMPLE_H:
                continue
            bw = float(np.exp(dw) * stride)
            bh = float(np.exp(dh) * stride)
            best_score = sc
            best_box = (cx, cy, bw, bh)
    return best_box


def compute_vertical_crop(
    face_boxes: list[tuple[float, float, float, float]],
    total_frames: int,
    src_w: int,
    src_h: int,
    out_h: int = 1920,
) -> tuple[int, int, int, int] | None:
    """Compute (x, y, w, h) 1080:out_h crop in source pixels from median face center, or None."""
    if total_frames <= 0 or len(face_boxes) / total_frames < MIN_DETECTION_RATIO:
        return None
    if src_w <= 0 or src_h <= 0 or out_h <= 0:
        return None

    crop_h = src_h - (src_h % 2)
    crop_w = int(round(crop_h * 1080.0 / out_h))
    crop_w -= crop_w % 2
    if crop_w <= 0 or src_w <= crop_w:
        return None

    med_cx = float(np.median([b[0] for b in face_boxes]))
    src_cx = (med_cx / SAMPLE_W) * src_w
    crop_x = int(round(src_cx - crop_w / 2.0))
    crop_x = max(0, min(src_w - crop_w, crop_x))
    crop_x -= crop_x % 2
    return (crop_x, 0, crop_w, crop_h)


def detect_speaker_crop(
    src_path: Path,
    start: float,
    duration: float,
    yunet_onnx: str | None = None,
    out_h: int = 1920,
) -> tuple[int, int, int, int] | None:
    """Sample 1 fps at 640x360, detect faces with YuNet ONNX, return 1080:out_h crop or None."""
    model_path = Path(yunet_onnx or os.environ.get("CLIPES_YUNET_ONNX", DEFAULT_YUNET_ONNX))
    if not model_path.is_file():
        print(f"[reframe] YuNet model not found at {model_path}; using fallback layout.", flush=True)
        return None

    try:
        import onnxruntime as ort

        frames = _sample_frames_bgr(src_path, start, duration)
        if not frames:
            return None
        size = _probe_video_size(src_path)
        if size is None:
            return None

        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        input_name = session.get_inputs()[0].name
        boxes: list[tuple[float, float, float, float]] = []
        for frame in frames:
            box = _detect_best_face(session, input_name, frame)
            if box is not None:
                boxes.append(box)
        return compute_vertical_crop(boxes, len(frames), size[0], size[1], out_h=out_h)
    except Exception as exc:
        print(f"[reframe] Detector fallback: {exc}", flush=True)
        return None
