import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _nucleo  # noqa: E402


class handler(BaseHTTPRequestHandler):
    client = None  # injetado nos testes (mongomock)

    def do_GET(self):
        _nucleo.tratar(self, "status")

    def log_message(self, *a):  # sem log de requisicao (evita vazar dados)
        pass
