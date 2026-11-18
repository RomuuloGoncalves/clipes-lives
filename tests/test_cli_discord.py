"""Unit tests for CLI Discord notification and clips.json merge (F3 T3 & T4)."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import numpy as np

from clipes import cli, discord_rest, monitor, notificar
from clipes.models import CandidatePeak, IngestResult, SignalBundle, SnappedClip, Word

FAKE_TOKEN = "cli-secret-discord-token-999"
FAKE_CHANNEL = "1122334455"


class FakeDiscordRest:
    instances: list["FakeDiscordRest"] = []

    def __init__(self, token: str):
        self.token = token
        self.posts: list[dict[str, Any]] = []
        self.threads: list[dict[str, Any]] = []
        self.fail = False
        self._seq = 0
        self.__class__.instances.append(self)

    def post(self, channel_id: str | int, content: str, file: Path | None = None) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError(f"boom with {self.token}")
        self._seq += 1
        msg_id = f"msg-{self._seq}"
        self.posts.append({
            "channel_id": str(channel_id),
            "content": content,
            "file": Path(file) if file is not None else None,
            "id": msg_id,
        })
        return {"id": msg_id}

    def thread_from(
        self,
        channel_id: str | int,
        message_id: str | int,
        name: str,
        auto_archive_duration: int = 1440,
    ) -> str:
        tid = f"thr-{len(self.threads) + 1}"
        self.threads.append({
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "name": name,
            "auto_archive_duration": auto_archive_duration,
            "id": tid,
        })
        return tid


def _make_ing(tmp_path: Path, video_id: str = "vid_cli") -> IngestResult:
    out_dir = tmp_path / "out" / video_id
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    wav_path = cache_dir / "audio.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    return IngestResult(
        video_id=video_id,
        title="VOD Epico",
        url="https://www.youtube.com/watch?v=abc123",
        extractor="youtube",
        is_live=False,
        duration=120.0,
        audio_path=wav_path,
        video_path=None,
        heatmap=None,
        chat_messages=None,
        out_dir=out_dir,
        cache_dir=cache_dir,
        channel="@StreamerTeste",
    )


def _wire_fake_pipeline(monkeypatch, ing: IngestResult, clip_count: int = 2) -> None:
    n = 120
    zeros = np.zeros(n)
    bundle = SignalBundle(
        duration=ing.duration,
        n=n,
        scores={"loudness": zeros},
        combined=zeros.copy(),
        smoothed=zeros.copy(),
        agreeing=np.zeros(n, dtype=np.int32),
        weights={"loudness": 1.0},
        rms_db=np.full(n * 2, -30.0, dtype=np.float32),
    )
    peaks = [
        CandidatePeak(peak=20.0 + i * 30.0, raw_score=0.9, start_win=10.0, end_win=40.0)
        for i in range(clip_count)
    ]
    clips = [
        SnappedClip(
            peak=20.0 + i * 30.0,
            start=10.0 + i * 30.0,
            end=35.0 + i * 30.0,
            score=0.9 - i * 0.1,
            text=f"Fala do clipe {i + 1}",
        )
        for i in range(clip_count)
    ]
    words = [Word(10.0, 11.0, "Fala"), Word(11.1, 12.0, "teste")]
    monkeypatch.setattr(cli, "ingest", lambda **kwargs: ing)
    monkeypatch.setattr(cli, "compute_rms_db", lambda *a, **k: np.zeros(4, np.float32))
    monkeypatch.setattr(cli, "compute_signals", lambda **k: bundle)
    monkeypatch.setattr(cli, "pick_candidate_peaks", lambda *a, **k: peaks)
    monkeypatch.setattr(cli, "transcribe_candidates", lambda **k: (words, "pt"))
    monkeypatch.setattr(cli, "select_clips", lambda **k: clips)


def test_merge_discord_preserva_ids_existentes_e_ignora_clipe_novo(tmp_path):
    clips_path = tmp_path / "clips.json"
    clips_path.write_text(
        json.dumps({
            "video_id": "v1",
            "discord": {"thread_id": "thr-1", "header_message_id": "hdr-1"},
            "clips": [
                {"clip_id": "v1_c01", "discord": {"thread_id": "thr-1", "message_id": "msg-1"}},
            ],
        }),
        encoding="utf-8",
    )
    new_manifest = {
        "video_id": "v1",
        "clips": [
            {"clip_id": "v1_c01", "title": "Atualizado"},
            {"clip_id": "v1_c02", "title": "Novo clipe"},
        ],
    }

    cli._merge_discord(clips_path, new_manifest)

    assert new_manifest["discord"] == {"thread_id": "thr-1", "header_message_id": "hdr-1"}
    assert new_manifest["clips"][0]["discord"] == {"thread_id": "thr-1", "message_id": "msg-1"}
    assert "discord" not in new_manifest["clips"][1]


def test_merge_discord_json_corrompido_loga_e_segue(tmp_path, capsys):
    clips_path = tmp_path / "clips.json"
    clips_path.write_text("{json invalido", encoding="utf-8")
    manifest = {"video_id": "v1", "clips": [{"clip_id": "v1_c01"}]}

    cli._merge_discord(clips_path, manifest)

    out = capsys.readouterr().out
    assert "[discord] clips.json antigo ilegivel; ids nao preservados" in out
    assert "discord" not in manifest


def test_cli_main_posta_2_clipes_e_nao_reposta_ao_rerodar(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    FakeDiscordRest.instances.clear()
    monkeypatch.setattr(discord_rest, "DiscordRest", FakeDiscordRest)

    ing = _make_ing(tmp_path)
    _wire_fake_pipeline(monkeypatch, ing, clip_count=2)

    rc1 = cli.main([
        ing.url,
        "--out-dir", str(tmp_path / "out"),
        "--no-llm",
        "--skip-render",
    ])
    assert rc1 == 0
    assert len(FakeDiscordRest.instances) == 1
    client1 = FakeDiscordRest.instances[0]
    assert len(client1.threads) == 1
    assert len(client1.posts) == 3

    saved = json.loads((ing.out_dir / "clips.json").read_text(encoding="utf-8"))
    assert saved["discord"]["thread_id"] == "thr-1"
    assert saved["clips"][0]["discord"] == {"thread_id": "thr-1", "message_id": "msg-2"}
    assert saved["clips"][1]["discord"] == {"thread_id": "thr-1", "message_id": "msg-3"}

    # Rerun on same folder: _merge_discord preserves ids -> 0 Discord calls
    rc2 = cli.main([
        ing.url,
        "--out-dir", str(tmp_path / "out"),
        "--no-llm",
        "--skip-render",
    ])
    assert rc2 == 0
    assert len(FakeDiscordRest.instances) == 1


def test_cli_main_sem_env_e_com_no_discord_nao_chamam_discord(tmp_path, monkeypatch):
    FakeDiscordRest.instances.clear()
    monkeypatch.setattr(discord_rest, "DiscordRest", FakeDiscordRest)

    ing = _make_ing(tmp_path, video_id="v_noenv")
    _wire_fake_pipeline(monkeypatch, ing, clip_count=1)

    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    assert cli.main([ing.url, "--out-dir", str(tmp_path / "out"), "--no-llm", "--skip-render"]) == 0
    assert FakeDiscordRest.instances == []

    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    assert cli.main([
        ing.url, "--out-dir", str(tmp_path / "out"), "--no-llm", "--skip-render", "--no-discord",
    ]) == 0
    assert FakeDiscordRest.instances == []


def test_cli_main_falha_discord_retorna_exit_0_sem_expor_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)

    class FailingRest(FakeDiscordRest):
        def __init__(self, token: str):
            super().__init__(token)
            self.fail = True

    FakeDiscordRest.instances.clear()
    monkeypatch.setattr(discord_rest, "DiscordRest", FailingRest)
    ing = _make_ing(tmp_path, video_id="v_fail")
    _wire_fake_pipeline(monkeypatch, ing, clip_count=1)

    rc = cli.main([ing.url, "--out-dir", str(tmp_path / "out"), "--no-llm", "--skip-render"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[discord] falha ao enviar previa" in out
    assert FAKE_TOKEN not in out


def test_cli_ate_acabar_live_ativa_nao_chama_notify_do_cli(tmp_path, monkeypatch):
    calls: list[Any] = []
    monkeypatch.setattr(cli, "notify_discord", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(cli, "probe_metadata", lambda url: {"id": "live1", "is_live": True})
    monkeypatch.setattr(cli, "follow_live", lambda *a, **kw: {"status": "ended", "jobs": []})

    rc = cli.main(["https://www.twitch.tv/gaules", "--ate-acabar", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    assert calls == []


def test_cli_usa_clipes_policy_file_no_aviso(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", FAKE_CHANNEL)
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"blocked": ["streamerteste"]}), encoding="utf-8")
    monkeypatch.setenv("CLIPES_POLICY_FILE", str(policy_path))

    FakeDiscordRest.instances.clear()
    monkeypatch.setattr(discord_rest, "DiscordRest", FakeDiscordRest)
    ing = _make_ing(tmp_path, video_id="v_pol")
    _wire_fake_pipeline(monkeypatch, ing, clip_count=1)

    rc = cli.main([ing.url, "--out-dir", str(tmp_path / "out"), "--no-llm", "--skip-render"])
    assert rc == 0
    assert "bloqueado pela policy" in FakeDiscordRest.instances[0].posts[1]["content"]


def test_monitor_run_local_pipeline_nao_chama_notify_discord(tmp_path, monkeypatch):
    calls: list[Any] = []
    monkeypatch.setattr(notificar, "notify_discord", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(cli, "notify_discord", lambda *a, **kw: calls.append((a, kw)))

    ing = _make_ing(tmp_path, video_id="job_local_1")
    job_dir = tmp_path / "job_local_1"
    job_dir.mkdir(parents=True)

    monkeypatch.setattr("clipes.ingest.ingest_local", lambda *a, **kw: ing)
    monkeypatch.setattr(cli, "compute_rms_db", lambda *a, **k: np.zeros(4, np.float32))
    monkeypatch.setattr(cli, "pick_candidate_peaks", lambda *a, **k: [])

    res = monitor.run_local_pipeline(job_dir, tmp_path / "out")
    assert res["video_id"] == "job_local_1"
    assert calls == []
