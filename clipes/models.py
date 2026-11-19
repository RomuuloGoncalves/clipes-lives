"""Shared dataclasses for the clipes pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SENTENCE_END = (".", "!", "?")


@dataclass
class Word:
    start: float
    end: float
    text: str
    no_speech_prob: float = 0.0

    @property
    def clean(self) -> str:
        return self.text.strip()

    @property
    def ends_sentence(self) -> bool:
        c = self.clean.rstrip("\"'”’)")
        if not c or c.endswith("...") or c.endswith("…"):
            return False
        return c.endswith(SENTENCE_END)


@dataclass
class IngestResult:
    video_id: str
    title: str
    url: str
    extractor: str
    is_live: bool
    duration: float
    audio_path: Path
    video_path: Path | None
    heatmap: list[dict[str, Any]] | None
    chat_messages: list[list[Any]] | None
    out_dir: Path
    cache_dir: Path
    channel: str = ""


@dataclass
class SignalBundle:
    duration: float
    n: int
    scores: dict[str, np.ndarray]
    combined: np.ndarray
    smoothed: np.ndarray
    agreeing: np.ndarray
    weights: dict[str, float]
    rms_db: np.ndarray


@dataclass
class CandidatePeak:
    peak: float
    raw_score: float
    start_win: float
    end_win: float
    active_signals: list[str] = field(default_factory=list)
    signal_peaks: dict[str, float] = field(default_factory=dict)
    topic_start: float | None = None
    topic_end: float | None = None


@dataclass
class TopicSegment:
    start: float
    end: float
    peak: float
    sentences: list[tuple[float, float, str]] = field(default_factory=list)
    boundary_score: float = 0.0

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


@dataclass
class SnappedClip:
    peak: float
    start: float
    end: float
    score: float
    active_signals: list[str] = field(default_factory=list)
    signal_scores: dict[str, float] = field(default_factory=dict)
    text: str = ""
    title: str = ""
    edge_mode: str = "speech"
    start_edge_type: str = "sentence"
    end_edge_type: str = "sentence"
    output_file: str = ""
    part_index: int = 1
    part_total: int = 1
    group_id: str = ""
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    llm_score: float | None = None
    llm_reason: str = ""
    title_source: str = ""
    hook_top: str = ""
    description_long: str = ""
    description_source: str = ""
    cabe_discord: bool = True

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    @property
    def peak_ratio(self) -> float:
        d = self.end - self.start
        return (self.peak - self.start) / d if d > 0 else 0.0
