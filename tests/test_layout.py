"""Tests for split-screen geometry (clipes.layout)."""

from __future__ import annotations

from clipes.layout import (
    CANVAS_H,
    DEFAULT_VARIANTE,
    VARIANTES,
    Layout,
    Rect,
    alturas,
    cam_crop,
    game_crop,
    split_layout,
)


def test_variantes_sum_to_1920():
    assert DEFAULT_VARIANTE == "split-starcuts"
    assert set(VARIANTES) == {"split-starcuts", "split-cheio", "split-barra", "split-fina"}
    for name, v in VARIANTES.items():
        assert v.bar_h + v.cam_h + v.game_h == CANVAS_H, name
        assert v.cam_h >= 400
        assert v.game_h >= 900

    assert (VARIANTES["split-starcuts"].bar_h, VARIANTES["split-starcuts"].cam_h, VARIANTES["split-starcuts"].game_h) == (0, 600, 1320)
    assert (VARIANTES["split-cheio"].bar_h, VARIANTES["split-cheio"].cam_h, VARIANTES["split-cheio"].game_h) == (0, 768, 1152)
    assert (VARIANTES["split-barra"].bar_h, VARIANTES["split-barra"].cam_h, VARIANTES["split-barra"].game_h) == (340, 520, 1060)
    assert (VARIANTES["split-fina"].bar_h, VARIANTES["split-fina"].cam_h, VARIANTES["split-fina"].game_h) == (170, 700, 1050)


def test_alturas_env_valid_and_invalid(capsys):
    assert alturas("split-starcuts", {}) == (0, 600, 1320)
    assert alturas("split-barra", {}) == (340, 520, 1060)
    assert alturas("", {}) == (0, 600, 1320)

    # Valid override
    assert alturas("split-starcuts", {"CLIPES_SPLIT_CAM_H": "700"}) == (0, 700, 1220)
    assert alturas("split-barra", {"CLIPES_SPLIT_BAR_H": "200", "CLIPES_SPLIT_CAM_H": "600"}) == (200, 600, 1120)
    assert alturas("split-cheio", {"CLIPES_SPLIT_CAM_H": "700"}) == (0, 700, 1220)

    # Empty env values behave like unset (no warning)
    assert alturas("split-fina", {"CLIPES_SPLIT_BAR_H": "", "CLIPES_SPLIT_CAM_H": "  "}) == (170, 700, 1050)
    assert capsys.readouterr().out == ""

    # Invalid: game < 900
    assert alturas("split-barra", {"CLIPES_SPLIT_BAR_H": "500", "CLIPES_SPLIT_CAM_H": "600"}) == (340, 520, 1060)
    assert "[layout] alturas invalidas; usando split-barra" in capsys.readouterr().out

    # Invalid: cam < 400
    assert alturas("split-fina", {"CLIPES_SPLIT_CAM_H": "350"}) == (170, 700, 1050)
    assert "[layout] alturas invalidas; usando split-fina" in capsys.readouterr().out

    # Invalid: negative bar or non-numeric
    assert alturas("split-cheio", {"CLIPES_SPLIT_BAR_H": "-10"}) == (0, 768, 1152)
    assert "[layout] alturas invalidas; usando split-cheio" in capsys.readouterr().out
    assert alturas("split-barra", {"CLIPES_SPLIT_CAM_H": "abc"}) == (340, 520, 1060)
    assert "[layout] alturas invalidas; usando split-barra" in capsys.readouterr().out


def _assert_rect_inside(r: Rect, src_w: int, src_h: int) -> None:
    assert r.w >= 2 and r.h >= 2
    assert r.w % 2 == 0 and r.h % 2 == 0
    assert 0 <= r.x and r.x + r.w <= src_w
    assert 0 <= r.y and r.y + r.h <= src_h


def _overlaps(a: Rect, fx: float, fy: float, fw: float, fh: float, src_w: int, src_h: int) -> bool:
    wx0, wy0 = fx * src_w, fy * src_h
    wx1, wy1 = wx0 + fw * src_w, wy0 + fh * src_h
    return not (a.x + a.w <= wx0 + 1 or a.x >= wx1 - 1 or a.y + a.h <= wy0 + 1 or a.y >= wy1 - 1)


def test_webcam_in_each_corner_avoids_webcam_in_game_crop():
    corners = [
        (0.01, 0.01, 0.22, 0.28),  # top-left
        (0.77, 0.01, 0.22, 0.28),  # top-right
        (0.01, 0.70, 0.22, 0.28),  # bottom-left
        (0.77, 0.70, 0.22, 0.28),  # bottom-right
    ]
    for fc in corners:
        for var in VARIANTES:
            lay = split_layout(1920, 1080, fc, var, env={})
            assert isinstance(lay, Layout)
            assert lay.bar_h + lay.cam_h + lay.game_h == 1920
            assert lay.emenda_y == lay.bar_h + lay.cam_h
            _assert_rect_inside(lay.cam, 1920, 1080)
            _assert_rect_inside(lay.game, 1920, 1080)
            assert not _overlaps(lay.game, *fc, 1920, 1080)
            assert "crop=" in lay.cam.crop()


def test_small_free_area_falls_back_to_centered_game_crop():
    # Centered huge webcam leaves < 50% height in every free band
    big_center_fc = (0.20, 0.20, 0.60, 0.65)
    g = game_crop(1920, 1080, big_center_fc, 1060)
    center = game_crop(1920, 1080, None, 1060)
    assert g == center


def test_non_16x9_source_4x3_1440x1080():
    fc = (0.02, 0.02, 0.25, 0.28)
    lay = split_layout(1440, 1080, fc, "split-barra", env={})
    _assert_rect_inside(lay.cam, 1440, 1080)
    _assert_rect_inside(lay.game, 1440, 1080)
    assert not _overlaps(lay.game, *fc, 1440, 1080)
    # cam_crop uses object with .x/.y/.w/.h as well
    class Obj:
        x, y, w, h = fc

    assert cam_crop(Obj(), 1440, 1080, 520) == lay.cam
