"""Tests for CLI integration of legenda-hashtags (T5): --no-llm, ordering, manifest, README, and .txt captions."""

from __future__ import annotations

import json
import time
import wave
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from clipes import cli, llm
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
    assert c0["title"] == "Olha esse clutch!"
    assert c0["title_source"] == "heuristic_fallback"
    assert c0["llm_score"] is None
    assert c0["llm_reason"] == ""
    assert 3 <= len(c0["hashtags"]) <= 5
    assert len(c0["caption"].splitlines()) == 3
    assert "twitch.tv/gaules" in c0["caption"].splitlines()[1]

    txt_path = ing.out_dir / "clip_01.txt"
    assert txt_path.is_file()
    assert txt_path.read_text(encoding="utf-8") == c0["caption"]

    readme = (ing.out_dir / "README.md").read_text(encoding="utf-8")
    assert "`llm_s`:" in readme
    assert "heuristic_fallback" in readme
    assert c0["hashtags"][0] in readme
    assert c0["caption"] in readme


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
    assert c0["title"] == "Nossa senhora!"
    assert c0["title_source"] == "heuristic_fallback"
    assert c0["llm_score"] is None
    assert 3 <= len(c0["hashtags"]) <= 5
    assert len(c0["caption"].splitlines()) == 3
    assert (ing.out_dir / "clip_01.txt").is_file()


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
