"""
api.py
Weverse API helpers: extractor setup, request runner, post fetcher,
and the community-scoped password cache.
"""
import hashlib
import hmac
import base64
import re
import json
import os
import time
import urllib.parse
import uuid
from pathlib import Path

import requests

import state
from utils import console
from rich.text import Text
from config import (
    COMMON_HEADERS, WEVERSE_API_BASE, WEVERSE_HMAC_KEY,
    get_folder, CACHE_ENABLED,
)

# ---------------------------------------------------------------------------
# API response cache
#
# Structure under {community}/Cache/:
#   artists/{member_id}.json        — all post details for one artist
#   official_media.json             — official media tab post details
#   lives.json                      — live post details
#   profiles.json                   — member profile data
#   post_details.json               — individual post details (fallback)
#
# Only stable single-item detail responses are cached.
# Paginated listing endpoints are never cached (each page is unique and
# must be fetched fresh to detect new posts).
# ---------------------------------------------------------------------------

def _cache_root() -> Path | None:
    if not CACHE_ENABLED or not state.COMMUNITY_NAME:
        return None
    base = Path(get_folder("api_cache", community=state.COMMUNITY_NAME))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve_cache_path(req: str) -> Path | None:
    """
    Map a request URL to a categorised cache file path.
    Returns None if this request should not be cached.

    Paginated pages (those with after=) are cached individually — each full
    URL (including cursor) maps to its own entry inside the listings file.
    This is safe because:
      - Each cursor uniquely identifies a specific page of results
      - Page 1 (no cursor) is always fetched live, so new posts are picked up
      - Pages 2+ are stable — their content never changes once published
    """
    root = _cache_root()
    if root is None:
        return None

    if "/inKey" in req or "/vod/" in req or "/me?" in req:
        return None

    m = re.search(r"/member-([a-f0-9]+)/posts", req)
    if m:
        mid = m.group(1)
        artists_dir = root / "artists"
        artists_dir.mkdir(exist_ok=True)
        name = _member_names.get(mid, "")
        safe_name = re.sub(r'[<>:"/\\|?* ]', "_", name) if name else ""
        fname = f"{safe_name}_{mid}.json" if safe_name else f"{mid}.json"
        return artists_dir / fname

    # Individual post detail
    if "/post/v1.0/post-" in req:
        return root / "post_details.json"

    # Member profile
    if "/member/v1.0/member-" in req and "/posts" not in req:
        return root / "profiles.json"

    # Artist highlight / roster
    if "/artistpedia/v1.0/" in req:
        return root / "profiles.json"

    # Official media (paginated listing + individual details)
    if "/media/v1.0/" in req or "/MEDIA_HOME/" in req:
        return root / "official_media.json"

    # Artist community feed (used for former members)
    if "/artistTabPosts" in req:
        return root / "artist_tab_posts.json"

    # Lives (paginated listing)
    if "/liveTabPosts" in req:
        return root / "lives.json"

    # Moments listing
    if "/filterType=MOMENT_VIEWER" in req:
        return root / "moments.json"

    # Artist comments
    if "/artistComments" in req:
        return root / "comments.json"

    return None


def _cache_load(path: Path) -> dict:
    """Load a cache file as a dict keyed by request URL hash."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _cache_save(path: Path, store: dict):
    """Persist a cache store dict to disk."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
    except Exception:
        pass


def _slim_author(a: dict) -> dict:
    """Strip author block to only fields read by the code."""
    if not a:
        return {}
    result = {
        "memberId":   a.get("memberId"),
        "profileType": a.get("profileType"),
        "profileName": a.get("profileName"),
    }
    aop = a.get("artistOfficialProfile")
    if aop:
        result["artistOfficialProfile"] = {
            "officialName": aop.get("officialName"),
            "birthday":     aop.get("birthday"),
        }
    return result


def _slim_response(req: str, data: dict) -> dict:
    """
    Strip a cached API response down to only the fields the code actually reads,
    discarding thumbnails, profile images, badges, community metadata, etc.
    Reduces cache file sizes by ~80-90%.
    """
    if not isinstance(data, dict):
        return data

    if "/artistComments" in req:
        def _slim_comment(c):
            parent = c.get("parent", {})
            slim_parent = {"type": parent.get("type")}
            pdata = parent.get("data")
            if pdata:
                slim_parent["data"] = {"body": pdata.get("body")}
            return {
                "body":      c.get("body"),
                "createdAt": c.get("createdAt"),
                "commentId": c.get("commentId"),
                "author":    _slim_author(c.get("author", {})),
                "parent":    slim_parent,
            }
        return {
            "data":   [_slim_comment(c) for c in data.get("data", [])],
            "paging": data.get("paging"),
        }

    if any(x in req for x in ("/posts", "/artistTabPosts", "/liveTabPosts")):
        def _slim_post(p):
            ext = p.get("extension", {})
            slim_ext = {}
            for key in ("moment", "momentW1", "video", "image", "mediaInfo"):
                if key in ext:
                    slim_ext[key] = ext[key]
            return {
                "postId":       p.get("postId"),
                "postType":     p.get("postType"),
                "publishedAt":  p.get("publishedAt"),
                "membershipOnly": p.get("membershipOnly"),
                "plainBody":    p.get("plainBody"),
                "author":       _slim_author(p.get("author", {})),
                "extension":    slim_ext,
                "summary": {
                    "videoCount": p.get("summary", {}).get("videoCount", 0),
                    "photoCount": p.get("summary", {}).get("photoCount", 0),
                    "thumbnails": p.get("summary", {}).get("thumbnails", [])[:1],
                },
                "locked": p.get("locked"),
                "shareUrl": p.get("shareUrl"),
            }
        return {
            "data":             [_slim_post(p) for p in data.get("data", [])],
            "paging":           data.get("paging"),
            "sortType":         data.get("sortType"),
            "availableSortTypes": data.get("availableSortTypes"),
        }

    if "/post/v1.0/post-" in req:
        return {
            "postId":        data.get("postId"),
            "postType":      data.get("postType"),
            "publishedAt":   data.get("publishedAt"),
            "membershipOnly": data.get("membershipOnly"),
            "plainBody":     data.get("plainBody"),
            "body":          data.get("body"),
            "title":         data.get("title"),
            "author":        _slim_author(data.get("author", {})),
            "attachment":    data.get("attachment"),
            "extension":     data.get("extension"),
        }

    if "/member/v1.0/member-" in req or "/artistpedia/v1.0/" in req:
        def _slim_profile(p):
            return {
                "memberId":             p.get("memberId"),
                "artistOfficialProfile": p.get("artistOfficialProfile"),
                "profileName":          p.get("profileName"),
                "profileImageUrl":      p.get("profileImageUrl"),
            }
        if "artistProfiles" in data:
            return {
                "artistProfiles": [_slim_profile(p) for p in data.get("artistProfiles", [])],
            }
        return data

    if "/media/v1.0/" in req or "/MEDIA_HOME/" in req:
        def _slim_media(p):
            ext = p.get("extension", {})
            slim_ext = {k: ext[k] for k in ("video", "image", "mediaInfo") if k in ext}
            return {
                "postId":        p.get("postId"),
                "mediaId":       p.get("mediaId"),
                "publishedAt":   p.get("publishedAt"),
                "createdAt":     p.get("createdAt"),
                "membershipOnly": p.get("membershipOnly"),
                "title":         p.get("title"),
                "author":        _slim_author(p.get("author", {})),
                "extension":     slim_ext,
                "summary": {
                    "videoCount": p.get("summary", {}).get("videoCount", 0),
                    "photoCount": p.get("summary", {}).get("photoCount", 0),
                    "thumbnails": p.get("summary", {}).get("thumbnails", [])[:1],
                },
            }
        return {
            "data":   [_slim_media(p) for p in data.get("data", [])],
            "paging": data.get("paging"),
        }

    return data


def _cache_get(req: str):
    """Return cached response dict for req, or None if not cached."""
    p = _resolve_cache_path(req)
    if p is None:
        return None
    store = _cache_load(p)
    return store.get(req)


def _cache_set(req: str, data: dict):
    """Store response data for req in the appropriate cache file."""
    p = _resolve_cache_path(req)
    if p is None or data is None:
        return
    store = _cache_load(p)
    store[req] = _slim_response(req, data)
    _cache_save(p, store)

_ext = None


class _DirectWeverseExtractor:


    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(COMMON_HEADERS)
        self._device_id = uuid.uuid4().hex

    @staticmethod
    def _append_query(path: str, extra: dict[str, str]) -> str:
        parsed = urllib.parse.urlsplit(path)
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        seen = {k for k, _ in pairs}
        for k, v in extra.items():
            if k not in seen:
                pairs.append((k, v))
        query = urllib.parse.urlencode(pairs)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))

    def _call_api(self, req: str, _unused: str = "", data: bytes | None = None):
        api_path = req if req.startswith("/") else f"/{req}"
        api_path = self._append_query(api_path, {
            "appId": "be4d79eb8fc7bd008ee82c8ec4ff6fd4",
            "language": "en",
            "os": "WEB",
            "platform": "WEB",
            "wpf": "pc",
        })
        wmsgpad = str(int(time.time() * 1000))
        sign_input = f"{api_path[:255]}{wmsgpad}".encode("utf-8")
        wmd = base64.b64encode(hmac.new(WEVERSE_HMAC_KEY, sign_input, hashlib.sha1).digest()).decode("utf-8")
        url = f"{WEVERSE_API_BASE}{api_path}"
        signed_query = {"wmsgpad": wmsgpad, "wmd": wmd}
        method = "POST" if data is not None else "GET"
        payload = data if data is not None else None
        headers = {"WEV-device-Id": self._device_id}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            resp = self._session.request(
                method, url, params=signed_query, data=payload, headers=headers, timeout=30
            )
        except Exception as e:
            raise Exception(f"HTTP request failed: {e}")

        if resp.status_code < 200 or resp.status_code >= 300:
            body = (resp.text or "")[:300]
            raise Exception(f"HTTP {resp.status_code}: {body}")

        try:
            return resp.json()
        except Exception:
            body = (resp.text or "")[:300]
            raise Exception(f"Invalid JSON response: {body}")


def make_extractor():
    global _ext
    if _ext is None:
        _ext = _DirectWeverseExtractor()
    return _ext


def _req_label(req: str) -> str:
    """Extract a short human-readable label from a request URL."""
    path = req.split("?")[0]
    parts = [p for p in path.split("/") if p]
    label = " / ".join(parts[-2:]) if len(parts) >= 2 else path[:60]
    if "after=" in req:
        import urllib.parse as _up
        qs = dict(_up.parse_qsl(req.split("?", 1)[-1]))
        cursor = qs.get("after", "")[:12]
        label += f" [after={cursor}…]"
    return label


def run_extr(extr, req, post=False, retries=None):
    """
    Call the Weverse API with automatic retry on transient errors.

    retries=None  -> retry forever   (pagination / metadata fetches)
    retries=N     -> give up after N attempts and raise (download steps)

    Every request prints either [Cache] (served from disk) or [API] (live
    fetch) so you can see exactly what is and isn't hitting the network.

    Cache strategy:
      - Detail endpoints (artistComments, playInfo, post detail, profiles)
        are stable — always served from cache if available.
      - Listing endpoints (member posts, liveTabPosts, media, moments):
          * Page 1 (no cursor): always fetched live to detect new content.
          * Page 2+ (with cursor): served from cache if available.
    """

    _STABLE_PATTERNS = (
        "/playInfo",
        "/post/v1.0/post-",
        "/member/v1.0/member-",
        "/artistpedia/v1.0/",
    )
    _is_stable  = any(p in req for p in _STABLE_PATTERNS)
    _has_cursor = "after=" in req
    _use_cache  = _is_stable or _has_cursor
    if _use_cache:
        cached = _cache_get(req)
        if cached is not None:
            if state.DEBUG_MODE:
                console.print(f"  [cyan dim][Cache][/cyan dim] {_req_label(req)}")
            return cached

    _silent = ("/me?" in req and "profileName" in req)
    if not _silent and state.DEBUG_MODE:
        if not _use_cache:
            console.print(f"  [yellow dim][API][/yellow dim] {_req_label(req)} [dim](page 1 — always fresh)[/dim]")
        else:
            console.print(f"  [yellow dim][API][/yellow dim] {_req_label(req)}")

    attempt = 0
    last_exc = None
    auth_refresh_done = False

    while retries is None or attempt < retries:
        try:
            post_byte = b"" if post else None
            resp = extr._call_api(req, "", data=post_byte)

            if "/me?" in req and "profileName" in req:
                _print_status_board(resp)

            _cache_set(req, resp)

            return resp

        except Exception as e:
            last_exc = e
            _e = str(e).lower()
            is_401 = (
                "401" in str(e)
                or "account_401" in _e
                or "unauthorized" in _e
            )

            if is_401 and not auth_refresh_done:
                try:
                    from weverse_auth import get_refresh_token, get_access_token

                    if get_refresh_token():
                        console.print(
                            "  [Auth] HTTP 401 — refreshing access token and retrying…"
                        )
                        get_access_token(force=True)
                        auth_refresh_done = True
                        continue
                except Exception as re_err:
                    console.print(f"  [Auth] Token refresh failed: {re_err}")

            attempt += 1

            is_access_denied = (
                "does not have access" in _e
                or "403" in str(e)
                or "logged-in" in _e
                or "only available for" in _e
                or "401" in str(e)
            )

            if is_access_denied and state.SKIP_MEMBERSHIP:
                return None

            if is_access_denied and retries is not None:
                if state.DEBUG_MODE:
                    console.print(f"  [Skip] Access denied for request (attempt {attempt}): {e}")
                break

            console.print(f"API Error (attempt {attempt}): {e}")

            if is_access_denied:
                time.sleep(30.0)
            else:
                time.sleep(5.0)

    if (
        state.SKIP_MEMBERSHIP
        and last_exc
        and ("403" in str(last_exc) or "access" in str(last_exc).lower())
    ):
        return None

    raise last_exc


_last_status: dict = {}

_member_names: dict = {}


def register_member_name(member_id: str, name: str):
    """Register a member_id -> name mapping for cache filename labelling."""
    if member_id and name:
        _member_names[member_id] = name


def _print_status_board(resp: dict):
    global _last_status
    _last_status = resp
    print_status_board(resp)


def print_status_board(resp: dict | None = None):
    """
    Print the community status board using Rich markup.
    Pass None to reprint the last board (e.g. at the top of the menu).
    """
    global _last_status
    if resp is None:
        resp = _last_status
    if not resp:
        return

    profile_name = resp.get("profileName", "Unknown")
    fandom_name  = resp.get("community", {}).get("fandomName", "N/A")
    has_mem      = resp.get("hasMembership", False)
    comm_id      = resp.get("communityId", "Unknown")
    mem_text     = "[green]Active[/green]" if has_mem else "[red]Inactive[/red]"

    console.print()
    console.print(f"  [bold]Community:            [/bold] {state.COMMUNITY_NAME}")
    console.print(f"  [bold]Community Profile Name:[/bold] [yellow]{profile_name}[/yellow]")
    console.print(f"  [bold]Community ID:         [/bold] {comm_id}")
    console.print(f"  [bold]Fandom:               [/bold] {fandom_name}")
    console.print(f"  [bold]Membership:           [/bold] {mem_text}")
    console.print(f"")


def menu_status_board_renderable(*, compact: bool = False) -> Text:
    """
    Same content as print_status_board(), as a Rich Text for embedding inside
    the interactive Live menu (screen clear would otherwise remove the printed board).

    compact=True: one line (wraps on very narrow terminals) for small windows.
    """
    if not _last_status:
        return Text("")
    resp = _last_status
    profile_name = resp.get("profileName", "Unknown")
    fandom_name  = resp.get("community", {}).get("fandomName", "N/A")
    has_mem      = resp.get("hasMembership", False)
    comm_id      = resp.get("communityId", "Unknown")
    mem_text     = "[green]Active[/green]" if has_mem else "[red]Inactive[/red]"
    if compact:
        line = (
            f"  [bold]{state.COMMUNITY_NAME}[/bold] • [yellow]{profile_name}[/yellow] • "
            f"{fandom_name} • [bold]ID[/bold] {comm_id} • [bold]Mem[/bold] {mem_text}"
        )
        return Text.from_markup(line)
    block = (
        "\n"
        f"  [bold]Community:            [/bold] {state.COMMUNITY_NAME}\n"
        f"  [bold]Community Profile Name:[/bold] [yellow]{profile_name}[/yellow]\n"
        f"  [bold]Community ID:         [/bold] {comm_id}\n"
        f"  [bold]Fandom:               [/bold] {fandom_name}\n"
        f"  [bold]Membership:           [/bold] {mem_text}"
    )
    return Text.from_markup(block)


def fetch_lives_page(cursor=None):
    """
    Fetch one page of liveTabPosts.
    fieldSet=postsV1 is REQUIRED — without it the API omits the
    'extension' block and all metadata falls back to defaults.
    """
    base = f"/post/v1.0/community-{state.COMMUNITY_ID}/liveTabPosts?fieldSet=postsV1"
    url  = f"{base}&after={cursor}" if cursor else base
    return run_extr(make_extractor(), url)


def fetch_onair_lives():
    """
    Fetch one snapshot of currently on-air lives (ongoing livestreams).

    Returns the raw JSON response from:
      /post/v1.0/community-{communityId}/liveTab?fields=onAirLivePosts...

    The result shape matches weverseRecorder.py where callers use:
      resp["onAirLivePosts"]["data"]
    """
    if not state.COMMUNITY_ID:
        return None

    # Important: keep the endpoint+fields structure aligned with the
    # standalone recorder script.
    fields_val = (
        "onAirLivePosts.fieldSet(postsV1).limit(10),"
        "liveTabPosts.fieldSet(postsV1).limit(500)"
    )
    req = (
        f"/post/v1.0/community-{state.COMMUNITY_ID}/liveTab"
        f"?fields={urllib.parse.quote(fields_val, safe='')}"
    )
    return run_extr(make_extractor(), req, retries=3)


def fetch_post_details(summary: dict):
    """
    Fetches full post metadata for a summary item.
    Includes a password caching system for locked posts.

    Returns None if the post should be skipped (membership skip flag,
    locked and password unknown, or API error).
    """
    post_id = summary.get("postId")

    if summary.get("membershipOnly") and state.SKIP_MEMBERSHIP:
        return None
    if not summary.get("membershipOnly") and state.SKIP_PUBLIC:
        return None

    if summary.get("locked") is True:
        console.print(f"\n  [LOCKED] Post {post_id}")
        pwd           = get_cached_password(post_id)
        is_from_cache = pwd is not None
        if is_from_cache:
            console.print(f"  -> Found saved password in {state.COMMUNITY_NAME} cache. Attempting...")
        else:
            console.print("  -> No saved password found.")
            console.print("  -> Press [Enter] to try an empty password, or type a password if known.")
            console.print("  -> Type 'skip' to skip this post.")
            pwd = console.input("  -> Password: ")
        while True:
            password_param = f"&lockPassword={urllib.parse.quote(pwd)}"
            req = f"/post/v1.0/post-{post_id}?fieldSet=postV1{password_param}"
            try:
                resp = run_extr(make_extractor(), req, retries=1)
                blind = resp.get("blindType", "") if resp else "LOCKED"
                if resp and blind not in ("LOCKED", "MEMBERSHIP", "NOT_MEMBER"):
                    console.print(f"  [Success] Post {post_id} unlocked.")
                    if not is_from_cache:
                        save_password_to_cache(post_id, pwd)
                    return resp
                if is_from_cache:
                    console.print("  -> [Fail] Cached password was incorrect.")
                    is_from_cache = False
                else:
                    console.print("  -> [Fail] Incorrect password.")
                console.print("  -> Enter password, or press Enter for empty. Type 'skip' to skip.")
                pwd = console.input("  -> Password: ")  # do NOT strip
                if pwd.lower().strip() in ("s", "skip"):
                    console.print(f"  -> Skipping {post_id}.")
                    return None
                continue
            except Exception:
                return None

    try:
        resp = run_extr(make_extractor(), f"/post/v1.0/post-{post_id}?fieldSet=postV1", retries=3)
    except Exception as e:
        if state.DEBUG_MODE:
            console.print(f"  -> Skipping {post_id}: {e}")
        return None

    if resp and resp.get("blindType") not in ("LOCKED", "MEMBERSHIP", "NOT_MEMBER"):
        return resp

    if resp is None:
        return None

    # Membership-gated content returns a blindType like "MEMBERSHIP" or "NOT_MEMBER"
    # rather than a 401. These are NOT password-locked — skip without prompting.
    blind = resp.get("blindType", "") if resp else ""
    if blind in ("MEMBERSHIP", "NOT_MEMBER"):
        console.print(f"  [Skip] Post {post_id} is membership-only content. Your account does not have access.")
        return None

    console.print(f"\n  [LOCKED] Post {post_id} detected (response flag).")

    pwd           = get_cached_password(post_id)
    is_from_cache = pwd is not None

    if is_from_cache:
        console.print(f"  -> Found saved password in {state.COMMUNITY_NAME} cache. Attempting...")
    else:
        console.print("  -> No saved password found.")
        console.print("  -> Press [Enter] to try an empty password, or type a password if known.")
        console.print("  -> Type 'skip' to skip this post.")
        pwd = console.input("  -> Password: ")
    while True:
        password_param = f"&lockPassword={urllib.parse.quote(pwd)}"
        req = f"/post/v1.0/post-{post_id}?fieldSet=postV1{password_param}"

        try:
            resp = run_extr(make_extractor(), req, retries=1)
            blind = resp.get("blindType", "") if resp else "LOCKED"

            if resp and blind not in ("LOCKED", "MEMBERSHIP", "NOT_MEMBER"):
                console.print(f"  [Success] Post {post_id} unlocked.")
                if not is_from_cache:
                    save_password_to_cache(post_id, pwd)
                return resp

            if blind in ("MEMBERSHIP", "NOT_MEMBER"):
                console.print(f"  [Skip] Post {post_id} is membership-only content. Your account does not have access.")
                return None

            if is_from_cache:
                console.print("  -> [Fail] Cached password was incorrect.")
                is_from_cache = False
            else:
                console.print("  -> [Fail] Incorrect password.")
            console.print("  -> Enter password, or press Enter for empty. Type 'skip' to skip.")
            pwd = console.input("  -> Password: ")  # do NOT strip
            if pwd.lower().strip() in ("s", "skip"):
                console.print(f"  -> Skipping {post_id}.")
                return None
            continue

        except Exception:
            return None


def get_cached_password(post_id: str):
    """Checks the community-specific JSON for a saved password."""
    cache_path = get_folder("password_cache", community=state.COMMUNITY_NAME)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f).get(post_id)
        except Exception:
            return None
    return None


def save_password_to_cache(post_id: str, password: str):
    """Saves a successful password to the community-specific JSON."""
    cache_path = get_folder("password_cache", community=state.COMMUNITY_NAME)
    cache: dict = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            pass
    cache[post_id] = password
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=4)