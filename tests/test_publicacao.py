"""Integration tests for clipes.publicacao (T7: publish queue, policy, idempotency, quota, run_pending)."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import mongomock
import pytest

from clipes.monitor import ChannelPolicy
from clipes.publicacao import LocalStore, MongoStore, find_clip, quota_ok, request, run_pending

T0 = 1_763_400_000.0
ALLOWED_USER = "111222333444"
OTHER_USER = "999888777666"
SECRET_ACCESS = "tok-access-secret-xyz"
SECRET_REFRESH = "tok-refresh-secret-abc"
SECRET_REFRESH_NEW = "tok-refresh-rotated-def"
SECRET_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=secret999"


class FakeClock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _make_clip_fixture(
    out_root: Path,
    *,
    clip_id: str = "live1_c01",
    channel: str = "gaules",
    owner: str = "w-pc1",
    ready_at: float | None = None,
    create_mp4: bool = True,
    duration: float = 45.0,
    thread_id: str = "thr-100",
) -> Path:
    clip_dir = out_root / "2026-11-17 - Live [live1]"
    clip_dir.mkdir(parents=True, exist_ok=True)
    mp4_name = "clip_01_jogada_epica.mp4"
    if create_mp4:
        (clip_dir / mp4_name).write_bytes(b"fake-mp4-data")

    manifest = {
        "video_id": "live1",
        "title": "Live Epica",
        "url": "https://www.twitch.tv/gaules",
        "channel_credit": f"twitch.tv/{channel}",
        "channel": channel,
        "owner": owner,
        "clips": [
            {
                "index": 1,
                "clip_id": clip_id,
                "file": mp4_name,
                "title": "Jogada epica!",
                "caption": "Jogada epica\nCredito: gaules\n#clips",
                "description_long": "Jogada epica na live de hoje.\n\nCredito: gaules\n#clips",
                "duration": duration,
                "rendered_duration": duration + 1.2,
                "discord": {"thread_id": thread_id, "message_id": "msg-1"},
            }
        ],
    }
    clips_json = clip_dir / "clips.json"
    clips_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    job_dir = out_root / "queue" / "done" / "job_1"
    job_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "job_id": "job_1",
        "channel": channel,
        "owner": owner,
        "created_at": T0 - 3600,
        "ready_at": ready_at,
        "output_dir": str(clip_dir),
        "status": "done",
    }
    (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    return clips_json


def _write_tokens(tokens_path: Path, data: dict[str, Any]) -> None:
    tokens_path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(tokens_path, 0o600)


def _mongo_with_worker(worker_id: str = "w-pc1", alias: str = "pc-mesa", heartbeat_ts: float = T0) -> tuple[Any, Any]:
    client = mongomock.MongoClient()
    db = client.clipes_lives
    if heartbeat_ts is not None:
        hb = datetime.fromtimestamp(heartbeat_ts, timezone.utc).replace(tzinfo=None)
        db.workers.insert_one({"_id": worker_id, "alias": alias, "heartbeat_at": hb})
    return client, db


# --- PUB-01 AC1: Permissoes ---


def test_request_denies_unauthorized_user_and_empty_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_clip_fixture(tmp_path)
    _, db = _mongo_with_worker()
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"refresh_token": SECRET_REFRESH}})

    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    res_other = request(store, "live1_c01", OTHER_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    assert res_other == "sem permissao"
    assert db.publish_requests.count_documents({}) == 0

    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "")
    res_empty = request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    assert res_empty == "sem permissao"
    assert db.publish_requests.count_documents({}) == 0


# --- PUB-01 AC2: Clipe nao encontrado ---


def test_request_clip_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    store = LocalStore(tmp_path / ".publicacoes.json")
    res = request(store, "inexistente_c99", ALLOWED_USER, T0, out_root=tmp_path)
    assert res == "clipe inexistente_c99 nao encontrado"
    assert store.find_pending() == []


# --- PUB-01 AC3: Politica e ready_at ---


def test_request_rejects_blocked_channel_without_creating_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_clip_fixture(tmp_path, channel="canal_proibido")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    _, db = _mongo_with_worker()
    store = MongoStore(db)
    policy = ChannelPolicy(blocked={"canal_proibido"})
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"refresh_token": SECRET_REFRESH}})

    res = request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, policy=policy, tokens_path=tokens_path)
    assert res == "bloqueado pela politica"
    assert db.publish_requests.count_documents({}) == 0


def test_request_rejects_before_ready_at_without_creating_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ready_at = T0 + 7200
    _make_clip_fixture(tmp_path, channel="gaules", ready_at=ready_at)
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    store = LocalStore(tmp_path / ".publicacoes.json")
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"refresh_token": SECRET_REFRESH}})

    res = request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    assert res.startswith("aguardar ate ")
    assert store.find_pending() == []


# --- PUB-01 AC4 + Assumptions: Enfileiramento, plataformas habilitadas, PC offline, chave unica ---


def test_request_enqueues_enabled_platforms_and_reports_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_clip_fixture(tmp_path, owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    _, db = _mongo_with_worker("w-pc1", "pc-mesa", T0)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"refresh_token": SECRET_REFRESH}})

    res = request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    assert "publicacao enfileirada" in res
    assert "tiktok: desligada" in res

    docs = list(db.publish_requests.find({}))
    assert len(docs) == 1
    doc = docs[0]
    assert doc["_id"] == "live1_c01:youtube"
    assert doc["clip_id"] == "live1_c01"
    assert doc["plataforma"] == "youtube"
    assert doc["owner"] == "w-pc1"
    assert doc["pedido_por"] == ALLOWED_USER
    assert doc["status"] == "pending"
    assert doc["thread_id"] == "thr-100"


def test_request_offline_owner_keeps_pending_and_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_clip_fixture(tmp_path, owner="w-pc2")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    # Heartbeat stale (> 180s)
    _, db = _mongo_with_worker("w-pc2", "pc-quarto", T0 - 600)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"tiktok": {"refresh_token": SECRET_REFRESH}})

    res = request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    assert "PC pc-quarto offline; pedido fica na fila" in res
    doc = db.publish_requests.find_one({"_id": "live1_c01:tiktok"})
    assert doc is not None
    assert doc["status"] == "pending"


# --- PUB-01 AC5: Idempotencia para publicacao ok ---


def test_second_request_after_ok_returns_existing_link_without_calling_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _make_clip_fixture(tmp_path, owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    store = LocalStore(tmp_path / ".publicacoes.json")
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"refresh_token": SECRET_REFRESH}})

    api_calls: list[str] = []

    def fake_yt(mp4, title, desc, token):
        api_calls.append("youtube")
        return {"video_id": "yt12345", "privacyStatus": "private"}

    def fake_refresh(plat, tok):
        return {"access_token": SECRET_ACCESS}

    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": fake_yt},
        refreshers={"youtube": fake_refresh},
    )
    assert len(api_calls) == 1

    res2 = request(store, "live1_c01", ALLOWED_USER, T0 + 10, out_root=tmp_path, tokens_path=tokens_path)
    assert "yt12345" in res2
    assert "publicacao enfileirada" not in res2
    assert len(api_calls) == 1
    assert store.find_pending() == []


# --- PUB-01 AC10: Cota local diaria ---


def test_quota_full_marks_adiado_without_calling_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_clip_fixture(tmp_path, clip_id="live1_c01", owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    monkeypatch.setenv("CLIPES_YT_MAX_DIA", "1")
    _, db = _mongo_with_worker("w-pc1", "pc-mesa", T0)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"refresh_token": SECRET_REFRESH}})
    quota_path = tmp_path / ".cotas_publicacao.json"

    # First clip consumes the 1/day quota
    api_calls: list[str] = []

    def fake_yt(mp4, title, desc, token):
        api_calls.append(mp4.name)
        return {"video_id": "yt-first", "privacyStatus": "private"}

    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path, quota_path=quota_path)
    run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        quota_path=quota_path,
        publishers={"youtube": fake_yt},
        refreshers={"youtube": lambda p, t: {"access_token": SECRET_ACCESS}},
    )
    assert len(api_calls) == 1
    assert quota_ok("youtube", T0, quota_path=quota_path) is False

    # Second clip on same day hits quota
    clip2_dir = tmp_path / "2026-11-17 - Live2 [live2]"
    clip2_dir.mkdir(parents=True, exist_ok=True)
    (clip2_dir / "clip_01_outro.mp4").write_bytes(b"fake")
    (clip2_dir / "clips.json").write_text(
        json.dumps({
            "video_id": "live2",
            "channel": "gaules",
            "owner": "w-pc1",
            "clips": [{"index": 1, "clip_id": "live2_c01", "file": "clip_01_outro.mp4", "title": "Outro", "discord": {"thread_id": "thr-2"}}],
        }),
        encoding="utf-8",
    )

    res2 = request(store, "live2_c01", ALLOWED_USER, T0 + 5, out_root=tmp_path, tokens_path=tokens_path, quota_path=quota_path)
    assert "cota diaria atingida (youtube)" in res2
    doc2 = db.publish_requests.find_one({"_id": "live2_c01:youtube"})
    # onda 3: nao cria mais `adiado` terminal; fica pending no proximo dia de cota
    assert doc2["status"] == "pending"
    assert doc2["agendar_para"] > datetime.fromtimestamp(T0 + 5, timezone.utc).replace(tzinfo=None)
    assert len(api_calls) == 1


# --- Edge Case: .mp4 ausente -> erro_arquivo ---


def test_run_pending_missing_mp4_marks_erro_arquivo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_clip_fixture(tmp_path, owner="w-pc1", create_mp4=False)
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    _, db = _mongo_with_worker("w-pc1", "pc-mesa", T0)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"refresh_token": SECRET_REFRESH}})

    thread_msgs: list[tuple[str, str]] = []
    called = False

    def fake_yt(*args):
        nonlocal called
        called = True
        return {}

    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": fake_yt},
        refreshers={"youtube": lambda p, t: {"access_token": SECRET_ACCESS}},
        discord_post=lambda tid, msg: thread_msgs.append((tid, msg)),
    )

    assert called is False
    assert outcomes[0]["status"] == "erro_arquivo"
    doc = db.publish_requests.find_one({"_id": "live1_c01:youtube"})
    assert doc["status"] == "erro_arquivo"
    assert thread_msgs and thread_msgs[0][0] == "thr-100"
    assert "erro_arquivo" in thread_msgs[0][1]


# --- Worker ownership isolation ---


def test_run_pending_executes_only_own_worker_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_clip_fixture(tmp_path, owner="w-pc2")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    _, db = _mongo_with_worker("w-pc2", "pc-dois", T0)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"refresh_token": SECRET_REFRESH}})

    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)

    called = False

    def fake_yt(*args):
        nonlocal called
        called = True
        return {"video_id": "x"}

    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": fake_yt},
        refreshers={"youtube": lambda p, t: {"access_token": SECRET_ACCESS}},
    )
    assert outcomes == []
    assert called is False
    assert db.publish_requests.find_one({"_id": "live1_c01:youtube"})["status"] == "pending"


# --- PUB-04 AC11 & AC12: 401 refresh 1x + regrava refresh novo + resposta na thread sem vazar segredo ---


def test_run_pending_refreshes_once_on_401_saves_rotated_token_and_replies_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    clips_json = _make_clip_fixture(tmp_path, owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    _, db = _mongo_with_worker("w-pc1", "pc-mesa", T0)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(
        tokens_path,
        {"tiktok": {"access_token": "expired-tok", "refresh_token": SECRET_REFRESH, "open_id": "op-1"}},
    )

    attempts = 0
    refresh_calls = 0

    def fake_tt(mp4, caption, token):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError(f"HTTP 401 Unauthorized token={token} url={SECRET_UPLOAD_URL}")
        assert token == SECRET_ACCESS
        return {"publish_id": "tt-pub-777", "privacy_level": "SELF_ONLY"}

    def fake_refresh(plat, tok):
        nonlocal refresh_calls
        refresh_calls += 1
        return {"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH_NEW}

    thread_msgs: list[tuple[str, str]] = []
    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"tiktok": fake_tt},
        refreshers={"tiktok": fake_refresh},
        discord_post=lambda tid, msg: thread_msgs.append((tid, msg)),
    )

    assert attempts == 2
    assert refresh_calls == 1
    assert outcomes[0]["status"] == "ok"

    # Rotated refresh_token saved atomically with mode 600
    saved_tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
    assert saved_tokens["tiktok"]["refresh_token"] == SECRET_REFRESH_NEW
    assert stat.S_IMODE(tokens_path.stat().st_mode) == 0o600

    # clips.json updated with publication record
    updated_manifest = json.loads(clips_json.read_text(encoding="utf-8"))
    pubs = updated_manifest["clips"][0]["publicacoes"]
    assert pubs == [{"plataforma": "tiktok", "id": "tt-pub-777", "privacidade": "SELF_ONLY"}]

    # Thread reply has status and id, zero secrets/upload URLs
    assert len(thread_msgs) == 1
    tid, msg = thread_msgs[0]
    assert tid == "thr-100"
    assert "tiktok" in msg
    assert "ok" in msg
    assert "tt-pub-777" in msg
    assert SECRET_ACCESS not in msg
    assert SECRET_REFRESH not in msg
    assert SECRET_REFRESH_NEW not in msg
    assert SECRET_UPLOAD_URL not in msg


def test_run_pending_401_refresh_failure_marks_erro_auth_and_never_leaks_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _make_clip_fixture(tmp_path, owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    _, db = _mongo_with_worker("w-pc1", "pc-mesa", T0)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(
        tokens_path,
        {"youtube": {"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH}},
    )

    thread_msgs: list[tuple[str, str]] = []
    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": lambda *a: (_ for _ in ()).throw(RuntimeError(f"401 token={SECRET_ACCESS} upload={SECRET_UPLOAD_URL}"))},
        refreshers={"youtube": lambda p, t: (_ for _ in ()).throw(RuntimeError(f"invalid_grant {SECRET_REFRESH}"))},
        discord_post=lambda tid, msg: thread_msgs.append((tid, msg)),
    )

    assert outcomes[0]["status"] == "erro_auth"
    doc = db.publish_requests.find_one({"_id": "live1_c01:youtube"})
    assert doc["status"] == "erro_auth"
    assert len(thread_msgs) == 1
    msg = thread_msgs[0][1]
    assert "refaca clipes auth youtube" in msg
    assert SECRET_ACCESS not in msg
    assert SECRET_REFRESH not in msg
    assert SECRET_UPLOAD_URL not in msg


# --- PUB-01 AC8: TikTok conta privada (erro_conta) + Edge case > 180s ---


def test_run_pending_tiktok_private_account_error_marks_erro_conta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_clip_fixture(tmp_path, owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    store = LocalStore(tmp_path / ".publicacoes.json")
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"tiktok": {"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH}})

    thread_msgs: list[tuple[str, str]] = []
    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"tiktok": lambda *a: (_ for _ in ()).throw(RuntimeError("unaudited_client_can_only_post_to_private_accounts"))},
        refreshers={"tiktok": lambda p, t: {"access_token": SECRET_ACCESS}},
        discord_post=lambda tid, msg: thread_msgs.append((tid, msg)),
    )

    assert outcomes[0]["status"] == "erro_conta"
    assert "deixe a conta do TikTok como privada" in thread_msgs[0][1]


def test_find_clip_reads_manifest_and_job_metadata(tmp_path: Path):
    _make_clip_fixture(tmp_path, clip_id="live1_c01", channel="Gaules", owner="w-pc1", ready_at=T0 + 60)
    found = find_clip(tmp_path, "live1_c01")
    assert found is not None
    assert found["clip_id"] == "live1_c01"
    assert found["channel"] == "gaules"
    assert found["owner"] == "w-pc1"
    assert found["ready_at"] == T0 + 60
    assert found["thread_id"] == "thr-100"
    assert found["mp4_path"].name == "clip_01_jogada_epica.mp4"


def test_duplicate_pending_request_keeps_single_unique_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_clip_fixture(tmp_path, owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    _, db = _mongo_with_worker("w-pc1", "pc-mesa", T0)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"refresh_token": SECRET_REFRESH}})

    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    request(store, "live1_c01", ALLOWED_USER, T0 + 5, out_root=tmp_path, tokens_path=tokens_path)
    assert db.publish_requests.count_documents({"clip_id": "live1_c01", "plataforma": "youtube"}) == 1


def test_run_pending_youtube_over_180s_marks_erro_and_reports_on_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _make_clip_fixture(tmp_path, owner="w-pc1", duration=181.0)
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    store = LocalStore(tmp_path / ".publicacoes.json")
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH}})

    thread_msgs: list[tuple[str, str]] = []
    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": lambda *a: (_ for _ in ()).throw(ValueError("clipe acima de 3 min"))},
        refreshers={"youtube": lambda p, t: {"access_token": SECRET_ACCESS}},
        discord_post=lambda tid, msg: thread_msgs.append((tid, msg)),
    )

    assert outcomes[0]["status"] == "erro"
    assert "clipe acima de 3 min" in thread_msgs[0][1]


def test_run_pending_default_refresh_uses_oauth_without_injected_refreshers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """B1: real _default_refresh -> oauth.refresh_google / refresh_tiktok -> urlopen path."""
    import io
    import urllib.parse
    import urllib.request

    class _FakeHttpResp:
        def __init__(self, body: dict[str, Any]):
            self._raw = json.dumps(body).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *args: Any):
            return False

    _make_clip_fixture(tmp_path, owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "yt-cid")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "yt-csec")
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "tt-ckey")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "tt-csec")

    tokens_path = tmp_path / "tokens.json"
    _write_tokens(
        tokens_path,
        {
            "youtube": {"refresh_token": SECRET_REFRESH},
            "tiktok": {"refresh_token": SECRET_REFRESH, "open_id": "op-1"},
        },
    )

    oauth_urls: list[str] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30):
        oauth_urls.append(req.full_url)
        form = urllib.parse.parse_qs(req.data.decode("utf-8"))
        assert form["grant_type"] == ["refresh_token"]
        assert form["refresh_token"] == [SECRET_REFRESH]
        if "googleapis.com" in req.full_url:
            return _FakeHttpResp({"access_token": "yt-fresh-acc"})
        return _FakeHttpResp({
            "access_token": "tt-fresh-acc",
            "refresh_token": SECRET_REFRESH_NEW,
            "open_id": "op-1",
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    seen_tokens: dict[str, str] = {}

    def fake_yt(mp4, title, desc, token):
        seen_tokens["youtube"] = token
        return {"video_id": "yt-real-refresh"}

    def fake_tt(mp4, caption, token):
        seen_tokens["tiktok"] = token
        return {"publish_id": "tt-real-refresh"}

    store = LocalStore(tmp_path / ".publicacoes.json")
    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": fake_yt, "tiktok": fake_tt},
    )

    by_plat = {o["plataforma"]: o for o in outcomes}
    assert by_plat["youtube"]["status"] == "ok"
    assert by_plat["tiktok"]["status"] == "ok"
    assert seen_tokens == {"youtube": "yt-fresh-acc", "tiktok": "tt-fresh-acc"}
    assert len(oauth_urls) == 2

    saved_tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
    assert saved_tokens["tiktok"]["refresh_token"] == SECRET_REFRESH_NEW
    assert stat.S_IMODE(tokens_path.stat().st_mode) == 0o600


def test_concurrent_run_pending_local_store_uploads_once_without_tmp_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """B3: two concurrent run_pending threads on LocalStore perform 1 upload and hit no tmp file collision."""
    import concurrent.futures
    import threading

    _make_clip_fixture(tmp_path, owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    store = LocalStore(tmp_path / ".publicacoes.json")
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH}})

    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)

    uploads: list[str] = []
    replies: list[tuple[str, str]] = []
    start_barrier = threading.Barrier(2)
    upload_started = threading.Event()
    allow_finish = threading.Event()

    def slow_yt(mp4, title, desc, token):
        uploads.append(mp4.name)
        upload_started.set()
        allow_finish.wait(timeout=5.0)
        return {"video_id": "yt-concurrent-1"}

    def worker() -> list[dict[str, Any]]:
        start_barrier.wait(timeout=5.0)
        return run_pending(
            store,
            "w-pc1",
            out_root=tmp_path,
            now=T0,
            tokens_path=tokens_path,
            publishers={"youtube": slow_yt},
            discord_post=lambda tid, msg: replies.append((tid, msg)),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(worker)
        f2 = pool.submit(worker)
        assert upload_started.wait(timeout=5.0)
        # Re-request while in-flight must not overwrite running -> pending
        res_inflight = request(store, "live1_c01", ALLOWED_USER, T0 + 1, out_root=tmp_path, tokens_path=tokens_path)
        assert "ja publicado" not in res_inflight
        allow_finish.set()
        out1 = f1.result(timeout=5.0)
        out2 = f2.result(timeout=5.0)

    assert len(uploads) == 1
    assert len(replies) == 1
    assert len(out1) + len(out2) == 1
    req_doc = store.get_request("live1_c01", "youtube")
    assert req_doc is not None and req_doc["status"] == "ok"


def test_concurrent_run_pending_mongo_store_claims_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """B3: two concurrent run_pending threads on MongoStore claim atomically and upload once."""
    import concurrent.futures
    import threading

    _make_clip_fixture(tmp_path, owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    _, db = _mongo_with_worker("w-pc1", "pc-mesa", T0)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH}})

    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)

    uploads: list[str] = []
    start_barrier = threading.Barrier(2)
    upload_started = threading.Event()
    allow_finish = threading.Event()

    def slow_yt(mp4, title, desc, token):
        uploads.append(mp4.name)
        upload_started.set()
        allow_finish.wait(timeout=5.0)
        return {"video_id": "yt-mongo-concurrent"}

    def worker() -> list[dict[str, Any]]:
        start_barrier.wait(timeout=5.0)
        return run_pending(
            store,
            "w-pc1",
            out_root=tmp_path,
            now=T0,
            tokens_path=tokens_path,
            publishers={"youtube": slow_yt},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(worker)
        f2 = pool.submit(worker)
        assert upload_started.wait(timeout=5.0)
        allow_finish.set()
        out1 = f1.result(timeout=5.0)
        out2 = f2.result(timeout=5.0)

    assert len(uploads) == 1
    assert len(out1) + len(out2) == 1
    doc = db.publish_requests.find_one({"_id": "live1_c01:youtube"})
    assert doc is not None and doc["status"] == "ok"


def test_mongo_store_claim_request_recusa_status_nao_pending():
    """MongoStore.claim_request must only transition documents whose status is `pending`."""
    _, db = _mongo_with_worker("w-pc1", "pc-mesa", T0)
    store = MongoStore(db)
    for st in ("running", "ok", "erro"):
        req_id = f"clip_{st}:youtube"
        db.publish_requests.insert_one({
            "_id": req_id,
            "clip_id": f"clip_{st}",
            "plataforma": "youtube",
            "owner": "w-pc1",
            "status": st,
        })
        assert store.claim_request(req_id, worker_id="w-pc1") is None
        saved = db.publish_requests.find_one({"_id": req_id})
        assert saved is not None and saved["status"] == st


def test_run_pending_before_ready_at_keeps_pending_for_later_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """B3 safety: claiming before ready_at must revert to pending so future run_pending executes."""
    ready_at = T0 + 3600
    _make_clip_fixture(tmp_path, owner="w-pc1", ready_at=None)
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    store = LocalStore(tmp_path / ".publicacoes.json")
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH}})

    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    delayed_policy = ChannelPolicy(delay_h={"gaules": 2.0})
    out_early = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        policy=delayed_policy,
        tokens_path=tokens_path,
        publishers={"youtube": lambda *a: {"video_id": "should-not-run"}},
    )
    assert out_early == []
    assert store.get_request("live1_c01", "youtube")["status"] == "pending"

    out_ready = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=ready_at + 7200,
        policy=delayed_policy,
        tokens_path=tokens_path,
        publishers={"youtube": lambda *a: {"video_id": "yt-after-wait"}},
    )
    assert len(out_ready) == 1
    assert out_ready[0]["status"] == "ok"


def test_empty_channel_is_blocked_by_allowed_policy_in_request_and_run_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """S2: clip with empty/unknown channel must not bypass policy.json allowed list."""
    clip_dir = tmp_path / "2026-11-17 - VOD [vod1]"
    clip_dir.mkdir(parents=True, exist_ok=True)
    (clip_dir / "clip_01.mp4").write_bytes(b"fake-mp4")
    (clip_dir / "clips.json").write_text(
        json.dumps({
            "video_id": "vod1",
            "title": "VOD sem canal",
            "owner": "w-pc1",
            "clips": [
                {
                    "index": 1,
                    "clip_id": "vod1_c01",
                    "file": "clip_01.mp4",
                    "title": "Sem canal",
                    "discord": {"thread_id": "thr-empty"},
                }
            ],
        }),
        encoding="utf-8",
    )
    (tmp_path / "policy.json").write_text(json.dumps({"allowed": ["gaules"]}), encoding="utf-8")

    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH}})

    store = LocalStore(tmp_path / ".publicacoes.json")
    res = request(store, "vod1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    assert res == "bloqueado pela politica"
    assert store.find_pending() == []

    # Even if a pending request already existed in store, run_pending must also block it
    store.enqueue_request({
        "_id": "vod1_c01:youtube",
        "clip_id": "vod1_c01",
        "plataforma": "youtube",
        "owner": "w-pc1",
        "pedido_por": ALLOWED_USER,
        "status": "pending",
        "resultado": None,
        "thread_id": "thr-empty",
        "criado_em": _dt_iso(T0),
        "concluido_em": None,
    })
    called = False

    def fake_yt(*args):
        nonlocal called
        called = True
        return {"video_id": "should-not-upload"}

    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": fake_yt},
    )
    assert called is False
    assert outcomes[0]["status"] == "erro"
    assert outcomes[0]["resultado"]["erro"] == "bloqueado pela politica"


def _dt_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None).isoformat()


def test_is_401_error_ignores_401_inside_clip_id_or_other_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """M4: substring '401' inside clip_id or arbitrary numbers must not trigger 401 refresh/erro_auth."""
    _make_clip_fixture(tmp_path, clip_id="clip-401-c01", owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    store = LocalStore(tmp_path / ".publicacoes.json")
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH}})

    refresh_calls = 0

    def fail_if_refresh(*args):
        nonlocal refresh_calls
        refresh_calls += 1
        return {"access_token": SECRET_ACCESS}

    request(store, "clip-401-c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": lambda *a: (_ for _ in ()).throw(RuntimeError("falha no clipe clip-401-c01"))},
        refreshers={"youtube": fail_if_refresh},
    )

    assert refresh_calls == 0
    assert outcomes[0]["status"] == "erro"
    assert "clip-401-c01" in outcomes[0]["resultado"]["erro"]


def test_run_pending_without_injected_publishers_refreshes_on_401_and_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Onda C: run_pending without injected publishers passes refresh=do_refresh to _default_publish.

    Simulates HTTP 401 on first attempt and 200 after do_refresh() with real
    publicar_youtube and publicar_tiktok modules, monkeypatched urlopen and oauth.refresh_*,
    validating status == 'ok', rotated token storage, and zero token leaks.
    """
    import io
    import urllib.error
    import urllib.request
    from email.message import Message

    from clipes import oauth, publicar_tiktok, publicar_youtube

    _make_clip_fixture(tmp_path, clip_id="live1_c01", owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)

    old_yt_token = "ya29.expired-yt-access-token-xyz"
    new_yt_token = "ya29.fresh-yt-access-token-abc"
    yt_refresh_token = "1//secret-google-refresh-token-123"

    old_tt_token = "act.expired-tiktok-access-token-xyz"
    new_tt_token = "act.fresh-tiktok-access-token-abc"
    tt_refresh_token = "rft.secret-tiktok-refresh-token-123"

    tokens_path = tmp_path / "tokens.json"
    _write_tokens(
        tokens_path,
        {
            "youtube": {
                "access_token": old_yt_token,
                "refresh_token": yt_refresh_token,
            },
            "tiktok": {
                "access_token": old_tt_token,
                "refresh_token": tt_refresh_token,
                "open_id": "op-user-1",
            },
        },
    )

    yt_refresh_calls = 0
    tt_refresh_calls = 0

    def fake_refresh_google(refresh_tok: str, tokens_path: Path | None = None) -> dict[str, str]:
        nonlocal yt_refresh_calls
        yt_refresh_calls += 1
        assert refresh_tok == yt_refresh_token
        return {"access_token": new_yt_token}

    def fake_refresh_tiktok(refresh_tok: str, tokens_path: Path | None = None) -> dict[str, str]:
        nonlocal tt_refresh_calls
        tt_refresh_calls += 1
        assert refresh_tok == tt_refresh_token
        return {"access_token": new_tt_token}

    monkeypatch.setattr(oauth, "refresh_google_tokens", fake_refresh_google)
    monkeypatch.setattr(oauth, "refresh_tiktok_tokens", fake_refresh_tiktok)
    monkeypatch.setattr("clipes.publicar_youtube.probe_duration", lambda _: 45.0)

    class _FakeHttpResp:
        def __init__(self, status: int, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None):
            self.status = status
            self._raw = json.dumps(body).encode("utf-8") if body is not None else b""
            self.headers = Message()
            for k, v in (headers or {}).items():
                self.headers[k] = v

        def read(self) -> bytes:
            return self._raw

        def getcode(self) -> int:
            return self.status

        def __enter__(self):
            return self

        def __exit__(self, *args: Any):
            return False

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeHttpResp:
        auth_header = str(req.headers.get("Authorization") or req.headers.get("authorization") or "")
        url = req.full_url

        # YouTube requests
        if "googleapis.com" in url:
            if auth_header == f"Bearer {old_yt_token}":
                msg = Message()
                fp = io.BytesIO(b'{"error": {"code": 401, "message": "Invalid Credentials"}}')
                raise urllib.error.HTTPError(url, 401, "HTTP 401 Unauthorized", msg, fp)
            assert auth_header == f"Bearer {new_yt_token}"
            if req.get_method() == "POST":
                return _FakeHttpResp(200, headers={"Location": "https://upload.youtube.test/session-yt-401"})
            raise AssertionError(f"Unexpected YouTube URL: {url}")
        if url == "https://upload.youtube.test/session-yt-401":
            assert req.get_method() == "PUT"
            assert auth_header == f"Bearer {new_yt_token}"
            return _FakeHttpResp(200, body={"id": "yt-vid-recovered"})

        # TikTok requests
        if "tiktokapis.com" in url:
            if auth_header == f"Bearer {old_tt_token}":
                msg = Message()
                fp = io.BytesIO(b'{"error": {"code": 401, "message": "Invalid Token"}}')
                raise urllib.error.HTTPError(url, 401, "HTTP 401 Unauthorized", msg, fp)
            assert auth_header == f"Bearer {new_tt_token}"
            if url.endswith("/creator_info/query/"):
                return _FakeHttpResp(200, body={
                    "data": {"privacy_level_options": ["SELF_ONLY"]},
                    "error": {"code": "ok", "message": ""},
                })
            if url.endswith("/video/init/"):
                return _FakeHttpResp(200, body={
                    "data": {"publish_id": "tt-pub-recovered", "upload_url": "https://upload.tiktok.test/sess-tt-401"},
                    "error": {"code": "ok", "message": ""},
                })
            if url.endswith("/status/fetch/"):
                return _FakeHttpResp(200, body={
                    "data": {"status": "PUBLISH_COMPLETE"},
                    "error": {"code": "ok", "message": ""},
                })
            raise AssertionError(f"Unexpected TikTok URL: {url}")
        if url == "https://upload.tiktok.test/sess-tt-401":
            assert req.get_method() == "PUT"
            return _FakeHttpResp(201)

        raise AssertionError(f"Unhandled request URL: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    yt_defaults = list(publicar_youtube.publish.__defaults__)
    yt_defaults[0] = fake_urlopen
    monkeypatch.setattr(publicar_youtube.publish, "__defaults__", tuple(yt_defaults))

    tt_defaults = list(publicar_tiktok.publish.__defaults__)
    tt_defaults[0] = fake_urlopen
    monkeypatch.setattr(publicar_tiktok.publish, "__defaults__", tuple(tt_defaults))

    store = LocalStore(tmp_path / ".publicacoes.json")
    req_res = request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    assert "publicacao enfileirada" in req_res

    thread_msgs: list[tuple[str, str]] = []
    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        discord_post=lambda tid, msg: thread_msgs.append((tid, msg)),
    )

    assert len(outcomes) == 2
    by_plat = {o["plataforma"]: o for o in outcomes}

    assert by_plat["youtube"]["status"] == "ok"
    assert by_plat["youtube"]["resultado"]["video_id"] == "yt-vid-recovered"
    assert "yt-vid-recovered" in by_plat["youtube"]["resultado"]["url"]

    assert by_plat["tiktok"]["status"] == "ok"
    assert by_plat["tiktok"]["resultado"]["publish_id"] == "tt-pub-recovered"
    assert by_plat["tiktok"]["resultado"]["privacidade"] == "SELF_ONLY"

    assert yt_refresh_calls == 1
    assert tt_refresh_calls == 1

    assert stat.S_IMODE(tokens_path.stat().st_mode) == 0o600

    assert len(thread_msgs) == 2
    for tid, msg in thread_msgs:
        assert tid == "thr-100"
        assert "ok" in msg

    all_secrets = [
        old_yt_token,
        new_yt_token,
        yt_refresh_token,
        old_tt_token,
        new_tt_token,
        tt_refresh_token,
    ]
    serialized_outcomes = json.dumps(outcomes)
    serialized_threads = json.dumps(thread_msgs)
    store_file_data = (tmp_path / ".publicacoes.json").read_text(encoding="utf-8")

    for secret in all_secrets:
        assert secret not in serialized_outcomes
        assert secret not in serialized_threads
        assert secret not in store_file_data


def test_publicar_do_discord_ok_sha_divergente_404_e_limpa_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import hashlib
    from clipes.discord_rest import DiscordRestError
    from clipes.registro import Registro

    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    _, db = _mongo_with_worker("w-pc1", "pc-mesa", T0)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"refresh_token": SECRET_REFRESH, "access_token": SECRET_ACCESS}})

    video_bytes = b"master-video-exact-bytes"
    video_sha = hashlib.sha256(video_bytes).hexdigest()
    signed_url = "https://cdn.discordapp.com/attachments/1/2/clip.mp4?ex=111&hm=signedsecret"

    clip_dir = tmp_path / "2026-11-18 - Live [v_disc]"
    clip_dir.mkdir(parents=True)
    (clip_dir / "clips.json").write_text(
        json.dumps({
            "video_id": "v_disc",
            "channel": "gaules",
            "owner": "w-pc1",
            "created_at": T0 - 10,
            "clips": [{"index": 1, "clip_id": "v_disc_c01", "file": "c1.mp4", "title": "Do Discord", "discord": {"thread_id": "thr-d", "message_id": "msg-d"}}],
        }),
        encoding="utf-8",
    )
    reg = Registro(tmp_path, mongo=db)
    reg.upsert({
        "clip_id": "v_disc_c01",
        "titulo": "Do Discord",
        "canal": "gaules",
        "criado_em": T0 - 10,
        "owner": "w-pc1",
        "arquivo": "2026-11-18 - Live [v_disc]/c1.mp4",
        "tamanho": len(video_bytes),
        "sha256": video_sha,
    })
    reg.set_discord("v_disc_c01", {
        "thread_id": "thr-d",
        "message_id": "msg-d",
        "attachment_id": "att-d",
        "filename": "c1.mp4",
        "size": len(video_bytes),
    })
    reg.marcar("v_disc_c01", status="so_discord", ack_em=T0 - 5)

    class FakeRest:
        def __init__(self, mode: str = "ok") -> None:
            self.mode = mode

        def get_message(self, channel_id: str, message_id: str) -> dict[str, Any]:
            if self.mode == "404":
                raise DiscordRestError("Discord HTTP 404: Unknown Message", status=404)
            if self.mode == "empty_att":
                return {"id": message_id, "attachments": []}
            return {
                "id": message_id,
                "attachments": [{"id": "att-d", "size": len(video_bytes), "url": signed_url}],
            }

        def download(self, url: str, dst: Path | None, max_bytes: int) -> tuple[int, str]:
            assert dst is not None
            data = b"tampered-bytes" if self.mode == "bad_sha" else video_bytes
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            return len(data), hashlib.sha256(data).hexdigest()

    seen_paths: list[Path] = []

    def pub_ok(mp4: Path, title: str, desc: str, tok: str) -> dict[str, Any]:
        seen_paths.append(mp4)
        assert mp4.parent == tmp_path / ".tmp_publicar"
        assert mp4.name.startswith("v_disc_c01_youtube_")
        assert mp4.read_bytes() == video_bytes
        return {"video_id": "yt_ok_1", "url": "https://youtube.com/shorts/yt_ok_1"}

    request(store, "v_disc_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": pub_ok},
        discord_post=lambda tid, msg: None,
        discord_client=FakeRest(mode="ok"),
    )
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "ok"
    assert len(seen_paths) == 1
    assert not seen_paths[0].exists()
    reg_after = reg.get("v_disc_c01")
    assert reg_after is not None
    assert reg_after["publicacoes"]["youtube"]["id"] == "yt_ok_1"
    assert "signedsecret" not in (tmp_path / "registro.json").read_text(encoding="utf-8")

    # Publisher fails -> temp file is still deleted in finally
    db.publish_requests.delete_many({})
    request(store, "v_disc_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    seen_fail: list[Path] = []

    def pub_fail(mp4: Path, title: str, desc: str, tok: str) -> dict[str, Any]:
        seen_fail.append(mp4)
        raise RuntimeError("upload crashed")

    outcomes_fail = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": pub_fail},
        discord_post=lambda tid, msg: None,
        discord_client=FakeRest(mode="ok"),
    )
    assert outcomes_fail[0]["status"] == "erro"
    assert len(seen_fail) == 1
    assert not seen_fail[0].exists()

    # Divergent sha256 -> aborts without publishing
    db.publish_requests.delete_many({})
    request(store, "v_disc_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    called_bad = False

    def pub_never(*args: Any) -> dict[str, Any]:
        nonlocal called_bad
        called_bad = True
        return {}

    outcomes_sha = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": pub_never},
        discord_post=lambda tid, msg: None,
        discord_client=FakeRest(mode="bad_sha"),
    )
    assert called_bad is False
    assert outcomes_sha[0]["status"] == "erro_arquivo"
    assert outcomes_sha[0]["message"] == "arquivo do Discord diferente do registrado"

    # Empty attachments (HTTP 200 []) or 404 -> marks perdido, erro_arquivo
    for mode in ("empty_att", "404"):
        reg.marcar("v_disc_c01", status="so_discord")
        db.publish_requests.delete_many({})
        request(store, "v_disc_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
        outcomes_lost = run_pending(
            store,
            "w-pc1",
            out_root=tmp_path,
            now=T0,
            tokens_path=tokens_path,
            publishers={"youtube": pub_never},
            discord_post=lambda tid, msg: None,
            discord_client=FakeRest(mode=mode),
        )
        assert outcomes_lost[0]["status"] == "erro_arquivo"
        assert outcomes_lost[0]["message"] == "clipe perdido: mensagem do Discord apagada"
        assert reg.get("v_disc_c01")["status"] == "perdido"


def test_run_pending_finishes_ok_even_if_registro_or_clips_json_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    import clipes.publicacao as pub_mod
    from clipes.registro import Registro

    _make_clip_fixture(tmp_path, owner="w-pc1")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", ALLOWED_USER)
    _, db = _mongo_with_worker("w-pc1", "pc-mesa", T0)
    store = MongoStore(db)
    tokens_path = tmp_path / "tokens.json"
    _write_tokens(tokens_path, {"youtube": {"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH}})

    uploads: list[str] = []

    def fake_yt(mp4, title, desc, token):
        uploads.append(mp4.name)
        return {"video_id": "yt-published-999"}

    monkeypatch.setattr(
        pub_mod,
        "_record_clip_publication",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("No space left on device")),
    )
    monkeypatch.setattr(
        Registro,
        "registrar_publicacao",
        lambda *a, **kw: (_ for _ in ()).throw(PermissionError("denied")),
    )

    thread_msgs: list[tuple[str, str]] = []
    request(store, "live1_c01", ALLOWED_USER, T0, out_root=tmp_path, tokens_path=tokens_path)
    outcomes = run_pending(
        store,
        "w-pc1",
        out_root=tmp_path,
        now=T0,
        tokens_path=tokens_path,
        publishers={"youtube": fake_yt},
        discord_post=lambda tid, msg: thread_msgs.append((tid, msg)),
    )

    assert len(uploads) == 1
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "ok"
    assert outcomes[0]["resultado"]["video_id"] == "yt-published-999"
    req_doc = db.publish_requests.find_one({"_id": "live1_c01:youtube"})
    assert req_doc is not None and req_doc["status"] == "ok"
    assert len(thread_msgs) == 1
    assert "yt-published-999" in thread_msgs[0][1]

    out = capsys.readouterr().out
    assert "[publicacao]" in out
    assert SECRET_ACCESS not in out
    assert SECRET_REFRESH not in out



