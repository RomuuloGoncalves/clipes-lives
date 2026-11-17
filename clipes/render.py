"""Render 1080x1920 vertical clips with blurred background and burned-in ASS captions."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .ingest import js_runtime_args
from .models import SnappedClip, Word

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
) -> int:
    """Write word-highlighted ASS subtitles for [clip_start, clip_end]. Returns dialogue count."""
    selected = [
        w for w in words
        if w.end > clip_start and w.start < clip_end and w.clean
    ]
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
        "-1,0,0,0,100,100,0,0,1,5,2,2,70,70,430,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    if not selected:
        ass_path.write_text(header, encoding="utf-8")
        return 0

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

    events: list[str] = []
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
            "--retries", "4", "--fragment-retries", "4",
            "--download-sections", section, "--force-keyframes-at-cuts",
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/best",
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
    for cmd in attempts:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        files = [p for p in tmp_dir.glob(f"seg_{idx:02d}.*") if not p.name.endswith(".part")]
        if res.returncode == 0 and files:
            return files[0]
        last_err = (res.stderr or res.stdout or "").strip()

    raise RuntimeError(f"yt-dlp --download-sections failed for {section}: {last_err[-500:]}")


def _escape_ffmpeg_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_clip(
    clip: SnappedClip,
    idx: int,
    words: list[Word],
    url: str,
    out_dir: Path,
    cache_dir: Path,
    live_video_path: Path | None = None,
) -> Path:
    """Download window (or slice live recording) and render 1080x1920 MP4 with ASS captions."""
    out_file = out_dir / f"clip_{idx:02d}.mp4"
    ass_path = cache_dir / f"clip_{idx:02d}.ass"
    n_subs = build_ass(words, clip.start, clip.end, ass_path)

    temp_seg: Path | None = None
    if live_video_path is not None:
        src_path = live_video_path
        pre_args = ["-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}"]
    else:
        temp_seg = download_clip_window(url, clip.start, clip.end, cache_dir / "segments", idx)
        src_path = temp_seg
        pre_args = ["-t", f"{clip.duration:.3f}"]

    # Fast 1080x1920 vertical layout: centered gameplay over downscale-blurred copy of itself.
    vf_parts = [
        "[0:v]split=2[bg][fg]",
        "[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,scale=1080:1920,setsar=1[bg_b]",
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fg_s]",
        "[bg_b][fg_s]overlay=(W-w)/2:(H-h)/2[v_base]",
    ]
    if n_subs > 0:
        escaped_ass = _escape_ffmpeg_filter_path(ass_path)
        vf_parts.append(f"[v_base]subtitles='{escaped_ass}'[v_out]")
        v_map = "[v_out]"
    else:
        v_map = "[v_base]"

    tmp_out = out_dir / f".tmp_clip_{idx:02d}.mp4"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *pre_args,
        "-i", str(src_path),
        "-filter_complex", ";".join(vf_parts),
        "-map", v_map,
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
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
