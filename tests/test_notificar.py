"""Unit tests for clipes.notificar.notify_discord (F3 T1)."""

from __future__ import annotations

import json

import pytest
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

    def post(
        self,
        channel_id: str | int,
        content: str,
        file: Path | None = None,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._seq += 1
        if self.fail_on_call is not None and self._seq >= self.fail_on_call:
            raise RuntimeError(f"HTTP 500 token={FAKE_TOKEN}")
        msg_id = f"msg-{self._seq}"
        self.posts.append({
            "channel_id": str(channel_id),
            "content": content,
            "file": Path(file) if file is not None else None,
            "components": components,
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


class _GuildClient(FakeDiscordClient):
    def __init__(self, canais=None, create_error=None):
        super().__init__()
        self.canais = canais or []
        self.create_error = create_error
        self.created: list[str] = []

    def list_guild_channels(self, guild_id):
        return self.canais

    def create_text_channel(self, guild_id, name):
        if self.create_error:
            raise self.create_error
        self.created.append(name)
        return {"id": "novo-1", "name": name}


@pytest.fixture
def aviso_env(monkeypatch, tmp_path):
    import clipes.notificar as n

    n._aviso_recente.clear()
    for k in ("DISCORD_LOG_CHANNEL_ID", "DISCORD_CHANNEL_ID", "DISCORD_GUILD_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    return tmp_path


def test_post_aviso_erro_mascarado(aviso_env, monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_LOG_CHANNEL_ID", "log-1")
    failing = FakeDiscordClient(fail_on_call=1)
    post_aviso("aviso falha", discord_client=failing)
    out = capsys.readouterr().out
    assert "[discord] falha ao enviar aviso" in out
    assert FAKE_TOKEN not in out and "***" in out


def test_post_aviso_so_channel_id_nao_posta(aviso_env, monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    client = FakeDiscordClient()
    post_aviso("x", discord_client=client)
    assert client.posts == []
    assert "x" in capsys.readouterr().out


def test_post_aviso_guild_com_bot_log_posta_e_grava_cache(aviso_env, monkeypatch):
    monkeypatch.setenv("DISCORD_GUILD_ID", "g1")
    client = _GuildClient([{"id": "42", "name": "bot-log"}])
    post_aviso("oi", discord_client=client)
    assert client.posts[0]["channel_id"] == "42"
    assert (aviso_env / ".discord_log_channel").read_text().strip() == "42"
    assert client.created == []


def test_post_aviso_403_na_criacao_so_stdout(aviso_env, monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_GUILD_ID", "g1")
    client = _GuildClient([], create_error=RuntimeError("HTTP 403"))
    post_aviso("oi", discord_client=client)
    assert client.posts == []
    assert "oi" in capsys.readouterr().out


def test_post_aviso_antispam_60s_e_contador(aviso_env, monkeypatch):
    monkeypatch.setenv("DISCORD_LOG_CHANNEL_ID", "log-1")
    t = [1000.0]
    monkeypatch.setattr("clipes.notificar.time.monotonic", lambda: t[0])
    client = FakeDiscordClient()
    for dt in (0, 5, 10):
        t[0] = 1000.0 + dt
        post_aviso("igual", discord_client=client)
    assert len(client.posts) == 1
    t[0] = 1071.0
    post_aviso("igual", discord_client=client)
    assert len(client.posts) == 2
    assert client.posts[1]["content"].endswith("(+2 repetidos)")


def test_notify_sem_env_discord_registra_local(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)

    out_dir = _make_out_dir(
        tmp_path,
        [{"index": 1, "clip_id": "vid1_c01", "file": "c1.mp4", "title": "Clip Sem Env", "score": 0.9, "duration": 30.0}],
    )
    notify_discord(out_dir, policy=ChannelPolicy(), out_root=tmp_path)

    reg_data = json.loads((tmp_path / "registro.json").read_text(encoding="utf-8"))
    rec = reg_data["clips"]["vid1_c01"]
    assert rec["status"] == "local"
    assert rec["tamanho"] == len(b"mp4data")
    assert len(rec["sha256"]) == 64


def test_notify_ack_ok_divergencias_verificar_0_e_retry_e_liberar(tmp_path, monkeypatch):
    monkeypatch.setattr("clipes.notificar.post_aviso", lambda *a, **k: None)  # aviso vai ao mesmo fake client
    import hashlib
    from clipes.discord_rest import DiscordRestError
    from clipes.publicacao import LocalStore

    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    monkeypatch.setenv("CLIPES_MASTER_MODE", "discord")

    class AckDiscordClient:
        def __init__(self, *, mode: str = "ok") -> None:
            self.mode = mode
            self.posts: list[dict[str, Any]] = []
            self.downloads: list[str] = []
            self.get_messages: list[tuple[str, str]] = []
            self._seq = 0

        def thread_from(self, channel_id, message_id, name, auto_archive_duration=1440):
            return "thr-ack"

        def post(self, channel_id, content, file=None, components=None):
            self._seq += 1
            msg_id = f"msg-{self._seq}"
            self.posts.append({
                "channel_id": str(channel_id),
                "content": content,
                "file": file,
                "components": components,
                "id": msg_id,
            })
            if file is None:
                return {"id": msg_id}
            if self.mode == "413":
                raise DiscordRestError("Discord HTTP 413: code=40005 Request entity too large", status=413, code=40005)
            if self.mode == "empty_att":
                return {"id": msg_id, "attachments": []}
            size = Path(file).stat().st_size
            if self.mode == "bad_size":
                size += 10
            return {
                "id": msg_id,
                "attachments": [{
                    "id": f"att-{self._seq}",
                    "filename": Path(file).name,
                    "size": size,
                    "url": "https://cdn.discordapp.com/att/1/2/c.mp4?ex=1&hm=secret",
                }],
            }

        def get_message(self, channel_id, message_id):
            self.get_messages.append((str(channel_id), str(message_id)))
            return {
                "id": str(message_id),
                "attachments": [{
                    "id": "att-2",
                    "filename": "c1.mp4",
                    "size": len(b"mp4data"),
                    "url": "https://cdn.discordapp.com/att/1/2/c.mp4?ex=2&hm=secret2",
                }],
            }

        def download(self, url, dst, max_bytes):
            self.downloads.append(url)
            data = b"corrupted" if self.mode == "bad_sha" else b"mp4data"
            return len(data), hashlib.sha256(data).hexdigest()

    # 1. Happy ACK + retencao=0 deletes local mp4, skips busy clip
    out_ok = _make_out_dir(
        tmp_path / "ok_run",
        [
            {"index": 1, "clip_id": "ok_c01", "file": "c1.mp4", "title": "OK 1", "score": 0.9, "duration": 20.0},
            {"index": 2, "clip_id": "busy_c02", "file": "c2.mp4", "title": "Busy 2", "score": 0.9, "duration": 20.0},
        ],
    )
    ok_root = tmp_path / "ok_run"
    LocalStore(ok_root).enqueue_request({"_id": "busy_c02:youtube", "clip_id": "busy_c02", "status": "running"})
    monkeypatch.setenv("CLIPES_RETENCAO_H", "0")
    monkeypatch.setenv("CLIPES_REGISTRO_VERIFICAR", "1")
    client_ok = AckDiscordClient(mode="ok")
    notify_discord(out_ok, policy=ChannelPolicy(), discord_client=client_ok, preview_fn=lambda s, d, c: s, out_root=ok_root)

    assert len(client_ok.downloads) == 2
    reg_ok = json.loads((ok_root / "registro.json").read_text(encoding="utf-8"))
    assert reg_ok["clips"]["ok_c01"]["status"] == "so_discord"
    assert not (out_ok / "c1.mp4").exists()
    assert reg_ok["clips"]["busy_c02"]["status"] == "no_discord"
    assert (out_ok / "c2.mp4").is_file()
    assert "url" not in reg_ok["clips"]["ok_c01"]["discord"]

    # 2. Size mismatch -> ack_falhou, file kept
    out_sz = _make_out_dir(
        tmp_path / "sz_run",
        [{"index": 1, "clip_id": "sz_c01", "file": "c1.mp4", "title": "SZ", "score": 0.9, "duration": 20.0}],
    )
    notify_discord(
        out_sz, policy=ChannelPolicy(), discord_client=AckDiscordClient(mode="bad_size"),
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "sz_run",
    )
    assert json.loads((tmp_path / "sz_run" / "registro.json").read_text())["clips"]["sz_c01"]["status"] == "ack_falhou"
    assert (out_sz / "c1.mp4").is_file()

    # 3. Sha mismatch -> ack_falhou, file kept
    out_sha = _make_out_dir(
        tmp_path / "sha_run",
        [{"index": 1, "clip_id": "sha_c01", "file": "c1.mp4", "title": "SHA", "score": 0.9, "duration": 20.0}],
    )
    notify_discord(
        out_sha, policy=ChannelPolicy(), discord_client=AckDiscordClient(mode="bad_sha"),
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "sha_run",
    )
    assert json.loads((tmp_path / "sha_run" / "registro.json").read_text())["clips"]["sha_c01"]["status"] == "ack_falhou"
    assert (out_sha / "c1.mp4").is_file()

    # 4. CLIPES_REGISTRO_VERIFICAR=0 skips download and ACKs by size
    monkeypatch.setenv("CLIPES_REGISTRO_VERIFICAR", "0")
    monkeypatch.setenv("CLIPES_RETENCAO_H", "")
    out_nover = _make_out_dir(
        tmp_path / "nv_run",
        [{"index": 1, "clip_id": "nv_c01", "file": "c1.mp4", "title": "NV", "score": 0.9, "duration": 20.0}],
    )
    client_nv = AckDiscordClient(mode="ok")
    notify_discord(
        out_nover, policy=ChannelPolicy(), discord_client=client_nv,
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "nv_run",
    )
    assert client_nv.downloads == []
    assert json.loads((tmp_path / "nv_run" / "registro.json").read_text())["clips"]["nv_c01"]["status"] == "no_discord"

    # 5. HTTP 413 -> cabe_discord=false, fallback notice without attachment, saves message_id, no ACK, no resend
    out_413 = _make_out_dir(
        tmp_path / "r413",
        [{"index": 1, "clip_id": "c413", "file": "c1.mp4", "title": "413", "score": 0.9, "duration": 20.0}],
    )
    client_413 = AckDiscordClient(mode="413")
    notify_discord(
        out_413, policy=ChannelPolicy(), discord_client=client_413,
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "r413",
    )
    man_413 = json.loads((out_413 / "clips.json").read_text())
    assert man_413["clips"][0]["cabe_discord"] is False
    assert man_413["clips"][0]["discord"] == {"thread_id": "thr-ack", "message_id": "msg-3"}
    reg_413 = json.loads((tmp_path / "r413" / "registro.json").read_text())
    assert reg_413["clips"]["c413"]["status"] == "local"
    assert reg_413["clips"]["c413"]["discord"] is None
    assert client_413.posts[-1]["file"] is None
    assert "arquivo grande demais para o Discord" in client_413.posts[-1]["content"]
    posts_before = len(client_413.posts)
    notify_discord(
        out_413, policy=ChannelPolicy(), discord_client=client_413,
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "r413",
    )
    assert len(client_413.posts) == posts_before

    # 6. Empty attachments -> cabe_discord=false, fallback notice without attachment, saves message_id, no resend
    out_emp = _make_out_dir(
        tmp_path / "remp",
        [{"index": 1, "clip_id": "cemp", "file": "c1.mp4", "title": "Emp", "score": 0.9, "duration": 20.0}],
    )
    client_emp = AckDiscordClient(mode="empty_att")
    notify_discord(
        out_emp, policy=ChannelPolicy(), discord_client=client_emp,
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "remp",
    )
    man_emp = json.loads((out_emp / "clips.json").read_text())
    assert man_emp["clips"][0]["cabe_discord"] is False
    assert man_emp["clips"][0]["discord"] == {"thread_id": "thr-ack", "message_id": "msg-3"}
    assert client_emp.posts[-1]["file"] is None
    posts_emp_before = len(client_emp.posts)
    notify_discord(
        out_emp, policy=ChannelPolicy(), discord_client=client_emp,
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "remp",
    )
    assert len(client_emp.posts) == posts_emp_before

    # 7. Retry ACK when message_id+attachment_id exist but status is still local (e.g. previous download failed)
    monkeypatch.setenv("CLIPES_REGISTRO_VERIFICAR", "1")
    out_retry = _make_out_dir(
        tmp_path / "retry_run",
        [{"index": 1, "clip_id": "rt_c01", "file": "c1.mp4", "title": "RT", "score": 0.9, "duration": 20.0}],
    )

    class FlakyDownloadClient(AckDiscordClient):
        def download(self, url, dst, max_bytes):
            raise RuntimeError("transient network error")

    notify_discord(
        out_retry, policy=ChannelPolicy(), discord_client=FlakyDownloadClient(),
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "retry_run",
    )
    assert json.loads((tmp_path / "retry_run" / "registro.json").read_text())["clips"]["rt_c01"]["status"] == "local"

    client_retry_ok = AckDiscordClient(mode="ok")
    notify_discord(
        out_retry, policy=ChannelPolicy(), discord_client=client_retry_ok,
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "retry_run",
    )
    assert client_retry_ok.posts == []
    assert client_retry_ok.get_messages == [("thr-ack", "msg-2")]
    assert json.loads((tmp_path / "retry_run" / "registro.json").read_text())["clips"]["rt_c01"]["status"] == "no_discord"

    # 8. CLIPES_MASTER_MODE=livre keeps status="local" and never deletes even with retencao=0
    monkeypatch.setenv("CLIPES_MASTER_MODE", "livre")
    monkeypatch.setenv("CLIPES_RETENCAO_H", "0")
    out_livre = _make_out_dir(
        tmp_path / "livre_run",
        [{"index": 1, "clip_id": "lv_c01", "file": "c1.mp4", "title": "LV", "score": 0.9, "duration": 20.0}],
    )
    notify_discord(
        out_livre, policy=ChannelPolicy(), discord_client=AckDiscordClient(mode="ok"),
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "livre_run",
    )
    assert json.loads((tmp_path / "livre_run" / "registro.json").read_text())["clips"]["lv_c01"]["status"] == "local"
    assert (out_livre / "c1.mp4").is_file()


def test_notify_envia_botoes_publicar_com_custom_id_no_post_e_fallback_413(tmp_path, monkeypatch):
    monkeypatch.setattr("clipes.notificar.post_aviso", lambda *a, **k: None)  # aviso vai ao mesmo fake client
    from clipes.discord_rest import DiscordRestError

    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)

    out_dir = _make_out_dir(
        tmp_path / "ok",
        [
            {"index": 1, "clip_id": "vid1_c01", "file": "c1.mp4", "title": "Clip 1", "score": 0.9, "duration": 20.0},
            {"index": 2, "clip_id": "vid1_c02", "file": "c2.mp4", "title": "Clip 2", "score": 0.8, "duration": 25.0},
        ],
    )
    client = FakeDiscordClient()
    notify_discord(
        out_dir,
        policy=ChannelPolicy(),
        discord_client=client,
        preview_fn=lambda src, dur, cache_dir: src,
    )

    assert len(client.posts) == 3
    assert client.posts[0]["components"] is None
    for idx, clip_id in ((1, "vid1_c01"), (2, "vid1_c02")):
        comps = client.posts[idx]["components"]
        assert comps == [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 4,
                        "label": "YouTube",
                        "custom_id": f"publicar:youtube:{clip_id}",
                    },
                    {
                        "type": 2,
                        "style": 2,
                        "label": "TikTok",
                        "custom_id": f"publicar:tiktok:{clip_id}",
                    },
                    {
                        "type": 2,
                        "style": 3,
                        "label": "Ambos",
                        "custom_id": f"publicar:ambos:{clip_id}",
                    },
                ],
            },
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 2,
                        "label": "Agendar: nao",
                        "custom_id": f"agendar:off:{clip_id}",
                    },
                    {
                        "type": 2,
                        "style": 4,
                        "label": "Descartar",
                        "custom_id": f"descartar:{clip_id}",
                    },
                ],
            },
        ]

    # 413 fallback text post also keeps the 3 destination buttons + Agendar toggle + Descartar
    class Client413(FakeDiscordClient):
        def post(self, channel_id, content, file=None, components=None):
            if file is not None:
                raise DiscordRestError("Discord HTTP 413: code=40005", status=413, code=40005)
            return super().post(channel_id, content, file=None, components=components)

    out_413 = _make_out_dir(
        tmp_path / "r413",
        [{"index": 1, "clip_id": "vid413_c01", "file": "c1.mp4", "title": "Clip 413", "score": 0.9, "duration": 20.0}],
    )
    c413 = Client413()
    notify_discord(out_413, policy=ChannelPolicy(), discord_client=c413, preview_fn=lambda s, d, c: s)
    assert c413.posts[-1]["file"] is None
    assert len(c413.posts[-1]["components"]) == 2
    row_btns = c413.posts[-1]["components"][0]["components"]
    assert [b["custom_id"] for b in row_btns] == [
        "publicar:youtube:vid413_c01",
        "publicar:tiktok:vid413_c01",
        "publicar:ambos:vid413_c01",
    ]
    assert [b["label"] for b in row_btns] == ["YouTube", "TikTok", "Ambos"]
    assert [b["style"] for b in row_btns] == [4, 2, 3]
    row_toggle = c413.posts[-1]["components"][1]["components"]
    assert row_toggle == [
        {
            "type": 2,
            "style": 2,
            "label": "Agendar: nao",
            "custom_id": "agendar:off:vid413_c01",
        },
        {
            "type": 2,
            "style": 4,
            "label": "Descartar",
            "custom_id": "descartar:vid413_c01",
        },
    ]


def test_notify_registro_e_liberar_nao_derrubam_pipeline(tmp_path, monkeypatch, capsys):
    import clipes.notificar as notificar_mod

    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    monkeypatch.setenv("CLIPES_RETENCAO_H", "0")

    # 1. Corrupted clips.json without result returns safely and logs [notificar]
    bad_dir = tmp_path / "bad_json"
    bad_dir.mkdir()
    (bad_dir / "clips.json").write_text("{corrupt json", encoding="utf-8")
    client_bad = FakeDiscordClient()
    notify_discord(bad_dir, policy=ChannelPolicy(), discord_client=client_bad)
    assert client_bad.posts == []
    assert "[notificar]" in capsys.readouterr().out

    # 2. Corrupted clips.json with result fallback still posts to Discord
    (bad_dir / "c1.mp4").write_bytes(b"mp4data")
    notify_discord(
        bad_dir,
        policy=ChannelPolicy(),
        result={"video_id": "v_res", "clips": [{"index": 1, "clip_id": "v_res_c01", "file": "c1.mp4", "title": "R"}]},
        discord_client=client_bad,
        preview_fn=lambda s, d, c: s,
    )
    assert len(client_bad.posts) == 2
    assert "[notificar]" in capsys.readouterr().out

    # 3. OSError / disk full during registro upsert & PermissionError in liberar never crash
    ok_dir = _make_out_dir(
        tmp_path / "disk_err",
        [{"index": 1, "clip_id": "d_c01", "file": "c1.mp4", "title": "Disk Err", "score": 0.9, "duration": 20.0}],
    )
    monkeypatch.setattr(notificar_mod, "sha256_arquivo", lambda _: (_ for _ in ()).throw(OSError("No space left on device")))

    class FailingLiberarReg:
        last_mongo_error = False

        def get(self, cid):
            return None

        def upsert(self, rec):
            raise OSError("No space left on device")

        def marcar(self, cid, **kw):
            return None

        def set_discord(self, cid, info):
            return None

        def liberar(self, retencao_h, ocupados=()):
            raise PermissionError("Operation not permitted on unlink")

    client_ok = FakeDiscordClient()
    notify_discord(
        ok_dir,
        policy=ChannelPolicy(),
        discord_client=client_ok,
        preview_fn=lambda s, d, c: s,
        out_root=tmp_path / "disk_err",
        registro=FailingLiberarReg(),  # type: ignore[arg-type]
    )
    assert len(client_ok.posts) == 2
    out_log = capsys.readouterr().out
    assert "[notificar]" in out_log
    assert "No space left on device" in out_log
    assert "Operation not permitted on unlink" in out_log


def test_notify_fecha_mongoclient_criado_internamente(tmp_path, monkeypatch):
    import sys
    import types
    import mongomock

    monkeypatch.setenv("CLIPES_MONGO_URI", "mongodb+srv://user:secret@cluster.example.net/clipes_lives")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)

    closed_count = 0

    class TrackingClient:
        def __init__(self, uri, **kwargs):
            self._inner = mongomock.MongoClient()

        def get_default_database(self, default="clipes_lives"):
            return self._inner[default]

        def close(self):
            nonlocal closed_count
            closed_count += 1
            self._inner.close()

    fake_pymongo = types.SimpleNamespace(MongoClient=TrackingClient)
    monkeypatch.setitem(sys.modules, "pymongo", fake_pymongo)

    out_dir = _make_out_dir(
        tmp_path / "mc1",
        [{"index": 1, "clip_id": "mc_c01", "file": "c1.mp4", "title": "MC", "score": 0.9, "duration": 20.0}],
    )
    # Early return (no Discord env) still closes MongoClient
    notify_discord(out_dir, policy=ChannelPolicy(), out_root=tmp_path / "mc1")
    assert closed_count == 1

    # Full Discord run also closes MongoClient
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    notify_discord(
        out_dir,
        policy=ChannelPolicy(),
        discord_client=FakeDiscordClient(),
        preview_fn=lambda s, d, c: s,
        out_root=tmp_path / "mc1",
    )
    assert closed_count == 2


def test_notify_cabe_discord_false_nao_refaz_upload_e_oserror_faz_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("clipes.notificar.post_aviso", lambda *a, **k: None)  # aviso vai ao mesmo fake client
    from clipes.discord_rest import DiscordRest

    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    monkeypatch.setenv("CLIPES_MASTER_MODE", "discord")

    # 1. cabe_discord=False ainda tenta a prévia; prévia None -> texto sem anexo, saves message_id
    out_false = _make_out_dir(
        tmp_path / "prefalse",
        [{"index": 1, "clip_id": "cf_c01", "file": "c1.mp4", "title": "Big", "cabe_discord": False, "duration": 20.0}],
    )
    preview_calls = 0

    def fail_if_preview(src, dur, cache):
        nonlocal preview_calls
        preview_calls += 1
        return None

    client_cf = FakeDiscordClient()
    notify_discord(
        out_false,
        policy=ChannelPolicy(),
        discord_client=client_cf,
        preview_fn=fail_if_preview,
        out_root=tmp_path / "prefalse",
    )
    assert preview_calls == 1
    assert len(client_cf.posts) == 2
    assert client_cf.posts[-1]["file"] is None
    assert "arquivo grande demais para o Discord" in client_cf.posts[-1]["content"]
    man_cf = json.loads((out_false / "clips.json").read_text())
    assert man_cf["clips"][0]["discord"]["message_id"] == "msg-2"

    # Second run makes 0 posts
    notify_discord(
        out_false,
        policy=ChannelPolicy(),
        discord_client=client_cf,
        preview_fn=fail_if_preview,
        out_root=tmp_path / "prefalse",
    )
    assert len(client_cf.posts) == 2

    # 2. Raw BrokenPipeError / ConnectionResetError on upload triggers fallback & saves message_id
    for err_cls in (BrokenPipeError, ConnectionResetError):
        sub = tmp_path / f"os_{err_cls.__name__}"
        out_os = _make_out_dir(
            sub,
            [{"index": 1, "clip_id": f"os_{err_cls.__name__}", "file": "c1.mp4", "title": "OS", "duration": 20.0}],
        )

        class OsErrClient(FakeDiscordClient):
            def post(self, channel_id, content, file=None, components=None):
                if file is not None:
                    raise err_cls("socket closed during large upload")
                return super().post(channel_id, content, file=None, components=components)

        c_os = OsErrClient()
        notify_discord(out_os, policy=ChannelPolicy(), discord_client=c_os, preview_fn=lambda s, d, c: s, out_root=sub)
        man_os = json.loads((out_os / "clips.json").read_text())
        assert man_os["clips"][0]["cabe_discord"] is False
        assert man_os["clips"][0]["discord"]["message_id"] == "msg-2"
        assert c_os.posts[-1]["file"] is None

        # Second run makes 0 posts
        notify_discord(out_os, policy=ChannelPolicy(), discord_client=c_os, preview_fn=lambda s, d, c: s, out_root=sub)
        assert len(c_os.posts) == 2

    # 3. DiscordRest wrapping BrokenPipeError from urlopen also triggers attachment fallback
    sub_rest = tmp_path / "rest_broken"
    out_rest = _make_out_dir(
        sub_rest,
        [{"index": 1, "clip_id": "rest_c01", "file": "c1.mp4", "title": "Rest", "duration": 20.0}],
    )
    rest_calls: list[bool] = []

    class FakeHttpResp:
        def __init__(self, body: dict[str, Any]):
            self.status = 200
            self._raw = json.dumps(body).encode("utf-8")
            self.headers = {}

        def read(self) -> bytes:
            return self._raw

        def getcode(self) -> int:
            return 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        ctype = req.get_header("Content-type") or ""
        is_multipart = ctype.startswith("multipart/form-data")
        rest_calls.append(is_multipart)
        if is_multipart:
            raise BrokenPipeError(f"broken pipe token={FAKE_TOKEN}")
        if req.full_url.endswith("/threads"):
            return FakeHttpResp({"id": "thr-rest"})
        return FakeHttpResp({"id": f"msg-rest-{len(rest_calls)}"})

    rest_client = DiscordRest(FAKE_TOKEN, urlopen=fake_urlopen, sleep=lambda _: None)
    notify_discord(
        out_rest,
        policy=ChannelPolicy(),
        discord_client=rest_client,
        preview_fn=lambda s, d, c: s,
        out_root=sub_rest,
    )
    man_rest = json.loads((out_rest / "clips.json").read_text())
    assert man_rest["clips"][0]["cabe_discord"] is False
    assert man_rest["clips"][0]["discord"]["message_id"] == "msg-rest-4"
    assert rest_calls == [False, False, True, False]


# --- T15: thread/cartao por live_id, cartao apagado (404), encerrada, mensagem enxuta e #bot-log ---


def test_live_id_thread_unica_edita_cartao_404_e_encerra_no_release(tmp_path, monkeypatch):
    import mongomock
    from clipes.coord import Coord
    from clipes.discord_rest import DiscordRestError

    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)

    class EditableDiscordClient(FakeDiscordClient):
        def __init__(self) -> None:
            super().__init__()
            self.edits: list[dict[str, Any]] = []
            self.fail_edit_404 = False

        def edit(self, channel_id, message_id, content, components=None):
            if self.fail_edit_404:
                raise DiscordRestError("Discord HTTP 404: code=10008 Unknown Message", status=404, code=10008)
            self.edits.append({
                "channel_id": str(channel_id),
                "message_id": str(message_id),
                "content": content,
                "components": components,
            })
            return {"id": str(message_id), "content": content}

    db = mongomock.MongoClient().clipes_lives
    coord = Coord(db, "w-pc1", now=lambda: 1_763_400_000.0)
    live_id = "twitch:gaules:999001"
    coord.ensure_live(live_id, channel="gaules", platform="twitch")
    assert coord.claim_live(live_id) is True

    client = EditableDiscordClient()
    long_title = "Major de CS2 ao vivo com muita emocao e playoffs decisivos hoje a noite inteira"
    created_ts = 1_763_400_000.0  # 2025-11-17 UTC

    # Janela 1 (2 clipes) no PC-1 -> cria 1 cartao (🔴 ao vivo) + 1 thread e grava em lives.discord
    w1_dir = _make_out_dir(
        tmp_path / "w1",
        [
            {"index": 1, "clip_id": "w1_c01", "file": "c1.mp4", "title": "Clutch 1", "hook_top": "INSANO", "llm_score": 9.1, "title_source": "gemini", "caption": "Legenda removida", "hashtags": ["#cs2"], "description_long": "Descricao longa 1", "duration": 25.0},
            {"index": 2, "clip_id": "w1_c02", "file": "c2.mp4", "title": "Clutch 2", "duration": 20.0},
        ],
    )
    w1_man = json.loads((w1_dir / "clips.json").read_text(encoding="utf-8"))
    w1_man["title"] = long_title
    (w1_dir / "clips.json").write_text(json.dumps(w1_man, ensure_ascii=False), encoding="utf-8")
    notify_discord(
        w1_dir,
        policy=ChannelPolicy(),
        job={"job_id": "j1", "live_id": live_id, "channel": "gaules", "title": long_title, "url": "https://www.twitch.tv/gaules", "created_at": created_ts},
        discord_client=client,
        preview_fn=lambda s, d, c: s,
        out_root=tmp_path / "w1",
        mongo_db=db,
    )

    assert len(client.threads) == 1
    assert client.threads[0]["name"] == f"gaules · 2025-11-17 · {long_title[:60]}"
    assert len(client.posts) == 3
    header_card = client.posts[0]["content"]
    assert header_card == f"**gaules** — {long_title}\nClipes: 2\n🔴 ao vivo\nhttps://www.twitch.tv/gaules"

    # Verifica mensagem enxuta na ordem exata e sem linhas proibidas (ORG-02 AC 1)
    clip_msg = client.posts[1]["content"]
    assert clip_msg.startswith("🟡 pendente — **Clutch 1**\n")
    pos_estado = clip_msg.index("🟡 pendente")
    pos_title = clip_msg.index("Clutch 1")
    pos_id = clip_msg.index("`w1_c01`")
    pos_hook = clip_msg.index("Gancho: INSANO")
    pos_nota = clip_msg.index("Nota: 9.1 (gemini)")
    pos_desc = clip_msg.index("Descricao: Descricao longa 1")
    assert pos_estado < pos_title < pos_id < pos_hook < pos_nota < pos_desc
    assert "Legenda:" not in clip_msg
    assert "Legenda removida" not in clip_msg
    assert "#cs2" not in clip_msg
    assert "publicar w1_c01" not in clip_msg
    assert "Policy:" not in clip_msg  # liberado nao polui

    live_doc = db.lives.find_one({"_id": live_id})
    assert live_doc["discord"]["thread_id"] == "thr-1"
    assert live_doc["discord"]["header_message_id"] == "msg-1"
    assert live_doc["discord"]["clipes"] == 2

    # Janela 2 (1 clipe) de outro PC (outro diretorio, sem clips.json anterior) -> reutiliza thr-1 e edita cartao para Clipes: 3
    w2_dir = _make_out_dir(
        tmp_path / "w2",
        [{"index": 1, "clip_id": "w2_c01", "file": "c3.mp4", "title": "Clutch 3", "duration": 22.0}],
    )
    notify_discord(
        w2_dir,
        policy=ChannelPolicy(),
        job={"job_id": "j2", "live_id": live_id, "channel": "gaules", "url": "https://www.twitch.tv/gaules", "created_at": created_ts + 300},
        discord_client=client,
        preview_fn=lambda s, d, c: s,
        out_root=tmp_path / "w2",
        mongo_db=db,
    )
    assert len(client.threads) == 1  # nenhuma thread nova
    assert client.posts[-1]["channel_id"] == "thr-1"
    assert len(client.edits) == 1
    assert client.edits[0]["channel_id"] == FAKE_CHANNEL
    assert client.edits[0]["message_id"] == "msg-1"
    assert "Clipes: 3" in client.edits[0]["content"]
    assert "🔴 ao vivo" in client.edits[0]["content"]
    assert db.lives.find_one({"_id": live_id})["discord"]["clipes"] == 3

    # Janela 3 com cartao original apagado (404 no edit) -> posta novo cartao na mesma thread e atualiza header_message_id
    client.fail_edit_404 = True
    w3_dir = _make_out_dir(
        tmp_path / "w3",
        [{"index": 1, "clip_id": "w3_c01", "file": "c4.mp4", "title": "Clutch 4", "duration": 22.0}],
    )
    notify_discord(
        w3_dir,
        policy=ChannelPolicy(),
        job={"job_id": "j3", "live_id": live_id, "channel": "gaules", "url": "https://www.twitch.tv/gaules", "created_at": created_ts + 600},
        discord_client=client,
        preview_fn=lambda s, d, c: s,
        out_root=tmp_path / "w3",
        mongo_db=db,
    )
    assert len(client.threads) == 1
    new_card_post = client.posts[-1]
    assert new_card_post["channel_id"] == "thr-1"
    assert "Clipes: 4" in new_card_post["content"]
    live_doc3 = db.lives.find_one({"_id": live_id})
    assert live_doc3["discord"]["header_message_id"] == new_card_post["id"]
    assert live_doc3["discord"]["clipes"] == 4

    # Encerramento da live via Coord.release(live_id, ended=True) edita o novo cartao para ✅ encerrada
    client.fail_edit_404 = False
    coord.release(live_id, True, discord_client=client)
    assert len(client.edits) == 2
    assert client.edits[-1]["channel_id"] == "thr-1"
    assert client.edits[-1]["message_id"] == new_card_post["id"]
    assert "✅ encerrada" in client.edits[-1]["content"]
    assert "🔴 ao vivo" not in client.edits[-1]["content"]
    assert "Clipes: 4" in client.edits[-1]["content"]

    # Janela que chega depois do encerramento (ainda na fila) reusa o cartao, mas ele continua encerrado
    w4_dir = _make_out_dir(
        tmp_path / "w4",
        [{"index": 1, "clip_id": "w4_c01", "file": "c5.mp4", "title": "Clutch 5", "duration": 22.0}],
    )
    notify_discord(
        w4_dir,
        policy=ChannelPolicy(),
        job={"job_id": "j4", "live_id": live_id, "channel": "gaules", "url": "https://www.twitch.tv/gaules", "created_at": created_ts + 900},
        discord_client=client,
        preview_fn=lambda s, d, c: s,
        out_root=tmp_path / "w4",
        mongo_db=db,
    )
    assert len(client.edits) == 3
    assert "✅ encerrada" in client.edits[-1]["content"]
    assert "🔴 ao vivo" not in client.edits[-1]["content"]
    assert "Clipes: 5" in client.edits[-1]["content"]


def test_post_aviso_usa_discord_log_channel_id(aviso_env, monkeypatch):
    client = FakeDiscordClient()
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    monkeypatch.setenv("DISCORD_LOG_CHANNEL_ID", "777777777")
    post_aviso("aviso no log", discord_client=client)
    assert client.posts[-1]["channel_id"] == "777777777"


def _clip_grande(tmp_path):
    return _make_out_dir(
        tmp_path,
        [{"index": 1, "clip_id": "big_c01", "file": "c1.mp4", "title": "Big", "score": 0.9,
          "duration": 20.0, "cabe_discord": False}],
    )


def test_modo_discord_cabe_false_ainda_chama_preview_fn(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    monkeypatch.setenv("CLIPES_MASTER_MODE", "discord")
    chamadas = []
    client = FakeDiscordClient()
    notify_discord(
        _clip_grande(tmp_path), policy=ChannelPolicy(), discord_client=client,
        preview_fn=lambda s, d, c: chamadas.append(s) or s, out_root=tmp_path / "run",
    )
    assert len(chamadas) == 1
    assert client.posts[-1]["file"] is not None


def test_preview_none_posta_aviso_com_clip_id(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    avisos = []
    monkeypatch.setattr("clipes.notificar.post_aviso", lambda t, **k: avisos.append(t))
    notify_discord(
        _clip_grande(tmp_path), policy=ChannelPolicy(), discord_client=FakeDiscordClient(),
        preview_fn=lambda s, d, c: None, out_root=tmp_path / "run",
    )
    assert len(avisos) == 1
    assert "prévia não coube no Discord: big_c01" in avisos[0]
    assert "limite" in avisos[0]


def test_anexo_recusado_posta_aviso(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    avisos = []
    monkeypatch.setattr("clipes.notificar.post_aviso", lambda t, **k: avisos.append(t))

    class Recusa(FakeDiscordClient):
        def post(self, channel_id, content, file=None, components=None):
            if file is not None:
                return {"id": "x", "attachments": []}
            return super().post(channel_id, content, file, components)

    notify_discord(
        _clip_grande(tmp_path), policy=ChannelPolicy(), discord_client=Recusa(),
        preview_fn=lambda s, d, c: s, out_root=tmp_path / "run",
    )
    assert len(avisos) == 1 and "big_c01" in avisos[0]


def _post_clip_gate(tmp_path, monkeypatch, gate: dict[str, Any] | None) -> FakeDiscordClient:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    clip: dict[str, Any] = {"index": 1, "clip_id": "vid1_c01", "file": "c1.mp4", "title": "Clip 1", "score": 0.9, "duration": 30.0}
    if gate is not None:
        clip["gate"] = gate
    out_dir = _make_out_dir(tmp_path, [clip])
    client = FakeDiscordClient()
    notify_discord(
        out_dir,
        policy=ChannelPolicy(),
        discord_client=client,
        preview_fn=lambda src, dur, cache_dir: src,
    )
    return client


def test_gate_reprovado_mostra_motivos_e_sem_botoes(tmp_path, monkeypatch):
    gate = {"ok": False, "motivos": ["duracao_fora", "caption_url"], "versao": 1}
    client = _post_clip_gate(tmp_path, monkeypatch, gate)

    clip_post = client.posts[1]
    assert "Bloqueado: duracao_fora, caption_url" in clip_post["content"]
    assert clip_post["components"] is None


def test_gate_aprovado_mantem_botoes_sem_linha_bloqueado(tmp_path, monkeypatch):
    gate = {"ok": True, "motivos": [], "versao": 1}
    client = _post_clip_gate(tmp_path, monkeypatch, gate)

    clip_post = client.posts[1]
    assert "Bloqueado" not in clip_post["content"]
    assert clip_post["components"] is not None
    assert len(clip_post["components"]) == 2


def test_sem_gate_igual_a_hoje(tmp_path, monkeypatch):
    client = _post_clip_gate(tmp_path, monkeypatch, None)

    clip_post = client.posts[1]
    assert "Bloqueado" not in clip_post["content"]
    assert clip_post["components"] is not None
