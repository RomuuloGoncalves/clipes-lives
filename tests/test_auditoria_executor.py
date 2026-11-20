"""Auditoria (onda 3, W3-C): A-05 (PublishLoop em acompanhar e bot local), A-04 (filtro clips.json/notificar) e M-16 (max_dur/min_dur)."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import mongomock
import pymongo.errors

from clipes import acompanhar, bot as bot_mod, cli, monitor, publicacao
from clipes.coord import Coord
from clipes.notificar import notify_discord
from clipes.publicacao import LocalStore
from clipes.registro import Registro
from test_acompanhar import B, Clock, FakeMultiPool
from test_notificar import FAKE_CHANNEL, FAKE_TOKEN, FakeDiscordClient


def _write_clip_bundle(out_root: Path, clip_id: str, *, owner: str = "w-acomp") -> Path:
    clip_dir = out_root / f"2026-11-18 - Live [{clip_id}]"
    clip_dir.mkdir(parents=True, exist_ok=True)
    (clip_dir / f"{clip_id}.mp4").write_bytes(b"fake-mp4-data")
    manifest = {
        "video_id": clip_id,
        "title": f"Live {clip_id}",
        "channel": "gaules",
        "owner": owner,
        "created_at": B - 3600,
        "clips": [
            {
                "index": 1,
                "clip_id": clip_id,
                "file": f"{clip_id}.mp4",
                "title": f"Titulo {clip_id}",
                "caption": "Legenda",
                "hashtags": ["#live"],
                "discord": {"thread_id": "thr-1", "message_id": "msg-1"},
            }
        ],
    }
    (clip_dir / "clips.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return clip_dir


def _write_tokens(path: Path) -> Path:
    path.write_text(json.dumps({"youtube": {"refresh_token": "yt-refresh-tok"}}), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


# ---- A-05: PublishLoop em acompanhar (Supervisor.run) ---------------------------------------------


def test_a05_acompanhar_executa_run_pending_com_coord_e_para_no_finally(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-acomp-pub", now=clock.now, lease_s=90)
    pool = FakeMultiPool(clock, {"gaules": B + 320})

    phase = ["running"]
    calls: list[dict[str, Any]] = []
    calls_after = [0]
    called_ev = threading.Event()

    def spy_run_pending(store_or_coord, wid, **kwargs):
        if phase[0] == "after":
            calls_after[0] += 1
            return []
        calls.append({
            "coord": store_or_coord,
            "worker_id": wid,
            "thread": threading.current_thread(),
            "kwargs": kwargs,
        })
        called_ev.set()
        return []

    slept_once = [False]

    def slow_is_live(url: str) -> bool:
        if not slept_once[0] and clock.t >= B + 70:
            slept_once[0] = True
            called_ev.wait(timeout=2.0)
            time.sleep(0.08)
        return clock.t < B + 320

    fake_discord = FakeDiscordClient()
    policy = monitor.ChannelPolicy(delay_h={"gaules": 2.0})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="501",
        is_live=slow_is_live,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        policy=policy,
        beat_s=0.03,
        run_pending_fn=spy_run_pending,
        discord_client=fake_discord,
    )
    res = sup.run()

    phase[0] = "after"
    time.sleep(0.1)

    assert res["status"] == "ended"
    assert len(calls) >= 1
    assert calls[0]["coord"] is coord
    assert calls[0]["worker_id"] == "w-acomp-pub"
    assert calls[0]["thread"] is not threading.main_thread()
    assert calls[0]["kwargs"]["out_root"] == tmp_path / "out"
    assert calls[0]["kwargs"]["policy"] is policy
    assert callable(calls[0]["kwargs"]["discord_post"])
    assert calls_after[0] == 0


def test_a05_acompanhar_erro_mongo_em_run_pending_chama_on_mongo_err(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-acomp-merr", now=clock.now, lease_s=90)
    pool = FakeMultiPool(clock, {"gaules": B + 320})

    err_hit = threading.Event()
    fail_once = [True]

    def failing_run_pending(*args, **kwargs):
        if fail_once[0]:
            fail_once[0] = False
            err_hit.set()
            raise pymongo.errors.ServerSelectionTimeoutError("mongo fora em pub")
        return []

    waited = [False]

    def is_live_wait(url: str) -> bool:
        if not waited[0] and clock.t >= B + 70:
            waited[0] = True
            err_hit.wait(timeout=2.0)
            time.sleep(0.05)
        return clock.t < B + 320

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="502",
        is_live=is_live_wait,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=0.03,
        run_pending_fn=failing_run_pending,
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert err_hit.is_set()
    assert sup._mongo_offline_until > float("-inf")


def test_a05_acompanhar_sem_coord_nao_dispara_run_pending(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    pool = FakeMultiPool(clock, {"gaules": B + 320})
    calls: list[int] = []

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=None,
        is_live=lambda u: clock.t < B + 320,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        beat_s=0.02,
        run_pending_fn=lambda *a, **kw: calls.append(1),
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert calls == []


def test_a05_acompanhar_processa_pedido_pendente_real_do_mongo(tmp_path, monkeypatch):
    """Prova ponta a ponta: pedido em db.publish_requests vira `ok` durante Supervisor.run()."""
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    out_root = tmp_path / "out"
    _write_clip_bundle(out_root, "pub1_c01", owner="w-acomp-real")
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    quota_path = tmp_path / "quota.json"

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-acomp-real", now=clock.now, lease_s=90)
    db.publish_requests.insert_one({
        "_id": "pub1_c01:youtube",
        "clip_id": "pub1_c01",
        "plataforma": "youtube",
        "owner": "w-acomp-real",
        "status": "pending",
        "thread_id": "thr-1",
        "solicitado_por": "111",
        "criado_em": datetime.fromtimestamp(B, timezone.utc),
    })

    done_ev = threading.Event()
    uploaded: list[str] = []

    def real_run_pending_with_fakes(c, wid, **kwargs):
        res = publicacao.run_pending(
            c,
            wid,
            out_root=kwargs["out_root"],
            now=kwargs["now"],
            policy=kwargs["policy"],
            tokens_path=tokens_path,
            quota_path=quota_path,
            refreshers={"youtube": lambda _plat, _tok: {"access_token": "acc-1"}},
            publishers={
                "youtube": lambda mp4, title, desc, tok: (
                    uploaded.append(Path(mp4).name),
                    {"video_id": "yt123", "url": "https://youtu.be/yt123"},
                )[1]
            },
            discord_post=kwargs.get("discord_post"),
        )
        if res:
            done_ev.set()
        return res

    waited = [False]

    def is_live_wait(url: str) -> bool:
        if not waited[0] and clock.t >= B + 70:
            waited[0] = True
            done_ev.wait(timeout=3.0)
        return clock.t < B + 320

    pool = FakeMultiPool(clock, {"gaules": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        out_root,
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="503",
        is_live=is_live_wait,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=0.03,
        run_pending_fn=real_run_pending_with_fakes,
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert uploaded == ["pub1_c01.mp4"]
    req_doc = db.publish_requests.find_one({"_id": "pub1_c01:youtube"})
    assert req_doc is not None and req_doc["status"] == "ok"


# ---- A-05: executor periodico no bot em modo local ------------------------------------------------


def test_a05_bot_local_executa_pedido_agendado_periodicamente_e_para_no_close(tmp_path):
    t_now = [float(B)]
    out_root = tmp_path / "out"
    _write_clip_bundle(out_root, "ag1_c01", owner="local")
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    quota_path = tmp_path / "quota.json"
    store = LocalStore(out_root)

    # Enfileira pedido agendado para o futuro (B + 1800) sem clicar em nada depois
    store.enqueue_request({
        "_id": "ag1_c01:youtube",
        "clip_id": "ag1_c01",
        "plataforma": "youtube",
        "owner": None,
        "status": "pending",
        "thread_id": "thr-1",
        "solicitado_por": "111",
        "criado_em": datetime.fromtimestamp(B, timezone.utc).isoformat(),
        "agendar_para": datetime.fromtimestamp(B + 1800, timezone.utc).isoformat(),
    })

    calls = [0]
    calls_after_close = [0]
    closed_flag = [False]
    published = threading.Event()
    uploaded: list[str] = []

    def run_pending_local(st, wid, **kwargs):
        if closed_flag[0]:
            calls_after_close[0] += 1
            return []
        calls[0] += 1
        res = publicacao.run_pending(
            st,
            wid,
            out_root=kwargs["out_root"],
            now=kwargs["now"],
            tokens_path=kwargs["tokens_path"],
            quota_path=kwargs["quota_path"],
            refreshers={"youtube": lambda _plat, _tok: {"access_token": "acc-local"}},
            publishers={
                "youtube": lambda mp4, title, desc, tok: (
                    uploaded.append(Path(mp4).name),
                    {"video_id": "yt-local-1", "url": "https://youtu.be/yt-local-1"},
                )[1]
            },
            discord_post=lambda _tid, _msg: None,
        )
        if res:
            published.set()
        return res

    bot = bot_mod.make_bot(
        store,
        allowed_ids=["111"],
        channel_id=123456,
        guild_id=654321,
        out_root=out_root,
        tokens_path=tokens_path,
        quota_path=quota_path,
        now=lambda: t_now[0],
        run_local=True,
        run_pending_fn=run_pending_local,
        local_poll_s=0.02,
    )
    bot.tree.copy_global_to = lambda *, guild: None  # type: ignore[method-assign]

    async def fake_sync(*, guild: Any = None) -> list[Any]:
        return []

    bot.tree.sync = fake_sync  # type: ignore[method-assign]

    async def driver() -> None:
        await bot.setup_hook()
        try:
            # Antes do horario agendado: roda o loop mas ainda nao publica
            await asyncio.sleep(0.08)
            assert calls[0] >= 1
            assert uploaded == []
            assert store.get_request("ag1_c01", "youtube")["status"] == "pending"

            # Avanca o relogio para depois do horario agendado -> publica sem nenhum clique
            t_now[0] = float(B + 1805)
            deadline = time.monotonic() + 3.0
            while not published.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert published.is_set()
        finally:
            await bot.close()

        closed_flag[0] = True
        await asyncio.sleep(0.08)

    asyncio.run(driver())

    assert uploaded == ["ag1_c01.mp4"]
    assert store.get_request("ag1_c01", "youtube")["status"] == "ok"
    assert calls_after_close[0] == 0


def test_a05_bot_local_loop_periodico_nao_bloqueia_event_loop(tmp_path):
    store = LocalStore(tmp_path)
    started = threading.Event()
    release = threading.Event()
    threads_seen: list[threading.Thread] = []

    def slow_run_pending(*args, **kwargs):
        threads_seen.append(threading.current_thread())
        started.set()
        release.wait(timeout=5.0)
        return []

    bot = bot_mod.make_bot(
        store,
        allowed_ids=["111"],
        channel_id=123456,
        guild_id=None,
        out_root=tmp_path,
        run_local=True,
        run_pending_fn=slow_run_pending,
        local_poll_s=0.01,
    )

    async def fake_sync(*, guild: Any = None) -> list[Any]:
        return []

    bot.tree.sync = fake_sync  # type: ignore[method-assign]

    async def driver() -> int:
        await bot.setup_hook()
        ticks_while_blocked = 0
        try:
            deadline = time.monotonic() + 2.0
            while not started.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            assert started.is_set()
            # Event loop segue livre enquanto run_pending esta travado na thread
            for _ in range(5):
                await asyncio.sleep(0.01)
                ticks_while_blocked += 1
        finally:
            release.set()
            await bot.close()
        return ticks_while_blocked

    ticks = asyncio.run(driver())
    assert ticks == 5
    assert threads_seen and threads_seen[0] is not threading.main_thread()


def test_a05_bot_com_mongo_nao_inicia_loop_periodico_local(tmp_path):
    store = LocalStore(tmp_path)
    calls: list[int] = []
    bot = bot_mod.make_bot(
        store,
        allowed_ids=["111"],
        channel_id=123456,
        guild_id=None,
        out_root=tmp_path,
        run_local=False,
        run_pending_fn=lambda *a, **kw: calls.append(1),
        local_poll_s=0.01,
    )

    async def fake_sync(*, guild: Any = None) -> list[Any]:
        return []

    bot.tree.sync = fake_sync  # type: ignore[method-assign]

    async def driver() -> None:
        await bot.setup_hook()
        await asyncio.sleep(0.06)
        await bot.close()

    asyncio.run(driver())
    assert calls == []


# ---- A-04: filtro owns/filter_emitted reescreve clips.json e filtra Discord + Registro ------------


def test_a04_livesession_pipeline_reescreve_clips_json_e_discord_registro_recebem_so_filtrados(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)

    clock = Clock(B + 310)
    out_root = tmp_path / "out"
    out_root.mkdir()
    queue = monitor.DiskQueue(out_root / "queue")
    pool = FakeMultiPool(clock, {"gaules": B + 900})
    alvo = acompanhar.parse_alvo("gaules")

    # Janela [B-300, B+300], step=300, pad=30 -> regiao dona: (B-30, B+270]
    e = float(B + 300)
    vs = float(B - 300)
    clip_dir = out_root / "2026-11-18 - gaules [gaules_w1]"
    clip_dir.mkdir(parents=True)

    # 3 clipes produzidos pelo pipeline interno:
    # 1) c_fora: centro em B+290 (FORA da regiao dona (B-30, B+270])
    # 2) c_duplicado: centro em B+100 (JA emitido anteriormente)
    # 3) c_dono: centro em B+200 (DENTRO da regiao dona e NAO emitido)
    all_clips = [
        {
            "index": 1,
            "clip_id": "w1_c01_fora",
            "file": "fora.mp4",
            "title": "Clipe Fora da Regiao",
            "score": 0.95,
            "start": (B + 290) - 10 - vs,
            "end": (B + 290) + 10 - vs,
            "duration": 20.0,
        },
        {
            "index": 2,
            "clip_id": "w1_c02_dup",
            "file": "dup.mp4",
            "title": "Clipe Ja Emitido",
            "score": 0.90,
            "start": (B + 100) - 15 - vs,
            "end": (B + 100) + 15 - vs,
            "duration": 30.0,
        },
        {
            "index": 3,
            "clip_id": "w1_c03_dono",
            "file": "dono.mp4",
            "title": "Clipe Dono Valido",
            "score": 0.85,
            "start": (B + 200) - 15 - vs,
            "end": (B + 200) + 15 - vs,
            "duration": 30.0,
        },
    ]
    for c in all_clips:
        (clip_dir / c["file"]).write_bytes(b"mp4bytes")

    raw_manifest = {
        "video_id": "gaules_w1",
        "title": "gaules - live",
        "url": "https://www.twitch.tv/gaules",
        "channel_credit": "twitch.tv/gaules",
        "timings": {"total_s": 1.2},
        "clips": all_clips,
    }

    def raw_pipeline(job_dir: Path, root: Path) -> dict[str, Any]:
        (clip_dir / "clips.json").write_text(
            json.dumps(raw_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {**raw_manifest, "output_dir": str(clip_dir)}

    sess = acompanhar.LiveSession(
        alvo,
        out_root,
        queue,
        pool,
        raw_pipeline,
        is_live=lambda _: True,
        now=clock.now,
        disk_usage=lambda _: SimpleNamespace(free=100 * 1e9),
        step=300,
        max_dur=60.0,
    )
    # Marca o intervalo de c_duplicado como ja emitido numa janela anterior
    sess.emitted_path.write_text(
        json.dumps([[float(B + 85), float(B + 115)]]),
        encoding="utf-8",
    )
    pool.start_on_demand("gaules")
    pool.first["gaules"] = int(vs)
    sess.fechar_janela(e, last=False)

    fake_discord = FakeDiscordClient()
    ran = monitor.run_worker_step(
        queue,
        out_root,
        monitor.ChannelPolicy(),
        sess.pipeline,
        discord_client=fake_discord,
        preview_fn=lambda src, dur, cache_dir: src,
    )
    assert ran is True

    # 1) clips.json reescrito atomicamente so com o clipe dono, preservando o formato do manifesto
    written = json.loads((clip_dir / "clips.json").read_text(encoding="utf-8"))
    assert [c["clip_id"] for c in written["clips"]] == ["w1_c03_dono"]
    assert written["video_id"] == "gaules_w1"
    assert written["title"] == "gaules - live"
    assert written["timings"] == {"total_s": 1.2}
    assert not (clip_dir / ".clips.json.tmp").exists()

    # 2) Discord recebeu cabecalho (Clipes: 1) + 1 unica postagem (so w1_c03_dono)
    assert len(fake_discord.threads) == 1
    assert len(fake_discord.posts) == 2
    assert "Clipes: 1" in fake_discord.posts[0]["content"]
    assert "w1_c03_dono" in fake_discord.posts[1]["content"]
    all_posted = "\n".join(p["content"] for p in fake_discord.posts)
    assert "w1_c01_fora" not in all_posted
    assert "w1_c02_dup" not in all_posted

    # 3) Registro recebeu somente o clipe dono
    reg = Registro(out_root)
    assert reg.get("w1_c03_dono") is not None
    assert reg.get("w1_c01_fora") is None
    assert reg.get("w1_c02_dup") is None


def test_a04_todos_clipes_filtrados_reescreve_clips_json_vazio_e_nao_notifica(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)

    clock = Clock(B + 310)
    out_root = tmp_path / "out"
    out_root.mkdir()
    queue = monitor.DiskQueue(out_root / "queue")
    pool = FakeMultiPool(clock, {"gaules": B + 900})
    alvo = acompanhar.parse_alvo("gaules")

    e = float(B + 300)
    vs = float(B - 300)
    clip_dir = out_root / "2026-11-18 - gaules [gaules_w2]"
    clip_dir.mkdir(parents=True)
    (clip_dir / "fora.mp4").write_bytes(b"mp4bytes")

    raw_manifest = {
        "video_id": "gaules_w2",
        "title": "gaules - live",
        "clips": [
            {
                "index": 1,
                "clip_id": "w2_c01_fora",
                "file": "fora.mp4",
                "title": "Fora",
                "score": 0.9,
                "start": (B + 290) - 10 - vs,
                "end": (B + 290) + 10 - vs,
                "duration": 20.0,
            }
        ],
    }

    def raw_pipeline(job_dir: Path, root: Path) -> dict[str, Any]:
        (clip_dir / "clips.json").write_text(json.dumps(raw_manifest), encoding="utf-8")
        return {**raw_manifest, "output_dir": str(clip_dir)}

    sess = acompanhar.LiveSession(
        alvo,
        out_root,
        queue,
        pool,
        raw_pipeline,
        is_live=lambda _: True,
        now=clock.now,
        disk_usage=lambda _: SimpleNamespace(free=100 * 1e9),
        step=300,
        max_dur=60.0,
    )
    pool.start_on_demand("gaules")
    pool.first["gaules"] = int(vs)
    sess.fechar_janela(e, last=False)

    fake_discord = FakeDiscordClient()
    assert monitor.run_worker_step(
        queue,
        out_root,
        monitor.ChannelPolicy(),
        sess.pipeline,
        discord_client=fake_discord,
        preview_fn=lambda src, dur, cache_dir: src,
    ) is True

    written = json.loads((clip_dir / "clips.json").read_text(encoding="utf-8"))
    assert written["clips"] == []
    assert fake_discord.posts == []
    assert Registro(out_root).get("w2_c01_fora") is None


def test_a04_notify_discord_prioriza_result_clips_e_mantem_idempotencia(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)

    out_root = tmp_path / "out"
    out_dir = out_root / "vid_n1"
    out_dir.mkdir(parents=True)
    (out_dir / "c1.mp4").write_bytes(b"mp4-1")
    (out_dir / "c2.mp4").write_bytes(b"mp4-2")

    c1 = {"index": 1, "clip_id": "n1_c01", "file": "c1.mp4", "title": "Fora", "score": 0.9, "duration": 30.0}
    c2 = {"index": 2, "clip_id": "n1_c02", "file": "c2.mp4", "title": "Dono", "score": 0.8, "duration": 30.0}
    (out_dir / "clips.json").write_text(
        json.dumps({"video_id": "n1", "title": "Live N1", "clips": [c1, c2]}),
        encoding="utf-8",
    )

    client = FakeDiscordClient()
    filtered_result = {"video_id": "n1", "clips": [dict(c2)]}
    notify_discord(
        out_dir,
        policy=monitor.ChannelPolicy(),
        result=filtered_result,
        discord_client=client,
        preview_fn=lambda src, dur, cache_dir: src,
        out_root=out_root,
    )

    assert len(client.posts) == 2
    assert "n1_c02" in client.posts[1]["content"]
    assert "n1_c01" not in client.posts[1]["content"]
    reg = Registro(out_root)
    assert reg.get("n1_c02") is not None
    assert reg.get("n1_c01") is None

    # Segunda chamada com o mesmo result (sem campo 'discord') nao reposta
    notify_discord(
        out_dir,
        policy=monitor.ChannelPolicy(),
        result=filtered_result,
        discord_client=client,
        preview_fn=lambda src, dur, cache_dir: src,
        out_root=out_root,
    )
    assert len(client.posts) == 2


# ---- M-16: max_dur / min_dur gravados no job.json e repassados em run_local_pipeline --------------


def test_m16_fechar_janela_grava_max_dur_e_min_dur_no_job_json(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    clock = Clock(B + 310)
    out_root = tmp_path / "out"
    out_root.mkdir()
    queue = monitor.DiskQueue(out_root / "queue")
    pool = FakeMultiPool(clock, {"gaules": B + 900, "alanzoka": B + 900})

    # 1) Sessao com max_dur e min_dur explicitos
    sess_custom = acompanhar.LiveSession(
        acompanhar.parse_alvo("gaules"),
        out_root,
        queue,
        pool,
        lambda d, r: {"clips": []},
        is_live=lambda _: True,
        now=clock.now,
        disk_usage=lambda _: SimpleNamespace(free=100 * 1e9),
        max_dur=45.0,
        min_dur=15.0,
    )
    pool.start_on_demand("gaules")
    pool.first["gaules"] = B - 300
    sess_custom.fechar_janela(float(B + 300), last=False)
    job_custom = monitor._read_job(queue.root / "pending" / f"gaules_{B + 300}")
    assert job_custom["max_dur"] == 45.0
    assert job_custom["min_dur"] == 15.0

    # 2) Sessao com padrao (max_dur=60.0, sem min_dur)
    sess_def = acompanhar.LiveSession(
        acompanhar.parse_alvo("alanzoka"),
        out_root,
        queue,
        pool,
        lambda d, r: {"clips": []},
        is_live=lambda _: True,
        now=clock.now,
        disk_usage=lambda _: SimpleNamespace(free=100 * 1e9),
    )
    pool.start_on_demand("alanzoka")
    pool.first["alanzoka"] = B - 300
    sess_def.fechar_janela(float(B + 300), last=False)
    job_def = monitor._read_job(queue.root / "pending" / f"alanzoka_{B + 300}")
    assert job_def["max_dur"] == 45.0
    assert job_def["min_dur"] == 15.0


def test_m16_run_local_pipeline_repassa_max_dur_e_min_dur_e_respeita_job_antigo(tmp_path, monkeypatch):
    seen_args: list[Any] = []

    def fake_run_pipeline(args, ingest_fn=None):
        seen_args.append(args)
        ing = (ingest_fn or cli.ingest)(args.url, Path(args.out_dir), args.live_minutes, args.chat)
        (ing.out_dir / "clips.json").write_text(json.dumps({"video_id": ing.video_id, "clips": []}), encoding="utf-8")
        return 0

    def fake_ingest_local(job_dir: Path, out_root: Path, enable_chat: bool):
        od = out_root / job_dir.name
        od.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(video_id=job_dir.name, out_dir=od)

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr("clipes.ingest.ingest_local", fake_ingest_local)

    out_root = tmp_path / "out"

    # 1) Job com max_dur e min_dur
    j1 = tmp_path / "job_both"
    j1.mkdir()
    (j1 / "job.json").write_text(json.dumps({"job_id": "job_both", "max_dur": 45.0, "min_dur": 18.0}), encoding="utf-8")
    monitor.run_local_pipeline(j1, out_root)
    assert seen_args[-1].max_dur == 45.0
    assert seen_args[-1].min_dur == 18.0

    # 2) Job so com max_dur (padrao do acompanhar = 60s)
    j2 = tmp_path / "job_max_only"
    j2.mkdir()
    (j2 / "job.json").write_text(json.dumps({"job_id": "job_max_only", "max_dur": 60.0}), encoding="utf-8")
    monitor.run_local_pipeline(j2, out_root)
    assert seen_args[-1].max_dur == 60.0
    assert seen_args[-1].min_dur == 15.0  # padrao do CLI (CLIPES_DUR_MIN)

    # 3) Job antigo sem os campos -> comportamento atual (padrao do CLI: 65 / 25)
    j3 = tmp_path / "job_legacy"
    j3.mkdir()
    (j3 / "job.json").write_text(json.dumps({"job_id": "job_legacy", "channel": "gaules"}), encoding="utf-8")
    monitor.run_local_pipeline(j3, out_root)
    assert seen_args[-1].max_dur == 45.0
    assert seen_args[-1].min_dur == 15.0


def test_m16_acompanhar_main_repassa_max_dur_e_min_dur_ao_supervisor(tmp_path, monkeypatch):
    captured: dict[str, Any] = {}

    class StubSupervisor:
        def __init__(self, alvos, out_root, **kwargs):
            captured["alvos"] = alvos
            captured["kwargs"] = kwargs

        def run(self):
            return {"status": "ended", "jobs": []}

    monkeypatch.delenv("CLIPES_MONGO_URI", raising=False)
    monkeypatch.setattr(acompanhar, "Supervisor", StubSupervisor)

    rc = acompanhar.main(["gaules", "--out-dir", str(tmp_path), "--max-dur", "42", "--min-dur", "12"])
    assert rc == 0
    assert captured["kwargs"]["max_dur"] == 42.0
    assert captured["kwargs"]["min_dur"] == 12.0
