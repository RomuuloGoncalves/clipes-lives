"""Bot do Discord (gateway discord.py) para comandos publicar e listar."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from . import publicacao
from .publicacao import LocalStore, MongoStore

_PUBLICAR_RE = re.compile(r"^\s*publicar\s+(\S+)\s*$", re.IGNORECASE)
_DISCORD_MAX_CHARS = 1900


def _resolve_out_root(store: Any, out_root: Path | str | None) -> Path:
    if out_root is not None:
        return Path(out_root)
    store_root = getattr(store, "out_root", None)
    if store_root is not None:
        return Path(store_root)
    return Path(os.environ.get("OUT_DIR") or "out")


def _extract_thread_id(channel: Any) -> str | None:
    if channel is None:
        return None
    parent_id = getattr(channel, "parent_id", None)
    if parent_id is None and getattr(channel, "parent", None) is not None:
        parent_id = getattr(channel.parent, "id", None)
    if parent_id is None:
        return None
    cid = getattr(channel, "id", None)
    return str(cid) if cid is not None else None


def _in_configured_channel(channel: Any, configured_channel_id: str | int | None) -> bool:
    if configured_channel_id is None or str(configured_channel_id).strip() == "":
        return False
    target = str(configured_channel_id).strip()
    if channel is None:
        return False
    if str(getattr(channel, "id", "") or "") == target:
        return True
    parent_id = getattr(channel, "parent_id", None)
    if parent_id is None and getattr(channel, "parent", None) is not None:
        parent_id = getattr(channel.parent, "id", None)
    return parent_id is not None and str(parent_id) == target


def _clip_id_from_entry(manifest: dict[str, Any], entry: dict[str, Any]) -> str | None:
    cid = entry.get("clip_id")
    if cid:
        return str(cid)
    video_id = manifest.get("video_id")
    index = entry.get("index")
    if video_id and index is not None:
        try:
            return f"{video_id}_c{int(index):02d}"
        except (TypeError, ValueError):
            return None
    return None


def _list_recent_clips(
    out_root: Path | None,
    *,
    store: Any = None,
    limit: int = 10,
) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    seen: set[str] = set()

    if out_root is not None and out_root.is_dir():
        manifests: list[tuple[float, str, Path]] = []
        for path in out_root.rglob("clips.json"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            manifests.append((mtime, str(path), path))
        manifests.sort(key=lambda row: (row[0], row[1]), reverse=True)

        for _, _, path in manifests:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
                continue
            for entry in reversed(data["clips"]):
                if not isinstance(entry, dict):
                    continue
                cid = _clip_id_from_entry(data, entry)
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                title = str(entry.get("title") or "").strip()
                items.append((cid, title))
                if len(items) >= limit:
                    return items

    db = getattr(store, "db", None)
    if db is not None and hasattr(db, "clips") and len(items) < limit:
        try:
            cursor = db.clips.find({}).sort("created_at", -1).limit(limit)
            for doc in cursor:
                cid = str(doc.get("clip_id") or doc.get("_id") or "").strip()
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                title = str(doc.get("title") or "").strip()
                items.append((cid, title))
                if len(items) >= limit:
                    break
        except Exception:
            pass

    return items


def _format_recent_clips(out_root: Path | None, *, store: Any = None, limit: int = 10) -> str:
    items = _list_recent_clips(out_root, store=store, limit=limit)
    if not items:
        return "nenhum clipe encontrado"
    lines: list[str] = []
    total_len = 0
    for cid, title in items:
        line = f"• `{cid}` — {title[:60]}" if title else f"• `{cid}`"
        if total_len + len(line) + 1 > _DISCORD_MAX_CHARS:
            break
        lines.append(line)
        total_len += len(line) + 1
    return "\n".join(lines) if lines else "nenhum clipe encontrado"


class ClipesBot(discord.Client):
    """Discord gateway client hosting `/publicar`, `/listar`, and `publicar <id>`."""

    def __init__(
        self,
        *,
        intents: discord.Intents,
        store: Any,
        allowed_ids: Iterable[str | int] | None,
        channel_id: str | int | None,
        guild_id: str | int | None,
        out_root: Path,
        tokens_path: Path | str | None = None,
        policy_path: Path | str | None = None,
        quota_path: Path | str | None = None,
        now: Callable[[], float] | float | None = None,
        run_local: bool = False,
        run_pending_fn: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.store = store
        self.allowed_ids = allowed_ids
        self.channel_id = channel_id
        self.guild_id = int(guild_id) if guild_id is not None and str(guild_id).strip() else None
        self.out_root = out_root
        self.tokens_path = tokens_path
        self.policy_path = policy_path
        self.quota_path = quota_path
        self.now = now
        self.run_local = run_local
        self.run_pending_fn = run_pending_fn or publicacao.run_pending

    async def setup_hook(self) -> None:
        if self.guild_id is not None:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def _maybe_notify_thread(self, clip_id: str, result: str, current_channel_id: Any) -> None:
        if "publicacao enfileirada" not in result:
            return
        found = publicacao.find_clip(self.out_root, clip_id, store=self.store)
        thread_id = found.get("thread_id") if found else None
        if not thread_id or str(thread_id) == str(current_channel_id or ""):
            return
        try:
            target = self.get_channel(int(thread_id))
        except (TypeError, ValueError):
            target = None
        if target is not None and hasattr(target, "send"):
            try:
                await target.send(result)
            except Exception:
                pass

    async def _maybe_run_local(self, result: str) -> None:
        if not self.run_local or "publicacao enfileirada" not in result:
            return
        await asyncio.to_thread(
            self.run_pending_fn,
            self.store,
            None,
            out_root=self.out_root,
            now=self.now,
            policy_path=self.policy_path,
            tokens_path=self.tokens_path,
            quota_path=self.quota_path,
        )

    def _request_publish(self, clip_id: str, user_id: str | int, thread_id: str | None) -> str:
        return publicacao.request(
            self.store,
            clip_id,
            user_id,
            now=self.now,
            out_root=self.out_root,
            allowed_ids=self.allowed_ids,
            policy_path=self.policy_path,
            tokens_path=self.tokens_path,
            quota_path=self.quota_path,
            thread_id=thread_id,
        )

    async def on_message(self, message: Any) -> None:
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False):
            return
        if self.user is not None and getattr(author, "id", None) == getattr(self.user, "id", None):
            return

        channel = getattr(message, "channel", None)
        if not _in_configured_channel(channel, self.channel_id):
            return

        match = _PUBLICAR_RE.match(getattr(message, "content", "") or "")
        if not match:
            return

        clip_id = match.group(1).strip()
        thread_id = _extract_thread_id(channel)
        result = self._request_publish(clip_id, getattr(author, "id", ""), thread_id)

        if hasattr(message, "reply"):
            await message.reply(result, mention_author=False)
        elif channel is not None and hasattr(channel, "send"):
            await channel.send(result)

        await self._maybe_notify_thread(clip_id, result, getattr(channel, "id", None))
        await self._maybe_run_local(result)


def make_bot(
    store: Any = None,
    allowed_ids: Iterable[str | int] | None = None,
    channel_id: str | int | None = None,
    guild_id: str | int | None = None,
    *,
    out_root: Path | str | None = None,
    tokens_path: Path | str | None = None,
    policy_path: Path | str | None = None,
    quota_path: Path | str | None = None,
    now: Callable[[], float] | float | None = None,
    run_local: bool = False,
    run_pending_fn: Callable[..., Any] | None = None,
) -> ClipesBot:
    """Create the Discord bot with `message_content` intent, `/publicar`, `/listar`, and `publicar <id>`."""
    resolved_root = _resolve_out_root(store, out_root)
    if store is None:
        store = LocalStore(resolved_root)

    intents = discord.Intents.default()
    intents.message_content = True

    bot = ClipesBot(
        intents=intents,
        store=store,
        allowed_ids=allowed_ids,
        channel_id=channel_id,
        guild_id=guild_id,
        out_root=resolved_root,
        tokens_path=tokens_path,
        policy_path=policy_path,
        quota_path=quota_path,
        now=now,
        run_local=run_local,
        run_pending_fn=run_pending_fn,
    )

    @bot.tree.command(name="publicar", description="Publica um clipe como privado no YouTube e no TikTok")
    @app_commands.describe(id="ID do clipe (ex: video_c01)")
    async def publicar_cmd(interaction: discord.Interaction, id: str) -> None:
        clip_id = id.strip()
        channel = getattr(interaction, "channel", None)
        thread_id = _extract_thread_id(channel)
        user_id = getattr(getattr(interaction, "user", None), "id", "")
        result = bot._request_publish(clip_id, user_id, thread_id)
        await interaction.response.send_message(result, ephemeral=(result == "sem permissao"))
        current_cid = getattr(interaction, "channel_id", getattr(channel, "id", None))
        await bot._maybe_notify_thread(clip_id, result, current_cid)
        await bot._maybe_run_local(result)

    @bot.tree.command(name="listar", description="Lista os 10 ultimos clip_id disponiveis")
    async def listar_cmd(interaction: discord.Interaction) -> None:
        text = _format_recent_clips(bot.out_root, store=bot.store, limit=10)
        await interaction.response.send_message(text, ephemeral=False)

    return bot


def _parse_optional_int(env_name: str) -> int | None:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return None
    return int(raw)


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for `python -m clipes bot`."""
    _ = argv
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        print("[bot] erro: DISCORD_BOT_TOKEN nao definido", file=sys.stderr, flush=True)
        return 2

    try:
        channel_id = _parse_optional_int("DISCORD_CHANNEL_ID")
        guild_id = _parse_optional_int("DISCORD_GUILD_ID")
    except ValueError as exc:
        print(f"[bot] erro: ID do Discord invalido ({exc})", file=sys.stderr, flush=True)
        return 2

    out_root = Path(os.environ.get("OUT_DIR") or "out").expanduser().resolve()
    mongo_uri = os.environ.get("CLIPES_MONGO_URI", "").strip()

    store: LocalStore | MongoStore
    run_local = False
    if mongo_uri:
        try:
            import pymongo

            client = pymongo.MongoClient(mongo_uri, maxPoolSize=5, serverSelectionTimeoutMS=5000)
            db = client.get_default_database(default="clipes_lives")
            store = MongoStore(db)
            store.ensure_indexes()
        except Exception as exc:
            msg = str(exc).replace(mongo_uri, "***").replace(token, "***")
            print(f"[bot] erro Mongo: {msg}", file=sys.stderr, flush=True)
            return 2
    else:
        store = LocalStore(out_root)
        run_local = True

    bot = make_bot(
        store,
        allowed_ids=None,
        channel_id=channel_id,
        guild_id=guild_id,
        out_root=out_root,
        run_local=run_local,
    )
    try:
        bot.run(token, log_handler=None)
    except Exception as exc:
        msg = str(exc).replace(token, "***")
        print(f"[bot] erro ao executar gateway: {msg}", file=sys.stderr, flush=True)
        return 1
    return 0
