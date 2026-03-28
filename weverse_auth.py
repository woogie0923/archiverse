"""
weverse_auth.py

Optional Weverse account token refresh support for long-running
DRM/live recording.

If a `weverse_refresh_token` is configured, we can refresh the Weverse
access token via accountapi.weverse.io and update `config.COMMON_HEADERS`
in-memory for subsequent API calls and N_m3u8DL-RE requests.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import requests

from config import CFG, COMMON_HEADERS


_REFRESH_ENDPOINT = "https://accountapi.weverse.io/api/v1/token/refresh"

# Values taken from the standalone recorder script (weverseRecorder.py).
_X_ACC_APP_SECRET = "5419526f1c624b38b10787e5c10b2a7a"
_X_ACC_APP_VERSION = "3.3.6"


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
    # Allow a couple of key names so users can choose what they have.
    return (
        str(CFG.get("weverse_refresh_token") or CFG.get("refresh_token") or "").strip()
    )


def get_access_token(min_valid_seconds: int = 6 * 3600) -> str:
    """
    Return a Weverse access token (raw token, not prefixed with "Bearer ").

    If a refresh token exists, refresh automatically when the cached token
    is close to expiring.
    """
    cached = _load_cached_token()
    now = int(time.time())
    expires = int(cached.get("expires") or 0)
    access = cached.get("accessToken") or ""

    # If cached access token is still sufficiently valid, reuse it.
    if access and expires and (expires - now) > min_valid_seconds:
        COMMON_HEADERS["Authorization"] = f"Bearer {access}"
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

    COMMON_HEADERS["Authorization"] = f"Bearer {accessToken}"
    return str(accessToken)
