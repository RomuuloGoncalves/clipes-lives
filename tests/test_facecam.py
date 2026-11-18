"""Tests for multi-face YuNet extraction (T2) and local facecam detection/cache/override (T3)."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import time
from types import SimpleNamespace

import cv2
import numpy as np

from clipes import facecam as facecam_mod
from clipes import reframe as reframe_mod


def _make_fake_yunet_outputs(
    detections: list[tuple[int, int, int, float, float, float, float, float]],
):
    """Build 9 YuNet ONNX output tensors for strides (8, 16, 32).

    Each item: (stride_idx, row, col, dx, dy, dw, dh, score).
    """
    strides = (8, 16, 32)
    cls_list = []
    obj_list = []
    box_list = []
    for s_idx, stride in enumerate(strides):
        n = (reframe_mod.NET_SIZE // stride) ** 2
        cls_arr = np.zeros((1, n, 1), dtype=np.float32)
        obj_arr = np.zeros((1, n, 1), dtype=np.float32)
        box_arr = np.zeros((1, n, 4), dtype=np.float32)
        cols = reframe_mod.NET_SIZE // stride
        for det_s, row, col, dx, dy, dw, dh, score in detections:
            if det_s != s_idx:
                continue
            k = row * cols + col
            cls_arr[0, k, 0] = score
            obj_arr[0, k, 0] = score
            box_arr[0, k] = [dx, dy, dw, dh]
        cls_list.append(cls_arr)
        obj_list.append(obj_arr)
        box_list.append(box_arr)
    return cls_list + obj_list + box_list


def test_reframe_detect_faces_returns_multiple_faces_and_preserves_best():
    # Two separated faces at stride 16 (index 1): grid (r=4, c=6) -> (96, 64) and (r=12, c=30) -> (480, 192)
    fake_outs = _make_fake_yunet_outputs([
        (1, 4, 6, 0.2, 0.1, float(np.log(2.0)), float(np.log(2.5)), 0.85),
        (1, 12, 30, 0.0, 0.0, float(np.log(3.0)), float(np.log(3.5)), 0.92),
    ])

    class FakeSession:
        def run(self, _out, _inp):
            return fake_outs

    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    faces = reframe_mod._detect_faces(FakeSession(), "input", frame)
    assert len(faces) == 2
    assert faces[0][4] > faces[1][4]
    assert abs(faces[0][0] - 480.0) < 1e-3
    assert abs(faces[1][0] - 99.2) < 1e-3

    best = reframe_mod._detect_best_face(FakeSession(), "input", frame)
    assert best == faces[0][:4]


def _setup_fake_detector(monkeypatch, tmp_path: Path, per_frame_faces: list[list[tuple[float, float, float, float, float]]], frames: list[np.ndarray] | None = None, size: tuple[int, int] = (1920, 1080)):
    fake_onnx = tmp_path / "yunet.onnx"
    fake_onnx.write_bytes(b"onnx")
    monkeypatch.setenv("CLIPES_YUNET_ONNX", str(fake_onnx))

    if frames is None:
        frames = [np.zeros((360, 640, 3), dtype=np.uint8) for _ in per_frame_faces]

    monkeypatch.setattr(reframe_mod, "_sample_frames_bgr", lambda *a, **kw: frames)
    monkeypatch.setattr(reframe_mod, "_probe_video_size", lambda *a, **kw: size)
    monkeypatch.setattr(
        facecam_mod,
        "_open_yunet",
        lambda yunet_onnx=None: (SimpleNamespace(idx=0), "input"),
    )
    call_idx = {"i": 0}

    def fake_detect(_sess, _inp, _frame):
        i = call_idx["i"]
        call_idx["i"] += 1
        return per_frame_faces[i] if i < len(per_frame_faces) else []

    monkeypatch.setattr(reframe_mod, "_detect_faces", fake_detect)
    return fake_onnx


def test_detectar_all_four_corners_overlay(monkeypatch, tmp_path: Path):
    src = tmp_path / "live.mkv"
    src.write_bytes(b"v")

    corners = [
        ("top-left", 80.0, 50.0),
        ("top-right", 560.0, 50.0),
        ("bottom-left", 80.0, 310.0),
        ("bottom-right", 560.0, 310.0),
    ]
    for label, cx, cy in corners:
        faces = [[(cx + (i % 2) * 1.5, cy, 34.0, 44.0, 0.9)] for i in range(12)]
        _setup_fake_detector(monkeypatch, tmp_path, faces)
        fc = facecam_mod.detectar(src, 0.0, 120.0)
        assert fc.tipo == "overlay", label
        assert fc.rect is not None, label
        rx, ry, rw, rh = fc.rect
        assert 0.01 <= rw * rh <= 0.40
        # Detected rectangle encloses the face center
        assert rx <= cx / 640.0 <= rx + rw
        assert ry <= cy / 360.0 <= ry + rh


def test_detectar_rejects_too_large_too_small_unstable_and_empty(monkeypatch, tmp_path: Path):
    src = tmp_path / "live.mkv"
    src.write_bytes(b"v")

    # 1. No faces -> nenhuma
    _setup_fake_detector(monkeypatch, tmp_path, [[] for _ in range(12)])
    res_none = facecam_mod.detectar(src, 0.0, 120.0)
    assert res_none.tipo == "nenhuma" and res_none.rect is None

    # 2. Too small (< 0.08% of 640x360 = <184 px^2) -> nenhuma
    tiny = [[(80.0, 50.0, 8.0, 10.0, 0.9)] for _ in range(12)]
    _setup_fake_detector(monkeypatch, tmp_path, tiny)
    res_tiny = facecam_mod.detectar(src, 0.0, 120.0)
    assert res_tiny.tipo == "nenhuma" and res_tiny.rect is None

    # 3. Too large (> 4% of 640x360 = >9216 px^2) -> camera_cheia (not overlay)
    big = [[(320.0, 180.0, 110.0, 140.0, 0.95)] for _ in range(12)]
    _setup_fake_detector(monkeypatch, tmp_path, big)
    res_big = facecam_mod.detectar(src, 0.0, 120.0)
    assert res_big.tipo == "camera_cheia" and res_big.rect is None

    # 4. Unstable / < 50% frames -> nenhuma
    unstable = [[(60.0 + i * 45.0, 40.0 + i * 20.0, 34.0, 44.0, 0.9)] for i in range(12)]
    _setup_fake_detector(monkeypatch, tmp_path, unstable)
    res_unst = facecam_mod.detectar(src, 0.0, 120.0)
    assert res_unst.tipo == "nenhuma" and res_unst.rect is None

    sparse = [[(80.0, 50.0, 34.0, 44.0, 0.9)] if i < 5 else [] for i in range(12)]
    _setup_fake_detector(monkeypatch, tmp_path, sparse)
    res_sparse = facecam_mod.detectar(src, 0.0, 120.0)
    assert res_sparse.tipo == "nenhuma" and res_sparse.rect is None


def test_detectar_multiple_faces_picks_stable_border_webcam_over_moving_game_faces(monkeypatch, tmp_path: Path):
    src = tmp_path / "live.mkv"
    src.write_bytes(b"v")

    # Stable corner webcam at (570, 60) even with lower score than moving/centered game characters
    per_frame = []
    for i in range(12):
        game_face_moving = (220.0 + i * 25.0, 170.0 + (i % 3) * 30.0, 40.0, 50.0, 0.95)
        game_hud_center = (320.0, 180.0, 34.0, 44.0, 0.93)  # stable in interior
        webcam_face = (570.0, 60.0, 32.0, 40.0, 0.85)  # stable in top-right border
        per_frame.append([game_face_moving, game_hud_center, webcam_face])

    _setup_fake_detector(monkeypatch, tmp_path, per_frame)
    res = facecam_mod.detectar(src, 0.0, 120.0)
    assert res.tipo == "overlay"
    assert res.rect is not None
    rx, ry, rw, rh = res.rect
    assert rx <= 570.0 / 640.0 <= rx + rw
    assert ry <= 60.0 / 360.0 <= ry + rh


def test_detectar_duo_webcam_encloses_both_faces(monkeypatch, tmp_path: Path):
    src = tmp_path / "live.mkv"
    src.write_bytes(b"v")

    # Two people side-by-side in top-left webcam
    duo = [[(60.0, 55.0, 28.0, 36.0, 0.9), (125.0, 58.0, 28.0, 36.0, 0.89)] for _ in range(12)]
    _setup_fake_detector(monkeypatch, tmp_path, duo)
    res = facecam_mod.detectar(src, 0.0, 120.0)
    assert res.tipo == "overlay"
    assert res.rect is not None
    rx, ry, rw, rh = res.rect
    assert rx <= 60.0 / 640.0 <= rx + rw
    assert rx <= 125.0 / 640.0 <= rx + rw


def test_edge_refinement_on_synthetic_bordered_webcam(monkeypatch, tmp_path: Path):
    src = tmp_path / "live.mkv"
    src.write_bytes(b"v")

    # True webcam box in 640x360: x=10..150, y=10..110 (normalized: 0.0156, 0.0278, 0.2188, 0.2778)
    frames = []
    for i in range(12):
        img = np.full((360, 640, 3), 30, dtype=np.uint8)
        # Moving gameplay noise outside webcam
        cv2.circle(img, (320 + i * 10, 220), 25, (0, 200, 100), -1)
        # Crisp webcam rectangle with bright frame border
        cv2.rectangle(img, (10, 10), (150, 110), (70, 70, 90), -1)
        cv2.rectangle(img, (10, 10), (150, 110), (255, 255, 255), 2)
        frames.append(img)

    # Face center slightly inside the box at (80, 50)
    faces = [[(80.0, 50.0, 34.0, 42.0, 0.92)] for _ in range(12)]
    _setup_fake_detector(monkeypatch, tmp_path, faces, frames=frames)

    res = facecam_mod.detectar(src, 0.0, 120.0)
    assert res.tipo == "overlay"
    assert res.metodo == "yunet+bordas"
    assert res.rect is not None
    rx, ry, rw, rh = res.rect
    assert abs((rx + rw) - 150.0 / 640.0) <= 0.03
    assert abs((ry + rh) - 110.0 / 360.0) <= 0.03


def test_cache_revalidation_resolution_invalidation_override_and_no_network(monkeypatch, tmp_path: Path):
    # Block all network sockets
    def _blocked_socket(*args, **kwargs):
        raise AssertionError("Network access is forbidden in local facecam detection")

    monkeypatch.setattr(socket, "socket", _blocked_socket)

    src = tmp_path / "live.mkv"
    src.write_bytes(b"v")
    out_root = tmp_path / "out"
    cfg_path = tmp_path / "facecam.json"

    detect_calls = {"n": 0}
    reval_calls = {"n": 0}

    def fake_detectar(src_p, start=0.0, dur=120.0, yunet_onnx=None):
        detect_calls["n"] += 1
        return facecam_mod.Facecam("overlay", (0.01, 0.01, 0.22, 0.29), "yunet+bordas")

    monkeypatch.setattr(facecam_mod, "detectar", fake_detectar)
    monkeypatch.setattr(reframe_mod, "_probe_video_size", lambda *a, **kw: (1920, 1080))

    # 1. First call writes cache
    fc1 = facecam_mod.obter("alanzoka", src, 0.0, 45.0, out_root, config_path=cfg_path)
    assert fc1.tipo == "overlay" and fc1.metodo == "yunet+bordas"
    assert detect_calls["n"] == 1
    cache_file = out_root / ".facecam" / "alanzoka.json"
    assert cache_file.is_file()
    saved = json.loads(cache_file.read_text(encoding="utf-8"))
    assert saved["src_w"] == 1920 and saved["src_h"] == 1080 and saved["metodo"] == "yunet+bordas"

    # 2. Second call within TTL + passing revalidation reuses cache without detectar
    def fake_reval_ok(src_p, start, dur, rect, yunet_onnx=None):
        reval_calls["n"] += 1
        return True

    monkeypatch.setattr(facecam_mod, "revalidar", fake_reval_ok)
    fc2 = facecam_mod.obter("alanzoka", src, 60.0, 45.0, out_root, config_path=cfg_path)
    assert fc2 == fc1
    assert detect_calls["n"] == 1
    assert reval_calls["n"] == 1

    # 3. Revalidation fails (e.g. scene changed) -> re-detects on clip
    monkeypatch.setattr(facecam_mod, "revalidar", lambda *a, **kw: False)
    fc3 = facecam_mod.obter("alanzoka", src, 120.0, 45.0, out_root, config_path=cfg_path)
    assert fc3.tipo == "overlay"
    assert detect_calls["n"] == 2

    # 4. Resolution changed (1920x1080 -> 1280x720) -> invalidates cache without needing revalidar
    monkeypatch.setattr(reframe_mod, "_probe_video_size", lambda *a, **kw: (1280, 720))
    monkeypatch.setattr(facecam_mod, "revalidar", lambda *a, **kw: True)
    facecam_mod.obter("alanzoka", src, 180.0, 45.0, out_root, config_path=cfg_path)
    assert detect_calls["n"] == 3

    # 5. Expired TTL -> invalidates cache
    monkeypatch.setenv("CLIPES_FACECAM_TTL_H", "1")
    expired = json.loads(cache_file.read_text(encoding="utf-8"))
    expired["em"] = time.time() - 7200.0
    cache_file.write_text(json.dumps(expired), encoding="utf-8")
    facecam_mod.obter("alanzoka", src, 240.0, 45.0, out_root, config_path=cfg_path)
    assert detect_calls["n"] == 4

    # 6. Manual override rect and "nenhuma" bypass detection completely
    cfg_path.write_text(json.dumps({
        "alanzoka": [0.005, 0.007, 0.219, 0.289],
        "semcam": "nenhuma",
    }), encoding="utf-8")
    fc_ov = facecam_mod.obter("alanzoka", src, 300.0, 45.0, out_root, config_path=cfg_path)
    assert fc_ov == facecam_mod.Facecam("overlay", (0.005, 0.007, 0.219, 0.289), "override")
    fc_none = facecam_mod.obter("semcam", src, 0.0, 45.0, out_root, config_path=cfg_path)
    assert fc_none == facecam_mod.Facecam("nenhuma", None, "override")
    assert detect_calls["n"] == 4


def test_revalidar_checks_3_frames_and_triggers_on_2_misses(monkeypatch, tmp_path: Path):
    src = tmp_path / "live.mkv"
    src.write_bytes(b"v")
    rect = (0.0, 0.0, 0.25, 0.30)

    # 2 inside, 1 outside -> True
    _setup_fake_detector(
        monkeypatch,
        tmp_path,
        [
            [(80.0, 50.0, 32.0, 40.0, 0.9)],
            [(400.0, 200.0, 32.0, 40.0, 0.9)],
            [(82.0, 52.0, 32.0, 40.0, 0.9)],
        ],
    )
    assert facecam_mod.revalidar(src, 0.0, 30.0, rect) is True

    # 1 inside, 2 outside -> False
    _setup_fake_detector(
        monkeypatch,
        tmp_path,
        [
            [(80.0, 50.0, 32.0, 40.0, 0.9)],
            [(400.0, 200.0, 32.0, 40.0, 0.9)],
            [(410.0, 210.0, 32.0, 40.0, 0.9)],
        ],
    )
    assert facecam_mod.revalidar(src, 0.0, 30.0, rect) is False


def test_baixa3_safe_key_sanitizes_cache_and_override_paths(monkeypatch, tmp_path: Path):
    """BAIXA-3: path traversal keys never escape OUT_DIR/.facecam and are sanitized for override lookup."""
    src = tmp_path / "live.mkv"
    src.write_bytes(b"v")
    out_root = tmp_path / "out"
    cfg_path = tmp_path / "facecam.json"

    # Put a raw traversal key in facecam.json; sanitized lookup must NOT match it
    cfg_path.write_text(json.dumps({"../../evil": [0.1, 0.1, 0.2, 0.2]}), encoding="utf-8")
    monkeypatch.setattr(reframe_mod, "_probe_video_size", lambda *a, **kw: (1920, 1080))
    monkeypatch.setattr(
        facecam_mod,
        "detectar",
        lambda *a, **kw: facecam_mod.Facecam("nenhuma", None, ""),
    )

    fc = facecam_mod.obter("../../evil", src, 0.0, 30.0, out_root, config_path=cfg_path)
    assert fc.metodo != "override"
    cache_dir = (out_root / ".facecam").resolve()
    files = list(cache_dir.glob("*.json"))
    assert len(files) == 1
    assert files[0].resolve().parent == cache_dir
    assert ".." not in files[0].name
    assert facecam_mod._safe_key("..") == "default"
    assert ".." not in facecam_mod._safe_key("twitch.tv/../../x")
    assert "/" not in facecam_mod._safe_key("a/b\\c")
