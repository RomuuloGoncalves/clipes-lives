"""Unit tests for CLI layout_key forwarding and clips.json layout fields (F5 T8)."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import numpy as np

from clipes import cli
from clipes.models import CandidatePeak, IngestResult, SignalBundle, SnappedClip, Word


def _make_ing(
    tmp_path: Path,
    *,
    video_id: str = "vod123",
    channel: str = "",
    url: str = "https://www.twitch.tv/videos/123456",
    extractor: str = "twitch:vod",
) -> IngestResult:
    out_dir = tmp_path / "out" / video_id.replace("/", "_")
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    wav_path = cache_dir / "audio.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    return IngestResult(
        video_id=video_id,
        title="Live Teste",
        url=url,
        extractor=extractor,
        is_live=False,
        duration=90.0,
        audio_path=wav_path,
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=out_dir,
        cache_dir=cache_dir,
        channel=channel,
    )


def _wire_fake_pipeline(monkeypatch, ing: IngestResult) -> None:
    n = 90
    zeros = np.zeros(n)
    bundle = SignalBundle(
        duration=ing.duration,
        n=n,
        scores={"loudness": zeros},
        combined=zeros.copy(),
        smoothed=zeros.copy(),
        agreeing=np.zeros(n, dtype=np.int32),
        weights={"loudness": 1.0},
        rms_db=np.full(n * 2, -30.0, dtype=np.float32),
    )
    peaks = [CandidatePeak(peak=20.0, raw_score=0.9, start_win=10.0, end_win=40.0)]
    clips = [
        SnappedClip(
            peak=20.0,
            start=10.0,
            end=35.0,
            score=0.9,
            text="Jogada absurda agora",
            title="Jogada absurda",
        )
    ]
    words = [Word(10.0, 11.0, "Jogada"), Word(11.1, 12.0, "absurda")]
    monkeypatch.setattr(cli, "ingest", lambda **kwargs: ing)
    monkeypatch.setattr(cli, "compute_rms_db", lambda *a, **k: np.zeros(4, np.float32))
    monkeypatch.setattr(cli, "compute_signals", lambda **k: bundle)
    monkeypatch.setattr(cli, "pick_candidate_peaks", lambda *a, **k: peaks)
    monkeypatch.setattr(cli, "transcribe_candidates", lambda **k: (words, "pt"))
    monkeypatch.setattr(cli, "select_clips", lambda **k: clips)


def test_cli_passes_layout_key_from_channel_and_writes_manifest_fields(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIPES_BLUR_BG", raising=False)
    ing = _make_ing(tmp_path, video_id="v2931573802", channel="twitch.tv/alanzoka")
    _wire_fake_pipeline(monkeypatch, ing)

    seen_calls: list[dict[str, Any]] = []

    def fake_render_clip(
        clip,
        idx,
        words,
        url,
        out_dir,
        cache_dir,
        live_video_path=None,
        channel_credit="",
        reframe=False,
        layout_key="",
    ):
        seen_calls.append({"channel_credit": channel_credit, "layout_key": layout_key})
        clip.layout = "split_webcam"
        clip.layout_fallback = False
        clip.facecam = {"rect": [0.01, 0.01, 0.22, 0.28], "metodo": "yunet+bordas"}
        out_mp4 = out_dir / f"clip_{idx:02d}.mp4"
        out_mp4.write_bytes(b"mp4")
        return out_mp4

    monkeypatch.setattr(cli, "render_clip", fake_render_clip)
    args = cli.build_parser().parse_args([
        ing.url, "--out-dir", str(tmp_path / "out"), "--no-llm", "--no-discord",
    ])
    assert cli.run_pipeline(args) == 0

    assert seen_calls == [{"channel_credit": "twitch.tv/alanzoka", "layout_key": "alanzoka"}]
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    entry = manifest["clips"][0]
    assert entry["layout"] == "split_webcam"
    assert entry["layout_fallback"] is False
    assert entry["facecam"] == {"rect": [0.01, 0.01, 0.22, 0.28], "metodo": "yunet+bordas"}


def test_cli_layout_key_strips_at_sanitizes_and_falls_back_to_video_id(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIPES_BLUR_BG", raising=False)

    # 1) @handle channel without --channel
    ing_yt = _make_ing(
        tmp_path / "yt",
        video_id="yt999",
        channel="@Cortes_Bistecone",
        url="https://www.youtube.com/watch?v=yt999",
        extractor="youtube",
    )
    _wire_fake_pipeline(monkeypatch, ing_yt)
    keys: list[str] = []

    def fake_render(
        clip, idx, words, url, out_dir, cache_dir,
        live_video_path=None, channel_credit="", reframe=False, layout_key="",
    ):
        keys.append(layout_key)
        clip.layout = "black_bars_16x9"
        clip.layout_fallback = True
        clip.facecam = {"rect": [0.75, 0.02, 0.22, 0.28], "metodo": "override"}
        out_mp4 = out_dir / f"clip_{idx:02d}.mp4"
        out_mp4.write_bytes(b"mp4")
        return out_mp4

    monkeypatch.setattr(cli, "render_clip", fake_render)
    args_yt = cli.build_parser().parse_args([
        ing_yt.url, "--out-dir", str(tmp_path / "yt" / "out"), "--no-llm", "--no-discord",
    ])
    assert cli.run_pipeline(args_yt) == 0
    assert keys[-1] == "Cortes_Bistecone"
    saved_yt = json.loads((ing_yt.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert saved_yt["clips"][0]["layout_fallback"] is True
    assert saved_yt["clips"][0]["layout"] == "black_bars_16x9"

    # 2) --channel override with spaces/symbols gets sanitized
    args_override = cli.build_parser().parse_args([
        ing_yt.url, "--out-dir", str(tmp_path / "yt" / "out"),
        "--channel", "Canal Novo! 2", "--no-llm", "--no-discord",
    ])
    assert cli.run_pipeline(args_override) == 0
    assert keys[-1] == "Canal_Novo__2"

    # 3) No channel -> falls back to sanitized video_id
    ing_novar = _make_ing(
        tmp_path / "vod",
        video_id="vod/987",
        channel="",
        url="https://www.twitch.tv/videos/987",
        extractor="twitch:vod",
    )
    _wire_fake_pipeline(monkeypatch, ing_novar)
    args_novar = cli.build_parser().parse_args([
        ing_novar.url, "--out-dir", str(tmp_path / "vod" / "out"), "--no-llm", "--no-discord",
    ])
    assert cli.run_pipeline(args_novar) == 0
    assert keys[-1] == "vod_987"


def test_cli_respects_render_clip_without_layout_key_and_skip_render(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIPES_BLUR_BG", raising=False)
    ing = _make_ing(tmp_path, video_id="legacy1", channel="twitch.tv/gaules")
    _wire_fake_pipeline(monkeypatch, ing)

    # Legacy fake signature without layout_key must still succeed
    def legacy_render(clip, idx, words, url, out_dir, cache_dir, live_video_path=None, channel_credit="", reframe=False):
        out_mp4 = out_dir / f"clip_{idx:02d}.mp4"
        out_mp4.write_bytes(b"mp4")
        return out_mp4

    monkeypatch.setattr(cli, "render_clip", legacy_render)
    args = cli.build_parser().parse_args([
        ing.url, "--out-dir", str(tmp_path / "out"), "--no-llm", "--no-discord",
    ])
    assert cli.run_pipeline(args) == 0
    saved = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert saved["clips"][0]["layout"] == "blurred_16x9"
    assert saved["clips"][0]["layout_fallback"] is False
    assert saved["clips"][0]["facecam"] is None

    # --skip-render populates default layout fields without calling render_clip
    args_skip = cli.build_parser().parse_args([
        ing.url, "--out-dir", str(tmp_path / "out"), "--no-llm", "--no-discord", "--skip-render",
    ])
    assert cli.run_pipeline(args_skip) == 0
    saved_skip = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert saved_skip["clips"][0]["layout"] == "blurred_16x9"
    assert saved_skip["clips"][0]["layout_fallback"] is False
    assert saved_skip["clips"][0]["facecam"] is None


def test_cli_resolve_layout_key_strips_kick_prefixes_and_help_mentions_kick():
    assert cli._resolve_layout_key("https://www.kick.com/foo-bar", "vid1") == "foo-bar"
    assert cli._resolve_layout_key("kick.com/foo-bar", "vid1") == "foo-bar"
    assert cli._resolve_layout_key("http://kick.com/foo-bar/", "vid1") == "foo-bar"
    assert cli._resolve_layout_key("https://www.twitch.tv/gaules", "vid1") == "gaules"
    assert "Kick" in cli.build_parser().format_help()


def _run_gate_pipeline(tmp_path, monkeypatch, words_first=10.0):
    from clipes import gate
    ing = _make_ing(tmp_path, video_id="gate1", channel="twitch.tv/x")
    _wire_fake_pipeline(monkeypatch, ing)
    monkeypatch.setattr(cli, "transcribe_candidates", lambda **k: (
        [Word(words_first, words_first + 1, "Jogada"), Word(words_first + 1.1, words_first + 2, "absurda")], "pt"))
    monkeypatch.setattr(gate, "dimensoes", lambda mp4, run=None: (1080, 1920))
    monkeypatch.setattr(gate, "dhash", lambda mp4, t=0.0, run=None: 12345)

    def fake_render(clip, idx, words, url, out_dir, cache_dir, live_video_path=None, **kw):
        out = out_dir / f"clip_{idx:02d}.mp4"
        out.write_bytes(b"mp4")
        return out

    monkeypatch.setattr(cli, "render_clip", fake_render)
    args = cli.build_parser().parse_args([ing.url, "--out-dir", str(tmp_path / "out"), "--no-llm", "--no-discord"])
    assert cli.run_pipeline(args) == 0
    return json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))["clips"][0]


def test_clips_json_traz_gate_phash_texto_hash(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_GATE", raising=False)
    e = _run_gate_pipeline(tmp_path, monkeypatch)
    assert isinstance(e["gate"]["ok"], bool) and "motivos" in e["gate"]
    assert e["phash"] == 12345
    assert len(e["texto_hash"]) == 12
    assert e["layout"] != "black_bars_16x9"
    assert "hook_cabe" in e


def test_gate_off_nao_grava_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPES_GATE", "off")
    assert "gate" not in _run_gate_pipeline(tmp_path, monkeypatch)


def test_start_ajustado_quando_primeira_palavra_em_mais_3s(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_GATE", raising=False)
    e = _run_gate_pipeline(tmp_path, monkeypatch, words_first=13.0)
    assert 12.5 <= e["start"] < 13.0


def test_duracao_padrao_15_45(monkeypatch):
    monkeypatch.delenv("CLIPES_DUR_MIN", raising=False)
    monkeypatch.delenv("CLIPES_DUR_MAX", raising=False)
    a = cli.build_parser().parse_args(["http://x"])
    assert (a.min_dur, a.max_dur) == (15.0, 45.0)
    monkeypatch.setenv("CLIPES_DUR_MIN", "20")
    assert cli.build_parser().parse_args(["http://x"]).min_dur == 20.0
