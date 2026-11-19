"""Leitura de variaveis de ambiente e caminhos comuns (OUT_DIR, policy.json) num lugar so.

Valor invalido nunca derruba o processo: cai no padrao e avisa uma vez por variavel (M-22, B-12, B-13, B-16, B-17, M-19).
"""

import math
import os
from pathlib import Path

_TRUE = ("1", "true", "yes", "on", "sim")
_FALSE = ("0", "false", "no", "off", "nao")
_avisados: set[str] = set()


def _avisar(name: str, raw: str, default: object) -> None:
    if name not in _avisados:
        _avisados.add(name)
        print(f"[config] {name}={raw!r} invalido; usando {default!r}", flush=True)


def env_float(name: str, default: float, *, min_value: float | None = None) -> float:
    """float da env; vazio -> padrao; invalido ou abaixo de `min_value` -> padrao com aviso."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        val = float(raw)
    except ValueError:
        _avisar(name, raw, default)
        return float(default)
    if not math.isfinite(val) or (min_value is not None and val < min_value):  # NaN/inf ou fora do piso
        _avisar(name, raw, default)
        return float(default)
    return val


def env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    """int da env (aceita '3' e '3.0'); vazio -> padrao; invalido ou abaixo de `min_value` -> padrao com aviso."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return int(default)
    try:
        f = float(raw)
        if f != int(f):
            raise ValueError(raw)
        val = int(f)
    except (ValueError, OverflowError):
        _avisar(name, raw, default)
        return int(default)
    if min_value is not None and val < min_value:
        _avisar(name, raw, default)
        return int(default)
    return val


def env_flag(name: str, default: bool) -> bool:
    """Booleano da env: 1/true/yes/on/sim e 0/false/no/off/nao (sem diferenciar maiusculas); outro valor -> padrao."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    _avisar(name, raw, default)
    return default


def resolve_out_dir(out_dir: Path | str | None = None) -> Path:
    """Raiz de saida: argumento, senao OUT_DIR, senao ./out; sempre `expanduser().resolve()`."""
    raw = out_dir if out_dir not in (None, "") else (os.environ.get("OUT_DIR", "").strip() or "out")
    return Path(raw).expanduser().resolve()


def resolve_policy_path(out_root: Path | str | None = None, *, legacy: Path | str | None = None) -> Path:
    """policy.json unico para worker, acompanhar, bot e publicacao: CLIPES_POLICY_FILE, senao `<OUT_DIR>/policy.json`.

    `legacy` (ex.: o policy.json ao lado do channels.txt) so e usado, com aviso, quando o arquivo padrao nao existe
    e o legado existe, para nao soltar bloqueios de quem ja usava o caminho antigo.
    """
    env = os.environ.get("CLIPES_POLICY_FILE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    path = resolve_out_dir(out_root) / "policy.json"
    if legacy is not None and not path.is_file():
        old = Path(legacy).expanduser().resolve()
        if old.is_file() and old != path:
            if "policy.json:legado" not in _avisados:
                _avisados.add("policy.json:legado")
                print(f"[config] {path} nao existe; usando policy legada {old} (mova-a para OUT_DIR ou use CLIPES_POLICY_FILE)", flush=True)
            return old
    return path
