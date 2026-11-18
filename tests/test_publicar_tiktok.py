"""Tests for TikTok Content Posting API v2 private publisher (clipes/publicar_tiktok.py)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

from clipes.publicar_tiktok import publish


class FakeResponse:
    def __init__(self, status: int, body: dict[str, Any] | None = None):
        self.status = status
        self._raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.headers = Message()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _http_error(url: str, code: int, body: dict[str, Any] | None = None) -> urllib.error.HTTPError:
    fp = io.BytesIO(json.dumps(body).encode("utf-8") if body is not None else b"")
    return urllib.error.HTTPError(url, code, f"HTTP {code}", Message(), fp)


def test_publish_tiktok_happy_path_until_publish_complete(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    payload_bytes = b"TIKTOK-MP4-BYTES"
    mp4.write_bytes(payload_bytes)
    fake_token = "act.secret-tiktok-token"
    upload_url = "https://open-upload.tiktokapis.com/upload/?token=secret-upload"
    seen: list[tuple[str, str, dict[str, str], bytes | None]] = []
    sleeps: list[float] = []
    status_polls = 0

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        nonlocal status_polls
        headers = {k.lower(): v for k, v in req.header_items()}
        seen.append((req.get_method(), req.full_url, headers, req.data))
        if req.full_url.endswith("/creator_info/query/"):
            return FakeResponse(200, {
                "data": {"privacy_level_options": ["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS"]},
                "error": {"code": "ok", "message": ""},
            })
        if req.full_url.endswith("/video/init/"):
            return FakeResponse(200, {
                "data": {"publish_id": "v_pub_123", "upload_url": upload_url},
                "error": {"code": "ok", "message": ""},
            })
        if req.full_url == upload_url:
            assert req.get_method() == "PUT"
            return FakeResponse(201)
        if req.full_url.endswith("/status/fetch/"):
            status_polls += 1
            st = "PROCESSING_UPLOAD" if status_polls == 1 else "PUBLISH_COMPLETE"
            return FakeResponse(200, {
                "data": {"status": st},
                "error": {"code": "ok", "message": ""},
            })
        raise AssertionError(f"Unexpected URL: {req.full_url}")

    long_caption = "x" * 2300
    res = publish(mp4, long_caption, fake_token, fake_urlopen, sleeps.append)

    assert res["status"] == "ok"
    assert res["publish_id"] == "v_pub_123"
    assert res["privacidade"] == "SELF_ONLY"
    serialized = json.dumps(res)
    assert fake_token not in serialized
    assert upload_url not in serialized

    assert len(seen) == 5  # creator_info, init, PUT, status(1), status(2)
    assert sleeps == [5]

    _, _, init_headers, init_data = seen[1]
    assert init_headers["authorization"] == f"Bearer {fake_token}"
    init_json = json.loads(init_data.decode("utf-8"))
    assert init_json["post_info"]["privacy_level"] == "SELF_ONLY"
    assert init_json["post_info"]["title"] == "x" * 2200
    assert init_json["source_info"] == {
        "source": "FILE_UPLOAD",
        "video_size": len(payload_bytes),
        "chunk_size": len(payload_bytes),
        "total_chunk_count": 1,
    }

    _, _, put_headers, put_data = seen[2]
    assert put_headers["content-range"] == f"bytes 0-{len(payload_bytes) - 1}/{len(payload_bytes)}"
    assert put_data == payload_bytes


def test_publish_tiktok_status_failed_returns_erro(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"MP4")
    fake_token = "act.secret-token-fail"
    upload_url = "https://open-upload.tiktokapis.com/upload/fail-secret"

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        if req.full_url.endswith("/creator_info/query/"):
            return FakeResponse(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if req.full_url.endswith("/video/init/"):
            return FakeResponse(200, {"data": {"publish_id": "v_pub_fail", "upload_url": upload_url}, "error": {"code": "ok"}})
        if req.full_url == upload_url:
            return FakeResponse(200)
        return FakeResponse(200, {"data": {"status": "FAILED", "fail_reason": "file_format_check_failed"}, "error": {"code": "ok"}})

    res = publish(mp4, "Legenda", fake_token, fake_urlopen, lambda _: None)

    assert res["status"] == "erro"
    assert res["publish_id"] == "v_pub_fail"
    serialized = json.dumps(res)
    assert fake_token not in serialized
    assert upload_url not in serialized


def test_publish_tiktok_polls_at_most_60_times_sleeping_5s_between(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"MP4")
    upload_url = "https://open-upload.tiktokapis.com/upload/timeout"
    sleeps: list[float] = []
    polls = 0

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        nonlocal polls
        if req.full_url.endswith("/creator_info/query/"):
            return FakeResponse(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if req.full_url.endswith("/video/init/"):
            return FakeResponse(200, {"data": {"publish_id": "v_pub_slow", "upload_url": upload_url}, "error": {"code": "ok"}})
        if req.full_url == upload_url:
            return FakeResponse(200)
        polls += 1
        return FakeResponse(200, {"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}})

    res = publish(mp4, "Legenda", "tok", fake_urlopen, sleeps.append)

    assert res["status"] == "erro"
    assert polls == 60
    assert sleeps == [5] * 59


def test_publish_tiktok_unaudited_or_missing_self_only_returns_erro_conta(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"MP4")

    # Case 1: unaudited_client_can_only_post_to_private_accounts returned by init
    def fake_urlopen_unaudited(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        if req.full_url.endswith("/creator_info/query/"):
            return FakeResponse(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        raise _http_error(
            req.full_url,
            403,
            {"error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "Account must be private"}},
        )

    res1 = publish(mp4, "Legenda", "tok", fake_urlopen_unaudited, lambda _: None)
    assert res1["status"] == "erro_conta"
    assert "deixe a conta do TikTok como privada" in res1["erro"]

    # Case 2: creator_info does not list SELF_ONLY -> stops before init/upload
    init_called = False

    def fake_urlopen_no_self_only(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        nonlocal init_called
        if req.full_url.endswith("/creator_info/query/"):
            return FakeResponse(200, {"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}, "error": {"code": "ok"}})
        init_called = True
        return FakeResponse(200)

    res2 = publish(mp4, "Legenda", "tok", fake_urlopen_no_self_only, lambda _: None)
    assert res2["status"] == "erro_conta"
    assert "deixe a conta do TikTok como privada" in res2["erro"]
    assert init_called is False


def test_publish_tiktok_refreshes_once_on_401_and_returns_erro_auth_on_failure(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"MP4")
    expired_token = "act.expired"
    fresh_token = "act.fresh"
    upload_url = "https://open-upload.tiktokapis.com/upload/ok401"
    refresh_calls = 0

    def fake_urlopen_recovers(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        headers = {k.lower(): v for k, v in req.header_items()}
        if req.full_url != upload_url and headers.get("authorization") == f"Bearer {expired_token}":
            raise _http_error(req.full_url, 401, {"error": {"code": "access_token_invalid"}})
        if req.full_url.endswith("/creator_info/query/"):
            return FakeResponse(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if req.full_url.endswith("/video/init/"):
            return FakeResponse(200, {"data": {"publish_id": "v_pub_401", "upload_url": upload_url}, "error": {"code": "ok"}})
        if req.full_url == upload_url:
            return FakeResponse(200)
        return FakeResponse(200, {"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}})

    def fake_refresh() -> str:
        nonlocal refresh_calls
        refresh_calls += 1
        return fresh_token

    res_ok = publish(
        mp4,
        "Legenda",
        lambda: expired_token,
        fake_urlopen_recovers,
        lambda _: None,
        refresh=fake_refresh,
    )
    assert res_ok["status"] == "ok"
    assert res_ok["publish_id"] == "v_pub_401"
    assert refresh_calls == 1

    def fake_urlopen_always_401(req: urllib.request.Request, timeout: float = 30) -> FakeResponse:
        raise _http_error(req.full_url, 401, {"error": {"code": "access_token_invalid"}})

    res_fail = publish(
        mp4,
        "Legenda",
        fresh_token,
        fake_urlopen_always_401,
        lambda _: None,
        refresh=lambda: fresh_token,
    )
    assert res_fail["status"] == "erro_auth"
    assert "refaca clipes auth tiktok" in res_fail["erro"]
    assert fresh_token not in json.dumps(res_fail)
