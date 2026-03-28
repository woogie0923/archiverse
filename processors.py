"""
processors.py
Archiving process functions (moments, artist/official feeds, profiles).
Official Media tab + menu: official_media.py, official_media_menu.py (re-exported below).
"""
import os
import re
import time
from pathlib import Path

import utils
from utils import console
import state
from config import DOWNLOAD_SLEEP, PAGED_SLEEP, STOP_THRESHOLD, get_folder
from text_writer import (
    save_post_text, embed_url_metadata, is_text_saved,
    artist_post_url, moment_url, official_post_url,
)
from api import make_extractor, run_extr, fetch_post_details, register_member_name
from helpers import (
    get_author_name, make_filename, matches_target, sanitise, sanitise_surrogates
)
from downloader import (
    is_already_downloaded, download_cvideo, download_drm_video,
    get_official_video_url, mark_downloaded
)
from official_media import process_official_media
from official_media_menu import process_official_media_menu


def process_single_post(post_id: str):
    """
    Download a single post by its post ID (--post argument).
    Works for artist posts (NORMAL, VIDEO), moments, and official channel posts (OFFICIAL).
    """
    console.print(f"\n  -> Fetching post: {post_id}")

    # First attempt: plain fetch (works for public and membership posts the user has access to)
    try:
        req = (
            f"/post/v1.0/post-{post_id}"
            f"?appId=be4d79eb8fc7bd008ee82c8ec4ff6fd4&fieldSet=postV1"
            f"&language=en&os=WEB&platform=WEB&wpf=pc"
        )
        full_post = run_extr(make_extractor(), req, retries=3)
    except Exception as e:
        _e = str(e).lower()
        if "does not have access" in _e or "403" in str(e) or "only available for" in _e or "401" in str(e):
            # May be a locked post that requires a password — try via fetch_post_details
            # which handles the password prompt and cache
            full_post = fetch_post_details({"postId": post_id, "locked": True})
            if not full_post:
                console.print(f"  [Error] Could not fetch post {post_id}. Access denied.")
                return
        else:
            console.print(f"  [Error] Could not fetch post {post_id}: {e}")
            return

    if not full_post:
        console.print(f"  [Error] Could not fetch post {post_id}.")
        return

    # Handle locked posts — delegate to fetch_post_details for password prompt/cache
    if full_post.get("blindType") == "LOCKED":
        full_post = fetch_post_details({
            "postId":         post_id,
            "membershipOnly": full_post.get("membershipOnly", False),
            "locked":         True,
        })
        if not full_post:
            return

    post_type   = full_post.get("postType", "")
    section     = full_post.get("sectionType", "")
    author      = full_post.get("author", {})
    member_id   = author.get("memberId", "")
    author_name = get_author_name(author)
    profile_type = author.get("profileType", "")

    console.print(f"  -> Post type: {post_type} | Author: {author_name}")

    if post_type == "OFFICIAL" or section == "OFFICIAL" or profile_type == "AGENCY":
        _process_single_official_post(full_post)
    elif post_type in ("MOMENT_W1", "MOMENT"):
        process_moments(direct_id=post_id)
    else:
        clean_name = sanitise(author_name)
        register_member_name(member_id, author_name)
        date        = utils.timestamp(full_post["publishedAt"])
        is_mem      = full_post.get("membershipOnly", False)
        tier        = "Membership" if is_mem else "Public"
        attachments = full_post.get("attachment", {})
        photos      = attachments.get("photo", {})
        videos      = attachments.get("video", {})
        _post_url   = artist_post_url(state.COMMUNITY_NAME, post_id)
        artist_dir  = get_folder("artist_posts", community=state.COMMUNITY_NAME, tier=tier, artist=clean_name)
        os.makedirs(artist_dir, exist_ok=True)

        if not photos and not videos:
            if state.SAVE_TEXT:
                txt_stem = make_filename(clean_name, date, post_id, title="", template_key="artist_posts", tier=tier)
                save_post_text(full_post, artist_dir, txt_stem, weverse_url=_post_url, fetch_artist_comments=True)
                mark_downloaded(post_id)
            return

        if not state.TEXT_ONLY:
            if state.DOWNLOAD_TYPE != "video":
                for pid, photo in photos.items():
                    filename = make_filename(clean_name, date, f"{post_id}_{pid}", title="", template_key="artist_posts", tier=tier)
                    path = f"{artist_dir}/{filename}"
                    if not is_already_downloaded(path, post_id=post_id):
                        utils.download_file(photo["url"], path, date)
                        embed_url_metadata(path, _post_url)
            if state.DOWNLOAD_TYPE != "photo":
                for vid, video_data in videos.items():
                    filename = make_filename(clean_name, date, f"{post_id}_{vid}", title="", template_key="artist_posts", tier=tier)
                    path = f"{artist_dir}/{filename}"
                    if not is_already_downloaded(path, post_id=post_id):
                        download_cvideo(vid, path, date)
                        embed_url_metadata(path, _post_url)
                        _av = next((f for f in Path(path).parent.iterdir() if f.name.startswith(Path(path).name + ".") and f.suffix.lower() in (".mkv", ".mp4")), None)
                        if _av: utils.edit_creation_date(str(_av), date)

        if state.SAVE_TEXT:
            txt_stem = make_filename(clean_name, date, post_id, title="", template_key="artist_posts", tier=tier)
            save_post_text(full_post, artist_dir, txt_stem, weverse_url=_post_url, fetch_artist_comments=True)

        mark_downloaded(post_id)


def _process_single_official_post(full_post: dict):
    """Download media and text from a single official channel post."""
    post_id      = full_post.get("postId", "")
    author       = full_post.get("author", {})
    channel_name = sanitise(author.get("profileName") or state.COMMUNITY_NAME)
    date         = utils.timestamp(full_post["publishedAt"])
    attachments  = full_post.get("attachment", {})
    photos       = attachments.get("photo", {})
    videos       = attachments.get("video", {})
    _off_url     = official_post_url(state.COMMUNITY_NAME, post_id)

    console.print(f"  -> Official channel: {channel_name}")

    if not state.TEXT_ONLY:
        if state.DOWNLOAD_TYPE != "video":
            for pid, photo in photos.items():
                filename = make_filename(channel_name, date, f"{post_id}_{pid}", title="", template_key="official_posts", tier="Public")
                path = get_folder("official_channel", community=state.COMMUNITY_NAME, channel=channel_name) + f"/{filename}"
                if not is_already_downloaded(path, post_id=post_id):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    utils.download_file(photo["url"], path, date)
                    embed_url_metadata(path, _off_url)
        if state.DOWNLOAD_TYPE != "photo":
            for wv_vid_id, video_entry in videos.items():
                naver_id = video_entry.get("uploadInfo", {}).get("videoId") or video_entry.get("videoId") or wv_vid_id
                filename = make_filename(channel_name, date, f"{post_id}_{wv_vid_id}", title="", template_key="official_posts", tier="Public")
                path = get_folder("official_channel", community=state.COMMUNITY_NAME, channel=channel_name) + f"/{filename}"
                if not is_already_downloaded(path, post_id=post_id):
                    url = get_official_video_url(wv_vid_id, naver_id)
                    if url:
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        utils.download_file(url, path, date)
                        embed_url_metadata(path, _off_url)

    if state.SAVE_TEXT:
        _off_dir  = get_folder("official_channel", community=state.COMMUNITY_NAME, channel=channel_name)
        _off_stem = make_filename(channel_name, date, post_id, title="", template_key="official_posts", tier="Public")
        save_post_text(full_post, _off_dir, _off_stem, weverse_url=_off_url, fetch_artist_comments=False)

    mark_downloaded(post_id)


def process_moments(direct_id=None):
    """
    Archive artist moments.
    Pass direct_id to download a single specific moment by post ID.
    """

    def _download_moment(m: dict, member_name: str):
        """Download a single moment item."""
        is_mem = m.get("membershipOnly", False)
        if is_mem and state.SKIP_MEMBERSHIP:
            return
        if not is_mem and state.SKIP_PUBLIC:
            return

        date    = utils.timestamp(m["publishedAt"])
        tier    = "Membership" if is_mem else "Public"
        moment_dir = get_folder(
            "moments",
            community=state.COMMUNITY_NAME,
            tier=tier,
            artist=member_name,
        )
        os.makedirs(moment_dir, exist_ok=True)

        filename = make_filename(member_name, date, m["postId"], title="", template_key="moments", tier=tier)
        path     = f"{moment_dir}/{filename}"

        if is_already_downloaded(path, post_id=m["postId"]):
            return

        ext_block     = m.get("extension", {})
        moment_w1     = ext_block.get("momentW1", {})
        moment_legacy = ext_block.get("moment", {})

        video_id  = (
            (moment_w1.get("video", {}).get("videoId") if moment_w1 else None)
            or moment_legacy.get("video", {}).get("videoId")
        )
        photo_url = (
            (moment_w1.get("photo", {}).get("url") if moment_w1 else None)
            or moment_legacy.get("photo", {}).get("url")
        )

        _moment_member_id = m.get("author", {}).get("memberId", "")
        _moment_url = moment_url(state.COMMUNITY_NAME, _moment_member_id, m["postId"])
        
        if not state.TEXT_ONLY:
            if state.DOWNLOAD_TYPE != "video" and photo_url:
                utils.download_file(photo_url, path, date)
                embed_url_metadata(path, _moment_url)
            elif state.DOWNLOAD_TYPE != "photo" and video_id:
                download_cvideo(video_id, path, date)
                embed_url_metadata(path, _moment_url)
                _mv = next((f for f in Path(path).parent.iterdir() if f.name.startswith(Path(path).name + ".") and f.suffix.lower() in (".mkv", ".mp4")), None)
                if _mv: utils.edit_creation_date(str(_mv), date)

        if state.SAVE_TEXT:
            save_post_text(m, os.path.dirname(path),
                           os.path.basename(path),
                           weverse_url=_moment_url,
                           fetch_artist_comments=True,
                           force_comments=True)

        mark_downloaded(m["postId"])

    if direct_id:
        console.print(f"\n[Moment] Fetching specific Moment ID: {direct_id}")
        m = fetch_post_details({"postId": direct_id})
        if not m:
            return
        if m.get("membershipOnly") and state.SKIP_MEMBERSHIP:
            console.print(f"  [Skip] Moment {direct_id} is Membership-only.")
            return
        member_name = get_author_name(m.get("author", {}))
        _download_moment(m, member_name)
        return

    console.print(f"\nProcessing Moments for {state.COMMUNITY_NAME}...")
    artists_data = run_extr(
        make_extractor(),
        f"/artistpedia/v1.0/community-{state.COMMUNITY_ID}/highlight",
    )

    for artist in artists_data.get("artistProfiles", []):
        member_name = artist["artistOfficialProfile"]["officialName"]
        register_member_name(artist["memberId"], member_name)
        if not matches_target(member_name):
            continue

        console.print(f"\n  -> Scanning {member_name}\n")
        new_this_session, consecutive_no_new, cursor = False, 0, None

        while True:
            req = (
                f'/post/v1.0/member-{artist["memberId"]}/posts'
                f"?fieldSet=postsV1&filterType=MOMENT_VIEWER&limit=10"
            )
            if cursor:
                req += f"&after={cursor}"
            resp  = run_extr(make_extractor(), req)
            items = resp.get("data", [])
            if not items:
                break

            for m in items:
                is_mem = m.get("membershipOnly", False)
                if is_mem and state.SKIP_MEMBERSHIP:
                    continue
                if not is_mem and state.SKIP_PUBLIC:
                    continue

                date    = utils.timestamp(m["publishedAt"])
                tier    = "Membership" if is_mem else "Public"
                moment_dir = get_folder(
                    "moments",
                    community=state.COMMUNITY_NAME,
                    tier=tier,
                    artist=member_name,
                )
                os.makedirs(moment_dir, exist_ok=True)

                filename = make_filename(member_name, date, m["postId"], title="", template_key="moments", tier=tier)
                path     = f"{moment_dir}/{filename}"

                if is_already_downloaded(path, post_id=m["postId"]):
                    continue

                ext_block     = m.get("extension", {})
                moment_w1     = ext_block.get("momentW1", {})
                moment_legacy = ext_block.get("moment", {})

                video_id  = (
                    (moment_w1.get("video", {}).get("videoId") if moment_w1 else None)
                    or moment_legacy.get("video", {}).get("videoId")
                )
                photo_url = (
                    (moment_w1.get("photo", {}).get("url") if moment_w1 else None)
                    or moment_legacy.get("photo", {}).get("url")
                )

                _moment_member_id = m.get("author", {}).get("memberId", "")
                _moment_url = moment_url(state.COMMUNITY_NAME, _moment_member_id, m["postId"])

                found_content = False
                if not state.TEXT_ONLY:
                    if state.DOWNLOAD_TYPE != "video" and photo_url:
                        utils.download_file(photo_url, path, date)
                        embed_url_metadata(path, _moment_url)
                        found_content = True
                    elif state.DOWNLOAD_TYPE != "photo" and video_id:
                        download_cvideo(video_id, path, date)
                        embed_url_metadata(path, _moment_url)
                        _mv2 = next((f for f in Path(path).parent.iterdir() if f.name.startswith(Path(path).name + ".") and f.suffix.lower() in (".mkv", ".mp4")), None)
                        if _mv2: utils.edit_creation_date(str(_mv2), date)
                        found_content = True

                if state.SAVE_TEXT:
                    save_post_text(m, moment_dir,
                                   os.path.basename(path),
                                   weverse_url=_moment_url,
                                   fetch_artist_comments=True,
                                   force_comments=True)

                if found_content:
                    consecutive_no_new = 0
                    new_this_session   = True
                    mark_downloaded(m["postId"])
                    time.sleep(DOWNLOAD_SLEEP)
                elif new_this_session:
                    consecutive_no_new += 1

            cursor = resp.get("paging", {}).get("nextParams", {}).get("after")
            if not cursor or (new_this_session and consecutive_no_new >= STOP_THRESHOLD):
                break


def _process_artist_posts_for_member(member_name: str, member_id: str, former: bool = False):
    """
    Download all artist posts for a single member.

    Current members: uses /post/v1.0/member-{id}/posts (direct feed).
    Former members:  uses /post/v1.0/community-{id}/artistTabPosts and
                     filters by memberId, since the member feed returns
                     403 after they leave the community.
    """
    clean_member_name = sanitise(member_name)
    register_member_name(member_id, member_name)
    console.print(f"\n  -> Scanning {member_name}{' (former member)' if former else ''}\n")

    if not former:
        base_req = f"/post/v1.0/member-{member_id}/posts?fieldSet=postsV1&limit=20"
    else:
        base_req = (
            f"/post/v1.0/community-{state.COMMUNITY_ID}/artistTabPosts"
            f"?fieldSet=postsV1&limit=20"
        )

    current_cursor     = None
    consecutive_no_new = 0

    while True:
        mod_req = base_req + (
            f"&after={current_cursor.replace(',', '%2C')}" if current_cursor else ""
        )
        resp  = run_extr(make_extractor(), mod_req)
        items = resp.get("data", [])
        if not items:
            break

        # For former members using the community feed, filter to their posts only
        if former:
            items = [p for p in items if p.get("author", {}).get("memberId") == member_id]

        for summary in items:
            post_id   = summary.get("postId")
            full_post = fetch_post_details(summary)
            if not full_post:
                continue

            date   = utils.timestamp(full_post["publishedAt"])
            is_mem = full_post.get("membershipOnly", False)

            if is_mem and state.SKIP_MEMBERSHIP:
                console.print(f"  [Skip] {post_id} is Membership-only content.")
                continue
            if not is_mem and state.SKIP_PUBLIC:
                console.print(f"  [Skip] {post_id} is Public content (--skip-public set).")
                continue

            attachments = full_post.get("attachment", {})
            photos      = attachments.get("photo", {})
            videos      = attachments.get("video", {})
            _post_url   = artist_post_url(state.COMMUNITY_NAME, post_id)

            tier       = "Membership" if is_mem else "Public"
            artist_dir = get_folder(
                "artist_posts",
                community=state.COMMUNITY_NAME,
                tier=tier,
                artist=clean_member_name,
            )
            os.makedirs(artist_dir, exist_ok=True)

            if not photos and not videos:
                if state.SAVE_TEXT:
                    txt_stem = make_filename(clean_member_name, date, post_id, title="", template_key="artist_posts", tier=tier)
                    save_post_text(full_post, artist_dir, txt_stem,
                                   weverse_url=_post_url,
                                   fetch_artist_comments=True)
                mark_downloaded(post_id)
                continue

            found_new = False

            if not state.TEXT_ONLY:
                if state.DOWNLOAD_TYPE != "video":
                    _photos_list = list(photos.items())
                    for _p_idx, (pid, photo) in enumerate(_photos_list):
                        filename = make_filename(clean_member_name, date, f"{post_id}_{pid}", title="", template_key="artist_posts", tier=tier)
                        path     = f"{artist_dir}/{filename}"
                        if not is_already_downloaded(path, post_id=post_id):
                            utils.download_file(photo["url"], path, date)
                            embed_url_metadata(path, _post_url)
                            found_new = True

                if state.DOWNLOAD_TYPE != "photo":
                    for vid, video_data in videos.items():
                        filename = make_filename(clean_member_name, date, f"{post_id}_{vid}", title="", template_key="artist_posts", tier=tier)
                        path     = f"{artist_dir}/{filename}"
                        if not is_already_downloaded(path, post_id=post_id):
                            download_cvideo(vid, path, date)
                            embed_url_metadata(path, _post_url)
                            _av = next((f for f in Path(path).parent.iterdir() if f.name.startswith(Path(path).name + ".") and f.suffix.lower() in (".mkv", ".mp4")), None)
                            if _av: utils.edit_creation_date(str(_av), date)
                            found_new = True

            if state.SAVE_TEXT:
                txt_stem = make_filename(clean_member_name, date, post_id, title="", template_key="artist_posts", tier=tier)
                save_post_text(full_post, artist_dir, txt_stem,
                               weverse_url=_post_url,
                               fetch_artist_comments=True)
                if state.TEXT_ONLY and (Path(artist_dir) / f"{txt_stem}.txt").exists():
                    found_new = True

            if found_new:
                consecutive_no_new = 0
                mark_downloaded(post_id)
            else:
                mark_downloaded(post_id)
                consecutive_no_new += 1

            if consecutive_no_new >= STOP_THRESHOLD:
                break

        if consecutive_no_new >= STOP_THRESHOLD:
            break
        current_cursor = resp.get("paging", {}).get("nextParams", {}).get("after")
        if not current_cursor:
            break


def process_artist_posts():
    """Archive individual artist posts (photos and videos)."""
    from config import CFG as _CFG

    console.print(f"\nProcessing Artist Posts for {state.COMMUNITY_NAME}...")
    artists_data = run_extr(
        make_extractor(),
        f"/artistpedia/v1.0/community-{state.COMMUNITY_ID}/highlight",
    )

    # Build set of current member IDs to not double-process former members
    current_ids: set = set()

    for artist in artists_data.get("artistProfiles", []):
        member_name = artist["artistOfficialProfile"]["officialName"]
        member_id   = artist["memberId"]
        current_ids.add(member_id)
        register_member_name(member_id, member_name)

        if not matches_target(member_name):
            continue

        _process_artist_posts_for_member(member_name, member_id, former=False)

    # Former members — use artistTabPosts community feed filtered by memberId
    for entry in _CFG.get("former_members", {}).get(state.COMMUNITY_NAME, []):
        mid  = entry.get("id", "").strip()
        name = entry.get("name", mid).strip()
        if not mid or mid in current_ids:
            continue
        if not matches_target(name):
            continue
        register_member_name(mid, name)
        _process_artist_posts_for_member(name, mid, former=True)



def process_official_posts(member_ids: list):
    """
    Archive posts from official agency accounts.
    Handles both photos and videos (including the 2-step neonplayer flow).
    """
    for member_id in member_ids:
        channel_name   = None
        console.print(f"\nProcessing Official Channel: {member_id}...")
        base_req       = f"/post/v1.0/member-{member_id}/posts?fieldSet=postsV1"
        current_cursor = None
        consecutive_no_new = 0

        while True:
            mod_req = base_req + (
                f"&after={current_cursor.replace(',', '%2C')}" if current_cursor else ""
            )
            resp = run_extr(make_extractor(), mod_req)

            for summary in resp.get("data", []):
                if channel_name is None:
                    raw_name     = summary.get("author", {}).get("profileName") or member_id
                    channel_name = sanitise(raw_name)
                    console.print(f"  -> Resolved channel name: {channel_name}")

                full_post = fetch_post_details(summary)
                if not full_post:
                    continue

                date        = utils.timestamp(full_post["publishedAt"])
                attachments = full_post.get("attachment", {})
                photos      = attachments.get("photo", {})
                videos      = attachments.get("video", {})
                _off_url    = official_post_url(state.COMMUNITY_NAME, summary["postId"])

                if not photos and not videos:
                    # Text-only official post — save text if enabled
                    if state.SAVE_TEXT and channel_name:
                        _off_dir  = get_folder("official_channel", community=state.COMMUNITY_NAME, channel=channel_name)
                        _off_stem = make_filename(channel_name, date, summary["postId"], title="", template_key="official_posts", tier="Public")
                        save_post_text(full_post, _off_dir, _off_stem,
                                       weverse_url=_off_url, fetch_artist_comments=False)
                    consecutive_no_new += 1
                    if consecutive_no_new >= STOP_THRESHOLD:
                        break
                    continue

                found_new = False

                if state.DOWNLOAD_TYPE != "video":
                    for pid, photo in photos.items():
                        filename = make_filename(channel_name, date, f'{summary["postId"]}_{pid}', title="", template_key="official_posts", tier="Public")
                        path     = get_folder(
                            "official_channel",
                            community=state.COMMUNITY_NAME,
                            channel=channel_name,
                        ) + f"/{filename}"
                        if not is_already_downloaded(path, post_id=summary["postId"]):
                            os.makedirs(os.path.dirname(path), exist_ok=True)
                            utils.download_file(photo["url"], path, date)
                            embed_url_metadata(path, _off_url)
                            found_new = True
                            time.sleep(DOWNLOAD_SLEEP)

                # Videos (2-step neonplayer flow)
                if state.DOWNLOAD_TYPE != "photo":
                    for wv_vid_id, video_entry in videos.items():
                        naver_id = video_entry.get("uploadInfo", {}).get("videoId")
                        if not naver_id:
                            naver_id = video_entry.get("videoId") or wv_vid_id
                        filename = make_filename(channel_name, date, f'{summary["postId"]}_{wv_vid_id}', title="", template_key="official_posts", tier="Public")
                        path     = get_folder(
                            "official_channel",
                            community=state.COMMUNITY_NAME,
                            channel=channel_name,
                        ) + f"/{filename}"
                        if not is_already_downloaded(path, post_id=summary["postId"]):
                            console.print(
                                f"  [Video] Fetching: {summary['postId']} "
                                f"/ wv={wv_vid_id} naver={naver_id}"
                            )
                            url = get_official_video_url(wv_vid_id, naver_id)
                            if url:
                                os.makedirs(os.path.dirname(path), exist_ok=True)
                                utils.download_file(url, path, date)
                                embed_url_metadata(path, _off_url)
                                _oc_vf = next((f for f in Path(path).parent.iterdir() if f.name.startswith(Path(path).name + ".") and f.suffix.lower() in (".mkv", ".mp4")), None)
                                if _oc_vf: utils.edit_creation_date(str(_oc_vf), date)
                                found_new = True
                                time.sleep(DOWNLOAD_SLEEP)

                if state.SAVE_TEXT and channel_name:
                    _off_dir  = get_folder("official_channel", community=state.COMMUNITY_NAME, channel=channel_name)
                    _off_stem = make_filename(channel_name, date, summary["postId"], title="", template_key="official_posts", tier="Public")
                    save_post_text(full_post, _off_dir, _off_stem,
                                   weverse_url=_off_url,
                                   fetch_artist_comments=False)
                if found_new:
                    consecutive_no_new = 0
                    mark_downloaded(summary["postId"])
                else:
                    consecutive_no_new += 1

                if consecutive_no_new >= STOP_THRESHOLD:
                    break

            current_cursor = resp.get("paging", {}).get("nextParams", {}).get("after")
            if consecutive_no_new >= STOP_THRESHOLD or not current_cursor:
                break


def process_member_profiles():
    """Archive artist profile pictures, cover images, and official images."""
    if state.DOWNLOAD_TYPE == "video":
        console.print("\n[Skip] Skipping Profiles because --type is set to 'video'.")
        return

    console.print(f"\nProcessing Member Profiles for {state.COMMUNITY_NAME}...")
    artists_data = run_extr(
        make_extractor(),
        f"/artistpedia/v1.0/community-{state.COMMUNITY_ID}/highlight",
    )

    for artist in artists_data.get("artistProfiles", []):
        member_name = artist["artistOfficialProfile"]["officialName"]
        member_id   = artist["memberId"]

        if not matches_target(member_name):
            continue
        console.print(f"")
        console.print(f"  -> Archiving profile media for {member_name}")
        console.print(f"")

        req = (
            f"/member/v1.0/member-{member_id}"
            "?fields=memberId%2CcommunityId%2Cjoined%2CprofileType%2CprofileName"
            "%2CprofileImageUrl%2CprofileCoverImageUrl%2CprofileComment%2CmyProfile"
            "%2Chidden%2Cblinded%2CmemberJoinStatus%2CfirstJoinAt%2CfollowCount"
            "%2Cfollowed%2ChasMembership%2ChasOfficialMark%2CartistOfficialProfile"
            "%2CavailableActions%2CprofileSpaceStatus%2Cbadges%2CshareUrl"
        )
        profile_data = run_extr(make_extractor(), req)

        pics = [
            (profile_data.get("profileImageUrl"), "profileImage"),
            (profile_data.get("profileCoverImageUrl"), "profileCover"),
            (
                profile_data.get("artistOfficialProfile", {}).get("officialImageUrl"),
                "profileOfficial",
            ),
        ]

        profile_dir = Path(
            get_folder(
                "profiles",
                community=state.COMMUNITY_NAME,
                artist=member_name,
            )
        )
        profile_dir.mkdir(parents=True, exist_ok=True)

        for pic_url, name in pics:
            if pic_url:
                # Extract a short hash from the URL so each profile picture
                # version gets a unique filename. This preserves historical
                # pictures when an artist changes their profile.
                import hashlib as _hl
                url_hash   = _hl.md5(pic_url.encode()).hexdigest()[:8]

                identifier = url_hash

                # TODO: Consider using the exact date format as seen in `helpers.make_filename`
                created_at_date = None
                if created_at_date := utils.get_date_from_url(pic_url):
                    identifier = f"{identifier}_{created_at_date.strftime("%Y%m%d")}"

                ext_match   = re.search(r"\.([a-zA-Z0-9]+)(\?|$)", pic_url)
                extension   = ext_match.group(1) if ext_match else "jpg"
                save_path   = profile_dir / f"{name}_{identifier}.{extension}"
                if not save_path.exists():
                    utils.download_file(pic_url, str(save_path.with_suffix("")), created_at_date)
                    time.sleep(DOWNLOAD_SLEEP)