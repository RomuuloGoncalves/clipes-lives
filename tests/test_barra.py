"""Tests for the fixed top brand bar (F2 / BAR-01..08)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from clipes import reframe as reframe_mod
from clipes import render as render_mod
from clipes.models import SnappedClip, Word


def _clear_bar_env(monkeypatch) -> None:
    for key in (
        "CLIPES_BAR",
        "CLIPES_BRAND_NAME",
        "CLIPES_BRAND_DISPLAY",
        "CLIPES_BRAND_LOGO",
        "CLIPES_BAR_STYLE",
        "CLIPES_BAR_H",
    ):
        monkeypatch.delenv(key, raising=False)


def test_bar_config_env_rules_and_missing_logo_warning(tmp_path: Path, monkeypatch, capsys):
    _clear_bar_env(monkeypatch)
    monkeypatch.setattr(render_mod, "_WARNED_MISSING_LOGOS", set())

    # Disabled when CLIPES_BRAND_NAME and CLIPES_BAR are unset
    assert render_mod.bar_config() is None

    logo_file = tmp_path / "logo.png"
    logo_file.write_bytes(b"fake-png")
    monkeypatch.setenv("CLIPES_BRAND_LOGO", str(logo_file))

    # Enabled automatically when CLIPES_BRAND_NAME exists; adds '@' if missing
    monkeypatch.setenv("CLIPES_BRAND_NAME", "cortes_lr1")
    monkeypatch.setenv("CLIPES_BRAND_DISPLAY", "CortesLR")
    cfg = render_mod.bar_config()
    assert cfg is not None
    assert cfg.handle == "@cortes_lr1"
    assert cfg.display == "CortesLR"
    assert cfg.h == 420
    assert (cfg.bg, cfg.fg, cfg.handle_fg) == ("#FFFFFF", "#000000", "#606060")
    assert cfg.logo == logo_file

    # Keep existing '@'
    monkeypatch.setenv("CLIPES_BRAND_NAME", "@cortes_lr1")
    assert render_mod.bar_config().handle == "@cortes_lr1"

    # CLIPES_BAR=0 disables even when CLIPES_BRAND_NAME is set
    monkeypatch.setenv("CLIPES_BAR", "0")
    assert render_mod.bar_config() is None

    # CLIPES_BAR=1 without CLIPES_BRAND_NAME enables title-only bar
    monkeypatch.setenv("CLIPES_BAR", "1")
    monkeypatch.delenv("CLIPES_BRAND_NAME")
    monkeypatch.delenv("CLIPES_BRAND_DISPLAY")
    cfg_no_name = render_mod.bar_config()
    assert cfg_no_name is not None
    assert cfg_no_name.handle == ""
    assert cfg_no_name.display == ""

    # CLIPES_BAR_H boundaries and invalid fallback with warning
    for valid_h in ("300", "600"):
        monkeypatch.setenv("CLIPES_BAR_H", valid_h)
        assert render_mod.bar_config().h == int(valid_h)
    capsys.readouterr()
    for bad_h in ("299", "601", "abc"):
        monkeypatch.setenv("CLIPES_BAR_H", bad_h)
        assert render_mod.bar_config().h == 420
        assert "CLIPES_BAR_H" in capsys.readouterr().out

    # Styles: claro, escuro, invalid -> claro
    monkeypatch.setenv("CLIPES_BAR_H", "420")
    monkeypatch.setenv("CLIPES_BAR_STYLE", "escuro")
    cfg_dark = render_mod.bar_config()
    assert (cfg_dark.bg, cfg_dark.fg, cfg_dark.handle_fg) == ("#000000", "#FFFFFF", "#B0B0B0")

    monkeypatch.setenv("CLIPES_BAR_STYLE", "roxo")
    cfg_invalid_style = render_mod.bar_config()
    assert (cfg_invalid_style.bg, cfg_invalid_style.fg, cfg_invalid_style.handle_fg) == (
        "#FFFFFF",
        "#000000",
        "#606060",
    )

    # Missing logo warns exactly once per process and sets logo=None
    missing = tmp_path / "nao_existe.jpg"
    monkeypatch.setenv("CLIPES_BRAND_LOGO", str(missing))
    capsys.readouterr()
    cfg_m1 = render_mod.bar_config()
    cfg_m2 = render_mod.bar_config()
    assert cfg_m1 is not None and cfg_m1.logo is None
    assert cfg_m2 is not None and cfg_m2.logo is None
    out = capsys.readouterr().out
    expected_msg = f"[render] logo nao encontrado: {missing}; barra so com texto"
    assert out.count(expected_msg) == 1

    # Default logo path resolves to /app/assets/logo.jpg when mounted/present in image
    monkeypatch.delenv("CLIPES_BRAND_LOGO")
    if Path(render_mod.DEFAULT_BRAND_LOGO).is_file():
        cfg_default_logo = render_mod.bar_config()
        assert cfg_default_logo is not None
        assert cfg_default_logo.logo == Path(render_mod.DEFAULT_BRAND_LOGO)


def test_build_ass_bar_text_positions_escape_and_disabled_parity(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIPES_HOOK_TOP", raising=False)
    words = [Word(1.0, 1.4, " Olha"), Word(1.5, 2.0, " isso!")]

    # Disabled bar produces identical ASS to default (including TopHook)
    ass_default = tmp_path / "default.ass"
    ass_bar_none = tmp_path / "bar_none.ass"
    n1 = render_mod.build_ass(
        words, 0.0, 10.0, ass_default,
        channel_credit="twitch.tv/gaules", part_index=1, part_total=2,
        hook_top="Ele perdeu tudo?",
    )
    n2 = render_mod.build_ass(
        words, 0.0, 10.0, ass_bar_none,
        channel_credit="twitch.tv/gaules", part_index=1, part_total=2,
        hook_top="Ele perdeu tudo?", title="Titulo ignorado", bar=None,
    )
    assert n1 == n2
    assert ass_default.read_text(encoding="utf-8") == ass_bar_none.read_text(encoding="utf-8")

    # Bar enabled with logo: text at x=240, escapes ASS chars, suppresses TopHook, covers outro
    logo = tmp_path / "logo.jpg"
    logo.write_bytes(b"jpg")
    bar_logo = render_mod.BarConfig(
        h=420, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display=r"Cortes {LR} \1", handle="@cortes_{lr1}", logo=logo,
    )
    ass_with_logo = tmp_path / "bar_logo.ass"
    render_mod.build_ass(
        words, 0.0, 10.0, ass_with_logo,
        outro_duration=1.2,
        hook_top="O bahia não é mais leão de pc KKKKKKK e mais texto longo para cortar",
        title="Fallback title",
        bar=bar_logo,
    )
    txt = ass_with_logo.read_text(encoding="utf-8")
    assert "TopHook" not in txt
    assert "Style: BarName,DejaVu Sans,56,&H00000000&" in txt
    assert "Style: BarHandle,DejaVu Sans,44,&H00606060&" in txt
    assert "Style: BarTitle,DejaVu Sans,60,&H00000000&" in txt

    name_line = next(l for l in txt.splitlines() if ",BarName," in l)
    handle_line = next(l for l in txt.splitlines() if ",BarHandle," in l)
    title_line = next(l for l in txt.splitlines() if ",BarTitle," in l)
    assert "0:00:00.00,0:00:11.20,BarName," in name_line
    assert r"\pos(240," in name_line
    assert "Cortes (LR) 1" in name_line
    assert "0:00:00.00,0:00:11.20,BarHandle," in handle_line
    assert r"\pos(240," in handle_line
    assert "@cortes_(lr1)" in handle_line
    assert "0:00:00.00,0:00:11.20,BarTitle," in title_line
    assert r"\pos(540," in title_line
    # Check title y is between 210 and H-20 (400)
    pos_part = title_line.split(r"\pos(540,")[1].split(")")[0]
    assert 210 <= int(pos_part) <= 400
    # Title wrapped <= 2 lines, <= 26 chars per line, no inline color override
    title_payload = title_line.split("}", 1)[1]
    assert r"\c&H" not in title_payload
    title_lines = title_payload.split(r"\N")
    assert 1 <= len(title_lines) <= 2
    assert all(len(line) <= 26 for line in title_lines)
    # hook longo nao cabe em 2 linhas -> usa o titulo inteiro, sem reticencias
    assert title_payload == "Fallback title"

    # Without logo: name & handle start at x=60; hook_top empty -> falls back to clip.title
    bar_no_logo = render_mod.BarConfig(
        h=420, bg="#000000", fg="#FFFFFF", handle_fg="#B0B0B0",
        display="", handle="@cortes_lr1", logo=None,
    )
    ass_no_logo = tmp_path / "bar_no_logo.ass"
    render_mod.build_ass(
        words, 0.0, 10.0, ass_no_logo,
        outro_duration=1.2, hook_top="", title="Titulo do clipe", bar=bar_no_logo,
    )
    txt_nl = ass_no_logo.read_text(encoding="utf-8")
    assert ",BarName," not in txt_nl
    assert r"\pos(60," in next(l for l in txt_nl.splitlines() if ",BarHandle," in l)
    assert "Titulo do clipe" in next(l for l in txt_nl.splitlines() if ",BarTitle," in l)

    # Empty hook_top and empty title -> no BarTitle dialogue
    ass_no_title = tmp_path / "bar_no_title.ass"
    render_mod.build_ass(words, 0.0, 10.0, ass_no_title, hook_top="", title="", bar=bar_no_logo)
    assert ",BarTitle," not in ass_no_title.read_text(encoding="utf-8")


def _dialogue_texts(path: Path) -> list[str]:
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]


def test_hook_longo_nunca_termina_com_reticencias(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIPES_HOOK_TOP", raising=False)
    words = [Word(1.0, 1.4, " Olha")]
    bar = render_mod.BarConfig(
        h=420, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display="CortesLR", handle="@cortes_lr1", logo=None,
    )
    hook_longo = "Ele perdeu tudo na hora quando o time inteiro morreu no mapa final"
    for bar_arg in (None, bar):
        out = tmp_path / f"hook_{bar_arg is None}.ass"
        render_mod.build_ass(
            words, 0.0, 10.0, out, hook_top=hook_longo, title="Titulo curto", bar=bar_arg,
        )
        for line in _dialogue_texts(out):
            payload = line.split("}", 1)[-1]
            assert "…" not in payload and "..." not in payload, line


def test_hook_nao_cabe_usa_titulo_e_nada_cabe_deixa_vazio(tmp_path: Path):
    words = [Word(1.0, 1.4, " Olha")]
    bar = render_mod.BarConfig(
        h=420, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display="", handle="", logo=None,
    )

    # Hook sem caber, titulo cabe -> titulo aparece e hook_cabe continua True
    clip_ok = SimpleNamespace()
    out = tmp_path / "titulo.ass"
    render_mod.build_ass(
        words, 0.0, 10.0, out, hook_top="A" * 30 + " B", title="Titulo do clipe",
        bar=bar, clip=clip_ok,
    )
    assert "Titulo do clipe" in next(l for l in _dialogue_texts(out) if ",BarTitle," in l)
    assert clip_ok.hook_cabe is True

    # Nada cabe (hook e titulo com palavra maior que a linha) -> sem BarTitle e hook_cabe False
    clip_vazio = SimpleNamespace()
    out_vazio = tmp_path / "vazio.ass"
    render_mod.build_ass(
        words, 0.0, 10.0, out_vazio, hook_top="A" * 30, title="B" * 30,
        bar=bar, clip=clip_vazio,
    )
    assert not any(",BarTitle," in l for l in _dialogue_texts(out_vazio))
    assert clip_vazio.hook_cabe is False


def test_build_ass_existing_elements_shifted_below_bar(tmp_path: Path):
    words = [Word(1.0, 1.4, " Olá")]
    bar = render_mod.BarConfig(
        h=460, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display="CortesLR", handle="@cortes_lr1", logo=None,
    )
    ass_bar = tmp_path / "shifted.ass"
    render_mod.build_ass(
        words, 0.0, 10.0, ass_bar,
        channel_credit="twitch.tv/gaules", part_index=1, part_total=2,
        outro_duration=1.2, bar=bar,
    )
    txt = ass_bar.read_text(encoding="utf-8")
    # Credit at (120, H+20) = (120, 480)
    assert r"\pos(120,480)\fad(300,400)" in txt
    assert r"\pos(120,480)\alpha&H99&" in txt
    # PartBadge at y = H+70 = 530
    assert r"\pos(540,530)" in txt
    assert ",120,200,530,1" in txt
    # Card at y = H + (1920 - H) // 2 = 460 + 730 = 1190
    assert r"\pos(540,1190)\fad(250,0)" in txt
    # Cap MarginV remains 560
    cap_style = next(l for l in txt.splitlines() if l.startswith("Style: Cap,"))
    assert cap_style.split(",")[-4:-1] == ["120", "200", "560"]

    # Without bar: original positions
    ass_plain = tmp_path / "plain.ass"
    render_mod.build_ass(
        words, 0.0, 10.0, ass_plain,
        channel_credit="twitch.tv/gaules", part_index=1, part_total=2,
        outro_duration=1.2, bar=None,
    )
    txt_p = ass_plain.read_text(encoding="utf-8")
    assert r"\pos(120,600)" in txt_p
    assert r"\pos(540,490)" in txt_p
    assert r"\pos(540,960)" in txt_p


def test_reframe_compute_crop_with_out_h(tmp_path: Path, monkeypatch):
    # Default out_h=1920 -> 9:16 crop (608x1080)
    boxes = [(320.0, 180.0, 50.0, 60.0), (320.0, 180.0, 50.0, 60.0)]
    crop_default = reframe_mod.compute_vertical_crop(boxes, total_frames=4, src_w=1920, src_h=1080)
    assert crop_default is not None
    assert crop_default[2:] == (608, 1080)

    # out_h=1500 -> aspect 1080:1500 (0.72 * 1080 = 777.6 -> 778, within +-2px)
    crop_bar = reframe_mod.compute_vertical_crop(boxes, total_frames=4, src_w=1920, src_h=1080, out_h=1500)
    assert crop_bar is not None
    _, _, cw, ch = crop_bar
    assert ch == 1080
    expected_w = ch * (1080.0 / 1500.0)
    assert abs(cw - expected_w) <= 2.0
    assert cw % 2 == 0


def test_build_video_filter_and_render_clip_with_bar(tmp_path: Path, monkeypatch):
    ass_path = tmp_path / "c.ass"
    logo = tmp_path / "logo.jpg"
    logo.write_bytes(b"jpg")

    # Disabled bar matches original strings
    monkeypatch.setenv("CLIPES_BLUR_BG", "0")
    fc_plain, map_plain = render_mod.build_video_filter(ass_path, n_subs=2, outro_duration=1.2, bar=None)
    assert map_plain == "[v_out]"
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" in fc_plain
    assert "drawbox" not in fc_plain

    bar_no_logo = render_mod.BarConfig(
        h=420, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display="CortesLR", handle="@cortes_lr1", logo=None,
    )
    bar_with_logo = render_mod.BarConfig(
        h=420, bg="#FFFFFF", fg="#000000", handle_fg="#606060",
        display="CortesLR", handle="@cortes_lr1", logo=logo,
    )

    # Branch 1: normal (black bars) + bar without logo
    fc_norm, _ = render_mod.build_video_filter(ass_path, n_subs=2, blur_bg=False, bar=bar_no_logo)
    assert "scale=1080:1500:force_original_aspect_ratio=decrease:force_divisible_by=2" in fc_norm
    assert "pad=1080:1920:(ow-iw)/2:420:black" in fc_norm
    assert "drawbox=x=0:y=0:w=1080:h=420:color=#FFFFFF:t=fill[v_base]" in fc_norm
    assert "[1:v]" not in fc_norm and "geq=" not in fc_norm

    # Branch 2: blur + bar with logo
    fc_blur, map_blur = render_mod.build_video_filter(ass_path, n_subs=2, blur_bg=True, bar=bar_with_logo)
    assert map_blur == "[v_out]"
    assert "scale=1080:1500,pad=1080:1920:0:420:black" in fc_blur
    assert "overlay=(W-w)/2:420,drawbox=x=0:y=0:w=1080:h=420:color=#FFFFFF:t=fill[v_base]" in fc_blur
    assert "[1:v]crop='min(iw,ih)':'min(iw,ih)',scale=150:150,setsar=1,format=rgba,geq=" in fc_blur
    assert "[v_base][logo]overlay=60:40[v_logo]" in fc_blur
    assert fc_blur.index("[v_logo]") < fc_blur.index("tpad=") < fc_blur.index("subtitles=")

    # Branch 3: crop (reframe) + bar
    fc_crop, _ = render_mod.build_video_filter(
        ass_path, n_subs=2, blur_bg=False, crop=(100, 0, 778, 1080), bar=bar_with_logo
    )
    assert "[0:v]crop=778:1080:100:0,scale=1080:1500,pad=1080:1920:0:420:black,setsar=1,drawbox=" in fc_crop
    assert "[v_base][logo]overlay=60:40[v_logo]" in fc_crop

    # render_clip integration: passes -i logo and out_h=1500 to detect_speaker_crop
    _clear_bar_env(monkeypatch)
    monkeypatch.setenv("CLIPES_LAYOUT", "full")
    monkeypatch.setenv("CLIPES_BRAND_NAME", "@cortes_lr1")
    monkeypatch.setenv("CLIPES_BRAND_DISPLAY", "CortesLR")
    monkeypatch.setenv("CLIPES_BRAND_LOGO", str(logo))

    reframe_calls: list[dict] = []
    monkeypatch.setattr(
        reframe_mod,
        "detect_speaker_crop",
        lambda *a, **kw: (reframe_calls.append(kw), (100, 0, 778, 1080))[-1],
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
    src = tmp_path / "src.mkv"
    src.write_bytes(b"src")
    out_dir, cache_dir = tmp_path / "out", tmp_path / "cache"
    out_dir.mkdir()
    cache_dir.mkdir()
    clip = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Teste barra")
    render_mod.render_clip(clip, 1, [], "", out_dir, cache_dir, live_video_path=src, reframe=True)
    assert reframe_calls[0]["out_h"] == 1500
    cmd = cmds[0]
    i_indices = [i for i, tok in enumerate(cmd) if tok == "-i"]
    assert len(i_indices) == 2
    assert cmd[i_indices[0] + 1] == str(src)
    assert cmd[i_indices[1] + 1] == str(logo)


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


def test_render_clip_real_ffmpeg_brand_bar_with_and_without_logo(tmp_path: Path, monkeypatch):
    """Real 3s synthetic render: 1080x1920, white bar pixel at (540,10), circular red logo at (135,115)."""
    _clear_bar_env(monkeypatch)
    monkeypatch.delenv("CLIPES_BLUR_BG", raising=False)
    monkeypatch.setattr(render_mod, "_WARNED_MISSING_LOGOS", set())

    src = tmp_path / "src.mkv"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src),
        ],
        check=True,
    )
    # Non-square 300x200 solid red PNG logo to exercise center crop + circular geq alpha
    logo = tmp_path / "red_300x200.png"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=red:s=300x200",
            "-frames:v", "1", str(logo),
        ],
        check=True,
    )

    out_dir, cache_dir = tmp_path / "out", tmp_path / "cache"
    out_dir.mkdir()
    cache_dir.mkdir()
    words = [Word(0.5, 1.0, " Olha"), Word(1.1, 1.8, " isso!")]
    clip = SnappedClip(
        peak=1.5, start=0.0, end=3.0, score=0.9,
        title="O bahia nao e mais leao de pc KKKKKKK",
        hook_top="O bahia não é mais leão de pc KKKKKKK",
    )

    # 1) With circular logo
    monkeypatch.setenv("CLIPES_BRAND_NAME", "@cortes_lr1")
    monkeypatch.setenv("CLIPES_BRAND_DISPLAY", "CortesLR")
    monkeypatch.setenv("CLIPES_BRAND_LOGO", str(logo))
    mp4_logo = render_mod.render_clip(
        clip, 1, words, "", out_dir, cache_dir, live_video_path=src, channel_credit="twitch.tv/gaules"
    )
    info = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(mp4_logo)],
            capture_output=True, text=True, check=True,
        ).stdout
    )
    assert (info["streams"][0]["width"], info["streams"][0]["height"]) == (1080, 1920)

    for sample_t in (1.0, 3.25):  # active clip + outro (0.5s)
        frame = _rgb_frame(mp4_logo, sample_t)
        # White background bar at (x=540, y=10)
        assert (frame[10, 540] > 240).all()
        # Center of 150px circular logo at (x=135, y=115) is red (not white)
        assert frame[115, 135, 0] > 200 and frame[115, 135, 1] < 50 and frame[115, 135, 2] < 50
        # Corner of logo bounding box at (x=62, y=42) is outside circle -> white background
        assert (frame[42, 62] > 240).all()

    # 2) Without logo (missing path -> text-only bar)
    monkeypatch.setenv("CLIPES_BRAND_LOGO", str(tmp_path / "missing.png"))
    mp4_nologo = render_mod.render_clip(clip, 2, words, "", out_dir, cache_dir, live_video_path=src)
    frame_nl = _rgb_frame(mp4_nologo, 1.0)
    assert (frame_nl[10, 540] > 240).all()
    assert (frame_nl[115, 135] > 240).all()

    # 3) Real shipped JPEG asset (/app/assets/logo.jpg) when CLIPES_BRAND_LOGO is unset
    if Path(render_mod.DEFAULT_BRAND_LOGO).is_file():
        monkeypatch.delenv("CLIPES_BRAND_LOGO")
        mp4_jpg = render_mod.render_clip(clip, 3, words, "", out_dir, cache_dir, live_video_path=src)
        frame_jpg = _rgb_frame(mp4_jpg, 1.0)
        assert (frame_jpg[10, 540] > 240).all()
        assert (frame_jpg[42, 62] > 240).all()
