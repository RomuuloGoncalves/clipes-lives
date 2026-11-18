"""Render 1080x1920 vertical clips with blurred background and burned-in ASS captions."""

from __future__ import annotations

import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path

from .ingest import js_runtime_args
from .models import SnappedClip, Word

SAFE_MARGIN_L = 120
SAFE_MARGIN_R = 200
SAFE_MARGIN_V = 560
OUTRO_DURATION = 1.2

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
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


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
) -> int:
    """Write word-highlighted ASS subtitles, channel credit, and end card. Returns dialogue count."""
    content_dur = max(0.0, clip_end - clip_start)
    selected = [
        w for w in words
        if w.end > clip_start and w.start < clip_end and w.clean
    ]
    part_style = (
        "Style: PartBadge,DejaVu Sans,52,&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,4,2,8,{SAFE_MARGIN_L},{SAFE_MARGIN_R},490,1\n"
        if part_total > 1
        else ""
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
        "Style: Cap,DejaVu Sans,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,5,2,2,{SAFE_MARGIN_L},{SAFE_MARGIN_R},{SAFE_MARGIN_V},1\n"
        "Style: Credit,DejaVu Sans,38,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,3,1,7,{SAFE_MARGIN_L},{SAFE_MARGIN_R},0,1\n"
        f"{part_style}"
        "Style: Card,DejaVu Sans,54,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,3,14,0,5,{SAFE_MARGIN_L},{SAFE_MARGIN_R},0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events: list[str] = []
    if part_total > 1:
        badge_end = _ass_time(max(0.1, content_dur))
        events.append(
            f"Dialogue: 1,0:00:00.00,{badge_end},PartBadge,,0,0,0,,"
            f"{{\\an8\\pos({width // 2},490)}}Parte {part_index}/{part_total}"
        )

    safe_credit = _ass_escape(channel_credit.strip())
    if safe_credit and content_dur > 0:
        strong_end = min(4.0, content_dur)
        events.append(
            f"Dialogue: 1,0:00:00.00,{_ass_time(strong_end)},Credit,,0,0,0,,"
            f"{{\\pos(120,600)\\fad(300,400)}}{safe_credit}"
        )
        if content_dur > 4.0:
            events.append(
                f"Dialogue: 1,{_ass_time(4.0)},{_ass_time(content_dur)},Credit,,0,0,0,,"
                f"{{\\pos(120,600)\\alpha&H99&}}{safe_credit}"
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
            f"{{\\pos({width // 2},{height // 2})\\fad(250,0)}}{card_text}"
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
                f"Dialogue: 0,{_ass_time(s0)},{_ass_time(en)},Cap,,0,0,0,,{' '.join(parts)}"
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
            "--download-sections", section,
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
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        files = [p for p in tmp_dir.glob(f"seg_{idx:02d}.*") if not p.name.endswith(".part")]
        if res.returncode == 0 and files:
            return files[0]
        last_err = (res.stderr or res.stdout or "").strip()

    raise RuntimeError(f"yt-dlp --download-sections failed for {section}: {last_err[-500:]}")


def _escape_ffmpeg_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def is_blur_bg_enabled() -> bool:
    """Blurred background only when CLIPES_BLUR_BG is explicitly enabled ('1', 'true', 'yes', 'on'). Default: black bars."""
    val = os.environ.get("CLIPES_BLUR_BG", "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def build_video_filter(
    ass_path: Path | None,
    n_subs: int,
    outro_duration: float = OUTRO_DURATION,
    blur_bg: bool | None = None,
    crop: tuple[int, int, int, int] | None = None,
) -> tuple[str, str]:
    """Build ffmpeg -filter_complex string and mapped video pad label."""
    if blur_bg is None:
        blur_bg = is_blur_bg_enabled()

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
    if outro_duration > 0:
        vf_parts.append(f"{cur}tpad=stop_mode=clone:stop_duration={outro_duration:g}[v_pad]")
        cur = "[v_pad]"

    if n_subs > 0 and ass_path is not None:
        escaped_ass = _escape_ffmpeg_filter_path(ass_path)
        vf_parts.append(f"{cur}subtitles='{escaped_ass}'[v_out]")
        cur = "[v_out]"

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
) -> Path:
    """Download window (or slice live recording) and render 1080x1920 MP4 with ASS captions."""
    stem = clip_stem(idx, clip)
    out_file = out_dir / f"{stem}.mp4"
    ass_path = cache_dir / f"{stem}.ass"
    n_subs = build_ass(
        words,
        clip.start,
        clip.end,
        ass_path,
        channel_credit=channel_credit,
        part_index=clip.part_index,
        part_total=clip.part_total,
        outro_duration=OUTRO_DURATION,
    )

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

    crop: tuple[int, int, int, int] | None = None
    if reframe:
        from .reframe import detect_speaker_crop
        crop = detect_speaker_crop(src_path, start=probe_start, duration=clip.duration)

    blur_bg = is_blur_bg_enabled()
    clip.layout = (  # type: ignore[attr-defined]
        "reframed_face" if crop is not None else ("blurred_16x9" if blur_bg else "black_bars_16x9")
    )
    filter_complex, v_map = build_video_filter(
        ass_path=ass_path,
        n_subs=n_subs,
        outro_duration=OUTRO_DURATION,
        blur_bg=blur_bg,
        crop=crop,
    )
    af_chain = build_audio_filter(clip.duration, outro_duration=OUTRO_DURATION)
    total_dur = clip.duration + OUTRO_DURATION

    tmp_out = out_dir / f".tmp_clip_{idx:02d}.mp4"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *pre_args,
        "-i", str(src_path),
        "-filter_complex", filter_complex,
        "-map", v_map,
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-af", af_chain,
        "-t", f"{total_dur:.3f}",
        "-movflags", "+faststart",
        str(tmp_out),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg render failed: {(res.stderr or res.stdout)[-500:]}")
        tmp_out.replace(out_file)
    finally:
        tmp_out.unlink(missing_ok=True)
        if temp_seg is not None:
            temp_seg.unlink(missing_ok=True)

    return out_file
