"""Auditoria (onda 3-A): cota de publicacao (A-06, M-11, M-12, M-14) e embargo delay_h (M-20)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mongomock
import pytest

from clipes import publicacao
from clipes.monitor import ChannelPolicy
from clipes.publicacao import (
    LocalStore,
    MongoStore,
    _next_quota_day_start,
    _quota_day,
    _record_quota,
    find_clip,
    quota_ok,
    request,
    run_pending,
)
from clipes.registro import Registro

USER = "111222333444"
T0 = 1_763_400_000.0  # 2025-11-17 17:20 UTC = 14:20 BRT = 09:20 PST
TOKENS = {"youtube": {"access_token": "yt-acc", "refresh_token": "yt-ref"}}


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _make_clip(out_root: Path, clip_id: str, *, manifest_extra: dict[str, Any] | None = None) -> None:
    d = out_root / f"2025-11-17 - Live [{clip_id}]"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{clip_id}.mp4").write_bytes(b"fake-" + clip_id.encode())
    manifest = {
        "video_id": clip_id.split("_")[0],
        "channel": "gaules",
        "owner": "w-1",
        "clips": [{"index": 1, "clip_id": clip_id, "file": f"{clip_id}.mp4", "title": clip_id,
                   "discord": {"thread_id": "thr"}}],
        **(manifest_extra or {}),
    }
    (d / "clips.json").write_text(json.dumps(manifest), encoding="utf-8")


def _pending_doc(clip_id: str, **extra: Any) -> dict[str, Any]:
    d = {"_id": f"{clip_id}:youtube", "clip_id": clip_id, "plataforma": "youtube", "owner": "w-1",
         "status": "pending", "criado_em": _dt(T0), "resultado": None, "thread_id": "thr"}
    d.update(extra)
    return d


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", USER)
    monkeypatch.setenv("CLIPES_TZ", "America/Sao_Paulo")
    monkeypatch.delenv("CLIPES_AGENDA_HORARIOS", raising=False)
    monkeypatch.delenv("CLIPES_QUOTA_PATH", raising=False)
    monkeypatch.setenv("CLIPES_YT_MAX_DIA", "6")


# ---- A-06
def test_dia_de_cota_youtube_e_o_do_pacifico_e_tiktok_segue_utc():
    t_1130_brt = _utc(2025, 11, 17, 14, 30)
    t_1900_brt = _utc(2025, 11, 17, 22, 0)  # 19:00 BRT = 22:00 UTC = 14:00 PST
    t_meia_noite_brt = _utc(2025, 11, 18, 3, 0)  # 00:00 BRT, ja dia 18 em UTC, ainda 17 no Pacifico
    assert _quota_day("youtube", t_1130_brt) == _quota_day("youtube", t_1900_brt) == "2025-11-17"
    assert _quota_day("youtube", t_meia_noite_brt) == "2025-11-17"
    assert _quota_day("youtube", _utc(2025, 11, 18, 8, 0)) == "2025-11-18"  # 00:00 PST
    assert _quota_day("tiktok", t_meia_noite_brt) == "2025-11-18"  # TikTok mantem UTC


def test_contador_youtube_vale_entre_11h30_e_19h_brt_e_zera_so_a_meia_noite_do_pacifico(tmp_path):
    qp = tmp_path / "q.json"
    t_1130 = _utc(2025, 11, 17, 14, 30)
    t_1900 = _utc(2025, 11, 17, 22, 0)
    _record_quota("youtube", t_1130, quota_path=qp)
    assert quota_ok("youtube", t_1900, quota_path=qp, limit=1) is False
    # 02:00 UTC do dia 18 ainda e dia 17 no Pacifico (com UTC o contador teria zerado)
    assert quota_ok("youtube", _utc(2025, 11, 18, 2, 0), quota_path=qp, limit=1) is False
    assert quota_ok("youtube", _utc(2025, 11, 18, 9, 0), quota_path=qp, limit=1) is True
    assert _next_quota_day_start("youtube", t_1900) == _utc(2025, 11, 18, 8, 0)


# ---- M-11
def test_request_conta_pedidos_pending_do_mesmo_dia_e_agenda_o_excedente_no_dia_seguinte(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_YT_MAX_DIA", "2")
    for i in range(3):
        _make_clip(tmp_path, f"v{i}_c01")
    store = LocalStore(tmp_path)
    resp = [
        request(store, f"v{i}_c01", USER, T0 + i, out_root=tmp_path, tokens=TOKENS, agendar=False)
        for i in range(3)
    ]
    assert "enfileirada" in resp[0] and "enfileirada" in resp[1]
    assert "cota diaria atingida (youtube)" in resp[2] and "agendado para" in resp[2]
    d = store.get_request("v2_c01", "youtube")
    assert d["status"] == "pending"  # nunca cria `adiado`
    assert publicacao._parse_ts(d["agendar_para"]) == _utc(2025, 11, 18, 8, 0)  # 00:00 PST do dia seguinte


def test_request_repetido_do_mesmo_pedido_nao_conta_a_si_mesmo(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_YT_MAX_DIA", "1")
    _make_clip(tmp_path, "v0_c01")
    store = LocalStore(tmp_path)
    r1 = request(store, "v0_c01", USER, T0, out_root=tmp_path, tokens=TOKENS, agendar=False)
    r2 = request(store, "v0_c01", USER, T0 + 1, out_root=tmp_path, tokens=TOKENS, agendar=False)
    assert "enfileirada" in r1 and "enfileirada" in r2  # r2 nao se conta (senao seria "cota diaria atingida")


def test_committed_count_pedido_atrasado_conta_para_hoje():
    class S:
        def active_requests(self, plat):
            return [{"_id": "a", "agendar_para": _dt(T0 - 3 * 86400)}, {"_id": "b", "agendar_para": None}]

    day = _quota_day("youtube", T0)
    assert publicacao._committed_count(S(), "youtube", day, T0) == 2
    assert publicacao._committed_count(S(), "youtube", _quota_day("youtube", T0 - 3 * 86400), T0) == 0


def test_run_pending_devolve_a_pending_no_proximo_slot_em_vez_de_adiado(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_YT_MAX_DIA", "1")
    monkeypatch.setenv("CLIPES_AGENDA_HORARIOS", "11:30")
    qp = tmp_path / "q.json"
    _record_quota("youtube", T0, quota_path=qp)  # dia 17 (Pacifico) cheio
    for i in range(2):
        _make_clip(tmp_path, f"v{i}_c01")
    store = LocalStore(tmp_path)
    for i in range(2):
        store.enqueue_request(_pending_doc(f"v{i}_c01", criado_em=_dt(T0 + i)))
    chamados: list[str] = []

    def pub(mp4, title, desc, tok):
        chamados.append(mp4.name)
        return {"video_id": "yt1"}

    out = run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens=TOKENS, quota_path=qp,
                      publishers={"youtube": pub})
    assert chamados == []
    assert all(o["status"] == "pending" for o in out)
    d0 = store.get_request("v0_c01", "youtube")
    d1 = store.get_request("v1_c01", "youtube")
    assert d0["status"] == d1["status"] == "pending"
    assert d0["lease_until"] is None
    s0, s1 = publicacao._parse_ts(d0["agendar_para"]), publicacao._parse_ts(d1["agendar_para"])
    assert s0 != s1  # slots distintos (1 por dia de cota)
    assert s0 == _utc(2025, 11, 18, 14, 30)  # 11:30 BRT do dia seguinte
    assert s1 == _utc(2025, 11, 19, 14, 30)

    # no dia seguinte o primeiro sai de fato
    out2 = run_pending(store, "w-1", out_root=tmp_path, now=_utc(2025, 11, 18, 14, 31), tokens=TOKENS,
                       quota_path=qp, publishers={"youtube": pub})
    assert [o["status"] for o in out2] == ["ok"] and chamados == ["v0_c01.mp4"]


def test_adiado_legado_e_ignorado_pela_fila_e_um_novo_request_o_recria(tmp_path):
    _make_clip(tmp_path, "v0_c01")
    store = LocalStore(tmp_path)
    store.enqueue_request(_pending_doc("v0_c01", status="adiado"))
    assert store.find_pending() == [] and store.active_clip_ids() == set()
    assert run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens=TOKENS) == []
    assert store.get_request("v0_c01", "youtube")["status"] == "adiado"  # legado intocado
    r = request(store, "v0_c01", USER, T0, out_root=tmp_path, tokens=TOKENS, agendar=False)
    assert "enfileirada" in r and store.get_request("v0_c01", "youtube")["status"] == "pending"


# ---- M-14
@pytest.mark.parametrize("com_flag", [True, False])
def test_quota_exceeded_marca_o_dia_e_os_seguintes_nao_sobem_o_video(tmp_path, com_flag):
    qp = tmp_path / "q.json"
    for i in range(2):
        _make_clip(tmp_path, f"v{i}_c01")
    store = LocalStore(tmp_path)
    for i in range(2):
        store.enqueue_request(_pending_doc(f"v{i}_c01", criado_em=_dt(T0 + i)))
    chamados: list[str] = []

    def pub(mp4, title, desc, tok):
        chamados.append(mp4.name)
        res: dict[str, Any] = {"status": "erro", "erro": "403 quotaExceeded: The request cannot be completed"}
        if com_flag:
            res = {"status": "erro", "erro": "falha", "quota_esgotada": True}
        return res

    out = run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens=TOKENS, quota_path=qp,
                      publishers={"youtube": pub})
    assert chamados == ["v0_c01.mp4"]  # o segundo nao tentou subir
    assert [o["status"] for o in out] == ["pending", "pending"]
    for i in range(2):
        d = store.get_request(f"v{i}_c01", "youtube")
        assert d["status"] == "pending" and publicacao._parse_ts(d["agendar_para"]) > T0
    assert quota_ok("youtube", T0, quota_path=qp) is False  # dia marcado como esgotado
    assert quota_ok("youtube", _utc(2025, 11, 18, 9, 0), quota_path=qp) is True


def test_falha_em_record_quota_vai_para_o_resultado_e_o_pedido_continua_ok(tmp_path, monkeypatch):
    _make_clip(tmp_path, "v0_c01")
    store = LocalStore(tmp_path)
    store.enqueue_request(_pending_doc("v0_c01"))

    def boom(*a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr(publicacao, "_record_quota", boom)
    out = run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens=TOKENS, quota_path=tmp_path / "q.json",
                      publishers={"youtube": lambda *a: {"video_id": "yt9"}})
    assert out[0]["status"] == "ok"
    d = store.get_request("v0_c01", "youtube")
    assert d["status"] == "ok" and d["resultado"]["video_id"] == "yt9"
    assert "disco cheio" in d["resultado"]["aviso_cota"]


# ---- M-12
def test_contador_fica_no_mongo_e_e_compartilhado_entre_pcs(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_YT_MAX_DIA", "1")
    db = mongomock.MongoClient().clipes_lives
    pc_a_root, pc_b_root = tmp_path / "a", tmp_path / "b"
    _make_clip(pc_a_root, "v0_c01")
    _make_clip(pc_b_root, "v1_c01")
    pc_a, pc_b = MongoStore(db), MongoStore(db)
    pc_a.enqueue_request(_pending_doc("v0_c01"))
    out = run_pending(pc_a, "w-1", out_root=pc_a_root, now=T0, tokens=TOKENS,
                      publishers={"youtube": lambda *a: {"video_id": "yt1"}})
    assert out[0]["status"] == "ok"
    assert db.cotas_publicacao.find_one({"_id": "youtube:2025-11-17"})["usado"] == 1  # reserva + record = 1, nao 2
    # outro PC (outro arquivo local), mesma conta: ja esta cheio
    pc_b.enqueue_request(_pending_doc("v1_c01"))
    chamados: list[str] = []
    out_b = run_pending(pc_b, "w-1", out_root=pc_b_root, now=T0, tokens=TOKENS,
                        publishers={"youtube": lambda *a: chamados.append("x") or {"video_id": "yt2"}})
    assert chamados == [] and out_b[0]["status"] == "pending"


def test_reserva_atomica_com_uma_vaga_so_um_pc_ganha():
    import threading

    db = mongomock.MongoClient().clipes_lives
    barrier = threading.Barrier(8)
    results: list[bool] = []

    def go():
        barrier.wait()
        results.append(publicacao._reserve_quota("youtube", T0, db=db, limit=1))

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count(True) == 1
    assert db.cotas_publicacao.find_one({"_id": "youtube:2025-11-17"})["usado"] == 1


def test_upload_que_falha_estorna_a_reserva(tmp_path):
    db = mongomock.MongoClient().clipes_lives
    _make_clip(tmp_path, "v0_c01")
    store = MongoStore(db)
    store.enqueue_request(_pending_doc("v0_c01"))
    out = run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens=TOKENS,
                      publishers={"youtube": lambda *a: {"status": "erro", "erro": "falha de rede"}})
    assert out[0]["status"] == "erro"
    assert db.cotas_publicacao.find_one({"_id": "youtube:2025-11-17"})["usado"] == 0


def test_quota_ok_le_o_maior_entre_mongo_e_arquivo_e_record_espelha(tmp_path):
    db = mongomock.MongoClient().clipes_lives
    qp = tmp_path / "q.json"
    _record_quota("youtube", T0, quota_path=qp)  # so arquivo
    assert quota_ok("youtube", T0, quota_path=qp, db=db, limit=1) is False
    _record_quota("youtube", T0 + 1, quota_path=tmp_path / "q2.json", db=db)  # mongo + espelho
    assert db.cotas_publicacao.find_one({"_id": "youtube:2025-11-17"})["usado"] == 1
    assert quota_ok("youtube", T0, quota_path=tmp_path / "q2.json", limit=1) is False  # espelho no arquivo


def test_mongo_quota_exceeded_marca_o_dia_no_mongo(tmp_path):
    db = mongomock.MongoClient().clipes_lives
    _make_clip(tmp_path, "v0_c01")
    store = MongoStore(db)
    store.enqueue_request(_pending_doc("v0_c01"))
    run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens=TOKENS,
                publishers={"youtube": lambda *a: {"status": "erro", "erro": "x", "quota_esgotada": True}})
    assert db.cotas_publicacao.find_one({"_id": "youtube:2025-11-17"})["usado"] >= 6
    assert db.publish_requests.find_one({"_id": "v0_c01:youtube"})["status"] == "pending"


# ---- M-20
def test_find_clip_usa_criado_em_do_registro_e_o_embargo_vale(tmp_path):
    _make_clip(tmp_path, "v0_c01")  # manifest sem created_at e sem job
    Registro(tmp_path).upsert({"clip_id": "v0_c01", "canal": "gaules", "criado_em": T0 - 3600,
                               "sha256": "a", "tamanho": 1})
    found = find_clip(tmp_path, "v0_c01")
    assert found is not None and found["created_at"] == T0 - 3600
    pol = ChannelPolicy(delay_h={"gaules": 24.0})
    resp = request(LocalStore(tmp_path), "v0_c01", USER, T0, out_root=tmp_path, tokens=TOKENS, policy=pol,
                   agendar=False)
    assert resp.startswith("aguardar ate")


def test_find_clip_usa_ready_at_do_registro(tmp_path):
    _make_clip(tmp_path, "v0_c01")
    Registro(tmp_path).upsert({"clip_id": "v0_c01", "canal": "gaules", "ready_at": T0 + 500,
                               "sha256": "a", "tamanho": 1})
    found = find_clip(tmp_path, "v0_c01")
    assert found is not None and found["ready_at"] == T0 + 500


# ---- A-06 horario de verao (PDT)
def test_dia_de_cota_em_pdt_vira_as_07h_utc():
    assert _quota_day("youtube", _utc(2025, 7, 17, 6, 59)) == "2025-07-16"  # 23:59 PDT
    assert _quota_day("youtube", _utc(2025, 7, 17, 7, 0)) == "2025-07-17"  # 00:00 PDT
    assert _next_quota_day_start("youtube", _utc(2025, 7, 17, 12, 0)) == _utc(2025, 7, 18, 7, 0)


# ---- review: reagendamento quando marcar esgotado falha / 401 com quotaExceeded
def test_falha_ao_marcar_esgotado_ainda_reagenda_a_partir_do_proximo_dia(tmp_path, monkeypatch):
    _make_clip(tmp_path, "v0_c01")
    store = LocalStore(tmp_path)
    store.enqueue_request(_pending_doc("v0_c01"))

    def boom(*a, **k):
        raise OSError("sem disco")

    monkeypatch.setattr(publicacao, "_mark_quota_exhausted", boom)
    run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens=TOKENS, quota_path=tmp_path / "q.json",
                publishers={"youtube": lambda *a: {"status": "erro", "erro": "x", "quota_esgotada": True}})
    d = store.get_request("v0_c01", "youtube")
    assert d["status"] == "pending"
    assert publicacao._parse_ts(d["agendar_para"]) >= _utc(2025, 11, 18, 8, 0)  # nunca o mesmo dia esgotado


def test_quota_exceeded_apos_refresh_401_reagenda_em_vez_de_erro_auth(tmp_path):
    _make_clip(tmp_path, "v0_c01")
    store = LocalStore(tmp_path)
    store.enqueue_request(_pending_doc("v0_c01"))
    n = {"c": 0}

    def pub(mp4, title, desc, tok):
        n["c"] += 1
        if n["c"] == 1:
            raise RuntimeError("HTTP 401 unauthorized")
        raise RuntimeError("403 quotaExceeded")

    run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens=TOKENS, quota_path=tmp_path / "q.json",
                publishers={"youtube": pub}, refreshers={"youtube": lambda p, t: {"access_token": "novo"}})
    assert store.get_request("v0_c01", "youtube")["status"] == "pending"


# ---- B-06: refresh nao sobrescreve tokens.json com snapshot velho
def test_refresh_persiste_so_o_refresh_da_plataforma_sem_regravar_snapshot_velho(tmp_path):
    import os

    tokens_path = tmp_path / "tokens.json"
    tokens_path.write_text(json.dumps({"youtube": {"access_token": "a", "refresh_token": "r1"},
                                       "tiktok": {"refresh_token": "t1"}}), encoding="utf-8")
    os.chmod(tokens_path, 0o600)
    _make_clip(tmp_path, "v0_c01")
    store = LocalStore(tmp_path)
    store.enqueue_request(_pending_doc("v0_c01"))
    n = {"c": 0}

    def pub(mp4, title, desc, tok):
        n["c"] += 1
        if n["c"] == 1:
            # outro processo grava tiktok enquanto o upload roda
            tokens_path.write_text(json.dumps({"youtube": {"access_token": "a", "refresh_token": "r1"},
                                               "tiktok": {"refresh_token": "t2-novo"}}), encoding="utf-8")
            raise RuntimeError("HTTP 401")
        return {"video_id": "yt1"}

    out = run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens_path=tokens_path,
                      quota_path=tmp_path / "q.json", publishers={"youtube": pub},
                      refreshers={"youtube": lambda p, t: {"access_token": "acc-novo", "refresh_token": "r2"}})
    assert out[0]["status"] == "ok"
    saved = json.loads(tokens_path.read_text(encoding="utf-8"))
    assert saved["youtube"]["refresh_token"] == "r2"
    assert saved["tiktok"]["refresh_token"] == "t2-novo"  # snapshot velho nao sobrescreveu
    assert "acc-novo" not in json.dumps(saved)  # access_token nao vai para o disco
