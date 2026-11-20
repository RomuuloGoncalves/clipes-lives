"""Tests for CLI integration of legenda-hashtags (T5): --no-llm, ordering, manifest, README, and .txt captions."""

from __future__ import annotations

import json
import time
import wave
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from clipes import cli, cloud_llm, llm
from clipes.models import CandidatePeak, IngestResult, SnappedClip, Word


def _make_ing(tmp_path: Path, video_id: str = "cli_test") -> IngestResult:
    out_dir = tmp_path / "out" / video_id
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(parents=True)
    wav_path = cache_dir / "audio.wav"
    sr = 16000
    duration = 120.0
    samples = (np.ones(int(sr * duration), dtype=np.int16) * 200).tobytes()
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples)
    return IngestResult(
        video_id=video_id,
        title="Live do Gaules no CS2",
        url="https://www.twitch.tv/gaules",
        extractor="twitch:vod",
        is_live=False,
        duration=duration,
        audio_path=wav_path,
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=out_dir,
        cache_dir=cache_dir,
        channel="twitch.tv/gaules",
    )


def _mock_base_stages(monkeypatch, ing: IngestResult, clips: list[SnappedClip], words: list[Word]):
    monkeypatch.setattr(cli, "ingest", lambda **kwargs: ing)
    monkeypatch.setattr(
        cli,
        "pick_candidate_peaks",
        lambda *a, **kw: [CandidatePeak(peak=40.0, raw_score=0.9, start_win=0.0, end_win=90.0)],
    )
    monkeypatch.setattr(cli, "transcribe_candidates", lambda **kwargs: (words, "pt"))
    monkeypatch.setattr(cli, "select_clips", lambda *a, **kw: list(clips))


def test_cli_no_llm_flag_fills_deterministic_metadata_fast(tmp_path: Path, monkeypatch):
    ing = _make_ing(tmp_path, "no_llm_vid")
    words = [Word(30.0, 30.5, " Olha"), Word(30.6, 31.1, " esse"), Word(31.2, 31.8, " clutch!")]
    clip = SnappedClip(
        peak=31.0,
        start=20.0,
        end=50.0,
        score=0.88,
        active_signals=["loudness", "chat"],
        text="Olha esse clutch!",
    )
    _mock_base_stages(monkeypatch, ing, [clip], words)

    session_called = {"count": 0}

    @contextmanager
    def fail_if_called(*args, **kwargs):
        session_called["count"] += 1
        raise AssertionError("llama_server_session must not be started with --no-llm")
        yield ""

    monkeypatch.setattr(llm, "llama_server_session", fail_if_called)

    t0 = time.perf_counter()
    rc = cli.main([
        "https://www.twitch.tv/gaules",
        "--out-dir", str(tmp_path / "out"),
        "--no-llm",
        "--skip-render",
    ])
    elapsed = time.perf_counter() - t0

    assert rc == 0
    assert session_called["count"] == 0
    assert elapsed < 1.0

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert "llm_s" in manifest["timings"]
    assert manifest["timings"]["llm_s"] < 0.05

    c0 = manifest["clips"][0]
    assert c0["file"] == "clip_01_olha-esse-clutch.mp4"
    assert c0["clip_id"] == "no_llm_vid_c01"
    assert c0["title"] == "Olha esse clutch!"
    assert c0["title_source"] == "heuristic_fallback"
    assert c0["hook_top"] == "Olha esse clutch!"
    assert c0["description_source"] == "fallback"
    assert c0["description_long"].startswith("Olha esse clutch!\n\nOlha esse clutch!")
    assert c0["llm_score"] is None
    assert c0["llm_reason"] == ""
    assert 1 <= len(c0["hashtags"]) <= 3
    assert len(c0["caption"].splitlines()) == 3
    assert "twitch.tv/gaules" in c0["caption"].splitlines()[1]
    assert "http" not in c0["caption"]

    txt_path = ing.out_dir / "clip_01_olha-esse-clutch.txt"
    assert txt_path.is_file()
    assert txt_path.read_text(encoding="utf-8") == c0["caption"]

    desc_path = ing.out_dir / "clip_01_olha-esse-clutch.descricao.txt"
    assert desc_path.is_file()
    assert desc_path.read_text(encoding="utf-8") == c0["description_long"]

    readme = (ing.out_dir / "README.md").read_text(encoding="utf-8")
    assert "`llm_s`:" in readme
    assert "heuristic_fallback" in readme
    assert c0["hashtags"][0] in readme
    assert c0["caption"] in readme
    assert "Texto da transcrição enviado a:" not in readme


def test_cli_enriches_clips_between_snap_and_render_and_writes_txt(tmp_path: Path, monkeypatch):
    ing = _make_ing(tmp_path, "llm_order")
    words = [Word(30.0, 30.5, " Jogada"), Word(30.6, 31.2, " absurda!")]
    c1 = SnappedClip(
        peak=31.0,
        start=20.0,
        end=50.0,
        score=0.90,
        active_signals=["loudness"],
        text="Jogada absurda no round final!",
    )
    c2 = SnappedClip(
        peak=70.0,
        start=55.0,
        end=85.0,
        score=0.80,
        active_signals=["chat"],
        text="Parte um da história!",
        part_index=1,
        part_total=2,
        group_id="g1",
    )

    stages: list[str] = []
    monkeypatch.setattr(cli, "ingest", lambda **kwargs: ing)
    monkeypatch.setattr(
        cli,
        "pick_candidate_peaks",
        lambda *a, **kw: [CandidatePeak(peak=31.0, raw_score=0.9, start_win=0.0, end_win=90.0)],
    )
    monkeypatch.setattr(cli, "transcribe_candidates", lambda **kwargs: (words, "pt"))

    def fake_select(*a, **kw):
        stages.append("snap")
        return [c1, c2]

    def fake_enrich(clips, w, ing_arg, enabled=True):
        stages.append(f"llm:{enabled}")
        assert w == words
        assert ing_arg is ing
        clips[0].title = "Que jogada absurda!"
        clips[0].title_source = "llm"
        clips[0].hashtags = ["#gaules", "#cs2br", "#cortes"]
        clips[0].llm_score = 8.7
        clips[0].llm_reason = "Clímax forte no final."
        clips[0].caption = (
            "Você viu esse round?\n"
            "Crédito: twitch.tv/gaules · Live completa: https://www.twitch.tv/gaules\n"
            "#gaules #cs2br #cortes"
        )
        clips[1].title = "Parte 1 da história!"
        clips[1].title_source = "llm"
        clips[1].hashtags = ["#gaules", "#cortes", "#livesbr"]
        clips[1].llm_score = 7.5
        clips[1].llm_reason = "Bom gancho inicial."
        clips[1].caption = (
            "Começo da resenha!\n"
            "Crédito: twitch.tv/gaules · Live completa: https://www.twitch.tv/gaules · Parte 1/2\n"
            "#gaules #cortes #livesbr"
        )

    heuristic_calls: list[SnappedClip] = []
    monkeypatch.setattr(cli, "select_clips", fake_select)
    monkeypatch.setattr(cli, "enrich_clips_with_llm", fake_enrich)
    monkeypatch.setattr(cli, "generate_title", lambda c, w: (heuristic_calls.append(c), "Fallback")[1])

    def fake_render(clip, idx, words, url, out_dir, cache_dir, live_video_path=None, **kwargs):
        stages.append(f"render:{idx}")
        stem = f"clip_{idx:02d}_part{clip.part_index}of{clip.part_total}" if clip.part_total > 1 else f"clip_{idx:02d}"
        out_file = out_dir / f"{stem}.mp4"
        out_file.write_bytes(b"mp4")
        return out_file

    monkeypatch.setattr(cli, "render_clip", fake_render)

    rc = cli.main(["https://www.twitch.tv/gaules", "--out-dir", str(tmp_path / "out")])

    assert rc == 0
    assert stages == ["snap", "llm:True", "render:1", "render:2"]
    assert heuristic_calls == []  # generate_title NOT called when LLM filled clip.title

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert "llm_s" in manifest["timings"]
    m1, m2 = manifest["clips"]
    assert m1["title"] == "Que jogada absurda!"
    assert m1["title_source"] == "llm"
    assert m1["llm_score"] == 8.7
    assert m1["llm_reason"] == "Clímax forte no final."
    assert m1["hashtags"] == ["#gaules", "#cs2br", "#cortes"]
    assert len(m1["caption"].splitlines()) == 3

    assert (ing.out_dir / "clip_01.txt").read_text(encoding="utf-8") == m1["caption"]
    assert (ing.out_dir / "clip_02_part1of2.txt").read_text(encoding="utf-8") == m2["caption"]

    readme = (ing.out_dir / "README.md").read_text(encoding="utf-8")
    assert "8.7/10" in readme
    assert "Clímax forte no final." in readme
    assert "#cs2br" in readme
    assert "Você viu esse round?" in readme


def test_cli_llm_failure_falls_back_deterministically_without_aborting(tmp_path: Path, monkeypatch):
    ing = _make_ing(tmp_path, "llm_crash")
    words = [Word(30.0, 30.5, " Nossa"), Word(30.6, 31.1, " senhora!")]
    clip = SnappedClip(
        peak=31.0,
        start=20.0,
        end=50.0,
        score=0.82,
        active_signals=["loudness"],
        text="Nossa senhora!",
    )
    _mock_base_stages(monkeypatch, ing, [clip], words)
    monkeypatch.setattr(
        cli,
        "enrich_clips_with_llm",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("llama-server boom")),
    )

    rc = cli.main([
        "https://www.twitch.tv/gaules",
        "--out-dir", str(tmp_path / "out"),
        "--skip-render",
    ])

    assert rc == 0
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert "llm_s" in manifest["timings"]
    c0 = manifest["clips"][0]
    assert c0["file"] == "clip_01_nossa-senhora.mp4"
    assert c0["clip_id"] == "llm_crash_c01"
    assert c0["title"] == "Nossa senhora!"
    assert c0["title_source"] == "heuristic_fallback"
    assert c0["description_source"] == "fallback"
    assert c0["description_long"]
    assert c0["llm_score"] is None
    assert 1 <= len(c0["hashtags"]) <= 3
    assert len(c0["caption"].splitlines()) == 3
    assert (ing.out_dir / "clip_01_nossa-senhora.txt").is_file()
    assert (ing.out_dir / "clip_01_nossa-senhora.descricao.txt").read_text(encoding="utf-8") == c0["description_long"]


def test_cli_generates_heuristic_title_only_when_title_empty_after_llm(tmp_path: Path, monkeypatch):
    ing = _make_ing(tmp_path, "partial_title")
    words = [Word(60.0, 60.5, " Que"), Word(60.6, 61.1, " susto!")]
    c_has_title = SnappedClip(peak=30.0, start=20.0, end=50.0, score=0.90, text="Momento um!")
    c_empty_title = SnappedClip(peak=60.5, start=50.0, end=80.0, score=0.80, text="Que susto!")
    _mock_base_stages(monkeypatch, ing, [c_has_title, c_empty_title], words)

    def partial_enrich(clips, w, ing_arg, enabled=True):
        clips[0].title = "Título gerado pelo LLM!"
        clips[0].title_source = "llm"
        clips[0].hashtags = ["#gaules", "#cortes", "#livesbr"]
        clips[0].caption = "L1\nCrédito: twitch.tv/gaules · Live completa: https://www.twitch.tv/gaules\n#gaules #cortes #livesbr"
        # clips[1] stays with empty title

    called_for: list[float] = []
    monkeypatch.setattr(cli, "enrich_clips_with_llm", partial_enrich)
    monkeypatch.setattr(
        cli,
        "generate_title",
        lambda c, w: (called_for.append(c.peak), "Que susto!")[1],
    )

    rc = cli.main([
        "https://www.twitch.tv/gaules",
        "--out-dir", str(tmp_path / "out"),
        "--skip-render",
    ])

    assert rc == 0
    assert called_for == [60.5]
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["clips"][0]["title"] == "Título gerado pelo LLM!"
    assert manifest["clips"][0]["title_source"] == "llm"
    assert manifest["clips"][1]["title"] == "Que susto!"
    assert manifest["clips"][1]["title_source"] == "heuristic_fallback"


def test_cli_skip_render_uses_clip_stem_and_writes_descricao_and_clip_id(tmp_path: Path, monkeypatch):
    ing = _make_ing(tmp_path, "abc/123!")
    words = [Word(30.0, 30.5, " Olha"), Word(30.6, 31.1, " isso!")]
    c1 = SnappedClip(
        peak=31.0, start=20.0, end=50.0, score=0.91,
        text="Ele não acreditou no que aconteceu naquela jogada absurda do round final.",
    )
    c2 = SnappedClip(
        peak=70.0, start=55.0, end=85.0, score=0.82,
        text="Começo da história que ninguém esperava ver na transmissão de hoje.",
        part_index=1, part_total=2, group_id="g1",
    )
    c3 = SnappedClip(
        peak=100.0, start=90.0, end=118.0, score=0.75,
        text="Momento sem letras no título para validar slug vazio.",
    )
    _mock_base_stages(monkeypatch, ing, [c1, c2, c3], words)

    def fake_enrich(clips, w, ing_arg, enabled=True):
        clips[0].title = "Ele NÃO acreditou no que viu?!"
        clips[0].title_source = "cloud:gemini:gemini-2.5-flash"
        clips[0].hook_top = "Ele perdeu tudo?"
        clips[0].hashtags = ["#gaules", "#cs2br", "#cortes"]
        clips[0].caption = "Gancho 1\nCrédito: twitch.tv/gaules\n#gaules #cs2br #cortes"
        clips[0].description_source = "llm"
        clips[0].description_long = (
            "Ele NÃO acreditou no que viu?!\n\n"
            "Parágrafo longo gerado pelo modelo descrevendo a jogada.\n\n"
            "Crédito: twitch.tv/gaules\n"
            "Live completa: https://www.twitch.tv/gaules\n\n"
            "#gaules #cs2br #cortes"
        )

        clips[1].title = "Começo da história!"
        clips[1].title_source = "llm"
        clips[1].hook_top = "Começo da história!"
        clips[1].hashtags = ["#gaules", "#cortes", "#livesbr"]
        clips[1].caption = "Gancho 2\nCrédito: twitch.tv/gaules · Parte 1/2\n#gaules #cortes #livesbr"
        clips[1].description_source = "llm"
        clips[1].description_long = (
            "Começo da história!\n\n"
            "Segunda descrição longa gerada pelo modelo para a parte 1.\n\n"
            "Crédito: twitch.tv/gaules\n"
            "Live completa: https://www.twitch.tv/gaules · Parte 1/2\n\n"
            "#gaules #cortes #livesbr"
        )

        clips[2].title = "!!!"
        clips[2].title_source = "heuristic_fallback"
        clips[2].hook_top = ""
        clips[2].hashtags = ["#gaules", "#cortes", "#livesbr"]
        clips[2].caption = "Gancho 3\nCrédito: twitch.tv/gaules\n#gaules #cortes #livesbr"
        clips[2].description_source = "fallback"
        clips[2].description_long = (
            "!!!\n\n"
            "Momento sem letras no título para validar slug vazio.\n\n"
            "Crédito: twitch.tv/gaules\n"
            "Live completa: https://www.twitch.tv/gaules\n\n"
            "#gaules #cortes #livesbr"
        )

    monkeypatch.setattr(cli, "enrich_clips_with_llm", fake_enrich)

    rc = cli.main(["https://www.twitch.tv/gaules", "--out-dir", str(tmp_path / "out"), "--skip-render"])
    assert rc == 0

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    m1, m2, m3 = manifest["clips"]

    assert m1["clip_id"] == "abc_123_c01"
    assert m1["file"] == "clip_01_ele-nao-acreditou-no-que-viu.mp4"
    assert m1["hook_top"] == "Ele perdeu tudo?"
    assert m1["description_source"] == "llm"
    assert (ing.out_dir / "clip_01_ele-nao-acreditou-no-que-viu.descricao.txt").read_text(encoding="utf-8") == m1["description_long"]

    assert m2["clip_id"] == "abc_123_c02"
    assert m2["file"] == "clip_02_part1of2_comeco-da-historia.mp4"
    assert (ing.out_dir / "clip_02_part1of2_comeco-da-historia.descricao.txt").read_text(encoding="utf-8") == m2["description_long"]

    assert m3["clip_id"] == "abc_123_c03"
    assert m3["file"] == "clip_03.mp4"
    assert m3["hook_top"] == ""
    assert m3["description_source"] == "fallback"
    assert (ing.out_dir / "clip_03.descricao.txt").read_text(encoding="utf-8") == m3["description_long"]


def test_cli_cloud_privacy_notice_written_only_when_cloud_used(tmp_path: Path, monkeypatch):
    ing_cloud = _make_ing(tmp_path, "cloud_vid")
    transcript = "O jogador conseguiu virar o round impossível no último segundo da partida e todo mundo gritou!"
    clip_cloud = SnappedClip(peak=31.0, start=20.0, end=50.0, score=0.90, text=transcript)
    _mock_base_stages(monkeypatch, ing_cloud, [clip_cloud], [Word(30.0, 31.0, " Virada!")])

    monkeypatch.setenv("GEMINI_API_KEYS", "secret-key-should-not-leak")
    monkeypatch.setenv("CLIPES_LLM_BACKEND", "auto")
    monkeypatch.setenv("CLIPES_GEMINI_MODELS", "gemini-2.5-flash")
    monkeypatch.setenv("CLIPES_GEMINI_MIN_INTERVAL_S", "0")
    monkeypatch.setenv("CLIPES_GEMINI_COTA_FILE", str(tmp_path / "cota.json"))

    long_desc = (
        "O jogador conseguiu virar um round impossível no último segundo da partida "
        "e todo mundo gritou sem acreditar na jogada decisiva que fechou a transmissão ao vivo."
    )
    assert 150 <= len(long_desc) <= 500
    monkeypatch.setattr(
        cloud_llm,
        "_call",
        lambda *a, **kw: {
            "titulo": "O jogador conseguiu virar no último segundo!",
            "gancho": "Ninguém esperava essa jogada no último segundo da partida.",
            "gancho_topo": "O jogador conseguiu virar?",
            "descricao_longa": long_desc,
            "hashtags": ["#gaules", "#cs2br", "#cortes"],
            "nota": 9.1,
            "motivo": "Clímax forte no fim do round.",
        },
    )

    rc = cli.main(["https://www.twitch.tv/gaules", "--out-dir", str(tmp_path / "out"), "--skip-render"])
    assert rc == 0

    readme_cloud = (ing_cloud.out_dir / "README.md").read_text(encoding="utf-8")
    assert "Texto da transcrição enviado a: gemini" in readme_cloud.splitlines()
    assert "secret-key-should-not-leak" not in readme_cloud

    manifest_cloud = json.loads((ing_cloud.out_dir / "clips.json").read_text(encoding="utf-8"))
    mc = manifest_cloud["clips"][0]
    assert mc["title_source"] == "cloud:gemini:gemini-2.5-flash"
    assert mc["hook_top"] == "O jogador conseguiu virar?"
    assert mc["description_source"] == "llm"
    assert mc["clip_id"] == "cloud_vid_c01"
    assert (ing_cloud.out_dir / "clip_01_o-jogador-conseguiu-virar-no-ultimo.descricao.txt").read_text(encoding="utf-8") == mc["description_long"]
    assert "secret-key-should-not-leak" not in json.dumps(manifest_cloud)

    # Segunda rodada na mesma instância do processo usando --no-llm: não pode herdar provedor anterior
    ing_local = _make_ing(tmp_path, "no_cloud_vid")
    clip_local = SnappedClip(peak=31.0, start=20.0, end=50.0, score=0.90, text=transcript)
    _mock_base_stages(monkeypatch, ing_local, [clip_local], [Word(30.0, 31.0, " Virada!")])

    rc_local = cli.main([
        "https://www.twitch.tv/gaules",
        "--out-dir", str(tmp_path / "out"),
        "--no-llm",
        "--skip-render",
    ])
    assert rc_local == 0
    readme_local = (ing_local.out_dir / "README.md").read_text(encoding="utf-8")
    assert "Texto da transcrição enviado a:" not in readme_local


def test_cli_cloud_cota_respects_out_dir(tmp_path: Path, monkeypatch):
    import io
    import urllib.error

    custom_out = tmp_path / "custom_out"
    env_out = tmp_path / "env_out"
    ing = _make_ing(tmp_path, "cota_out_dir")
    clip = SnappedClip(peak=31.0, start=20.0, end=50.0, score=0.90, text="Jogada absurda no round final!")
    _mock_base_stages(monkeypatch, ing, [clip], [Word(30.0, 31.0, " Jogada!")])

    monkeypatch.delenv("CLIPES_GEMINI_COTA_FILE", raising=False)
    monkeypatch.setenv("OUT_DIR", str(env_out))
    monkeypatch.setenv("GEMINI_API_KEYS", "secret-key-cota")
    monkeypatch.setenv("CLIPES_LLM_BACKEND", "auto")
    monkeypatch.setenv("CLIPES_GEMINI_MODELS", "m1")
    monkeypatch.setenv("CLIPES_GEMINI_MIN_INTERVAL_S", "0")
    monkeypatch.setattr(cloud_llm, "_out_dir", None)

    daily_body = json.dumps({
        "error": {
            "status": "RESOURCE_EXHAUSTED",
            "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}],
        }
    }).encode()

    def raise_daily(*a, **kw):
        raise urllib.error.HTTPError("u", 429, "err", {}, io.BytesIO(daily_body))

    monkeypatch.setattr(cloud_llm, "_call", raise_daily)
    monkeypatch.setattr(
        llm,
        "llama_server_session",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("sem local")),
    )

    rc = cli.main(["https://www.twitch.tv/gaules", "--out-dir", str(custom_out), "--skip-render"])
    assert rc == 0

    cota_file = custom_out / ".gemini_cota.json"
    assert cota_file.is_file()
    assert json.loads(cota_file.read_text(encoding="utf-8"))["esgotados"] == ["m1#0"]
    assert not (env_out / ".gemini_cota.json").exists()


def test_cli_empty_transcript_leaves_description_source_empty(tmp_path: Path, monkeypatch):
    ing = _make_ing(tmp_path, "empty_desc")
    clip = SnappedClip(peak=31.0, start=20.0, end=50.0, score=0.90, text="")
    _mock_base_stages(monkeypatch, ing, [clip], [])

    rc = cli.main([
        "https://www.twitch.tv/gaules",
        "--out-dir", str(tmp_path / "out"),
        "--no-llm",
        "--skip-render",
    ])
    assert rc == 0

    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    c0 = manifest["clips"][0]
    assert c0["description_long"] == ""
    assert c0["description_source"] == ""
    assert not list(ing.out_dir.glob("*.descricao.txt"))
