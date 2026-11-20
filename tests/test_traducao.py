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


def test_llm_complete_respects_custom_max_tokens():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    captured: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            captured.append(body)
            resp = json.dumps({"choices": [{"message": {"content": json.dumps({"blocos": []})}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        res = llm.complete("traduzir", schema=traducao.TRADUCAO_JSON_SCHEMA, server_url=url, max_tokens=1200)
        assert res == {"blocos": []}
        assert captured[0]["max_tokens"] == 1200
    finally:
        server.shutdown()
        server.server_close()


def test_enrich_clips_with_llm_accepts_pt_terms_from_translated_text(tmp_path, monkeypatch):
    from clipes.models import IngestResult

    ing = IngestResult(
        video_id="vid1",
        title="Live stream",
        url="https://twitch.tv/streamer",
        extractor="twitch",
        is_live=True,
        duration=120.0,
        audio_path=tmp_path / "audio.wav",
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=tmp_path,
        cache_dir=tmp_path,
        channel="streamer",
    )
    clip = SnappedClip(
        peak=5.0,
        start=0.0,
        end=10.0,
        score=0.9,
        text="We arrived in Tokyo and won five hundred dollars!",
        translated_text="Chegamos em Tóquio e ganhamos quinhentos dólares!",
    )
    words = [_w(1.0, 1.5, "We"), _w(1.6, 2.0, "arrived")]

    # Confirm without translated_text, "Tóquio" would be flagged as ungrounded in English transcript
    assert llm.validate_title("Vitória épica em Tóquio!", clip.text, "Live stream streamer") is False

    monkeypatch.setattr(
        cloud_llm,
        "complete_cloud",
        lambda *a, **kw: (
            {
                "titulo": "Vitória épica em Tóquio!",
                "gancho": "Chegamos em Tóquio com tudo!",
                "gancho_topo": "Chegamos em Tóquio!",
                "descricao_longa": "Um momento incrível da live quando chegamos em Tóquio e comemoramos muito com o chat ao vivo.",
                "hashtags": ["#cortes", "#livesbr", "#streamer"],
                "nota": 8.5,
                "motivo": "Clímax forte.",
            },
            "cloud:gemini:gemini-2.5-flash",
        ),
    )

    llm.enrich_clips_with_llm([clip], words, ing, enabled=True)
    assert clip.title == "Vitória épica em Tóquio!"
    assert clip.title_source == "cloud:gemini:gemini-2.5-flash"

    # Fallback mode also uses translated_text when enabled=False
    clip_fb = SnappedClip(
        peak=5.0,
        start=0.0,
        end=10.0,
        score=0.9,
        text="We arrived in Tokyo!",
        translated_text="Chegamos em Tóquio agora!",
    )
    llm.enrich_clips_with_llm([clip_fb], words, ing, enabled=False)
    assert clip_fb.title_source == "heuristic_fallback"
    assert "Tóquio" in clip_fb.title
    assert "Tóquio" in clip_fb.description_long


def test_providers_used_preserved_across_traduzir_clipes_and_enrich_clips(tmp_path, monkeypatch):
    from clipes.models import IngestResult

    monkeypatch.setenv("GEMINI_API_KEYS", "AIzaFAKE-KEY")
    monkeypatch.setenv("CLIPES_GEMINI_MODELS", "gemini-2.5-flash")
    monkeypatch.setenv("CLIPES_GEMINI_MIN_INTERVAL_S", "0")
    monkeypatch.setenv("CLIPES_GEMINI_COTA_FILE", str(tmp_path / "cota.json"))
    monkeypatch.delenv("CLIPES_LLM_BACKEND", raising=False)
    monkeypatch.delenv("CLIPES_LEGENDA_IDIOMA", raising=False)
    cloud_llm.reset_round()

    ing = IngestResult(
        video_id="vid2",
        title="English Live",
        url="https://twitch.tv/streamer",
        extractor="twitch",
        is_live=True,
        duration=60.0,
        audio_path=tmp_path / "audio.wav",
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=tmp_path,
        cache_dir=tmp_path,
        channel="streamer",
    )
    words = [_w(1.0, 1.5, "What"), _w(1.6, 2.2, "happened?")]
    clip = SnappedClip(peak=1.5, start=0.0, end=5.0, score=0.95, text="What happened?")

    # 1) Translation succeeds via cloud_llm, metadata enrichment fails cloud & local -> "gemini" must remain
    calls = {"n": 0}

    def fake_call(model, key, prompt, schema, system_prompt, urlopen):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"blocos": [{"id": 0, "pt": "O que aconteceu?"}]}
        raise RuntimeError("cloud quota exhausted on metadata step")

    monkeypatch.setattr(cloud_llm, "_call", fake_call)
    monkeypatch.setattr(llm, "llama_server_session", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("no bin")))

    traduzir_clipes([clip], words, "en", enabled=True)
    assert clip.legenda_traduzida is True
    assert clip.translated_text == "O que aconteceu?"
    assert cloud_llm.providers_used() == ["gemini"]

    llm.enrich_clips_with_llm([clip], words, ing, enabled=True)
    assert cloud_llm.providers_used() == ["gemini"]
    assert clip.title_source == "heuristic_fallback"
    assert "aconteceu" in clip.title.lower()


def test_build_ass_translated_cues_parity_pt_and_bilingual(tmp_path, monkeypatch):
    from clipes import render

    words = [
        _w(1.0, 1.4, "What"),
        _w(1.4, 1.8, "is"),
        _w(1.8, 2.2, "happening?"),
    ]
    cues = [
        SubtitleCue(
            start=1.0,
            end=2.2,
            original="What is happening?",
            translated="O que está acontecendo agora?",
        ),
        SubtitleCue(
            start=2.8,
            end=4.5,
            original="Let's go!",
            translated="Bora lá!",
        ),
    ]

    # 1) Parity: translated_cues=None must match word-by-word karaoke bit-for-bit
    ass_default = tmp_path / "default.ass"
    ass_none = tmp_path / "none.ass"
    n_def = render.build_ass(words, 0.0, 6.0, ass_default, channel_credit="@streamer")
    n_none = render.build_ass(words, 0.0, 6.0, ass_none, channel_credit="@streamer", translated_cues=None)
    assert n_def == n_none
    assert ass_default.read_text(encoding="utf-8") == ass_none.read_text(encoding="utf-8")

    # 2) Translated pure PT-BR mode (CLIPES_LEGENDA_ORIGINAL unset / 0)
    monkeypatch.delenv("CLIPES_LEGENDA_ORIGINAL", raising=False)
    assert render.is_bilingual_subtitle_enabled() is False
    ass_pt = tmp_path / "pt.ass"
    render.build_ass(words, 0.0, 6.0, ass_pt, channel_credit="@streamer", translated_cues=cues)
    pt_lines = [line for line in ass_pt.read_text(encoding="utf-8").splitlines() if ",Cap,," in line]
    assert len(pt_lines) == 2
    assert pt_lines[0].startswith("Dialogue: 0,0:00:01.00,0:00:02.20,Cap,,0,0,0,,")
    assert "O que está" in pt_lines[0] and "acontecendo agora?" in pt_lines[0]
    assert pt_lines[1].startswith("Dialogue: 0,0:00:02.80,0:00:04.50,Cap,,0,0,0,,Bora lá!")
    assert "What is happening?" not in ass_pt.read_text(encoding="utf-8")

    # 3) Bilingual mode (CLIPES_LEGENDA_ORIGINAL=1)
    monkeypatch.setenv("CLIPES_LEGENDA_ORIGINAL", "1")
    assert render.is_bilingual_subtitle_enabled() is True
    ass_bi = tmp_path / "bi.ass"
    render.build_ass(words, 0.0, 6.0, ass_bi, channel_credit="@streamer", translated_cues=cues)
    bi_lines = [line for line in ass_bi.read_text(encoding="utf-8").splitlines() if ",Cap,," in line]
    assert len(bi_lines) == 2
    assert r"{\fs44\c&H00D0D0D0&}What is happening?\N{\fs64\c&H00FFFFFF&}" in bi_lines[0]
    assert r"{\fs44\c&H00D0D0D0&}Let's go!\N{\fs64\c&H00FFFFFF&}Bora lá!" in bi_lines[1]


def test_cli_run_pipeline_translates_non_pt_and_writes_manifest(tmp_path, monkeypatch):
    import wave
    import numpy as np
    from clipes import cli
    from clipes.models import CandidatePeak, IngestResult

    def _setup_ing(vid: str) -> IngestResult:
        out_dir = tmp_path / "out" / vid
        cache_dir = out_dir / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        wav_path = cache_dir / "audio.wav"
        sr = 16000
        duration = 90.0
        samples = (np.ones(int(sr * duration), dtype=np.int16) * 200).tobytes()
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(samples)
        return IngestResult(
            video_id=vid,
            title="English Stream",
            url="https://www.twitch.tv/streamer",
            extractor="twitch:vod",
            is_live=False,
            duration=duration,
            audio_path=wav_path,
            video_path=None,
            heatmap=None,
            chat_messages=None,
            out_dir=out_dir,
            cache_dir=cache_dir,
            channel="twitch.tv/streamer",
        )

    words = [_w(30.0, 30.4, "What"), _w(30.4, 30.8, "a"), _w(30.8, 31.3, "play!")]

    def _mock_pipeline(ing: IngestResult, detected_lang: str):
        clip = SnappedClip(peak=31.0, start=20.0, end=50.0, score=0.9, text="What a play!")
        monkeypatch.setattr(cli, "ingest", lambda **kwargs: ing)
        monkeypatch.setattr(
            cli,
            "pick_candidate_peaks",
            lambda *a, **kw: [CandidatePeak(peak=31.0, raw_score=0.9, start_win=0.0, end_win=80.0)],
        )
        monkeypatch.setattr(cli, "transcribe_candidates", lambda **kwargs: (words, detected_lang))
        monkeypatch.setattr(cli, "select_clips", lambda *a, **kw: [clip])
        monkeypatch.setattr(cli, "enrich_clips_with_llm", lambda *a, **kw: None)
        return clip

    # 1) lang="en" translates and records legenda_traduzida=True
    monkeypatch.delenv("CLIPES_LEGENDA_IDIOMA", raising=False)
    ing_en = _setup_ing("vid_en")
    clip_en = _mock_pipeline(ing_en, "en")
    monkeypatch.setattr(
        cloud_llm,
        "complete_cloud",
        lambda *a, **kw: ({"blocos": [{"id": 0, "pt": "Que jogada!"}]}, "cloud:gemini:gemini-2.5-flash"),
    )
    rc = cli.main(["https://www.twitch.tv/streamer", "--out-dir", str(tmp_path / "out"), "--skip-render"])
    assert rc == 0
    assert clip_en.translated_cues is not None
    manifest_en = json.loads((ing_en.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert "traducao_s" in manifest_en["timings"]
    c0_en = manifest_en["clips"][0]
    assert c0_en["legenda_traduzida"] is True
    assert c0_en["legenda_idioma"] == "pt"
    assert c0_en["legenda_fonte"] == "cloud:gemini:gemini-2.5-flash"

    # 2) lang="pt", --no-llm, and CLIPES_LEGENDA_IDIOMA=off never call translator
    def fail_cloud(*a, **kw):
        raise AssertionError("translator should not be called")

    monkeypatch.setattr(cloud_llm, "complete_cloud", fail_cloud)

    ing_pt = _setup_ing("vid_pt")
    _mock_pipeline(ing_pt, "pt")
    assert cli.main(["https://www.twitch.tv/streamer", "--out-dir", str(tmp_path / "out"), "--skip-render"]) == 0
    c0_pt = json.loads((ing_pt.out_dir / "clips.json").read_text(encoding="utf-8"))["clips"][0]
    assert c0_pt["legenda_traduzida"] is False
    assert c0_pt["legenda_fonte"] == "original"

    ing_nollm = _setup_ing("vid_nollm")
    _mock_pipeline(ing_nollm, "en")
    assert (
        cli.main(["https://www.twitch.tv/streamer", "--out-dir", str(tmp_path / "out"), "--no-llm", "--skip-render"])
        == 0
    )
    c0_nollm = json.loads((ing_nollm.out_dir / "clips.json").read_text(encoding="utf-8"))["clips"][0]
    assert c0_nollm["legenda_traduzida"] is False
    assert c0_nollm["legenda_fonte"] == "original"

    monkeypatch.setenv("CLIPES_LEGENDA_IDIOMA", "off")
    ing_off = _setup_ing("vid_off")
    _mock_pipeline(ing_off, "en")
    assert cli.main(["https://www.twitch.tv/streamer", "--out-dir", str(tmp_path / "out"), "--skip-render"]) == 0
    c0_off = json.loads((ing_off.out_dir / "clips.json").read_text(encoding="utf-8"))["clips"][0]
    assert c0_off["legenda_traduzida"] is False
    assert c0_off["legenda_fonte"] == "original"
