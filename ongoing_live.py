"""
ongoing_live.py

Monitor and record ongoing (on-air) Weverse livestreams using HLS + N_m3u8DL-RE.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import state
from api import fetch_onair_lives
from downloader import (
    get_live_hls_url,
    is_already_downloaded,
    mark_downloaded,
    record_ongoing_live_streamlink,
    download_ongoing_live_subtitles_nm3u8dlre,
)
from helpers import get_author_name, make_filename, sanitise
from utils import console, edit_creation_date


def _parse_published_at(published_at) -> datetime | None:
    """
    Parse publishedAt values from liveTab into a datetime.

    Weverse formats vary; we support:
      - integer/float epoch millis
      - numeric strings (epoch millis)
      - ISO-8601 strings
    """
    if published_at is None:
        return None

    tz_name = None
    try:
        from config import TIMEZONE
        tz_name = TIMEZONE
    except Exception:
        tz_name = "Asia/Seoul"

    try:
        tz = ZoneInfo(tz_name or "Asia/Seoul")
    except Exception:
        tz = ZoneInfo("Asia/Seoul")

    try:
        if isinstance(published_at, (int, float)):
            # Heuristic: values > 10^10 are almost certainly millis.
            ts = float(published_at)
            if ts > 10_000_000_000:
                return datetime.fromtimestamp(ts / 1000.0, tz=tz)
            return datetime.fromtimestamp(ts, tz=tz)
    except Exception:
        pass

    s = str(published_at).strip()
    if s.isdigit():
        ts = int(s)
        # If it looks like epoch millis, convert.
        if ts > 10_000_000_000:
            return datetime.fromtimestamp(ts / 1000.0, tz=tz)
        return datetime.fromtimestamp(ts, tz=tz)

    # ISO strings
    try:
        # Handle trailing Z
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except Exception:
        return None


def _extract_live_info(item: dict) -> dict | None:
    """
    Extract the fields we need from an onAirLivePosts item.
    """
    if not item:
        return None

    post_id = item.get("postId")
    title = item.get("title") or ""
    published_at = item.get("publishedAt")
    share_url = item.get("shareUrl") or ""
    is_membership = bool(item.get("membershipOnly", False))

    ext_video = (item.get("extension") or {}).get("video") or {}
    video_id = ext_video.get("videoId") or item.get("videoId")
    is_membership = is_membership or bool(ext_video.get("membershipOnly", False))

    if not post_id and video_id:
        # Still allow recording, but dedupe will use videoId as a fallback.
        post_id = video_id

    if not video_id:
        return None

    return {
        "post_id": str(post_id) if post_id else "",
        "video_id": str(video_id),
        "title": str(title),
        "published_at": published_at,
        "share_url": str(share_url),
        "is_membership": is_membership,
        "author": item.get("author") or {},
    }


def _compute_output(file_info: dict) -> tuple[Path, str]:
    """
    Compute lives output directory and a filename stem.
    """
    from config import get_folder

    artist_name = get_author_name(file_info.get("author", {}))
    clean_artist = sanitise(artist_name)

    published_dt = _parse_published_at(file_info.get("published_at")) or datetime.now()

    is_mem = bool(file_info.get("is_membership", False))
    tier = "Membership" if is_mem else "Public"

    # Use make_filename so folder+file naming matches the rest of the project.
    stem = make_filename(
        artist=artist_name,
        date=published_dt,
        post_id=file_info["post_id"],
        title=file_info.get("title", ""),
        template_key="lives",
        tier=tier,
    )

    # Ensure it can't contain line separators or other problematic whitespace
    stem = " ".join(stem.split()).strip()

    out_dir = Path(
        get_folder(
            "lives",
            community=state.COMMUNITY_NAME,
            tier=tier,
            artist=clean_artist,
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir, stem


def _record_one(file_info: dict, poll_conf: dict):
    """
    Record one specific live post:
      - Streamlink for video (mkv)
      - N_m3u8DL-RE for subtitles
    """
    from text_writer import embed_url_metadata, live_url

    post_id = file_info["post_id"]
    video_id = file_info["video_id"]

    # Tier gating (membership/public).
    if file_info.get("is_membership") and state.SKIP_MEMBERSHIP:
        console.print(f"  [Skip] Membership live {post_id}")
        return
    if (not file_info.get("is_membership")) and state.SKIP_PUBLIC:
        console.print(f"  [Skip] Public live {post_id}")
        return

    out_dir, stem = _compute_output(file_info)

    # Deduping using existing download history.
    if is_already_downloaded(str(out_dir / stem), post_id):
        return

    hls_url, is_drm_like = get_live_hls_url(video_id)
    if not hls_url:
        console.print(f"  [Live Record] No HLS URL for {post_id}")
        return

    output_path = out_dir / f"{stem}.mkv"
    output_file = record_ongoing_live_streamlink(hls_url=hls_url, output_path=output_path)
    if not output_file or not output_file.exists():
        return

    # Subtitles (download separately; does not mux automatically here)
    download_ongoing_live_subtitles_nm3u8dlre(
        hls_url=hls_url,
        output_dir=out_dir,
        save_name=stem,
        subtitle_langs=poll_conf["subtitle_langs"].replace("eng", "eng").replace("kor", "kor"),
        live_take_count=poll_conf["subs_live_take_count"],
        live_wait_time=poll_conf["subs_live_wait_time"],
    )

    # Set/overwrite created timestamp (belt-and-suspenders).
    try:
        created_dt = _parse_published_at(file_info.get("published_at"))
        if created_dt:
            edit_creation_date(str(output_file), created_dt)
    except Exception:
        pass

    # Embed URL metadata if we have it.
    try:
        weverse_url = file_info.get("share_url") or live_url(state.COMMUNITY_NAME, post_id)
        if weverse_url:
            embed_url_metadata(str(output_file), weverse_url, title=file_info.get("title", ""))
    except Exception:
        pass

    # Mark recorded so we don't start again on next poll.
    mark_downloaded(post_id)


def process_ongoing_lives(
    direct_match: str | None = None,
    poll_seconds: int = 30,
    record_all: bool = False,
    live_wait_time: int = 5,
    subtitle_langs: str = "eng|kor",
    output_format: str = "mp4",
):
    """
    Monitor ongoing lives and record them until the user stops the program.
    """
    active: set[str] = set()
    lock = threading.Lock()

    # For ongoing lives: no chat saving (explicit requirement).
    poll_conf = {
        "live_wait_time": live_wait_time,
        "subtitle_langs": subtitle_langs,
        "output_format": output_format,
        "subs_live_take_count": 500,
        "subs_live_wait_time": 4,
    }

    ended: list[str] = []

    def _record_candidates_sync(items: list[dict]):
        newest = sorted(items, key=lambda it: str(it.get("publishedAt") or "0"))
        if not newest:
            return
        to_run = newest if record_all else [newest[-1]]
        for item in to_run:
            info = _extract_live_info(item)
            if not info:
                continue
            post_id = info.get("post_id") or ""
            if not post_id:
                continue
            _record_one(info, poll_conf)

    def _spawn_for_candidates(items: list[dict]):
        newest = sorted(
            items,
            key=lambda it: str(it.get("publishedAt") or "0"),
        )
        if not newest:
            return

        if record_all:
            to_start = newest
        else:
            to_start = [newest[-1]]

        for item in to_start:
            info = _extract_live_info(item)
            if not info:
                continue
            post_id = info["post_id"]
            if not post_id:
                continue

            with lock:
                if post_id in active:
                    continue
                active.add(post_id)

            def _runner():
                try:
                    _record_one(info, poll_conf)
                finally:
                    with lock:
                        active.discard(post_id)
                    ended.append(post_id)

            threading.Thread(target=_runner, daemon=True).start()

    def _match_items(items: list[dict], match_val: str) -> list[dict]:
        mv = str(match_val)
        matched = []
        for it in items:
            if not isinstance(it, dict):
                continue
            info = _extract_live_info(it)
            if not info:
                continue
            if mv in (
                info.get("post_id", ""),
                info.get("video_id", ""),
                info.get("share_url", ""),
            ):
                matched.append(it)
        return matched

    if direct_match:
        resp = fetch_onair_lives()
        items = resp.get("onAirLivePosts", {}).get("data", []) if isinstance(resp, dict) else []
        if direct_match == "__LATEST__":
            # Record the newest currently on-air live.
            _record_candidates_sync(items)
            return

        matches = _match_items(items, direct_match)
        if not matches:
            console.print(f"  [Ongoing Live] No on-air live found matching: {direct_match}")
            return
        _record_candidates_sync(matches)
        return

    console.print(f"  [Ongoing Live] Monitoring on-air livestreams (poll={poll_seconds}s)...")
    while True:
        resp = fetch_onair_lives()
        items = resp.get("onAirLivePosts", {}).get("data", []) if isinstance(resp, dict) else []
        if items:
            _spawn_for_candidates(items)
        # If a live ended and we are idle, ask user what to do.
        if ended:
            with lock:
                is_idle = (len(active) == 0)
            if is_idle:
                ended_ids = ", ".join(ended[-3:])
                ended.clear()
                ans = console.input(
                    f"\n  [Ongoing Live] Recording finished ({ended_ids}). "
                    "Keep monitoring? [Y/n]: "
                ).strip().lower()
                if ans in ("n", "no", "q", "quit", "exit"):
                    return
        time.sleep(poll_seconds)

