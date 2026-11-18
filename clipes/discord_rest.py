"""Minimal Discord REST v10 client for messages, file attachments, and threads."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

DISCORD_API_BASE = "https://discord.com/api/v10"
MAX_429_RETRIES = 3


class DiscordRestError(RuntimeError):
    """Raised when a Discord REST request fails."""


def _encode_multipart(payload: dict[str, Any], file_path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    safe_name = file_path.name.replace('"', "").replace("\r", "").replace("\n", "") or "clip.mp4"
    payload_bytes = json.dumps(payload).encode("utf-8")
    file_bytes = file_path.read_bytes()

    parts = [
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="payload_json"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode("utf-8")
        + payload_bytes
        + b"\r\n",
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files[0]"; filename="{safe_name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        + file_bytes
        + b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _parse_retry_after(body: bytes, headers: Any) -> float:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(data, dict) and "retry_after" in data:
            return max(0.0, float(data["retry_after"]))
    except Exception:
        pass
    if headers:
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if raw is not None:
            try:
                return max(0.0, float(raw))
            except ValueError:
                pass
    return 1.0


def _format_api_error(status: int, body: bytes, token: str) -> str:
    detail = ""
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            code = data.get("code")
            msg = data.get("message") or ""
            detail = f" code={code} {msg}".strip()
    except Exception:
        pass
    if not detail and body:
        detail = body.decode("utf-8", errors="replace")[:200]
    text = f"Discord HTTP {status}" + (f": {detail}" if detail else "")
    return text.replace(token, "***") if token else text


class DiscordRest:
    """Synchronous Discord REST v10 client with 429 retry-after handling."""

    def __init__(
        self,
        token: str,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = DISCORD_API_BASE,
        timeout: float = 60.0,
    ) -> None:
        clean = (token or "").strip()
        if not clean:
            raise ValueError("DISCORD_BOT_TOKEN is required")
        self._token = clean
        self._urlopen = urlopen
        self._sleep = sleep
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout)

    def __repr__(self) -> str:
        return f"DiscordRest(token='***', base_url={self._base_url!r})"

    def _request(self, path: str, body: bytes, content_type: str) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bot {self._token}",
            "Content-Type": content_type,
            "User-Agent": "clipes-lives (https://discord.com, 1.0)",
        }

        for attempt in range(MAX_429_RETRIES + 1):
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with self._urlopen(req, timeout=self._timeout) as resp:
                    status = getattr(resp, "status", None) or resp.getcode() or 200
                    resp_body = resp.read()
                    resp_headers = getattr(resp, "headers", None)
            except urllib.error.HTTPError as err:
                status = err.code
                resp_body = err.read() if err.fp is not None else b""
                resp_headers = err.headers
            except urllib.error.URLError as err:
                reason = str(err.reason).replace(self._token, "***")
                raise DiscordRestError(f"Discord network error: {reason}") from None

            if status == 429:
                if attempt < MAX_429_RETRIES:
                    self._sleep(_parse_retry_after(resp_body, resp_headers))
                    continue
                raise DiscordRestError(
                    f"Discord HTTP 429: rate limit exceeded after {MAX_429_RETRIES} retries"
                )

            if status >= 400:
                raise DiscordRestError(_format_api_error(status, resp_body, self._token))

            if not resp_body:
                return {}
            return json.loads(resp_body.decode("utf-8"))

        raise DiscordRestError("Discord request failed")  # pragma: no cover

    def post(self, channel_id: str | int, content: str, file: Path | None = None) -> dict[str, Any]:
        """Post a message (and optional file attachment) to a channel or thread."""
        payload = {"content": content}
        if file is None:
            raw = json.dumps(payload).encode("utf-8")
            ctype = "application/json"
        else:
            raw, ctype = _encode_multipart(payload, Path(file))
        return self._request(f"/channels/{channel_id}/messages", raw, ctype)

    def thread_from(
        self,
        channel_id: str | int,
        message_id: str | int,
        name: str,
        auto_archive_duration: int = 1440,
    ) -> str:
        """Create a public thread from an existing message and return the thread ID."""
        payload = {
            "name": (name or "clipes")[:100],
            "auto_archive_duration": int(auto_archive_duration),
        }
        data = self._request(
            f"/channels/{channel_id}/messages/{message_id}/threads",
            json.dumps(payload).encode("utf-8"),
            "application/json",
        )
        return str(data["id"])
