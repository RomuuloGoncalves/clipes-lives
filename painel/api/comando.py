import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _nucleo  # noqa: E402


class handler(BaseHTTPRequestHandler):
    client = None  # injetado nos testes (mongomock)

    def do_POST(self):
        _nucleo.tratar(self, "comando")

    def do_GET(self):
        _nucleo._enviar(self, 405, {"erro": "metodo nao permitido"}, {"Allow": "POST"})

    def log_message(self, *a):
        pass
