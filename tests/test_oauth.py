"""Tests for OAuth token storage, refresh, and local auth flow (AUTH-01, PUB-04)."""

from __future__ import annotations

import io
import json
import stat
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from clipes.oauth import (
    OAuthError,
    auth_flow,
    load_tokens,
    refresh_google,
    refresh_tiktok,
    save_tokens,
)


class _FakeResp:
    def __init__(self, body: dict):
        self._raw = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_save_and_load_tokens_mode_600(tmp_path: Path):
    tokens_path = tmp_path / "cfg" / "tokens.json"
    data = {"youtube": {"refresh_token": "yt-ref-1"}, "tiktok": {"refresh_token": "tt-ref-1", "open_id": "oid-1"}}

    saved = save_tokens(data, tokens_path)
    assert saved == tokens_path
    assert (tokens_path.stat().st_mode & 0o777) == 0o600
    assert load_tokens(tokens_path) == data
    assert load_tokens(tmp_path / "missing.json") == {}


def test_auth_flow_wrong_state_aborts_without_code_exchange(tmp_path: Path):
    tokens_path = tmp_path / "tokens.json"
    token_requests = []

    def fake_urlopen(req, timeout=None):
        token_requests.append(req)
        return _FakeResp({"refresh_token": "should-not-happen"})

    def fake_serve(auth_url: str) -> dict[str, str]:
        return {"code": "code-123", "state": "wrong-state-value"}

    with pytest.raises(OAuthError, match="state"):
        auth_flow(
            "youtube",
            serve=fake_serve,
            client_id="yt-cid",
            client_secret="yt-csec",
            tokens_path=tokens_path,
            urlopen=fake_urlopen,
            printer=lambda _: None,
        )

    assert token_requests == []
    assert not tokens_path.exists()


def test_auth_flow_youtube_local_server_saves_mode_600_and_prints_only_token_salvo(tmp_path: Path):
    tokens_path = tmp_path / "tokens.json"
    printed: list[str] = []
    token_requests = []
    secret_refresh = "yt-secret-refresh-xyz"
    secret_access = "yt-secret-access-xyz"

    def fake_open_url(auth_url: str) -> None:
        parsed = urllib.parse.urlparse(auth_url)
        qs = urllib.parse.parse_qs(parsed.query)
        redirect_uri = qs["redirect_uri"][0]
        state = qs["state"][0]
        assert redirect_uri.startswith("http://127.0.0.1:")
        cb_url = f"{redirect_uri}?{urllib.parse.urlencode({'code': 'auth-code-yt', 'state': state})}"
        with urllib.request.urlopen(cb_url, timeout=5) as resp:
            assert resp.status == 200

    def fake_urlopen(req, timeout=None):
        token_requests.append(req)
        return _FakeResp({
            "access_token": secret_access,
            "refresh_token": secret_refresh,
            "expires_in": 3600,
        })

    saved = auth_flow(
        "youtube",
        open_url=fake_open_url,
        port=0,
        client_id="yt-cid",
        client_secret="yt-csec",
        tokens_path=tokens_path,
        urlopen=fake_urlopen,
        printer=printed.append,
    )

    assert saved == tokens_path
    assert stat.S_IMODE(tokens_path.stat().st_mode) == 0o600
    assert load_tokens(tokens_path) == {"youtube": {"refresh_token": secret_refresh}}

    assert len(token_requests) == 1
    body = urllib.parse.parse_qs(token_requests[0].data.decode("utf-8"))
    assert body["code"] == ["auth-code-yt"]
    assert body["grant_type"] == ["authorization_code"]

    assert len(printed) == 2
    assert printed[0].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "state=" in printed[0]
    assert printed[1] == "token salvo"
    all_output = "\n".join(printed)
    assert secret_refresh not in all_output
    assert secret_access not in all_output
    assert "yt-csec" not in all_output


def test_auth_flow_tiktok_saves_refresh_and_open_id_mode_600(tmp_path: Path):
    tokens_path = tmp_path / "tokens.json"
    printed: list[str] = []

    def fake_serve(auth_url: str) -> tuple[str, str]:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)
        return ("tt-code-1", qs["state"][0])

    def fake_urlopen(req, timeout=None):
        assert req.full_url == "https://open.tiktokapis.com/v2/oauth/token/"
        return _FakeResp({
            "access_token": "tt-acc-1",
            "refresh_token": "tt-ref-1",
            "open_id": "tt-user-99",
        })

    auth_flow(
        "tiktok",
        serve=fake_serve,
        client_id="tt-key",
        client_secret="tt-sec",
        tokens_path=tokens_path,
        urlopen=fake_urlopen,
        printer=printed.append,
    )

    assert stat.S_IMODE(tokens_path.stat().st_mode) == 0o600
    assert load_tokens(tokens_path) == {
        "tiktok": {"refresh_token": "tt-ref-1", "open_id": "tt-user-99"}
    }
    assert printed[-1] == "token salvo"


def test_refresh_tiktok_updates_rotated_refresh_token_preserving_other_platform(tmp_path: Path):
    tokens_path = tmp_path / "tokens.json"
    save_tokens(
        {
            "youtube": {"refresh_token": "yt-keep"},
            "tiktok": {"refresh_token": "tt-old-ref", "open_id": "tt-oid-1"},
        },
        tokens_path,
    )

    def fake_urlopen(req, timeout=None):
        assert req.full_url == "https://open.tiktokapis.com/v2/oauth/token/"
        form = urllib.parse.parse_qs(req.data.decode("utf-8"))
        assert form["refresh_token"] == ["tt-old-ref"]
        assert form["grant_type"] == ["refresh_token"]
        return _FakeResp({
            "access_token": "tt-new-acc",
            "refresh_token": "tt-rotated-ref",
            "open_id": "tt-oid-1",
        })

    access = refresh_tiktok(
        client_key="tt-key",
        client_secret="tt-sec",
        tokens_path=tokens_path,
        urlopen=fake_urlopen,
    )

    assert access == "tt-new-acc"
    assert stat.S_IMODE(tokens_path.stat().st_mode) == 0o600
    assert load_tokens(tokens_path) == {
        "youtube": {"refresh_token": "yt-keep"},
        "tiktok": {"refresh_token": "tt-rotated-ref", "open_id": "tt-oid-1"},
    }


def test_refresh_google_and_error_scrubs_secrets(tmp_path: Path):
    tokens_path = tmp_path / "tokens.json"
    save_tokens({"youtube": {"refresh_token": "yt-ref-secret"}}, tokens_path)

    def ok_urlopen(req, timeout=None):
        assert req.full_url == "https://oauth2.googleapis.com/token"
        return _FakeResp({"access_token": "yt-acc-ok"})

    assert refresh_google(
        client_id="cid",
        client_secret="csec-secret",
        tokens_path=tokens_path,
        urlopen=ok_urlopen,
    ) == "yt-acc-ok"

    def fail_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request",
            hdrs={}, fp=io.BytesIO(b'{"error":"invalid_grant","detail":"yt-ref-secret csec-secret"}'),
        )

    with pytest.raises(OAuthError) as exc_info:
        refresh_google(
            client_id="cid",
            client_secret="csec-secret",
            tokens_path=tokens_path,
            urlopen=fail_urlopen,
        )

    err_msg = str(exc_info.value)
    assert "yt-ref-secret" not in err_msg
    assert "csec-secret" not in err_msg
