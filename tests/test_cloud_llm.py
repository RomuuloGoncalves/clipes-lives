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
        self.timeouts = []

    def __call__(self, req, timeout=None):
        self.calls.append((req.full_url, dict(req.header_items()), req.data))
        self.timeouts.append(timeout)
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
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEYS", KEYS)
    monkeypatch.setenv("CLIPES_GEMINI_MODELS", "m1,m2")
    monkeypatch.delenv("CLIPES_LLM_BACKEND", raising=False)
    monkeypatch.setenv("CLIPES_GEMINI_MIN_INTERVAL_S", "0")
    monkeypatch.setenv("CLIPES_GEMINI_COTA_FILE", str(tmp_path / "cota.json"))
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


# ---- T2: cotas ----
DIA = 1_700_000_000.0  # fixo; dia seguinte = +86400


def _daily():
    body = json.dumps({"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}]}}).encode()
    return urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO(body))


def _perminute():
    body = json.dumps({"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]}]}}).encode()
    return urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO(body))


@pytest.fixture
def cota(tmp_path, monkeypatch):
    p = tmp_path / ".gemini_cota.json"
    monkeypatch.setenv("CLIPES_GEMINI_COTA_FILE", str(p))
    monkeypatch.setenv("CLIPES_GEMINI_MIN_INTERVAL_S", "0")
    return p


def go(fake, t=DIA):
    return cloud_llm.complete_cloud("p", SCHEMA, "sys", urlopen=fake, now=lambda: t, sleep=lambda s: None)


def test_429_diario_grava_par_e_nova_rodada_nao_chama(cota):
    assert go(Fake(_daily(), {"titulo": "x"}))[1] == "cloud:gemini:m1"
    assert json.loads(cota.read_text())["esgotados"] == ["m1#0"]
    cloud_llm.reset_round()
    f = Fake({"titulo": "y"})
    go(f)
    assert len(f.calls) == 1 and f.calls[0][1]["X-goog-api-key"] == "AIzaKEY-B"


def test_virada_do_dia_libera_par(cota):
    go(Fake(_daily(), {"titulo": "x"}))
    cloud_llm.reset_round()
    f = Fake({"titulo": "y"})
    go(f, DIA + 86400)
    assert f.calls[0][1]["X-goog-api-key"] == "AIzaKEY-A"


def test_429_por_minuto_so_rodada_atual(cota):
    go(Fake(_perminute(), {"titulo": "x"}))
    assert not cota.exists()
    cloud_llm.reset_round()
    f = Fake({"titulo": "y"})
    go(f)
    assert f.calls[0][1]["X-goog-api-key"] == "AIzaKEY-A"


def test_todos_esgotados_none_sem_rede_log_uma_vez(cota, capsys):
    go(Fake(*[_daily() for _ in range(4)]))
    cloud_llm.reset_round()
    capsys.readouterr()
    f = Fake()
    assert go(f) is None and go(f) is None
    assert f.calls == []
    assert capsys.readouterr().out.count("[llm] cota gratuita do Gemini esgotada") == 1


def test_arquivo_corrompido_vira_vazio_e_reescrito(cota, capsys):
    cota.write_text("{lixo")
    f = Fake({"titulo": "x"})
    assert go(f)[1] == "cloud:gemini:m1"
    assert json.loads(cota.read_text())["esgotados"] == []
    assert "corrompido" in capsys.readouterr().out


def test_valor_da_chave_nunca_no_arquivo(cota):
    go(Fake(_daily(), _daily(), {"titulo": "x"}))
    assert "AIzaKEY" not in cota.read_text()


def test_intervalo_minimo_por_chave(cota, monkeypatch):
    monkeypatch.setenv("CLIPES_GEMINI_MIN_INTERVAL_S", "4")
    cloud_llm._last_call.clear()
    esperas = []
    kw = dict(urlopen=Fake({"titulo": "a"}, {"titulo": "b"}), now=lambda: DIA, sleep=esperas.append)
    cloud_llm.complete_cloud("p", SCHEMA, "s", **kw)
    cloud_llm.complete_cloud("p", SCHEMA, "s", **kw)
    assert esperas == [4.0]


def test_timeout_usa_clipes_cloud_timeout_padrao_20s(monkeypatch):
    monkeypatch.delenv("CLIPES_CLOUD_TIMEOUT", raising=False)
    monkeypatch.setenv("CLIPES_LLM_TIMEOUT", "60")
    f_default = Fake({"titulo": "x"})
    run(f_default)
    assert f_default.timeouts == [20.0]

    monkeypatch.setenv("CLIPES_CLOUD_TIMEOUT", "12.5")
    f_custom = Fake({"titulo": "x"})
    run(f_custom)
    assert f_custom.timeouts == [12.5]

    for bad in ("invalido", "0", "-5"):
        monkeypatch.setenv("CLIPES_CLOUD_TIMEOUT", bad)
        f_bad = Fake({"titulo": "x"})
        run(f_bad)
        assert f_bad.timeouts == [20.0]
