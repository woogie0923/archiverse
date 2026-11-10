"""
weverse_auth.py

Optional Weverse account token refresh support for long-running
DRM/live recording.

If a `weverse_refresh_token` is configured, we can refresh the Weverse
access token via accountapi.weverse.io, update `config.COMMON_HEADERS`
in-memory, and persist the new access + refresh tokens into `config.yaml`
when possible.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import requests

from . import state
from .utils import console
from .config import (
    CFG,
    COMMON_HEADERS,
    apply_weverse_tokens_in_memory,
    persist_weverse_tokens_to_config,
    reload_weverse_tokens_from_config,
)


_REFRESH_ENDPOINT = "https://accountapi.weverse.io/api/v1/token/refresh"

_X_ACC_APP_SECRET = "5419526f1c624b38b10787e5c10b2a7a"
_X_ACC_APP_VERSION = "3.3.6"


def _format_duration_dhm(seconds: int, *, include_zero_days: bool = False) -> str:
    """
    Format seconds as a compact 'Xd Yh Zm' string.

    - Days and hours are omitted when zero (unless include_zero_days=True).
    - Minutes are always included.
    """
    s = max(0, int(seconds or 0))
    days = s // 86400
    hours = (s % 86400) // 3600
    mins = (s % 3600) // 60

    parts: list[str] = []
    if include_zero_days or days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    parts.append(f"{mins}m")
    return " ".join(parts)


def _token_cache_path() -> Path:
    # Keep next to this module so it works regardless of cwd.
    return Path(__file__).parent / "weverse_token.json"


def _load_cached_token() -> dict:
    p = _token_cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cached_token(data: dict):
    try:
        _token_cache_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _config_refresh_token() -> str:
    return str(CFG.get("weverse_refresh_token") or CFG.get("refresh_token") or "").strip()


def _clear_cached_token() -> None:
    p = _token_cache_path()
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def get_refresh_token() -> str:
    """
    Refresh token for accountapi.weverse.io.

    A newly pasted token in config.yaml wins over weverse_token.json, so a
    rotated/stale cache cannot block a token the user just copied in.
    After a successful refresh, the rotated token from the server is used.
    """
    reload_weverse_tokens_from_config()
    config_rt = _config_refresh_token()
    cached = _load_cached_token()
    cached_rt = str(cached.get("refreshToken") or "").strip()
    source_config_rt = str(cached.get("sourceConfigRefreshToken") or "").strip()
    if config_rt and config_rt not in (cached_rt, source_config_rt):
        return config_rt
    if cached_rt:
        return cached_rt
    return config_rt


def _refresh_headers() -> dict:
    # Never send Authorization: accountapi rejects refresh if an expired
    # access token is attached as Bearer.
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://weverse.io",
        "Referer": "https://weverse.io/",
        "User-Agent": COMMON_HEADERS.get("User-Agent") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "x-acc-app-secret": _X_ACC_APP_SECRET,
        "x-acc-app-version": _X_ACC_APP_VERSION,
        "x-acc-language": "en",
        "x-acc-service-id": "weverse",
        "x-acc-trace-id": str(uuid.uuid4()),
        "x-clog-user-device-id": str(uuid.uuid4()),
    }


def _post_refresh(refresh_token: str) -> dict:
    """POST /token/refresh with only the refresh token. No access token."""
    payload = json.dumps({"refreshToken": refresh_token})
    with requests.Session() as session:
        session.headers.clear()
        resp = session.post(
            _REFRESH_ENDPOINT,
            headers=_refresh_headers(),
            data=payload,
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json()


def get_access_token(min_valid_seconds: int = 6 * 3600, *, force: bool = False) -> str:
    """
    Return a Weverse access token (raw token, not prefixed with "Bearer ").

    If a refresh token exists, refresh automatically when the cached token
    is close to expiring.

    force=True: always POST /token/refresh (e.g. after HTTP 401 from the API).
    Reloads config.yaml first so a newly pasted refresh token is used.
    """
    reload_weverse_tokens_from_config()
    cached = _load_cached_token()
    now = int(time.time())
    expires = int(cached.get("expires") or 0)
    access = cached.get("accessToken") or ""

    # If cached access token is still sufficiently valid, reuse it.
    if (
        not force
        and access
        and expires
        and (expires - now) > min_valid_seconds
    ):
        COMMON_HEADERS["Authorization"] = f"Bearer {access}"
        remaining = max(0, expires - now)
        time_str = _format_duration_dhm(remaining, include_zero_days=True)
        msg = f"  [Auth] Using cached Weverse access token (~{time_str} remaining)."
        state.AUTH_STATUS_LINES = [msg]
        if state.DEBUG_MODE:
            console.print(msg)
        return str(access)

    refresh_token = get_refresh_token()
    config_rt = _config_refresh_token()
    # If no refresh token is configured, fall back to the static auth_token.
    if not refresh_token:
        bearer = COMMON_HEADERS.get("Authorization", "")
        if bearer.lower().startswith("bearer "):
            return bearer[7:]
        return bearer

    candidates: list[str] = []
    for token in (refresh_token, config_rt):
        if token and token not in candidates:
            candidates.append(token)

    last_err: Exception | None = None
    response = None
    for i, candidate in enumerate(candidates):
        try:
            if i > 0:
                console.print(
                    "  [Auth] Cached refresh token was rejected; "
                    "retrying with weverse_refresh_token from config.yaml…"
                )
            response = _post_refresh(candidate)
            last_err = None
            break
        except Exception as e:
            last_err = e

    if response is None:
        _clear_cached_token()
        raise last_err or RuntimeError("Token refresh failed")

    accessToken = response["accessToken"]
    refreshToken = response["refreshToken"]
    expiresIn = int(response["expiresIn"])

    data = {
        "accessToken": accessToken,
        "refreshToken": refreshToken,
        "sourceConfigRefreshToken": refreshToken,
        # Store absolute epoch seconds.
        "expires": (now + expiresIn),
    }
    _save_cached_token(data)

    apply_weverse_tokens_in_memory(accessToken, refreshToken)
    time_str = _format_duration_dhm(expiresIn, include_zero_days=True)
    msg_refresh = f"  [Auth] Refreshed Weverse access token via refresh token (valid for ~{time_str})."
    state.AUTH_STATUS_LINES = [msg_refresh]
    console.print(msg_refresh)

    if persist_weverse_tokens_to_config(accessToken, refreshToken):
        msg_saved = "  [Auth] Saved new tokens to config.yaml."
        state.AUTH_STATUS_LINES = [msg_refresh, msg_saved]
        console.print(msg_saved)
    return str(accessToken) 
