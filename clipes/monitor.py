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
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import config
from .executor_publicacao import PublishLoop
from .ingest import BOT_AUTHORS, _compact_chat_rows, js_runtime_args, probe_duration, probe_metadata, sanitize_id
from .notificar import notify_discord

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
SEGMENT_GAP_TOLERANCE_S = SEGMENT_S / 2  # segments cut on keyframes: spacing may exceed SEGMENT_S a bit
LOG_MAX_BYTES = 5_000_000  # recorder stderr log per channel, rotated to .log.1 past this
BUFFER_S = 480
PRE_ROLL_S = 180
POST_ROLL_MIN_S = 60
POST_ROLL_MAX_S = 120
MIN_CLIP_SOURCE_S = 25.0  # a source shorter than min_dur cannot yield a clip
STREAMLINK_QUALITY = "720p60,720p,480p,best"
CHANNEL_RE = re.compile(r"[a-z0-9_]{1,25}")
_YT_KEY_RE = re.compile(r"(?:yt|kick)[:_][A-Za-z0-9_-]{1,64}")

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
    return ["streamlink", "--quiet", "--stdout", "--", f"twitch.tv/{channel}", STREAMLINK_QUALITY]


def ytdlp_live_cmd(url: str) -> list[str]:
    return [
        "yt-dlp", "-q", "--no-progress", "--no-part", "-f", "b[height<=1080]/bv*[height<=1080]+ba/b",
        "--retries", "infinite", "--fragment-retries", "infinite", "--socket-timeout", "30",
        "--hls-use-mpegts", *js_runtime_args(), "-o", "-", "--", url,
    ]


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
    log: Any = None  # handle do log de stderr do canal (fechado no _stop)

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
        source_cmd: Callable[[str], list[str]] = streamlink_cmd,
    ):
        self.buf_root = buf_root
        self.max_hot = max_hot
        self.max_on_demand = max_on_demand
        self.buffer_seconds = buffer_seconds
        self.popen = popen
        self.source_cmd = source_cmd
        self.recordings: dict[str, _Recording] = {}
        self.holds: dict[str, float] = {}  # channel -> oldest timestamp cleanup must keep
        self._gate = threading.Lock()  # serializa start x freeze (liberar roda em outra thread)
        self.frozen = False  # liberar: nenhum gravador novo depois do stop_all

    def _start(self, channel: str, on_demand: bool) -> None:
        key_re = _YT_KEY_RE if channel.startswith(("yt_", "yt:", "kick_", "kick:")) else CHANNEL_RE
        if not key_re.fullmatch(channel):
            raise ValueError(f"invalid channel name: {channel!r}")
        seg_dir = self.buf_root / channel
        seg_dir.mkdir(parents=True, exist_ok=True)
        log = self._open_log(channel)
        err = log if log is not None else subprocess.DEVNULL
        # own session: Ctrl+C on the monitor does not hit the recorders, _stop terminates them in order
        try:
            source = self.popen(self.source_cmd(channel), stdout=subprocess.PIPE, stderr=err,
                                start_new_session=True)
        except BaseException:
            if log is not None:
                log.close()
            raise
        try:
            sink = self.popen(segment_cmd(seg_dir), stdin=source.stdout, stderr=err,
                              start_new_session=True)
        except BaseException:
            source.kill()
            source.wait()
            if log is not None:
                log.close()
            if not any(seg_dir.glob("*.ts")):  # keep the buffer of a restart that failed
                shutil.rmtree(seg_dir, ignore_errors=True)
            raise
        finally:
            source.stdout.close()  # streamlink gets SIGPIPE if ffmpeg dies
        self.recordings[channel] = _Recording(channel, seg_dir, [source, sink], on_demand, log)

    def _open_log(self, channel: str):
        """stderr log of the channel's source+ffmpeg (append; rotated to .log.1 past LOG_MAX_BYTES)."""
        path = self.buf_root / f"{channel}.log"
        try:
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
            return open(path, "ab")
        except OSError as exc:
            print(f"[monitor] log do gravador de {channel} indisponivel: {exc}", flush=True)
            return None

    def _stop(self, channel: str, remove_buffer: bool) -> None:
        rec = self.recordings.pop(channel, None)
        if rec is None:  # parado em paralelo (liberar na thread x tick_loop)
            return
        source, *rest = rec.procs
        for proc in rec.procs:
            code = proc.poll()
            if code is not None:
                print(f"[monitor] gravador de {channel} tinha morrido (returncode={code})", flush=True)
        # source first, wait for it, so ffmpeg sees EOF and closes the last segment
        source.terminate()
        try:
            source.wait(timeout=5)
        except subprocess.TimeoutExpired:
            source.kill()
            source.wait()  # reap: no zombie left behind
        for proc in rest:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        if rec.log is not None:
            try:
                rec.log.close()
            except OSError:
                pass
        if remove_buffer:
            shutil.rmtree(rec.seg_dir, ignore_errors=True)

    def _try_start(self, channel: str, on_demand: bool) -> bool:
        with self._gate:
            if self.frozen:
                return False
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
        if self.frozen:
            return
        held = [c for c, r in list(self.recordings.items()) if not r.on_demand and c in self.holds]
        wanted = (held + [c for c in ranked_channels if c not in held])[: self.max_hot]
        for channel in [c for c, r in list(self.recordings.items()) if not r.on_demand and c not in wanted]:
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
        if sum(1 for r in list(self.recordings.values()) if r.on_demand) >= self.max_on_demand:
            return False
        return self._try_start(channel, on_demand=True)

    def restart_on_demand(self, channel: str) -> bool:
        """Restart the channel's recorder keeping its buffer and slot kind; True when it is recording again."""
        rec = self.recordings.get(channel)
        if rec is None:
            return self.start_on_demand(channel)
        on_demand = rec.on_demand
        self._stop(channel, remove_buffer=False)
        return self._try_start(channel, on_demand=on_demand)

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

    def freeze(self) -> None:
        """Para tudo e impede novos gravadores (tick_loop/capturas em voo nao religam durante o liberar)."""
        with self._gate:
            self.frozen = True
        self.stop_all()  # fora do lock: nao trava o event loop; _start em curso ja registrou em recordings

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
        for channel in set(list(self.recordings)) | set(list(self.holds)):
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
        # a gap would shift the concat (video_start + t stops being absolute time): keep the latest continuous run
        first = 0
        for i in range(len(chosen) - 1, 0, -1):
            if chosen[i][0] - chosen[i - 1][0] > SEGMENT_S + SEGMENT_GAP_TOLERANCE_S:
                first = i
                break
        if first:
            print(f"[monitor] {channel}: buraco no buffer, descartando {first} segmento(s) anteriores a "
                  f"{chosen[first][0]}", flush=True)
            chosen = chosen[first:]
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
                "origem": "busca",
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
            if live_id is not None:
                job["live_id"] = live_id
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
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(job, ensure_ascii=False, indent=2))
        fh.flush()
        os.fsync(fh.fileno())  # o rename so e atomico/duravel se os dados ja estao no disco
    tmp.replace(job_dir / "job.json")


class DiskQueue:
    """Job directories under queue_root/{pending,processing,done,rejected}/<job_id>/, moved with os.replace."""

    def __init__(self, queue_root: Path):
        self.root = queue_root
        self.mutex = threading.RLock()  # worker, replay (reconexao) e recover mexem no mesmo job.json
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
                job = _read_job(job_dir)
                pending.append((
                    -float(job.get("z_score") or 0.0),
                    float(job.get("created_at") or 0.0),
                    job_dir.name,
                    job_dir,
                ))
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                print(f"[monitor] unreadable job {job_dir.name} rejected: {exc!r}", flush=True)
                self._move(job_dir, "rejected")
        for *_, job_dir in sorted(pending):
            try:
                with self.mutex:
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
        with self.mutex:
            job = {**_read_job(job_dir), **manifest, "status": status}
            _write_job(job_dir, job)
            dst = self._move(job_dir, status)
        # ~200 MB per job: the clips live in out/, the source is not needed. Only after the move, so a crash
        # in between never leaves a final job without its source in processing/ (M-05)
        (dst / "source.mkv").unlink(missing_ok=True)
        return dst

    def requeue(self, job_dir: Path) -> Path:
        """Return a claimed job (processing/) to pending/ untouched (stop/liberar interrupted its pipeline)."""
        with self.mutex:
            job = _read_job(job_dir)
            job["status"] = "pending"
            _write_job(job_dir, job)
            return self._move(job_dir, "pending")

    def recover_orphans(self) -> int:
        """Put jobs left in processing/ by a crash back into pending/.

        A job whose job.json already carries a final status (crash between the write and the move in
        `complete`) just finishes the move to done//rejected/ instead of being reprocessed without a source.
        """
        orphans = list((self.root / "processing").iterdir())
        for job_dir in orphans:
            target = "pending"
            with self.mutex:
                try:
                    job = _read_job(job_dir)
                    if job.get("status") in ("done", "rejected"):
                        target = job["status"]
                        job["coord_synced"] = False  # o finish_job pode nunca ter ido ao Mongo: replay sincroniza
                    else:
                        job["status"] = "pending"
                    _write_job(job_dir, job)
                except (OSError, ValueError, TypeError):  # corrupt: claim_next will reject it
                    pass
                dst = self._move(job_dir, target)
            if target != "pending":
                (dst / "source.mkv").unlink(missing_ok=True)
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

    cli_args = [job_dir.name, "--out-dir", str(out_root), "--min-per-hour", "0"]
    job_path = job_dir / "job.json"
    if job_path.is_file():
        try:
            job_meta = _read_job(job_dir)
        except (OSError, ValueError):
            job_meta = {}
        if isinstance(job_meta, dict):
            # M-16: repassa max_dur/min_dur gravados no job.json (jobs antigos sem o campo usam o padrao do CLI)
            for key, flag in (("max_dur", "--max-dur"), ("min_dur", "--min-dur")):
                raw_dur = job_meta.get(key)
                if raw_dur is None or isinstance(raw_dur, bool):
                    continue
                try:
                    dur_val = float(raw_dur)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(dur_val) and dur_val > 0:
                    cli_args.extend([flag, str(dur_val)])
            # FIL-01: regiao dona da janela (tempo relativo ao source) para o cli filtrar antes da ASR 2
            try:
                we, vs, md = (float(job_meta[k]) for k in ("window_end", "video_start", "max_dur"))
                pad = md / 2
                hi = (we if job_meta["last"] else we - pad) - vs
                cli_args.extend(["--own-range", f"{we - LIVE_STEP_S - pad - vs},{hi}"])
            except (KeyError, TypeError, ValueError):
                pass  # job VOD/antigo: sem filtro

    args = cli.build_parser().parse_args(cli_args)
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


def replay_unsynced_jobs(
    queue: DiskQueue,
    coord: Any,
    *,
    skip_lives: "set[str] | frozenset[str] | tuple[str, ...]" = (),
    out_root: Path | None = None,
    live_remap: "dict[str, str] | None" = None,
) -> int:
    """Envia ao Mongo os jobs locais com `coord_synced=False` (criados com o Mongo fora do ar). Devolve quantos.

    Idempotente: `add_job` ignora duplicado; jobs ja finais (done/rejected) tem o status final sincronizado via
    start_job + finish_job (no-op se o Mongo ja os terminou). Marca `coord_synced=True`/`live_id`/`mongo_job_id` no
    job.json ao conseguir. Jobs de lives em `skip_lives` (lease perdida) ficam como estao. Erro de Mongo SOBE para o
    chamador (que marca offline e tenta de novo na proxima reconexao). Jobs em processing/ ficam para o worker,
    que marca `coord_synced=False` se nao conseguir sincronizar o status final. `live_remap` troca o live_id de
    fallback (`twitch:<canal>`) pelo id real depois do claim. Cada job e lido, enviado e gravado sob `queue.mutex`
    (o mesmo do `claim_next`), entao o worker nao move o job no meio do envio.
    """
    synced = 0
    remap = live_remap or {}
    for state in ("pending", "done", "rejected"):
        for job_dir in sorted((queue.root / state).iterdir()):
            with queue.mutex:
                try:
                    job = _read_job(job_dir)
                except (OSError, ValueError, TypeError):
                    continue
                if job.get("coord_synced") is not False:
                    continue
                channel = job.get("channel") or ""
                old_live = job.get("live_id") or f"twitch:{channel}"
                live_id = remap.get(old_live, old_live)
                if live_id in skip_lives or old_live in skip_lives:
                    continue
                w_end = int(job.get("window_end") or job.get("created_at") or 0)
                w_start = int(job.get("window_start") or (w_end - PRE_ROLL_S))
                mongo_job_id = job.get("mongo_job_id")
                if not mongo_job_id or live_id != old_live:
                    mongo_job_id = f"{live_id}:{w_end}"
                coord.add_job(mongo_job_id, live_id, (w_start, w_end), float(job.get("z_score") or 0.0))
                if state != "pending" and coord.start_job(mongo_job_id):
                    result = {"clips": job.get("clips"), "score": job.get("score"),
                              "output_dir": _rel_output_dir(job.get("output_dir"), out_root) if out_root else job.get("output_dir")}
                    if job.get("reject_reason"):
                        result["reject_reason"] = job["reject_reason"]
                    coord.finish_job(mongo_job_id, state, result)
                job.update(live_id=live_id, mongo_job_id=mongo_job_id, coord_synced=True)
                _write_job(job_dir, job)
                synced += 1
    return synced


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


def _effective_lease_s(beat_s: float | None = None) -> float:
    """CLIPES_LEASE_S garantindo lease >= 3 * beat (um beat perdido nao pode soltar a live)."""
    beat = max(1.0, config.env_float("CLIPES_BEAT_S", 20.0, min_value=1.0) if beat_s is None else float(beat_s))
    lease_s = config.env_float("CLIPES_LEASE_S", 90.0, min_value=1e-9)
    if lease_s < 3 * beat:
        print(f"[fila] CLIPES_LEASE_S={lease_s:g} < 3*beat ({beat:g}s); usando {3 * beat:g}s", flush=True)
        return 3 * beat
    return lease_s


class _CoordSession:
    """Optional MongoDB coordination wrapper with lazy import and 60s fallback retry."""

    def __init__(
        self,
        out_root: Path,
        *,
        coord: Any | None = None,
        now: Callable[[], float] = time.time,
        beat_s: float | None = None,
        on_connect: Callable[[Any], Any] | None = None,
    ):
        self.out_root = out_root
        self.uri = os.environ.get("CLIPES_MONGO_URI", "").strip() or None
        self.coord = coord
        self.now = now
        self.beat_s = beat_s
        self.on_connect = on_connect  # roda a cada (re)conexao com o Coord (ex.: replay de jobs sem sync)
        self.configured = coord is not None or self.uri is not None
        self.online = False
        self.released_expired = False
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
                lease_s = _effective_lease_s(self.beat_s)
                self.coord = Coord(db, get_worker_id(self.out_root), now=self.now, lease_s=lease_s)
            self.coord.ensure_indexes()
            if not self.released_expired:
                # so na inicializacao: numa reconexao posterior as lives ativas deste PC (owned_lives) nao podem ser soltas
                self.coord.release_expired_own()  # sobras de crash (lease vencida) nao contam no max_lives
                self.released_expired = True
            if self.on_connect is not None:
                self.on_connect(self.coord)
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
    out_dir_raw = result.get("output_dir")
    notify_discord(
        Path(out_dir_raw) if out_dir_raw else None,
        policy=policy,
        job_dir=completed_dir,
        job=job,
        result=result,
        discord_client=discord_client,
        discord_channel_id=discord_channel_id,
        preview_fn=preview_fn,
    )


def _flag_if_unsynced(job_dir: Path, unsynced: bool, queue: DiskQueue) -> None:
    """Job final cujo status nao chegou ao Mongo: coord_synced=False para o replay da proxima conexao."""
    if not unsynced:
        return
    try:
        with queue.mutex:
            job = _read_job(job_dir)
            job["coord_synced"] = False
            _write_job(job_dir, job)
    except (OSError, ValueError, TypeError):
        pass


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
    cancel: threading.Event | None = None,
) -> bool:
    """Process one pending job end to end (never publishes). False when the queue is empty.

    `cancel` (liberar/shutdown): if the pipeline fails while it is set, the job goes back to pending/ and
    nothing is marked rejected/failed (M-04).
    """
    job_dir = queue.claim_next()
    if job_dir is None:
        return False
    job = _read_job(job_dir)
    if cancel is not None and cancel.is_set():  # nem gasta um attempt do start_job
        queue.requeue(job_dir)
        return True
    channel = job["channel"]
    mongo_job_id = job.get("mongo_job_id") or job["job_id"]
    live_id = job.get("live_id")
    use_coord = coord if (coord is not None and job.get("coord_synced") is not False) else None

    unsynced = [coord is None and job.get("coord_synced") is True]  # processado sem Mongo: replay sobe depois

    def report_mongo_error(exc: BaseException) -> None:
        unsynced[0] = True  # status final nao chegou ao Mongo: replay_unsynced_jobs tenta de novo
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
        blocked_dir = queue.complete(job_dir, "rejected", {"reject_reason": "blocked_by_policy"})
        _flag_if_unsynced(blocked_dir, unsynced[0], queue)
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

    if cancel is not None and cancel.is_set():  # liberar/encerramento chegou durante o start_job: nem comeca
        queue.requeue(job_dir)
        return True
    started = time.perf_counter()
    if on_job_active is not None:
        on_job_active(mongo_job_id)
    try:
        result = pipeline(job_dir, out_root)
    except Exception as exc:  # a broken job must not stop the worker
        if cancel is not None and cancel.is_set():
            queue.requeue(job_dir)  # interrupted on purpose (children killed): not the job's fault
            print(f"[fila] job {mongo_job_id} devolvido a pending (interrompido)", flush=True)
            return True
        failed_dir = queue.complete(job_dir, "rejected", {
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
        _flag_if_unsynced(failed_dir, unsynced[0], queue)
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
    _flag_if_unsynced(completed_dir, unsynced[0] and not (lost_lives and live_id in lost_lives), queue)
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
    from .acompanhar import Supervisor, parse_alvo

    active_policy = policy or ChannelPolicy()
    try:
        alvo = parse_alvo(url_or_channel)
    except ValueError:
        return {"status": "blocked", "jobs": []}
    if alvo.plataforma == "twitch" and not active_policy.permits(alvo.canal):
        return {"status": "blocked", "jobs": []}

    sup = Supervisor(
        [alvo],
        out_root,
        queue_root=queue_root,
        pool=pool,
        pipeline=pipeline,
        is_live=is_live,
        probe=None if probe is probe_metadata else probe,
        coord=coord,
        now=now,
        sleep=sleep,
        disk_usage=disk_usage,
        step=step,
        max_dur=max_dur,
        min_free_gb=min_free_gb,
        policy=active_policy,
        stream_id=stream_id,
        capacity_fn=capacity_fn,
        max_lives_fn=max_lives_fn,
        reraise_start_error=True,
    )
    res = sup.run()
    if res["status"] == "not_claimed":
        motivo = res.get("reason") or "recusada"
        print(f"[live] nao acompanhada {alvo.canal}: {motivo}", flush=True)
    return {"status": res["status"], "jobs": res["jobs"]}


# --- main loop and `python -m clipes monitor` entrypoint ------------------------------------------

def buffer_cap_bytes() -> int:
    """Global ceiling for queue/.buffer: CLIPES_BUFFER_CAP_GB (default 1.5; invalid -> default)."""
    raw = os.environ.get("CLIPES_BUFFER_CAP_GB", "").strip()
    if raw:
        try:
            gb = float(raw)
            if gb > 0 and gb != float("inf"):
                return int(gb * 1_000_000_000)
        except ValueError:
            pass
        print(f"[monitor] CLIPES_BUFFER_CAP_GB invalido ({raw!r}); usando 1.5", flush=True)
    return 1_500_000_000


BUFFER_CAP_BYTES = buffer_cap_bytes()
RANK_INTERVAL_S = 10.0
WORKER_IDLE_S = 5.0
REPLAY_EVERY_BEATS = 5  # a cada N beats, reenvia jobs com coord_synced=False
WORKER_JOIN_S = 60.0  # espera maxima pela thread do worker no encerramento


def _lock_worker(queue_root: Path):
    """Exclusive flock on queue_root/.worker.lock: a second monitor on the same queue fails fast."""
    from .registro import FCNTL_AUSENTE_MSG

    try:
        import fcntl
    except ImportError:
        raise RuntimeError(FCNTL_AUSENTE_MSG) from None

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


EXCLUIR_CACHE_S = 15.0  # cache <= 20s de alvos_ativos() para exclusao da busca geral


def _norm_excluded(items: Any) -> set[str]:
    out: set[str] = set()
    if not items:
        return out
    for raw in items:
        if not isinstance(raw, str):
            continue
        s = raw.strip().lower()
        if not s:
            continue
        out.add(s)
        if ":" in s:
            _, resto = s.split(":", 1)
            if resto:
                out.add(resto)
    return out


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
    worker: bool = True,
    excluir: Callable[[], set[str]] | None = None,
    coord_session: Any | None = None,
    owned_lives_out: dict[str, str] | None = None,
) -> None:
    """IRC -> ChannelSpikeDetector -> rolling recorder -> capture_job -> DiskQueue, plus 1 sequential worker.

    dry_run only reads IRC and logs spikes: no recording, no queue, no worker.
    worker=False runs in producer-only mode (no queue lock, no worker/heartbeat/PublishLoop).
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
    owned_lives: dict[str, str] = owned_lives_out if owned_lives_out is not None else {}
    claiming_tasks: dict[str, asyncio.Task[bool]] = {}
    claim_retry_at: dict[str, float] = {}
    lost_lives: set[str] = set()
    reserving: dict[str, None] = {}  # canais com claim em andamento, em ordem de chegada: ocupam vaga do max_lives (M-02)
    unleased: set[str] = set()  # canais gravando sem lease (Mongo fora do ar): reclamam ao reconectar (M-01)
    cancel = threading.Event()  # liberar/encerramento: o job em andamento volta a pending em vez de rejected (M-04)
    worker_busy = threading.Lock()  # preso pela thread do worker enquanto roda um job
    publish_loop: PublishLoop | None = None
    excl_cache: list[Any] = [float("-inf"), set()]  # [ts, set[str]] para alvos_ativos()

    def on_join(channel: str, t: float) -> None:
        detector = detectors.get(channel)
        if detector is not None:
            detector.start_watching(t)

    def on_message(channel: str, t: float, author: str, text: str) -> None:
        detector = detectors.get(channel)
        if detector is not None:
            detector.record_message(t, author, text)

    def _replay_skip() -> set[str]:
        """Lives cujos jobs nao devem subir ao Mongo: perdidas ou com o claim ainda nao decidido."""
        return {*lost_lives, *(f"twitch:{c}" for c in unleased)}

    queue = pool = lock = None
    cap_getter = capacity_fn
    max_lives_getter = max_lives_fn
    busca_slot = config.slots()["busca"] if (not worker and max_lives_getter is None) else None
    worker_alias = alias or os.environ.get("CLIPES_ALIAS", "").strip()
    beat_interval = max(1.0, config.env_float("CLIPES_BEAT_S", 20.0, min_value=1.0) if beat_s is None else float(beat_s))
    child_killer = kill_children or _terminate_child_processes
    current_job: list[str | None] = [None]
    if not dry_run:
        queue_root = queue_root or out_root / "queue"
        queue = DiskQueue(queue_root)
        if worker:
            lock = _lock_worker(queue_root)
            queue.recover_orphans()
        staging = queue_root / ".staging"  # same filesystem as the queue, so enqueue is an os.replace
        staging.mkdir(exist_ok=True)
        buf_dir = queue_root / (".buffer" if worker else ".buffer_busca")
        shutil.rmtree(buf_dir, ignore_errors=True)  # segments of a previous run are stale
        hot_limit = max(0, min(max_recorders, 3))
        on_demand_limit = 1
        if busca_slot is not None:
            hot_limit = max(0, min(hot_limit, busca_slot))
            on_demand_limit = 1 if busca_slot > 0 else 0
        pool = RollingRecorderPool(buf_dir, max_hot=hot_limit, max_on_demand=on_demand_limit, popen=popen)
        if coord_session is None:
            coord_session = _CoordSession(
                out_root, coord=coord, now=now, beat_s=beat_interval,
                on_connect=(
                    (lambda c: replay_unsynced_jobs(queue, c, skip_lives=_replay_skip(), out_root=out_root))
                    if worker
                    else None
                ),
            )
            if coord_session.configured:
                if cap_getter is None:
                    from .capacidade import capacity as cap_getter
                if max_lives_getter is None and worker:
                    from .perfil import max_lives as max_lives_getter
                if not worker_alias:
                    from .capacidade import worker_id as get_worker_id
                    worker_alias = get_worker_id(out_root)
                coord_session.connect()
        elif coord_session.configured and cap_getter is None:
            from .capacidade import capacity as cap_getter

    def over_limit(channel: str, ml: int) -> bool:
        """True se `channel` nao cabe no max_lives: lives donas + reservas que chegaram antes dele."""
        ahead = list(reserving).index(channel) if channel in reserving else len(reserving)
        return len(owned_lives) + ahead >= ml

    def _current_limits() -> tuple[dict[str, Any], int]:
        cap = cap_getter(out_root) if cap_getter is not None else {"cpus": 1, "score": 1}
        if max_lives_getter is not None:
            ml = int(max_lives_getter(out_root, cap.get("cpus")))
        elif busca_slot is not None:
            ml = int(busca_slot)
        else:
            ml = 1
        return cap, max(0 if not worker else 1, ml)

    async def _current_limits_async() -> tuple[dict[str, Any], int]:
        return await asyncio.to_thread(_current_limits)

    def _get_excluded(t: float) -> set[str]:
        out = _norm_excluded(excluir()) if excluir is not None else set()
        active_coord = coord_session.active if coord_session is not None else None
        if active_coord is not None:
            alvos_fn = getattr(active_coord, "alvos_ativos", None)
            if callable(alvos_fn):
                if t - excl_cache[0] >= EXCLUIR_CACHE_S:
                    try:
                        excl_cache[1] = _norm_excluded(alvos_fn())
                        excl_cache[0] = t
                    except Exception as exc:
                        if _is_mongo_error(exc):
                            coord_session.mark_offline(exc)
                out |= excl_cache[1]
        return out

    async def _get_excluded_async(t: float) -> set[str]:
        return await asyncio.to_thread(_get_excluded, t)

    async def _claim_live(channel: str) -> bool:
        try:
            active_coord = coord_session.active if coord_session is not None else None
            if active_coord is None:
                return True
            if channel in owned_lives:
                return True
            reserving[channel] = None  # reserva ANTES do primeiro await: o tick_loop e as outras tasks ja a enxergam
            cap, ml = await _current_limits_async()
            if over_limit(channel, ml):
                print(f"[live] nao acompanhada {channel}: limite max_lives={ml}", flush=True)
                unleased.discard(channel)
                return False
            sid = await asyncio.to_thread(_resolve_stream_id, channel, stream_ids, probe)
            if not sid:
                print(f"[live] nao acompanhada {channel}: stream_id indisponivel", flush=True)
                claim_retry_at[channel] = now() + 30.0
                unleased.discard(channel)
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
                unleased.add(channel)
                return True
            await _wait_claim(30.0 / max(1.0, float(cap.get("score") or 1)), now=now, sleep=sleep, stop=stop)
            if stop.is_set():
                return False
            active_coord = coord_session.active
            if active_coord is None:
                unleased.add(channel)
                return True
            _, ml = await _current_limits_async()
            if channel in owned_lives:
                return True
            if over_limit(channel, ml):
                print(f"[live] nao acompanhada {channel}: limite max_lives={ml}", flush=True)
                unleased.discard(channel)
                return False
            try:
                claimed = await asyncio.to_thread(active_coord.claim_live, live_id)
            except Exception as exc:
                if not _is_mongo_error(exc):
                    raise
                coord_session.mark_offline(exc)
                unleased.add(channel)
                return True
            if claimed:
                owned_lives[channel] = live_id
                lost_lives.discard(live_id)
                # claim aceito: o id de fallback deixa de ser pulado pelo replay (e passa a mapear para o id real)
                was_unleased = channel in unleased or f"twitch:{channel}" in lost_lives
                lost_lives.discard(f"twitch:{channel}")
                unleased.discard(channel)
                if was_unleased and queue is not None:  # jobs gravados sem lease passam a pertencer a esta live
                    try:
                        await asyncio.to_thread(
                            replay_unsynced_jobs, queue, active_coord, skip_lives=_replay_skip(),
                            out_root=out_root, live_remap={f"twitch:{channel}": live_id},
                        )
                    except Exception as exc:
                        if not _is_mongo_error(exc) or coord_session is None:
                            raise
                        coord_session.mark_offline(exc)
                return True
            print(f"[live] nao acompanhada {channel}: lease com outro PC", flush=True)
            if channel in unleased:  # gravava sem lease e outro PC e o dono: para de gravar e nao sobe os jobs dele
                unleased.discard(channel)
                lost_lives.update((f"twitch:{channel}", live_id))  # id de fallback e id real: o replay pula os dois
            claim_retry_at[channel] = now() + 30.0
            return False
        finally:
            reserving.pop(channel, None)
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
            excluded = await _get_excluded_async(now())
            if event.channel.lower() in excluded:
                return
            if not worker:
                _, ml = await _current_limits_async()
                if ml < 1:
                    return
                if (coord_session is None or not coord_session.configured) and event.channel not in pool.recordings and len(pool.recordings) >= ml:
                    return
            if not await ensure_claimed(event.channel):
                return
            excluded_after = await _get_excluded_async(now())
            if event.channel.lower() in excluded_after:
                lid_drop = owned_lives.pop(event.channel, None)
                if lid_drop and coord_session is not None and coord_session.active is not None:
                    try:
                        await asyncio.to_thread(coord_session.active.release, lid_drop, False)
                    except Exception as exc:
                        if _is_mongo_error(exc):
                            coord_session.mark_offline(exc)
                return
            job_dir = await capture_job(pool, detectors[event.channel], event, staging, now=now, sleep=sleep)
            if job_dir is None:
                print(f"[monitor] no free recorder for {event.channel}; spike dropped", flush=True)
            else:
                job = _read_job(job_dir)
                changed = False
                if job.get("origem") != "busca":
                    job["origem"] = "busca"
                    changed = True
                if coord_session is not None and coord_session.configured:
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
                            job.update(live_id=live_id, mongo_job_id=mongo_job_id, coord_synced=False)
                        else:
                            job.update(live_id=live_id, mongo_job_id=mongo_job_id, coord_synced=True)
                        changed = True
                    elif job.get("status") != "rejected":
                        job.update(live_id=live_id, coord_synced=False)  # replay_unsynced_jobs sobe quando der
                        changed = True
                if changed:
                    _write_job(job_dir, job)
                print(f"[monitor] queued {queue.enqueue(job_dir)}", flush=True)
        except Exception as exc:  # one failed capture must not stop the monitor
            print(f"[monitor] capture failed for {event.channel}: {type(exc).__name__}: {exc}", flush=True)
        finally:
            capturing.discard(event.channel)

    async def tick_loop() -> None:
        last_rank = float("-inf")
        last_renew = float("-inf")
        while not stop.is_set():
            if cancel.is_set():  # liberar em curso: nao religa gravadores nem abre capturas
                await sleep(1.0)
                continue
            t = now()
            if coord_session is not None and coord_session.configured and not coord_session.online and t >= coord_session.next_retry:
                await asyncio.to_thread(coord_session.maybe_reconnect)
            excluded = await _get_excluded_async(t) if (pool is not None and (excluir is not None or (coord_session is not None and coord_session.active is not None))) else set()
            for detector in detectors.values():
                event = detector.check_spike(t)
                if event is None:
                    continue
                if event.channel.lower() in excluded:
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
                active = sorted((c for c in detectors if recent(c) > 0 and c.lower() not in excluded), key=lambda c: -recent(c))
                if excluded:
                    unleased.difference_update(c for c in list(unleased) if c.lower() in excluded)
                if coord_session is not None and coord_session.active is not None:
                    _, ml = await _current_limits_async()
                    for c in active:
                        if c in owned_lives or c in claiming_tasks or t < claim_retry_at.get(c, 0.0):
                            continue
                        if len(owned_lives) + len(reserving) < ml:
                            task = asyncio.create_task(ensure_claimed(c))
                            captures.add(task)
                            task.add_done_callback(captures.discard)
                        else:
                            unleased.discard(c)  # sem vaga: nao grava sem lease
                    # canais que gravavam sem lease seguem quentes enquanto o claim pos-reconexao decide;
                    # se for negado (outro PC dono) saem de `unleased` e deixam de gravar
                    hot_targets = [c for c in active if c in owned_lives or c in unleased]
                else:
                    hot_targets = active
                    if coord_session is not None and coord_session.configured:
                        unleased.update(c for c in active if c not in owned_lives)
                if cancel.is_set():  # liberar chegou durante os awaits acima
                    continue
                pool.set_hot(hot_targets)
                if coord_session is not None and coord_session.active is not None:
                    for c in list(owned_lives):
                        if c.lower() in excluded and c not in capturing:
                            pool.stop_channel(c)
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
            if not worker and owned_lives and coord_session is not None and coord_session.active is not None and t - last_renew >= beat_interval:
                last_renew = t
                active_coord = coord_session.active
                for ch, live_id in list(owned_lives.items()):
                    try:
                        ok = await asyncio.to_thread(active_coord.renew, live_id)
                    except Exception as exc:
                        if _is_mongo_error(exc):
                            coord_session.mark_offline(exc)
                            break
                        raise
                    if not ok:
                        print(f"[fila] lease perdida {live_id}", flush=True)
                        owned_lives.pop(ch, None)
                        unleased.discard(ch)
                        lost_lives.add(live_id)
                        if pool is not None:
                            pool.stop_channel(ch)
            await sleep(1.0)

    def worker_step_sync(active_coord: Any, on_err: Callable[[BaseException], None] | None) -> bool:
        with worker_busy:  # o finally de run_monitor so solta o lock da fila depois deste job terminar
            return run_worker_step(
                queue, out_root, policy, pipeline,
                coord=active_coord, on_mongo_error=on_err, lost_lives=lost_lives,
                on_job_active=lambda jid: current_job.__setitem__(0, jid),
                discord_client=discord_client, discord_channel_id=discord_channel_id,
                preview_fn=preview_fn, cancel=cancel,
            )

    async def worker_loop() -> None:
        while not stop.is_set():
            if cancel.is_set():
                # liberar em curso: nao processa mais jobs, mas a task NAO termina (terminar encerraria o
                # run_monitor normalmente e engoliria o SystemExit(75) que o heartbeat esta prestes a levantar)
                await sleep(WORKER_IDLE_S)
                continue
            active_coord = coord_session.active if coord_session is not None else None
            on_err = coord_session.mark_offline if coord_session is not None else None
            if not await asyncio.to_thread(worker_step_sync, active_coord, on_err):
                await sleep(WORKER_IDLE_S)

    def liberar() -> None:
        """Comando `liberar` do painel: cancela o job em andamento (volta a pending), para gravadores e filhos."""
        cancel.set()
        if pool is not None:
            pool.freeze()
        child_killer()

    def publish_once() -> None:
        """Uma rodada de publicacao (thread propria do PublishLoop, fora do heartbeat: C-02)."""
        active_coord = coord_session.active if coord_session is not None else None
        if active_coord is None:
            return
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
            runner(
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
                print(f"[publicar] erro na publicacao ({_sanitize_error(exc, uri)})", flush=True)

    if not dry_run and worker and coord_session is not None and coord_session.configured:
        publish_loop = PublishLoop(publish_once, 0, name="publicacao")  # sem timer: o heartbeat dispara run_now()

    async def heartbeat_loop() -> None:
        last_beat = float("-inf")
        beats_done = 0
        while not stop.is_set():
            t = now()
            active_coord = coord_session.active if coord_session is not None else None
            if active_coord is not None and t - last_beat >= beat_interval:
                last_beat = t
                cap, ml = await _current_limits_async()
                razao = _read_perfil_razao(out_root, cap.get("cpus"))
                try:
                    await asyncio.to_thread(
                        active_coord.beat, worker_alias, cap, current_job[0], ml, razao, beat_s=beat_interval
                    )
                    for ch, live_id in list(owned_lives.items()):
                        ok = await asyncio.to_thread(active_coord.renew, live_id)
                        if not ok:
                            print(f"[fila] lease perdida {live_id}", flush=True)
                            owned_lives.pop(ch, None)
                            unleased.discard(ch)  # lease perdida: nao religa o gravador como "sem lease"
                            lost_lives.add(live_id)
                            if pool is not None:
                                pool.stop_channel(ch)
                    beats_done += 1
                    if queue is not None and beats_done % REPLAY_EVERY_BEATS == 0:
                        # job que terminou sem sincronizar com o Mongo online nao espera a proxima reconexao
                        await asyncio.to_thread(
                            replay_unsynced_jobs, queue, active_coord, skip_lives=_replay_skip(), out_root=out_root
                        )
                    action = await asyncio.to_thread(active_coord.run_pending_command, liberar)
                except Exception as exc:
                    if not _is_mongo_error(exc):
                        raise
                    coord_session.mark_offline(exc)
                    if cancel.is_set():  # liberar ja cancelou tudo: nao vira processo zumbi so porque o "feito" falhou
                        print("[comando] liberar recursos (Mongo caiu ao marcar feito)", flush=True)
                        raise SystemExit(75)
                else:
                    if action == "liberar":
                        print("[comando] liberar recursos", flush=True)
                        raise SystemExit(75)
                    if publish_loop is not None:
                        publish_loop.run_now()  # nao bloqueia: upload longo nao atrasa o renew
            wait_rem = beat_interval if active_coord is None else max(1.0, beat_interval - (now() - last_beat))
            await sleep(max(1.0, wait_rem))

    irc_call = irc(channels, on_message, stop=stop, on_join=on_join) if real_irc else irc(channels, on_message, stop=stop)
    coros = [irc_call, tick_loop()]
    if not dry_run and worker:
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
        cancel.set()  # um pipeline que falhe daqui em diante volta a pending em vez de rejected
        pending = [*tasks, stopper, *captures, *claiming_tasks.values()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if pool is not None:
            pool.stop_all()
        if publish_loop is not None:
            publish_loop.stop(timeout=10.0)
        if coord_session is not None and coord_session.active is not None:
            for live_id in list(owned_lives.values()):
                try:
                    coord_session.active.release(live_id, False)
                except Exception:
                    pass
        if lock is not None:
            # a thread do worker pode ainda estar num job (ASR em processo): so solta o lock depois dela
            if await asyncio.to_thread(worker_busy.acquire, True, WORKER_JOIN_S):
                worker_busy.release()
                lock.close()
            else:
                print(f"[monitor] worker ainda ocupado apos {WORKER_JOIN_S:g}s; lock da fila mantido", flush=True)


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
    p.add_argument("--policy-file", type=Path, default=None, help="Padrão: CLIPES_POLICY_FILE, senão <OUT_DIR>/policy.json (legado ao lado do channels.txt/cwd só se o padrão não existir)")
    p.add_argument("--game-id", default=None, help="Filtro de categoria do Helix (só com TWITCH_CLIENT_ID/SECRET no env)")
    p.add_argument("--max-recorders", type=int, default=3, help="Canais quentes gravando (máx. 3, + 1 sob demanda)")
    p.add_argument("--out-dir", default=None, help="Padrão: OUT_DIR ou ./out")
    p.add_argument("--queue-dir", type=Path, default=None, help="Padrão: <out-dir>/queue")
    p.add_argument("--dry-run", action="store_true", help="Só IRC + detector; não grava nem processa")
    p.add_argument("--follow", default=None, help="Acompanha um único canal em janelas contínuas até acabar")
    args = p.parse_args(argv)

    out_root = config.resolve_out_dir(args.out_dir)
    policy = load_policy(args.policy_file or config.resolve_policy_path(out_root, legacy=args.channels_file.with_name("policy.json")))
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
