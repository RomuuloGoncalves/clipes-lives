"""Bot do Discord (gateway discord.py) para comandos publicar e listar."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import sys
import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from . import config, publicacao
from .executor_publicacao import PublishLoop
from .publicacao import LocalStore, MongoStore
from .registro import Registro

_PUBLICAR_RE = re.compile(r"^\s*publicar\s+(\S+)(?:\s+(agora))?\s*$", re.IGNORECASE)
_BUSCAR_RE = re.compile(r"^\s*buscar(?:\s+(.*))?$", re.IGNORECASE)
_DISCORD_MAX_CHARS = 1900
_PLATFORM_LABELS: dict[str, str] = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
}
_DEST_PLATFORMS: dict[str, tuple[str, ...]] = {
    "youtube": ("youtube",),
    "tiktok": ("tiktok",),
    "ambos": publicacao.PLATFORMS,
}


def _parse_button_custom_id(custom_id: str) -> tuple[tuple[str, ...], str] | None:
    if custom_id.startswith("aprovar:"):
        clip_id = custom_id[len("aprovar:") :].strip()
        return (publicacao.PLATFORMS, clip_id) if clip_id else None
    if custom_id.startswith("publicar:"):
        dest, sep, raw_clip_id = custom_id[len("publicar:") :].partition(":")
        clip_id = raw_clip_id.strip()
        platforms = _DEST_PLATFORMS.get(dest)
        if not sep or not clip_id or platforms is None:
            return None
        return (platforms, clip_id)
    return None


def _parse_agendar_custom_id(custom_id: str) -> tuple[bool, str] | None:
    if not custom_id.startswith("agendar:"):
        return None
    state, sep, raw_clip_id = custom_id[len("agendar:") :].partition(":")
    clip_id = raw_clip_id.strip()
    if not sep or not clip_id or state not in ("on", "off"):
        return None
    return (state == "on", clip_id)


def _read_agendar_toggle(message: Any) -> bool:
    components = getattr(message, "components", None)
    if not components:
        return True
    for row in components:
        items = row.get("components") if isinstance(row, dict) else (
            getattr(row, "children", None) or getattr(row, "components", None) or ()
        )
        for item in items:
            cid = str(item.get("custom_id") if isinstance(item, dict) else getattr(item, "custom_id", "") or "")
            if cid.startswith("agendar:on:"):
                return True
            if cid.startswith("agendar:off:"):
                return False
    return True


def _build_clip_view(clip_id: str, *, agendar_on: bool) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(style=discord.ButtonStyle(4), label="YouTube", custom_id=f"publicar:youtube:{clip_id}", row=0))
    view.add_item(discord.ui.Button(style=discord.ButtonStyle(2), label="TikTok", custom_id=f"publicar:tiktok:{clip_id}", row=0))
    view.add_item(discord.ui.Button(style=discord.ButtonStyle(3), label="Ambos", custom_id=f"publicar:ambos:{clip_id}", row=0))
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle(1 if agendar_on else 2),
            label="Agendar: sim" if agendar_on else "Agendar: nao",
            custom_id=f"agendar:{'on' if agendar_on else 'off'}:{clip_id}",
            row=1,
        )
    )
    return view


def _accepted_platforms(result: str) -> list[str]:
    accepted: list[str] = []
    for line in result.splitlines():
        plat, sep, rest = line.partition(":")
        if not sep or plat not in _PLATFORM_LABELS:
            continue
        if "publicacao enfileirada" in rest or "pedido fica na fila" in rest:
            accepted.append(plat)
    return accepted


def _has_platform_line(content: str, label: str) -> bool:
    prefix = f"{label} por "
    return any(line.startswith(prefix) for line in content.splitlines())


def _append_message_lines(orig_content: str, lines: list[str]) -> str:
    if not lines:
        return orig_content
    suffix = "\n".join(lines)
    if not orig_content:
        return suffix[:2000]
    if len(orig_content) + 1 + len(suffix) <= 2000:
        return f"{orig_content}\n{suffix}"
    max_orig = max(0, 2000 - len(suffix) - 1)
    return f"{orig_content[:max_orig]}\n{suffix}" if max_orig > 0 else suffix[:2000]


def _accepts_kw(fn: Any, kw: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    return kw in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def _resolve_out_root(store: Any, out_root: Path | str | None) -> Path:
    if out_root is not None:
        return Path(out_root)
    store_root = getattr(store, "out_root", None)
    if store_root is not None:
        return Path(store_root)
    return config.resolve_out_dir()


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


def _interaction_in_configured_channel(interaction: Any, configured_channel_id: str | int | None) -> bool:
    channel = getattr(interaction, "channel", None)
    if channel is not None:
        return _in_configured_channel(channel, configured_channel_id)
    if configured_channel_id is None or str(configured_channel_id).strip() == "":
        return False
    cid = getattr(interaction, "channel_id", None)
    return cid is not None and str(cid) == str(configured_channel_id).strip()


def _interaction_user_allowed(interaction: Any, allowed_ids: Iterable[str | int] | None) -> bool:
    user_id = getattr(getattr(interaction, "user", None), "id", "")
    return publicacao.user_allowed(user_id, allowed_ids)


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


def _resolve_guild_id(source: Any, bot_guild_id: int | None) -> str | None:
    gid = getattr(source, "guild_id", None)
    if gid is None and getattr(source, "guild", None) is not None:
        gid = getattr(source.guild, "id", None)
    if gid is not None:
        return str(gid)
    if bot_guild_id is not None:
        return str(bot_guild_id)
    env_gid = os.environ.get("DISCORD_GUILD_ID", "").strip()
    return env_gid or None


def _format_search_results(
    out_root: Path,
    texto: str,
    *,
    store: Any = None,
    guild_id: str | None = None,
    limit: int = 10,
) -> str:
    clean = (texto or "").strip()
    if len(clean) < 2:
        return "busca precisa de 2+ caracteres"

    reg = Registro(out_root, mongo=getattr(store, "db", None))
    hits = reg.buscar(clean, limite=limit)
    if not hits:
        return f'nenhum clipe para "{clean}"'

    lines: list[str] = []
    total_len = 0
    for doc in hits:
        cid = str(doc.get("clip_id") or doc.get("_id") or "")
        titulo = str(doc.get("titulo") or doc.get("title") or cid)[:50]
        canal = str(doc.get("canal") or doc.get("channel") or "-")
        status = str(doc.get("status") or "local")
        ts = doc.get("criado_em") if doc.get("criado_em") is not None else doc.get("created_at")
        try:
            date_str = datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d") if ts is not None else "-"
        except (TypeError, ValueError, OSError):
            date_str = "-"

        d_info = doc.get("discord") if isinstance(doc.get("discord"), dict) else {}
        t_id = d_info.get("thread_id")
        m_id = d_info.get("message_id")
        link_part = (
            f" | https://discord.com/channels/{guild_id}/{t_id}/{m_id}"
            if (guild_id and t_id and m_id)
            else ""
        )
        line = f"• `{cid}` — {titulo} | {canal} | {date_str} | {status}{link_part}"
        if total_len + len(line) + (1 if lines else 0) > _DISCORD_MAX_CHARS:
            if not lines:
                lines.append(line[:_DISCORD_MAX_CHARS])
            break
        lines.append(line)
        total_len += len(line) + (1 if len(lines) > 1 else 0)
    return "\n".join(lines)


_SECRET_ENV_HINTS = ("TOKEN", "SECRET", "PASSWORD", "MONGO_URI", "API_KEY")


def _redact(text: str) -> str:
    """Mascara segredos de env (token do Discord, URI do Mongo e variaveis com cara de segredo) em mensagens de erro."""
    for name, value in os.environ.items():
        value = value.strip()
        if len(value) >= 6 and (name == "DISCORD_BOT_TOKEN" or any(h in name.upper() for h in _SECRET_ENV_HINTS)):
            text = text.replace(value, "***")
    return text


async def _reply_now(interaction: Any, text: str, *, ephemeral: bool, no_mentions: Any) -> None:
    """Resposta imediata; se a interacao ja foi respondida/deferida, usa followup (evita InteractionResponded).

    Caso is_done com defer publico: o primeiro followup vira a propria resposta adiada e herda a visibilidade
    do defer, entao `ephemeral=True` pode sair publico. Nos handlers daqui a rejeicao vem antes do defer.
    """
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=ephemeral, allowed_mentions=no_mentions)
    else:
        await interaction.response.send_message(text, ephemeral=ephemeral, allowed_mentions=no_mentions)


async def _defer_once(interaction: Any, *, thinking: bool = False) -> None:
    """defer() so se ainda nao respondida. `thinking=True` em botoes: resposta nova (nao edicao) com 'pensando...'."""
    if interaction.response.is_done():
        return
    if thinking:
        await interaction.response.defer(thinking=True)
    else:
        await interaction.response.defer()


async def _run_deferred(interaction: Any, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Roda `fn` em thread apos o defer; se falhar, avisa por followup efemero e devolve None (sem deixar 'pensando...')."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except Exception as exc:
        print(f"[bot] erro no handler ({_redact(f'{type(exc).__name__}: {exc}')})", flush=True)
        try:
            # o 1o followup apos defer herda a visibilidade do defer (publico): remove o "pensando..." antes
            await interaction.delete_original_response()
        except Exception:
            pass
        try:
            await interaction.followup.send(
                "erro interno ao processar o pedido",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass
        return None


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
        local_poll_s: float = 60.0,
    ) -> None:
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
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
        self.local_poll_s = float(local_poll_s)
        self._local_run_lock = threading.Lock()
        self._request_lock = threading.Lock()  # serializa publicacao.request (slots/cota nao sao atomicos)
        self._publish_loop: PublishLoop | None = (
            PublishLoop(self._run_local_once, self.local_poll_s, name="bot-publicacao")
            if self.run_local
            else None
        )

    async def setup_hook(self) -> None:
        if self.guild_id is not None:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        if self._publish_loop is not None:
            self._publish_loop.start()

    async def close(self) -> None:
        if self._publish_loop is not None:
            await asyncio.to_thread(self._publish_loop.stop, 10.0)
        await super().close()

    async def _maybe_notify_thread(self, clip_id: str, result: str, current_channel_id: Any) -> None:
        if "publicacao enfileirada" not in result:
            return
        try:
            found = await asyncio.to_thread(publicacao.find_clip, self.out_root, clip_id, store=self.store)
        except Exception as exc:
            print(f"[bot] erro ao localizar clipe para notificar thread ({type(exc).__name__})", flush=True)
            return
        thread_id = found.get("thread_id") if found else None
        if not thread_id or str(thread_id) == str(current_channel_id or ""):
            return
        try:
            target = self.get_channel(int(thread_id))
        except (TypeError, ValueError):
            target = None
        if target is not None and hasattr(target, "send"):
            try:
                await target.send(result, allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                pass

    def _run_local_once(self, *, wait: bool = False) -> None:
        """Executa uma rodada local de run_pending fora do event loop (A-05)."""
        if not self._local_run_lock.acquire(blocking=wait):
            return
        try:
            self.run_pending_fn(
                self.store,
                None,
                out_root=self.out_root,
                now=self.now,
                policy_path=self.policy_path,
                tokens_path=self.tokens_path,
                quota_path=self.quota_path,
            )
        except Exception as exc:
            msg = _redact(f"{type(exc).__name__}: {exc}")
            print(f"[bot] erro na publicacao local ({msg})", flush=True)
        finally:
            self._local_run_lock.release()

    async def _maybe_run_local(self, result: str) -> None:
        if not self.run_local or "publicacao enfileirada" not in result:
            return
        await asyncio.to_thread(self._run_local_once, wait=True)

    def _request_publish(
        self,
        clip_id: str,
        user_id: str | int,
        thread_id: str | None,
        *,
        agendar: bool = True,
        platforms: tuple[str, ...] = publicacao.PLATFORMS,
    ) -> str:
        with self._request_lock:
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
                platforms=platforms,
                agendar=agendar,
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

        content = getattr(message, "content", "") or ""
        user_id = getattr(author, "id", "")

        search_match = _BUSCAR_RE.match(content)
        if search_match:
            if not publicacao.user_allowed(user_id, self.allowed_ids):
                reply_text = "sem permissao"
            else:
                gid = _resolve_guild_id(message, self.guild_id)
                reply_text = await asyncio.to_thread(
                    _format_search_results,
                    self.out_root,
                    search_match.group(1) or "",
                    store=self.store,
                    guild_id=gid,
                )
            no_mentions = discord.AllowedMentions.none()
            if hasattr(message, "reply"):
                await message.reply(reply_text, mention_author=False, allowed_mentions=no_mentions)
            elif channel is not None and hasattr(channel, "send"):
                await channel.send(reply_text, allowed_mentions=no_mentions)
            return

        match = _PUBLICAR_RE.match(content)
        if not match:
            return

        clip_id = match.group(1).strip()
        agora = match.group(2) is not None
        thread_id = _extract_thread_id(channel)
        result = await asyncio.to_thread(self._request_publish, clip_id, user_id, thread_id, agendar=not agora)

        no_mentions = discord.AllowedMentions.none()
        if hasattr(message, "reply"):
            await message.reply(result, mention_author=False, allowed_mentions=no_mentions)
        elif channel is not None and hasattr(channel, "send"):
            await channel.send(result, allowed_mentions=no_mentions)

        await self._maybe_notify_thread(clip_id, result, getattr(channel, "id", None))
        await self._maybe_run_local(result)

    async def _update_button_message(
        self,
        msg: Any,
        user: Any,
        user_id: Any,
        accepted: list[str],
        result: str,
        no_mentions: discord.AllowedMentions,
        *,
        agendar: bool = True,
    ) -> None:
        if msg is None or not hasattr(msg, "edit"):
            return
        raw_name = (
            getattr(user, "display_name", None)
            or getattr(user, "name", None)
            or str(user_id)
        )
        user_name = str(raw_name).replace("\n", " ").strip()
        orig_content = str(getattr(msg, "content", "") or "")
        mode = "agendado" if agendar else "agora"
        lines_to_add: list[str] = []
        for plat in accepted:
            label = _PLATFORM_LABELS[plat]
            if not _has_platform_line(orig_content, label):
                lines_to_add.append(f"{label} por {user_name} ({mode})")
        new_content = _append_message_lines(orig_content, lines_to_add)
        has_yt = _has_platform_line(new_content, "YouTube") or ("youtube: ja publicado" in result)
        has_tt = _has_platform_line(new_content, "TikTok") or ("tiktok: ja publicado" in result)
        remove_buttons = has_yt and has_tt
        if new_content == orig_content and not remove_buttons:
            return
        edit_kw: dict[str, Any] = (
            {"allowed_mentions": no_mentions}
            if _accepts_kw(msg.edit, "allowed_mentions")
            else {}
        )
        if remove_buttons:
            edit_kw["view"] = None
        try:
            await msg.edit(content=new_content, **edit_kw)
        except Exception:
            pass

    async def on_interaction(self, interaction: Any) -> None:
        itype = getattr(interaction, "type", None)
        if itype not in (discord.InteractionType.component, 3):
            return
        data = getattr(interaction, "data", None)
        if not isinstance(data, dict):
            return
        custom_id = str(data.get("custom_id") or "")
        toggle_parsed = _parse_agendar_custom_id(custom_id)
        dest_parsed = None if toggle_parsed is not None else _parse_button_custom_id(custom_id)
        if toggle_parsed is None and dest_parsed is None:
            return

        no_mentions = discord.AllowedMentions.none()
        if not _interaction_in_configured_channel(interaction, self.channel_id):
            await _reply_now(interaction, "canal nao permitido", ephemeral=True, no_mentions=no_mentions)
            return
        if not _interaction_user_allowed(interaction, self.allowed_ids):
            await _reply_now(interaction, "sem permissao", ephemeral=True, no_mentions=no_mentions)
            return

        if toggle_parsed is not None:
            current_on, clip_id = toggle_parsed
            view = _build_clip_view(clip_id, agendar_on=not current_on)
            edit_kw: dict[str, Any] = {"view": view}
            if _accepts_kw(interaction.response.edit_message, "allowed_mentions"):
                edit_kw["allowed_mentions"] = no_mentions
            await interaction.response.edit_message(**edit_kw)
            return

        assert dest_parsed is not None
        platforms, clip_id = dest_parsed
        msg = getattr(interaction, "message", None)
        agendar = _read_agendar_toggle(msg)
        channel = getattr(interaction, "channel", None)
        thread_id = _extract_thread_id(channel)
        user = getattr(interaction, "user", None)
        user_id = getattr(user, "id", "")
        # A-07: defer antes do I/O (o Discord invalida a interacao em 3 s) e resposta por followup
        await _defer_once(interaction, thinking=True)
        result = await _run_deferred(
            interaction, self._request_publish, clip_id, user_id, thread_id, agendar=agendar, platforms=platforms
        )
        if result is None:
            return
        await interaction.followup.send(
            result,
            allowed_mentions=no_mentions,
        )

        accepted = _accepted_platforms(result)
        if accepted:
            await self._update_button_message(
                msg,
                user,
                user_id,
                accepted,
                result,
                no_mentions,
                agendar=agendar,
            )

        current_cid = getattr(interaction, "channel_id", getattr(channel, "id", None))
        await self._maybe_notify_thread(clip_id, result, current_cid)
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
    local_poll_s: float = 60.0,
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
        local_poll_s=local_poll_s,
    )

    @bot.tree.command(name="publicar", description="Publica um clipe como privado no YouTube e no TikTok")
    @app_commands.describe(
        id="ID do clipe (ex: video_c01)",
        agora="Publica na hora, ignorando a agenda",
    )
    async def publicar_cmd(interaction: discord.Interaction, id: str, agora: bool = False) -> None:
        no_mentions = discord.AllowedMentions.none()
        if not _interaction_in_configured_channel(interaction, bot.channel_id):
            await _reply_now(interaction, "canal nao permitido", ephemeral=True, no_mentions=no_mentions)
            return
        clip_id = id.strip()
        channel = getattr(interaction, "channel", None)
        thread_id = _extract_thread_id(channel)
        user_id = getattr(getattr(interaction, "user", None), "id", "")
        # A-07: defer antes do I/O; "sem permissao" e checado antes para sair efemero
        if not _interaction_user_allowed(interaction, bot.allowed_ids):
            await _reply_now(interaction, "sem permissao", ephemeral=True, no_mentions=no_mentions)
            return
        await _defer_once(interaction)
        result = await _run_deferred(
            interaction, bot._request_publish, clip_id, user_id, thread_id, agendar=not agora
        )
        if result is None:
            return
        await interaction.followup.send(
            result,
            allowed_mentions=no_mentions,
        )
        current_cid = getattr(interaction, "channel_id", getattr(channel, "id", None))
        await bot._maybe_notify_thread(clip_id, result, current_cid)
        await bot._maybe_run_local(result)

    @bot.tree.command(name="listar", description="Lista os 10 ultimos clip_id disponiveis")
    async def listar_cmd(interaction: discord.Interaction) -> None:
        no_mentions = discord.AllowedMentions.none()
        if not _interaction_in_configured_channel(interaction, bot.channel_id):
            await _reply_now(interaction, "canal nao permitido", ephemeral=True, no_mentions=no_mentions)
            return
        if not _interaction_user_allowed(interaction, bot.allowed_ids):
            await _reply_now(interaction, "sem permissao", ephemeral=True, no_mentions=no_mentions)
            return
        await _defer_once(interaction)
        text = await _run_deferred(interaction, _format_recent_clips, bot.out_root, store=bot.store, limit=10)
        if text is None:
            return
        await interaction.followup.send(text, ephemeral=False, allowed_mentions=no_mentions)

    @bot.tree.command(name="buscar", description="Busca clipes no registro por texto")
    @app_commands.describe(texto="Palavras para buscar (minimo 2 caracteres)")
    async def buscar_cmd(interaction: discord.Interaction, texto: str) -> None:
        no_mentions = discord.AllowedMentions.none()
        if not _interaction_in_configured_channel(interaction, bot.channel_id):
            await _reply_now(interaction, "canal nao permitido", ephemeral=True, no_mentions=no_mentions)
            return
        if not _interaction_user_allowed(interaction, bot.allowed_ids):
            await _reply_now(interaction, "sem permissao", ephemeral=True, no_mentions=no_mentions)
            return
        gid = _resolve_guild_id(interaction, bot.guild_id)
        await _defer_once(interaction)
        reply = await _run_deferred(
            interaction,
            _format_search_results,
            bot.out_root,
            texto,
            store=bot.store,
            guild_id=gid,
        )
        if reply is None:
            return
        await interaction.followup.send(reply, ephemeral=False, allowed_mentions=no_mentions)

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

    out_root = config.resolve_out_dir()
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
