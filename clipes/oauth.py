"""OAuth token storage (mode 600), refresh, and local authorization flow."""

from __future__ import annotations

import http.server
import inspect
import json
import os
import secrets
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

DEFAULT_TOKENS_PATH = Path("/config/tokens.json")
DEFAULT_OAUTH_HOST = "127.0.0.1"
DEFAULT_OAUTH_PORT = 8765

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_SCOPE = "user.info.basic,video.publish"


class OAuthError(RuntimeError):
    """Raised when an OAuth authorization or refresh flow fails."""


def resolve_tokens_path(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    env_path = os.environ.get("CLIPES_TOKENS_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_TOKENS_PATH


def load_tokens(path: Path | None = None) -> dict[str, Any]:
    """Load OAuth tokens JSON dict, or {} if the file does not exist yet."""
    target = resolve_tokens_path(path)
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OAuthError(f"Failed to read tokens file: {exc}") from None
    if not isinstance(data, dict):
        raise OAuthError("Invalid tokens file format: expected JSON object")
    return data


def save_tokens(tokens: dict[str, Any], path: Path | None = None) -> Path:
    """Atomically write tokens JSON with mode 0600."""
    target = resolve_tokens_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(tokens, indent=2, ensure_ascii=False) + "\n"

    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_tokens_", suffix=".json", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(target)
    finally:
        tmp_path.unlink(missing_ok=True)
    return target


def _scrub(text: str, secrets_list: list[str]) -> str:
    out = text
    for sec in secrets_list:
        if sec:
            out = out.replace(sec, "***")
    return out


def _post_form(
    url: str,
    params: dict[str, str],
    urlopen: Callable[..., Any],
    timeout: float,
    secrets_to_scrub: list[str],
) -> dict[str, Any]:
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="replace") if err.fp is not None else ""
        detail = _scrub(err_body[:200], secrets_to_scrub)
        raise OAuthError(f"OAuth HTTP {err.code}: {detail}".strip()) from None
    except urllib.error.URLError as err:
        raise OAuthError(f"OAuth network error: {_scrub(str(err.reason), secrets_to_scrub)}") from None

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise OAuthError(f"Invalid OAuth JSON response: {exc}") from None
    if not isinstance(data, dict):
        raise OAuthError("Invalid OAuth response: expected JSON object")
    if isinstance(data.get("data"), dict) and "access_token" in data["data"]:
        data = data["data"]
    if data.get("error"):
        err_code = _scrub(str(data.get("error")), secrets_to_scrub)
        err_desc = _scrub(str(data.get("error_description") or ""), secrets_to_scrub)
        raise OAuthError(f"OAuth error {err_code}: {err_desc}".strip(": "))
    return data


def _google_creds(client_id: str | None, client_secret: str | None) -> tuple[str, str]:
    cid = (client_id or os.environ.get("YOUTUBE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    csec = (
        client_secret
        or os.environ.get("YOUTUBE_CLIENT_SECRET")
        or os.environ.get("GOOGLE_CLIENT_SECRET")
        or ""
    ).strip()
    if not cid or not csec:
        raise OAuthError("YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET are required")
    return cid, csec


def _tiktok_creds(client_key: str | None, client_secret: str | None) -> tuple[str, str]:
    ckey = (client_key or os.environ.get("TIKTOK_CLIENT_KEY") or os.environ.get("TIKTOK_CLIENT_ID") or "").strip()
    csec = (client_secret or os.environ.get("TIKTOK_CLIENT_SECRET") or "").strip()
    if not ckey or not csec:
        raise OAuthError("TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET are required")
    return ckey, csec


def refresh_google(
    refresh_token: str | None = None,
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    tokens_path: Path | None = None,
    urlopen: Callable[..., Any] | None = None,
    timeout: float = 30.0,
) -> str:
    """Exchange Google refresh_token for a new access_token."""
    opener = urlopen if urlopen is not None else urllib.request.urlopen
    tokens = load_tokens(tokens_path) if refresh_token is None else None
    rtok = (refresh_token or (tokens or {}).get("youtube", {}).get("refresh_token") or "").strip()
    if not rtok:
        raise OAuthError("YouTube refresh_token missing; run clipes auth youtube")

    cid, csec = _google_creds(client_id, client_secret)
    data = _post_form(
        GOOGLE_TOKEN_URL,
        {
            "client_id": cid,
            "client_secret": csec,
            "refresh_token": rtok,
            "grant_type": "refresh_token",
        },
        urlopen=opener,
        timeout=timeout,
        secrets_to_scrub=[csec, rtok],
    )
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise OAuthError("OAuth response missing access_token")

    new_refresh = str(data.get("refresh_token") or "").strip()
    if new_refresh and new_refresh != rtok:
        current = load_tokens(tokens_path)
        yt_entry = dict(current.get("youtube") or {})
        yt_entry["refresh_token"] = new_refresh
        current["youtube"] = yt_entry
        save_tokens(current, tokens_path)

    return access_token


def refresh_tiktok(
    refresh_token: str | None = None,
    *,
    client_key: str | None = None,
    client_secret: str | None = None,
    tokens_path: Path | None = None,
    urlopen: Callable[..., Any] | None = None,
    timeout: float = 30.0,
) -> str:
    """Exchange TikTok refresh_token for a new access_token and persist rotated refresh_token."""
    opener = urlopen if urlopen is not None else urllib.request.urlopen
    tokens = load_tokens(tokens_path) if refresh_token is None else None
    rtok = (refresh_token or (tokens or {}).get("tiktok", {}).get("refresh_token") or "").strip()
    if not rtok:
        raise OAuthError("TikTok refresh_token missing; run clipes auth tiktok")

    ckey, csec = _tiktok_creds(client_key, client_secret)
    data = _post_form(
        TIKTOK_TOKEN_URL,
        {
            "client_key": ckey,
            "client_secret": csec,
            "grant_type": "refresh_token",
            "refresh_token": rtok,
        },
        urlopen=opener,
        timeout=timeout,
        secrets_to_scrub=[csec, rtok],
    )
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise OAuthError("OAuth response missing access_token")

    new_refresh = str(data.get("refresh_token") or "").strip()
    open_id = str(data.get("open_id") or "").strip()
    if new_refresh or open_id:
        current = load_tokens(tokens_path)
        tt_entry = dict(current.get("tiktok") or {})
        if new_refresh:
            tt_entry["refresh_token"] = new_refresh
        if open_id:
            tt_entry["open_id"] = open_id
        current["tiktok"] = tt_entry
        save_tokens(current, tokens_path)

    return access_token


def _parse_callback_result(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, (tuple, list)) and len(raw) == 2:
        return {"code": str(raw[0]), "state": str(raw[1])}
    if isinstance(raw, str):
        parsed = urllib.parse.urlparse(raw)
        query = parsed.query if ("?" in raw or parsed.scheme) else raw.lstrip("?")
        qs = urllib.parse.parse_qs(query)
        return {k: v[0] for k, v in qs.items() if v}
    raise OAuthError("Invalid OAuth callback payload")


def _listen_local_callback(
    port: int,
    host: str = DEFAULT_OAUTH_HOST,
) -> tuple[http.server.HTTPServer, dict[str, str], threading.Thread]:
    captured: dict[str, str] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            for k, v in qs.items():
                if v:
                    captured[k] = v[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body>Autorizacao recebida. Pode fechar esta aba.</body></html>")

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = http.server.HTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return server, captured, thread


def _build_auth_url(plataforma: str, cid: str, redirect_uri: str, state: str) -> str:
    if plataforma == "youtube":
        params = {
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": YOUTUBE_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    params = {
        "client_key": cid,
        "scope": TIKTOK_SCOPE,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{TIKTOK_AUTH_URL}?{urllib.parse.urlencode(params)}"


def auth_flow(
    plataforma: str,
    open_url: Callable[[str], Any] | None = None,
    serve: Callable[..., Any] | None = None,
    *,
    port: int | None = None,
    bind_host: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    tokens_path: Path | None = None,
    urlopen: Callable[..., Any] | None = None,
    printer: Callable[[str], Any] = print,
    timeout: float = 180.0,
) -> Path:
    """Run local OAuth authorization flow and persist refresh token atomically with mode 0600."""
    opener = urlopen if urlopen is not None else urllib.request.urlopen
    plat = (plataforma or "").strip().lower()
    if plat not in ("youtube", "tiktok"):
        raise ValueError(f"Unsupported OAuth platform: {plataforma}")

    if plat == "youtube":
        cid, csec = _google_creds(client_id, client_secret)
    else:
        cid, csec = _tiktok_creds(client_id, client_secret)

    state = secrets.token_urlsafe(24)

    if serve is not None:
        eff_port = port if port is not None else DEFAULT_OAUTH_PORT
        redirect_uri = f"http://127.0.0.1:{eff_port}/callback"
        auth_url = _build_auth_url(plat, cid, redirect_uri, state)
        printer(auth_url)
        if open_url is not None:
            open_url(auth_url)
        sig = inspect.signature(serve)
        raw_cb = serve() if len(sig.parameters) == 0 else serve(auth_url)
        cb = _parse_callback_result(raw_cb)
    else:
        raw_port = os.environ.get("CLIPES_OAUTH_PORT", "").strip()
        eff_port = port if port is not None else (int(raw_port) if raw_port else DEFAULT_OAUTH_PORT)
        eff_host = (bind_host or os.environ.get("CLIPES_OAUTH_BIND") or DEFAULT_OAUTH_HOST).strip() or DEFAULT_OAUTH_HOST
        server, captured, thread = _listen_local_callback(eff_port, host=eff_host)
        try:
            redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
            auth_url = _build_auth_url(plat, cid, redirect_uri, state)
            printer(auth_url)
            if open_url is not None:
                open_url(auth_url)
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise OAuthError("Timed out waiting for OAuth callback")
            cb = dict(captured)
        finally:
            server.server_close()

    if cb.get("error"):
        raise OAuthError(f"OAuth authorization error: {cb['error']}")

    returned_state = cb.get("state", "")
    if not secrets.compare_digest(returned_state, state):
        raise OAuthError("OAuth state mismatch; aborting without exchanging code")

    code = cb.get("code", "").strip()
    if not code:
        raise OAuthError("OAuth callback missing authorization code")

    if plat == "youtube":
        data = _post_form(
            GOOGLE_TOKEN_URL,
            {
                "code": code,
                "client_id": cid,
                "client_secret": csec,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            urlopen=opener,
            timeout=30.0,
            secrets_to_scrub=[csec, code],
        )
        refresh_tok = str(data.get("refresh_token") or "").strip()
        if not refresh_tok:
            raise OAuthError("Google OAuth response missing refresh_token")
        entry: dict[str, Any] = {"refresh_token": refresh_tok}
    else:
        data = _post_form(
            TIKTOK_TOKEN_URL,
            {
                "code": code,
                "client_key": cid,
                "client_secret": csec,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            urlopen=opener,
            timeout=30.0,
            secrets_to_scrub=[csec, code],
        )
        refresh_tok = str(data.get("refresh_token") or "").strip()
        if not refresh_tok:
            raise OAuthError("TikTok OAuth response missing refresh_token")
        entry = {"refresh_token": refresh_tok}
        open_id = str(data.get("open_id") or "").strip()
        if open_id:
            entry["open_id"] = open_id

    tokens = load_tokens(tokens_path)
    tokens[plat] = entry
    saved = save_tokens(tokens, tokens_path)
    printer("token salvo")
    return saved
