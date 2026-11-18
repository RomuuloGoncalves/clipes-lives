"""Twitch monitor: channel discovery, anonymous IRC spike detection, rolling recorder, disk queue."""

from __future__ import annotations

import asyncio
import signal
import json
import math
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .ingest import BOT_AUTHORS, _compact_chat_rows, probe_duration, probe_metadata, sanitize_id
from .janelas import filter_emitted, owns, window_end

MAX_CHANNELS = 80
HELIX_MIN_INTERVAL_S = 60.0
HELIX_STREAMS_URL = "https://api.twitch.tv/helix/streams"
HELIX_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
JOIN_INTERVAL_S = 0.65
IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
IRC_READ_TIMEOUT_S = 400.0  # Twitch pings about every 5 min; silence beyond this means a dead socket
IRC_MAX_BACKOFF_S = 60.0
WARMUP_S = 60.0
COOLDOWN_S = 180.0
BASELINE_S = 180
BASELINE_SKIP_S = 15
CHAT_KEEP_S = 400.0  # pre-roll 180s + post-roll 120s + margin
SEGMENT_S = 10
BUFFER_S = 480
PRE_ROLL_S = 180
POST_ROLL_MIN_S = 60
POST_ROLL_MAX_S = 120
MIN_CLIP_SOURCE_S = 25.0  # a source shorter than min_dur cannot yield a clip
STREAMLINK_QUALITY = "720p60,720p,480p,best"
CHANNEL_RE = re.compile(r"[a-z0-9_]{1,25}")

# (game_id, limit) -> (monotonic timestamp of last Helix attempt, logins it returned); enforces >= 60s polling.
_helix_cache: dict[tuple[str | None, int], tuple[float, list[str]]] = {}
_helix_stream_ids: dict[str, str] = {}
COMMENT_RE = re.compile(r"(^|\s)#(\s|$)")  # `# comment` / `name  # note`; `#gaules` stays a channel


# --- T2: discovery and per-channel policy -------------------------------------------------------


@dataclass
class ChannelPolicy:
    blocked: set[str] = field(default_factory=set)
    allowed: set[str] = field(default_factory=set)
    delay_h: dict[str, float] = field(default_factory=dict)

    def permits(self, channel: str) -> bool:
        if channel in self.blocked:
            return False
        return not self.allowed or channel in self.allowed


def load_policy(path: Path | None) -> ChannelPolicy:
    """Read policy.json (`blocked`, `allowed`, `delay_h`). Missing file means no restrictions."""
    if path is None or not path.is_file():
        return ChannelPolicy()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ChannelPolicy(
        blocked={str(c).lower() for c in raw.get("blocked", [])},
        allowed={str(c).lower() for c in raw.get("allowed", [])},
        delay_h={str(c).lower(): float(h) for c, h in raw.get("delay_h", {}).items()},
    )


def _http_json(req: urllib.request.Request) -> tuple[dict[str, Any], Any]:
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.headers


def _helix_logins(client_id: str, client_secret: str, game_id: str | None, limit: int) -> list[str]:
    """Live channel logins from Helix Get Streams. Token lives only in memory."""
    token_body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode()
    token_data, _ = _http_json(urllib.request.Request(HELIX_TOKEN_URL, data=token_body, method="POST"))
    headers = {"Client-Id": client_id, "Authorization": f"Bearer {token_data['access_token']}"}

    logins: list[str] = []
    cursor = None
    while len(logins) < limit:
        query = {"first": min(100, limit), "type": "live"}
        if game_id:
            query["game_id"] = game_id
        if cursor:
            query["after"] = cursor
        url = f"{HELIX_STREAMS_URL}?{urllib.parse.urlencode(query)}"
        data, resp_headers = _http_json(urllib.request.Request(url, headers=headers))
        for s in data.get("data", []):
            login = str(s["user_login"]).lower()
            logins.append(login)
            if s.get("id"):
                _helix_stream_ids[login] = str(s["id"])
        cursor = (data.get("pagination") or {}).get("cursor")
        if not cursor or not data.get("data") or int(resp_headers.get("Ratelimit-Remaining", 1)) <= 0:
            break
    return logins


def discover_channels(
    channels_file: Path,
    policy: ChannelPolicy,
    game_id: str | None = None,
    limit: int = MAX_CHANNELS,
) -> list[str]:
    """channels.txt first (one login per line, `# ` comments), then Helix when env credentials exist."""
    limit = min(limit, MAX_CHANNELS)
    candidates: list[str] = []
    if channels_file.is_file():
        for line in channels_file.read_text(encoding="utf-8").splitlines():
            name = COMMENT_RE.split(line, 1)[0].strip().lstrip("#").lower()
            if name:
                candidates.append(name)

    client_id = os.environ.get("TWITCH_CLIENT_ID")
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET")
    if client_id and client_secret:
        last_try, cached = _helix_cache.get((game_id, limit), (float("-inf"), []))
        if time.monotonic() - last_try >= HELIX_MIN_INTERVAL_S:
            try:
                cached = _helix_logins(client_id, client_secret, game_id, limit)
            except (OSError, ValueError, KeyError) as exc:  # HTTP 429/5xx, network, bad payload
                print(f"[monitor] Helix unavailable, using channels.txt only: {exc}", flush=True)
                cached = []
            _helix_cache[(game_id, limit)] = (time.monotonic(), cached)
        candidates += cached

    channels: list[str] = []
    for name in candidates:
        if name in channels or not CHANNEL_RE.fullmatch(name) or not policy.permits(name):
            continue
        channels.append(name)
    return channels[:limit]


# --- T3: IRC parser, spike detector, anonymous IRC client ----------------------------------------


def _is_noise(author: str, text: str) -> bool:
    return author.casefold().lstrip("@") in BOT_AUTHORS or text.startswith("!")


def parse_irc_line(line: str) -> tuple[dict[str, str], str, str, str] | None:
    """Parse a Twitch PRIVMSG into (tags, channel, author, text); None for anything not countable.

    Drops bots, `!commands` and Shared Chat copies (`source-room-id` different from `room-id`).
    """
    line = line.strip()
    tags: dict[str, str] = {}
    if line.startswith("@"):
        tag_str, _, line = line[1:].partition(" ")
        tags = {k: v for k, _, v in (kv.partition("=") for kv in tag_str.split(";"))}
    if not line.startswith(":"):
        return None
    prefix, _, rest = line[1:].partition(" ")
    command, _, rest = rest.partition(" ")
    if command != "PRIVMSG":
        return None
    target, _, text = rest.partition(" :")
    author = prefix.split("!", 1)[0].lower()
    source_room = tags.get("source-room-id")
    if source_room and source_room != tags.get("room-id"):
        return None
    if not text or _is_noise(author, text):
        return None
    return tags, target.lstrip("#").lower(), author, text


@dataclass
class SpikeEvent:
    channel: str
    peak_ts: float
    count10: int
    unique_authors: int
    expected: float
    z_score: float


class ChannelSpikeDetector:
    """Relative chat spike detector over 10s windows (Poisson z-score, same model as signals.signal_chat)."""

    def __init__(self, channel: str, t0: float):
        self.channel = channel
        self.t0 = t0  # first JOIN (or first message) of this channel; warmup and baseline start here
        self.joined = math.isfinite(t0)  # t0=+inf until start_watching: the channel is not monitored yet
        self.last_spike = float("-inf")
        self.last_check = float("-inf")
        self.messages: deque[tuple[float, str, str]] = deque()  # (t, author, text)
        self.buckets: deque[list[int]] = deque()  # [epoch second, message count], ascending seconds

    def start_watching(self, t: float) -> None:
        """Start this channel's 60s warmup at its first JOIN. A later re-JOIN must not restart it."""
        if self.joined:
            return
        self.joined = True
        self.t0 = t

    def record_message(self, t: float, author: str, text: str) -> None:
        if not self.joined:  # message arrived before the JOIN callback
            self.start_watching(t)
        if _is_noise(author, text):
            return
        self.messages.append((t, author.casefold().lstrip("@"), text))
        while self.messages and self.messages[0][0] < t - CHAT_KEEP_S:
            self.messages.popleft()
        sec = int(t)
        for i in range(len(self.buckets) - 1, -1, -1):  # newest first: O(1) for in-order messages
            if self.buckets[i][0] == sec:
                self.buckets[i][1] += 1
                break
            if self.buckets[i][0] < sec:
                self.buckets.insert(i + 1, [sec, 1])
                break
        else:
            self.buckets.appendleft([sec, 1])
        while self.buckets[0][0] < sec - CHAT_KEEP_S:
            self.buckets.popleft()

    def _last10(self, now: float) -> list[tuple[float, str, str]]:
        recent = []
        for msg in reversed(self.messages):
            if msg[0] <= now - 10:
                break
            if msg[0] <= now:
                recent.append(msg)
        return recent

    def count10(self, now: float) -> int:
        return len(self._last10(now))

    def chat_rows(self, start: float, end: float) -> list[tuple[float, str, str]]:
        """Messages in [start, end] as (t, text, author)."""
        return [(t, text, author) for t, author, text in self.messages if start <= t <= end]

    def expected(self, now: float) -> float:
        """max(median 10s count over last 180s excluding last 15s, 0.4 * mean, 2.5)."""
        first = int(max(self.t0, now - BASELINE_S))
        last = int(now - BASELINE_SKIP_S)
        if last - first < 10:
            return 2.5
        per_second = np.zeros(last - first)
        for sec, count in self.buckets:
            if first <= sec < last:
                per_second[sec - first] += count
        sums = np.convolve(per_second, np.ones(10), mode="valid")
        return max(float(np.median(sums)), 0.4 * float(sums.mean()), 2.5)

    def check_spike(self, now: float) -> SpikeEvent | None:
        """At most one evaluation per second; extra calls inside the same second return None."""
        if now - self.last_check < 1.0:
            return None
        self.last_check = now
        if now - self.t0 < WARMUP_S or now - self.last_spike < COOLDOWN_S:
            return None
        recent = self._last10(now)
        count10 = len(recent)
        unique_authors = len({author for _, author, _ in recent})
        if count10 < 6 or unique_authors < 4:
            return None
        expected = self.expected(now)
        z_score = (count10 - expected) / math.sqrt(expected + 1.0)
        if z_score < 3.5 or count10 / expected < 3.0:
            return None
        self.last_spike = now
        return SpikeEvent(self.channel, now, count10, unique_authors, expected, z_score)


async def _open_tls(host: str, port: int):
    return await asyncio.open_connection(host, port, ssl=ssl.create_default_context())


async def irc_chat_loop(
    channels: list[str],
    on_message: Callable[[str, float, str, str], None],
    *,
    host: str = IRC_HOST,
    port: int = IRC_PORT,
    open_connection=_open_tls,
    sleep=asyncio.sleep,
    stop: asyncio.Event | None = None,
    on_join: Callable[[str, float], None] | None = None,
) -> None:
    """Anonymous (justinfan) IRC reader. Reconnects with 1s..60s backoff and re-JOINs at 0.65s per channel."""
    channels = [c for c in channels if CHANNEL_RE.fullmatch(c)][:MAX_CHANNELS]  # no IRC injection via names
    nick = f"justinfan{random.randint(10000, 99999)}"
    backoff = 1.0
    while not (stop and stop.is_set()):
        writer = None
        try:
            reader, writer = await open_connection(host, port)
            writer.write(f"CAP REQ :twitch.tv/tags twitch.tv/commands\r\nPASS SCHMOOPIIE\r\nNICK {nick}\r\n".encode())
            for i, channel in enumerate(channels):
                if i:
                    await sleep(JOIN_INTERVAL_S)
                writer.write(f"JOIN #{channel}\r\n".encode())
                await writer.drain()
                if on_join is not None:  # warmup of this channel starts now, not at loop start
                    try:
                        on_join(channel, time.time())
                    except Exception as exc:
                        print(f"[monitor] on_join failed for #{channel}: {exc!r}", flush=True)
            t_joined = time.time()
            while not (stop and stop.is_set()):
                raw = await asyncio.wait_for(reader.readline(), IRC_READ_TIMEOUT_S)
                if not raw:
                    raise ConnectionError("IRC socket closed")
                backoff = 1.0
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("PING"):
                    writer.write(b"PONG :tmi.twitch.tv\r\n")
                    await writer.drain()
                    continue
                sent = re.search(r"tmi-sent-ts=(\d+)", line)
                if sent and int(sent.group(1)) / 1000.0 < t_joined:
                    continue  # sent before the last JOIN finished: backlog, not live chat (no false spike on reconnect)
                parsed = parse_irc_line(line)
                if parsed:
                    _, channel, author, text = parsed
                    try:
                        on_message(channel, time.time(), author, text)
                    except Exception as exc:  # a bad message must not drop the IRC connection
                        print(f"[monitor] on_message failed for #{channel}: {exc!r}", flush=True)
        # ValueError/LimitOverrunError: a line longer than the StreamReader limit
        except (OSError, EOFError, asyncio.TimeoutError, ValueError, asyncio.LimitOverrunError) as exc:
            print(f"[monitor] IRC disconnected ({exc!r}); retrying in {backoff:.0f}s", flush=True)
            await sleep(backoff)
            backoff = min(backoff * 2, IRC_MAX_BACKOFF_S)
        finally:
            if writer is not None:
                writer.close()


# --- T4: rolling recorder (streamlink | ffmpeg -c copy) -------------------------------------------


def streamlink_cmd(channel: str) -> list[str]:
    return ["streamlink", "--quiet", "--stdout", f"twitch.tv/{channel}", STREAMLINK_QUALITY]


def segment_cmd(seg_dir: Path, segment_s: int = SEGMENT_S) -> list[str]:
    """ffmpeg reading mpegts on stdin and writing `<epoch>.ts` segments without re-encoding."""
    return [
        "ffmpeg", "-v", "error", "-i", "pipe:0", "-c", "copy",
        "-f", "segment", "-segment_time", str(segment_s), "-reset_timestamps", "1",
        "-strftime", "1", "-segment_format", "mpegts", str(seg_dir / "%s.ts"),
    ]


@dataclass
class _Recording:
    channel: str
    seg_dir: Path
    procs: list[Any]
    on_demand: bool

    def alive(self) -> bool:
        return all(proc.poll() is None for proc in self.procs)


class RollingRecorderPool:
    """Up to `max_hot` hot channels + `max_on_demand` on-demand ones, each a streamlink|ffmpeg pipeline."""

    def __init__(
        self,
        buf_root: Path,
        max_hot: int = 3,
        max_on_demand: int = 1,
        buffer_seconds: int = BUFFER_S,
        popen=subprocess.Popen,
    ):
        self.buf_root = buf_root
        self.max_hot = max_hot
        self.max_on_demand = max_on_demand
        self.buffer_seconds = buffer_seconds
        self.popen = popen
        self.recordings: dict[str, _Recording] = {}
        self.holds: dict[str, float] = {}  # channel -> oldest timestamp cleanup must keep

    def _start(self, channel: str, on_demand: bool) -> None:
        if not CHANNEL_RE.fullmatch(channel):
            raise ValueError(f"invalid channel name: {channel!r}")
        seg_dir = self.buf_root / channel
        seg_dir.mkdir(parents=True, exist_ok=True)
        # own session: Ctrl+C on the monitor does not hit the recorders, _stop terminates them in order
        source = self.popen(streamlink_cmd(channel), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            start_new_session=True)
        try:
            sink = self.popen(segment_cmd(seg_dir), stdin=source.stdout, stderr=subprocess.DEVNULL,
                              start_new_session=True)
        except BaseException:
            source.kill()
            source.wait()
            shutil.rmtree(seg_dir, ignore_errors=True)
            raise
        finally:
            source.stdout.close()  # streamlink gets SIGPIPE if ffmpeg dies
        self.recordings[channel] = _Recording(channel, seg_dir, [source, sink], on_demand)

    def _stop(self, channel: str, remove_buffer: bool) -> None:
        rec = self.recordings.pop(channel)
        for proc in rec.procs:  # streamlink first so ffmpeg sees EOF and closes the last segment
            proc.terminate()
        for proc in rec.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()  # reap: no zombie left behind
        if remove_buffer:
            shutil.rmtree(rec.seg_dir, ignore_errors=True)

    def _try_start(self, channel: str, on_demand: bool) -> bool:
        try:
            self._start(channel, on_demand)
        except (OSError, ValueError) as exc:  # streamlink/ffmpeg missing, bad name
            print(f"[monitor] recorder for {channel} failed to start: {exc}", flush=True)
            return False
        return True

    def set_hot(self, ranked_channels: list[str]) -> None:
        """Keep recorders only for the `max_hot` most active channels; drop (and delete) the rest.

        Hot channels with an in-flight capture (hold) keep their slot whatever their rank.
        """
        held = [c for c, r in self.recordings.items() if not r.on_demand and c in self.holds]
        wanted = (held + [c for c in ranked_channels if c not in held])[: self.max_hot]
        for channel in [c for c, r in self.recordings.items() if not r.on_demand and c not in wanted]:
            self._stop(channel, remove_buffer=True)
        for channel in wanted:
            rec = self.recordings.get(channel)
            if rec is not None and not rec.alive():
                self._stop(channel, remove_buffer=False)
                rec = None
            if rec is None:
                self._try_start(channel, on_demand=False)
            else:
                rec.on_demand = False  # promoted from the on-demand slot

    def start_on_demand(self, channel: str) -> bool:
        """True when `channel` is being recorded (already, or via the free on-demand slot)."""
        if channel in self.recordings:
            return True
        if sum(1 for r in self.recordings.values() if r.on_demand) >= self.max_on_demand:
            return False
        return self._try_start(channel, on_demand=True)

    def release_on_demand(self, channel: str) -> None:
        rec = self.recordings.get(channel)
        if rec is not None and rec.on_demand:
            self._stop(channel, remove_buffer=True)

    def is_recording(self, channel: str) -> bool:
        rec = self.recordings.get(channel)
        return rec is not None and rec.alive()

    def stop_all(self) -> None:
        for channel in list(self.recordings):
            self._stop(channel, remove_buffer=True)

    def stop_channel(self, channel: str) -> None:
        self.release_hold(channel)
        if channel in self.recordings:
            self._stop(channel, remove_buffer=True)

    def segments(self, channel: str) -> list[tuple[int, Path]]:
        """(start epoch, path) of the channel's `<epoch>.ts` files, oldest first."""
        found = [(int(p.stem), p) for p in (self.buf_root / channel).glob("*.ts") if p.stem.isdigit()]
        return sorted(found)

    def cleanup(self, now: float | None = None) -> None:
        """Delete segments older than the buffer, except those held for an in-flight job."""
        now = time.time() if now is None else now
        for channel in {c for c in self.recordings} | set(self.holds):
            hold_from = self.holds.get(channel)
            for start, path in self.segments(channel):
                if hold_from is not None and start + SEGMENT_S > hold_from:
                    continue
                try:
                    if path.stat().st_mtime < now - self.buffer_seconds:
                        path.unlink(missing_ok=True)
                except FileNotFoundError:  # removed by a stopping recorder meanwhile
                    continue

    def enforce_cap(self, max_bytes: int) -> None:
        """Delete the oldest unheld segments across all channels until the buffer fits in max_bytes."""
        sized = []
        for seg_dir in self.buf_root.glob("*/"):
            hold_from = self.holds.get(seg_dir.name)
            for start, path in self.segments(seg_dir.name):
                held = hold_from is not None and start + SEGMENT_S > hold_from
                try:
                    sized.append((start, held, path, path.stat().st_size))
                except FileNotFoundError:
                    continue
        total = sum(size for *_, size in sized)
        for _, held, path, size in sorted(sized, key=lambda s: s[0]):
            if total <= max_bytes:
                break
            if not held:
                path.unlink(missing_ok=True)
                total -= size

    def hold(self, channel: str, from_ts: float) -> None:
        self.holds[channel] = from_ts

    def release_hold(self, channel: str) -> None:
        self.holds.pop(channel, None)

    def _select(self, channel: str, start_ts: float, end_ts: float) -> list[tuple[int, Path]]:
        chosen = [(s, p) for s, p in self.segments(channel) if s + SEGMENT_S > start_ts and s <= end_ts]
        if not chosen:
            raise ValueError(f"no buffered segments for {channel} in [{start_ts}, {end_ts}]")
        return chosen

    def video_start_ts(self, channel: str, start_ts: float, end_ts: float) -> float:
        """Epoch at which the concatenated video begins (anchor for chat.json times)."""
        return float(self._select(channel, start_ts, end_ts)[0][0])

    def freeze_and_concat(self, channel: str, start_ts: float, end_ts: float, dst_mkv: Path) -> Path:
        """Concatenate buffered segments overlapping [start_ts, end_ts] into dst_mkv with -c copy."""
        chosen = self._select(channel, start_ts, end_ts)
        list_file = dst_mkv.with_suffix(".concat.txt")
        list_file.write_text("".join(f"file '{path.absolute()}'\n" for _, path in chosen), encoding="utf-8")
        tmp_mkv = dst_mkv.with_suffix(".tmp.mkv")
        try:
            res = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                 "-i", str(list_file), "-c", "copy", str(tmp_mkv)],
                capture_output=True, text=True, check=False,
            )
            if res.returncode != 0:
                raise RuntimeError(f"ffmpeg concat failed: {res.stderr[-500:]}")
            tmp_mkv.replace(dst_mkv)
        finally:
            list_file.unlink(missing_ok=True)
            tmp_mkv.unlink(missing_ok=True)
        return dst_mkv


def write_chat_json(rows: list[tuple[float, str, str]], video_start_ts: float, dst: Path) -> None:
    """Save (t_abs, text, author) rows as [t_rel, text, author_idx] aligned to the video start."""
    relative = [(t - video_start_ts, text, author) for t, text, author in rows]
    dst.write_text(json.dumps(_compact_chat_rows(relative), ensure_ascii=False), encoding="utf-8")


async def capture_job(
    pool: RollingRecorderPool,
    detector: ChannelSpikeDetector,
    event: SpikeEvent,
    staging_root: Path,
    *,
    now=time.time,
    sleep=asyncio.sleep,
    coord: Any | None = None,
    live_id: str | None = None,
    score: float = 1.0,
    owned_lives: dict[str, str] | None = None,
    max_lives: int = 1,
) -> Path | None:
    """Record post-roll after a spike and build a job dir (source.mkv, chat.json, meta.json, job.json).

    Returns None when no recording slot is free. A source shorter than MIN_CLIP_SOURCE_S gets
    `status: rejected` in job.json so the caller can file it under queue/rejected/.
    """
    channel = event.channel
    if coord is not None and live_id is not None:
        owned = owned_lives if owned_lives is not None else {}
        if channel not in owned:
            if len(owned) >= max_lives:
                return None
            coord.ensure_live(live_id, channel=channel, platform="twitch")
            await _wait_claim(30.0 / max(1.0, float(score)), now=now, sleep=sleep)
            if len(owned) >= max_lives or not coord.claim_live(live_id):
                return None
            owned[channel] = live_id
    start_ts = event.peak_ts - PRE_ROLL_S
    if not pool.start_on_demand(channel):
        return None
    pool.hold(channel, start_ts)
    try:
        while True:
            elapsed = now() - event.peak_ts
            if elapsed >= POST_ROLL_MAX_S or not pool.is_recording(channel):
                break
            if elapsed >= POST_ROLL_MIN_S and detector.count10(now()) <= 1.2 * event.expected:
                break
            await sleep(1.0)
        end_ts = now()

        job_id = sanitize_id(f"{channel}_{int(event.peak_ts)}")
        job_dir = staging_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        try:
            job = {
                "job_id": job_id,
                "channel": channel,
                "url": f"https://www.twitch.tv/{channel}",
                "peak_ts": event.peak_ts,
                "count10": event.count10,
                "unique_authors10": event.unique_authors,
                "expected10": event.expected,
                "z_score": event.z_score,
                "created_at": end_ts,
                "ready_at": None,
                "score": None,
                "status": "pending",
            }
            duration = 0.0
            try:
                video_start = pool.video_start_ts(channel, start_ts, end_ts)
                # ffmpeg/ffprobe block for seconds: keep them off the event loop (IRC keeps reading)
                await asyncio.to_thread(pool.freeze_and_concat, channel, start_ts, end_ts, job_dir / "source.mkv")
                duration = await asyncio.to_thread(probe_duration, job_dir / "source.mkv")
            except ValueError:  # nothing buffered
                job.update(status="rejected", reject_reason="no_segments")
            else:
                job.update(peak_rel_s=round(event.peak_ts - video_start, 2),
                           partial_preroll=video_start > start_ts + 1)
                write_chat_json(detector.chat_rows(video_start, end_ts), video_start, job_dir / "chat.json")
                if duration < MIN_CLIP_SOURCE_S:
                    job.update(status="rejected", reject_reason="too_short")
            (job_dir / "meta.json").write_text(json.dumps({
                "id": job_id,
                "title": f"{channel} - pico de chat",
                "url": job["url"],
                "extractor": "twitch",
                "is_live": False,
                "duration": duration,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            (job_dir / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        except BaseException:  # ffmpeg failure, disk full, cancellation: no half-built job left in staging
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        return job_dir
    finally:
        pool.release_hold(channel)
        pool.release_on_demand(channel)


# --- T5: atomic disk queue and sequential worker ---------------------------------------------------

QUEUE_STATES = ("pending", "processing", "done", "rejected")


def _read_job(job_dir: Path) -> dict[str, Any]:
    return json.loads((job_dir / "job.json").read_text(encoding="utf-8"))


def _write_job(job_dir: Path, job: dict[str, Any]) -> None:
    tmp = job_dir / "job.json.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(job_dir / "job.json")


class DiskQueue:
    """Job directories under queue_root/{pending,processing,done,rejected}/<job_id>/, moved with os.replace."""

    def __init__(self, queue_root: Path):
        self.root = queue_root
        for state in QUEUE_STATES:
            (queue_root / state).mkdir(parents=True, exist_ok=True)

    def _move(self, job_dir: Path, state: str) -> Path:
        dst = self.root / state / job_dir.name
        os.replace(job_dir, dst)
        return dst

    def enqueue(self, job_dir: Path) -> Path:
        """Publish a staged job (same filesystem). `status: rejected` in job.json goes to rejected/."""
        job = _read_job(job_dir)
        state = "rejected" if job.get("status") == "rejected" else "pending"
        job["status"] = state
        _write_job(job_dir, job)
        return self._move(job_dir, state)

    def claim_next(self) -> Path | None:
        """Move the pending job with the highest z_score to processing/ (None when empty).

        A job whose job.json is missing or corrupt goes straight to rejected/ instead of blocking the queue.
        """
        pending = []
        for job_dir in (self.root / "pending").iterdir():
            try:
                pending.append((-float(_read_job(job_dir).get("z_score") or 0.0), job_dir.name, job_dir))
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                print(f"[monitor] unreadable job {job_dir.name} rejected: {exc!r}", flush=True)
                self._move(job_dir, "rejected")
        for _, _, job_dir in sorted(pending):
            try:
                job = _read_job(job_dir)
                job["status"] = "processing"
                _write_job(job_dir, job)
                return self._move(job_dir, "processing")
            except OSError:  # vanished meanwhile
                continue
        return None

    def complete(self, job_dir: Path, status: str, manifest: dict[str, Any]) -> Path:
        """Merge `manifest` into job.json and move the job to done/ or rejected/."""
        if status not in ("done", "rejected"):
            raise ValueError(f"invalid final status: {status}")
        job = {**_read_job(job_dir), **manifest, "status": status}
        _write_job(job_dir, job)
        (job_dir / "source.mkv").unlink(missing_ok=True)  # ~200 MB per job: the clips live in out/, the source is not needed
        return self._move(job_dir, status)

    def recover_orphans(self) -> int:
        """Put jobs left in processing/ by a crash back into pending/."""
        orphans = list((self.root / "processing").iterdir())
        for job_dir in orphans:
            try:
                job = _read_job(job_dir)
                job["status"] = "pending"
                _write_job(job_dir, job)
            except (OSError, ValueError, TypeError):  # corrupt: claim_next will reject it
                pass
            self._move(job_dir, "pending")
        return len(orphans)

    def index(self, state: str = "done") -> list[dict[str, Any]]:
        """Manifests of a state ordered by score, highest first."""
        jobs = [_read_job(d) for d in (self.root / state).iterdir()]
        return sorted(jobs, key=lambda j: -(j.get("score") or 0.0))


def run_local_pipeline(job_dir: Path, out_root: Path) -> dict[str, Any]:
    """Run the existing pipeline on a job dir via ingest_local (no yt-dlp). Returns the clips.json manifest."""
    from . import cli  # heavy imports (ASR, render) stay out of the IRC-only process
    from .ingest import ingest_local

    ingested: dict[str, Any] = {}

    def ingest_job(url, out_root, live_minutes, enable_chat):
        ingested["result"] = ingest_local(job_dir, out_root, enable_chat)
        return ingested["result"]

    args = cli.build_parser().parse_args([job_dir.name, "--out-dir", str(out_root)])
    original_ingest = cli.ingest
    cli.ingest = ingest_job
    try:
        exit_code = cli.run_pipeline(args)
    finally:
        cli.ingest = original_ingest
    if exit_code != 0:
        raise RuntimeError(f"pipeline exited with {exit_code}")
    out_dir = ingested["result"].out_dir
    return {**json.loads((out_dir / "clips.json").read_text(encoding="utf-8")), "output_dir": str(out_dir)}


MONGO_RECONNECT_S = 60.0
_MONGO_ERR_NAMES = frozenset({
    "ServerSelectionTimeoutError", "PyMongoError", "ConnectionFailure",
    "NetworkTimeout", "AutoReconnect", "ExecutionTimeout",
})


def _mask_uri(uri: str) -> str:
    if "clipes.coord" in sys.modules:
        return sys.modules["clipes.coord"].mask_uri(uri)
    return uri.split("://", 1)[0] + "://***@***" if "://" in uri else "***"


def _sanitize_error(exc: BaseException, uri: str | None = None) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    if uri:
        msg = msg.replace(uri, _mask_uri(uri))
        parsed = urllib.parse.urlsplit(uri)
        for secret in (parsed.password, parsed.username, parsed.hostname, parsed.netloc):
            if secret and len(secret) >= 3:
                msg = msg.replace(secret, "***")
    return re.sub(r"mongodb(?:\+srv)?://[^\s,;'\"]+", lambda m: _mask_uri(m.group(0)), msg)


def _is_mongo_error(exc: BaseException) -> bool:
    pymongo_errors = sys.modules.get("pymongo.errors")
    if pymongo_errors is not None and isinstance(exc, pymongo_errors.PyMongoError):
        return True
    return type(exc).__name__ in _MONGO_ERR_NAMES


def _rel_output_dir(output_dir: str | Path | None, out_root: Path) -> str | None:
    if output_dir is None:
        return None
    try:
        return str(Path(output_dir).resolve().relative_to(out_root.resolve()))
    except ValueError:
        return str(output_dir)


class _CoordSession:
    """Optional MongoDB coordination wrapper with lazy import and 60s fallback retry."""

    def __init__(self, out_root: Path, *, coord: Any | None = None, now: Callable[[], float] = time.time):
        self.out_root = out_root
        self.uri = os.environ.get("CLIPES_MONGO_URI", "").strip() or None
        self.coord = coord
        self.now = now
        self.configured = coord is not None or self.uri is not None
        self.online = False
        self.next_retry = 0.0

    @property
    def active(self) -> Any | None:
        return self.coord if self.online else None

    def connect(self) -> bool:
        if not self.configured:
            return False
        try:
            if self.coord is None:
                import pymongo
                from .capacidade import worker_id as get_worker_id
                from .coord import Coord

                client = pymongo.MongoClient(self.uri, maxPoolSize=5, serverSelectionTimeoutMS=5000)
                db = client.get_default_database(default="clipes_lives")
                lease_s = float(os.environ.get("CLIPES_LEASE_S", "90"))
                self.coord = Coord(db, get_worker_id(self.out_root), now=self.now, lease_s=lease_s)
            self.coord.ensure_indexes()
        except Exception as exc:
            if _is_mongo_error(exc) or isinstance(exc, OSError):
                self.mark_offline(exc)
                return False
            raise
        self.online = True
        self.next_retry = 0.0
        return True

    def mark_offline(self, exc: BaseException) -> None:
        self.online = False
        self.next_retry = self.now() + MONGO_RECONNECT_S
        print(f"[fila] Mongo indisponivel ({_sanitize_error(exc, self.uri)}); seguindo local", flush=True)

    def maybe_reconnect(self) -> bool:
        if self.configured and not self.online and self.now() >= self.next_retry:
            return self.connect()
        return self.online


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _policy_status_text(policy: ChannelPolicy, channel: str, ready_at: Any) -> str:
    if channel and not policy.permits(channel):
        return "bloqueado pela policy"
    delay_h = policy.delay_h.get(channel, 0.0)
    if delay_h > 0 and ready_at is not None:
        ready_str = datetime.fromtimestamp(float(ready_at), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"aguardar ate {ready_str} (delay {delay_h:g}h)"
    return "liberado"


def _format_clip_discord_message(
    clip: dict[str, Any],
    clip_id: str,
    policy_status: str,
    too_large_path: Path | None = None,
) -> str:
    title = str(clip.get("title") or clip_id)
    hook = str(clip.get("hook_top") or "-")
    score_val = clip.get("llm_score") if clip.get("llm_score") is not None else clip.get("score")
    score_txt = f"{score_val}" if score_val is not None else "-"
    ai_src = str(clip.get("title_source") or clip.get("description_source") or clip.get("llm_reason") or "-")
    caption = str(clip.get("caption") or "-")
    desc_long = str(clip.get("description_long") or "-")

    head = (
        f"**{title}** (`{clip_id}`)\n"
        f"Gancho: {hook}\n"
        f"Nota/IA: {score_txt} ({ai_src})\n"
        f"Legenda: {caption}\n"
    )
    tail = f"Policy: {policy_status}\nPara publicar: `publicar {clip_id}`"
    if too_large_path is not None:
        tail = f"arquivo grande demais para o Discord: `{too_large_path}`\n" + tail

    budget = 2000 - len(head) - len(tail) - len("Descricao: \n")
    if budget < 20:
        desc_part = ""
    elif len(desc_long) > budget:
        desc_part = f"Descricao: {desc_long[: max(0, budget - 3)]}...\n"
    else:
        desc_part = f"Descricao: {desc_long}\n"
    return (head + desc_part + tail)[:2000]


def _notify_discord_job(
    completed_dir: Path,
    job: dict[str, Any],
    result: dict[str, Any],
    policy: ChannelPolicy,
    *,
    discord_client: Any | None = None,
    discord_channel_id: str | None = None,
    preview_fn: Callable[..., Path | None] | None = None,
) -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        return
    target_channel = (discord_channel_id or os.environ.get("DISCORD_CHANNEL_ID", "")).strip()
    if not target_channel:
        return

    client = discord_client
    if client is None:
        from .discord_rest import DiscordRest

        client = DiscordRest(token)

    out_dir_raw = result.get("output_dir")
    out_dir = Path(out_dir_raw) if out_dir_raw else None
    clips_json_path = (out_dir / "clips.json") if out_dir is not None else None

    manifest: dict[str, Any] | None = None
    if clips_json_path is not None and clips_json_path.is_file():
        manifest = json.loads(clips_json_path.read_text(encoding="utf-8"))
        clips = list(manifest.get("clips") or [])
    else:
        clips = list(result.get("clips") or [])

    if not clips or all(isinstance(c.get("discord"), dict) and c["discord"].get("message_id") for c in clips):
        return

    completed_job = _read_job(completed_dir) if (completed_dir / "job.json").is_file() else dict(job)
    channel_name = str(job.get("channel") or (manifest or {}).get("channel") or "")
    live_title = str((manifest or {}).get("title") or job.get("title") or job.get("job_id") or channel_name)
    live_url = str((manifest or {}).get("url") or job.get("url") or "")
    video_id = str((manifest or {}).get("video_id") or result.get("video_id") or job.get("job_id") or "clip")
    policy_status = _policy_status_text(policy, channel_name, completed_job.get("ready_at"))

    thread_id = (
        ((manifest or {}).get("discord") or {}).get("thread_id")
        or ((completed_job.get("discord") or {}).get("thread_id"))
        or next(
            (c["discord"]["thread_id"] for c in clips if isinstance(c.get("discord"), dict) and c["discord"].get("thread_id")),
            None,
        )
    )

    try:
        if not thread_id:
            header = f"**{channel_name}** — {live_title}\nClipes: {len(clips)}"
            if live_url:
                header += f"\n{live_url}"
            header_resp = client.post(target_channel, header[:2000])
            header_msg_id = str(header_resp["id"])
            thread_name = f"{channel_name} · {live_title[:70]}" if channel_name else live_title[:70]
            thread_id = str(client.thread_from(target_channel, header_msg_id, thread_name, auto_archive_duration=1440))

            if manifest is not None and clips_json_path is not None:
                manifest_discord = dict(manifest.get("discord") or {})
                manifest_discord.update(thread_id=thread_id, header_message_id=header_msg_id)
                manifest["discord"] = manifest_discord
                _write_json_atomic(clips_json_path, manifest)

            job_discord = dict(completed_job.get("discord") or {})
            job_discord.update(thread_id=thread_id, header_message_id=header_msg_id)
            completed_job["discord"] = job_discord
            _write_job(completed_dir, completed_job)

        for idx, clip in enumerate(clips, start=1):
            try:
                if isinstance(clip.get("discord"), dict) and clip["discord"].get("message_id"):
                    continue
                clip_id = str(clip.get("clip_id") or f"{video_id}_c{int(clip.get('index', idx)):02d}")
                file_rel = clip.get("file")
                mp4_path = (out_dir / file_rel) if (out_dir is not None and file_rel) else None

                attach_path: Path | None = None
                too_large_path: Path | None = None
                if mp4_path is not None and mp4_path.is_file():
                    preview_impl = preview_fn
                    if preview_impl is None:
                        from .previa import preview_for_discord as preview_impl
                    duration = float(clip.get("rendered_duration") or clip.get("duration") or 60.0)
                    attach_path = preview_impl(mp4_path, duration, out_dir / ".cache")
                    if attach_path is None:
                        too_large_path = mp4_path

                msg_text = _format_clip_discord_message(clip, clip_id, policy_status, too_large_path=too_large_path)
                msg_resp = client.post(thread_id, msg_text, file=attach_path)
                msg_id = str(msg_resp["id"])

                clip["discord"] = {"thread_id": thread_id, "message_id": msg_id}
                if manifest is not None and clips_json_path is not None:
                    manifest["clips"] = clips
                    _write_json_atomic(clips_json_path, manifest)

                job_discord = dict(completed_job.get("discord") or {})
                job_clips = dict(job_discord.get("clips") or {})
                job_clips[clip_id] = msg_id
                job_discord["thread_id"] = thread_id
                job_discord["clips"] = job_clips
                completed_job["discord"] = job_discord
                _write_job(completed_dir, completed_job)
            except Exception as exc:
                err_text = f"{type(exc).__name__}: {exc}".replace(token, "***")
                completed_job["discord_error"] = err_text[-500:]
                _write_job(completed_dir, completed_job)
                print(f"[discord] falha ao enviar previa ({err_text[-200:]})", flush=True)
    except Exception as exc:
        err_text = f"{type(exc).__name__}: {exc}".replace(token, "***")
        completed_job["discord_error"] = err_text[-500:]
        _write_job(completed_dir, completed_job)
        print(f"[discord] falha ao enviar previa ({err_text[-200:]})", flush=True)


def run_worker_step(
    queue: DiskQueue,
    out_root: Path,
    policy: ChannelPolicy,
    pipeline: Callable[[Path, Path], dict[str, Any]] = run_local_pipeline,
    *,
    coord: Any | None = None,
    on_mongo_error: Callable[[BaseException], None] | None = None,
    lost_lives: set[str] | None = None,
    on_job_active: Callable[[str | None], None] | None = None,
    discord_client: Any | None = None,
    discord_channel_id: str | None = None,
    preview_fn: Callable[..., Path | None] | None = None,
) -> bool:
    """Process one pending job end to end (never publishes). False when the queue is empty."""
    job_dir = queue.claim_next()
    if job_dir is None:
        return False
    job = _read_job(job_dir)
    channel = job["channel"]
    mongo_job_id = job.get("mongo_job_id") or job["job_id"]
    live_id = job.get("live_id")
    use_coord = coord if (coord is not None and job.get("coord_synced") is not False) else None

    def report_mongo_error(exc: BaseException) -> None:
        if on_mongo_error is not None:
            on_mongo_error(exc)
        else:
            uri = os.environ.get("CLIPES_MONGO_URI", "").strip() or None
            print(f"[fila] erro Mongo ({_sanitize_error(exc, uri)}); seguindo local", flush=True)

    if not policy.permits(channel):
        if use_coord is not None:
            try:
                if use_coord.start_job(mongo_job_id):
                    use_coord.finish_job(mongo_job_id, "rejected", {"reject_reason": "blocked_by_policy"})
            except Exception as exc:
                if not _is_mongo_error(exc):
                    raise
                report_mongo_error(exc)
        queue.complete(job_dir, "rejected", {"reject_reason": "blocked_by_policy"})
        return True

    if use_coord is not None:
        try:
            started_ok = use_coord.start_job(mongo_job_id)
        except Exception as exc:
            if not _is_mongo_error(exc):
                raise
            report_mongo_error(exc)
            use_coord = None
        else:
            if not started_ok:
                queue.complete(job_dir, "rejected", {"reject_reason": "coord_denied"})
                return True

    started = time.perf_counter()
    if on_job_active is not None:
        on_job_active(mongo_job_id)
    try:
        result = pipeline(job_dir, out_root)
    except Exception as exc:  # a broken job must not stop the worker
        queue.complete(job_dir, "rejected", {
            "reject_reason": "pipeline_error",
            "error": f"{type(exc).__name__}: {exc}"[-500:],
            "worker_s": round(time.perf_counter() - started, 2),
        })
        if use_coord is not None and not (lost_lives and live_id in lost_lives):
            try:
                use_coord.finish_job(mongo_job_id, "rejected", {"reject_reason": "pipeline_error"})
            except Exception as mexc:
                if not _is_mongo_error(mexc):
                    raise
                report_mongo_error(mexc)
        return True
    finally:
        if on_job_active is not None:
            on_job_active(None)

    clips = result.get("clips") or []
    final_status = "done" if clips else "rejected"
    score = max((float(c["score"]) for c in clips), default=0.0)
    completed_dir = queue.complete(job_dir, final_status, {
        "ready_at": job["created_at"] + policy.delay_h.get(channel, 0.0) * 3600,
        "score": score,
        "clips": len(clips),
        "output_dir": result.get("output_dir"),
        "timings": {**result.get("timings", {}), "worker_s": round(time.perf_counter() - started, 2)},
    })
    if use_coord is not None and not (lost_lives and live_id in lost_lives):
        mongo_res = {"clips": len(clips), "score": score, "output_dir": _rel_output_dir(result.get("output_dir"), out_root)}
        try:
            use_coord.finish_job(mongo_job_id, final_status, mongo_res)
        except Exception as mexc:
            if not _is_mongo_error(mexc):
                raise
            report_mongo_error(mexc)
    if final_status == "done":
        _notify_discord_job(
            completed_dir,
            job,
            result,
            policy,
            discord_client=discord_client,
            discord_channel_id=discord_channel_id,
            preview_fn=preview_fn,
        )
    return True


# --- follow_live: one live clipped in aligned windows until it ends ------------------------------

LIVE_STEP_S = 300
LIVE_WINDOW_S = 600
LIVE_NO_SEGMENT_S = 90.0
LIVE_END_PERSIST_S = 300.0
LIVE_MAX_BACKLOG = 2
LIVE_IS_LIVE_EVERY_S = 60.0


def _resolve_stream_id(
    channel: str,
    stream_ids: dict[str, str] | None = None,
    probe: Callable[[str], dict[str, Any]] = probe_metadata,
) -> str | None:
    if stream_ids and channel in stream_ids:
        return str(stream_ids[channel])
    if channel in _helix_stream_ids:
        return _helix_stream_ids[channel]
    try:
        meta = probe(f"https://www.twitch.tv/{channel}")
    except Exception:
        return None
    if isinstance(meta, dict) and meta.get("id"):
        return str(meta["id"])
    return None


async def _wait_claim(
    wait_s: float,
    *,
    now: Callable[[], float],
    sleep: Callable[[float], Any],
    stop: asyncio.Event | None = None,
) -> None:
    deadline = now() + wait_s
    while now() < deadline and not (stop is not None and stop.is_set()):
        t_before = now()
        await sleep(deadline - t_before)
        if now() <= t_before:
            break


def _wait_claim_sync(wait_s: float, *, now: Callable[[], float], sleep: Callable[[float], Any]) -> None:
    deadline = now() + wait_s
    while now() < deadline:
        t_before = now()
        sleep(deadline - t_before)
        if now() <= t_before:
            break


def _live_channel(url_or_channel: str) -> str:
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", url_or_channel)
    return (m.group(1) if m else url_or_channel).strip().lower().lstrip("@")


def follow_live(
    url_or_channel: str,
    out_root: Path,
    *,
    queue_root: Path | None = None,
    pool: RollingRecorderPool | None = None,
    pipeline: Callable[[Path, Path], dict[str, Any]] = run_local_pipeline,
    is_live: Callable[[str], bool] | None = None,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], Any] = time.sleep,
    disk_usage: Callable[[Any], Any] = shutil.disk_usage,
    step: int = LIVE_STEP_S,
    max_dur: float = 60.0,
    min_free_gb: float | None = None,
    coord: Any | None = None,
    stream_id: str | None = None,
    capacity_fn: Callable[[Path], dict[str, Any]] | None = None,
    max_lives_fn: Callable[[Path, int | None], int] | None = None,
    probe: Callable[[str], dict[str, Any]] = probe_metadata,
    policy: ChannelPolicy | None = None,
) -> dict[str, Any]:
    """Clip one live in `step`-aligned windows [e-600, e] until it ends; returns {"status", "jobs"}.

    Each window becomes a job; only clips whose center lies in the window's owned region and that
    were not already emitted (absolute time, emitted file) survive. `source.mkv` is deleted after
    each window. More than LIVE_MAX_BACKLOG pending windows: the oldest is marked `lost`.
    """
    channel = _live_channel(url_or_channel)
    active_policy = policy or ChannelPolicy()
    if not CHANNEL_RE.fullmatch(channel) or not active_policy.permits(channel):
        return {"status": "blocked", "jobs": []}
    url = f"https://www.twitch.tv/{channel}"
    out_root.mkdir(parents=True, exist_ok=True)
    min_free = float(os.environ.get("CLIPES_MIN_FREE_GB", "5") if min_free_gb is None else min_free_gb)
    pad = max_dur / 2
    queue = DiskQueue(queue_root or out_root / "queue")
    pool = pool or RollingRecorderPool(queue.root / ".buffer", max_hot=0, max_on_demand=1,
                                       buffer_seconds=LIVE_WINDOW_S + 2 * step)
    is_live = is_live or (lambda u: bool(probe_metadata(u).get("is_live")))
    emitted_path = out_root / f".emitted_{sanitize_id(channel)}.json"
    jobs: list[str] = []

    live_id: str | None = None
    cap_fn = capacity_fn
    ml_fn = max_lives_fn
    worker_alias = os.environ.get("CLIPES_ALIAS", "").strip()
    beat_interval = max(1.0, float(os.environ.get("CLIPES_BEAT_S", "20")))
    if coord is not None:
        from .capacidade import capacity as default_cap, worker_id as get_worker_id
        from .perfil import max_lives as default_max_lives

        cap_fn = cap_fn or default_cap
        ml_fn = ml_fn or default_max_lives
        if not worker_alias:
            worker_alias = get_worker_id(out_root)
        cap = cap_fn(out_root)
        allowed = ml_fn(out_root, cap.get("cpus"))
        if allowed < 1:
            return {"status": "not_claimed", "jobs": jobs}
        sid = stream_id or _resolve_stream_id(channel, probe=probe)
        if not sid:
            return {"status": "not_claimed", "jobs": jobs}
        live_id = f"twitch:{channel}:{sid}"
        if hasattr(coord, "db") and hasattr(coord, "worker_id"):
            owned_count = coord.db.lives.count_documents({
                "_id": {"$ne": live_id},
                "owner": coord.worker_id,
                "status": "live",
            })
            if owned_count >= allowed:
                return {"status": "not_claimed", "jobs": jobs}
        coord.ensure_live(live_id, channel=channel, platform="twitch", stream_id=sid)
        _wait_claim_sync(30.0 / max(1.0, float(cap.get("score") or 1)), now=now, sleep=sleep)
        if not coord.claim_live(live_id):
            return {"status": "not_claimed", "jobs": jobs}

    mongo_offline_until = float("-inf")
    current_job: list[str | None] = [None]
    lost_lives: set[str] = set()

    def _coord_active() -> Any | None:
        if coord is None or live_id is None or now() < mongo_offline_until:
            return None
        return coord

    def _on_mongo_err(exc: BaseException) -> None:
        nonlocal mongo_offline_until
        mongo_offline_until = now() + MONGO_RECONNECT_S
        uri = os.environ.get("CLIPES_MONGO_URI", "").strip() or None
        print(f"[fila] Mongo indisponivel ({_sanitize_error(exc, uri)}); seguindo local", flush=True)

    def pending() -> list[Path]:
        found = []
        for d in (queue.root / "pending").iterdir():
            try:
                if _read_job(d).get("channel") == channel:
                    found.append(d)
            except (OSError, ValueError):
                continue
        return sorted(found, key=lambda d: d.name)

    def live_pipeline(job_dir: Path, root: Path) -> dict[str, Any]:
        job = _read_job(job_dir)
        result = pipeline(job_dir, root)
        vs = job["video_start"]
        mine = [c for c in result.get("clips") or []
                if owns(vs + (c["start"] + c["end"]) / 2, job["window_start"], job["window_end"], step, pad, job["last"])]
        keep = filter_emitted([(vs + c["start"], vs + c["end"]) for c in mine], emitted_path)
        return {**result, "clips": [c for c, ok in zip(mine, keep) if ok]}

    def close_window(e: float, last: bool) -> None:
        start_ts = e - LIVE_WINDOW_S
        job_id = sanitize_id(f"{channel}_{int(e)}")
        job_dir = queue.root / ".staging" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job = {"job_id": job_id, "channel": channel, "url": url, "peak_ts": e, "z_score": 0.0,
               "created_at": now(), "ready_at": None, "score": None, "status": "pending",
               "window_start": start_ts, "window_end": e, "last": last, "video_start": start_ts}
        duration = 0.0
        try:
            job["video_start"] = pool.video_start_ts(channel, start_ts, e)
            pool.freeze_and_concat(channel, start_ts, e, job_dir / "source.mkv")
            duration = probe_duration(job_dir / "source.mkv")
        except ValueError:
            job.update(status="rejected", reject_reason="no_segments")
        else:
            if duration < MIN_CLIP_SOURCE_S:
                job.update(status="rejected", reject_reason="too_short")
        if job["status"] == "rejected":
            (job_dir / "source.mkv").unlink(missing_ok=True)
        (job_dir / "meta.json").write_text(json.dumps(
            {"id": job_id, "title": f"{channel} - live", "url": url, "extractor": "twitch",
             "channel": channel, "is_live": False, "duration": duration}, ensure_ascii=False), encoding="utf-8")

        c_active = _coord_active()
        if c_active is not None and job["status"] != "rejected":
            mongo_job_id = f"{live_id}:{int(e)}"
            try:
                added = c_active.add_job(mongo_job_id, live_id, (start_ts, e), 0.0)
            except Exception as exc:
                if not _is_mongo_error(exc):
                    raise
                _on_mongo_err(exc)
                job["coord_synced"] = False
            else:
                if not added:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    return
                job.update(live_id=live_id, mongo_job_id=mongo_job_id, coord_synced=True)
        elif coord is not None:
            job["coord_synced"] = False

        _write_job(job_dir, job)
        queue.enqueue(job_dir)
        jobs.append(job_id)
        pool.hold(channel, e + step - LIVE_WINDOW_S)
        backlog = pending()
        if len(backlog) > LIVE_MAX_BACKLOG:
            print(f"[live] backlog {len(backlog)} > {LIVE_MAX_BACKLOG}: {backlog[0].name} lost", flush=True)
            lost_dir = backlog[0]
            try:
                lost_job = _read_job(lost_dir)
            except (OSError, ValueError):
                lost_job = {}
            queue.complete(lost_dir, "rejected", {"reject_reason": "lost", "lost": True})
            c_lost = _coord_active()
            if c_lost is not None and lost_job.get("mongo_job_id"):
                try:
                    if hasattr(c_lost, "mark_job_lost"):
                        c_lost.mark_job_lost(lost_job["mongo_job_id"])
                    elif hasattr(c_lost, "db"):
                        c_lost.db.jobs.update_one(
                            {
                                "_id": lost_job["mongo_job_id"],
                                "owner": c_lost.worker_id,
                                "status": {"$in": ["pending", "processing"]},
                            },
                            {"$set": {"status": "lost", "updated_at": c_lost._dt()}},
                        )
                except Exception as exc:
                    if not _is_mongo_error(exc):
                        raise
                    _on_mongo_err(exc)

    def process_one() -> bool:
        return run_worker_step(
            queue,
            out_root,
            active_policy,
            live_pipeline,
            coord=_coord_active(),
            on_mongo_error=_on_mongo_err,
            lost_lives=lost_lives,
            on_job_active=lambda jid: current_job.__setitem__(0, jid),
        )

    live_ended = False
    lease_lost = False
    last_beat = float("-inf")
    try:
        if not pool.start_on_demand(channel):
            raise RuntimeError(f"nao foi possivel gravar {channel}")
        t0 = now()
        next_e = window_end(t0, step) + step
        last_check = t0 - LIVE_IS_LIVE_EVERY_S
        live_ok = True
        since: dict[str, float] = {}
        while True:
            t = now()
            c_active = _coord_active()
            if c_active is not None and t - last_beat >= beat_interval:
                last_beat = t
                cap_now = cap_fn(out_root) if cap_fn is not None else {"cpus": 1, "score": 1}
                ml_now = max(1, int(ml_fn(out_root, cap_now.get("cpus")) if ml_fn is not None else 1))
                razao_now = _read_perfil_razao(out_root, cap_now.get("cpus"))
                try:
                    c_active.beat(worker_alias, cap_now, current_job[0], ml_now, razao_now)
                    renewed = c_active.renew(live_id)
                except Exception as exc:
                    if not _is_mongo_error(exc):
                        raise
                    _on_mongo_err(exc)
                else:
                    if not renewed:
                        print(f"[fila] lease perdida {live_id}", flush=True)
                        lost_lives.add(live_id)
                        if hasattr(pool, "stop_channel"):
                            pool.stop_channel(channel)
                        else:
                            pool.release_on_demand(channel)
                        lease_lost = True
                        live_ended = False
                        break
            if t - last_check >= LIVE_IS_LIVE_EVERY_S:
                last_check = t
                try:
                    live_ok = is_live(url)
                except Exception:  # unknown is not an end signal
                    live_ok = True
            segs = pool.segments(channel)
            newest = segs[-1][0] + SEGMENT_S if segs else t0
            signals = {"exited": not pool.is_recording(channel), "silent": t - newest >= LIVE_NO_SEGMENT_S,
                       "offline": not live_ok}
            for k, v in signals.items():
                if v:
                    since.setdefault(k, t)
                else:
                    since.pop(k, None)
            if len(since) >= 2 or any(t - v >= LIVE_END_PERSIST_S for v in since.values()):
                live_ended = True
                break
            if t >= next_e:
                if disk_usage(out_root).free < min_free * 1e9:
                    print("[live] janela pulada: disco", flush=True)
                else:
                    close_window(float(next_e), last=False)
                next_e += step
                continue
            if not (pending() and process_one()):
                sleep(1.0)
            pool.cleanup(t)
        if not lease_lost:
            # end: close the tail (>= MIN_CLIP_SOURCE_S past the last boundary), then drain this live's queue
            segs = pool.segments(channel)
            e = min(now(), segs[-1][0] + SEGMENT_S) if segs else 0.0
            if e - (next_e - step) >= MIN_CLIP_SOURCE_S:
                close_window(e, last=True)
            while pending():
                process_one()
    finally:
        pool.release_hold(channel)
        pool.release_on_demand(channel)
        if coord is not None and live_id is not None:
            try:
                coord.release(live_id, live_ended)
            except Exception as exc:
                if not _is_mongo_error(exc):
                    raise
    return {"status": "lease_lost" if lease_lost else "ended", "jobs": jobs}


# --- main loop and `python -m clipes monitor` entrypoint ------------------------------------------

BUFFER_CAP_BYTES = 1_500_000_000  # global ceiling for queue/.buffer
RANK_INTERVAL_S = 10.0
WORKER_IDLE_S = 5.0


def _lock_worker(queue_root: Path):
    """Exclusive flock on queue_root/.worker.lock: a second monitor on the same queue fails fast."""
    import fcntl

    lock = open(queue_root / ".worker.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError(f"another monitor worker already holds {queue_root}") from None
    return lock


def _terminate_child_processes(
    proc_root: Path = Path("/proc"),
    *,
    kill_fn: Callable[[int, int], None] = os.kill,
) -> None:
    """Send SIGTERM to all descendant PIDs of the current process (ffmpeg, streamlink, llama-server)."""
    me = os.getpid()
    children: dict[int, list[int]] = {}
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="ignore")
            rparen = stat.rfind(")")
            if rparen < 0:
                continue
            ppid = int(stat[rparen + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(pid)

    stack = list(children.get(me, ()))
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid == me or pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, ()))
        try:
            kill_fn(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _read_perfil_razao(out_root: Path, cpus: int | None) -> float:
    try:
        data = json.loads((out_root / ".perfil.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return 0.0
    if not isinstance(data, dict) or (cpus is not None and data.get("cpus") != cpus):
        return 0.0
    razao = data.get("razao")
    if isinstance(razao, (int, float)) and not isinstance(razao, bool) and razao > 0:
        return float(razao)
    return 0.0


async def run_monitor(
    channels: list[str],
    out_root: Path,
    policy: ChannelPolicy,
    *,
    queue_root: Path | None = None,
    max_recorders: int = 3,
    dry_run: bool = False,
    irc=irc_chat_loop,
    popen=subprocess.Popen,
    pipeline: Callable[[Path, Path], dict[str, Any]] = run_local_pipeline,
    now=time.time,
    sleep=asyncio.sleep,
    stop: asyncio.Event | None = None,
    coord: Any | None = None,
    capacity_fn: Callable[[Path], dict[str, Any]] | None = None,
    max_lives_fn: Callable[[Path, int | None], int] | None = None,
    stream_ids: dict[str, str] | None = None,
    probe: Callable[[str], dict[str, Any]] = probe_metadata,
    beat_s: float | None = None,
    alias: str | None = None,
    kill_children: Callable[[], None] | None = None,
    discord_client: Any | None = None,
    discord_channel_id: str | None = None,
    preview_fn: Callable[..., Path | None] | None = None,
    run_pending_fn: Callable[..., list[dict[str, Any]]] | None = None,
) -> None:
    """IRC -> ChannelSpikeDetector -> rolling recorder -> capture_job -> DiskQueue, plus 1 sequential worker.

    dry_run only reads IRC and logs spikes: no recording, no queue, no worker.
    """
    stop = stop or asyncio.Event()
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)  # docker stop: shut recorders down
    except (NotImplementedError, RuntimeError, ValueError):
        pass
    channels = [c for c in channels if policy.permits(c)][:MAX_CHANNELS]
    # Real IRC: each channel warms up from its own JOIN. Test fakes have no JOIN, so they count from now.
    real_irc = irc is irc_chat_loop
    detectors = {c: ChannelSpikeDetector(c, float("inf") if real_irc else now()) for c in channels}
    capturing: set[str] = set()
    captures: set[asyncio.Task] = set()
    owned_lives: dict[str, str] = {}
    claiming_tasks: dict[str, asyncio.Task[bool]] = {}
    claim_retry_at: dict[str, float] = {}
    lost_lives: set[str] = set()

    def on_join(channel: str, t: float) -> None:
        detector = detectors.get(channel)
        if detector is not None:
            detector.start_watching(t)

    def on_message(channel: str, t: float, author: str, text: str) -> None:
        detector = detectors.get(channel)
        if detector is not None:
            detector.record_message(t, author, text)

    queue = pool = lock = coord_session = None
    cap_getter = capacity_fn
    max_lives_getter = max_lives_fn
    worker_alias = alias or os.environ.get("CLIPES_ALIAS", "").strip()
    beat_interval = max(1.0, float(os.environ.get("CLIPES_BEAT_S", "20") if beat_s is None else beat_s))
    child_killer = kill_children or _terminate_child_processes
    current_job: list[str | None] = [None]
    if not dry_run:
        queue_root = queue_root or out_root / "queue"
        queue = DiskQueue(queue_root)
        lock = _lock_worker(queue_root)
        queue.recover_orphans()
        staging = queue_root / ".staging"  # same filesystem as the queue, so enqueue is an os.replace
        staging.mkdir(exist_ok=True)
        shutil.rmtree(queue_root / ".buffer", ignore_errors=True)  # segments of a previous run are stale
        pool = RollingRecorderPool(queue_root / ".buffer", max_hot=max(0, min(max_recorders, 3)), popen=popen)
        coord_session = _CoordSession(out_root, coord=coord, now=now)
        if coord_session.configured:
            if cap_getter is None:
                from .capacidade import capacity as cap_getter
            if max_lives_getter is None:
                from .perfil import max_lives as max_lives_getter
            if not worker_alias:
                from .capacidade import worker_id as get_worker_id
                worker_alias = get_worker_id(out_root)
            coord_session.connect()

    def _current_limits() -> tuple[dict[str, Any], int]:
        cap = cap_getter(out_root) if cap_getter is not None else {"cpus": 1, "score": 1}
        ml = max_lives_getter(out_root, cap.get("cpus")) if max_lives_getter is not None else 1
        return cap, max(1, int(ml))

    async def _current_limits_async() -> tuple[dict[str, Any], int]:
        return await asyncio.to_thread(_current_limits)

    async def _claim_live(channel: str) -> bool:
        try:
            active_coord = coord_session.active if coord_session is not None else None
            if active_coord is None:
                return True
            if channel in owned_lives:
                return True
            cap, ml = await _current_limits_async()
            if len(owned_lives) >= ml:
                return False
            sid = await asyncio.to_thread(_resolve_stream_id, channel, stream_ids, probe)
            if not sid:
                claim_retry_at[channel] = now() + 30.0
                return False
            live_id = f"twitch:{channel}:{sid}"
            try:
                await asyncio.to_thread(
                    active_coord.ensure_live, live_id, channel=channel, platform="twitch", stream_id=sid
                )
            except Exception as exc:
                if not _is_mongo_error(exc):
                    raise
                coord_session.mark_offline(exc)
                return True
            await _wait_claim(30.0 / max(1.0, float(cap.get("score") or 1)), now=now, sleep=sleep, stop=stop)
            if stop.is_set():
                return False
            active_coord = coord_session.active
            if active_coord is None:
                return True
            _, ml = await _current_limits_async()
            if channel in owned_lives:
                return True
            if len(owned_lives) >= ml:
                return False
            try:
                claimed = await asyncio.to_thread(active_coord.claim_live, live_id)
            except Exception as exc:
                if not _is_mongo_error(exc):
                    raise
                coord_session.mark_offline(exc)
                return True
            if claimed:
                owned_lives[channel] = live_id
                lost_lives.discard(live_id)
                return True
            claim_retry_at[channel] = now() + 30.0
            return False
        finally:
            claiming_tasks.pop(channel, None)

    async def ensure_claimed(channel: str) -> bool:
        if coord_session is None or coord_session.active is None:
            return True
        if channel in owned_lives:
            return True
        if channel in claiming_tasks:
            return await claiming_tasks[channel]
        if now() < claim_retry_at.get(channel, 0.0):
            return False
        task = asyncio.create_task(_claim_live(channel))
        claiming_tasks[channel] = task
        return await task

    async def capture_and_enqueue(event: SpikeEvent) -> None:
        try:
            if not await ensure_claimed(event.channel):
                return
            job_dir = await capture_job(pool, detectors[event.channel], event, staging, now=now, sleep=sleep)
            if job_dir is None:
                print(f"[monitor] no free recorder for {event.channel}; spike dropped", flush=True)
            else:
                if coord_session is not None and coord_session.configured:
                    job = _read_job(job_dir)
                    channel = job["channel"]
                    live_id = owned_lives.get(channel) or job.get("live_id") or f"twitch:{channel}"
                    active_coord = coord_session.active
                    if active_coord is not None and job.get("status") != "rejected" and live_id not in lost_lives:
                        w_end = int(job.get("window_end") or job.get("created_at") or now())
                        w_start = int(job.get("window_start") or (w_end - PRE_ROLL_S))
                        mongo_job_id = job.get("mongo_job_id") or f"{live_id}:{w_end}"
                        try:
                            await asyncio.to_thread(
                                active_coord.add_job,
                                mongo_job_id,
                                live_id,
                                (w_start, w_end),
                                float(job.get("z_score") or 0.0),
                            )
                        except Exception as exc:
                            if not _is_mongo_error(exc):
                                raise
                            coord_session.mark_offline(exc)
                            job["coord_synced"] = False
                        else:
                            job.update(live_id=live_id, mongo_job_id=mongo_job_id, coord_synced=True)
                        _write_job(job_dir, job)
                    elif job.get("status") != "rejected":
                        job["coord_synced"] = False
                        _write_job(job_dir, job)
                print(f"[monitor] queued {queue.enqueue(job_dir)}", flush=True)
        except Exception as exc:  # one failed capture must not stop the monitor
            print(f"[monitor] capture failed for {event.channel}: {type(exc).__name__}: {exc}", flush=True)
        finally:
            capturing.discard(event.channel)

    async def tick_loop() -> None:
        last_rank = float("-inf")
        while not stop.is_set():
            t = now()
            if coord_session is not None and coord_session.configured and not coord_session.online and t >= coord_session.next_retry:
                await asyncio.to_thread(coord_session.maybe_reconnect)
            for detector in detectors.values():
                event = detector.check_spike(t)
                if event is None:
                    continue
                print(f"[monitor] spike {event.channel} count10={event.count10} authors={event.unique_authors} "
                      f"expected={event.expected:.1f} z={event.z_score:.2f}", flush=True)
                if pool is not None and event.channel not in capturing:
                    capturing.add(event.channel)
                    task = asyncio.create_task(capture_and_enqueue(event))
                    captures.add(task)
                    task.add_done_callback(captures.discard)
            if pool is not None and t - last_rank >= RANK_INTERVAL_S:
                def recent(c: str) -> int:  # only the last 60s: an offline channel must not stay "hot" and re-arm its recorder
                    return sum(1 for m in detectors[c].messages if m[0] >= t - 60.0)
                active = sorted((c for c in detectors if recent(c) > 0), key=lambda c: -recent(c))
                if coord_session is not None and coord_session.active is not None:
                    _, ml = await _current_limits_async()
                    for c in active:
                        if (c not in owned_lives and c not in claiming_tasks
                                and len(owned_lives) < ml and t >= claim_retry_at.get(c, 0.0)):
                            task = asyncio.create_task(ensure_claimed(c))
                            captures.add(task)
                            task.add_done_callback(captures.discard)
                    hot_targets = [c for c in active if c in owned_lives]
                else:
                    hot_targets = active
                pool.set_hot(hot_targets)
                if coord_session is not None and coord_session.active is not None:
                    for c in list(owned_lives):
                        if c not in pool.recordings and c not in capturing:
                            live_id = owned_lives.pop(c)
                            try:
                                await asyncio.to_thread(coord_session.active.release, live_id, False)
                            except Exception as exc:
                                if _is_mongo_error(exc):
                                    coord_session.mark_offline(exc)
                                    break
                pool.cleanup(t)
                pool.enforce_cap(BUFFER_CAP_BYTES)
                last_rank = t
            await sleep(1.0)

    async def worker_loop() -> None:
        while not stop.is_set():
            active_coord = coord_session.active if coord_session is not None else None
            on_err = coord_session.mark_offline if coord_session is not None else None
            if not await asyncio.to_thread(
                run_worker_step, queue, out_root, policy, pipeline,
                coord=active_coord, on_mongo_error=on_err, lost_lives=lost_lives,
                on_job_active=lambda jid: current_job.__setitem__(0, jid),
                discord_client=discord_client, discord_channel_id=discord_channel_id,
                preview_fn=preview_fn,
            ):
                await sleep(WORKER_IDLE_S)

    async def heartbeat_loop() -> None:
        last_beat = float("-inf")
        while not stop.is_set():
            t = now()
            active_coord = coord_session.active if coord_session is not None else None
            if active_coord is not None and t - last_beat >= beat_interval:
                last_beat = t
                cap, ml = await _current_limits_async()
                razao = _read_perfil_razao(out_root, cap.get("cpus"))
                try:
                    await asyncio.to_thread(
                        active_coord.beat, worker_alias, cap, current_job[0], ml, razao
                    )
                    for ch, live_id in list(owned_lives.items()):
                        ok = await asyncio.to_thread(active_coord.renew, live_id)
                        if not ok:
                            print(f"[fila] lease perdida {live_id}", flush=True)
                            owned_lives.pop(ch, None)
                            lost_lives.add(live_id)
                            if pool is not None:
                                pool.stop_channel(ch)
                    cmd = await asyncio.to_thread(active_coord.pending_command)
                except Exception as exc:
                    if not _is_mongo_error(exc):
                        raise
                    coord_session.mark_offline(exc)
                else:
                    if cmd and cmd.get("acao") == "liberar":
                        await asyncio.to_thread(active_coord.mark_command, cmd["_id"], "executando")
                        if pool is not None:
                            pool.stop_all()
                        await asyncio.to_thread(child_killer)
                        await asyncio.to_thread(active_coord.mark_command, cmd["_id"], "feito")
                        print("[comando] liberar recursos", flush=True)
                        raise SystemExit(75)
                    runner = run_pending_fn
                    if runner is None:
                        from . import publicacao

                        runner = publicacao.run_pending
                    discord_post = (
                        (lambda tid, msg: discord_client.post(tid, msg))
                        if discord_client is not None
                        else None
                    )
                    try:
                        await asyncio.to_thread(
                            runner,
                            active_coord,
                            active_coord.worker_id,
                            out_root=out_root,
                            now=now,
                            policy=policy,
                            discord_post=discord_post,
                        )
                    except Exception as exc:
                        if _is_mongo_error(exc):
                            coord_session.mark_offline(exc)
                        else:
                            uri = os.environ.get("CLIPES_MONGO_URI", "").strip() or None
                            print(f"[publicar] erro no heartbeat ({_sanitize_error(exc, uri)})", flush=True)
            wait_rem = beat_interval if active_coord is None else max(1.0, beat_interval - (now() - last_beat))
            await sleep(max(1.0, wait_rem))

    irc_call = irc(channels, on_message, stop=stop, on_join=on_join) if real_irc else irc(channels, on_message, stop=stop)
    coros = [irc_call, tick_loop()]
    if not dry_run:
        coros.append(worker_loop())
        if coord_session is not None and coord_session.configured:
            coros.append(heartbeat_loop())
    tasks = [asyncio.create_task(c) for c in coros]
    stopper = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # re-raise a crashed loop
    finally:
        stop.set()
        pending = [*tasks, stopper, *captures, *claiming_tasks.values()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if pool is not None:
            pool.stop_all()
        if coord_session is not None and coord_session.active is not None:
            for live_id in list(owned_lives.values()):
                try:
                    coord_session.active.release(live_id, False)
                except Exception:
                    pass
        if lock is not None:
            lock.close()


def _connect_coord_for_follow(out_root: Path) -> Any | None:
    session = _CoordSession(out_root)
    if not session.configured:
        return None
    session.connect()
    return session.active


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="clipes monitor",
        description="Vigia chats da Twitch, grava picos em buffer rolante e enfileira jobs (sem publicar).",
    )
    p.add_argument("--channels-file", type=Path, default=Path("channels.txt"), help="1 canal por linha")
    p.add_argument("--policy-file", type=Path, default=None, help="Padrão: policy.json ao lado de --channels-file")
    p.add_argument("--game-id", default=None, help="Filtro de categoria do Helix (só com TWITCH_CLIENT_ID/SECRET no env)")
    p.add_argument("--max-recorders", type=int, default=3, help="Canais quentes gravando (máx. 3, + 1 sob demanda)")
    p.add_argument("--out-dir", default=None, help="Padrão: OUT_DIR ou ./out")
    p.add_argument("--queue-dir", type=Path, default=None, help="Padrão: <out-dir>/queue")
    p.add_argument("--dry-run", action="store_true", help="Só IRC + detector; não grava nem processa")
    p.add_argument("--follow", default=None, help="Acompanha um único canal em janelas contínuas até acabar")
    args = p.parse_args(argv)

    out_root = Path(args.out_dir or os.environ.get("OUT_DIR") or "out").expanduser().resolve()
    policy = load_policy(args.policy_file or args.channels_file.with_name("policy.json"))
    queue_root = args.queue_dir.expanduser().resolve() if args.queue_dir else None
    if args.follow is not None:
        channel = _live_channel(args.follow)
        if not CHANNEL_RE.fullmatch(channel) or not policy.permits(channel):
            print(f"[monitor] canal bloqueado ou invalido: {args.follow}", flush=True)
            return 2
        print(f"[monitor] acompanhando {channel}", flush=True)
        follow_coord = _connect_coord_for_follow(out_root)
        try:
            follow_live(
                channel,
                out_root,
                queue_root=queue_root,
                policy=policy,
                coord=follow_coord,
            )
        except KeyboardInterrupt:
            pass
        except RuntimeError as exc:
            print(f"[monitor] {exc}", flush=True)
            return 1
        return 0

    channels = discover_channels(args.channels_file, policy, args.game_id)
    if not channels:
        print(f"[monitor] nenhum canal para vigiar (veja {args.channels_file} e policy.json)", flush=True)
        return 2
    print(f"[monitor] vigiando {len(channels)} canais{' (dry-run)' if args.dry_run else ''}", flush=True)
    try:
        asyncio.run(run_monitor(
            channels, out_root, policy,
            queue_root=queue_root,
            max_recorders=args.max_recorders,
            dry_run=args.dry_run,
        ))
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        print(f"[monitor] {exc}", flush=True)
        return 1
    return 0
