import json

from clipes.janelas import filter_emitted, owns, window_end

STEP, PAD = 300, 30.0


def test_window_end_alinha_ao_relogio():
    assert window_end(1763416799.9, 300) == 1763416500
    assert window_end(1763416800, 300) == 1763416800


def test_picos_nas_bordas_aceitos_exatamente_uma_vez():
    ends = [600 + STEP * i for i in range(6)]
    for c in [e - STEP - PAD + k for e in ends for k in (0, 1)] + [e - PAD for e in ends]:
        n = sum(owns(c, e - 600, e, STEP, PAD, last=False) for e in ends)
        assert n == (1 if ends[0] - STEP - PAD <= c < ends[-1] - PAD else 0), c


def test_ultima_janela_dona_ate_o_fim():
    e = 1200
    assert owns(e - STEP - PAD, e - 600, e, STEP, PAD, last=True)
    assert owns(e, e - 600, e, STEP, PAD, last=True)
    assert owns(e - PAD + 1, e - 600, e, STEP, PAD, last=True)
    assert not owns(e - PAD + 1, e - 600, e, STEP, PAD, last=False)
    assert not owns(e - STEP - PAD - 1, e - 600, e, STEP, PAD, last=True)


def test_filter_emitted_descarta_sobreposicao_50(tmp_path):
    p = tmp_path / "emitted.json"
    assert filter_emitted([(100, 140)], p) == [True]
    # 20 s de 40 s = 50% -> descartado; 19 s -> aceito; disjunto -> aceito
    assert filter_emitted([(120, 160), (121, 161), (500, 540)], p) == [False, True, True]
    assert json.loads(p.read_text()) == [[100, 140], [121, 161], [500, 540]]


def test_emitted_corrompido_vira_vazio_e_reescrito(tmp_path, capsys):
    p = tmp_path / "emitted.json"
    p.write_text("{nao é json")
    assert filter_emitted([], p) == []
    assert json.loads(p.read_text()) == []
    assert "corrompido" in capsys.readouterr().out
    p.write_text("{nao é json")
    assert filter_emitted([(1, 41)], p) == [True]
    assert json.loads(p.read_text()) == [[1, 41]]
