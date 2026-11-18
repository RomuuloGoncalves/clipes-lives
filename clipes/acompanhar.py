"""Supervisor multi-live (`acompanhar`): acompanha N lives (Twitch/YouTube) com 1 fila e 1 worker."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
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
        has_coord: bool = False,
        on_backlog_lost: Callable[[Alvo, str], None] | None = None,
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
        self.has_coord = has_coord
        self.on_backlog_lost = on_backlog_lost
        self.emitted_path = out_root / f".emitted_{sanitize_id(self.key)}.json"
        self.jobs: list[str] = []
        self.t0 = 0.0
        self.next_e = 0.0
        self.last_check = 0.0
        self.live_ok = True
        self.since: dict[str, float] = {}
        self.live_ended = False
        self.lease_lost = False

    def start(self) -> None:
        if not self.pool.start_on_demand(self.key):
            raise RuntimeError(f"nao foi possivel gravar {self.key}")
        self.t0 = self.now()
        self.next_e = window_end(self.t0, self.step) + self.step
        self.last_check = self.t0 - monitor.LIVE_IS_LIVE_EVERY_S
        self.live_ok = True
        self.since = {}

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

            c_active = self.coord_getter()
            if c_active is not None and self.live_id is not None and job["status"] != "rejected":
                mongo_job_id = f"{self.live_id}:{int(e)}"
                try:
                    added = c_active.add_job(mongo_job_id, self.live_id, (start_ts, e), 0.0)
                except Exception as exc:
                    if not monitor._is_mongo_error(exc):
                        raise
                    self.on_mongo_error(exc)
                    job["coord_synced"] = False
                else:
                    if not added:
                        shutil.rmtree(job_dir, ignore_errors=True)
                        return
                    job.update(live_id=self.live_id, mongo_job_id=mongo_job_id, coord_synced=True)
            elif self.has_coord:
                job["coord_synced"] = False

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
        if t - self.last_check >= monitor.LIVE_IS_LIVE_EVERY_S:
            self.last_check = t
            try:
                self.live_ok = bool(self.is_live_fn(self.alvo.url))
            except Exception:  # unknown is not an end signal
                self.live_ok = True

        segs = self.pool.segments(self.key)
        newest = segs[-1][0] + monitor.SEGMENT_S if segs else self.t0
        signals = {
            "exited": not self.pool.is_recording(self.key),
            "silent": t - newest >= monitor.LIVE_NO_SEGMENT_S,
            "offline": not self.live_ok,
        }
        for k, v in signals.items():
            if v:
                self.since.setdefault(k, t)
            else:
                self.since.pop(k, None)
        if len(self.since) >= 2 or any(t - v >= monitor.LIVE_END_PERSIST_S for v in self.since.values()):
            self.live_ended = True
            return False

        while t >= self.next_e:
            if self.disk_usage(self.out_root).free < self.min_free * 1e9:
                print("[live] janela pulada: disco", flush=True)
            else:
                self.fechar_janela(float(self.next_e), last=False)
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

        self._active: dict[str, LiveSession] = {}
        self._all_sessions: dict[str, LiveSession] = {}
        self._aguardando: list[tuple[Alvo, str]] = []
        self._avisados: set[tuple[str, str]] = set()
        self._lost_lives: set[str] = set()
        self._alvo_by_key: dict[str, Alvo] = {a.chave: a for a in self.alvos}
        self._mongo_offline_until = float("-inf")
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
        uri = os.environ.get("CLIPES_MONGO_URI", "").strip() or None
        print(f"[fila] Mongo indisponivel ({monitor._sanitize_error(exc, uri)}); seguindo local", flush=True)

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
                if hasattr(c_active, "db") and hasattr(c_active, "worker_id"):
                    active_ids = {s.live_id for s in self._active.values() if s.live_id}
                    other_owned = c_active.db.lives.count_documents({
                        "_id": {"$nin": list(active_ids | {live_id})},
                        "owner": c_active.worker_id,
                        "status": "live",
                    })
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
                live_id = None
            else:
                if not claimed:
                    self._saw_not_claimed = True
                    self._recusar(alvo, "lease com outro PC")
                    return "aguardando"

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

        self._active[sess.key] = sess
        self._all_sessions[sess.key] = sess
        return "started"

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

    def _has_own_pending(self, queue: monitor.DiskQueue, *, skip_lost: bool = False) -> bool:
        for d in (queue.root / "pending").iterdir():
            try:
                job = monitor._read_job(d)
                skey = job.get("session_key") or job.get("channel")
                if skey in self._all_sessions:
                    if skip_lost and self._all_sessions[skey].lease_lost:
                        continue
                    return True
            except (OSError, ValueError):
                continue
        return False

    def _process_one(
        self,
        queue: monitor.DiskQueue,
        pipeline_fn: Callable[[Path, Path], dict[str, Any]],
    ) -> bool:
        def dispatch_pipeline(job_dir: Path, root: Path) -> dict[str, Any]:
            job = monitor._read_job(job_dir)
            skey = job.get("session_key") or job.get("channel")
            sess = self._all_sessions.get(skey)
            if sess is not None:
                return sess.pipeline(job_dir, root)
            return pipeline_fn(job_dir, root)

        return monitor.run_worker_step(
            queue,
            self.out_root,
            self.policy,
            dispatch_pipeline,
            coord=self._coord_active(),
            on_mongo_error=self._on_mongo_err,
            lost_lives=self._lost_lives,
            on_job_active=lambda jid: self._current_job.__setitem__(0, jid),
        )

    def run(self) -> dict[str, Any]:
        self.out_root.mkdir(parents=True, exist_ok=True)
        queue = monitor.DiskQueue(self.queue_root)
        lock = monitor._lock_worker(queue.root)
        try:
            queue.recover_orphans()
            pipeline_fn = self.pipeline or monitor.run_local_pipeline
            probe_fn = self.probe_arg or monitor.probe_metadata
            is_live_fn = self.is_live_arg or (lambda u: bool(probe_fn(u).get("is_live")))

            if self.pool is not None:
                pool = self.pool
            else:
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
            beat_interval = max(1.0, float(os.environ.get("CLIPES_BEAT_S", "20")))
            last_beat = float("-inf")

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
                t = self.now()
                c_active = self._coord_active()
                if c_active is not None and t - last_beat >= beat_interval:
                    last_beat = t
                    cap_now = self._cap()
                    ml_now = max(1, int(resolve_max_lives(self.out_root, cap_now.get("cpus"), max_lives_fn=self.ml_fn)))
                    razao_now = monitor._read_perfil_razao(self.out_root, cap_now.get("cpus"))
                    try:
                        c_active.beat(worker_alias, cap_now, self._current_job[0], ml_now, razao_now)
                        for key, sess in list(self._active.items()):
                            if sess.live_id is None:
                                continue
                            if not c_active.renew(sess.live_id):
                                print(f"[fila] lease perdida {sess.live_id}", flush=True)
                                self._lost_lives.add(sess.live_id)
                                if hasattr(pool, "stop_channel"):
                                    pool.stop_channel(key)
                                else:
                                    pool.release_on_demand(key)
                                sess.lease_lost = True
                                sess.live_ended = False
                                self._saw_lease_lost = True
                                self._active.pop(key, None)
                    except Exception as exc:
                        if not monitor._is_mongo_error(exc):
                            raise
                        self._on_mongo_err(exc)

                slot_opened = False
                for key, sess in list(self._active.items()):
                    if not sess.tick(t):
                        sess.encerrar()
                        if self.coord is not None and sess.live_id is not None:
                            try:
                                self.coord.release(sess.live_id, sess.live_ended)
                            except Exception as exc:
                                if not monitor._is_mongo_error(exc):
                                    raise
                        self._active.pop(key, None)
                        slot_opened = True

                if slot_opened and self._aguardando:
                    self._promote_aguardando(queue, pool, pipeline_fn, is_live_fn, probe_fn)

                if not self._active:
                    break

                if not (self._has_own_pending(queue) and self._process_one(queue, pipeline_fn)):
                    self.sleep(1.0)
                pool.cleanup(t)

            while self._has_own_pending(queue, skip_lost=True):
                if not self._process_one(queue, pipeline_fn):
                    break

            all_jobs: list[str] = []
            for sess in self._all_sessions.values():
                all_jobs.extend(sess.jobs)
            status = "lease_lost" if (self._saw_lease_lost and not any(s.live_ended for s in self._all_sessions.values())) else "ended"
            return {"status": status, "jobs": all_jobs, "reason": None}
        finally:
            for key, sess in list(self._active.items()):
                try:
                    pool_ref = sess.pool
                    pool_ref.release_hold(key)
                    pool_ref.release_on_demand(key)
                except Exception:
                    pass
                if self.coord is not None and sess.live_id is not None:
                    try:
                        self.coord.release(sess.live_id, sess.live_ended)
                    except Exception as exc:
                        if not monitor._is_mongo_error(exc):
                            raise
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
    coord = monitor._connect_coord_for_follow(out_root) if os.environ.get("CLIPES_MONGO_URI", "").strip() else None

    sup = Supervisor(
        parsed,
        out_root,
        queue_root=queue_root,
        max_dur=args.max_dur,
        policy=policy,
        coord=coord,
    )
    try:
        sup.run()
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        print(f"[acompanhar] {exc}", flush=True)
        return 1
    return 0
