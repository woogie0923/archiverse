"""
official_media.py
Official Media tab crawl (searchAllMedia) and thumbnail embedding for public videos.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from . import utils
from .utils import console
from . import state
from .config import PAGED_SLEEP, get_folder
from .text_writer import save_post_text, embed_url_metadata, media_url
from .api import make_extractor, run_extr, fetch_post_details
from .helpers import get_author_name, make_filename, sanitise_surrogates
from .downloader import (
    is_already_downloaded,
    download_cvideo,
    download_drm_video,
    mark_downloaded,
)


def _embed_thumbnail(video_path_stem: str, thumb_url: str, url_meta: str = "", title: str = ""):
    """
    Download a thumbnail and embed it into the video file.
    Also handles embedding the URL metadata and title to guarantee it happens post-download.
    """
    from pathlib import Path as _Path
    import subprocess as _sp
    from .config import BINARIES as _BINS
    from . import utils as _utils

    stem = _Path(video_path_stem)
    matches = [
        f for f in stem.parent.iterdir()
        if f.name.startswith(stem.name + ".")
    ]
    if not matches:
        return
    video_file = matches[0]

    if video_file.suffix.lower() not in (".mp4", ".mkv"):
        return

    if url_meta or title:
        embed_url_metadata(str(video_file), url_meta, title=title)

    if not thumb_url:
        return

    thumb_stem = stem.parent / f"{stem.name}_cover"
    _utils.download_file(thumb_url, str(thumb_stem))
    thumb_matches = [
        f for f in stem.parent.iterdir()
        if f.name.startswith(thumb_stem.name + ".")
    ]
    if not thumb_matches:
        return
    thumb_path = thumb_matches[0]

    try:
        if video_file.suffix.lower() == ".mkv":
            mkvpropedit = _BINS.get("mkvpropedit", "mkvpropedit")
            cmd = [
                mkvpropedit, str(video_file),
                "--attachment-mime-type", "image/jpeg",
                "--attachment-name", "cover.jpg",
                "--add-attachment", str(thumb_path),
            ]
            result = _sp.run(cmd, capture_output=True)
            if result.returncode == 0:
                console.print(f"  [Thumbnail] Embedded into {video_file.name}")
            else:
                console.print(f"  [Thumbnail] mkvpropedit failed: {result.stderr.decode()[:200]}")
        else:
            ffmpeg   = _BINS.get("ffmpeg", "ffmpeg")
            out_file = stem.parent / f"{stem.name}_thumb{video_file.suffix}"
            cmd = [
                ffmpeg, "-y",
                "-i", str(video_file),
                "-i", str(thumb_path),
                "-map", "0", "-map", "1",
                "-c", "copy",
                "-disposition:v:1", "attached_pic",
                str(out_file),
            ]

            cf = 0x08000000 if hasattr(_sp, 'CREATE_NO_WINDOW') else 0
            result = _sp.run(cmd, capture_output=True, creationflags=cf)

            if result.returncode == 0:
                video_file.unlink()
                out_file.rename(video_file)
                console.print(f"  [Thumbnail] Embedded into {video_file.name}")
            else:
                console.print(f"  [Thumbnail] ffmpeg failed: {result.stderr.decode()[:200]}")
    finally:
        if thumb_path.exists():
            thumb_path.unlink()


def process_official_media(direct_id=None):
    """
    Archive content from the Official Media tab.
    Pass direct_id to process a single specific post.
    """

    def _build_media_dir(is_mem: bool) -> str:
        tier = "Membership" if is_mem else "Public"
        folder = get_folder(
            "official_media",
            community=state.COMMUNITY_NAME,
            tier=tier,
        )
        os.makedirs(folder, exist_ok=True)
        return folder

    def _clean_title(raw: str) -> str:
        return " ".join(re.sub(r'[<>:"/\\|?*]', "-", raw).split())[:80]

    def _process_post(p: dict, media_dir: str, thumb_url: str = ""):
        date        = utils.timestamp(p.get("publishedAt"))
        title       = p.get("title", "No Title").strip()
        clean_t     = _clean_title(title) or "No Title"
        raw_t       = sanitise_surrogates(title)
        ext_b       = p.get("extension", {})
        post_id     = p.get("postId")
        is_mem      = p.get("membershipOnly", False)
        author_name = get_author_name(p.get("author", {}))
        _media_url  = media_url(state.COMMUNITY_NAME, post_id)
        found_any   = False

        if not thumb_url:
            thumb_url = ext_b.get("mediaInfo", {}).get("thumbnail", {}).get("url", "")

        if state.DOWNLOAD_TYPE != "photo":
            if v := ext_b.get("video"):
                if vid := v.get("videoId"):
                    path = f"{media_dir}/{make_filename(author_name, date, f'{post_id}_{vid}', title=clean_t, template_key='official_media', tier='Membership' if is_mem else 'Public')}"
                    if not is_already_downloaded(path, post_id=post_id):
                        if is_mem or v.get("membershipOnly"):
                            download_drm_video(post_id, path, thumb_url=thumb_url, weverse_url=_media_url, title=raw_t)
                        else:
                            download_cvideo(vid, path, date)
                            _embed_thumbnail(path, thumb_url, url_meta=_media_url, title=raw_t)
                            _vf = next((f for f in Path(path).parent.iterdir() if f.name.startswith(Path(path).name + ".") and f.suffix.lower() in (".mkv", ".mp4")), None)
                            if _vf:
                                found_any = True
                                utils.edit_creation_date(str(_vf), date)

                        # For DRM downloads, determine success by output existence.
                        if not found_any:
                            _matches = [
                                f
                                for f in Path(path).parent.iterdir()
                                if f.name.startswith(Path(path).name + ".")
                                and f.suffix.lower() in (".mkv", ".mp4")
                            ]
                            if _matches:
                                found_any = True

        if state.DOWNLOAD_TYPE != "video":
            if phs := ext_b.get("image", {}).get("photos"):
                for idx, ph in enumerate(phs):
                    photo_id = ph["photoId"]
                    path = f"{media_dir}/{make_filename(author_name, date, f'{post_id}_{photo_id}_{idx+1}', title=clean_t, template_key='official_media', tier='Membership' if is_mem else 'Public')}"
                    if not is_already_downloaded(path, post_id=post_id):
                        ok = utils.download_file(ph["url"], path, date)
                        if ok:
                            found_any = True
                        embed_url_metadata(path, _media_url)

        if state.SAVE_TEXT:
            _med_txt_stem = make_filename(author_name, date, post_id, title=clean_t, template_key="official_media", tier="Membership" if is_mem else "Public")
            save_post_text(p, media_dir, _med_txt_stem,
                           weverse_url=_media_url,
                           fetch_artist_comments=False)
            if (Path(media_dir) / f"{_med_txt_stem}.txt").exists():
                found_any = True

        if found_any:
            mark_downloaded(post_id)

    if direct_id:
        console.print(f"\n[Media] Checking specific media ID: {direct_id}")
        try:
            p = fetch_post_details({"postId": direct_id})
            if not p:
                return
            is_mem = p.get("membershipOnly", False)
            if is_mem and state.SKIP_MEMBERSHIP:
                console.print(f"  [Skip] Media {direct_id} is Membership-only.")
                return
            if not is_mem and state.SKIP_PUBLIC:
                console.print(f"  [Skip] Media {direct_id} is Public content (--skip-public set).")
                return
            _process_post(p, _build_media_dir(is_mem))
        except Exception as e:
            if not ("access" in str(e).lower() or "403" in str(e)):
                console.print(f"  [Error] Failed to process specific media {direct_id}: {e}")
        return

    console.print(f"\nProcessing Media Tab for {state.COMMUNITY_NAME}...")
    cursor = None
    while True:
        req = (
            f"/media/v1.0/community-{state.COMMUNITY_ID}/searchAllMedia?fieldSet=postsV1"
            + (f"&after={cursor.replace(',', '%2C')}" if cursor else "")
        )
        resp  = run_extr(make_extractor(), req)
        items = resp.get("data", [])
        if not items:
            break

        for p in items:
            is_mem = p.get("membershipOnly", False)
            if is_mem and state.SKIP_MEMBERSHIP:
                continue
            if not is_mem and state.SKIP_PUBLIC:
                continue
            thumb_url = ""
            thumbs = p.get("summary", {}).get("thumbnails", [])
            if thumbs:
                thumb_url = thumbs[0].get("url", "")
            full_p = fetch_post_details(p)
            if not full_p:
                continue
            _process_post(full_p, _build_media_dir(is_mem), thumb_url=thumb_url)

        cursor = resp.get("paging", {}).get("nextParams", {}).get("after")
        if not cursor:
            break
        time.sleep(PAGED_SLEEP)
