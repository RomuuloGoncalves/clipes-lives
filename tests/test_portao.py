"""Testes unitarios do portao de fala (PICO-01) e avaliacao offline (PICO-04)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipes.eval import count_speech_words, evaluate_speech_gate, main as eval_main, speech_gate
from clipes.models import Word


def test_speech_gate_casos_reais_e_regras():
    # Caso ishowspeed_1795046100_c01: 2 palavras em 64 s -> descartado
    ok, wps = speech_gate(2, 64.0)
    assert ok is False
    assert wps == pytest.approx(2 / 64.0, rel=1e-4)

    # Caso paulinholokobr_1795009500_c06: 24 palavras em 52 s -> wps ~0.46 >= 0.4, 24 >= 15 -> passa
    ok, wps = speech_gate(24, 52.0)
    assert ok is True
    assert wps == pytest.approx(24 / 52.0, rel=1e-4)

    # Menos de 15 palavras falha mesmo com wps alto (1.4)
    ok, wps = speech_gate(14, 10.0)
    assert ok is False
    assert wps == pytest.approx(1.4, rel=1e-4)

    # wps < 0.4 falha mesmo com >= 15 palavras (23 palavras em 62 s -> wps ~0.37)
    ok, wps = speech_gate(23, 62.0)
    assert ok is False
    assert wps == pytest.approx(23 / 62.0, rel=1e-4)


def test_speech_gate_fronteiras():
    # Fronteira exata wps == 0.4 e n_words >= 15 -> passa
    ok, wps = speech_gate(16, 40.0)
    assert ok is True
    assert wps == pytest.approx(0.4)

    # Fronteira exata n_words == 15 e wps >= 0.4 -> passa
    ok, wps = speech_gate(15, 37.5)
    assert ok is True
    assert wps == pytest.approx(0.4)

    # duration == 0 e negativa -> falha com wps 0.0
    assert speech_gate(20, 0.0) == (False, 0.0)
    assert speech_gate(20, -5.0) == (False, 0.0)


def test_count_speech_words_filtra_intervalo_e_no_speech_prob():
    words = [
        # Fora antes do intervalo (centro 9.5 < 10.0)
        SimpleNamespace(start=9.0, end=10.0, text="fora1", no_speech_prob=0.1),
        # Dentro com nsp baixo -> conta
        SimpleNamespace(start=10.0, end=10.4, text="fala", no_speech_prob=0.1),
        # Dentro no limite nsp == 0.6 -> conta (excluido somente quando > 0.6)
        SimpleNamespace(start=12.0, end=12.5, text="limite", no_speech_prob=0.6),
        # Dentro com nsp alto (0.9 > 0.6) -> ignorada
        SimpleNamespace(start=15.0, end=15.5, text="alucinacao", no_speech_prob=0.9),
        # Word sem atributo no_speech_prob (ou default 0.0) -> conta
        Word(start=18.0, end=18.4, text="normal"),
        SimpleNamespace(start=19.0, end=19.4, text="sem_attr"),
        # Fora depois do intervalo (centro 20.5 > 20.0)
        SimpleNamespace(start=20.0, end=21.0, text="fora2", no_speech_prob=0.0),
    ]
    assert count_speech_words(words, 10.0, 20.0) == 4


def _write_manifest(base: Path, folder: str, payload: dict) -> None:
    d = base / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "clips.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_evaluate_speech_gate_sintetico_e_cli(tmp_path: Path, capsys):
    # 1) Live com todos os clipes vazios -> conta como zerada (1) e c01 descartado
    _write_manifest(tmp_path, "live_vazia", {
        "video_id": "lv1",
        "is_live": True,
        "duration": 1800.0,  # 0.5h
        "clips": [
            {"index": 1, "clip_id": "lv1_c01", "duration": 60.0, "transcript": "so duas"},
            {"index": 2, "clip_id": "lv1_c02", "duration": 10.0, "transcript": " ".join(["ok"] * 12)},  # wps=1.2 >= 1, mas <15 palavras
        ],
    })
    # 2) Live mista: 1 falado, 1 vazio -> nao zera
    _write_manifest(tmp_path, "live_mista", {
        "video_id": "lm1",
        "is_live": True,
        "duration": 1800.0,  # 0.5h (total live = 1.0h)
        "clips": [
            {"index": 1, "clip_id": "lm1_c01", "duration": 40.0, "transcript": " ".join(["fala"] * 30)},
            {"index": 2, "clip_id": "lm1_c02", "duration": 62.0, "transcript": " ".join(["rala"] * 23)},  # wps 0.37 < 0.4
        ],
    })
    # 3) VOD com 1 clipe falado
    _write_manifest(tmp_path, "vod_ok", {
        "video_id": "v1",
        "is_live": False,
        "duration": 3600.0,  # 1.0h
        "clips": [
            {"index": 1, "clip_id": "v1_c01", "duration": 50.0, "transcript": " ".join(["texto"] * 40)},
        ],
    })
    # Pastas com _ e . devem ser ignoradas
    _write_manifest(tmp_path, "_testes", {
        "video_id": "ign1",
        "is_live": True,
        "duration": 600.0,
        "clips": [{"index": 1, "clip_id": "ign1_c01", "duration": 60.0, "transcript": "nada"}],
    })
    _write_manifest(tmp_path, ".oculto", {
        "video_id": "ign2",
        "is_live": True,
        "duration": 600.0,
        "clips": [{"index": 1, "clip_id": "ign2_c01", "duration": 60.0, "transcript": "nada"}],
    })

    rep = evaluate_speech_gate(tmp_path)
    assert rep["total"] == 5
    assert rep["discarded_count"] == 3
    assert rep["kept"] == 2
    assert rep["discarded_pct"] == 60.0
    assert rep["discarded_wps_ge_1"] == 1
    assert rep["lives_total"] == 3
    assert rep["lives_zeroed"] == 1
    assert rep["c01_discarded"] == 1
    assert rep["by_origin"]["live"]["clips_per_hour_before"] == 4.0
    assert rep["by_origin"]["live"]["clips_per_hour_after"] == 1.0
    assert rep["by_origin"]["vod"]["clips_per_hour_before"] == 1.0
    assert rep["by_origin"]["vod"]["clips_per_hour_after"] == 1.0
    assert [d["clip_id"] for d in rep["discarded"]] == ["lm1_c02", "lv1_c01", "lv1_c02"]

    # CLI --json e texto
    rc = eval_main(["portao", str(tmp_path), "--json"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["discarded_count"] == 3
    assert parsed["lives_zeroed"] == 1

    rc2 = eval_main(["portao", str(tmp_path)])
    assert rc2 == 0
    out_txt = capsys.readouterr().out
    assert "Descartados: 3/5" in out_txt
    assert "Lives com 0 clipes (zeradas): 1/3" in out_txt
    assert "lv1_c01" in out_txt


def test_evaluate_speech_gate_ilegivel_e_sem_clipes(tmp_path: Path, capsys):
    # clips.json corrompido -> pulado com aviso, sem derrubar
    bad = tmp_path / "corrompido"
    bad.mkdir()
    (bad / "clips.json").write_text("{json quebrado", encoding="utf-8")

    # clips.json com clips: [] -> conta como live, nao como zerada pelo portao
    _write_manifest(tmp_path, "live_sem_clipes", {
        "video_id": "empty1",
        "is_live": True,
        "duration": 600.0,
        "clips": [],
    })
    _write_manifest(tmp_path, "live_boa", {
        "video_id": "ok1",
        "is_live": True,
        "duration": 600.0,
        "clips": [{"index": 1, "clip_id": "ok1_c01", "duration": 30.0, "transcript": " ".join(["a"] * 20)}],
    })

    rep = evaluate_speech_gate(tmp_path)
    err = capsys.readouterr().err
    assert "ilegivel" in err
    assert rep["skipped_files"] == 1
    assert rep["lives_total"] == 2
    assert rep["lives_zeroed"] == 0
    assert rep["total"] == 1
    assert rep["discarded_count"] == 0
