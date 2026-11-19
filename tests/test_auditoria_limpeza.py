"""Auditoria (onda 5): B-03 (fcntl ausente) e A-07 (find_clip via registro antes do rglob)."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from clipes import monitor, publicacao, registro


def test_registro_importa_e_falha_claro_sem_fcntl(monkeypatch, tmp_path: Path):
    monkeypatch.setitem(sys.modules, "fcntl", None)  # import fcntl -> ImportError
    try:
        reloaded = importlib.reload(registro)
        assert reloaded.fcntl is None
        with pytest.raises(RuntimeError, match="Linux/Docker"):
            with reloaded._locked_path(tmp_path / "x.json"):
                pass
    finally:
        monkeypatch.undo()
        importlib.reload(registro)


def test_lock_worker_falha_claro_sem_fcntl(monkeypatch, tmp_path: Path):
    monkeypatch.setitem(sys.modules, "fcntl", None)
    with pytest.raises(RuntimeError, match="Linux/Docker"):
        monitor._lock_worker(tmp_path)


def test_find_clip_usa_registro_sem_rglob(monkeypatch, tmp_path: Path):
    pasta = tmp_path / "gaules" / "v1"
    pasta.mkdir(parents=True)
    (pasta / "c01.mp4").write_bytes(b"x")
    (pasta / "clips.json").write_text(
        json.dumps({"video_id": "v1", "clips": [{"clip_id": "v1_c01", "file": "c01.mp4"}]}), encoding="utf-8"
    )
    Registro = registro.Registro
    Registro(tmp_path).upsert({"clip_id": "v1_c01", "arquivo": "gaules/v1/c01.mp4", "canal": "gaules"})

    def boom(self, pattern):
        raise AssertionError("rglob nao deveria ser chamado")

    monkeypatch.setattr(Path, "rglob", boom)
    doc = publicacao.find_clip(tmp_path, "v1_c01")
    assert doc is not None
    assert doc["clips_json_path"] == pasta / "clips.json"
    assert doc["mp4_path"] == pasta / "c01.mp4"


def test_find_clip_cai_no_rglob_quando_registro_nao_acha(tmp_path: Path):
    pasta = tmp_path / "x" / "v2"
    pasta.mkdir(parents=True)
    (pasta / "clips.json").write_text(
        json.dumps({"video_id": "v2", "clips": [{"clip_id": "v2_c01", "file": "c.mp4"}]}), encoding="utf-8"
    )
    doc = publicacao.find_clip(tmp_path, "v2_c01")
    assert doc is not None and doc["clips_json_path"] == pasta / "clips.json"


def test_pool_congelado_nao_inicia_gravadores(tmp_path: Path):
    pool = monitor.RollingRecorderPool(tmp_path / "buf", max_hot=2, popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("popen")))
    pool.freeze()
    pool.set_hot(["canal"])
    assert pool.start_on_demand("canal") is False
    assert pool.recordings == {}


def test_find_clip_ignora_arquivo_do_registro_fora_do_out_root(monkeypatch, tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "clips.json").write_text(
        json.dumps({"video_id": "v3", "clips": [{"clip_id": "v3_c01", "file": "c.mp4"}]}), encoding="utf-8"
    )
    registro.Registro(out).upsert({"clip_id": "v3_c01", "arquivo": "../fora/c.mp4", "canal": "x"})
    paths = list(publicacao._candidate_clips_paths(out, {"arquivo": "../fora/c.mp4"}))
    assert fora / "clips.json" not in paths and (out / ".." / "fora" / "clips.json") not in paths
    paths_abs = list(publicacao._candidate_clips_paths(out, {"arquivo": str(fora / "c.mp4")}))
    assert paths_abs == []


def test_freeze_nao_segura_o_gate_durante_o_stop_all(monkeypatch, tmp_path: Path):
    pool = monitor.RollingRecorderPool(tmp_path / "buf", max_hot=1)
    visto: list[bool] = []
    monkeypatch.setattr(pool, "stop_all", lambda: visto.append(pool._gate.locked()))
    pool.freeze()
    assert visto == [False] and pool.frozen


def test_set_hot_congelado_nao_toca_nas_gravacoes(tmp_path: Path):
    class Explode(dict):
        def items(self):
            raise AssertionError("nao deveria iterar")

    pool = monitor.RollingRecorderPool(tmp_path / "buf", max_hot=1)
    pool.frozen = True
    pool.recordings = Explode()
    pool.set_hot(["canal"])


def test_find_clip_nao_repete_clips_json_com_ponto_ponto(tmp_path: Path):
    pasta = tmp_path / "a" / "v4"
    pasta.mkdir(parents=True)
    (pasta / "clips.json").write_text("{}", encoding="utf-8")
    paths = list(publicacao._candidate_clips_paths(tmp_path, {"arquivo": "a/../a/v4/c.mp4"}))
    assert len(paths) == 1
