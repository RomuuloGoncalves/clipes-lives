"""Unit tests for clipes.notificar.notify_discord (F3 T1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clipes.monitor import ChannelPolicy
from clipes.notificar import notify_discord, post_aviso

FAKE_TOKEN = "secret-discord-token-xyz"
FAKE_CHANNEL = "9988776655"


class FakeDiscordClient:
    def __init__(self, *, fail_on_call: int | None = None):
        self.posts: list[dict[str, Any]] = []
        self.threads: list[dict[str, Any]] = []
        self.fail_on_call = fail_on_call
        self._seq = 0

    def post(self, channel_id: str | int, content: str, file: Path | None = None) -> dict[str, Any]:
        self._seq += 1
        if self.fail_on_call is not None and self._seq >= self.fail_on_call:
            raise RuntimeError(f"HTTP 500 token={FAKE_TOKEN}")
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


def _make_out_dir(tmp_path: Path, clips: list[dict[str, Any]], *, discord: dict[str, Any] | None = None) -> Path:
    out_dir = tmp_path / "out_video"
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in clips:
        if c.get("file"):
            (out_dir / c["file"]).write_bytes(b"mp4data")
    manifest: dict[str, Any] = {
        "video_id": "vid1",
        "title": "Live Teste",
        "url": "https://www.twitch.tv/gaules",
        "channel_credit": "twitch.tv/gaules",
        "clips": clips,
    }
    if discord is not None:
        manifest["discord"] = discord
    (out_dir / "clips.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir


def test_notify_sem_job_dir_grava_ids_so_em_clips_json(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)

    out_dir = _make_out_dir(
        tmp_path,
        [
            {"index": 1, "clip_id": "vid1_c01", "file": "c1.mp4", "title": "Clip 1", "score": 0.9, "duration": 30.0},
            {"index": 2, "clip_id": "vid1_c02", "file": "c2.mp4", "title": "Clip 2", "score": 0.8, "duration": 35.0},
        ],
    )
    client = FakeDiscordClient()

    notify_discord(
        out_dir,
        policy=ChannelPolicy(),
        discord_client=client,
        preview_fn=lambda src, dur, cache_dir: src,
    )

    assert len(client.threads) == 1
    assert len(client.posts) == 3
    assert client.posts[0]["channel_id"] == FAKE_CHANNEL
    assert client.posts[1]["channel_id"] == "thr-1"
    assert client.posts[2]["channel_id"] == "thr-1"

    manifest = json.loads((out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["discord"] == {"thread_id": "thr-1", "header_message_id": "msg-1"}
    assert manifest["clips"][0]["discord"] == {"thread_id": "thr-1", "message_id": "msg-2"}
    assert manifest["clips"][1]["discord"] == {"thread_id": "thr-1", "message_id": "msg-3"}
    assert not (out_dir / "job.json").exists()


def test_notify_com_job_dir_atualiza_job_json(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)

    out_dir = _make_out_dir(
        tmp_path,
        [{"index": 1, "clip_id": "vid1_c01", "file": "c1.mp4", "title": "Clip 1", "score": 0.9, "duration": 30.0}],
    )
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    job_data = {"job_id": "gaules_1", "channel": "gaules", "status": "done"}
    (job_dir / "job.json").write_text(json.dumps(job_data), encoding="utf-8")

    client = FakeDiscordClient()
    notify_discord(
        out_dir,
        policy=ChannelPolicy(),
        job_dir=job_dir,
        job=job_data,
        discord_client=client,
        preview_fn=lambda src, dur, cache_dir: src,
    )

    job_after = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job_after["discord"]["thread_id"] == "thr-1"
    assert job_after["discord"]["header_message_id"] == "msg-1"
    assert job_after["discord"]["clips"] == {"vid1_c01": "msg-2"}


def test_notify_reaproveita_thread_existente_e_pula_clipe_postado(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)

    out_dir = _make_out_dir(
        tmp_path,
        [
            {
                "index": 1,
                "clip_id": "vid1_c01",
                "file": "c1.mp4",
                "title": "Clip 1",
                "score": 0.9,
                "duration": 30.0,
                "discord": {"thread_id": "thr-old", "message_id": "msg-old"},
            },
            {
                "index": 2,
                "clip_id": "vid1_c02",
                "file": "c2.mp4",
                "title": "Clip 2",
                "score": 0.8,
                "duration": 35.0,
            },
        ],
        discord={"thread_id": "thr-old", "header_message_id": "hdr-old"},
    )
    client = FakeDiscordClient()

    notify_discord(
        out_dir,
        policy=ChannelPolicy(),
        discord_client=client,
        preview_fn=lambda src, dur, cache_dir: src,
    )

    assert len(client.threads) == 0
    assert len(client.posts) == 1
    assert client.posts[0]["channel_id"] == "thr-old"
    assert "vid1_c02" in client.posts[0]["content"]

    manifest = json.loads((out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["clips"][0]["discord"] == {"thread_id": "thr-old", "message_id": "msg-old"}
    assert manifest["clips"][1]["discord"] == {"thread_id": "thr-old", "message_id": "msg-1"}

    # Second call with all clips posted makes 0 calls
    notify_discord(
        out_dir,
        policy=ChannelPolicy(),
        discord_client=client,
        preview_fn=lambda src, dur, cache_dir: src,
    )
    assert len(client.posts) == 1


def test_notify_zero_clipes_e_sem_env_nao_chama_discord(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)

    empty_dir = _make_out_dir(tmp_path, [])
    client = FakeDiscordClient()
    notify_discord(empty_dir, policy=ChannelPolicy(), discord_client=client)
    assert client.posts == []
    assert client.threads == []

    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    out_dir = _make_out_dir(
        tmp_path / "two",
        [{"index": 1, "clip_id": "vid1_c01", "file": "c1.mp4", "title": "C1", "score": 0.9, "duration": 30.0}],
    )
    notify_discord(out_dir, policy=ChannelPolicy(), discord_client=client)
    assert client.posts == []


def test_notify_falha_mascara_token_no_log_e_nao_levanta(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)

    out_dir = _make_out_dir(
        tmp_path,
        [{"index": 1, "clip_id": "vid1_c01", "file": "c1.mp4", "title": "C1", "score": 0.9, "duration": 30.0}],
    )
    client = FakeDiscordClient(fail_on_call=1)
    notify_discord(
        out_dir,
        policy=ChannelPolicy(),
        discord_client=client,
        preview_fn=lambda src, dur, cache_dir: src,
    )

    out = capsys.readouterr().out
    assert "[discord] falha ao enviar previa" in out
    assert FAKE_TOKEN not in out
    assert "***" in out


def test_post_aviso_com_e_sem_env_e_erro_mascarado(monkeypatch, capsys):
    client = FakeDiscordClient()
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    post_aviso("sem token", discord_client=client)
    assert client.posts == []

    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    post_aviso("aviso teste", discord_client=client)
    assert len(client.posts) == 1
    assert client.posts[0]["channel_id"] == FAKE_CHANNEL
    assert client.posts[0]["content"] == "aviso teste"

    failing = FakeDiscordClient(fail_on_call=1)
    post_aviso("aviso falha", discord_client=failing)
    out = capsys.readouterr().out
    assert "[discord] falha ao enviar aviso" in out
    assert FAKE_TOKEN not in out
    assert "***" in out
