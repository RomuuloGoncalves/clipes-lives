"""Tests for Discord REST v10 client (DISC-01, DISC-03)."""

from __future__ import annotations

import email
import io
import json
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from clipes.discord_rest import DiscordRest, DiscordRestError


class _FakeResponse:
    def __init__(self, status: int, body: dict | list | bytes, headers: dict[str, str] | None = None):
        self.status = status
        self._raw = json.dumps(body).encode("utf-8") if isinstance(body, (dict, list)) else body
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


def test_get_message_200_404_and_429():
    token = "secret-discord-token-get"
    sleeps: list[float] = []
    calls = 0

    def fake_urlopen(req, timeout=None):
        nonlocal calls
        calls += 1
        assert req.get_method() == "GET"
        assert req.get_header("Authorization") == f"Bot {token}"
        if calls == 1:
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests",
                hdrs={}, fp=io.BytesIO(json.dumps({"retry_after": 0.2}).encode("utf-8")),
            )
        if calls == 2:
            return _FakeResponse(200, {
                "id": "msg_1",
                "attachments": [{"id": "att_1", "size": 123, "url": "https://cdn.discordapp.com/x.mp4?ex=1&hm=2"}],
            })
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found",
            hdrs={}, fp=io.BytesIO(json.dumps({"code": 10008, "message": "Unknown Message"}).encode("utf-8")),
        )

    rest = DiscordRest(token, urlopen=fake_urlopen, sleep=sleeps.append)
    msg = rest.get_message("thr_1", "msg_1")
    assert msg["id"] == "msg_1"
    assert msg["attachments"][0]["id"] == "att_1"
    assert sleeps == [0.2]

    with pytest.raises(DiscordRestError) as exc_info:
        rest.get_message("thr_1", "msg_missing")
    assert exc_info.value.status == 404
    assert "404" in str(exc_info.value)


def test_download_streams_checks_limit_and_never_leaks_signed_url(tmp_path: Path):
    import hashlib

    token = "secret-token-dl"
    signed_url = "https://cdn.discordapp.com/attachments/1/2/clip.mp4?ex=6789abcd&is=67890000&hm=deadbeefsecret"
    payload = b"hello-discord-mp4-" * 200
    expected_sha = hashlib.sha256(payload).hexdigest()

    def fake_urlopen_ok(req, timeout=None):
        assert req.full_url == signed_url
        return _FakeResponse(200, payload)

    rest = DiscordRest(token, urlopen=fake_urlopen_ok, sleep=lambda _: None)

    # Stream without writing to disk (dst=None)
    size_mem, sha_mem = rest.download(signed_url, None, max_bytes=len(payload) + 10)
    assert size_mem == len(payload)
    assert sha_mem == expected_sha

    # Stream to destination file
    dst = tmp_path / "dl" / "clip.mp4"
    size_disk, sha_disk = rest.download(signed_url, dst, max_bytes=len(payload))
    assert size_disk == len(payload)
    assert sha_disk == expected_sha
    assert dst.read_bytes() == payload

    # Exceeding max_bytes aborts, removes temp, and hides query string
    dst_big = tmp_path / "dl" / "too_big.mp4"
    with pytest.raises(DiscordRestError) as exc_big:
        rest.download(signed_url, dst_big, max_bytes=len(payload) - 1)
    assert not dst_big.exists()
    assert list(dst_big.parent.glob(".too_big.mp4.tmp.*")) == []
    assert "deadbeefsecret" not in str(exc_big.value)
    assert "ex=6789abcd" not in str(exc_big.value)

    # Network error / 404 also hides signed query string
    def fake_urlopen_err(req, timeout=None):
        raise urllib.error.URLError(f"failed fetching {signed_url}")

    rest_err = DiscordRest(token, urlopen=fake_urlopen_err, sleep=lambda _: None)
    with pytest.raises(DiscordRestError) as exc_net:
        rest_err.download(signed_url, None, max_bytes=1024)
    assert "deadbeefsecret" not in str(exc_net.value)
    assert "hm=" not in str(exc_net.value)


def test_post_includes_components_in_json_and_multipart(tmp_path: Path):
    token = "secret-discord-token-comp"
    captured: list[Any] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _FakeResponse(200, {"id": f"msg_{len(captured)}"})

    rest = DiscordRest(token, urlopen=fake_urlopen, sleep=lambda _: None)
    components = [
        {"type": 1, "components": [{"type": 2, "style": 3, "label": "Aprovar", "custom_id": "aprovar:vid_c01"}]}
    ]

    # Without file (JSON)
    res_json = rest.post("thr_1", "texto clipe", components=components)
    assert res_json["id"] == "msg_1"
    req_json = captured[0]
    assert req_json.get_header("Content-type") == "application/json"
    assert json.loads(req_json.data.decode("utf-8")) == {
        "content": "texto clipe",
        "components": components,
    }

    # With file (multipart)
    clip_file = tmp_path / "clip.mp4"
    clip_file.write_bytes(b"mp4bytes")
    res_mp = rest.post("thr_1", "texto com anexo", file=clip_file, components=components)
    assert res_mp["id"] == "msg_2"
    req_mp = captured[1]
    ctype = req_mp.get_header("Content-type")
    assert ctype and ctype.startswith("multipart/form-data; boundary=")
    parts = _parse_multipart(ctype, req_mp.data)
    assert json.loads(parts["payload_json"][1].decode("utf-8")) == {
        "content": "texto com anexo",
        "components": components,
    }


def test_post_and_download_wrap_raw_oserror_without_leaking_secrets(tmp_path: Path):
    token = "SUPER_SECRET_RAW_OSERROR_TOK"
    signed_url = "https://cdn.discordapp.com/attachments/1/2/clip.mp4?ex=111&hm=signedsecret999"

    def urlopen_broken(req, timeout=None):
        raise BrokenPipeError(f"broken pipe token={token}")

    rest_post = DiscordRest(token, urlopen=urlopen_broken, sleep=lambda _: None)
    clip_file = tmp_path / "clip.mp4"
    clip_file.write_bytes(b"mp4bytes")
    with pytest.raises(DiscordRestError) as exc_post:
        rest_post.post("thr_1", "aviso", file=clip_file)
    assert isinstance(exc_post.value.__cause__, BrokenPipeError)
    assert token not in str(exc_post.value)

    def urlopen_reset(req, timeout=None):
        raise ConnectionResetError(f"reset fetching {signed_url}")

    rest_dl = DiscordRest(token, urlopen=urlopen_reset, sleep=lambda _: None)
    with pytest.raises(DiscordRestError) as exc_dl:
        rest_dl.download(signed_url, None, max_bytes=1024)
    assert isinstance(exc_dl.value.__cause__, ConnectionResetError)
    assert "signedsecret999" not in str(exc_dl.value)
    assert "hm=" not in str(exc_dl.value)


def test_edit_patch_content_and_components():
    token = "secret-discord-token-edit"
    captured: list[Any] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _FakeResponse(200, {"id": "msg_999", "content": "updated"})

    rest = DiscordRest(token, urlopen=fake_urlopen, sleep=lambda _: None)

    # Content only (omits components so existing components stay untouched)
    card_text = "**gaules** — CS2\nClipes: 3\n🔴 ao vivo\nhttps://twitch.tv/gaules"
    res1 = rest.edit("157241", "msg_999", card_text)
    assert res1["id"] == "msg_999"
    r1 = captured[0]
    assert r1.get_method() == "PATCH"
    assert r1.full_url.endswith("/channels/157241/messages/msg_999")
    assert r1.get_header("Authorization") == f"Bot {token}"
    assert r1.get_header("Content-type") == "application/json"
    body1 = json.loads(r1.data.decode("utf-8"))
    assert body1 == {"content": card_text}
    assert "components" not in body1

    # Explicit empty components=[] removes buttons (e.g., Descartar)
    res2 = rest.edit("thr_777", "msg_clip_01", "🗑️ descartado · titulo", components=[])
    assert res2["id"] == "msg_999"
    r2 = captured[1]
    assert r2.get_method() == "PATCH"
    assert r2.full_url.endswith("/channels/thr_777/messages/msg_clip_01")
    assert r2.get_header("Content-type") == "application/json"
    assert json.loads(r2.data.decode("utf-8")) == {
        "content": "🗑️ descartado · titulo",
        "components": [],
    }

    # Explicit non-empty components list replaces components
    comps = [
        {"type": 1, "components": [{"type": 2, "style": 2, "label": "Descartar", "custom_id": "descartar:c01"}]}
    ]
    rest.edit(157241, 888, "🗓️ agendado 20:00", components=comps)
    r3 = captured[2]
    assert r3.get_method() == "PATCH"
    assert r3.full_url.endswith("/channels/157241/messages/888")
    assert json.loads(r3.data.decode("utf-8")) == {
        "content": "🗓️ agendado 20:00",
        "components": comps,
    }


def test_edit_429_retries_with_retry_after_and_exhaustion_hides_token():
    token = "SUPER_SECRET_EDIT_429_TOKEN"
    sleeps: list[float] = []
    calls = 0

    def fake_urlopen_recover(req, timeout=None):
        nonlocal calls
        calls += 1
        assert req.get_method() == "PATCH"
        if calls == 1:
            raise urllib.error.HTTPError(
                req.full_url,
                429,
                "Too Many Requests",
                hdrs={},
                fp=io.BytesIO(json.dumps({"retry_after": 0.3}).encode("utf-8")),
            )
        return _FakeResponse(200, {"id": "msg_edited"})

    rest = DiscordRest(token, urlopen=fake_urlopen_recover, sleep=sleeps.append)
    res = rest.edit("157241", "msg_edited", "✅ encerrada")
    assert res == {"id": "msg_edited"}
    assert calls == 2
    assert sleeps == [0.3]

    # Exhausting 429 retries raises DiscordRestError(status=429) without leaking token
    exhaust_sleeps: list[float] = []
    exhaust_calls = 0

    def fake_urlopen_always_429(req, timeout=None):
        nonlocal exhaust_calls
        exhaust_calls += 1
        raise urllib.error.HTTPError(
            req.full_url,
            429,
            f"Rate limited {token}",
            hdrs={},
            fp=io.BytesIO(json.dumps({"retry_after": 0.15, "token": token}).encode("utf-8")),
        )

    rest_fail = DiscordRest(token, urlopen=fake_urlopen_always_429, sleep=exhaust_sleeps.append)
    with pytest.raises(DiscordRestError) as exc_info:
        rest_fail.edit("157241", "msg_edited", "✅ encerrada")
    assert exhaust_calls == 4
    assert exhaust_sleeps == [0.15, 0.15, 0.15]
    assert exc_info.value.status == 429
    assert token not in str(exc_info.value)


def test_edit_404_raises_with_status_code_and_masks_token():
    token = "SUPER_SECRET_EDIT_404_TOKEN"

    def fake_urlopen_404(req, timeout=None):
        assert req.get_method() == "PATCH"
        raise urllib.error.HTTPError(
            req.full_url,
            404,
            "Not Found",
            hdrs={},
            fp=io.BytesIO(
                json.dumps({"code": 10008, "message": f"Unknown Message ({token})"}).encode("utf-8")
            ),
        )

    rest = DiscordRest(token, urlopen=fake_urlopen_404, sleep=lambda _: None)
    with pytest.raises(DiscordRestError) as exc_info:
        rest.edit("157241", "msg_deleted", "novo texto")

    assert exc_info.value.status == 404
    assert exc_info.value.code == 10008
    err_text = str(exc_info.value)
    assert "404" in err_text
    assert token not in err_text


def test_list_guild_channels_e_create_text_channel():
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.get_method(), req.full_url, req.data))
        if req.get_method() == "GET":
            return _FakeResponse(200, [{"id": "1", "name": "bot-log"}])
        return _FakeResponse(200, {"id": "2"})

    rest = DiscordRest("tok", urlopen=fake_urlopen, sleep=lambda _: None)
    assert rest.list_guild_channels("g")[0]["name"] == "bot-log"
    assert rest.create_text_channel("g", "bot-log")["id"] == "2"
    assert calls[0][:2] == ("GET", "https://discord.com/api/v10/guilds/g/channels")
    assert json.loads(calls[1][2]) == {"name": "bot-log", "type": 0}
