"""Media and chat ingestion via yt-dlp and ffmpeg (VOD and live recording)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .models import IngestResult

BOT_AUTHORS = frozenset({
    "nightbot", "streamelements", "streamlabs", "moobot", "fossabot",
    "botrix", "wizebot", "sery_bot", "soundalerts", "botrixoficial",
})

TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
TWITCH_COMMENTS_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


def js_runtime_args() -> list[str]:
    """Return yt-dlp --js-runtimes flag if a supported runtime is installed."""
    for rt in ("deno", "node", "bun"):
        if shutil.which(rt):
            return ["--js-runtimes", rt]
    return []


def sanitize_id(raw_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw_id or "video")
    return cleaned.strip("_") or "video"


def out_dir_for(out_root: Path, video_id: str, title: str, upload_date: str | None = None) -> Path:
    """Pasta de saida legivel: 'AAAA-MM-DD - Titulo [id]'. Reaproveita pasta existente do mesmo id
    (nome novo ou legado so com o id) para nao perder cache."""
    legacy = out_root / video_id
    if legacy.is_dir():
        return legacy
    found = next(iter(sorted(out_root.glob(f"*[[]{video_id}[]]"))), None) if out_root.is_dir() else None
    if found is not None:
        return found
    slug = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|#&%$@!^~`]+', " ", title or "")).strip(" .-")[:70].rstrip(" .-")
    date = ""
    if upload_date and re.fullmatch(r"\d{8}", str(upload_date)):
        date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]} - "
    return out_root / (f"{date}{slug} [{video_id}]" if slug else video_id)


def _run(cmd: list[str], timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)


def probe_duration(media_path: Path) -> float:
    res = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(media_path),
    ])
    return float(res.stdout.strip())


def probe_metadata(url: str) -> dict[str, Any]:
    """Fetch video metadata, live status, and YouTube heatmap via yt-dlp --dump-json."""
    cmd = [
        "yt-dlp", "--dump-json", "--no-playlist", "--skip-download",
        "--socket-timeout", "30",
        *js_runtime_args(),
        url,
    ]
    res = _run(cmd, timeout=90, check=False)
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(f"yt-dlp metadata failed (exit {res.returncode}): {err[-600:]}")
    lines = [ln for ln in res.stdout.splitlines() if ln.strip().startswith("{")]
    if not lines:
        raise RuntimeError("yt-dlp --dump-json returned no JSON payload")
    return json.loads(lines[0])


def extract_wav(src_path: Path, wav_path: Path) -> Path:
    """Convert any audio/video file to 16 kHz mono PCM WAV for analysis and ASR."""
    tmp_wav = wav_path.with_suffix(".tmp.wav")
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(src_path),
        "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
        str(tmp_wav),
    ]
    _run(cmd, timeout=600)
    tmp_wav.replace(wav_path)
    return wav_path


def download_vod_audio(url: str, cache_dir: Path) -> Path:
    """Download lightweight audio-only stream for VOD analysis."""
    wav_path = cache_dir / "audio.wav"
    if wav_path.is_file() and wav_path.stat().st_size > 1024:
        print(f"[ingest] Reusing cached audio: {wav_path}", flush=True)
        return wav_path

    raw_tmpl = str(cache_dir / "raw_audio.%(ext)s")
    cmd = [
        "yt-dlp", "--no-playlist", "--socket-timeout", "30",
        "--retries", "5", "--fragment-retries", "5",
        "-f", "bestaudio[abr<=128]/bestaudio/worstaudio/best",
        "-o", raw_tmpl,
        *js_runtime_args(),
        url,
    ]
    res = _run(cmd, timeout=900, check=False)
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(f"yt-dlp audio download failed: {err[-600:]}")

    candidates = sorted(cache_dir.glob("raw_audio.*"))
    if not candidates:
        raise RuntimeError("yt-dlp finished without producing an audio file")
    raw_audio = candidates[0]
    try:
        extract_wav(raw_audio, wav_path)
    finally:
        raw_audio.unlink(missing_ok=True)
    return wav_path


def record_live_stream(url: str, live_minutes: float, cache_dir: Path) -> tuple[Path, Path]:
    """Record `live_minutes` from a live stream to disk and extract 16 kHz WAV."""
    seconds = max(30, int(round(live_minutes * 60)))
    video_path = cache_dir / "live_source.mkv"
    wav_path = cache_dir / "audio.wav"

    if video_path.is_file() and video_path.stat().st_size > 10240:
        try:
            existing_dur = probe_duration(video_path)
        except Exception:
            existing_dur = 0.0
        if existing_dur >= seconds * 0.9:
            print(f"[ingest] Reusing recorded live file ({existing_dur:.1f}s): {video_path}", flush=True)
            if not wav_path.is_file() or wav_path.stat().st_size <= 1024:
                extract_wav(video_path, wav_path)
            return wav_path, video_path

    g_cmd = [
        "yt-dlp", "-g", "--no-playlist", "--socket-timeout", "30",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/best",
        *js_runtime_args(),
        url,
    ]
    g_res = _run(g_cmd, timeout=60, check=False)
    urls = [u.strip() for u in g_res.stdout.splitlines() if u.strip().startswith("http")]

    tmp_vid = cache_dir / "live_source.tmp.mkv"
    recorded = False
    if 1 <= len(urls) <= 2:
        ff_cmd = ["ffmpeg", "-y", "-v", "warning"]
        for stream_url in urls:
            ff_cmd.extend([
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                "-i", stream_url,
            ])
        ff_cmd.extend(["-t", str(seconds), "-c", "copy", str(tmp_vid)])
        ff_res = _run(ff_cmd, timeout=seconds + 180, check=False)
        if ff_res.returncode == 0 and tmp_vid.is_file() and tmp_vid.stat().st_size > 10240:
            tmp_vid.replace(video_path)
            recorded = True
        else:
            tmp_vid.unlink(missing_ok=True)

    if not recorded:
        # Fallback: yt-dlp native ffmpeg downloader
        dl_cmd = [
            "yt-dlp", "--no-playlist", "--socket-timeout", "30",
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/best",
            "--downloader", "ffmpeg",
            "--downloader-args", f"ffmpeg_i:-t {seconds}",
            "-o", str(cache_dir / "live_source.%(ext)s"),
            *js_runtime_args(),
            url,
        ]
        dl_res = _run(dl_cmd, timeout=seconds + 180, check=False)
        found = [p for p in cache_dir.glob("live_source.*") if ".tmp." not in p.name]
        if not found:
            err = (dl_res.stderr or dl_res.stdout or "").strip()
            raise RuntimeError(f"Live recording failed: {err[-600:]}")
        video_path = found[0]

    extract_wav(video_path, wav_path)
    return wav_path, video_path


def _yt_runs_to_text(runs: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for r in runs:
        if "text" in r:
            parts.append(str(r["text"]))
        elif "emoji" in r:
            emoji = r["emoji"]
            if emoji.get("isCustomEmoji"):
                shortcuts = emoji.get("shortcuts") or [":emote:"]
                parts.append(f" {shortcuts[0].strip(':')} ")
            else:
                parts.append(str(emoji.get("emojiId", "")))
    return "".join(parts)


def parse_ytdlp_live_chat(lines: list[str] | Any) -> list[tuple[float, str, str]]:
    """Parse yt-dlp .live_chat.json JSON-lines into (time_s, text, author)."""
    rows: list[tuple[float, str, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        replay = obj.get("replayChatItemAction") or {}
        offset_ms = replay.get("videoOffsetTimeMsec")
        if offset_ms is None:
            continue
        try:
            t_s = int(offset_ms) / 1000.0
        except (TypeError, ValueError):
            continue
        if t_s < 0:
            continue
        for action in replay.get("actions", []):
            item = (action.get("addChatItemAction") or {}).get("item") or {}
            renderer = (
                item.get("liveChatTextMessageRenderer")
                or item.get("liveChatPaidMessageRenderer")
            )
            if not renderer:
                continue
            text = _yt_runs_to_text((renderer.get("message") or {}).get("runs", []))
            author = (renderer.get("authorName") or {}).get("simpleText", "")
            if text.strip():
                rows.append((t_s, text, author))
    return rows


def _compact_chat_rows(rows: list[tuple[float, str, str]]) -> list[list[Any]]:
    authors: dict[str, int] = {}
    msgs: list[list[Any]] = []
    for t, text, author in rows:
        clean = " ".join(str(text).split())
        if not clean or t < 0:
            continue
        if (author or "").casefold().lstrip("@") in BOT_AUTHORS:
            continue
        idx = authors.setdefault(author or f"anon_{len(authors)}", len(authors))
        msgs.append([round(float(t), 2), clean[:300], idx])
    msgs.sort(key=lambda m: m[0])
    return msgs


def _twitch_gql_page(video_id: str, offset: float) -> list[dict[str, Any]]:
    payload = json.dumps([{
        "operationName": "VideoCommentsByOffsetOrCursor",
        "variables": {"videoID": video_id, "contentOffsetSeconds": int(offset)},
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": TWITCH_COMMENTS_HASH}},
    }]).encode("utf-8")
    req = urllib.request.Request(
        TWITCH_GQL,
        data=payload,
        headers={
            "Client-Id": TWITCH_CLIENT_ID,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            video = (data[0].get("data") or {}).get("video") or {}
            return (video.get("comments") or {}).get("edges") or []
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return []


def _download_twitch_chat(url: str, duration: float) -> list[tuple[float, str, str]]:
    m = re.search(r"/videos?/(\d+)", url)
    if not m:
        return []
    video_id = m.group(1)
    dur = duration or 3600.0
    parts = max(1, min(6, int(dur // 900)))
    bounds = [dur * i / parts for i in range(parts + 1)]
    bounds[-1] = dur + 60.0

    def fetch_segment(i: int) -> list[tuple[float, str, str]]:
        a, b = bounds[i], bounds[i + 1]
        offset = a
        seen: set[str] = set()
        seg_rows: list[tuple[float, str, str]] = []
        while offset < b:
            edges = _twitch_gql_page(video_id, offset)
            if not edges:
                break
            new_count = 0
            last_t = offset
            for edge in edges:
                node = edge.get("node") or {}
                t = node.get("contentOffsetSeconds")
                if t is None:
                    continue
                last_t = max(last_t, float(t))
                nid = str(node.get("id") or "")
                if nid in seen or not (a <= t < b):
                    continue
                seen.add(nid)
                new_count += 1
                frags = (node.get("message") or {}).get("fragments") or []
                text = "".join(f.get("text", "") for f in frags)
                author = (node.get("commenter") or {}).get("login", "")
                seg_rows.append((float(t), text, author))
            offset = last_t if (new_count and last_t > offset) else offset + 1
            if not new_count and len(edges) < 5:
                break
        return seg_rows

    with ThreadPoolExecutor(max_workers=parts) as pool:
        chunks = list(pool.map(fetch_segment, range(parts)))
    return [row for chunk in chunks for row in chunk]


def download_chat(url: str, info: dict[str, Any], cache_dir: Path) -> list[list[Any]] | None:
    """Best-effort chat replay download. Never raises on unavailable chat."""
    chat_json = cache_dir / "chat.json"
    if chat_json.is_file():
        try:
            cached = json.loads(chat_json.read_text(encoding="utf-8"))
            if cached.get("status") == "ok":
                print(f"[ingest] Reusing cached chat ({len(cached['messages'])} msgs)", flush=True)
                return cached["messages"]
            return None
        except Exception:
            pass

    extractor = str(info.get("extractor") or "").lower()
    rows: list[tuple[float, str, str]] = []
    source = extractor or "unknown"

    try:
        if "youtube" in extractor or "youtu" in url:
            source = "youtube"
            subs = info.get("subtitles") or {}
            if "live_chat" in subs:
                chat_dir = cache_dir / "chat_raw"
                chat_dir.mkdir(parents=True, exist_ok=True)
                cmd = [
                    "yt-dlp", "--write-subs", "--sub-langs", "live_chat",
                    "--skip-download", "--no-playlist", "--socket-timeout", "30",
                    "-o", str(chat_dir / "chat.%(ext)s"),
                    *js_runtime_args(),
                    url,
                ]
                _run(cmd, timeout=240, check=False)
                files = list(chat_dir.glob("*.live_chat.json"))
                if files:
                    with files[0].open(encoding="utf-8", errors="replace") as fh:
                        rows = parse_ytdlp_live_chat(fh)
        elif "twitch" in extractor or "twitch.tv" in url:
            source = "twitch"
            rows = _download_twitch_chat(url, float(info.get("duration") or 0.0))
    except Exception as exc:
        print(f"[ingest] Chat unavailable (degrading cleanly): {exc}", flush=True)
        rows = []

    msgs = _compact_chat_rows(rows)
    payload = {
        "status": "ok" if msgs else "none",
        "source": source,
        "count": len(msgs),
        "messages": msgs,
    }
    chat_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if msgs:
        print(f"[ingest] Chat loaded: {len(msgs)} messages ({source})", flush=True)
        return msgs
    print("[ingest] No chat replay available; proceeding without chat signal.", flush=True)
    return None


def format_channel_credit(channel: str, url: str = "", extractor: str = "") -> str:
    """Format channel credit as 'twitch.tv/<canal>' for Twitch or '@<canal>' for YouTube."""
    raw_ch = (channel or "").strip()
    low_ext = (extractor or "").lower()
    low_url = (url or "").lower()

    if raw_ch.lower().startswith(("twitch.tv/", "www.twitch.tv/")):
        name = raw_ch.split("/", 1)[1].strip("/ ").lstrip("@")
        return f"twitch.tv/{name}" if name else ""
    if raw_ch.startswith("@"):
        if "twitch" in low_ext or "twitch.tv" in low_url:
            name = raw_ch.lstrip("@").strip("/")
            return f"twitch.tv/{name}" if name else ""
        return raw_ch

    if "twitch" in low_ext or "twitch.tv" in low_url:
        if raw_ch:
            return f"twitch.tv/{raw_ch.lstrip('@').strip('/')}"
        m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", url or "", re.IGNORECASE)
        if m and m.group(1).lower() not in ("videos", "video", "directory", "clip", "clips"):
            return f"twitch.tv/{m.group(1)}"
        return ""

    if "youtube" in low_ext or "youtu.be" in low_url or "youtube.com" in low_url:
        if raw_ch:
            return f"@{raw_ch.lstrip('@').strip('/')}"
        m = re.search(r"youtube\.com/@([A-Za-z0-9_.-]+)", url or "", re.IGNORECASE)
        if m:
            return f"@{m.group(1)}"
        return ""

    return raw_ch


def ingest_local(local_dir: Path, out_root: Path, enable_chat: bool = True) -> IngestResult:
    """Ingest a recording already on disk (source.mkv + meta.json + chat.json). No network calls."""
    source = local_dir / "source.mkv"
    if not source.is_file():
        raise FileNotFoundError(f"source.mkv not found in {local_dir}")

    meta_path = local_dir / "meta.json"
    meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    video_id = sanitize_id(str(meta.get("id") or local_dir.name))
    title = str(meta.get("title") or video_id)
    url = str(meta.get("webpage_url") or meta.get("url") or "")
    extractor = str(meta.get("extractor") or "local")
    raw_channel = str(meta.get("channel") or meta.get("uploader") or meta.get("uploader_id") or "").strip()
    channel_credit = format_channel_credit(raw_channel, url, extractor) or video_id

    out_dir = out_dir_for(out_root, video_id, title, str(meta.get("upload_date") or ""))
    cache_dir = out_dir / ".cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    wav_path = cache_dir / "audio.wav"
    if not (wav_path.is_file() and wav_path.stat().st_size > 1024):
        extract_wav(source, wav_path)

    chat_messages: list[list[Any]] | None = None
    chat_path = local_dir / "chat.json"
    if enable_chat and chat_path.is_file():
        chat = json.loads(chat_path.read_text(encoding="utf-8"))
        rows = chat.get("messages", []) if isinstance(chat, dict) else chat
        chat_messages = rows or None

    return IngestResult(
        video_id=video_id,
        title=title,
        url=url,
        extractor=extractor,
        is_live=bool(meta.get("is_live")),
        duration=probe_duration(wav_path),
        audio_path=wav_path,
        video_path=source,
        heatmap=None,
        chat_messages=chat_messages,
        out_dir=out_dir,
        cache_dir=cache_dir,
        channel=channel_credit,
    )


def ingest(
    url: str,
    out_root: Path,
    live_minutes: float = 20.0,
    enable_chat: bool = True,
) -> IngestResult:
    """Fetch metadata, download VOD audio or record live stream, and optionally fetch chat."""
    info = probe_metadata(url)
    raw_id = str(info.get("id") or "video")
    video_id = sanitize_id(raw_id)
    title = str(info.get("title") or video_id)
    extractor = str(info.get("extractor") or "")
    is_live = bool(info.get("is_live"))
    heatmap = info.get("heatmap") if isinstance(info.get("heatmap"), list) else None
    webpage_url = str(info.get("webpage_url") or url)
    raw_channel = str(info.get("channel") or info.get("uploader") or info.get("uploader_id") or "").strip()
    channel_credit = format_channel_credit(raw_channel, webpage_url, extractor) or video_id

    out_dir = out_dir_for(out_root, video_id, title, str(info.get("upload_date") or ""))
    cache_dir = out_dir / ".cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    meta_path = cache_dir / "meta.json"
    meta_path.write_text(json.dumps({
        "id": video_id,
        "title": title,
        "url": url,
        "webpage_url": webpage_url,
        "extractor": extractor,
        "channel": channel_credit,
        "is_live": is_live,
        "duration": info.get("duration"),
        "has_heatmap": bool(heatmap),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    video_path: Path | None = None
    if is_live:
        print(f"[ingest] Live stream detected; recording {live_minutes:.1f} min...", flush=True)
        audio_path, video_path = record_live_stream(url, live_minutes, cache_dir)
        duration = probe_duration(audio_path)
    else:
        audio_path = download_vod_audio(url, cache_dir)
        duration = float(info.get("duration") or 0.0)
        if duration <= 0:
            duration = probe_duration(audio_path)

    chat_messages: list[list[Any]] | None = None
    if enable_chat and not is_live:
        chat_messages = download_chat(url, info, cache_dir)
    elif not enable_chat:
        print("[ingest] Chat signal disabled (--no-chat).", flush=True)
    elif is_live:
        print("[ingest] Live mode: skipping replay chat fetch.", flush=True)

    return IngestResult(
        video_id=video_id,
        title=title,
        url=webpage_url,
        extractor=extractor,
        is_live=is_live,
        duration=duration,
        audio_path=audio_path,
        video_path=video_path,
        heatmap=heatmap,
        chat_messages=chat_messages,
        out_dir=out_dir,
        cache_dir=cache_dir,
        channel=channel_credit,
    )
