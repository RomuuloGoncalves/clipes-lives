import io
import json
import urllib.error

import pytest

from clipes import cloud_llm

SCHEMA = {"type": "object", "required": ["titulo"]}
KEYS = "AIzaKEY-A,AIzaKEY-B"


class Fake:
    """urlopen falso: respostas por chamada (dict -> JSON válido, int -> HTTPError, str -> texto cru)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append((req.full_url, dict(req.header_items()), req.data))
        o = self.outcomes.pop(0)
        if isinstance(o, int):
            raise urllib.error.HTTPError(req.full_url, o, "err", {}, io.BytesIO(b""))
        if isinstance(o, Exception):
            raise o
        text = json.dumps(o) if isinstance(o, dict) else o
        body = json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode()
        return _Resp(body)


class _Resp:
    def __init__(self, b):
        self.b = b

    def read(self):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", KEYS)
    monkeypatch.setenv("CLIPES_GEMINI_MODELS", "m1,m2")
    monkeypatch.delenv("CLIPES_LLM_BACKEND", raising=False)
    cloud_llm.reset_round()


def run(fake):
    return cloud_llm.complete_cloud("p", SCHEMA, "sys", urlopen=fake)


def test_primeiro_par_ok():
    f = Fake({"titulo": "x"})
    assert run(f) == ({"titulo": "x"}, "cloud:gemini:m1")
    assert len(f.calls) == 1
    assert cloud_llm.providers_used() == ["gemini"]


def test_ordem_modelo_x_chave():
    f = Fake(429, 500, 503, {"titulo": "x"})
    assert run(f)[1] == "cloud:gemini:m2"
    urls = [c[0] for c in f.calls]
    assert [("m1" in u) for u in urls] == [True, True, False, False]


@pytest.mark.parametrize("falha", [429, 500, 503, 404, TimeoutError("t"), "não é json", '{"outro": 1}'])
def test_falha_passa_ao_proximo_par(falha):
    f = Fake(falha, {"titulo": "x"})
    assert run(f) == ({"titulo": "x"}, "cloud:gemini:m1")
    assert len(f.calls) == 2
    assert f.calls[0][1]["X-goog-api-key"] != f.calls[1][1]["X-goog-api-key"]


def test_par_que_falhou_nao_repete_na_rodada():
    f = Fake(429, 429, 429, 429)
    assert run(f) is None
    assert len(f.calls) == 4
    f2 = Fake()
    assert run(f2) is None
    assert f2.calls == []
    cloud_llm.reset_round()
    assert len(Fake({"titulo": "y"}).outcomes) == 1
    assert run(Fake({"titulo": "y"}))[0] == {"titulo": "y"}


def test_chave_so_no_header():
    f = Fake({"titulo": "x"})
    run(f)
    url, headers, body = f.calls[0]
    assert headers["X-goog-api-key"] == "AIzaKEY-A"
    assert "AIzaKEY" not in url and b"AIzaKEY" not in body


def test_mask_cobre_excecao():
    assert cloud_llm.mask("erro AIzaKEY-A e AIzaKEY-B") == "erro *** e ***"


def test_log_nao_vaza_chave(capsys):
    run(Fake(RuntimeError("boom AIzaKEY-A"), {"titulo": "x"}))
    out = capsys.readouterr().out
    assert "AIzaKEY" not in out and "***" in out


def test_backend_local_zero_chamadas(monkeypatch):
    monkeypatch.setenv("CLIPES_LLM_BACKEND", "local")
    f = Fake()
    assert not cloud_llm.cloud_enabled()
    assert run(f) is None and f.calls == []


def test_sem_chave_zero_chamadas(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEYS")
    f = Fake()
    assert not cloud_llm.cloud_enabled()
    assert run(f) is None and f.calls == []


def test_think_e_markdown_removidos():
    f = Fake('<think>hm</think>\n```json\n{"titulo": "x"}\n```')
    assert run(f)[0] == {"titulo": "x"}
