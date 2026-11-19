"""Minimal Discord REST v10 client for messages, file attachments, and threads."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

DISCORD_API_BASE = "https://discord.com/api/v10"
MAX_429_RETRIES = 3
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_URL_QUERY_RE = re.compile(r"(https?://[^\s'\"?]+)\?[^\s'\"]+")


class DiscordRestError(RuntimeError):
    """Raised when a Discord REST request fails."""

    def __init__(self, message: str, *, status: int | None = None, code: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


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


def _mask_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return "***"
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return f"{base}?***" if parsed.query else base


def _scrub_secrets(text: str, *, token: str = "", url: str = "") -> str:
    out = text or ""
    if url:
        out = out.replace(url, _mask_url(url))
        query = urllib.parse.urlsplit(url).query
        if query:
            out = out.replace(query, "***")
    out = _URL_QUERY_RE.sub(r"\1?***", out)
    if token:
        out = out.replace(token, "***")
    return out


def _extract_error_code(body: bytes) -> int | None:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(data, dict) and data.get("code") is not None:
            return int(data["code"])
    except Exception:
        pass
    return None


def _format_api_error(status: int, body: bytes, token: str, *, url: str = "") -> str:
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
    return _scrub_secrets(text, token=token, url=url)


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

    def _request(
        self,
        path: str,
        body: bytes | None = None,
        content_type: str | None = None,
        *,
        method: str = "POST",
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bot {self._token}",
            "User-Agent": "clipes-lives (https://discord.com, 1.0)",
        }
        if content_type:
            headers["Content-Type"] = content_type

        for attempt in range(MAX_429_RETRIES + 1):
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
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
                reason = _scrub_secrets(str(err.reason), token=self._token)
                cause = err.reason if isinstance(err.reason, (BrokenPipeError, ConnectionResetError)) else None
                raise DiscordRestError(f"Discord network error: {reason}") from cause
            except OSError as err:
                reason = _scrub_secrets(str(err), token=self._token)
                raise DiscordRestError(f"Discord network error: {reason}") from err

            if status == 429:
                if attempt < MAX_429_RETRIES:
                    self._sleep(_parse_retry_after(resp_body, resp_headers))
                    continue
                raise DiscordRestError(
                    f"Discord HTTP 429: rate limit exceeded after {MAX_429_RETRIES} retries",
                    status=429,
                )

            if status >= 400:
                raise DiscordRestError(
                    _format_api_error(status, resp_body, self._token),
                    status=status,
                    code=_extract_error_code(resp_body),
                )

            if not resp_body:
                return {}
            return json.loads(resp_body.decode("utf-8"))

        raise DiscordRestError("Discord request failed")  # pragma: no cover

    def get_message(self, channel_id: str | int, message_id: str | int) -> dict[str, Any]:
        """Fetch a message (including freshly signed attachment URLs) from a channel or thread."""
        return self._request(f"/channels/{channel_id}/messages/{message_id}", method="GET")

    def download(self, url: str, dst: Path | None, max_bytes: int) -> tuple[int, str]:
        """Stream-download `url` in 1 MiB chunks, enforcing `max_bytes` and never leaking signed URLs."""
        safe_url = _mask_url(url)
        headers = {"User-Agent": "clipes-lives (https://discord.com, 1.0)"}

        for attempt in range(MAX_429_RETRIES + 1):
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with self._urlopen(req, timeout=self._timeout) as resp:
                    status = getattr(resp, "status", None) or resp.getcode() or 200
                    resp_headers = getattr(resp, "headers", None)
                    if status == 429:
                        resp_body = resp.read()
                        if attempt < MAX_429_RETRIES:
                            self._sleep(_parse_retry_after(resp_body, resp_headers))
                            continue
                        raise DiscordRestError(
                            f"Discord HTTP 429: rate limit exceeded after {MAX_429_RETRIES} retries",
                            status=429,
                        )
                    if status >= 400:
                        resp_body = resp.read()
                        raise DiscordRestError(
                            _format_api_error(status, resp_body, self._token, url=url),
                            status=status,
                            code=_extract_error_code(resp_body),
                        )

                    hasher = hashlib.sha256()
                    total = 0
                    tmp_path: Path | None = None
                    fh = None
                    try:
                        if dst is not None:
                            dst_path = Path(dst)
                            dst_path.parent.mkdir(parents=True, exist_ok=True)
                            tmp_path = dst_path.with_name(f".{dst_path.name}.tmp.{uuid.uuid4().hex[:8]}")
                            fh = open(tmp_path, "wb")
                        while True:
                            single_read = False
                            try:
                                chunk = resp.read(_DOWNLOAD_CHUNK_BYTES)
                            except TypeError:
                                chunk = resp.read()
                                single_read = True
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > max_bytes:
                                raise DiscordRestError(
                                    f"Discord download exceeds limit ({max_bytes} bytes) for {safe_url}"
                                )
                            hasher.update(chunk)
                            if fh is not None:
                                fh.write(chunk)
                            if single_read:
                                break
                        if fh is not None:
                            fh.flush()
                            fh.close()
                            fh = None
                            tmp_path.replace(dst_path)
                    finally:
                        if fh is not None:
                            fh.close()
                        if tmp_path is not None:
                            tmp_path.unlink(missing_ok=True)
                    return total, hasher.hexdigest()
            except urllib.error.HTTPError as err:
                status = err.code
                resp_body = err.read() if err.fp is not None else b""
                resp_headers = err.headers
                if status == 429:
                    if attempt < MAX_429_RETRIES:
                        self._sleep(_parse_retry_after(resp_body, resp_headers))
                        continue
                    raise DiscordRestError(
                        f"Discord HTTP 429: rate limit exceeded after {MAX_429_RETRIES} retries",
                        status=429,
                    ) from None
                raise DiscordRestError(
                    _format_api_error(status, resp_body, self._token, url=url),
                    status=status,
                    code=_extract_error_code(resp_body),
                ) from None
            except urllib.error.URLError as err:
                reason = _scrub_secrets(str(err.reason), token=self._token, url=url)
                cause = err.reason if isinstance(err.reason, (BrokenPipeError, ConnectionResetError)) else None
                raise DiscordRestError(f"Discord network error: {reason}") from cause
            except OSError as err:
                reason = _scrub_secrets(str(err), token=self._token, url=url)
                raise DiscordRestError(f"Discord network error: {reason}") from err

        raise DiscordRestError("Discord download failed")  # pragma: no cover

    def post(
        self,
        channel_id: str | int,
        content: str,
        file: Path | None = None,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Post a message (and optional file attachment) to a channel or thread."""
        payload: dict[str, Any] = {"content": content}
        if components is not None:
            payload["components"] = components
        if file is None:
            raw = json.dumps(payload).encode("utf-8")
            ctype = "application/json"
        else:
            raw, ctype = _encode_multipart(payload, Path(file))
        return self._request(f"/channels/{channel_id}/messages", raw, ctype)

    def edit(
        self,
        channel_id: str | int,
        message_id: str | int,
        content: str,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Edit an existing message (and optional components) in a channel or thread."""
        payload: dict[str, Any] = {"content": content}
        if components is not None:
            payload["components"] = components
        return self._request(
            f"/channels/{channel_id}/messages/{message_id}",
            json.dumps(payload).encode("utf-8"),
            "application/json",
            method="PATCH",
        )

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
