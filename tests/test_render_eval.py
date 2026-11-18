"""Tests for ASS caption generation, title heuristic, and evaluation metrics."""

from pathlib import Path

from clipes.eval import evaluate_clips
from clipes.models import SnappedClip, Word
from clipes.render import build_ass, generate_title


def test_build_ass_and_empty_ass(tmp_path: Path):
    words = [
        Word(10.0, 10.4, " Olha"),
        Word(10.5, 10.9, " isso"),
        Word(11.0, 11.4, " mano!"),
        Word(12.2, 12.6, " Caralho!"),
    ]
    ass_path = tmp_path / "subs.ass"
    count = build_ass(words, clip_start=9.5, clip_end=15.0, ass_path=ass_path, channel_credit="twitch.tv/gaules")
    assert count == 7  # 2 Credit/Watermark + 1 Card + 4 word dialogues
    content = ass_path.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "120,200,560,1" in content
    assert "Style: Credit" in content
    assert "Style: Card" in content
    assert r"\pos(120,600)\fad(300,400)" in content
    assert r"\pos(120,600)\alpha&H99&" in content
    assert r"\pos(540,960)\fad(250,0)" in content
    assert "Live completa: twitch.tv/gaules" in content
    assert "Dialogue:" in content
    assert "mano!" in content

    # Even with zero speech words, Credit and Card events must be emitted
    empty_ass = tmp_path / "empty.ass"
    empty_count = build_ass([], clip_start=0.0, clip_end=10.0, ass_path=empty_ass, channel_credit="@podpah")
    assert empty_count == 3  # 2 Credit + 1 Card
    empty_txt = empty_ass.read_text(encoding="utf-8")
    assert "Live completa: @podpah" in empty_txt
    assert r"\alpha&H99&" in empty_txt

    # Short clip D < 4.0s must clamp Credit to [0, D] with no negative/inverted watermark interval
    short_ass = tmp_path / "short.ass"
    short_count = build_ass([], clip_start=0.0, clip_end=2.5, ass_path=short_ass, channel_credit="twitch.tv/gaules")
    assert short_count == 2  # 1 Credit (0..2.5) + 1 Card (2.5..3.7)
    short_txt = short_ass.read_text(encoding="utf-8")
    assert "0:00:00.00,0:00:02.50,Credit" in short_txt
    assert r"\alpha&H99&" not in short_txt
    assert "0:00:02.50,0:00:03.70,Card" in short_txt


def test_generate_title_heuristic():
    words = [
        Word(5.0, 5.4, " Vamos"),
        Word(5.5, 6.0, " ver."),
        Word(12.0, 12.5, " Caralho,"),
        Word(12.6, 13.0, " olha"),
        Word(13.1, 13.6, " esse"),
        Word(13.7, 14.3, " susto!"),
    ]
    clip = SnappedClip(
        peak=13.5, start=4.0, end=32.0, score=0.92,
        active_signals=["loudness", "chat"],
        text="Vamos ver. Caralho, olha esse susto!",
    )
    title = generate_title(clip, words)
    assert "susto!" in title.lower()
    assert len(title) <= 60

    no_speech_clip = SnappedClip(
        peak=15.0, start=4.0, end=32.0, score=0.8, active_signals=["loudness"],
    )
    fallback_title = generate_title(no_speech_clip, [])
    assert "loudness" in fallback_title


def test_evaluate_clips_recall():
    words = [Word(10.0, 10.5, " Teste."), Word(40.0, 40.5, " Fim.")]
    clips = [
        SnappedClip(
            peak=25.0, start=10.8, end=39.8, score=0.9,
            edge_mode="speech", start_edge_type="sentence", end_edge_type="sentence",
        )
    ]
    res = evaluate_clips(clips, words, min_dur=25.0, max_dur=55.0, ground_truth_peaks=[24.0, 100.0], k=5)
    assert res["mid_word_cut_rate"] == 0.0
    assert res["sentence_edge_rate"] == 1.0
    assert res["duration_ok_rate"] == 1.0
    assert res["peak_position_ok_rate"] == 1.0
    assert res["recall_at_5"] == 0.5


def test_zero_clips_flat_signal_degrades_cleanly(tmp_path: Path, monkeypatch):
    """A flat/music-only live recording with 0 peaks must exit 0 and write empty clips.json."""
    import json
    import wave
    import numpy as np
    from clipes import cli
    from clipes.models import IngestResult

    out_root = tmp_path / "out"
    vid_dir = out_root / "live_music"
    cache_dir = vid_dir / ".cache"
    cache_dir.mkdir(parents=True)
    wav_path = cache_dir / "audio.wav"
    sr = 16000
    samples = (np.ones(sr * 60, dtype=np.int16) * 500).tobytes()
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples)

    fake_ing = IngestResult(
        video_id="live_music",
        title="24/7 Music Live",
        url="https://www.youtube.com/watch?v=live_music",
        extractor="youtube",
        is_live=True,
        duration=60.0,
        audio_path=wav_path,
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=vid_dir,
        cache_dir=cache_dir,
    )
    monkeypatch.setattr(cli, "ingest", lambda **kwargs: fake_ing)
    rc = cli.main(["https://www.youtube.com/watch?v=live_music", "--out-dir", str(out_root), "--live-minutes", "1"])
    assert rc == 0
    manifest = json.loads((vid_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["is_live"] is True
    assert manifest["clips"] == []
    assert (vid_dir / "README.md").is_file()


def test_build_ass_part_badge_present_only_when_multipart(tmp_path: Path):
    words = [Word(1.0, 1.4, " Olá"), Word(1.5, 2.0, " mundo!")]

    single_ass = tmp_path / "single.ass"
    build_ass(words, clip_start=0.0, clip_end=30.0, ass_path=single_ass, part_index=1, part_total=1)
    single_txt = single_ass.read_text(encoding="utf-8")
    assert "Style: PartBadge" not in single_txt
    assert "Parte " not in single_txt
    assert "Live completa" in single_txt and "Live completa:" not in single_txt  # no credit to show

    part_ass = tmp_path / "part1of2.ass"
    count = build_ass(
        words, clip_start=0.0, clip_end=45.0, ass_path=part_ass,
        channel_credit="twitch.tv/gaules", part_index=1, part_total=2,
    )
    part_txt = part_ass.read_text(encoding="utf-8")
    assert count == 6  # 1 PartBadge + 2 Credit + 1 Card + 2 word dialogues
    assert "Style: PartBadge" in part_txt
    assert "490" in part_txt
    assert "Parte 1/2" in part_txt
    assert "Parte 2 →" in part_txt

    part2_ass = tmp_path / "part2of2.ass"
    build_ass(
        words, clip_start=0.0, clip_end=45.0, ass_path=part2_ass,
        channel_credit="twitch.tv/gaules", part_index=2, part_total=2,
    )
    assert "Live completa: twitch.tv/gaules" in part2_ass.read_text(encoding="utf-8")


def test_evaluate_clips_topic_and_multipart_bounds():
    words = [Word(0.0, 0.5, " Início."), Word(55.0, 55.5, " Meio."), Word(112.0, 112.5, " Fim.")]
    clips = [
        SnappedClip(
            peak=50.0, start=0.8, end=54.8, score=0.9,
            edge_mode="topic", start_edge_type="sentence", end_edge_type="sentence",
            part_index=1, part_total=2, group_id="topic_50",
        ),
        SnappedClip(
            peak=50.0, start=55.6, end=111.6, score=0.9,
            edge_mode="topic", start_edge_type="sentence", end_edge_type="sentence",
            part_index=2, part_total=2, group_id="topic_50",
        ),
    ]
    res = evaluate_clips(clips, words, min_dur=25.0, max_dur=65.0)
    assert res["duration_ok_rate"] == 1.0
    assert res["peak_position_ok_rate"] == 1.0
    assert res["sentence_edge_rate"] == 1.0
    assert res["mid_word_cut_rate"] == 0.0

    # A part > 58.0s must fail duration_ok_rate even if max_dur=65.0
    too_long_part = [
        SnappedClip(
            peak=50.0, start=0.8, end=60.8, score=0.9,
            edge_mode="topic", part_index=1, part_total=2, group_id="topic_50",
        )
    ]
    assert evaluate_clips(too_long_part, words, min_dur=25.0, max_dur=65.0)["duration_ok_rate"] == 0.0


def test_cli_skip_render_writes_part_metadata_in_manifest_and_readme(tmp_path: Path, monkeypatch):
    import json
    import wave
    import numpy as np
    from clipes import cli
    from clipes.models import CandidatePeak, IngestResult

    out_root = tmp_path / "out"
    vid_dir = out_root / "vod_parts"
    cache_dir = vid_dir / ".cache"
    cache_dir.mkdir(parents=True)
    wav_path = cache_dir / "audio.wav"
    sr = 16000
    duration = 160.0
    samples = (np.ones(int(sr * duration), dtype=np.int16) * 200).tobytes()
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples)

    fake_ing = IngestResult(
        video_id="vod_parts",
        title="Teste Partes",
        url="https://www.youtube.com/watch?v=vod_parts",
        extractor="youtube",
        is_live=False,
        duration=duration,
        audio_path=wav_path,
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=vid_dir,
        cache_dir=cache_dir,
    )
    monkeypatch.setattr(cli, "ingest", lambda **kwargs: fake_ing)
    monkeypatch.setattr(
        cli,
        "pick_candidate_peaks",
        lambda *a, **kw: [CandidatePeak(peak=80.0, raw_score=0.9, start_win=0.0, end_win=140.0, topic_start=0.0, topic_end=100.0)],
    )
    words = []
    for i in range(11):
        s = i * 10.0
        for j, tok in enumerate(["Parte", "da", "história", "completa", f"aqui{i}."]):
            words.append(Word(s + j * 1.5, s + j * 1.5 + 1.2, (" " + tok) if j else tok))
    monkeypatch.setattr(cli, "transcribe_candidates", lambda **kwargs: (words, "pt"))

    rc = cli.main(["https://www.youtube.com/watch?v=vod_parts", "--out-dir", str(out_root), "--skip-render"])
    assert rc == 0
    manifest = json.loads((vid_dir / "clips.json").read_text(encoding="utf-8"))
    assert "topics_s" in manifest["timings"]
    assert len(manifest["clips"]) == 2
    assert manifest["clips"][0]["part_index"] == 1
    assert manifest["clips"][0]["part_total"] == 2
    assert manifest["clips"][1]["part_index"] == 2
    assert manifest["clips"][0]["group_id"] == manifest["clips"][1]["group_id"] != ""
    assert manifest["clips"][0]["file"] == "clip_01_part1of2.mp4"
    readme = (vid_dir / "README.md").read_text(encoding="utf-8")
    assert "1/2" in readme and "2/2" in readme
    assert manifest["clips"][0]["group_id"] in readme


def test_format_channel_credit_and_ingest_channel(tmp_path: Path, monkeypatch):
    import json
    from clipes import ingest as ingest_mod

    assert ingest_mod.format_channel_credit("gaules", "https://www.twitch.tv/videos/123", "twitch:vod") == "twitch.tv/gaules"
    assert ingest_mod.format_channel_credit("", "https://www.twitch.tv/gaules", "twitch") == "twitch.tv/gaules"
    assert ingest_mod.format_channel_credit("podpah", "https://www.youtube.com/watch?v=abc", "youtube") == "@podpah"
    assert ingest_mod.format_channel_credit("", "https://www.youtube.com/@podpah/streams", "youtube") == "@podpah"
    assert ingest_mod.format_channel_credit("twitch.tv/gaules", "https://www.twitch.tv/videos/1", "") == "twitch.tv/gaules"

    # ingest_local populates .channel from meta.json
    local_dir = tmp_path / "local_job"
    local_dir.mkdir()
    (local_dir / "source.mkv").write_bytes(b"fake")
    (local_dir / "meta.json").write_text(json.dumps({
        "id": "job1",
        "title": "Live Test",
        "url": "https://www.twitch.tv/videos/999",
        "extractor": "twitch",
        "channel": "gaules",
    }), encoding="utf-8")
    monkeypatch.setattr(ingest_mod, "extract_wav", lambda src, wav: wav.write_bytes(b"0" * 2048) or wav)
    monkeypatch.setattr(ingest_mod, "probe_duration", lambda p: 120.0)
    res_local = ingest_mod.ingest_local(local_dir, tmp_path / "out_local", enable_chat=False)
    assert res_local.channel == "twitch.tv/gaules"

    # ingest populates .channel and writes it to meta.json
    monkeypatch.setattr(ingest_mod, "probe_metadata", lambda url: {
        "id": "yt1",
        "title": "Podcast",
        "webpage_url": "https://www.youtube.com/watch?v=yt1",
        "extractor": "youtube",
        "uploader": "podpah",
        "is_live": False,
        "duration": 60.0,
    })
    monkeypatch.setattr(ingest_mod, "download_vod_audio", lambda url, cdir: cdir / "audio.wav")
    res_yt = ingest_mod.ingest("https://www.youtube.com/watch?v=yt1", tmp_path / "out_yt", enable_chat=False)
    assert res_yt.channel == "@podpah"
    meta_written = json.loads((res_yt.cache_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta_written["channel"] == "@podpah"


def test_filter_chains_outro_and_blur_env(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from clipes import render as render_mod

    ass_path = tmp_path / "clip.ass"

    # Default (unset) -> black bars; CLIPES_BLUR_BG=1 -> split/boxblur/overlay + tpad before subtitles + no video fade
    monkeypatch.delenv("CLIPES_BLUR_BG", raising=False)
    assert render_mod.is_blur_bg_enabled() is False
    monkeypatch.setenv("CLIPES_BLUR_BG", "1")
    assert render_mod.is_blur_bg_enabled() is True
    fc_blur, v_map = render_mod.build_video_filter(ass_path, n_subs=3, outro_duration=1.2)
    assert v_map == "[v_out]"
    assert "split=2" in fc_blur
    assert "boxblur=10:2" in fc_blur
    assert "overlay=(W-w)/2:(H-h)/2" in fc_blur
    assert "tpad=stop_mode=clone:stop_duration=1.2" in fc_blur
    assert fc_blur.index("tpad=") < fc_blur.index("subtitles=")
    assert "fade=" not in fc_blur

    # CLIPES_BLUR_BG=0 -> black bars, NO split/boxblur/overlay, centered on black
    monkeypatch.setenv("CLIPES_BLUR_BG", "0")
    assert render_mod.is_blur_bg_enabled() is False
    fc_black, v_map_black = render_mod.build_video_filter(ass_path, n_subs=3, outro_duration=1.2)
    assert v_map_black == "[v_out]"
    assert "split" not in fc_black
    assert "boxblur" not in fc_black
    assert "overlay" not in fc_black
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" in fc_black
    assert "tpad=stop_mode=clone:stop_duration=1.2" in fc_black

    # Audio filter order: loudnorm -> afade -> apad
    af = render_mod.build_audio_filter(content_duration=10.0, outro_duration=1.2)
    assert af == "loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=out:st=9.500:d=0.5,apad=pad_dur=1.2"
    assert af.index("loudnorm") < af.index("afade") < af.index("apad")

    # render_clip passes -t D+1.2, -af, and -filter_complex to ffmpeg
    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        Path(cmd[-1]).write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)
    live_src = tmp_path / "live.mkv"
    live_src.write_bytes(b"src")
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir()
    cache_dir.mkdir()
    clip = SnappedClip(peak=15.0, start=10.0, end=20.0, score=0.9)
    out_mp4 = render_mod.render_clip(
        clip=clip,
        idx=1,
        words=[],
        url="",
        out_dir=out_dir,
        cache_dir=cache_dir,
        live_video_path=live_src,
        channel_credit="twitch.tv/gaules",
        reframe=False,
    )
    assert out_mp4.is_file()
    cmd = captured_cmds[0]
    t_idx = len(cmd) - 1 - cmd[::-1].index("-t")
    assert cmd[t_idx + 1] == "11.200"
    af_idx = cmd.index("-af")
    assert cmd[af_idx + 1] == "loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=out:st=9.500:d=0.5,apad=pad_dur=1.2"
    fc_idx = cmd.index("-filter_complex")
    assert "split" not in cmd[fc_idx + 1]
    assert "pad=1080:1920" in cmd[fc_idx + 1]


def test_reframe_yunet_and_fallback(tmp_path: Path, monkeypatch):
    import os
    import numpy as np
    from types import SimpleNamespace
    from clipes import reframe as reframe_mod
    from clipes import render as render_mod

    # Threshold < 30% returns None; >= 30% computes median 9:16 crop in source coords
    assert reframe_mod.compute_vertical_crop([(320.0, 180.0, 50.0, 60.0)], total_frames=4, src_w=1920, src_h=1080) is None
    crop = reframe_mod.compute_vertical_crop(
        [(480.0, 180.0, 50.0, 60.0), (480.0, 180.0, 50.0, 60.0)],
        total_frames=4,
        src_w=1920,
        src_h=1080,
    )
    assert crop is not None
    cx, cy, cw, ch = crop
    assert cy == 0 and ch == 1080 and cw == 608
    assert 0 <= cx <= 1920 - cw

    # Filter uses crop when provided
    fc_crop, _ = render_mod.build_video_filter(tmp_path / "c.ass", n_subs=1, crop=crop)
    assert f"crop={cw}:{ch}:{cx}:{cy},scale=1080:1920" in fc_crop
    assert "split" not in fc_crop

    # Missing ONNX model degrades safely to None
    assert reframe_mod.detect_speaker_crop(tmp_path / "v.mp4", 0.0, 5.0, yunet_onnx=str(tmp_path / "missing.onnx")) is None

    # Real YuNet ONNX model in container returns None cleanly on blank frames
    yunet_path = Path(os.environ.get("CLIPES_YUNET_ONNX", reframe_mod.DEFAULT_YUNET_ONNX))
    if yunet_path.is_file():
        blank_frames = [np.zeros((360, 640, 3), dtype=np.uint8) for _ in range(3)]
        monkeypatch.setattr(reframe_mod, "_sample_frames_bgr", lambda *a, **kw: blank_frames)
        monkeypatch.setattr(reframe_mod, "_probe_video_size", lambda p: (1920, 1080))
        assert reframe_mod.detect_speaker_crop(tmp_path / "v.mp4", 0.0, 3.0) is None

    # render_clip with reframe=True falls back to blurred_16x9 when detect_speaker_crop returns None
    monkeypatch.setenv("CLIPES_BLUR_BG", "1")
    monkeypatch.setattr(reframe_mod, "detect_speaker_crop", lambda *a, **kw: None)
    captured: list[list[str]] = []
    monkeypatch.setattr(
        render_mod.subprocess,
        "run",
        lambda cmd, **kw: (captured.append(cmd), Path(cmd[-1]).write_bytes(b"mp4"), SimpleNamespace(returncode=0, stderr="", stdout=""))[-1],
    )
    src = tmp_path / "src.mkv"
    src.write_bytes(b"src")
    out_dir = tmp_path / "out_r"
    cache_dir = tmp_path / "cache_r"
    out_dir.mkdir()
    cache_dir.mkdir()
    clip = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.8)
    render_mod.render_clip(clip, 1, [], "", out_dir, cache_dir, live_video_path=src, reframe=True)
    assert getattr(clip, "layout", "") == "blurred_16x9"
    assert "boxblur=10:2" in captured[0][captured[0].index("-filter_complex") + 1]


def test_cli_channel_and_reframe_propagation_and_manifest(tmp_path: Path, monkeypatch):
    import json
    import wave
    import numpy as np
    from clipes import cli
    from clipes.models import CandidatePeak, IngestResult

    out_root = tmp_path / "out"
    vid_dir = out_root / "twitch_vod"
    cache_dir = vid_dir / ".cache"
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

    fake_ing = IngestResult(
        video_id="twitch_vod",
        title="Live Twitch",
        url="https://www.twitch.tv/videos/12345",
        extractor="twitch:vod",
        is_live=False,
        duration=duration,
        audio_path=wav_path,
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=vid_dir,
        cache_dir=cache_dir,
    )
    fake_ing.channel = "twitch.tv/original"  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "ingest", lambda **kwargs: fake_ing)
    monkeypatch.setattr(
        cli,
        "pick_candidate_peaks",
        lambda *a, **kw: [CandidatePeak(peak=50.0, raw_score=0.9, start_win=0.0, end_win=100.0)],
    )
    words = [
        Word(40.0 + i * 2.0, 40.0 + i * 2.0 + 1.5, f" palavra{i}." if i % 4 == 3 else f" palavra{i}")
        for i in range(18)
    ]
    monkeypatch.setattr(cli, "transcribe_candidates", lambda **kwargs: (words, "pt"))

    render_calls: list[dict] = []

    def fake_render_clip(clip, idx, words, url, out_dir, cache_dir, live_video_path=None, channel_credit="", reframe=False):
        render_calls.append({"channel_credit": channel_credit, "reframe": reframe})
        clip.layout = "reframed_face" if reframe else "blurred_16x9"
        p = out_dir / f"clip_{idx:02d}.mp4"
        p.write_bytes(b"mp4")
        return p

    monkeypatch.setattr(cli, "render_clip", fake_render_clip)

    rc = cli.main([
        "https://www.twitch.tv/videos/12345",
        "--out-dir", str(out_root),
        "--channel", "gaules",
        "--reframe",
    ])
    assert rc == 0
    assert len(render_calls) >= 1
    assert render_calls[0]["channel_credit"] == "twitch.tv/gaules"
    assert render_calls[0]["reframe"] is True

    manifest = json.loads((vid_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["channel_credit"] == "twitch.tv/gaules"
    first = manifest["clips"][0]
    assert first["channel_credit"] == "twitch.tv/gaules"
    assert first["rendered_duration"] == round(first["duration"] + 1.2, 3)
    assert first["layout"] == "reframed_face"


def _ffprobe_streams(path: Path) -> list[dict]:
    import json
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)["streams"]


def _gray_frame(path: Path, t: float):
    import subprocess

    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080).astype(float)


def test_render_clip_real_ffmpeg_outro_card_and_credit(tmp_path: Path, monkeypatch):
    """Real render of a synthetic source: duration D+1.2, 1080x1920 h264+aac, frozen outro with burned card."""
    import subprocess

    from clipes import render as render_mod
    from clipes.ingest import probe_duration

    monkeypatch.delenv("CLIPES_BLUR_BG", raising=False)
    src = tmp_path / "live.mkv"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=14",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=14",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src)],
        check=True,
    )
    out_dir, cache_dir = tmp_path / "out", tmp_path / "cache"
    out_dir.mkdir()
    cache_dir.mkdir()
    words = [Word(3.0, 3.4, " Olha"), Word(3.5, 3.9, " isso"), Word(4.0, 4.4, " mano!")]
    content = 10.0

    rendered = {}
    for part_index in (1, 2):
        clip = SnappedClip(peak=7.0, start=2.0, end=2.0 + content, score=0.9, part_index=part_index, part_total=2)
        rendered[part_index] = render_mod.render_clip(
            clip, part_index, words, "", out_dir, cache_dir,
            live_video_path=src, channel_credit="twitch.tv/gaules",
        )

    for mp4 in rendered.values():
        assert abs(probe_duration(mp4) - (content + 1.2)) <= 0.05
        streams = _ffprobe_streams(mp4)
        video = next(s for s in streams if s["codec_type"] == "video")
        assert (video["codec_name"], video["width"], video["height"]) == ("h264", 1080, 1920)
        assert any(s["codec_type"] == "audio" and s["codec_name"] == "aac" for s in streams)

    part1_ass = (cache_dir / "clip_01_part1of2.ass").read_text(encoding="utf-8")
    part2_ass = (cache_dir / "clip_02_part2of2.ass").read_text(encoding="utf-8")
    cap_style = next(line for line in part1_ass.splitlines() if line.startswith("Style: Cap,"))
    assert cap_style.split(",")[-4:-1] == ["120", "200", "560"]  # MarginL, MarginR, MarginV
    for ass in (part1_ass, part2_ass):
        assert r"\pos(120,600)\fad(300,400)}twitch.tv/gaules" in ass
        assert r"\pos(120,600)\alpha&H99&}twitch.tv/gaules" in ass
    assert "0:00:10.00,0:00:11.20,Card" in part1_ass and "Parte 2 →" in part1_ass

    # Burned frames: the outro freezes the last frame (no fade to black) and shows the centered card.
    mp4 = rendered[1]
    before = _gray_frame(mp4, content - 0.1)
    outro_early, outro_late = _gray_frame(mp4, content + 0.4), _gray_frame(mp4, content + 1.1)
    card_box = (slice(900, 1020), slice(240, 840))
    assert abs(outro_late[card_box] - before[card_box]).mean() > 20  # card drawn over the frozen frame
    background = (slice(1500, 1900), slice(0, 1080))  # blurred bottom band, no text during the outro
    assert abs(outro_late[background] - outro_early[background]).mean() < 2  # frozen, not moving
    assert outro_late.mean() > 20  # not faded to black

    assert "Live completa: twitch.tv/gaules" in part2_ass and "Parte 3" not in part2_ass  # CRED-04


def test_slugify_and_clip_stem():
    from clipes.render import clip_stem, slugify

    assert slugify("Ele NÃO acreditou no que viu?!") == "ele-nao-acreditou-no-que-viu"
    assert slugify("  Olá---Mundo!! 2026 @#$ ") == "ola-mundo-2026"
    assert slugify("?!🔥💥...") == ""
    assert slugify("") == ""

    # Word-boundary truncation <= 40 chars
    long_title = "Ele nao acreditou no que aconteceu naquela live inteira hoje"
    slug = slugify(long_title)
    assert len(slug) <= 40
    assert slug == "ele-nao-acreditou-no-que-aconteceu"
    assert not slug.endswith("-")

    # Exact boundary right before hyphen
    exact = "aaaa bbbb cccc dddd eeee ffff gggg hhhh iiii"
    exact_slug = slugify(exact)
    assert len(exact_slug) <= 40
    assert exact_slug == "aaaa-bbbb-cccc-dddd-eeee-ffff-gggg-hhhh"

    # Single word > 40 chars still respects <= 40
    single_word = "a" * 55
    assert len(slugify(single_word)) == 40

    # clip_stem variants
    c_title = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Ele NÃO acreditou no que viu?!")
    assert clip_stem(3, c_title) == "clip_03_ele-nao-acreditou-no-que-viu"

    c_empty = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="???")
    assert clip_stem(1, c_empty) == "clip_01"

    c_part = SnappedClip(
        peak=5.0, start=0.0, end=10.0, score=0.9,
        title="Olha isso!", part_index=1, part_total=2,
    )
    assert clip_stem(2, c_part) == "clip_02_part1of2_olha-isso"

    c_part_empty = SnappedClip(
        peak=5.0, start=0.0, end=10.0, score=0.9,
        title="", part_index=2, part_total=3,
    )
    assert clip_stem(4, c_part_empty) == "clip_04_part2of3"

    # Same title on two clips keeps files distinct via NN
    c_dup1 = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Mesmo título")
    c_dup2 = SnappedClip(peak=15.0, start=10.0, end=20.0, score=0.9, title="Mesmo título")
    assert clip_stem(1, c_dup1) == "clip_01_mesmo-titulo"
    assert clip_stem(2, c_dup2) == "clip_02_mesmo-titulo"


def test_render_clip_uses_slug_stem_and_overwrites_same_index(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from clipes import render as render_mod

    monkeypatch.delenv("CLIPES_BLUR_BG", raising=False)
    monkeypatch.setattr(
        render_mod.subprocess,
        "run",
        lambda cmd, **kw: (
            Path(cmd[-1]).write_bytes(b"new-mp4"),
            SimpleNamespace(returncode=0, stderr="", stdout=""),
        )[-1],
    )

    src = tmp_path / "live.mkv"
    src.write_bytes(b"src")
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir()
    cache_dir.mkdir()

    clip = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="Ele NÃO acreditou no que viu?!")
    out_mp4 = render_mod.render_clip(clip, 3, [], "", out_dir, cache_dir, live_video_path=src)
    assert out_mp4 == out_dir / "clip_03_ele-nao-acreditou-no-que-viu.mp4"
    assert out_mp4.is_file()
    assert (cache_dir / "clip_03_ele-nao-acreditou-no-que-viu.ass").is_file()

    # Re-render with same index overwrites cleanly
    out_mp4.write_bytes(b"old-mp4")
    out_mp4_2 = render_mod.render_clip(clip, 3, [], "", out_dir, cache_dir, live_video_path=src)
    assert out_mp4_2 == out_mp4
    assert out_mp4_2.read_bytes() == b"new-mp4"

    # Multipart with slug
    clip_part = SnappedClip(
        peak=5.0, start=0.0, end=10.0, score=0.9,
        title="Momento épico", part_index=1, part_total=2,
    )
    out_part = render_mod.render_clip(clip_part, 2, [], "", out_dir, cache_dir, live_video_path=src)
    assert out_part == out_dir / "clip_02_part1of2_momento-epico.mp4"
    assert out_part.is_file()
    assert (cache_dir / "clip_02_part1of2_momento-epico.ass").is_file()

    # Empty slug falls back to clip_NN.mp4
    clip_empty = SnappedClip(peak=5.0, start=0.0, end=10.0, score=0.9, title="")
    out_empty = render_mod.render_clip(clip_empty, 4, [], "", out_dir, cache_dir, live_video_path=src)
    assert out_empty == out_dir / "clip_04.mp4"
    assert out_empty.is_file()
    assert (cache_dir / "clip_04.ass").is_file()


def test_wrap_top_hook():
    from clipes.render import wrap_top_hook

    assert wrap_top_hook("") == ""
    assert wrap_top_hook("   ") == ""

    short = wrap_top_hook("Ele perdeu tudo?")
    assert short == r"{\c&H00FFFFFF&}ELE PERDEU TUDO?"
    assert r"\N" not in short

    two_lines = wrap_top_hook("Ele não acreditou no que viu!")
    assert two_lines == r"{\c&H00FFFFFF&}ELE NÃO ACREDITOU\N{\c&H0000FFFF&}NO QUE VIU!"
    l1, l2 = two_lines.split(r"\N")
    assert l1.startswith(r"{\c&H00FFFFFF&}")
    assert l2.startswith(r"{\c&H0000FFFF&}")
    assert len(l1.removeprefix(r"{\c&H00FFFFFF&}")) <= 18
    assert len(l2.removeprefix(r"{\c&H0000FFFF&}")) <= 18

    # ASS special chars {, }, \ are escaped before going into ASS
    escaped = wrap_top_hook(r"O {cara} \sumiu!")
    assert escaped == r"{\c&H00FFFFFF&}O (CARA) SUMIU!"

    # Single word longer than max_chars is clamped <= 18
    long_word = wrap_top_hook("INCONSTITUCIONALISSIMAMENTE")
    assert len(long_word.removeprefix(r"{\c&H00FFFFFF&}")) <= 18


def test_build_ass_top_hook_and_opt_out(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIPES_HOOK_TOP", raising=False)
    words = [Word(1.0, 1.4, " Olha"), Word(1.5, 2.0, " isso!")]

    # Happy path: 10s clip -> TopHook from 0.00 to 5.00
    ass_10 = tmp_path / "hook_10s.ass"
    count = build_ass(
        words, clip_start=0.0, clip_end=10.0, ass_path=ass_10,
        channel_credit="twitch.tv/gaules", part_index=1, part_total=2,
        hook_top="Ele perdeu tudo?",
    )
    assert count == 7  # 1 PartBadge + 2 Credit + 1 Card + 1 TopHook + 2 words
    txt = ass_10.read_text(encoding="utf-8")
    assert "Style: TopHook,DejaVu Sans,64," in txt
    top_style = next(line for line in txt.splitlines() if line.startswith("Style: TopHook,"))
    style_fields = top_style.split(",")
    assert style_fields[16:19] == ["6", "3", "5"]  # Outline 6, Shadow 3, Alignment 5
    hook_line = next(line for line in txt.splitlines() if ",TopHook," in line)
    assert hook_line.startswith("Dialogue: 3,0:00:00.00,0:00:05.00,TopHook,,0,0,0,,")
    assert r"\an5\pos(540,365)\fad(120,350)" in hook_line
    assert r"\fscx75\fscy75\t(0,220,\fscx106\fscy106)\t(220,320,\fscx100\fscy100)" in hook_line
    assert r"{\c&H00FFFFFF&}ELE PERDEU TUDO?" in hook_line
    # Does not overlap PartBadge (Y=490) or Credit (Y=600)
    assert r"\pos(540,490)" in txt
    assert r"\pos(120,600)" in txt

    # 2-line hook splits with \N and alternating white/yellow colors
    ass_2l = tmp_path / "hook_2l.ass"
    build_ass(
        [], clip_start=0.0, clip_end=8.0, ass_path=ass_2l,
        hook_top="Ele não acreditou no que viu!",
    )
    txt_2l = ass_2l.read_text(encoding="utf-8")
    hook_2l = next(line for line in txt_2l.splitlines() if ",TopHook," in line)
    assert r"{\c&H00FFFFFF&}ELE NÃO ACREDITOU\N{\c&H0000FFFF&}NO QUE VIU!" in hook_2l

    # Short clip (3.0s) clamps TopHook end to 3.00s
    ass_3s = tmp_path / "hook_3s.ass"
    build_ass([], clip_start=0.0, clip_end=3.0, ass_path=ass_3s, hook_top="Ele perdeu tudo?")
    txt_3s = ass_3s.read_text(encoding="utf-8")
    assert "Dialogue: 3,0:00:00.00,0:00:03.00,TopHook," in txt_3s

    # Empty hook emits no TopHook event
    ass_empty = tmp_path / "hook_empty.ass"
    build_ass([], clip_start=0.0, clip_end=10.0, ass_path=ass_empty, hook_top="   ")
    assert "TopHook" not in ass_empty.read_text(encoding="utf-8")

    # Opt-out CLIPES_HOOK_TOP=0 emits no TopHook event
    monkeypatch.setenv("CLIPES_HOOK_TOP", "0")
    ass_off = tmp_path / "hook_off.ass"
    count_off = build_ass([], clip_start=0.0, clip_end=10.0, ass_path=ass_off, hook_top="Ele perdeu tudo?")
    assert count_off == 1  # Card only
    assert "TopHook" not in ass_off.read_text(encoding="utf-8")


def test_render_clip_forwards_hook_top_and_respects_opt_out(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from clipes import render as render_mod

    monkeypatch.delenv("CLIPES_BLUR_BG", raising=False)
    monkeypatch.delenv("CLIPES_HOOK_TOP", raising=False)
    monkeypatch.setattr(
        render_mod.subprocess,
        "run",
        lambda cmd, **kw: (
            Path(cmd[-1]).write_bytes(b"mp4"),
            SimpleNamespace(returncode=0, stderr="", stdout=""),
        )[-1],
    )

    src = tmp_path / "live.mkv"
    src.write_bytes(b"src")
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir()
    cache_dir.mkdir()

    clip = SnappedClip(
        peak=5.0, start=0.0, end=10.0, score=0.9,
        title="Ele perdeu tudo", hook_top="Ele perdeu tudo?",
    )
    render_mod.render_clip(clip, 1, [], "", out_dir, cache_dir, live_video_path=src)
    ass_on = (cache_dir / "clip_01_ele-perdeu-tudo.ass").read_text(encoding="utf-8")
    assert "Dialogue: 3,0:00:00.00,0:00:05.00,TopHook," in ass_on
    assert "ELE PERDEU TUDO?" in ass_on

    monkeypatch.setenv("CLIPES_HOOK_TOP", "0")
    render_mod.render_clip(clip, 2, [], "", out_dir, cache_dir, live_video_path=src)
    ass_off = (cache_dir / "clip_02_ele-perdeu-tudo.ass").read_text(encoding="utf-8")
    assert "TopHook" not in ass_off
