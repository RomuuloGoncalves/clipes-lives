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
    count = build_ass(words, clip_start=9.5, clip_end=15.0, ass_path=ass_path)
    assert count == 4
    content = ass_path.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Dialogue:" in content
    assert "mano!" in content

    empty_ass = tmp_path / "empty.ass"
    assert build_ass([], clip_start=0.0, clip_end=10.0, ass_path=empty_ass) == 0


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
