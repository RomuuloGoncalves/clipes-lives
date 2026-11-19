"""Render 1080x1920 vertical clips with blurred background and burned-in ASS captions."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any

from . import config
from .ingest import js_runtime_args
from .models import SnappedClip, Word

SAFE_MARGIN_L = 120
SAFE_MARGIN_R = 200
SAFE_MARGIN_V = 560
OUTRO_DURATION = 1.2
DEFAULT_BAR_H = 420
MIN_BAR_H = 300
MAX_BAR_H = 600
DEFAULT_BRAND_LOGO = "/app/assets/logo.jpg"
DEFAULT_LAYOUT = "split-barra"
VALID_LAYOUTS = ("full", "split-cheio", "split-barra", "split-fina")

_BAR_STYLES: dict[str, tuple[str, str, str]] = {
    "claro": ("#FFFFFF", "#000000", "#606060"),
    "escuro": ("#000000", "#FFFFFF", "#B0B0B0"),
}

_WARNED_MISSING_LOGOS: set[str] = set()


@dataclass(frozen=True)
class _SplitStyle:
    bar_bg: str
    fg: str
    handle_fg: str
    text_outline: int
    text_shadow: int
    logo_size: int
    logo_x: int
    logo_y: int
    text_x_with_logo: int
    text_x_no_logo: int
    name_y: int
    handle_y: int
    handle_only_y: int
    name_fs: int
    handle_fs: int
    title_in_bar: bool
    hook_on_cam: bool


# B-14: estilo/geometria do caminho de render por filter_complex (render_clip/preview, via _SPLIT_STYLES).
# Diverge de layout.VARIANTES (name_y, etc.) de proposito: layout.py e a tabela do recorte split_layout (cam/jogo);
# unificar mudaria o visual dos clipes, por isso as duas tabelas ficam separadas (adiado).
_SPLIT_STYLES: dict[str, _SplitStyle] = {
    "split-cheio": _SplitStyle(
        bar_bg="",
        fg="#FFFFFF",
        handle_fg="#E0E0E0",
        text_outline=4,
        text_shadow=1,
        logo_size=140,
        logo_x=40,
        logo_y=30,
        text_x_with_logo=205,
        text_x_no_logo=40,
        name_y=48,
        handle_y=104,
        handle_only_y=82,
        name_fs=50,
        handle_fs=36,
        title_in_bar=False,
        hook_on_cam=True,
    ),
    "split-barra": _SplitStyle(
        bar_bg="#FFFFFF",
        fg="#101010",
        handle_fg="#707070",
        text_outline=0,
        text_shadow=0,
        logo_size=150,
        logo_x=50,
        logo_y=30,
        text_x_with_logo=235,
        text_x_no_logo=50,
        name_y=52,
        handle_y=114,
        handle_only_y=86,
        name_fs=54,
        handle_fs=38,
        title_in_bar=True,
        hook_on_cam=False,
    ),
    "split-fina": _SplitStyle(
        bar_bg="#14141C",
        fg="#FFFFFF",
        handle_fg="#E0E0E0",
        text_outline=0,
        text_shadow=0,
        logo_size=120,
        logo_x=40,
        logo_y=25,
        text_x_with_logo=190,
        text_x_no_logo=40,
        name_y=36,
        handle_y=94,
        handle_only_y=67,
        name_fs=50,
        handle_fs=36,
        title_in_bar=False,
        hook_on_cam=True,
    ),
}


def resolve_layout_mode() -> str:
    """Resolve CLIPES_LAYOUT env var; empty defaults to split-barra."""
    raw = os.environ.get("CLIPES_LAYOUT", "").strip().lower()
    if not raw:
        return DEFAULT_LAYOUT
    if raw in VALID_LAYOUTS:
        return raw
    print(f"[layout] CLIPES_LAYOUT invalido ({raw!r}); usando {DEFAULT_LAYOUT}", flush=True)
    return DEFAULT_LAYOUT


def _resolve_split_variante(split: Any, variante: str | None = None) -> str:
    bar_h = int(getattr(split, "bar_h", 0))
    cand = (variante or getattr(split, "variante", "") or "").strip().lower()
    if cand == "split-barra" and bar_h < MIN_BAR_H:
        return "split-cheio"
    if cand in _SPLIT_STYLES:
        if bar_h <= 0 and cand != "split-cheio":
            return "split-cheio"
        return cand
    if bar_h <= 0:
        return "split-cheio"
    if bar_h <= 220:
        return "split-fina"
    return "split-barra"


@dataclass(frozen=True)
class BarConfig:
    """Configuration for the fixed top brand bar."""

    h: int
    bg: str
    fg: str
    handle_fg: str
    display: str
    handle: str
    logo: Path | None


def bar_config() -> BarConfig | None:
    """Read brand bar configuration from env; returns None when disabled."""
    raw_name = os.environ.get("CLIPES_BRAND_NAME", "").strip()
    # tri-estado: CLIPES_BAR explicito manda; vazio/invalido cai em "ligada so se houver CLIPES_BRAND_NAME"
    if not config.env_flag("CLIPES_BAR", bool(raw_name)):
        return None

    handle = f"@{raw_name}" if raw_name and not raw_name.startswith("@") else raw_name
    display = os.environ.get("CLIPES_BRAND_DISPLAY", "").strip()

    raw_h = os.environ.get("CLIPES_BAR_H", "").strip()
    h = DEFAULT_BAR_H
    if raw_h:
        try:
            parsed_h = int(raw_h)
        except ValueError:
            parsed_h = -1
        if MIN_BAR_H <= parsed_h <= MAX_BAR_H:
            h = parsed_h
        else:
            print(f"[render] CLIPES_BAR_H invalido ({raw_h!r}); usando {DEFAULT_BAR_H}", flush=True)

    raw_style = os.environ.get("CLIPES_BAR_STYLE", "claro").strip().lower()
    bg, fg, handle_fg = _BAR_STYLES.get(raw_style, _BAR_STYLES["claro"])

    logo_raw = os.environ.get("CLIPES_BRAND_LOGO", "").strip() or DEFAULT_BRAND_LOGO
    logo_path = Path(logo_raw)
    try:
        has_logo = logo_path.is_file() and os.access(logo_path, os.R_OK) and logo_path.stat().st_size > 0
    except OSError:
        has_logo = False
    if has_logo:
        logo: Path | None = logo_path
    else:
        logo = None
        if logo_raw not in _WARNED_MISSING_LOGOS:
            _WARNED_MISSING_LOGOS.add(logo_raw)
            print(f"[render] logo nao encontrado: {logo_raw}; barra so com texto", flush=True)

    return BarConfig(
        h=h,
        bg=bg,
        fg=fg,
        handle_fg=handle_fg,
        display=display,
        handle=handle,
        logo=logo,
    )


def _ass_color(hex_rgb: str) -> str:
    """Convert '#RRGGBB' hex color to ASS '&H00BBGGRR&'."""
    h = hex_rgb.lstrip("#")
    if len(h) == 6:
        r, g, b = h[0:2], h[2:4], h[4:6]
        return f"&H00{b}{g}{r}&".upper()
    return "&H00FFFFFF&"

TITLE_KEYWORDS = re.compile(
    r"(?:caralho|carai|meu deus|mds|pqp|olha|mentira|nossa|que isso|"
    r"gta|rockstar|clipa|omg|wtf|no way|insane|holy|crazy|what)",
    re.IGNORECASE,
)


def generate_title(
    clip: SnappedClip,
    words: list[Word],
    max_len: int = 60,
) -> str:
    """Deterministic title heuristic from strongest speech phrase/keywords (no LLM)."""
    clip_words = [w for w in words if w.end > clip.start and w.start < clip.end]
    if not clip_words:
        sigs = " + ".join(clip.active_signals) if clip.active_signals else "pico"
        return f"Momento de pico ({sigs})"

    # Group words into sentences/clauses
    clauses: list[tuple[float, str]] = []
    buf: list[Word] = []
    for i, w in enumerate(clip_words):
        buf.append(w)
        nxt = clip_words[i + 1] if i + 1 < len(clip_words) else None
        gap = (nxt.start - w.end) if nxt else 99.0
        if w.ends_sentence or gap >= 0.8 or len(buf) >= 10:
            phrase = "".join(x.text for x in buf).strip()
            mid_t = (buf[0].start + buf[-1].end) * 0.5
            clauses.append((mid_t, phrase))
            buf = []
    if buf:
        phrase = "".join(x.text for x in buf).strip()
        mid_t = (buf[0].start + buf[-1].end) * 0.5
        clauses.append((mid_t, phrase))

    best_phrase = ""
    best_score = -1e9
    for mid_t, phrase in clauses:
        clean = " ".join(phrase.split())
        if len(clean) < 4:
            continue
        score = 0.0
        if "!" in clean or "?" in clean:
            score += 2.0
        if TITLE_KEYWORDS.search(clean):
            score += 2.5
        # Prefer phrases near the climax peak
        dist = abs(mid_t - clip.peak)
        score += max(0.0, 2.0 - dist / 10.0)
        word_count = len(clean.split())
        if 3 <= word_count <= 9:
            score += 1.0
        if score > best_score:
            best_score = score
            best_phrase = clean

    if not best_phrase:
        best_phrase = " ".join(clip.text.split())
    best_phrase = best_phrase.strip(" ,;:-")
    if len(best_phrase) > max_len:
        cut = best_phrase[: max_len - 1].rsplit(" ", 1)[0]
        best_phrase = (cut or best_phrase[: max_len - 1]).rstrip(" ,;:-") + "…"
    if best_phrase:
        best_phrase = best_phrase[0].upper() + best_phrase[1:]
    return best_phrase or f"Clipe ({clip.start:.0f}s-{clip.end:.0f}s)"


def slugify(title: str, max_len: int = 40) -> str:
    """Convert title to ASCII lowercase slug <= max_len, cut at last word boundary."""
    if not title or max_len <= 0:
        return ""
    ascii_text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if len(slug) <= max_len:
        return slug
    window = slug[: max_len + 1]
    if "-" in window:
        cut = window.rsplit("-", 1)[0].strip("-")
        if cut:
            return cut
    return slug[:max_len].strip("-")


def clip_stem(idx: int, clip: SnappedClip) -> str:
    """Build output filename stem: clip_NN[_partKofN][_slug]."""
    base = (
        f"clip_{idx:02d}_part{clip.part_index}of{clip.part_total}"
        if clip.part_total > 1
        else f"clip_{idx:02d}"
    )
    slug = slugify(clip.title)
    return f"{base}_{slug}" if slug else base


def _ass_time(seconds: float) -> str:
    t = max(0.0, seconds)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60.0
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    return " ".join(text.replace("\\", "").replace("{", "(").replace("}", ")").split())


def is_hook_top_enabled() -> bool:
    """Top hook overlay is enabled by default unless CLIPES_HOOK_TOP is '0', 'false', 'no', or 'off'."""
    return config.env_flag("CLIPES_HOOK_TOP", True)


def _truncate_ellipsis(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if max_chars == 1:
        return "…"
    return text[: max_chars - 1].rstrip(" ,;:-") + "…"


def wrap_top_hook(
    text: str,
    max_chars: int = 18,
    max_lines: int = 2,
    *,
    uppercase: bool = True,
    colored: bool = True,
) -> str:
    """ASS-escape and wrap hook into up to max_lines of <= max_chars (optionally uppercased and colored)."""
    clean = _ass_escape(" ".join(text.split()))
    if uppercase:
        clean = clean.upper()
    if not clean or max_chars <= 0 or max_lines <= 0:
        return ""
    lines: list[str] = []
    cur = ""
    truncated = False
    for word in clean.split():
        if len(word) > max_chars:
            if cur:
                lines.append(cur)
                cur = ""
                if len(lines) >= max_lines:
                    truncated = True
                    break
            lines.append(_truncate_ellipsis(word, max_chars))
            if len(lines) >= max_lines:
                break
            continue
        candidate = f"{cur} {word}" if cur else word
        if len(candidate) <= max_chars:
            cur = candidate
        else:
            lines.append(cur)
            if len(lines) >= max_lines:
                cur = ""
                truncated = True
                break
            cur = word
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if truncated and lines and not lines[-1].endswith("…"):
        base = lines[-1].rstrip(" ,;:-")
        lines[-1] = (base + "…") if len(base) < max_chars else _truncate_ellipsis(lines[-1], max_chars)
    if not colored:
        return r"\N".join(lines)
    colors = ("&H00FFFFFF&", "&H0000FFFF&")
    return r"\N".join(f"{{\\c{colors[i % 2]}}}{line}" for i, line in enumerate(lines))


def build_ass(
    words: list[Word],
    clip_start: float,
    clip_end: float,
    ass_path: Path,
    width: int = 1080,
    height: int = 1920,
    words_per_cue: int = 3,
    channel_credit: str = "",
    part_index: int = 1,
    part_total: int = 1,
    outro_duration: float = OUTRO_DURATION,
    hook_top: str = "",
    title: str = "",
    bar: BarConfig | None = None,
    split: Any | None = None,
    variante: str | None = None,
) -> int:
    """Write word-highlighted ASS subtitles, channel credit, and end card. Returns dialogue count."""
    content_dur = max(0.0, clip_end - clip_start)
    total_dur = content_dur + max(0.0, outro_duration)
    selected = [
        w for w in words
        if w.end > clip_start and w.start < clip_end and w.clean
    ]

    split_style = (
        _SPLIT_STYLES[_resolve_split_variante(split, variante)]
        if split is not None
        else None
    )

    if split is not None and split_style is not None:
        wrapped_hook = (
            wrap_top_hook(hook_top, max_chars=14, max_lines=3)
            if (split_style.hook_on_cam and hook_top.strip() and content_dur > 0 and is_hook_top_enabled())
            else ""
        )
        hook_dur = min(5.4, content_dur)
        hook_y = split.bar_h + split.cam_h // 2
        hook_fs = 64
        hook_margin_l = 60
        hook_margin_r = 60
        hook_pop = r"\fscx75\fscy75\t(0,200,\fscx106\fscy106)\t(200,300,\fscx100\fscy100)"
        part_y = split.emenda_y + 70
        part_align = 7
        part_pos = f"{{\\an7\\pos(120,{part_y})}}"
        credit_fs = 34
        credit_align = 5
        credit_pos = f"\\an5\\pos({width // 2},1860)"
        card_y = split.emenda_y + split.game_h // 2
        cap_style_line = (
            "Style: Cap,DejaVu Sans,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,"
            "-1,0,0,0,100,100,0,0,1,5,2,5,60,60,0,1\n"
        )
        cap_prefix = f"{{\\an5\\pos({width // 2},{split.emenda_y})}}"
    else:
        wrapped_hook = (
            wrap_top_hook(hook_top)
            if (bar is None and hook_top and content_dur > 0 and is_hook_top_enabled())
            else ""
        )
        hook_dur = min(5.0, content_dur)
        hook_y = 365
        hook_fs = 64
        hook_margin_l = SAFE_MARGIN_L
        hook_margin_r = SAFE_MARGIN_R
        hook_pop = r"\fscx75\fscy75\t(0,220,\fscx106\fscy106)\t(220,320,\fscx100\fscy100)"
        part_y = (bar.h + 70) if bar is not None else 490
        part_align = 8
        part_pos = f"{{\\an8\\pos({width // 2},{part_y})}}"
        credit_y = (bar.h + 20) if bar is not None else 600
        credit_fs = 38
        credit_align = 7
        credit_pos = f"\\pos(120,{credit_y})"
        card_y = (bar.h + (height - bar.h) // 2) if bar is not None else (height // 2)
        cap_style_line = (
            "Style: Cap,DejaVu Sans,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,"
            f"-1,0,0,0,100,100,0,0,1,5,2,2,{SAFE_MARGIN_L},{SAFE_MARGIN_R},{SAFE_MARGIN_V},1\n"
        )
        cap_prefix = ""

    part_style = (
        "Style: PartBadge,DejaVu Sans,52,&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,4,2,{part_align},{SAFE_MARGIN_L},{SAFE_MARGIN_R},{part_y},1\n"
        if part_total > 1
        else ""
    )
    hook_style = (
        f"Style: TopHook,DejaVu Sans,{hook_fs},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,6,3,5,{hook_margin_l},{hook_margin_r},0,1\n"
        if wrapped_hook
        else ""
    )
    bar_styles = ""
    if split is not None and split_style is not None:
        fg_ass = _ass_color(split_style.fg)
        handle_ass = _ass_color(split_style.handle_fg)
        if bar is not None:
            ol, sh = split_style.text_outline, split_style.text_shadow
            bar_styles += (
                f"Style: BarName,DejaVu Sans,{split_style.name_fs},{fg_ass},{fg_ass},&H00000000&,&H80000000&,"
                f"-1,0,0,0,100,100,0,0,1,{ol},{sh},7,60,60,0,1\n"
                f"Style: BarHandle,DejaVu Sans,{split_style.handle_fs},{handle_ass},{handle_ass},&H00000000&,&H80000000&,"
                f"0,0,0,0,100,100,0,0,1,{ol},{sh},7,60,60,0,1\n"
            )
        if split_style.title_in_bar and split.bar_h >= MIN_BAR_H:
            bar_styles += (
                f"Style: BarTitle,DejaVu Sans,58,{fg_ass},{fg_ass},&H00000000&,&H00000000&,"
                "-1,0,0,0,100,100,0,0,1,0,0,5,60,60,0,1\n"
            )
    elif bar is not None:
        fg_ass = _ass_color(bar.fg)
        handle_ass = _ass_color(bar.handle_fg)
        bar_styles = (
            f"Style: BarName,DejaVu Sans,56,{fg_ass},{fg_ass},&H00000000&,&H00000000&,"
            "-1,0,0,0,100,100,0,0,1,0,0,7,60,60,0,1\n"
            f"Style: BarHandle,DejaVu Sans,44,{handle_ass},{handle_ass},&H00000000&,&H00000000&,"
            "0,0,0,0,100,100,0,0,1,0,0,7,60,60,0,1\n"
            f"Style: BarTitle,DejaVu Sans,60,{fg_ass},{fg_ass},&H00000000&,&H00000000&,"
            "-1,0,0,0,100,100,0,0,1,0,0,5,60,60,0,1\n"
        )
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"{cap_style_line}"
        f"Style: Credit,DejaVu Sans,{credit_fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,3,1,{credit_align},{SAFE_MARGIN_L},{SAFE_MARGIN_R},0,1\n"
        f"{part_style}"
        f"{hook_style}"
        f"{bar_styles}"
        "Style: Card,DejaVu Sans,54,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,3,14,0,5,{SAFE_MARGIN_L},{SAFE_MARGIN_R},0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events: list[str] = []
    if total_dur > 0:
        bar_end = _ass_time(total_dur)
        if split is not None and split_style is not None:
            if bar is not None:
                text_x = split_style.text_x_with_logo if bar.logo is not None else split_style.text_x_no_logo
                safe_display = _ass_escape(bar.display.strip())
                safe_handle = _ass_escape(bar.handle.strip())
                if safe_display:
                    events.append(
                        f"Dialogue: 3,0:00:00.00,{bar_end},BarName,,0,0,0,,"
                        f"{{\\an7\\pos({text_x},{split_style.name_y})}}{safe_display}"
                    )
                if safe_handle:
                    handle_y = split_style.handle_y if safe_display else split_style.handle_only_y
                    events.append(
                        f"Dialogue: 3,0:00:00.00,{bar_end},BarHandle,,0,0,0,,"
                        f"{{\\an7\\pos({text_x},{handle_y})}}{safe_handle}"
                    )
            if split_style.title_in_bar and split.bar_h >= MIN_BAR_H:
                bar_title_src = hook_top.strip() or title.strip()
                wrapped_title = (
                    wrap_top_hook(bar_title_src, max_chars=26, max_lines=2, uppercase=False, colored=False)
                    if bar_title_src
                    else ""
                )
                if wrapped_title:
                    title_y = (195 + max(220, split.bar_h - 15)) // 2
                    events.append(
                        f"Dialogue: 3,0:00:00.00,{bar_end},BarTitle,,0,0,0,,"
                        f"{{\\an5\\pos({width // 2},{title_y})}}{wrapped_title}"
                    )
        elif bar is not None:
            text_x = 240 if bar.logo is not None else 60
            safe_display = _ass_escape(bar.display.strip())
            safe_handle = _ass_escape(bar.handle.strip())
            if safe_display:
                events.append(
                    f"Dialogue: 3,0:00:00.00,{bar_end},BarName,,0,0,0,,"
                    f"{{\\an7\\pos({text_x},56)}}{safe_display}"
                )
            if safe_handle:
                handle_y = 122 if safe_display else 93
                events.append(
                    f"Dialogue: 3,0:00:00.00,{bar_end},BarHandle,,0,0,0,,"
                    f"{{\\an7\\pos({text_x},{handle_y})}}{safe_handle}"
                )
            bar_title_src = hook_top.strip() or title.strip()
            wrapped_title = (
                wrap_top_hook(bar_title_src, max_chars=26, max_lines=2, uppercase=False, colored=False)
                if bar_title_src
                else ""
            )
            if wrapped_title:
                title_y = (210 + (bar.h - 20)) // 2
                events.append(
                    f"Dialogue: 3,0:00:00.00,{bar_end},BarTitle,,0,0,0,,"
                    f"{{\\an5\\pos({width // 2},{title_y})}}{wrapped_title}"
                )
    if wrapped_hook:
        hook_end = _ass_time(hook_dur)
        events.append(
            f"Dialogue: 3,0:00:00.00,{hook_end},TopHook,,0,0,0,,"
            f"{{\\an5\\pos({width // 2},{hook_y})\\fad(120,350){hook_pop}}}"
            f"{wrapped_hook}"
        )
    if part_total > 1:
        badge_end = _ass_time(max(0.1, content_dur))
        events.append(
            f"Dialogue: 1,0:00:00.00,{badge_end},PartBadge,,0,0,0,,"
            f"{part_pos}Parte {part_index}/{part_total}"
        )

    safe_credit = _ass_escape(channel_credit.strip())
    if safe_credit and content_dur > 0:
        strong_end = min(4.0, content_dur)
        events.append(
            f"Dialogue: 1,0:00:00.00,{_ass_time(strong_end)},Credit,,0,0,0,,"
            f"{{{credit_pos}\\fad(300,400)}}{safe_credit}"
        )
        if content_dur > 4.0:
            events.append(
                f"Dialogue: 1,{_ass_time(4.0)},{_ass_time(content_dur)},Credit,,0,0,0,,"
                f"{{{credit_pos}\\alpha&H99&}}{safe_credit}"
            )

    if outro_duration > 0:
        if part_index < part_total:
            card_text = f"Parte {part_index + 1} →"
        elif safe_credit:
            card_text = f"Live completa: {safe_credit}"
        else:
            card_text = "Live completa"
        events.append(
            f"Dialogue: 2,{_ass_time(content_dur)},{_ass_time(content_dur + outro_duration)},Card,,0,0,0,,"
            f"{{\\pos({width // 2},{card_y})\\fad(250,0)}}{card_text}"
        )

    if not selected:
        ass_path.write_text(header + ("\n".join(events) + "\n" if events else ""), encoding="utf-8")
        return len(events)

    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in selected:
        cur.append(w)
        if len(cur) >= words_per_cue or w.ends_sentence:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    # ASS colors are &HBBGGRR&
    white = "&H00FFFFFF&"
    highlight = "&H0000FFFF&"  # bright yellow

    for group in groups:
        for i, w in enumerate(group):
            s0 = max(0.0, max(w.start, clip_start) - clip_start)
            next_s = group[i + 1].start if i + 1 < len(group) else w.end
            en = min(clip_end - clip_start, max(w.end, next_s) - clip_start)
            if en <= s0:
                en = min(clip_end - clip_start, s0 + 0.12)
            if en <= s0:
                continue

            parts: list[str] = []
            for j, item in enumerate(group):
                tok = _ass_escape(item.clean)
                col = highlight if j == i else white
                parts.append(f"{{\\c{col}}}{tok}")
            events.append(
                f"Dialogue: 0,{_ass_time(s0)},{_ass_time(en)},Cap,,0,0,0,,{cap_prefix}{' '.join(parts)}"
            )

    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return len(events)


def download_clip_window(
    url: str,
    start: float,
    end: float,
    tmp_dir: Path,
    idx: int,
) -> Path:
    """Download only the [start, end] clip window up to 1080p using yt-dlp --download-sections."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for existing in tmp_dir.glob(f"seg_{idx:02d}.*"):
        existing.unlink(missing_ok=True)

    tmpl = str(tmp_dir / f"seg_{idx:02d}.%(ext)s")
    section = f"*{start:.3f}-{end:.3f}"

    attempts = [
        [
            "yt-dlp", "--no-playlist", "--socket-timeout", "30",
            "--retries", "6", "--fragment-retries", "6",
            "--download-sections", section, "--force-keyframes-at-cuts",
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/best",
            "--merge-output-format", "mp4",
            "-o", tmpl,
            *js_runtime_args(),
            url,
        ],
        [
            "yt-dlp", "--no-playlist", "--socket-timeout", "30",
            "--retries", "6", "--fragment-retries", "6",
            "--download-sections", section, "--force-keyframes-at-cuts",
            "-f", "bv*[height<=720]+ba/b[height<=720]/best",
            "--merge-output-format", "mp4",
            "-o", tmpl,
            *js_runtime_args(),
            url,
        ],
        [
            "yt-dlp", "--no-playlist", "--socket-timeout", "30",
            "--download-sections", section, "--force-keyframes-at-cuts",
            "-f", "b[height<=1080]/best",
            "-o", tmpl,
            *js_runtime_args(),
            url,
        ],
    ]

    last_err = ""
    for i, cmd in enumerate(attempts):
        if i > 0:
            time.sleep(2.0 * i)
            for leftover in tmp_dir.glob(f"seg_{idx:02d}.*"):
                leftover.unlink(missing_ok=True)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        except subprocess.TimeoutExpired as exc:
            # Limpa sobras da tentativa que deu timeout antes de seguir para a proxima
            for leftover in tmp_dir.glob(f"seg_{idx:02d}.*"):
                leftover.unlink(missing_ok=True)
            stderr_raw = exc.stderr or exc.output or ""
            if isinstance(stderr_raw, bytes):
                stderr_txt = stderr_raw.decode("utf-8", errors="replace").strip()
            else:
                stderr_txt = str(stderr_raw).strip()
            last_err = f"timeout ({exc.timeout}s): {stderr_txt}" if stderr_txt else f"timeout ({exc.timeout}s)"
            continue
        files = [p for p in tmp_dir.glob(f"seg_{idx:02d}.*") if not p.name.endswith(".part")]
        if res.returncode == 0 and files:
            return files[0]
        last_err = (res.stderr or res.stdout or "").strip()

    raise RuntimeError(f"yt-dlp --download-sections failed for {section}: {last_err[-500:]}")


def _escape_ffmpeg_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def is_blur_bg_enabled() -> bool:
    """Blurred background only when CLIPES_BLUR_BG is explicitly enabled ('1', 'true', 'yes', 'on'). Default: black bars."""
    return config.env_flag("CLIPES_BLUR_BG", False)


def _circular_logo_filter(size: int = 150) -> str:
    r = size // 2
    return (
        f"[1:v]crop='min(iw,ih)':'min(iw,ih)',scale={size}:{size},setsar=1,format=rgba,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lte(hypot(X-{r},Y-{r}),{r}),255,0)'[logo]"
    )


def _build_split_video_parts(
    split: Any,
    bar: BarConfig | None,
    variante: str,
    has_hook: bool,
) -> tuple[list[str], str]:
    style = _SPLIT_STYLES[variante]
    cam = split.cam
    game = split.game
    cam_darken = (
        f",drawbox=x=0:y=0:w=1080:h={split.cam_h}:color=black@0.5:t=fill:enable='lt(t,5.4)'"
        if (style.hook_on_cam and has_hook)
        else ""
    )
    bar_bg = style.bar_bg or "black"
    pad_bar = f",pad=1080:1920:0:{split.bar_h}:{bar_bg}" if split.bar_h > 0 else ""
    vf_parts = [
        "[0:v]split=2[c0][g0]",
        f"[c0]crop={cam.w}:{cam.h}:{cam.x}:{cam.y},scale=1080:{split.cam_h},setsar=1{cam_darken}[cam]",
        f"[g0]crop={game.w}:{game.h}:{game.x}:{game.y},scale=1080:{split.game_h},setsar=1[game]",
        f"[cam][game]vstack=inputs=2{pad_bar}[v_base]",
    ]
    cur = "[v_base]"
    if bar is not None and bar.logo is not None:
        vf_parts.append(_circular_logo_filter(style.logo_size))
        vf_parts.append(f"{cur}[logo]overlay={style.logo_x}:{style.logo_y}[v_logo]")
        cur = "[v_logo]"
    return vf_parts, cur


def build_video_filter(
    ass_path: Path | None,
    n_subs: int,
    outro_duration: float = OUTRO_DURATION,
    blur_bg: bool | None = None,
    crop: tuple[int, int, int, int] | None = None,
    bar: BarConfig | None = None,
    downscale_720: bool = False,
    split: Any | None = None,
    variante: str | None = None,
    has_hook: bool = False,
) -> tuple[str, str]:
    """Build ffmpeg -filter_complex string and mapped video pad label."""
    if blur_bg is None:
        blur_bg = is_blur_bg_enabled()

    if split is not None:
        resolved_var = _resolve_split_variante(split, variante)
        vf_parts, cur = _build_split_video_parts(split, bar, resolved_var, has_hook)
    elif bar is None:
        if crop is not None:
            cx, cy, cw, ch = crop
            vf_parts = [f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale=1080:1920,setsar=1[v_base]"]
        elif blur_bg:
            # Fast 1080x1920 vertical layout: centered gameplay over downscale-blurred copy of itself.
            vf_parts = [
                "[0:v]split=2[bg][fg]",
                "[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,scale=1080:1920,setsar=1[bg_b]",
                "[fg]scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fg_s]",
                "[bg_b][fg_s]overlay=(W-w)/2:(H-h)/2[v_base]",
            ]
        else:
            vf_parts = [
                "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1[v_base]",
            ]
        cur = "[v_base]"
    else:
        content_h = 1920 - bar.h
        drawbox = f"drawbox=x=0:y=0:w=1080:h={bar.h}:color={bar.bg}:t=fill"
        if crop is not None:
            cx, cy, cw, ch = crop
            vf_parts = [
                f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale=1080:{content_h},"
                f"pad=1080:1920:0:{bar.h}:black,setsar=1,{drawbox}[v_base]",
            ]
        elif blur_bg:
            blur_h = max(2, content_h // 4)
            vf_parts = [
                "[0:v]split=2[bg][fg]",
                f"[bg]scale=270:{blur_h}:force_original_aspect_ratio=increase,crop=270:{blur_h},"
                f"boxblur=10:2,scale=1080:{content_h},pad=1080:1920:0:{bar.h}:black,setsar=1[bg_b]",
                f"[fg]scale=1080:{content_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1[fg_s]",
                f"[bg_b][fg_s]overlay=(W-w)/2:{bar.h},{drawbox}[v_base]",
            ]
        else:
            vf_parts = [
                f"[0:v]scale=1080:{content_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad=1080:1920:(ow-iw)/2:{bar.h}:black,setsar=1,{drawbox}[v_base]",
            ]
        cur = "[v_base]"
        if bar.logo is not None:
            vf_parts.append(_circular_logo_filter(150))
            vf_parts.append(f"{cur}[logo]overlay=60:40[v_logo]")
            cur = "[v_logo]"

    if outro_duration > 0:
        vf_parts.append(f"{cur}tpad=stop_mode=clone:stop_duration={outro_duration:g}[v_pad]")
        cur = "[v_pad]"

    if n_subs > 0 and ass_path is not None:
        escaped_ass = _escape_ffmpeg_filter_path(ass_path)
        vf_parts.append(f"{cur}subtitles='{escaped_ass}'[v_out]")
        cur = "[v_out]"

    if downscale_720:
        vf_parts.append(f"{cur}scale=720:1280,setsar=1[v_720]")
        cur = "[v_720]"

    return ";".join(vf_parts), cur


def build_audio_filter(
    content_duration: float,
    outro_duration: float = OUTRO_DURATION,
) -> str:
    """Build ffmpeg -af chain: loudnorm -> afade -> apad (order preserves loudness calibration)."""
    fade_start = max(0.0, content_duration - 0.5)
    return (
        f"loudnorm=I=-14:TP=-1.5:LRA=11,"
        f"afade=t=out:st={fade_start:.3f}:d=0.5,"
        f"apad=pad_dur={outro_duration:g}"
    )


def _master_mode() -> str:
    raw = os.environ.get("CLIPES_MASTER_MODE", "").strip().lower()
    return "livre" if raw == "livre" else "discord"


def _build_render_cmd(
    *,
    pre_args: list[str],
    src_path: Path,
    logo_args: list[str],
    filter_complex: str,
    v_map: str,
    af_chain: str,
    total_dur: float,
    tmp_out: Path,
    maxrate_kbps: int | None = None,
) -> list[str]:
    rate_args = (
        ["-maxrate", f"{maxrate_kbps}k", "-bufsize", f"{maxrate_kbps * 2}k"]
        if maxrate_kbps is not None
        else []
    )
    return [
        "ffmpeg", "-y", "-v", "error",
        *pre_args,
        "-i", str(src_path),
        *logo_args,
        "-filter_complex", filter_complex,
        "-map", v_map,
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        *rate_args,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-af", af_chain,
        "-t", f"{total_dur:.3f}",
        "-movflags", "+faststart",
        str(tmp_out),
    ]


def _normalize_rect_list(rect: Any) -> list[float] | None:
    if rect is None:
        return None
    if isinstance(rect, (tuple, list)) and len(rect) == 4:
        return [round(float(v), 4) for v in rect]
    if all(hasattr(rect, attr) for attr in ("x", "y", "w", "h")):
        return [round(float(getattr(rect, attr)), 4) for attr in ("x", "y", "w", "h")]
    return None


def _sanitize_layout_key(key: str) -> str:
    cleaned = re.sub(r"[^\w.-]", "_", (key or "").strip())
    cleaned = re.sub(r"\.{2,}", "_", cleaned).strip(".-")
    if not cleaned or not cleaned.strip("_"):
        return "default"
    return cleaned


def _resolve_layout_key(layout_key: str, channel_credit: str, out_dir: Path) -> str:
    if layout_key.strip():
        raw = layout_key.strip()
    else:
        cred = channel_credit.strip()
        if cred:
            raw = cred.removeprefix("twitch.tv/").lstrip("@").strip("/")
        else:
            raw = out_dir.name or "default"
    return _sanitize_layout_key(raw)


def _validate_split_layout(split: Any) -> None:
    """Validate split Layout attributes and 1920px even-height budget."""
    for attr in ("cam", "game", "bar_h", "cam_h", "game_h", "emenda_y"):
        if not hasattr(split, attr):
            raise AttributeError(f"split layout sem atributo {attr!r}")
    cam = split.cam
    game = split.game
    if cam is None or game is None:
        raise ValueError("split.cam ou split.game nulo")
    for rect_name, rect in (("cam", cam), ("game", game)):
        for attr in ("x", "y", "w", "h"):
            if not hasattr(rect, attr):
                raise AttributeError(f"split.{rect_name} sem atributo {attr!r}")
        if int(rect.w) <= 0 or int(rect.h) <= 0:
            raise ValueError(f"split.{rect_name} com dimensoes invalidas: {rect.w}x{rect.h}")
    bar_h, cam_h, game_h, emenda_y = split.bar_h, split.cam_h, split.game_h, split.emenda_y
    for name, val in (("bar_h", bar_h), ("cam_h", cam_h), ("game_h", game_h), ("emenda_y", emenda_y)):
        if isinstance(val, bool) or not isinstance(val, int):
            raise ValueError(f"split.{name} deve ser int, recebido {val!r}")
    if bar_h < 0 or cam_h <= 0 or game_h <= 0:
        raise ValueError(f"alturas invalidas: bar={bar_h} cam={cam_h} game={game_h}")
    if bar_h % 2 != 0 or cam_h % 2 != 0 or game_h % 2 != 0:
        raise ValueError(f"alturas devem ser pares: bar={bar_h} cam={cam_h} game={game_h}")
    if bar_h + cam_h + game_h != 1920:
        raise ValueError(f"soma das alturas != 1920: {bar_h}+{cam_h}+{game_h}={bar_h + cam_h + game_h}")
    if emenda_y != bar_h + cam_h:
        raise ValueError(f"emenda_y ({emenda_y}) != bar_h+cam_h ({bar_h + cam_h})")


def _resolve_split_for_clip(
    *,
    layout_mode: str,
    layout_key: str,
    src_path: Path,
    probe_start: float,
    duration: float,
    out_root: Path,
) -> tuple[Any | None, str | None, dict[str, Any] | None, str | None]:
    """Resolve split Layout via facecam.obter + layout.split_layout; returns (split, var, fc_info, tipo)."""
    if layout_mode == "full":
        return None, None, None, None
    try:
        from .reframe import _probe_video_size
        size = _probe_video_size(src_path)
    except Exception as exc:
        print(f"[facecam] deteccao falhou: {exc}", flush=True)
        return None, None, None, None
    if size is None:
        return None, None, None, None
    src_w, src_h = size
    if src_w <= 0 or src_h <= 0 or src_h > src_w:
        return None, None, None, None
    try:
        from . import facecam as facecam_mod
        from . import layout as layout_mod

        fc = facecam_mod.obter(layout_key, src_path, probe_start, duration, out_root)
        if fc is None:
            return None, None, None, "nenhuma"
        tipo = str(getattr(fc, "tipo", "nenhuma") or "nenhuma")
        metodo = str(getattr(fc, "metodo", "") or "")
        rect_raw = getattr(fc, "rect", None)
        fc_info: dict[str, Any] | None = (
            {"rect": _normalize_rect_list(rect_raw), "metodo": metodo}
            if (metodo or rect_raw is not None)
            else None
        )
        if tipo == "overlay" and rect_raw is not None:
            split_obj = layout_mod.split_layout(src_w, src_h, rect_raw, layout_mode)
            if split_obj is not None and getattr(split_obj, "cam", None) is not None:
                return split_obj, layout_mode, fc_info, tipo
            return None, None, fc_info, "nenhuma"
        return None, None, fc_info, tipo
    except Exception as exc:
        print(f"[facecam] deteccao falhou: {exc}", flush=True)
        return None, None, None, None


def render_clip(
    clip: SnappedClip,
    idx: int,
    words: list[Word],
    url: str,
    out_dir: Path,
    cache_dir: Path,
    live_video_path: Path | None = None,
    channel_credit: str = "",
    reframe: bool = False,
    layout_key: str = "",
) -> Path:
    """Download window (or slice live recording) and render 1080x1920 (or 720x1280) MP4 with ASS captions."""
    bar = bar_config()
    stem = clip_stem(idx, clip)
    out_file = out_dir / f"{stem}.mp4"
    ass_path = cache_dir / f"{stem}.ass"

    temp_seg: Path | None = None
    if live_video_path is not None:
        src_path = live_video_path
        pre_args = ["-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}"]
        probe_start = clip.start
    else:
        temp_seg = download_clip_window(url, clip.start, clip.end, cache_dir / "segments", idx)
        src_path = temp_seg
        pre_args = ["-t", f"{clip.duration:.3f}"]
        probe_start = 0.0

    tmp_out = out_dir / f".tmp_clip_{idx:02d}.mp4"
    tmp_retry = out_dir / f".tmp_clip_{idx:02d}.retry.mp4"

    try:
        layout_mode = resolve_layout_mode()
        resolved_key = _resolve_layout_key(layout_key, channel_credit, out_dir)
        out_root = out_dir.parent if out_dir.parent != out_dir else out_dir
        split_obj, split_variante, fc_info, fc_tipo = _resolve_split_for_clip(
            layout_mode=layout_mode,
            layout_key=resolved_key,
            src_path=src_path,
            probe_start=probe_start,
            duration=clip.duration,
            out_root=out_root,
        )

        clip.facecam = fc_info  # type: ignore[attr-defined]
        clip.layout_fallback = False  # type: ignore[attr-defined]

        crop: tuple[int, int, int, int] | None = None
        if split_obj is None and reframe and fc_tipo != "nenhuma":
            from .reframe import detect_speaker_crop
            if bar is not None:
                crop = detect_speaker_crop(src_path, start=probe_start, duration=clip.duration, out_h=1920 - bar.h)
            else:
                crop = detect_speaker_crop(src_path, start=probe_start, duration=clip.duration)

        blur_bg = is_blur_bg_enabled()
        full_layout_name = "reframed_face" if crop is not None else ("blurred_16x9" if blur_bg else "black_bars_16x9")
        clip.layout = "split_webcam" if split_obj is not None else full_layout_name  # type: ignore[attr-defined]

        has_hook = bool(clip.hook_top.strip() and clip.duration > 0 and is_hook_top_enabled())
        af_chain = build_audio_filter(clip.duration, outro_duration=OUTRO_DURATION)
        total_dur = clip.duration + OUTRO_DURATION
        logo_args = ["-i", str(bar.logo)] if (bar is not None and bar.logo is not None) else []

        mode = _master_mode()
        rate_kbps: int | None = None
        accept_bytes: int | None = None
        if mode == "livre":
            clip.cabe_discord = False
        else:
            from .previa import _resolve_limit_mb, bitrate_alvo

            limit_mb = _resolve_limit_mb(None)
            accept_bytes = int(limit_mb * 1024 * 1024 * 0.98)
            v_kbps = bitrate_alvo(total_dur, limit_mb, teto=6000, audio_kbps=160)
            if v_kbps < 400:
                clip.cabe_discord = False
            else:
                rate_kbps = v_kbps
                clip.cabe_discord = True

        def _build_ass_for(active_split: Any | None, active_var: str | None) -> int:
            return build_ass(
                words,
                clip.start,
                clip.end,
                ass_path,
                channel_credit=channel_credit,
                part_index=clip.part_index,
                part_total=clip.part_total,
                outro_duration=OUTRO_DURATION,
                hook_top=clip.hook_top,
                title=clip.title,
                bar=bar,
                split=active_split,
                variante=active_var,
            )

        def _run_encode(
            maxrate: int | None,
            target: Path,
            active_split: Any | None,
            active_var: str | None,
            active_hook: bool,
            n_subs: int,
        ) -> None:
            downscale = maxrate is not None and maxrate < 1500
            filter_complex, v_map = build_video_filter(
                ass_path=ass_path,
                n_subs=n_subs,
                outro_duration=OUTRO_DURATION,
                blur_bg=blur_bg,
                crop=crop,
                bar=bar,
                downscale_720=downscale,
                split=active_split,
                variante=active_var,
                has_hook=active_hook,
            )
            cmd = _build_render_cmd(
                pre_args=pre_args,
                src_path=src_path,
                logo_args=logo_args,
                filter_complex=filter_complex,
                v_map=v_map,
                af_chain=af_chain,
                total_dur=total_dur,
                tmp_out=target,
                maxrate_kbps=maxrate,
            )
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
            if res.returncode != 0:
                raise RuntimeError(f"ffmpeg render failed: {(res.stderr or res.stdout)[-500:]}")

        def _encode_with_master_retry(
            active_split: Any | None,
            active_var: str | None,
            active_hook: bool,
            n_subs: int,
        ) -> None:
            _run_encode(rate_kbps, tmp_out, active_split, active_var, active_hook, n_subs)
            if rate_kbps is not None and accept_bytes is not None and tmp_out.is_file():
                if tmp_out.stat().st_size > accept_bytes:
                    retry_kbps = max(1, round(rate_kbps * 0.85))
                    try:
                        _run_encode(retry_kbps, tmp_retry, active_split, active_var, active_hook, n_subs)
                    except Exception as exc:
                        print(f"[render] re-encode falhou; mantendo master: {exc}", flush=True)
                        clip.cabe_discord = False
                    else:
                        if tmp_retry.is_file():
                            if tmp_retry.stat().st_size > accept_bytes:
                                clip.cabe_discord = False
                            tmp_retry.replace(tmp_out)

        if split_obj is not None:
            try:
                _validate_split_layout(split_obj)
                n_subs = _build_ass_for(split_obj, split_variante)
                _encode_with_master_retry(split_obj, split_variante, has_hook, n_subs)
            except Exception as exc:
                print(f"[layout] ffmpeg split falhou; caindo para full: {exc}", flush=True)
                clip.layout_fallback = True  # type: ignore[attr-defined]
                tmp_out.unlink(missing_ok=True)
                tmp_retry.unlink(missing_ok=True)
                clip.layout = full_layout_name  # type: ignore[attr-defined]
                n_subs = _build_ass_for(None, None)
                _encode_with_master_retry(None, None, False, n_subs)
        else:
            n_subs = _build_ass_for(None, None)
            _encode_with_master_retry(None, None, False, n_subs)

        tmp_out.replace(out_file)
    finally:
        tmp_out.unlink(missing_ok=True)
        tmp_retry.unlink(missing_ok=True)
        if temp_seg is not None:
            temp_seg.unlink(missing_ok=True)

    return out_file
