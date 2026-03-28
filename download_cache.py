"""
download_cache.py
Community-scoped JSON caches used by the downloader:
  - downloaded.json       (post IDs already archived)
  - drm_keys.json         (Widevine keys per video_id)
  - video_urls.json       (resolved stream URLs per video_id)
  - n_m3u8dl_commands.log (last N_m3u8DL-RE command per post, for DRM fallback)
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import state
from config import get_folder
from utils import console

# {community: set of post_id strings}
_dl_history_cache: dict = {}
# post IDs for which the "in history" message has been printed once
_printed_history_ids: set = set()


def _dl_history_path() -> Path | None:
    """Resolve path to the download history JSON for the current community."""
    try:
        if not state.COMMUNITY_NAME:
            return None
        cache_dir = get_folder("api_cache", community=state.COMMUNITY_NAME)
        return Path(cache_dir) / "downloaded.json"
    except Exception:
        return None


def _load_dl_history() -> set:
    """Load the download history for the current community into memory."""
    if not state.DOWNLOAD_HISTORY_ENABLED:
        return set()
    comm = state.COMMUNITY_NAME
    if comm and comm in _dl_history_cache:
        return _dl_history_cache[comm]
    p = _dl_history_path()
    if p and p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # Normalise to str so membership matches is_already_downloaded / save_post_text
            # (JSON may contain ints; API sometimes returns postId as int).
            result = set()
            for x in data.get("downloaded", []) or []:
                if x is None:
                    continue
                s = str(x).strip()
                if s:
                    result.add(s)
        except Exception:
            result = set()
    else:
        result = set()
    if comm:
        _dl_history_cache[comm] = result
    return result


def _save_dl_history(history: set) -> None:
    """Persist the download history for the current community to disk."""
    if not state.DOWNLOAD_HISTORY_ENABLED:
        return
    comm = state.COMMUNITY_NAME
    if comm:
        _dl_history_cache[comm] = history
    p = _dl_history_path()
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"downloaded": sorted(history)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def mark_downloaded(post_id: str) -> None:
    """Record a post ID as downloaded in the history cache."""
    if not state.DOWNLOAD_HISTORY_ENABLED or not post_id:
        return
    history = _load_dl_history()
    if str(post_id) in history:
        return
    history.add(str(post_id))
    _save_dl_history(history)


def is_already_downloaded(base_path: str, post_id: str = "") -> bool:
    """
    Returns True if the content has already been downloaded.

    Checks in order:
    1. Download history cache (post_id) — works even if files were moved.
    2. Filesystem scan for base_path + '.*' — original extension-agnostic check.
    """
    if post_id:
        history = _load_dl_history()
        if str(post_id) in history:
            if post_id not in _printed_history_ids:
                _printed_history_ids.add(post_id)
                console.print(f"  [History] Post {post_id} already in download history — skipping.")
            return True
    path_obj = Path(base_path)
    if path_obj.parent.exists():
        for file in path_obj.parent.iterdir():
            if file.name.startswith(path_obj.name + "."):
                return True
    return False


def _drm_keys_path() -> Path | None:
    """Resolve path to the DRM key cache JSON for the current community."""
    try:
        if not state.COMMUNITY_NAME:
            return None
        cache_dir = get_folder("api_cache", community=state.COMMUNITY_NAME)
        return Path(cache_dir) / "drm_keys.json"
    except Exception:
        return None


def _load_drm_keys() -> dict:
    """Load cached DRM keys keyed by video_id."""
    p = _drm_keys_path()
    if p and p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_drm_key(video_id: str, keys: list, infra_id: str = "") -> None:
    """Persist a video_id -> {keys, infra_id} mapping to the DRM key cache."""
    p = _drm_keys_path()
    if p is None:
        return
    try:
        store = _load_drm_keys()
        store[video_id] = {"keys": keys, "infra_id": infra_id}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _video_url_cache_path() -> Path | None:
    """Resolve path to the video URL cache JSON for the current community."""
    try:
        if not state.COMMUNITY_NAME:
            return None
        cache_dir = get_folder("api_cache", community=state.COMMUNITY_NAME)
        return Path(cache_dir) / "video_urls.json"
    except Exception:
        return None


def _load_video_url_cache() -> dict:
    """Load cached video URLs keyed by video_id."""
    p = _video_url_cache_path()
    if p and p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_video_url(video_id: str, url: str) -> None:
    """Persist a video_id -> url mapping to the video URL cache."""
    p = _video_url_cache_path()
    if p is None or not url:
        return
    try:
        store = _load_video_url_cache()
        store[str(video_id)] = url
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _log_n_m3u8dl_command(command: str, post_id: str) -> None:
    """Append an N_m3u8DL-RE command to the community log file."""
    try:
        if not state.COMMUNITY_NAME:
            return
        cache_dir = get_folder("api_cache", community=state.COMMUNITY_NAME)
        log_path = Path(cache_dir) / "n_m3u8dl_commands.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] post_id={post_id}\n{command}\n\n")
    except Exception:
        pass


def _get_logged_command(post_id: str) -> str | None:
    """
    Return the most recent N_m3u8DL-RE command logged for a given post_id,
    or None if no entry exists.
    """
    try:
        if not state.COMMUNITY_NAME:
            return None
        cache_dir = get_folder("api_cache", community=state.COMMUNITY_NAME)
        log_path = Path(cache_dir) / "n_m3u8dl_commands.log"
        if not log_path.exists():
            return None
        text = log_path.read_text(encoding="utf-8")
        blocks = [b.strip() for b in text.strip().split("\n\n") if b.strip()]
        for block in reversed(blocks):
            lines = block.splitlines()
            if not lines:
                continue
            header = lines[0]
            if f"post_id={post_id}" in header and len(lines) >= 2:
                return lines[1]
        return None
    except Exception:
        return None