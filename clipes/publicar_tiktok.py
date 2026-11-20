"""TikTok Content Posting API v2 private publisher (REST)."""

from __future__ import annotations

import http.client
import json
import os
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2/post/publish"
CREATOR_INFO_URL = f"{TIKTOK_API_BASE}/creator_info/query/"
VIDEO_INIT_URL = f"{TIKTOK_API_BASE}/video/init/"
STATUS_FETCH_URL = f"{TIKTOK_API_BASE}/status/fetch/"

PRIVACY_LEVEL = "SELF_ONLY"
_VALID_PRIVACY = frozenset({
    "SELF_ONLY",
    "MUTUAL_FOLLOW_FRIENDS",
    "FOLLOWER_OF_CREATOR",
    "PUBLIC_TO_EVERYONE",
})
UNAUDITED_CODE = "unaudited_client_can_only_post_to_private_accounts"
MAX_STATUS_POLLS = 60
POLL_INTERVAL_S = 5
MAX_STATUS_WAIT_S = 600.0
STATUS_RETRY_DELAY_S = 1.0
MAX_RETRIES = 5  # 1 tentativa inicial + 5 retentativas = 6 tentativas (faixa 5..8)
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 60.0

_RETRYABLE_403_CODES = frozenset({
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "backendError",
    "rate_limit_exceeded",
})
_NETWORK_ERRORS = (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException)


def _resolve_token(token: str | Callable[[], str]) -> str:
    return str(token() if callable(token) else token)


def _resolve_privacy(privacy_opts: list[str]) -> str | None:
    raw = os.environ.get("CLIPES_TIKTOK_PRIVACY", "").strip().upper()
    requested = raw if raw in _VALID_PRIVACY else PRIVACY_LEVEL
    if requested in privacy_opts:
        return requested
    if PRIVACY_LEVEL in privacy_opts:
        return PRIVACY_LEVEL
    return None


def _extract_error_code(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return ""
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        code = err.get("code")
        if code:
            return str(code)
        errors = err.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            reason = errors[0].get("reason")
            if reason:
                return str(reason)
    return ""


def _is_retryable_http(code: int, err_code: str = "") -> bool:
    return (500 <= code < 600) or code == 429 or (code == 403 and err_code in _RETRYABLE_403_CODES)


def _backoff_delay(attempt: int, rand: Callable[[], float] = random.random) -> float:
    """Backoff exponencial com jitter limitado a [1.0, 60.0] segundos."""
    base = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** attempt))
    jitter = max(0.0, min(1.0, float(rand())))
    return min(BACKOFF_MAX_S, max(BACKOFF_BASE_S, base * (1.0 + 0.5 * jitter)))


def _account_error(publish_id: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"status": "erro_conta", "erro": "deixe a conta do TikTok como privada"}
    if publish_id:
        out["publish_id"] = publish_id
    return out


def _auth_error() -> dict[str, Any]:
    return {"status": "erro_auth", "erro": "refaca clipes auth tiktok"}


def publish(
    mp4: Path | str,
    caption: str,
    token: str | Callable[[], str],
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    *,
    refresh: Callable[[], str] | None = None,
    rand: Callable[[], float] = random.random,
) -> dict[str, Any]:
    """Upload and direct-post an MP4 clip as private (SELF_ONLY) on TikTok."""
    mp4_path = Path(mp4)
    mp4_bytes = mp4_path.read_bytes()
    file_size = len(mp4_bytes)
    access_token = _resolve_token(token)
    refreshed = False

    def try_refresh() -> bool:
        nonlocal access_token, refreshed
        if refresh is None or refreshed:
            return False
        refreshed = True
        try:
            access_token = str(refresh())
        except Exception:
            return False
        return bool(access_token)

    def post_json(
        url: str,
        body: dict[str, Any],
        *,
        max_retries: int = MAX_RETRIES,
        use_backoff: bool = True,
        retry_delay: float = STATUS_RETRY_DELAY_S,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        retries = 0
        while True:
            req = urllib.request.Request(
                url,
                data=raw_body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                method="POST",
            )
            try:
                with urlopen(req, timeout=30) as resp:
                    parsed = json.loads(resp.read().decode("utf-8"))
                err = parsed.get("error") if isinstance(parsed, dict) else None
                err_code = str(err.get("code") or "") if isinstance(err, dict) else ""
                if err_code == UNAUDITED_CODE:
                    return None, _account_error()
                if err_code and err_code != "ok":
                    return None, {"status": "erro", "erro": f"tiktok {err_code}"}
                return parsed, None
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and try_refresh():
                    continue
                if exc.code == 401:
                    return None, _auth_error()
                err_code = _extract_error_code(exc.read() if exc.fp else b"")
                if err_code == UNAUDITED_CODE:
                    return None, _account_error()
                if _is_retryable_http(exc.code, err_code) and retries < max_retries:
                    delay = _backoff_delay(retries, rand) if use_backoff else retry_delay
                    sleep(delay)
                    retries += 1
                    continue
                detail = f"tiktok HTTP {exc.code}" + (f": {err_code}" if err_code else "")
                return None, {"status": "erro", "erro": detail}
            except _NETWORK_ERRORS as exc:
                if retries < max_retries:
                    delay = _backoff_delay(retries, rand) if use_backoff else retry_delay
                    sleep(delay)
                    retries += 1
                    continue
                return None, {"status": "erro", "erro": f"tiktok falha de rede: {type(exc).__name__}"}
            except ValueError as exc:
                return None, {"status": "erro", "erro": f"tiktok falha de rede: {type(exc).__name__}"}

    info_resp, err = post_json(CREATOR_INFO_URL, {})
    if err is not None:
        return err
    privacy_opts = (info_resp or {}).get("data", {}).get("privacy_level_options", [])
    privacy_level = _resolve_privacy(privacy_opts)
    if privacy_level is None:
        return _account_error()

    init_payload = {
        "post_info": {
            "title": (caption or "")[:2200],
            "privacy_level": privacy_level,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        },
    }
    init_resp, err = post_json(VIDEO_INIT_URL, init_payload)
    if err is not None and err.get("status") == "erro_conta" and privacy_level != PRIVACY_LEVEL and PRIVACY_LEVEL in privacy_opts:
        privacy_level = PRIVACY_LEVEL
        init_payload["post_info"]["privacy_level"] = privacy_level
        init_resp, err = post_json(VIDEO_INIT_URL, init_payload)
    if err is not None:
        return err

    init_data = (init_resp or {}).get("data", {})
    publish_id = str(init_data.get("publish_id") or "")
    upload_url = str(init_data.get("upload_url") or "")
    if not publish_id or not upload_url:
        return {"status": "erro", "erro": "tiktok init sem publish_id ou upload_url"}

    content_range = f"bytes 0-{max(file_size - 1, 0)}/{file_size}"
    put_retries = 0
    while True:
        put_req = urllib.request.Request(
            upload_url,
            data=mp4_bytes,
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(file_size),
                "Content-Range": content_range,
            },
            method="PUT",
        )
        try:
            with urlopen(put_req, timeout=120) as resp:
                status = int(getattr(resp, "status", None) or resp.getcode())
                if status in (200, 201, 204):
                    break
                if _is_retryable_http(status) and put_retries < MAX_RETRIES:
                    sleep(_backoff_delay(put_retries, rand))
                    put_retries += 1
                    continue
                return {"status": "erro", "publish_id": publish_id, "erro": f"tiktok upload HTTP {status}"}
        except urllib.error.HTTPError as exc:
            err_code = _extract_error_code(exc.read() if exc.fp else b"")
            if err_code == UNAUDITED_CODE:
                return _account_error(publish_id)
            if _is_retryable_http(exc.code, err_code) and put_retries < MAX_RETRIES:
                sleep(_backoff_delay(put_retries, rand))
                put_retries += 1
                continue
            return {"status": "erro", "publish_id": publish_id, "erro": f"tiktok upload HTTP {exc.code}"}
        except _NETWORK_ERRORS as exc:
            if put_retries < MAX_RETRIES:
                sleep(_backoff_delay(put_retries, rand))
                put_retries += 1
                continue
            return {"status": "erro", "publish_id": publish_id, "erro": f"tiktok upload falhou: {type(exc).__name__}"}

    poll_start = time.monotonic()
    simulated_sleep_s = 0.0
    for attempt in range(MAX_STATUS_POLLS):
        if attempt > 0:
            if max(time.monotonic() - poll_start, simulated_sleep_s) + POLL_INTERVAL_S > MAX_STATUS_WAIT_S:
                break
            sleep(POLL_INTERVAL_S)
            simulated_sleep_s += POLL_INTERVAL_S
        # Polling de status: sem backoff exponencial longo (no maximo 1 retry curto)
        st_resp, err = post_json(
            STATUS_FETCH_URL,
            {"publish_id": publish_id},
            max_retries=1,
            use_backoff=False,
            retry_delay=STATUS_RETRY_DELAY_S,
        )
        if err is not None:
            err.setdefault("publish_id", publish_id)
            return err
        st_data = (st_resp or {}).get("data", {})
        st_value = str(st_data.get("status") or "")
        if st_value == "PUBLISH_COMPLETE":
            return {
                "status": "ok",
                "publish_id": publish_id,
                "privacidade": privacy_level,
            }
        if st_value == "FAILED":
            fail_reason = str(st_data.get("fail_reason") or "FAILED")
            if fail_reason == UNAUDITED_CODE:
                return _account_error(publish_id)
            return {"status": "erro", "publish_id": publish_id, "erro": f"tiktok {fail_reason}"}

    return {"status": "erro", "publish_id": publish_id, "erro": f"tiktok timeout apos {MAX_STATUS_POLLS} consultas"}
