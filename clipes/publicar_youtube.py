"""YouTube Shorts private resumable uploader via Data API v3 (REST)."""

from __future__ import annotations

import http.client
import json
import random
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .ingest import probe_duration

YOUTUBE_UPLOAD_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&part=snippet,status"
)
MAX_SHORT_DURATION_S = 180.0
MAX_RESUMES = 5  # 1 tentativa inicial + 5 retentativas = 6 tentativas (faixa 5..8)
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 60.0
PRIVACY_STATUS = "private"
CATEGORY_ID = "20"

_RANGE_RE = re.compile(r"bytes=0-(\d+)")
_QUOTA_REASONS = frozenset({"quotaExceeded", "uploadLimitExceeded"})
_RETRYABLE_403_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "backendError"})
_NETWORK_ERRORS = (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException)


def _resolve_token(token: str | Callable[[], str]) -> str:
    return str(token() if callable(token) else token)


def _sanitize_title(title: str) -> str:
    clean = " ".join(title.replace("<", "").replace(">", "").split()) or "Clipe"
    if "#shorts" in clean.lower():
        return clean[:100]
    return f"{clean[:90].rstrip()} #Shorts"


def _format_description(description: str) -> str:
    base = (description or "").replace("<", "").replace(">", "").strip()
    if "#shorts" in base.lower():
        return base[:5000]
    if not base:
        return "#Shorts"
    suffix = "\n\n#Shorts"
    return f"{base[: 5000 - len(suffix)].rstrip()}{suffix}"


def _parse_range_offset(headers: Any) -> int:
    if not headers:
        return 0
    raw = headers.get("Range") or headers.get("range") or ""
    match = _RANGE_RE.search(str(raw))
    return int(match.group(1)) + 1 if match else 0


def _extract_api_reason(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return ""
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        return ""
    errors = err.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        reason = errors[0].get("reason")
        if reason:
            return str(reason)
    status = err.get("status")
    return str(status) if status else ""


def _build_put_request(
    upload_url: str,
    access_token: str,
    data: bytes,
    total_size: int,
    offset: int = 0,
) -> urllib.request.Request:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "video/mp4",
        "Content-Length": str(len(data)),
    }
    if offset > 0 and total_size > 0:
        headers["Content-Range"] = f"bytes {offset}-{total_size - 1}/{total_size}"
    return urllib.request.Request(upload_url, data=data, headers=headers, method="PUT")


def _send(req: urllib.request.Request, urlopen: Callable[..., Any], timeout: float = 120) -> tuple[int, Any, bytes]:
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return int(status), getattr(resp, "headers", None), resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 308:
            return 308, exc.headers, b""
        raise


def _ok_result(raw: bytes) -> dict[str, Any]:
    data = json.loads(raw.decode("utf-8")) if raw else {}
    video_id = str(data.get("id") or "")
    return {
        "status": "ok",
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}" if video_id else "",
        "privacidade": PRIVACY_STATUS,
    }


def _auth_error() -> dict[str, Any]:
    return {"status": "erro_auth", "erro": "refaca clipes auth youtube"}


def _quota_error(reason: str) -> dict[str, Any]:
    return {"status": "erro", "erro": f"youtube HTTP 403: {reason}", "quota_esgotada": True}


def _is_quota_error(code: int, reason: str) -> bool:
    return code == 403 and reason in _QUOTA_REASONS


def _is_retryable_http(code: int, reason: str = "") -> bool:
    return (500 <= code < 600) or code == 429 or (code == 403 and reason in _RETRYABLE_403_REASONS)


def _backoff_delay(attempt: int, rand: Callable[[], float] = random.random) -> float:
    """Backoff exponencial com jitter limitado a [1.0, 60.0] segundos."""
    base = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** attempt))
    jitter = max(0.0, min(1.0, float(rand())))
    return min(BACKOFF_MAX_S, max(BACKOFF_BASE_S, base * (1.0 + 0.5 * jitter)))


def publish(
    mp4: Path | str,
    title: str,
    description: str,
    token: str | Callable[[], str],
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    *,
    refresh: Callable[[], str] | None = None,
    probe_duration_fn: Callable[[Path], float] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
) -> dict[str, Any]:
    """Upload an MP4 clip as a private YouTube Short using resumable upload."""
    mp4_path = Path(mp4)
    duration_fn = probe_duration_fn or probe_duration
    duration = duration_fn(mp4_path)
    if duration > MAX_SHORT_DURATION_S:
        return {"status": "erro", "erro": "clipe acima de 3 min"}

    mp4_bytes = mp4_path.read_bytes()
    total_size = len(mp4_bytes)
    access_token = _resolve_token(token)
    refreshed = False

    metadata = {
        "snippet": {
            "title": _sanitize_title(title),
            "description": _format_description(description),
            "categoryId": CATEGORY_ID,
        },
        "status": {
            "privacyStatus": PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }
    init_body = json.dumps(metadata, ensure_ascii=False).encode("utf-8")

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

    upload_url = ""
    init_retries = 0
    while True:
        init_req = urllib.request.Request(
            YOUTUBE_UPLOAD_URL,
            data=init_body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(total_size),
                "X-Upload-Content-Type": "video/mp4",
            },
            method="POST",
        )
        try:
            status, headers, _ = _send(init_req, urlopen, timeout=30)
            if status in (200, 201):
                upload_url = str((headers.get("Location") or headers.get("location") or "") if headers else "")
                break
            if _is_retryable_http(status) and init_retries < MAX_RESUMES:
                sleep(_backoff_delay(init_retries, rand))
                init_retries += 1
                continue
            return {"status": "erro", "erro": f"youtube HTTP {status}"}
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and try_refresh():
                continue
            if exc.code == 401:
                return _auth_error()
            reason = _extract_api_reason(exc.read() if exc.fp else b"")
            if _is_quota_error(exc.code, reason):
                return _quota_error(reason)
            detail = f"youtube HTTP {exc.code}" + (f": {reason}" if reason else "")
            if _is_retryable_http(exc.code, reason) and init_retries < MAX_RESUMES:
                sleep(_backoff_delay(init_retries, rand))
                init_retries += 1
                continue
            return {"status": "erro", "erro": detail}
        except _NETWORK_ERRORS as exc:
            if init_retries < MAX_RESUMES:
                sleep(_backoff_delay(init_retries, rand))
                init_retries += 1
                continue
            return {"status": "erro", "erro": f"youtube falha ao iniciar sessao: {type(exc).__name__}"}

    if not upload_url:
        return {"status": "erro", "erro": "youtube sem header Location"}

    offset = 0
    resumes_used = 0
    while True:
        put_req = _build_put_request(upload_url, access_token, mp4_bytes[offset:], total_size, offset=offset)
        try:
            status, headers, body = _send(put_req, urlopen, timeout=120)
            if status in (200, 201):
                return _ok_result(body)
            if status == 308:
                if resumes_used >= MAX_RESUMES:
                    return {"status": "erro", "erro": f"youtube upload incompleto apos {MAX_RESUMES} retomadas"}
                resumes_used += 1
                offset = _parse_range_offset(headers)
                continue
            if not _is_retryable_http(status):
                return {"status": "erro", "erro": f"youtube HTTP {status}"}
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and try_refresh():
                continue
            if exc.code == 401:
                return _auth_error()
            reason = _extract_api_reason(exc.read() if exc.fp else b"")
            if _is_quota_error(exc.code, reason):
                return _quota_error(reason)
            if not _is_retryable_http(exc.code, reason):
                detail = f"youtube HTTP {exc.code}" + (f": {reason}" if reason else "")
                return {"status": "erro", "erro": detail}
        except _NETWORK_ERRORS:
            pass

        if resumes_used >= MAX_RESUMES:
            return {"status": "erro", "erro": f"youtube upload falhou apos {MAX_RESUMES} retomadas"}
        sleep(_backoff_delay(resumes_used, rand))
        resumes_used += 1

        while True:
            status_req = urllib.request.Request(
                upload_url,
                data=b"",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Length": "0",
                    "Content-Range": f"bytes */{total_size}",
                },
                method="PUT",
            )
            try:
                st_code, st_headers, st_body = _send(status_req, urlopen, timeout=30)
                if st_code in (200, 201):
                    return _ok_result(st_body)
                if st_code == 308:
                    offset = _parse_range_offset(st_headers)
                    break
                if not _is_retryable_http(st_code):
                    return {"status": "erro", "erro": f"youtube HTTP {st_code}"}
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and try_refresh():
                    continue
                if exc.code == 401:
                    return _auth_error()
                reason = _extract_api_reason(exc.read() if exc.fp else b"")
                if _is_quota_error(exc.code, reason):
                    return _quota_error(reason)
                if not _is_retryable_http(exc.code, reason):
                    detail = f"youtube HTTP {exc.code}" + (f": {reason}" if reason else "")
                    return {"status": "erro", "erro": detail}
            except _NETWORK_ERRORS:
                # Consulta de status caiu; aguarda backoff antes de repetir
                pass

            if resumes_used >= MAX_RESUMES:
                return {"status": "erro", "erro": f"youtube upload falhou apos {MAX_RESUMES} retomadas"}
            sleep(_backoff_delay(resumes_used, rand))
            resumes_used += 1
