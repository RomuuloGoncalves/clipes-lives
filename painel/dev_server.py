"""Servidor HTTP local de desenvolvimento para o painel.

Executa `painel/index.html` e roteia `/api/status`, `/api/alvos` e `/api/comando`
diretamente para `painel.api._nucleo.tratar`, carregando o `.env` da raiz do
repositorio apenas quando `PAINEL_MONGO_URI` nao estiver definida no ambiente.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping, MutableMapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_PAINEL_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PAINEL_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from painel.api import _nucleo  # noqa: E402

DEFAULT_PORT: int = 8787
_ENV_LINE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _parse_env_value(raw: str) -> str:
    """Extrai o valor de uma linha KEY=VALUE, respeitando aspas e comentarios inline."""
    val = raw.strip()
    if not val or val.startswith("#"):
        return ""
    if val[0] in ('"', "'"):
        quote = val[0]
        end = val.find(quote, 1)
        if end != -1:
            return val[1:end]
        return val[1:]
    return re.split(r"\s+#", val, maxsplit=1)[0].strip()


def carregar_env(env_path: Path, environ: MutableMapping[str, str] | None = None) -> None:
    """Carrega variaveis de `env_path` sem sobrescrever as ja definidas em `environ`."""
    target = os.environ if environ is None else environ
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE_RE.match(line)
        if not match:
            continue
        key, raw_val = match.groups()
        if key not in target:
            target[key] = _parse_env_value(raw_val)


def configurar_ambiente(
    repo_root: Path = _REPO_ROOT,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Prepara `PAINEL_MONGO_URI` e `PAINEL_MONGO_DB` a partir do ambiente ou `.env` local."""
    target = os.environ if environ is None else environ
    if not target.get("PAINEL_MONGO_URI"):
        carregar_env(repo_root / ".env", target)
    if not target.get("PAINEL_MONGO_URI") and target.get("CLIPES_MONGO_URI"):
        target["PAINEL_MONGO_URI"] = target["CLIPES_MONGO_URI"]
    if not target.get("PAINEL_MONGO_DB") and target.get("CLIPES_MONGO_DB"):
        target["PAINEL_MONGO_DB"] = target["CLIPES_MONGO_DB"]


def resolver_porta(
    cli_port: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve a porta local via `--port`, `PAINEL_PORT` ou padrao 8787."""
    if cli_port is not None:
        port = cli_port
    else:
        env = os.environ if environ is None else environ
        raw = (env.get("PAINEL_PORT") or "").strip()
        port = int(raw) if raw else DEFAULT_PORT
    if port < 0 or port > 65535:
        raise ValueError(f"Porta invalida: {port}")
    return port


class PainelDevHandler(BaseHTTPRequestHandler):
    """Handler HTTP local para servir `index.html` e as rotas `/api/*` do painel."""

    client: Any = None
    index_path: Path = _PAINEL_DIR / "index.html"

    def _servir_index(self) -> None:
        if not self.index_path.is_file():
            return _nucleo._enviar(self, 404, {"erro": "index.html nao encontrado"})
        conteudo = self.index_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(conteudo)))
        self.end_headers()
        self.wfile.write(conteudo)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._servir_index()
        elif path == "/api/status":
            _nucleo.tratar(self, "status")
        elif path in ("/api/alvos", "/api/comando", "/api/config"):
            _nucleo._enviar(self, 405, {"erro": "metodo nao permitido"}, {"Allow": "POST"})
        else:
            _nucleo._enviar(self, 404, {"erro": "nao encontrado"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/alvos":
            _nucleo.tratar(self, "alvos")
        elif path == "/api/comando":
            _nucleo.tratar(self, "comando")
        elif path == "/api/config":
            _nucleo.tratar(self, "config")
        elif path == "/api/status":
            _nucleo._enviar(self, 405, {"erro": "metodo nao permitido"}, {"Allow": "GET"})
        else:
            _nucleo._enviar(self, 404, {"erro": "nao encontrado"})

    def log_message(self, format: str, *args: Any) -> None:
        pass


def criar_servidor(
    port: int = DEFAULT_PORT,
    handler_cls: type[PainelDevHandler] = PainelDevHandler,
    host: str = "127.0.0.1",
) -> ThreadingHTTPServer:
    """Instancia o `ThreadingHTTPServer` no host e porta informados."""
    return ThreadingHTTPServer((host, port), handler_cls)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Servidor local de desenvolvimento do painel.")
    parser.add_argument("--port", type=int, default=None, help="Porta HTTP local (padrao: PAINEL_PORT ou 8787).")
    parser.add_argument("--host", default="127.0.0.1", help="Endereco de bind HTTP (padrao: 127.0.0.1).")
    args = parser.parse_args(argv)

    configurar_ambiente()
    port = resolver_porta(args.port)
    servidor = criar_servidor(port, host=args.host)
    porta_real = servidor.server_address[1]
    print(f"Painel local disponivel em http://127.0.0.1:{porta_real}", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
