"""
live.py
Interactive live stream menu, keyboard input handler, and live download logic.
"""
import time
from pathlib import Path

from . import utils
from .utils import console
from . import state
from .config import BINARIES
from .api import fetch_lives_page
from .helpers import (
    fix_metadata, get_author_name, get_filtered_items, 
    make_filename, sanitise
)
from .download_cache import invalidate_video_url_cache_entry
from .downloader import (
    get_vod_url,
    download_drm_video,
    is_already_downloaded,
    mark_downloaded,
)

from .terminal_input import get_key


def live_vod_thumbnail_url(item_data: dict, vod_playinfo_thumb: str = "") -> str:
    """
    Thumbnail URL for a live VOD, matching liveTabPosts / postsV1 fieldSet.

    Priority:
      1. extension.mediaInfo.thumbnail.url  (upload / API thumbnail)
      2. extension.video.thumb
      3. summary.thumbnails[0].url
      4. vod_playinfo_thumb from get_vod_url() XML (last resort)
    """
    if not item_data:
        return vod_playinfo_thumb or ""

    ext = item_data.get("extension") or {}
    media_info = ext.get("mediaInfo") or {}
    tn = media_info.get("thumbnail")
    if isinstance(tn, dict):
        u = (tn.get("url") or "").strip()
        if u:
            return u

    video = ext.get("video") or {}
    u = (video.get("thumb") or "").strip()
    if u:
        return u

    for t in item_data.get("summary", {}).get("thumbnails", []) or []:
        if isinstance(t, dict):
            u = (t.get("url") or "").strip()
            if u:
                return u

    return (vod_playinfo_thumb or "").strip()


def render_lives_menu(lives: list, page_idx: int, selected_ids: set, cursor_pos: int):
    """Clear the screen and repaint the full selection menu."""
    from rich.table import Table
    from rich.text import Text

    from .menu_rich import cell, clear_menu_screen

    clear_menu_screen()
    console.print(
        Text(
            f"=== {state.COMMUNITY_NAME} Live Stream Archive  [page {page_idx + 1}] ===",
            style="bold",
        )
    )
    console.print(f"  Selected : {len(selected_ids)} item(s)")
    console.print("  Nav      : [↑ ↓] move   [← →] page   [Space] toggle   [A] select all on page")
    console.print("  Action   : [S / Enter] start download   [B] back to menu   [Q / Esc] exit")
    console.rule(style="dim")

    if not lives:
        console.print("  (no items on this page)")
    else:
        table = Table(
            show_header=True,
            header_style="bold",
            box=None,
            pad_edge=False,
            show_lines=False,
        )
        # Checkbox must not shrink below "[ ]"/"[X]" or Rich ellipsizes ("[...").
        table.add_column("", min_width=4, no_wrap=True, overflow="crop")
        table.add_column("Date", min_width=12, no_wrap=True)
        table.add_column("Duration", min_width=10, no_wrap=True)
        table.add_column("Tier", min_width=11, no_wrap=True)
        table.add_column("Artist", min_width=18, no_wrap=True)
        table.add_column("Post ID", min_width=14, no_wrap=True)
        table.add_column("Title", min_width=28, overflow="ellipsis", no_wrap=True)

        for idx, item in enumerate(lives):
            meta    = fix_metadata(item)
            sel     = idx == cursor_pos
            check   = "[X]" if meta["id"] in selected_ids else "[ ]"
            memb    = "Membership" if meta["is_membership"] else "Public"
            artist  = meta["artist"][:20]
            post_id = meta["id"] or "N/A"
            title   = meta["title"][:48]
            dur     = meta.get("duration_str") or "—"
            table.add_row(
                cell(check, cursor=sel),
                cell(meta["date_str"], cursor=sel),
                cell(dur, cursor=sel),
                cell(memb, cursor=sel),
                cell(artist, cursor=sel),
                cell(post_id, cursor=sel),
                cell(title, cursor=sel),
            )
        console.print(table)

    console.rule(style="dim")



def process_lives(direct_id=None, debug=False):
    if state.DOWNLOAD_TYPE == "photo":
        console.print("\n[Skip] Skipping Lives because --type is set to 'photo'.")
        return

    from .menu_rich import clear_menu_screen

    clear_menu_screen()
    console.print(f"\n--- Live Stream Archive: {state.COMMUNITY_NAME} ---")

    if direct_id is not None:
        console.print(f"  [Direct Mode] Searching for ID: {direct_id}...")
        cursor = None
        found  = False

        while not found:
            resp  = fetch_lives_page(cursor)
            items = resp.get("data", [])

            for item in items:
                if item.get("postId") == direct_id:
                    console.print(f"  [Found!] Initiating download for {direct_id}...")
                    # Back-compat: some older copies of this script had a
                    # download_single_live(item_data, post_id) signature.
                    try:
                        download_single_live(item)
                    except TypeError:
                        download_single_live(item, direct_id)
                    found = True
                    break

            if found:
                return

            cursor = resp.get("paging", {}).get("nextParams", {}).get("after")
            if not cursor:
                console.print(f"  [Error] Could not find ID {direct_id} in this community.")
                return
            console.print("  Checking next page of results...")

    pages: list[list] = []
    console.print("Connecting to Weverse...")

    first_page_resp  = fetch_lives_page(None)
    first_page_items = get_filtered_items(first_page_resp)

    if not first_page_items:
        console.print("  -> No live streams found matching your criteria.")
        return

    pages.append(first_page_items)
    current_page_idx = 0
    selected_ids: set = set()
    cursor_pos   = 0
    last_cursor  = (
        first_page_resp.get("paging", {}).get("nextParams", {}).get("after")
    )

    while True:
        current_items = pages[current_page_idx]
        cursor_pos    = max(0, min(cursor_pos, len(current_items) - 1))
        render_lives_menu(current_items, current_page_idx, selected_ids, cursor_pos)

        key = get_key()

        if key == "up":
            cursor_pos = max(0, cursor_pos - 1)
        elif key == "down":
            cursor_pos = min(len(current_items) - 1, cursor_pos + 1)
        elif key == "space" and current_items:
            item_id = current_items[cursor_pos].get("postId")
            if item_id in selected_ids:
                selected_ids.remove(item_id)
            else:
                selected_ids.add(item_id)
        elif key == "right":
            if current_page_idx == len(pages) - 1:
                if last_cursor:
                    next_resp  = fetch_lives_page(last_cursor)
                    next_items = get_filtered_items(next_resp)
                    if next_items:
                        pages.append(next_items)
                        last_cursor      = next_resp.get("paging", {}).get("nextParams", {}).get("after")
                        current_page_idx += 1
                        cursor_pos       = 0
                else:
                    console.print("\n  No more pages available.")
                    time.sleep(0.7)
            else:
                current_page_idx += 1
                cursor_pos = 0
        elif key == "left" and current_page_idx > 0:
            current_page_idx -= 1
            cursor_pos = 0
        elif key == "a":
            for item in current_items:
                selected_ids.add(item.get("postId"))
        elif key in ("s", "enter"):
            if not selected_ids:
                console.print("\n  Nothing selected! Use [Space] to select items.")
                time.sleep(1)
            else:
                break
        elif key == "b":
            return "back"
        elif key in ("q", "quit"):
            console.print("\nExiting menu.")
            return "quit"

    console.print(f"\nPreparing to download {len(selected_ids)} selected items...")
    lookup: dict = {}
    for page in pages:
        for item in page:
            lookup[item.get("postId")] = item

    for item_id in selected_ids:
        item_data = lookup.get(item_id)
        if item_data:
            try:
                download_single_live(item_data)
            except TypeError:
                download_single_live(item_data, item_id)
        else:
            console.print(f"  [Error] Metadata for {item_id} lost. Skipping.")

    console.print("\nBatch download complete.")


def download_single_live(item_data: dict, post_id: str | None = None):
    """Process a single live stream item with artist-specific subfolders."""
    
    post_id = post_id or item_data.get("postId")
    if not post_id:
        return

    from .text_writer import live_url
    _live_url = live_url(state.COMMUNITY_NAME, post_id)

    video_id = item_data.get("extension", {}).get("video", {}).get("videoId")
    if not video_id:
        return

    artist_name       = get_author_name(item_data.get("author", {}))
    clean_artist_name = sanitise(artist_name)
    meta              = fix_metadata(item_data)
    is_mem            = item_data.get("membershipOnly", False)

    if is_mem and state.SKIP_MEMBERSHIP:
        console.print(f"  [Skip] Live {post_id} is Membership-only.")
        return

    if not is_mem and state.SKIP_PUBLIC:
        console.print(f"  [Skip] Live {post_id} is Public content (--skip-public set).")
        return

    filename = make_filename(
        artist_name,
        meta["date"],
        post_id,
        title=meta.get("title", ""),
        template_key="lives",
        tier="Membership" if is_mem else "Public",
    )

    # Safety net: ensure the on-disk name can't contain line separators or other
    # problematic characters even if a different helpers.py is being imported at runtime.
    def _safe_fs_stem(name: str) -> str:
        import re as _re
        s = "" if name is None else str(name)
        # Collapse ALL whitespace (includes \r, \n, \t, and unicode separators)
        s = " ".join(s.split())
        # Remove ASCII control chars if any remain
        s = _re.sub(r"[\x00-\x1f]+", " ", s)
        # Replace Windows-illegal filename characters
        s = _re.sub(r'[<>:"/\\|?*]+', "-", s)
        s = " ".join(s.split()).strip().strip(".")
        return s or f"live_{post_id}"

    safe_filename = _safe_fs_stem(filename)

    tier      = "Membership" if is_mem else "Public"
    from .config import get_folder
    lives_dir = Path(
        get_folder(
            "lives",
            community=state.COMMUNITY_NAME,
            tier=tier,
            artist=clean_artist_name,
        )
    )
    lives_dir.mkdir(parents=True, exist_ok=True)
    # Use a Windows-safe filename for everything on disk.
    save_path = lives_dir / safe_filename

    if meta["is_membership"]:
        if is_already_downloaded(str(save_path), post_id=post_id):
            console.print(f"  -> Already exists (DRM), skipping: {filename}")
            return

        drm_thumb = live_vod_thumbnail_url(item_data)
        try:
            download_drm_video(
                post_id,
                str(save_path),
                thumb_url=drm_thumb,
                weverse_url=_live_url,
                title=meta.get("title", ""),
                created_at=meta.get("date"),
            )
        except Exception as e:
            console.print(f"  [Error] DRM live download failed for {post_id}: {e}")
            return

        drm_ok = any(
            p.is_file()
            and p.stem == safe_filename
            and p.suffix.lower() in (".mkv", ".mp4")
            for p in lives_dir.iterdir()
        )
        if drm_ok:
            mark_downloaded(post_id)

        if state.SAVE_TEXT:
            from .text_writer import save_live_chat, save_live_artist_chat
            save_live_chat(post_id, str(lives_dir), safe_filename)
            save_live_artist_chat(post_id, str(lives_dir), safe_filename)
        return

    final_path = lives_dir / f"{safe_filename}.mkv"
    if final_path.exists():
        console.print(f"  -> Already exists, skipping: {final_path.name}")
        return
    if is_already_downloaded(str(lives_dir / safe_filename), post_id=post_id):
        console.print(f"  -> Already in download history, skipping: {filename}")
        return

    try:
        video_url, subs, vod_thumb_url = get_vod_url(video_id)
        if not video_url:
            return

        console.print(f"  -> Downloading Standard VOD: {filename}")
        # Use an ASCII-safe temp base so we can always locate/cleanup outputs.
        import uuid as _uuid
        temp_id           = f"wv_live_{post_id}_{_uuid.uuid4().hex[:8]}"
        temp_video_base   = lives_dir / f"{temp_id}_temp"
        expected_video    = lives_dir / f"{temp_id}_temp.mp4"

        ok_video = utils.download_file(video_url, str(temp_video_base), meta["date"])
        if not ok_video:
            # Cached MP4 URLs use time-limited Akamai signatures; refetch playInfo once.
            invalidate_video_url_cache_entry(str(video_id))
            console.print(
                "  [VOD URL] Stream URL likely expired; refreshing playInfo and retrying video download…"
            )
            video_url, subs, vod_thumb_url = get_vod_url(video_id, force_refresh=True)
            if not video_url:
                console.print("  [Error] Could not resolve VOD URL after refresh.")
                return
            ok_video = utils.download_file(video_url, str(temp_video_base), meta["date"])
            if not ok_video:
                console.print("  [Error] VOD video download failed after refreshing playInfo.")
                return

        actual_video = expected_video
        try:
            # Iterate directory to find the file literally (avoids glob bracket bugs)
            for f in lives_dir.iterdir():
                if f.name.startswith(temp_video_base.name) and f.suffix == ".mp4":
                    actual_video = f
                    break
        except Exception as e:
            console.print(f"  [Discovery Error] {e}")

        if not actual_video.exists():
            console.print("  [Error] VOD MP4 missing after download; aborting.")
            return

        sub_files: list[tuple[str, str]] = []
        for s in subs:
            lang = s.get("lang", "und")
            sub_stem = lives_dir / f"{temp_id}_{lang}"
            utils.download_file(s["url"], str(sub_stem))

            # download_file decides extension from Content-Type; find what we got.
            sub_matches = [
                f for f in lives_dir.iterdir()
                if f.name.startswith(sub_stem.name + ".")
            ]
            if not sub_matches:
                continue
            actual_sub = sub_matches[0]

            # Normalise to .vtt when possible (some servers return text/plain -> .txt)
            if actual_sub.suffix.lower() != ".vtt":
                desired = actual_sub.with_suffix(".vtt")
                try:
                    if desired.exists():
                        desired.unlink()
                    actual_sub.rename(desired)
                    actual_sub = desired
                except Exception:
                    pass

            sub_files.append((str(actual_sub), lang))

        thumb_url = live_vod_thumbnail_url(item_data, vod_playinfo_thumb=vod_thumb_url)
        thumb_path = None
        if thumb_url:
            thumb_stem = lives_dir / f"{temp_id}_thumb"
            utils.download_file(thumb_url, str(thumb_stem))
            thumb_matches = [
                f for f in lives_dir.iterdir()
                if f.name.startswith(thumb_stem.name + ".")
            ]
            if thumb_matches:
                thumb_path = thumb_matches[0]

        from .text_writer import live_url
        weverse_url = live_url(state.COMMUNITY_NAME, post_id)
        live_title  = meta.get("title", "")

        ffmpeg = BINARIES.get("ffmpeg", "ffmpeg")
        cmd    = [ffmpeg, "-y", "-i", str(actual_video)]

        for sub_p, _ in sub_files:
            cmd.extend(["-i", sub_p])

        cmd.extend(["-map", "0:v", "-map", "0:a?"])

        for i, (_, lang) in enumerate(sub_files):
            # Each .vtt input is its own file, with stream index 0.
            cmd.extend(["-map", f"{i+1}:0"])
            short_lang = lang.split("_")[0] if "_" in lang else lang
            cmd.extend([f"-metadata:s:s:{i}", f"language={short_lang}"])

        if thumb_path and thumb_path.exists():
            cmd.extend([
                "-attach", str(thumb_path),
                "-metadata:s:t", "mimetype=image/jpeg",
                "-metadata:s:t", "filename=cover.jpg",
            ])

        if weverse_url:
            cmd.extend(["-metadata", f"comment={weverse_url}"])
        if live_title:
            cmd.extend(["-metadata", f"title={live_title}"])

        cmd.extend(["-c", "copy", str(final_path)])

        rc, mux_err = utils.run_ffmpeg_with_progress(
            cmd,
            duration_source=Path(actual_video),
            description=f"Muxing {final_path.name}",
        )

        if rc == 0:
            try:
                if actual_video.exists():
                    actual_video.unlink()
            except FileNotFoundError:
                pass
            for sub_p, _ in sub_files:
                try:
                    Path(sub_p).unlink()
                except FileNotFoundError:
                    pass
                except Exception:
                    pass
            try:
                if thumb_path and thumb_path.exists():
                    thumb_path.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
            try:
                _live_date = meta["date"]
                utils.edit_creation_date(str(final_path), _live_date)
            except Exception:
                pass
            if state.SAVE_TEXT:
                from .text_writer import save_live_chat, save_live_artist_chat
                save_live_chat(post_id, str(lives_dir), safe_filename)
                save_live_artist_chat(post_id, str(lives_dir), safe_filename)
            if final_path.exists():
                mark_downloaded(post_id)
        else:
            _detail = (mux_err or "").strip()[:800]
            console.print(
                f"  [Error] FFmpeg failed for {post_id}: {_detail or '(no stderr)'}"
            )

    except Exception as e:
        console.print(f"  [Error] Failed processing live {post_id}: {e}")