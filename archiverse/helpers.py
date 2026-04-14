"""
helpers.py
Pure helper functions: metadata extraction, filename building,
author name resolution, target matching, and screen utilities.
"""
import datetime
import os
import re
import shutil

from . import state
from pathlib import Path

from .utils import console, run_ffmpeg_with_progress


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def fix_surrogates(s) -> str:
    if not isinstance(s, str):
        return s
    try:
        return s.encode("utf-16", "surrogatepass").decode("utf-16")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def format_playtime_hhmmss(total_seconds) -> str:
    """
    Format API playTime (whole seconds) as HH:MM:SS for live/menu display.
    e.g. 3605 -> 01:00:05
    """
    if total_seconds is None:
        return ""
    try:
        s = int(round(float(total_seconds)))
    except (TypeError, ValueError):
        return ""
    if s < 0:
        s = 0
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def sanitise_surrogates(s: str) -> str:
    return "".join(ch for ch in s if not (0xD800 <= ord(ch) <= 0xDFFF))



def get_author_name(author: dict) -> str:
    """
    Resolve the display name for any Weverse profile type.
    """
    official = author.get("artistOfficialProfile", {})
    return (
        official.get("officialName")
        or author.get("profileName")
        or state.COMMUNITY_NAME
    )



def matches_target(name: str) -> bool:
    """
    Check whether 'name' (from the API) matches any entry in TARGET_ARTISTS.

    Returns True when TARGET_ARTISTS is None (no filter = match all).
    """
    if not state.TARGET_ARTISTS:
        return True
    name_lower = name.lower()
    for target in state.TARGET_ARTISTS:
        t = target.lower()
        if t in name_lower or name_lower in t:
            return True
    return False



def make_filename(artist: str, date, post_id: str, title: str = "", template_key: str = "default", tier: str = "") -> str:
    """
    Build a filename stem using the template defined in config.yaml.
    """
    from .config import FILENAME_TEMPLATES, DATE_FORMAT, DATE_FORMATS, DATE_SEP, TIME_SEP, TIER_BRACKET, POSTID_BRACKET
    import re as _re
    import datetime as _dt

    _key_fmt    = DATE_FORMATS.get(template_key)
    active_fmt  = _key_fmt if _key_fmt else DATE_FORMAT

    active_fmt = active_fmt.replace("%Y.%m.%d", f"%Y{DATE_SEP}%m{DATE_SEP}%d")
    active_fmt = active_fmt.replace("%H-%M-%S", f"%H{TIME_SEP}%M{TIME_SEP}%S")

    if isinstance(date, _dt.datetime):
        try:
            date_str = date.strftime(active_fmt)
            date_str = date_str.replace(":", "-")
        except Exception:
            date_str = date.strftime(f"%Y{DATE_SEP}%m{DATE_SEP}%d")
        try:
            date_dotted = date.strftime(active_fmt).replace(":", "-").replace("-", ".").replace(" ", ".")
        except Exception:
            date_dotted = date.strftime("%Y.%m.%d")
    else:
        date_str    = str(date)[:10]
        date_dotted = str(date)[:10].replace("-", ".")

    def _safe_part(val: str) -> str:
        s = "" if val is None else str(val)
        s = _re.sub(r"[\x00-\x1f]+", " ", s)
        s = _re.sub(r'[<>:"/\\|?*]+', "-", s)
        s = sanitise_surrogates(s)
        return " ".join(s.split()).strip()

    safe_title   = _safe_part(title) if title else ""
    safe_artist  = _safe_part(artist)
    safe_comm    = _safe_part(state.COMMUNITY_NAME)

    # Apply bracket styles
    _BRACKETS = {"square": ("[", "]"), "curly": ("{", "}"), "none": ("", "")}
    _tl, _tr   = _BRACKETS.get(TIER_BRACKET,   ("[", "]"))
    _pl, _pr   = _BRACKETS.get(POSTID_BRACKET, ("[", "]"))

    tier_fmt   = f"{_tl}{tier}{_tr}"   if tier    else ""
    post_id_fmt = f"{_pl}{post_id}{_pr}"

    template = FILENAME_TEMPLATES.get(template_key) or FILENAME_TEMPLATES.get("default", "{community} {artist} {date} [{post_id}]")

    result = template.format(
        community   = safe_comm,
        artist      = safe_artist,
        date        = date_str,
        date_dotted = date_dotted,
        post_id     = post_id_fmt,
        title       = safe_title,
        tier        = tier_fmt,
    )

    result = _re.sub(r"[\x00-\x1f]+", " ", result)
    result = " ".join(result.split()).strip()
    return result


def fix_metadata(item: dict) -> dict:
    """
    Extract display metadata from one liveTabPosts entry.
    """
    ext_block = item.get("extension", {})
    video     = ext_block.get("video", {})
    media     = ext_block.get("mediaInfo", {})

    title    = fix_surrogates(media.get("title") or "No Title")
    on_air   = video.get("onAirStartAt")
    from .config import TIMEZONE
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = ZoneInfo("Asia/Seoul")

    if on_air:
        date_dt = datetime.datetime.fromtimestamp(float(on_air) / 1000.0, tz=tz)
    else:
        date_dt = datetime.datetime(1, 1, 1, tzinfo=tz)  # sentinel for unknown date

    artist_name = get_author_name(item.get("author", {}))
    item_id     = item.get("postId") or video.get("videoId")

    play_seconds = None
    thumbs = item.get("summary", {}).get("thumbnails") or []
    if isinstance(thumbs, list):
        for t in thumbs:
            if not isinstance(t, dict):
                continue
            if t.get("type") == "VIDEO" and t.get("playTime") is not None:
                play_seconds = t.get("playTime")
                break
        if play_seconds is None:
            for t in thumbs:
                if isinstance(t, dict) and t.get("playTime") is not None:
                    play_seconds = t.get("playTime")
                    break

    return {
        "title":         title,
        "date":          date_dt,
        "date_str":      date_dt.strftime("%Y-%m-%d") if on_air else "0000-00-00",
        "is_membership": bool(video.get("membershipOnly", False)),
        "video_id":      video.get("videoId"),
        "artist":        artist_name,
        "id":            str(item_id),
        "duration_str":  format_playtime_hhmmss(play_seconds),
    }


def get_filtered_items(resp_data: dict) -> list:
    """
    Extracts items from an API response and filters by TARGET_ARTISTS and tier
    (SKIP_MEMBERSHIP / SKIP_PUBLIC).
    """
    items = resp_data.get("data", [])

    filtered = []
    for item in items:
        is_mem = (
            item.get("membershipOnly", False)
            or item.get("extension", {}).get("video", {}).get("membershipOnly", False)
        )
        if is_mem and state.SKIP_MEMBERSHIP:
            continue
        if not is_mem and state.SKIP_PUBLIC:
            continue
        if state.TARGET_ARTISTS:
            author_name = get_author_name(item.get("author", {}))
            if not any(t.lower() == author_name.lower() for t in state.TARGET_ARTISTS):
                continue
        filtered.append(item)
    return filtered

def mux_media_with_subtitles(video_path: Path, sub_list: list, ffmpeg_bin: str = "ffmpeg"):
    """
    Bulletproof muxer: Handles emojis, uses absolute paths, and logs exact FFmpeg errors.
    """
    if not video_path.exists():
        print(f"  [Mux Error] Source video not found: {video_path}")
        return None

    # 1. Force absolute paths to avoid working directory confusion
    video_abs = video_path.resolve()
    output_abs = video_abs.with_suffix(".mkv")
    
    # 2. Verify FFmpeg actually exists on the system
    actual_ffmpeg = shutil.which(ffmpeg_bin)
    if not actual_ffmpeg:
        print(f"  [Mux Error] FFmpeg executable not found! Please check your system PATH or BINARIES config.")
        return None

    # Build the command
    cmd = [actual_ffmpeg, "-y", "-i", str(video_abs)]
    
    for sub in sub_list:
        sub_abs = Path(sub["path"]).resolve()
        cmd.extend(["-i", str(sub_abs)])
        
    cmd.extend(["-map", "0:v", "-map", "0:a?"])
    
    for i, sub in enumerate(sub_list):
        cmd.extend(["-map", f"{i+1}:s"])
        lang = sub["lang"].split("_")[0]
        cmd.extend([f"-metadata:s:s:{i}", f"language={lang}"])

    cmd.extend(["-c:v", "copy", "-c:a", "copy", "-c:s", "srt", str(output_abs)])

    try:
        rc, err = run_ffmpeg_with_progress(
            cmd,
            duration_source=video_abs,
            description=f"Muxing {video_abs.name}",
        )
        if rc == 0:
            console.print(f"  [Mux] Successfully created MKV: {output_abs.name}")
            video_abs.unlink(missing_ok=True)
            for sub in sub_list:
                Path(sub["path"]).resolve().unlink(missing_ok=True)
            return output_abs
        console.print(f"  [FFmpeg Error] Process failed with code {rc}")
        err = (err or "").strip()
        if err:
            console.print(f"  [FFmpeg Details] {err[:2000]}")
        return None
    except Exception as e:
        console.print(f"  [Mux Exception] A critical error occurred: {e}")
        return None


def mux_subtitles_into_video(
    video_path: Path,
    subtitle_entries: list[dict],
    ffmpeg_bin: str = "ffmpeg",
) -> Path | None:
    """
    Embed subtitle files into an existing video container (.mp4 or .mkv).

    Used for optional ongoing-live subtitle muxing after Streamlink finishes.
    """
    if not video_path.exists():
        console.print(f"  [Mux Error] Source video not found: {video_path}")
        return None
    if not subtitle_entries:
        return None

    video_abs = video_path.resolve()
    ext = video_abs.suffix.lower()
    subtitle_codec = "mov_text" if ext == ".mp4" else "srt"

    actual_ffmpeg = shutil.which(ffmpeg_bin) or ffmpeg_bin
    if not actual_ffmpeg:
        console.print(
            "  [Mux Error] FFmpeg executable not found! "
            "Please check your system PATH or BINARIES config."
        )
        return None

    out_tmp = video_abs.with_name(f"{video_abs.stem}.tmp{video_abs.suffix}")

    cmd: list[str] = [actual_ffmpeg, "-y", "-i", str(video_abs)]
    for sub in subtitle_entries:
        cmd.extend(["-i", str(Path(sub["path"]).resolve())])

    cmd.extend(["-map", "0:v", "-map", "0:a?"])
    for i, sub in enumerate(subtitle_entries):
        cmd.extend(["-map", f"{i+1}:0"])
        lang = str(sub.get("lang") or "und").split("_")[0].split("-")[0]
        cmd.extend([f"-metadata:s:s:{i}", f"language={lang}"])

    cmd.extend(["-c:v", "copy", "-c:a", "copy", "-c:s", subtitle_codec, str(out_tmp)])

    rc, err = run_ffmpeg_with_progress(
        cmd,
        duration_source=video_abs,
        description=f"Muxing subtitles → {video_abs.name}",
    )

    if rc == 0 and out_tmp.exists():
        try:
            video_abs.unlink(missing_ok=True)
        except Exception:
            pass
        out_tmp.rename(video_abs)
        return video_abs

    if err:
        console.print(f"  [FFmpeg Error] Subtitle mux failed: {err.strip()[:800]}")
    else:
        console.print("  [FFmpeg Error] Subtitle mux failed (no stderr captured).")
    return None
        
        
def sanitise(name: str) -> str:
    """Strip characters that are illegal in Windows filenames."""
    return re.sub(r'[<>:"/\\|?*]', "-", name).strip()