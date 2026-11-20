import subprocess

import pytest

from clipes import gate
from clipes.models import Word

CFG = {"hook_max_atraso": 1.0, "dur_min": 15, "dur_max": 45, "hashtags_max": 3,
       "hashtags_banidas": ["fyp"]}
OK_DIM = lambda _p: (1080, 1920)  # noqa: E731
W = [Word(10.5, 11.0, "oi")]


def clip(**kw):
    base = {"hook_top": "Ele não acreditou", "start": 10.0, "end": 30.0, "layout": "split",
            "caption": "Que jogada absurda\nvia canal", "hashtags": ["#valorant", "#clutch"]}
    return {**base, **kw}


def av(c, words=W, dim=OK_DIM):
    return gate.avaliar(c, words, None if dim is None else __import__("pathlib").Path("x.mp4"),
                        CFG, dimensoes_fn=dim)


def test_ok():
    assert av(clip()) == {"ok": True, "motivos": [], "versao": 1}


@pytest.mark.parametrize("c,motivo", [
    (clip(hook_top=""), "hook_ausente"),
    (clip(hook_top="Ele não acre…"), "hook_truncado"),
    (clip(hook_top="Ele não acre..."), "hook_truncado"),
    (clip(hook_cabe=False), "hook_truncado"),
    (clip(hook_top="a�b"), "hook_invalido"),
    (clip(hook_top="a臂 jogada"), "hook_invalido"),
    (clip(end=20.0), "duracao_fora"),
    (clip(end=60.0), "duracao_fora"),
    (clip(layout="black_bars_16x9"), "barras_pretas"),
    (clip(caption="veja https://x.com/a\nb"), "caption_url"),
    (clip(hashtags=[]), "caption_hashtags"),
    (clip(hashtags=["#a", "#b", "#c", "#d"]), "caption_hashtags"),
    (clip(hashtags=["#valorant", "#FYP"]), "caption_hashtags"),
    (clip(caption="ELE NÃO ACREDITOU!\nx"), "caption_repete_hook"),
])
def test_motivo(c, motivo):
    assert motivo in av(c)["motivos"]
    assert av(c)["ok"] is False


def test_fala_tardia():
    assert "fala_tardia" in av(clip(), [Word(12.0, 12.5, "oi")])["motivos"]


def test_resolucao():
    assert "resolucao" in av(clip(), dim=lambda _p: (1920, 1080))["motivos"]


def test_texto_hash_estavel():
    a = gate.texto_hash([Word(0, 1, "Ação"), Word(1, 2, "RÁPIDA,")])
    assert a == gate.texto_hash([Word(0, 1, "acao"), Word(1, 2, "rapida")])
    assert len(a) == 12


def _lavfi(tmp_path, nome, src):
    p = tmp_path / nome
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", src, "-t", "1", str(p)], check=True)
    return p


def test_dhash(tmp_path):
    a = _lavfi(tmp_path, "a.mp4", "testsrc=size=320x240:rate=10")
    b = _lavfi(tmp_path, "b.mp4", "testsrc=size=320x240:rate=10")
    c = _lavfi(tmp_path, "c.mp4", "color=black:size=320x240:rate=10")
    assert gate.distancia(gate.dhash(a, 0.5), gate.dhash(b, 0.5)) == 0
    assert gate.distancia(gate.dhash(a, 0.5), gate.dhash(c, 0.5)) > 6
