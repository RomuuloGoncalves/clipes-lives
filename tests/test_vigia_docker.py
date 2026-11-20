"""Vigia como servico do compose (clipes/vigia.py): temperatura, pausa e limites ao vivo, offline."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from clipes import vigia
from clipes.vigia import Vigia, temp_lhm_json, temp_sysfs

ROOT = Path(__file__).resolve().parent.parent
CID = "abc123def456"


# --- sysfs / LHM ---------------------------------------------------------------------------------


def _hwmon(sysfs: Path, n: int, nome: str, *milli: int) -> None:
    d = sysfs / "class" / "hwmon" / f"hwmon{n}"
    d.mkdir(parents=True)
    (d / "name").write_text(nome + "\n")
    for i, m in enumerate(milli, 1):
        (d / f"temp{i}_input").write_text(f"{m}\n")


def test_sysfs_k10temp_e_maximo(tmp_path):
    _hwmon(tmp_path, 0, "nvme", 90000)  # ignorado
    _hwmon(tmp_path, 1, "k10temp", 61000, 55500)
    assert temp_sysfs(tmp_path) == 61


def test_sysfs_thermal_zone_quando_sem_hwmon_de_cpu(tmp_path):
    for i, v in enumerate((40000, 47999)):
        z = tmp_path / "class" / "thermal" / f"thermal_zone{i}"
        z.mkdir(parents=True)
        (z / "temp").write_text(f"{v}\n")
    assert temp_sysfs(tmp_path) == 47


def test_sysfs_vazio_grava_sem_sensor(tmp_path):
    v = Vigia({"OUT_DIR": str(tmp_path / "out"), "SYSFS_ROOT": str(tmp_path / "sys"), "DOCKER_SOCK": str(tmp_path / "nao")})
    v.lhm.ler = lambda: None
    v.ciclo()
    assert (tmp_path / "out" / ".temp").read_text() == "sem-sensor\n"


def test_lhm_virgula_decimal_varios_sensores_maximo():
    arvore = {"Text": "Sensor", "Children": [{"Text": "PC", "Children": [{"Text": "CPU", "Children": [
        {"Text": "Temperatures", "Children": [
            {"Text": "CPU Package", "Type": "Temperature", "Value": "45,0 °C"},
            {"Text": "Core #1", "Type": "Temperature", "Value": "62,5 °C"},
            {"Text": "Core Max", "Type": "Load", "Value": "99,0 %"},
            {"Text": "GPU Core", "Type": "Temperature", "Value": "50,0 °C"},
            {"Text": "Chipset", "Type": "Temperature", "Value": "80,0 °C"},
        ]}]}]}]}
    assert temp_lhm_json(arvore) == 62  # GPU Core (50) e Chipset (80) nao entram; maximo da CPU = 62,5
    assert temp_lhm_json({"Children": []}) is None


def test_lhm_ignora_gpu_e_distancia_ao_tjmax():
    cpu = {"Text": "Ryzen", "ImageURL": "images_icon/cpu.png", "Children": [{"Text": "Temperatures", "Children": [
        {"Text": "Core (Tctl/Tdie)", "Type": "Temperature", "Value": "55,0 °C"},
        {"Text": "CPU Core #1 Distance to TjMax", "Type": "Temperature", "Value": "95,0 °C"},
        {"Text": "Core #2 Distance to TjMax", "Type": "Temperature", "Value": "96,0 °C"}]}]}
    gpu = {"Text": "RTX", "ImageURL": "images_icon/nvidia.png", "Children": [
        {"Text": "GPU Core", "Type": "Temperature", "Value": "99,0 °C"},
        {"Text": "Core", "Type": "Temperature", "Value": "98,0 °C"}]}
    assert temp_lhm_json({"Children": [cpu, gpu]}) == 55
    assert temp_lhm_json({"Children": [gpu]}) is None


def _http(handler_body):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            handler_body(self)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/data.json"


def _resp(h, corpo: bytes):
    h.send_response(200)
    h.send_header("Content-Length", str(len(corpo)))
    h.end_headers()
    h.wfile.write(corpo)


def test_lhm_ok_json_aninhado_corpo_enorme_e_lento():
    modo = {"m": "ok"}

    def h(r):
        if modo["m"] == "ok":
            _resp(r, json.dumps({"Text": "CPU", "Children": [{"Text": "CPU Package", "Type": "Temperature", "Value": "47,0 °C"}]}).encode())
        elif modo["m"] == "fundo":
            _resp(r, b'{"Children":[' * 5000 + b"]}" * 5000)
        elif modo["m"] == "enorme":
            _resp(r, b'{"Text":"CPU","x":"' + b"a" * 3_000_000 + b'"}')
        else:  # gota a gota: cada leitura chega antes do timeout do socket
            r.send_response(200)
            r.send_header("Content-Length", "100000")
            r.end_headers()
            try:
                for _ in range(100):
                    r.wfile.write(b" ")
                    r.wfile.flush()
                    time.sleep(0.5)
            except OSError:
                pass

    srv, url = _http(h)
    try:
        lhm = vigia._LHM(url)
        assert lhm.ler() == 47
        for m in ("fundo", "enorme", "lento"):
            modo["m"], lhm.ts = m, 0.0
            ini = time.monotonic()
            assert lhm.ler() is None, m  # falha zera o cache (nao fica o 47 antigo)
            assert time.monotonic() - ini < 4.5, m
    finally:
        srv.shutdown()


def test_ciclo_grava_temp_mesmo_com_lhm_quebrado(ambiente):
    d, out, _ = ambiente
    srv, url = _http(lambda r: _resp(r, b'{"Children":[' * 5000 + b"]}" * 5000))
    try:
        v = Vigia({"OUT_DIR": str(out), "DOCKER_SOCK": str(d / "d.sock"), "SYSFS_ROOT": str(d / "sys"), "CLIPES_LHM_URL": url})
        v.ciclo()
    finally:
        srv.shutdown()
    assert (out / ".temp").read_text() == "sem-sensor\n"


def test_arquivos_hostis_em_out_nao_travam_e_usam_padrao(ambiente):
    d, out, novo = ambiente
    f = novo(mem=1024**3)
    os.mkfifo(out / ".vigia")
    (out / ".recursos").symlink_to("/dev/zero")
    (out / ".temp.log").symlink_to(out / "alvo")
    ini = time.monotonic()
    _vigia(d, out, 90).ciclo()  # padrao 1 85 75 -> pausa; .recursos ilegivel -> nada
    assert time.monotonic() - ini < 5 and len(f.posts("/pause")) == 1 and not f.posts("/update")
    assert not (out / "alvo").exists() and (out / ".temp").read_text() == "90\n"


def test_ler_pequeno_limita_e_tolera_lixo(tmp_path):
    (tmp_path / "gigante").write_text("a" * 10_000_000)
    (tmp_path / "bin").write_bytes(b"1 90 \xff\xfe")
    (tmp_path / "ok").write_text("1 90 70\n")
    assert len(vigia._ler_pequeno(tmp_path / "gigante")) == 256
    assert vigia._ler_pequeno(tmp_path / "bin").startswith("1 90")
    assert vigia._ler_pequeno(tmp_path / "ok") == "1 90 70\n"
    assert vigia._ler_pequeno(tmp_path / "nao") is None
    (tmp_path / "ln").symlink_to(tmp_path / "ok")  # symlink comum tambem e recusado
    assert vigia._ler_pequeno(tmp_path / "ln") is None


def test_gravar_temp_nao_segue_symlink_plantado(ambiente, monkeypatch):
    d, out, _ = ambiente
    alvo = d / "alvo"
    alvo.write_text("intacto")
    monkeypatch.setattr(vigia.secrets, "token_hex", lambda n: "fixo")
    (out / ".temp.tmp.fixo").symlink_to(alvo)
    v = _vigia(d, out, 50)
    v.gravar_temp("50")
    assert alvo.read_text() == "intacto" and not (out / ".temp").exists()
    (out / ".temp.tmp.fixo").unlink()  # sem o symlink, grava normal (nome sem pid previsivel)
    v.gravar_temp("50")
    assert (out / ".temp").read_text() == "50\n"


# --- fake Docker sobre socket unix ---------------------------------------------------------------


class FakeDocker:
    def __init__(self, sock: str, mem: int = 0, nano: int = 0, update_status: int = 200, paused: bool = False, uso: int = 0, pause_status: int = 204):
        self.reqs: list[tuple[str, str, dict | None]] = []
        self.paused, self.uso, self.pause_status = paused, uso, pause_status
        self.mem, self.nano, self.update_status = mem, nano, update_status
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
                pass

            def _send(self, st, obj=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(st)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _ler(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else None

            def do_GET(self):
                outer.reqs.append(("GET", self.path, None))
                if self.path.startswith("/containers/json"):
                    return self._send(200, [{"Id": CID}])
                if self.path == f"/containers/{CID}/json":
                    return self._send(200, {"HostConfig": {"Memory": outer.mem, "NanoCpus": outer.nano}, "State": {"Paused": outer.paused}})
                if self.path == f"/containers/{CID}/stats?stream=false":
                    return self._send(200, {"memory_stats": {"usage": outer.uso}})
                self._send(404, {"message": "no such container"})

            def do_POST(self):
                corpo = self._ler()
                outer.reqs.append(("POST", self.path, corpo))
                if self.path.endswith("/update"):
                    return self._send(outer.update_status, {"message": "recusado"} if outer.update_status != 200 else {})
                if self.path.endswith("/pause"):
                    outer.paused = outer.paused or outer.pause_status == 204
                    return self._send(outer.pause_status)
                if self.path.endswith("/unpause"):
                    outer.paused = False
                self._send(204)

        class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        self.srv = S(sock, H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def posts(self, fim: str) -> list:
        return [r for r in self.reqs if r[0] == "POST" and r[1].endswith(fim)]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def ambiente():
    d = Path(tempfile.mkdtemp(prefix="vg"))
    out = d / "out"
    out.mkdir()
    servidores: list[FakeDocker] = []

    def novo(**kw):
        f = FakeDocker(str(d / "d.sock"), **kw)
        servidores.append(f)
        return f

    yield d, out, novo
    for s in servidores:
        s.close()
    shutil.rmtree(d, ignore_errors=True)


def _vigia(d: Path, out: Path, temp: int | str, **extra) -> Vigia:
    env = {"OUT_DIR": str(out), "DOCKER_SOCK": str(d / "d.sock"), "SYSFS_ROOT": str(d / "sys"), **extra}
    v = Vigia(env)
    v.ler_temp = lambda: str(temp)
    return v


def test_pausa_acima_do_max_uma_vez_e_retoma_abaixo_do_min(ambiente):
    d, out, novo = ambiente
    f = novo()
    v = _vigia(d, out, 90)  # padrao 1 85 75
    v.ciclo()
    v.ciclo()
    assert len(f.posts("/pause")) == 1
    v.ler_temp = lambda: "80"  # entre MIN e MAX: nada
    v.ciclo()
    assert len(f.posts("/pause")) == 1 and not f.posts("/unpause")
    v.ler_temp = lambda: "70"
    v.ciclo()
    assert len(f.posts("/unpause")) == 1
    assert (out / ".temp").read_text() == "70\n"


def test_vigia_novo_com_container_ja_pausado_despausa_se_frio(ambiente):
    d, out, novo = ambiente
    f = novo(paused=True)
    _vigia(d, out, 60).ciclo()  # vigia reiniciado: sem memoria do pause anterior
    assert len(f.posts("/unpause")) == 1 and not f.paused


def test_vigia_novo_com_container_pausado_e_quente_mantem_sem_erro(ambiente):
    d, out, novo = ambiente
    f = novo(paused=True)
    v = _vigia(d, out, 90)
    v.ciclo()
    v.ciclo()
    assert f.paused and not f.posts("/unpause") and not f.posts("/pause")
    v.ler_temp = lambda: "70"  # depois esfria: retoma pelo MIN
    v.ciclo()
    assert not f.paused


def test_pause_409_conta_como_pausado_por_nos(ambiente):
    d, out, novo = ambiente
    f = novo(pause_status=409)
    v = _vigia(d, out, 90)
    v.ciclo()
    assert v.pausados == {CID}


def test_recursos_manda_swap_2x_e_nao_reduz_abaixo_do_uso(ambiente, capsys):
    d, out, novo = ambiente
    f = novo(mem=4 * 1024**3, uso=3 * 1024**3)
    (out / ".recursos").write_text("2g -\n")  # reducao abaixo do uso atual: adia
    v = _vigia(d, out, 50)
    v.ciclo()
    v.ciclo()
    assert not f.posts("/update")
    assert capsys.readouterr().out.count("adiado: uso atual") == 1
    f.uso = 1024**3  # uso caiu: aplica
    v.ciclo()
    (_, _, corpo), = f.posts("/update")
    assert corpo == {"Memory": 2 * 1024**3, "MemorySwap": 4 * 1024**3}


def test_entre_min_e_max_nao_faz_nada(ambiente):
    d, out, novo = ambiente
    f = novo()
    _vigia(d, out, 80).ciclo()
    assert not f.posts("/pause") and not f.posts("/unpause")


def test_ativo_zero_nao_pausa_mas_grava_temp(ambiente):
    d, out, novo = ambiente
    f = novo()
    (out / ".vigia").write_text("0 85 75\n")
    _vigia(d, out, 99).ciclo()
    assert not f.posts("/pause")
    assert (out / ".temp").read_text() == "99\n"


def test_vigia_invalido_usa_padrao(ambiente):
    d, out, novo = ambiente
    f = novo()
    (out / ".vigia").write_text("1 50 75\n")  # max < 60 e min >= max: invalido
    _vigia(d, out, 80).ciclo()
    assert not f.posts("/pause")


def test_recursos_difere_aplica_update(ambiente):
    d, out, novo = ambiente
    f = novo(mem=1024**3, nano=int(1e9))
    (out / ".recursos").write_text("4g 2\n")
    _vigia(d, out, 50).ciclo()
    (_, _, corpo), = f.posts("/update")
    assert corpo == {"Memory": 4294967296, "MemorySwap": 2 * 4294967296, "NanoCpus": 2_000_000_000}


def test_recursos_igual_ou_traco_nao_atualiza(ambiente):
    d, out, novo = ambiente
    f = novo(mem=4294967296, nano=2_000_000_000)
    for txt in ("4g 2\n", "- -\n", "- 2\n", "4g -\n", "lixo\n"):
        (out / ".recursos").write_text(txt)
        _vigia(d, out, 50).ciclo()
    assert not f.posts("/update")


def test_recursos_asterisco_vira_tamanho_do_host_e_nao_repete(ambiente, monkeypatch):
    d, out, novo = ambiente
    monkeypatch.setattr(vigia, "_mem_host", lambda: 8 * 1024**3)
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    f = novo(mem=1024**3, nano=int(1e9))
    (out / ".recursos").write_text("* *\n")
    _vigia(d, out, 50).ciclo()
    (_, _, corpo), = f.posts("/update")
    assert corpo == {"Memory": 8 * 1024**3, "MemorySwap": 16 * 1024**3, "NanoCpus": 8_000_000_000}
    f.mem, f.nano = 8 * 1024**3, 8_000_000_000  # ja aplicado
    _vigia(d, out, 50).ciclo()
    assert len(f.posts("/update")) == 1


def test_update_recusado_loga_uma_vez_e_tenta_de_novo(ambiente, capsys):
    d, out, novo = ambiente
    f = novo(mem=1024**3, nano=0, update_status=400)
    (out / ".recursos").write_text("512m -\n")
    v = _vigia(d, out, 50)
    v.ciclo()
    v.ciclo()
    assert len(f.posts("/update")) == 2  # retenta
    assert capsys.readouterr().out.count("update recusado") == 1  # sem spam


def test_sem_socket_so_grava_temp(ambiente, capsys):
    d, out, _ = ambiente
    v = _vigia(d, out, 61)
    v.ciclo()
    v.ciclo()
    assert (out / ".temp").read_text() == "61\n"
    assert capsys.readouterr().out.count("socket do Docker ausente") == 1


def test_sigterm_despausa_o_que_pausou(ambiente):
    d, out, novo = ambiente
    f = novo()
    env = {**os.environ, "OUT_DIR": str(out), "DOCKER_SOCK": str(d / "d.sock"), "SYSFS_ROOT": str(d / "sys"), "VIGIA_INTERVALO_S": "0.1"}
    for k in ("CLIPES_VIGIA_HOST", "COMPOSE_PROJECT_NAME"):
        env.pop(k, None)
    _hwmon(d / "sys", 0, "k10temp", 95000)
    p = subprocess.Popen([sys.executable, "-m", "clipes.vigia"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if f.posts("/pause"):
                break
            time.sleep(0.1)
        assert f.posts("/pause")
        p.send_signal(signal.SIGTERM)
        assert p.wait(10) == 0
    finally:
        p.kill()
    assert len(f.posts("/unpause")) == 1


def test_ocioso_com_outro_escritor_e_volta_quando_ele_para(ambiente):
    d, out, novo = ambiente
    f = novo()
    relogio = [1000.0]
    v = _vigia(d, out, 90)
    v.agora = lambda: relogio[0]
    v.ciclo()  # sozinho: ativo, pausa e grava o .temp
    assert len(f.posts("/pause")) == 1 and v.pausados == {CID}
    # outro escritor (ex.: ./vigia-temp) grava o .temp "agora": mtime recente e ino/mtime diferentes dos nossos
    (out / ".temp.outro").write_text("50\n")
    os.replace(out / ".temp.outro", out / ".temp")
    os.utime(out / ".temp", (relogio[0], relogio[0]))
    v.ciclo()
    assert v.ocioso and len(f.posts("/unpause")) == 1  # despausou o que ele pausou
    assert (out / ".temp").read_text() == "50\n"  # nao sobrescreve
    relogio[0] += 10
    v.ciclo()
    assert v.ocioso and (out / ".temp").read_text() == "50\n"
    relogio[0] += 6  # >15 s sem o outro escrever: volta sozinho
    v.ciclo()
    assert not v.ocioso and (out / ".temp").read_text() == "90\n" and len(f.posts("/pause")) == 2
    v.ciclo()  # o mtime agora e o proprio: segue ativo
    assert not v.ocioso


def test_host_env_decide_a_cada_ciclo(ambiente):
    d, out, novo = ambiente
    f = novo()
    v = _vigia(d, out, 50, CLIPES_VIGIA_HOST="1")
    v.ciclo()
    assert v.ocioso and f.reqs == [] and not (out / ".temp").exists()
    v.env.pop("CLIPES_VIGIA_HOST")
    v.ciclo()
    assert not v.ocioso and (out / ".temp").exists()


def test_vigia_host_nao_toca_socket_nem_temp(ambiente):
    d, out, novo = ambiente
    f = novo()
    env = {**os.environ, "OUT_DIR": str(out), "DOCKER_SOCK": str(d / "d.sock"), "CLIPES_VIGIA_HOST": "1"}
    p = subprocess.Popen([sys.executable, "-m", "clipes.vigia"], cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
    try:
        time.sleep(1)
        p.send_signal(signal.SIGTERM)
        saida, _ = p.communicate(timeout=10)
    finally:
        p.kill()
    assert "vigia do host ativo" in saida
    assert f.reqs == [] and not (out / ".temp").exists()


# --- compose -------------------------------------------------------------------------------------


def _blocos() -> dict[str, str]:
    texto = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    blocos, atual = {}, None
    for ln in texto.splitlines():
        if ln.startswith("  ") and not ln.startswith("   ") and ln.strip().endswith(":") and not ln.strip().startswith("#"):
            atual = ln.strip()[:-1]
            blocos[atual] = ""
        elif atual:
            blocos[atual] += ln + "\n"
    return blocos


def test_compose_so_o_vigia_tem_o_socket_e_e_endurecido():
    b = _blocos()
    assert "docker.sock" in b["vigia"]
    for svc in ("clipes", "bot", "test"):
        assert "docker.sock" not in b[svc]
    for trecho in ("read_only: true", "cap_drop: [ALL]", "cap_add: [DAC_OVERRIDE, CHOWN]", "no-new-privileges:true", "- /tmp", "mem_limit: 128m", "cpus: 0.25",
                   "host.docker.internal:host-gateway", "restart: unless-stopped", "CLIPES_VIGIA_HOST: ${CLIPES_VIGIA_HOST:-}"):
        assert trecho in b["vigia"], trecho
    assert "ports:" not in b["vigia"]
    assert "depends_on:\n      - vigia" in b["clipes"]
    assert "condition:" not in b["clipes"]


def test_compose_vigia_sem_segredos():
    b = _blocos()["vigia"]
    for seg in ("DISCORD", "GEMINI", "MONGO", "TOKEN", "SECRET", "CLIENT", "/config", "env_file", "clipes_models"):
        assert seg not in b, seg


def test_compose_valido():
    if not shutil.which("docker"):
        pytest.skip("sem docker")
    assert subprocess.run(["docker", "compose", "config", "-q"], cwd=ROOT, capture_output=True).returncode == 0


def test_clipar_exporta_vigia_host():
    assert "CLIPES_VIGIA_HOST=1" in (ROOT / "clipar").read_text(encoding="utf-8")
