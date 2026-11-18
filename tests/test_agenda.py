"""Unit and integration tests for E2 publishing scheduler (`ler_horarios`, `proximo_horario`, `request`, `find_pending`, `run_pending`)."""

from __future__ import annotations

import json
import os
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import mongomock
import pytest

from clipes.publicacao import LocalStore, MongoStore, ler_horarios, proximo_horario, request, run_pending
from clipes.registro import Registro, sha256_arquivo

ALLOWED_USER = "111222333444"
TZ_SP = ZoneInfo("America/Sao_Paulo")


def _make_clip(out_root: Path, clip_id: str, *, owner: str = "w-pc1", folder: str | None = None) -> Path:
    clip_dir = out_root / (folder or f"2026-11-18 - Live [{clip_id}]")
    clip_dir.mkdir(parents=True, exist_ok=True)
    mp4 = clip_dir / f"{clip_id}.mp4"
    mp4.write_bytes(b"fake-mp4-" + clip_id.encode("utf-8"))
    manifest = {
        "video_id": clip_id.split("_")[0],
        "title": f"Live {clip_id}",
        "channel": "gaules",
        "owner": owner,
        "created_at": 1_700_000_000.0,
        "clips": [
            {
                "index": 1,
                "clip_id": clip_id,
                "file": mp4.name,
                "title": f"Titulo {clip_id}",
                "caption": f"Legenda {clip_id}",
                "discord": {"thread_id": "thr-1", "message_id": f"msg-{clip_id}"},
            }
        ],
    }
    (clip_dir / "clips.json").write_text(json.dumps(manifest), encoding="utf-8")
    return mp4


def _write_tokens(path: Path) -> Path:
    path.write_text(
        json.dumps({
            "youtube": {"access_token": "yt-acc", "refresh_token": "yt-ref"},
            "tiktok": {"access_token": "tt-acc", "refresh_token": "tt-ref"},
        }),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return path


def _make_store(kind: str, tmp_path: Path, now_ts: float) -> Any:
    if kind == "local":
        return LocalStore(tmp_path)
    client = mongomock.MongoClient()
    db = client.clipes_lives
    hb = datetime.fromtimestamp(now_ts, timezone.utc).replace(tzinfo=None)
    db.workers.insert_one({"_id": "w-pc1", "alias": "pc-mesa", "heartbeat_at": hb})
    return MongoStore(db)


# --- T6: ler_horarios e proximo_horario ---


def test_ler_horarios_valido_vazio_e_invalido(capsys: pytest.CaptureFixture[str]):
    assert ler_horarios("") == []
    assert ler_horarios(None) == []
    assert ler_horarios("19:00, 11:30") == [time(11, 30), time(19, 0)]

    parsed = ler_horarios("11:30, 25:00, abc, 19:00, 12:75")
    assert parsed == [time(11, 30), time(19, 0)]
    out = capsys.readouterr().out
    assert "[agenda] horario invalido: 25:00" in out
    assert "[agenda] horario invalido: abc" in out
    assert "[agenda] horario invalido: 12:75" in out


def test_proximo_horario_3_pedidos_2_horarios():
    horarios = [time(11, 30), time(19, 0)]
    agora = datetime(2026, 11, 18, 10, 0, tzinfo=TZ_SP)

    s1 = proximo_horario(agora, horarios, [], TZ_SP)
    assert s1 == datetime(2026, 11, 18, 11, 30, tzinfo=TZ_SP)

    s2 = proximo_horario(agora, horarios, [s1], TZ_SP)
    assert s2 == datetime(2026, 11, 18, 19, 0, tzinfo=TZ_SP)

    s3 = proximo_horario(agora, horarios, [s1, s2], TZ_SP)
    assert s3 == datetime(2026, 11, 19, 11, 30, tzinfo=TZ_SP)


def test_proximo_horario_folga_30_min_e_virada_de_dia():
    horarios = [time(11, 30), time(19, 0)]

    # Exatamente 30 min antes -> ainda cabe
    assert proximo_horario(datetime(2026, 11, 18, 11, 0, tzinfo=TZ_SP), horarios, [], TZ_SP) == datetime(
        2026, 11, 18, 11, 30, tzinfo=TZ_SP
    )

    # 29 min antes -> pula 11:30, vai para 19:00
    assert proximo_horario(datetime(2026, 11, 18, 11, 1, tzinfo=TZ_SP), horarios, [], TZ_SP) == datetime(
        2026, 11, 18, 19, 0, tzinfo=TZ_SP
    )

    # 18:45 (< 30 min para 19:00) -> vira para amanha 11:30
    assert proximo_horario(datetime(2026, 11, 18, 18, 45, tzinfo=TZ_SP), horarios, [], TZ_SP) == datetime(
        2026, 11, 19, 11, 30, tzinfo=TZ_SP
    )


def test_proximo_horario_respeita_fuso():
    horarios = [time(11, 30), time(19, 0)]
    # 14:00 UTC == 11:00 America/Sao_Paulo -> em SP cabe 11:30 do mesmo dia
    agora_utc = datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc)
    slot_sp = proximo_horario(agora_utc, horarios, [], "America/Sao_Paulo")
    assert slot_sp == datetime(2026, 11, 18, 11, 30, tzinfo=TZ_SP)
    assert slot_sp.utcoffset().total_seconds() == -3 * 3600

    # Em UTC (14:00 UTC), 11:30 UTC ja passou -> vai para 19:00 UTC
    slot_utc = proximo_horario(agora_utc, horarios, [], "UTC")
    assert slot_utc == datetime(2026, 11, 18, 19, 0, tzinfo=ZoneInfo("UTC"))


# --- T7: request grava agendar_para, find_pending e run_pending respeitam (local + mongomock) ---


@pytest.mark.parametrize("store_kind", ["local", "mongo"])
def test_agenda_local_e_mongo_nao_sai_antes_e_sai_no_horario(
    store_kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    monkeypatch.setenv("CLIPES_AGENDA_HORARIOS", "11:30,19:00")
    monkeypatch.setenv("CLIPES_TZ", "America/Sao_Paulo")

    agora_10h = datetime(2026, 11, 18, 10, 0, tzinfo=TZ_SP).timestamp()
    for cid in ("c1_c01", "c2_c01", "c3_c01"):
        _make_clip(tmp_path, cid)
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    quota_path = tmp_path / ".cotas.json"
    store = _make_store(store_kind, tmp_path, agora_10h)

    r1 = request(store, "c1_c01", ALLOWED_USER, agora_10h, out_root=tmp_path, tokens_path=tokens_path, quota_path=quota_path)
    r2 = request(store, "c2_c01", ALLOWED_USER, agora_10h + 60, out_root=tmp_path, tokens_path=tokens_path, quota_path=quota_path)
    r3 = request(store, "c3_c01", ALLOWED_USER, agora_10h + 120, out_root=tmp_path, tokens_path=tokens_path, quota_path=quota_path)

    assert "youtube: publicacao enfileirada; agendado para 2026-11-18 11:30 (America/Sao_Paulo)" in r1
    assert "tiktok: publicacao enfileirada; agendado para 2026-11-18 11:30 (America/Sao_Paulo)" in r1
    assert "youtube: publicacao enfileirada; agendado para 2026-11-18 19:00 (America/Sao_Paulo)" in r2
    assert "tiktok: publicacao enfileirada; agendado para 2026-11-18 19:00 (America/Sao_Paulo)" in r2
    assert "youtube: publicacao enfileirada; agendado para 2026-11-19 11:30 (America/Sao_Paulo)" in r3

    published: list[tuple[str, str]] = []
    pubs = {
        "youtube": lambda mp4, t, d, tok: published.append(("youtube", mp4.name)) or {"video_id": mp4.stem},
        "tiktok": lambda mp4, cap, tok: published.append(("tiktok", mp4.name)) or {"publish_id": mp4.stem},
    }

    # Antes de 11:30 (11:29) -> nada sai
    t_1129 = datetime(2026, 11, 18, 11, 29, tzinfo=TZ_SP).timestamp()
    assert store.find_pending("w-pc1", now=t_1129) == []
    assert run_pending(store, "w-pc1", out_root=tmp_path, now=t_1129, tokens_path=tokens_path, quota_path=quota_path, publishers=pubs) == []
    assert published == []

    # As 11:30 -> sai apenas c1
    t_1130 = datetime(2026, 11, 18, 11, 30, tzinfo=TZ_SP).timestamp()
    out_1130 = run_pending(store, "w-pc1", out_root=tmp_path, now=t_1130, tokens_path=tokens_path, quota_path=quota_path, publishers=pubs)
    assert len(out_1130) == 2
    assert set(published) == {("youtube", "c1_c01.mp4"), ("tiktok", "c1_c01.mp4")}

    # As 19:00 -> sai apenas c2
    published.clear()
    t_1900 = datetime(2026, 11, 18, 19, 0, tzinfo=TZ_SP).timestamp()
    out_1900 = run_pending(store, "w-pc1", out_root=tmp_path, now=t_1900, tokens_path=tokens_path, quota_path=quota_path, publishers=pubs)
    assert len(out_1900) == 2
    assert set(published) == {("youtube", "c2_c01.mp4"), ("tiktok", "c2_c01.mp4")}

    # Edge case: PC desligado as 11:30 do dia seguinte; liga as 15:00 -> c3 ainda sai (nao perde)
    published.clear()
    t_next_1500 = datetime(2026, 11, 19, 15, 0, tzinfo=TZ_SP).timestamp()
    out_next = run_pending(store, "w-pc1", out_root=tmp_path, now=t_next_1500, tokens_path=tokens_path, quota_path=quota_path, publishers=pubs)
    assert len(out_next) == 2
    assert set(published) == {("youtube", "c3_c01.mp4"), ("tiktok", "c3_c01.mp4")}


@pytest.mark.parametrize("store_kind", ["local", "mongo"])
@pytest.mark.parametrize("agenda_env", ["", None])
def test_agenda_vazia_ou_ausente_igual_a_hoje(
    store_kind: str, agenda_env: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    if agenda_env is None:
        monkeypatch.delenv("CLIPES_AGENDA_HORARIOS", raising=False)
    else:
        monkeypatch.setenv("CLIPES_AGENDA_HORARIOS", agenda_env)

    agora = datetime(2026, 11, 18, 10, 0, tzinfo=TZ_SP).timestamp()
    _make_clip(tmp_path, "v1_c01")
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    quota_path = tmp_path / ".cotas.json"
    store = _make_store(store_kind, tmp_path, agora)

    resp = request(store, "v1_c01", ALLOWED_USER, agora, out_root=tmp_path, tokens_path=tokens_path, quota_path=quota_path)
    assert "agendado para" not in resp
    doc = store.get_request("v1_c01", "youtube")
    assert doc is not None and "agendar_para" not in doc

    published: list[str] = []
    out = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=agora,
        tokens_path=tokens_path,
        quota_path=quota_path,
        publishers={"youtube": lambda m, t, d, tok: published.append(m.name) or {"video_id": "yt1"}},
    )
    assert any(o["plataforma"] == "youtube" and o["status"] == "ok" for o in out)
    assert published == ["v1_c01.mp4"]


def test_alterar_agenda_nao_muda_pedidos_existentes_e_agendar_false_fura_fila(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    monkeypatch.setenv("CLIPES_AGENDA_HORARIOS", "11:30,19:00")
    monkeypatch.setenv("CLIPES_TZ", "America/Sao_Paulo")

    agora = datetime(2026, 11, 18, 10, 0, tzinfo=TZ_SP).timestamp()
    _make_clip(tmp_path, "a1_c01")
    _make_clip(tmp_path, "a2_c01")
    _make_clip(tmp_path, "a3_c01")
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    quota_path = tmp_path / ".cotas.json"
    store = LocalStore(tmp_path)

    r1 = request(store, "a1_c01", ALLOWED_USER, agora, out_root=tmp_path, tokens_path=tokens_path, quota_path=quota_path, platforms=("youtube",))
    assert "2026-11-18 11:30" in r1

    # Muda a agenda -> pedido existente mantem 11:30, novo recebe 14:00
    monkeypatch.setenv("CLIPES_AGENDA_HORARIOS", "14:00,20:00")
    r2 = request(store, "a2_c01", ALLOWED_USER, agora + 30, out_root=tmp_path, tokens_path=tokens_path, quota_path=quota_path, platforms=("youtube",))
    assert "2026-11-18 14:00" in r2
    assert store.find_pending("w-pc1", now=datetime(2026, 11, 18, 11, 30, tzinfo=TZ_SP).timestamp())[0]["clip_id"] == "a1_c01"

    # agendar=False -> sem agendar_para, sai imediatamente
    r3 = request(
        store,
        "a3_c01",
        ALLOWED_USER,
        agora + 60,
        out_root=tmp_path,
        tokens_path=tokens_path,
        quota_path=quota_path,
        platforms=("youtube",),
        agendar=False,
    )
    assert "agendado para" not in r3
    assert "agendar_para" not in (store.get_request("a3_c01", "youtube") or {})
    due_now = [r["clip_id"] for r in store.find_pending("w-pc1", now=agora + 60)]
    assert due_now == ["a3_c01"]


@pytest.mark.parametrize("store_kind", ["local", "mongo"])
def test_agendado_futuro_permanece_protegido_no_liberar_f4(
    store_kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    monkeypatch.setenv("CLIPES_AGENDA_HORARIOS", "11:30,19:00")
    monkeypatch.setenv("CLIPES_TZ", "America/Sao_Paulo")

    agora = datetime(2026, 11, 18, 10, 0, tzinfo=TZ_SP).timestamp()
    mp4 = _make_clip(tmp_path, "prot_c01")
    tokens_path = _write_tokens(tmp_path / "tokens.json")
    store = _make_store(store_kind, tmp_path, agora)

    reg = Registro(tmp_path)
    reg.upsert({
        "clip_id": "prot_c01",
        "titulo": "Clipe Protegido",
        "canal": "gaules",
        "criado_em": agora - 7200,
        "arquivo": str(mp4.relative_to(tmp_path)),
        "tamanho": mp4.stat().st_size,
        "sha256": sha256_arquivo(mp4),
        "cabe_discord": True,
    })
    reg.set_discord("prot_c01", {"thread_id": "thr-1", "message_id": "m-1", "attachment_id": "att-1"})
    reg.marcar("prot_c01", status="no_discord", ack_em=agora - 3600)

    request(store, "prot_c01", ALLOWED_USER, agora, out_root=tmp_path, tokens_path=tokens_path, platforms=("youtube",))
    assert store.find_pending("w-pc1", now=agora) == []
    assert "prot_c01" in store.active_clip_ids()

    liberados = reg.liberar(retencao_h=0, now=agora, ocupados=store.active_clip_ids())
    assert liberados == []
    assert mp4.is_file()
