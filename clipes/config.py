"""Leitura de variaveis de ambiente e caminhos comuns (OUT_DIR, policy.json) num lugar so.

Valor invalido nunca derruba o processo: cai no padrao e avisa uma vez por variavel (M-22, B-12, B-13, B-16, B-17, M-19).
"""

import json
import math
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TRUE = ("1", "true", "yes", "on", "sim")
_FALSE = ("0", "false", "no", "off", "nao")
_SLOTS_DEFAULT = {"twitch": 1, "kick": 1, "youtube": 1, "busca": 1}
_SLOTS_INT_RE = re.compile(r"[0-9]+")
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


def slots(strict: bool = True) -> dict[str, int]:
    """Slots por plataforma lidos de CLIPES_SLOTS (padrao: twitch=1,kick=1,youtube=1,busca=1).

    Aceita inteiros >= 0 (0 desliga a plataforma no PC). Com `strict=True`, trecho invalido sai com codigo 2;
    com `strict=False`, cai no padrao com aviso.
    """
    result = dict(_SLOTS_DEFAULT)
    raw = os.environ.get("CLIPES_SLOTS", "").strip()
    if not raw:
        return result
    for parte in raw.split(","):
        trecho = parte.strip()
        if not trecho or "=" not in trecho:
            if strict:
                print(f"CLIPES_SLOTS invalido: {trecho}", file=sys.stderr, flush=True)
                raise SystemExit(2)
            _avisar("CLIPES_SLOTS", raw, _SLOTS_DEFAULT)
            return dict(_SLOTS_DEFAULT)
        plat_raw, val_raw = trecho.split("=", 1)
        plat = plat_raw.strip().lower()
        val_str = val_raw.strip()
        if plat not in result or not _SLOTS_INT_RE.fullmatch(val_str):
            if strict:
                print(f"CLIPES_SLOTS invalido: {trecho}", file=sys.stderr, flush=True)
                raise SystemExit(2)
            _avisar("CLIPES_SLOTS", raw, _SLOTS_DEFAULT)
            return dict(_SLOTS_DEFAULT)
        result[plat] = int(val_str)
    return result


# ---------------------------------------------------------------------------
# Catalogo CONFIG_SCHEMA (57 variaveis nao sensiveis), validacao e runtime
# ---------------------------------------------------------------------------

_FORBIDDEN_SUBSTRINGS = ("SECRET", "TOKEN", "KEY", "PASS", "URI")

FORBIDDEN_ENV_KEYS: frozenset[str] = frozenset({
    "CLIPES_MONGO_URI",
    "PAINEL_MONGO_URI",
    "PAINEL_MONGO_DB",
    "PAINEL_SENHA",
    "CLIPES_SECRETS",
    "CLIPES_CONFIG_DIR",
    "DISCORD_BOT_TOKEN",
    "DISCORD_CHANNEL_ID",
    "DISCORD_GUILD_ID",
    "DISCORD_ALLOWED_USER_IDS",
    "DISCORD_LOG_CHANNEL_ID",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "TIKTOK_CLIENT_KEY",
    "TIKTOK_CLIENT_ID",
    "TIKTOK_CLIENT_SECRET",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "GEMINI_API_KEYS",
    "GROQ_API_KEY",
})

CONFIG_SCHEMA: dict[str, dict[str, Any]] = {
    # --- lives (8) ---
    "CLIPES_SLOTS": {
        "grupo": "lives",
        "tipo": "slots",
        "default": "twitch=1,kick=1,youtube=1,busca=1",
        "descricao": "Vagas simultaneas por plataforma e busca (twitch=N,kick=N,youtube=N,busca=N)",
    },
    "CLIPES_CPU_THREADS": {
        "grupo": "lives",
        "tipo": "int",
        "default": "",
        "min": 1,
        "max": 64,
        "descricao": "Threads de CPU para Whisper, llama-server e FFmpeg (vazio = auto cgroup)",
    },
    "CLIPES_MIN_FREE_GB": {
        "grupo": "lives",
        "tipo": "float",
        "default": "5",
        "min": 0.0,
        "max": 1000.0,
        "descricao": "Espaco livre minimo em disco (GB) antes de pausar captura",
    },
    "CLIPES_BUFFER_CAP_GB": {
        "grupo": "lives",
        "tipo": "float",
        "default": "1.5",
        "min": 0.2,
        "max": 50.0,
        "descricao": "Limite maximo (GB) do buffer circular de lives",
    },
    "CLIPES_LIVE_CAPTURA_MORTA_S": {
        "grupo": "lives",
        "tipo": "float",
        "default": "900",
        "min": 60.0,
        "max": 7200.0,
        "descricao": "Tempo sem novos bytes (s) para reiniciar captura travada",
    },
    "CLIPES_BEAT_S": {
        "grupo": "lives",
        "tipo": "float",
        "default": "20",
        "min": 5.0,
        "max": 300.0,
        "descricao": "Intervalo de heartbeat (s) com o MongoDB",
    },
    "CLIPES_LEASE_S": {
        "grupo": "lives",
        "tipo": "float",
        "default": "90",
        "min": 30.0,
        "max": 600.0,
        "descricao": "Duracao do lease (s) de alvos e locks no MongoDB",
    },
    "CLIPES_JOBS": {
        "grupo": "lives",
        "tipo": "int",
        "default": "",
        "min": 1,
        "max": 8,
        "descricao": "Jobs processados em paralelo (vazio = 1; limitado por CPUs e RAM/2,5 GB)",
    },
    # --- render (12) ---
    "CLIPES_LAYOUT": {
        "grupo": "render",
        "escopo": "global",
        "tipo": "enum",
        "default": "split-starcuts",
        "opcoes": ["split-starcuts", "split-barra", "split-cheio", "split-fina", "full"],
        "descricao": "Modo de composicao vertical do clipe",
    },
    "CLIPES_SPLIT_BAR_H": {
        "grupo": "render",
        "tipo": "int",
        "default": "",
        "min": 0,
        "max": 600,
        "even": True,
        "descricao": "Altura (px, par) da barra no layout split",
    },
    "CLIPES_SPLIT_CAM_H": {
        "grupo": "render",
        "tipo": "int",
        "default": "",
        "min": 400,
        "max": 1020,
        "even": True,
        "descricao": "Altura (px, par) da facecam no layout split",
    },
    "CLIPES_FACECAM_TTL_H": {
        "grupo": "render",
        "tipo": "float",
        "default": "12",
        "min": 0.0,
        "max": 168.0,
        "descricao": "Validade (h) do cache de deteccao de facecam",
    },
    "CLIPES_BLUR_BG": {
        "grupo": "render",
        "tipo": "bool",
        "default": "1",
        "opcoes": ["1", "0"],
        "descricao": "Fundo desfocado no modo full (1=sim, 0=nao)",
    },
    "CLIPES_HOOK_TOP": {
        "grupo": "render",
        "tipo": "bool",
        "default": "1",
        "opcoes": ["1", "0"],
        "descricao": "Exibir gancho no topo do clipe (1=sim, 0=nao)",
    },
    "CLIPES_BAR": {
        "grupo": "render",
        "tipo": "bool",
        "default": "",
        "opcoes": ["1", "0"],
        "descricao": "Forcar barra de marca (vazio=automatico, 1=sim, 0=nao)",
    },
    "CLIPES_BRAND_NAME": {
        "grupo": "render",
        "tipo": "str",
        "default": "",
        "max_len": 40,
        "descricao": "Nome principal da marca na barra",
    },
    "CLIPES_BRAND_DISPLAY": {
        "grupo": "render",
        "tipo": "str",
        "default": "",
        "max_len": 40,
        "descricao": "Texto secundario/arroba na barra",
    },
    "CLIPES_BRAND_LOGO": {
        "grupo": "render",
        "tipo": "str",
        "default": "",
        "max_len": 120,
        "no_dotdot": True,
        "descricao": "Caminho do arquivo de logo da barra",
    },
    "CLIPES_BAR_STYLE": {
        "grupo": "render",
        "tipo": "enum",
        "default": "claro",
        "opcoes": ["claro", "escuro"],
        "descricao": "Tema visual da barra de marca",
    },
    "CLIPES_BAR_H": {
        "grupo": "render",
        "tipo": "int",
        "default": "420",
        "min": 300,
        "max": 600,
        "descricao": "Altura (px) da barra de marca",
    },
    "CLIPES_LAYOUT_FALLBACK": {
        "grupo": "render",
        "tipo": "enum",
        "default": "blur",
        "opcoes": ["blur", "recorte"],
        "escopo": "global",
        "descricao": "Fundo quando a facecam nao e detectada (blur ou recorte)",
    },
    # --- ia (11) ---
    "CLIPES_WHISPER_MODEL": {
        "grupo": "ia",
        "tipo": "enum",
        "default": "small",
        "opcoes": ["small", "large-v3-turbo"],
        "descricao": "Modelo Whisper para transcricao ASR",
    },
    "CLIPES_LLM_BACKEND": {
        "grupo": "ia",
        "tipo": "enum",
        "default": "auto",
        "opcoes": ["auto", "local"],
        "descricao": "Backend de LLM para roteiro/titulos (auto=Gemini+local, local=apenas local)",
    },
    "CLIPES_GEMINI_MODELS": {
        "grupo": "ia",
        "tipo": "str",
        "default": "gemini-2.5-flash,gemini-2.5-flash-lite",
        "max_len": 120,
        "descricao": "Lista CSV de modelos Gemini em ordem de prioridade",
    },
    "CLIPES_GEMINI_MIN_INTERVAL_S": {
        "grupo": "ia",
        "tipo": "float",
        "default": "4",
        "min": 0.0,
        "max": 60.0,
        "descricao": "Intervalo minimo (s) entre chamadas Gemini",
    },
    "CLIPES_CLOUD_TIMEOUT": {
        "grupo": "ia",
        "tipo": "float",
        "default": "20",
        "min": 5.0,
        "max": 120.0,
        "descricao": "Timeout (s) para chamadas Gemini na nuvem",
    },
    "CLIPES_LLM_TIMEOUT": {
        "grupo": "ia",
        "tipo": "float",
        "default": "45",
        "min": 10.0,
        "max": 300.0,
        "descricao": "Timeout (s) para chamadas ao llama-server local",
    },
    "CLIPES_PORTAO": {
        "grupo": "ia",
        "tipo": "enum",
        "default": "log",
        "opcoes": ["log", "on", "off"],
        "descricao": "Modo do portao de fala (log, on ou off)",
    },
    "CLIPES_MIN_WPS": {
        "grupo": "ia",
        "tipo": "float",
        "default": "0.4",
        "min": 0.0,
        "max": 5.0,
        "descricao": "Taxa minima de palavras por segundo no portao de fala",
    },
    "CLIPES_MIN_PALAVRAS": {
        "grupo": "ia",
        "tipo": "int",
        "default": "15",
        "min": 0,
        "max": 200,
        "descricao": "Minimo de palavras no trecho para passar no portao de fala",
    },
    "CLIPES_LEGENDA_IDIOMA": {
        "grupo": "ia",
        "tipo": "enum",
        "default": "pt",
        "opcoes": ["pt", "off"],
        "descricao": "Idioma alvo da traducao de legendas (pt ou off)",
    },
    "CLIPES_LEGENDA_ORIGINAL": {
        "grupo": "ia",
        "tipo": "bool",
        "default": "0",
        "opcoes": ["1", "0"],
        "descricao": "Exibir legenda original junto com a traduzida (1=sim, 0=nao)",
    },
    # --- publicacao (12) ---
    "CLIPES_MASTER_MODE": {
        "grupo": "publicacao",
        "tipo": "enum",
        "default": "livre",
        "opcoes": ["livre", "discord"],
        "descricao": "Modo de aprovacao antes da publicacao (livre ou discord)",
    },
    "CLIPES_DISCORD_MAX_MB": {
        "grupo": "publicacao",
        "tipo": "float",
        "default": "10",
        "min": 1.0,
        "max": 100.0,
        "descricao": "Tamanho maximo (MB) de video enviado como previa no Discord",
    },
    "CLIPES_REGISTRO_VERIFICAR": {
        "grupo": "publicacao",
        "tipo": "bool",
        "default": "1",
        "opcoes": ["1", "0"],
        "descricao": "Verificar ACK de anexo no registro do Discord (1=sim, 0=nao)",
    },
    "CLIPES_RETENCAO_H": {
        "grupo": "publicacao",
        "tipo": "float",
        "default": "",
        "min": 0.0,
        "max": 720.0,
        "descricao": "Horas para limpar arquivos locais apos envio ao Discord (vazio=manter)",
    },
    "CLIPES_YT_MAX_DIA": {
        "grupo": "publicacao",
        "tipo": "int",
        "default": "6",
        "min": 0,
        "max": 100,
        "descricao": "Limite diario de publicacoes no YouTube",
    },
    "CLIPES_TIKTOK_MAX_DIA": {
        "grupo": "publicacao",
        "tipo": "int",
        "default": "5",
        "min": 0,
        "max": 100,
        "descricao": "Limite diario de publicacoes no TikTok",
    },
    "CLIPES_YT_PRIVACY": {
        "grupo": "publicacao",
        "tipo": "enum",
        "default": "private",
        "opcoes": ["private", "unlisted", "public"],
        "descricao": "Visibilidade padrao no YouTube",
    },
    "CLIPES_TIKTOK_PRIVACY": {
        "grupo": "publicacao",
        "tipo": "enum",
        "default": "SELF_ONLY",
        "opcoes": ["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE"],
        "descricao": "Visibilidade padrao no TikTok",
    },
    "CLIPES_PUB_LEASE_S": {
        "grupo": "publicacao",
        "tipo": "float",
        "default": "1800",
        "min": 60.0,
        "max": 7200.0,
        "descricao": "Lease (s) de pedido de publicacao no MongoDB",
    },
    "CLIPES_PUB_MAX_POR_CICLO": {
        "grupo": "publicacao",
        "tipo": "int",
        "default": "3",
        "min": 1,
        "max": 20,
        "descricao": "Maximo de publicacoes por ciclo de run_pending",
    },
    "CLIPES_AGENDA_HORARIOS": {
        "grupo": "publicacao",
        "tipo": "schedule",
        "default": "11:30,19:00",
        "descricao": "Horarios agendados HH:MM separados por virgula",
    },
    "CLIPES_TZ": {
        "grupo": "publicacao",
        "tipo": "tz",
        "default": "America/Sao_Paulo",
        "descricao": "Fuso horario IANA para agendamento e cotas",
    },
    # --- temperatura (3) ---
    "CLIPES_VIGIA": {
        "grupo": "temperatura",
        "tipo": "bool",
        "default": "1",
        "opcoes": ["1", "0"],
        "descricao": "Vigia de temperatura: pausa o processamento quando o PC esquenta (1=sim, 0=nao)",
    },
    "CLIPES_TEMP_MAX": {
        "grupo": "temperatura",
        "tipo": "int",
        "default": "85",
        "min": 60,
        "max": 100,
        "descricao": "Temperatura (C) em que o vigia pausa o processamento",
    },
    "CLIPES_TEMP_MIN": {
        "grupo": "temperatura",
        "tipo": "int",
        "default": "75",
        "min": 40,
        "max": 95,
        "descricao": "Temperatura (C) em que o vigia retoma o processamento (menor que CLIPES_TEMP_MAX)",
    },
    "CLIPES_MEM": {
        "grupo": "recursos",
        "tipo": "mem",
        "default": "",
        "sem_limite": "*",
        "descricao": "Limite de RAM do conteiner (ex.: 4g ou 4096m; 512m a 128g; * = sem limite; vazio = automatico). Aplicado ao reiniciar o ./clipar (sai com codigo 75)",
    },
    "CLIPES_RAM_POR_JOB": {
        "grupo": "recursos",
        "tipo": "mem",
        "default": "",
        "min_mb": 1,
        "descricao": "RAM reservada por job (ex.: 2g ou 2560m; 1m a 128g; vazio = padrao 2,5 GB); define quantos jobs cabem na RAM do conteiner",
    },
    "CLIPES_CPUS": {
        "grupo": "recursos",
        "tipo": "float",
        "default": "",
        "min": 0.5,
        "max": 64.0,
        "sem_limite": "*",
        "descricao": "Limite de CPUs do conteiner (ex.: 2 ou 1.5; * = sem limite; vazio = automatico). Aplicado ao reiniciar o ./clipar (sai com codigo 75)",
    },
    "CLIPES_VISIBILIDADE": {
        "grupo": "publicacao",
        "tipo": "enum",
        "default": "padrao",
        "opcoes": ["padrao", "private", "public"],
        "escopo": "global",
        "descricao": "Visibilidade da publicacao (padrao = a de cada plataforma)",
    },
    # --- antiflop (7) ---
    "CLIPES_GATE": {
        "grupo": "antiflop",
        "tipo": "enum",
        "default": "bloquear",
        "opcoes": ["bloquear", "avisar", "off"],
        "escopo": "global",
        "descricao": "Gate de qualidade antes de publicar (bloquear, avisar ou off)",
    },
    "CLIPES_HOOK_MAX_ATRASO": {
        "grupo": "antiflop",
        "tipo": "float",
        "default": "1.0",
        "min": 0.3,
        "max": 3.0,
        "escopo": "global",
        "descricao": "Atraso maximo (s) do gancho no inicio do clipe",
    },
    "CLIPES_DUR_MIN": {
        "grupo": "antiflop",
        "tipo": "int",
        "default": "15",
        "min": 8,
        "max": 60,
        "escopo": "global",
        "descricao": "Duracao minima (s) do clipe",
    },
    "CLIPES_DUR_MAX": {
        "grupo": "antiflop",
        "tipo": "int",
        "default": "45",
        "min": 15,
        "max": 120,
        "escopo": "global",
        "descricao": "Duracao maxima (s) do clipe",
    },
    "CLIPES_CARTAO_FINAL": {
        "grupo": "antiflop",
        "tipo": "bool",
        "default": "0",
        "opcoes": ["1", "0"],
        "escopo": "global",
        "descricao": "Cartao final no clipe (1=sim, 0=nao)",
    },
    "CLIPES_HASHTAGS": {
        "grupo": "antiflop",
        "tipo": "int",
        "default": "3",
        "min": 1,
        "max": 5,
        "escopo": "global",
        "descricao": "Quantidade de hashtags por publicacao",
    },
    "CLIPES_DEDUP_PHASH": {
        "grupo": "antiflop",
        "tipo": "int",
        "default": "6",
        "min": 0,
        "max": 32,
        "escopo": "global",
        "descricao": "Distancia pHash maxima para considerar clipe duplicado (0 desliga)",
    },
}

_SCHEDULE_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_INT_STR_RE = re.compile(r"^-?[0-9]+$")
_MEM_RE = re.compile(r"^([0-9]{1,6})([mg])$")
_BOOT_ENV: dict[str, str] = {k: os.environ[k] for k in CONFIG_SCHEMA if k in os.environ}
_APPLIED_REMOTE_KEYS: set[str] = set()


def _is_forbidden_key(key: str) -> bool:
    upper = key.upper()
    if key in FORBIDDEN_ENV_KEYS or upper in FORBIDDEN_ENV_KEYS:
        return True
    if any(sub in upper for sub in _FORBIDDEN_SUBSTRINGS):
        return True
    return key not in CONFIG_SCHEMA


def _validate_single(key: str, raw_val: Any) -> tuple[str | None, str | None]:
    """Valida um par (key, raw_val). Retorna (valor_normalizado, erro).

    Se `raw_val` for None ou string vazia, retorna (None, None) indicando uso do padrao.
    """
    if _is_forbidden_key(key):
        return None, f"variavel nao permitida: {key}"
    if raw_val is None:
        return None, None
    meta = CONFIG_SCHEMA[key]
    tipo = meta["tipo"]

    if isinstance(raw_val, bool):
        if tipo != "bool":
            return None, f"valor invalido para {key}: tipo booleano nao aceito"
        val_str = "1" if raw_val else "0"
    elif isinstance(raw_val, (dict, list)):
        return None, f"valor invalido para {key}: tipo composto nao aceito"
    else:
        val_str = str(raw_val).strip()

    if val_str == "":
        return None, None

    if val_str == meta.get("sem_limite"):
        return val_str, None

    if tipo == "int":
        if not _INT_STR_RE.fullmatch(val_str):
            return None, f"valor invalido para {key}: esperado inteiro"
        val_i = int(val_str)
        if "min" in meta and val_i < meta["min"]:
            return None, f"valor invalido para {key}: minimo {meta['min']}"
        if "max" in meta and val_i > meta["max"]:
            return None, f"valor invalido para {key}: maximo {meta['max']}"
        if meta.get("even") and val_i % 2 != 0:
            return None, f"valor invalido para {key}: deve ser par"
        return str(val_i), None

    if tipo == "float":
        try:
            val_f = float(val_str)
        except ValueError:
            return None, f"valor invalido para {key}: esperado numero"
        if not math.isfinite(val_f):
            return None, f"valor invalido para {key}: numero nao finito"
        if "min" in meta and val_f < meta["min"]:
            return None, f"valor invalido para {key}: minimo {meta['min']}"
        if "max" in meta and val_f > meta["max"]:
            return None, f"valor invalido para {key}: maximo {meta['max']}"
        return val_str, None

    if tipo == "bool":
        low = val_str.lower()
        if low in _TRUE:
            return "1", None
        if low in _FALSE:
            return "0", None
        return None, f"valor invalido para {key}: esperado 1 ou 0"

    if tipo == "enum":
        if val_str not in meta["opcoes"]:
            return None, f"valor invalido para {key}: opcoes {', '.join(meta['opcoes'])}"
        return val_str, None

    if tipo == "mem":
        m = _MEM_RE.fullmatch(val_str.lower())
        if m is None:
            return None, f"valor invalido para {key}: formato 4g ou 4096m"
        mb = int(m.group(1)) * (1024 if m.group(2) == "g" else 1)
        min_mb = meta.get("min_mb", 512)
        if mb < min_mb or mb > 131072:
            return None, f"valor invalido para {key}: entre {min_mb}m e 128g"
        return f"{m.group(1)}{m.group(2)}", None

    if tipo == "slots":
        seen: set[str] = set()
        for parte in val_str.split(","):
            trecho = parte.strip()
            if not trecho or "=" not in trecho:
                return None, f"valor invalido para {key}: formato plataforma=N"
            plat_raw, num_raw = trecho.split("=", 1)
            plat = plat_raw.strip().lower()
            num_str = num_raw.strip()
            if plat not in _SLOTS_DEFAULT or not _SLOTS_INT_RE.fullmatch(num_str) or plat in seen:
                return None, f"valor invalido para {key}: trecho {trecho!r}"
            seen.add(plat)
        return val_str, None

    if tipo == "schedule":
        for parte in val_str.split(","):
            hora = parte.strip()
            if not _SCHEDULE_RE.fullmatch(hora):
                return None, f"valor invalido para {key}: horario {hora!r} (use HH:MM)"
        return val_str, None

    if tipo == "tz":
        try:
            ZoneInfo(val_str)
        except (ZoneInfoNotFoundError, ValueError):
            return None, f"valor invalido para {key}: fuso horario desconhecido"
        return val_str, None

    if tipo == "str":
        if "\n" in val_str or "\r" in val_str or "\x00" in val_str:
            return None, f"valor invalido para {key}: quebras de linha nao permitidas"
        max_len = int(meta.get("max_len", 120))
        if len(val_str) > max_len:
            return None, f"valor invalido para {key}: maximo {max_len} caracteres"
        if meta.get("no_dotdot") and ".." in val_str:
            return None, f"valor invalido para {key}: '..' nao permitido"
        return val_str, None

    return None, f"valor invalido para {key}"


def validate_config_payload(raw_cfg: Mapping[str, Any]) -> tuple[dict[str, str], str | None]:
    """Valida um dicionario de configuracoes contra `CONFIG_SCHEMA`.

    Chaves com valor vazio (`""`) ou `None` sao omitidas (significam usar o padrao).
    Retorna `(clean_dict, None)` ou `({}, mensagem_de_erro)`.
    """
    if not isinstance(raw_cfg, Mapping):
        return {}, "config deve ser um objeto"
    clean: dict[str, str] = {}
    for raw_key, raw_val in raw_cfg.items():
        key = str(raw_key).strip()
        norm, err = _validate_single(key, raw_val)
        if err is not None:
            return {}, err
        if norm is not None:
            clean[key] = norm
    temp_min = int(clean.get("CLIPES_TEMP_MIN", CONFIG_SCHEMA["CLIPES_TEMP_MIN"]["default"]))
    temp_max = int(clean.get("CLIPES_TEMP_MAX", CONFIG_SCHEMA["CLIPES_TEMP_MAX"]["default"]))
    if temp_min >= temp_max:
        return {}, "CLIPES_TEMP_MIN deve ser menor que CLIPES_TEMP_MAX"
    lo = int(clean.get("CLIPES_DUR_MIN") or CONFIG_SCHEMA["CLIPES_DUR_MIN"]["default"])
    hi = int(clean.get("CLIPES_DUR_MAX") or CONFIG_SCHEMA["CLIPES_DUR_MAX"]["default"])
    if lo >= hi:
        return {}, f"CLIPES_DUR_MIN ({lo}) deve ser menor que CLIPES_DUR_MAX ({hi})"
    return clean, None


def chaves_globais() -> frozenset[str]:
    """Chaves do schema com `escopo: "global"` (valem para todos os PCs; vencem a config por PC)."""
    return frozenset(k for k, m in CONFIG_SCHEMA.items() if m.get("escopo") == "global")


def config_global(db: Any) -> dict[str, str]:
    """Le `config_global/_id=global` do Mongo; so chaves globais validas. Sem db ou com erro: `{}`."""
    if db is None:
        return {}
    try:
        doc = db.config_global.find_one({"_id": "global"}) or {}
        cfg = doc.get("config")
        if not isinstance(cfg, Mapping):
            return {}
        globais = chaves_globais()
        clean, err = validate_config_payload({k: v for k, v in cfg.items() if k in globais})
        return {} if err else clean
    except Exception:
        return {}


def pc_alias(out_root: Path | str | None = None) -> str:
    """Identificador canonico do PC: CLIPES_PC_ALIAS -> CLIPES_ALIAS -> worker_id."""
    for env_name in ("CLIPES_PC_ALIAS", "CLIPES_ALIAS"):
        val = os.environ.get(env_name, "").strip()
        if val:
            return val
    from clipes import capacidade

    return capacidade.worker_id(resolve_out_dir(out_root))


def _cache_path(out_root: Path | str | None, alias: str) -> Path:
    safe_alias = re.sub(r"[^A-Za-z0-9_.-]+", "_", alias or "pc")
    return resolve_out_dir(out_root) / f".pc_config_{safe_alias}.json"


def apply_remote_config(
    remote_cfg: Mapping[str, Any] | None,
    *,
    out_root: Path | str | None = None,
    alias: str | None = None,
) -> dict[str, str]:
    """Aplica overrides do MongoDB em `os.environ` com precedencia Mongo > _BOOT_ENV > Default.

    Chaves fora de `CONFIG_SCHEMA`, segredos ou valores invalidos sao ignorados.
    Chaves ausentes ou vazias restauram o valor de `_BOOT_ENV` ou sao removidas de `os.environ`.
    Grava espelho local em `OUT_DIR/.pc_config_<alias>.json`.
    """
    applied: dict[str, str] = {}
    if isinstance(remote_cfg, Mapping):
        for raw_key, raw_val in remote_cfg.items():
            key = str(raw_key).strip()
            norm, err = _validate_single(key, raw_val)
            if err is not None:
                _avisar(f"remote:{key}", str(raw_val), "ignorado")
                continue
            if norm is not None:
                applied[key] = norm

    for key in CONFIG_SCHEMA:
        if key in applied:
            if key not in _APPLIED_REMOTE_KEYS:
                if key in os.environ:
                    _BOOT_ENV[key] = os.environ[key]
                else:
                    _BOOT_ENV.pop(key, None)
            os.environ[key] = applied[key]
            _APPLIED_REMOTE_KEYS.add(key)
        elif key in _APPLIED_REMOTE_KEYS:
            _APPLIED_REMOTE_KEYS.discard(key)
            if key in _BOOT_ENV:
                os.environ[key] = _BOOT_ENV[key]
            else:
                os.environ.pop(key, None)

    resolved_alias = (alias or "").strip() or pc_alias(out_root)
    cache_file = _cache_path(out_root, resolved_alias)
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(applied, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except OSError:
        pass

    # VIG-03: o worker grava os valores efetivos para o vigia-temp reler (tmp + os.replace).
    def _efetivo(key: str) -> str:
        return os.environ.get(key, "").strip() or CONFIG_SCHEMA[key]["default"]

    vigia_file = resolve_out_dir(out_root) / ".vigia"
    try:
        tmp_file = vigia_file.with_name(".vigia.tmp")
        tmp_file.write_text(
            f"{_efetivo('CLIPES_VIGIA')} {_efetivo('CLIPES_TEMP_MAX')} {_efetivo('CLIPES_TEMP_MIN')}\n",
            encoding="utf-8",
        )
        os.replace(tmp_file, vigia_file)
    except OSError:
        pass

    # O limite de RAM/CPU e do Docker: o worker so grava o pedido do painel em `.recursos`
    # ("mem cpus", "-" = automatico) para o ./clipar reler ao reiniciar o contêiner.
    recursos_file = resolve_out_dir(out_root) / ".recursos"
    try:
        tmp_file = recursos_file.with_name(".recursos.tmp")
        tmp_file.write_text(
            f"{applied.get('CLIPES_MEM', '-')} {applied.get('CLIPES_CPUS', '-')}\n",
            encoding="utf-8",
        )
        os.replace(tmp_file, recursos_file)
    except OSError:
        pass
    return applied


def _ler(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _cgroup_limites(cgroup_root: Path | str = "/sys/fs/cgroup") -> tuple[int | None, float | None]:
    """Le (memoria em bytes, CPUs) do cgroup do proprio conteiner (v2, com fallback v1).

    0 = sem limite; None = ilegivel.
    """
    root = Path(cgroup_root)
    mem: int | None = None
    cpus: float | None = None
    raw = _ler(root / "memory.max")
    if raw is None:
        raw = _ler(root / "memory" / "memory.limit_in_bytes")
        if raw is not None and raw.isdigit():
            raw = "max" if int(raw) >= 2**60 else raw
    if raw == "max":
        mem = 0
    elif raw is not None and raw.isdigit():
        mem = int(raw)
    cpu = _ler(root / "cpu.max")
    if cpu is not None:
        partes = cpu.split()
        quota, periodo = (partes + ["", ""])[:2]
    else:
        quota = _ler(root / "cpu" / "cpu.cfs_quota_us") or ""
        periodo = _ler(root / "cpu" / "cpu.cfs_period_us") or "100000"
        quota = "max" if quota == "-1" else quota
    if quota == "max":
        cpus = 0.0
    elif quota.isdigit() and periodo.isdigit() and int(periodo) > 0:
        cpus = int(quota) / int(periodo)
    return mem, cpus


def recursos_divergentes(cgroup_root: Path | str = "/sys/fs/cgroup") -> bool:
    """True se o painel pediu RAM/CPU diferentes do limite atual do cgroup (`*` = sem limite)."""
    mem_atual, cpus_atual = _cgroup_limites(cgroup_root)
    pedido_mem = os.environ.get("CLIPES_MEM", "").strip().lower() if "CLIPES_MEM" in _APPLIED_REMOTE_KEYS else ""
    pedido_cpus = os.environ.get("CLIPES_CPUS", "").strip() if "CLIPES_CPUS" in _APPLIED_REMOTE_KEYS else ""
    if pedido_mem and mem_atual is not None:
        if pedido_mem == "*":
            if mem_atual != 0:
                return True
        else:
            m = _MEM_RE.fullmatch(pedido_mem)
            if m is not None:
                alvo = int(m.group(1)) * (1024 if m.group(2) == "g" else 1) * 1024 * 1024
                if mem_atual == 0 or abs(alvo - mem_atual) > alvo * 0.01:
                    return True
    if pedido_cpus and cpus_atual is not None:
        if pedido_cpus == "*":
            if cpus_atual != 0:
                return True
        else:
            try:
                if cpus_atual == 0 or abs(float(pedido_cpus) - cpus_atual) > 0.05:
                    return True
            except ValueError:
                pass
    return False


def recursos_pendentes(cgroup_root: Path | str = "/sys/fs/cgroup") -> bool:
    """True se o painel pediu RAM/CPU diferentes do limite atual do contêiner.

    So vale sob `./clipar`/compose (`CLIPES_RECURSOS_GERIDO=1`), que relê `.recursos` ao reiniciar;
    sem isso reiniciar nao mudaria nada e o worker ficaria em laco.
    """
    return os.environ.get("CLIPES_RECURSOS_GERIDO") == "1" and recursos_divergentes(cgroup_root)


def load_cached_remote_config(out_root: Path | str | None = None, alias: str | None = None) -> dict[str, str]:
    """Le o cache local `.pc_config_<alias>.json` para uso quando o MongoDB esta offline no boot."""
    resolved_alias = (alias or "").strip() or pc_alias(out_root)
    cache_file = _cache_path(out_root, resolved_alias)
    if not cache_file.is_file():
        return {}
    try:
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, Mapping):
        return {}
    valid: dict[str, str] = {}
    for raw_key, raw_val in raw.items():
        key = str(raw_key).strip()
        norm, err = _validate_single(key, raw_val)
        if err is None and norm is not None:
            valid[key] = norm
    return valid
