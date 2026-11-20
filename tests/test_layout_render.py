"""Tests for split webcam+game layout rendering (F5 / T4-T7)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import clipes as clipes_pkg
from clipes import reframe as reframe_mod
from clipes import render as render_mod
from clipes.models import SnappedClip, Word


def _clear_layout_and_bar_env(monkeypatch) -> None:
    for key in (
        "CLIPES_LAYOUT",
        "CLIPES_BAR",
        "CLIPES_BRAND_NAME",
        "CLIPES_BRAND_DISPLAY",
        "CLIPES_BRAND_LOGO",
        "CLIPES_BAR_STYLE",
        "CLIPES_BAR_H",
        "CLIPES_HOOK_TOP",
        "CLIPES_BLUR_BG",
        "CLIPES_MASTER_MODE",
        "CLIPES_DISCORD_MAX_MB",
        "CLIPES_STARCUTS_OVERLAY",
    ):
        monkeypatch.delenv(key, raising=False)


def _rect(x: int, y: int, w: int, h: int) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, w=w, h=h, crop=lambda: f"crop={w}:{h}:{x}:{y}")


def _make_layout(variante: str, src_w: int = 1920, src_h: int = 1080, fc_corner: str = "tl") -> SimpleNamespace:
    heights = {
        "split-starcuts": (0, 600, 1320),
        "split-cheio": (0, 768, 1152),
        "split-barra": (340, 520, 1060),
        "split-fina": (170, 700, 1050),
    }
    bar_h, cam_h, game_h = heights[variante]
    cam_w = int(round(src_w * 0.22)) // 2 * 2
    cam_h_src = int(round(cam_w * cam_h / 1080.0)) // 2 * 2
    if fc_corner == "tr":
        cam_x, cam_y = src_w - cam_w - 20, 16
        game_w = int(round(src_h * 1080.0 / game_h)) // 2 * 2
        game_x = max(0, (src_w - game_w) // 2 - cam_w // 2)
    else:
        cam_x, cam_y = 20, 16
        game_w = int(round(src_h * 1080.0 / game_h)) // 2 * 2
        game_x = min(src_w - game_w, max(cam_x + cam_w, (src_w - game_w) // 2))
    game_x -= game_x % 2
    return SimpleNamespace(
        cam=_rect(cam_x, cam_y, cam_w, cam_h_src),
        game=_rect(game_x, 0, game_w, src_h - (src_h % 2)),
        bar_h=bar_h,
        cam_h=cam_h,
        game_h=game_h,
        emenda_y=bar_h + cam_h,
        variante=variante,
    )


def _install_fake_layout_and_facecam(monkeypatch, fc_result, *, on_obter=None):
    fake_facecam = SimpleNamespace(
        obter=lambda chave, src, start, dur, out_root: (
            on_obter(chave, src, start, dur, out_root) if on_obter else None,
            fc_result() if callable(fc_result) else fc_result,
        )[-1]
    )
    fake_layout = SimpleNamespace(
        split_layout=lambda w, h, fc, variante="split-barra": _make_layout(variante, w, h)
    )
    monkeypatch.setitem(sys.modules, "clipes.facecam", fake_facecam)
    monkeypatch.setitem(sys.modules, "clipes.layout", fake_layout)
    monkeypatch.setattr(clipes_pkg, "facecam", fake_facecam, raising=False)
    monkeypatch.setattr(clipes_pkg, "layout", fake_layout, raising=False)
    return fake_facecam, fake_layout


def test_build_video_filter_split_variants_logo_hook_and_full_parity(tmp_path: Path, monkeypatch):
    _clear_layout_and_bar_env(monkeypatch)
    ass_path = tmp_path / "clip.ass"
    logo = tmp_path / "logo.jpg"
    logo.write_bytes(b"jpg")

    bar_logo = render_mod.BarConfig(
        h=420, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display="CortesLR", handle="@cortes_lr1", logo=logo,
    )
    bar_nologo = render_mod.BarConfig(
        h=420, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display="CortesLR", handle="@cortes_lr1", logo=None,
    )

    # Full parity: split=None produces exact same strings as pre-F5
    fc_full_plain, map_plain = render_mod.build_video_filter(ass_path, n_subs=2, blur_bg=False, bar=None)
    assert map_plain == "[v_out]"
    assert fc_full_plain == (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1[v_base];"
        "[v_base]tpad=stop_mode=clone:stop_duration=0.5[v_pad];"
        f"[v_pad]subtitles='{render_mod._escape_ffmpeg_filter_path(ass_path)}'[v_out]"
    )

    # Variant A (split-cheio): no bar pad, logo 140 at 40:30, drawbox 50% when has_hook=True
    lay_a = _make_layout("split-cheio")
    fc_a_hook, map_a = render_mod.build_video_filter(
        ass_path, n_subs=2, bar=bar_logo, split=lay_a, variante="split-cheio", has_hook=True
    )
    assert map_a == "[v_out]"
    assert "[0:v]split=2[c0][g0]" in fc_a_hook
    assert "scale=1080:768,setsar=1,drawbox=x=0:y=0:w=1080:h=768:color=black@0.5:t=fill:enable='lt(t,5.4)'[cam]" in fc_a_hook
    assert "scale=1080:1152,setsar=1[game]" in fc_a_hook
    assert "[cam][game]vstack=inputs=2[v_base]" in fc_a_hook
    assert "scale=140:140" in fc_a_hook
    assert "[v_base][logo]overlay=40:30[v_logo]" in fc_a_hook

    fc_a_nohook, _ = render_mod.build_video_filter(
        ass_path, n_subs=2, bar=None, split=lay_a, variante="split-cheio", has_hook=False
    )
    assert "drawbox" not in fc_a_nohook
    assert "[logo]" not in fc_a_nohook

    # Variant B (split-barra): white bar pad 340, NO drawbox even with has_hook=True, logo 150 at 50:30
    lay_b = _make_layout("split-barra")
    fc_b, _ = render_mod.build_video_filter(
        ass_path, n_subs=2, bar=bar_logo, split=lay_b, variante="split-barra", has_hook=True
    )
    assert "scale=1080:520,setsar=1[cam]" in fc_b
    assert "scale=1080:1060,setsar=1[game]" in fc_b
    assert "[cam][game]vstack=inputs=2,pad=1080:1920:0:340:#FFFFFF[v_base]" in fc_b
    assert "drawbox" not in fc_b
    assert "scale=150:150" in fc_b
    assert "[v_base][logo]overlay=50:30[v_logo]" in fc_b

    # Variant C (split-fina): dark bar #14141C pad 170, drawbox when has_hook=True, logo 120 at 40:25
    lay_c = _make_layout("split-fina")
    fc_c, _ = render_mod.build_video_filter(
        ass_path, n_subs=2, bar=bar_logo, split=lay_c, variante="split-fina", has_hook=True
    )
    assert "drawbox=x=0:y=0:w=1080:h=700:color=black@0.5:t=fill:enable='lt(t,5.4)'[cam]" in fc_c
    assert "[cam][game]vstack=inputs=2,pad=1080:1920:0:170:#14141C[v_base]" in fc_c
    assert "scale=120:120" in fc_c
    assert "[v_base][logo]overlay=40:25[v_logo]" in fc_c

    # Without logo file -> no [1:v] / overlay
    fc_b_nologo, _ = render_mod.build_video_filter(
        ass_path, n_subs=2, bar=bar_nologo, split=lay_b, variante="split-barra", downscale_720=True
    )
    assert "[1:v]" not in fc_b_nologo
    assert "scale=720:1280,setsar=1[v_720]" in fc_b_nologo


def test_build_ass_split_positions_variants_and_full_parity(tmp_path: Path, monkeypatch):
    _clear_layout_and_bar_env(monkeypatch)
    words = [Word(1.0, 1.4, " Olha"), Word(1.5, 2.0, " isso!")]
    logo = tmp_path / "logo.jpg"
    logo.write_bytes(b"jpg")
    bar = render_mod.BarConfig(
        h=420, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display="CortesLR", handle="@cortes_lr1", logo=logo,
    )

    # Full parity check: split=None matches F2 behavior exactly
    ass_full = tmp_path / "full.ass"
    render_mod.build_ass(
        words, 0.0, 10.0, ass_full,
        channel_credit="twitch.tv/alanzoka", part_index=1, part_total=2,
        hook_top="Gancho topo", title="Titulo", bar=bar, split=None,
    )
    txt_full = ass_full.read_text(encoding="utf-8")
    assert r"\pos(120,440)" in txt_full
    assert r"\pos(540,490)" in txt_full
    assert r"\pos(540,1170)" in txt_full
    assert "Style: Cap,DejaVu Sans,72," in txt_full

    # Variant A (split-cheio): white outlined name/handle on cam, TopHook 0..5.4s at cam center (540,384), Cap at (540,768)
    ass_a = tmp_path / "a.ass"
    lay_a = _make_layout("split-cheio")
    render_mod.build_ass(
        words, 0.0, 10.0, ass_a,
        channel_credit="twitch.tv/alanzoka", part_index=1, part_total=2,
        hook_top="ELE PERDEU TUDO NA HORA!", title="Fallback", bar=bar, split=lay_a,
    )
    txt_a = ass_a.read_text(encoding="utf-8")
    assert "Style: TopHook,DejaVu Sans,64," in txt_a
    assert ",60,60,0,1\n" in next(l for l in txt_a.splitlines() if l.startswith("Style: TopHook,")) + "\n"
    assert "0:00:00.00,0:00:05.40,TopHook," in txt_a
    assert r"\an5\pos(540,384)" in txt_a
    assert ",BarTitle," not in txt_a
    assert "Style: BarName,DejaVu Sans,50,&H00FFFFFF&" in txt_a
    assert r"\an7\pos(205,48)}CortesLR" in txt_a
    assert r"\an7\pos(205,104)}@cortes_lr1" in txt_a
    assert r"\an7\pos(120,838)}Parte 1/2" in txt_a
    assert r"\an5\pos(540,1305)" in txt_a
    assert "Style: Credit,DejaVu Sans,34," in txt_a
    assert r"\pos(540,1344)\fad(250,0)}Parte 2 →" in txt_a
    cap_lines_a = [l for l in txt_a.splitlines() if ",Cap," in l]
    assert len(cap_lines_a) == 2
    assert all(r"{\an5\pos(540,1380)}" in l and r"{\c&H0000FFFF&}" in l for l in cap_lines_a)

    # Variant B (split-barra): fixed title in bar for entire file (0..11.20), NO TopHook, Cap at (540,1380)
    ass_b = tmp_path / "b.ass"
    lay_b = _make_layout("split-barra")
    render_mod.build_ass(
        words, 0.0, 10.0, ass_b,
        channel_credit="twitch.tv/alanzoka", part_index=2, part_total=2,
        outro_duration=1.2, hook_top="Gancho fixo na barra", title="Titulo", bar=bar, split=lay_b,
    )
    txt_b = ass_b.read_text(encoding="utf-8")
    assert "TopHook" not in txt_b
    assert "0:00:00.00,0:00:11.20,BarTitle," in txt_b
    assert "Gancho fixo na barra" in txt_b
    assert "Style: BarName,DejaVu Sans,54,&H00101010&" in txt_b
    assert r"\an7\pos(235,52)}CortesLR" in txt_b
    assert r"\an5\pos(540,1380)}" in txt_b
    assert r"\pos(540,1390)\fad(250,0)}Live completa: twitch.tv/alanzoka" in txt_b

    # Variant B with empty hook_top falls back to clip.title and still works when bar=None (CLIPES_BAR=0)
    ass_b_nobar = tmp_path / "b_nobar.ass"
    render_mod.build_ass(
        words, 0.0, 10.0, ass_b_nobar,
        hook_top="", title="Titulo fallback", bar=None, split=lay_b,
    )
    txt_b_nb = ass_b_nobar.read_text(encoding="utf-8")
    assert ",BarName," not in txt_b_nb
    assert ",BarHandle," not in txt_b_nb
    assert ",BarTitle," in txt_b_nb and "Titulo fallback" in txt_b_nb

    # Variant C (split-fina): TopHook at cam center (170 + 700//2 = 520), Cap at (540,1380), name at (190,36)
    ass_c = tmp_path / "c.ass"
    lay_c = _make_layout("split-fina")
    render_mod.build_ass(
        words, 0.0, 10.0, ass_c,
        channel_credit="twitch.tv/alanzoka",
        hook_top="Gancho C", title="Titulo", bar=bar, split=lay_c,
    )
    txt_c = ass_c.read_text(encoding="utf-8")
    assert r"\an5\pos(540,520)" in txt_c
    assert "Style: BarName,DejaVu Sans,50,&H00FFFFFF&" in txt_c
    assert r"\an7\pos(190,36)}CortesLR" in txt_c
    assert r"\an5\pos(540,1380)}" in txt_c

    # Variant D (split-starcuts): BarTitle 44px #101010 at (540,696), PartBadge at (120,785), Cap at (540,1380), Credit at (540,1305)
    ass_s = tmp_path / "starcuts.ass"
    lay_s = SimpleNamespace(
        cam=_rect(16, 12, 440, 244),
        game=_rect(450, 0, 884, 1080),
        bar_h=0,
        cam_h=600,
        game_h=1320,
        emenda_y=600,
        variante="split-starcuts",
    )
    render_mod.build_ass(
        words, 0.0, 10.0, ass_s,
        channel_credit="twitch.tv/alanzoka", part_index=1, part_total=2,
        hook_top="Que isso mandy??", title="Titulo", bar=bar, split=lay_s, variante="split-starcuts",
    )
    txt_s = ass_s.read_text(encoding="utf-8")
    assert "Style: BarTitle,DejaVu Sans,44,&H00101010&" in txt_s
    assert r"\an5\pos(540,696)}Que isso mandy??" in txt_s
    assert ",BarName," not in txt_s
    assert ",BarHandle," not in txt_s
    assert "TopHook" not in txt_s
    assert r"\an7\pos(120,785)}Parte 1/2" in txt_s
    assert r"\an5\pos(540,1305)" in txt_s
    assert r"\an5\pos(540,1380)}" in txt_s
    assert ",120,180,0,1\n" in next(l for l in txt_s.splitlines() if l.startswith("Style: Cap,")) + "\n"


def test_render_clip_layout_resolution_fallbacks_and_master_retry(tmp_path: Path, monkeypatch, capsys):
    _clear_layout_and_bar_env(monkeypatch)
    monkeypatch.setenv("CLIPES_MASTER_MODE", "livre")
    assert render_mod.resolve_layout_mode() == "split-starcuts"
    monkeypatch.setenv("CLIPES_LAYOUT", "invalido")
    assert render_mod.resolve_layout_mode() == "split-starcuts"
    assert "CLIPES_LAYOUT invalido" in capsys.readouterr().out
    monkeypatch.delenv("CLIPES_LAYOUT")

    src = tmp_path / "src.mkv"
    src.write_bytes(b"src")
    out_dir = tmp_path / "out" / "vod1"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir(parents=True)
    cache_dir.mkdir()
    monkeypatch.setattr(reframe_mod, "_probe_video_size", lambda p: (1920, 1080))

    # 1) overlay -> split_webcam + clip.facecam populated
    obter_calls: list[tuple] = []
    _install_fake_layout_and_facecam(
        monkeypatch,
        SimpleNamespace(tipo="overlay", rect=(0.01, 0.01, 0.22, 0.28), metodo="yunet+bordas"),
        on_obter=lambda *a: obter_calls.append(a),
    )
    cmds: list[list[str]] = []
    monkeypatch.setattr(
        render_mod.subprocess,
        "run",
        lambda cmd, **kw: (
            cmds.append(cmd),
            Path(cmd[-1]).write_bytes(b"mp4"),
            SimpleNamespace(returncode=0, stderr="", stdout=""),
        )[-1],
    )
    clip1 = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Overlay", hook_top="Olha")
    render_mod.render_clip(clip1, 1, [], "", out_dir, cache_dir, live_video_path=src, layout_key="alanzoka")
    assert obter_calls[0][0] == "alanzoka"
    assert obter_calls[0][4] == out_dir.parent
    assert clip1.layout == "split_webcam"
    assert clip1.layout_fallback is False
    assert clip1.facecam == {"rect": [0.01, 0.01, 0.22, 0.28], "metodo": "yunet+bordas"}
    assert "vstack=inputs=2" in cmds[0][cmds[0].index("-filter_complex") + 1]

    # 2) camera_cheia + reframe=True -> reframed_face; camera_cheia + reframe=False -> black_bars_16x9
    _install_fake_layout_and_facecam(
        monkeypatch,
        SimpleNamespace(tipo="camera_cheia", rect=(0.2, 0.1, 0.5, 0.6), metodo="yunet"),
    )
    monkeypatch.setattr(reframe_mod, "detect_speaker_crop", lambda *a, **kw: (300, 0, 608, 1080))
    cmds.clear()
    clip2 = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Cam cheia")
    render_mod.render_clip(clip2, 2, [], "", out_dir, cache_dir, live_video_path=src, reframe=True)
    assert clip2.layout == "reframed_face"
    assert "vstack" not in cmds[0][cmds[0].index("-filter_complex") + 1]

    cmds.clear()
    clip2b = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Cam cheia sem reframe")
    render_mod.render_clip(clip2b, 2, [], "", out_dir, cache_dir, live_video_path=src, reframe=False)
    assert clip2b.layout == "blurred_16x9"

    # 3) nenhuma -> full without error and without calling detect_speaker_crop
    reframe_called: list[bool] = []
    monkeypatch.setattr(reframe_mod, "detect_speaker_crop", lambda *a, **kw: (reframe_called.append(True), None)[-1])
    _install_fake_layout_and_facecam(
        monkeypatch,
        SimpleNamespace(tipo="nenhuma", rect=None, metodo="yunet"),
    )
    cmds.clear()
    clip3 = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Sem cam")
    render_mod.render_clip(clip3, 3, [], "", out_dir, cache_dir, live_video_path=src, reframe=True)
    assert clip3.layout == "blurred_16x9"
    assert reframe_called == []

    # 4) facecam.obter raises -> logs [facecam] deteccao falhou and falls back to full
    def _boom(*a):
        raise RuntimeError("yunet missing")

    _install_fake_layout_and_facecam(monkeypatch, None, on_obter=_boom)
    capsys.readouterr()
    clip4 = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Erro deteccao")
    render_mod.render_clip(clip4, 4, [], "", out_dir, cache_dir, live_video_path=src)
    assert "[facecam] deteccao falhou: yunet missing" in capsys.readouterr().out
    assert clip4.layout == "blurred_16x9"

    # 5) CLIPES_LAYOUT=full -> does NOT call facecam.obter
    monkeypatch.setenv("CLIPES_LAYOUT", "full")
    obter_calls.clear()
    _install_fake_layout_and_facecam(
        monkeypatch,
        SimpleNamespace(tipo="overlay", rect=(0.01, 0.01, 0.22, 0.28), metodo="override"),
        on_obter=lambda *a: obter_calls.append(a),
    )
    cmds.clear()
    clip5 = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Full explicito")
    render_mod.render_clip(clip5, 5, [], "", out_dir, cache_dir, live_video_path=src)
    assert obter_calls == []
    assert clip5.layout == "blurred_16x9"
    assert "vstack" not in cmds[0][cmds[0].index("-filter_complex") + 1]

    # 6) ffmpeg split fails -> falls back to full, sets layout_fallback=True, regenerates ASS
    monkeypatch.setenv("CLIPES_LAYOUT", "split-cheio")
    _install_fake_layout_and_facecam(
        monkeypatch,
        SimpleNamespace(tipo="overlay", rect=(0.01, 0.01, 0.22, 0.28), metodo="override"),
    )
    fallback_cmds: list[list[str]] = []

    def fake_run_fail_first(cmd, **kw):
        fallback_cmds.append(cmd)
        if len(fallback_cmds) == 1:
            return SimpleNamespace(returncode=1, stderr="split filter error", stdout="")
        Path(cmd[-1]).write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run_fail_first)
    clip6 = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Fallback ffmpeg")
    render_mod.render_clip(clip6, 6, [], "", out_dir, cache_dir, live_video_path=src, channel_credit="twitch.tv/gaules")
    assert len(fallback_cmds) == 2
    assert "vstack" in fallback_cmds[0][fallback_cmds[0].index("-filter_complex") + 1]
    assert "vstack" not in fallback_cmds[1][fallback_cmds[1].index("-filter_complex") + 1]
    assert clip6.layout_fallback is True
    assert clip6.layout == "blurred_16x9"
    # Regenerated ASS uses full credit position (120,600) instead of split (540,1305)
    ass6 = (cache_dir / "clip_06_fallback-ffmpeg.ass").read_text(encoding="utf-8")
    assert r"\pos(120,600)" in ass6
    assert r"\pos(540,1305)" not in ass6

    # 7) F4 master retry uses the SAME split layout on the 2nd encode
    monkeypatch.setenv("CLIPES_MASTER_MODE", "discord")
    monkeypatch.setenv("CLIPES_DISCORD_MAX_MB", "10")
    monkeypatch.setenv("CLIPES_LAYOUT", "split-barra")
    retry_cmds: list[list[str]] = []
    sizes = [10 * 1024 * 1024, 4 * 1024 * 1024]

    def fake_run_retry(cmd, **kw):
        retry_cmds.append(cmd)
        Path(cmd[-1]).write_bytes(b"\x00" * sizes.pop(0))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run_retry)
    clip7 = SnappedClip(peak=30.0, start=0.0, end=60.0, score=0.9, title="Split master retry")
    render_mod.render_clip(clip7, 7, [], "", out_dir, cache_dir, live_video_path=src)
    assert len(retry_cmds) == 2
    assert clip7.layout == "split_webcam"
    assert clip7.cabe_discord is True
    for rc_cmd in retry_cmds:
        fc_str = rc_cmd[rc_cmd.index("-filter_complex") + 1]
        assert "vstack=inputs=2,pad=1080:1920:0:340:#FFFFFF[v_base]" in fc_str
        assert "scale=720:1280" in fc_str

    # 8) Vertical source (height > width) does NOT use split
    monkeypatch.setattr(reframe_mod, "_probe_video_size", lambda p: (1080, 1920))
    monkeypatch.setenv("CLIPES_MASTER_MODE", "livre")
    vert_cmds: list[list[str]] = []
    monkeypatch.setattr(
        render_mod.subprocess,
        "run",
        lambda cmd, **kw: (
            vert_cmds.append(cmd),
            Path(cmd[-1]).write_bytes(b"mp4"),
            SimpleNamespace(returncode=0, stderr="", stdout=""),
        )[-1],
    )
    clip8 = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Vertical")
    render_mod.render_clip(clip8, 8, [], "", out_dir, cache_dir, live_video_path=src)
    assert clip8.layout == "blurred_16x9"
    assert "vstack" not in vert_cmds[0][vert_cmds[0].index("-filter_complex") + 1]


def _rgb_frame(path: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3)


def test_render_clip_real_ffmpeg_split_three_variants_and_corners(tmp_path: Path, monkeypatch):
    """Real 3s synthetic render in all 3 split variants + 1920x1080 TL & 1280x720 TR sources."""
    _clear_layout_and_bar_env(monkeypatch)
    monkeypatch.setenv("CLIPES_MASTER_MODE", "livre")
    monkeypatch.setattr(render_mod, "_WARNED_MISSING_LOGOS", set())

    # 1920x1080 synthetic source: green game field + bright red webcam box in top-left (16..456, 12..322)
    src_1080 = tmp_path / "src_1080_tl.mkv"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            "color=c=#1E9020:s=1920x1080:r=30:d=3.2,"
            "drawbox=x=16:y=12:w=440:h=310:color=#FF1010:t=fill",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3.2",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src_1080),
        ],
        check=True,
    )

    # 1280x720 synthetic source: cyan/blue game field + magenta webcam box in top-right
    src_720 = tmp_path / "src_720_tr.mkv"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            "testsrc2=size=1280x720:rate=30:duration=3.2,"
            "drawbox=x=0:y=0:w=1280:h=720:color=#1040B0:t=fill,"
            "drawbox=x=960:y=16:w=290:h=210:color=#FF00FF:t=fill",
            "-f", "lavfi", "-i", "sine=frequency=520:duration=3.2",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src_720),
        ],
        check=True,
    )

    # Solid yellow-blue logo (pure blue PNG) to verify circular logo placement
    logo = tmp_path / "logo_blue.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=#0050FF:s=200x200", "-frames:v", "1", str(logo)],
        check=True,
    )

    monkeypatch.setenv("CLIPES_BRAND_NAME", "@cortes_lr1")
    monkeypatch.setenv("CLIPES_BRAND_DISPLAY", "CortesLR")
    monkeypatch.setenv("CLIPES_BRAND_LOGO", str(logo))

    out_dir = tmp_path / "out" / "synth"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir(parents=True)
    cache_dir.mkdir()
    words = [Word(0.5, 1.0, " Olha"), Word(1.1, 1.8, " isso!")]
    clip = SnappedClip(
        peak=1.5, start=0.0, end=3.0, score=0.95,
        title="Jogada absurda", hook_top="GANCHO TESTE!",
    )

    # Render all 3 variants on 1920x1080 top-left webcam
    _install_fake_layout_and_facecam(
        monkeypatch,
        SimpleNamespace(tipo="overlay", rect=(0.01, 0.02, 0.21, 0.26), metodo="override"),
    )
    for idx, (var, emenda_y) in enumerate(
        [("split-cheio", 768), ("split-barra", 860), ("split-fina", 870)], start=1
    ):
        monkeypatch.setenv("CLIPES_LAYOUT", var)
        mp4 = render_mod.render_clip(
            clip, idx, words, "", out_dir, cache_dir,
            live_video_path=src_1080, channel_credit="twitch.tv/alanzoka",
        )
        assert clip.layout == "split_webcam"
        info = json.loads(
            subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "json", str(mp4)],
                capture_output=True, text=True, check=True,
            ).stdout
        )
        assert (info["streams"][0]["width"], info["streams"][0]["height"]) == (1080, 1920)

        frame = _rgb_frame(mp4, 1.5)
        # Pixel well below seam comes from green game area (G > R, G > B)
        game_px = frame[emenda_y + 80, 540]
        assert game_px[1] > 100 and game_px[1] > game_px[0] + 40 and game_px[1] > game_px[2] + 40

        if var == "split-cheio":
            # Webcam at (y=620, x=950) away from logo/name/hook is darkened ~50% by drawbox at t=1.5s
            cam_px = frame[620, 950]
            assert cam_px[0] > cam_px[1] and cam_px[0] > cam_px[2]
            assert int(cam_px[0]) < 140
            # Blue circular logo center at (40+70, 30+70) = (110, 100)
            assert frame[100, 110, 2] > 180 and frame[100, 110, 0] < 60
        elif var == "split-barra":
            # White bar at (540, 10)
            assert (frame[10, 540] > 240).all()
            # Webcam at (340 + 260, 540) = (600, 540) is bright red (no drawbox darkening)
            cam_px = frame[600, 540]
            assert cam_px[0] > 200 and cam_px[1] < 60 and cam_px[2] < 60
            # Blue logo center at (50+75, 30+75) = (125, 105)
            assert frame[105, 125, 2] > 180 and frame[105, 125, 0] < 60
        elif var == "split-fina":
            # Dark bar #14141C at (540, 10)
            bar_px = frame[10, 540]
            assert bar_px[0] < 40 and bar_px[1] < 40 and bar_px[2] < 50
            # Webcam at (170 + 100, 900) = (270, 900) is darkened by drawbox at t=1.5s
            cam_px = frame[270, 900]
            assert cam_px[0] > cam_px[1] and cam_px[0] > cam_px[2]
            assert int(cam_px[0]) < 140
            # Blue logo center at (40+60, 25+60) = (100, 85)
            assert frame[85, 100, 2] > 180 and frame[85, 100, 0] < 60

    # Render 1280x720 source with top-right webcam in split-barra
    monkeypatch.setenv("CLIPES_LAYOUT", "split-barra")
    fake_layout_tr = SimpleNamespace(
        split_layout=lambda w, h, fc, variante="split-barra": _make_layout(variante, w, h, fc_corner="tr")
    )
    monkeypatch.setitem(sys.modules, "clipes.layout", fake_layout_tr)
    monkeypatch.setattr(clipes_pkg, "layout", fake_layout_tr, raising=False)

    mp4_tr = render_mod.render_clip(
        clip, 4, words, "", out_dir, cache_dir,
        live_video_path=src_720, channel_credit="twitch.tv/bisteconee",
    )
    frame_tr = _rgb_frame(mp4_tr, 1.5)
    assert (frame_tr[10, 540] > 240).all()  # white bar
    cam_tr = frame_tr[600, 540]  # magenta webcam (R>180, B>180, G<60)
    assert cam_tr[0] > 180 and cam_tr[2] > 180 and cam_tr[1] < 60
    game_tr = frame_tr[960, 540]  # blue game (B>140, R<50)
    assert game_tr[2] > 130 and game_tr[0] < 60


def test_media2_baixa4_invalid_layout_falls_back_without_double_render(tmp_path: Path, monkeypatch):
    """MEDIA-2 + BAIXA-4: missing attrs, sum != 1920, or odd heights fall back directly with 1 ffmpeg call."""
    _clear_layout_and_bar_env(monkeypatch)
    monkeypatch.setenv("CLIPES_MASTER_MODE", "livre")
    monkeypatch.setenv("CLIPES_LAYOUT", "split-barra")

    src = tmp_path / "src.mkv"
    src.write_bytes(b"src")
    out_dir = tmp_path / "out" / "vod"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir(parents=True)
    cache_dir.mkdir()
    monkeypatch.setattr(reframe_mod, "_probe_video_size", lambda p: (1920, 1080))
    _install_fake_layout_and_facecam(
        monkeypatch,
        SimpleNamespace(tipo="overlay", rect=(0.01, 0.01, 0.22, 0.28), metodo="override"),
    )

    bad_layouts = [
        # Missing emenda_y (MEDIA-2: previously raised AttributeError before fallback try)
        SimpleNamespace(cam=_rect(10, 10, 300, 200), game=_rect(400, 0, 1000, 1080), bar_h=340, cam_h=520, game_h=1060),
        # Sum != 1920 (BAIXA-4)
        SimpleNamespace(cam=_rect(10, 10, 300, 200), game=_rect(400, 0, 1000, 1080), bar_h=340, cam_h=520, game_h=1000, emenda_y=860),
        # Odd heights (BAIXA-4)
        SimpleNamespace(cam=_rect(10, 10, 300, 200), game=_rect(400, 0, 1000, 1080), bar_h=341, cam_h=520, game_h=1059, emenda_y=861),
    ]

    for idx, bad in enumerate(bad_layouts, start=1):
        fake_layout = SimpleNamespace(split_layout=lambda w, h, fc, variante="split-barra", _b=bad: _b)
        monkeypatch.setitem(sys.modules, "clipes.layout", fake_layout)
        monkeypatch.setattr(clipes_pkg, "layout", fake_layout, raising=False)

        cmds: list[list[str]] = []
        monkeypatch.setattr(
            render_mod.subprocess,
            "run",
            lambda cmd, **kw: (
                cmds.append(cmd),
                Path(cmd[-1]).write_bytes(b"mp4"),
                SimpleNamespace(returncode=0, stderr="", stdout=""),
            )[-1],
        )
        clip = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title=f"Bad {idx}")
        render_mod.render_clip(clip, idx, [], "", out_dir, cache_dir, live_video_path=src)
        assert clip.layout_fallback is True
        assert clip.layout == "blurred_16x9"
        assert len(cmds) == 1
        assert "vstack" not in cmds[0][cmds[0].index("-filter_complex") + 1]


def test_baixa2_split_barra_small_or_zero_bar_uses_barless_style(tmp_path: Path, monkeypatch):
    """BAIXA-2: split-barra with bar_h < 300 (e.g. 200 or 0) switches to barless style without BarTitle."""
    _clear_layout_and_bar_env(monkeypatch)
    ass_path = tmp_path / "small_bar.ass"
    logo = tmp_path / "logo.jpg"
    logo.write_bytes(b"jpg")
    bar = render_mod.BarConfig(
        h=420, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display="CortesLR", handle="@cortes_lr1", logo=logo,
    )
    words = [Word(0.5, 1.0, " Olha"), Word(1.1, 1.8, " isso!")]

    for bar_h in (200, 0):
        cam_h = 520
        lay = SimpleNamespace(
            cam=_rect(20, 16, 420, 200),
            game=_rect(450, 0, 1080, 1080),
            bar_h=bar_h,
            cam_h=cam_h,
            game_h=1920 - bar_h - cam_h,
            emenda_y=bar_h + cam_h,
            variante="split-barra",
        )
        fc, _ = render_mod.build_video_filter(
            ass_path, n_subs=2, bar=bar, split=lay, variante="split-barra", has_hook=True
        )
        assert "drawbox=x=0:y=0:w=1080:h=520:color=black@0.5" in fc
        assert "scale=140:140" in fc
        assert "overlay=40:30[v_logo]" in fc
        if bar_h == 0:
            assert ",pad=" not in fc
        else:
            assert f",pad=1080:1920:0:{bar_h}:black[v_base]" in fc

        render_mod.build_ass(
            words, 0.0, 10.0, ass_path,
            hook_top="GANCHO TESTE", title="Titulo", bar=bar, split=lay, variante="split-barra",
        )
        txt = ass_path.read_text(encoding="utf-8")
        assert "BarTitle" not in txt
        assert "TopHook" in txt
        assert "Style: BarName,DejaVu Sans,50,&H00FFFFFF&" in txt


def test_baixa3_ass_escape_collapses_newlines_and_layout_key_sanitizes(tmp_path: Path):
    """BAIXA-3: _ass_escape collapses CR/LF so no extra Dialogue line is injected; layout keys remove '../'."""
    escaped = render_mod._ass_escape("Linha 1\nDialogue: 0,0:00:00.00,0:00:01.00,Cap,,0,0,0,,evil\r\nLinha 2")
    assert "\n" not in escaped and "\r" not in escaped
    assert escaped == "Linha 1 Dialogue: 0,0:00:00.00,0:00:01.00,Cap,,0,0,0,,evil Linha 2"

    ass_path = tmp_path / "inject.ass"
    bar = render_mod.BarConfig(
        h=420, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display="Cortes\nDialogue: fake", handle="@cortes\r\nlr1", logo=None,
    )
    n_ev = render_mod.build_ass(
        [Word(0.2, 0.8, "oi")], 0.0, 5.0, ass_path,
        channel_credit="twitch.tv/canal\nDialogue: fake2", bar=bar,
    )
    lines = [l for l in ass_path.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    assert len(lines) == n_ev
    assert not any(l.startswith("Dialogue: fake") for l in lines)

    out_dir = tmp_path / "vod"
    k1 = render_mod._resolve_layout_key("", "twitch.tv/../../x", out_dir)
    k2 = render_mod._resolve_layout_key("../evil/path", "", out_dir)
    k3 = render_mod._resolve_layout_key("..", "", out_dir)
    for k in (k1, k2, k3):
        assert ".." not in k and "/" not in k and "\\" not in k
    assert k1.endswith("x") and k3 == "default"


def test_render_resolve_layout_key_strips_kick_prefix(tmp_path: Path):
    out_dir = tmp_path / "vod"
    assert render_mod._resolve_layout_key("", "kick.com/foo-bar", out_dir) == "foo-bar"
    assert render_mod._resolve_layout_key("", "twitch.tv/gaules", out_dir) == "gaules"
    assert render_mod._resolve_layout_key("", "@podpah", out_dir) == "podpah"


def test_real_ffmpeg_media1_18W_hook_and_baixa1_partbadge_no_overlap(tmp_path: Path, monkeypatch):
    """MEDIA-1 + BAIXA-1 + BAIXA-2 real 3s ffmpeg check: 18 'W's hook fits inside cam <=3 lines; PartBadge clears Cap."""
    _clear_layout_and_bar_env(monkeypatch)
    monkeypatch.setenv("CLIPES_MASTER_MODE", "livre")

    src = tmp_path / "dark_src.mkv"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=#101820:s=1920x1080:r=30:d=3.2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3.2",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src),
        ],
        check=True,
    )
    out_dir = tmp_path / "out" / "m1"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir(parents=True)
    cache_dir.mkdir()
    _install_fake_layout_and_facecam(
        monkeypatch,
        SimpleNamespace(tipo="overlay", rect=(0.01, 0.01, 0.22, 0.28), metodo="override"),
    )

    # MEDIA-1: test 18 'W's (single token and multi-line) in split-fina (smaller cam=700)
    monkeypatch.setenv("CLIPES_LAYOUT", "split-fina")
    words = [Word(0.5, 2.5, " gjpqy"), Word(0.5, 2.5, " GJPQY!")]
    for idx, hook in enumerate(["W" * 18, "WWWWWWWWWWWWWW WWWWWWWWWWWWWW WWWWWWWWWWWWWW"], start=1):
        clip = SnappedClip(
            peak=1.5, start=0.0, end=3.0, score=0.9,
            title="Teste W", hook_top=hook, part_index=1, part_total=2,
        )
        mp4 = render_mod.render_clip(clip, idx, words, "", out_dir, cache_dir, live_video_path=src)
        for sample_t in (0.20, 1.5):  # peak 106% pop + settled
            pop_frame = _rgb_frame(mp4, sample_t)
            cam_band = pop_frame[180:820, :]
            bright = cam_band.max(axis=2) > 80
            assert bright.any()
            _, xs_h = np.where(bright)
            assert xs_h.min() >= 60 and xs_h.max() <= 1020
            row_has = bright.any(axis=1)
            bands = sum(1 for k in range(len(row_has)) if row_has[k] and (k == 0 or not row_has[k - 1]))
            assert 1 <= bands <= 3

        frame = _rgb_frame(mp4, 1.5)
        # BAIXA-1: PartBadge at emenda_y+70=940 and Cap at y=1380 have a clean gap
        seam_region = frame[900:1450, :]
        text_rows = (seam_region.max(axis=2) > 100).any(axis=1)
        v_bands: list[tuple[int, int]] = []
        st = None
        for r_i, active in enumerate(text_rows):
            if active and st is None:
                st = 900 + r_i
            elif not active and st is not None:
                v_bands.append((st, 900 + r_i - 1))
                st = None
        assert len(v_bands) == 2  # PartBadge band + Cap band separated by empty background rows
        assert v_bands[0][1] < v_bands[1][0] - 10

    # BAIXA-2 real 3s ffmpeg render: split-barra with CLIPES_SPLIT_BAR_H=200 uses barless style (white text + hook on cam)
    monkeypatch.setenv("CLIPES_LAYOUT", "split-barra")
    monkeypatch.setenv("CLIPES_BRAND_NAME", "@cortes_lr1")
    monkeypatch.setenv("CLIPES_BRAND_DISPLAY", "CortesLR")
    monkeypatch.setattr(
        clipes_pkg,
        "layout",
        SimpleNamespace(
            split_layout=lambda w, h, fc, variante="split-barra": SimpleNamespace(
                cam=_rect(20, 16, 420, 200),
                game=_rect(450, 0, 1080, 1080),
                bar_h=200,
                cam_h=520,
                game_h=1200,
                emenda_y=720,
                variante="split-barra",
            )
        ),
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "clipes.layout", clipes_pkg.layout)
    clip_b2 = SnappedClip(peak=1.5, start=0.0, end=3.0, score=0.9, title="Bar 200", hook_top="GANCHO")
    mp4_b2 = render_mod.render_clip(clip_b2, 3, [], "", out_dir, cache_dir, live_video_path=src)
    frame_b2 = _rgb_frame(mp4_b2, 1.5)
    # Bar at y=10 is black (not white), and TopHook renders bright white pixels in cam [200..720]
    assert (frame_b2[10, 540] < 30).all()
    assert (frame_b2[350:550, 300:780].max(axis=2) > 150).any()


def test_render_clip_real_ffmpeg_split_starcuts_and_antiflop(tmp_path: Path, monkeypatch):
    """Real 3s synthetic render in default split-starcuts: pixels, 48kHz audio, and no source/Lavf tags."""
    _clear_layout_and_bar_env(monkeypatch)
    monkeypatch.setattr(render_mod, "_WARNED_MISSING_LOGOS", set())
    assert render_mod.OUTRO_DURATION == 0.5
    assert render_mod.is_blur_bg_enabled() is True
    assert render_mod._master_mode() == "livre"

    src = tmp_path / "src_starcuts.mkv"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            "color=c=#1E9020:s=1920x1080:r=30:d=3.2,"
            "drawbox=x=16:y=12:w=440:h=310:color=#FF1010:t=fill",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3.2",
            "-metadata", "title=SourceTagShouldBeStripped",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src),
        ],
        check=True,
    )

    out_dir = tmp_path / "out" / "starcuts"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir(parents=True)
    cache_dir.mkdir()
    _install_fake_layout_and_facecam(
        monkeypatch,
        SimpleNamespace(tipo="overlay", rect=(0.01, 0.01, 0.22, 0.28), metodo="override"),
    )

    # Restore real split_layout for split-starcuts (0+600+1320)
    from clipes import layout as real_layout
    monkeypatch.setitem(sys.modules, "clipes.layout", real_layout)
    monkeypatch.setattr(clipes_pkg, "layout", real_layout, raising=False)

    words = [Word(0.5, 1.0, " Olha"), Word(1.1, 1.8, " isso!")]
    clip = SnappedClip(
        peak=1.5, start=0.0, end=3.0, score=0.95,
        title="Que isso mandy??", hook_top="Que isso mandy??",
    )
    mp4 = render_mod.render_clip(
        clip, 1, words, "", out_dir, cache_dir,
        live_video_path=src, channel_credit="twitch.tv/alanzoka",
    )
    assert clip.layout == "split_webcam"

    probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(mp4)],
            capture_output=True, text=True, check=True,
        ).stdout
    )
    v_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a_stream = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert (v_stream["width"], v_stream["height"]) == (1080, 1920)
    assert a_stream["sample_rate"] == "48000"
    fmt_tags = probe.get("format", {}).get("tags", {})
    assert "SourceTagShouldBeStripped" not in json.dumps(probe)
    assert "Lavf" not in str(fmt_tags.get("encoder", ""))

    frame = _rgb_frame(mp4, 1.5)
    # Red webcam in top band (y=300, x=540)
    cam_px = frame[300, 540]
    assert cam_px[0] > 180 and cam_px[1] < 60 and cam_px[2] < 60
    # Purple/blue divider bar at y=595, x=100 (strong blue component, B > G)
    div_px = frame[595, 100]
    assert int(div_px[2]) > 140 and int(div_px[2]) > int(div_px[1])
    # White title box at y=660, x=350
    box_px = frame[660, 350]
    assert (box_px > 235).all()
    # Green gameplay below y=770 (y=820, x=540)
    game_px = frame[820, 540]
    assert game_px[1] > 100 and game_px[1] > game_px[0] + 40 and game_px[1] > game_px[2] + 40


def test_t9_master_livre_teto_6000k_sem_retry_nem_downscale(tmp_path: Path, monkeypatch):
    """DSC-05: master livre passa teto 6000k ao ffmpeg, sem retry e sem scale=720, cabe_discord=False."""
    _clear_layout_and_bar_env(monkeypatch)
    monkeypatch.setenv("CLIPES_MASTER_MODE", "livre")
    monkeypatch.setattr(reframe_mod, "_probe_video_size", lambda p: (1920, 1080))
    _install_fake_layout_and_facecam(
        monkeypatch,
        SimpleNamespace(tipo="nenhuma", rect=None, metodo="yunet"),
    )
    src = tmp_path / "src.mkv"
    src.write_bytes(b"src")
    out_dir = tmp_path / "out" / "vod1"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir(parents=True)
    cache_dir.mkdir()

    cmds: list[list[str]] = []

    def fake_run(cmd, **kw):
        cmds.append(cmd)
        # Saída bem acima de 10 MB: se houvesse retry por limite Discord, ele aconteceria aqui
        with open(cmd[-1], "wb") as fh:
            fh.truncate(50 * 1024 * 1024)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)
    clip = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Livre teto")
    render_mod.render_clip(clip, 1, [], "", out_dir, cache_dir, live_video_path=src)

    assert len(cmds) == 1
    cmd = cmds[0]
    joined = " ".join(cmd)
    assert "-crf 21 -maxrate 6000k -bufsize 12000k" in joined
    assert "scale=720" not in cmd[cmd.index("-filter_complex") + 1]
    assert "scale=720" not in joined
    assert clip.cabe_discord is False


def _render_sem_webcam(tmp_path: Path, monkeypatch, env: dict[str, str], full_cmd: bool = False):
    _clear_layout_and_bar_env(monkeypatch)
    monkeypatch.setenv("CLIPES_MASTER_MODE", "livre")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    src = tmp_path / "src.mkv"
    src.write_bytes(b"src")
    out_dir = tmp_path / "out" / "vod1"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir(parents=True)
    cache_dir.mkdir()
    _install_fake_layout_and_facecam(
        monkeypatch, SimpleNamespace(tipo="nenhuma", rect=None, metodo="yunet"),
    )
    cmds: list[list[str]] = []
    monkeypatch.setattr(
        render_mod.subprocess,
        "run",
        lambda cmd, **kw: (
            cmds.append(cmd),
            Path(cmd[-1]).write_bytes(b"mp4"),
            SimpleNamespace(returncode=0, stderr="", stdout=""),
        )[-1],
    )
    clip = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Sem cam", hook_top="")
    render_mod.render_clip(clip, 1, [], "", out_dir, cache_dir, live_video_path=src)
    cmd = next(c for c in cmds if "-filter_complex" in c)
    if full_cmd:
        return clip, cmd
    return clip, cmd[cmd.index("-filter_complex") + 1]


def test_lay01_sem_webcam_blur_bg_off_nao_usa_barras_pretas(tmp_path: Path, monkeypatch):
    clip, fc = _render_sem_webcam(tmp_path, monkeypatch, {"CLIPES_BLUR_BG": "0"})
    assert clip.layout == "blurred_16x9"
    assert "boxblur" in fc


def test_lay01_fallback_recorte_usa_crop_central(tmp_path: Path, monkeypatch):
    clip, fc = _render_sem_webcam(tmp_path, monkeypatch, {"CLIPES_LAYOUT_FALLBACK": "recorte"})
    assert clip.layout == "reframed_center"
    assert "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920" in fc


def test_lay01_barras_pretas_so_com_full_e_blur_off_explicitos(tmp_path: Path, monkeypatch):
    clip, fc = _render_sem_webcam(tmp_path, monkeypatch, {"CLIPES_LAYOUT": "full", "CLIPES_BLUR_BG": "0"})
    assert clip.layout == "black_bars_16x9"
    assert "pad=1080:1920" in fc


def test_fim01_cartao_final_off_sem_tpad_apad_e_t_igual_duracao(tmp_path: Path, monkeypatch):
    clip, cmd = _render_sem_webcam(tmp_path, monkeypatch, {"CLIPES_CARTAO_FINAL": "0"}, full_cmd=True)
    joined = " ".join(cmd)
    assert "tpad" not in joined and "apad" not in joined
    assert f"afade=t=out:st={clip.duration - 0.5:.3f}:d=0.5" in joined
    assert cmd[len(cmd) - 1 - cmd[::-1].index("-t") + 1] == f"{clip.duration:.3f}"


def test_fim01_cartao_final_on_mantem_comando_atual(tmp_path: Path, monkeypatch):
    clip, cmd = _render_sem_webcam(tmp_path, monkeypatch, {"CLIPES_CARTAO_FINAL": "1"}, full_cmd=True)
    joined = " ".join(cmd)
    assert "tpad=stop_mode=clone:stop_duration=0.5" in joined
    assert "apad=pad_dur=0.5" in joined
    assert cmd[len(cmd) - 1 - cmd[::-1].index("-t") + 1] == f"{clip.duration + 0.5:.3f}"


def test_fim02_ass_sem_cartao_final_quando_off(tmp_path: Path):
    ass = tmp_path / "off.ass"
    render_mod.build_ass(
        [Word(1.0, 1.4, " Olha")], 0.0, 10.0, ass,
        channel_credit="twitch.tv/x", part_index=1, part_total=2, outro_duration=0.0,
    )
    txt = ass.read_text(encoding="utf-8")
    assert "Live completa" not in txt and "Parte 2" not in txt
