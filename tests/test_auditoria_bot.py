"""Auditoria (onda 4, bot): A-07 defer + thread nos handlers, B-13 OUT_DIR resolvido."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import discord

from clipes import bot as bot_mod
from clipes import publicacao
from clipes.publicacao import LocalStore

CHANNEL_ID = 1572417050771595386
THREAD_ID = 555666777888999000
USER = 111


class _Resp:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.done = False
        self.deferred: list[dict[str, Any]] = []
        self.sent: list[tuple[str, bool]] = []

    def is_done(self) -> bool:
        return self.done

    async def defer(self, **kwargs: Any) -> None:
        if self.done:
            raise RuntimeError("InteractionResponded")
        self.done = True
        self.deferred.append(kwargs)
        self.log.append("defer")

    async def send_message(self, content: str, *, ephemeral: bool = False, **_: Any) -> None:
        if self.done:
            raise RuntimeError("InteractionResponded")
        self.done = True
        self.sent.append((content, ephemeral))
        self.log.append("send_message")


class _Followup:
    """Modela o Discord: o 1o followup apos defer vira a resposta adiada e herda a visibilidade do defer."""

    def __init__(self, log: list[str], sent: list[tuple[str, bool]], resp: "_Resp") -> None:
        self.log = log
        self.sent = sent
        self.resp = resp
        self.first_after_defer = True

    async def send(self, content: str, *, ephemeral: bool = False, **_: Any) -> None:
        self.log.append("followup")
        if self.resp.deferred and self.first_after_defer:
            self.first_after_defer = False
            ephemeral = bool(self.resp.deferred[0].get("ephemeral", False))
        self.sent.append((content, ephemeral))


class _Chan:
    def __init__(self, cid: int, parent_id: int | None = None) -> None:
        self.id = cid
        self.parent_id = parent_id


class _Inter:
    def __init__(self, user_id: int = USER) -> None:
        self.log: list[str] = []
        self.sent: list[tuple[str, bool]] = []
        self.response = _Resp(self.log)
        self.followup = _Followup(self.log, self.sent, self.response)
        self.deleted = 0
        self.user = type("U", (), {"id": user_id, "display_name": "x", "name": "x"})()
        self.channel = _Chan(CHANNEL_ID)
        self.channel_id = CHANNEL_ID


def _add_delete(cls: type) -> type:
    async def delete_original_response(self: Any) -> None:
        self.deleted += 1
        self.log.append("delete_original")
        self.followup.first_after_defer = False  # o original sumiu: o proximo followup e mensagem nova (efemera)

    cls.delete_original_response = delete_original_response  # type: ignore[attr-defined]
    return cls


_add_delete(_Inter)


def _bot(tmp_path: Path, allowed: list[int] | None = None) -> Any:
    return bot_mod.make_bot(
        LocalStore(tmp_path), allowed_ids=allowed or [USER], channel_id=CHANNEL_ID, guild_id=1, out_root=tmp_path
    )


def test_publicar_defer_antes_do_trabalho_e_roda_em_thread(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    seen: dict[str, Any] = {}
    inter = _Inter()

    def fake_request(*_a: Any, **_k: Any) -> str:
        seen["thread"] = threading.current_thread()
        seen["log"] = list(inter.log)
        return "youtube: ja publicado (x)"

    monkeypatch.setattr(bot, "_request_publish", fake_request)
    cmd = bot.tree.get_command("publicar")
    asyncio.run(cmd.callback(inter, "c1_c01"))
    assert seen["thread"] is not threading.main_thread()
    assert seen["log"] == ["defer"]  # defer ja tinha acontecido quando o I/O rodou
    assert inter.log == ["defer", "followup"]
    assert inter.sent == [("youtube: ja publicado (x)", False)]


def test_publicar_sem_permissao_responde_efemero_sem_trabalho(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    monkeypatch.setattr(bot, "_request_publish", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nao deveria rodar")))
    inter = _Inter(user_id=999)
    asyncio.run(bot.tree.get_command("publicar").callback(inter, "c1_c01"))
    assert inter.log == ["send_message"]


def test_listar_e_buscar_fazem_defer_antes_do_trabalho_e_em_thread(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    for name, fn_name, args in (("listar", "_format_recent_clips", ()), ("buscar", "_format_search_results", ("abc",))):
        inter = _Inter()
        seen: dict[str, Any] = {}

        def fake(*_a: Any, _inter: Any = inter, _seen: dict[str, Any] = seen, **_k: Any) -> str:
            _seen["thread"] = threading.current_thread()
            _seen["log"] = list(_inter.log)
            return "ok"

        monkeypatch.setattr(bot_mod, fn_name, fake)
        asyncio.run(bot.tree.get_command(name).callback(inter, *args))
        assert seen["log"] == ["defer"], name
        assert seen["thread"] is not threading.main_thread(), name
        assert inter.log == ["defer", "followup"], name
        assert inter.sent[0] == ("ok", False)


def test_on_message_publicar_roda_request_em_thread(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    seen: dict[str, Any] = {}

    def fake_request(*_a: Any, **_k: Any) -> str:
        seen["thread"] = threading.current_thread()
        return "x"

    monkeypatch.setattr(bot, "_request_publish", fake_request)
    replies: list[str] = []

    class _Msg:
        content = "publicar c1_c01"
        author = type("A", (), {"id": USER, "bot": False})()
        channel = _Chan(CHANNEL_ID)

        async def reply(self, text: str, **_: Any) -> None:
            replies.append(text)

    asyncio.run(bot.on_message(_Msg()))
    assert seen["thread"] is not threading.main_thread()
    assert replies == ["x"]


def test_excecao_no_trabalho_envia_erro_efemero_sem_travar(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)

    def boom(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("falhou segredo-xyz")

    monkeypatch.setattr(bot, "_request_publish", boom)
    monkeypatch.setattr(bot_mod, "_format_recent_clips", boom)
    monkeypatch.setattr(bot_mod, "_format_search_results", boom)
    for name, args in (("publicar", ("c1_c01",)), ("listar", ()), ("buscar", ("abc",))):
        inter = _Inter()
        asyncio.run(bot.tree.get_command(name).callback(inter, *args))
        assert inter.log == ["defer", "delete_original", "followup"], name
        text, eph = inter.sent[0]
        assert eph is True and "segredo-xyz" not in text, name


def test_botao_excecao_envia_erro_efemero(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    inter = _button_inter()
    monkeypatch.setattr(bot, "_request_publish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    asyncio.run(bot.on_interaction(inter))
    assert inter.log == ["defer", "delete_original", "followup"]
    assert inter.sent[0][1] is True


def test_interacao_ja_respondida_usa_followup(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    monkeypatch.setattr(bot, "_request_publish", lambda *a, **k: "ok")
    inter = _Inter()
    inter.response.done = True  # ja respondida: defer/send_message levantariam
    asyncio.run(bot.tree.get_command("publicar").callback(inter, "c1_c01"))
    assert inter.log == ["followup"]
    deny = _Inter(user_id=999)
    deny.response.done = True
    asyncio.run(bot.tree.get_command("listar").callback(deny))
    assert deny.log == ["followup"] and deny.sent == [("sem permissao", True)]


def test_botao_usa_defer_thinking(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    monkeypatch.setattr(bot, "_request_publish", lambda *a, **k: "ok")
    inter = _button_inter()
    asyncio.run(bot.on_interaction(inter))
    assert inter.response.deferred == [{"thinking": True}]


def test_notify_thread_find_clip_com_erro_nao_propaga(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    monkeypatch.setattr(publicacao, "find_clip", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    asyncio.run(bot._maybe_notify_thread("c1_c01", "youtube: publicacao enfileirada", 1))


def test_listar_formata_fora_do_event_loop(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    seen: dict[str, Any] = {}

    def fake_format(*_a: Any, **_k: Any) -> str:
        seen["thread"] = threading.current_thread()
        return "lista"

    monkeypatch.setattr(bot_mod, "_format_recent_clips", fake_format)
    asyncio.run(bot.tree.get_command("listar").callback(_Inter()))
    assert seen["thread"] is not threading.main_thread()


def _button_inter() -> _Inter:
    inter = _Inter()
    inter.channel = _Chan(THREAD_ID, parent_id=CHANNEL_ID)
    inter.channel_id = THREAD_ID
    inter.type = discord.InteractionType.component  # type: ignore[attr-defined]
    inter.data = {"custom_id": "publicar:youtube:c1_c01", "component_type": 2}  # type: ignore[attr-defined]
    inter.message = None  # type: ignore[attr-defined]
    return inter


def test_botao_faz_defer_e_responde_por_followup(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    seen: dict[str, Any] = {}
    inter = _button_inter()

    def fake_request(*_a: Any, **_k: Any) -> str:
        seen["thread"] = threading.current_thread()
        seen["log"] = list(inter.log)
        return "youtube: ja publicado (x)"

    monkeypatch.setattr(bot, "_request_publish", fake_request)
    asyncio.run(bot.on_interaction(inter))
    assert seen["thread"] is not threading.main_thread()
    assert seen["log"] == ["defer"]
    assert inter.log == ["defer", "followup"]


def test_notify_thread_chama_find_clip_em_thread(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    seen: dict[str, Any] = {}

    def fake_find(*_a: Any, **_k: Any) -> None:
        seen["thread"] = threading.current_thread()
        return None

    monkeypatch.setattr(publicacao, "find_clip", fake_find)
    asyncio.run(bot._maybe_notify_thread("c1_c01", "youtube: publicacao enfileirada", 1))
    assert seen["thread"] is not threading.main_thread()


def test_out_root_padrao_usa_resolve_out_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OUT_DIR", "~/rel_out_x")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    root = bot_mod._resolve_out_root(object(), None)
    assert root.is_absolute() and "~" not in str(root)
    assert root == (tmp_path / "rel_out_x").resolve()


def test_request_publish_serializado_entre_threads(tmp_path: Path, monkeypatch):
    bot = _bot(tmp_path)
    state = {"ativos": 0, "max": 0}
    guard = threading.Lock()

    def slow_request(*_a: Any, **_k: Any) -> str:
        with guard:
            state["ativos"] += 1
            state["max"] = max(state["max"], state["ativos"])
        threading.Event().wait(0.05)
        with guard:
            state["ativos"] -= 1
        return "ok"

    monkeypatch.setattr(publicacao, "request", slow_request)

    async def go() -> None:
        await asyncio.gather(*(asyncio.to_thread(bot._request_publish, f"c{i}", USER, None) for i in range(4)))

    asyncio.run(go())
    assert state["max"] == 1


def test_redact_mascara_segredos_de_env(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok-abcdef123")
    monkeypatch.setenv("CLIPES_MONGO_URI", "mongodb://u:pw@host/db")
    out = bot_mod._redact("erro tok-abcdef123 e mongodb://u:pw@host/db")
    assert "tok-abcdef123" not in out and "pw@host" not in out
