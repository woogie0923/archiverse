"""
official_media_menu.py
Interactive Rich keyboard UI for browsing Official Media by category.
"""
from __future__ import annotations

import os
import re
import time as _time
from pathlib import Path

import utils
from utils import console
import state
from config import DOWNLOAD_SLEEP, get_folder
from text_writer import embed_url_metadata, media_url
from api import make_extractor, run_extr, fetch_post_details
from helpers import get_author_name, make_filename, sanitise_surrogates
from downloader import (
    is_already_downloaded,
    download_cvideo,
    download_drm_video,
    mark_downloaded,
)
from live import get_key
from rich.table import Table
from rich.text import Text

from menu_rich import cell, clear_menu_screen
from official_media import _embed_thumbnail


def process_official_media_menu():
    """
    Interactive menu for browsing and selecting Official Media by category.
    """

    def _get_pid(p_dict):
        return p_dict.get("postId") or p_dict.get("mediaId") or p_dict.get("id")

    clear_menu_screen()
    console.print(f"\n--- Official Media Menu: {state.COMMUNITY_NAME} ---")

    cat_req  = (
        f"/community/v1.0/community-{state.COMMUNITY_ID}"
        f"/MEDIA_HOME/tabContent?fieldSet=postsV1&fields=categorySummary"
    )
    cats = []
    try:
        cat_resp = run_extr(make_extractor(), cat_req)
        for entry in cat_resp.get("content", {}).get("categorySummary", []):
            cid = entry.get("id") or entry.get("categoryId")
            if cid:
                cats.append({
                    "id":    cid,
                    "title": entry.get("title", f"Category {cid}"),
                    "count": entry.get("postCount", 0),
                    "videoCount": entry.get("videoCount", 0),
                    "photoCount": entry.get("photoCount", 0) or entry.get("imageCount", 0),
                })
    except Exception as e:
        console.print(f"  [!] tabContent fetch failed: {e}")

    if not cats:
        console.print("  Falling back: scanning media feed for categories...", end="", flush=True)
        MAX_SCAN_PAGES = 15
        cats_by_id: dict = {}
        scan_cursor = None
        for _ in range(MAX_SCAN_PAGES):
            req = (
                f"/media/v1.0/community-{state.COMMUNITY_ID}/searchAllMedia?fieldSet=postsV1"
                + (f"&after={scan_cursor.replace(',', '%2C')}" if scan_cursor else "")
            )
            resp = run_extr(make_extractor(), req)
            for p in resp.get("data", []):
                for cat in p.get("extension", {}).get("mediaInfo", {}).get("categories", []):
                    cid = cat.get("id")
                    if cid and cid not in cats_by_id:
                        cats_by_id[cid] = {
                            "id":    cid,
                            "title": cat.get("title", f"Category {cid}"),
                            "count": 0,
                            "videoCount": 0,
                            "photoCount": 0,
                        }
                    if cid:
                        cats_by_id[cid]["count"] += 1
                        if p.get("extension", {}).get("video"):
                            cats_by_id[cid]["videoCount"] += 1
                        phs = p.get("extension", {}).get("image", {}).get("photos", [])
                        if phs:
                            cats_by_id[cid]["photoCount"] += len(phs)
            scan_cursor = resp.get("paging", {}).get("nextParams", {}).get("after")
            console.print(".", end="", flush=True)
            if not scan_cursor:
                break
        console.print()
        cats = list(cats_by_id.values())

    if not state.SKIP_MEMBERSHIP:
        try:
            mem_req = (
                f"/media/v1.0/community-{state.COMMUNITY_ID}/more"
                f"?appId=be4d79eb8fc7bd008ee82c8ec4ff6fd4&fieldSet=postsV1"
                f"&filterType=MEMBERSHIP&language=en&os=WEB&platform=WEB&wpf=pc"
            )
            mem_resp = run_extr(make_extractor(), mem_req, retries=3)
            if mem_resp and mem_resp.get("data"):
                cats.append({
                    "id":             "__MEMBERSHIP__",
                    "title":          "Membership",
                    "count":          0,
                    "videoCount":     0,
                    "photoCount":     0,
                    "_is_membership": True,
                })
        except Exception:
            pass

    if state.SKIP_PUBLIC:
        cats = [c for c in cats if c.get("_is_membership")]

    cat_cursor = 0
    while True:

        CAT_PAGE_SIZE = 15
        while True:
            clear_menu_screen()

            total_cats  = len(cats)
            half        = CAT_PAGE_SIZE // 2
            win_start   = max(0, min(cat_cursor - half, total_cats - CAT_PAGE_SIZE))
            win_end     = min(total_cats, win_start + CAT_PAGE_SIZE)
            visible     = cats[win_start:win_end]

            console.print(
                Text(
                    f"=== {state.COMMUNITY_NAME} — Official Media Categories "
                    f"[{cat_cursor + 1}/{total_cats}] ===",
                    style="bold",
                )
            )
            console.print(
                "  Nav: [↑ ↓] move   [Enter] open category   [B] back to menu   [Q] exit"
            )
            if total_cats > CAT_PAGE_SIZE:
                console.print(f"  Showing {win_start + 1}–{win_end} of {total_cats}")
            console.rule(style="dim")

            cat_table = Table(show_header=False, box=None, pad_edge=False)
            cat_table.add_column(no_wrap=True)
            for idx, cat in enumerate(visible):
                real_i = win_start + idx
                prefix = ">" if real_i == cat_cursor else " "
                row = f"  {prefix}  {cat['title']}"
                cat_table.add_row(cell(row, cursor=(real_i == cat_cursor)))
            console.print(cat_table)
            console.rule(style="dim")

            key = get_key()
            if key == "up":
                cat_cursor = max(0, cat_cursor - 1)
            elif key == "down":
                cat_cursor = min(total_cats - 1, cat_cursor + 1)
            elif key in ("enter", "s"):
                break
            elif key == "b":
                return "back"
            elif key in ("q", "quit"):
                console.print("\nExiting menu.")
                return "quit"

        chosen_cat = cats[cat_cursor]
        clear_menu_screen()
        console.print(f"  Loading: {chosen_cat['title']}...")

        pages:       list = []
        page_thumbs: list = []
        cat_api_cursor    = None

        def _fetch_cat_page(cat, after=None):
            if cat.get("_is_membership"):
                url = (
                    f"/media/v1.0/community-{state.COMMUNITY_ID}/more"
                    f"?appId=be4d79eb8fc7bd008ee82c8ec4ff6fd4&fieldSet=postsV1"
                    f"&filterType=MEMBERSHIP&language=en&os=WEB&platform=WEB&wpf=pc"
                )
                if after:
                    url += f"&after={after.replace(',', '%2C')}"
            else:
                url = f"/media/v1.0/category-{cat['id']}/mediaPosts?fieldSet=postsV1&sortOrder=NEWEST"
                if after:
                    url += f"&after={after.replace(',', '%2C')}"
            return run_extr(make_extractor(), url)

        def _tier_filter(items: list) -> tuple[list, list]:
            """Filter items by membership tier and return (filtered_items, filtered_thumbs)."""
            filtered_items  = []
            filtered_thumbs = []
            for p in items:
                is_mem = p.get("membershipOnly", False)
                if is_mem and state.SKIP_MEMBERSHIP:
                    continue
                if not is_mem and state.SKIP_PUBLIC:
                    continue
                filtered_items.append(p)
                filtered_thumbs.append(
                    (p.get("summary", {}).get("thumbnails") or [{}])[0].get("url", "")
                )
            return filtered_items, filtered_thumbs

        first = _fetch_cat_page(chosen_cat)
        first_items, first_thumbs = _tier_filter(first.get("data", []))
        pages.append(first_items)
        page_thumbs.append(first_thumbs)
        cat_api_cursor = first.get("paging", {}).get("nextParams", {}).get("after")

        if not pages[0]:
            console.print("  No posts found in this category.")
            _time.sleep(1)
            continue

        current_page = 0
        selected:    set = set()
        row_cursor       = 0
        go_back          = False

        def _render_posts(items, page_idx, sel, rcur, _cat=chosen_cat):
            clear_menu_screen()
            console.print(
                Text(
                    f"=== {state.COMMUNITY_NAME} — {_cat['title']}  [page {page_idx + 1}] ===",
                    style="bold",
                )
            )
            console.print(f"  Selected: {len(sel)} item(s)")
            console.print(
                "  Nav: [↑ ↓] move   [← →] page   [Space] toggle   [A] all"
            )
            console.print(
                "  Action:  [S/Enter] download   [B] back to categories   [Q] back to menu"
            )
            console.rule(style="dim")

            post_table = Table(
                show_header=True,
                header_style="bold",
                box=None,
                pad_edge=False,
                show_lines=False,
            )
            post_table.add_column("", width=5, no_wrap=True)
            post_table.add_column("Date", min_width=14, no_wrap=True)
            post_table.add_column("Tier", min_width=14, no_wrap=True)
            post_table.add_column("Type", min_width=10, no_wrap=True)
            post_table.add_column("Title", min_width=30, overflow="ellipsis", no_wrap=True)

            for i, p in enumerate(items):
                date = str(
                    utils.timestamp(p.get("publishedAt", 0) or p.get("createdAt", 0))
                )[:10]
                title = (p.get("title") or "No Title")[:60]
                pid = _get_pid(p)
                check = "[X]" if pid in sel else "[ ]"
                mem = "Membership" if p.get("membershipOnly") else "Public"
                summary = p.get("summary", {})
                ext_b = p.get("extension", {})
                has_vid = summary.get("videoCount", 0) > 0 or bool(ext_b.get("video"))
                has_img = summary.get("photoCount", 0) > 0 or bool(ext_b.get("image"))
                if has_vid and has_img:
                    mtype = "Vid+Img"
                elif has_vid:
                    mtype = "Video"
                elif has_img:
                    mtype = "Image"
                else:
                    mtype = ""
                sel_row = i == rcur
                check_disp = f"  {check}"
                post_table.add_row(
                    cell(check_disp, cursor=sel_row),
                    cell(date, cursor=sel_row),
                    cell(mem, cursor=sel_row),
                    cell(mtype, cursor=sel_row),
                    cell(title, cursor=sel_row),
                )
            console.print(post_table)
            console.rule(style="dim")

        while True:
            items = pages[current_page]
            row_cursor = max(0, min(row_cursor, len(items) - 1))
            thumbs     = page_thumbs[current_page]
            _render_posts(items, current_page, selected, row_cursor)

            key = get_key()
            if key == "up":
                row_cursor = max(0, row_cursor - 1)
            elif key == "down":
                row_cursor = min(len(items) - 1, row_cursor + 1)
            elif key == "space" and items:
                pid = _get_pid(items[row_cursor])
                if pid:
                    if pid in selected: selected.remove(pid)
                    else: selected.add(pid)
            elif key == "right":
                if current_page == len(pages) - 1 and cat_api_cursor:
                    nxt = _fetch_cat_page(chosen_cat, cat_api_cursor)
                    nxt_items, nxt_thumbs = _tier_filter(nxt.get("data", []))
                    pages.append(nxt_items)
                    page_thumbs.append(nxt_thumbs)
                    cat_api_cursor = nxt.get("paging", {}).get("nextParams", {}).get("after")
                    current_page += 1
                    row_cursor = 0
                elif current_page < len(pages) - 1:
                    current_page += 1
                    row_cursor = 0
            elif key == "left" and current_page > 0:
                current_page -= 1
                row_cursor = 0
            elif key == "a":
                for p in items:
                    pid = _get_pid(p)
                    if pid: selected.add(pid)
            elif key == "b":
                go_back = True
                break
            elif key in ("s", "enter"):
                if not selected:
                    console.print("\n  Nothing selected.")
                    _time.sleep(1)
                else:
                    break
            elif key in ("q", "quit"):
                return "back"

        if go_back:
            continue
        else:
            break

    console.print(f"\nDownloading {len(selected)} selected items from '{chosen_cat['title']}'...")

    lookup = {}
    for pg, th_list in zip(pages, page_thumbs):
        for item, thumb in zip(pg, th_list):
            pid = _get_pid(item)
            if pid:
                item["postId"] = pid
                lookup[pid] = (item, thumb)

    def _clean_title(raw):
        return " ".join(re.sub(r'[<>:"/\\|?*]', "-", raw).split())[:80]

    for pid in selected:
        if pid not in lookup:
            continue
        summary, thumb_url = lookup[pid]

        is_mem = summary.get("membershipOnly", False)
        if is_mem and state.SKIP_MEMBERSHIP:
            console.print(f"  [Skip] {pid} is Membership-only.")
            continue
        if not is_mem and state.SKIP_PUBLIC:
            console.print(f"  [Skip] {pid} is Public (--skip-public set).")
            continue

        tier    = "Membership" if is_mem else "Public"
        m_dir   = get_folder("official_media", community=state.COMMUNITY_NAME, tier=tier)
        os.makedirs(m_dir, exist_ok=True)

        full_p = fetch_post_details(summary)
        if not full_p:
            full_p = summary

        date        = utils.timestamp(full_p.get("publishedAt") or summary.get("publishedAt") or 0)
        title       = (full_p.get("title") or summary.get("title") or "No Title").strip()
        clean_t     = _clean_title(title) or "No Title"
        raw_t       = sanitise_surrogates(title)
        ext_b       = full_p.get("extension", {}) or summary.get("extension", {})
        author_name = get_author_name(full_p.get("author", {}) or summary.get("author", {}))
        _media_url  = media_url(state.COMMUNITY_NAME, pid)

        found_any = False

        if state.DOWNLOAD_TYPE != "video":
            if v := ext_b.get("video"):
                if vid := v.get("videoId"):
                    path = f"{m_dir}/{make_filename(author_name, date, f'{pid}_{vid}', title=clean_t, template_key='official_media', tier=tier)}"
                    if not is_already_downloaded(path, post_id=pid):
                        console.print(f"  [Video] {title}")
                        if is_mem or v.get("membershipOnly"):
                            download_drm_video(pid, path, thumb_url=thumb_url, weverse_url=_media_url, title=raw_t)
                        else:
                            download_cvideo(vid, path, date)
                            _embed_thumbnail(path, thumb_url, url_meta=_media_url, title=raw_t)
                            _vf2 = next((f for f in Path(path).parent.iterdir() if f.name.startswith(Path(path).name + ".") and f.suffix.lower() in (".mkv", ".mp4")), None)
                            if _vf2: utils.edit_creation_date(str(_vf2), date)
                        _time.sleep(DOWNLOAD_SLEEP)
                        found_any = True

        if state.DOWNLOAD_TYPE != "video":
            if phs := ext_b.get("image", {}).get("photos"):
                for idx, ph in enumerate(phs):
                    photo_id = ph["photoId"]
                    path = f"{m_dir}/{make_filename(author_name, date, f'{pid}_{photo_id}_{idx+1}', title=clean_t, template_key='official_media', tier=tier)}"
                    if not is_already_downloaded(path, post_id=pid):
                        console.print(f"  [Photo] {title} ({idx+1}/{len(phs)})")
                        utils.download_file(ph["url"], path, date)
                        embed_url_metadata(path, _media_url)
                        found_any = True

        if not found_any:
            console.print(f"  [Skip] {pid} contained no downloadable video or photos.")
        else:
            mark_downloaded(pid)

    console.print("\nDone.")
