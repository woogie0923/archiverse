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

import state
from utils import console
from config import (
    CFG,
    COMMON_HEADERS,
    apply_weverse_tokens_in_memory,
    persist_weverse_tokens_to_config,
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


def get_refresh_token() -> str:
    """
    Refresh token for accountapi.weverse.io.

    Prefer the token stored in weverse_token.json after a successful refresh
    (server may rotate it); fall back to config.
    """
    cached = _load_cached_token()
    cached_rt = str(cached.get("refreshToken") or "").strip()
    if cached_rt:
        return cached_rt
    return str(CFG.get("weverse_refresh_token") or CFG.get("refresh_token") or "").strip()


def get_access_token(min_valid_seconds: int = 6 * 3600, *, force: bool = False) -> str:
    """
    Return a Weverse access token (raw token, not prefixed with "Bearer ").

    If a refresh token exists, refresh automatically when the cached token
    is close to expiring.

    force=True: always POST /token/refresh (e.g. after HTTP 401 from the API).
    """
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
        if state.DEBUG_MODE:
            remaining = max(0, expires - now)
            time_str = _format_duration_dhm(remaining, include_zero_days=True)
            console.print(
                f"  [Auth] Using cached Weverse access token (~{time_str} remaining)."
            )
        return str(access)

    refresh_token = get_refresh_token()
    # If no refresh token is configured, fall back to the static auth_token.
    if not refresh_token:
        # COMMON_HEADERS.Authorization is already "Bearer <token>".
        bearer = COMMON_HEADERS.get("Authorization", "")
        if bearer.lower().startswith("bearer "):
            return bearer[7:]
        return bearer

    headers = {
        "content-type": "application/json",
        "origin": "https://weverse.io",
        "referer": "https://weverse.io/",
        "x-acc-app-secret": _X_ACC_APP_SECRET,
        "x-acc-app-version": _X_ACC_APP_VERSION,
        "x-acc-language": "en",
        "x-acc-service-id": "weverse",
        "x-acc-trace-id": str(uuid.uuid4()),
        "x-clog-user-device-id": str(uuid.uuid4()),
    }
    raw = json.dumps({"refreshToken": refresh_token})

    resp = requests.post(_REFRESH_ENDPOINT, headers=headers, data=raw, timeout=30)
    resp.raise_for_status()
    response = resp.json()

    accessToken = response["accessToken"]
    refreshToken = response["refreshToken"]
    expiresIn = int(response["expiresIn"])

    data = {
        "accessToken": accessToken,
        "refreshToken": refreshToken,
        # Store absolute epoch seconds.
        "expires": (now + expiresIn),
    }
    _save_cached_token(data)

    apply_weverse_tokens_in_memory(accessToken, refreshToken)
    time_str = _format_duration_dhm(expiresIn, include_zero_days=True)
    console.print(
        f"  [Auth] Refreshed Weverse access token via refresh token (valid for ~{time_str})."
    )

    if persist_weverse_tokens_to_config(accessToken, refreshToken):
        console.print("  [Auth] Saved new tokens to config.yaml.")
    return str(accessToken) 
