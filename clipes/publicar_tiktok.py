"""TikTok Content Posting API v2 private publisher (REST)."""

from __future__ import annotations

import http.client
import json
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
UNAUDITED_CODE = "unaudited_client_can_only_post_to_private_accounts"
MAX_STATUS_POLLS = 60
POLL_INTERVAL_S = 5


def _resolve_token(token: str | Callable[[], str]) -> str:
    return str(token() if callable(token) else token)


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
        return str(code) if code else ""
    return ""


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

    def post_json(url: str, body: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
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
                detail = f"tiktok HTTP {exc.code}" + (f": {err_code}" if err_code else "")
                return None, {"status": "erro", "erro": detail}
            except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException, ValueError) as exc:
                return None, {"status": "erro", "erro": f"tiktok falha de rede: {type(exc).__name__}"}

    info_resp, err = post_json(CREATOR_INFO_URL, {})
    if err is not None:
        return err
    privacy_opts = (info_resp or {}).get("data", {}).get("privacy_level_options", [])
    if PRIVACY_LEVEL not in privacy_opts:
        return _account_error()

    init_payload = {
        "post_info": {
            "title": (caption or "")[:2200],
            "privacy_level": PRIVACY_LEVEL,
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
    if err is not None:
        return err

    init_data = (init_resp or {}).get("data", {})
    publish_id = str(init_data.get("publish_id") or "")
    upload_url = str(init_data.get("upload_url") or "")
    if not publish_id or not upload_url:
        return {"status": "erro", "erro": "tiktok init sem publish_id ou upload_url"}

    content_range = f"bytes 0-{max(file_size - 1, 0)}/{file_size}"
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
            status = getattr(resp, "status", None) or resp.getcode()
            if int(status) not in (200, 201, 204):
                return {"status": "erro", "publish_id": publish_id, "erro": f"tiktok upload HTTP {status}"}
    except urllib.error.HTTPError as exc:
        err_code = _extract_error_code(exc.read() if exc.fp else b"")
        if err_code == UNAUDITED_CODE:
            return _account_error(publish_id)
        return {"status": "erro", "publish_id": publish_id, "erro": f"tiktok upload HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as exc:
        return {"status": "erro", "publish_id": publish_id, "erro": f"tiktok upload falhou: {type(exc).__name__}"}

    for attempt in range(MAX_STATUS_POLLS):
        if attempt > 0:
            sleep(POLL_INTERVAL_S)
        st_resp, err = post_json(STATUS_FETCH_URL, {"publish_id": publish_id})
        if err is not None:
            err.setdefault("publish_id", publish_id)
            return err
        st_data = (st_resp or {}).get("data", {})
        st_value = str(st_data.get("status") or "")
        if st_value == "PUBLISH_COMPLETE":
            return {
                "status": "ok",
                "publish_id": publish_id,
                "privacidade": PRIVACY_LEVEL,
            }
        if st_value == "FAILED":
            fail_reason = str(st_data.get("fail_reason") or "FAILED")
            if fail_reason == UNAUDITED_CODE:
                return _account_error(publish_id)
            return {"status": "erro", "publish_id": publish_id, "erro": f"tiktok {fail_reason}"}

    return {"status": "erro", "publish_id": publish_id, "erro": f"tiktok timeout apos {MAX_STATUS_POLLS} consultas"}
