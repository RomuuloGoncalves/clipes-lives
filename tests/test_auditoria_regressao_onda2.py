"""Regressao cruzada da onda 2 da auditoria: C-01, M-08, M-10 e B-25 (acompanhar + monitor + coord).

Os unitarios ficam em test_auditoria_captura.py e test_auditoria_gravador.py; aqui os fluxos
passam por mais de um modulo (pool real com popen falso, Supervisor, Mongo via mongomock).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import mongomock

from clipes import acompanhar, monitor
from clipes.coord import Coord
from test_acompanhar import B, Clock, FakeProc
from test_auditoria_captura import _make_session

GAULES_EPOCHS_COM_BURACO = [B, B + 10, B + 20, B + 60, B + 70, B + 80]  # buraco entre B+20 e B+60
TRECHO_CONTINUO_RECENTE = [B + 60, B + 70, B + 80]


def _fake_ffmpeg_concat(monkeypatch) -> list[str]:
    """Substitui o ffmpeg do freeze_and_concat e devolve os nomes dos segmentos da lista do concat."""
    names: list[str] = []

    def fake_run(cmd, **kw):
        list_file = Path(cmd[cmd.index("-i") + 1])
        for line in list_file.read_text(encoding="utf-8").splitlines():
            names.append(Path(line.split("'")[1]).name)
        Path(cmd[-1]).write_bytes(b"mkv")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(monitor.subprocess, "run", fake_run)
    return names


def _write_segments(seg_dir: Path, epochs: list[int]) -> None:
    seg_dir.mkdir(parents=True, exist_ok=True)
    for epoch in epochs:
        (seg_dir / f"{epoch}.ts").write_bytes(b"ts")


def _popen_registrando(procs: list[FakeProc]):
    def fake_popen(cmd, **kw):
        procs.append(FakeProc(cmd, **kw))
        return procs[-1]

    return fake_popen


# ---- C-01 ponta a ponta: Supervisor + Mongo + pool real ------------------------------------------


def test_c01_supervisor_reinicia_gravador_que_cai_e_live_so_vira_ended_quando_is_live_false(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_LIVE_CAPTURA_MORTA_S", "10000")  # so o sinal offline pode encerrar aqui
    procs: list[FakeProc] = []
    pool = monitor.RollingRecorderPool(tmp_path / "buf", max_hot=0, max_on_demand=1, popen=_popen_registrando(procs))
    db = mongomock.MongoClient().db
    coord = Coord(db, "w-c01-e2e", now=clock.now, lease_s=600)
    observado: dict[str, object] = {}
    queda_feita = [False]

    def sleep(d: float) -> None:
        clock.sleep(d)
        if not queda_feita[0] and clock.t >= B + 60:
            procs[0].returncode = 1  # fonte (streamlink) cai com a live ainda no ar
            queda_feita[0] = True
        if clock.t >= B + 120 and not observado:
            doc = db.lives.find_one({"_id": "twitch:gaules:c01-e2e"})
            observado.update(status=doc["status"], gravando=pool.is_recording("gaules"), processos=len(procs))

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        tmp_path / "out",
        pool=pool,
        pipeline=lambda d, o: {"clips": []},
        coord=coord,
        stream_id="c01-e2e",
        is_live=lambda u: clock.t < B + 200,
        now=clock.now,
        sleep=sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
        capacity_fn=lambda _: {"cpus": 4, "score": 30},
    )
    res = sup.run()

    # Durante a queda com live ativa: Mongo continua 'live' e o gravador foi reiniciado (fonte + ffmpeg de novo)
    assert observado == {"status": "live", "gravando": True, "processos": 4}
    assert [p.cmd[0] for p in procs] == ["streamlink", "ffmpeg", "streamlink", "ffmpeg"]
    # Depois de is_live=False confirmado: encerra e grava ended
    assert res["status"] == "ended"
    assert db.lives.find_one({"_id": "twitch:gaules:c01-e2e"})["status"] == "ended"


# ---- M-08: video_start e lista do concat vem do mesmo trecho continuo ----------------------------


def test_m08_freeze_and_concat_usa_a_mesma_lista_que_video_start_ts(tmp_path, monkeypatch):
    pool = monitor.RollingRecorderPool(tmp_path / "buf", popen=lambda cmd, **kw: FakeProc(cmd, **kw))
    _write_segments(pool.buf_root / "gaules", GAULES_EPOCHS_COM_BURACO)
    names = _fake_ffmpeg_concat(monkeypatch)

    video_start = pool.video_start_ts("gaules", B, B + 85)
    dst = pool.freeze_and_concat("gaules", B, B + 85, tmp_path / "source.mkv")

    assert video_start == B + 60  # trecho continuo mais recente, nao o inicio da janela
    assert dst.exists()
    assert names == [f"{e}.ts" for e in TRECHO_CONTINUO_RECENTE]
    assert names[0] == f"{int(video_start)}.ts"  # o inicio do video concatenado e o video_start


def test_m08_job_da_janela_grava_video_start_do_trecho_continuo_e_nao_o_inicio_da_janela(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    pool = monitor.RollingRecorderPool(tmp_path / "buf", max_hot=0, max_on_demand=1,
                                       popen=lambda cmd, **kw: FakeProc(cmd, **kw))
    sess = _make_session(tmp_path, clock, pool, is_live=lambda u: True)
    sess.start()
    _write_segments(pool.buf_root / sess.key, GAULES_EPOCHS_COM_BURACO)
    names = _fake_ffmpeg_concat(monkeypatch)

    sess.fechar_janela(float(B + 90), last=False)  # janela [B-510, B+90]

    job = monitor._read_job(sess.queue.root / "pending" / sess.jobs[0])
    assert job["video_start"] == B + 60
    assert job["status"] == "pending"
    assert names == [f"{e}.ts" for e in TRECHO_CONTINUO_RECENTE]


# ---- M-10: URL fixada em watch?v=<id> tambem no gravador reiniciado -------------------------------


def test_m10_gravador_reiniciado_continua_usando_watch_url_do_video(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    monkeypatch.setenv("CLIPES_MAX_LIVES", "1")
    monkeypatch.setenv("CLIPES_LIVE_CAPTURA_MORTA_S", "10000")
    procs: list[FakeProc] = []
    pool_original = monitor.RollingRecorderPool
    monkeypatch.setattr(
        monitor,
        "RollingRecorderPool",
        lambda buf_root, **kw: pool_original(buf_root, **{**kw, "popen": _popen_registrando(procs)}),
    )
    esperado = "https://www.youtube.com/watch?v=VidFixo123"
    probe_urls: list[str] = []
    is_live_urls: list[str] = []
    queda_feita = [False]

    def fake_probe(url: str) -> dict:
        probe_urls.append(url)
        return {"id": "VidFixo123", "channel_id": "UC_FIXO", "uploader_id": "@CanalTeste", "is_live": True}

    def fake_is_live(url: str) -> bool:
        is_live_urls.append(url)
        return clock.t < B + 200

    def sleep(d: float) -> None:
        clock.sleep(d)
        if not queda_feita[0] and clock.t >= B + 60:
            procs[0].returncode = 1  # yt-dlp cai; o restart tem que reusar a mesma URL
            queda_feita[0] = True

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("https://www.youtube.com/@CanalTeste/live")],
        tmp_path / "out",
        pipeline=lambda d, o: {"clips": []},
        is_live=fake_is_live,
        probe=fake_probe,
        now=clock.now,
        sleep=sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    res = sup.run()

    yt_dlp_cmds = [p.cmd for p in procs if p.cmd[0] == "yt-dlp"]
    assert probe_urls == ["https://www.youtube.com/@CanalTeste/live"]
    assert len(yt_dlp_cmds) == 2  # gravador inicial + reinicio apos a queda
    assert all(cmd[-1] == esperado for cmd in yt_dlp_cmds)
    assert set(is_live_urls) == {esperado}
    assert res["status"] == "ended"


# ---- B-25: Supervisor com pool proprio nao deixa sobras de .buffer chegarem ao gravador ----------


def test_b25_pool_proprio_do_supervisor_nao_expoe_sobras_do_buffer_ao_gravador(tmp_path, monkeypatch):
    clock = Clock(B + 10)
    out_root = tmp_path / "out"
    queue_root = out_root / "queue"
    sobra = queue_root / ".buffer" / "gaules" / f"{B + 5}.ts"  # dentro da primeira janela
    sobra.parent.mkdir(parents=True)
    sobra.write_bytes(b"sobra da execucao anterior")

    existia_na_subida: list[bool] = []
    procs: list[FakeProc] = []
    base_popen = _popen_registrando(procs)

    def popen_checando_sobra(cmd, **kw):
        if cmd[0] != "ffmpeg":  # fonte do gravador: a sobra ja deve ter sido apagada
            existia_na_subida.append(sobra.exists())
        return base_popen(cmd, **kw)

    pool_original = monitor.RollingRecorderPool
    monkeypatch.setattr(
        monitor,
        "RollingRecorderPool",
        lambda buf_root, **kw: pool_original(buf_root, **{**kw, "popen": popen_checando_sobra}),
    )
    queda_feita = [False]

    def sleep(d: float) -> None:
        clock.sleep(d)
        if not queda_feita[0] and clock.t >= B + 20:
            procs[0].returncode = 1  # forca um restart, que tambem tem que ver o buffer limpo
            queda_feita[0] = True

    sup = acompanhar.Supervisor(
        [acompanhar.parse_alvo("gaules")],
        out_root,
        queue_root=queue_root,
        is_live=lambda u: clock.t < B + 20,
        now=clock.now,
        sleep=sleep,
        disk_usage=lambda p: SimpleNamespace(free=100 * 1e9),
    )
    sup.run()

    assert len(existia_na_subida) == 2  # subida inicial + restart
    assert not any(existia_na_subida)
    assert not sobra.exists()
