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
from discord.components import _component_factory

from clipes import __main__ as entry
from clipes import bot as bot_mod
from clipes import oauth as oauth_mod
from clipes import publicacao
from clipes.publicacao import LocalStore, MongoStore

T0 = 1_763_400_000.0
ALLOWED_USER = "111222333444"
OTHER_USER = "999888777666"
CHANNEL_ID = 1572417050771595386
GUILD_ID = 1572417049932599479
OTHER_CHANNEL_ID = 987654321012345678
THREAD_ID = 555666777888999000


class _FakeUser:
    def __init__(self, uid: int | str, *, bot: bool = False, name: str | None = None):
        self.id = int(uid)
        self.bot = bot
        self.name = name or str(uid)
        self.display_name = self.name


class _FakeChannel:
    def __init__(self, cid: int | str, *, parent_id: int | str | None = None):
        self.id = int(cid)
        self.parent_id = int(parent_id) if parent_id is not None else None
        self.sent: list[str] = []
        self.sent_kwargs: list[dict[str, Any]] = []

    async def send(self, content: str, **kwargs: Any) -> None:
        self.sent.append(content)
        self.sent_kwargs.append(kwargs)


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
        self.reply_kwargs: list[dict[str, Any]] = []

    async def reply(self, content: str, **kwargs: Any) -> None:
        self.replies.append(content)
        self.reply_kwargs.append(kwargs)


class _FakeFollowup:
    """followup.send grava no mesmo log do response (A-07: handlers fazem defer e respondem por followup)."""

    def __init__(self, response: "_FakeInteractionResponse") -> None:
        self._response = response

    async def send(self, content: str, *, ephemeral: bool = False, **kwargs: Any) -> None:
        self._response.messages.append((content, ephemeral))
        self._response.kwargs_list.append(kwargs)


class _FakeInteractionResponse:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []
        self.kwargs_list: list[dict[str, Any]] = []
        self.deferred: list[bool] = []
        self._done = False
        self.followup = _FakeFollowup(self)

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, **kwargs: Any) -> None:
        self.deferred.append(ephemeral)
        self._done = True

    async def send_message(self, content: str, *, ephemeral: bool = False, **kwargs: Any) -> None:
        self._done = True
        self.messages.append((content, ephemeral))
        self.kwargs_list.append(kwargs)


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
        self.followup = self.response.followup


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
    assert [p.name for p in pub_cmd.parameters] == ["id", "agora"]
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
    # onda 3: cota cheia nao cria mais `adiado` terminal; o pedido fica pending agendado para o proximo dia
    doc_q = db.publish_requests.find_one({"_id": "q_vid_c01:youtube"})
    assert doc_q["status"] == "pending"
    assert doc_q["agendar_para"] > datetime.fromtimestamp(T0, timezone.utc).replace(tzinfo=None)


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


def test_slash_publicar_and_listar_reject_outside_configured_channel_and_threads(tmp_path: Path):
    """S1: /publicar and /listar must reject outside configured channel or its threads with ephemeral message."""
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
    list_cmd = bot.tree.get_command("listar")
    assert pub_cmd is not None and list_cmd is not None

    wrong_channels = [
        _FakeChannel(OTHER_CHANNEL_ID),
        _FakeChannel(THREAD_ID, parent_id=OTHER_CHANNEL_ID),
    ]
    for ch in wrong_channels:
        inter_pub = _FakeInteraction(user_id=ALLOWED_USER, channel=ch)
        inter_list = _FakeInteraction(user_id=ALLOWED_USER, channel=ch)
        asyncio.run(pub_cmd.callback(inter_pub, "live1_c01"))
        asyncio.run(list_cmd.callback(inter_list))
        assert inter_pub.response.messages == [("sem permissao", True)]
        assert inter_list.response.messages == [("sem permissao", True)]

    assert db.publish_requests.count_documents({}) == 0

    # Allowed inside a thread of the configured channel
    valid_thread = _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
    inter_thread_list = _FakeInteraction(user_id=ALLOWED_USER, channel=valid_thread)
    asyncio.run(list_cmd.callback(inter_thread_list))
    assert inter_thread_list.response.messages[0][1] is False
    assert "live1_c01" in inter_thread_list.response.messages[0][0]

    inter_thread_pub = _FakeInteraction(user_id=ALLOWED_USER, channel=valid_thread)
    asyncio.run(pub_cmd.callback(inter_thread_pub, "live1_c01"))
    assert "youtube: publicacao enfileirada" in inter_thread_pub.response.messages[0][0]


def test_slash_listar_denies_unauthorized_and_empty_allowed_list(tmp_path: Path):
    """S1: /listar must require an allowed user and never leak clip titles/IDs to unauthorized users."""
    _make_clip(tmp_path)
    store = LocalStore(tmp_path)

    bot_restricted = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
    )
    list_cmd = bot_restricted.tree.get_command("listar")
    assert list_cmd is not None
    inter_other = _FakeInteraction(user_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(list_cmd.callback(inter_other))
    assert inter_other.response.messages == [("sem permissao", True)]

    bot_empty = bot_mod.make_bot(
        store,
        allowed_ids=[],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
    )
    list_cmd_empty = bot_empty.tree.get_command("listar")
    assert list_cmd_empty is not None
    inter_owner = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(list_cmd_empty.callback(inter_owner))
    assert inter_owner.response.messages == [("sem permissao", True)]


def test_slash_rejects_when_channel_id_not_configured(tmp_path: Path):
    """S1 fail-closed: unconfigured channel_id rejects /publicar and /listar."""
    _make_clip(tmp_path)
    store = LocalStore(tmp_path)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=None,
        guild_id=GUILD_ID,
        out_root=tmp_path,
    )
    pub_cmd = bot.tree.get_command("publicar")
    list_cmd = bot.tree.get_command("listar")
    assert pub_cmd is not None and list_cmd is not None

    inter_pub = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(CHANNEL_ID))
    inter_list = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(pub_cmd.callback(inter_pub, "live1_c01"))
    asyncio.run(list_cmd.callback(inter_list))
    assert inter_pub.response.messages == [("sem permissao", True)]
    assert inter_list.response.messages == [("sem permissao", True)]


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


# --- T9: Entrypoint routing for `bot` and `auth` ---


def test_entrypoint_bot_missing_token_exits_2_without_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("CLIPES_MONGO_URI", "mongodb+srv://user:supersecret@cluster.example.net/db")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "yt-supersecret")

    code = entry.main(["bot"])
    captured = capsys.readouterr()

    assert code == 2
    assert "DISCORD_BOT_TOKEN" in captured.err
    assert "supersecret" not in captured.err + captured.out
    assert "yt-supersecret" not in captured.err + captured.out


def test_entrypoint_bot_routes_to_bot_main(monkeypatch: pytest.MonkeyPatch):
    received: list[list[str] | None] = []

    def fake_bot_main(argv: list[str] | None = None) -> int:
        received.append(argv)
        return 0

    monkeypatch.setattr(bot_mod, "main", fake_bot_main)

    code = entry.main(["bot", "--extra"])
    assert code == 0
    assert received == [["--extra"]]


def test_entrypoint_auth_invalid_platform_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    called: list[str] = []

    def fail_if_called(plataforma: str, *args: Any, **kwargs: Any) -> Path:
        called.append(plataforma)
        raise AssertionError("auth_flow should not be called for invalid platform")

    monkeypatch.setattr(oauth_mod, "auth_flow", fail_if_called)

    code = entry.main(["auth", "foo"])
    captured = capsys.readouterr()

    assert code == 2
    assert called == []
    assert "foo" in captured.err
    assert "youtube" in captured.err and "tiktok" in captured.err


def test_entrypoint_auth_routes_youtube_and_tiktok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    called: list[str] = []

    def fake_auth_flow(plataforma: str, *args: Any, **kwargs: Any) -> Path:
        called.append(plataforma)
        return tmp_path / "tokens.json"

    monkeypatch.setattr(oauth_mod, "auth_flow", fake_auth_flow)

    assert entry.main(["auth", "youtube"]) == 0
    assert entry.main(["auth", "tiktok"]) == 0
    assert called == ["youtube", "tiktok"]


def test_entrypoint_auth_passes_explicit_bind_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str | None]] = []

    def fake_auth_flow(plataforma: str, *args: Any, **kwargs: Any) -> Path:
        calls.append((plataforma, kwargs.get("bind_host")))
        return tmp_path / "tokens.json"

    monkeypatch.setattr(oauth_mod, "auth_flow", fake_auth_flow)

    assert entry.main(["auth", "youtube", "--bind", "0.0.0.0"]) == 0
    assert calls == [("youtube", "0.0.0.0")]


def test_entrypoint_auth_oauth_error_exits_2_without_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "yt-secret-xyz")

    def fake_auth_flow(plataforma: str, *args: Any, **kwargs: Any) -> Path:
        raise oauth_mod.OAuthError("YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET are required")

    monkeypatch.setattr(oauth_mod, "auth_flow", fake_auth_flow)

    code = entry.main(["auth", "youtube"])
    captured = capsys.readouterr()

    assert code == 2
    assert "YOUTUBE_CLIENT_ID" in captured.err
    assert "yt-secret-xyz" not in captured.err + captured.out



def test_asterisco_libera_qualquer_usuario():
    assert publicacao.user_allowed("999", ["*"])
    assert publicacao.user_allowed("999", ["111"]) is False
    assert publicacao.user_allowed("999", []) is False


def test_buscar_slash_e_texto_resultado_vazio_curto_permissao_canal_e_limite_1900(tmp_path: Path):
    from clipes.registro import Registro

    reg = Registro(tmp_path)
    for i in range(20):
        reg.upsert({
            "clip_id": f"gta_c{i:02d}",
            "titulo": f"Trailer Épico GTA Parte {i} " + ("x" * 40),
            "hashtags": ["#gta6"],
            "legenda": "Momento insano",
            "canal": "gaules",
            "criado_em": T0 + i * 86400,
            "arquivo": f"p/gta_c{i:02d}.mp4",
            "tamanho": 100,
            "sha256": f"sha_{i}",
        })
        reg.set_discord(f"gta_c{i:02d}", {
            "thread_id": "555666777888999000",
            "message_id": f"888000{i:02d}",
            "attachment_id": f"att_{i}",
            "filename": f"gta_c{i:02d}.mp4",
            "size": 100,
        })
        reg.marcar(f"gta_c{i:02d}", status="no_discord", ack_em=T0 + i * 86400)

    store = LocalStore(tmp_path)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )
    buscar_cmd = bot.tree.get_command("buscar")
    assert buscar_cmd is not None

    # 1. Happy path via /buscar -> <= 1900 chars, newest first, with link & status & date
    i_ok = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(CHANNEL_ID))
    i_ok.guild_id = GUILD_ID
    asyncio.run(buscar_cmd.callback(i_ok, "gta trailer"))
    resp_text, eph = i_ok.response.messages[0]
    assert eph is False
    assert len(resp_text) <= 1900
    assert "gta_c19" in resp_text
    assert f"https://discord.com/channels/{GUILD_ID}/555666777888999000/88800019" in resp_text
    assert "no_discord" in resp_text

    # 2. Text command `buscar <texto>` using message.guild.id
    class _FakeGuild:
        id = GUILD_ID

    m_ok = _FakeMessage("buscar gta trailer", author_id=ALLOWED_USER, channel=_FakeChannel(CHANNEL_ID))
    m_ok.guild = _FakeGuild()
    asyncio.run(bot.on_message(m_ok))
    assert len(m_ok.replies) == 1
    assert "gta_c19" in m_ok.replies[0]
    assert f"https://discord.com/channels/{GUILD_ID}/" in m_ok.replies[0]

    # 3. Empty search result
    i_empty = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(buscar_cmd.callback(i_empty, "naoexiste"))
    assert i_empty.response.messages[0][0] == 'nenhum clipe para "naoexiste"'

    # 4. Short query (< 2 chars) via slash and message `buscar a`
    i_short = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(buscar_cmd.callback(i_short, "x"))
    assert i_short.response.messages[0][0] == "busca precisa de 2+ caracteres"

    m_short = _FakeMessage("buscar x", author_id=ALLOWED_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(bot.on_message(m_short))
    assert m_short.replies == ["busca precisa de 2+ caracteres"]

    # 5. Unauthorized user
    i_unauth = _FakeInteraction(user_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(buscar_cmd.callback(i_unauth, "gta"))
    assert i_unauth.response.messages == [("sem permissao", True)]

    m_unauth = _FakeMessage("buscar gta", author_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(bot.on_message(m_unauth))
    assert m_unauth.replies == ["sem permissao"]

    # 6. Wrong channel
    i_wrong = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(OTHER_CHANNEL_ID))
    asyncio.run(buscar_cmd.callback(i_wrong, "gta"))
    assert i_wrong.response.messages == [("sem permissao", True)]

    m_wrong = _FakeMessage("buscar gta", author_id=ALLOWED_USER, channel=_FakeChannel(OTHER_CHANNEL_ID))
    asyncio.run(bot.on_message(m_wrong))
    assert m_wrong.replies == []


def test_on_interaction_aprovar_botao_permitido_negado_desconhecido_e_inexistente(tmp_path: Path):
    _make_clip(tmp_path, video_id="btn1", clip_id="btn1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    tokens_path = _write_tokens(
        tmp_path / "tokens.json",
        {"youtube": {"refresh_token": "yt-refresh"}, "tiktok": {"refresh_token": "tt-refresh"}},
    )
    db, store = _mongo_store("w-pc1", "pc-mesa", T0)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
    )

    class _FakeComponentMsg:
        def __init__(self, content: str):
            self.content = content
            self.edits: list[dict[str, Any]] = []

        async def edit(self, *, content: str | None = None, view: Any = "UNSET") -> None:
            self.edits.append({"content": content, "view": view})
            if content is not None:
                self.content = content

    class _FakeComponentInteraction:
        def __init__(
            self,
            *,
            custom_id: str,
            user_id: int | str = ALLOWED_USER,
            user_name: str = "Romulo",
            channel: _FakeChannel | None = None,
            itype: Any = discord.InteractionType.component,
            msg: _FakeComponentMsg | None = None,
        ):
            self.type = itype
            self.data = {"custom_id": custom_id, "component_type": 2}
            self.user = _FakeUser(user_id, name=user_name)
            self.channel = channel or _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
            self.channel_id = self.channel.id
            self.message = msg
            self.response = _FakeInteractionResponse()
            self.followup = self.response.followup

    # 1. Unknown prefix or non-component type -> ignored
    i_other_prefix = _FakeComponentInteraction(custom_id="outro:btn1_c01", msg=_FakeComponentMsg("post"))
    i_app_cmd = _FakeComponentInteraction(
        custom_id="aprovar:btn1_c01",
        itype=discord.InteractionType.application_command,
        msg=_FakeComponentMsg("post"),
    )
    asyncio.run(bot.on_interaction(i_other_prefix))
    asyncio.run(bot.on_interaction(i_app_cmd))
    assert i_other_prefix.response.messages == []
    assert i_app_cmd.response.messages == []
    assert db.publish_requests.count_documents({}) == 0

    # 2. Unauthorized user -> ephemeral, no request, message not edited
    msg_unauth = _FakeComponentMsg("Clipe bom")
    i_unauth = _FakeComponentInteraction(custom_id="aprovar:btn1_c01", user_id=OTHER_USER, msg=msg_unauth)
    asyncio.run(bot.on_interaction(i_unauth))
    assert i_unauth.response.messages == [("sem permissao", True)]
    assert msg_unauth.edits == []
    assert db.publish_requests.count_documents({}) == 0

    # 3. Wrong channel -> ephemeral, no request
    msg_wrong = _FakeComponentMsg("Clipe bom")
    i_wrong = _FakeComponentInteraction(
        custom_id="aprovar:btn1_c01",
        channel=_FakeChannel(OTHER_CHANNEL_ID),
        msg=msg_wrong,
    )
    asyncio.run(bot.on_interaction(i_wrong))
    assert i_wrong.response.messages == [("sem permissao", True)]
    assert msg_wrong.edits == []
    assert db.publish_requests.count_documents({}) == 0

    # 4. Nonexistent clip -> returns request message, does not edit button
    msg_nf = _FakeComponentMsg("Clipe antigo")
    i_nf = _FakeComponentInteraction(custom_id="aprovar:fantasma_c99", msg=msg_nf)
    asyncio.run(bot.on_interaction(i_nf))
    assert i_nf.response.messages == [("clipe fantasma_c99 nao encontrado", False)]
    assert msg_nf.edits == []

    # 5. Legacy `aprovar:` button works as Ambos after simulated restart
    bot_restarted = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
    )
    msg_ok = _FakeComponentMsg("**Titulo btn1_c01** (`btn1_c01`)")
    i_ok = _FakeComponentInteraction(custom_id="aprovar:btn1_c01", user_id=ALLOWED_USER, user_name="Romulo", msg=msg_ok)
    asyncio.run(bot_restarted.on_interaction(i_ok))
    assert len(i_ok.response.messages) == 1
    assert "youtube: publicacao enfileirada" in i_ok.response.messages[0][0]
    assert "tiktok: publicacao enfileirada" in i_ok.response.messages[0][0]
    assert i_ok.response.messages[0][1] is False
    assert db.publish_requests.find_one({"_id": "btn1_c01:youtube"})["status"] == "pending"
    assert db.publish_requests.find_one({"_id": "btn1_c01:tiktok"})["status"] == "pending"
    assert len(msg_ok.edits) == 1
    assert msg_ok.edits[0]["view"] is None
    assert "YouTube por Romulo" in msg_ok.edits[0]["content"]
    assert "TikTok por Romulo" in msg_ok.edits[0]["content"]


def test_on_interaction_publicar_por_destino_yt_tt_ambos_e_sequencia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_clip(tmp_path, video_id="yt1", clip_id="yt1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    _make_clip(tmp_path, video_id="tt1", clip_id="tt1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    _make_clip(tmp_path, video_id="amb1", clip_id="amb1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    _make_clip(tmp_path, video_id="seq1", clip_id="seq1_c01", owner="w-off", thread_id=str(THREAD_ID))
    tokens_path = _write_tokens(
        tmp_path / "tokens.json",
        {"youtube": {"refresh_token": "yt-refresh"}, "tiktok": {"refresh_token": "tt-refresh"}},
    )
    db, store = _mongo_store("w-pc1", "pc-mesa", T0)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
    )

    class _CompMsg:
        def __init__(self, content: str):
            self.content = content
            self.edits: list[dict[str, Any]] = []

        async def edit(self, *, content: str | None = None, view: Any = "UNSET") -> None:
            self.edits.append({"content": content, "view": view})
            if content is not None:
                self.content = content

    class _CompInteraction:
        def __init__(self, custom_id: str, msg: _CompMsg, user_name: str = "Romulo"):
            self.type = discord.InteractionType.component
            self.data = {"custom_id": custom_id, "component_type": 2}
            self.user = _FakeUser(ALLOWED_USER, name=user_name)
            self.channel = _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
            self.channel_id = THREAD_ID
            self.message = msg
            self.response = _FakeInteractionResponse()
            self.followup = self.response.followup

    recorded_platforms: list[tuple[str, tuple[str, ...]]] = []
    real_request = publicacao.request

    def spy_request(*args: Any, **kwargs: Any) -> str:
        recorded_platforms.append((args[1], kwargs.get("platforms", publicacao.PLATFORMS)))
        return real_request(*args, **kwargs)

    monkeypatch.setattr(publicacao, "request", spy_request)

    # 1. YouTube only -> calls request with ("youtube",), keeps buttons
    msg_yt = _CompMsg("**Titulo yt1_c01**")
    i_yt = _CompInteraction("publicar:youtube:yt1_c01", msg_yt)
    asyncio.run(bot.on_interaction(i_yt))
    assert recorded_platforms[-1] == ("yt1_c01", ("youtube",))
    assert "youtube: publicacao enfileirada" in i_yt.response.messages[0][0]
    assert "tiktok" not in i_yt.response.messages[0][0]
    assert db.publish_requests.find_one({"_id": "yt1_c01:youtube"})["status"] == "pending"
    assert db.publish_requests.find_one({"_id": "yt1_c01:tiktok"}) is None
    assert msg_yt.edits[-1]["view"] == "UNSET"
    assert msg_yt.content == "**Titulo yt1_c01**\nYouTube por Romulo (agendado)"

    # 2. TikTok only -> calls request with ("tiktok",), keeps buttons
    msg_tt = _CompMsg("**Titulo tt1_c01**")
    i_tt = _CompInteraction("publicar:tiktok:tt1_c01", msg_tt)
    asyncio.run(bot.on_interaction(i_tt))
    assert recorded_platforms[-1] == ("tt1_c01", ("tiktok",))
    assert "tiktok: publicacao enfileirada" in i_tt.response.messages[0][0]
    assert "youtube" not in i_tt.response.messages[0][0]
    assert db.publish_requests.find_one({"_id": "tt1_c01:tiktok"})["status"] == "pending"
    assert db.publish_requests.find_one({"_id": "tt1_c01:youtube"}) is None
    assert msg_tt.edits[-1]["view"] == "UNSET"
    assert msg_tt.content == "**Titulo tt1_c01**\nTikTok por Romulo (agendado)"

    # 3. Ambos -> calls request with PLATFORMS, adds both lines, removes buttons
    msg_amb = _CompMsg("**Titulo amb1_c01**")
    i_amb = _CompInteraction("publicar:ambos:amb1_c01", msg_amb)
    asyncio.run(bot.on_interaction(i_amb))
    assert recorded_platforms[-1] == ("amb1_c01", publicacao.PLATFORMS)
    assert "youtube: publicacao enfileirada" in i_amb.response.messages[0][0]
    assert "tiktok: publicacao enfileirada" in i_amb.response.messages[0][0]
    assert db.publish_requests.find_one({"_id": "amb1_c01:youtube"})["status"] == "pending"
    assert db.publish_requests.find_one({"_id": "amb1_c01:tiktok"})["status"] == "pending"
    assert msg_amb.edits[-1]["view"] is None
    assert msg_amb.content == "**Titulo amb1_c01**\nYouTube por Romulo (agendado)\nTikTok por Romulo (agendado)"

    # 4. Sequential clicks (even with offline worker -> "pedido fica na fila"):
    #    1st click keeps buttons; 2nd click removes buttons
    msg_seq = _CompMsg("**Titulo seq1_c01**")
    i_seq_tt = _CompInteraction("publicar:tiktok:seq1_c01", msg_seq, user_name="Romulo")
    asyncio.run(bot.on_interaction(i_seq_tt))
    assert "pedido fica na fila" in i_seq_tt.response.messages[0][0]
    assert msg_seq.edits[-1]["view"] == "UNSET"
    assert msg_seq.content == "**Titulo seq1_c01**\nTikTok por Romulo (agendado)"

    i_seq_yt = _CompInteraction("publicar:youtube:seq1_c01", msg_seq, user_name="Maria")
    asyncio.run(bot.on_interaction(i_seq_yt))
    assert "pedido fica na fila" in i_seq_yt.response.messages[0][0]
    assert msg_seq.edits[-1]["view"] is None
    assert msg_seq.content == "**Titulo seq1_c01**\nTikTok por Romulo (agendado)\nYouTube por Maria (agendado)"


def test_on_interaction_publicar_destino_negado_desligada_e_idempotente(tmp_path: Path):
    _make_clip(tmp_path, video_id="desl1", clip_id="desl1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    _make_clip(tmp_path, video_id="dup1", clip_id="dup1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    _make_clip(tmp_path, video_id="pub1", clip_id="pub1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    # Only YouTube token configured -> TikTok is desligada
    tokens_path = _write_tokens(tmp_path / "tokens.json", {"youtube": {"refresh_token": "yt-refresh"}})
    db, store = _mongo_store("w-pc1", "pc-mesa", T0)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
    )

    class _CompMsg:
        def __init__(self, content: str):
            self.content = content
            self.edits: list[dict[str, Any]] = []

        async def edit(self, *, content: str | None = None, view: Any = "UNSET") -> None:
            self.edits.append({"content": content, "view": view})
            if content is not None:
                self.content = content

    class _CompInteraction:
        def __init__(
            self,
            custom_id: str,
            msg: _CompMsg,
            *,
            user_id: int | str = ALLOWED_USER,
            channel: _FakeChannel | None = None,
        ):
            self.type = discord.InteractionType.component
            self.data = {"custom_id": custom_id, "component_type": 2}
            self.user = _FakeUser(user_id, name="Romulo")
            self.channel = channel or _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
            self.channel_id = self.channel.id
            self.message = msg
            self.response = _FakeInteractionResponse()
            self.followup = self.response.followup

    # 1. Channel / user denied on publicar:<dest>:
    for cid in ("publicar:youtube:desl1_c01", "publicar:tiktok:desl1_c01", "publicar:ambos:desl1_c01"):
        m_bad_ch = _CompMsg("post")
        i_bad_ch = _CompInteraction(cid, m_bad_ch, channel=_FakeChannel(OTHER_CHANNEL_ID))
        asyncio.run(bot.on_interaction(i_bad_ch))
        assert i_bad_ch.response.messages == [("sem permissao", True)]
        assert m_bad_ch.edits == []

        m_bad_u = _CompMsg("post")
        i_bad_u = _CompInteraction(cid, m_bad_u, user_id=OTHER_USER)
        asyncio.run(bot.on_interaction(i_bad_u))
        assert i_bad_u.response.messages == [("sem permissao", True)]
        assert m_bad_u.edits == []

    # Invalid destination ignored
    m_invalid = _CompMsg("post")
    i_invalid = _CompInteraction("publicar:instagram:desl1_c01", m_invalid)
    asyncio.run(bot.on_interaction(i_invalid))
    assert i_invalid.response.messages == []
    assert db.publish_requests.count_documents({}) == 0

    # 2. TikTok desligada: clicking TikTok does not mark or edit
    msg_desl = _CompMsg("**Titulo desl1_c01**")
    i_tt_off = _CompInteraction("publicar:tiktok:desl1_c01", msg_desl)
    asyncio.run(bot.on_interaction(i_tt_off))
    assert i_tt_off.response.messages == [("tiktok: desligada", False)]
    assert msg_desl.edits == []
    assert db.publish_requests.count_documents({}) == 0

    # 3. TikTok desligada: clicking Ambos marks ONLY YouTube and keeps buttons
    i_amb_off = _CompInteraction("publicar:ambos:desl1_c01", msg_desl)
    asyncio.run(bot.on_interaction(i_amb_off))
    resp_text = i_amb_off.response.messages[0][0]
    assert "youtube: publicacao enfileirada" in resp_text
    assert "tiktok: desligada" in resp_text
    assert db.publish_requests.find_one({"_id": "desl1_c01:youtube"})["status"] == "pending"
    assert db.publish_requests.find_one({"_id": "desl1_c01:tiktok"}) is None
    assert len(msg_desl.edits) == 1
    assert msg_desl.edits[0]["view"] == "UNSET"
    assert msg_desl.content == "**Titulo desl1_c01**\nYouTube por Romulo (agendado)"
    assert "TikTok por" not in msg_desl.content

    # 4. Double-click idempotency: clicking YouTube twice does not duplicate line or DB doc
    msg_dup = _CompMsg("**Titulo dup1_c01**")
    asyncio.run(bot.on_interaction(_CompInteraction("publicar:youtube:dup1_c01", msg_dup)))
    i_dup2 = _CompInteraction("publicar:youtube:dup1_c01", msg_dup)
    asyncio.run(bot.on_interaction(i_dup2))
    assert "youtube: publicacao enfileirada" in i_dup2.response.messages[0][0]
    assert db.publish_requests.count_documents({"clip_id": "dup1_c01"}) == 1
    assert msg_dup.content.count("YouTube por") == 1
    assert len(msg_dup.edits) == 1

    # 5. Already published ("ja publicado"): replies without duplicating or editing
    db.publish_requests.insert_one({
        "_id": "pub1_c01:youtube",
        "clip_id": "pub1_c01",
        "plataforma": "youtube",
        "status": "ok",
        "resultado": {"url": "https://youtube.com/shorts/xyz"},
    })
    msg_pub = _CompMsg("**Titulo pub1_c01**")
    i_pub = _CompInteraction("publicar:youtube:pub1_c01", msg_pub)
    asyncio.run(bot.on_interaction(i_pub))
    assert "youtube: ja publicado (https://youtube.com/shorts/xyz)" in i_pub.response.messages[0][0]
    assert msg_pub.edits == []
    assert db.publish_requests.count_documents({"clip_id": "pub1_c01"}) == 1


def test_on_interaction_publicar_destino_mantem_agenda_e_notifica_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from zoneinfo import ZoneInfo

    sp = ZoneInfo("America/Sao_Paulo")
    t_now = datetime(2026, 11, 18, 10, 0, tzinfo=sp).timestamp()
    monkeypatch.setenv("CLIPES_AGENDA_HORARIOS", "11:30,19:00")
    monkeypatch.setenv("CLIPES_TZ", "America/Sao_Paulo")

    _make_clip(tmp_path, video_id="agp1", clip_id="agp1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    tokens_path = _write_tokens(
        tmp_path / "tokens.json",
        {"youtube": {"refresh_token": "yt-refresh"}, "tiktok": {"refresh_token": "tt-refresh"}},
    )
    db, store = _mongo_store("w-pc1", "pc-mesa", t_now)
    thread_ch = _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=t_now,
    )
    bot.get_channel = lambda cid: thread_ch if int(cid) == THREAD_ID else None  # type: ignore[method-assign]

    class _Msg:
        def __init__(self, content: str):
            self.content = content

        async def edit(self, *, content: str | None = None, view: Any = "UNSET") -> None:
            if content is not None:
                self.content = content

    class _MainChannelInteraction:
        def __init__(self, custom_id: str, msg: _Msg):
            self.type = discord.InteractionType.component
            self.data = {"custom_id": custom_id, "component_type": 2}
            self.user = _FakeUser(ALLOWED_USER, name="Romulo")
            self.channel = _FakeChannel(CHANNEL_ID)
            self.channel_id = CHANNEL_ID
            self.message = msg
            self.response = _FakeInteractionResponse()
            self.followup = self.response.followup

    i_yt = _MainChannelInteraction("publicar:youtube:agp1_c01", _Msg("**Titulo agp1_c01**"))
    asyncio.run(bot.on_interaction(i_yt))
    assert "agendado para 2026-11-18 11:30 (America/Sao_Paulo)" in i_yt.response.messages[0][0]
    doc = db.publish_requests.find_one({"_id": "agp1_c01:youtube"})
    assert doc is not None and doc["agendar_para"].astimezone(sp).hour == 11
    assert len(thread_ch.sent) == 1
    assert "youtube: publicacao enfileirada" in thread_ch.sent[0]


def test_bot_publicar_com_agenda_e_sufixo_agora(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from zoneinfo import ZoneInfo

    sp = ZoneInfo("America/Sao_Paulo")
    t_now = datetime(2026, 11, 18, 10, 0, tzinfo=sp).timestamp()
    monkeypatch.setenv("CLIPES_AGENDA_HORARIOS", "11:30,19:00")
    monkeypatch.setenv("CLIPES_TZ", "America/Sao_Paulo")

    _make_clip(tmp_path, video_id="ag1", clip_id="ag1_c01", owner="w-pc1", thread_id=None)
    _make_clip(tmp_path, video_id="ag2", clip_id="ag2_c01", owner="w-pc1", thread_id=None)
    _make_clip(tmp_path, video_id="ag3", clip_id="ag3_c01", owner="w-pc1", thread_id=None)
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    db, store = _mongo_store("w-pc1", "pc-mesa", t_now)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=t_now,
    )

    # 1. Text `publicar ag1_c01` -> agendado para 11:30
    msg_ag = _FakeMessage("publicar ag1_c01", channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(bot.on_message(msg_ag))
    assert len(msg_ag.replies) == 1
    assert "agendado para 2026-11-18 11:30 (America/Sao_Paulo)" in msg_ag.replies[0]
    doc_ag = db.publish_requests.find_one({"_id": "ag1_c01:youtube"})
    assert doc_ag is not None and doc_ag.get("agendar_para") is not None

    # 2. Text `publicar ag2_c01 agora` -> sem agendar_para
    msg_now = _FakeMessage("publicar ag2_c01 agora", channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(bot.on_message(msg_now))
    assert len(msg_now.replies) == 1
    assert "youtube: publicacao enfileirada" in msg_now.replies[0]
    assert "agendado para" not in msg_now.replies[0]
    doc_now = db.publish_requests.find_one({"_id": "ag2_c01:youtube"})
    assert doc_now is not None and "agendar_para" not in doc_now

    # 3. Slash `/publicar id:ag3_c01 agora:True` -> sem agendar_para
    pub_cmd = bot.tree.get_command("publicar")
    assert pub_cmd is not None
    inter_now = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(pub_cmd.callback(inter_now, "ag3_c01", agora=True))
    assert "agendado para" not in inter_now.response.messages[0][0]
    doc_slash = db.publish_requests.find_one({"_id": "ag3_c01:youtube"})
    assert doc_slash is not None and "agendar_para" not in doc_slash


def test_bot_respostas_usam_allowed_mentions_none_e_buscar_usa_to_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from clipes.registro import Registro

    _make_clip(tmp_path, video_id="sec1", clip_id="sec1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    reg = Registro(tmp_path)
    reg.upsert({"clip_id": "sec1_c01", "titulo": "Clip Sec", "canal": "gaules", "criado_em": T0})

    _, store = _mongo_store("w-pc1", "pc-mesa", T0)
    thread_ch = _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=["*"],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=T0,
    )
    bot.get_channel = lambda cid: thread_ch if int(cid) == THREAD_ID else None  # type: ignore[method-assign]

    def _assert_none_mentions(am: Any) -> None:
        assert isinstance(am, discord.AllowedMentions)
        assert am.everyone is False
        assert am.users is False
        assert am.roles is False
        assert am.replied_user is False

    to_thread_calls: list[Any] = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)

    # 1. Text `buscar @everyone <@123>` with allowed_ids=["*"]
    m_search = _FakeMessage("buscar @everyone <@123>", author_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(bot.on_message(m_search))
    assert "@everyone" in m_search.replies[0]
    _assert_none_mentions(m_search.reply_kwargs[0].get("allowed_mentions"))

    # 2. Slash `/buscar @here`
    buscar_cmd = bot.tree.get_command("buscar")
    assert buscar_cmd is not None
    i_search = _FakeInteraction(user_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(buscar_cmd.callback(i_search, "@here"))
    _assert_none_mentions(i_search.response.kwargs_list[0].get("allowed_mentions"))
    assert to_thread_calls == [bot_mod._format_search_results, bot_mod._format_search_results]

    # 3. Text `publicar` & Slash `/publicar` & `/listar`
    m_pub = _FakeMessage("publicar sec1_c01", author_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(bot.on_message(m_pub))
    _assert_none_mentions(m_pub.reply_kwargs[0].get("allowed_mentions"))
    _assert_none_mentions(thread_ch.sent_kwargs[0].get("allowed_mentions"))

    pub_cmd = bot.tree.get_command("publicar")
    list_cmd = bot.tree.get_command("listar")
    assert pub_cmd is not None and list_cmd is not None
    i_pub = _FakeInteraction(user_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))
    i_list = _FakeInteraction(user_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))
    asyncio.run(pub_cmd.callback(i_pub, "sec1_c01"))
    asyncio.run(list_cmd.callback(i_list))
    _assert_none_mentions(i_pub.response.kwargs_list[0].get("allowed_mentions"))
    _assert_none_mentions(i_list.response.kwargs_list[0].get("allowed_mentions"))

    # 4. Button `aprovar:` (with edit accepting allowed_mentions)
    class _EditMsg:
        def __init__(self, content: str):
            self.content = content
            self.edits: list[dict[str, Any]] = []

        async def edit(self, *, content: str | None = None, view: Any = "UNSET", allowed_mentions: Any = None) -> None:
            self.edits.append({"content": content, "view": view, "allowed_mentions": allowed_mentions})

    class _BtnInteraction:
        def __init__(self, custom_id: str, msg: _EditMsg):
            self.type = discord.InteractionType.component
            self.data = {"custom_id": custom_id, "component_type": 2}
            self.user = _FakeUser(OTHER_USER, name="@everyone")
            self.channel = _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
            self.channel_id = THREAD_ID
            self.message = msg
            self.response = _FakeInteractionResponse()
            self.followup = self.response.followup

    edit_msg = _EditMsg("post")
    i_btn = _BtnInteraction("aprovar:sec1_c01", edit_msg)
    asyncio.run(bot.on_interaction(i_btn))
    _assert_none_mentions(i_btn.response.kwargs_list[0].get("allowed_mentions"))
    assert len(edit_msg.edits) == 1
    _assert_none_mentions(edit_msg.edits[0].get("allowed_mentions"))


class _ToggleMsg:
    def __init__(self, content: str, components: list[Any]):
        self.content = content
        self.components = components
        self.edits: list[dict[str, Any]] = []

    async def edit(self, *, content: str | None = None, view: Any = "UNSET") -> None:
        self.edits.append({"content": content, "view": view})
        if content is not None:
            self.content = content
        if view is None:
            self.components = []


class _ToggleResponse(_FakeInteractionResponse):
    def __init__(self, msg: _ToggleMsg | None) -> None:
        super().__init__()
        self._msg = msg
        self.edited: list[dict[str, Any]] = []

    async def edit_message(self, *, view: Any = None, **kwargs: Any) -> None:
        self.edited.append({"view": view, **kwargs})
        if self._msg is not None and view is not None:
            self._msg.components = [_component_factory(c) for c in view.to_components()]


class _ToggleInteraction:
    def __init__(
        self,
        custom_id: str,
        msg: _ToggleMsg | None,
        *,
        user_id: int | str = ALLOWED_USER,
        user_name: str = "Romulo",
        channel: _FakeChannel | None = None,
    ):
        self.type = discord.InteractionType.component
        self.data = {"custom_id": custom_id, "component_type": 2}
        self.user = _FakeUser(user_id, name=user_name)
        self.channel = channel or _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
        self.channel_id = self.channel.id
        self.message = msg
        self.response = _ToggleResponse(msg)
        self.followup = self.response.followup


def test_on_interaction_toggle_agendar_on_off_destinos_modos_e_legado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from zoneinfo import ZoneInfo
    from discord.components import _component_factory
    from clipes.notificar import _approve_components

    sp = ZoneInfo("America/Sao_Paulo")
    t_now = datetime(2026, 11, 18, 10, 0, tzinfo=sp).timestamp()
    monkeypatch.setenv("CLIPES_AGENDA_HORARIOS", "11:30,19:00")
    monkeypatch.setenv("CLIPES_TZ", "America/Sao_Paulo")

    _make_clip(tmp_path, video_id="tg1", clip_id="tg1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    _make_clip(tmp_path, video_id="tg2", clip_id="tg2_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    _make_clip(tmp_path, video_id="leg1", clip_id="leg1_c01", owner="w-pc1", thread_id=str(THREAD_ID))
    tokens_path = _write_tokens(
        tmp_path / "tokens.json",
        {"youtube": {"refresh_token": "yt-refresh"}, "tiktok": {"refresh_token": "tt-refresh"}},
    )
    db, store = _mongo_store("w-pc1", "pc-mesa", t_now)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        tokens_path=tokens_path,
        now=t_now,
    )

    recorded_agendar: list[tuple[str, bool, tuple[str, ...]]] = []
    real_request = publicacao.request

    def spy_request(*args: Any, **kwargs: Any) -> str:
        recorded_agendar.append((args[1], bool(kwargs.get("agendar", True)), kwargs.get("platforms", publicacao.PLATFORMS)))
        return real_request(*args, **kwargs)

    monkeypatch.setattr(publicacao, "request", spy_request)

    # 1. Negados e custom_ids invalidos no toggle
    msg_neg = _ToggleMsg("**Titulo tg1_c01**", [_component_factory(c) for c in _approve_components("tg1_c01")])
    i_wrong_ch = _ToggleInteraction("agendar:off:tg1_c01", msg_neg, channel=_FakeChannel(OTHER_CHANNEL_ID))
    i_wrong_user = _ToggleInteraction("agendar:off:tg1_c01", msg_neg, user_id=OTHER_USER)
    i_bad_state = _ToggleInteraction("agendar:talvez:tg1_c01", msg_neg)
    i_empty_id = _ToggleInteraction("agendar:on:", msg_neg)
    asyncio.run(bot.on_interaction(i_wrong_ch))
    asyncio.run(bot.on_interaction(i_wrong_user))
    asyncio.run(bot.on_interaction(i_bad_state))
    asyncio.run(bot.on_interaction(i_empty_id))
    assert i_wrong_ch.response.messages == [("sem permissao", True)]
    assert i_wrong_ch.response.edited == []
    assert i_wrong_user.response.messages == [("sem permissao", True)]
    assert i_wrong_user.response.edited == []
    assert i_bad_state.response.messages == [] and i_bad_state.response.edited == []
    assert i_empty_id.response.messages == [] and i_empty_id.response.edited == []
    assert msg_neg.content == "**Titulo tg1_c01**"

    # 2. Toggle off -> on -> off via edit_message mantendo linha 1 e conteudo
    msg_tg = _ToggleMsg("**Titulo tg1_c01**", [_component_factory(c) for c in _approve_components("tg1_c01")])
    i_on = _ToggleInteraction("agendar:off:tg1_c01", msg_tg)
    asyncio.run(bot.on_interaction(i_on))
    assert i_on.response.messages == []
    assert len(i_on.response.edited) == 1
    comps_on = i_on.response.edited[0]["view"].to_components()
    assert [b["custom_id"] for b in comps_on[0]["components"]] == [
        "publicar:youtube:tg1_c01",
        "publicar:tiktok:tg1_c01",
        "publicar:ambos:tg1_c01",
    ]
    # Row 1 now has Agendar toggle + Descartar button
    assert len(comps_on[1]["components"]) == 2
    assert comps_on[1]["components"][0] == {
        "type": 2,
        "style": 1,
        "disabled": False,
        "label": "Agendar: sim",
        "custom_id": "agendar:on:tg1_c01",
    }
    assert comps_on[1]["components"][1] == {
        "type": 2,
        "style": 4,
        "disabled": False,
        "label": "Descartar",
        "custom_id": "descartar:tg1_c01",
    }
    assert msg_tg.content == "**Titulo tg1_c01**"

    i_off = _ToggleInteraction("agendar:on:tg1_c01", msg_tg)
    asyncio.run(bot.on_interaction(i_off))
    assert i_off.response.messages == []
    comps_off = i_off.response.edited[0]["view"].to_components()
    assert len(comps_off[1]["components"]) == 2
    assert comps_off[1]["components"][0] == {
        "type": 2,
        "style": 2,
        "disabled": False,
        "label": "Agendar: nao",
        "custom_id": "agendar:off:tg1_c01",
    }
    assert comps_off[1]["components"][1] == {
        "type": 2,
        "style": 4,
        "disabled": False,
        "label": "Descartar",
        "custom_id": "descartar:tg1_c01",
    }

    # 3. Clique YouTube com toggle off -> agendar=False (agora), preserva toggle
    i_yt_now = _ToggleInteraction("publicar:youtube:tg1_c01", msg_tg, user_name="Romulo")
    asyncio.run(bot.on_interaction(i_yt_now))
    assert recorded_agendar[-1] == ("tg1_c01", False, ("youtube",))
    assert "agendado para" not in i_yt_now.response.messages[0][0]
    doc_yt = db.publish_requests.find_one({"_id": "tg1_c01:youtube"})
    assert doc_yt is not None and "agendar_para" not in doc_yt
    assert msg_tg.content == "**Titulo tg1_c01**\nYouTube por Romulo (agora)"
    assert msg_tg.edits[-1]["view"] == "UNSET"
    assert len(msg_tg.components) == 2

    # 4. Alterna para on e re-clica YouTube (prefixo "YouTube por " nao duplica), depois clica TikTok -> (agendado) e remove tudo
    asyncio.run(bot.on_interaction(_ToggleInteraction("agendar:off:tg1_c01", msg_tg)))
    i_yt_dup = _ToggleInteraction("publicar:youtube:tg1_c01", msg_tg, user_name="Maria")
    asyncio.run(bot.on_interaction(i_yt_dup))
    assert msg_tg.content.count("YouTube por ") == 1

    i_tt_sched = _ToggleInteraction("publicar:tiktok:tg1_c01", msg_tg, user_name="Maria")
    asyncio.run(bot.on_interaction(i_tt_sched))
    assert recorded_agendar[-1] == ("tg1_c01", True, ("tiktok",))
    assert "agendado para 2026-11-18 11:30 (America/Sao_Paulo)" in i_tt_sched.response.messages[0][0]
    doc_tt = db.publish_requests.find_one({"_id": "tg1_c01:tiktok"})
    assert doc_tt is not None and doc_tt.get("agendar_para") is not None
    assert msg_tg.content == "**Titulo tg1_c01**\nYouTube por Romulo (agora)\nTikTok por Maria (agendado)"
    assert msg_tg.edits[-1]["view"] is None
    assert msg_tg.components == []

    # 5. Toggle off com dicts crus (Ambos -> agora em ambas as plataformas e remove tudo)
    msg_dict = _ToggleMsg("**Titulo tg2_c01**", _approve_components("tg2_c01"))
    i_amb_now = _ToggleInteraction("publicar:ambos:tg2_c01", msg_dict, user_name="Romulo")
    asyncio.run(bot.on_interaction(i_amb_now))
    assert recorded_agendar[-1] == ("tg2_c01", False, publicacao.PLATFORMS)
    assert msg_dict.content == "**Titulo tg2_c01**\nYouTube por Romulo (agora)\nTikTok por Romulo (agora)"
    assert msg_dict.edits[-1]["view"] is None

    # 6. Mensagem legada sem toggle -> agendar=True (agendado)
    legacy_row = _component_factory({"type": 1, "components": [{"type": 2, "style": 3, "label": "Aprovar", "custom_id": "aprovar:leg1_c01"}]})
    msg_leg = _ToggleMsg("**Titulo leg1_c01**", [legacy_row])
    i_leg = _ToggleInteraction("aprovar:leg1_c01", msg_leg, user_name="Romulo")
    asyncio.run(bot.on_interaction(i_leg))
    assert recorded_agendar[-1] == ("leg1_c01", True, publicacao.PLATFORMS)
    assert "agendado para 2026-11-18 19:00 (America/Sao_Paulo)" in i_leg.response.messages[0][0]
    assert msg_leg.content == "**Titulo leg1_c01**\nYouTube por Romulo (agendado)\nTikTok por Romulo (agendado)"
    assert msg_leg.edits[-1]["view"] is None


# --- T10: _autorizado() centralizado e aviso para DISCORD_ALLOWED_USER_IDS="*" ---


def test_autorizado_centralizado_e_aviso_curinga(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    store = LocalStore(tmp_path)

    # Sem "*": nao imprime aviso
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    bot_mod.make_bot(store, allowed_ids=None, channel_id=CHANNEL_ID, guild_id=GUILD_ID, out_root=tmp_path)
    assert "aviso: comandos liberados para qualquer membro do canal" not in capsys.readouterr().out

    # Com "*" via env (como no main()): imprime aviso exato
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "*")
    bot_mod.make_bot(store, allowed_ids=None, channel_id=CHANNEL_ID, guild_id=GUILD_ID, out_root=tmp_path)
    assert "aviso: comandos liberados para qualquer membro do canal" in capsys.readouterr().out

    # Com "*" via allowed_ids explícito: imprime aviso exato
    monkeypatch.delenv("DISCORD_ALLOWED_USER_IDS", raising=False)
    bot_mod.make_bot(store, allowed_ids=["*"], channel_id=CHANNEL_ID, guild_id=GUILD_ID, out_root=tmp_path)
    assert "aviso: comandos liberados para qualquer membro do canal" in capsys.readouterr().out

    # _autorizado aprova canal configurado e thread filha, nega canal errado e usuario nao permitido
    i_ok = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(CHANNEL_ID))
    i_thr = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(THREAD_ID, parent_id=CHANNEL_ID))
    i_bad_ch = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(OTHER_CHANNEL_ID))
    i_bad_user = _FakeInteraction(user_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))

    assert asyncio.run(bot_mod._autorizado(i_ok, CHANNEL_ID, [ALLOWED_USER])) is True
    assert i_ok.response.messages == []
    assert asyncio.run(bot_mod._autorizado(i_thr, CHANNEL_ID, [ALLOWED_USER])) is True
    assert i_thr.response.messages == []
    assert asyncio.run(bot_mod._autorizado(i_bad_ch, CHANNEL_ID, [ALLOWED_USER])) is False
    assert i_bad_ch.response.messages == [("sem permissao", True)]
    assert asyncio.run(bot_mod._autorizado(i_bad_user, CHANNEL_ID, [ALLOWED_USER])) is False
    assert i_bad_user.response.messages == [("sem permissao", True)]


# --- T11: /live adicionar, remover e lista (CTRL-01) ---


def test_slash_live_adicionar_remover_lista_happy_pc_fixo_sem_vaga_e_duplicata(tmp_path: Path):
    hb = datetime.fromtimestamp(T0, timezone.utc).replace(tzinfo=None)
    db, store = _mongo_store("w-pc1", "PC-1", T0)
    db.workers.update_one(
        {"_id": "w-pc1"},
        {"$set": {
            "beat_s": 20.0,
            "slots": {"twitch": 1, "kick": 1, "youtube": 0, "busca": 1},
            "ocupados": {"twitch": 0, "kick": 0, "youtube": 0, "busca": 0},
        }},
    )
    db.workers.insert_one({
        "_id": "w-pc2",
        "alias": "PC-2",
        "heartbeat_at": hb,
        "beat_s": 20.0,
        "slots": {"twitch": 1, "kick": 0, "youtube": 1, "busca": 0},
        "ocupados": {"twitch": 1, "kick": 0, "youtube": 0, "busca": 0},
    })

    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )
    live_grp = bot.tree.get_command("live")
    assert live_grp is not None
    add_cmd = live_grp.get_command("adicionar")
    rem_cmd = live_grp.get_command("remover")
    lst_cmd = live_grp.get_command("lista")
    assert add_cmd is not None and rem_cmd is not None and lst_cmd is not None

    # 1. Lista vazia
    i_empty = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(lst_cmd.callback(i_empty))
    assert i_empty.response.messages == [("nenhum alvo ativo", False)]

    # 2. Adicionar Twitch com vaga disponivel (CTRL-01 AC 1)
    i_add = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(add_cmd.callback(i_add, "gaules"))
    assert i_add.response.messages == [("alvo adicionado: gaules", False)]
    doc = db.alvos.find_one({"_id": "gaules"})
    assert doc is not None
    assert (doc["tipo"], doc["plataforma"], doc["entrada"], doc["estado"], doc["pc_fixo"], doc["owner"], doc["lease_until"], doc["fase"]) == (
        "live", "twitch", "gaules", "ativo", None, None, None, "sem vaga",
    )
    assert doc["criado_por"] == ALLOWED_USER

    # 3. Duplicata ativa informa dono (CTRL-01 AC 10)
    db.alvos.update_one({"_id": "gaules"}, {"$set": {"owner": "w-pc1", "fase": "gravando"}})
    i_dup = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(add_cmd.callback(i_dup, "https://www.twitch.tv/gaules"))
    assert i_dup.response.messages == [("ja acompanhado por PC-1", False)]

    # 4. Adicionar Kick com pc_fixo sem vaga no PC escolhido -> indica "sem vaga" (CTRL-01 AC 3, 8)
    i_kick = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(add_cmd.callback(i_kick, "https://kick.com/xqc", pc="PC-2"))
    assert i_kick.response.messages == [("alvo adicionado: kick_xqc (sem vaga)", False)]
    doc_kick = db.alvos.find_one({"_id": "kick_xqc"})
    assert doc_kick is not None and doc_kick["plataforma"] == "kick" and doc_kick["pc_fixo"] == "PC-2"

    # 5. Adicionar YouTube com vaga em PC-2
    i_yt = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(add_cmd.callback(i_yt, "https://www.youtube.com/@caze/live"))
    assert i_yt.response.messages == [("alvo adicionado: yt_caze", False)]
    assert db.alvos.find_one({"_id": "yt_caze"})["plataforma"] == "youtube"

    # 6. /live lista mostra dono (ou sem PC) e fase (CTRL-01 AC 5)
    i_list = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(lst_cmd.callback(i_list))
    txt, eph = i_list.response.messages[0]
    assert eph is False
    assert "gaules" in txt and "PC-1" in txt and "gravando" in txt
    assert "kick_xqc" in txt and "sem PC" in txt and "sem vaga" in txt and "PC-2" in txt
    assert "yt_caze" in txt

    # 7. /live remover marca estado="removido" aceitando URL ou chave (CTRL-01 AC 4)
    i_rem = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(rem_cmd.callback(i_rem, "https://www.twitch.tv/gaules"))
    assert i_rem.response.messages == [("alvo removido: gaules", False)]
    assert db.alvos.find_one({"_id": "gaules"})["estado"] == "removido"

    # Re-adicionar apos removido funciona
    i_readd = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(add_cmd.callback(i_readd, "gaules"))
    assert i_readd.response.messages == [("alvo adicionado: gaules", False)]
    assert db.alvos.find_one({"_id": "gaules"})["estado"] == "ativo"


def test_slash_live_invalido_limite_30_sem_mongo_erro_mongo_e_autorizacao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    # 1. Sem Mongo -> controle requer CLIPES_MONGO_URI (CTRL-01 AC 11)
    bot_local = bot_mod.make_bot(
        LocalStore(tmp_path),
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )
    lg_local = bot_local.tree.get_command("live")
    assert lg_local is not None
    for sub, args in (("adicionar", ("gaules",)), ("remover", ("gaules",)), ("lista", ())):
        inter = _FakeInteraction(user_id=ALLOWED_USER)
        asyncio.run(lg_local.get_command(sub).callback(inter, *args))
        assert inter.response.messages == [("controle requer CLIPES_MONGO_URI", False)], sub

    # 2. Alvos invalidos -> "alvo invalido" sem gravar nada (CTRL-01 AC 9)
    db, store = _mongo_store("w-pc1", "PC-1", T0)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )
    lg = bot.tree.get_command("live")
    add_cmd = lg.get_command("adicionar")
    rem_cmd = lg.get_command("remover")
    lst_cmd = lg.get_command("lista")

    for bad in ("", "https://exemplo.com/live", "ftp://twitch.tv/gaules", "-flag", "canal com espaco"):
        inter = _FakeInteraction(user_id=ALLOWED_USER)
        asyncio.run(add_cmd.callback(inter, bad))
        assert inter.response.messages == [("alvo invalido", False)], bad
    assert db.alvos.count_documents({}) == 0

    # 3. Autorizacao negada (usuario e canal) antes de tocar no Mongo
    for sub, args in (("adicionar", ("gaules",)), ("remover", ("gaules",)), ("lista", ())):
        i_u = _FakeInteraction(user_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))
        i_c = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(OTHER_CHANNEL_ID))
        asyncio.run(lg.get_command(sub).callback(i_u, *args))
        asyncio.run(lg.get_command(sub).callback(i_c, *args))
        assert i_u.response.messages == [("sem permissao", True)], sub
        assert i_c.response.messages == [("sem permissao", True)], sub
    assert db.alvos.count_documents({}) == 0

    # 4. Limite de 30 alvos ativos
    for idx in range(30):
        db.alvos.insert_one({
            "_id": f"canal_{idx}",
            "tipo": "live",
            "plataforma": "twitch",
            "entrada": f"canal_{idx}",
            "estado": "ativo",
            "criado_em": datetime.fromtimestamp(T0 + idx, timezone.utc).replace(tzinfo=None),
        })
    i_cap = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(add_cmd.callback(i_cap, "canal_31"))
    assert "30" in i_cap.response.messages[0][0]
    assert db.alvos.find_one({"_id": "canal_31"}) is None

    # 5. Falha do Mongo responde "controle indisponivel (Mongo)" com segredo mascarado no log
    monkeypatch.setenv("CLIPES_MONGO_URI", "mongodb+srv://u:senhasecreta99@cluster.test/db")

    class _BrokenCol:
        def find_one(self, *a, **k):
            raise RuntimeError("falha em mongodb+srv://u:senhasecreta99@cluster.test/db")

        def find(self, *a, **k):
            raise RuntimeError("falha em mongodb+srv://u:senhasecreta99@cluster.test/db")

    broken_store = type("S", (), {"db": type("D", (), {"alvos": _BrokenCol(), "workers": _BrokenCol()})()})()
    bot_broken = bot_mod.make_bot(
        broken_store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )
    lg_b = bot_broken.tree.get_command("live")
    for sub, args in (("adicionar", ("gaules",)), ("remover", ("gaules",)), ("lista", ())):
        inter = _FakeInteraction(user_id=ALLOWED_USER)
        asyncio.run(lg_b.get_command(sub).callback(inter, *args))
        assert inter.response.messages == [("controle indisponivel (Mongo)", False)], sub
    out = capsys.readouterr().out
    assert "senhasecreta99" not in out


# --- T12: /clipar e /status (CTRL-02, CTRL-03) ---


def test_slash_clipar_valido_duplicata_teto_20_invalido_sem_mongo_e_auth(tmp_path: Path):
    import hashlib

    # 1. Sem Mongo -> controle requer CLIPES_MONGO_URI
    bot_local = bot_mod.make_bot(
        LocalStore(tmp_path),
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )
    clip_local = bot_local.tree.get_command("clipar")
    assert clip_local is not None
    i_no_mongo = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(clip_local.callback(i_no_mongo, "https://www.twitch.tv/videos/123"))
    assert i_no_mongo.response.messages == [("controle requer CLIPES_MONGO_URI", False)]

    # 2. Com Mongo: auth negada antes de tocar no banco
    db, store = _mongo_store("w-pc1", "PC-1", T0)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )
    clip_cmd = bot.tree.get_command("clipar")
    assert clip_cmd is not None

    i_u = _FakeInteraction(user_id=OTHER_USER, channel=_FakeChannel(CHANNEL_ID))
    i_c = _FakeInteraction(user_id=ALLOWED_USER, channel=_FakeChannel(OTHER_CHANNEL_ID))
    asyncio.run(clip_cmd.callback(i_u, "https://www.twitch.tv/videos/123"))
    asyncio.run(clip_cmd.callback(i_c, "https://www.twitch.tv/videos/123"))
    assert i_u.response.messages == [("sem permissao", True)]
    assert i_c.response.messages == [("sem permissao", True)]
    assert db.alvos.count_documents({}) == 0

    # 3. URLs invalidas -> "url invalida" sem gravar nada (CTRL-02 AC 5)
    for bad in (
        "",
        "twitch.tv/videos/123",
        "ftp://twitch.tv/videos/123",
        "https://exemplo.com/watch?v=1",
        "https://twitch.tv/",
        "https://user:pass@twitch.tv/videos/123",
        "https://youtu.be/abc def",
    ):
        inter = _FakeInteraction(user_id=ALLOWED_USER)
        asyncio.run(clip_cmd.callback(inter, bad))
        assert inter.response.messages == [("url invalida", False)], bad
    assert db.alvos.count_documents({}) == 0

    # 4. URLs validas de todas as 4 origens permitidas -> vod:<sha8(url)> e "na fila: posição N" (CTRL-02 AC 1)
    urls = [
        ("https://www.twitch.tv/videos/111", "twitch"),
        ("https://kick.com/xqc/videos/222", "kick"),
        ("https://www.youtube.com/watch?v=abc12345", "youtube"),
        ("https://youtu.be/xyz98765", "youtube"),
    ]
    for pos, (url, plat) in enumerate(urls, start=1):
        inter = _FakeInteraction(user_id=ALLOWED_USER)
        asyncio.run(clip_cmd.callback(inter, url))
        assert inter.response.messages == [(f"na fila: posição {pos}", False)]
        expected_id = f"vod:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]}"
        doc = db.alvos.find_one({"_id": expected_id})
        assert doc is not None
        assert (doc["tipo"], doc["plataforma"], doc["url"], doc["estado"]) == ("vod", plat, url, "ativo")

    # 5. Duplicata ativa -> "ja na fila" (CTRL-02 AC 3)
    i_dup = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(clip_cmd.callback(i_dup, urls[0][0]))
    assert i_dup.response.messages == [("ja na fila", False)]

    # 6. Teto de 20 VODs ativos -> "fila cheia (20)" (CTRL-02 AC 4)
    for idx in range(5, 21):
        inter = _FakeInteraction(user_id=ALLOWED_USER)
        asyncio.run(clip_cmd.callback(inter, f"https://youtu.be/vod{idx:04d}"))
        assert inter.response.messages == [(f"na fila: posição {idx}", False)]

    i_full = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(clip_cmd.callback(i_full, "https://youtu.be/vod_extra_21"))
    assert i_full.response.messages == [("fila cheia (20)", False)]
    assert db.alvos.count_documents({"tipo": "vod", "estado": "ativo"}) == 20


def test_slash_status_online_offline_slots_fila_metricas_limite_1900_e_sem_mongo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from zoneinfo import ZoneInfo

    monkeypatch.setenv("CLIPES_TZ", "America/Sao_Paulo")
    sp = ZoneInfo("America/Sao_Paulo")
    now_dt = datetime(2026, 11, 19, 14, 30, tzinfo=sp)
    now_ts = now_dt.timestamp()
    hb_on = now_dt.astimezone(timezone.utc).replace(tzinfo=None)
    hb_off = datetime(2026, 11, 19, 12, 15, tzinfo=sp).astimezone(timezone.utc).replace(tzinfo=None)

    # 1. Sem Mongo -> controle requer CLIPES_MONGO_URI
    bot_local = bot_mod.make_bot(
        LocalStore(tmp_path),
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=now_ts,
    )
    st_local = bot_local.tree.get_command("status")
    assert st_local is not None
    i_nm = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(st_local.callback(i_nm))
    assert i_nm.response.messages == [("controle requer CLIPES_MONGO_URI", False)]

    # 2. Com Mongo: online + offline (desde HH:MM), slots/ocupados, job_atual, fila, temp_c, disco_livre_gb
    client = mongomock.MongoClient()
    db = client.clipes_lives
    db.workers.insert_many([
        {
            "_id": "w-1",
            "alias": "PC-Mesa",
            "heartbeat_at": hb_on,
            "beat_s": 20.0,
            "slots": {"twitch": 1, "kick": 1, "youtube": 1, "busca": 1},
            "ocupados": {"twitch": 1, "kick": 0, "youtube": 1, "busca": 1},
            "job_atual": "twitch:gaules:101",
            "fila": {"janelas": 2, "busca": 1, "vods": 0},
            "capacity": {"temp_c": 74, "disco_livre_gb": 42.5},
        },
        {
            "_id": "w-2",
            "alias": "PC-Quarto",
            "heartbeat_at": hb_off,
            "beat_s": 20.0,
            "slots": {"twitch": 1, "kick": 0, "youtube": 1, "busca": 0},
            "ocupados": {"twitch": 0, "kick": 0, "youtube": 0, "busca": 0},
            "job_atual": None,
            "fila": {"janelas": 0, "busca": 0, "vods": 3},
            "capacity": {"temp_c": 61, "disco_livre_gb": 18.2},
        },
    ])

    bot = bot_mod.make_bot(
        MongoStore(db),
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=now_ts,
    )
    st_cmd = bot.tree.get_command("status")
    assert st_cmd is not None

    # Auth negada
    i_unauth = _FakeInteraction(user_id=OTHER_USER)
    asyncio.run(st_cmd.callback(i_unauth))
    assert i_unauth.response.messages == [("sem permissao", True)]

    # Happy path
    i_ok = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(st_cmd.callback(i_ok))
    text, eph = i_ok.response.messages[0]
    assert eph is False
    lines = text.splitlines()
    assert len(lines) == 2
    assert "PC-Mesa" in lines[0] and "(online)" in lines[0]
    assert "twitch:1/1" in lines[0] and "kick:0/1" in lines[0] and "youtube:1/1" in lines[0] and "busca:1/1" in lines[0]
    assert "twitch:gaules:101" in lines[0]
    assert "janelas=2" in lines[0] and "busca=1" in lines[0] and "vods=0" in lines[0]
    assert "74C" in lines[0] and "42.5GB" in lines[0]

    assert "PC-Quarto" in lines[1] and "offline (desde 12:15)" in lines[1]
    assert "ocioso" in lines[1] and "vods=3" in lines[1] and "61C" in lines[1] and "18.2GB" in lines[1]

    # 3. Respeita teto de 1900 chars com muitos workers
    for idx in range(40):
        db.workers.insert_one({
            "_id": f"w-extra-{idx:02d}",
            "alias": f"PC-Extra-{idx:02d}-" + ("X" * 20),
            "heartbeat_at": hb_on,
            "beat_s": 20.0,
            "slots": {"twitch": 1, "kick": 1, "youtube": 1, "busca": 1},
            "ocupados": {"twitch": 1, "kick": 1, "youtube": 1, "busca": 1},
            "job_atual": "job_longo_de_teste",
            "fila": {"janelas": 1, "busca": 2, "vods": 3},
            "capacity": {"temp_c": 70, "disco_livre_gb": 10.0},
        })
    i_many = _FakeInteraction(user_id=ALLOWED_USER)
    asyncio.run(st_cmd.callback(i_many))
    assert len(i_many.response.messages[0][0]) <= 1900


# --- T16: Botão Descartar e atualização de estado na mensagem do clipe ---


def test_on_interaction_descartar_marca_descartado_remove_botoes_e_atualiza_estado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """T16: Clicar Descartar -> _autorizado(), status=descartado no Registro/Mongo, linha 🗑️, botões removidos."""
    import mongomock
    from discord.components import _component_factory
    from clipes.notificar import _approve_components
    from clipes.registro import Registro

    db = mongomock.MongoClient().clipes_lives
    store = bot_mod.MongoStore(db)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )

    # Criar um clipe no registro local e no Mongo
    reg = Registro(tmp_path, mongo=db)
    clip_id = "tg_discard_c01"
    reg.upsert({
        "clip_id": clip_id,
        "titulo": "Titulo para descartar",
        "canal": "gaules",
        "video_id": "live_discard",
        "owner": "w-pc1",
        "status": "pendente",
        "criado_em": T0,
    })
    reg.set_discord(clip_id, {
        "thread_id": THREAD_ID,
        "message_id": "msg-discard-1",
        "attachment_id": "att-1",
        "filename": "clip.mp4",
        "size": 1000,
    })

    # Mock message class para testar edição via msg.edit
    class _DiscardMsg:
        def __init__(self, content: str, components: list[Any]):
            self.content = content
            self.components = components
            self.edits: list[dict[str, Any]] = []

        async def edit(self, *, content: str | None = None, view: Any = "UNSET", allowed_mentions: Any = None) -> None:
            self.edits.append({"content": content, "view": view, "allowed_mentions": allowed_mentions})
            if content is not None:
                self.content = content
            if view is None:
                self.components = []

    class _DiscardResponse(_FakeInteractionResponse):
        def __init__(self, msg: _DiscardMsg | None) -> None:
            super().__init__()
            self._msg = msg
            self.edited: list[dict[str, Any]] = []

    class _DiscardInteraction:
        def __init__(
            self,
            custom_id: str,
            msg: _DiscardMsg | None,
            *,
            user_id: int | str = ALLOWED_USER,
            user_name: str = "Romulo",
            channel: _FakeChannel | None = None,
        ):
            self.type = discord.InteractionType.component
            self.data = {"custom_id": custom_id, "component_type": 2}
            self.user = _FakeUser(user_id, name=user_name)
            self.channel = channel or _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
            self.channel_id = self.channel.id
            self.message = msg
            self.response = _DiscardResponse(msg)
            self.followup = self.response.followup

    # Mensagem original com estado 🟡 pendente
    orig_content = "🟡 pendente — **Titulo para descartar**\nID: `tg_discard_c01`\nGancho: INSANO\nNota: 9.0 (gemini)\nDescricao: Descricao longa"
    msg = _DiscardMsg(orig_content, [_component_factory(c) for c in _approve_components(clip_id)])

    # Click Descartar (autorizado)
    i_discard = _DiscardInteraction(f"descartar:{clip_id}", msg, user_name="Romulo")
    asyncio.run(bot.on_interaction(i_discard))

    # Resposta followup com confirmação
    assert i_discard.response.messages == [(f"clipe descartado: {clip_id}", False)]
    assert i_discard.response.edited == []  # edição da mensagem feita via msg.edit, não interaction.edit

    # Mensagem editada para 🗑️ descartado e sem botões
    assert msg.content.startswith("🗑️ descartado — **Titulo para descartar**")
    assert msg.edits[-1]["view"] is None  # botões removidos

    # Registro local marcado como descartado
    reg_rec = reg.get(clip_id)
    assert reg_rec is not None
    assert reg_rec["status"] == "descartado"

    # Mongo clips também marcado como descartado
    mongo_clip = db.clips.find_one({"$or": [{"_id": clip_id}, {"clip_id": clip_id}]})
    assert mongo_clip is not None
    assert mongo_clip["status"] == "descartado"


def test_on_interaction_descartar_negado_sem_permissao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """T16: Descartar negado para usuário não autorizado ou canal errado."""
    import mongomock
    from discord.components import _component_factory
    from clipes.notificar import _approve_components
    from clipes.registro import Registro

    db = mongomock.MongoClient().clipes_lives
    store = bot_mod.MongoStore(db)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )

    reg = Registro(tmp_path, mongo=db)
    clip_id = "tg_discard_c02"
    reg.upsert({
        "clip_id": clip_id,
        "titulo": "Titulo 2",
        "canal": "gaules",
        "video_id": "live_discard2",
        "owner": "w-pc1",
        "status": "pendente",
        "criado_em": T0,
    })

    # Mock message class para testar edição via msg.edit
    class _DiscardMsg:
        def __init__(self, content: str, components: list[Any]):
            self.content = content
            self.components = components
            self.edits: list[dict[str, Any]] = []

        async def edit(self, *, content: str | None = None, view: Any = "UNSET", allowed_mentions: Any = None) -> None:
            self.edits.append({"content": content, "view": view, "allowed_mentions": allowed_mentions})
            if content is not None:
                self.content = content
            if view is None:
                self.components = []

    class _DiscardResponse(_FakeInteractionResponse):
        def __init__(self, msg: _DiscardMsg | None) -> None:
            super().__init__()
            self._msg = msg
            self.edited: list[dict[str, Any]] = []

    class _DiscardInteraction:
        def __init__(
            self,
            custom_id: str,
            msg: _DiscardMsg | None,
            *,
            user_id: int | str = ALLOWED_USER,
            user_name: str = "Romulo",
            channel: _FakeChannel | None = None,
        ):
            self.type = discord.InteractionType.component
            self.data = {"custom_id": custom_id, "component_type": 2}
            self.user = _FakeUser(user_id, name=user_name)
            self.channel = channel or _FakeChannel(THREAD_ID, parent_id=CHANNEL_ID)
            self.channel_id = self.channel.id
            self.message = msg
            self.response = _DiscardResponse(msg)
            self.followup = self.response.followup

    orig_content = "🟡 pendente — **Titulo 2**\nID: `tg_discard_c02`"
    msg = _DiscardMsg(orig_content, [_component_factory(c) for c in _approve_components(clip_id)])

    # Usuário não autorizado
    i_bad_user = _DiscardInteraction(f"descartar:{clip_id}", msg, user_id=OTHER_USER)
    asyncio.run(bot.on_interaction(i_bad_user))
    assert i_bad_user.response.messages == [("sem permissao", True)]
    assert msg.content == orig_content  # não editado

    # Canal errado
    i_bad_ch = _DiscardInteraction(f"descartar:{clip_id}", msg, channel=_FakeChannel(OTHER_CHANNEL_ID))
    asyncio.run(bot.on_interaction(i_bad_ch))
    assert i_bad_ch.response.messages == [("sem permissao", True)]
    assert msg.content == orig_content


def test_update_button_message_altera_estado_para_agendado_e_publicado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """T16: _update_button_message troca 🟡 -> 🗓️ (agendado) -> ✅ (publicado) quando ambas plataformas."""
    import mongomock

    db = mongomock.MongoClient().clipes_lives
    store = bot_mod.MongoStore(db)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )

    # Mock message object - usa sentinel para distinguir "não passado" de "explicitamente None"
    _UNSET = object()

    class MockMessage:
        def __init__(self, content: str):
            self.content = content
            self.edits: list[dict] = []

        async def edit(self, *, content: str | None = None, view: Any = _UNSET, allowed_mentions: Any = None):
            if content is not None:
                self.content = content
            self.edits.append({"content": content, "view": view, "allowed_mentions": allowed_mentions})

    import discord
    no_mentions = discord.AllowedMentions.none()

    # 1. Estado inicial 🟡 pendente -> clica YouTube (agendado)
    msg = MockMessage("🟡 pendente — **Titulo teste**\nID: `tg_state_c01`")

    asyncio.run(bot._update_button_message(
        msg, _FakeUser(ALLOWED_USER, name="Romulo"), ALLOWED_USER,
        accepted=["youtube"],
        result="youtube: publicacao enfileirada; agendado para 2026-11-20 10:00 (America/Sao_Paulo)",
        no_mentions=no_mentions,
        agendar=True,
    ))

    # Estado deve ser 🗓️ agendado com data/hora
    assert "🗓️ agendado (" in msg.content
    assert "2026-11-20 10:00" in msg.content
    assert "YouTube por Romulo (agendado)" in msg.content
    # Botões devem permanecer (ainda não tem TikTok no conteúdo) - view não deve ser None
    assert msg.edits[-1]["view"] is not None

    # 2. Segundo clique: TikTok também agendado -> ✅ publicado (YouTube, TikTok) e botões removidos
    # Porque o conteúdo já tem "YouTube por", o has_yt=True, e ao adicionar "TikTok por", has_tt=True
    msg2 = MockMessage("🗓️ agendado (2026-11-20 10:00) — **Titulo teste**\nID: `tg_state_c01`\nYouTube por Romulo (agendado)")
    asyncio.run(bot._update_button_message(
        msg2, _FakeUser(OTHER_USER, name="Maria"), OTHER_USER,
        accepted=["tiktok"],
        result="tiktok: publicacao enfileirada; agendado para 2026-11-20 19:00 (America/Sao_Paulo)",
        no_mentions=no_mentions,
        agendar=True,
    ))

    # Estado deve ser ✅ publicado (YouTube, TikTok) porque ambas plataformas estão no conteúdo
    assert "✅ publicado (YouTube, TikTok)" in msg2.content
    assert "TikTok por Maria (agendado)" in msg2.content
    # Botões devem ser removidos (ambas plataformas agora no conteúdo)
    assert msg2.edits[-1]["view"] is None

    # 3. O estado ✅ publicado também é atualizado por _edit_published_status_message no run_pending
    # Testado em tests/test_publicacao.py


def test_publicar_clip_descartado_retorna_clipe_descartado_sem_enfileirar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """T16: publicacao.request recusa clipe descartado com 'clipe descartado'."""
    import mongomock
    from clipes.registro import Registro
    from clipes import publicacao

    db = mongomock.MongoClient().clipes_lives
    store = bot_mod.MongoStore(db)
    bot = bot_mod.make_bot(
        store,
        allowed_ids=[ALLOWED_USER],
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        out_root=tmp_path,
        now=T0,
    )

    # Criar clipe no registro e depois marcar como descartado
    reg = Registro(tmp_path, mongo=db)
    clip_id = "tg_discard_c03"
    reg.upsert({
        "clip_id": clip_id,
        "titulo": "Titulo descartado",
        "canal": "gaules",
        "video_id": "live_discard3",
        "owner": "w-pc1",
        "criado_em": T0,
    })
    reg.marcar(clip_id, status="descartado")

    # Tentar publicar via bot._request_publish (que chama publicacao.request)
    result = bot._request_publish(clip_id, ALLOWED_USER, THREAD_ID, agendar=True, platforms=publicacao.PLATFORMS)
    assert result == "clipe descartado"

    # Nenhum request de publicação deve ter sido criado
    assert db.publish_requests.count_documents({"clip_id": clip_id}) == 0
