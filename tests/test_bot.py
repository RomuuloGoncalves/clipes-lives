"""Unit tests for clipes.bot (T8: Discord bot with publicar and listar commands, no gateway)."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
import mongomock
import pytest

from clipes import bot as bot_mod
from clipes.publicacao import LocalStore, MongoStore

T0 = 1_763_400_000.0
ALLOWED_USER = "111222333444"
OTHER_USER = "999888777666"
CHANNEL_ID = 1572417050771595386
GUILD_ID = 1572417049932599479
OTHER_CHANNEL_ID = 987654321012345678
THREAD_ID = 555666777888999000


class _FakeUser:
    def __init__(self, uid: int | str, *, bot: bool = False):
        self.id = int(uid)
        self.bot = bot


class _FakeChannel:
    def __init__(self, cid: int | str, *, parent_id: int | str | None = None):
        self.id = int(cid)
        self.parent_id = int(parent_id) if parent_id is not None else None
        self.sent: list[str] = []

    async def send(self, content: str, **kwargs: Any) -> None:
        self.sent.append(content)


class _FakeMessage:
    def __init__(
        self,
        content: str,
        *,
        author_id: int | str = ALLOWED_USER,
        author_bot: bool = False,
        channel: _FakeChannel | None = None,
    ):
        self.content = content
        self.author = _FakeUser(author_id, bot=author_bot)
        self.channel = channel or _FakeChannel(CHANNEL_ID)
        self.replies: list[str] = []

    async def reply(self, content: str, **kwargs: Any) -> None:
        self.replies.append(content)


class _FakeInteractionResponse:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    async def send_message(self, content: str, *, ephemeral: bool = False, **kwargs: Any) -> None:
        self.messages.append((content, ephemeral))


class _FakeInteraction:
    def __init__(
        self,
        *,
        user_id: int | str = ALLOWED_USER,
        channel: _FakeChannel | None = None,
    ):
        self.user = _FakeUser(user_id)
        self.channel = channel or _FakeChannel(CHANNEL_ID)
        self.channel_id = self.channel.id
        self.response = _FakeInteractionResponse()


def _make_clip(
    out_root: Path,
    *,
    video_id: str = "live1",
    clip_id: str = "live1_c01",
    channel: str = "gaules",
    owner: str = "w-pc1",
    ready_at: float | None = None,
    thread_id: str | None = str(THREAD_ID),
    folder_name: str | None = None,
    mtime: float | None = None,
) -> Path:
    clip_dir = out_root / (folder_name or f"2026-11-17 - Live [{video_id}]")
    clip_dir.mkdir(parents=True, exist_ok=True)
    (clip_dir / f"{clip_id}.mp4").write_bytes(b"fake-mp4")

    clip_entry: dict[str, Any] = {
        "index": 1,
        "clip_id": clip_id,
        "file": f"{clip_id}.mp4",
        "title": f"Titulo {clip_id}",
    }
    if thread_id is not None:
        clip_entry["discord"] = {"thread_id": thread_id, "message_id": f"msg-{clip_id}"}
    if ready_at is not None:
        clip_entry["ready_at"] = ready_at

    manifest = {
        "video_id": video_id,
        "title": f"Live {video_id}",
        "channel": channel,
        "owner": owner,
        "created_at": T0 - 3600,
        "clips": [clip_entry],
    }
    clips_json = clip_dir / "clips.json"
    clips_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if mtime is not None:
        os.utime(clips_json, (mtime, mtime))
    return clips_json


def _write_tokens(path: Path, data: dict[str, Any] | None = None) -> Path:
    payload = data if data is not None else {"youtube": {"refresh_token": "secret-refresh-token"}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _mongo_store(worker_id: str = "w-pc1", alias: str = "pc-mesa", heartbeat_ts: float = T0) -> tuple[Any, MongoStore]:
    client = mongomock.MongoClient()
    db = client.clipes_lives
    if heartbeat_ts is not None:
        hb = datetime.fromtimestamp(heartbeat_ts, timezone.utc).replace(tzinfo=None)
        db.workers.insert_one({"_id": worker_id, "alias": alias, "heartbeat_at": hb})
    return db, MongoStore(db)


# --- T8: Intents, guild sync, commands ---


def test_make_bot_intents_commands_and_guild_sync(tmp_path: Path):
    store = LocalStore(tmp_path)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
    )

    assert isinstance(bot, discord.Client)
    assert bot.intents.message_content is True

    pub_cmd = bot.tree.get_command("publicar")
    list_cmd = bot.tree.get_command("listar")
    assert pub_cmd is not None
    assert [p.name for p in pub_cmd.parameters] == ["id"]
    assert list_cmd is not None

    synced: list[tuple[str, int | None]] = []

    def fake_copy(*, guild: Any) -> None:
        synced.append(("copy", getattr(guild, "id", None)))

    async def fake_sync(*, guild: Any = None) -> list[Any]:
        synced.append(("sync", getattr(guild, "id", None)))
        return []

    bot.tree.copy_global_to = fake_copy  # type: ignore[method-assign]
    bot.tree.sync = fake_sync  # type: ignore[method-assign]

    asyncio.run(bot.setup_hook())
    assert synced == [("copy", GUILD_ID), ("sync", GUILD_ID)]


# --- T8 Done when: Mensagem fora do canal configurado e das threads dele e ignorada ---


def test_on_message_ignores_outside_channel_threads_bot_and_non_command(tmp_path: Path):
    _make_clip(tmp_path)
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    db, store = _mongo_store()
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
    )

    msg_other_channel = _FakeMessage("publicar live1_c01", channel=_FakeChannel(OTHER_CHANNEL_ID))
    msg_other_thread = _FakeMessage("publicar live1_c01", channel=_FakeChannel(THREAD_ID, parent_id=OTHER_CHANNEL_ID))
    msg_from_bot = _FakeMessage("publicar live1_c01", author_bot=True, channel=_FakeChannel(CHANNEL_ID))
    msg_prose = _FakeMessage("Use: publicar live1_c01 para publicar", channel=_FakeChannel(CHANNEL_ID))

    async def run_all() -> None:
        await bot.on_message(msg_other_channel)
        await bot.on_message(msg_other_thread)
        await bot.on_message(msg_from_bot)
        await bot.on_message(msg_prose)

    asyncio.run(run_all())

    assert msg_other_channel.replies == []
    assert msg_other_channel.channel.sent == []
    assert msg_other_thread.replies == []
    assert msg_other_thread.channel.sent == []
    assert msg_from_bot.replies == []
    assert msg_prose.replies == []
    assert db.publish_requests.count_documents({}) == 0


# --- T8 + PUB-01: Message `publicar <id>` in configured channel and its threads ---


def test_on_message_publicar_in_channel_and_thread_enqueues_requests(tmp_path: Path):
    _make_clip(tmp_path, video_id="live1", clip_id="live1_c01", thread_id=None)
    _make_clip(tmp_path, video_id="live2", clip_id="live2_c01", thread_id=None)
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    db, store = _mongo_store()
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
    )

    msg_channel = _FakeMessage("publicar live1_c01", channel=_FakeChannel(CHANNEL_ID))
    msg_thread = _FakeMessage("  Publicar live2_c01  ", channel=_FakeChannel(THREAD_ID, parent_id=CHANNEL_ID))

    async def run_all() -> None:
        await bot.on_message(msg_channel)
        await bot.on_message(msg_thread)

    asyncio.run(run_all())

    assert len(msg_channel.replies) == 1
    assert "youtube: publicacao enfileirada" in msg_channel.replies[0]
    assert "tiktok: desligada" in msg_channel.replies[0]

    assert len(msg_thread.replies) == 1
    assert "youtube: publicacao enfileirada" in msg_thread.replies[0]

    doc1 = db.publish_requests.find_one({"_id": "live1_c01:youtube"})
    doc2 = db.publish_requests.find_one({"_id": "live2_c01:youtube"})
    assert doc1 is not None and doc1["status"] == "pending" and doc1["thread_id"] is None
    assert doc2 is not None and doc2["status"] == "pending" and doc2["thread_id"] == str(THREAD_ID)


def test_on_message_publicar_denies_unauthorized_and_empty_allowed_list(tmp_path: Path):
    _make_clip(tmp_path)
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    db, store = _mongo_store()

    bot_restricted = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
    )
    msg_unauth = _FakeMessage("publicar live1_c01", author_id=OTHER_USER)
    asyncio.run(bot_restricted.on_message(msg_unauth))
    assert msg_unauth.replies == ["sem permissao"]
    assert db.publish_requests.count_documents({}) == 0

    bot_empty = bot_mod.make_bot(
        store,
        allowed_ids=[],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
    )
    msg_owner = _FakeMessage("publicar live1_c01", author_id=ALLOWED_USER)
    asyncio.run(bot_empty.on_message(msg_owner))
    assert msg_owner.replies == ["sem permissao"]
    assert db.publish_requests.count_documents({}) == 0


# --- PUB-01: Slash `/publicar id:` ---


def test_slash_publicar_unauthorized_responds_ephemeral_and_registers_nothing(tmp_path: Path):
    _make_clip(tmp_path)
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    db, store = _mongo_store()
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
    )

    pub_cmd = bot.tree.get_command("publicar")
    assert pub_cmd is not None
    inter = _FakeInteraction(user_id=OTHER_USER)
    asyncio.run(pub_cmd.callback(inter, "live1_c01"))

    assert inter.response.messages == [("sem permissao", True)]
    assert db.publish_requests.count_documents({}) == 0


def test_slash_publicar_covers_happy_not_found_policy_idempotency_quota_and_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _make_clip(tmp_path, video_id="ok_vid", clip_id="ok_vid_c01", channel="gaules", owner="w-pc1", thread_id=str(THREAD_ID))
    _make_clip(tmp_path, video_id="blk_vid", clip_id="blk_vid_c01", channel="banido", owner="w-pc1")
    _make_clip(tmp_path, video_id="wait_vid", clip_id="wait_vid_c01", channel="gaules", owner="w-pc1", ready_at=T0 + 3600)
    _make_clip(tmp_path, video_id="off_vid", clip_id="off_vid_c01", channel="gaules", owner="w-offline")

    tokens_path = _write_tokens(tmp_path / "tokens.json")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"blocked": ["banido"]}), encoding="utf-8")
    quota_path = tmp_path / ".cotas_publicacao.json"
    monkeypatch.setenv("CLIPES_YT_MAX_DIA", "1")

    db, store = _mongo_store("w-pc1", "pc-mesa", T0)
    hb_old = datetime.fromtimestamp(T0 - 600, timezone.utc).replace(tzinfo=None)
    db.workers.insert_one({"_id": "w-offline", "alias": "pc-quarto", "heartbeat_at": hb_old})

    thread_ch = _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        policy_path=policy_path,
        quota_path=quota_path,
        now=T0,
    )
    bot.get_channel = lambda cid: thread_ch if int(cid) == THREAD_ID else None  # type: ignore[method-assign]
    pub_cmd = bot.tree.get_command("publicar")
    assert pub_cmd is not None

    # 1. Happy path from main channel -> replies to interaction AND forwards to clip thread
    inter_ok = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(pub_cmd.callback(inter_ok, "ok_vid_c01"))
    assert len(inter_ok.response.messages) == 1
    assert "youtube: publicacao enfileirada" in inter_ok.response.messages[0][0]
    assert inter_ok.response.messages[0][1] is False
    assert any("youtube: publicacao enfileirada" in msg for msg in thread_ch.sent)
    assert db.publish_requests.find_one({"_id": "ok_vid_c01:youtube"})["status"] == "pending"

    # 2. Not found
    inter_nf = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(pub_cmd.callback(inter_nf, "inexistente_c99"))
    assert inter_nf.response.messages == [("clipe inexistente_c99 nao encontrado", False)]

    # 3. Policy blocked & ready_at
    inter_blk = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(pub_cmd.callback(inter_blk, "blk_vid_c01"))
    assert inter_blk.response.messages == [("bloqueado pela politica", False)]
    assert db.publish_requests.find_one({"_id": "blk_vid_c01:youtube"}) is None

    inter_wait = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(pub_cmd.callback(inter_wait, "wait_vid_c01"))
    assert inter_wait.response.messages[0][0].startswith("aguardar ate ")
    assert db.publish_requests.find_one({"_id": "wait_vid_c01:youtube"}) is None

    # 4. Idempotency when already ok
    db.publish_requests.update_one(
        {"_id": "ok_vid_c01:youtube"},
        {"$set": {"status": "ok", "resultado": {"video_id": "ytXYZ", "url": "https://youtube.com/shorts/ytXYZ"}}},
    )
    inter_dup = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(pub_cmd.callback(inter_dup, "ok_vid_c01"))
    assert "https://youtube.com/shorts/ytXYZ" in inter_dup.response.messages[0][0]
    assert "enfileirada" not in inter_dup.response.messages[0][0]

    # 5. Offline PC
    inter_off = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(pub_cmd.callback(inter_off, "off_vid_c01"))
    assert "PC pc-quarto offline; pedido fica na fila" in inter_off.response.messages[0][0]
    assert db.publish_requests.find_one({"_id": "off_vid_c01:youtube"})["status"] == "pending"

    # 6. Quota exhausted
    day = datetime.fromtimestamp(T0, timezone.utc).strftime("%Y-%m-%d")
    quota_path.write_text(json.dumps({day: {"youtube": 1}}), encoding="utf-8")
    _make_clip(tmp_path, video_id="q_vid", clip_id="q_vid_c01", channel="gaules", owner="w-pc1")
    inter_q = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(pub_cmd.callback(inter_q, "q_vid_c01"))
    assert "cota diaria atingida (youtube)" in inter_q.response.messages[0][0]
    assert db.publish_requests.find_one({"_id": "q_vid_c01:youtube"})["status"] == "adiado"


# --- Slash `/listar` ---


def test_slash_listar_returns_last_10_clips_and_handles_empty(tmp_path: Path):
    store = LocalStore(tmp_path)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
    )
    list_cmd = bot.tree.get_command("listar")
    assert list_cmd is not None

    inter_empty = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(list_cmd.callback(inter_empty))
    assert inter_empty.response.messages == [("nenhum clipe encontrado", False)]

    for i in range(1, 13):
        _make_clip(
            tmp_path,
            video_id=f"vid{i:02d}",
            clip_id=f"vid{i:02d}_c01",
            folder_name=f"run_{i:02d}",
            mtime=T0 + i * 10,
        )

    inter_full = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(list_cmd.callback(inter_full))
    text, ephemeral = inter_full.response.messages[0]
    assert ephemeral is False
    for i in range(3, 13):
        assert f"vid{i:02d}_c01" in text
    assert "vid01_c01" not in text
    assert "vid02_c01" not in text


# --- Single-PC LocalStore direct execution ---


def test_local_mode_runs_pending_in_process_after_enqueue(tmp_path: Path):
    _make_clip(tmp_path, video_id="local1", clip_id="local1_c01")
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    store = LocalStore(tmp_path)
    ran_calls: list[tuple[Any, str | None]] = []

    def fake_run_pending(st: Any, worker_id: str | None, **kwargs: Any) -> list[dict[str, Any]]:
        ran_calls.append((st, worker_id))
        return [{"_id": "local1_c01:youtube", "status": "ok"}]

    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
        run_local=True,
        run_pending_fn=fake_run_pending,
    )
    pub_cmd = bot.tree.get_command("publicar")
    assert pub_cmd is not None
    inter = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(pub_cmd.callback(inter, "local1_c01"))

    assert "youtube: publicacao enfileirada" in inter.response.messages[0][0]
    assert len(ran_calls) == 1
    assert ran_calls[0][0] is store


# --- main() CLI check without secret ---


def test_main_missing_token_exits_2_without_secrets(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("CLIPES_MONGO_URI", "mongodb+srv://user:supersecret@cluster.example.net/db")

    code = bot_mod.main([])
    captured = capsys.readouterr()

    assert code == 2
    assert "DISCORD_BOT_TOKEN" in captured.err
    assert "supersecret" not in captured.err
    assert "supersecret" not in captured.out
