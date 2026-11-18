"""follow_live: janelas alinhadas, fim por 2 sinais, disco, backlog (gravador, relógio e pipeline falsos)."""

import json
from types import SimpleNamespace

from clipes import monitor

B = 1_700_000_100  # múltiplo de 300
STEP = 300


class Clock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, d):
        self.t += d


class FakePool:
    """Grava segmentos de 10 s até `stop_at`; `alive` cai quando o gravador "sai"."""

    def __init__(self, clock, stop_at):
        self.clock, self.stop_at, self.first = clock, stop_at, None
        self.started = False

    def start_on_demand(self, ch):
        self.started, self.first = True, int(self.clock.t) // 10 * 10
        return True

    def release_on_demand(self, ch):
        self.started = False

    def is_recording(self, ch):
        return self.clock.t < self.stop_at

    def segments(self, ch):
        last = min(self.clock.t, self.stop_at) - 10
        return [(e, None) for e in range(self.first, int(last) + 1, 10)]

    def video_start_ts(self, ch, s, e):
        return float(max(self.first, int(s) // 10 * 10))

    def freeze_and_concat(self, ch, s, e, dst):
        dst.write_bytes(b"x")

    def hold(self, ch, ts): pass
    def release_hold(self, ch): pass
    def cleanup(self, now): pass


def run(tmp_path, monkeypatch, *, stop_after, pipeline, free_gb=100, jump=None):
    clock = Clock(B + 10)
    monkeypatch.setattr(monitor, "probe_duration", lambda p: 600.0)
    pool = FakePool(clock, B + stop_after)
    res = monitor.follow_live(
        "https://www.twitch.tv/Canal", tmp_path / "out", pool=pool, pipeline=pipeline(clock),
        is_live=lambda u: True, now=clock.now, sleep=clock.sleep, min_free_gb=5,
        disk_usage=lambda p: SimpleNamespace(free=free_gb * 1e9),
    )
    return res, tmp_path / "out" / "queue"


def jobs_in(q, state):
    return sorted(p.name for p in (q / state).iterdir())


def test_tres_janelas_e_depois_gravador_parado_encerra_a_live(tmp_path, monkeypatch):
    # boundaries B+300, B+600, B+900; stream stops at B+920 (tail 20 s < 25 s: no 4th job)
    res, q = run(tmp_path, monkeypatch, stop_after=920, pipeline=lambda c: lambda d, o: {"clips": []})
    assert res["status"] == "ended"
    assert res["jobs"] == [f"canal_{B + 300 * i}" for i in (1, 2, 3)]
    assert len(jobs_in(q, "pending")) == 0 and len(jobs_in(q, "rejected")) == 3  # no clips -> rejected
    assert not list(q.glob("*/*/source.mkv"))


def test_disco_abaixo_do_piso_pula_a_janela(tmp_path, monkeypatch, capsys):
    res, q = run(tmp_path, monkeypatch, stop_after=620, pipeline=lambda c: lambda d, o: {"clips": []}, free_gb=4)
    assert res["jobs"] == []
    assert capsys.readouterr().out.count("[live] janela pulada: disco") == 2  # B+300 and B+600


def test_backlog_maior_que_2_descarta_a_mais_antiga_como_lost(tmp_path, monkeypatch):
    def slow(clock):
        calls = []

        def pipe(d, o):
            if not calls:
                clock.t += 1000  # three boundaries pass while the first window is processed
            calls.append(d.name)
            return {"clips": []}
        return pipe
    res, q = run(tmp_path, monkeypatch, stop_after=2500, pipeline=slow)
    lost = [n for n in jobs_in(q, "rejected")
            if json.loads((q / "rejected" / n / "job.json").read_text()).get("reject_reason") == "lost"]
    assert lost == [f"canal_{B + 600}"]
    assert not (q / "rejected" / lost[0] / "source.mkv").exists()


def test_so_clipes_da_regiao_dona_e_sem_repeticao_sobrevivem(tmp_path, monkeypatch):
    def make(clock):
        def pipe(job_dir, out):
            job = json.loads((job_dir / "job.json").read_text())
            vs, e = job["video_start"], job["window_end"]
            def clip(center):
                return {"file": "c.mp4", "score": 1.0, "start": center - 20 - vs, "end": center + 20 - vs}
            if e == B + 300:
                return {"clips": [clip(e - 100), clip(e - 10)]}  # 2nd is in the NEXT window's region
            if e == B + 600:
                return {"clips": [clip(B + 290), clip(B + 290)]}  # same absolute interval twice
            return {"clips": []}
        return pipe
    _, q = run(tmp_path, monkeypatch, stop_after=620, pipeline=make)
    counts = {n: json.loads((q / "done" / n / "job.json").read_text())["clips"] for n in jobs_in(q, "done")}
    assert counts == {f"canal_{B + 300}": 1, f"canal_{B + 600}": 1}
