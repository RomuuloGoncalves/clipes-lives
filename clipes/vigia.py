"""Vigia termico e de recursos como servico do compose (`python -m clipes.vigia`).

Equivale ao `./vigia-temp` + a releitura de `.recursos` do `./clipar`, mas dentro do Docker, para a config
do painel funcionar com `docker compose run --rm clipes servico` direto (inclusive no Windows).
So stdlib: fala com o Docker Engine pelo socket unix (http.client).

- Temperatura -> `$OUT_DIR/.temp` (inteiro ou `sem-sensor`) a cada ciclo; `.vigia` ("ativo max min") manda
  pausar o contêiner `clipes` acima de MAX e retomar abaixo de MIN.
- `.recursos` ("mem cpus") -> `POST /containers/{id}/update` ao vivo (sem reiniciar).
- Ocioso (decidido a cada ciclo) se `CLIPES_VIGIA_HOST=1` (./clipar) ou se outro escritor gravou o `.temp` nos ultimos 15 s;
  volta a ativo sozinho quando o outro para.

O `update` do Docker trata Memory=0 e NanoCpus=0 como "sem mudanca" (nao remove o limite): "*" (sem limite)
vira MemTotal / nproc do host.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import secrets
import signal
import socket
import stat
import sys
import threading
import time
import urllib.parse
from pathlib import Path

CORES_HWMON = ("k10temp", "zenpower", "coretemp", "cpu_thermal")
SEM_SENSOR = "sem-sensor"
_MEM_RE = re.compile(r"(\d+)([mg])")
_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_SENSOR_RE = re.compile(r"CPU|Core|Package|Tctl|Tdie", re.I)
_NAO_CPU_RE = re.compile(r"GPU|Distance|TjMax", re.I)
LABEL_PROJ = "com.docker.compose.project"
LABEL_SVC = "com.docker.compose.service"


def _log(msg: str) -> None:
    print(f"[vigia] {msg}", flush=True)


# --- temperatura ------------------------------------------------------------------------------


def _ler_milli(p: Path) -> int | None:
    try:
        raw = p.read_text().strip()
        return int(raw) // 1000 if re.fullmatch(r"-?\d+", raw) else None
    except OSError:
        return None


def temp_sysfs(sysfs: Path) -> int | None:
    """hwmon (k10temp|zenpower|coretemp|cpu_thermal), depois thermal_zone; maior valor (mesma regra do vigia-temp)."""
    vals: list[int] = []
    for h in sorted((sysfs / "class" / "hwmon").glob("hwmon*")):
        try:
            nome = (h / "name").read_text().strip()
        except OSError:
            continue
        if nome in CORES_HWMON:
            vals += [v for f in sorted(h.glob("temp*_input")) if (v := _ler_milli(f)) is not None]
    if not vals:
        vals = [v for z in sorted((sysfs / "class" / "thermal").glob("thermal_zone*")) if (v := _ler_milli(z / "temp")) is not None]
    return max(vals) if vals else None


def temp_lhm_json(no: object) -> int | None:
    """Maior sensor Temperature de CPU da arvore do LibreHardwareMonitor ("45,0 °C"); ignora GPU e "Distance to TjMax"."""
    achados: list[float] = []

    def anda(n: object, cpu: bool) -> None:
        if not isinstance(n, dict):
            return
        cpu = cpu or "cpu" in str(n.get("ImageURL", "")).lower() or bool(re.search(r"\bCPU\b", str(n.get("Text", "")), re.I))
        nome = str(n.get("Text", ""))
        if n.get("Type") == "Temperature" and not _NAO_CPU_RE.search(nome) and (cpu or "cpu" in nome.lower()) and _SENSOR_RE.search(nome):
            m = _NUM_RE.search(str(n.get("Value", "")))
            if m:
                achados.append(float(m.group().replace(",", ".")))
        for filho in n.get("Children") or []:
            anda(filho, cpu)

    anda(no, False)
    return int(max(achados)) if achados else None


def _corta(sock: socket.socket | None) -> None:
    try:
        if sock:
            sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class _LHM:
    """Consulta HTTP com prazo TOTAL de 3 s, corpo <= 1 MB e cache de 15 s (inclusive de falha)."""

    def __init__(self, url: str) -> None:
        self.url, self.ts, self.val = url, 0.0, None

    def _consulta(self) -> int | None:
        u = urllib.parse.urlsplit(self.url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return None
        ini = time.monotonic()
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        c = cls(u.hostname, u.port, timeout=3)
        c.connect()
        # o shutdown do socket interrompe qualquer leitura travada: o prazo vale para a consulta toda
        timer = threading.Timer(max(0.05, 3 - (time.monotonic() - ini)), _corta, (c.sock,))
        timer.start()
        try:
            c.request("GET", (u.path or "/") + (f"?{u.query}" if u.query else ""))
            r = c.getresponse()
            corpo = r.read(1_000_000)
            return temp_lhm_json(json.loads(corpo)) if r.status == 200 else None
        finally:
            timer.cancel()
            c.close()

    def ler(self) -> int | None:
        agora = time.monotonic()
        if self.ts and agora - self.ts < 15:
            return self.val
        self.ts = agora
        try:
            self.val = self._consulta()
        except Exception:  # noqa: BLE001  RecursionError, JSON invalido, timeout...: sem valor velho
            self.val = None
        return self.val


# --- Docker Engine API ------------------------------------------------------------------------


class _Unix(http.client.HTTPConnection):
    def __init__(self, path: str) -> None:
        super().__init__("docker", timeout=5)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


class Docker:
    def __init__(self, sock: str) -> None:
        self.sock = sock

    def existe(self) -> bool:
        return os.path.exists(self.sock)

    def req(self, metodo: str, caminho: str, corpo: dict | None = None) -> tuple[int, object]:
        c = _Unix(self.sock)
        try:
            dados = json.dumps(corpo) if corpo is not None else None
            c.request(metodo, caminho, dados, {"Content-Type": "application/json"} if dados else {})
            r = c.getresponse()
            raw = r.read()
            try:
                return r.status, json.loads(raw) if raw else None
            except ValueError:
                return r.status, None
        finally:
            c.close()


# --- recursos ---------------------------------------------------------------------------------


def parse_recursos(texto: str, mem_host: int, cpus_host: float) -> tuple[int | None, float | None]:
    """".recursos" -> (bytes, cpus); None = nao mexer ("-" ou invalido); "*"/0 = sem limite = tamanho do host."""
    partes = texto.split()
    mem_s, cpu_s = (partes + ["-", "-"])[:2]
    mem = cpus = None
    if mem_s == "*":
        mem = mem_host
    elif m := _MEM_RE.fullmatch(mem_s.lower()):
        mem = int(m[1]) * (1024 if m[2] == "g" else 1) * 1024 * 1024 or mem_host
    if cpu_s == "*":
        cpus = cpus_host
    elif re.fullmatch(r"\d+(\.\d+)?", cpu_s):
        cpus = float(cpu_s) or cpus_host
    return mem, cpus


def _difere(alvo: float | None, atual: float, host: float, tol: float) -> bool:
    """Precisa de update? Sem limite (atual 0) so difere de um alvo menor que o host."""
    if not alvo:
        return False
    if alvo >= host * 0.99:  # "*": o update nao remove limite, entao "sem limite" = tamanho do host
        return 0 < atual < host * 0.99
    return atual == 0 or abs(alvo - atual) > tol


def _mem_host() -> int:
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemTotal:"):
                return int(ln.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _ler_pequeno(p: Path) -> str | None:
    """/out e controlado pelo clipes (nao confiavel): so arquivo regular, sem seguir symlink, sem travar em FIFO, <= 256 bytes."""
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        return os.read(fd, 256).decode("utf-8", "replace") if stat.S_ISREG(os.fstat(fd).st_mode) else None
    except OSError:
        return None
    finally:
        os.close(fd)


# --- vigia ------------------------------------------------------------------------------------


class Vigia:
    def __init__(self, env: dict[str, str]) -> None:
        self.out = Path(env.get("OUT_DIR") or "/out")
        self.sysfs = Path(env.get("SYSFS_ROOT") or "/sys")
        self.docker = Docker(env.get("DOCKER_SOCK") or "/var/run/docker.sock")
        self.lhm = _LHM(env.get("CLIPES_LHM_URL") or "http://host.docker.internal:8085/data.json")
        self.env = env
        self.intervalo = float(env.get("VIGIA_INTERVALO_S") or 5)
        self.uid, self.gid = (int(env[k]) if (env.get(k) or "").isdigit() else -1 for k in ("HOST_UID", "HOST_GID"))
        self.pausados: set[str] = set()
        self.iniciou = False
        self.ocioso = False
        self.meu_temp: tuple[int, int] | None = None  # (mtime_ns, ino) da ultima gravacao propria do .temp
        self.agora = time.time
        self.avisos: set[str] = set()
        self.projeto: str | None = None
        self.parar = threading.Event()

    def aviso(self, chave: str, msg: str | None = None) -> None:
        if chave not in self.avisos:
            self.avisos.add(chave)
            _log(msg or chave)

    # arquivos em $OUT_DIR (dono HOST_UID:GID, best-effort, para o usuario continuar mexendo neles)
    def _dono(self, fd: int) -> None:
        if self.uid >= 0 or self.gid >= 0:
            try:
                os.fchown(fd, self.uid, self.gid)
            except OSError:
                pass

    def gravar_temp(self, t: str) -> None:
        tmp = self.out / f".temp.tmp.{secrets.token_hex(8)}"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)  # nunca segue symlink plantado
            try:
                os.write(fd, (t + "\n").encode())
                self._dono(fd)
                st = os.fstat(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self.out / ".temp")
            self.meu_temp = (st.st_mtime_ns, st.st_ino)
        except OSError as e:
            self.aviso(f"temp:{e}", f"nao consegui gravar .temp: {e}")

    def gravar_log(self, linha: str) -> None:
        log = self.out / ".temp.log"
        try:
            fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    return
                if st.st_size > 1_000_000:  # ponytail: 1 geracao, sem rotacao maior
                    os.replace(log, self.out / ".temp.log.1")
                    os.close(fd)
                    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644)
                    st = os.fstat(fd)
                if st.st_size == 0:
                    self._dono(fd)
                os.write(fd, f"{time.strftime('%H:%M:%S')} {linha}\n".encode())
            finally:
                os.close(fd)
        except OSError:
            pass

    def ler_temp(self) -> str:
        t = temp_sysfs(self.sysfs)
        if t is None:
            t = self.lhm.ler()
        if t is None:
            self.aviso(
                "sem-sensor",
                f"sem sensor de temperatura: sem hwmon e o LibreHardwareMonitor em {self.lhm.url} nao respondeu "
                "(abra-o como administrador, Options > Remote Web Server > Run na porta 8085, e libere a porta no firewall)",
            )
        return SEM_SENSOR if t is None else str(t)

    def cfg(self) -> tuple[int, int, int]:
        e = self.env
        padrao = (int(e.get("CLIPES_VIGIA") or 1), int(e.get("CLIPES_TEMP_MAX") or 85), int(e.get("CLIPES_TEMP_MIN") or 75))
        try:
            p = (_ler_pequeno(self.out / ".vigia") or "").split()[:3]
            a, mx, mn = (int(x) for x in p if x.isdigit())
        except ValueError:
            return padrao
        if len(p) == 3 and a <= 1 and 60 <= mx <= 100 and 40 <= mn <= 95 and mn < mx:
            return a, mx, mn
        return padrao

    # Docker
    def containers(self) -> list[str]:
        if self.projeto is None:
            self.projeto = self.env.get("COMPOSE_PROJECT_NAME") or "clipes-lives"
            try:  # o proprio vigia e do mesmo projeto: o label dele e a fonte mais fiel
                st, d = self.docker.req("GET", f"/containers/{socket.gethostname()}/json")
                if st == 200 and isinstance(d, dict):
                    self.projeto = d["Config"]["Labels"].get(LABEL_PROJ) or self.projeto
            except (OSError, KeyError, AttributeError):
                pass
        f = urllib.parse.quote(json.dumps({"label": [f"{LABEL_PROJ}={self.projeto}", f"{LABEL_SVC}=clipes"]}))
        st, d = self.docker.req("GET", f"/containers/json?filters={f}")
        # a imagem traz labels de compose: nunca tratar o proprio vigia (hostname = id curto) como alvo
        return [c["Id"] for c in d if not c["Id"].startswith(socket.gethostname())] if st == 200 and isinstance(d, list) else []

    def pausar(self, ids: list[str], acao: str) -> None:
        for i in ids:
            st, _ = self.docker.req("POST", f"/containers/{i}/{acao}")
            if acao == "pause" and st in (204, 409):  # 409: ja estava pausado (conta como nosso)
                self.pausados.add(i)
        if acao == "unpause":
            self.pausados.difference_update(ids)

    def despausar_tudo(self) -> None:
        if self.pausados and self.docker.existe():
            try:
                self.pausar(list(self.pausados), "unpause")
            except OSError:
                pass

    def esta_pausado(self, i: str) -> bool:
        st, d = self.docker.req("GET", f"/containers/{i}/json")
        return st == 200 and isinstance(d, dict) and bool((d.get("State") or {}).get("Paused"))

    def termica(self, t: str, ids: list[str]) -> None:
        ativo, mx, mn = self.cfg()
        self.pausados = {i for i in ids if self.esta_pausado(i)}  # estado real: sobrevive a reinicio do vigia
        primeira, self.iniciou = not self.iniciou, True
        if self.pausados and (t == SEM_SENSOR or not ativo or (primeira and int(t) <= mx)):
            self.pausar(sorted(self.pausados), "unpause")
        elif t == SEM_SENSOR or not ativo:
            return
        elif not self.pausados and int(t) >= mx:
            self.pausar(ids, "pause")
        elif self.pausados and int(t) <= mn:
            self.pausar(sorted(self.pausados), "unpause")
        elif self.pausados:  # contêiner novo durante a pausa
            self.pausar([i for i in ids if i not in self.pausados], "pause")

    def uso_mem(self, i: str) -> int:
        st, d = self.docker.req("GET", f"/containers/{i}/stats?stream=false")
        ms = d.get("memory_stats") if st == 200 and isinstance(d, dict) else None
        return int(ms.get("usage") or 0) if isinstance(ms, dict) else 0

    def recursos(self, ids: list[str]) -> None:
        texto = _ler_pequeno(self.out / ".recursos")
        if texto is None:
            return
        mem_h, cpus_h = _mem_host(), float(os.cpu_count() or 1)
        mem, cpus = parse_recursos(texto, mem_h, cpus_h)
        for i in ids:
            st, d = self.docker.req("GET", f"/containers/{i}/json")
            if st != 200 or not isinstance(d, dict):
                continue
            hc = d.get("HostConfig", {})
            atual_mem, atual_cpus = hc.get("Memory", 0), hc.get("NanoCpus", 0) / 1e9
            corpo: dict[str, int] = {}
            if _difere(mem, atual_mem, mem_h, mem * 0.01 if mem else 0):
                uso = self.uso_mem(i) if atual_mem == 0 or mem < atual_mem else 0  # reducao: nao abaixo do uso
                if mem < uso * 1.1:
                    self.aviso(f"adiado:{i}:{mem}", f"adiado: uso atual {uso} > novo limite {mem} em {i[:12]}; tento no proximo ciclo")
                else:
                    corpo |= {"Memory": mem, "MemorySwap": 2 * mem}  # mesmo padrao do Docker (swap = 2x)
            if _difere(cpus, atual_cpus, cpus_h, 0.05):
                corpo["NanoCpus"] = int(cpus * 1e9)
            if not corpo:
                continue
            chave = f"{i}:{sorted(corpo.items())}"
            st, r = self.docker.req("POST", f"/containers/{i}/update", corpo)
            if st == 200:
                self.avisos.discard(chave)
                _log(f"recursos aplicados em {i[:12]}: mem={corpo.get('Memory', '-')} nanocpus={corpo.get('NanoCpus', '-')}")
            else:
                msg = r.get("message") if isinstance(r, dict) else ""
                self.aviso(chave, f"update recusado ({st}) em {i[:12]}: {msg}; tento no proximo ciclo")

    def outro_escritor(self) -> str | None:
        """Por que ficar ocioso: CLIPES_VIGIA_HOST=1 ou .temp gravado por outro escritor nos ultimos 15 s."""
        if self.env.get("CLIPES_VIGIA_HOST"):
            return "vigia do host ativo: este fica ocioso"
        try:
            st = os.lstat(self.out / ".temp")
        except OSError:
            return None
        if (st.st_mtime_ns, st.st_ino) != self.meu_temp and self.agora() - st.st_mtime < 15:
            return "outro escritor do .temp ativo: este fica ocioso"
        return None

    def ciclo(self) -> None:
        motivo = self.outro_escritor()
        if motivo:
            if not self.ocioso:
                self.ocioso = True
                _log(motivo)
                self.despausar_tudo()
            return
        if self.ocioso:
            self.ocioso = False
            _log("o outro escritor parou: vigia ativo de novo")
        self.out.mkdir(parents=True, exist_ok=True)
        t = self.ler_temp()
        self.gravar_temp(t)
        ativo, _, _ = self.cfg()
        self.gravar_log(f"{t + 'C' if t != SEM_SENSOR else t} pausado={int(bool(self.pausados))} ativo={ativo}")
        if not self.docker.existe():
            self.aviso("socket", "socket do Docker ausente: so gravo .temp (sem pausa nem limites)")
            return
        try:
            ids = self.containers()
            self.termica(t, ids)
            self.recursos(ids)
        except (OSError, http.client.HTTPException) as e:
            self.aviso(f"docker:{type(e).__name__}", f"falha ao falar com o Docker: {e}; tento no proximo ciclo")

    def rodar(self) -> None:
        try:
            while not self.parar.is_set():
                try:
                    self.ciclo()
                except Exception as e:  # noqa: BLE001  o laco nunca cai
                    self.aviso(f"ciclo:{type(e).__name__}", f"erro no ciclo: {e!r}")
                self.parar.wait(self.intervalo)
        finally:
            self.despausar_tudo()


def main(env: dict[str, str] | None = None) -> int:
    v = Vigia(dict(os.environ if env is None else env))
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: v.parar.set())
    v.rodar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
