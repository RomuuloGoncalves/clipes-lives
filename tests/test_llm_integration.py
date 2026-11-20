"""Real integration smoke test against local llama-server + Qwen3-1.7B GGUF inside the container."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from clipes.llm import FORBIDDEN_HASHTAGS, enrich_clips_with_llm
from clipes.models import IngestResult, SnappedClip, Word

GGUF_PATH = Path(os.environ.get("CLIPES_LLM_GGUF", "/opt/models/llm/Qwen3-1.7B-Q4_K_M.gguf"))


@pytest.mark.skipif(not GGUF_PATH.is_file(), reason=f"GGUF not found at {GGUF_PATH}")
def test_real_llama_server_enrich_clips_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLIPES_LLM_TIMEOUT", "120")
    out_dir = tmp_path / "out"
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(parents=True)
    ing = IngestResult(
        video_id="smoke1",
        title="Gaules jogando CS2 ao vivo",
        url="https://www.twitch.tv/gaules",
        extractor="twitch:vod",
        is_live=False,
        duration=300.0,
        audio_path=cache_dir / "audio.wav",
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=out_dir,
        cache_dir=cache_dir,
    )
    words = [
        Word(10.0, 10.4, " Caralho,"),
        Word(10.5, 10.9, " olha"),
        Word(11.0, 11.4, " esse"),
        Word(11.5, 12.1, " clutch"),
        Word(12.2, 12.8, " impossível!"),
    ]
    clip = SnappedClip(
        peak=11.5,
        start=5.0,
        end=35.0,
        score=0.91,
        active_signals=["loudness", "chat"],
        text="Caralho, olha esse clutch impossível que o Gaules acabou de ganhar no CS2!",
    )

    enrich_clips_with_llm([clip], words, ing, enabled=True)

    assert clip.title
    assert len(clip.title) <= 60
    assert clip.title_source in ("llm", "heuristic_fallback")
    assert clip.llm_score is not None
    assert 0.0 <= clip.llm_score <= 10.0
    assert len(clip.caption.splitlines()) == 3
    assert "Crédito: gaules" in clip.caption.splitlines()[1]
    assert "http" not in clip.caption  # T11: caption sem URL
    assert 3 <= len(clip.hashtags) <= 5
    for tag in clip.hashtags:
        assert tag.lower() not in FORBIDDEN_HASHTAGS
        assert re.fullmatch(r"#[a-zA-Z0-9_À-ÿ]+", tag)
