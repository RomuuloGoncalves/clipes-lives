"""Regressao ponta a ponta da onda 1 da auditoria: C-02, A-01, A-02 (cenarios cruzados entre componentes).

Os unitarios de cada peca ficam em test_auditoria_monitor/fila/acompanhar/test_coord; aqui os fluxos
completos: dois PCs no mesmo Mongo (mongomock), crash no meio do upload, retomada depois da lease, painel.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import mongomock
import pytest

from clipes import publicacao
from clipes.coord import Coord
from clipes.executor_publicacao import PublishLoop
from clipes.publicacao import MongoStore, run_pending
from painel.api import _nucleo

T0 = 1_763_400_000.0
LEASE_S = 90


class Relogio:
    """Relogio falso injetavel: `now=relogio` nos Coord e `relogio.t += n` para avancar."""

    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _naive(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


@pytest.fixture
def db():
    return mongomock.MongoClient().clipes_lives


# ---- A-02: live orfa de um PC que morreu nao trava o outro PC nem o painel ----------------------------


def test_a02_pc_a_morre_pc_b_assume_live_e_painel_nao_mostra_fantasma(db):
    relogio = Relogio()
    pc_a = Coord(db, "pc-a", now=relogio, lease_s=LEASE_S)
    pc_b = Coord(db, "pc-b", now=relogio, lease_s=LEASE_S)
    pc_a.ensure_live("twitch:canal:1", channel="canal", platform="twitch")
    assert pc_a.claim_live("twitch:canal:1") is True

    relogio.t += LEASE_S + 60  # pc-a morreu (kill -9): nunca mais renova
    agora = _naive(relogio.t)
    assert _nucleo.status(db, agora)["lives"] == []  # painel nao mostra a live fantasma

    assert pc_b.claim_live("twitch:canal:1") is True  # pc-b assume sem precisar de limpeza manual
    assert db.lives.find_one({"_id": "twitch:canal:1"})["owner"] == "pc-b"
    lives = _nucleo.status(db, agora)["lives"]
    assert [(l["id"], l["owner"]) for l in lives] == [("twitch:canal:1", "pc-b")]


def test_a02_pc_a_reinicia_libera_lease_vencida_e_max_lives_volta_a_zero(db):
    relogio = Relogio()
    pc_a = Coord(db, "pc-a", now=relogio, lease_s=LEASE_S)
    pc_a.ensure_live("twitch:velha:1", channel="velha", platform="twitch")
    assert pc_a.claim_live("twitch:velha:1") is True
    relogio.t += LEASE_S + 300  # crash; a lease venceu enquanto estava fora

    # a contagem do max_lives ja ignora a live fantasma, mesmo antes de soltar
    assert pc_a.count_owned_live() == 0

    # reinicio do processo: novo Coord com o mesmo worker_id (ID persistente)
    pc_a_novo = Coord(db, "pc-a", now=relogio, lease_s=LEASE_S)
    assert pc_a_novo.release_expired_own() == 1
    doc = db.lives.find_one({"_id": "twitch:velha:1"})
    assert doc["owner"] is None and doc["lease_until"] is None and doc["status"] == "live"
    assert pc_a_novo.count_owned_live() == 0

    # uma live valida deste PC continua contando no max_lives
    pc_a_novo.ensure_live("twitch:nova:2", channel="nova", platform="twitch")
    assert pc_a_novo.claim_live("twitch:nova:2") is True
    assert pc_a_novo.count_owned_live() == 1


# ---- A-01: pedido de publicacao nao fica `running` para sempre ---------------------------------------


def _req_youtube(clip_id: str, **extra: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_id": f"{clip_id}:youtube", "clip_id": clip_id, "plataforma": "youtube", "owner": "w-1",
        "status": "pending", "criado_em": _naive(T0),
    }
    doc.update(extra)
    return doc


@pytest.fixture
def pub_env(db, tmp_path, monkeypatch):
    """Clipe real em disco + tokens e cota em tmp, sem tocar em OAuth, Discord ou clips.json."""
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"video")

    def achar_clipe(root: Any, clip_id: str, store: Any = None) -> dict[str, Any]:
        # formato de publicacao.find_clip: entrada do clipe em "clip", metadados no topo
        return {"clip_id": clip_id, "clip": {"title": clip_id}, "manifest": {}, "clips_json_path": None,
                "mp4_path": str(mp4), "channel": "canal", "created_at": T0, "ready_at": None,
                "thread_id": None}

    monkeypatch.setattr(publicacao, "find_clip", achar_clipe)
    monkeypatch.setattr(publicacao, "_record_clip_publication", lambda *a, **k: None)

    def rodar(relogio_ts: float, publishers: dict[str, Any], store: Any | None = None):
        return run_pending(
            store or MongoStore(db), "w-1", out_root=tmp_path, now=relogio_ts,
            tokens={"youtube": {"access_token": "tok"}}, quota_path=tmp_path / "quota.json",
            publishers=publishers,
        )

    return rodar


def test_a01_publicador_com_excecao_inesperada_vira_erro_e_o_proximo_pedido_roda(db, pub_env):
    db.publish_requests.insert_many([_req_youtube("a"), _req_youtube("b")])
    publicados: list[str] = []

    def publicador(mp4_path: Any, titulo: str, descricao: str, token: str) -> dict[str, Any]:
        if titulo == "a":
            raise ValueError("falha https://www.googleapis.com/upload?upload_id=seg")
        publicados.append(titulo)
        return {"video_id": "yt-b"}

    out = pub_env(T0, {"youtube": publicador})
    statuses = {d["_id"]: d["status"] for d in db.publish_requests.find()}
    assert "running" not in statuses.values()
    assert statuses == {"a:youtube": "erro", "b:youtube": "ok"}
    assert publicados == ["b"]  # o erro de "a" nao derrubou a rodada
    assert "upload_id=seg" not in db.publish_requests.find_one({"_id": "a:youtube"})["resultado"]["erro"]
    assert len(out) == 2


def test_a01_pedido_running_com_lease_vencida_volta_a_pending_e_nao_reenvia_video_ja_publicado(db, pub_env):
    # crash (SystemExit simula kill/OOM) depois do upload e antes de gravar o resultado final
    db.publish_requests.insert_one(_req_youtube("a"))
    subidos: list[str] = []

    def publica_primeira_vez(*args: Any, **kwargs: Any) -> dict[str, Any]:
        subidos.append("primeira")
        return {"video_id": "yt-1"}

    orig_update = MongoStore.update_request

    def morre_ao_finalizar(self: MongoStore, req_id: str, fields: dict[str, Any]) -> None:
        if fields.get("status") == "ok":
            raise SystemExit(75)
        orig_update(self, req_id, fields)

    MongoStore.update_request = morre_ao_finalizar  # type: ignore[method-assign]
    try:
        with pytest.raises(SystemExit):
            pub_env(T0, {"youtube": publica_primeira_vez})
    finally:
        MongoStore.update_request = orig_update  # type: ignore[method-assign]

    preso = db.publish_requests.find_one({"_id": "a:youtube"})
    assert preso["status"] == "running"  # estado que o bug deixava para sempre
    assert preso["resultado"]["video_id"] == "yt-1"  # id do upload ja gravado antes do crash
    assert subidos == ["primeira"]

    # proxima chamada depois da lease (1800 s): volta a pending e fecha sem subir de novo
    reenvios: list[str] = []
    out = pub_env(T0 + 1801 + 60, {"youtube": lambda *a, **k: reenvios.append("de novo") or {"video_id": "yt-2"}})
    final = db.publish_requests.find_one({"_id": "a:youtube"})
    assert reenvios == []
    assert final["status"] == "ok" and final["resultado"]["video_id"] == "yt-1"
    assert [o["status"] for o in out] == ["ok"]


def test_a01_crash_antes_do_upload_com_lease_vencida_publica_uma_vez_na_retomada(db, pub_env):
    db.publish_requests.insert_one(_req_youtube("a"))

    def morre_no_upload(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise SystemExit(75)

    with pytest.raises(SystemExit):
        pub_env(T0, {"youtube": morre_no_upload})
    assert db.publish_requests.find_one({"_id": "a:youtube"})["status"] == "running"

    # antes da lease vencer, o pedido continua reservado (nao e reenviado por outro PC)
    assert pub_env(T0 + 100, {"youtube": lambda *a, **k: pytest.fail("reenviou antes da lease")}) == []

    chamadas: list[int] = []
    out = pub_env(T0 + 1801 + 1, {"youtube": lambda *a, **k: chamadas.append(1) or {"video_id": "yt-x"}})
    assert chamadas == [1]
    assert db.publish_requests.find_one({"_id": "a:youtube"})["status"] == "ok"
    assert [o["status"] for o in out] == ["ok"]


# ---- C-02: o executor de publicacao nunca bloqueia nem mata a rodada do heartbeat --------------------


def test_c02_rodada_lenta_nao_bloqueia_stop_e_stop_so_confirma_depois_de_liberar():
    comeco, liberar = threading.Event(), threading.Event()

    def upload_lento() -> None:
        comeco.set()
        liberar.wait(5)

    loop = PublishLoop(upload_lento, 0)
    assert loop.run_now() is True
    assert comeco.wait(2)
    assert loop.stop(timeout=0.05) is False  # upload ainda rodando: stop nao mente que parou
    assert loop.running
    liberar.set()
    assert loop.stop(timeout=5) is True
    assert not loop.running
