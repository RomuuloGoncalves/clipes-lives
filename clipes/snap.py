"""Deterministic word- and silence-snapping boundary selection."""

from __future__ import annotations

import numpy as np

from .models import CandidatePeak, SignalBundle, SnappedClip, TopicSegment, Word
from .signals import FPS_RMS, SILENCE_DB
from .topics import segment_topics, split_topic_into_parts, words_to_sentences

MIN_PRE_ROLL = 6.0
IDEAL_PRE_ROLL_LOW = 6.0
IDEAL_PRE_ROLL_HIGH = 12.0
MIN_POST_ROLL = 3.0
PAUSE_LONG = 0.8
PAUSE_MED = 0.5
PEAK_POS_MIN = 0.35
PEAK_POS_MAX = 0.75


def _local_baseline_db(rms_db: np.ndarray, center_t: float, radius_s: float = 45.0) -> float:
    if rms_db.size == 0:
        return -35.0
    i0 = max(0, int((center_t - radius_s) * FPS_RMS))
    i1 = min(len(rms_db), int((center_t + radius_s) * FPS_RMS))
    seg = rms_db[i0:i1]
    voiced = seg[seg > SILENCE_DB]
    if voiced.size > 0:
        return float(np.median(voiced))
    return float(np.median(seg)) if seg.size > 0 else -35.0


def _db_at(rms_db: np.ndarray, t: float) -> float:
    if rms_db.size == 0:
        return -60.0
    idx = min(len(rms_db) - 1, max(0, int(round(t * FPS_RMS))))
    return float(rms_db[idx])


def _max_db_in(rms_db: np.ndarray, a: float, b: float) -> float:
    if rms_db.size == 0 or b <= a:
        return -60.0
    i0 = max(0, int(a * FPS_RMS))
    i1 = min(len(rms_db), max(i0 + 1, int(np.ceil(b * FPS_RMS))))
    return float(rms_db[i0:i1].max())


def _cuts_mid_word(t: float, words: list[Word], tol: float = 0.005) -> bool:
    for w in words:
        if w.start + tol < t < w.end - tol:
            return True
    return False


def _extend_past_reaction(
    w_end: float,
    next_start: float,
    rms_db: np.ndarray,
    baseline_db: float,
    duration: float,
) -> float:
    """Protect post-speech laughter/scream until RMS drops near baseline, never entering next word."""
    limit = min(duration, next_start - 0.02)
    base_end = min(limit, w_end + 0.35)
    if rms_db.size == 0 or limit <= base_end:
        return base_end

    # Scan 0.5s frames starting at w_end up to limit
    i_start = max(0, int(np.floor(w_end * FPS_RMS)))
    i_limit = min(len(rms_db), int(np.floor(limit * FPS_RMS)))
    hot_thr = baseline_db + 3.0
    calm_thr = baseline_db + 2.0

    last_hot_idx = -1
    calm_streak = 0
    for idx in range(i_start, i_limit):
        if rms_db[idx] > hot_thr:
            last_hot_idx = idx
            calm_streak = 0
        elif rms_db[idx] <= calm_thr:
            calm_streak += 1
            if last_hot_idx >= 0 and calm_streak >= 2:
                break

    if last_hot_idx >= 0:
        reaction_end = (last_hot_idx + 1) / float(FPS_RMS) + 0.35
        return min(limit, max(base_end, reaction_end))
    return base_end


def _speech_start_candidates(
    words: list[Word],
    peak: float,
    max_dur: float,
) -> list[tuple[float, float, str]]:
    """Return (t_start, quality_score, edge_type) candidates."""
    earliest = max(0.0, peak - PEAK_POS_MAX * max_dur)
    latest = peak - MIN_PRE_ROLL
    out: list[tuple[float, float, str]] = []

    for i, w in enumerate(words):
        if w.start < earliest or w.start > latest:
            continue
        prev = words[i - 1] if i > 0 else None
        if prev is not None and prev.end > w.start + 0.005:
            # Overlapping Whisper timestamps; avoid risking a mid-word cut.
            continue

        if prev is None:
            t_s = max(0.0, w.start - 0.25)
        elif w.start - prev.end >= 0.04:
            t_s = max(0.0, w.start - 0.25, prev.end + 0.02)
        else:
            t_s = (prev.end + w.start) * 0.5
        if _cuts_mid_word(t_s, words):
            continue

        gap = w.start - prev.end if prev else 99.0
        is_sentence = prev is None or prev.ends_sentence
        q = 0.0
        if is_sentence:
            q += 3.0
        if w.clean and w.clean[0].isupper():
            q += 0.6
        if gap >= PAUSE_LONG:
            q += 1.6
        elif gap >= PAUSE_MED:
            q += 0.9
        elif gap >= 0.3:
            q += 0.3
        if not is_sentence and gap < PAUSE_MED:
            q -= 2.5

        pre_roll = peak - t_s
        if IDEAL_PRE_ROLL_LOW <= pre_roll <= IDEAL_PRE_ROLL_HIGH:
            q += 1.0
        elif IDEAL_PRE_ROLL_HIGH < pre_roll <= 18.0:
            q += 0.5
        else:
            q -= min(1.5, abs(pre_roll - 10.0) / 12.0)

        edge_type = "sentence" if is_sentence else ("pause" if gap >= PAUSE_MED else "word_gap")
        out.append((round(t_s, 3), q, edge_type))

    return out


def _speech_end_candidates(
    words: list[Word],
    peak: float,
    max_dur: float,
    duration: float,
    rms_db: np.ndarray,
    baseline_db: float,
) -> list[tuple[float, float, str]]:
    """Return (t_end, quality_score, edge_type) candidates."""
    earliest = peak + MIN_POST_ROLL
    latest = min(duration, peak + (1.0 - PEAK_POS_MIN) * max_dur)
    out: list[tuple[float, float, str]] = []

    for j, w in enumerate(words):
        if w.end < peak - 2.0 or w.end > latest:
            continue
        nxt = words[j + 1] if j + 1 < len(words) else None
        if nxt is not None and nxt.start < w.end - 0.005:
            continue

        if nxt is None:
            t_e = _extend_past_reaction(w.end, duration, rms_db, baseline_db, duration)
            t_e = min(duration, max(t_e, w.end + 0.2))
        elif nxt.start - w.end >= 0.04:
            t_e = _extend_past_reaction(w.end, nxt.start, rms_db, baseline_db, duration)
        else:
            t_e = (w.end + nxt.start) * 0.5
        if t_e < earliest or t_e > latest or t_e > duration or _cuts_mid_word(t_e, words):
            continue

        gap = nxt.start - w.end if nxt else 99.0
        is_sentence = w.ends_sentence or nxt is None
        q = 0.0
        if is_sentence:
            q += 3.0
        if gap >= PAUSE_LONG:
            q += 1.6
        elif gap >= PAUSE_MED:
            q += 0.9
        elif gap >= 0.3:
            q += 0.3
        if not is_sentence and gap < PAUSE_MED:
            q -= 2.5

        # Prefer cuts where reaction/scream energy has subsided
        post_db = _max_db_in(rms_db, t_e, min(duration, t_e + 1.5))
        if post_db <= baseline_db + 2.5:
            q += 0.8
        elif post_db > baseline_db + 5.0:
            q -= 1.2

        edge_type = "sentence" if is_sentence else ("pause" if gap >= PAUSE_MED else "word_gap")
        out.append((round(t_e, 3), q, edge_type))

    return out


def _silence_edge_points(
    lo: float,
    hi: float,
    rms_db: np.ndarray,
    baseline_db: float,
    words: list[Word],
    peak: float | None = None,
) -> list[tuple[float, float, str]]:
    """Return quiet audio boundary points (t, score, 'silence') in [lo, hi]."""
    if rms_db.size == 0 or hi <= lo:
        return []
    pts: list[tuple[float, float, str]] = []
    for k in range(max(0, int(lo * FPS_RMS)), min(len(rms_db), int(hi * FPS_RMS) + 1)):
        t = round(k / float(FPS_RMS), 2)
        if _cuts_mid_word(t, words):
            continue
        db = float(rms_db[k])
        quiet_bonus = max(0.0, (baseline_db - db) / 6.0) + (1.0 if db <= -42.0 else 0.0)
        setup_bonus = 0.0
        if peak is not None:
            pre_roll = peak - t
            setup_bonus = 1.0 if IDEAL_PRE_ROLL_LOW <= pre_roll <= IDEAL_PRE_ROLL_HIGH else 0.3
        pts.append((t, quiet_bonus + setup_bonus, "silence"))
    pts.sort(key=lambda x: -x[1])
    return pts[:20]


def _silence_snap(
    peak: float,
    duration: float,
    rms_db: np.ndarray,
    baseline_db: float,
    min_dur: float,
    max_dur: float,
    words: list[Word],
) -> tuple[float, float, str, str, str] | None:
    """Fallback boundary snapping using audio silence/quiet dips when no speech is present."""
    if rms_db.size == 0:
        return None

    s_lo = max(0.0, peak - PEAK_POS_MAX * max_dur)
    s_hi = peak - MIN_PRE_ROLL
    e_lo = peak + MIN_POST_ROLL
    e_hi = min(duration, peak + (1.0 - PEAK_POS_MIN) * max_dur)
    s_cands = _silence_edge_points(s_lo, s_hi, rms_db, baseline_db, words, peak=peak)
    e_cands = _silence_edge_points(e_lo, e_hi, rms_db, baseline_db, words, peak=None)
    if not s_cands or not e_cands:
        return None

    best_pair = None
    best_q = -1e9
    for t_s, qs, _ in s_cands:
        for t_e, qe, _ in e_cands:
            dur = t_e - t_s
            if not (min_dur <= dur <= max_dur):
                continue
            ratio = (peak - t_s) / dur
            if not (PEAK_POS_MIN <= ratio <= PEAK_POS_MAX):
                continue
            q = qs + qe
            if q > best_q:
                best_q = q
                best_pair = (t_s, t_e, "silence", "silence", "silence")
    return best_pair


def _compute_signal_scores(
    candidate: CandidatePeak,
    start_t: float,
    end_t: float,
    bundle: SignalBundle | None,
) -> dict[str, float]:
    sig_scores = dict(candidate.signal_peaks)
    if bundle is not None:
        a = max(0, int(start_t))
        b = min(bundle.n, max(a + 1, int(np.ceil(end_t))))
        sig_scores = {
            name: round(float(vec[a:b].max()), 3)
            for name, vec in bundle.scores.items()
        }
    return sig_scores


def snap_candidate(
    candidate: CandidatePeak,
    words: list[Word],
    rms_db: np.ndarray,
    duration: float,
    min_dur: float = 25.0,
    max_dur: float = 55.0,
    bundle: SignalBundle | None = None,
) -> SnappedClip | None:
    """Snap candidate boundaries to sentence/pause or audio silence; return None if invalid."""
    if duration < min_dur:
        return None

    peak = candidate.peak
    local_words = [
        w for w in words
        if w.end >= peak - max_dur - 10.0 and w.start <= peak + max_dur + 10.0
    ]
    baseline_db = _local_baseline_db(rms_db, peak)

    best: tuple[float, float, str, str, str] | None = None
    best_q = -1e9

    if len(local_words) >= 4:
        s_cands = _speech_start_candidates(local_words, peak, max_dur)
        e_cands = _speech_end_candidates(local_words, peak, max_dur, duration, rms_db, baseline_db)
        if not s_cands:
            s_cands = _silence_edge_points(
                max(0.0, peak - PEAK_POS_MAX * max_dur), peak - MIN_PRE_ROLL,
                rms_db, baseline_db, local_words, peak=peak,
            )
        if not e_cands:
            e_cands = _silence_edge_points(
                peak + MIN_POST_ROLL, min(duration, peak + (1.0 - PEAK_POS_MIN) * max_dur),
                rms_db, baseline_db, local_words, peak=None,
            )
        for t_s, qs, s_type in s_cands:
            for t_e, qe, e_type in e_cands:
                dur = t_e - t_s
                if not (min_dur <= dur <= max_dur):
                    continue
                ratio = (peak - t_s) / dur
                if not (PEAK_POS_MIN <= ratio <= PEAK_POS_MAX):
                    continue
                pair_q = qs + qe
                if s_type in ("sentence", "pause") and e_type in ("sentence", "pause"):
                    pair_q += 1.5
                if 0.40 <= ratio <= 0.65:
                    pair_q += 0.4
                mode = "silence" if (s_type == "silence" and e_type == "silence") else "speech"
                if pair_q > best_q:
                    best_q = pair_q
                    best = (t_s, t_e, mode, s_type, e_type)
    else:
        best = _silence_snap(peak, duration, rms_db, baseline_db, min_dur, max_dur, local_words)

    if best is None:
        return None

    start_t, end_t, edge_mode, s_type, e_type = best
    # Final hard invariant checks (never force invalid boundaries)
    if not (min_dur - 1e-6 <= end_t - start_t <= max_dur + 1e-6):
        return None
    ratio = (peak - start_t) / (end_t - start_t)
    if not (PEAK_POS_MIN - 1e-6 <= ratio <= PEAK_POS_MAX + 1e-6):
        return None
    if _cuts_mid_word(start_t, local_words) or _cuts_mid_word(end_t, local_words):
        return None

    clip_words = [w.text for w in local_words if w.end > start_t and w.start < end_t]
    text = "".join(clip_words).strip()

    sig_scores = _compute_signal_scores(candidate, start_t, end_t, bundle)

    return SnappedClip(
        peak=round(peak, 2),
        start=round(start_t, 2),
        end=round(end_t, 2),
        score=round(candidate.raw_score, 4),
        active_signals=list(candidate.active_signals),
        signal_scores=sig_scores,
        text=text,
        edge_mode=edge_mode,
        start_edge_type=s_type,
        end_edge_type=e_type,
    )


def _fallback_single(
    candidate: CandidatePeak,
    win_words: list[Word],
    rms_db: np.ndarray,
    duration: float,
    min_dur: float,
    single_max_dur: float,
    bundle: SignalBundle | None,
) -> list[SnappedClip]:
    single = snap_candidate(
        candidate,
        words=win_words,
        rms_db=rms_db,
        duration=duration,
        min_dur=min_dur,
        max_dur=single_max_dur,
        bundle=bundle,
    )
    return [single] if single is not None else []


def snap_topic_parts(
    candidate: CandidatePeak,
    words: list[Word],
    rms_db: np.ndarray,
    duration: float,
    min_dur: float = 25.0,
    single_max_dur: float = 65.0,
    part_max_dur: float = 58.0,
    bundle: SignalBundle | None = None,
    onnx_path: str | None = None,
) -> list[SnappedClip]:
    """Snap a candidate to its semantic topic boundaries, splitting >65s topics into 2-3 parts."""
    if duration < min_dur:
        return []

    win_words = sorted(
        (
            w for w in words
            if w.clean and w.end >= candidate.start_win - 1.0 and w.start <= candidate.end_win + 1.0
        ),
        key=lambda w: (w.start, w.end),
    )
    if len(win_words) < 4:
        return _fallback_single(candidate, win_words, rms_db, duration, min_dur, single_max_dur, bundle)

    if candidate.topic_start is not None and candidate.topic_end is not None:
        t_words = [w for w in win_words if w.end > candidate.topic_start - 0.01 and w.start < candidate.topic_end + 0.01]
        topic: TopicSegment | None = TopicSegment(
            start=candidate.topic_start,
            end=candidate.topic_end,
            peak=candidate.peak,
            sentences=words_to_sentences(t_words),
        )
    else:
        topic = segment_topics(win_words, peak=candidate.peak, max_topic_dur=180.0, onnx_path=onnx_path)

    if topic is None or topic.duration < min_dur - 1e-6:
        return _fallback_single(candidate, win_words, rms_db, duration, min_dur, single_max_dur, bundle)

    # Legacy 90s window with uniform interior text falls back to peak-centered single snap.
    interior_texts = {
        s[2] for s in topic.sentences
        if s[0] >= candidate.start_win + 1.0 and s[1] <= candidate.end_win - 1.0
    }
    if (
        topic.duration > single_max_dur
        and (candidate.end_win - candidate.start_win) <= 90.5
        and len(interior_texts) <= 1
    ):
        return _fallback_single(candidate, win_words, rms_db, duration, min_dur, single_max_dur, bundle)

    raw_parts = split_topic_into_parts(
        topic,
        win_words,
        min_dur=min_dur,
        single_max_dur=single_max_dur,
        part_max_dur=part_max_dur,
    )
    if not raw_parts:
        return _fallback_single(candidate, win_words, rms_db, duration, min_dur, single_max_dur, bundle)

    baseline_db = _local_baseline_db(rms_db, candidate.peak)
    group_id = f"topic_{int(round(candidate.peak))}" if raw_parts[0][3] > 1 else ""
    built: list[SnappedClip] = []

    for idx_in_list, (p_start, p_end, part_idx, part_total) in enumerate(raw_parts):
        part_idxs = [
            i for i, w in enumerate(win_words)
            if w.end > p_start + 0.01 and w.start < p_end - 0.01
        ]
        if not part_idxs:
            return _fallback_single(candidate, win_words, rms_db, duration, min_dur, single_max_dur, bundle)

        first_pos = part_idxs[0]
        last_pos = part_idxs[-1]
        first_w = win_words[first_pos]
        last_w = win_words[last_pos]
        part_words = [win_words[i] for i in part_idxs]
        prev_w = win_words[first_pos - 1] if first_pos > 0 else None
        nxt_w = win_words[last_pos + 1] if last_pos + 1 < len(win_words) else None

        max_allowed = part_max_dur if part_total > 1 else single_max_dur
        s_t = p_start
        if part_idx == 1:
            if prev_w is None:
                cand_s = max(0.0, first_w.start - 0.25)
            elif first_w.start - prev_w.end >= 0.04:
                cand_s = max(0.0, first_w.start - 0.25, prev_w.end + 0.02)
            else:
                cand_s = (prev_w.end + first_w.start) * 0.5
            # Never let the 0.25s pre-roll push part 1 over max_allowed and abort the series.
            cand_s = max(cand_s, p_end - max_allowed)
            if not _cuts_mid_word(cand_s, win_words) and cand_s < p_end:
                s_t = cand_s

        e_t = p_end
        next_limit = min(duration, last_w.end + 15.0)
        if nxt_w is not None:
            next_limit = min(next_limit, nxt_w.start)
        if part_idx < part_total:
            next_limit = min(next_limit, raw_parts[idx_in_list + 1][0])
        if nxt_w is None or nxt_w.start - last_w.end >= 0.04:
            cand_e = _extend_past_reaction(last_w.end, next_limit, rms_db, baseline_db, duration)
            cand_e = min(cand_e, s_t + max_allowed, next_limit)
            if not _cuts_mid_word(cand_e, win_words) and cand_e >= last_w.end:
                e_t = cand_e

        dur = e_t - s_t
        if not (min_dur - 1e-6 <= dur <= max_allowed + 1e-6):
            return _fallback_single(candidate, win_words, rms_db, duration, min_dur, single_max_dur, bundle)
        if _cuts_mid_word(s_t, win_words) or _cuts_mid_word(e_t, win_words):
            return _fallback_single(candidate, win_words, rms_db, duration, min_dur, single_max_dur, bundle)

        gap_s = (first_w.start - prev_w.end) if prev_w is not None else 99.0
        s_type = "sentence" if (prev_w is None or prev_w.ends_sentence) else ("pause" if gap_s >= PAUSE_MED else "word_gap")
        gap_e = (nxt_w.start - last_w.end) if nxt_w is not None else 99.0
        e_type = "sentence" if (last_w.ends_sentence or nxt_w is None) else ("pause" if gap_e >= PAUSE_MED else "word_gap")

        text = " ".join(w.clean for w in part_words)
        sig_scores = _compute_signal_scores(candidate, s_t, e_t, bundle)

        built.append(SnappedClip(
            peak=round(candidate.peak, 2),
            start=round(s_t, 2),
            end=round(e_t, 2),
            score=round(candidate.raw_score, 4),
            active_signals=list(candidate.active_signals),
            signal_scores=sig_scores,
            text=text,
            edge_mode="topic",
            start_edge_type=s_type,
            end_edge_type=e_type,
            part_index=part_idx,
            part_total=part_total,
            group_id=group_id,
        ))

    return built


def select_clips(
    candidates: list[CandidatePeak],
    words: list[Word],
    rms_db: np.ndarray,
    duration: float,
    max_clips: int = 8,
    min_dur: float = 25.0,
    max_dur: float = 65.0,
    bundle: SignalBundle | None = None,
) -> list[SnappedClip]:
    """Snap candidates by topic/parts, apply group-aware NMS, and never truncate a multi-part series."""
    chosen: list[SnappedClip] = []
    for cand in sorted(candidates, key=lambda c: -c.raw_score):
        if len(chosen) >= max_clips:
            break
        parts = snap_topic_parts(
            cand,
            words=words,
            rms_db=rms_db,
            duration=duration,
            min_dur=min_dur,
            single_max_dur=max_dur,
            part_max_dur=min(58.0, max_dur),
            bundle=bundle,
        )
        if not parts:
            continue
        if cand.topic_start is None and len(parts) > 1:
            hit = next((p for p in parts if p.start <= cand.peak <= p.end), None)
            if hit is None:
                hit = min(
                    parts,
                    key=lambda p: min(abs(cand.peak - p.start), abs(cand.peak - p.end)),
                )
            hit.part_index = 1
            hit.part_total = 1
            hit.group_id = ""
            parts = [hit]
        if len(chosen) > 0 and len(chosen) + len(parts) > max_clips + 1:
            continue
        if any(p.start < c.end + 1.0 and p.end > c.start - 1.0 for p in parts for c in chosen):
            continue
        chosen.extend(parts)
    return chosen
