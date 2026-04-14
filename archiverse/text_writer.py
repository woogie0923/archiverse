"""
text_writer.py
Handles text-only post archiving, artist comment saving, and URL metadata
embedding into image and video files.
"""
import html
import re
import struct
import time
import subprocess
from pathlib import Path
from datetime import datetime

from . import state
from . import utils
from .utils import console
from .config import BINARIES
from .api import make_extractor, run_extr


def _clean_post_body_text(raw: str) -> str:
    """
    Normalize Weverse post/comment body text for .txt output:
    - Strip WordprocessingML tags (<w:b>, </w:b>, <w:t/>, …)
    - Decode HTML/XML character references (&gt;, &lt;, &amp;, &#…;, etc.)
    - Collapse runs of blank lines
    """
    if not raw:
        return ""
    s = raw.strip()
    # Weverse uses <w:attachment .../> placeholders inside body text. When we
    # strip tags, these can accidentally glue adjacent text together (e.g.
    # "...channel:<w:attachment .../>#TAG" -> "...channel:#TAG"). Treat them as
    # a line break to preserve the original post formatting.
    s = re.sub(r"<w:attachment\b[^>]*/\s*>", "\n", s)
    s = re.sub(r"</?w:[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def _format_post_header_ts(ts) -> str:
    """Readable local timestamp for .txt headers (e.g. March 28, 2026 03:45 PM)."""
    if ts is None:
        return ""
    try:
        s = str(utils.timestamp(ts))[:19]
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%B %d, %Y %I:%M %p")
    except Exception:
        return str(ts)[:19] if ts else ""


def embed_url_metadata(file_path: str, url: str, title: str = ""):
    """
    Universal metadata embedder.
    Embeds Weverse URL as COMMENT and optionally a title into MKV/MP4/JPEG/PNG.
    """
    if not url and not title:
        return

    import subprocess
    from pathlib import Path
    from .config import BINARIES

    target_file = Path(file_path)

    if not target_file.exists():
        matches = [f for f in target_file.parent.iterdir() if f.name.startswith(target_file.name + ".")]
        if not matches:
            return
        target_file = matches[0]

    ext = target_file.suffix.lower()

    if ext == '.mkv':
        import os as _os, tempfile as _tf
        mkvpropedit = BINARIES.get("mkvpropedit", "mkvpropedit")
        cf = 0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0

        if title:
            try:
                cmd = [mkvpropedit, str(target_file), "--edit", "info", "--set", f"title={title}"]
                subprocess.run(cmd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', creationflags=cf)
            except Exception:
                pass

        if url:
            safe_url = url.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE Tags SYSTEM "matroskatags.dtd">\n'
                '<Tags>\n'
                '  <Tag>\n'
                '    <Simple>\n'
                f'      <Name>COMMENT</Name>\n'
                f'      <String>{safe_url}</String>\n'
                '    </Simple>\n'
                '  </Tag>\n'
                '</Tags>\n'
            )
            tmp_xml = None
            try:
                with _tf.NamedTemporaryFile(mode='w', suffix='.xml', delete=False,
                                            encoding='utf-8') as f:
                    f.write(xml)
                    tmp_xml = f.name

                cmd = [mkvpropedit, str(target_file), "--tags", f"global:{tmp_xml}"]
                result = subprocess.run(cmd, capture_output=True, text=True,
                                        encoding='utf-8', errors='replace', creationflags=cf)

                if result.returncode == 0:
                    console.print(f"  [Metadata] Tagged MKV: {target_file.name}")
                else:
                    err = (result.stdout or result.stderr or "").strip()
                    console.print(f"  [Metadata] mkvpropedit error: {err}")
            except Exception as e:
                console.print(f"  [Metadata] Failed to run mkvpropedit: {e}")
            finally:
                if tmp_xml and _os.path.exists(tmp_xml):
                    _os.unlink(tmp_xml)

    elif ext == '.mp4':
        ffmpeg = BINARIES.get("ffmpeg", "ffmpeg")
        temp_file = target_file.with_name(f"temp_meta_{target_file.name}")

        cmd = [ffmpeg, "-y", "-i", str(target_file)]
        if url:
            cmd.extend(["-metadata", f"comment={url}"])
        if title:
            cmd.extend(["-metadata", f"title={title}"])
        cmd.extend(["-c", "copy", "-map_metadata", "0", str(temp_file)])

        try:
            cf = 0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            if subprocess.run(cmd, capture_output=True, creationflags=cf).returncode == 0:
                target_file.unlink()
                temp_file.rename(target_file)
                console.print(f"  [Metadata] Successfully tagged MP4: {target_file.name}")
        except Exception as e:
            if temp_file.exists(): temp_file.unlink()
            console.print(f"  [Metadata] FFmpeg tagging failed: {e}")

    elif ext in ('.jpg', '.jpeg'):
        _embed_jpeg(target_file, url)
    elif ext == '.png':
        _embed_png(target_file, url)

def _embed_jpeg(image_file: Path, url: str):
    try:
        import piexif
    except ImportError:
        _inject_jpeg_com(image_file, url)
        return

    try:
        try:
            exif_dict = piexif.load(str(image_file))
        except Exception:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}

        exif_dict["0th"][piexif.ImageIFD.XPComment] = url.encode("utf-16le") + b"\x00\x00"

        for tag in (piexif.ImageIFD.ImageDescription, piexif.ImageIFD.XPTitle,
                    piexif.ImageIFD.XPSubject, piexif.ImageIFD.XPKeywords):
            exif_dict["0th"].pop(tag, None)

        piexif.insert(piexif.dump(exif_dict), str(image_file))
    except Exception as e:
        if state.DEBUG_MODE:
            console.print(f"  https://www.merriam-webster.com/dictionary/embed {image_file.name}: {e}")
        _inject_jpeg_com(image_file, url)


def _inject_jpeg_com(image_file: Path, url: str):
    """Pure-Python fallback: JPEG COM (0xFFFE) marker."""
    try:
        data = image_file.read_bytes()
        if data[:2] != b"\xff\xd8":
            return
        com_bytes = url.encode("utf-8")
        length = len(com_bytes) + 2
        marker = b"\xff\xfe" + struct.pack(">H", length) + com_bytes
        image_file.write_bytes(data[:2] + marker + data[2:])
    except Exception:
        pass


def _embed_png(image_file: Path, url: str):
    import zlib
    try:
        data = image_file.read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return
        chunk_data = b"Comment\x00" + url.encode("utf-8")
        crc = zlib.crc32(b"tEXt" + chunk_data) & 0xFFFFFFFF
        chunk = struct.pack(">I", len(chunk_data)) + b"tEXt" + chunk_data + struct.pack(">I", crc)
        image_file.write_bytes(data[:33] + chunk + data[33:])
    except Exception:
        pass


def _embed_video(video_file: Path, url: str):
    """
    Handles video metadata embedding.
    MKV: Uses mkvpropedit (in-place).
    MP4/Others: Uses FFmpeg (copy to temp then replace).
    """
    ext = video_file.suffix.lower()
    
    if ext == ".mkv":
        _embed_mkv_mkvpropedit(video_file, url)
    else:
        _embed_mp4_ffmpeg(video_file, url)


def _embed_mkv_url(video_file: Path, url: str):
    """Embed URL into MKV global metadata (Comment field)."""
    try:
        from .config import BINARIES
        import subprocess
        mkvpropedit = BINARIES.get("mkvpropedit", "mkvpropedit")
        cmd = [mkvpropedit, str(video_file), "--edit", "info", "--set", f"comment={url}"]
        subprocess.run(cmd, capture_output=True)
    except Exception as e:
        if state.DEBUG_MODE:
            console.print(f"  [Video URL embed] {video_file.name}: {e}")


def _embed_mp4_ffmpeg(video_file: Path, url: str):
    """Sets the comment/description tags using FFmpeg."""
    try:
        ffmpeg = BINARIES.get("ffmpeg", "ffmpeg")
        temp_file = video_file.with_suffix(video_file.suffix + ".metadata.tmp")
        
        cmd = [
            ffmpeg, "-y", "-i", str(video_file),
            "-metadata", f"comment={url}",
            "-metadata", f"description={url}",
            "-c", "copy", "-map_metadata", "0",
            str(temp_file)
        ]
        
        cf = 0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        result = subprocess.run(cmd, capture_output=True, creationflags=cf)
        
        if result.returncode == 0 and temp_file.exists():
            temp_file.replace(video_file)
        elif temp_file.exists():
            temp_file.unlink()
            
    except Exception as e:
        if state.DEBUG_MODE:
            console.print(f"  [FFmpeg Metadata Error] {video_file.name}: {e}")



def is_text_saved(base_path: str) -> bool:
    p = Path(base_path)
    return (p.parent / f"{p.name}.txt").exists()


def _fetch_comment_page(post_id: str, cursor: str | None) -> tuple[list, str | None]:
    req = (
        f"/comment/v1.0/post-{post_id}/artistComments"
        f"?appId=be4d79eb8fc7bd008ee82c8ec4ff6fd4"
        f"&fieldSet=postArtistCommentsV1"
        f"&language=en&limit=100&os=WEB&platform=WEB&sortType=LATEST&wpf=pc"
    )
    if cursor:
        req += f"&after={cursor.replace(',', '%2C')}"
    try:
        resp = run_extr(make_extractor(), req, retries=3)
        items = resp.get("data", [])
        cursor = resp.get("paging", {}).get("nextParams", {}).get("after")
        return items, cursor
    except Exception:
        return [], None


def fetch_comments(post_id: str) -> tuple[list, str]:
    """
    Fetch artist comments for a post.

    Returns (comments, post_body) where:
      - comments  : list of artist comment dicts (sorted oldest-first)
      - post_body : body text extracted from parent.data.body in the first
                    comment whose parent.type == "POST". This is the actual
                    moment caption, since the Weverse API returns
                    plainBody = "Moment uploaded." for moment posts and
                    stores the real text inside the comments response.
    """
    comments: list = []
    post_body: str = ""

    def _collect(items):
        nonlocal post_body
        for c in items:
            if not post_body:
                parent = c.get("parent", {})
                if parent.get("type") == "POST":
                    raw = (parent.get("data", {}).get("body") or "").strip()
                    if raw:
                        post_body = _clean_post_body_text(raw)

            author = c.get("author", {})
            if author.get("profileType") not in ("ARTIST", "AGENCY"):
                continue
            body = _clean_post_body_text(c.get("body") or "")
            if not body:
                continue

            official_name = author.get("artistOfficialProfile", {}).get("officialName", "")
            profile_name  = author.get("profileName", "")
            if official_name and profile_name and official_name != profile_name:
                display_name = f"{official_name} ({profile_name})"
            else:
                display_name = official_name or profile_name

            parent_block = c.get("parent", {})
            parent_data  = None
            if parent_block.get("type") == "COMMENT":
                pd          = parent_block.get("data", {})
                parent_body = _clean_post_body_text(pd.get("body") or "")
                if parent_body:
                    fan_author = pd.get("author", {})
                    fan_ts     = pd.get("createdAt") or pd.get("publishedAt")
                    parent_data = {
                        "commentId": pd.get("commentId", ""),
                        "fanName":   fan_author.get("profileName", ""),
                        "body":      parent_body,
                        "timestamp": _format_post_header_ts(fan_ts),
                    }

            ts = c.get("createdAt") or c.get("publishedAt")
            ts_header = _format_post_header_ts(ts)
            comments.append({
                "commentId":  c.get("commentId"),
                "authorName": display_name,
                "body":       body,
                "timestamp":  ts_header,
                "_ts_raw":    ts or 0,
                "parent":     parent_data,
            })

    cursor = None
    while True:
        items, cursor = _fetch_comment_page(post_id, cursor)
        if not items: break
        _collect(items)
        if not cursor: break
        time.sleep(0.3)

    comments.sort(key=lambda c: c["_ts_raw"])
    for c in comments: del c["_ts_raw"]
    return comments, post_body


def save_post_text(post: dict, output_dir: str, filename_stem: str, weverse_url: str = "", fetch_artist_comments: bool = False, force_comments: bool = False):
    """
    force_comments=True: always include artist comments regardless of SAVE_COMMENTS.
    Use for moments where artist replies are primary content, not supplemental.
    """
    if not state.SAVE_TEXT: return
    txt_path = Path(output_dir) / f"{filename_stem}.txt"
    if txt_path.exists(): return
    raw_pid = post.get("postId")
    pid_str = str(raw_pid).strip() if raw_pid is not None and raw_pid != "" else ""
    if pid_str:
        from .download_cache import _load_dl_history
        if pid_str in _load_dl_history(): return

    raw_body = (post.get("body") or post.get("plainBody") or "").strip()
    if raw_body.strip().lower() in ("moment uploaded.", ""):
        raw_body = ""
    body = _clean_post_body_text(raw_body)

    comments = []
    post_body_from_api = ""
    _fetch_pid = pid_str or str(post.get("postId", ""))
    if fetch_artist_comments:
        if force_comments or state.SAVE_COMMENTS:
            comments, post_body_from_api = fetch_comments(_fetch_pid)
            if not force_comments and not state.SAVE_COMMENTS:
                comments = []
        else:
            _, post_body_from_api = fetch_comments(_fetch_pid)

    if not body and post_body_from_api:
        body = post_body_from_api

    # External links (YouTube, etc.) embedded as snippet / official media metadata.
    urls: list[str] = []
    try:
        # Official-channel posts sometimes include "attachment.snippet" items (e.g. YouTube share).
        att = post.get("attachment") or {}
        snips = att.get("snippet") or {}
        if isinstance(snips, dict):
            for v in snips.values():
                if isinstance(v, dict):
                    u = (v.get("url") or "").strip()
                    if u:
                        urls.append(u)

        # Official Media tab posts can include extension.youtube.videoPath.
        ext = post.get("extension") or {}
        yt = ext.get("youtube") or {}
        if isinstance(yt, dict):
            u = (yt.get("videoPath") or "").strip()
            if u:
                urls.append(u)
    except Exception:
        urls = urls or []

    # De-dup while preserving order.
    if urls:
        seen: set[str] = set()
        urls = [u for u in urls if not (u in seen or seen.add(u))]

    if not body and not comments and not urls:
        return

    txt_path.parent.mkdir(parents=True, exist_ok=True)
    author = post.get("author", {})
    author_name = author.get("artistOfficialProfile", {}).get("officialName") or author.get("profileName") or state.COMMUNITY_NAME or "Unknown"
    pub_at = _format_post_header_ts(post.get("publishedAt", 0))

    lines = [f"Post ID   : {post.get('postId', '')}", f"Artist    : {author_name}", f"Date      : {pub_at}"]
    if weverse_url: lines.append(f"URL       : {weverse_url}")
    lines.append("\u2500" * 55)
    if body: lines.append(body)
    if comments:
        lines.append("\n" + "\u2500" * 19 + " Artist Comments " + "\u2500" * 19)
        # Build a set of comment IDs that belong to artist comments to
        # detect when an artist replied to their own comment.
        artist_comment_ids = {c["commentId"] for c in comments if c.get("commentId")}
        seen_parent_ids: set = set()
        for c in comments:
            parent = c.get("parent")
            if parent:
                pid = parent.get("commentId", "")
                if pid in artist_comment_ids:
                    # Artist replied to their own comment — just indent the reply,
                    # the parent is already rendered as a standalone artist comment.
                    lines.append(f"    \u2514 [{c['timestamp']}] {c['authorName']}: {c['body']}")
                else:
                    # Artist replied to a fan comment — show fan comment once then indent.
                    if pid not in seen_parent_ids:
                        seen_parent_ids.add(pid)
                        fan_name  = parent.get("fanName", "")
                        fan_ts    = parent.get("timestamp", "")
                        fan_label = f"{fan_name}: " if fan_name else ""
                        fan_time  = f"[{fan_ts}] " if fan_ts else ""
                        lines.append(f"  {fan_time}{fan_label}{parent['body']}")
                    lines.append(f"    \u2514 [{c['timestamp']}] {c['authorName']}: {c['body']}")
            else:
                lines.append(f"[{c['timestamp']}] {c['authorName']}: {c['body']}")

    if urls:
        lines.append("\n" + "\u2500" * 24 + " Links " + "\u2500" * 24)
        lines.extend(urls)

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"  [Text] Saved: {txt_path.name}")


def artist_post_url(community: str, post_id: str) -> str:
    return f"https://weverse.io/{community.lower()}/artist/{post_id}"

def moment_url(community: str, member_id: str, post_id: str) -> str:
    return f"https://weverse.io/{community.lower()}/moment/{member_id}/post/{post_id}"

def media_url(community: str, post_id: str) -> str:
    return f"https://weverse.io/{community.lower()}/media/{post_id}"

def official_post_url(community: str, post_id: str) -> str:
    return f"https://weverse.io/{community.lower()}/fanpost/{post_id}"

def live_url(community: str, post_id: str) -> str:
    return f"https://weverse.io/{community.lower()}/live/{post_id}"


def _find_first_value_by_keys(obj, keys: set[str]):
    """Depth-first search for the first value whose key is in keys."""
    try:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in keys and v:
                    return v
                found = _find_first_value_by_keys(v, keys)
                if found:
                    return found
        elif isinstance(obj, list):
            for it in obj:
                found = _find_first_value_by_keys(it, keys)
                if found:
                    return found
    except Exception:
        return None
    return None


def _get_chat_id(post_id: str) -> str | None:
    """
    Resolve the live chatId for a live post.

    We don't assume a fixed JSON shape; instead we fetch the post and
    search for a likely chat identifier field.
    """
    if not post_id:
        return None
    try:
        post = run_extr(make_extractor(), f"/post/v1.0/post-{post_id}?fieldSet=postV1", retries=3)
    except Exception:
        post = None
    if not post:
        return None

    val = _find_first_value_by_keys(post, {"chatId", "chat_id", "chatRoomId", "chatRoomID"})
    if val is None:
        return None
    return str(val)


def _fetch_chat_page(chat_id: str, cursor: str | None) -> tuple[list, str | None]:
    """Fetch one page of full (all) chat messages."""
    req = (
        f"/chat/v1.0/chat-{chat_id}/messages"
        f"?appId=be4d79eb8fc7bd008ee82c8ec4ff6fd4"
        f"&language=en&limit=50&os=WEB&platform=WEB&wpf=pc"
    )
    if cursor:
        req += f"&after={cursor.replace(',', '%2C')}"
    try:
        resp = run_extr(make_extractor(), req, retries=3)
        items = resp.get("data", []) if resp else []
        next_cursor = (
            resp.get("paging", {}).get("nextParams", {}).get("after")
            if resp else None
        )
        return items, next_cursor
    except Exception:
        return [], None


def _fetch_artist_chat_page(chat_id: str, cursor: str | None) -> tuple[list, str | None]:
    """Fetch one page of artist-only chat messages."""
    req = (
        f"/chat/v1.0/chat-{chat_id}/artistMessages"
        f"?appId=be4d79eb8fc7bd008ee82c8ec4ff6fd4"
        f"&language=en&limit=50&os=WEB&platform=WEB&wpf=pc"
    )
    if cursor:
        req += f"&after={cursor.replace(',', '%2C')}"
    try:
        resp = run_extr(make_extractor(), req, retries=3)
        items = resp.get("data", []) if resp else []
        next_cursor = (
            resp.get("paging", {}).get("nextParams", {}).get("after")
            if resp else None
        )
        return items, next_cursor
    except Exception:
        return [], None


def _format_chat_lines(messages: list) -> list[str]:
    """Convert a list of raw chat message dicts to formatted log lines."""
    from zoneinfo import ZoneInfo
    from .config import TIMEZONE
    import datetime as _dt
    try:
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = ZoneInfo("Asia/Seoul")

    lines = []
    for msg in messages:
        ts     = msg.get("messageTime", 0)
        dt     = _dt.datetime.fromtimestamp(ts / 1000, tz=tz)
        dt_str = dt.strftime("%B %d, %Y %I:%M %p")
        nick   = msg.get("profile", {}).get("profileName", "")
        text   = (msg.get("content") or "").strip()
        if nick or text:
            lines.append(f"{dt_str}  {nick}: {text}")
    return lines


def save_live_chat(post_id: str, output_dir: str, filename_stem: str):
    """
    Download and save the full live stream chat log.
    File: {filename_stem}_chat_all.txt
    Only runs when state.SAVE_TEXT is True.
    """
    if not state.SAVE_TEXT:
        return

    chat_path = Path(output_dir) / f"{filename_stem}_chat_all.txt"
    if chat_path.exists():
        return

    console.print(f"  [Chat] Fetching full chat log for {post_id}...")

    chat_id = _get_chat_id(post_id)
    if not chat_id:
        if state.DEBUG_MODE:
            console.print(f"  [Chat] No chatId found for {post_id} — skipping.")
        return

    all_messages: list[dict] = []
    cursor = None
    while True:
        items, cursor = _fetch_chat_page(chat_id, cursor)
        if not items:
            break
        all_messages.extend(items)
        if not cursor:
            break
        time.sleep(0.2)

    if not all_messages:
        if state.DEBUG_MODE:
            console.print(f"  [Chat] No messages found for {post_id}.")
        return

    all_messages.reverse()
    lines = _format_chat_lines(all_messages)
    if not lines:
        return

    chat_path.parent.mkdir(parents=True, exist_ok=True)
    chat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"  [Chat] Saved {len(lines)} messages: {chat_path.name}")


def save_live_artist_chat(post_id: str, output_dir: str, filename_stem: str):
    """
    Download and save only the artist messages from a live stream chat.
    File: {filename_stem}_chat_artist.txt
    Only runs when state.SAVE_TEXT is True.
    """
    if not state.SAVE_TEXT:
        return

    chat_path = Path(output_dir) / f"{filename_stem}_chat_artist.txt"
    if chat_path.exists():
        return

    chat_id = _get_chat_id(post_id)
    if not chat_id:
        return

    console.print(f"  [Chat] Fetching artist messages for {post_id}...")

    all_messages: list[dict] = []
    cursor = None
    while True:
        items, cursor = _fetch_artist_chat_page(chat_id, cursor)
        if not items:
            break
        all_messages.extend(items)
        if not cursor:
            break
        time.sleep(0.2)

    if not all_messages:
        if state.DEBUG_MODE:
            console.print(f"  [Chat] No artist messages found for {post_id}.")
        return

    all_messages.reverse()
    lines = _format_chat_lines(all_messages)
    if not lines:
        return

    chat_path.parent.mkdir(parents=True, exist_ok=True)
    chat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"  [Chat] Saved {len(lines)} artist messages: {chat_path.name}")