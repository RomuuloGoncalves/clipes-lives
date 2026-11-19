"""Testes de auditoria para upload e OAuth (M-13, M-14, B-06)."""

from __future__ import annotations

import concurrent.futures
import io
import json
import stat
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from clipes import oauth, publicar_tiktok, publicar_youtube


class _FakeResp:
    def __init__(
        self,
        status: int = 200,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.status = status
        self._raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self) -> bytes:
        return self._raw

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _http_error(
    url: str,
    code: int,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> urllib.error.HTTPError:
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    fp = io.BytesIO(json.dumps(body).encode("utf-8") if body is not None else b"")
    return urllib.error.HTTPError(url, code, f"HTTP {code}", msg, fp)


def _yt_err_body(reason: str) -> dict[str, Any]:
    return {"error": {"code": 403, "errors": [{"reason": reason}]}}


# ---------------------------------------------------------------------------
# M-13: Backoff exponencial com jitter (YouTube init e PUT)
# ---------------------------------------------------------------------------


def test_backoff_delay_bounded_and_jittered_for_youtube_and_tiktok():
    for fn in (publicar_youtube._backoff_delay, publicar_tiktok._backoff_delay):
        d0_low = fn(0, lambda: 0.0)
        d0_high = fn(0, lambda: 1.0)
        assert d0_low == 1.0
        assert d0_high > d0_low
        assert 1.0 <= d0_high <= 60.0

        prev = 0.0
        for attempt in range(10):
            delay = fn(attempt, lambda: 0.5)
            assert 1.0 <= delay <= 60.0
            assert delay >= prev
            prev = delay
        assert fn(10, lambda: 1.0) == 60.0


def test_youtube_init_retries_5xx_429_rate_limit_and_backend_error_with_backoff(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"YT-MP4")
    sleeps: list[float] = []
    init_attempts = 0
    failures = [
        _http_error(publicar_youtube.YOUTUBE_UPLOAD_URL, 503),
        _http_error(publicar_youtube.YOUTUBE_UPLOAD_URL, 429),
        _http_error(publicar_youtube.YOUTUBE_UPLOAD_URL, 403, _yt_err_body("rateLimitExceeded")),
        _http_error(publicar_youtube.YOUTUBE_UPLOAD_URL, 403, _yt_err_body("userRateLimitExceeded")),
        _http_error(publicar_youtube.YOUTUBE_UPLOAD_URL, 403, _yt_err_body("backendError")),
    ]

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        nonlocal init_attempts
        if req.get_method() == "POST":
            init_attempts += 1
            if init_attempts <= len(failures):
                raise failures[init_attempts - 1]
            return _FakeResp(200, headers={"Location": "https://upload.example/yt-init-ok"})
        return _FakeResp(200, body={"id": "yt_ok_init"})

    res = publicar_youtube.publish(
        mp4,
        "Titulo",
        "Desc",
        "tok",
        fake_urlopen,
        probe_duration_fn=lambda _: 30.0,
        sleep=sleeps.append,
        rand=lambda: 0.5,
    )

    assert res["status"] == "ok"
    assert res["video_id"] == "yt_ok_init"
    assert init_attempts == 6
    assert sleeps == [1.25, 2.5, 5.0, 10.0, 20.0]


def test_youtube_init_retries_network_error_and_stops_within_5_to_8_attempts(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"YT-MP4")
    sleeps_low: list[float] = []
    sleeps_high: list[float] = []
    attempts = 0

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        nonlocal attempts
        attempts += 1
        raise urllib.error.URLError("dns temporary failure")

    res = publicar_youtube.publish(
        mp4,
        "Titulo",
        "Desc",
        "tok",
        fake_urlopen,
        probe_duration_fn=lambda _: 30.0,
        sleep=sleeps_low.append,
        rand=lambda: 0.0,
    )

    assert res["status"] == "erro"
    assert 5 <= attempts <= 8
    assert len(sleeps_low) == attempts - 1
    assert all(1.0 <= s <= 60.0 for s in sleeps_low)

    publicar_youtube.publish(
        mp4,
        "Titulo",
        "Desc",
        "tok",
        fake_urlopen,
        probe_duration_fn=lambda _: 30.0,
        sleep=sleeps_high.append,
        rand=lambda: 1.0,
    )
    assert all(hi > lo for lo, hi in zip(sleeps_low, sleeps_high))


def test_youtube_init_and_put_do_not_retry_400_or_generic_403(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"YT-MP4")

    for code, body in ((400, {"error": {"errors": [{"reason": "invalidTitle"}]}}), (403, _yt_err_body("forbidden"))):
        sleeps: list[float] = []
        calls = 0

        def fake_init_err(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
            nonlocal calls
            calls += 1
            raise _http_error(req.full_url, code, body)

        res = publicar_youtube.publish(
            mp4,
            "Titulo",
            "Desc",
            "tok",
            fake_init_err,
            probe_duration_fn=lambda _: 30.0,
            sleep=sleeps.append,
            rand=lambda: 0.0,
        )
        assert res["status"] == "erro"
        assert "quota_esgotada" not in res
        assert calls == 1
        assert sleeps == []

    # PUT 400 nao retenta
    sleeps_put: list[float] = []
    put_calls = 0

    def fake_put_400(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        nonlocal put_calls
        if req.get_method() == "POST":
            return _FakeResp(200, headers={"Location": "https://upload.example/put400"})
        put_calls += 1
        raise _http_error(req.full_url, 400, {"error": {"errors": [{"reason": "badRequest"}]}})

    res_put = publicar_youtube.publish(
        mp4,
        "Titulo",
        "Desc",
        "tok",
        fake_put_400,
        probe_duration_fn=lambda _: 30.0,
        sleep=sleeps_put.append,
        rand=lambda: 0.0,
    )
    assert res_put["status"] == "erro"
    assert put_calls == 1
    assert sleeps_put == []


def test_youtube_put_retries_429_5xx_and_403_rate_limit_with_exponential_backoff(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"0123456789")
    upload_url = "https://upload.example/yt-put-retry"
    sleeps: list[float] = []
    put_requests: list[tuple[dict[str, str], bytes | None]] = []
    failures = [
        _http_error(upload_url, 429),
        _http_error(upload_url, 502),
        _http_error(upload_url, 403, _yt_err_body("rateLimitExceeded")),
        _http_error(upload_url, 403, _yt_err_body("backendError")),
    ]

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        if req.get_method() == "POST":
            return _FakeResp(200, headers={"Location": upload_url})
        headers = {k.lower(): v for k, v in req.header_items()}
        if headers.get("content-range", "").startswith("bytes */"):
            return _FakeResp(308, headers={"Range": "bytes=0-3"})
        put_requests.append((headers, req.data))
        if len(put_requests) <= len(failures):
            raise failures[len(put_requests) - 1]
        return _FakeResp(200, body={"id": "yt_put_ok"})

    res = publicar_youtube.publish(
        mp4,
        "Titulo",
        "Desc",
        "tok",
        fake_urlopen,
        probe_duration_fn=lambda _: 30.0,
        sleep=sleeps.append,
        rand=lambda: 0.0,
    )

    assert res["status"] == "ok"
    assert res["video_id"] == "yt_put_ok"
    assert len(put_requests) == 5
    assert put_requests[0][1] == b"0123456789"
    for headers, body in put_requests[1:]:
        assert headers.get("content-range") == "bytes 4-9/10"
        assert body == b"456789"
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_youtube_status_req_repeats_after_401_refresh_and_backs_off_on_5xx(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"0123456789")
    upload_url = "https://upload.example/yt-status-retry"
    sleeps: list[float] = []
    status_calls: list[str] = []
    put_requests: list[tuple[dict[str, str], bytes | None]] = []
    refresh_calls = 0

    def fake_refresh() -> str:
        nonlocal refresh_calls
        refresh_calls += 1
        return "tok-new"

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        if req.get_method() == "POST":
            return _FakeResp(200, headers={"Location": upload_url})
        headers = {k.lower(): v for k, v in req.header_items()}
        if headers.get("content-range", "").startswith("bytes */"):
            status_calls.append(headers["authorization"])
            if len(status_calls) == 1:
                raise _http_error(upload_url, 401)
            if len(status_calls) == 2:
                raise _http_error(upload_url, 503)
            return _FakeResp(308, headers={"Range": "bytes=0-4"})
        put_requests.append((headers, req.data))
        if len(put_requests) == 1:
            raise ConnectionResetError("drop")
        return _FakeResp(200, body={"id": "yt_status_ok"})

    res = publicar_youtube.publish(
        mp4,
        "Titulo",
        "Desc",
        "tok-old",
        fake_urlopen,
        refresh=fake_refresh,
        probe_duration_fn=lambda _: 30.0,
        sleep=sleeps.append,
        rand=lambda: 0.0,
    )

    assert res["status"] == "ok"
    assert res["video_id"] == "yt_status_ok"
    assert refresh_calls == 1
    assert status_calls == ["Bearer tok-old", "Bearer tok-new", "Bearer tok-new"]
    assert len(put_requests) == 2
    assert put_requests[1][0].get("content-range") == "bytes 5-9/10"
    assert put_requests[1][1] == b"56789"
    # Backoff no drop inicial (1.0) + backoff na falha 503 do status_req (2.0)
    assert sleeps == [1.0, 2.0]


# ---------------------------------------------------------------------------
# M-14: YouTube 403 quotaExceeded / uploadLimitExceeded sem retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["quotaExceeded", "uploadLimitExceeded"])
def test_youtube_init_quota_error_sets_flag_without_retry(tmp_path: Path, reason: str):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"YT-MP4")
    sleeps: list[float] = []
    attempts = 0

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        nonlocal attempts
        attempts += 1
        raise _http_error(req.full_url, 403, _yt_err_body(reason))

    res = publicar_youtube.publish(
        mp4,
        "Titulo",
        "Desc",
        "tok",
        fake_urlopen,
        probe_duration_fn=lambda _: 30.0,
        sleep=sleeps.append,
        rand=lambda: 0.0,
    )

    assert res == {
        "status": "erro",
        "erro": f"youtube HTTP 403: {reason}",
        "quota_esgotada": True,
    }
    assert attempts == 1
    assert sleeps == []


@pytest.mark.parametrize("reason", ["quotaExceeded", "uploadLimitExceeded"])
def test_youtube_put_quota_error_sets_flag_without_retry(tmp_path: Path, reason: str):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"YT-MP4")
    sleeps: list[float] = []
    put_attempts = 0

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        nonlocal put_attempts
        if req.get_method() == "POST":
            return _FakeResp(200, headers={"Location": "https://upload.example/yt-quota-put"})
        put_attempts += 1
        raise _http_error(req.full_url, 403, _yt_err_body(reason))

    res = publicar_youtube.publish(
        mp4,
        "Titulo",
        "Desc",
        "tok",
        fake_urlopen,
        probe_duration_fn=lambda _: 30.0,
        sleep=sleeps.append,
        rand=lambda: 0.0,
    )

    assert res == {
        "status": "erro",
        "erro": f"youtube HTTP 403: {reason}",
        "quota_esgotada": True,
    }
    assert put_attempts == 1
    assert sleeps == []


# ---------------------------------------------------------------------------
# M-13: Backoff exponencial com jitter no TikTok (init e PUT)
# ---------------------------------------------------------------------------


def test_tiktok_put_retries_5xx_429_403_rate_limit_and_network_with_backoff(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"TT-MP4")
    upload_url = "https://open-upload.tiktokapis.com/upload/retry-ok"
    sleeps: list[float] = []
    put_attempts = 0
    failures: list[BaseException] = [
        _http_error(upload_url, 503),
        _http_error(upload_url, 429),
        _http_error(upload_url, 403, {"error": {"code": "rateLimitExceeded"}}),
        _http_error(upload_url, 403, {"error": {"code": "backendError"}}),
        ConnectionResetError("connection reset mid-upload"),
    ]

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        nonlocal put_attempts
        if req.full_url.endswith("/creator_info/query/"):
            return _FakeResp(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if req.full_url.endswith("/video/init/"):
            return _FakeResp(200, {"data": {"publish_id": "tt_pub_ok", "upload_url": upload_url}, "error": {"code": "ok"}})
        if req.full_url == upload_url:
            put_attempts += 1
            if put_attempts <= len(failures):
                raise failures[put_attempts - 1]
            return _FakeResp(201)
        return _FakeResp(200, {"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}})

    res = publicar_tiktok.publish(
        mp4,
        "Legenda",
        "tok",
        fake_urlopen,
        sleeps.append,
        rand=lambda: 0.5,
    )

    assert res["status"] == "ok"
    assert res["publish_id"] == "tt_pub_ok"
    assert put_attempts == 6
    assert sleeps == [1.25, 2.5, 5.0, 10.0, 20.0]


def test_tiktok_put_exhausts_retries_without_leaking_secrets(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"TT-MP4")
    secret_url = "https://open-upload.tiktokapis.com/upload/secret-token-xyz"
    secret_tok = "act.secret-tt-tok"
    sleeps: list[float] = []
    put_attempts = 0

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        nonlocal put_attempts
        if req.full_url.endswith("/creator_info/query/"):
            return _FakeResp(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if req.full_url.endswith("/video/init/"):
            return _FakeResp(200, {"data": {"publish_id": "tt_pub_fail", "upload_url": secret_url}, "error": {"code": "ok"}})
        put_attempts += 1
        raise _http_error(secret_url, 502)

    res = publicar_tiktok.publish(
        mp4,
        "Legenda",
        secret_tok,
        fake_urlopen,
        sleeps.append,
        rand=lambda: 0.0,
    )

    assert res["status"] == "erro"
    assert res["publish_id"] == "tt_pub_fail"
    assert 5 <= put_attempts <= 8
    assert len(sleeps) == put_attempts - 1
    assert all(1.0 <= s <= 60.0 for s in sleeps)
    serialized = json.dumps(res)
    assert secret_url not in serialized
    assert secret_tok not in serialized


def test_tiktok_put_does_not_retry_400_or_non_retryable_403(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"TT-MP4")
    upload_url = "https://open-upload.tiktokapis.com/upload/no-retry"

    for code, body, expected_status in (
        (400, {"error": {"code": "invalid_params"}}, "erro"),
        (403, {"error": {"code": publicar_tiktok.UNAUDITED_CODE}}, "erro_conta"),
        (403, {"error": {"code": "quotaExceeded"}}, "erro"),
    ):
        sleeps: list[float] = []
        put_attempts = 0

        def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
            nonlocal put_attempts
            if req.full_url.endswith("/creator_info/query/"):
                return _FakeResp(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
            if req.full_url.endswith("/video/init/"):
                return _FakeResp(200, {"data": {"publish_id": "tt_nr", "upload_url": upload_url}, "error": {"code": "ok"}})
            put_attempts += 1
            raise _http_error(upload_url, code, body)

        res = publicar_tiktok.publish(mp4, "Legenda", "tok", fake_urlopen, sleeps.append, rand=lambda: 0.0)
        assert res["status"] == expected_status
        assert put_attempts == 1
        assert sleeps == []


def test_tiktok_init_retries_transient_5xx_and_429_with_backoff(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"TT-MP4")
    upload_url = "https://open-upload.tiktokapis.com/upload/init-retry"
    sleeps: list[float] = []
    init_attempts = 0

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        nonlocal init_attempts
        if req.full_url.endswith("/creator_info/query/"):
            return _FakeResp(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if req.full_url.endswith("/video/init/"):
            init_attempts += 1
            if init_attempts == 1:
                raise _http_error(req.full_url, 503)
            if init_attempts == 2:
                raise _http_error(req.full_url, 429)
            return _FakeResp(200, {"data": {"publish_id": "tt_init_ok", "upload_url": upload_url}, "error": {"code": "ok"}})
        if req.full_url == upload_url:
            return _FakeResp(200)
        return _FakeResp(200, {"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}})

    res = publicar_tiktok.publish(mp4, "Legenda", "tok", fake_urlopen, sleeps.append, rand=lambda: 0.0)
    assert res["status"] == "ok"
    assert init_attempts == 3
    assert sleeps == [1.0, 2.0]


def test_tiktok_status_poll_has_no_long_backoff_and_caps_total_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"TT-MP4")
    upload_url = "https://open-upload.tiktokapis.com/upload/status-cap"

    # Caso 1: falha transitoria em STATUS_FETCH_URL faz no maximo 1 retry curto (1s), nao 5 retries exponenciais
    sleeps1: list[float] = []
    status_calls1 = 0

    def fake_urlopen_fail_status(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        nonlocal status_calls1
        if req.full_url.endswith("/creator_info/query/"):
            return _FakeResp(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if req.full_url.endswith("/video/init/"):
            return _FakeResp(200, {"data": {"publish_id": "tt_st_1", "upload_url": upload_url}, "error": {"code": "ok"}})
        if req.full_url == upload_url:
            return _FakeResp(200)
        status_calls1 += 1
        raise _http_error(req.full_url, 503)

    res1 = publicar_tiktok.publish(mp4, "Legenda", "tok", fake_urlopen_fail_status, sleeps1.append, rand=lambda: 1.0)
    assert res1["status"] == "erro"
    assert res1["publish_id"] == "tt_st_1"
    assert status_calls1 == 2  # 1 tentativa + 1 retry curto
    assert sleeps1 == [publicar_tiktok.STATUS_RETRY_DELAY_S]

    # Caso 2: teto de tempo total no polling impede ultrapassar MAX_STATUS_WAIT_S
    monkeypatch.setattr(publicar_tiktok, "MAX_STATUS_WAIT_S", 12.0)
    sleeps2: list[float] = []
    status_calls2 = 0

    def fake_urlopen_slow_poll(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        nonlocal status_calls2
        if req.full_url.endswith("/creator_info/query/"):
            return _FakeResp(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if req.full_url.endswith("/video/init/"):
            return _FakeResp(200, {"data": {"publish_id": "tt_st_2", "upload_url": upload_url}, "error": {"code": "ok"}})
        if req.full_url == upload_url:
            return _FakeResp(200)
        status_calls2 += 1
        return _FakeResp(200, {"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}})

    res2 = publicar_tiktok.publish(mp4, "Legenda", "tok", fake_urlopen_slow_poll, sleeps2.append, rand=lambda: 0.0)
    assert res2["status"] == "erro"
    assert res2["publish_id"] == "tt_st_2"
    assert sleeps2 == [5, 5]
    assert status_calls2 == 3


# ---------------------------------------------------------------------------
# B-06: Rotacao de refresh_token e lock de arquivo no tokens.json
# ---------------------------------------------------------------------------


def test_refresh_tiktok_tokens_returns_and_persists_rotated_refresh_token(tmp_path: Path):
    tokens_path = tmp_path / "tokens.json"
    oauth.save_tokens(
        {
            "youtube": {"refresh_token": "yt-keep"},
            "tiktok": {"refresh_token": "tt-old-ref", "open_id": "oid-1"},
        },
        tokens_path,
    )

    def fake_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        return _FakeResp(
            200,
            {
                "access_token": "tt-acc-new",
                "refresh_token": "tt-ref-rotated",
                "open_id": "oid-2",
            },
        )

    result = oauth.refresh_tiktok_tokens(
        client_key="ckey",
        client_secret="csec",
        tokens_path=tokens_path,
        urlopen=fake_urlopen,
    )

    assert result == {
        "access_token": "tt-acc-new",
        "refresh_token": "tt-ref-rotated",
        "open_id": "oid-2",
    }
    assert stat.S_IMODE(tokens_path.stat().st_mode) == 0o600
    assert oauth.load_tokens(tokens_path) == {
        "youtube": {"refresh_token": "yt-keep"},
        "tiktok": {"refresh_token": "tt-ref-rotated", "open_id": "oid-2"},
    }


def test_refresh_google_tokens_returns_rotated_refresh_token_when_present(tmp_path: Path):
    tokens_path = tmp_path / "tokens.json"
    oauth.save_tokens({"youtube": {"refresh_token": "yt-old"}, "tiktok": {"refresh_token": "tt-keep"}}, tokens_path)

    def rotated_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        return _FakeResp(200, {"access_token": "yt-acc-1", "refresh_token": "yt-new"})

    res = oauth.refresh_google_tokens(
        client_id="cid",
        client_secret="csec",
        tokens_path=tokens_path,
        urlopen=rotated_urlopen,
    )
    assert res == {"access_token": "yt-acc-1", "refresh_token": "yt-new"}
    assert oauth.load_tokens(tokens_path) == {
        "youtube": {"refresh_token": "yt-new"},
        "tiktok": {"refresh_token": "tt-keep"},
    }

    def same_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        return _FakeResp(200, {"access_token": "yt-acc-2"})

    res2 = oauth.refresh_google_tokens(
        client_id="cid",
        client_secret="csec",
        tokens_path=tokens_path,
        urlopen=same_urlopen,
    )
    assert res2 == {"access_token": "yt-acc-2"}


def test_oauth_refresh_uses_locked_path_and_serializes_concurrent_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    tokens_path = tmp_path / "tokens.json"
    oauth.save_tokens(
        {
            "youtube": {"refresh_token": "yt-r0"},
            "tiktok": {"refresh_token": "tt-r0", "open_id": "oid-0"},
        },
        tokens_path,
    )

    locked_targets: list[Path] = []
    orig_locked_path = oauth._locked_path
    orig_load_tokens = oauth.load_tokens
    load_barrier = threading.Event()

    @contextmanager
    def spy_locked_path(path: Path):
        locked_targets.append(path)
        with orig_locked_path(path):
            yield

    def slow_load_tokens(path: Path | None = None) -> dict[str, Any]:
        data = orig_load_tokens(path)
        # Pequeno atraso durante o read-modify-write para expor corridas sem lock
        if load_barrier.is_set():
            time.sleep(0.03)
        return data

    monkeypatch.setattr(oauth, "_locked_path", spy_locked_path)
    monkeypatch.setattr(oauth, "load_tokens", slow_load_tokens)
    load_barrier.set()

    def yt_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        return _FakeResp(200, {"access_token": "yt-acc", "refresh_token": "yt-r1"})

    def tt_urlopen(req: urllib.request.Request, timeout: float = 30) -> _FakeResp:
        return _FakeResp(200, {"access_token": "tt-acc", "refresh_token": "tt-r1", "open_id": "oid-1"})

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_yt = pool.submit(
            oauth.refresh_google_tokens,
            "yt-r0",
            client_id="cid",
            client_secret="csec",
            tokens_path=tokens_path,
            urlopen=yt_urlopen,
        )
        f_tt = pool.submit(
            oauth.refresh_tiktok_tokens,
            "tt-r0",
            client_key="ckey",
            client_secret="csec",
            tokens_path=tokens_path,
            urlopen=tt_urlopen,
        )
        yt_res = f_yt.result()
        tt_res = f_tt.result()

    assert yt_res == {"access_token": "yt-acc", "refresh_token": "yt-r1"}
    assert tt_res == {"access_token": "tt-acc", "refresh_token": "tt-r1", "open_id": "oid-1"}
    assert len(locked_targets) >= 2
    assert all(p == tokens_path for p in locked_targets)

    final_tokens = orig_load_tokens(tokens_path)
    assert final_tokens == {
        "youtube": {"refresh_token": "yt-r1"},
        "tiktok": {"refresh_token": "tt-r1", "open_id": "oid-1"},
    }
    assert stat.S_IMODE(tokens_path.stat().st_mode) == 0o600
