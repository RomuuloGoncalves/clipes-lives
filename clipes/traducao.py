"""Agrupamento de palavras em blocos de legenda e tradução automática para PT-BR."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from . import cloud_llm, llm
from .models import SnappedClip, Word

logger = logging.getLogger(__name__)

MAX_BLOCKS_PER_BATCH = 12

_OFF_VALUES = frozenset({"off", "0", "false", "no", "original"})
_PT_VALUES = frozenset({"", "pt", "pt-br", "pt_br"})

TRADUCAO_SYSTEM_PROMPT = (
    "Você é um tradutor de legendas de lives para português do Brasil (pt-BR). "
    "Traduza cada bloco mantendo tom natural, gírias equivalentes, nomes próprios e números exatos. "
    "Responda apenas JSON válido conforme o schema, mantendo exatamente os mesmos ids."
)

TRADUCAO_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "blocos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "pt": {"type": "string"},
                },
                "required": ["id", "pt"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["blocos"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    original: str
    translated: str = ""


def _ass_escape(text: str) -> str:
    return " ".join(text.replace("\\", "").replace("{", "(").replace("}", ")").split())


def resolve_target_lang(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve target subtitle language from CLIPES_LEGENDA_IDIOMA (default 'pt'; None when off)."""
    source = os.environ if env is None else env
    raw = str(source.get("CLIPES_LEGENDA_IDIOMA", "pt") or "").strip().lower()
    if raw in _OFF_VALUES:
        return None
    if raw in _PT_VALUES:
        return "pt"
    return raw


def should_translate(detected_lang: str | None, target_lang: str | None = "pt") -> bool:
    """Return True when target_lang is 'pt' and detected_lang is non-empty and not Portuguese."""
    if target_lang != "pt":
        return False
    if not detected_lang or not isinstance(detected_lang, str):
        return False
    norm = detected_lang.strip().lower()
    if not norm:
        return False
    return not norm.startswith("pt")


def agrupar_blocos(
    words: list[Word],
    clip_start: float,
    clip_end: float,
    *,
    max_gap: float = 0.7,
    max_words: int = 10,
    max_dur: float = 3.5,
) -> list[SubtitleCue]:
    """Group clip words into timed sentence/pause blocks preserving exact start/end timestamps."""
    clip_words = [w for w in words if w.end > clip_start and w.start < clip_end and w.clean]
    if not clip_words:
        return []

    cues: list[SubtitleCue] = []
    buf: list[Word] = []

    def _flush() -> None:
        if not buf:
            return
        cues.append(
            SubtitleCue(
                start=buf[0].start,
                end=buf[-1].end,
                original=" ".join(w.clean for w in buf),
            )
        )
        buf.clear()

    for word in clip_words:
        if buf:
            gap = word.start - buf[-1].end
            cur_dur = buf[-1].end - buf[0].start
            next_dur = word.end - buf[0].start
            if (
                buf[-1].ends_sentence
                or gap >= max_gap
                or len(buf) >= max_words
                or cur_dur >= max_dur
                or next_dur > max_dur
            ):
                _flush()
        buf.append(word)

    _flush()
    return cues


def _build_translation_prompt(batch: list[SubtitleCue], source_lang: str) -> str:
    items = [{"id": idx, "texto": cue.original} for idx, cue in enumerate(batch)]
    payload = json.dumps(items, ensure_ascii=False)
    return (
        f"Traduza os blocos abaixo do idioma '{source_lang}' para português do Brasil (pt-BR).\n"
        "Mantenha exatamente o mesmo 'id' de cada bloco e não omita nenhum item.\n"
        f"<blocos>{payload}</blocos>"
    )


def _validate_batch_response(res: Any, batch_size: int) -> dict[int, str] | None:
    if not isinstance(res, dict):
        return None
    blocos = res.get("blocos")
    if not isinstance(blocos, list):
        return None

    mapped: dict[int, str] = {}
    for item in blocos:
        if not isinstance(item, dict):
            return None
        idx = item.get("id")
        pt = item.get("pt")
        if isinstance(idx, bool) or not isinstance(idx, int):
            return None
        if idx < 0 or idx >= batch_size:
            return None
        if not isinstance(pt, str):
            return None
        clean_pt = _ass_escape(pt)
        if not clean_pt:
            return None
        mapped[idx] = clean_pt

    if len(mapped) != batch_size:
        return None
    return mapped


def traduzir_blocos(
    cues: list[SubtitleCue],
    source_lang: str,
    *,
    server_url: str | None = None,
    timeout: float | None = None,
) -> tuple[list[SubtitleCue] | None, str]:
    """Translate SubtitleCues in batches of up to 12 via Gemini cloud -> local Qwen fallback."""
    if not cues:
        return [], "original"

    translated_all: list[SubtitleCue] = []
    sources: list[str] = []

    for start_idx in range(0, len(cues), MAX_BLOCKS_PER_BATCH):
        batch = cues[start_idx : start_idx + MAX_BLOCKS_PER_BATCH]
        prompt = _build_translation_prompt(batch, source_lang)

        res: dict[str, Any] | None = None
        source = "llm"
        cloud = cloud_llm.complete_cloud(prompt, TRADUCAO_JSON_SCHEMA, TRADUCAO_SYSTEM_PROMPT)
        if cloud is not None:
            res, source = cloud
        else:
            try:
                res = llm.complete(
                    prompt,
                    schema=TRADUCAO_JSON_SCHEMA,
                    server_url=server_url,
                    timeout=timeout,
                    system_prompt=TRADUCAO_SYSTEM_PROMPT,
                    max_tokens=1200,
                )
            except Exception as exc:
                logger.warning("[traducao] falha no LLM (%s); mantendo legenda original", exc)
                return None, "fallback_original"

        mapped = _validate_batch_response(res, len(batch))
        if mapped is None:
            logger.warning("[traducao] resposta LLM inválida ou incompleta; mantendo legenda original")
            return None, "fallback_original"

        sources.append(source)
        for idx, cue in enumerate(batch):
            translated_all.append(
                SubtitleCue(
                    start=cue.start,
                    end=cue.end,
                    original=cue.original,
                    translated=mapped[idx],
                )
            )

    final_source = "llm" if "llm" in sources else (sources[0] if sources else "original")
    return translated_all, final_source


def traduzir_clipes(
    clips: list[SnappedClip],
    words: list[Word],
    detected_lang: str | None,
    *,
    enabled: bool = True,
    timeout: float | None = None,
) -> None:
    """Populate translated_cues, translated_text, and manifest metadata on each clip in place."""
    if not clips:
        return

    target_lang = resolve_target_lang()
    lang_clean = (detected_lang or "").strip()

    if not enabled or not should_translate(detected_lang, target_lang):
        for clip in clips:
            clip.translated_cues = None
            clip.translated_text = ""
            clip.legenda_traduzida = False
            clip.legenda_idioma = lang_clean or "pt"
            clip.legenda_fonte = "original"
        return

    for clip in clips:
        cues = agrupar_blocos(words, clip.start, clip.end)
        if not cues:
            clip.translated_cues = None
            clip.translated_text = ""
            clip.legenda_traduzida = False
            clip.legenda_idioma = lang_clean or "original"
            clip.legenda_fonte = "original"
            continue

        translated, fonte = traduzir_blocos(cues, lang_clean, timeout=timeout)
        if translated is None:
            clip.translated_cues = None
            clip.translated_text = ""
            clip.legenda_traduzida = False
            clip.legenda_idioma = lang_clean or "original"
            clip.legenda_fonte = "fallback_original"
        else:
            clip.translated_cues = translated
            clip.translated_text = " ".join(c.translated for c in translated).strip()
            clip.legenda_traduzida = True
            clip.legenda_idioma = target_lang or "pt"
            clip.legenda_fonte = fonte
