"""Synthetic tests for deterministic word/silence snapping."""

import numpy as np

from clipes.eval import cuts_mid_word, evaluate_clips
from clipes.models import CandidatePeak, Word
from clipes.signals import FPS_RMS
from clipes.snap import select_clips, snap_candidate, snap_topic_parts


def _make_speech_timeline(
    total_s: float = 300.0,
    sentence_period: float = 8.0,
) -> tuple[list[Word], np.ndarray]:
    """Generate realistic synthetic words with sentences, pauses, and baseline RMS."""
    words: list[Word] = []
    t = 2.0
    sent_idx = 0
    while t + 6.5 < total_s:
        tokens = ["Olha", "só", "o", "que", "acabou", "de", "acontecer", "ali", "agora!"]
        for k, tok in enumerate(tokens):
            w_start = round(t, 3)
            w_end = round(t + 0.38, 3)
            words.append(Word(start=w_start, end=w_end, text=(" " if k else " ") + tok))
            t = w_end + 0.12
        # Pause >= 0.9s after each sentence
        t += max(0.95, sentence_period - len(tokens) * 0.5)
        sent_idx += 1

    rms = np.full(int(total_s * FPS_RMS), -32.0, dtype=np.float32)
    return words, rms


def test_snap_synthetic_speech_metrics():
    duration = 320.0
    words, rms = _make_speech_timeline(duration, sentence_period=7.5)
    peaks = [65.0, 160.0, 255.0]
    for p in peaks:
        # Plant a loud reaction around each peak
        i0 = int((p - 1.0) * FPS_RMS)
        i1 = int((p + 2.0) * FPS_RMS)
        rms[i0:i1] = -14.0

    cands = [
        CandidatePeak(peak=p, raw_score=0.9 - i * 0.1, start_win=p - 45, end_win=p + 45, active_signals=["loudness"])
        for i, p in enumerate(peaks)
    ]
    clips = select_clips(cands, words, rms, duration, max_clips=5, min_dur=25.0, max_dur=55.0)
    assert len(clips) == 3

    metrics = evaluate_clips(clips, words, min_dur=25.0, max_dur=55.0, ground_truth_peaks=peaks, k=5)
    assert metrics["mid_word_cut_rate"] == 0.0
    assert metrics["sentence_edge_rate"] >= 0.85
    assert metrics["duration_ok_rate"] == 1.0
    assert metrics["peak_position_ok_rate"] == 1.0
    assert metrics["recall_at_5"] == 1.0


def test_snap_protects_post_speech_laughter():
    """When elevated RMS (laughter/scream) follows the final word before a long pause, end extends."""
    words = [
        Word(10.0, 10.4, " Olha"),
        Word(10.5, 11.0, " isso"),
        Word(11.1, 11.6, " aqui!"),
        # pause 1.2s
        Word(12.8, 13.2, " Cara,"),
        Word(13.3, 13.8, " espera"),
        Word(13.9, 14.4, " um"),
        Word(14.5, 15.0, " pouco."),
        # climax sentence around t=25
        Word(23.5, 24.0, " Meu"),
        Word(24.1, 24.5, " Deus"),
        Word(24.6, 25.2, " do"),
        Word(25.3, 25.8, " céu!"),
        # 5s laughter/scream gap from 25.8 to 33.5 before next sentence at 34.0
        Word(34.0, 34.4, " Bom,"),
        Word(34.5, 35.0, " vamos"),
        Word(35.1, 35.7, " seguir."),
        # long extra sentence for range
        Word(38.0, 38.4, " Tudo"),
        Word(38.5, 39.0, " certo."),
    ]
    duration = 60.0
    rms = np.full(int(duration * FPS_RMS), -35.0, dtype=np.float32)
    # Laughing/screaming elevated energy from 25.0s through 31.0s
    rms[int(25.0 * FPS_RMS):int(31.0 * FPS_RMS)] = -15.0

    cand = CandidatePeak(peak=25.5, raw_score=0.95, start_win=0.0, end_win=60.0, active_signals=["loudness"])
    clip = snap_candidate(cand, words, rms, duration, min_dur=20.0, max_dur=55.0)
    assert clip is not None
    assert not cuts_mid_word(clip.start, words)
    assert not cuts_mid_word(clip.end, words)
    # Must protect the laugh through ~31.0s rather than cutting at 26.15s
    assert clip.end >= 31.0
    assert 20.0 <= clip.duration <= 55.0
    assert 0.35 <= clip.peak_ratio <= 0.75


def test_snap_sound_only_fallback_uses_silence():
    """Window with no speech falls back to audio silence boundaries."""
    duration = 120.0
    rms = np.full(int(duration * FPS_RMS), -30.0, dtype=np.float32)
    # Quiet dips around 40s and 72s; scream peak at 55s
    rms[int(39.0 * FPS_RMS):int(41.0 * FPS_RMS)] = -52.0
    rms[int(54.0 * FPS_RMS):int(57.0 * FPS_RMS)] = -10.0
    rms[int(71.0 * FPS_RMS):int(73.0 * FPS_RMS)] = -52.0

    cand = CandidatePeak(peak=55.0, raw_score=0.9, start_win=10.0, end_win=100.0, active_signals=["loudness"])
    clip = snap_candidate(cand, words=[], rms_db=rms, duration=duration, min_dur=25.0, max_dur=55.0)
    assert clip is not None
    assert clip.edge_mode == "silence"
    assert 25.0 <= clip.duration <= 55.0
    assert 0.35 <= clip.peak_ratio <= 0.75
    assert 38.5 <= clip.start <= 42.0
    assert 70.5 <= clip.end <= 73.5


def test_snap_discards_impossible_candidate():
    """Candidates that cannot meet duration/peak-ratio/non-mid-word constraints are discarded."""
    # Video shorter than min_dur
    rms = np.full(40, -30.0, dtype=np.float32)
    cand = CandidatePeak(peak=10.0, raw_score=0.9, start_win=0.0, end_win=20.0)
    assert snap_candidate(cand, words=[], rms_db=rms, duration=20.0, min_dur=25.0, max_dur=55.0) is None

    # One single giant unbreakable word spanning the whole possible boundary range
    words = [Word(0.0, 90.0, "unbreakable")]
    rms_long = np.full(200, -30.0, dtype=np.float32)
    cand2 = CandidatePeak(peak=45.0, raw_score=0.9, start_win=0.0, end_win=90.0)
    assert snap_candidate(cand2, words=words, rms_db=rms_long, duration=100.0, min_dur=25.0, max_dur=55.0) is None


def _words_from_specs(specs: list[tuple[float, float, str]]) -> list[Word]:
    words: list[Word] = []
    for s, e, text in specs:
        toks = text.split()
        step = (e - s) / max(1, len(toks))
        for i, tok in enumerate(toks):
            ws = round(s + i * step, 3)
            we = round(s + (i + 1) * step - 0.04, 3)
            if we <= ws:
                we = round(ws + 0.05, 3)
            words.append(Word(ws, we, (" " + tok) if i > 0 else tok))
    return words


def test_snap_topic_punchline_at_end_keeps_setup():
    """A story with peak_ratio ~0.85 must keep its full setup without being discarded."""
    specs = [
        (0.0, 8.0, "Comprei farinha, ovos e chocolate para assar um bolo na cozinha."),
        (8.3, 16.0, "Misturei tudo na tigela e coloquei a forma no forno bem quente."),
        (16.3, 24.0, "O cheiro do bolo assado tomou conta da casa durante a tarde."),
        # Story from 26s to 80s (54s), peak at 72s -> ratio ~0.85
        (26.0, 35.0, "Entrei na arena do chefe final sem nenhuma poção sobrando no inventário."),
        (35.3, 45.0, "O chefe levantou a espada gigante e eu rolei para desviar do ataque."),
        (45.3, 56.0, "Minha barra de vida estava com um pixel e quase morri no canto."),
        (56.3, 67.0, "Esperei o último segundo do golpe dele para encaixar o contra-ataque."),
        (67.3, 80.0, "Derrotei o chefe sem tomar dano nenhum, meu Deus do céu que absurdo!"),
        # Topic 3
        (83.0, 92.0, "Mudando de assunto, o preço da placa de vídeo subiu neste mês."),
        (92.3, 102.0, "Muita gente está comparando memória RAM e SSD antes de montar PC."),
        (102.3, 112.0, "A fonte precisa ter potência suficiente para aguentar o processador."),
    ]
    words = _words_from_specs(specs)
    duration = 120.0
    rms = np.full(int(duration * FPS_RMS), -34.0, dtype=np.float32)
    rms[int(71.0 * FPS_RMS) : int(74.0 * FPS_RMS)] = -12.0

    cand = CandidatePeak(peak=72.0, raw_score=0.95, start_win=0.0, end_win=120.0, active_signals=["loudness"])
    parts = snap_topic_parts(cand, words, rms, duration, min_dur=25.0, single_max_dur=65.0)
    assert len(parts) == 1
    clip = parts[0]
    assert clip.edge_mode == "topic"
    assert clip.part_index == 1 and clip.part_total == 1
    assert clip.peak_ratio > 0.75
    assert 25.0 <= clip.duration <= 65.0
    assert clip.start <= 30.0 and clip.end >= 79.0
    assert not cuts_mid_word(clip.start, words)
    assert not cuts_mid_word(clip.end, words)


def test_snap_topic_120s_splits_into_valid_series():
    """A 120s topic must emit 2-3 consecutive parts <= 58s with zero mid-word cuts."""
    specs = [
        (i * 10.0, i * 10.0 + 9.5, f"Capítulo {i} da história completa sobre a corrida maluca no torneio.")
        for i in range(12)
    ]
    words = _words_from_specs(specs)
    duration = 130.0
    rms = np.full(int(duration * FPS_RMS), -34.0, dtype=np.float32)
    rms[int(95.0 * FPS_RMS) : int(98.0 * FPS_RMS)] = -12.0

    cand = CandidatePeak(
        peak=96.0, raw_score=0.92, start_win=0.0, end_win=125.0,
        topic_start=0.0, topic_end=119.46, active_signals=["loudness"],
    )
    parts = snap_topic_parts(cand, words, rms, duration, min_dur=25.0, single_max_dur=65.0, part_max_dur=58.0)
    assert len(parts) in (2, 3)
    n_total = len(parts)
    assert all(p.part_total == n_total for p in parts)
    assert [p.part_index for p in parts] == list(range(1, n_total + 1))
    assert len({p.group_id for p in parts}) == 1 and parts[0].group_id != ""
    for p in parts:
        assert 25.0 <= p.duration <= 58.0
        assert not cuts_mid_word(p.start, words)
        assert not cuts_mid_word(p.end, words)


def test_select_clips_never_truncates_multipart_series():
    """When max_clips would cut a 3-part series in half, select_clips skips the incomplete series."""
    specs = []
    # Topic A around 70s (single 40s clip)
    for i in range(4):
        specs.append((40.0 + i * 10.0, 40.0 + i * 10.0 + 9.4, f"História rápida {i}."))
    # Topic B around 250s (135s -> 3 parts)
    for i in range(15):
        specs.append((180.0 + i * 9.0, 180.0 + i * 9.0 + 8.5, f"História longa {i}."))
    words = _words_from_specs(specs)
    duration = 350.0
    rms = np.full(int(duration * FPS_RMS), -34.0, dtype=np.float32)

    cands = [
        CandidatePeak(peak=70.0, raw_score=0.95, start_win=35.0, end_win=85.0, topic_start=40.0, topic_end=79.36),
        CandidatePeak(peak=250.0, raw_score=0.80, start_win=175.0, end_win=320.0, topic_start=180.0, topic_end=314.46),
    ]
    # max_clips=2: Topic A takes 1 slot, Topic B wants 3 slots (1 + 3 = 4 > 2 + 1) -> must NOT emit 1/3 alone
    chosen = select_clips(cands, words, rms, duration, max_clips=2, min_dur=25.0, max_dur=65.0)
    assert len(chosen) == 1
    assert chosen[0].part_total == 1

    # max_clips=3: Topic A (1) + Topic B (3) = 4 <= 3 + 1 -> emits complete Topic B (1/3, 2/3, 3/3)
    chosen_full = select_clips(cands, words, rms, duration, max_clips=3, min_dur=25.0, max_dur=65.0)
    assert len(chosen_full) == 4
    assert [c.part_index for c in chosen_full[1:]] == [1, 2, 3]
    assert all(c.part_total == 3 for c in chosen_full[1:])


def test_snap_topic_part1_preroll_capped_to_part_max_dur():
    """Part 1 pre-roll step-back (-0.25s) must not exceed part_max_dur and abort the series."""
    specs = [
        (0.0, 5.0, "Assunto anterior bem longe."),
        (10.0, 29.0, "Começo exato da história principal muito longa."),
        (29.3, 48.0, "Desenvolvimento detalhado do meio da história."),
        (48.3, 67.7, "Preparação final antes da virada da parte um."),
        (68.1, 87.0, "Continuação imediata logo após o corte de parte."),
        (87.3, 106.0, "Clímax emocionante da história inteira acontecendo!"),
        (106.3, 125.5, "Desfecho completo fechando toda a narrativa."),
    ]
    words = _words_from_specs(specs)
    duration = 135.0
    rms = np.full(int(duration * FPS_RMS), -34.0, dtype=np.float32)
    rms[int(95.0 * FPS_RMS) : int(98.0 * FPS_RMS)] = -12.0

    cand = CandidatePeak(
        peak=96.0, raw_score=0.92, start_win=0.0, end_win=130.0,
        topic_start=10.0, topic_end=125.5, active_signals=["loudness"],
    )
    parts = snap_topic_parts(cand, words, rms, duration, min_dur=25.0, single_max_dur=65.0, part_max_dur=58.0)
    assert len(parts) == 2
    assert [(p.part_index, p.part_total) for p in parts] == [(1, 2), (2, 2)]
    assert 9.75 < parts[0].start < 10.0
    for p in parts:
        assert 25.0 <= p.duration <= 58.0
        assert not cuts_mid_word(p.start, words)
        assert not cuts_mid_word(p.end, words)


def test_select_clips_peak_candidate_emits_only_part_containing_peak():
    """Candidato de pico (topic_start=None) em topico de 3 partes emite so a parte do pico; quota mantem as 3."""
    specs = [
        (10.0 + i * 9.0, 10.0 + i * 9.0 + 8.5, f"Capítulo {i} da mesma história longa sobre o torneio.")
        for i in range(22)
    ]
    words = _words_from_specs(specs)
    duration = 220.0
    rms = np.full(int(duration * FPS_RMS), -34.0, dtype=np.float32)
    peak_t = 115.0
    rms[int((peak_t - 1.0) * FPS_RMS) : int((peak_t + 2.0) * FPS_RMS)] = -12.0

    # 1) Candidato de pico (topic_start=None): snap_topic_parts gera 3 partes, mas select_clips mantem so a do pico
    peak_cand = CandidatePeak(
        peak=peak_t, raw_score=0.95, start_win=5.0, end_win=210.0, active_signals=["loudness"],
    )
    raw_parts = snap_topic_parts(peak_cand, words, rms, duration, min_dur=25.0, single_max_dur=65.0, part_max_dur=58.0)
    assert len(raw_parts) == 3

    chosen_peak = select_clips([peak_cand], words, rms, duration, max_clips=5, min_dur=25.0, max_dur=65.0)
    assert len(chosen_peak) == 1
    assert chosen_peak[0].start <= peak_t <= chosen_peak[0].end
    assert chosen_peak[0].part_index == 1
    assert chosen_peak[0].part_total == 1

    # 2) Mesmo topico com candidato de quota (topic_start preenchido): mantem a serie completa (3 partes)
    quota_cand = CandidatePeak(
        peak=peak_t, raw_score=0.95, start_win=5.0, end_win=210.0,
        topic_start=raw_parts[0].start, topic_end=raw_parts[-1].end, active_signals=["loudness"],
    )
    chosen_quota = select_clips([quota_cand], words, rms, duration, max_clips=5, min_dur=25.0, max_dur=65.0)
    assert len(chosen_quota) == 3
    assert [c.part_index for c in chosen_quota] == [1, 2, 3]
    assert all(c.part_total == 3 for c in chosen_quota)
