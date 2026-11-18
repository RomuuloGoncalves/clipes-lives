"""Cliente Gemini (free tier) em cascata modelo x chave. Só stdlib; sem API paga."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from typing import Any

from clipes.llm import _THINK_RE

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_DEFAULT_MODELS = "gemini-2.5-flash,gemini-2.5-flash-lite"
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_failed: set[tuple[str, int]] = set()
_used: list[str] = []


def _keys() -> list[str]:
    return [k.strip() for k in os.environ.get("GEMINI_API_KEYS", "").split(",") if k.strip()]


def _models() -> list[str]:
    raw = os.environ.get("CLIPES_GEMINI_MODELS", "") or _DEFAULT_MODELS
    return [m.strip() for m in raw.split(",") if m.strip()]


def cloud_enabled() -> bool:
    return os.environ.get("CLIPES_LLM_BACKEND", "auto").strip().lower() != "local" and bool(_keys())


def mask(text: str) -> str:
    for k in _keys():
        text = text.replace(k, "***")
    return text


def reset_round() -> None:
    _failed.clear()
    _used.clear()


def providers_used() -> list[str]:
    return list(_used)


def _timeout() -> float:
    try:
        v = float(os.environ.get("CLIPES_LLM_TIMEOUT", ""))
        return v if v > 0 else 60.0
    except ValueError:
        return 60.0


def _parse(text: str, schema: dict) -> dict | None:
    text = _FENCE_RE.sub("", _THINK_RE.sub("", text).strip()).strip()
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict) or any(r not in obj for r in schema.get("required", [])):
        return None
    return obj


def _call(model: str, key: str, prompt: str, schema: dict, system_prompt: str, urlopen: Any) -> dict | None:
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }
    req = urllib.request.Request(
        _URL.format(model=model),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    with urlopen(req, timeout=_timeout()) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _parse(data["candidates"][0]["content"]["parts"][0]["text"], schema)


def complete_cloud(
    prompt: str,
    schema: dict,
    system_prompt: str,
    *,
    urlopen=urllib.request.urlopen,
    now=time.time,
) -> tuple[dict, str] | None:
    """Tenta cada (modelo, chave) na ordem; devolve (resposta, 'cloud:gemini:<modelo>') ou None."""
    if not cloud_enabled():
        return None
    keys = _keys()
    for model in _models():
        for idx, key in enumerate(keys):
            pair = (model, idx)
            if pair in _failed:
                continue
            try:
                obj = _call(model, key, prompt, schema, system_prompt, urlopen)
            except Exception as exc:  # 429/5xx/404/timeout/rede: segue para o próximo par
                print(f"[llm] gemini {model}#{idx} falhou: {mask(str(exc))}")
                obj = None
            if obj is None:
                _failed.add(pair)
                continue
            if "gemini" not in _used:
                _used.append("gemini")
            return obj, f"cloud:gemini:{model}"
    return None
