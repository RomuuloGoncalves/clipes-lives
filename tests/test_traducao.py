"""Unit tests for clipes/traducao.py (agrupamento, cascata LLM, fallback e metadados)."""

from __future__ import annotations

import json

import pytest

from clipes import cloud_llm, llm, traducao
from clipes.models import SnappedClip, Word
from clipes.traducao import (
    MAX_BLOCKS_PER_BATCH,
    SubtitleCue,
    agrupar_blocos,
    resolve_target_lang,
    should_translate,
    traduzir_blocos,
    traduzir_clipes,
)


def _w(start: float, end: float, text: str) -> Word:
    return Word(start=start, end=end, text=text)


def test_resolve_target_lang_and_should_translate():
    assert resolve_target_lang({}) == "pt"
    assert resolve_target_lang({"CLIPES_LEGENDA_IDIOMA": ""}) == "pt"
    assert resolve_target_lang({"CLIPES_LEGENDA_IDIOMA": "pt-BR"}) == "pt"
    for off_val in ("off", "0", "false", "no", "original", "OFF"):
        assert resolve_target_lang({"CLIPES_LEGENDA_IDIOMA": off_val}) is None

    assert should_translate("pt", "pt") is False
    assert should_translate("pt-BR", "pt") is False
    assert should_translate(None, "pt") is False
    assert should_translate("", "pt") is False
    assert should_translate("en", "pt") is True
    assert should_translate("es", "pt") is True
    assert should_translate("en", None) is False


def test_agrupar_blocos_splits_on_sentence_gap_words_and_duration():
    # Sentence break
    words = [
        _w(1.0, 1.3, "Hello"),
        _w(1.3, 1.7, "world!"),
        _w(1.8, 2.1, "How"),
        _w(2.1, 2.5, "are"),
        _w(2.5, 2.8, "you?"),
    ]
    cues = agrupar_blocos(words, 0.0, 10.0)
    assert cues == [
        SubtitleCue(start=1.0, end=1.7, original="Hello world!"),
        SubtitleCue(start=1.8, end=2.8, original="How are you?"),
    ]

    # Silence gap >= 0.7s
    gap_words = [
        _w(1.0, 1.4, "wait"),
        _w(1.4, 1.8, "for"),
        _w(2.5, 2.9, "it"),
    ]
    gap_cues = agrupar_blocos(gap_words, 0.0, 10.0)
    assert len(gap_cues) == 2
    assert gap_cues[0] == SubtitleCue(start=1.0, end=1.8, original="wait for")
    assert gap_cues[1] == SubtitleCue(start=2.5, end=2.9, original="it")

    # Max 10 words
    many_words = [_w(i * 0.2, i * 0.2 + 0.15, f"w{i}") for i in range(12)]
    many_cues = agrupar_blocos(many_words, 0.0, 10.0)
    assert len(many_cues) == 2
    assert len(many_cues[0].original.split()) == 10
    assert len(many_cues[1].original.split()) == 2

    # Max duration 3.5s
    long_words = [_w(i * 1.0, i * 1.0 + 0.8, f"t{i}") for i in range(5)]
    long_cues = agrupar_blocos(long_words, 0.0, 10.0)
    assert len(long_cues) == 2
    assert long_cues[0].start == 0.0
    assert long_cues[0].end == 2.8


def test_traduzir_blocos_cloud_success_and_ass_sanitize(monkeypatch):
    cues = [
        SubtitleCue(start=1.0, end=2.0, original="What is this?"),
        SubtitleCue(start=2.2, end=3.2, original="Let's go!"),
    ]

    def fake_cloud(prompt, schema, system_prompt):
        return (
            {
                "blocos": [
                    {"id": 0, "pt": "O que é {isso}?\n\\N"},
                    {"id": 1, "pt": "Bora lá!"},
                ]
            },
            "cloud:gemini:gemini-2.5-flash",
        )

    def fail_local(*args, **kwargs):
        raise AssertionError("local llm should not be called when cloud succeeds")

    monkeypatch.setattr(cloud_llm, "complete_cloud", fake_cloud)
    monkeypatch.setattr(llm, "complete", fail_local)

    translated, fonte = traduzir_blocos(cues, "en")
    assert fonte == "cloud:gemini:gemini-2.5-flash"
    assert translated is not None
    assert translated[0].translated == "O que é (isso)? N"
    assert translated[1].translated == "Bora lá!"
    assert translated[0].start == 1.0 and translated[0].end == 2.0


def test_traduzir_blocos_falls_back_to_local_and_batches_over_12(monkeypatch):
    cues = [SubtitleCue(start=float(i), end=float(i) + 0.5, original=f"line {i}") for i in range(15)]
    calls: list[dict] = []

    monkeypatch.setattr(cloud_llm, "complete_cloud", lambda *a, **kw: None)

    def fake_local(prompt, schema=None, server_url=None, timeout=None, system_prompt=None, max_tokens=600):
        calls.append({"prompt": prompt, "max_tokens": max_tokens})
        start_tag = "<blocos>"
        end_tag = "</blocos>"
        raw = prompt.split(start_tag, 1)[1].split(end_tag, 1)[0]
        items = json.loads(raw)
        return {"blocos": [{"id": item["id"], "pt": f"linha {item['texto']}"} for item in items]}

    monkeypatch.setattr(llm, "complete", fake_local)

    translated, fonte = traduzir_blocos(cues, "en")
    assert fonte == "llm"
    assert translated is not None
    assert len(translated) == 15
    assert len(calls) == 2
    assert all(c["max_tokens"] == 1200 for c in calls)
    assert MAX_BLOCKS_PER_BATCH == 12


@pytest.mark.parametrize(
    "bad_response",
    [
        {"blocos": [{"id": 0, "pt": "Só um"}]},  # missing id 1
        {"blocos": [{"id": 0, "pt": ""}, {"id": 1, "pt": "ok"}]},  # empty pt
        {"blocos": [{"id": 0, "pt": "   "}, {"id": 1, "pt": "ok"}]},  # whitespace-only pt
        {"blocos": "not a list"},
    ],
)
def test_traduzir_blocos_invalid_json_returns_fallback(monkeypatch, bad_response):
    cues = [
        SubtitleCue(start=1.0, end=2.0, original="One"),
        SubtitleCue(start=2.1, end=3.0, original="Two"),
    ]
    monkeypatch.setattr(cloud_llm, "complete_cloud", lambda *a, **kw: (bad_response, "cloud:gemini:m1"))

    translated, fonte = traduzir_blocos(cues, "en")
    assert translated is None
    assert fonte == "fallback_original"


def test_traduzir_blocos_local_exception_returns_fallback(monkeypatch):
    cues = [SubtitleCue(start=1.0, end=2.0, original="One")]
    monkeypatch.setattr(cloud_llm, "complete_cloud", lambda *a, **kw: None)

    def boom(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(llm, "complete", boom)

    translated, fonte = traduzir_blocos(cues, "en")
    assert translated is None
    assert fonte == "fallback_original"


def test_traduzir_clipes_skips_pt_or_disabled_or_empty_words(monkeypatch):
    called = False

    def spy_cloud(*args, **kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(cloud_llm, "complete_cloud", spy_cloud)

    clip_pt = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, text="Olá mundo")
    words_pt = [_w(1.0, 1.5, "Olá"), _w(1.6, 2.0, "mundo")]

    traduzir_clipes([clip_pt], words_pt, "pt")
    assert called is False
    assert clip_pt.translated_cues is None
    assert clip_pt.legenda_traduzida is False
    assert clip_pt.legenda_fonte == "original"

    # CLIPES_LEGENDA_IDIOMA=off
    monkeypatch.setenv("CLIPES_LEGENDA_IDIOMA", "off")
    clip_en = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, text="Hello world")
    words_en = [_w(1.0, 1.5, "Hello"), _w(1.6, 2.0, "world")]
    traduzir_clipes([clip_en], words_en, "en")
    assert called is False
    assert clip_en.legenda_traduzida is False
    assert clip_en.legenda_fonte == "original"

    # Empty words in clip window
    monkeypatch.delenv("CLIPES_LEGENDA_IDIOMA", raising=False)
    clip_empty = SnappedClip(peak=50.0, start=40.0, end=60.0, score=0.9, text="")
    traduzir_clipes([clip_empty], words_en, "en")
    assert called is False
    assert clip_empty.legenda_traduzida is False
    assert clip_empty.legenda_fonte == "original"
