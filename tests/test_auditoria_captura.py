"""Testes da auditoria para captura e resiliencia em clipes/acompanhar.py (C-01, M-10, B-25, M-09, B-01)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import mongomock
import pymongo.errors

from clipes import acompanhar, monitor
from clipes.coord import Coord
from test_acompanhar import B, Clock, FakeMultiPool, FakeProc


class RestartablePool(FakeMultiPool):
    """Fake pool que suporta `restart_on_demand` sem apagar o buffer e controla falhas de gravacao."""

    def __init__(self, clock: Clock, *, restart_results: list[bool] | None = None):
        super().__init__(clock, {})
        self.recording = False
        self.restart_results = list(restart_results or [])
        self.restart_calls: list[tuple[str, float]] = []
        self.seg_list: list[int] = []
        self.holds: dict[str, float] = {}

    def start_on_demand(self, ch: str) -> bool:
        self.started_order.append(ch)
        self.active.add(ch)
        self.recording = True
        self.first[ch] = int(self.clock.t) // 10 * 10
        self.seg_list = [self.first[ch]]
        return True

    def restart_on_demand(self, ch: str) -> bool:
        self.restart_calls.append((ch, self.clock.t))
        ok = self.restart_results.pop(0) if self.restart_results else True
        if ok:
            self.active.add(ch)
            self.recording = True
        return ok

    def is_recording(self, ch: str) -> bool:
        return ch in self.active and self.recording

    def segments(self, ch: str):
        return [(s, None) for s in self.seg_list]

    def hold(self, ch: str, ts: float) -> None:
        self.holds[ch] = ts

    def release_hold(self, ch: str) -> None:
        self.holds.pop(ch, None)


def _make_session(
    tmp_path: Path,
    clock: Clock,
    pool,
    *,
    is_live,
    alvo: acompanhar.Alvo | None = None,
    captura_morta_s: float | None = None,
) -> acompanhar.LiveSession:
    out_root = tmp_path / "out"
    out_root.mkdir(parents=True, exist_ok=True)
    queue = monitor.DiskQueue(out_root / "queue")
    return acompanhar.LiveSession(
        alvo or acompanhar.parse_alvo("gaules"),
        out_root,
        queue,
        pool,
        lambda d, o: {"clips": []},
        is_live=is_live,
        now=clock.now,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        captura_morta_s=captura_morta_s,
    )


# ---- C-01: reinicio com backoff, sinal unico "captura", offline confirmado, saida de seguranca ----


def test_c01_gravador_cai_com_live_ativa_reinicia_com_backoff_e_zera_ao_voltar(tmp_path):
    clock = Clock(B + 10)
    # 6 falhas seguidas (testa 5, 10, 20, 40, 60, 60), 7a tentativa funciona; depois cai de novo
    pool = RestartablePool(clock, restart_results=[False, False, False, False, False, False, True, False, True])
    sess = _make_session(tmp_path, clock, pool, is_live=lambda u: True)
    sess.start()

    # Tudo normal no inicio
    assert sess.tick(clock.t) is True
    assert pool.restart_calls == []

    # Gravador cai em t = B + 20 enquanto is_live=True
    pool.recording = False
    for sec in range(20, 230):
        clock.t = B + sec
        assert sess.tick(clock.t) is True
        if pool.recording and not any(s >= int(clock.t) - 10 for s in pool.seg_list):
            pool.seg_list.append(int(clock.t) // 10 * 10)

    # Tentativas: B+20 (+5->25), B+25 (+10->35), B+35 (+20->55), B+55 (+40->95), B+95 (+60->155), B+155 (+60->215), B+215 (ok!)
    expected_first_wave = [B + 20, B + 25, B + 35, B + 55, B + 95, B + 155, B + 215]
    assert [t for _, t in pool.restart_calls] == expected_first_wave
    assert pool.recording is True
    assert sess.live_ended is False

    # Novo segmento e tempo de gravacao estavel zeraram o backoff; segunda queda volta a tentar com +5 s
    clock.t = B + 230
    pool.seg_list.append(B + 230)
    assert sess.tick(clock.t) is True

    pool.recording = False
    for sec in range(240, 250):
        clock.t = B + sec
        assert sess.tick(clock.t) is True

    second_wave = [t for _, t in pool.restart_calls[len(expected_first_wave):]]
    assert second_wave == [B + 240, B + 245]
    assert pool.recording is True


def test_c01_exited_e_silent_sao_um_sinal_so_e_saida_seguranca_encerra_sem_live_ended(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_LIVE_CAPTURA_MORTA_S", "500")
    # FakeMultiPool NAO tem restart_on_demand -> testa fallback sem quebrar
    pool = FakeMultiPool(clock, {"gaules": B + 20})
    sess = _make_session(tmp_path, clock, pool, is_live=lambda u: True)
    sess.start()

    # Em B+20 exited liga; em B+110 silent tambem liga; em B+400 (> LIVE_END_PERSIST_S=300) ainda NAO encerra
    for t in (B + 20, B + 110, B + 320, B + 450, B + 519):
        clock.t = t
        assert sess.tick(t) is True
        assert list(sess.since.keys()) == ["captura"]

    # Ao atingir captura_morta_s (B+20 + 500 = B+520), encerra SEM marcar live_ended
    clock.t = B + 520
    assert sess.tick(clock.t) is False
    assert sess.live_ended is False


def test_c01_captura_morta_no_supervisor_nao_marca_ended_no_mongo(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_LIVE_CAPTURA_MORTA_S", "120")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-c01-morta", now=clock.now, lease_s=600)
    pool = FakeMultiPool(clock, {"gaules": B + 30})

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="c01-1",
        is_live=lambda u: True,  # plataforma diz que a live ainda esta ativa
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    sup.run()

    doc = db.lives.find_one({"_id": "twitch:gaules:c01-1"})
    assert doc is not None
    assert doc["status"] == "live"  # NAO vira ended em queda do gravador
    assert doc["owner"] is None
    assert doc["lease_until"] is None


def test_c01_erro_no_probe_nao_confirma_offline_nem_marca_ended(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_LIVE_CAPTURA_MORTA_S", "150")

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-c01-probe", now=clock.now, lease_s=600)
    pool = FakeMultiPool(clock, {"gaules": B + 20})

    calls = [0]

    def failing_probe(url: str) -> bool:
        calls[0] += 1
        if calls[0] == 1:
            return True  # abertura no _try_start_alvo
        raise RuntimeError("yt-dlp network timeout")

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="c01-2",
        is_live=failing_probe,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    sup.run()

    doc = db.lives.find_one({"_id": "twitch:gaules:c01-2"})
    assert doc is not None
    assert doc["status"] == "live"  # erro no probe nao conta como is_live=False
    assert doc["owner"] is None


def test_c01_offline_confirmado_encerra_com_live_ended_true_em_ambos_os_modos(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    # 1) offline + captura -> encerra imediato com live_ended=True e SEM chamar restart_on_demand
    clock1 = Clock(B + 10)
    pool1 = RestartablePool(clock1)
    is_live_val = [True]
    sess1 = _make_session(tmp_path / "m1", clock1, pool1, is_live=lambda u: is_live_val[0])
    sess1.start()
    assert sess1.tick(B + 10) is True

    is_live_val[0] = False
    pool1.recording = False
    clock1.t = B + 70
    assert sess1.tick(clock1.t) is False
    assert sess1.live_ended is True
    assert pool1.restart_calls == []  # nao tenta reiniciar se a plataforma confirmou offline

    # 2) offline sozinho (gravador ainda vivo) persistindo LIVE_END_PERSIST_S -> live_ended=True
    clock2 = Clock(B + 10)
    pool2 = RestartablePool(clock2)
    live2 = [True]
    sess2 = _make_session(tmp_path / "m2", clock2, pool2, is_live=lambda u: live2[0])
    sess2.start()
    assert sess2.tick(B + 10) is True

    live2[0] = False
    clock2.t = B + 70
    pool2.seg_list.append(B + 70)
    assert sess2.tick(clock2.t) is True
    assert "offline" in sess2.since and "captura" not in sess2.since

    # Ainda abaixo de LIVE_END_PERSIST_S (300 s)
    clock2.t = B + 369
    pool2.seg_list.append(B + 360)
    assert sess2.tick(clock2.t) is True

    # Atinge LIVE_END_PERSIST_S (70 + 300 = 370)
    clock2.t = B + 370
    pool2.seg_list.append(B + 370)
    assert sess2.tick(clock2.t) is False
    assert sess2.live_ended is True


def test_c01_integracao_real_pool_restart_on_demand_preserva_buffer(tmp_path):
    clock = Clock(B + 10)
    procs: list[FakeProc] = []

    def fake_popen(cmd, **kw):
        p = FakeProc(cmd, **kw)
        procs.append(p)
        return p

    buf_root = tmp_path / "buf"
    real_pool = monitor.RollingRecorderPool(buf_root, max_hot=0, max_on_demand=1, popen=fake_popen)
    sess = _make_session(tmp_path, clock, real_pool, is_live=lambda u: True)
    sess.start()

    seg = buf_root / sess.key / f"{B + 10}.ts"
    seg.write_bytes(b"segmento-existente")
    assert len(procs) == 2  # source + sink

    # Simula queda do processo fonte com live ainda ativa
    procs[0].returncode = 1
    assert real_pool.is_recording(sess.key) is False

    clock.t = B + 20
    assert sess.tick(clock.t) is True
    assert real_pool.is_recording(sess.key) is True
    assert len(procs) == 4  # reiniciou source + sink
    assert seg.exists()  # buffer preservado pelo restart_on_demand
    real_pool.stop_all()


def test_c01_backoff_e_since_captura_preservados_sem_segmento_novo_apos_restart(tmp_path):
    """Item 1: restart que fica vivo >=5s e cai sem gerar segmento nao zera backoff nem since['captura']."""
    clock = Clock(B + 10)
    # Todos os restarts "sobem" (True), mas o processo cai antes de gravar segmento novo
    pool = RestartablePool(clock, restart_results=[True, True, True, True, True])
    sess = _make_session(tmp_path, clock, pool, is_live=lambda u: True, captura_morta_s=120.0)
    sess.start()

    # 1a queda em B+20: restart ok (backoff +5 -> B+25), mas sem segmento novo `captura` continua em B+20
    pool.recording = False
    clock.t = B + 20
    assert sess.tick(clock.t) is True
    assert pool.recording is True
    assert sess.since.get("captura") == B + 20

    # Fica rodando 30s (t >= _next_restart_at=B+25) sem gerar segmento: nao pode zerar backoff nem limpar `captura`
    for sec in range(21, 50):
        clock.t = B + sec
        assert sess.tick(clock.t) is True
        assert sess.since.get("captura") == B + 20

    # Cai em B+50: 2a tentativa usa backoff 10s (-> B+60), nao 5s
    pool.recording = False
    clock.t = B + 50
    assert sess.tick(clock.t) is True
    assert pool.restart_calls == [("gaules", B + 20), ("gaules", B + 50)]
    assert sess.since.get("captura") == B + 20

    # Cai rapido em B+55 (< B+60): aguarda ate B+60 para tentar a 3a vez (backoff 20s -> B+80)
    pool.recording = False
    for sec in range(55, 61):
        clock.t = B + sec
        assert sess.tick(clock.t) is True
    assert pool.restart_calls == [("gaules", B + 20), ("gaules", B + 50), ("gaules", B + 60)]
    assert sess.since.get("captura") == B + 20

    # Continua sem segmento ate captura_morta_s (B+20 + 120 = B+140): encerra com live_ended=False
    clock.t = B + 140
    assert sess.tick(clock.t) is False
    assert sess.live_ended is False


def test_c01_pula_janela_periodica_durante_falha_de_captura_sem_segmento_novo(tmp_path, monkeypatch, capsys):
    """Item 3: durante captura morta, nao fecha janelas vazias/repetidas nem avanca hold."""
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    pool = FakeMultiPool(clock, {"gaules": B + 301})
    holds: list[float] = []
    orig_hold = pool.hold
    pool.hold = lambda ch, ts: (holds.append(ts), orig_hold(ch, ts))[1]

    sess = _make_session(tmp_path, clock, pool, is_live=lambda u: True, captura_morta_s=900.0)
    sess.start()
    assert holds == [B + 10]

    # B+300: captura normal fecha a 1a janela e avanca hold (ultimo seg = B+290)
    clock.t = B + 300
    assert sess.tick(clock.t) is True
    assert len(sess.jobs) == 1
    assert len(holds) == 2

    # Captura morre em B+301 sem gravar segmento novo (ultimo seg permanece B+290)
    # B+600 e B+900: captura em falha e sem segmento novo -> pula sem chamar fechar_janela nem avancar hold
    clock.t = B + 600
    assert sess.tick(clock.t) is True
    clock.t = B + 900
    assert sess.tick(clock.t) is True

    assert len(sess.jobs) == 1
    assert len(holds) == 2
    assert sess.next_e == B + 1200
    out = capsys.readouterr().out
    assert out.count("[live] janela pulada: captura sem segmento novo (gaules)") == 2

    # Se houver 1 segmento novo gravado apos a ultima janela (B+300.ts antes de morrer em B+320), fecha uma vez e pula a seguinte
    clock2 = Clock(B + 10)
    pool2 = FakeMultiPool(clock2, {"gaules": B + 320})
    sess2 = _make_session(tmp_path / "partial", clock2, pool2, is_live=lambda u: True, captura_morta_s=900.0)
    sess2.start()
    clock2.t = B + 300
    assert sess2.tick(clock2.t) is True
    clock2.t = B + 600
    assert sess2.tick(clock2.t) is True  # tinha B+300.ts novo -> fecha B+600
    clock2.t = B + 900
    assert sess2.tick(clock2.t) is True  # nenhum seg depois de B+300.ts -> pula B+900
    assert sess2.jobs == [f"gaules_{B + 300}", f"gaules_{B + 600}"]


# ---- M-10: fixar alvo.url para watch?v=<video_id> apos _probe_youtube ----------------------------


def test_m10_probe_youtube_fixa_url_no_video_id_para_gravador_is_live_e_meta(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")

    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    alvo = acompanhar.parse_alvo("https://www.youtube.com/@CanalTeste/live")

    probe_urls: list[str] = []
    is_live_urls: list[str] = []

    def fake_probe(url: str) -> dict:
        probe_urls.append(url)
        return {"id": "VidFixo123", "channel_id": "UC_FIXO", "uploader_id": "@CanalTeste", "is_live": True}

    def fake_is_live(url: str) -> bool:
        is_live_urls.append(url)
        return clock.t < B + 320

    pool = FakeMultiPool(clock, {"yt_VidFixo123": B + 320})
    sup = acompanhar.Supervisor(
        [alvo],
        out_root,
        queue_root=queue_root,
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        is_live=fake_is_live,
        probe=fake_probe,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    res = sup.run()

    expected_url = "https://www.youtube.com/watch?v=VidFixo123"
    assert res["status"] == "ended"
    assert probe_urls == ["https://www.youtube.com/@CanalTeste/live"]
    assert alvo.url == expected_url
    assert is_live_urls and all(u == expected_url for u in is_live_urls)

    # meta.json e job.json seguem a URL do video fixada
    job_dir = queue_root / "rejected" / f"yt_VidFixo123_{B + 300}"
    meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert meta["url"] == expected_url
    assert job["url"] == expected_url

    # Gravador criado pelo Supervisor tambem usa a URL fixada no video_id
    popen_cmds: list[list[str]] = []
    orig_pool_cls = monitor.RollingRecorderPool

    def fake_popen(cmd, **kwargs):
        popen_cmds.append(cmd)
        return FakeProc(cmd, **kwargs)

    class PopenSpyPool(orig_pool_cls):
        def __init__(self, *args, **kwargs):
            kwargs["popen"] = fake_popen
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(monitor, "RollingRecorderPool", PopenSpyPool)
    clock2 = Clock(B + 10)
    alvo2 = acompanhar.parse_alvo("https://www.youtube.com/@CanalTeste/live")
    sup2 = acompanhar.Supervisor(
        [alvo2],
        tmp_path / "out2",
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: clock2.t < B + 20,  # entra live, depois cai em offline
        probe=fake_probe,
        now=clock2.now,
        sleep=clock2.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    sup2.run()
    ytdlp_cmds = [c for c in popen_cmds if c and c[0] == "yt-dlp"]
    assert ytdlp_cmds and ytdlp_cmds[0][-1] == expected_url


# ---- B-25: limpar queue/.buffer ao criar pool proprio (e nao quando injetado) --------------------


def test_b25_limpa_buffer_ao_criar_pool_proprio_e_preserva_quando_injetado(tmp_path, monkeypatch):
    # 1) Pool proprio: limpa sobras de .buffer antes de instanciar RollingRecorderPool
    out1 = tmp_path / "out1"
    q1 = out1 / "queue"
    stale1 = q1 / ".buffer" / "gaules" / "1000.ts"
    stale1.parent.mkdir(parents=True)
    stale1.write_bytes(b"stale-ts")

    saw_stale_at_init = [None]
    orig_pool_cls = monitor.RollingRecorderPool

    class SpyPool(orig_pool_cls):
        def __init__(self, buf_root: Path, *args, **kwargs):
            saw_stale_at_init[0] = stale1.exists()
            super().__init__(buf_root, *args, **kwargs)

    monkeypatch.setattr(monitor, "RollingRecorderPool", SpyPool)
    sup1 = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        out1,
        queue_root=q1,
        is_live=lambda u: False,  # sai cedo apos criar o pool
    )
    sup1.run()
    assert saw_stale_at_init[0] is False
    assert not stale1.exists()

    # 2) Pool injetado: NAO apaga queue/.buffer
    out2 = tmp_path / "out2"
    q2 = out2 / "queue"
    stale2 = q2 / ".buffer" / "gaules" / "2000.ts"
    stale2.parent.mkdir(parents=True)
    stale2.write_bytes(b"injected-ts")

    clock = Clock(B + 10)
    pool = FakeMultiPool(clock, {"gaules": B + 20})
    sup2 = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        out2,
        queue_root=q2,
        pool=pool,
        is_live=lambda u: False,
        now=clock.now,
        sleep=clock.sleep,
    )
    sup2.run()
    assert stale2.exists()


# ---- M-09: hold(self.key, self.t0) em LiveSession.start() e uso de monitor.BUFFER_CAP_BYTES ------


def test_m09_start_faz_hold_em_t0_protegendo_primeira_janela_do_enforce_cap(tmp_path, monkeypatch):
    clock = Clock(B + 15)
    buf_root = tmp_path / "buf"
    real_pool = monitor.RollingRecorderPool(
        buf_root,
        max_hot=0,
        max_on_demand=2,
        popen=lambda cmd, **kw: FakeProc(cmd, **kw),
    )

    sess = _make_session(tmp_path, clock, real_pool, is_live=lambda u: True)
    sess.start()

    assert real_pool.holds[sess.key] == clock.t

    # Segmentos da primeira janela da sessao nova vs segmentos sem hold de outro canal
    own_seg = buf_root / sess.key / f"{B + 10}.ts"
    own_seg.write_bytes(b"a" * 500)
    other_dir = buf_root / "outro"
    other_dir.mkdir(parents=True)
    other_seg = other_dir / f"{B + 10}.ts"
    other_seg.write_bytes(b"b" * 500)

    real_pool.enforce_cap(100)  # teto apertado
    assert own_seg.exists()  # protegido pelo hold em t0
    assert not other_seg.exists()  # sem hold foi removido
    real_pool.stop_all()

    # Supervisor.run() repassa monitor.BUFFER_CAP_BYTES (sem duplicar constante)
    monkeypatch.setattr(monitor, "BUFFER_CAP_BYTES", 777_000)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    clock2 = Clock(B + 10)
    pool2 = FakeMultiPool(clock2, {"gaules": B + 320})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out_cap",
        pool=pool2,
        pipeline=lambda d, o: {"clips": []},
        is_live=lambda u: clock2.t < B + 320,
        now=clock2.now,
        sleep=clock2.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    sup.run()
    assert 777_000 in pool2.enforce_caps


# ---- B-01: replay_unsynced_jobs no inicio do run() e ao reconectar -------------------------------


def test_b01_replay_no_inicio_do_run_sincroniza_jobs_antigos(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    queue = monitor.DiskQueue(queue_root)

    # Job deixado em done/ com coord_synced=False de uma execucao offline anterior
    d = queue_root / ".staging" / f"gaules_{B - 300}"
    d.mkdir(parents=True)
    monitor._write_job(d, {
        "job_id": f"gaules_{B - 300}",
        "channel": "gaules",
        "live_id": "twitch:gaules:prev1",
        "mongo_job_id": f"twitch:gaules:prev1:{B - 300}",
        "window_start": B - 900,
        "window_end": B - 300,
        "z_score": 0.0,
        "created_at": B - 300,
        "clips": 2,
        "score": 0.85,
        "output_dir": str(out_root / "clips_prev"),
        "status": "pending",
        "coord_synced": False,
    })
    enq = queue.enqueue(d)
    done_dir = queue.complete(enq, "done", {"coord_synced": False})

    db = mongomock.MongoClient().db
    coord = Coord(db, "w-b01-init", now=clock.now, lease_s=120)

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        out_root,
        queue_root=queue_root,
        pool=FakeMultiPool(clock, {"gaules": B + 20}),
        coord=coord,
        is_live=lambda u: False,  # sai cedo apos inicializacao
        now=clock.now,
        sleep=clock.sleep,
    )
    sup.run()

    doc = db.jobs.find_one({"_id": f"twitch:gaules:prev1:{B - 300}"})
    assert doc is not None
    assert doc["status"] == "done"
    assert doc["result"]["clips"] == 2
    assert monitor._read_job(done_dir)["coord_synced"] is True


def test_b01_replay_ao_reconectar_e_erro_mongo_chama_on_mongo_err(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_BEAT_S", "20")

    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-b01-rec", now=clock.now, lease_s=600)

    # Derruba add_job na primeira janela (B+300) e depois derruba o primeiro replay apos reconexao
    orig_add_job = coord.add_job
    orig_replay = monitor.replay_unsynced_jobs
    replay_attempts: list[float] = []
    fail_replay_once = [True]

    def flaky_add_job(job_id, live_id, window, priority):
        if clock.t <= B + 310:
            raise pymongo.errors.AutoReconnect("mongo down on window 1")
        return orig_add_job(job_id, live_id, window, priority)

    def spy_replay(q, c, **kw):
        replay_attempts.append(clock.t)
        if clock.t >= B + 350 and fail_replay_once[0]:
            fail_replay_once[0] = False
            raise pymongo.errors.AutoReconnect("mongo blip during replay")
        return orig_replay(q, c, **kw)

    coord.add_job = flaky_add_job
    monkeypatch.setattr(monitor, "replay_unsynced_jobs", spy_replay)

    pool = FakeMultiPool(clock, {"gaules": B + 620})
    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        out_root,
        queue_root=queue_root,
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="b01-77",
        is_live=lambda u: clock.t < B + 620,
        now=clock.now,
        sleep=clock.sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    assert res["status"] == "ended"
    # 1o replay no inicio (B+10), 2o apos reconectar (falha -> backoff 60s), 3o apos novo backoff (sucesso!)
    assert len(replay_attempts) >= 3
    assert replay_attempts[2] - replay_attempts[1] >= monitor.MONGO_RECONNECT_S

    # Ambos os jobs sincronizados no Mongo com o live_id correto
    j1 = db.jobs.find_one({"_id": f"twitch:gaules:b01-77:{B + 300}"})
    j2 = db.jobs.find_one({"_id": f"twitch:gaules:b01-77:{B + 600}"})
    assert j1 is not None and j1["status"] == "rejected"
    assert j2 is not None and j2["status"] == "rejected"
    assert monitor._read_job(queue_root / "rejected" / f"gaules_{B + 300}")["coord_synced"] is True
