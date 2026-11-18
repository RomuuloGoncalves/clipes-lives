"""Tests for clipes.registro (REG-01, REG-02, REG-03, REG-07, REG-09)."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from typing import Any

import mongomock
import pytest
from pymongo.errors import ServerSelectionTimeoutError

from clipes.registro import VERSAO, Registro, sha256_arquivo

T0 = 1_795_000_000.0


def _mp_worker(out_root_str: str, worker_idx: int, count: int) -> None:
    reg = Registro(Path(out_root_str))
    for i in range(count):
        cid = f"w{worker_idx}_c{i:02d}"
        reg.upsert({
            "clip_id": cid,
            "titulo": f"Clipe {cid}",
            "hashtags": ["#gta6"],
            "legenda": f"Legenda {cid}",
            "descricao_longa": "Desc",
            "canal": "gaules",
            "origem_url": "https://twitch.tv/gaules",
            "video_id": f"v{worker_idx}",
            "criado_em": T0 + i,
            "arquivo": f"pasta/clip_{cid}.mp4",
            "tamanho": 1024 + i,
            "sha256": f"sha_{cid}",
            "cabe_discord": True,
        })


def _make_sample_rec(out_root: Path, clip_id: str = "vid1_c01", content: bytes = b"video-bytes") -> tuple[dict[str, Any], Path]:
    pasta = out_root / "2026-11-18 - Live [vid1]"
    pasta.mkdir(parents=True, exist_ok=True)
    mp4 = pasta / f"{clip_id}.mp4"
    mp4.write_bytes(content)
    rec = {
        "clip_id": clip_id,
        "titulo": "Jogada Insana no GTA",
        "hashtags": ["#gta6", "#rp"],
        "legenda": "Olha esse clipe incrível",
        "descricao_longa": "Descrição completa da jogada.",
        "canal": "@Gaules",
        "origem_url": "https://twitch.tv/gaules",
        "video_id": "vid1",
        "criado_em": T0,
        "ready_at": T0 + 3600.0,
        "owner": "w-pc1",
        "arquivo": mp4,
        "tamanho": mp4.stat().st_size,
        "sha256": sha256_arquivo(mp4),
        "cabe_discord": True,
    }
    return rec, mp4


def test_upsert_get_preserva_ack_e_rerender_zera_ack(tmp_path: Path):
    reg = Registro(tmp_path)
    rec, mp4 = _make_sample_rec(tmp_path)

    saved = reg.upsert(rec)
    assert saved is not None
    assert saved["schema"] == 1
    assert saved["status"] == "local"
    assert saved["canal"] == "gaules"
    assert saved["arquivo"] == "2026-11-18 - Live [vid1]/vid1_c01.mp4"
    assert saved["tamanho"] == len(b"video-bytes")
    assert saved["sha256"] == sha256_arquivo(mp4)
    assert saved["ready_at"] == T0 + 3600.0
    assert saved["owner"] == "w-pc1"

    reg.set_discord("vid1_c01", {
        "thread_id": "thr-1",
        "message_id": "msg-1",
        "attachment_id": "att-1",
        "filename": "vid1_c01.mp4",
        "size": len(b"video-bytes"),
        "url": "https://cdn.discordapp.com/attachments/1/2/vid1_c01.mp4?ex=abc&hm=xyz",
    })
    reg.marcar("vid1_c01", status="no_discord", ack_em=T0 + 10.0)
    reg.registrar_publicacao("vid1_c01", "youtube", {"id": "yt123", "url": "https://youtube.com/shorts/yt123", "em": T0 + 20.0})

    got = reg.get("vid1_c01")
    assert got is not None
    assert got["status"] == "no_discord"
    assert got["ack_em"] == T0 + 10.0
    assert "url" not in got["discord"]
    assert got["discord"] == {
        "thread_id": "thr-1",
        "message_id": "msg-1",
        "attachment_id": "att-1",
        "filename": "vid1_c01.mp4",
        "size": len(b"video-bytes"),
    }
    assert got["publicacoes"]["youtube"]["id"] == "yt123"

    # Upsert with same sha256 & tamanho preserves status, ack_em, discord, publicacoes
    again = reg.upsert(rec)
    assert again is not None
    assert again["status"] == "no_discord"
    assert again["ack_em"] == T0 + 10.0
    assert again["discord"]["message_id"] == "msg-1"
    assert again["publicacoes"]["youtube"]["id"] == "yt123"

    # Re-render (different content -> different sha256/tamanho) resets status/ack/discord/publicacoes
    mp4.write_bytes(b"new-rendered-video")
    rec["tamanho"] = mp4.stat().st_size
    rec["sha256"] = sha256_arquivo(mp4)
    rerendered = reg.upsert(rec)
    assert rerendered is not None
    assert rerendered["status"] == "local"
    assert rerendered["ack_em"] is None
    assert rerendered["discord"] is None
    assert rerendered["publicacoes"] == {}

    # Pre-F4 clip missing from registro does not raise KeyError on marcar/registrar_publicacao
    assert reg.marcar("nao_existe", status="perdido") is None
    assert reg.registrar_publicacao("nao_existe", "youtube", {"id": "x", "url": "u", "em": T0}) is None


def test_versao_maior_nao_sobrescreve_e_loga(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    payload = {"version": VERSAO + 1, "clips": {"futuro_c01": {"clip_id": "futuro_c01"}}}
    (tmp_path / "registro.json").write_text(json.dumps(payload), encoding="utf-8")

    reg = Registro(tmp_path)
    res = reg.upsert({"clip_id": "novo_c01", "titulo": "X", "arquivo": "a.mp4", "tamanho": 1, "sha256": "abc"})
    assert res is None
    out = capsys.readouterr().out
    assert f"[registro] versao {VERSAO + 1} desconhecida; registro nao alterado" in out

    raw = json.loads((tmp_path / "registro.json").read_text(encoding="utf-8"))
    assert raw == payload


def test_buscar_tokens_sem_acento_ordenado_limite_10(tmp_path: Path):
    reg = Registro(tmp_path)
    for i in range(15):
        reg.upsert({
            "clip_id": f"c_{i:02d}",
            "titulo": f"Ação Épica no Trailer {i}",
            "hashtags": ["#gta6"],
            "legenda": "História incrível de ação",
            "canal": "Gaules",
            "criado_em": T0 + i,
            "arquivo": f"p/c_{i:02d}.mp4",
            "tamanho": 100 + i,
            "sha256": f"sha_{i}",
        })
    reg.upsert({
        "clip_id": "outro",
        "titulo": "Só Valorante",
        "hashtags": ["#fps"],
        "legenda": "Nada a ver",
        "canal": "casimito",
        "criado_em": T0 + 100,
        "arquivo": "p/outro.mp4",
        "tamanho": 99,
        "sha256": "sha_outro",
    })

    hits = reg.buscar("acao trailer gta6 gaules")
    assert len(hits) == 10
    assert [h["clip_id"] for h in hits] == [f"c_{i:02d}" for i in range(14, 4, -1)]
    assert reg.buscar("acao inexistentetoken") == []


def test_dois_processos_multiprocessing_sem_perda(tmp_path: Path):
    ctx = multiprocessing.get_context("spawn")
    p1 = ctx.Process(target=_mp_worker, args=(str(tmp_path), 1, 15))
    p2 = ctx.Process(target=_mp_worker, args=(str(tmp_path), 2, 15))
    p1.start()
    p2.start()
    p1.join(timeout=15)
    p2.join(timeout=15)
    assert p1.exitcode == 0
    assert p2.exitcode == 0

    data = json.loads((tmp_path / "registro.json").read_text(encoding="utf-8"))
    assert len(data["clips"]) == 30


def test_mongo_upsert_falha_simulada_reenvio_e_busca(tmp_path: Path):
    client = mongomock.MongoClient()
    db = client.clipes_lives

    class FlakyClips:
        def __init__(self, real_col: Any) -> None:
            self.real = real_col
            self.fail = True

        def replace_one(self, *args: Any, **kwargs: Any) -> Any:
            if self.fail:
                raise ServerSelectionTimeoutError("mongo offline")
            return self.real.replace_one(*args, **kwargs)

        def find_one(self, *args: Any, **kwargs: Any) -> Any:
            if self.fail:
                raise ServerSelectionTimeoutError("mongo offline")
            return self.real.find_one(*args, **kwargs)

        def find(self, *args: Any, **kwargs: Any) -> Any:
            if self.fail:
                raise ServerSelectionTimeoutError("mongo offline")
            return self.real.find(*args, **kwargs)

    class FlakyDb:
        def __init__(self, real_db: Any) -> None:
            self.clips = FlakyClips(real_db.clips)

    flaky = FlakyDb(db)
    reg = Registro(tmp_path, mongo=flaky)

    rec1, _ = _make_sample_rec(tmp_path, clip_id="m_c01")
    saved1 = reg.upsert(rec1)
    assert saved1 is not None
    assert saved1["mongo_synced"] is False
    assert reg.last_mongo_error is True
    assert db.clips.count_documents({}) == 0

    # Mongo comes back online -> next write syncs pending m_c01 as well
    flaky.clips.fail = False
    rec2, _ = _make_sample_rec(tmp_path, clip_id="m_c02")
    saved2 = reg.upsert(rec2)
    assert saved2 is not None
    assert saved2["mongo_synced"] is True
    assert reg.last_mongo_error is False
    assert reg.get("m_c01")["mongo_synced"] is True
    assert db.clips.count_documents({}) == 2

    doc1 = db.clips.find_one({"_id": "m_c01"})
    assert doc1 is not None
    assert doc1["channel"] == "gaules"
    assert doc1["created_at"] == T0
    assert doc1["title"] == "Jogada Insana no GTA"
    assert doc1["caption"] == "Olha esse clipe incrível"
    assert doc1["description_long"] == "Descrição completa da jogada."
    assert doc1["owner"] == "w-pc1"

    # Another PC inserts a clip directly into db.clips; buscar finds it via Mongo
    db.clips.insert_one({
        "_id": "remote_c01",
        "clip_id": "remote_c01",
        "titulo": "Clipe Remoto Exclusivo",
        "hashtags": ["#gta6"],
        "legenda": "Veio de outro PC",
        "canal": "gaules",
        "criado_em": T0 + 500,
    })
    hits = reg.buscar("remoto exclusivo")
    assert [h["clip_id"] for h in hits] == ["remote_c01"]


def test_liberar_retencao_ocupados_e_protecao_out_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out_root = tmp_path / "out"
    out_root.mkdir()
    outside_file = tmp_path / "fora_de_out.mp4"
    outside_file.write_bytes(b"nao-apagar")

    reg = Registro(out_root)
    now_ts = T0 + 48 * 3600.0

    # 1. Old ACK (> 24h) -> should be deleted along with preview cache; txt/clips.json stay
    rec_old, mp4_old = _make_sample_rec(out_root, clip_id="old_c01", content=b"A" * (2 * 1024 * 1024))
    txt_old = mp4_old.with_suffix(".txt")
    txt_old.write_text("legenda", encoding="utf-8")
    clips_json = mp4_old.parent / "clips.json"
    clips_json.write_text("{}", encoding="utf-8")
    prev_old = mp4_old.parent / ".cache" / "discord_old_c01.mp4"
    prev_old.parent.mkdir(parents=True, exist_ok=True)
    prev_old.write_bytes(b"prev")
    reg.upsert(rec_old)
    reg.marcar("old_c01", status="no_discord", ack_em=now_ts - 30 * 3600.0)

    # 2. Recent ACK (< 24h) -> kept
    rec_new, mp4_new = _make_sample_rec(out_root, clip_id="new_c01", content=b"new")
    reg.upsert(rec_new)
    reg.marcar("new_c01", status="no_discord", ack_em=now_ts - 2 * 3600.0)

    # 3. No ACK -> kept
    rec_local, mp4_local = _make_sample_rec(out_root, clip_id="loc_c01", content=b"loc")
    reg.upsert(rec_local)

    # 4. Old ACK but busy (ocupado) -> kept
    rec_busy, mp4_busy = _make_sample_rec(out_root, clip_id="busy_c01", content=b"busy")
    reg.upsert(rec_busy)
    reg.marcar("busy_c01", status="no_discord", ack_em=now_ts - 30 * 3600.0)

    # 5. Old ACK pointing outside OUT_DIR -> refused
    reg.upsert({
        "clip_id": "escape_c01",
        "titulo": "Escape",
        "arquivo": "../fora_de_out.mp4",
        "tamanho": 10,
        "sha256": "abc",
    })
    reg.marcar("escape_c01", status="no_discord", ack_em=now_ts - 30 * 3600.0)

    # Empty or invalid retention does nothing
    assert reg.liberar("", ocupados={"busy_c01"}, now=now_ts) == []
    assert reg.liberar(None, ocupados={"busy_c01"}, now=now_ts) == []
    assert reg.liberar("invalido", ocupados={"busy_c01"}, now=now_ts) == []
    assert reg.liberar(-1, ocupados={"busy_c01"}, now=now_ts) == []
    assert mp4_old.is_file()

    freed = reg.liberar(24, ocupados={"busy_c01"}, now=now_ts)
    assert freed == ["old_c01"]
    assert not mp4_old.exists()
    assert not prev_old.exists()
    assert txt_old.is_file()
    assert clips_json.is_file()

    assert mp4_new.is_file()
    assert mp4_local.is_file()
    assert mp4_busy.is_file()
    assert outside_file.is_file()

    assert reg.get("old_c01")["status"] == "so_discord"
    out = capsys.readouterr().out
    assert "[registro] liberado old_c01 (" in out


def test_liberar_exige_sufixo_mp4_e_trata_oserror_no_unlink(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]):
    out_root = tmp_path / "out"
    out_root.mkdir()
    reg = Registro(out_root)
    now_ts = T0 + 48 * 3600.0

    # Adulterated arquivo pointing to registro.json or .txt must not be unlinked
    reg.upsert({"clip_id": "tamper_json", "titulo": "Tamper", "arquivo": "registro.json", "tamanho": 10, "sha256": "a"})
    reg.marcar("tamper_json", status="no_discord", ack_em=now_ts - 30 * 3600.0)
    nota = out_root / "pasta" / "nota.txt"
    nota.parent.mkdir(parents=True, exist_ok=True)
    nota.write_text("keep", encoding="utf-8")
    reg.upsert({"clip_id": "tamper_txt", "titulo": "Tamper2", "arquivo": "pasta/nota.txt", "tamanho": 4, "sha256": "b"})
    reg.marcar("tamper_txt", status="no_discord", ack_em=now_ts - 30 * 3600.0)

    # Eligible clips: err_c01 raises OSError on unlink; ok_c01 succeeds
    rec_err, mp4_err = _make_sample_rec(out_root, clip_id="err_c01", content=b"err")
    reg.upsert(rec_err)
    reg.marcar("err_c01", status="no_discord", ack_em=now_ts - 30 * 3600.0)

    rec_ok, mp4_ok = _make_sample_rec(out_root, clip_id="ok_c01", content=b"ok")
    reg.upsert(rec_ok)
    reg.marcar("ok_c01", status="no_discord", ack_em=now_ts - 30 * 3600.0)

    orig_unlink = Path.unlink

    def flaky_unlink(self: Path, missing_ok: bool = False) -> None:
        if self == mp4_err.resolve():
            raise OSError("arquivo em uso")
        return orig_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    freed = reg.liberar(24, now=now_ts)
    out = capsys.readouterr().out

    assert freed == ["ok_c01"]
    assert reg.path.is_file()
    assert nota.is_file()
    assert mp4_err.is_file()
    assert not mp4_ok.exists()
    assert reg.get("tamper_json")["status"] == "no_discord"
    assert reg.get("tamper_txt")["status"] == "no_discord"
    assert reg.get("err_c01")["status"] == "no_discord"
    assert reg.get("ok_c01")["status"] == "so_discord"
    assert "[registro] erro ao remover err_c01" in out


def test_sync_mongo_fora_do_lock_sem_perder_updates(tmp_path: Path):
    import fcntl

    client = mongomock.MongoClient()
    db = client.clipes_lives
    reg = Registro(tmp_path)
    lock_file = tmp_path / ".registro.json.lock"
    lock_free_in_mongo: list[bool] = []
    concurrent_done = [False]

    class HookClips:
        def __init__(self, real_col: Any) -> None:
            self.real = real_col

        def replace_one(self, *args: Any, **kwargs: Any) -> Any:
            # Verify the file lock is NOT held while talking to Mongo
            with open(lock_file, "a+", encoding="utf-8") as fh:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    lock_free_in_mongo.append(True)
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except BlockingIOError:
                    lock_free_in_mongo.append(False)

            # Simulate a concurrent writer persisting c_other while c_first's Mongo call is in flight
            if not concurrent_done[0]:
                concurrent_done[0] = True
                reg2 = Registro(tmp_path)  # local-only concurrent writer
                rec2, _ = _make_sample_rec(tmp_path, clip_id="c_other", content=b"other")
                reg2.upsert(rec2)

            return self.real.replace_one(*args, **kwargs)

    class HookDb:
        def __init__(self, real_db: Any) -> None:
            self.clips = HookClips(real_db.clips)

    reg_mongo = Registro(tmp_path, mongo=HookDb(db))
    rec1, _ = _make_sample_rec(tmp_path, clip_id="c_first", content=b"first")
    saved1 = reg_mongo.upsert(rec1)

    assert saved1 is not None
    assert saved1["mongo_synced"] is True
    assert lock_free_in_mongo == [True]
    assert reg.get("c_first") is not None and reg.get("c_first")["mongo_synced"] is True
    assert reg.get("c_other") is not None and reg.get("c_other")["mongo_synced"] is False
