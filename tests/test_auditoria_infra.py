"""Testes de auditoria de infra e documentacao (M-21, B-18, B-19, B-20, B-21, B-24)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_PATH = ROOT / "docker-compose.yml"
DOCKERFILE_PATH = ROOT / "Dockerfile"
CLIPAR_PATH = ROOT / "clipar"
VIGIA_PATH = ROOT / "vigia-temp"
PYPROJECT_PATH = ROOT / "pyproject.toml"
README_PATH = ROOT / "README.md"
ENV_EXAMPLE_PATH = ROOT / ".env.example"

# Variaveis lidas em clipes/*.py que NAO devem ser repassadas pelo docker-compose.yml:
# - fixadas via ENV no Dockerfile apontando para modelos assados na imagem;
#   repassar vazio (${VAR:-}) sobrescreveria o caminho do Dockerfile e quebraria o container.
EXCECOES_COMPOSE: dict[str, str] = {
    "CLIPES_EMBED_ONNX": "fixada no Dockerfile (/opt/models/embed/...)",
    "CLIPES_LLM_GGUF": "fixada no Dockerfile (/opt/models/llm/atual.gguf)",
    "CLIPES_YUNET_ONNX": "fixada no Dockerfile (/opt/models/yunet/...)",
    "CLIPES_MAX_LIVES": "substituida por CLIPES_SLOTS (AD-019; mantida apenas em codigo legado)",
}

# Variaveis internas do container definidas no compose que nao pertencem ao .env do usuario.
VARS_INTERNAS_CONTAINER: set[str] = {"HOME", "XDG_CACHE_HOME", "HF_HOME"}

# Variaveis de credenciais/segredos que nunca podem ter valor preenchido no .env.example.
VARS_SEGREDOS: set[str] = {
    "DISCORD_BOT_TOKEN",
    "CLIPES_MONGO_URI",
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
}

_ENV_RE = re.compile(
    r"""(?:os\.environ\.get|os\.getenv|\benv_float|\benv_int|\benv_flag)\(\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]"""
)


def _parse_compose_services(text: str) -> dict[str, dict[str, object]]:
    """Extrai services -> {user, environment: dict[str, str]} sem depender de PyYAML."""
    services: dict[str, dict[str, object]] = {}
    in_services = False
    cur_service: str | None = None
    in_env = False

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            in_services = stripped == "services:"
            cur_service = None
            in_env = False
            continue

        if not in_services:
            continue

        if indent == 2 and stripped.endswith(":"):
            cur_service = stripped[:-1]
            services[cur_service] = {"user": None, "restart": None, "environment": {}}
            in_env = False
            continue

        if cur_service is None:
            continue

        if indent == 4:
            in_env = stripped == "environment:"
            if stripped.startswith("user:"):
                val = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                services[cur_service]["user"] = val
            elif stripped.startswith("restart:"):
                val = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                services[cur_service]["restart"] = val
            continue

        if indent == 6 and in_env and ":" in stripped:
            key, val = stripped.split(":", 1)
            env_map = services[cur_service]["environment"]
            assert isinstance(env_map, dict)
            env_map[key.strip()] = val.strip()

    return services


def _extract_code_env_vars() -> set[str]:
    found: set[str] = set()
    for path in sorted((ROOT / "clipes").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _ENV_RE.finditer(text):
            var = match.group(1) or match.group(2)
            if var:
                found.add(var)
    return found


def _parse_env_example(text: str) -> dict[str, str]:
    """Extrai variaveis declaradas (ativas ou comentadas) em .env.example."""
    result: dict[str, str] = {}
    pat = re.compile(r"^\s*(?:#\s*)?([A-Z][A-Z0-9_]*)=(.*)$")
    for line in text.splitlines():
        m = pat.match(line)
        if not m:
            continue
        name = m.group(1)
        raw_val = m.group(2).split("#", 1)[0].strip()
        result[name] = raw_val
    return result


# --- M-21 ---------------------------------------------------------------------


def test_m21_todas_envs_do_codigo_estao_no_compose_ou_excecoes():
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "env_file:" not in compose_text, "compose nao deve usar env_file (nao vazar .env inteiro)"

    services = _parse_compose_services(compose_text)
    clipes_env = services["clipes"]["environment"]
    bot_env = services["bot"]["environment"]
    assert isinstance(clipes_env, dict)
    assert isinstance(bot_env, dict)

    code_vars = _extract_code_env_vars()
    assert code_vars, "regex nao encontrou nenhuma variavel em clipes/*.py"

    faltando = sorted(v for v in code_vars if v not in clipes_env and v not in EXCECOES_COMPOSE)
    assert not faltando, f"variaveis lidas em clipes/*.py ausentes do servico clipes no compose: {faltando}"

    # Variaveis fixadas no Dockerfile nao podem aparecer no compose (repassar vazio quebraria).
    vazadas = sorted(v for v in EXCECOES_COMPOSE if v in clipes_env or v in bot_env)
    assert not vazadas, f"variaveis fixadas no Dockerfile nao devem ser repassadas no compose: {vazadas}"


def test_m21_variaveis_novas_e_da_tabela_repassadas_no_compose():
    services = _parse_compose_services(COMPOSE_PATH.read_text(encoding="utf-8"))
    clipes_env = services["clipes"]["environment"]
    bot_env = services["bot"]["environment"]
    assert isinstance(clipes_env, dict)
    assert isinstance(bot_env, dict)

    esperadas_clipes = [
        "TWITCH_CLIENT_ID",
        "TWITCH_CLIENT_SECRET",
        "CLIPES_BLUR_BG",
        "CLIPES_QUOTA_PATH",
        "CLIPES_FACECAM_CONFIG",
        "CLIPES_LLM_TIMEOUT",
        "CLIPES_CLOUD_TIMEOUT",
        "CLIPES_GEMINI_COTA_FILE",
        "CLIPES_MIN_FREE_GB",
        "CLIPES_PUB_LEASE_S",
        "CLIPES_PUB_MAX_POR_CICLO",
        "CLIPES_BUFFER_CAP_GB",
        "CLIPES_LIVE_CAPTURA_MORTA_S",
        "CLIPES_SLOTS",
        "DISCORD_LOG_CHANNEL_ID",
    ]
    for var in esperadas_clipes:
        assert clipes_env.get(var) == f"${{{var}:-}}", f"clipes.environment[{var}] esperado ${{{var}:-}}"

    esperadas_bot = [
        "CLIPES_SLOTS",
        "CLIPES_POLICY_FILE",
        "DISCORD_LOG_CHANNEL_ID",
        "CLIPES_QUOTA_PATH",
        "CLIPES_PUB_LEASE_S",
        "CLIPES_PUB_MAX_POR_CICLO",
    ]
    for var in esperadas_bot:
        assert bot_env.get(var) == f"${{{var}:-}}", f"bot.environment[{var}] esperado ${{{var}:-}}"

    assert "CLIPES_MAX_LIVES" not in clipes_env
    assert "CLIPES_MAX_LIVES" not in bot_env
    assert services["bot"]["restart"] == "unless-stopped"


# --- B-24 ---------------------------------------------------------------------


def test_b24_compose_name_consistente_com_clipar_e_vigia_temp():
    compose_lines = COMPOSE_PATH.read_text(encoding="utf-8").splitlines()
    top_level = [ln.strip() for ln in compose_lines if ln.strip() and not ln.startswith(" ") and not ln.startswith("#")]
    assert top_level[0] == "name: clipes-lives"

    label = "label=com.docker.compose.project=clipes-lives"
    svc_label = "label=com.docker.compose.service=clipes"
    clipar_text = CLIPAR_PATH.read_text(encoding="utf-8")
    assert label in clipar_text
    assert svc_label in clipar_text
    vigia_text = VIGIA_PATH.read_text(encoding="utf-8")
    assert vigia_text.count(label) >= 3
    assert vigia_text.count(svc_label) >= 3


# --- B-20 ---------------------------------------------------------------------


def test_b20_compose_usa_host_uid_gid_e_clipar_exporta(tmp_path):
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "${UID:-" not in compose_text
    assert "${GID:-" not in compose_text

    services = _parse_compose_services(compose_text)
    for svc in ("clipes", "bot", "test"):
        assert services[svc]["user"] == "${HOST_UID:-1000}:${HOST_GID:-1000}"

    # Testa em execucao que ./clipar exporta HOST_UID e HOST_GID com id -u / id -g.
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    clipar_copy = app_dir / "clipar"
    shutil.copy2(CLIPAR_PATH, clipar_copy)
    clipar_copy.chmod(0o755)

    vigia_fake = app_dir / "vigia-temp"
    vigia_fake.write_text("#!/bin/sh\n[ \"${1:-}\" = \"--check\" ] && echo 70 && exit 0\nexit 0\n", encoding="utf-8")
    vigia_fake.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def _mk(name: str, body: str) -> None:
        p = bin_dir / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)

    _mk("id", '#!/bin/sh\n[ "$1" = "-u" ] && echo 1234 && exit 0\n[ "$1" = "-g" ] && echo 5678 && exit 0\nexit 1\n')
    _mk("nproc", "#!/bin/sh\necho 8\n")
    docker_log = tmp_path / "docker.log"
    _mk(
        "docker",
        f'#!/bin/sh\n[ "$1" = "compose" ] && printf "UID=%s|GID=%s\\n" "${{HOST_UID:-}}" "${{HOST_GID:-}}" >> "{docker_log}"\nexit 0\n',
    )

    sysfs = tmp_path / "sys"
    (sysfs / "class" / "power_supply").mkdir(parents=True)
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text("MemAvailable:   8388608 kB\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["SYSFS_ROOT"] = str(sysfs)
    env["PROC_ROOT"] = str(proc)
    env["OUT_DIR"] = str(tmp_path / "out")
    env["CLIPES_SECRETS"] = str(tmp_path / "inexistente.env")
    for k in ("HOST_UID", "HOST_GID", "CLIPES_CPUS", "CLIPES_MEM", "CLIPES_CPU_THREADS"):
        env.pop(k, None)

    res = subprocess.run([str(clipar_copy), "monitor"], env=env, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    assert docker_log.read_text(encoding="utf-8").strip() == "UID=1234|GID=5678"


# --- B-19 ---------------------------------------------------------------------


def test_b19_dockerfile_liga_with_llm_4b_ao_symlink_e_compose_documenta():
    df = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "CLIPES_LLM_GGUF=/opt/models/llm/atual.gguf" in df
    assert "ARG WITH_LLM_4B=0" in df
    assert "os.symlink(alvo, link)" in df
    assert "/opt/models/llm/atual.gguf" in df
    assert "Qwen3-4B-Instruct-2507-Q4_K_M.gguf" in df

    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "WITH_LLM_4B" in compose_text
    assert "# args:" in compose_text


# --- B-21 ---------------------------------------------------------------------


def test_b21_pyproject_optional_dependencies_batem_com_dockerfile():
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    opt = data["project"]["optional-dependencies"]
    expected = {
        "live": ["streamlink>=7.0"],
        "coord": ["pymongo>=4.8"],
        "bot": ["discord.py>=2.4,<3"],
        "visao": ["onnxruntime>=1.18", "tokenizers>=0.19", "opencv-python-headless>=4.9"],
        "dev": ["pytest>=8.0.0", "mongomock>=4.3"],
    }
    assert opt == expected

    df = DOCKERFILE_PATH.read_text(encoding="utf-8")
    for deps in expected.values():
        for spec in deps:
            assert f'"{spec}"' in df, f"spec {spec!r} do pyproject deve bater com o Dockerfile"


# --- B-18 ---------------------------------------------------------------------


def test_b18_readme_atualizado():
    readme = README_PATH.read_text(encoding="utf-8")
    assert "onda-ab" not in readme
    assert "git checkout master" in readme
    assert "limita o container a 3 CPUs" not in readme
    assert "CLIPES_CPUS" in readme
    assert "sem chaves de API" not in readme
    assert "GEMINI_API_KEYS" in readme


def test_b18_env_example_completo_e_sem_segredos():
    env_text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "nao implementado" not in env_text.lower()

    services = _parse_compose_services(COMPOSE_PATH.read_text(encoding="utf-8"))
    clipes_env = services["clipes"]["environment"]
    assert isinstance(clipes_env, dict)

    esperadas = {v for v in clipes_env if v not in VARS_INTERNAS_CONTAINER}
    declaradas = _parse_env_example(env_text)

    faltando = sorted(esperadas - set(declaradas))
    assert not faltando, f"variaveis do compose ausentes em .env.example: {faltando}"

    # Nenhum segredo pode ter valor preenchido no .env.example.
    for var in sorted(VARS_SEGREDOS):
        assert var in declaradas, f"{var} deve estar documentada em .env.example"
        assert declaradas[var] == "", f"{var} nao pode conter valor/segredo em .env.example"
