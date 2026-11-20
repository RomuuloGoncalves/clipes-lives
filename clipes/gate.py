"""Gate de eliminatorios (anti-flop v2): avaliacao pura + dHash/texto_hash para dedup."""
from __future__ import annotations

import hashlib
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Callable

VERSAO = 1
_URL = re.compile(r"https?://|www\.|\b\w+\.(?:com|tv|gg|be|ly)/", re.I)
_HASHTAG = re.compile(r"#\w+")
_ASIATICO = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def _norm(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", "", s)


def _tokens(texto: str) -> list[str]:
    return _norm(texto).split()


def texto_hash(words: list[Any]) -> str:
    """sha1[:12] das 20 primeiras palavras normalizadas (aceita Word, dict ou str)."""
    toks: list[str] = []
    for w in words:
        t = w if isinstance(w, str) else (w["text"] if isinstance(w, dict) else w.text)
        toks += _tokens(t)
    return hashlib.sha1(" ".join(toks[:20]).encode()).hexdigest()[:12]


def dimensoes(mp4: Path, run: Callable = subprocess.run) -> tuple[int, int]:
    r = run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0:s=x", str(mp4)],
        capture_output=True, text=True, check=True,
    )
    w, h = r.stdout.strip().split("x")
    return int(w), int(h)


def dhash(mp4: Path, t: float = 0.0, run: Callable = subprocess.run) -> int:
    """dHash de 64 bits: quadro em t, 9x8 cinza, pixel > vizinho a direita."""
    r = run(
        ["ffmpeg", "-v", "error", "-ss", str(max(t, 0.0)), "-i", str(mp4), "-frames:v", "1",
         "-vf", "scale=9:8,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    )
    px = r.stdout
    if len(px) < 72:
        raise ValueError("dhash: quadro ausente")
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (px[y * 9 + x] > px[y * 9 + x + 1])
    return bits


def distancia(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _t0(w: Any) -> float:
    return w["start"] if isinstance(w, dict) else w.start


def avaliar(clip: dict, words: list[Any], mp4: Path | None, cfg: dict, *,
            dimensoes_fn: Callable = dimensoes) -> dict:
    """clip: hook_top, hook_cabe, start, end, layout, caption, hashtags, idioma.
    cfg: hook_max_atraso, dur_min, dur_max, hashtags_max, hashtags_banidas."""
    m: list[str] = []
    hook = (clip.get("hook_top") or "").strip()
    start, end = clip.get("start", 0.0), clip.get("end", 0.0)
    if not hook:
        m.append("hook_ausente")
    if hook.endswith(("…", "...")) or clip.get("hook_cabe") is False:
        m.append("hook_truncado")
    if hook and (
        "\ufffd" in hook
        or (_ASIATICO.search(hook) and clip.get("idioma", "pt") not in ("ja", "zh", "ko"))
    ):
        m.append("hook_invalido")
    if words and _t0(words[0]) > start + cfg["hook_max_atraso"]:
        m.append("fala_tardia")
    if not cfg["dur_min"] <= end - start <= cfg["dur_max"]:
        m.append("duracao_fora")
    if clip.get("layout") == "black_bars_16x9":
        m.append("barras_pretas")
    if mp4 is not None and dimensoes_fn(mp4) != (1080, 1920):
        m.append("resolucao")
    cap = clip.get("caption") or ""
    if _URL.search(cap):
        m.append("caption_url")
    tags = clip.get("hashtags") or _HASHTAG.findall(cap)
    banidas = {b.lstrip("#").casefold() for b in cfg.get("hashtags_banidas", ())}
    if not tags or len(tags) > cfg["hashtags_max"] or any(
        t.lstrip("#").casefold() in banidas for t in tags
    ):
        m.append("caption_hashtags")
    linha1 = cap.strip().splitlines()[0] if cap.strip() else ""
    if hook and _norm(linha1) == _norm(hook):
        m.append("caption_repete_hook")
    return {"ok": not m, "motivos": m, "versao": VERSAO}
