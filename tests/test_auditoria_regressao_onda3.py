"""Regressao cruzada da onda 3: A-06 + M-11 (cota do Pacifico e reagendamento), M-14 (quotaExceeded),
A-05 (run_pending no acompanhar respeita agendar_para) e A-04 (clips.json filtrado visto pela publicacao).

Os unitarios ficam em test_auditoria_cota.py, test_auditoria_upload.py e test_auditoria_executor.py;
aqui so ha fluxos que atravessam modulos (publicacao, publicar_youtube, acompanhar, monitor).
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import mongomock

from clipes import acompanhar, monitor, publicacao, publicar_youtube
from clipes.coord import Coord
from clipes.publicacao import LocalStore, _quota_day, _record_quota, find_clip, quota_ok, request, run_pending
from test_acompanhar import B, Clock, FakeMultiPool
from test_auditoria_cota import TOKENS, USER, _dt, _env, _make_clip, _pending_doc, _utc  # noqa: F401 (_env e autouse)
from test_auditoria_executor import _write_clip_bundle, _write_tokens
from test_auditoria_upload import _http_error, _yt_err_body

T0 = 1_763_400_000.0  # 2025-11-17 17:20 UTC = 14:20 BRT = 09:20 PST (mesmo dia de cota nos dois fusos)


# ---- A-06 + M-11: slots 11:30 e 19:00 BRT no mesmo dia do Pacifico -----------------------------------
def test_a06_m11_slots_1130_e_1900_brt_e_terceiro_pedido_cai_no_dia_seguinte_do_pacifico(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_YT_MAX_DIA", "2")
    monkeypatch.setenv("CLIPES_AGENDA_HORARIOS", "11:30,19:00")
    for i in range(3):
        _make_clip(tmp_path, f"s{i}_c01")
    store = LocalStore(tmp_path)
    as_11h = _utc(2025, 11, 17, 14, 0)  # 11:00 BRT
    as_19h = _utc(2025, 11, 17, 22, 0)  # 19:00 BRT

    r0 = request(store, "s0_c01", USER, as_11h, out_root=tmp_path, tokens=TOKENS)
    r1 = request(store, "s1_c01", USER, as_11h, out_root=tmp_path, tokens=TOKENS)
    r2 = request(store, "s2_c01", USER, as_19h, out_root=tmp_path, tokens=TOKENS)

    assert "enfileirada" in r0 and "enfileirada" in r1
    assert "agendado para 2025-11-18 11:30" in r2
    assert "adiado" not in r0 + r1 + r2
    s0, s1, s2 = (store.get_request(f"s{i}_c01", "youtube") for i in range(3))
    assert all(d["status"] == "pending" for d in (s0, s1, s2))
    # os dois primeiros ficam no dia 17 do Pacifico (11:30 e 19:00 BRT); o terceiro vai para o dia 18
    assert _quota_day("youtube", publicacao._parse_ts(s0["agendar_para"])) == "2025-11-17"
    assert _quota_day("youtube", publicacao._parse_ts(s1["agendar_para"])) == "2025-11-17"
    assert publicacao._parse_ts(s2["agendar_para"]) == _utc(2025, 11, 18, 14, 30)  # 11:30 BRT dia 18
    assert _quota_day("youtube", publicacao._parse_ts(s2["agendar_para"])) == "2025-11-18"


# ---- A-06: cota do Pacifico continua cheia depois da meia-noite UTC ----------------------------------
def test_a06_cota_do_dia_pacifico_segue_cheia_depois_da_meia_noite_utc(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_YT_MAX_DIA", "2")
    qp = tmp_path / "q.json"
    _record_quota("youtube", _utc(2025, 11, 17, 14, 30), quota_path=qp)  # upload 11:30 BRT
    _record_quota("youtube", _utc(2025, 11, 17, 22, 0), quota_path=qp)  # upload 19:00 BRT
    _make_clip(tmp_path, "m0_c01")
    store = LocalStore(tmp_path)

    agora = _utc(2025, 11, 18, 0, 30)  # 21:30 BRT do dia 17 (00:30 UTC dia 18, ainda 16:30 PST dia 17)
    resp = request(store, "m0_c01", USER, agora, out_root=tmp_path, tokens=TOKENS, agendar=False, quota_path=qp)

    assert "cota diaria atingida (youtube)" in resp
    d = store.get_request("m0_c01", "youtube")
    assert d["status"] == "pending"
    # proximo dia de cota do Pacifico = 00:00 PST dia 18 = 08:00 UTC; nao pode sair ja (contador UTC zeraria)
    assert publicacao._parse_ts(d["agendar_para"]) == _utc(2025, 11, 18, 8, 0)


# ---- M-14: publicador devolve quota_esgotada -----------------------------------------------------------
def test_m14_quota_esgotada_do_publicador_nao_sobe_o_proximo_e_reagenda_os_dois_para_o_dia_seguinte(tmp_path):
    qp = tmp_path / "q.json"
    for i in range(2):
        _make_clip(tmp_path, f"v{i}_c01")
    store = LocalStore(tmp_path)
    for i in range(2):
        store.enqueue_request(_pending_doc(f"v{i}_c01", criado_em=_dt(T0 + i)))
    chamados: list[str] = []

    def pub(mp4, title, desc, tok):
        chamados.append(mp4.name)
        return {"status": "erro", "erro": "youtube HTTP 403: quotaExceeded", "quota_esgotada": True}

    out = run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens=TOKENS, quota_path=qp,
                      publishers={"youtube": pub})

    assert chamados == ["v0_c01.mp4"]  # o segundo nao chegou a subir
    assert [o["status"] for o in out] == ["pending", "pending"]
    proximo_dia_pacifico = _utc(2025, 11, 18, 8, 0)
    for i in range(2):
        d = store.get_request(f"v{i}_c01", "youtube")
        assert d["status"] == "pending"
        assert publicacao._parse_ts(d["agendar_para"]) >= proximo_dia_pacifico
    assert quota_ok("youtube", T0, quota_path=qp) is False  # dia 17 do Pacifico marcado como esgotado


# ---- M-14 de ponta a ponta: 403 quotaExceeded real do upload do YouTube ----------------------------------
def test_m14_403_quotaExceeded_do_init_do_youtube_chega_ao_run_pending_como_cota_esgotada(tmp_path):
    qp = tmp_path / "q.json"
    for i in range(2):
        _make_clip(tmp_path, f"y{i}_c01")
    store = LocalStore(tmp_path)
    for i in range(2):
        store.enqueue_request(_pending_doc(f"y{i}_c01", criado_em=_dt(T0 + i)))
    posts: list[str] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> Any:
        if req.get_method() == "POST":
            posts.append(req.full_url)
            raise _http_error(req.full_url, 403, _yt_err_body("quotaExceeded"))
        raise AssertionError("upload nao deve chegar ao PUT apos quotaExceeded")

    def pub(mp4, title, desc, tok):
        return publicar_youtube.publish(
            mp4, title, desc, tok, fake_urlopen,
            probe_duration_fn=lambda _: 30.0, sleep=lambda s: None, rand=lambda: 0.0,
        )

    out = run_pending(store, "w-1", out_root=tmp_path, now=T0, tokens=TOKENS, quota_path=qp,
                      publishers={"youtube": pub})

    assert len(posts) == 1  # so o primeiro pedido tentou o init; o segundo foi barrado pela marca do dia
    assert [o["status"] for o in out] == ["pending", "pending"]
    assert quota_ok("youtube", T0, quota_path=qp) is False
    for i in range(2):
        d = store.get_request(f"y{i}_c01", "youtube")
        assert d["status"] == "pending" and publicacao._parse_ts(d["agendar_para"]) >= _utc(2025, 11, 18, 8, 0)


# ---- A-05: run_pending do acompanhar (Mongo) respeita agendar_para futuro -------------------------------
def test_a05_run_pending_do_acompanhar_nao_publica_agendamento_futuro_e_publica_o_vencido(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    out_root = tmp_path / "out"
    _write_clip_bundle(out_root, "fut_c01", owner="w-acomp-x")
    _write_clip_bundle(out_root, "ven_c01", owner="w-acomp-x")
    tokens_path = _write_tokens(tmp_path / "tokens.json")

    db = mongomock.MongoClient().db
    for clip_id, agendar_ts in (("fut_c01", B + 3600), ("ven_c01", B - 60)):
        db.publish_requests.insert_one({
            "_id": f"{clip_id}:youtube", "clip_id": clip_id, "plataforma": "youtube", "owner": "w-acomp-x",
            "status": "pending", "thread_id": "thr-1", "solicitado_por": "111",
            "criado_em": _dt(B - 120), "agendar_para": _dt(agendar_ts),
        })
    coord = Coord(db, "w-acomp-x", now=clock.now, lease_s=90)

    done_ev = threading.Event()
    uploaded: list[str] = []

    def run_pending_real(c, wid, **kwargs):
        res = publicacao.run_pending(
            c, wid,
            out_root=kwargs["out_root"], now=kwargs["now"], policy=kwargs["policy"],
            tokens_path=tokens_path, quota_path=tmp_path / "quota.json",
            refreshers={"youtube": lambda _p, _t: {"access_token": "acc-1"}},
            publishers={"youtube": lambda mp4, t, d, tok: (
                uploaded.append(Path(mp4).name), {"video_id": "yt-x", "url": "https://youtu.be/yt-x"})[1]},
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

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")], out_root,
        pool=FakeMultiPool(clock, {"gaules": B + 320}),
        pipeline=lambda d, o: {"clips": []},
        coord=coord, stream_id="507", is_live=is_live_wait, now=clock.now, sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
        beat_s=0.03, run_pending_fn=run_pending_real,
    )
    res = sup.run()

    assert res["status"] == "ended"
    assert uploaded == ["ven_c01.mp4"]
    assert db.publish_requests.find_one({"_id": "ven_c01:youtube"})["status"] == "ok"
    assert db.publish_requests.find_one({"_id": "fut_c01:youtube"})["status"] == "pending"


# ---- A-04: o clipe descartado pelo filtro nao existe para a publicacao ----------------------------------
def test_a04_clipe_descartado_pelo_filtro_nao_e_encontrado_pela_publicacao(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    clock = Clock(B + 310)
    out_root = tmp_path / "out"
    out_root.mkdir()
    queue = monitor.DiskQueue(out_root / "queue")
    pool = FakeMultiPool(clock, {"gaules": B + 900})

    e = float(B + 300)
    vs = float(B - 300)
    clip_dir = out_root / "2026-11-18 - gaules [gaules_w1]"
    clip_dir.mkdir(parents=True)
    clips = [
        {"index": 1, "clip_id": "w1_c01_fora", "file": "fora.mp4", "title": "Fora", "score": 0.95,
         "start": (B + 290) - 10 - vs, "end": (B + 290) + 10 - vs, "duration": 20.0},
        {"index": 2, "clip_id": "w1_c03_dono", "file": "dono.mp4", "title": "Dono", "score": 0.85,
         "start": (B + 200) - 15 - vs, "end": (B + 200) + 15 - vs, "duration": 30.0},
    ]
    for c in clips:
        (clip_dir / c["file"]).write_bytes(b"mp4bytes")
    manifest = {"video_id": "gaules_w1", "title": "gaules - live", "clips": clips}

    def raw_pipeline(job_dir: Path, root: Path) -> dict[str, Any]:
        (clip_dir / "clips.json").write_text(json.dumps(manifest), encoding="utf-8")
        return {**manifest, "output_dir": str(clip_dir)}

    sess = acompanhar.LiveSession(
        acompanhar.parse_alvo("gaules"), out_root, queue, pool, raw_pipeline,
        is_live=lambda _: True, now=clock.now,
        disk_usage=lambda _: SimpleNamespace(free=100 * 1e9), step=300, max_dur=60.0,
    )
    pool.start_on_demand("gaules")
    pool.first["gaules"] = int(vs)
    sess.fechar_janela(e, last=False)
    job_dir = queue.root / "pending" / f"gaules_{B + 300}"

    result = sess.pipeline(job_dir, out_root)

    assert [c["clip_id"] for c in result["clips"]] == ["w1_c03_dono"]
    # a publicacao (que le clips.json do output_dir) nao enxerga o clipe fora da regiao dona
    assert find_clip(out_root, "w1_c01_fora") is None
    assert find_clip(out_root, "w1_c03_dono") is not None
    resp = request(LocalStore(out_root), "w1_c01_fora", USER, T0, out_root=out_root, tokens=TOKENS, agendar=False)
    assert resp == "clipe w1_c01_fora nao encontrado"
