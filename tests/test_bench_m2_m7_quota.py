"""M2 and M7 measurements. Skipped unless CLIPES_RUN_BENCH=1; not part of the default suite.

Reads the real VOD cache (CLIPES_BENCH_CACHE, default /cache) and never writes there.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d

from clipes.asr import transcribe_candidates, transcribe_fast_blocks
from clipes.candidates import pick_quota_candidates
from clipes.eval import evaluate_clips, evaluate_quota_calibration
from clipes.models import SignalBundle
from clipes.signals import triage_audio_blocks
from clipes.snap import select_clips
from clipes.topics import score_topic_segments

pytestmark = pytest.mark.skipif(
    os.environ.get("CLIPES_RUN_BENCH") != "1",
    reason="medição M2/M7 só com CLIPES_RUN_BENCH=1",
)

CACHE = Path(os.environ.get("CLIPES_BENCH_CACHE", "/cache"))
LABELS = Path(__file__).parent / "fixtures" / "calib_labels.json"


def test_m2_m7_two_pass_on_real_vod(tmp_path: Path):
    work = Path(os.environ.get("CLIPES_BENCH_OUT", str(tmp_path)))
    work.mkdir(parents=True, exist_ok=True)
    assert (CACHE / "audio.wav").is_file(), CACHE
    assert (CACHE / "signals.npz").is_file()
    data = np.load(CACHE / "signals.npz")
    loud = np.asarray(data["sig_loudness"], dtype=np.float64)
    n = len(loud)
    smoothed = np.clip(gaussian_filter1d(loud, 2.0), 0.0, 1.0) if n > 3 else loud.copy()
    bundle = SignalBundle(
        duration=float(n),
        n=n,
        scores={"loudness": loud},
        combined=loud.copy(),
        smoothed=smoothed,
        agreeing=(loud >= 0.5).astype(np.int32),
        weights={"loudness": 1.0},
        rms_db=np.asarray(data["rms_db"], dtype=np.float32),
    )
    labels = json.loads(LABELS.read_text(encoding="utf-8"))[0]["moments"]
    blocks = triage_audio_blocks(bundle, budget_ratio=0.30)
    audio_s = sum(e - s for s, e, _ in blocks)
    print(f"[m7] pass1 blocks={len(blocks)} audio_s={audio_s:.0f}", flush=True)

    t0 = time.perf_counter()
    fast = transcribe_fast_blocks(CACHE / "audio.wav", blocks, work, model_size="small", cpu_threads=6)
    pass1_s = time.perf_counter() - t0
    xrt = pass1_s / audio_s if audio_s else 0.0
    print(f"[m2] pass1_s={pass1_s:.1f} xrt={xrt:.3f} segments={len(fast)}", flush=True)

    from clipes.cli import _topics_from_pass1

    scored = score_topic_segments(_topics_from_pass1(fast), bundle)
    chosen, floor_mode = pick_quota_candidates(bundle, scored, max_clips=8, min_per_hour=3.0)
    print(f"[m7] quota={len(chosen)} floor={floor_mode}", flush=True)

    t1 = time.perf_counter()
    words, lang = transcribe_candidates(
        CACHE / "audio.wav", chosen, work, model_size="small", cpu_threads=6, beam_size=1,
    )
    pass2_s = time.perf_counter() - t1
    clips = select_clips(chosen, words, bundle.rms_db, bundle.duration, max_clips=8, bundle=bundle)
    quality = evaluate_clips(clips, words)
    ranking = evaluate_quota_calibration(clips, labels, k=8)
    per_hour = (pass1_s + pass2_s) / 60.0 / (n / 3600.0)
    print(
        f"[m7] pass2_s={pass2_s:.1f} lang={lang} clips={len(clips)} "
        f"min_per_hour={per_hour:.2f} quality={quality} ranking={ranking}",
        flush=True,
    )

    assert xrt <= 0.38
    assert quality["sentence_edge_rate"] >= 0.90
    assert quality["mid_word_cut_rate"] == 0.0
    assert per_hour <= 12.0
    assert ranking["precision_at_k"] >= 0.625
