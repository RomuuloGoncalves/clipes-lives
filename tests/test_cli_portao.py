"""Testes de integracao do portao de fala no pipeline (PICO-01, CLIPES_PORTAO off|log|on)."""

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from clipes import cli
from clipes.models import CandidatePeak, IngestResult, SignalBundle, SnappedClip, Word


def _ing(tmp_path: Path, duration: float = 600.0) -> IngestResult:
    cache = tmp_path / "out" / "vid" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    wav = cache / "audio.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    return IngestResult(
        video_id="vid",
        title="Live teste",
        url="https://www.twitch.tv/gaules",
        extractor="twitch",
        is_live=True,
        duration=duration,
        audio_path=wav,
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=cache.parent,
        cache_dir=cache,
        channel="gaules",
    )


def _bundle(duration: float) -> SignalBundle:
    n = max(1, int(duration))
    zeros = np.zeros(n)
    return SignalBundle(
        duration=duration,
        n=n,
        scores={"loudness": zeros},
        combined=zeros.copy(),
        smoothed=zeros.copy(),
        agreeing=np.zeros(n, dtype=np.int32),
        weights={"loudness": 1.0},
        rms_db=np.full(n * 2, -30.0, dtype=np.float32),
    )


def _make_words(start: float, count: int, step: float = 0.8, nsp: float = 0.05) -> list[Word]:
    return [
        Word(
            start=round(start + i * step, 3),
            end=round(start + i * step + 0.35, 3),
            text=f" p{i}",
            no_speech_prob=nsp,
        )
        for i in range(count)
    ]


def _wire_portao(monkeypatch, ing: IngestResult, clips: list[SnappedClip], words: list[Word]):
    monkeypatch.setattr(cli, "ingest", lambda **k: ing)
    monkeypatch.setattr(cli, "compute_rms_db", lambda *a, **k: np.zeros(4, np.float32))
    monkeypatch.setattr(cli, "compute_signals", lambda **k: _bundle(ing.duration))
    monkeypatch.setattr(
        cli,
        "pick_candidate_peaks",
        lambda *a, **k: [CandidatePeak(peak=100.0, raw_score=0.9, start_win=50.0, end_win=150.0)],
    )
    monkeypatch.setattr(cli, "transcribe_candidates", lambda **k: (list(words), "pt"))
    monkeypatch.setattr(cli, "select_clips", lambda **k: list(clips))


def _run(tmp_path: Path) -> int:
    return cli.main([
        "https://www.twitch.tv/gaules",
        "--out-dir",
        str(tmp_path / "out"),
        "--no-llm",
        "--skip-render",
        "--no-discord",
    ])


def _two_clips_and_words() -> tuple[list[SnappedClip], list[Word]]:
    falado = SnappedClip(peak=30.0, start=10.0, end=50.0, score=0.9, text="fala boa")
    vazio = SnappedClip(peak=130.0, start=100.0, end=164.0, score=0.8, text="oi oi")
    words = _make_words(11.0, 40, step=0.9) + _make_words(110.0, 2, step=1.0)
    return [falado, vazio], words


def test_portao_on_descarta_vazio_e_grava_wps_palavras(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIPES_PORTAO", "on")
    ing = _ing(tmp_path)
    clips, words = _two_clips_and_words()
    _wire_portao(monkeypatch, ing, clips, words)

    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "descartado: pouca fala" in out
    assert "[portao] 1 descartados de 2" in out

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 1
    assert manifest["clips"][0]["start"] == 10.0
    assert manifest["clips"][0]["palavras"] == 40
    assert manifest["clips"][0]["wps"] == pytest.approx(1.0, abs=1e-3)


def test_portao_padrao_log_mantem_clipes_e_loga(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CLIPES_PORTAO", raising=False)
    ing = _ing(tmp_path)
    clips, words = _two_clips_and_words()
    _wire_portao(monkeypatch, ing, clips, words)

    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "descartado: pouca fala" in out
    assert "[portao] 1 descartados de 2" in out

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 2
    assert manifest["clips"][0]["palavras"] == 40
    assert manifest["clips"][0]["wps"] == pytest.approx(1.0, abs=1e-3)
    assert manifest["clips"][1]["palavras"] == 2
    assert manifest["clips"][1]["wps"] == pytest.approx(2 / 64.0, abs=1e-3)


def test_portao_off_mantem_clipes_sem_log_do_portao(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIPES_PORTAO", "off")
    ing = _ing(tmp_path)
    clips, words = _two_clips_and_words()
    _wire_portao(monkeypatch, ing, clips, words)

    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "[portao]" not in out

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 2
    assert "wps" in manifest["clips"][0] and "palavras" in manifest["clips"][0]


def test_portao_on_com_todos_vazios_emite_zero_clipes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIPES_PORTAO", "on")
    ing = _ing(tmp_path)
    c1 = SnappedClip(peak=30.0, start=10.0, end=74.0, score=0.9, text="so duas")
    c2 = SnappedClip(peak=130.0, start=100.0, end=164.0, score=0.8, text="mais duas")
    words = _make_words(12.0, 2, step=1.0) + _make_words(105.0, 2, step=1.0)
    _wire_portao(monkeypatch, ing, [c1, c2], words)

    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "[portao] 2 descartados de 2" in out

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["clips"] == []


def test_portao_ignora_palavras_com_no_speech_prob_alto(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIPES_PORTAO", "on")
    ing = _ing(tmp_path)
    clip = SnappedClip(peak=30.0, start=10.0, end=40.0, score=0.9, text="alucinacao")
    # 25 palavras, mas todas com no_speech_prob=0.9 -> contagem efetiva = 0 -> descartado
    words = _make_words(11.0, 25, step=0.9, nsp=0.9)
    _wire_portao(monkeypatch, ing, [clip], words)

    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "descartado: pouca fala" in out
    assert "palavras=0" in out

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["clips"] == []


def test_portao_on_renumera_partes_quando_parte_do_meio_cai(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_PORTAO", "on")
    ing = _ing(tmp_path)
    p1 = SnappedClip(peak=25.0, start=10.0, end=50.0, score=0.9, part_index=1, part_total=3, group_id="g1", text="p1")
    p2 = SnappedClip(peak=75.0, start=51.0, end=101.0, score=0.9, part_index=2, part_total=3, group_id="g1", text="p2")
    p3 = SnappedClip(peak=125.0, start=102.0, end=142.0, score=0.9, part_index=3, part_total=3, group_id="g1", text="p3")
    words = _make_words(11.0, 30, step=1.0) + _make_words(55.0, 2, step=1.0) + _make_words(103.0, 30, step=1.0)
    _wire_portao(monkeypatch, ing, [p1, p2, p3], words)

    assert _run(tmp_path) == 0
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 2
    assert [(c["part_index"], c["part_total"]) for c in manifest["clips"]] == [(1, 2), (2, 2)]


def test_portao_sem_asr_ignora_portao_e_mantem_clipes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIPES_PORTAO", "on")
    ing = _ing(tmp_path)
    c1 = SnappedClip(peak=30.0, start=10.0, end=50.0, score=0.9, text="")
    _wire_portao(monkeypatch, ing, [c1], words=[])

    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "[portao] sem ASR: portao ignorado" in out

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 1


def test_portao_valor_invalido_cai_em_log_com_aviso(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIPES_PORTAO", "invalido")
    ing = _ing(tmp_path)
    clips, words = _two_clips_and_words()
    _wire_portao(monkeypatch, ing, clips, words)

    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "invalido; usando 'log'" in out
    assert "descartado: pouca fala" in out

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 2
