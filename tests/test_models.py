"""Unit tests for topic/part dataclass extensions in clipes.models."""

from clipes.models import CandidatePeak, SnappedClip, TopicSegment


def test_topic_segment_duration_and_defaults():
    seg = TopicSegment(
        start=12.345,
        end=87.654,
        peak=70.0,
        sentences=[(12.345, 20.0, "Primeira frase."), (21.0, 87.654, "Segunda frase.")],
        boundary_score=0.42,
    )
    assert seg.duration == 75.309
    assert seg.boundary_score == 0.42
    assert len(seg.sentences) == 2

    minimal = TopicSegment(start=0.0, end=10.0, peak=5.0)
    assert minimal.sentences == []
    assert minimal.boundary_score == 0.0
    assert minimal.duration == 10.0


def test_snapped_clip_and_candidate_part_defaults():
    cand = CandidatePeak(peak=50.0, raw_score=0.8, start_win=0.0, end_win=110.0)
    assert cand.topic_start is None
    assert cand.topic_end is None

    clip = SnappedClip(peak=50.0, start=10.0, end=60.0, score=0.8)
    assert clip.part_index == 1
    assert clip.part_total == 1
    assert clip.group_id == ""
    assert clip.duration == 50.0


def test_snapped_clip_hook_and_description_defaults():
    clip = SnappedClip(peak=50.0, start=10.0, end=60.0, score=0.8)
    assert clip.hook_top == ""
    assert clip.description_long == ""
    assert clip.description_source == ""

    populated = SnappedClip(
        peak=50.0,
        start=10.0,
        end=60.0,
        score=0.8,
        hook_top="Ele perdeu tudo?",
        description_long="Resumo do trecho.",
        description_source="llm",
    )
    assert populated.hook_top == "Ele perdeu tudo?"
    assert populated.description_long == "Resumo do trecho."
    assert populated.description_source == "llm"
