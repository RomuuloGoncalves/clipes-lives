"""Unit tests for semantic topic segmentation and multi-part splitting."""

from __future__ import annotations

import logging
import sys
import types

import numpy as np

import clipes.capacidade as capacidade_mod
from clipes.capacidade import cpu_threads

from clipes.eval import cuts_mid_word, evaluate_clips
from clipes.models import CandidatePeak, TopicSegment, Word
from clipes.signals import FPS_RMS
from clipes.snap import snap_topic_parts
from clipes.topics import (
    embed_sentences,
    segment_topics,
    split_topic_into_parts,
    words_to_sentences,
)


def _make_words_from_sentences(
    specs: list[tuple[float, float, str]],
) -> list[Word]:
    """Turn (start, end, sentence_text) specs into Word tokens evenly spaced inside [start, end]."""
    words: list[Word] = []
    for s, e, text in specs:
        toks = text.split()
        step = (e - s) / max(1, len(toks))
        for i, tok in enumerate(toks):
            w_s = round(s + i * step, 3)
            w_e = round(s + (i + 1) * step - 0.04, 3)
            if w_e <= w_s:
                w_e = round(w_s + 0.05, 3)
            words.append(Word(start=w_s, end=w_e, text=(" " + tok) if i > 0 else tok))
    return words


def test_words_to_sentences_punctuation_and_pause():
    words = [
        Word(0.0, 0.4, "Olá"),
        Word(0.45, 0.9, "pessoal."),
        Word(1.1, 1.5, "Tudo"),
        Word(1.55, 2.0, "bem"),
        # 1.0s pause -> splits sentence even without punctuation
        Word(3.0, 3.4, "Vamos"),
        Word(3.45, 3.9, "começar!"),
    ]
    sents = words_to_sentences(words, pause_split=0.8)
    assert len(sents) == 3
    assert sents[0] == (0.0, 0.9, "Olá pessoal.")
    assert sents[1] == (1.1, 2.0, "Tudo bem")
    assert sents[2] == (3.0, 3.9, "Vamos começar!")


def test_embed_sentences_real_minilm_and_similarity():
    texts = [
        "O goleiro defendeu o pênalti no último minuto do jogo de futebol.",
        "O atacante chutou a bola na trave e o estádio inteiro gritou gol.",
        "Para fazer o bolo de chocolate, misture farinha, ovos e açúcar na tigela.",
    ]
    embs = embed_sentences(texts)
    assert embs is not None
    assert embs.shape == (3, 384)
    norms = np.linalg.norm(embs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4)
    sim_same = float(np.dot(embs[0], embs[1]))
    sim_diff = float(np.dot(embs[0], embs[2]))
    assert sim_same > sim_diff + 0.1


def test_segment_topics_real_onnx_finds_story_boundaries():
    # Topic 1 (0..30s): cooking recipe
    # Topic 2 (32..87s): intense boss fight in Elden Ring, punchline at 80s (~87% into topic 2)
    # Topic 3 (89..120s): hardware and graphics card prices
    specs = [
        (0.0, 7.0, "Hoje eu comprei farinha e chocolate para assar um bolo na cozinha."),
        (7.3, 14.5, "A receita pede três ovos, leite morno e manteiga derretida na massa."),
        (14.8, 22.0, "Depois de misturar tudo na tigela, coloquei a forma no forno quente."),
        (22.3, 29.5, "O cheiro de bolo assado tomou conta da casa inteira à tarde."),
        # Topic 2 starts at 32.0s
        (32.0, 41.0, "Agora preste atenção porque eu entrei na arena do chefe final sem poção."),
        (41.3, 51.0, "O chefe levantou a espada gigante e eu rolei para desviar do golpe."),
        (51.3, 61.0, "Minha barra de vida estava com um pixel e a espada quase me acertou."),
        (61.3, 72.0, "Eu esperei o último segundo do ataque dele para dar o golpe crítico."),
        (72.3, 86.0, "Derrotei o chefe sem tomar dano nenhum, meu Deus do céu que absurdo!"),
        # Topic 3 starts at 89.0s
        (89.0, 97.0, "Mudando de assunto, o preço da placa de vídeo subiu muito neste mês."),
        (97.3, 105.0, "Muita gente está comparando memória RAM e SSD antes de montar o computador."),
        (105.3, 113.0, "A fonte de alimentação precisa ter potência suficiente para o processador."),
        (113.3, 120.0, "Também vale olhar a temperatura do gabinete durante o verão."),
    ]
    words = _make_words_from_sentences(specs)
    topic = segment_topics(words, peak=80.0)
    assert topic is not None
    # Should stay focused on Topic 2 (~32s to ~86s), not swallow Topic 1 or Topic 3
    assert 20.0 <= topic.start <= 42.0
    assert 80.0 <= topic.end <= 98.0
    assert 25.0 <= topic.duration <= 75.0


def test_segment_topics_fallback_without_onnx(caplog):
    specs = [
        (0.0, 12.0, "Assunto antigo antes da pausa."),
        # 1.5s pause
        (13.5, 25.0, "Começo da história principal."),
        (25.3, 40.0, "Desenvolvimento da história aqui."),
        (40.3, 55.0, "Clímax e risada da história!"),
        # 1.5s pause
        (56.5, 70.0, "Outro assunto depois da pausa."),
    ]
    words = _make_words_from_sentences(specs)
    with caplog.at_level(logging.WARNING):
        topic = segment_topics(words, peak=48.0, onnx_path="/nonexistent/model.onnx")
    assert topic is not None
    assert "MiniLM ONNX indisponível" in caplog.text
    assert topic.start == 13.5
    assert 54.9 <= topic.end <= 55.0


def test_segment_topics_few_words_and_180s_trim():
    assert segment_topics([Word(1.0, 1.5, "Oi"), Word(2.0, 2.5, "Ei")], peak=2.0) is None

    # 220s single homogeneous transcript -> must be trimmed to <= 180s around peak
    specs = [(i * 10.0, i * 10.0 + 9.2, f"Frase número {i} da mesma conversa longa.") for i in range(22)]
    words = _make_words_from_sentences(specs)
    topic = segment_topics(words, peak=110.0, max_topic_dur=180.0, onnx_path="/nonexistent/model.onnx")
    assert topic is not None
    assert topic.duration <= 180.0
    assert topic.start <= 110.0 <= topic.end


ANTIGO_58 = dict(min_dur=25.0, single_max_dur=65.0, part_max_dur=58.0)


def test_split_topic_into_parts_single_two_three_and_invalid():
    # Single clip <= 65s
    specs_short = [(i * 10.0, i * 10.0 + 9.5, f"Frase curta {i}.") for i in range(5)]
    words_short = _make_words_from_sentences(specs_short)
    t_short = TopicSegment(start=0.0, end=49.5, peak=35.0, sentences=words_to_sentences(words_short))
    parts_short = split_topic_into_parts(t_short, words_short, **ANTIGO_58)
    assert parts_short == [(0.0, 49.5, 1, 1)]

    # 95s -> 2 parts <= 58s
    specs_2 = [(i * 9.5, i * 9.5 + 9.1, f"Parte da história {i}.") for i in range(10)]
    words_2 = _make_words_from_sentences(specs_2)
    t_2 = TopicSegment(start=0.0, end=94.6, peak=70.0, sentences=words_to_sentences(words_2))
    parts_2 = split_topic_into_parts(t_2, words_2, **ANTIGO_58)
    assert len(parts_2) == 2
    for a, b, idx, total in parts_2:
        assert total == 2
        assert 25.0 <= (b - a) <= 58.0
        assert not cuts_mid_word(a, words_2)
        assert not cuts_mid_word(b, words_2)

    # 135s -> 3 parts <= 58s
    specs_3 = [(i * 9.0, i * 9.0 + 8.6, f"Trecho longo {i}.") for i in range(15)]
    words_3 = _make_words_from_sentences(specs_3)
    t_3 = TopicSegment(start=0.0, end=134.6, peak=100.0, sentences=words_to_sentences(words_3))
    parts_3 = split_topic_into_parts(t_3, words_3, **ANTIGO_58)
    assert [(p[2], p[3]) for p in parts_3] == [(1, 3), (2, 3), (3, 3)]
    for a, b, _, _ in parts_3:
        assert 25.0 <= (b - a) <= 58.0
        assert not cuts_mid_word(a, words_3)
        assert not cuts_mid_word(b, words_3)

    # Unbreakable 100s monologue with no punctuation and <0.5s gaps -> []
    words_mono = [Word(i * 1.0, i * 1.0 + 0.85, f"palavra{i}") for i in range(100)]
    t_mono = TopicSegment(start=0.0, end=99.85, peak=50.0, sentences=[(0.0, 99.85, "monologo")])
    assert split_topic_into_parts(t_mono, words_mono, **ANTIGO_58) == []


def test_split_topic_into_parts_respeita_max_dur_45(monkeypatch):
    monkeypatch.delenv("CLIPES_DUR_MIN", raising=False)
    monkeypatch.delenv("CLIPES_DUR_MAX", raising=False)

    # 94.6s com max 45 -> 3 partes, todas <= 45
    specs_2 = [(i * 9.5, i * 9.5 + 9.1, f"Parte da história {i}.") for i in range(10)]
    words_2 = _make_words_from_sentences(specs_2)
    t_2 = TopicSegment(start=0.0, end=94.6, peak=70.0, sentences=words_to_sentences(words_2))
    parts_2 = split_topic_into_parts(t_2, words_2, part_max_dur=45.0)
    assert len(parts_2) == 3
    for a, b, _, _ in parts_2:
        assert b - a <= 45.0 + 1e-6

    # 66.1s sem max explícito -> padrão CLIPES_DUR_MAX=45 -> 2 partes <= 45
    specs_66 = [(i * 9.5, i * 9.5 + 9.1, f"Trecho {i}.") for i in range(7)]
    words_66 = _make_words_from_sentences(specs_66)
    t_66 = TopicSegment(start=0.0, end=66.1, peak=30.0, sentences=words_to_sentences(words_66))
    parts_66 = split_topic_into_parts(t_66, words_66)
    assert len(parts_66) == 2
    for a, b, _, _ in parts_66:
        assert b - a <= 45.0 + 1e-6

    # Mesmo tópico com CLIPES_DUR_MAX=30 vindo da config -> 3 partes <= 30
    monkeypatch.setenv("CLIPES_DUR_MAX", "30")
    parts_30 = split_topic_into_parts(t_66, words_66)
    assert len(parts_30) == 3
    for a, b, _, _ in parts_30:
        assert b - a <= 30.0 + 1e-6


def test_long_transcript_real_segment_topics_split_and_snap_integration():
    """End-to-end integration: real MiniLM segment_topics + split + snap_topic_parts with 0 mid-word cuts."""
    specs = [
        # Topic 1 (0..38s): cooking recipe
        (0.0, 9.0, "Hoje eu comprei farinha, açúcar e chocolate para fazer um bolo."),
        (9.3, 18.5, "Misturei os ovos com leite morno e manteiga na tigela grande."),
        (18.8, 28.0, "Coloquei a forma untada no forno bem quente durante a tarde."),
        (28.3, 38.0, "O cheiro doce do bolo assado tomou conta da cozinha inteira."),
        # Topic 2 (40..152s, ~112s): long boss fight story
        (40.0, 49.0, "Agora escuta essa porque eu entrei na masmorra do chefe final sem poção."),
        (49.3, 58.5, "Logo na entrada o chefe gigante rugiu e bateu o martelo no chão."),
        (58.8, 68.0, "Eu rolei para a esquerda no último milissegundo para escapar do golpe."),
        (68.3, 77.5, "Minha espada estava quebrando e faltava energia para defender o escudo."),
        (77.8, 87.0, "Na segunda fase ele começou a soltar raios pela arena inteira."),
        (87.3, 96.5, "Fiquei preso no canto com apenas um pixel sobrando na barra de vida."),
        (96.8, 106.0, "Esperei o chefe levantar a armadura para tentar um ataque desesperado."),
        (106.3, 115.5, "Acertei dois golpes críticos seguidos bem no ponto fraco das costas."),
        (115.8, 125.0, "Ele caiu de joelhos atordoado enquanto a torcida gritava no chat."),
        (125.3, 134.5, "Dei o golpe final antes dele recuperar o fôlego na última barra."),
        (134.8, 144.0, "Derrotei o chefe final sem tomar dano nenhum na fase dois!"),
        (144.3, 152.0, "Foi a vitória mais absurda que eu já consegui nessa campanha inteira!"),
        # Topic 3 (154..194s): PC hardware prices
        (154.0, 163.5, "Mudando de assunto, o preço da placa de vídeo subiu muito neste mês."),
        (163.8, 173.5, "Muita gente compara memória RAM e SSD antes de montar computador novo."),
        (173.8, 183.5, "A fonte precisa de potência real para aguentar o processador moderno."),
        (183.8, 194.0, "Também vale revisar a ventilação do gabinete durante o verão quente."),
    ]
    words = _make_words_from_sentences(specs)
    duration = 200.0
    rms = np.full(int(duration * FPS_RMS), -34.0, dtype=np.float32)
    rms[int(139.0 * FPS_RMS) : int(143.0 * FPS_RMS)] = -12.0

    topic = segment_topics(words, peak=140.0)
    assert topic is not None
    assert 65.0 < topic.duration <= 180.0
    raw_parts = split_topic_into_parts(topic, words, **ANTIGO_58)
    assert len(raw_parts) in (2, 3)

    cand = CandidatePeak(
        peak=140.0, raw_score=0.95, start_win=0.0, end_win=duration, active_signals=["loudness"]
    )
    parts = snap_topic_parts(cand, words, rms, duration, min_dur=25.0, single_max_dur=65.0, part_max_dur=58.0)
    assert len(parts) in (2, 3)
    assert [(p.part_index, p.part_total) for p in parts] == [(i, len(parts)) for i in range(1, len(parts) + 1)]
    for p in parts:
        assert 25.0 <= p.duration <= 58.0

    metrics = evaluate_clips(parts, words, min_dur=25.0, max_dur=65.0)
    assert metrics["mid_word_cut_rate"] == 0.0
    assert metrics["duration_ok_rate"] == 1.0


def test_embed_sentences_uses_cpu_threads_in_onnx_session(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPES_CPU_THREADS", raising=False)
    model_file = tmp_path / "model.onnx"
    model_file.write_bytes(b"fake-onnx")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")

    observed_threads: list[int] = []

    class FakeSessionOptions:
        intra_op_num_threads = None
        inter_op_num_threads = None

    class FakeInput:
        def __init__(self, name: str):
            self.name = name

    class FakeInferenceSession:
        def __init__(self, path, sess_options=None, providers=None):
            observed_threads.append(sess_options.intra_op_num_threads)

        def get_inputs(self):
            return [FakeInput("input_ids"), FakeInput("attention_mask")]

        def run(self, output_names, feeds):
            batch = feeds["input_ids"].shape[0]
            seq = feeds["input_ids"].shape[1]
            return [np.ones((batch, seq, 4), dtype=np.float32)]

    class FakeEncoding:
        ids = [1, 2]
        attention_mask = [1, 1]
        type_ids = [0, 0]

    class FakeTokenizer:
        padding = True
        truncation = True

        @classmethod
        def from_file(cls, path):
            return cls()

        def encode_batch(self, texts):
            return [FakeEncoding() for _ in texts]

    fake_ort = types.ModuleType("onnxruntime")
    fake_ort.SessionOptions = FakeSessionOptions
    fake_ort.InferenceSession = FakeInferenceSession
    fake_tok = types.ModuleType("tokenizers")
    fake_tok.Tokenizer = FakeTokenizer
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    monkeypatch.setitem(sys.modules, "tokenizers", fake_tok)

    expected_real = cpu_threads()
    embs = embed_sentences(["primeira frase"], onnx_path=str(model_file))
    assert embs is not None
    assert observed_threads == [expected_real]

    observed_threads.clear()
    monkeypatch.setattr(capacidade_mod, "cpu_threads", lambda: 5)
    embs2 = embed_sentences(["segunda frase"], onnx_path=str(model_file))
    assert embs2 is not None
    assert observed_threads == [5]
