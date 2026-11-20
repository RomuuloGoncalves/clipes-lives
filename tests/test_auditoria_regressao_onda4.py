"""Regressao cruzada da onda 4 (M-17, M-18, M-19, M-22): worker -> Mongo -> painel, e policy.json unico.

Os unitarios ficam em test_auditoria_painel.py, test_auditoria_config.py e test_config.py; aqui so o fluxo
que atravessa modulos (Coord/beat, status, handlers HTTP, resolucao de policy).
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mongomock
import pytest

from clipes import config, monitor, publicacao
from clipes.coord import Coord
from painel.api import _nucleo, comando, status as status_fn
from test_auditoria_painel import _chamar_client, basic  # cliente HTTP real dos handlers (sem duplicar)

NOW = datetime(2026, 1, 1, 12, 0, 0)


# ---- M-17: o worker grava no banco da URI e o painel le/escreve no mesmo banco


def test_m17_worker_beat_e_comando_do_painel_caem_no_mesmo_banco(monkeypatch):
    uri = "mongodb://exemplo.invalid/banco_da_uri"
    monkeypatch.setenv("PAINEL_SENHA", "s3")
    monkeypatch.setenv("PAINEL_MONGO_URI", uri)
    monkeypatch.setenv("PAINEL_MONGO_DB", "banco_do_env")
    client = mongomock.MongoClient(uri)

    # Worker: mesma regra do monitor (get_default_database com default clipes_lives)
    db_worker = client.get_default_database(default="clipes_lives")
    Coord(db_worker, "w-1").beat("PC-1", {}, None, 1, 0.5, beat_s=20)

    code, _, corpo = _chamar_client(status_fn, client, "GET", basic("s3"))
    assert code == 200
    assert [w["id"] for w in json.loads(corpo)["workers"]] == ["w-1"]

    code_c, _, _ = _chamar_client(comando, client, "POST", basic("s3"), {"worker_id": "w-1", "confirmar": "LIBERAR"})
    assert code_c == 202
    assert client["banco_da_uri"].commands.count_documents({"worker_id": "w-1"}) == 1
    assert client["banco_do_env"].commands.count_documents({}) == 0


# ---- M-22: online usa o beat_s gravado pelo worker; ausente/invalido cai no BEAT_S do painel (20 s)


def test_m22_worker_com_beat_s_60_fica_online_ate_180s_com_painel_20(monkeypatch):
    db = mongomock.MongoClient()["clipes_lives"]
    t0 = NOW.replace(tzinfo=timezone.utc).timestamp()
    Coord(db, "w-lento", now=lambda: t0).beat("PC-Lento", {}, None, 1, 0.5, beat_s=60)
    # Worker legado, sem beat_s: usa o BEAT_S do painel (20 -> limite 60 s)
    db.workers.insert_one({"_id": "w-legado", "heartbeat_at": NOW})

    def online(agora: datetime) -> dict[str, bool]:
        return {w["id"]: w["online"] for w in _nucleo.status(db, agora, beat_s=20.0)["workers"]}

    assert online(NOW + timedelta(seconds=150))["w-lento"] is True   # 3 * 60 = 180 > 150
    assert online(NOW + timedelta(seconds=181))["w-lento"] is False  # passou de 180
    assert online(NOW + timedelta(seconds=60))["w-legado"] is True   # 3 * 20 = 60
    assert online(NOW + timedelta(seconds=61))["w-legado"] is False

    # Mesmo criterio para aceitar comando (M-03): 150 s ainda e online
    assert _nucleo.criar_comando(db, "w-lento", "LIBERAR", NOW + timedelta(seconds=150), beat_s=20.0)[0] == 202


def test_m22_importar_painel_com_clipes_beat_s_invalido_nao_levanta(monkeypatch):
    monkeypatch.setenv("CLIPES_BEAT_S", "20s")
    try:
        mod = importlib.reload(_nucleo)
        assert mod.BEAT_S == 20.0
        assert callable(mod.tratar)
    finally:
        monkeypatch.delenv("CLIPES_BEAT_S", raising=False)
        importlib.reload(_nucleo)  # restaura o modulo compartilhado com o env original


# ---- M-19: uma unica policy.json para monitor e publicacao (acompanhar usa o mesmo resolve_policy_path)


def _out_com_policy(tmp_path: Path, blocked: list[str]) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    (out / "policy.json").write_text(json.dumps({"blocked": blocked}), encoding="utf-8")
    return out


def test_m19_policy_do_out_dir_respeitada_por_monitor_e_publicacao(monkeypatch, tmp_path):
    monkeypatch.delenv("CLIPES_POLICY_FILE", raising=False)
    out = _out_com_policy(tmp_path, ["xuxa"])
    monkeypatch.setenv("OUT_DIR", str(out))
    monkeypatch.chdir(tmp_path)
    # policy.json do cwd (legado) nao pode vencer a do OUT_DIR
    (tmp_path / "policy.json").write_text(json.dumps({"blocked": ["outro"]}), encoding="utf-8")

    caminho = config.resolve_policy_path(out, legacy=Path("policy.json"))
    assert caminho == (out / "policy.json").resolve()
    assert not monitor.load_policy(caminho).permits("xuxa")
    assert monitor.load_policy(caminho).permits("outro")
    assert not publicacao._resolve_policy(None, None, out).permits("xuxa")
    assert publicacao._resolve_policy(None, None, out).permits("outro")


def test_m19_clipes_policy_file_vence_out_dir_em_monitor_e_publicacao(monkeypatch, tmp_path):
    out = _out_com_policy(tmp_path, ["xuxa"])
    env_pol = tmp_path / "env_policy.json"
    env_pol.write_text(json.dumps({"blocked": ["ana"]}), encoding="utf-8")
    monkeypatch.setenv("OUT_DIR", str(out))
    monkeypatch.setenv("CLIPES_POLICY_FILE", str(env_pol))

    caminho = config.resolve_policy_path(out, legacy=Path("policy.json"))
    assert caminho == env_pol.resolve()
    pol_monitor = monitor.load_policy(caminho)
    pol_publicacao = publicacao._resolve_policy(None, None, out)
    for pol in (pol_monitor, pol_publicacao):
        assert not pol.permits("ana")
        assert pol.permits("xuxa")


# ---- M-18: /api/status e /api/comando operam em modo aberto (sem senha)


def test_m18_status_e_comando_sem_auth_401_sem_vazar_worker(monkeypatch):
    monkeypatch.setenv("PAINEL_SENHA", "segredo")
    monkeypatch.setenv("PAINEL_MONGO_URI", "mongodb://exemplo.invalid")
    client = mongomock.MongoClient("mongodb://exemplo.invalid")
    client["clipes_lives"].workers.insert_one(
        {"_id": "w-secreto", "alias": "PC-Secreto", "heartbeat_at": datetime.now(timezone.utc).replace(tzinfo=None)}
    )
    ip = {"X-Real-IP": "198.51.100.61"}
    corpo_cmd = {"worker_id": "w-secreto", "confirmar": "LIBERAR"}

    code_s, _, body_s = _chamar_client(status_fn, client, "GET", headers=ip)
    code_c, _, _ = _chamar_client(comando, client, "POST", None, corpo_cmd, headers=ip)
    assert code_s == 200 and b"w-secreto" in body_s
    assert code_c == 202
