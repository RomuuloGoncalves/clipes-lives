"""Testes da gravacao e leitura de no_speech_prob na Passada 2 (clipes/asr.py)."""

import json
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipes.asr import transcribe_candidates
from clipes.models import CandidatePeak


def _make_wav(path: Path, seconds: float = 20.0, sr: int = 16000) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * int(seconds * sr))
    return path


def test_transcribe_candidates_grava_e_recarrega_no_speech_prob(tmp_path: Path, monkeypatch):
    wav = _make_wav(tmp_path / "audio.wav", seconds=20.0)
    cache = tmp_path / "cache"
    cache.mkdir()

    calls = {"transcribe": 0}

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio_slice, **kwargs):
            calls["transcribe"] += 1
            seg1 = SimpleNamespace(
                no_speech_prob=0.1,
                words=[
                    SimpleNamespace(start=0.5, end=1.0, word=" Olha"),
                    SimpleNamespace(start=1.1, end=1.6, word=" isso!"),
                ],
            )
            seg2 = SimpleNamespace(
                no_speech_prob=0.9,
                words=[
                    SimpleNamespace(start=3.0, end=3.5, word=" Alucinacao"),
                ],
            )
            info = SimpleNamespace(language="pt", language_probability=0.99)
            return [seg1, seg2], info

    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)

    cand = CandidatePeak(peak=10.0, raw_score=0.9, start_win=2.0, end_win=12.0)
    words, lang = transcribe_candidates(wav, [cand], cache, model_size="small", beam_size=5)

    assert lang == "pt"
    assert calls["transcribe"] == 1
    assert len(words) == 3  # todas as palavras seguem na lista (legenda nao muda)
    assert [w.clean for w in words] == ["Olha", "isso!", "Alucinacao"]
    assert [w.no_speech_prob for w in words] == [pytest.approx(0.1), pytest.approx(0.1), pytest.approx(0.9)]

    saved = json.loads((cache / "words.json").read_text(encoding="utf-8"))
    assert [w["nsp"] for w in saved["words"]] == [0.1, 0.1, 0.9]

    # Segunda chamada deve reusar o cache preservando nsp sem chamar Whisper de novo
    words_cached, lang_cached = transcribe_candidates(wav, [cand], cache, model_size="small", beam_size=5)
    assert calls["transcribe"] == 1
    assert lang_cached == "pt"
    assert [w.no_speech_prob for w in words_cached] == [pytest.approx(0.1), pytest.approx(0.1), pytest.approx(0.9)]


def test_transcribe_candidates_cache_antigo_sem_nsp_carrega_como_zero(tmp_path: Path):
    wav = _make_wav(tmp_path / "audio.wav", seconds=20.0)
    cache = tmp_path / "cache"
    cache.mkdir()
    legacy_payload = {
        "model": "small",
        "beam_size": 5,
        "language": "pt",
        "elapsed_s": 0.1,
        "windows": [[2.0, 12.0]],
        "words": [
            {"start": 2.5, "end": 3.0, "word": " Olha"},
            {"start": 3.1, "end": 3.6, "word": " antigo"},
        ],
    }
    (cache / "words.json").write_text(json.dumps(legacy_payload), encoding="utf-8")

    cand = CandidatePeak(peak=10.0, raw_score=0.9, start_win=2.0, end_win=12.0)
    words, lang = transcribe_candidates(wav, [cand], cache, model_size="small", beam_size=5)
    assert lang == "pt"
    assert len(words) == 2
    assert all(w.no_speech_prob == 0.0 for w in words)


def _recording_whisper(monkeypatch, record: list[dict]):
    class RecordingWhisperModel:
        def __init__(self, *args, **kwargs):
            record.append(kwargs)

        def transcribe(self, audio_slice, **kwargs):
            seg = SimpleNamespace(start=0.5, end=1.0, text=" ola", no_speech_prob=0.0, words=[])
            info = SimpleNamespace(language="pt", language_probability=0.99)
            return [seg], info

    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = RecordingWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)


def test_transcribe_candidates_usa_threads_por_job_sem_argumento(tmp_path: Path, monkeypatch):
    from clipes import capacidade

    wav = _make_wav(tmp_path / "audio.wav", seconds=20.0)
    cache = tmp_path / "cache"
    cache.mkdir()
    record: list[dict] = []
    _recording_whisper(monkeypatch, record)
    monkeypatch.setattr(capacidade, "threads_por_job", lambda: 2)
    monkeypatch.setattr(capacidade, "cpu_threads", lambda: 8)  # distingue threads_por_job do cpu_threads bruto

    cand = CandidatePeak(peak=10.0, raw_score=0.9, start_win=2.0, end_win=12.0)
    transcribe_candidates(wav, [cand], cache, model_size="small", beam_size=5)

    assert record[0]["cpu_threads"] == 2


def test_transcribe_candidates_argumento_explicito_vence(tmp_path: Path, monkeypatch):
    from clipes import capacidade

    wav = _make_wav(tmp_path / "audio.wav", seconds=20.0)
    cache = tmp_path / "cache"
    cache.mkdir()
    record: list[dict] = []
    _recording_whisper(monkeypatch, record)
    monkeypatch.setattr(capacidade, "threads_por_job", lambda: 2)
    monkeypatch.setattr(capacidade, "cpu_threads", lambda: 8)  # distingue threads_por_job do cpu_threads bruto

    cand = CandidatePeak(peak=10.0, raw_score=0.9, start_win=2.0, end_win=12.0)
    transcribe_candidates(wav, [cand], cache, model_size="small", beam_size=5, cpu_threads=5)

    assert record[0]["cpu_threads"] == 5


def test_transcribe_fast_blocks_usa_threads_por_job(tmp_path: Path, monkeypatch):
    from clipes import capacidade
    from clipes.asr import transcribe_fast_blocks

    wav = _make_wav(tmp_path / "audio.wav", seconds=20.0)
    cache = tmp_path / "cache"
    cache.mkdir()
    record: list[dict] = []
    _recording_whisper(monkeypatch, record)
    monkeypatch.setattr(capacidade, "threads_por_job", lambda: 2)
    monkeypatch.setattr(capacidade, "cpu_threads", lambda: 8)  # distingue threads_por_job do cpu_threads bruto

    transcribe_fast_blocks(wav, [(2.0, 12.0, 0.9)], cache, model_size="small")

    assert record[0]["cpu_threads"] == 2
