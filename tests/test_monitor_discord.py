"""Tests for Discord preview delivery and publish_requests execution in clipes.monitor (T10)."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import mongomock
import pytest
from pymongo.errors import ServerSelectionTimeoutError

from clipes import monitor
from clipes.coord import Coord
from clipes.discord_rest import DiscordRest

T0 = 1_763_410_000.0
FAKE_BOT_TOKEN = "bot-token-super-secret-123"
FAKE_CHANNEL_ID = "1572417050771595386"
SECRET_URI = "mongodb+srv://worker:supersecret@cluster0.example.mongodb.net/clipes_lives"


class Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += min(seconds, 1.0)
        await asyncio.sleep(0.001)


class FakeDiscordClient:
    def __init__(self, *, fail_on_call: int | None = None):
        self.posts: list[dict[str, Any]] = []
        self.threads: list[dict[str, Any]] = []
        self.fail_on_call = fail_on_call
        self._seq = 0

    def post(self, channel_id: str | int, content: str, file: Path | None = None) -> dict[str, Any]:
        self._seq += 1
        if self.fail_on_call is not None and self._seq >= self.fail_on_call:
            raise RuntimeError(f"Discord HTTP 500 with {FAKE_BOT_TOKEN}")
        msg_id = f"msg-{self._seq}"
        self.posts.append({
            "channel_id": str(channel_id),
            "content": content,
            "file": Path(file) if file is not None else None,
            "id": msg_id,
        })
        return {"id": msg_id}

    def thread_from(
        self,
        channel_id: str | int,
        message_id: str | int,
        name: str,
        auto_archive_duration: int = 1440,
    ) -> str:
        tid = f"thr-{len(self.threads) + 1}"
        self.threads.append({
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "name": name,
            "auto_archive_duration": auto_archive_duration,
            "id": tid,
        })
        return tid


def _make_job_with_clips(
    queue: monitor.DiskQueue,
    tmp_path: Path,
    *,
    job_id: str = "gaules_1763410000",
    channel: str = "gaules",
    clips_spec: list[dict[str, Any]],
    live_title: str = "Live Epica de CS2 com a Galera",
) -> tuple[Path, Path]:
    stage = tmp_path / "staging" / job_id
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "job.json").write_text(
        json.dumps({
            "job_id": job_id,
            "channel": channel,
            "url": f"https://www.twitch.tv/{channel}",
            "z_score": 6.5,
            "created_at": T0,
            "status": "pending",
        }),
        encoding="utf-8",
    )
    queue.enqueue(stage)

    out_dir = tmp_path / "out" / f"2026-11-17 - {live_title} [{job_id}]"
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in clips_spec:
        mp4 = out_dir / c["file"]
        mp4.write_bytes(b"\x00" * int(c.get("size_bytes", 1024)))

    manifest = {
        "video_id": job_id,
        "title": live_title,
        "url": f"https://www.twitch.tv/{channel}",
        "channel": channel,
        "clips": clips_spec,
    }
    (out_dir / "clips.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir, manifest


def test_job_done_2_clipes_posta_cabecalho_thread_2_msgs_e_nao_reposta(tmp_path, monkeypatch):
    """DISC-01 AC 1,2,6: 2 clips -> 1 header, 1 thread (1440m), 2 messages; rerun does not repost."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL_ID)

    queue = monitor.DiskQueue(tmp_path / "q")
    clips_spec = [
        {
            "index": 1,
            "clip_id": "v1_c01",
            "file": "clip_01.mp4",
            "title": "Clutch 1v4 insano",
            "title_source": "gemini",
            "hook_top": "IMPOSSIVEL ISSO",
            "caption": "Clutch absurdo na mirage",
            "description_long": "Descricao longa do clutch na mirage.",
            "llm_score": 9.2,
            "score": 0.91,
            "duration": 38.0,
        },
        {
            "index": 2,
            "clip_id": "v1_c02",
            "file": "clip_02.mp4",
            "title": "Reacao engracada no intervalo",
            "title_source": "local_llm",
            "hook_top": "OLHA O QUE ELE FALOU",
            "caption": "Momento engracado",
            "description_long": "Descricao longa da reacao.",
            "llm_score": 8.0,
            "score": 0.77,
            "duration": 29.0,
        },
    ]
    out_dir, manifest = _make_job_with_clips(queue, tmp_path, clips_spec=clips_spec)
    fake_discord = FakeDiscordClient()
    policy = monitor.ChannelPolicy(delay_h={"gaules": 24.0})

    def pipeline(job_dir, out_root):
        return {**manifest, "output_dir": str(out_dir)}

    assert monitor.run_worker_step(
        queue,
        tmp_path / "out",
        policy,
        pipeline=pipeline,
        discord_client=fake_discord,
        preview_fn=lambda src, dur, cache_dir: src,
    ) is True

    # 1 header in channel + 1 thread + 2 clip messages in thread
    assert len(fake_discord.threads) == 1
    thr = fake_discord.threads[0]
    assert thr["channel_id"] == FAKE_CHANNEL_ID
    assert thr["auto_archive_duration"] == 1440
    assert thr["name"].startswith("gaules · ")

    assert len(fake_discord.posts) == 3
    header = fake_discord.posts[0]
    assert header["channel_id"] == FAKE_CHANNEL_ID
    assert header["file"] is None
    assert "gaules" in header["content"]
    assert "Live Epica de CS2" in header["content"]
    assert "2" in header["content"]
    assert "https://www.twitch.tv/gaules" in header["content"]

    msg1, msg2 = fake_discord.posts[1], fake_discord.posts[2]
    assert msg1["channel_id"] == thr["id"]
    assert msg1["file"] == out_dir / "clip_01.mp4"
    for expected in (
        "v1_c01",
        "Clutch 1v4 insano",
        "IMPOSSIVEL ISSO",
        "9.2",
        "gemini",
        "Clutch absurdo na mirage",
        "Descricao longa do clutch na mirage.",
        "aguardar",
        "publicar v1_c01",
    ):
        assert expected in msg1["content"]

    assert msg2["channel_id"] == thr["id"]
    assert msg2["file"] == out_dir / "clip_02.mp4"
    assert "publicar v1_c02" in msg2["content"]

    updated_manifest = json.loads((out_dir / "clips.json").read_text(encoding="utf-8"))
    assert updated_manifest["discord"]["thread_id"] == thr["id"]
    assert updated_manifest["clips"][0]["discord"] == {"thread_id": thr["id"], "message_id": msg1["id"]}
    assert updated_manifest["clips"][1]["discord"] == {"thread_id": thr["id"], "message_id": msg2["id"]}

    done_job = json.loads((queue.root / "done" / "gaules_1763410000" / "job.json").read_text(encoding="utf-8"))
    assert done_job["discord"]["thread_id"] == thr["id"]

    # Re-running the same output_dir/clips.json must NOT repost any message or thread
    stage2 = tmp_path / "staging" / "gaules_1763410000_retry"
    stage2.mkdir(parents=True)
    (stage2 / "job.json").write_text(
        json.dumps({"job_id": "gaules_1763410000_retry", "channel": "gaules", "z_score": 5.0, "created_at": T0, "status": "pending"}),
        encoding="utf-8",
    )
    queue.enqueue(stage2)
    assert monitor.run_worker_step(
        queue,
        tmp_path / "out",
        policy,
        pipeline=pipeline,
        discord_client=fake_discord,
        preview_fn=lambda src, dur, cache_dir: src,
    ) is True
    assert len(fake_discord.posts) == 3
    assert len(fake_discord.threads) == 1


def test_previa_recompressao_e_arquivo_grande_demais_sem_anexo(tmp_path, monkeypatch):
    """DISC-02 AC 3,4: attaches recompressed preview when returned; posts without file + path when None."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL_ID)

    queue = monitor.DiskQueue(tmp_path / "q")
    clips_spec = [
        {"index": 1, "clip_id": "v2_c01", "file": "clip_recomp.mp4", "title": "Recomp", "score": 0.8, "duration": 30.0},
        {"index": 2, "clip_id": "v2_c02", "file": "clip_gigante.mp4", "title": "Gigante", "score": 0.7, "duration": 120.0},
    ]
    out_dir, manifest = _make_job_with_clips(queue, tmp_path, job_id="gaules_2", clips_spec=clips_spec)
    recomp_path = out_dir / ".cache" / "discord_clip_recomp.mp4"
    recomp_path.parent.mkdir(parents=True, exist_ok=True)
    recomp_path.write_bytes(b"small-preview")

    def fake_preview(src: Path, duration: float, cache_dir: Path) -> Path | None:
        if src.name == "clip_recomp.mp4":
            return recomp_path
        return None

    fake_discord = FakeDiscordClient()
    assert monitor.run_worker_step(
        queue,
        tmp_path / "out",
        monitor.ChannelPolicy(),
        pipeline=lambda *_: {**manifest, "output_dir": str(out_dir)},
        discord_client=fake_discord,
        preview_fn=fake_preview,
    ) is True

    assert len(fake_discord.posts) == 3
    assert fake_discord.posts[1]["file"] == recomp_path
    assert fake_discord.posts[2]["file"] is None
    assert "arquivo grande demais para o Discord" in fake_discord.posts[2]["content"]
    assert str(out_dir / "clip_gigante.mp4") in fake_discord.posts[2]["content"]


def test_falha_discord_429_grava_discord_error_sem_token_e_worker_segue(tmp_path, monkeypatch):
    """DISC-03 AC 5: 4x 429 records discord_error (scrubbed) on job and worker keeps processing."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL_ID)

    queue = monitor.DiskQueue(tmp_path / "q")
    out1, man1 = _make_job_with_clips(
        queue,
        tmp_path,
        job_id="gaules_fail",
        clips_spec=[{"index": 1, "clip_id": "vf_c01", "file": "c1.mp4", "title": "T1", "score": 0.9, "duration": 20.0}],
    )
    stage_next = tmp_path / "staging" / "gaules_next"
    stage_next.mkdir(parents=True)
    (stage_next / "job.json").write_text(
        json.dumps({"job_id": "gaules_next", "channel": "gaules", "z_score": 1.0, "created_at": T0, "status": "pending"}),
        encoding="utf-8",
    )
    queue.enqueue(stage_next)

    sleeps: list[float] = []

    def always_429(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url,
            429,
            "Too Many Requests",
            hdrs={"Retry-After": "0.25"},
            fp=io.BytesIO(json.dumps({"retry_after": 0.25}).encode("utf-8")),
        )

    rest = DiscordRest(FAKE_BOT_TOKEN, urlopen=always_429, sleep=sleeps.append)

    assert monitor.run_worker_step(
        queue,
        tmp_path / "out",
        monitor.ChannelPolicy(),
        pipeline=lambda *_: {**man1, "output_dir": str(out1)},
        discord_client=rest,
        preview_fn=lambda src, dur, cache_dir: src,
    ) is True

    assert sleeps == [0.25, 0.25, 0.25]
    failed_job = json.loads((queue.root / "done" / "gaules_fail" / "job.json").read_text(encoding="utf-8"))
    assert "discord_error" in failed_job
    assert "429" in failed_job["discord_error"]
    assert FAKE_BOT_TOKEN not in failed_job["discord_error"]

    # Worker continues to process the next job even though Discord failed
    assert monitor.run_worker_step(
        queue,
        tmp_path / "out",
        monitor.ChannelPolicy(),
        pipeline=lambda *_: {"clips": []},
        discord_client=rest,
    ) is True
    assert (queue.root / "rejected" / "gaules_next").is_dir()


def test_falha_em_um_clipe_registra_discord_error_sem_token_e_continua_proximos(tmp_path, monkeypatch):
    """M1: falha ao enviar um clipe isolado grava discord_error sanitizado e continua proximos clipes."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL_ID)

    queue = monitor.DiskQueue(tmp_path / "q")
    clips_spec = [
        {"index": 1, "clip_id": "c_fail", "file": "c1.mp4", "title": "Clip 1", "score": 0.9, "duration": 20.0},
        {"index": 2, "clip_id": "c_ok", "file": "c2.mp4", "title": "Clip 2", "score": 0.8, "duration": 20.0},
    ]
    out_dir, manifest = _make_job_with_clips(queue, tmp_path, job_id="job_part_fail", clips_spec=clips_spec)

    class PartialFailClient(FakeDiscordClient):
        def post(self, channel_id: str | int, content: str, file: Path | None = None) -> dict[str, Any]:
            self._seq += 1
            if self._seq == 2:
                raise RuntimeError(f"HTTP 500 simulating failure with {FAKE_BOT_TOKEN}")
            msg_id = f"msg-{self._seq}"
            self.posts.append({
                "channel_id": str(channel_id),
                "content": content,
                "file": Path(file) if file is not None else None,
                "id": msg_id,
            })
            return {"id": msg_id}

    fake_discord = PartialFailClient()
    policy = monitor.ChannelPolicy()

    assert monitor.run_worker_step(
        queue,
        tmp_path / "out",
        policy,
        pipeline=lambda *_: {**manifest, "output_dir": str(out_dir)},
        discord_client=fake_discord,
        preview_fn=lambda src, dur, cache_dir: src,
    ) is True

    # 1 cabecalho + 1 clipe enviado com sucesso (c_ok)
    assert len(fake_discord.posts) == 2
    header, msg_ok = fake_discord.posts[0], fake_discord.posts[1]
    assert header["channel_id"] == FAKE_CHANNEL_ID
    assert msg_ok["channel_id"] == fake_discord.threads[0]["id"]
    assert "c_ok" in msg_ok["content"]

    updated_manifest = json.loads((out_dir / "clips.json").read_text(encoding="utf-8"))
    assert "discord" not in updated_manifest["clips"][0]
    assert updated_manifest["clips"][1]["discord"]["message_id"] == msg_ok["id"]

    done_job = json.loads((queue.root / "done" / "job_part_fail" / "job.json").read_text(encoding="utf-8"))
    assert "discord_error" in done_job
    assert FAKE_BOT_TOKEN not in done_job["discord_error"]
    assert "***" in done_job["discord_error"]
    assert "c_fail" not in (done_job.get("discord") or {}).get("clips", {})
    assert (done_job.get("discord") or {}).get("clips", {}).get("c_ok") == msg_ok["id"]


def test_sem_discord_bot_token_pula_previa_e_run_pending(tmp_path, monkeypatch):
    """Sem DISCORD_BOT_TOKEN, pula previa mas executa run_pending no heartbeat sem erro."""
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL_ID)

    queue = monitor.DiskQueue(tmp_path / "q")
    out_dir, man = _make_job_with_clips(
        queue,
        tmp_path,
        job_id="gaules_notok",
        clips_spec=[{"index": 1, "clip_id": "vnt_c01", "file": "c1.mp4", "title": "T", "score": 0.9, "duration": 20.0}],
    )

    assert monitor.run_worker_step(
        queue,
        tmp_path / "out",
        monitor.ChannelPolicy(),
        pipeline=lambda *_: {**man, "output_dir": str(out_dir)},
    ) is True
    done_job = json.loads((queue.root / "done" / "gaules_notok" / "job.json").read_text(encoding="utf-8"))
    assert "discord_error" not in done_job
    assert "discord" not in done_job

    # Heartbeat with Coord and no DISCORD_BOT_TOKEN runs run_pending without error
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-notok", now=clock)
    db.publish_requests.insert_one({
        "_id": "vnt_c01:youtube",
        "clip_id": "vnt_c01",
        "plataforma": "youtube",
        "owner": "w-notok",
        "status": "pending",
        "resultado": None,
        "criado_em": coord._dt(),
    })

    called: list[bool] = []

    async def irc(channels, on_message, stop):
        for _ in range(50):
            if called:
                break
            await clock.sleep(1)
        stop.set()

    asyncio.run(monitor.run_monitor(
        ["gaules"],
        tmp_path / "out",
        monitor.ChannelPolicy(),
        queue_root=queue.root,
        irc=irc,
        pipeline=lambda *_: {"clips": []},
        now=clock,
        sleep=clock.sleep,
        coord=coord,
        capacity_fn=lambda _: {"cpus": 2, "score": 2},
        run_pending_fn=lambda *a, **kw: called.append(True) or [],
    ))
    assert len(called) >= 1
    assert db.publish_requests.find_one({"_id": "vnt_c01:youtube"})["status"] == "pending"


def test_heartbeat_executa_publicacao_run_pending_para_proprio_worker(tmp_path, monkeypatch):
    """PUB-01 / T10: heartbeat runs publicacao.run_pending for own worker_id when DISCORD_BOT_TOKEN is set."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("CLIPES_BEAT_S", "20")
    monkeypatch.setenv("CLIPES_TOKENS_PATH", str(tmp_path / "tokens.json"))

    (tmp_path / "tokens.json").write_text(
        json.dumps({"youtube": {"access_token": "yt-acc-secret", "refresh_token": "yt-ref"}}),
        encoding="utf-8",
    )
    out_root = tmp_path / "out"
    clip_dir = out_root / "2026-11-17 - Live [live1]"
    clip_dir.mkdir(parents=True)
    (clip_dir / "clip_01.mp4").write_bytes(b"mp4")
    (clip_dir / "clips.json").write_text(
        json.dumps({
            "video_id": "live1",
            "title": "Live",
            "channel": "gaules",
            "owner": "w-pub",
            "clips": [{
                "index": 1,
                "clip_id": "live1_c01",
                "file": "clip_01.mp4",
                "title": "Jogada",
                "description_long": "Descricao",
                "discord": {"thread_id": "thr-99", "message_id": "msg-1"},
            }],
        }),
        encoding="utf-8",
    )

    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-pub", now=clock)
    db.publish_requests.insert_many([
        {
            "_id": "live1_c01:youtube",
            "clip_id": "live1_c01",
            "plataforma": "youtube",
            "owner": "w-pub",
            "status": "pending",
            "resultado": None,
            "thread_id": "thr-99",
            "criado_em": coord._dt(),
        },
        {
            "_id": "other_c01:youtube",
            "clip_id": "other_c01",
            "plataforma": "youtube",
            "owner": "w-outro",
            "status": "pending",
            "resultado": None,
            "thread_id": "thr-2",
            "criado_em": coord._dt(),
        },
    ])

    fake_discord = FakeDiscordClient()
    from clipes import publicacao

    def fake_run_pending(store, worker_id, **kwargs):
        return publicacao.run_pending(
            store,
            worker_id,
            publishers={"youtube": lambda mp4, title, desc, tok: {"video_id": "yt-abc-123"}},
            **kwargs,
        )

    async def irc(channels, on_message, stop):
        for _ in range(300):
            doc = db.publish_requests.find_one({"_id": "live1_c01:youtube"})
            if doc and doc["status"] == "ok":
                break
            await clock.sleep(1)
        stop.set()

    asyncio.run(monitor.run_monitor(
        ["gaules"],
        out_root,
        monitor.ChannelPolicy(),
        queue_root=tmp_path / "q",
        irc=irc,
        pipeline=lambda *_: {"clips": []},
        now=clock,
        sleep=clock.sleep,
        coord=coord,
        capacity_fn=lambda _: {"cpus": 2, "score": 2},
        discord_client=fake_discord,
        run_pending_fn=fake_run_pending,
    ))

    mine = db.publish_requests.find_one({"_id": "live1_c01:youtube"})
    assert mine["status"] == "ok"
    assert mine["resultado"]["video_id"] == "yt-abc-123"
    assert mine["resultado"]["privacidade"] == "private"
    assert db.publish_requests.find_one({"_id": "other_c01:youtube"})["status"] == "pending"
    assert len(fake_discord.posts) == 1
    assert fake_discord.posts[0]["channel_id"] == "thr-99"
    assert "yt-abc-123" in fake_discord.posts[0]["content"]


def test_heartbeat_run_pending_erro_mongo_marca_offline_sem_vazar_uri(tmp_path, monkeypatch, capsys):
    """FILA-03 AC 11-12: Mongo error inside run_pending marks coord_session offline and masks URI."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("CLIPES_MONGO_URI", SECRET_URI)
    clock = Clock()
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-err", now=clock)
    calls: list[int] = []

    def boom_pending(*args, **kwargs):
        calls.append(1)
        raise ServerSelectionTimeoutError(f"cannot reach {SECRET_URI}")

    async def irc(channels, on_message, stop):
        while not calls:
            await clock.sleep(1)
        await clock.sleep(1)
        stop.set()

    asyncio.run(monitor.run_monitor(
        ["gaules"],
        tmp_path / "out",
        monitor.ChannelPolicy(),
        queue_root=tmp_path / "q",
        irc=irc,
        pipeline=lambda *_: {"clips": []},
        now=clock,
        sleep=clock.sleep,
        coord=coord,
        capacity_fn=lambda _: {"cpus": 2, "score": 2},
        run_pending_fn=boom_pending,
    ))

    out_log = capsys.readouterr().out
    assert len(calls) == 1
    assert "supersecret" not in out_log
    assert "mongodb+srv://***@***" in out_log
