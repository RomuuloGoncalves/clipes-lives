"""Cliente Gemini (free tier) em cascata modelo x chave. Só stdlib; sem API paga."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from clipes import config
from clipes.llm import _THINK_RE

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_DEFAULT_MODELS = "gemini-2.5-flash,gemini-2.5-flash-lite"
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_tl = threading.local()  # _failed/_used/_logged por thread (um job por thread)
_lock = threading.Lock()  # protege _last_call e _save_cota
_last_call: dict[int, float] = {}
_out_dir: Path | None = None


def set_out_dir(out_dir: Path | str | None) -> None:
    global _out_dir
    _out_dir = Path(out_dir) if out_dir is not None else None


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


def _st() -> threading.local:
    if not hasattr(_tl, "failed"):
        _tl.failed, _tl.used, _tl.logged = set(), [], False
    return _tl


def reset_round() -> None:
    _tl.failed, _tl.used, _tl.logged = set(), [], False


def providers_used() -> list[str]:
    return list(_st().used)


def _timeout() -> float:
    try:
        v = float(os.environ.get("CLIPES_CLOUD_TIMEOUT", ""))
        return v if v > 0 else 20.0
    except ValueError:
        return 20.0


def _min_interval() -> float:
    try:
        return max(0.0, float(os.environ.get("CLIPES_GEMINI_MIN_INTERVAL_S", "4")))
    except ValueError:
        return 4.0


def _cota_path() -> Path:
    env = os.environ.get("CLIPES_GEMINI_COTA_FILE", "").strip()
    if env:
        return Path(env)
    base = config.resolve_out_dir(_out_dir)
    return base / ".gemini_cota.json"


def _dia(now: Any) -> str:
    # cota diária do free tier zera à meia-noite do Pacífico
    return datetime.fromtimestamp(now(), ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def _load_cota(path: Path, dia: str) -> set[str]:
    try:
        d = json.loads(path.read_text())
        if d["dia"] == dia:
            return {str(x) for x in d["esgotados"]}
        return set()  # dia novo: zera
    except FileNotFoundError:
        return set()
    except Exception:
        print("[llm] aviso: .gemini_cota.json corrompido, recriando")
        _save_cota(path, dia, set())
        return set()


def _save_cota(path: Path, dia: str, esgotados: set[str]) -> None:
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps({"dia": dia, "esgotados": sorted(esgotados)}))
            tmp.replace(path)
    except OSError as exc:
        print(f"[llm] aviso: não gravou cota: {mask(str(exc))}")


def _is_daily_quota(exc: Exception) -> bool:
    if not (isinstance(exc, urllib.error.HTTPError) and exc.code == 429):
        return False
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:
        return False
    return "RESOURCE_EXHAUSTED" in body and ("PerDay" in body or "per day" in body.lower())


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
    sleep=time.sleep,
) -> tuple[dict, str] | None:
    """Tenta cada (modelo, chave) na ordem; devolve (resposta, 'cloud:gemini:<modelo>') ou None."""
    if not cloud_enabled():
        return None
    st = _st()
    keys = _keys()
    pairs = [(m, i) for m in _models() for i in range(len(keys))]
    path, dia = _cota_path(), _dia(now)
    esgotados = _load_cota(path, dia)
    for model, idx in pairs:
        pair = (model, idx)
        if pair in st.failed or f"{model}#{idx}" in esgotados:
            continue
        with _lock:  # sleep dentro do lock: serializa o intervalo minimo por chave
            wait = _min_interval() - (now() - _last_call.get(idx, float("-inf")))
            if wait > 0:
                sleep(wait)
            _last_call[idx] = now()
        try:
            obj = _call(model, keys[idx], prompt, schema, system_prompt, urlopen)
        except Exception as exc:  # 429/5xx/404/timeout/rede: segue para o próximo par
            if _is_daily_quota(exc):
                esgotados.add(f"{model}#{idx}")
                _save_cota(path, dia, esgotados)
            print(f"[llm] gemini {model}#{idx} falhou: {mask(str(exc))}")
            obj = None
        if obj is None:
            st.failed.add(pair)
            continue
        if "gemini" not in st.used:
            st.used.append("gemini")
        return obj, f"cloud:gemini:{model}"
    if not st.logged and all(f"{m}#{i}" in esgotados for m, i in pairs):
        st.logged = True
        print("[llm] cota gratuita do Gemini esgotada")
    return None
