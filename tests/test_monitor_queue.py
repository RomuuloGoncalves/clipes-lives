"""Tests for DiskQueue state machine and the sequential worker (no network, no publishing)."""

import json
import socket

import pytest

from clipes import cli, ingest as ingest_mod, monitor
from clipes.models import CandidatePeak, SnappedClip
from test_monitor import CHAT_ROWS, make_source_mkv

CREATED = 1_763_380_095.0


def stage_job(root, channel="gaules", peak=1_763_380_000, z=5.0, **extra):
    job_id = f"{channel}_{peak}"
    job_dir = root / "staging" / job_id
    job_dir.mkdir(parents=True)
    job = {"job_id": job_id, "channel": channel, "z_score": z, "created_at": CREATED, "status": "pending", **extra}
    (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    return job_dir


def states(queue):
    return {s: sorted(p.name for p in (queue.root / s).iterdir()) for s in monitor.QUEUE_STATES}


def test_job_moves_pending_processing_done_with_manifest(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    staged = stage_job(tmp_path)
    queue.enqueue(staged)
    assert states(queue)["pending"] == ["gaules_1763380000"] and not staged.exists()

    claimed = queue.claim_next()
    assert claimed.parent.name == "processing"
    assert json.loads((claimed / "job.json").read_text())["status"] == "processing"

    queue.complete(claimed, "done", {"score": 0.9})
    final = json.loads((queue.root / "done" / "gaules_1763380000" / "job.json").read_text())
    assert final["status"] == "done" and final["score"] == 0.9 and final["channel"] == "gaules"
    assert states(queue)["processing"] == [] and queue.claim_next() is None


def test_complete_rejects_invalid_final_status(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    queue.enqueue(stage_job(tmp_path))
    with pytest.raises(ValueError):
        queue.complete(queue.claim_next(), "pending", {})


def test_enqueue_files_rejected_jobs_under_rejected(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    queue.enqueue(stage_job(tmp_path, status="rejected", reject_reason="too_short"))
    assert states(queue)["rejected"] == ["gaules_1763380000"] and states(queue)["pending"] == []


def test_claim_next_takes_highest_z_score_first(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    for channel, z in [("low", 4.0), ("high", 12.0), ("mid", 7.0)]:
        queue.enqueue(stage_job(tmp_path, channel=channel, z=z))
    assert [queue.claim_next().name.split("_")[0] for _ in range(3)] == ["high", "mid", "low"]


def test_claim_next_orders_fifo_by_created_at_when_z_score_ties(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    queue.enqueue(stage_job(tmp_path, channel="aaa", peak=200, z=0.0, created_at=2.0))
    queue.enqueue(stage_job(tmp_path, channel="zzz", peak=100, z=0.0, created_at=1.0))
    queue.enqueue(stage_job(tmp_path, channel="spike", peak=300, z=5.0, created_at=9.0))
    assert [queue.claim_next().name for _ in range(3)] == ["spike_300", "zzz_100", "aaa_200"]


def test_recover_orphans_returns_processing_jobs_to_pending(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    queue.enqueue(stage_job(tmp_path))
    queue.claim_next()  # simulates a crash while processing
    restarted = monitor.DiskQueue(tmp_path / "queue")
    assert restarted.recover_orphans() == 1
    assert states(restarted)["pending"] == ["gaules_1763380000"] and states(restarted)["processing"] == []
    assert json.loads((restarted.root / "pending" / "gaules_1763380000" / "job.json").read_text())["status"] == "pending"


def test_index_orders_by_score_descending(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    for channel, score in [("a", 0.2), ("b", 0.9), ("c", 0.5)]:
        queue.enqueue(stage_job(tmp_path, channel=channel))
        queue.complete(queue.claim_next(), "done", {"score": score})
    assert [j["channel"] for j in queue.index("done")] == ["b", "c", "a"]


def clips_result(*scores):
    return {"clips": [{"score": s} for s in scores], "timings": {"asr_s": 1.5}, "output_dir": "/out/x"}


def test_worker_done_sets_ready_at_from_policy_delay(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    queue.enqueue(stage_job(tmp_path, channel="gaules"))
    policy = monitor.ChannelPolicy(delay_h={"gaules": 24.0})

    assert monitor.run_worker_step(queue, tmp_path / "out", policy, pipeline=lambda *_: clips_result(0.4, 0.8)) is True

    job = json.loads((queue.root / "done" / "gaules_1763380000" / "job.json").read_text())
    assert job["ready_at"] == CREATED + 86400 and job["score"] == 0.8 and job["clips"] == 2
    assert job["timings"]["asr_s"] == 1.5 and "worker_s" in job["timings"]


def test_worker_without_delay_policy_is_ready_at_creation(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    queue.enqueue(stage_job(tmp_path))
    monitor.run_worker_step(queue, tmp_path / "out", monitor.ChannelPolicy(), pipeline=lambda *_: clips_result(0.5))
    assert queue.index("done")[0]["ready_at"] == CREATED


def test_worker_moves_job_without_clips_to_rejected(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    queue.enqueue(stage_job(tmp_path))
    monitor.run_worker_step(queue, tmp_path / "out", monitor.ChannelPolicy(), pipeline=lambda *_: clips_result())
    assert states(queue)["rejected"] == ["gaules_1763380000"] and states(queue)["done"] == []


def test_worker_pipeline_error_rejects_job_and_keeps_going(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    queue.enqueue(stage_job(tmp_path, channel="boom", z=9.0))
    queue.enqueue(stage_job(tmp_path, channel="fine", z=1.0))

    def pipeline(job_dir, out_root):
        if "boom" in job_dir.name:
            raise RuntimeError("ffmpeg exploded")
        return clips_result(0.7)

    policy = monitor.ChannelPolicy()
    assert monitor.run_worker_step(queue, tmp_path / "out", policy, pipeline=pipeline)
    assert monitor.run_worker_step(queue, tmp_path / "out", policy, pipeline=pipeline)
    failed = json.loads((queue.root / "rejected" / "boom_1763380000" / "job.json").read_text())
    assert failed["reject_reason"] == "pipeline_error" and "ffmpeg exploded" in failed["error"]
    assert states(queue)["done"] == ["fine_1763380000"]


def test_worker_processes_one_job_per_step_and_false_when_empty(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    assert monitor.run_worker_step(queue, tmp_path / "out", monitor.ChannelPolicy()) is False
    queue.enqueue(stage_job(tmp_path, channel="a"))
    queue.enqueue(stage_job(tmp_path, channel="b"))
    seen = []
    monitor.run_worker_step(queue, tmp_path / "out", monitor.ChannelPolicy(),
                            pipeline=lambda d, o: seen.append(d.name) or clips_result(0.1))
    assert len(seen) == 1 and len(states(queue)["pending"]) == 1


def test_worker_never_processes_blocked_channel(tmp_path):
    queue = monitor.DiskQueue(tmp_path / "queue")
    queue.enqueue(stage_job(tmp_path, channel="gaules"))
    policy = monitor.ChannelPolicy(blocked={"gaules"})

    def pipeline(*_):
        raise AssertionError("blocked channel must not reach the pipeline")

    monitor.run_worker_step(queue, tmp_path / "out", policy, pipeline=pipeline)
    assert states(queue)["rejected"] == ["gaules_1763380000"]


@pytest.fixture
def local_job_queue(tmp_path, monkeypatch):
    """A queued job whose dir holds a real source.mkv + chat.json; yt-dlp and sockets are forbidden."""
    real_run = ingest_mod._run

    def guarded_run(cmd, *args, **kwargs):
        assert cmd[0] != "yt-dlp", "worker must not call yt-dlp"
        return real_run(cmd, *args, **kwargs)

    def no_connect(*args, **kwargs):
        raise AssertionError("worker must not open network connections")

    monkeypatch.setattr(ingest_mod, "_run", guarded_run)
    monkeypatch.setattr(socket.socket, "connect", no_connect)

    queue = monitor.DiskQueue(tmp_path / "queue")
    staged = stage_job(tmp_path)
    make_source_mkv(staged / "source.mkv", seconds=4)
    (staged / "chat.json").write_text(json.dumps(CHAT_ROWS), encoding="utf-8")
    (staged / "meta.json").write_text(json.dumps({"id": staged.name, "title": "gaules", "extractor": "twitch"}))
    queue.enqueue(staged)
    return queue


def test_worker_runs_real_pipeline_on_local_dir_and_rejects_empty_result(local_job_queue, tmp_path):
    out_root = tmp_path / "out"
    monitor.run_worker_step(local_job_queue, out_root, monitor.ChannelPolicy())

    manifest = json.loads(next(out_root.glob("*[[]gaules_1763380000[]]")).joinpath("clips.json").read_text())
    assert manifest["clips"] == []  # a 4s tone has no highlight candidates
    assert states(local_job_queue)["rejected"] == ["gaules_1763380000"]
    assert cli.ingest is ingest_mod.ingest  # cli.ingest restored after the job


def test_worker_runs_full_stage_chain_sequentially_for_done_job(local_job_queue, tmp_path, monkeypatch):
    calls = []
    peak = CandidatePeak(peak=2.0, raw_score=1.0, start_win=0.0, end_win=4.0)
    clip = SnappedClip(peak=2.0, start=0.0, end=4.0, score=0.77, active_signals=["audio"])

    def stage(name, value):
        def run(*args, **kwargs):
            calls.append(name)
            return value
        return run

    def fake_render(clip, idx, words, url, out_dir, cache_dir, live_video_path):
        calls.append("render")
        assert live_video_path.name == "source.mkv"  # renders from the job's local recording
        (out_dir / "clip_01.mp4").write_bytes(b"x")
        return out_dir / "clip_01.mp4"

    monkeypatch.setattr(cli, "pick_candidate_peaks", stage("candidates", [peak]))
    monkeypatch.setattr(cli, "transcribe_candidates", stage("asr", ([], "pt")))
    monkeypatch.setattr(cli, "select_clips", stage("snap", [clip]))
    monkeypatch.setattr(cli, "enrich_clips_with_llm", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "generate_title", stage("title", "Titulo"))
    monkeypatch.setattr(cli, "render_clip", fake_render)

    monitor.run_worker_step(local_job_queue, tmp_path / "out", monitor.ChannelPolicy(delay_h={"gaules": 24}))

    job = local_job_queue.index("done")[0]
    assert calls == ["candidates", "asr", "snap", "title", "render"]
    assert job["score"] == 0.77 and job["ready_at"] == CREATED + 86400 and job["clips"] == 1


def test_worker_with_llm_title_skips_cli_heuristic_fallback(local_job_queue, tmp_path, monkeypatch):
    calls = []
    peak = CandidatePeak(peak=2.0, raw_score=1.0, start_win=0.0, end_win=4.0)
    clip = SnappedClip(peak=2.0, start=0.0, end=4.0, score=0.77, active_signals=["audio"], text="Olha isso!")

    def stage(name, value):
        def run(*args, **kwargs):
            calls.append(name)
            return value
        return run

    def fake_enrich(clips, words, ing, enabled=True):
        calls.append("llm")
        clips[0].title = "Titulo LLM!"
        clips[0].title_source = "llm"
        clips[0].hashtags = ["#gaules", "#cortes", "#livesbr"]
        clips[0].caption = f"Gancho!\nCrédito: gaules · Live completa: {ing.url}\n#gaules #cortes #livesbr"
        clips[0].llm_score = 8.5

    def fake_render(clip, idx, words, url, out_dir, cache_dir, live_video_path):
        calls.append("render")
        (out_dir / "clip_01.mp4").write_bytes(b"x")
        return out_dir / "clip_01.mp4"

    monkeypatch.setattr(cli, "pick_candidate_peaks", stage("candidates", [peak]))
    monkeypatch.setattr(cli, "transcribe_candidates", stage("asr", ([], "pt")))
    monkeypatch.setattr(cli, "select_clips", stage("snap", [clip]))
    monkeypatch.setattr(cli, "enrich_clips_with_llm", fake_enrich)
    monkeypatch.setattr(cli, "generate_title", stage("title", "Titulo"))
    monkeypatch.setattr(cli, "render_clip", fake_render)

    monitor.run_worker_step(local_job_queue, tmp_path / "out", monitor.ChannelPolicy(delay_h={"gaules": 24}))

    assert calls == ["candidates", "asr", "snap", "llm", "render"]


@pytest.mark.parametrize("last", [False, True])
def test_run_local_pipeline_passa_own_range_da_regiao_dona(tmp_path, monkeypatch, last):
    from pathlib import Path
    from types import SimpleNamespace

    from clipes.janelas import owns

    seen = []

    def fake_run_pipeline(args):
        seen.append(args)
        ing = cli.ingest(args.url, Path(args.out_dir), args.live_minutes, args.chat)
        (ing.out_dir / "clips.json").write_text(json.dumps({"clips": []}), encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        "clipes.ingest.ingest_local",
        lambda jd, root, chat: SimpleNamespace(video_id=jd.name, out_dir=(root / jd.name).resolve()),
    )
    (tmp_path / "out" / "j").mkdir(parents=True)
    job = tmp_path / "j"
    job.mkdir()
    (job / "job.json").write_text(json.dumps(
        {"window_start": 700.0, "window_end": 1000.0, "video_start": 400.0, "max_dur": 60.0, "last": last}))
    monitor.run_local_pipeline(job, tmp_path / "out")
    lo, hi = seen[-1].own_range
    step, pad, vs = monitor.LIVE_STEP_S, 30.0, 400.0
    eps = 1e-6
    # centros logo dentro/fora da regiao batem com janelas.owns (tempo absoluto = vs + relativo)
    for rel in (lo - eps, lo + eps, hi - eps, hi + eps):
        in_range = lo <= rel <= hi if last else lo <= rel < hi
        assert in_range == owns(vs + rel, 700.0, 1000.0, step, pad, last), rel

    # job sem os campos (VOD/antigo): sem flag
    old = tmp_path / "old"
    old.mkdir()
    (old / "job.json").write_text(json.dumps({"job_id": "old", "max_dur": 60.0}))
    (tmp_path / "out" / "old").mkdir()
    monitor.run_local_pipeline(old, tmp_path / "out")
    assert seen[-1].own_range is None
