"""Tests for Discord REST v10 client (DISC-01, DISC-03)."""

from __future__ import annotations

import email
import io
import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from clipes.discord_rest import DiscordRest, DiscordRestError


class _FakeResponse:
    def __init__(self, status: int, body: dict | bytes, headers: dict[str, str] | None = None):
        self.status = status
        self._raw = json.dumps(body).encode("utf-8") if isinstance(body, dict) else body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._raw

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _parse_multipart(content_type: str, body: bytes) -> dict[str, tuple[str | None, bytes]]:
    raw_msg = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    msg = email.message_from_bytes(raw_msg)
    assert msg.is_multipart()
    parts: dict[str, tuple[str | None, bytes]] = {}
    for part in msg.get_payload():
        assert isinstance(part, Message)
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if name:
            parts[name] = (filename, payload)
    return parts


def test_post_text_and_thread_from_headers_and_payload():
    token = "secret-discord-token-123"
    requests_seen = []

    def fake_urlopen(req, timeout=None):
        requests_seen.append(req)
        if req.full_url.endswith("/channels/157241/messages"):
            return _FakeResponse(200, {"id": "msg_999"})
        if req.full_url.endswith("/channels/157241/messages/msg_999/threads"):
            return _FakeResponse(201, {"id": "thr_777"})
        raise AssertionError(f"Unexpected URL: {req.full_url}")

    rest = DiscordRest(token, urlopen=fake_urlopen, sleep=lambda _: None)

    msg_res = rest.post("157241", "Live do canal X · 2 clipes")
    assert msg_res["id"] == "msg_999"

    thr_id = rest.thread_from("157241", msg_res["id"], "canal · titulo da live")
    assert thr_id == "thr_777"

    assert len(requests_seen) == 2
    r1, r2 = requests_seen
    assert r1.get_header("Authorization") == f"Bot {token}"
    assert r2.get_header("Authorization") == f"Bot {token}"
    assert json.loads(r1.data.decode("utf-8")) == {"content": "Live do canal X · 2 clipes"}
    assert json.loads(r2.data.decode("utf-8")) == {
        "name": "canal · titulo da live",
        "auto_archive_duration": 1440,
    }


def test_post_multipart_with_file_sends_payload_json_and_files0(tmp_path: Path):
    token = "secret-discord-token-456"
    clip_file = tmp_path / "discord_clip_01.mp4"
    clip_bytes = b"\x00\x00\x00\x18ftypmp42fake-video-bytes"
    clip_file.write_bytes(clip_bytes)

    captured = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _FakeResponse(200, {"id": "msg_clip_01"})

    rest = DiscordRest(token, urlopen=fake_urlopen, sleep=lambda _: None)
    res = rest.post("thr_777", "clip_id: abc_c01", file=clip_file)

    assert res["id"] == "msg_clip_01"
    assert len(captured) == 1
    req = captured[0]
    assert req.full_url.endswith("/channels/thr_777/messages")
    assert req.get_header("Authorization") == f"Bot {token}"

    ctype = req.get_header("Content-type")
    assert ctype and ctype.startswith("multipart/form-data; boundary=")
    parts = _parse_multipart(ctype, req.data)
    assert "payload_json" in parts
    assert "files[0]" in parts
    assert json.loads(parts["payload_json"][1].decode("utf-8")) == {"content": "clip_id: abc_c01"}
    assert parts["files[0]"][0] == "discord_clip_01.mp4"
    assert parts["files[0]"][1] == clip_bytes


def test_rate_limit_429_retries_with_retry_after_and_succeeds():
    token = "secret-discord-token-789"
    sleeps: list[float] = []
    attempts = 0

    def fake_urlopen(req, timeout=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests",
                hdrs={}, fp=io.BytesIO(json.dumps({"retry_after": 0.25}).encode("utf-8")),
            )
        if attempts == 2:
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests",
                hdrs={}, fp=io.BytesIO(json.dumps({"retry_after": 0.5}).encode("utf-8")),
            )
        return _FakeResponse(200, {"id": "msg_ok"})

    rest = DiscordRest(token, urlopen=fake_urlopen, sleep=sleeps.append)
    res = rest.post("157241", "ok after retries")

    assert res == {"id": "msg_ok"}
    assert attempts == 3
    assert sleeps == [0.25, 0.5]


def test_rate_limit_429_four_times_raises_without_leaking_token():
    token = "SUPER_SECRET_BOT_TOKEN_XYZ"
    sleeps: list[float] = []
    attempts = 0

    def fake_urlopen(req, timeout=None):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(
            req.full_url, 429, f"Rate limited {token}",
            hdrs={}, fp=io.BytesIO(json.dumps({"retry_after": 0.1, "echo": token}).encode("utf-8")),
        )

    rest = DiscordRest(token, urlopen=fake_urlopen, sleep=sleeps.append)
    with pytest.raises(DiscordRestError) as exc_info:
        rest.post("157241", "will fail")

    assert attempts == 4
    assert sleeps == [0.1, 0.1, 0.1]
    err_text = str(exc_info.value)
    assert "429" in err_text
    assert token not in err_text


def test_http_error_does_not_leak_token():
    token = "TOKEN_SHOULD_NEVER_APPEAR_IN_ERR"

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized",
            hdrs={}, fp=io.BytesIO(json.dumps({"message": f"bad token {token}", "code": 0}).encode("utf-8")),
        )

    rest = DiscordRest(token, urlopen=fake_urlopen, sleep=lambda _: None)
    with pytest.raises(DiscordRestError) as exc_info:
        rest.post("157241", "hi")

    err_text = str(exc_info.value)
    assert "401" in err_text
    assert token not in err_text
