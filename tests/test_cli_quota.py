"""CLI flags --mode, --min-per-hour and --asr-budget-ratio. Heavy stages are mocked."""

import json
import wave
from pathlib import Path

import numpy as np

from clipes import cli
from clipes.models import CandidatePeak, IngestResult, SignalBundle, SnappedClip, Word


def _ing(tmp_path: Path, duration: float = 7200.0) -> IngestResult:
    cache = tmp_path / "out" / "vid" / ".cache"
    cache.mkdir(parents=True)
    wav = cache / "audio.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    out = cache.parent
    return IngestResult(
        video_id="vid", title="Live longa", url="https://www.twitch.tv/gaules",
        extractor="twitch:vod", is_live=False, duration=duration, audio_path=wav,
        video_path=None, heatmap=None, chat_messages=None, out_dir=out, cache_dir=cache,
        channel="twitch.tv/gaules",
    )


def _bundle(duration: float) -> SignalBundle:
    n = max(1, int(duration))
    zeros = np.zeros(n)
    return SignalBundle(
        duration=duration, n=n, scores={"loudness": zeros}, combined=zeros.copy(),
        smoothed=zeros.copy(), agreeing=np.zeros(n, dtype=np.int32),
        weights={"loudness": 1.0}, rms_db=np.full(n * 2, -30.0, dtype=np.float32),
    )


def _peaks(n: int, score: float = 0.9) -> list[CandidatePeak]:
    return [
        CandidatePeak(peak=200.0 + i * 200.0, raw_score=score, start_win=100.0, end_win=300.0)
        for i in range(n)
    ]


def _wire(monkeypatch, ing: IngestResult, peaks: list[CandidatePeak], quota):
    seen: dict[str, list] = {"triage": [], "pass1": [], "quota": [], "pass2": [], "pass2_candidates": []}
    monkeypatch.setattr(cli, "ingest", lambda **kwargs: ing)
    monkeypatch.setattr(cli, "compute_rms_db", lambda *a, **k: ing and np.zeros(4, np.float32))
    monkeypatch.setattr(cli, "compute_signals", lambda **k: _bundle(ing.duration))
    monkeypatch.setattr(cli, "pick_candidate_peaks", lambda *a, **k: list(peaks))

    def triage(bundle, budget_ratio=0.30, **k):
        seen["triage"].append(budget_ratio)
        return [(10.0, 40.0, 0.8)]

    def pass1(*a, **k):
        seen["pass1"].append(k.get("model_size"))
        return [Word(10.0, 50.0, "fala " * 40)]

    def pick_quota(*a, **k):
        seen["quota"].append(k.get("min_per_hour"))
        return quota

    def pass2(*a, **k):
        seen["pass2"].append(k.get("beam_size"))
        seen["pass2_candidates"].append(list(k.get("candidates") or []))
        return [Word(12.0, 12.4, " Olha"), Word(12.5, 13.0, " isso!")], "pt"

    monkeypatch.setattr(cli, "triage_audio_blocks", triage)
    monkeypatch.setattr(cli, "transcribe_fast_blocks", pass1)
    monkeypatch.setattr(cli, "pick_quota_candidates", pick_quota)
    monkeypatch.setattr(cli, "transcribe_candidates", pass2)
    clip = SnappedClip(peak=30.0, start=10.0, end=50.0, score=0.8, text="Olha isso!")
    monkeypatch.setattr(cli, "select_clips", lambda **k: [clip])
    return seen


def _run(tmp_path: Path, extra: list[str]) -> int:
    return cli.main(["https://www.twitch.tv/gaules", "--out-dir", str(tmp_path / "out"), "--no-llm", "--skip-render", *extra])


def test_auto_triggers_quota_when_strong_peaks_miss_the_target(tmp_path, monkeypatch):
    ing = _ing(tmp_path)
    seen = _wire(monkeypatch, ing, _peaks(1), ([_peaks(1)[0]], True))
    assert _run(tmp_path, ["--min-per-hour", "3"]) == 0
    assert seen["triage"] == [0.30]
    assert seen["pass1"] == [None]
    assert seen["quota"] == [3.0]
    assert seen["pass2"] == [1]
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["detection_mode"] == "auto"
    assert manifest["floor_mode"] is True
    assert manifest["quota_target"] == 6
    assert "asr_pass1_s" in manifest["timings"] and "asr_pass2_s" in manifest["timings"]
    assert "Cota 6" in manifest["note"]


def test_auto_stays_on_peaks_when_strong_peaks_fill_the_quota(tmp_path, monkeypatch):
    ing = _ing(tmp_path)
    strong_peaks = _peaks(6, score=0.9)
    weak_peaks = _peaks(2, score=0.1)
    seen = _wire(monkeypatch, ing, strong_peaks + weak_peaks, ([], False))
    assert _run(tmp_path, []) == 0
    assert seen["triage"] == [] and seen["pass1"] == [] and seen["quota"] == []
    assert seen["pass2"] == [5]
    assert len(seen["pass2_candidates"][0]) == 6
    assert all(c.raw_score >= 0.25 for c in seen["pass2_candidates"][0])
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["detection_mode"] == "auto"
    assert manifest["floor_mode"] is False
    assert manifest["quota_target"] == 6
    assert "note" not in manifest


def test_auto_with_one_strong_and_five_weak_passes_only_strong(tmp_path, monkeypatch):
    ing = _ing(tmp_path, duration=600.0)
    strong = _peaks(1, score=0.8)
    weak = _peaks(5, score=0.15)
    seen = _wire(monkeypatch, ing, strong + weak, ([], False))
    assert _run(tmp_path, []) == 0
    assert seen["pass1"] == []
    assert seen["pass2"] == [5]
    assert len(seen["pass2_candidates"][0]) == 1
    assert seen["pass2_candidates"][0][0].raw_score == 0.8


def test_mode_peaks_keeps_strong_and_weak_candidates(tmp_path, monkeypatch):
    ing = _ing(tmp_path, duration=600.0)
    strong = _peaks(1, score=0.8)
    weak = _peaks(5, score=0.15)
    seen = _wire(monkeypatch, ing, strong + weak, ([], False))
    assert _run(tmp_path, ["--mode", "peaks"]) == 0
    assert seen["pass1"] == []
    assert seen["pass2"] == [5]
    assert len(seen["pass2_candidates"][0]) == 6


def test_min_per_hour_zero_with_no_strong_peaks_emits_zero_clips(tmp_path, monkeypatch):
    ing = _ing(tmp_path, duration=600.0)
    weak = _peaks(5, score=0.15)
    seen = _wire(monkeypatch, ing, weak, ([], False))
    assert _run(tmp_path, ["--min-per-hour", "0"]) == 0
    assert seen["triage"] == [] and seen["pass1"] == [] and seen["quota"] == []
    assert seen["pass2"] == []
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["quota_target"] == 0
    assert manifest["clips"] == []


def test_vod_defaults_keep_quota_target_at_least_one(tmp_path, monkeypatch):
    ing = _ing(tmp_path, duration=300.0)
    seen = _wire(monkeypatch, ing, [], ([_peaks(1)[0]], True))
    assert _run(tmp_path, []) == 0
    assert seen["pass1"] == [None]
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["quota_target"] >= 1


def test_mode_peaks_skips_pass1_and_mode_quota_always_runs_it(tmp_path, monkeypatch):
    ing = _ing(tmp_path, duration=3600.0)
    seen = _wire(monkeypatch, ing, _peaks(8), ([_peaks(1)[0]], False))
    assert _run(tmp_path, ["--mode", "peaks"]) == 0
    assert seen["pass1"] == [] and seen["pass2"] == [5]

    seen2 = _wire(monkeypatch, ing, _peaks(8), ([_peaks(1)[0]], False))
    assert _run(tmp_path, ["--mode", "quota", "--asr-budget-ratio", "0.5", "--min-per-hour", "1"]) == 0
    assert seen2["triage"] == [0.5]
    assert seen2["pass1"] == [None]
    assert seen2["quota"] == [1.0]
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["detection_mode"] == "quota"
    assert manifest["quota_target"] == 1


def test_invalid_quota_flags_exit_2():
    assert cli.main(["http://x", "--min-per-hour", "-1", "--skip-render"]) == 2
    assert cli.main(["http://x", "--asr-budget-ratio", "0.05", "--skip-render"]) == 2
    assert cli.main(["http://x", "--asr-budget-ratio", "1.5", "--skip-render"]) == 2


def _mixed_peaks() -> list[CandidatePeak]:  # centros 200, 400, 600, 800, 1000
    return _peaks(5)


def test_own_range_filters_candidates_before_asr2(tmp_path, monkeypatch):
    ing = _ing(tmp_path)
    seen = _wire(monkeypatch, ing, _mixed_peaks(), ([], False))
    assert _run(tmp_path, ["--mode", "peaks", "--own-range", "390,610"]) == 0  # [375, 625]
    assert [c.peak for c in seen["pass2_candidates"][0]] == [400.0, 600.0]
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert manifest["timings"]["candidatos_fora_dono"] == 3


def test_without_own_range_keeps_all_candidates(tmp_path, monkeypatch):
    ing = _ing(tmp_path)
    seen = _wire(monkeypatch, ing, _mixed_peaks(), ([], False))
    assert _run(tmp_path, ["--mode", "peaks"]) == 0
    assert len(seen["pass2_candidates"][0]) == 5
    manifest = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert "candidatos_fora_dono" not in manifest["timings"]


def test_own_range_inclusive_margin_edges(tmp_path, monkeypatch):
    ing = _ing(tmp_path)
    seen = _wire(monkeypatch, ing, _mixed_peaks(), ([], False))
    assert _run(tmp_path, ["--mode", "peaks", "--own-range", "415,585"]) == 0  # [400, 600]
    assert [c.peak for c in seen["pass2_candidates"][0]] == [400.0, 600.0]


def test_invalid_own_range_is_argparse_error():
    import pytest
    for bad in ("5,5", "9,1", "a,b", "10"):
        with pytest.raises(SystemExit) as e:
            cli.main(["http://x", "--own-range", bad, "--skip-render"])
        assert e.value.code == 2
