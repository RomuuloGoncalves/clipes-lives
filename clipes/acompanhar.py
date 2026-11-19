"""Supervisor multi-live (`acompanhar`): acompanha N lives (Twitch/YouTube) com 1 fila e 1 worker."""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import shutil
import signal
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import monitor
from .ingest import sanitize_id
from .janelas import filter_emitted, owns, window_end
from .notificar import post_aviso
from .perfil import max_lives as perfil_max_lives

_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{4,32}")
_YT_HANDLE_RE = re.compile(r"@([A-Za-z0-9_.-]{1,64})")

RECORDER_RESTART_BASE_S = 5.0
RECORDER_RESTART_MAX_S = 60.0
LIVE_CAPTURA_MORTA_S = 900.0


def _captura_morta_s() -> float:
    raw = os.environ.get("CLIPES_LIVE_CAPTURA_MORTA_S", "").strip()
    if not raw:
        return LIVE_CAPTURA_MORTA_S
    try:
        val = float(raw)
    except ValueError:
        print(f"[live] CLIPES_LIVE_CAPTURA_MORTA_S invalido ({raw!r}); usando {LIVE_CAPTURA_MORTA_S:g}s", flush=True)
        return LIVE_CAPTURA_MORTA_S
    return val if val > 0 else LIVE_CAPTURA_MORTA_S


@dataclass
class Alvo:
    plataforma: str
    chave: str
    url: str
    canal: str
    video_id: str | None = None
    channel_id: str | None = None
    raw: str = ""


def parse_alvo(s: str) -> Alvo:
    """Parse Twitch/YouTube live target into Alvo; raise ValueError on unsupported format."""
    raw = (s or "").strip()
    if not raw or any(ch.isspace() for ch in raw):
        raise ValueError(f"alvo invalido: {s!r}")

    if "://" in raw or "/" in raw or "." in raw:
        to_parse = raw if "://" in raw else f"https://{raw}"
        parsed = urllib.parse.urlparse(to_parse)
        host = parsed.netloc.lower()
        path = parsed.path.strip("/")

        if host in {"twitch.tv", "www.twitch.tv", "m.twitch.tv"}:
            if not path or "/" in path:
                raise ValueError(f"alvo invalido: {s!r}")
            canal = path.lower()
            if not monitor.CHANNEL_RE.fullmatch(canal):
                raise ValueError(f"alvo invalido: {s!r}")
            return Alvo(
                plataforma="twitch",
                chave=canal,
                url=f"https://www.twitch.tv/{canal}",
                canal=canal,
                raw=raw,
            )

        if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            parts = [p for p in path.split("/") if p]
            if parts == ["watch"]:
                qs = urllib.parse.parse_qs(parsed.query)
                vid = (qs.get("v") or [""])[0]
                if not _YT_ID_RE.fullmatch(vid):
                    raise ValueError(f"alvo invalido: {s!r}")
                return Alvo(
                    plataforma="youtube",
                    chave=f"yt_{vid}",
                    url=f"https://www.youtube.com/watch?v={vid}",
                    canal=vid.lower(),
                    video_id=vid,
                    raw=raw,
                )
            if len(parts) == 2 and parts[0] == "live" and _YT_ID_RE.fullmatch(parts[1]):
                vid = parts[1]
                return Alvo(
                    plataforma="youtube",
                    chave=f"yt_{vid}",
                    url=f"https://www.youtube.com/watch?v={vid}",
                    canal=vid.lower(),
                    video_id=vid,
                    raw=raw,
                )
            if (len(parts) == 1 or (len(parts) == 2 and parts[1] == "live")) and (
                m := _YT_HANDLE_RE.fullmatch(parts[0])
            ):
                handle = m.group(1)
                safe_handle = re.sub(r"[^A-Za-z0-9_-]", "_", handle.lower())
                return Alvo(
                    plataforma="youtube",
                    chave=f"yt_{safe_handle}",
                    url=f"https://www.youtube.com/@{handle}/live",
                    canal=handle.lower(),
                    raw=raw,
                )
            raise ValueError(f"alvo invalido: {s!r}")

        if host == "youtu.be":
            if not path or "/" in path or not _YT_ID_RE.fullmatch(path):
                raise ValueError(f"alvo invalido: {s!r}")
            return Alvo(
                plataforma="youtube",
                chave=f"yt_{path}",
                url=f"https://www.youtube.com/watch?v={path}",
                canal=path.lower(),
                video_id=path,
                raw=raw,
            )

        raise ValueError(f"alvo invalido: {s!r}")

    canal = raw.lstrip("@").lower()
    if not monitor.CHANNEL_RE.fullmatch(canal):
        raise ValueError(f"alvo invalido: {s!r}")
    return Alvo(
        plataforma="twitch",
        chave=canal,
        url=f"https://www.twitch.tv/{canal}",
        canal=canal,
        raw=raw,
    )


def dedup_alvos(alvos: list[Alvo]) -> list[Alvo]:
    seen: set[tuple[str, str]] = set()
    unique: list[Alvo] = []
    for alvo in alvos:
        k = (alvo.plataforma, alvo.chave)
        if k not in seen:
            seen.add(k)
            unique.append(alvo)
    return unique


def resolve_max_lives(
    out_root: Path,
    cpus: int | None = None,
    *,
    max_lives_fn: Callable[[Path, int | None], int] | None = None,
) -> int:
    raw = os.environ.get("CLIPES_MAX_LIVES", "").strip()
    if raw:
        try:
            val = int(raw)
        except ValueError:
            val = 0
        if val >= 1:
            return val
    ml_fn = max_lives_fn or perfil_max_lives
    return int(ml_fn(out_root, cpus))


def _is_window_job(job: dict[str, Any]) -> bool:
    """Identifica jobs de janela criados por LiveSession (diferente de picos do monitor)."""
    return all(k in job for k in ("window_start", "window_end", "video_start", "last"))


class _SessionQueueView(monitor.DiskQueue):
    """Visao da DiskQueue que so reivindica jobs de janela das sessoes deste Supervisor."""

    def __init__(self, queue: monitor.DiskQueue, own_keys: Callable[[], set[str]]) -> None:
        self._queue = queue
        self.root = queue.root
        self._own_keys = own_keys

    def __getattr__(self, name: str) -> Any:
        return getattr(self._queue, name)

    def claim_next(self) -> Path | None:
        own = self._own_keys()
        pending = []
        for job_dir in (self.root / "pending").iterdir():
            try:
                job = monitor._read_job(job_dir)
                skey = job.get("session_key") or job.get("channel")
                if skey not in own or not _is_window_job(job):
                    continue
                pending.append((
                    -float(job.get("z_score") or 0.0),
                    float(job.get("created_at") or 0.0),
                    job_dir.name,
                    job_dir,
                ))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        mutex = getattr(self._queue, "mutex", None)
        for *_, job_dir in sorted(pending):
            try:
                if mutex is not None:
                    with mutex:
                        job = monitor._read_job(job_dir)
                        job["status"] = "processing"
                        monitor._write_job(job_dir, job)
                        return self._move(job_dir, "processing")
                job = monitor._read_job(job_dir)
                job["status"] = "processing"
                monitor._write_job(job_dir, job)
                return self._move(job_dir, "processing")
            except OSError:
                continue
        return None


class LiveSession:
    """One active live recording session feeding aligned windows into the shared DiskQueue."""

    def __init__(
        self,
        alvo: Alvo,
        out_root: Path,
        queue: monitor.DiskQueue,
        pool: monitor.RollingRecorderPool,
        pipeline: Callable[[Path, Path], dict[str, Any]],
        *,
        is_live: Callable[[str], bool],
        now: Callable[[], float],
        disk_usage: Callable[[Any], Any],
        step: int = monitor.LIVE_STEP_S,
        max_dur: float = 60.0,
        min_free_gb: float | None = None,
        coord_getter: Callable[[], Any | None] | None = None,
        on_mongo_error: Callable[[BaseException], None] | None = None,
        live_id: str | None = None,
        pending_live_id: str | None = None,
        pending_stream_id: str | None = None,
        has_coord: bool = False,
        on_backlog_lost: Callable[[Alvo, str], None] | None = None,
        captura_morta_s: float | None = None,
    ) -> None:
        self.alvo = alvo
        self.key = alvo.chave
        self.out_root = out_root
        self.queue = queue
        self.pool = pool
        self.pipeline_fn = pipeline
        self.is_live_fn = is_live
        self.now = now
        self.disk_usage = disk_usage
        self.step = step
        self.pad = max_dur / 2
        self.min_free = float(os.environ.get("CLIPES_MIN_FREE_GB", "5") if min_free_gb is None else min_free_gb)
        self.coord_getter = coord_getter or (lambda: None)
        self.on_mongo_error = on_mongo_error or (lambda _exc: None)
        self.live_id = live_id
        self.pending_live_id = pending_live_id
        self.pending_stream_id = pending_stream_id
        self.has_coord = has_coord
        self.on_backlog_lost = on_backlog_lost
        self.captura_morta_s = _captura_morta_s() if captura_morta_s is None else float(captura_morta_s)
        self.emitted_path = out_root / f".emitted_{sanitize_id(self.key)}.json"
        self.jobs: list[str] = []
        self.t0 = 0.0
        self.next_e = 0.0
        self.last_check = 0.0
        self.live_ok = True
        self.since: dict[str, float] = {}
        self.live_ended = False
        self._restart_attempts = 0
        self._next_restart_at = float("-inf")
        self._restart_newest: float | None = None
        self._last_window_seg: int | None = None
        self._lease_lost_event = threading.Event()

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost_event.is_set()

    @lease_lost.setter
    def lease_lost(self, value: bool) -> None:
        if value:
            self._lease_lost_event.set()
        else:
            self._lease_lost_event.clear()

    def start(self) -> None:
        if not self.pool.start_on_demand(self.key):
            raise RuntimeError(f"nao foi possivel gravar {self.key}")
        self.t0 = self.now()
        self.pool.hold(self.key, self.t0)
        self.next_e = window_end(self.t0, self.step) + self.step
        self.last_check = self.t0 - monitor.LIVE_IS_LIVE_EVERY_S
        self.live_ok = True
        self.since = {}
        self.live_ended = False
        self._restart_attempts = 0
        self._next_restart_at = float("-inf")
        self._restart_newest = None
        self._last_window_seg = None

    def pending(self) -> list[Path]:
        found = []
        for d in (self.queue.root / "pending").iterdir():
            try:
                job = monitor._read_job(d)
                skey = job.get("session_key") or job.get("channel")
                if skey == self.key:
                    found.append(d)
            except (OSError, ValueError):
                continue
        return sorted(found, key=lambda d: d.name)

    def pipeline(self, job_dir: Path, root: Path) -> dict[str, Any]:
        job = monitor._read_job(job_dir)
        result = self.pipeline_fn(job_dir, root)
        vs = job["video_start"]
        mine = [
            c for c in result.get("clips") or []
            if owns(vs + (c["start"] + c["end"]) / 2, job["window_start"], job["window_end"], self.step, self.pad, job["last"])
        ]
        keep = filter_emitted([(vs + c["start"], vs + c["end"]) for c in mine], self.emitted_path)
        return {**result, "clips": [c for c, ok in zip(mine, keep) if ok]}

    def fechar_janela(self, e: float, last: bool) -> None:
        if self.lease_lost:
            return
        start_ts = e - monitor.LIVE_WINDOW_S
        job_id = sanitize_id(f"{self.key}_{int(e)}")
        job_dir = self.queue.root / ".staging" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        try:
            job: dict[str, Any] = {
                "job_id": job_id,
                "channel": self.alvo.canal,
                "session_key": self.key,
                "url": self.alvo.url,
                "platform": self.alvo.plataforma,
                "peak_ts": e,
                "z_score": 0.0,
                "created_at": self.now(),
                "ready_at": None,
                "score": None,
                "status": "pending",
                "window_start": start_ts,
                "window_end": e,
                "last": last,
                "video_start": start_ts,
            }
            duration = 0.0
            try:
                job["video_start"] = self.pool.video_start_ts(self.key, start_ts, e)
                self.pool.freeze_and_concat(self.key, start_ts, e, job_dir / "source.mkv")
                duration = monitor.probe_duration(job_dir / "source.mkv")
            except ValueError:
                job.update(status="rejected", reject_reason="no_segments")
            else:
                if duration < monitor.MIN_CLIP_SOURCE_S:
                    job.update(status="rejected", reject_reason="too_short")
            if job["status"] == "rejected":
                (job_dir / "source.mkv").unlink(missing_ok=True)
            (job_dir / "meta.json").write_text(
                json.dumps(
                    {
                        "id": job_id,
                        "title": f"{self.alvo.canal} - live",
                        "url": self.alvo.url,
                        "extractor": self.alvo.plataforma,
                        "channel": self.alvo.canal,
                        "is_live": False,
                        "duration": duration,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if self.lease_lost:
                shutil.rmtree(job_dir, ignore_errors=True)
                return

            c_active = self.coord_getter()
            if c_active is not None and self.live_id is not None and job["status"] != "rejected":
                mongo_job_id = f"{self.live_id}:{int(e)}"
                try:
                    added = c_active.add_job(mongo_job_id, self.live_id, (start_ts, e), 0.0)
                except Exception as exc:
                    if not monitor._is_mongo_error(exc):
                        raise
                    self.on_mongo_error(exc)
                    job.update(live_id=self.live_id, mongo_job_id=mongo_job_id, coord_synced=False)
                else:
                    if not added:
                        shutil.rmtree(job_dir, ignore_errors=True)
                        return
                    job.update(live_id=self.live_id, mongo_job_id=mongo_job_id, coord_synced=True)
            elif self.has_coord and job["status"] != "rejected":
                job["coord_synced"] = False
                lid = self.live_id or self.pending_live_id
                if lid is not None:
                    job.update(live_id=lid, mongo_job_id=f"{lid}:{int(e)}")

            if self.lease_lost:
                shutil.rmtree(job_dir, ignore_errors=True)
                return

            monitor._write_job(job_dir, job)
            self.queue.enqueue(job_dir)
            self.jobs.append(job_id)
            self.pool.hold(self.key, e + self.step - monitor.LIVE_WINDOW_S)
        except BaseException:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

        backlog = self.pending()
        if len(backlog) > monitor.LIVE_MAX_BACKLOG:
            print(f"[live] backlog {len(backlog)} > {monitor.LIVE_MAX_BACKLOG}: {backlog[0].name} lost", flush=True)
            lost_dir = backlog[0]
            try:
                lost_job = monitor._read_job(lost_dir)
            except (OSError, ValueError):
                lost_job = {}
            self.queue.complete(lost_dir, "rejected", {"reject_reason": "lost", "lost": True})
            c_lost = self.coord_getter()
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
                    if not monitor._is_mongo_error(exc):
                        raise
                    self.on_mongo_error(exc)
            if self.on_backlog_lost is not None:
                self.on_backlog_lost(self.alvo, lost_dir.name)

    def tick(self, t: float) -> bool:
        """Advance session at clock `t`; returns False when live has ended."""
        if self.lease_lost:
            return True
        if t - self.last_check >= monitor.LIVE_IS_LIVE_EVERY_S:
            self.last_check = t
            try:
                self.live_ok = bool(self.is_live_fn(self.alvo.url))
            except Exception:  # unknown is not an end signal
                self.live_ok = True
        if self.lease_lost:
            return True

        segs = self.pool.segments(self.key)
        newest = segs[-1][0] + monitor.SEGMENT_S if segs else self.t0
        exited = not self.pool.is_recording(self.key)

        if not exited:
            # So zera o backoff quando aparecer segmento novo apos o restart
            if self._restart_newest is not None and newest > self._restart_newest:
                self._restart_attempts = 0
                self._next_restart_at = float("-inf")
                self._restart_newest = None
        elif self.live_ok and t >= self._next_restart_at:
            restart_fn = getattr(self.pool, "restart_on_demand", None)
            if callable(restart_fn):
                if self._restart_newest is not None and newest > self._restart_newest:
                    self._restart_attempts = 0
                delay = min(RECORDER_RESTART_MAX_S, RECORDER_RESTART_BASE_S * (2 ** self._restart_attempts))
                self._restart_attempts += 1
                self._next_restart_at = t + delay
                self._restart_newest = newest
                restarted = bool(restart_fn(self.key))
                print(
                    f"[live] gravador {self.key} caiu com live ativa; reinicio {'ok' if restarted else 'falhou'} "
                    f"(tentativa {self._restart_attempts}, proximo backoff {delay:g}s)",
                    flush=True,
                )

        # Mantem o sinal 'captura' ligado (preservando a 1a queda) enquanto houver restart sem segmento novo
        awaiting_new_seg = self._restart_newest is not None and newest <= self._restart_newest
        silent = t - newest >= monitor.LIVE_NO_SEGMENT_S
        captura_falha = exited or silent or awaiting_new_seg
        signals = {
            "captura": captura_falha,
            "offline": not self.live_ok,
        }
        for k, v in signals.items():
            if v:
                self.since.setdefault(k, t)
            else:
                self.since.pop(k, None)

        offline_since = self.since.get("offline")
        captura_since = self.since.get("captura")
        # Fim confirmado pela plataforma (is_live=False): offline + captura ou offline persistindo
        if offline_since is not None and (
            captura_since is not None or t - offline_since >= monitor.LIVE_END_PERSIST_S
        ):
            self.live_ended = True
            return False
        # Saida de seguranca: captura morta por muito tempo sem offline confirmado -> encerra sem live_ended
        if captura_since is not None and t - captura_since >= self.captura_morta_s:
            print(
                f"[live] captura morta ha {t - captura_since:.0f}s sem confirmacao offline ({self.key}); "
                "encerrando sem ended",
                flush=True,
            )
            self.live_ended = False
            return False

        while t >= self.next_e:
            if self.lease_lost:
                break
            last_seg = segs[-1][0] if segs else None
            has_new_seg = last_seg is not None and (
                self._last_window_seg is None or last_seg > self._last_window_seg
            )
            if captura_falha and not has_new_seg:
                print(f"[live] janela pulada: captura sem segmento novo ({self.key})", flush=True)
            elif self.disk_usage(self.out_root).free < self.min_free * 1e9:
                print("[live] janela pulada: disco", flush=True)
            else:
                self.fechar_janela(float(self.next_e), last=False)
                if last_seg is not None:
                    covered = min(last_seg, int(self.next_e))
                    if self._last_window_seg is None or covered > self._last_window_seg:
                        self._last_window_seg = covered
            self.next_e += self.step
        return True

    def encerrar(self) -> None:
        if not self.lease_lost:
            segs = self.pool.segments(self.key)
            e = min(self.now(), segs[-1][0] + monitor.SEGMENT_S) if segs else 0.0
            if e - (self.next_e - self.step) >= monitor.MIN_CLIP_SOURCE_S:
                self.fechar_janela(e, last=True)
        self.pool.release_hold(self.key)
        self.pool.release_on_demand(self.key)


@contextmanager
def _exit_signals_to_kb() -> Iterator[None]:
    """Convert SIGTERM/SIGHUP into KeyboardInterrupt on the main thread."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _raise_kb(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    prev: list[tuple[int, Any]] = []
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            old = signal.signal(sig, _raise_kb)
            prev.append((sig, old))
        except (OSError, ValueError):
            pass
    try:
        yield
    finally:
        for sig, old in prev:
            try:
                signal.signal(sig, old)
            except (OSError, ValueError):
                pass


class Supervisor:
    """Supervise up to L simultaneous live sessions sharing 1 DiskQueue and 1 worker."""

    def __init__(
        self,
        alvos: list[Alvo],
        out_root: Path,
        *,
        queue_root: Path | None = None,
        pool: monitor.RollingRecorderPool | None = None,
        pipeline: Callable[[Path, Path], dict[str, Any]] | None = None,
        is_live: Callable[[str], bool] | None = None,
        probe: Callable[[str], dict[str, Any]] | None = None,
        coord: Any | None = None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], Any] = time.sleep,
        disk_usage: Callable[[Any], Any] = shutil.disk_usage,
        step: int = monitor.LIVE_STEP_S,
        max_dur: float = 60.0,
        min_free_gb: float | None = None,
        policy: monitor.ChannelPolicy | None = None,
        stream_id: str | None = None,
        capacity_fn: Callable[[Path], dict[str, Any]] | None = None,
        max_lives_fn: Callable[[Path, int | None], int] | None = None,
        limite_fn: Callable[[], int] | None = None,
        aviso_fn: Callable[[str], None] | None = None,
        reraise_start_error: bool = False,
        beat_s: float | None = None,
        kill_children: Callable[[], None] | None = None,
    ) -> None:
        self.alvos = dedup_alvos(alvos)
        self.out_root = Path(out_root)
        self.queue_root = Path(queue_root) if queue_root is not None else self.out_root / "queue"
        self.pool = pool
        self.pipeline = pipeline
        self.is_live_arg = is_live
        self.probe_arg = probe
        self.coord = coord
        self.now = now
        self.sleep = sleep
        self.disk_usage = disk_usage
        self.step = step
        self.max_dur = max_dur
        self.min_free_gb = min_free_gb
        self.policy = policy or monitor.ChannelPolicy()
        self.stream_id = stream_id
        self.cap_fn = capacity_fn
        self.ml_fn = max_lives_fn
        self.limite_fn = limite_fn
        self.aviso_fn = aviso_fn or post_aviso
        self.reraise_start_error = reraise_start_error
        self.beat_s = beat_s
        self.kill_children = kill_children

        self._lock = threading.Lock()
        self._active: dict[str, LiveSession] = {}
        self._all_sessions: dict[str, LiveSession] = {}
        self._aguardando: list[tuple[Alvo, str]] = []
        self._avisados: set[tuple[str, str]] = set()
        self._lost_lives: set[str] = set()
        self._pending_lease_lost: set[str] = set()
        self._alvo_by_key: dict[str, Alvo] = {a.chave: a for a in self.alvos}
        self._mongo_offline_until = float("-inf")
        self._need_replay = self.coord is not None
        self._current_job: list[str | None] = [None]
        self._jobs: list[str] = []
        self._last_refusal_reason: str | None = None
        self._saw_blocked = False
        self._saw_not_claimed = False
        self._saw_lease_lost = False

    def _coord_active(self) -> Any | None:
        if self.coord is None or self.now() < self._mongo_offline_until:
            return None
        return self.coord

    def _on_mongo_err(self, exc: BaseException) -> None:
        self._mongo_offline_until = self.now() + monitor.MONGO_RECONNECT_S
        self._need_replay = True
        uri = os.environ.get("CLIPES_MONGO_URI", "").strip() or None
        print(f"[fila] Mongo indisponivel ({monitor._sanitize_error(exc, uri)}); seguindo local", flush=True)

    def _replay_unsynced(self, queue: monitor.DiskQueue, c_active: Any) -> None:
        """Reenvia ao Mongo os jobs locais com coord_synced=False quando o Coord esta ativo (B-01)."""
        if not self._need_replay:
            return
        with self._lock:
            sessions = list(self._all_sessions.values())
            active = set(self._active.values())
            lost = set(self._lost_lives)
        skip = set(lost)
        remap: dict[str, str] = {}
        has_unclaimed = False
        for sess in sessions:
            if sess.live_id is None:
                if not sess.lease_lost and sess in active:
                    has_unclaimed = True
                if sess.pending_live_id:
                    skip.add(sess.pending_live_id)
                skip.add(f"twitch:{sess.alvo.canal}")
            else:
                remap[f"twitch:{sess.alvo.canal}"] = sess.live_id
        fn = monitor.replay_unsynced_jobs
        try:
            params = inspect.signature(fn).parameters
            has_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            kwargs: dict[str, Any] = {}
            if has_kw or "skip_lives" in params:
                kwargs["skip_lives"] = skip
            if has_kw or "out_root" in params:
                kwargs["out_root"] = self.out_root
            if has_kw or "live_remap" in params:
                kwargs["live_remap"] = remap
            fn(queue, c_active, **kwargs)
        except Exception as exc:
            if not monitor._is_mongo_error(exc):
                raise
            self._on_mongo_err(exc)
        else:
            if not has_unclaimed:
                self._need_replay = False

    def _cap(self) -> dict[str, Any]:
        if self.cap_fn is not None:
            return self.cap_fn(self.out_root)
        if self.coord is not None:
            from .capacidade import capacity as default_cap

            self.cap_fn = default_cap
            return default_cap(self.out_root)
        return {"cpus": None, "score": 1}

    def _limite(self) -> int:
        if self.limite_fn is not None:
            return int(self.limite_fn())
        cap = self._cap()
        return resolve_max_lives(self.out_root, cap.get("cpus"), max_lives_fn=self.ml_fn)

    def _recusar(self, alvo: Alvo, motivo: str) -> None:
        self._last_refusal_reason = motivo
        key = (alvo.chave, motivo)
        if key not in self._avisados:
            self._avisados.add(key)
            label = alvo.raw or alvo.chave
            texto = f"[acompanhar] recusada {label}: {motivo}"
            print(texto, flush=True)
            self.aviso_fn(texto)

    def _label(self, alvo: Alvo) -> str:
        return alvo.raw or alvo.chave

    def _probe_youtube(self, alvo: Alvo, probe_fn: Callable[[str], dict[str, Any]]) -> bool:
        """Populate YouTube video_id/channel_id/handle and return True if live."""
        need_probe = (
            self.probe_arg is not None
            or alvo.video_id is None
            or self.coord is not None
            or self.is_live_arg is None
        )
        if not need_probe:
            return True
        try:
            meta = probe_fn(alvo.url)
        except Exception:
            print(f"[acompanhar] {self._label(alvo)} offline; ignorado", flush=True)
            return False
        if not isinstance(meta, dict):
            print(f"[acompanhar] {self._label(alvo)} offline; ignorado", flush=True)
            return False
        if meta.get("id"):
            alvo.video_id = str(meta["id"])
            alvo.chave = f"yt_{alvo.video_id}"
            alvo.url = f"https://www.youtube.com/watch?v={alvo.video_id}"
        if meta.get("channel_id"):
            alvo.channel_id = str(meta["channel_id"])
        handle = str(meta.get("uploader_id") or meta.get("channel") or "").strip().lstrip("@").lower()
        if handle:
            alvo.canal = handle
        elif alvo.channel_id:
            alvo.canal = alvo.channel_id.lower()
        if meta.get("is_live") is False:
            print(f"[acompanhar] {self._label(alvo)} offline; ignorado", flush=True)
            return False
        return True

    def _try_start_alvo(
        self,
        alvo: Alvo,
        limite: int,
        queue: monitor.DiskQueue,
        pool: monitor.RollingRecorderPool,
        pipeline_fn: Callable[[Path, Path], dict[str, Any]],
        is_live_fn: Callable[[str], bool],
        probe_fn: Callable[[str], dict[str, Any]],
        *,
        already_probed: bool = False,
    ) -> str:
        if alvo.plataforma == "youtube" and not already_probed:
            if not self._probe_youtube(alvo, probe_fn):
                return "skip"
            if alvo.chave in self._all_sessions or any(a.chave == alvo.chave for a, _ in self._aguardando):
                return "skip"
            self._alvo_by_key[alvo.chave] = alvo

        if not self.policy.permits(alvo.canal):
            self._saw_blocked = True
            print(f"[acompanhar] {self._label(alvo)} bloqueado pela policy", flush=True)
            return "skip"

        if already_probed or self.is_live_arg is not None or alvo.plataforma == "twitch":
            try:
                live_now = bool(is_live_fn(alvo.url))
            except Exception:
                live_now = True
            if not live_now:
                print(f"[acompanhar] {self._label(alvo)} offline; ignorado", flush=True)
                return "skip"

        if limite < 1 or len(self._active) >= limite:
            self._saw_not_claimed = True
            self._recusar(alvo, f"limite max_lives={limite} (rode bench-live ou defina CLIPES_MAX_LIVES)")
            return "aguardando"

        live_id: str | None = None
        pending_live_id: str | None = None
        pending_stream_id: str | None = None
        c_active = self._coord_active()
        if c_active is not None:
            cap = self._cap()
            if alvo.plataforma == "twitch":
                sid = self.stream_id or monitor._resolve_stream_id(alvo.canal, probe=probe_fn)
                if not sid:
                    self._saw_not_claimed = True
                    self._recusar(alvo, "stream_id indisponivel")
                    return "aguardando"
                live_id = f"twitch:{alvo.canal}:{sid}"
            else:
                sid = alvo.video_id or alvo.chave
                live_id = f"youtube:{alvo.channel_id or alvo.canal}:{sid}"

            try:
                count_fn = getattr(c_active, "count_owned_live", None)
                if callable(count_fn):
                    with self._lock:
                        active_ids = {s.live_id for s in self._active.values() if s.live_id}
                    other_owned = int(count_fn(exclude=active_ids | {live_id}))
                    if len(self._active) + other_owned >= limite:
                        self._saw_not_claimed = True
                        self._recusar(alvo, f"limite max_lives={limite} (rode bench-live ou defina CLIPES_MAX_LIVES)")
                        return "aguardando"
                c_active.ensure_live(live_id, channel=alvo.canal, platform=alvo.plataforma, stream_id=sid)
                monitor._wait_claim_sync(30.0 / max(1.0, float(cap.get("score") or 1)), now=self.now, sleep=self.sleep)
                claimed = c_active.claim_live(live_id)
            except Exception as exc:
                if not monitor._is_mongo_error(exc):
                    raise
                self._on_mongo_err(exc)
                pending_live_id = live_id
                pending_stream_id = sid
                live_id = None
            else:
                if not claimed:
                    self._saw_not_claimed = True
                    self._recusar(alvo, "lease com outro PC")
                    return "aguardando"
        elif self.coord is not None:
            # Mongo em backoff na abertura: guarda live_id pretendido para tentar claim ao reconectar (M-01)
            if alvo.plataforma == "youtube":
                pending_stream_id = alvo.video_id or alvo.chave
                pending_live_id = f"youtube:{alvo.channel_id or alvo.canal}:{pending_stream_id}"
            elif self.stream_id:
                pending_stream_id = self.stream_id
                pending_live_id = f"twitch:{alvo.canal}:{self.stream_id}"

        sess = LiveSession(
            alvo,
            self.out_root,
            queue,
            pool,
            pipeline_fn,
            is_live=is_live_fn,
            now=self.now,
            disk_usage=self.disk_usage,
            step=self.step,
            max_dur=self.max_dur,
            min_free_gb=self.min_free_gb,
            coord_getter=self._coord_active,
            on_mongo_error=self._on_mongo_err,
            live_id=live_id,
            pending_live_id=pending_live_id,
            pending_stream_id=pending_stream_id,
            has_coord=self.coord is not None,
            on_backlog_lost=lambda a, _name: self.aviso_fn(f"janela perdida {self._label(a)} (fila cheia)"),
        )
        self._alvo_by_key[sess.key] = alvo
        try:
            sess.start()
        except RuntimeError as exc:
            if live_id is not None and self.coord is not None:
                try:
                    self.coord.release(live_id, False)
                except Exception as mexc:
                    if not monitor._is_mongo_error(mexc):
                        raise
            if self.reraise_start_error:
                raise
            msg = f"[acompanhar] erro ao gravar {self._label(alvo)}: {exc}"
            print(msg, flush=True)
            self.aviso_fn(msg)
            return "skip"

        with self._lock:
            self._active[sess.key] = sess
        self._all_sessions[sess.key] = sess
        return "started"

    def _call_beat(
        self,
        c: Any,
        alias: str,
        cap: dict[str, Any],
        job_atual: str | None,
        ml: int,
        razao: float,
        beat_s: float,
    ) -> None:
        beat_fn = getattr(c, "beat", None)
        if not callable(beat_fn):
            return
        try:
            params = inspect.signature(beat_fn).parameters
            accepts_beat_s = "beat_s" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):
            accepts_beat_s = True
        if accepts_beat_s:
            beat_fn(alias, cap, job_atual, ml, razao, beat_s=beat_s)
        else:
            beat_fn(alias, cap, job_atual, ml, razao)

    def _handle_lease_lost(self, key: str, sess: LiveSession, pool: Any, *, msg: str | None = None) -> None:
        lid = sess.live_id or sess.pending_live_id
        if lid is not None:
            print(msg or f"[fila] lease perdida {lid}", flush=True)
            self._lost_lives.add(lid)
        if hasattr(pool, "release_hold"):
            pool.release_hold(key)
        if hasattr(pool, "stop_channel"):
            pool.stop_channel(key)
        else:
            pool.release_on_demand(key)
        sess.lease_lost = True
        sess.live_ended = False
        sess.pending_live_id = None
        self._saw_lease_lost = True
        with self._lock:
            self._active.pop(key, None)
            self._pending_lease_lost.discard(key)

    def _drain_lost_leases(self, pool: Any) -> bool:
        """Aplica no loop principal as perdas de lease registradas pela thread de keepalive."""
        with self._lock:
            lost_keys = set(self._pending_lease_lost)
            for k, s in self._active.items():
                if s.lease_lost:
                    lost_keys.add(k)
            if not lost_keys:
                return False
            self._pending_lease_lost.clear()
        changed = False
        for key in lost_keys:
            with self._lock:
                sess = self._active.get(key)
            if sess is not None:
                self._handle_lease_lost(key, sess, pool)
                changed = True
        return changed

    def _try_claim_pending(
        self,
        c_active: Any,
        key: str,
        sess: LiveSession,
        pool: Any,
        probe_fn: Callable[[str], dict[str, Any]],
    ) -> bool | None:
        """Tenta reivindicar a lease de sessao iniciada com o Mongo offline (M-01)."""
        if not sess.has_coord or sess.live_id is not None:
            return None
        if not (hasattr(c_active, "ensure_live") and hasattr(c_active, "claim_live")):
            return None
        lid = sess.pending_live_id
        sid = sess.pending_stream_id
        if lid is None:
            if sess.alvo.plataforma == "twitch":
                sid = self.stream_id or monitor._resolve_stream_id(sess.alvo.canal, probe=probe_fn)
                if not sid:
                    return None
                lid = f"twitch:{sess.alvo.canal}:{sid}"
            else:
                sid = sess.alvo.video_id or sess.alvo.chave
                lid = f"youtube:{sess.alvo.channel_id or sess.alvo.canal}:{sid}"
            sess.pending_live_id = lid
            sess.pending_stream_id = sid

        c_active.ensure_live(lid, channel=sess.alvo.canal, platform=sess.alvo.plataforma, stream_id=sid)
        if c_active.claim_live(lid):
            sess.live_id = lid
            sess.pending_live_id = None
            sess.pending_stream_id = None
            self._need_replay = True
            return True

        self._handle_lease_lost(key, sess, pool, msg=f"[fila] lease perdida {lid} (negada apos reconexao)")
        return False

    def _promote_aguardando(
        self,
        queue: monitor.DiskQueue,
        pool: monitor.RollingRecorderPool,
        pipeline_fn: Callable[[Path, Path], dict[str, Any]],
        is_live_fn: Callable[[str], bool],
        probe_fn: Callable[[str], dict[str, Any]],
    ) -> None:
        if not self._aguardando:
            return
        limite = self._limite()
        still_waiting: list[tuple[Alvo, str]] = []
        for idx, (alvo, motivo) in enumerate(self._aguardando):
            if len(self._active) >= limite:
                still_waiting.extend(self._aguardando[idx:])
                break
            status = self._try_start_alvo(
                alvo, limite, queue, pool, pipeline_fn, is_live_fn, probe_fn,
                already_probed=(alvo.plataforma == "youtube"),
            )
            if status == "aguardando":
                still_waiting.append((alvo, self._last_refusal_reason or motivo))
        self._aguardando = still_waiting

    def _own_session_keys(self, *, skip_lost: bool = False) -> set[str]:
        if not skip_lost:
            return set(self._all_sessions.keys())
        return {k for k, s in self._all_sessions.items() if not s.lease_lost}

    def _has_own_pending(self, queue: monitor.DiskQueue, *, skip_lost: bool = False) -> bool:
        own = self._own_session_keys(skip_lost=skip_lost)
        for d in (queue.root / "pending").iterdir():
            try:
                job = monitor._read_job(d)
                skey = job.get("session_key") or job.get("channel")
                if skey in own and _is_window_job(job):
                    return True
            except (OSError, ValueError):
                continue
        return False

    def _process_one(
        self,
        queue: monitor.DiskQueue,
        pipeline_fn: Callable[[Path, Path], dict[str, Any]],
        *,
        skip_lost: bool = True,
    ) -> bool:
        def dispatch_pipeline(job_dir: Path, root: Path) -> dict[str, Any]:
            job = monitor._read_job(job_dir)
            skey = job.get("session_key") or job.get("channel")
            sess = self._all_sessions.get(skey) if isinstance(skey, str) else None
            if sess is None or not _is_window_job(job):
                raise RuntimeError(f"job sem sessao ativa neste Supervisor: {skey!r}")
            if skip_lost and sess.lease_lost:
                raise RuntimeError(f"job de sessao com lease perdida: {skey!r}")
            return sess.pipeline(job_dir, root)

        scoped_queue = _SessionQueueView(queue, lambda: self._own_session_keys(skip_lost=skip_lost))
        return monitor.run_worker_step(
            scoped_queue,
            self.out_root,
            self.policy,
            dispatch_pipeline,
            coord=self._coord_active(),
            on_mongo_error=self._on_mongo_err,
            lost_lives=self._lost_lives,
            on_job_active=lambda jid: self._current_job.__setitem__(0, jid),
        )

    def _release_session(
        self,
        key: str,
        sess: LiveSession,
        *,
        release_pool: bool = True,
        reraise_non_mongo: bool = True,
    ) -> None:
        if release_pool:
            for fn in (sess.pool.release_hold, sess.pool.release_on_demand):
                try:
                    fn(key)
                except Exception:
                    pass
        if self.coord is not None and sess.live_id is not None:
            try:
                self.coord.release(sess.live_id, sess.live_ended)
            except Exception as exc:
                if not monitor._is_mongo_error(exc):
                    if reraise_non_mongo:
                        raise
                    print(f"[acompanhar] erro ao liberar lease {sess.live_id}: {exc}", flush=True)
                else:
                    self._on_mongo_err(exc)

    def run(self) -> dict[str, Any]:
        self.out_root.mkdir(parents=True, exist_ok=True)
        queue = monitor.DiskQueue(self.queue_root)
        lock = monitor._lock_worker(queue.root)
        pool: Any | None = self.pool
        stop_hb: threading.Event | None = None
        hb_thread: threading.Thread | None = None

        def _stop_keepalive() -> None:
            if stop_hb is not None:
                stop_hb.set()
            if hb_thread is not None and hb_thread.is_alive():
                hb_thread.join(timeout=5.0)

        try:
            with _exit_signals_to_kb():
                queue.recover_orphans()
                pipeline_fn = self.pipeline or monitor.run_local_pipeline
                probe_fn = self.probe_arg or monitor.probe_metadata
                is_live_fn = self.is_live_arg or (lambda u: bool(probe_fn(u).get("is_live")))

                if pool is None:
                    shutil.rmtree(queue.root / ".buffer", ignore_errors=True)  # sobras de execucao anterior (B-25)

                    def _source_cmd(key: str) -> list[str]:
                        alvo = self._alvo_by_key.get(key)
                        if alvo is not None and alvo.plataforma == "youtube":
                            return monitor.ytdlp_live_cmd(alvo.url)
                        return monitor.streamlink_cmd(key)

                    pool = monitor.RollingRecorderPool(
                        queue.root / ".buffer",
                        max_hot=0,
                        max_on_demand=max(1, len(self.alvos)),
                        buffer_seconds=monitor.LIVE_WINDOW_S + 2 * self.step,
                        source_cmd=_source_cmd,
                    )

                worker_alias = os.environ.get("CLIPES_ALIAS", "").strip()
                if self.coord is not None and not worker_alias:
                    from .capacidade import worker_id as get_worker_id

                    worker_alias = get_worker_id(self.out_root)
                if self.beat_s is not None:
                    beat_interval = max(0.01, float(self.beat_s))
                else:
                    beat_interval = max(1.0, float(os.environ.get("CLIPES_BEAT_S", "20")))
                last_beat = float("-inf")

                # A-02: solta lives proprias com lease vencida (sobras de crash) na inicializacao
                c_init = self._coord_active()
                if c_init is not None:
                    release_exp = getattr(c_init, "release_expired_own", None)
                    if callable(release_exp):
                        try:
                            release_exp()
                        except Exception as exc:
                            if not monitor._is_mongo_error(exc):
                                raise
                            self._on_mongo_err(exc)
                    # B-01: sincroniza jobs locais pendentes de execucao anterior / offline
                    c_init = self._coord_active()
                    if c_init is not None:
                        self._replay_unsynced(queue, c_init)

                # A-03: keepalive continuo durante todo o run() (beat + renew das sessoes ativas)
                if self.coord is not None:
                    stop_hb = threading.Event()

                    def _keepalive() -> None:
                        while not stop_hb.wait(beat_interval):
                            c = self._coord_active()
                            if c is None:
                                continue
                            try:
                                cap_k = self._cap()
                                ml_k = max(
                                    1,
                                    int(resolve_max_lives(self.out_root, cap_k.get("cpus"), max_lives_fn=self.ml_fn)),
                                )
                                razao_k = monitor._read_perfil_razao(self.out_root, cap_k.get("cpus"))
                                self._call_beat(
                                    c, worker_alias, cap_k, self._current_job[0], ml_k, razao_k, beat_interval
                                )
                                with self._lock:
                                    active_snapshot = list(self._active.items())
                                    already_lost = set(self._pending_lease_lost)
                                renew_fn = getattr(c, "renew", None)
                                if callable(renew_fn):
                                    for key, sess in active_snapshot:
                                        lid = sess.live_id
                                        if lid is None or key in already_lost:
                                            continue
                                        with self._lock:
                                            if (
                                                self._active.get(key) is not sess
                                                or key in self._pending_lease_lost
                                                or sess.lease_lost
                                            ):
                                                continue
                                        if not renew_fn(lid):
                                            with self._lock:
                                                if self._active.get(key) is sess:
                                                    sess.lease_lost = True
                                                    self._lost_lives.add(lid)
                                                    self._pending_lease_lost.add(key)
                            except Exception as exc:
                                if monitor._is_mongo_error(exc):
                                    self._on_mongo_err(exc)

                    hb_thread = threading.Thread(target=_keepalive, daemon=True)
                    hb_thread.start()

                def _liberar() -> None:
                    # M-03: para gravadores, mata filhos e solta leases sem marcar ended
                    _stop_keepalive()
                    if pool is not None and hasattr(pool, "stop_all"):
                        try:
                            pool.stop_all()
                        except Exception:
                            pass
                    (self.kill_children or monitor._terminate_child_processes)()
                    with self._lock:
                        active_items = list(self._active.items())
                        self._active.clear()
                    for key, sess in active_items:
                        sess.live_ended = False
                        self._release_session(key, sess, release_pool=True, reraise_non_mongo=False)

                limite = self._limite()
                for alvo in self.alvos:
                    status = self._try_start_alvo(alvo, limite, queue, pool, pipeline_fn, is_live_fn, probe_fn)
                    if status == "aguardando":
                        self._aguardando.append((alvo, self._last_refusal_reason or ""))

                if not self._active:
                    if self._saw_blocked and not self._saw_not_claimed:
                        return {"status": "blocked", "jobs": [], "reason": None}
                    if self._saw_not_claimed:
                        return {"status": "not_claimed", "jobs": [], "reason": self._last_refusal_reason}
                    return {"status": "ended", "jobs": [], "reason": None}

                while self._active:
                    slot_opened = self._drain_lost_leases(pool)
                    if not self._active:
                        if slot_opened and self._aguardando:
                            self._promote_aguardando(queue, pool, pipeline_fn, is_live_fn, probe_fn)
                        if not self._active:
                            break

                    t = self.now()
                    c_active = self._coord_active()
                    if c_active is not None and t - last_beat >= beat_interval:
                        last_beat = t
                        cap_now = self._cap()
                        ml_now = max(
                            1,
                            int(resolve_max_lives(self.out_root, cap_now.get("cpus"), max_lives_fn=self.ml_fn)),
                        )
                        razao_now = monitor._read_perfil_razao(self.out_root, cap_now.get("cpus"))
                        cmd_action: str | None = None
                        try:
                            self._call_beat(
                                c_active, worker_alias, cap_now, self._current_job[0], ml_now, razao_now, beat_interval
                            )
                            with self._lock:
                                active_beat = list(self._active.items())
                            for key, sess in active_beat:
                                if sess.lease_lost:
                                    continue
                                if sess.live_id is None:
                                    if self._try_claim_pending(c_active, key, sess, pool, probe_fn) is False:
                                        slot_opened = True
                                    continue
                                renew_fn = getattr(c_active, "renew", None)
                                if callable(renew_fn) and not renew_fn(sess.live_id):
                                    self._handle_lease_lost(key, sess, pool)
                                    slot_opened = True
                            run_cmd = getattr(c_active, "run_pending_command", None)
                            if callable(run_cmd):
                                cmd_action = run_cmd(_liberar)
                        except Exception as exc:
                            if not monitor._is_mongo_error(exc):
                                raise
                            self._on_mongo_err(exc)
                        else:
                            if cmd_action == "liberar":
                                print("[comando] liberar recursos", flush=True)
                                raise SystemExit(75)

                    c_replay = self._coord_active()
                    if c_replay is not None and self._need_replay:
                        self._replay_unsynced(queue, c_replay)

                    for key, sess in list(self._active.items()):
                        try:
                            ended = not sess.tick(t)
                            with self._lock:
                                lost_now = key in self._pending_lease_lost or sess.lease_lost
                            if lost_now:
                                self._drain_lost_leases(pool)
                                slot_opened = True
                                continue
                            if ended:
                                sess.encerrar()
                        except Exception as exc:
                            canal = sess.alvo.canal or self._label(sess.alvo)
                            print(f"[acompanhar] erro na sessao {canal}: {exc}", flush=True)
                            with self._lock:
                                self._active.pop(key, None)
                                self._pending_lease_lost.discard(key)
                            self._release_session(key, sess, release_pool=True, reraise_non_mongo=False)
                            if self.reraise_start_error:  # live unica (follow_live): o chamador ve a falha
                                raise
                            slot_opened = True
                            continue

                        with self._lock:
                            lost_now = key in self._pending_lease_lost or sess.lease_lost
                        if lost_now:
                            self._drain_lost_leases(pool)
                            slot_opened = True
                            continue

                        if ended:
                            with self._lock:
                                self._active.pop(key, None)
                            self._release_session(key, sess, release_pool=False, reraise_non_mongo=True)
                            slot_opened = True

                    if slot_opened and self._aguardando:
                        self._promote_aguardando(queue, pool, pipeline_fn, is_live_fn, probe_fn)

                    if not self._active:
                        break

                    if not (self._has_own_pending(queue, skip_lost=True) and self._process_one(queue, pipeline_fn, skip_lost=True)):
                        self.sleep(1.0)
                    pool.cleanup(t)
                    if hasattr(pool, "enforce_cap"):
                        pool.enforce_cap(monitor.BUFFER_CAP_BYTES)

                while self._has_own_pending(queue, skip_lost=True):
                    if not self._process_one(queue, pipeline_fn, skip_lost=True):
                        break

                c_end = self._coord_active()
                if c_end is not None and self._need_replay:
                    self._replay_unsynced(queue, c_end)

                all_jobs: list[str] = []
                for sess in self._all_sessions.values():
                    all_jobs.extend(sess.jobs)
                status = "lease_lost" if (self._saw_lease_lost and not any(s.live_ended for s in self._all_sessions.values())) else "ended"
                return {"status": status, "jobs": all_jobs, "reason": None}
        finally:
            try:
                _stop_keepalive()
                for key, sess in list(self._active.items()):
                    self._release_session(key, sess, release_pool=True, reraise_non_mongo=True)
            finally:
                if pool is not None and hasattr(pool, "stop_all"):
                    try:
                        pool.stop_all()
                    except Exception:
                        pass
                lock.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="clipes acompanhar",
        description="Acompanha uma ou mais lives (Twitch/YouTube) em uma fila unica.",
    )
    p.add_argument("alvos", nargs="+", help="Canais/URLs Twitch ou URLs de live do YouTube")
    p.add_argument("--out-dir", default=None, help="Padrao: OUT_DIR ou ./out")
    p.add_argument("--queue-dir", type=Path, default=None, help="Padrao: <out-dir>/queue")
    p.add_argument("--policy-file", type=Path, default=None, help="Padrao: policy.json ao lado de channels.txt")
    p.add_argument("--max-dur", type=float, default=60.0, help="Duracao maxima de cada clipe em segundos")
    args = p.parse_args(argv)

    if args.max_dur <= 0:
        print("[acompanhar] --max-dur deve ser > 0", file=sys.stderr, flush=True)
        return 2

    parsed: list[Alvo] = []
    for raw in args.alvos:
        try:
            parsed.append(parse_alvo(raw))
        except ValueError:
            print(f"[acompanhar] alvo invalido: {raw}", flush=True)
            return 2

    out_root = Path(args.out_dir or os.environ.get("OUT_DIR") or "out").expanduser().resolve()
    queue_root = args.queue_dir.expanduser().resolve() if args.queue_dir else None
    policy = monitor.load_policy(args.policy_file) if args.policy_file else monitor.load_policy(Path("policy.json"))
    try:
        with _exit_signals_to_kb():
            coord = monitor._connect_coord_for_follow(out_root) if os.environ.get("CLIPES_MONGO_URI", "").strip() else None

            sup = Supervisor(
                parsed,
                out_root,
                queue_root=queue_root,
                max_dur=args.max_dur,
                policy=policy,
                coord=coord,
            )
            sup.run()
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        print(f"[acompanhar] {exc}", flush=True)
        return 1
    return 0
