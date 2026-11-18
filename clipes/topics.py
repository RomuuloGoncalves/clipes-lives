"""Semantic topic segmentation (MiniLM ONNX int8 + pause fallback) and multi-part splitting."""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.stats import rankdata

from . import capacidade
from .models import SignalBundle, TopicSegment, Word
from .render import TITLE_KEYWORDS
from .signals import signal_prosody

logger = logging.getLogger(__name__)

DEFAULT_EMBED_ONNX = "/opt/models/embed/onnx/model_quint8_avx2.onnx"


def _cuts_mid_word(t: float, words: list[Word], tol: float = 0.005) -> bool:
    return any(w.start + tol < t < w.end - tol for w in words)


def words_to_sentences(words: list[Word], pause_split: float = 0.8) -> list[tuple[float, float, str]]:
    """Group Word tokens into (start, end, text) sentences using punctuation or pauses."""
    ordered = sorted((w for w in words if w.clean), key=lambda w: (w.start, w.end))
    if not ordered:
        return []

    sentences: list[tuple[float, float, str]] = []
    buf: list[Word] = []
    for i, w in enumerate(ordered):
        buf.append(w)
        nxt = ordered[i + 1] if i + 1 < len(ordered) else None
        gap = (nxt.start - w.end) if nxt is not None else 0.0
        if w.ends_sentence or (nxt is not None and gap >= pause_split):
            text = " ".join(x.clean for x in buf)
            sentences.append((round(buf[0].start, 3), round(buf[-1].end, 3), text))
            buf = []
    if buf:
        text = " ".join(x.clean for x in buf)
        sentences.append((round(buf[0].start, 3), round(buf[-1].end, 3), text))
    return sentences


def embed_sentences(texts: list[str], onnx_path: str | None = None) -> np.ndarray | None:
    """Compute L2-normalized mean-pooled embeddings via MiniLM ONNX int8; None on failure."""
    if not texts:
        return None

    raw_path = onnx_path if onnx_path is not None else os.environ.get("CLIPES_EMBED_ONNX", DEFAULT_EMBED_ONNX)
    model_path = Path(raw_path)
    tok_candidates = [
        model_path.parent / "tokenizer.json",
        model_path.parent.parent / "tokenizer.json",
    ]
    tok_path = next((p for p in tok_candidates if p.is_file()), None)

    if not model_path.is_file() or tok_path is None:
        msg = "[topics] MiniLM ONNX indisponível, usando fallback de pausa/frase"
        logger.warning(msg)
        print(msg, flush=True)
        return None

    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(str(tok_path))
        if tok.padding is None:
            pad_id = tok.token_to_id("<pad>")
            tok.enable_padding(pad_id=pad_id if pad_id is not None else 1, pad_token="<pad>")
        if tok.truncation is None:
            tok.enable_truncation(max_length=128)

        encoded = tok.encode_batch(texts)
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        token_type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = capacidade.cpu_threads()
        opts.inter_op_num_threads = 1
        sess = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])

        input_names = {inp.name for inp in sess.get_inputs()}
        feeds: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in input_names:
            feeds["token_type_ids"] = token_type_ids

        last_hidden = sess.run(None, feeds)[0]
        mask = attention_mask[:, :, None].astype(np.float32)
        summed = (last_hidden * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), 1e-9, None)
        emb = summed / counts
        norms = np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9, None)
        return (emb / norms).astype(np.float32)
    except Exception as exc:
        msg = f"[topics] MiniLM ONNX indisponível, usando fallback de pausa/frase ({exc})"
        logger.warning(msg)
        print(msg, flush=True)
        return None


def _peak_sentence_index(sentences: list[tuple[float, float, str]], peak: float) -> int:
    for i, (s, e, _) in enumerate(sentences):
        if s <= peak <= e:
            return i
    # If peak falls in a reaction gap right after a sentence, prefer the sentence just before peak
    best_i = 0
    best_dist = 1e9
    for i, (s, e, _) in enumerate(sentences):
        dist = 0.0 if s <= peak <= e else min(abs(peak - s), abs(peak - e))
        if e <= peak <= e + 5.0:
            dist *= 0.5
        if dist < best_dist:
            best_dist = dist
            best_i = i
    return best_i


def _trim_sentences_to_max(
    sentences: list[tuple[float, float, str]],
    start_idx: int,
    end_idx: int,
    peak: float,
    peak_idx: int,
    max_dur: float,
) -> tuple[int, int]:
    """Trim outermost sentences farthest from peak until span <= max_dur."""
    while end_idx - start_idx > 1 and (sentences[end_idx - 1][1] - sentences[start_idx][0]) > max_dur:
        can_drop_left = start_idx < peak_idx
        can_drop_right = (end_idx - 1) > peak_idx
        if not can_drop_left and not can_drop_right:
            break
        if can_drop_left and can_drop_right:
            left_dist = peak - sentences[start_idx][0]
            right_dist = sentences[end_idx - 1][1] - peak
            if left_dist >= right_dist:
                start_idx += 1
            else:
                end_idx -= 1
        elif can_drop_left:
            start_idx += 1
        else:
            end_idx -= 1
    return start_idx, end_idx


def _block_similarities(embs: np.ndarray, block_size: int = 3) -> np.ndarray:
    """Cosine similarity between left and right windows of up to `block_size` sentences."""
    m = len(embs)
    sims = np.empty(max(0, m - 1), dtype=np.float32)
    for b in range(1, m):
        left = embs[max(0, b - block_size) : b].mean(axis=0)
        right = embs[b : min(m, b + block_size)].mean(axis=0)
        ln = float(np.linalg.norm(left))
        rn = float(np.linalg.norm(right))
        sims[b - 1] = float(np.dot(left, right) / (ln * rn)) if ln > 1e-9 and rn > 1e-9 else 0.0
    return sims


def _fallback_bounds(
    sentences: list[tuple[float, float, str]],
    peak_idx: int,
    pause_split: float = 0.8,
) -> tuple[int, int]:
    """Delimit topic around peak_idx using sentence endings followed by pauses >= pause_split."""
    m = len(sentences)

    def _is_pause_boundary(b: int) -> bool:
        gap = sentences[b][0] - sentences[b - 1][1]
        prev_text = sentences[b - 1][2].rstrip("\"'”’)")
        ends_sent = prev_text.endswith((".", "!", "?"))
        return gap >= pause_split and (ends_sent or gap >= 1.2)

    start_idx = 0
    for b in range(peak_idx, 0, -1):
        if _is_pause_boundary(b):
            start_idx = b
            break

    end_idx = m
    for b in range(peak_idx + 1, m):
        if _is_pause_boundary(b):
            end_idx = b
            break

    # If every sentence had a >=0.8s pause, don't collapse to a tiny <25s stub when context exists.
    if sentences[end_idx - 1][1] - sentences[start_idx][0] < 25.0 and m > 1:
        gaps = [sentences[b][0] - sentences[b - 1][1] for b in range(1, m)]
        strong_thr = max(1.5, float(np.median(gaps)) * 1.4) if gaps else 1.5
        s_idx, e_idx = start_idx, end_idx
        while (sentences[e_idx - 1][1] - sentences[s_idx][0]) < 25.0 and (s_idx > 0 or e_idx < m):
            left_gap = (sentences[s_idx][0] - sentences[s_idx - 1][1]) if s_idx > 0 else 1e9
            right_gap = (sentences[e_idx][0] - sentences[e_idx - 1][1]) if e_idx < m else 1e9
            if min(left_gap, right_gap) >= strong_thr:
                break
            if left_gap <= right_gap and s_idx > 0:
                s_idx -= 1
            elif e_idx < m:
                e_idx += 1
            else:
                break
        start_idx, end_idx = s_idx, e_idx

    return start_idx, end_idx


def segment_topics(
    words: list[Word],
    peak: float,
    max_topic_dur: float = 180.0,
    onnx_path: str | None = None,
) -> TopicSegment | None:
    """Detect [topic_start, topic_end] around `peak` using MiniLM ONNX valleys or pause fallback."""
    local_words = [
        w for w in words
        if w.clean and w.end >= peak - max_topic_dur - 30.0 and w.start <= peak + max_topic_dur + 30.0
    ]
    if len(local_words) < 4:
        return None

    sentences = words_to_sentences(local_words, pause_split=0.8)
    if not sentences:
        return None

    peak_idx = _peak_sentence_index(sentences, peak)
    m = len(sentences)
    if m == 1:
        s0, e0, _ = sentences[0]
        return TopicSegment(start=s0, end=e0, peak=peak, sentences=sentences, boundary_score=0.0)

    embs = embed_sentences([s[2] for s in sentences], onnx_path=onnx_path)
    boundary_score = 0.0

    if embs is not None:
        sims = _block_similarities(embs, block_size=3)
        mean_sim = float(np.mean(sims))
        std_sim = float(np.std(sims))
        threshold = mean_sim - 1.0 * std_sim

        start_idx = 0
        end_idx = m
        if std_sim > 1e-6:
            def _is_boundary(b: int) -> bool:
                s = float(sims[b - 1])
                if s < threshold:
                    return True
                gap = sentences[b][0] - sentences[b - 1][1]
                left_s = float(sims[b - 2]) if b >= 2 else s + 1.0
                right_s = float(sims[b]) if b < len(sims) else s + 1.0
                return gap >= 0.8 and s < left_s and s < right_s and s < mean_sim

            for b in range(peak_idx, 0, -1):
                if _is_boundary(b):
                    start_idx = b
                    break
            for b in range(peak_idx + 1, m):
                if _is_boundary(b):
                    end_idx = b
                    break

        drops: list[float] = []
        if start_idx > 0:
            drops.append(mean_sim - float(sims[start_idx - 1]))
        if end_idx < m:
            drops.append(mean_sim - float(sims[end_idx - 1]))
        if drops:
            boundary_score = round(float(np.mean(drops)), 4)
    else:
        start_idx, end_idx = _fallback_bounds(sentences, peak_idx, pause_split=0.8)

    start_idx, end_idx = _trim_sentences_to_max(
        sentences, start_idx, end_idx, peak=peak, peak_idx=peak_idx, max_dur=max_topic_dur
    )
    chosen_sents = sentences[start_idx:end_idx]
    return TopicSegment(
        start=chosen_sents[0][0],
        end=chosen_sents[-1][1],
        peak=peak,
        sentences=chosen_sents,
        boundary_score=boundary_score,
    )


def _valid_cut_points(words: list[Word], topic_start: float, topic_end: float) -> list[float]:
    """Return sorted timestamps inside (topic_start, topic_end) on sentence ends or pauses >= 0.5s."""
    topic_words = sorted(
        (w for w in words if w.clean and w.end > topic_start - 0.01 and w.start < topic_end + 0.01),
        key=lambda w: (w.start, w.end),
    )
    cuts: list[float] = []
    for i in range(len(topic_words) - 1):
        w_left = topic_words[i]
        w_right = topic_words[i + 1]
        if w_right.start < w_left.end - 0.005:
            continue
        gap = w_right.start - w_left.end
        if not (w_left.ends_sentence or gap >= 0.5):
            continue
        t_cut = round((w_left.end + w_right.start) * 0.5, 3)
        if topic_start + 0.1 < t_cut < topic_end - 0.1 and not _cuts_mid_word(t_cut, topic_words):
            cuts.append(t_cut)
    return cuts


def _best_k_split(
    start: float,
    end: float,
    cuts: list[float],
    n_parts: int,
    min_dur: float,
    max_dur: float,
) -> list[tuple[float, float]] | None:
    """Find n_parts contiguous ranges splitting [start, end] at valid cuts with minimal variance."""
    target = (end - start) / float(n_parts)

    def _penalty(durs: list[float]) -> float:
        return sum((d - target) ** 2 + (max(0.0, 45.0 - d) * 2.0) ** 2 for d in durs)

    if n_parts == 2:
        best_pair: list[tuple[float, float]] | None = None
        best_cost = 1e18
        for c in cuts:
            d1 = c - start
            d2 = end - c
            if min_dur - 1e-6 <= d1 <= max_dur + 1e-6 and min_dur - 1e-6 <= d2 <= max_dur + 1e-6:
                cost = _penalty([d1, d2])
                if cost < best_cost:
                    best_cost = cost
                    best_pair = [(start, c), (c, end)]
        return best_pair

    if n_parts == 3:
        best_triple: list[tuple[float, float]] | None = None
        best_cost = 1e18
        for i, c1 in enumerate(cuts):
            d1 = c1 - start
            if d1 < min_dur - 1e-6:
                continue
            if d1 > max_dur + 1e-6:
                break
            for c2 in cuts[i + 1 :]:
                d2 = c2 - c1
                if d2 < min_dur - 1e-6:
                    continue
                if d2 > max_dur + 1e-6:
                    break
                d3 = end - c2
                if min_dur - 1e-6 <= d3 <= max_dur + 1e-6:
                    cost = _penalty([d1, d2, d3])
                    if cost < best_cost:
                        best_cost = cost
                        best_triple = [(start, c1), (c1, c2), (c2, end)]
        return best_triple

    return None


def split_topic_into_parts(
    topic: TopicSegment,
    words: list[Word],
    min_dur: float = 25.0,
    single_max_dur: float = 65.0,
    part_max_dur: float = 58.0,
) -> list[tuple[float, float, int, int]]:
    """Split topic into 1 part (<= 65s) or 2-3 parts (<= 58s each) at sentence/pause borders."""
    dur = topic.end - topic.start
    if dur < min_dur - 1e-6:
        return []
    if dur <= single_max_dur + 1e-6:
        return [(round(topic.start, 3), round(topic.end, 3), 1, 1)]

    # Ensure topic fits within 3 * part_max_dur (174s) when splitting into up to 3 parts
    sents = list(topic.sentences) if topic.sentences else words_to_sentences(
        [w for w in words if w.end > topic.start - 0.01 and w.start < topic.end + 0.01]
    )
    t_start, t_end = topic.start, topic.end
    max_total = 3.0 * part_max_dur
    if dur > max_total and len(sents) > 1:
        p_idx = _peak_sentence_index(sents, topic.peak)
        s_i, e_i = _trim_sentences_to_max(sents, 0, len(sents), topic.peak, p_idx, max_total)
        t_start, t_end = sents[s_i][0], sents[e_i - 1][1]
        dur = t_end - t_start
        if dur < min_dur - 1e-6:
            return []
        if dur <= single_max_dur + 1e-6:
            return [(round(t_start, 3), round(t_end, 3), 1, 1)]

    cuts = _valid_cut_points(words, t_start, t_end)
    if not cuts:
        return []

    n_order = (2, 3) if dur <= 2.0 * part_max_dur + 1e-6 else (3,)
    for n_parts in n_order:
        ranges = _best_k_split(t_start, t_end, cuts, n_parts, min_dur, part_max_dur)
        if ranges is not None:
            return [
                (round(a, 3), round(b, 3), idx, n_parts)
                for idx, (a, b) in enumerate(ranges, 1)
            ]
    return []


@dataclass
class ScoredSegment(TopicSegment):
    """TopicSegment with its CheapScore in [0, 1] and word count (quota mode)."""

    score: float = 0.0
    word_count: int = 0


STORY_RE = re.compile(
    r"\b(?:aí|daí|quando eu|eu tava|tava|história|lembro|imagina|do nada|de repente|"
    r"sabe o que|cara|mano|velho|sério|juro|nunca|primeira vez|risos?)\b|k{3,}|(?:ha){2,}|rs{2,}",
    re.IGNORECASE,
)
DONATE_RE = re.compile(
    r"(?:obrigad[oa]|brigad[oa]|valeu)\s+(?:pel[oa]s?|por)\s+(?:\w+\s+){0,2}?"
    r"(?:sub|subs|inscri\w*|bits?|pix|donate|doa\w*|prime|membro\w*|super ?chat)\b"
    r"|\bse inscrev\w*|\bdeixa (?:o|seu) like\b",
    re.IGNORECASE,
)
CHEAP_WEIGHTS = {"hook": 0.25, "story": 0.20, "pace": 0.15, "prosody": 0.25, "ext": 0.15}


def _window_mean(vec: np.ndarray, start: float, end: float) -> float:
    i0 = max(0, int(start))
    i1 = min(len(vec), max(i0 + 1, int(math.ceil(end))))
    return float(vec[i0:i1].mean()) if i1 > i0 else 0.0


def _video_rank(values: list[float]) -> np.ndarray:
    """Percentile rank in [0, 1] within this video (ties share the average rank)."""
    if len(values) == 1:
        return np.array([0.5])
    return (rankdata(values, method="average") - 1.0) / (len(values) - 1.0)


def score_topic_segments(segments: list[TopicSegment], bundle: SignalBundle) -> list[ScoredSegment]:
    """CheapScore: hook + story/reaction (minus sub/donate reading) + pace + prosody + chat/heatmap.

    hook and pace are absolute; story, prosody and ext are ranked by percentile inside the video.
    """
    if not segments:
        return []

    prosody = signal_prosody(bundle.rms_db, bundle.n)
    loudness = bundle.scores.get("loudness", np.zeros(bundle.n))
    ext_vecs = [bundle.scores[k] for k in ("chat", "heatmap") if k in bundle.scores]

    hooks, paces, stories, pros, exts, donates, counts = [], [], [], [], [], [], []
    for seg in segments:
        texts = [s[2] for s in seg.sentences]
        text = " ".join(texts)
        n_words = len(re.findall(r"\w+", text))
        dur = max(seg.end - seg.start, 1.0)
        wps = n_words / dur

        hooks.append(1.0 if any("?" in t or "!" in t for t in texts[:2]) else 0.0)
        paces.append(1.0 if 2.0 <= wps <= 4.2 else max(0.0, 1.0 - min(abs(wps - 2.0), abs(wps - 4.2)) / 1.5))
        hits = len(STORY_RE.findall(text)) + len(TITLE_KEYWORDS.findall(text))
        stories.append(hits * 60.0 / dur)
        pros.append(0.7 * _window_mean(prosody, seg.start, seg.end) + 0.3 * _window_mean(loudness, seg.start, seg.end))
        exts.append(float(np.mean([_window_mean(v, seg.start, seg.end) for v in ext_vecs])) if ext_vecs else 0.0)
        donates.append(bool(DONATE_RE.search(text)))
        counts.append(n_words)

    story_r = _video_rank(stories)
    pros_r = _video_rank(pros)
    ext_r = _video_rank(exts)
    weights = dict(CHEAP_WEIGHTS)
    if not ext_vecs:
        weights.pop("ext")
    total_w = sum(weights.values())

    out: list[ScoredSegment] = []
    for i, seg in enumerate(segments):
        feats = {"hook": hooks[i], "story": story_r[i], "pace": paces[i], "prosody": pros_r[i], "ext": ext_r[i]}
        score = sum(w * feats[k] for k, w in weights.items()) / total_w
        if donates[i]:
            score *= 0.5
        out.append(ScoredSegment(
            start=seg.start,
            end=seg.end,
            peak=seg.peak,
            sentences=list(seg.sentences),
            boundary_score=seg.boundary_score,
            score=round(float(np.clip(score, 0.0, 1.0)), 4),
            word_count=counts[i],
        ))
    return out


def _bigrams(seg: TopicSegment) -> set[tuple[str, str]]:
    tokens = re.findall(r"\w+", " ".join(s[2] for s in seg.sentences).casefold())
    return set(zip(tokens, tokens[1:]))


def dedup_segments_text(segments: list[TopicSegment], max_jaccard: float = 0.60) -> list[TopicSegment]:
    """Drop segments whose bigram Jaccard with a higher-scored kept segment exceeds max_jaccard."""
    kept: list[TopicSegment] = []
    kept_grams: list[set[tuple[str, str]]] = []
    for seg in sorted(segments, key=lambda s: -float(getattr(s, "score", 0.0))):
        grams = _bigrams(seg)
        if any(grams and g and len(grams & g) / len(grams | g) > max_jaccard for g in kept_grams):
            continue
        kept.append(seg)
        kept_grams.append(grams)
    return kept
