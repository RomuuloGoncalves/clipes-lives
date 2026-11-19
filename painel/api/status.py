import os
import sys
from http.server import BaseHTTPRequestHandler
from typing import Any

if __package__:
    from . import _nucleo
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _nucleo  # type: ignore[no-redef]  # noqa: E402


class handler(BaseHTTPRequestHandler):
    client: Any = None  # injetado nos testes (mongomock)

    def do_GET(self) -> None:
        _nucleo.tratar(self, "status")

    def log_message(self, format: str, *args: Any) -> None:  # sem log de requisicao (evita vazar dados)
        pass
