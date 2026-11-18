"""YouTube Shorts private resumable uploader via Data API v3 (REST)."""

from __future__ import annotations

import http.client
import json
import re
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
MAX_RESUMES = 3
PRIVACY_STATUS = "private"
CATEGORY_ID = "20"

_RANGE_RE = re.compile(r"bytes=0-(\d+)")
_TRANSIENT_HTTP = frozenset({500, 502, 503, 504})


def _resolve_token(token: str | Callable[[], str]) -> str:
    return str(token() if callable(token) else token)


def _sanitize_title(title: str) -> str:
    clean = " ".join(title.replace("<", "").replace(">", "").split()) or "Clipe"
    if "#shorts" in clean.lower():
        return clean[:100]
    return f"{clean[:90].rstrip()} #Shorts"


def _format_description(description: str) -> str:
    base = (description or "").strip()
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


def publish(
    mp4: Path | str,
    title: str,
    description: str,
    token: str | Callable[[], str],
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    *,
    refresh: Callable[[], str] | None = None,
    probe_duration_fn: Callable[[Path], float] | None = None,
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
            _, headers, _ = _send(init_req, urlopen, timeout=30)
            upload_url = str((headers.get("Location") or headers.get("location") or "") if headers else "")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and try_refresh():
                continue
            if exc.code == 401:
                return _auth_error()
            reason = _extract_api_reason(exc.read() if exc.fp else b"")
            detail = f"youtube HTTP {exc.code}" + (f": {reason}" if reason else "")
            return {"status": "erro", "erro": detail}
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as exc:
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
            return {"status": "erro", "erro": f"youtube HTTP {status}"}
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and try_refresh():
                continue
            if exc.code == 401:
                return _auth_error()
            if exc.code not in _TRANSIENT_HTTP:
                reason = _extract_api_reason(exc.read() if exc.fp else b"")
                detail = f"youtube HTTP {exc.code}" + (f": {reason}" if reason else "")
                return {"status": "erro", "erro": detail}
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
            pass

        if resumes_used >= MAX_RESUMES:
            return {"status": "erro", "erro": f"youtube upload falhou apos {MAX_RESUMES} retomadas"}
        resumes_used += 1

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
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and try_refresh():
                continue
            if exc.code == 401:
                return _auth_error()
            if exc.code not in _TRANSIENT_HTTP:
                return {"status": "erro", "erro": f"youtube HTTP {exc.code}"}
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
            # Status check also dropped; next loop iteration attempts resume from last known offset
            pass
