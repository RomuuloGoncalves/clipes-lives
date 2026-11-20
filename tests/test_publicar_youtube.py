"""Tests for YouTube Shorts resumable private publisher (clipes/publicar_youtube.py)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

from clipes.publicar_youtube import publish


class FakeResponse:
    def __init__(self, status: int, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None):
        self.status = status
        self._raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _http_error(url: str, code: int, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    fp = io.BytesIO(json.dumps(body).encode("utf-8") if body is not None else b"")
    return urllib.error.HTTPError(url, code, f"HTTP {code}", msg, fp)


def test_publish_youtube_happy_path_captures_private_json_and_title(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    payload_bytes = b"MP4-TEST-DATA-123456"
    mp4.write_bytes(payload_bytes)
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    fake_token = "ya29.super-secret-token"
    upload_url = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=sess123"

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        headers = {k.lower(): v for k, v in req.header_items()}
        calls.append((req.get_method(), req.full_url, headers, req.data))
        if req.get_method() == "POST":
            return FakeResponse(200, headers={"Location": upload_url})
        assert req.get_method() == "PUT"
        assert req.full_url == upload_url
        return FakeResponse(200, body={"id": "yt_vid_123"})

    result = publish(
        mp4,
        "Jogada absurda na live",
        "Descricao longa da jogada.",
        fake_token,
        fake_urlopen,
        probe_duration_fn=lambda _: 45.0,
    )

    assert result["status"] == "ok"
    assert result["video_id"] == "yt_vid_123"
    assert result["privacidade"] == "private"
    assert "yt_vid_123" in result["url"]
    assert fake_token not in json.dumps(result)

    assert len(calls) == 2
    post_method, post_url, post_headers, post_data = calls[0]
    assert post_method == "POST"
    assert "uploadType=resumable" in post_url
    assert post_headers["authorization"] == f"Bearer {fake_token}"
    sent_json = json.loads(post_data.decode("utf-8"))
    assert sent_json["status"]["privacyStatus"] == "private"
    assert sent_json["status"]["selfDeclaredMadeForKids"] is False
    assert sent_json["snippet"]["categoryId"] == "20"
    assert sent_json["snippet"]["title"] == "Jogada absurda na live"
    assert "#Shorts" in sent_json["snippet"]["description"]

    _, _, _, put_data = calls[1]
    assert put_data == payload_bytes


def test_publish_youtube_sanitizes_angle_brackets_and_truncates_title(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"MP4DATA")
    captured_json: list[dict[str, Any]] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        if req.get_method() == "POST":
            captured_json.append(json.loads(req.data.decode("utf-8")))
            return FakeResponse(200, headers={"Location": "https://upload.example/u1"})
        return FakeResponse(200, body={"id": "yt_99"})

    raw_title = "<" + ("a" * 95) + "> teste"
    res = publish(
        mp4,
        raw_title,
        "Desc #Shorts",
        "tok",
        fake_urlopen,
        probe_duration_fn=lambda _: 30.0,
    )

    assert res["status"] == "ok"
    sent_title = captured_json[0]["snippet"]["title"]
    assert "<" not in sent_title and ">" not in sent_title
    assert sent_title == ("a" * 95) + " test"
    assert len(sent_title) == 100


def test_publish_youtube_rejects_clip_over_180_seconds_without_http_call(tmp_path: Path):
    mp4 = tmp_path / "long.mp4"
    mp4.write_bytes(b"MP4DATA")
    called = False

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        nonlocal called
        called = True
        return FakeResponse(200)

    res = publish(
        mp4,
        "Clipe longo",
        "Desc",
        "tok",
        fake_urlopen,
        probe_duration_fn=lambda _: 181.0,
    )

    assert res["status"] == "erro"
    assert "clipe acima de 3 min" in res["erro"]
    assert called is False


def test_publish_youtube_resumes_after_put_drop_and_succeeds(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    payload = b"0123456789ABCDEF"
    mp4.write_bytes(payload)
    upload_url = "https://upload.example/resumable-session"
    put_requests: list[tuple[dict[str, str], bytes | None]] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        if req.get_method() == "POST":
            return FakeResponse(200, headers={"Location": upload_url})
        headers = {k.lower(): v for k, v in req.header_items()}
        put_requests.append((headers, req.data))
        if len(put_requests) == 1:
            raise ConnectionResetError("connection dropped mid-upload")
        if headers.get("content-range") == f"bytes */{len(payload)}":
            raise _http_error(upload_url, 308, headers={"Range": "bytes=0-5"})
        return FakeResponse(200, body={"id": "yt_resumed"})

    res = publish(
        mp4,
        "Clipe retomado",
        "Desc",
        "tok",
        fake_urlopen,
        probe_duration_fn=lambda _: 50.0,
        sleep=lambda _: None,
    )

    assert res["status"] == "ok"
    assert res["video_id"] == "yt_resumed"
    assert len(put_requests) == 3
    resumed_headers, resumed_body = put_requests[2]
    assert resumed_headers["content-range"] == f"bytes 6-{len(payload) - 1}/{len(payload)}"
    assert resumed_body == payload[6:]


def test_publish_youtube_fails_after_4_put_drops_without_leaking_secrets(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"0123456789")
    secret_token = "ya29.secret-never-leak"
    secret_upload_url = "https://upload.example/secret-upload-url-xyz"
    put_attempts = 0

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        nonlocal put_attempts
        if req.get_method() == "POST":
            return FakeResponse(200, headers={"Location": secret_upload_url})
        headers = {k.lower(): v for k, v in req.header_items()}
        if headers.get("content-range", "").startswith("bytes */"):
            return FakeResponse(308, headers={"Range": "bytes=0-1"})
        put_attempts += 1
        raise urllib.error.URLError("network reset")

    res = publish(
        mp4,
        "Clipe cai sempre",
        "Desc",
        secret_token,
        fake_urlopen,
        probe_duration_fn=lambda _: 40.0,
        sleep=lambda _: None,
    )

    assert res["status"] == "erro"
    assert put_attempts == 6  # 1 inicial + 5 retomadas (M-13)
    serialized = json.dumps(res)
    assert secret_token not in serialized
    assert secret_upload_url not in serialized


def test_publish_youtube_refreshes_once_on_401_and_returns_erro_auth_on_failure(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"MP4")
    expired_token = "ya29.expired-token"
    fresh_token = "ya29.fresh-token"
    seen_tokens: list[str] = []

    def fake_urlopen_recovers(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        headers = {k.lower(): v for k, v in req.header_items()}
        seen_tokens.append(headers["authorization"])
        if headers["authorization"] == f"Bearer {expired_token}":
            raise _http_error(req.full_url, 401, {"error": {"code": 401, "message": "Invalid Credentials"}})
        if req.get_method() == "POST":
            return FakeResponse(200, headers={"Location": "https://upload.example/u401"})
        return FakeResponse(200, body={"id": "yt_after_refresh"})

    refresh_calls = 0

    def fake_refresh() -> str:
        nonlocal refresh_calls
        refresh_calls += 1
        return fresh_token

    res_ok = publish(
        mp4,
        "Titulo",
        "Desc",
        lambda: expired_token,
        fake_urlopen_recovers,
        refresh=fake_refresh,
        probe_duration_fn=lambda _: 20.0,
    )
    assert res_ok["status"] == "ok"
    assert res_ok["video_id"] == "yt_after_refresh"
    assert refresh_calls == 1
    assert seen_tokens[0] == f"Bearer {expired_token}"
    assert seen_tokens[1] == f"Bearer {fresh_token}"

    # When 401 persists after refresh (or no refresh), must return erro_auth without leaking token
    def fake_urlopen_always_401(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        raise _http_error(req.full_url, 401)

    res_fail = publish(
        mp4,
        "Titulo",
        "Desc",
        fresh_token,
        fake_urlopen_always_401,
        refresh=lambda: fresh_token,
        probe_duration_fn=lambda _: 20.0,
    )
    assert res_fail["status"] == "erro_auth"
    assert "refaca clipes auth youtube" in res_fail["erro"]
    assert fresh_token not in json.dumps(res_fail)


def test_publish_youtube_strips_angle_brackets_from_description(tmp_path: Path):
    """M6: YouTube description must also strip '<' and '>' while keeping multiline text."""
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"MP4DATA")
    captured_json: list[dict[str, Any]] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        if req.get_method() == "POST":
            captured_json.append(json.loads(req.data.decode("utf-8")))
            return FakeResponse(200, headers={"Location": "https://upload.example/u-desc"})
        return FakeResponse(200, body={"id": "yt_desc"})

    res = publish(
        mp4,
        "Titulo",
        "Linha <um>\n\nLinha >dois<",
        "tok",
        fake_urlopen,
        probe_duration_fn=lambda _: 25.0,
    )

    assert res["status"] == "ok"
    sent_desc = captured_json[0]["snippet"]["description"]
    assert "<" not in sent_desc and ">" not in sent_desc
    assert sent_desc == "Linha um\n\nLinha dois\n\n#Shorts"


def test_publish_youtube_privacy_env_and_invalid_fallback(tmp_path: Path, monkeypatch):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"MP4DATA")
    captured_json: list[dict[str, Any]] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        if req.get_method() == "POST":
            captured_json.append(json.loads(req.data.decode("utf-8")))
            return FakeResponse(200, headers={"Location": "https://upload.example/u-priv"})
        return FakeResponse(200, body={"id": "yt_priv"})

    monkeypatch.setenv("CLIPES_YT_PRIVACY", "public")
    res_pub = publish(mp4, "Gancho forte", "Desc", "tok", fake_urlopen, probe_duration_fn=lambda _: 25.0)
    assert res_pub["privacidade"] == "public"
    assert captured_json[0]["status"]["privacyStatus"] == "public"
    assert captured_json[0]["snippet"]["title"] == "Gancho forte"

    monkeypatch.setenv("CLIPES_YT_PRIVACY", "unlisted")
    res_unl = publish(mp4, "Gancho forte", "Desc", "tok", fake_urlopen, probe_duration_fn=lambda _: 25.0)
    assert res_unl["privacidade"] == "unlisted"
    assert captured_json[1]["status"]["privacyStatus"] == "unlisted"

    monkeypatch.setenv("CLIPES_YT_PRIVACY", "invalid_value")
    res_inv = publish(mp4, "Gancho forte", "Desc", "tok", fake_urlopen, probe_duration_fn=lambda _: 25.0)
    assert res_inv["privacidade"] == "private"
    assert captured_json[2]["status"]["privacyStatus"] == "private"
