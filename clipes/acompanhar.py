"""Supervisor multi-live (`acompanhar`): acompanha N lives (Twitch/YouTube/Kick) com 1 fila e 1 worker."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import config, monitor
from .executor_publicacao import PublishLoop
from .ingest import sanitize_id
from .janelas import filter_emitted, owns, window_end
from .notificar import _write_json_atomic, post_aviso
from .perfil import max_lives as perfil_max_lives

_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{4,32}")
_YT_HANDLE_RE = re.compile(r"@([A-Za-z0-9_.-]{1,64})")
_KICK_SLUG_RE = re.compile(r"[a-z0-9_-]{1,25}")
_KICK_RESERVED = frozenset({"video", "videos", "categories", "search", "auth", "clips"})  # espelho em painel/api/_nucleo.py

RECORDER_RESTART_BASE_S = 5.0
RECORDER_RESTART_MAX_S = 60.0
LIVE_CAPTURA_MORTA_S = 900.0
ALVO_RECHECK_S = 120.0  # Q4: reconsulta alvo offline / pos-live a cada 2 min
_PLATFORMS_LIVE = ("twitch", "kick", "youtube")


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
    is_recorded_vod: bool = False


@dataclass
class _ManagedAlvo:
    alvo_id: str
    alvo: Alvo
    entrada: str
    plataforma: str
    fase: str = "aguardando live"
    next_check_at: float = float("-inf")
    session_key: str | None = None
    auto_vod: bool = False


def parse_alvo(s: str) -> Alvo:
    """Parse Twitch/YouTube/Kick live target into Alvo; raise ValueError on unsupported format."""
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

        if host in {"kick.com", "www.kick.com", "m.kick.com"}:
            # urlsplit (sem ;params), como o painel; chave kick_<slug> casa com monitor._YT_KEY_RE (live_id usa kick:<slug>:<sid>)
            path = urllib.parse.urlsplit(to_parse).path.strip("/")
            if not path or "/" in path:
                raise ValueError(f"alvo invalido: {s!r}")
            slug = path.lower()
            if slug in _KICK_RESERVED or not _KICK_SLUG_RE.fullmatch(slug):
                raise ValueError(f"alvo invalido: {s!r}")
            return Alvo(
                plataforma="kick",
                chave=f"kick_{slug}",
                url=f"https://kick.com/{slug}",
                canal=slug,
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
    val = config.env_int("CLIPES_MAX_LIVES", 0)
    if val >= 1:
        return val
    ml_fn = max_lives_fn or perfil_max_lives
    return int(ml_fn(out_root, cpus))


def _is_window_job(job: dict[str, Any]) -> bool:
    """Identifica jobs de janela criados por LiveSession (diferente de picos do monitor)."""
    return all(k in job for k in ("window_start", "window_end", "video_start", "last"))


def _is_busca_job(job: dict[str, Any]) -> bool:
    """Identifica jobs de pico criados pela busca geral integrada."""
    return job.get("origem") == "busca"


class _SessionQueueView(monitor.DiskQueue):
    """Visao da DiskQueue que reivindica janelas proprias e jobs com origem='busca'."""

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
                if not (_is_busca_job(job) or (skey in own and _is_window_job(job))):
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
        max_dur: float | None = None,
        min_dur: float | None = None,
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
        # sem argumento explicito: le CLIPES_DUR_MAX/MIN no momento de abrir a sessao (config recarregada no heartbeat)
        self.max_dur = config.env_float("CLIPES_DUR_MAX", 45) if max_dur is None else float(max_dur)
        self.min_dur = config.env_float("CLIPES_DUR_MIN", 15) if min_dur is None else float(min_dur)
        self.pad = self.max_dur / 2
        self.min_free = config.env_float("CLIPES_MIN_FREE_GB", 5.0, min_value=0.0) if min_free_gb is None else float(min_free_gb)
        self.max_backlog = config.env_int("CLIPES_MAX_BACKLOG", 6, min_value=1)
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
        filtered = [c for c, ok in zip(mine, keep) if ok]
        # A-04: reescreve clips.json com a lista filtrada (atomico) para Discord, registro e bot verem so os donos
        out_dir_raw = result.get("output_dir")
        if out_dir_raw:
            clips_json_path = Path(out_dir_raw) / "clips.json"
            if clips_json_path.is_file():
                try:
                    manifest = json.loads(clips_json_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    manifest = None
                if isinstance(manifest, dict):
                    manifest["clips"] = filtered
                    _write_json_atomic(clips_json_path, manifest)
        return {**result, "clips": filtered}

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
                "max_dur": self.max_dur,
            }
            if self.min_dur is not None:
                job["min_dur"] = self.min_dur
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
        disco_baixo = self.disk_usage(self.out_root).free < self.min_free * 1e9
        if len(backlog) > self.max_backlog or (disco_baixo and len(backlog) > 1):
            print(
                f"[live] backlog {len(backlog)} (limite {self.max_backlog}, disco_baixo={disco_baixo}): "
                f"{backlog[0].name} lost",
                flush=True,
            )
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


class _SupervisorCoordSession:
    """Adapter da sessao Mongo do Supervisor compartilhada com a thread da busca geral."""

    def __init__(self, sup: Supervisor) -> None:
        self._sup = sup

    @property
    def configured(self) -> bool:
        return self._sup.coord is not None

    @property
    def online(self) -> bool:
        return self._sup._coord_active() is not None

    @property
    def active(self) -> Any | None:
        return self._sup._coord_active()

    @property
    def next_retry(self) -> float:
        return self._sup._mongo_offline_until

    def mark_offline(self, exc: BaseException) -> None:
        self._sup._on_mongo_err(exc)

    def maybe_reconnect(self) -> bool:
        return self._sup._coord_active() is not None


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
        max_dur: float | None = None,
        min_dur: float | None = None,
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
        run_pending_fn: Callable[..., Any] | None = None,
        discord_client: Any | None = None,
        stop_event: threading.Event | None = None,
        channels_file: Path | None = None,
        busca_channels: list[str] | None = None,
        busca_irc: Any | None = None,
        busca_popen: Any | None = None,
        busca_sleep: Any | None = None,
        busca_stream_ids: dict[str, str] | None = None,
        vod_pipeline: Callable[[str, Path], int] | None = None,
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
        self.min_dur = min_dur
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
        self.run_pending_fn = run_pending_fn
        self.discord_client = discord_client
        self.stop_event = stop_event
        self.channels_file = Path(channels_file) if channels_file is not None else None
        self.busca_channels = list(busca_channels) if busca_channels is not None else None
        self.busca_irc = busca_irc
        self.busca_popen = busca_popen
        self.busca_sleep = busca_sleep
        self.busca_stream_ids = busca_stream_ids
        self.vod_pipeline = vod_pipeline
        self.slots = config.slots()

        self._lock = threading.Lock()
        self._active: dict[str, LiveSession] = {}
        self._all_sessions: dict[str, LiveSession] = {}
        self._managed_alvos: dict[str, _ManagedAlvo] = {}
        self._busca_owned_lives: dict[str, str] = {}
        self._active_vod_id: list[str | None] = [None]
        self._invalid_alvo_ids: set[str] = set()
        self._aguardando: list[tuple[Alvo, str]] = []
        self._avisados: set[tuple[str, str]] = set()
        self._lost_lives: set[str] = set()
        self._pending_lease_lost: set[str] = set()
        self._cancelled_vod_ids: set[str] = set()
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

    def _limite(self, plataforma: str = "twitch") -> int:
        if self.limite_fn is not None:
            try:
                if len(inspect.signature(self.limite_fn).parameters) >= 1:
                    return int(self.limite_fn(plataforma))
            except (TypeError, ValueError):
                pass
            return int(self.limite_fn())
        if self.ml_fn is not None:
            cap = self._cap()
            return int(self.ml_fn(self.out_root, cap.get("cpus")))
        return int(self.slots.get(plataforma, 0))

    def _ativos_na_plataforma(self, plataforma: str, *, exclude_alvo_id: str | None = None) -> int:
        with self._lock:
            managed_keys = {m.session_key for m in self._managed_alvos.values() if m.session_key is not None}
            managed_count = sum(
                1
                for aid, m in self._managed_alvos.items()
                if m.plataforma == plataforma and aid != exclude_alvo_id
            )
            extra_active = sum(
                1
                for k, s in self._active.items()
                if s.alvo.plataforma == plataforma and k not in managed_keys
            )
        return managed_count + extra_active

    def _ocupados(self) -> dict[str, int]:
        counts = {"twitch": 0, "kick": 0, "youtube": 0, "busca": len(self._busca_owned_lives)}
        for plat in _PLATFORMS_LIVE:
            counts[plat] = self._ativos_na_plataforma(plat)
        return counts

    def _excluir_para_busca(self) -> set[str]:
        """Canais acompanhados localmente neste PC (exclusao em memoria para a busca geral)."""
        with self._lock:
            out: set[str] = set()
            for sess in self._active.values():
                if sess.alvo.canal:
                    out.add(sess.alvo.canal.lower())
                if sess.alvo.chave:
                    out.add(sess.alvo.chave.lower())
            for m in self._managed_alvos.values():
                if m.alvo.canal:
                    out.add(m.alvo.canal.lower())
                if m.alvo.chave:
                    out.add(m.alvo.chave.lower())
                if m.alvo_id:
                    out.add(m.alvo_id.lower())
            return out

    def _resolve_busca_channels(self) -> list[str]:
        if int(self.slots.get("busca", 0)) <= 0:
            return []
        if self.busca_channels is not None:
            raw = self.busca_channels
        elif self.channels_file is not None and self.channels_file.is_file():
            return monitor.discover_channels(self.channels_file, self.policy)
        else:
            return []
        return [c for c in raw if self.policy.permits(c)]

    def _fila(self, queue: monitor.DiskQueue | None, c: Any | None = None) -> dict[str, int]:
        janelas = 0
        busca = 0
        if queue is not None:
            pending_dir = queue.root / "pending"
            if pending_dir.is_dir():
                for d in pending_dir.iterdir():
                    try:
                        job = monitor._read_job(d)
                    except (OSError, ValueError, TypeError):
                        continue
                    if job.get("origem") == "busca":
                        busca += 1
                    elif _is_window_job(job):
                        janelas += 1
        vods = 0
        if c is not None and hasattr(c, "db"):
            try:
                vods = int(c.db.alvos.count_documents({"tipo": "vod", "estado": "ativo"}))
            except Exception as exc:
                if monitor._is_mongo_error(exc):
                    raise
        return {"janelas": janelas, "busca": busca, "vods": vods}

    def _total_slots_live(self) -> int:
        return sum(int(v) for k, v in self.slots.items() if k != "busca")

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

    def _run_probe(self, alvo: Alvo, probe_fn: Callable[[str], dict[str, Any]]) -> dict[str, Any] | None:
        try:
            meta = probe_fn(alvo.url)
        except Exception:
            print(f"[acompanhar] {self._label(alvo)} offline; ignorado", flush=True)
            return None
        if not isinstance(meta, dict):
            print(f"[acompanhar] {self._label(alvo)} offline; ignorado", flush=True)
            return None
        return meta

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
        meta = self._run_probe(alvo, probe_fn)
        if meta is None:
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
        # Detecta se e um video gravado/live encerrada (nao live ativa nem proxima)
        is_live = meta.get("is_live")
        live_status = str(meta.get("live_status") or "").strip().lower()
        if is_live is False and live_status != "is_upcoming":
            alvo.is_recorded_vod = True
            print(f"[acompanhar] {self._label(alvo)} video gravado/live encerrada detectado", flush=True)
            return False
        if is_live is False:
            print(f"[acompanhar] {self._label(alvo)} offline; ignorado", flush=True)
            return False
        return True

    def _probe_kick(self, alvo: Alvo, probe_fn: Callable[[str], dict[str, Any]]) -> bool:
        """Populate Kick video_id/channel_id and return True only when is_live is True."""
        meta = self._run_probe(alvo, probe_fn)
        if meta is None:
            return False
        if meta.get("is_live") is not True:
            print(f"[acompanhar] {self._label(alvo)} offline; ignorado", flush=True)
            return False
        if meta.get("id") not in (None, ""):
            alvo.video_id = str(meta["id"])
        if meta.get("channel_id") not in (None, ""):
            alvo.channel_id = str(meta["channel_id"])
        return True

    def _resolve_live_id(
        self,
        alvo: Alvo,
        probe_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[str | None, str | None]:
        """Resolve (live_id, stream_id) por plataforma; devolve (None, None) se indisponivel."""
        if alvo.plataforma == "twitch":
            sid = self.stream_id
            if not sid and probe_fn is not None:
                sid = monitor._resolve_stream_id(alvo.canal, probe=probe_fn)
            if not sid:
                return None, None
            return f"twitch:{alvo.canal}:{sid}", sid
        if alvo.plataforma == "kick":
            sid = self.stream_id or alvo.video_id
            if not sid:
                return None, None
            return f"kick:{alvo.canal}:{sid}", sid
        sid = alvo.video_id or alvo.chave
        return f"youtube:{alvo.channel_id or alvo.canal}:{sid}", sid

    def _should_stop(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _safe_release_alvo(self, c: Any | None, alvo_id: str) -> None:
        if c is None:
            return
        rel_fn = getattr(c, "release_alvo", None)
        if not callable(rel_fn):
            return
        try:
            rel_fn(alvo_id)
        except Exception as exc:
            if monitor._is_mongo_error(exc):
                self._on_mongo_err(exc)

    def _parse_alvo_doc(self, doc: Any) -> tuple[str, Alvo, str] | None:
        if not isinstance(doc, dict):
            print(f"[acompanhar] alvo invalido no Mongo: {doc!r}", flush=True)
            return None
        alvo_id = doc.get("_id")
        if not isinstance(alvo_id, str) or not alvo_id:
            print(f"[acompanhar] alvo invalido no Mongo ({alvo_id!r}): _id invalido", flush=True)
            return None
        if "entrada" in doc:
            raw_entrada = doc.get("entrada")
        else:
            raw_entrada = alvo_id.split(":", 1)[1] if ":" in alvo_id else alvo_id
        if not isinstance(raw_entrada, str) or not raw_entrada.strip():
            print(f"[acompanhar] alvo invalido no Mongo ({alvo_id!r}): entrada ausente", flush=True)
            return None
        entrada = raw_entrada.strip()
        try:
            alvo = parse_alvo(entrada)
        except ValueError as exc:
            print(f"[acompanhar] alvo invalido no Mongo ({alvo_id!r}): {exc}", flush=True)
            return None
        plat_doc = doc.get("plataforma")
        if plat_doc and plat_doc != alvo.plataforma:
            print(
                f"[acompanhar] alvo invalido no Mongo ({alvo_id!r}): plataforma {plat_doc!r} != {alvo.plataforma!r}",
                flush=True,
            )
            return None
        return alvo_id, alvo, entrada

    def _resume_own_alvos(self, c: Any) -> None:
        own_fn = getattr(c, "own_alvos", None)
        if not callable(own_fn):
            return
        try:
            docs = own_fn()
        except Exception as exc:
            if not monitor._is_mongo_error(exc):
                raise
            self._on_mongo_err(exc)
            return
        for doc in docs:
            parsed = self._parse_alvo_doc(doc)
            if parsed is None:
                if isinstance(doc, dict) and isinstance(doc.get("_id"), str):
                    self._invalid_alvo_ids.add(doc["_id"])
                    self._safe_release_alvo(c, doc["_id"])
                continue
            alvo_id, alvo, entrada = parsed
            limite = self._limite(alvo.plataforma)
            if limite < 1 or self._ativos_na_plataforma(alvo.plataforma) >= limite:
                self._safe_release_alvo(c, alvo_id)
                continue
            auto_vod = bool(doc.get("auto_vod"))
            with self._lock:
                self._managed_alvos[alvo_id] = _ManagedAlvo(
                    alvo_id=alvo_id,
                    alvo=alvo,
                    entrada=entrada,
                    plataforma=alvo.plataforma,
                    fase="aguardando live",
                    next_check_at=float("-inf"),
                    auto_vod=auto_vod,
                )

    def _stop_removed_alvo(self, m: _ManagedAlvo, c_active: Any) -> None:
        key = m.session_key
        sess: LiveSession | None = None
        if key is not None:
            with self._lock:
                sess = self._active.get(key)
        if sess is not None and key is not None:
            try:
                sess.encerrar()
            except Exception as exc:
                print(f"[acompanhar] erro ao encerrar alvo removido {m.alvo_id}: {exc}", flush=True)
            with self._lock:
                self._active.pop(key, None)
                self._pending_lease_lost.discard(key)
            sess.live_ended = False
            self._release_session(key, sess, release_pool=False, reraise_non_mongo=False)
        self._safe_release_alvo(c_active, m.alvo_id)

    def _release_all_managed_alvos(self, c: Any | None) -> None:
        with self._lock:
            ids = list(self._managed_alvos.keys())
            self._managed_alvos.clear()
        if c is None:
            return
        for aid in ids:
            self._safe_release_alvo(c, aid)

    def _sync_mongo_alvos(self, c_active: Any, worker_alias: str, pool: Any) -> None:
        renew_alvos_fn = getattr(c_active, "renew_alvos", None)
        if callable(renew_alvos_fn):
            with self._lock:
                fases = {aid: m.fase for aid, m in self._managed_alvos.items()}
            if fases:
                renewed = renew_alvos_fn(fases)
                for aid in list(fases):
                    if aid in renewed:
                        continue
                    with self._lock:
                        m = self._managed_alvos.pop(aid, None)
                    if m is None:
                        continue
                    doc = c_active.db.alvos.find_one({"_id": aid}) if hasattr(c_active, "db") else None
                    if doc is None or doc.get("estado") != "ativo":
                        self._stop_removed_alvo(m, c_active)
                    elif m.session_key is not None:
                        with self._lock:
                            sess = self._active.get(m.session_key)
                        if sess is not None:
                            self._handle_lease_lost(m.session_key, sess, pool)

        claim_alvo_fn = getattr(c_active, "claim_alvo", None)
        if not callable(claim_alvo_fn):
            return
        for plat in _PLATFORMS_LIVE:
            limite = self._limite(plat)
            while limite >= 1 and self._ativos_na_plataforma(plat) < limite:
                with self._lock:
                    exclude = set(self._managed_alvos) | self._invalid_alvo_ids
                doc = claim_alvo_fn(plat, worker_alias, exclude_ids=exclude)
                if doc is None:
                    break
                parsed = self._parse_alvo_doc(doc)
                if parsed is None:
                    aid_bad = doc.get("_id") if isinstance(doc, dict) else None
                    if isinstance(aid_bad, str) and aid_bad:
                        self._invalid_alvo_ids.add(aid_bad)
                        self._safe_release_alvo(c_active, aid_bad)
                    continue
                alvo_id, alvo, entrada = parsed
                prev_owner = doc.get("previous_owner")
                if prev_owner:
                    msg = f"[acompanhar] failover {alvo_id}: assumido de {prev_owner} por {worker_alias}"
                    print(msg, flush=True)
                    self.aviso_fn(msg)
                with self._lock:
                    self._managed_alvos[alvo_id] = _ManagedAlvo(
                        alvo_id=alvo_id,
                        alvo=alvo,
                        entrada=entrada,
                        plataforma=alvo.plataforma,
                        fase="aguardando live",
                        next_check_at=float("-inf"),
                        auto_vod=bool(doc.get("auto_vod")),
                    )

    def _tick_managed_alvos(
        self,
        t: float,
        beat_interval: float,
        queue: monitor.DiskQueue,
        pool: Any,
        pipeline_fn: Callable[[Path, Path], dict[str, Any]],
        is_live_fn: Callable[[str], bool],
        probe_fn: Callable[[str], dict[str, Any]],
    ) -> None:
        with self._lock:
            managed = list(self._managed_alvos.values())
        for m in managed:
            with self._lock:
                is_active = m.session_key is not None and m.session_key in self._active
            if is_active:
                m.fase = "gravando"
                continue
            if t < m.next_check_at:
                continue
            if self._coord_active() is None:
                continue  # queda do Mongo: mantem sessoes ativas sem novos claims/inicios
            try:
                fresh_alvo = parse_alvo(m.entrada)
            except ValueError:
                m.next_check_at = t + ALVO_RECHECK_S
                continue

            limite = self._limite(m.plataforma)
            status = self._try_start_alvo(
                fresh_alvo,
                limite,
                queue,
                pool,
                pipeline_fn,
                is_live_fn,
                probe_fn,
                exclude_alvo_id=m.alvo_id,
                managed=True,
            )
            # Promocao automatica YouTube live -> VOD (reusa a sonda do _try_start_alvo)
            if (
                status == "skip"
                and fresh_alvo.is_recorded_vod
                and m.auto_vod
                and m.plataforma == "youtube"
                and m.alvo.video_id is not None
            ):
                c_now = self._coord_active()
                if c_now is not None:
                    promovido = c_now.promover_para_vod(m.alvo_id, fresh_alvo.url)
                    if promovido == "promovido":
                        print(f"[acompanhar] alvo {m.alvo_id} promovido para VOD ({fresh_alvo.url})", flush=True)
                        with self._lock:
                            self._managed_alvos.pop(m.alvo_id, None)
                        continue
                    elif promovido == "duplicado":
                        print(f"[acompanhar] alvo {m.alvo_id} duplicado (VOD ativo existe), marcado removido", flush=True)
                        with self._lock:
                            self._managed_alvos.pop(m.alvo_id, None)
                        continue
            if status == "started":
                m.alvo = fresh_alvo
                m.session_key = fresh_alvo.chave
                m.fase = "gravando"
            elif status == "busy":
                m.session_key = None
                m.fase = "aguardando live"
                m.next_check_at = t + min(beat_interval, 5.0)
            else:
                m.session_key = None
                m.fase = "aguardando live"
                m.next_check_at = t + ALVO_RECHECK_S
            c_now = self._coord_active()
            if c_now is not None and callable(getattr(c_now, "renew_alvos", None)):
                try:
                    c_now.renew_alvos({m.alvo_id: m.fase})
                except Exception as exc:
                    if monitor._is_mongo_error(exc):
                        self._on_mongo_err(exc)

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
        exclude_alvo_id: str | None = None,
        managed: bool = False,
    ) -> str:
        if alvo.plataforma in ("youtube", "kick") and not already_probed:
            probed = (
                self._probe_youtube(alvo, probe_fn)
                if alvo.plataforma == "youtube"
                else self._probe_kick(alvo, probe_fn)
            )
            if not probed:
                return "skip"
            dup_pool = self._active if managed else self._all_sessions
            if alvo.chave in dup_pool or any(a.chave == alvo.chave for a, _ in self._aguardando):
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

        if limite < 1 or self._ativos_na_plataforma(alvo.plataforma, exclude_alvo_id=exclude_alvo_id) >= limite:
            self._saw_not_claimed = True
            self._recusar(alvo, f"limite slots {alvo.plataforma}={limite} (defina CLIPES_SLOTS)")
            return "aguardando"

        if alvo.plataforma == "twitch" and alvo.canal.lower() in self._busca_owned_lives:
            self._saw_not_claimed = True
            if not managed:
                self._recusar(alvo, "live em captura pela busca local")
            return "busy" if managed else "aguardando"

        live_id: str | None = None
        pending_live_id: str | None = None
        pending_stream_id: str | None = None
        c_active = self._coord_active()
        if c_active is not None:
            cap = self._cap()
            live_id, sid = self._resolve_live_id(alvo, probe_fn=probe_fn)
            if not live_id:
                self._saw_not_claimed = True
                if not managed:
                    self._recusar(alvo, "stream_id indisponivel")
                return "busy" if managed else "aguardando"

            try:
                c_active.ensure_live(live_id, channel=alvo.canal, platform=alvo.plataforma, stream_id=sid)
                monitor._wait_claim_sync(30.0 / max(1.0, float(cap.get("score") or 1)), now=self.now, sleep=self.sleep)
                claimed = c_active.claim_live(live_id)
            except Exception as exc:
                if not monitor._is_mongo_error(exc):
                    raise
                self._on_mongo_err(exc)
                if managed:
                    return "busy"
                pending_live_id = live_id
                pending_stream_id = sid
                live_id = None
            else:
                if not claimed:
                    self._saw_not_claimed = True
                    if not managed:
                        self._recusar(alvo, "lease com outro PC")
                    return "busy" if managed else "aguardando"
        elif self.coord is not None:
            if managed:
                return "busy"
            # Mongo em backoff na abertura: guarda live_id pretendido para tentar claim ao reconectar (M-01)
            pending_live_id, pending_stream_id = self._resolve_live_id(alvo)

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
            min_dur=self.min_dur,
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
        prev_sess = self._all_sessions.get(sess.key)
        if prev_sess is not None and prev_sess is not sess:
            self._jobs.extend(prev_sess.jobs)
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
        *,
        queue: monitor.DiskQueue | None = None,
    ) -> None:
        beat_fn = getattr(c, "beat", None)
        if not callable(beat_fn):
            return
        try:
            params = inspect.signature(beat_fn).parameters
            has_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        except (TypeError, ValueError):
            params = {}
            has_kw = True
        kwargs: dict[str, Any] = {}
        if has_kw or "beat_s" in params:
            kwargs["beat_s"] = beat_s
        if has_kw or "slots" in params:
            kwargs["slots"] = dict(self.slots)
        if has_kw or "ocupados" in params:
            kwargs["ocupados"] = self._ocupados()
        if has_kw or "fila" in params:
            kwargs["fila"] = self._fila(queue, c)
        res = beat_fn(alias, cap, job_atual, ml, razao, **kwargs)
        if isinstance(res, dict):
            self._apply_runtime_config(res, alias=alias, c=c)
        return res

    def _apply_runtime_config(
        self,
        remote_cfg: dict[str, Any] | None,
        *,
        alias: str | None = None,
        c: Any = None,
    ) -> dict[str, str]:
        """Aplica overrides do MongoDB em runtime e atualiza slots, lease e sessoes ativas."""
        applied = config.apply_remote_config(remote_cfg, out_root=self.out_root, alias=alias)
        new_slots = config.slots(strict=False)
        with self._lock:
            self.slots = new_slots
            new_min_free = (
                config.env_float("CLIPES_MIN_FREE_GB", 5.0, min_value=0.0)
                if self.min_free_gb is None
                else float(self.min_free_gb)
            )
            new_captura_morta = _captura_morta_s()
            for sess in self._active.values():
                sess.min_free = new_min_free
                sess.captura_morta_s = new_captura_morta
        if c is not None and hasattr(c, "lease_s") and "CLIPES_LEASE_S" in applied:
            c.lease_s = config.env_float("CLIPES_LEASE_S", float(c.lease_s), min_value=30.0)
        return applied

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
            for m in self._managed_alvos.values():
                if m.session_key == key:
                    m.session_key = None
                    m.fase = "aguardando live"
                    m.next_check_at = self.now() + 5.0

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
            lid, sid = self._resolve_live_id(sess.alvo, probe_fn=probe_fn)
            if not lid:
                return None
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
        still_waiting: list[tuple[Alvo, str]] = []
        for alvo, motivo in self._aguardando:
            limite = self._limite(alvo.plataforma)
            if limite < 1 or self._ativos_na_plataforma(alvo.plataforma) >= limite:
                still_waiting.append((alvo, motivo))
                continue
            status = self._try_start_alvo(
                alvo, limite, queue, pool, pipeline_fn, is_live_fn, probe_fn,
                already_probed=(alvo.plataforma in ("youtube", "kick")),
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
                if _is_busca_job(job):
                    return True
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
            if _is_busca_job(job):
                return pipeline_fn(job_dir, root)
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

    def _run_vod_cli(self, url: str, out_root: Path) -> int:
        from . import cli

        # VOD continua com 60s quando nao ha --max-dur explicito (CLIPES_DUR_MAX so vale para live)
        max_dur = 60.0 if self.max_dur is None else self.max_dur
        argv = [url, "--out-dir", str(out_root), "--max-dur", str(max_dur)]
        if self.min_dur is not None:
            argv.extend(["--min-dur", str(self.min_dur)])
        try:
            return int(cli.main(argv))
        except SystemExit as exc:  # erro de argumento do cli nao pode matar o servico
            raise RuntimeError(f"argumentos invalidos do cli (codigo {exc.code})") from None

    def _check_vod_policy(self, vod: dict[str, Any], url: str, probe_fn: Callable[[str], dict[str, Any]]) -> str | None:
        host = urllib.parse.urlparse(url).hostname or ""
        try:
            ips = {ipaddress.ip_address(i[4][0].split("%")[0]) for i in socket.getaddrinfo(host, None)}
        except (OSError, ValueError):
            return "host nao permitido"
        if not ips or any(
            ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified
            for ip in ips
        ):
            return "host nao permitido"
        if not self.policy.blocked and self.policy.allowed is None:
            return None
        candidatos: list[str] = []
        canal_doc = vod.get("canal")
        if isinstance(canal_doc, str) and canal_doc.strip():
            candidatos.append(canal_doc.strip().lstrip("@").lower())
        if not candidatos:
            try:
                meta = probe_fn(url)
            except Exception:
                meta = {}
            if isinstance(meta, dict):
                for campo in ("uploader_id", "channel", "uploader", "channel_id"):
                    val = meta.get(campo)
                    if isinstance(val, str) and val.strip():
                        candidatos.append(val.strip().lstrip("@").lower())
        for ch in candidatos:
            if not self.policy.permits(ch):
                return f"bloqueado pela policy ({ch})"
        return None

    def _process_one_vod(self, worker_alias: str, probe_fn: Callable[[str], dict[str, Any]]) -> bool:
        c = self._coord_active()
        if c is None:
            return False
        pegar_fn = getattr(c, "pegar_vod", None)
        fim_fn = getattr(c, "fim_vod", None)
        if not (callable(pegar_fn) and callable(fim_fn)):
            return False
        try:
            vod = pegar_fn(worker_alias)
        except Exception as exc:
            if not monitor._is_mongo_error(exc):
                raise
            self._on_mongo_err(exc)
            return False
        if not isinstance(vod, dict) or not vod.get("_id"):
            return False

        vod_id = str(vod["_id"])
        url = str(vod.get("url") or "").strip()
        self._current_job[0] = vod_id
        self._active_vod_id[0] = vod_id
        try:
            if not url:
                raise ValueError("url de VOD ausente")
            bloqueio = self._check_vod_policy(vod, url, probe_fn)
            if bloqueio is not None:
                raise PermissionError(bloqueio)

            # Revalidar se o VOD ainda esta ativo antes de rodar o pipeline
            renew_alvos_fn = getattr(c, "renew_alvos", None)
            if callable(renew_alvos_fn):
                renewed = renew_alvos_fn({vod_id: "processando"})
                if vod_id not in renewed:
                    # VOD foi removido durante a validacao - aborta silenciosamente
                    print(f"[vod] alvo removido antes do pipeline {vod_id}", flush=True)
                    return True

            runner = self.vod_pipeline or self._run_vod_cli
            rc = runner(url, self.out_root)
            if isinstance(rc, int) and rc != 0:
                raise RuntimeError(f"pipeline VOD retornou codigo {rc}")
        except Exception as exc:
            uri = os.environ.get("CLIPES_MONGO_URI", "").strip() or None
            erro = monitor._sanitize_error(exc, uri)
            from .coord import _sanitize_erro

            erro_limpo = _sanitize_erro(erro) or erro
            c_fin = self._coord_active() or self.coord
            if c_fin is not None and callable(getattr(c_fin, "fim_vod", None)):
                try:
                    c_fin.fim_vod(vod_id, "falhou", erro_limpo)
                except Exception as mexc:
                    if monitor._is_mongo_error(mexc):
                        self._on_mongo_err(mexc)
            # Se o VOD foi cancelado intencionalmente (removido no painel), evita notificacao ruidosa de falha
            if vod_id in self._cancelled_vod_ids:
                self._cancelled_vod_ids.discard(vod_id)
            else:
                msg = f"[vod] falha {vod_id}: {erro_limpo}"
                print(msg, flush=True)
                self.aviso_fn(msg)
            return True
        else:
            c_fin = self._coord_active() or self.coord
            if c_fin is not None and callable(getattr(c_fin, "fim_vod", None)):
                try:
                    c_fin.fim_vod(vod_id, "feito")
                except Exception as mexc:
                    if monitor._is_mongo_error(mexc):
                        self._on_mongo_err(mexc)
            return True
        finally:
            self._cancelled_vod_ids.discard(vod_id)
            self._active_vod_id[0] = None
            self._current_job[0] = None

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
        publish_loop: PublishLoop | None = None
        busca_thread: threading.Thread | None = None
        busca_loop_ref: list[asyncio.AbstractEventLoop | None] = [None]
        busca_stop_ref: list[asyncio.Event | None] = [None]
        busca_stop_early = threading.Event()

        def _stop_busca() -> None:
            busca_stop_early.set()
            loop = busca_loop_ref[0]
            ev = busca_stop_ref[0]
            if loop is not None and ev is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(ev.set)
                except RuntimeError:
                    pass
            if busca_thread is not None and busca_thread.is_alive():
                busca_thread.join(timeout=10.0)

        def _stop_keepalive() -> None:
            _stop_busca()
            if stop_hb is not None:
                stop_hb.set()
            if hb_thread is not None and hb_thread.is_alive():
                hb_thread.join(timeout=5.0)
            if publish_loop is not None:
                publish_loop.stop(timeout=10.0)

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
                        if alvo is not None and alvo.plataforma in ("youtube", "kick"):
                            return monitor.ytdlp_live_cmd(alvo.url)
                        return monitor.streamlink_cmd(key)

                    pool = monitor.RollingRecorderPool(
                        queue.root / ".buffer",
                        max_hot=0,
                        max_on_demand=max(1, len(self.alvos), self._total_slots_live()),
                        buffer_seconds=monitor.LIVE_WINDOW_S + 2 * self.step,
                        source_cmd=_source_cmd,
                    )

                worker_alias = config.pc_alias(self.out_root)
                c_init = self._coord_active()
                if self.coord is not None:
                    boot_cfg: dict[str, Any] | None = None
                    if c_init is not None:
                        reconcile_fn = getattr(c_init, "reconcile_alias", None)
                        if callable(reconcile_fn):
                            try:
                                boot_cfg = reconcile_fn(worker_alias)
                            except Exception as exc:
                                if not monitor._is_mongo_error(exc):
                                    raise
                                self._on_mongo_err(exc)
                    if boot_cfg is None:
                        boot_cfg = config.load_cached_remote_config(out_root=self.out_root, alias=worker_alias)
                    if boot_cfg:
                        self._apply_runtime_config(boot_cfg, alias=worker_alias, c=c_init)
                    else:
                        self.slots = config.slots(strict=False)

                if self.beat_s is not None:
                    beat_interval = max(0.01, float(self.beat_s))
                else:
                    beat_interval = config.env_float("CLIPES_BEAT_S", 20.0, min_value=1.0)
                last_beat = float("-inf")

                mongo_mode = self.coord is not None and not self.alvos
                busca_chans = self._resolve_busca_channels()
                service_mode = mongo_mode or (not self.alvos and bool(busca_chans))

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
                    # Retomada pos-reinicio: assume alvos proprios com lease valida antes de tomar novos
                    c_init = self._coord_active()
                    if mongo_mode and c_init is not None:
                        self._resume_own_alvos(c_init)

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
                                ml_k = max(1, self._total_slots_live())
                                razao_k = monitor._read_perfil_razao(self.out_root, cap_k.get("cpus"))
                                self._call_beat(
                                    c, worker_alias, cap_k, self._current_job[0], ml_k, razao_k, beat_interval,
                                    queue=queue,
                                )
                                renew_alvos_fn = getattr(c, "renew_alvos", None)
                                if callable(renew_alvos_fn):
                                    with self._lock:
                                        fases_k = {aid: m.fase for aid, m in self._managed_alvos.items()}
                                    if fases_k:
                                        renew_alvos_fn(fases_k)
                                    vod_k = self._active_vod_id[0]
                                    if vod_k:
                                        # Renova VOD com fase="processando" para detectar cancelamento
                                        renewed_vod = renew_alvos_fn({vod_k: "processando"})
                                        if vod_k not in renewed_vod:
                                            self._cancelled_vod_ids.add(vod_k)
                                            print(f"[vod] alvo removido durante execucao {vod_k}", flush=True)
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

                    # A-05: executor periodico de publicacao (thread propria, fora do heartbeat)
                    def _publish_once() -> None:
                        c = self._coord_active()
                        if c is None:
                            return
                        runner = self.run_pending_fn
                        if runner is None:
                            from . import publicacao

                            runner = publicacao.run_pending
                        dc = self.discord_client
                        discord_post = (
                            (lambda tid, msg, _dc=dc: _dc.post(tid, msg))
                            if dc is not None
                            else None
                        )
                        try:
                            runner(
                                c,
                                getattr(c, "worker_id", None),
                                out_root=self.out_root,
                                now=self.now,
                                policy=self.policy,
                                discord_post=discord_post,
                            )
                        except Exception as exc:
                            if monitor._is_mongo_error(exc):
                                self._on_mongo_err(exc)
                            else:
                                uri = os.environ.get("CLIPES_MONGO_URI", "").strip() or None
                                print(
                                    f"[publicar] erro na publicacao ({monitor._sanitize_error(exc, uri)})",
                                    flush=True,
                                )

                    publish_loop = PublishLoop(_publish_once, beat_interval, name="publicacao")
                    publish_loop.start()

                def _start_busca() -> None:
                    nonlocal busca_thread
                    chans_now = self._resolve_busca_channels()
                    if not chans_now or (busca_thread is not None and busca_thread.is_alive()):
                        return
                    busca_stop_early.clear()
                    busca_loop_ref[0] = None
                    busca_stop_ref[0] = None
                    shared_cs = _SupervisorCoordSession(self)

                    def _busca_worker() -> None:
                        async def _run_busca() -> None:
                            loop = asyncio.get_running_loop()
                            ev = asyncio.Event()
                            busca_loop_ref[0] = loop
                            busca_stop_ref[0] = ev
                            if busca_stop_early.is_set():
                                ev.set()
                                return
                            await monitor.run_monitor(
                                chans_now,
                                self.out_root,
                                self.policy,
                                queue_root=queue.root,
                                irc=self.busca_irc or monitor.irc_chat_loop,
                                popen=self.busca_popen or subprocess.Popen,
                                now=self.now,
                                sleep=self.busca_sleep or asyncio.sleep,
                                stop=ev,
                                coord_session=shared_cs,
                                capacity_fn=self.cap_fn,
                                stream_ids=self.busca_stream_ids,
                                probe=probe_fn,
                                beat_s=beat_interval,
                                alias=worker_alias,
                                worker=False,
                                excluir=self._excluir_para_busca,
                                owned_lives_out=self._busca_owned_lives,
                            )

                        try:
                            asyncio.run(_run_busca())
                        except Exception as exc:
                            print(f"[busca] encerrada com erro: {exc}", flush=True)

                    busca_thread = threading.Thread(target=_busca_worker, daemon=True, name="busca-geral")
                    busca_thread.start()

                def _sync_busca_thread() -> None:
                    nonlocal busca_thread
                    want_busca = int(self.slots.get("busca", 0)) > 0
                    alive = busca_thread is not None and busca_thread.is_alive()
                    if not want_busca and alive:
                        _stop_busca()
                        busca_thread = None
                    elif want_busca and not alive:
                        _start_busca()

                if busca_chans:
                    _start_busca()

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
                    self._release_all_managed_alvos(self.coord)

                if not service_mode:
                    for alvo in self.alvos:
                        limite = self._limite(alvo.plataforma)
                        status = self._try_start_alvo(alvo, limite, queue, pool, pipeline_fn, is_live_fn, probe_fn)
                        if status == "aguardando":
                            self._aguardando.append((alvo, self._last_refusal_reason or ""))

                    if not self._active:
                        if self._saw_blocked and not self._saw_not_claimed:
                            return {"status": "blocked", "jobs": [], "reason": None}
                        if self._saw_not_claimed:
                            return {"status": "not_claimed", "jobs": [], "reason": self._last_refusal_reason}
                        return {"status": "ended", "jobs": [], "reason": None}

                while (service_mode and not self._should_stop()) or (not service_mode and self._active):
                    slot_opened = self._drain_lost_leases(pool)
                    if not service_mode and not self._active:
                        if slot_opened and self._aguardando:
                            self._promote_aguardando(queue, pool, pipeline_fn, is_live_fn, probe_fn)
                        if not self._active:
                            break

                    t = self.now()
                    c_active = self._coord_active()
                    if c_active is not None and t - last_beat >= beat_interval:
                        last_beat = t
                        cap_now = self._cap()
                        ml_now = max(1, self._total_slots_live())
                        razao_now = monitor._read_perfil_razao(self.out_root, cap_now.get("cpus"))
                        cmd_action: str | None = None
                        try:
                            self._call_beat(
                                c_active, worker_alias, cap_now, self._current_job[0], ml_now, razao_now, beat_interval,
                                queue=queue,
                            )
                            if self.beat_s is None:
                                beat_interval = config.env_float("CLIPES_BEAT_S", 20.0, min_value=5.0)
                            _sync_busca_thread()
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
                            if mongo_mode:
                                self._sync_mongo_alvos(c_active, worker_alias, pool)
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
                            if publish_loop is not None:
                                publish_loop.run_now()

                    if mongo_mode:
                        self._tick_managed_alvos(
                            t, beat_interval, queue, pool, pipeline_fn, is_live_fn, probe_fn
                        )

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
                                for m in self._managed_alvos.values():
                                    if m.session_key == key:
                                        m.session_key = None
                                        m.fase = "aguardando live"
                                        m.next_check_at = t + ALVO_RECHECK_S
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
                            ended_alvos: dict[str, str] = {}
                            with self._lock:
                                self._active.pop(key, None)
                                for m in self._managed_alvos.values():
                                    if m.session_key == key:
                                        m.session_key = None
                                        m.fase = "aguardando live"
                                        m.next_check_at = t + ALVO_RECHECK_S
                                        ended_alvos[m.alvo_id] = m.fase
                            self._release_session(key, sess, release_pool=False, reraise_non_mongo=True)
                            if ended_alvos:
                                c_upd = self._coord_active()
                                if c_upd is not None and callable(getattr(c_upd, "renew_alvos", None)):
                                    try:
                                        c_upd.renew_alvos(ended_alvos)
                                    except Exception as exc:
                                        if monitor._is_mongo_error(exc):
                                            self._on_mongo_err(exc)
                            slot_opened = True

                    if not service_mode:
                        if slot_opened and self._aguardando:
                            self._promote_aguardando(queue, pool, pipeline_fn, is_live_fn, probe_fn)

                        if not self._active:
                            break

                    worked = False
                    if self._has_own_pending(queue, skip_lost=True):
                        worked = self._process_one(queue, pipeline_fn, skip_lost=True)
                    elif mongo_mode:
                        worked = self._process_one_vod(worker_alias, probe_fn)
                    if not worked:
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

                all_jobs: list[str] = list(self._jobs)
                for sess in self._all_sessions.values():
                    all_jobs.extend(sess.jobs)
                status = "lease_lost" if (self._saw_lease_lost and not any(s.live_ended for s in self._all_sessions.values())) else "ended"
                return {"status": status, "jobs": all_jobs, "reason": None}
        finally:
            try:
                _stop_keepalive()
                for key, sess in list(self._active.items()):
                    self._release_session(key, sess, release_pool=True, reraise_non_mongo=True)
                self._release_all_managed_alvos(self.coord)
            finally:
                if pool is not None and hasattr(pool, "stop_all"):
                    try:
                        pool.stop_all()
                    except Exception:
                        pass
                lock.close()


def _aviso_servico(texto: str) -> None:
    # aviso de ciclo de vida: falha ao postar nunca derruba o servico
    try:
        post_aviso(texto)
    except Exception as exc:
        print(f"[acompanhar] falha ao postar aviso ({type(exc).__name__})", flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="clipes servico",
        description="Servico unico por PC: acompanha lives (Twitch/YouTube/Kick), roda busca geral e processa VODs em uma fila unica.",
    )
    p.add_argument("alvos", nargs="*", help="Canais/URLs Twitch ou URLs de live do YouTube/Kick (opcional com CLIPES_MONGO_URI)")
    p.add_argument("--out-dir", default=None, help="Padrao: OUT_DIR ou ./out")
    p.add_argument("--queue-dir", type=Path, default=None, help="Padrao: <out-dir>/queue")
    p.add_argument("--channels-file", type=Path, default=Path("channels.txt"), help="Canais da busca geral (1 por linha; padrao: channels.txt)")
    p.add_argument("--policy-file", type=Path, default=None, help="Padrao: CLIPES_POLICY_FILE, senao <OUT_DIR>/policy.json (legado ao lado do channels.txt/cwd so se o padrao nao existir)")
    p.add_argument("--max-dur", type=float, default=None, help="Duracao maxima de cada clipe em segundos (padrao: CLIPES_DUR_MAX ou 45)")
    p.add_argument("--min-dur", type=float, default=None, help="Duracao minima de cada clipe em segundos (padrao: CLIPES_DUR_MIN ou 15)")
    args = p.parse_args(argv)

    if args.max_dur is not None and args.max_dur <= 0:
        print("[acompanhar] --max-dur deve ser > 0", file=sys.stderr, flush=True)
        return 2
    if args.min_dur is not None and args.min_dur <= 0:
        print("[acompanhar] --min-dur deve ser > 0", file=sys.stderr, flush=True)
        return 2

    has_mongo = bool(os.environ.get("CLIPES_MONGO_URI", "").strip())
    if not args.alvos and not has_mongo:
        print("[acompanhar] informe ao menos um alvo ou configure CLIPES_MONGO_URI", file=sys.stderr, flush=True)
        return 2

    parsed: list[Alvo] = []
    for raw in args.alvos:
        try:
            parsed.append(parse_alvo(raw))
        except ValueError:
            print(f"[acompanhar] alvo invalido: {raw}", flush=True)
            return 2

    out_root = config.resolve_out_dir(args.out_dir)
    queue_root = args.queue_dir.expanduser().resolve() if args.queue_dir else None
    policy = monitor.load_policy(args.policy_file) if args.policy_file else monitor.load_policy(config.resolve_policy_path(out_root, legacy=Path("policy.json")))
    try:
        with _exit_signals_to_kb():
            coord = monitor._connect_coord_for_follow(out_root) if has_mongo else None
            if not parsed and coord is None:
                print("[acompanhar] CLIPES_MONGO_URI indisponivel e nenhum alvo informado", file=sys.stderr, flush=True)
                return 1

            sup = Supervisor(
                parsed,
                out_root,
                queue_root=queue_root,
                max_dur=args.max_dur,
                min_dur=args.min_dur,
                policy=policy,
                coord=coord,
                channels_file=args.channels_file,
            )
            _aviso_servico("servico iniciado")
            try:
                sup.run()
            finally:
                _aviso_servico("servico parado")
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        print(f"[acompanhar] {exc}", flush=True)
        return 1
    return 0
