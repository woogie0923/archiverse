"""
downloader.py
Download orchestration (DRM VOD, live VOD URLs, cvideo, official neonplayer).

Persistent JSON caches and download history live in download_cache.py; this
module re-exports mark_downloaded / is_already_downloaded for compatibility.
"""
import json
import re
import subprocess
import urllib.parse
import urllib.request 
import shutil
import uuid
from pathlib import Path

import requests
import xmltodict
from pywidevine import PSSH, Device, Cdm

import utils
from utils import console
import state
from config import COMMON_HEADERS, BINARIES, WVD_DEVICE_PATH
from api import make_extractor, run_extr
from helpers import mux_media_with_subtitles
from download_cache import (
    _get_logged_command,
    _load_drm_keys,
    _load_video_url_cache,
    _log_n_m3u8dl_command,
    _save_drm_key,
    _save_video_url,
    _video_url_cache_path,
    is_already_downloaded,
    mark_downloaded,
)


def get_safe_int(obj: dict, key: str, default: int = 0) -> int:
    try:
        return int(obj.get(key, default))
    except (TypeError, ValueError):
        return default


def _embed_thumbnail_drm(video_path: Path, thumb_url: str):
    """
    Download a thumbnail and embed it into an MKV file using mkvpropedit.

    mkvpropedit attaches the image in-place without re-encoding or creating
    a second file — much faster and more reliable for MKV than the ffmpeg
    -disposition:v:1 approach.

    Command:
      mkvpropedit video.mkv
        --attachment-mime-type image/jpeg
        --attachment-name cover.jpg
        --add-attachment cover.jpg
    """
    if not thumb_url or not video_path.exists():
        return
    try:
        mkvpropedit = BINARIES.get("mkvpropedit", "mkvpropedit")
        thumb_stem  = video_path.parent / f"{video_path.stem}_cover"
        utils.download_file(thumb_url, str(thumb_stem))
        thumb_matches = [
            f for f in video_path.parent.iterdir()
            if f.name.startswith(thumb_stem.name + ".")
        ]
        if not thumb_matches:
            console.print("  [Thumbnail] Download failed, skipping embed.")
            return
        thumb_path = thumb_matches[0]

        cmd = [
            mkvpropedit, str(video_path),
            "--attachment-mime-type", "image/jpeg",
            "--attachment-name", "cover.jpg",
            "--add-attachment", str(thumb_path),
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            console.print(f"  [Thumbnail] Embedded into {video_path.name}")
        else:
            console.print(f"  [Thumbnail] mkvpropedit failed: {result.stderr.decode()[:200]}")
        if thumb_path.exists():
            thumb_path.unlink()
    except Exception as e:
        console.print(f"  [Thumbnail] Error embedding thumbnail: {e}")


def download_drm_video(
    post_id: str,
    save_path: str,
    thumb_url: str = "",
    weverse_url: str = "",
    title: str = "",
    created_at=None,
):
    """
    Download a Widevine-encrypted video using the CDM and N_m3u8DL-RE.

    Flow:
      1. Fetch full post to get videoId / infraVideoId
      2. POST to /video/v1.2/vod/{videoId}/inKey to get inKey + licenseUrl
      3. Fetch the neonplayer MPD URL
      4. Extract PSSH from the MPD manifest
      5. Obtain content keys via CDM (skipped if keys are already cached)
      6. Run N_m3u8DL-RE with --key flags
      7. Embed thumbnail via ffmpeg if thumb_url is provided
         (N_m3u8DL-RE strips [ ] from filenames, so we must use
         safe_name — not save_path.name — to locate the output file)
    """
    console.print(f"  [DRM] Initiating encrypted download for {post_id}...")
    try:
        # 1. Fetch post details
        post_res    = run_extr(make_extractor(), f"/post/v1.0/post-{post_id}?fieldSet=postV1")
        is_membership = post_res.get("membershipOnly", False)
        video_data  = post_res["extension"]["video"]
        video_id    = video_data["videoId"]
        infra_id    = video_data["infraVideoId"]

        # 5a. Check DRM key cache before doing any license request
        _drm_cache_entry = _load_drm_keys().get(str(video_id))
        if _drm_cache_entry:
            # Support both old format (plain list) and new format (dict with keys + infra_id)
            if isinstance(_drm_cache_entry, list):
                cached_keys = _drm_cache_entry
            else:
                cached_keys = _drm_cache_entry.get("keys", [])
        else:
            cached_keys = None
        if cached_keys:
            console.print(f"  [DRM] Using cached keys for video {video_id}.")
            all_keys = cached_keys
            # Still need the MPD URL for N_m3u8DL-RE — fetch inKey and playback
            key_res = run_extr(
                make_extractor(),
                f"/video/v1.2/vod/{video_id}/inKey?drm=Widevine&securityLevelByTrack=true",
                post=True,
            )
            in_key = key_res.get("inKey") if key_res else None
            if not in_key:
                console.print(f"  [DRM] inKey fetch failed for post {post_id} — trying logged command fallback...")
                logged_cmd = _get_logged_command(post_id)
                if not logged_cmd:
                    console.print(
                        f"  [DRM] No logged command found for post {post_id}.\n"
                        f"  [DRM] Decryption keys are cached in drm_keys.json.\n"
                        f"  [DRM] Refresh your auth_token and re-run to download this video."
                    )
                    return

                # Extract the MPD URL (first quoted token after the binary name)
                import shlex as _shlex
                try:
                    tokens = _shlex.split(logged_cmd)
                except Exception:
                    tokens = logged_cmd.split()

                if len(tokens) < 2:
                    console.print(f"  [DRM] Logged command could not be parsed.")
                    return

                mpd_url_logged = tokens[1].strip('"').strip("'")

                # Build a fresh command: reuse the MPD URL and --key values from the
                # logged command, but use a new temp save-name and the current save_dir.
                save_obj = Path(save_path)
                base_dir = save_obj.parent
                base_dir.mkdir(parents=True, exist_ok=True)

                display_name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "", save_obj.name)
                display_name = "".join(ch for ch in display_name if not (0xD800 <= ord(ch) <= 0xDFFF))
                display_name = " ".join(display_name.split()).strip(".")

                import uuid as _uuid
                temp_dl_name = f"wv_drm_{_uuid.uuid4().hex[:12]}"

                n_m3u8 = BINARIES.get("n_m3u8dl_re", "N_m3u8DL-RE")
                cmd_parts = [
                    n_m3u8, f'"{mpd_url_logged}"',
                    "-sv", "best", "-sa", "best", "-ss", "all", "-mt",
                    "--save-dir", f'"{base_dir}"',
                    "--save-name", f'"{temp_dl_name}"',
                    "-M", "format=mkv:muxer=mkvmerge",
                    "--use-shaka-packager",
                ]
                for k in all_keys:
                    cmd_parts.extend(["--key", k])

                full_command = " ".join(cmd_parts)
                console.print(f"  [DRM] Destination: {base_dir}")
                console.print(f"  [DRM] Output file: {display_name}")
                console.print(f"  [DRM] Command (from log): {full_command}")
                _log_n_m3u8dl_command(full_command, post_id)
                subprocess.run(full_command, shell=True, check=True)

                # Locate the temp output and rename to the display name
                temp_file = base_dir / f"{temp_dl_name}.mkv"
                if not temp_file.exists():
                    temp_file = base_dir / f"{temp_dl_name}.mp4"
                if not temp_file.exists():
                    console.print(f"  [DRM Error] Output file not found after log-fallback download.")
                    return
                output_ext  = temp_file.suffix
                output_file = base_dir / f"{display_name}{output_ext}"
                temp_file.rename(output_file)
                if output_file.exists():
                    if thumb_url:
                        _embed_thumbnail_drm(output_file, thumb_url)
                    if weverse_url:
                        from text_writer import embed_url_metadata
                        embed_url_metadata(str(output_file), weverse_url, title=title)
                    # Set Windows "Created" timestamp. Prefer provided created_at,
                    # otherwise fall back to parsing YYYY-MM-DD from filename.
                    try:
                        if created_at:
                            utils.edit_creation_date(str(output_file), created_at)
                        else:
                            import re as _re, datetime as _dt
                            _dm = _re.search(r'(\d{4}-\d{2}-\d{2})', save_obj.name)
                            if _dm:
                                _file_date = _dt.datetime.strptime(_dm.group(1), "%Y-%m-%d")
                                utils.edit_creation_date(str(output_file), _file_date)
                    except Exception:
                        pass
                return
            playback_api = (
                f"https://apis.naver.com/neonplayer/vodplay/v3/playback/{infra_id}"
                f"?key={in_key}&sid=2070&devt=html5_pc&prv=N&lc=en&cpl=en"
                f"&adi=%5B%7B%22adSystem%22%3A%22null%22%7D%5D&adu=%2F&drm=Widevine"
            )
            playback_res = requests.get(playback_api, headers=COMMON_HEADERS)
            data = (
                xmltodict.parse(playback_res.text)
                if playback_res.text.strip().startswith("<")
                else playback_res.json()
            )
            mpd_root = data.get("MPD", {})
            if isinstance(mpd_root, list): mpd_root = mpd_root[0]
            period = mpd_root.get("Period", {})
            if isinstance(period, list): period = period[0]
            mpd_url = period.get("href") or period.get("@xlink:href")
        else:
            # 2. Get DRM inKey and License URL
            key_res = run_extr(
                make_extractor(),
                f"/video/v1.2/vod/{video_id}/inKey?drm=Widevine&securityLevelByTrack=true",
                post=True,
            )
            in_key  = key_res.get("inKey")
            lic_url = key_res.get("licenseUrl")

            if not in_key:
                console.print(f"  [Error] No inKey returned for {post_id}.")
                return

            # 3. Fetch MPD URL from neonplayer
            playback_api = (
                f"https://apis.naver.com/neonplayer/vodplay/v3/playback/{infra_id}"
                f"?key={in_key}&sid=2070&devt=html5_pc&prv=N&lc=en&cpl=en"
                f"&adi=%5B%7B%22adSystem%22%3A%22null%22%7D%5D&adu=%2F&drm=Widevine"
            )
            playback_res = requests.get(playback_api, headers=COMMON_HEADERS)
            data = (
                xmltodict.parse(playback_res.text)
                if playback_res.text.strip().startswith("<")
                else playback_res.json()
            )
            mpd_root = data.get("MPD", {})
            if isinstance(mpd_root, list): mpd_root = mpd_root[0]
            period = mpd_root.get("Period", {})
            if isinstance(period, list): period = period[0]
            mpd_url = period.get("href") or period.get("@xlink:href")

            # 4. Extract PSSH from the MPD manifest
            mpd_content = requests.get(mpd_url).text
            pssh_list   = re.findall(r"<cenc:pssh[^>]*>(.*?)</cenc:pssh>", mpd_content)

            # 5. Obtain content keys via CDM
            cdm        = Cdm.from_device(Device.load(WVD_DEVICE_PATH))
            session_id = cdm.open()
            all_keys: list[str] = []
            for pssh_val in set(pssh_list):
                try:
                    challenge = cdm.get_license_challenge(session_id, PSSH(pssh_val))
                    lic_res   = requests.post(lic_url, data=challenge, headers=COMMON_HEADERS)
                    if lic_res.status_code == 200:
                        cdm.parse_license(session_id, lic_res.content)
                except Exception:
                    continue
            for k in cdm.get_keys(session_id):
                if k.type == "CONTENT":
                    all_keys.append(f"{k.kid.hex}:{k.key.hex()}")
            cdm.close(session_id)

            # 5b. Cache the keys for future downloads of the same video
            if all_keys:
                _save_drm_key(str(video_id), all_keys, infra_id=str(infra_id))
                console.print(f"  [DRM] Keys cached for video {video_id}.")

        # 6. Build save directory and sanitise filename
        save_obj = Path(save_path)
        base_dir = save_obj.parent
        base_dir.mkdir(parents=True, exist_ok=True)

        # Full display name — strip only Windows-illegal chars and lone surrogates,
        # keeping emojis and all valid Unicode.
        display_name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "", save_obj.name)
        display_name = "".join(ch for ch in display_name if not (0xD800 <= ord(ch) <= 0xDFFF))
        display_name = " ".join(display_name.split()).strip(".")

        # N_m3u8DL-RE is passed an ASCII-safe temp name to avoid any shell/tool
        # emoji handling issues.  We rename to display_name after download.
        import uuid as _uuid
        temp_dl_name = f"wv_drm_{_uuid.uuid4().hex[:12]}"

        # 7. Execute N_m3u8DL-RE
        n_m3u8 = BINARIES.get("n_m3u8dl_re", "N_m3u8DL-RE")
        cmd_parts = [
            n_m3u8, f'"{mpd_url}"',
            "-sv", "best", "-sa", "best", "-ss", "all", "-mt",
            "--save-dir", f'"{base_dir}"',
            "--save-name", f'"{temp_dl_name}"',
            "-M", "format=mkv:muxer=mkvmerge",
            "--use-shaka-packager",
        ]
        for k in all_keys:
            cmd_parts.extend(["--key", k])

        full_command = " ".join(cmd_parts)
        console.print(f"  [DRM] Destination: {base_dir}")
        console.print(f"  [DRM] Output file: {display_name}")
        console.print(f"  [DRM] Command: {full_command}")
        _log_n_m3u8dl_command(full_command, post_id)
        subprocess.run(full_command, shell=True, check=True)

        # Locate the temp output and rename to the display name
        temp_file = base_dir / f"{temp_dl_name}.mkv"
        if not temp_file.exists():
            temp_file = base_dir / f"{temp_dl_name}.mp4"

        if not temp_file.exists():
            console.print(f"  [DRM Error] Output file not found after download.")
            return

        output_ext  = temp_file.suffix
        output_file = base_dir / f"{display_name}{output_ext}"
        temp_file.rename(output_file)

        if output_file.exists():
            # Embed thumbnail
            if thumb_url:
                _embed_thumbnail_drm(output_file, thumb_url)
            # Embed Weverse URL into MKV/MP4 comment metadata
            if weverse_url:
                from text_writer import embed_url_metadata
                embed_url_metadata(str(output_file), weverse_url, title=title)
            # Set Windows "Created" timestamp. Prefer provided created_at,
            # otherwise fall back to parsing YYYY-MM-DD from filename.
            try:
                if created_at:
                    utils.edit_creation_date(str(output_file), created_at)
                else:
                    import re as _re, datetime as _dt
                    _dm = _re.search(r'(\d{4}-\d{2}-\d{2})', save_obj.name)
                    if _dm:
                        _file_date = _dt.datetime.strptime(_dm.group(1), "%Y-%m-%d")
                        utils.edit_creation_date(str(output_file), _file_date)
            except Exception:
                pass

    except Exception as e:
        console.print(f"  [DRM Error] {post_id}: {e}")


def get_vod_url(video_id: str):
    """
    Fetch the highest-quality MP4 URL and subtitle list for a live VOD.
    Returns (video_url, subtitles, thumb_url) where subtitles is a list of
    {'url': ..., 'lang': ...} dicts.
    """
    # Check video URL cache first (supports both legacy str and new dict format)
    _cached = _load_video_url_cache().get(str(video_id))
    if _cached:
        if isinstance(_cached, dict) and _cached.get("url"):
            console.print(f"  [VOD URL] Using cached URL+subs for {video_id}.")
            return _cached.get("url"), (_cached.get("subtitles") or []), (_cached.get("thumb_url") or "")
        # Legacy cache stored only the URL string; we must refresh once to recover subtitles.
        if isinstance(_cached, str):
            console.print(f"  [VOD URL] Cached URL is legacy-only for {video_id}; refreshing for subtitles.")

    req = f"/video/v2.1/vod/{video_id}/playInfo?version=v3"

    if state.DEBUG_MODE:
        console.print(f"  [API Access] -> {req}")

    data     = run_extr(make_extractor(), req)
    xml_data = xmltodict.parse(data["playback"])
    mpd      = xml_data.get("MPD", {})
    period   = mpd.get("Period", {})

    adaptation_sets = period.get("AdaptationSet", [])
    if isinstance(adaptation_sets, dict):
        adaptation_sets = [adaptation_sets]

    video_set = next(
        (s for s in adaptation_sets if s.get("@mimeType") == "video/mp4"), None
    )
    if not video_set:
        return None, [], ""
    reps = video_set.get("Representation", [])
    if isinstance(reps, dict): reps = [reps]
    video_url = sorted(reps, key=lambda v: int(v["@bandwidth"]), reverse=True)[0]["BaseURL"]
    console.print(f"  [VOD URL] -> {video_url[:80]}...")

    subtitles: list[dict] = []
    supp_prop = period.get("SupplementalProperty", {})
    sub_set   = supp_prop.get("nvod:SubtitleSet", {})
    sub_list  = sub_set.get("nvod:Subtitle", [])
    if isinstance(sub_list, dict): sub_list = [sub_list]

    for s in sub_list:
        vtt_url = s.get("nvod:Source", {}).get("#text")
        if vtt_url:
            import re as _re
            match = _re.search(r"([a-z]{2}_[A-Z]{2})", vtt_url)
            lang  = match.group(1) if match else s.get("@lang", "und")
            if lang.startswith("in_"): lang = "id_ID"
            subtitles.append({"url": vtt_url, "lang": lang})

    if subtitles:
        console.print(f"  [Found {len(subtitles)} subtitles]")
    else:
        console.print("  [No subtitles found in XML]")

    thumb_url = ""
    try:
        summary_node = supp_prop.get("nvod:Summary", {})
        cover_node   = summary_node.get("nvod:Cover", {})
        if isinstance(cover_node, dict):
            thumb_url = cover_node.get("#text", "")
        elif isinstance(cover_node, str):
            thumb_url = cover_node
    except Exception:
        thumb_url = ""

    if thumb_url:
        console.print(f"  [Thumbnail] -> {thumb_url[:80]}...")

    # Cache full metadata so future runs still mux subtitles.
    try:
        p = _video_url_cache_path()
        if p is not None:
            store = _load_video_url_cache()
            store[str(video_id)] = {
                "url": video_url,
                "subtitles": subtitles,
                "thumb_url": thumb_url,
            }
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Fallback to legacy URL-only cache on any failure.
        _save_video_url(str(video_id), video_url)

    return video_url, subtitles, thumb_url


def get_live_hls_url(video_id: str) -> tuple[str | None, bool]:
    """
    Resolve the best live HLS playlist URL for an ongoing livestream.

    Returns (hls_url, is_drm_like). The DRM heuristic mirrors the standalone
    recorder: it checks whether the resolved mediaInfo contains an `aes`
    marker.
    """
    req = (
        f"/video/v1.3/lives/{video_id}/playInfo"
        f"?preview.format=json&preview.version=v2"
    )
    if state.DEBUG_MODE:
        console.print(f"  [Live API] -> {req}")

    try:
        data = run_extr(make_extractor(), req, retries=3)
    except Exception as e:
        # Optional: if access token expired and refresh token exists,
        # refresh and retry once.
        try:
            from weverse_auth import get_refresh_token, get_access_token
            if get_refresh_token():
                console.print("  [Live API] Access token error; refreshing token and retrying...")
                get_access_token(min_valid_seconds=0)
                data = run_extr(make_extractor(), req, retries=3)
            else:
                data = None
        except Exception:
            data = None
    if not data:
        return None, False

    lip_playback = data.get("lipPlayback")
    if not lip_playback:
        return None, False

    try:
        video_info = json.loads(lip_playback)
    except Exception:
        return None, False

    media_infos = [
        info for info in (video_info.get("media", []) or [])
        if info.get("protocol") == "HLS" and info.get("path")
    ]
    if not media_infos:
        return None, False

    # Choose first HLS stream (mirrors recorder script).
    media_info = media_infos[0]
    is_drm_like = "aes" in media_info
    hls_url = media_info.get("path")
    console.print(f"  [Live HLS] -> {str(hls_url)[:80]}...")
    return hls_url, is_drm_like


def record_ongoing_live_nm3u8dlre(
    hls_url: str,
    output_dir: str,
    save_name: str,
    is_drm_like: bool,
    created_at=None,
    live_wait_time: int = 5,
    live_take_count: int = 100000,
    thread_count: int = 5,
    http_request_timeout: int = 10,
    subtitle_langs: str = "eng|kor",
    output_format: str = "mp4",
) -> Path | None:
    """
    Record an ongoing livestream using N_m3u8DL-RE live mode.

    Returns the path to the final muxed file, or None on failure.
    """
    if not hls_url:
        return None

    n_m3u8 = BINARIES.get("n_m3u8dl_re", "N_m3u8DL-RE")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Capture a before snapshot to help us identify the new output file.
    before = {p for p in out_dir.iterdir() if p.is_file() and p.name.startswith(save_name)}

    if is_drm_like:
        # Optional: refresh access token just before DRM/live capture.
        try:
            from weverse_auth import get_refresh_token, get_access_token
            if get_refresh_token():
                get_access_token(min_valid_seconds=0)
        except Exception:
            pass

    cmd = [
        n_m3u8,
        "--save-dir",
        str(out_dir),
        "--save-name",
        save_name,
        "--log-level",
        "INFO",
        "-sv",
        "best",
        "-sa",
        "best",
        "-ss",
        f'lang="{subtitle_langs}":for=all',
        "--binary-merge",
        "True",
        "--http-request-timeout",
        str(http_request_timeout),
        "--thread-count",
        str(thread_count),
        "--live-take-count",
        str(live_take_count),
        "--live-wait-time",
        str(live_wait_time),
        "--live-real-time-merge",
        "True",
        "--custom-range",
        "0-",
        "--download-retry-count",
        "7",
        "--mux-after-done",
        f"format={output_format}",
        "--live-keep-segments",
        "-H",
        f"user-agent: {COMMON_HEADERS.get('User-Agent', '')}",
        "-H",
        "Accept: */*",
        "-H",
        f"Origin: {COMMON_HEADERS.get('Origin', 'https://weverse.io')}",
        "-H",
        f"Referer: {COMMON_HEADERS.get('Referer', 'https://weverse.io/')}",
    ]

    if is_drm_like:
        # COMMON_HEADERS.Authorization already includes the "Bearer ..." prefix.
        auth_val = COMMON_HEADERS.get("Authorization", "")
        if auth_val:
            cmd.extend(["-H", f"Authorization: {auth_val}"])

    cmd.append(hls_url)

    console.print(f"  [Live Record] Running N_m3u8DL-RE: {save_name}")
    try:
        # Avoid capturing stdout/stderr for long-running recordings; it can
        # grow very large and consume memory.
        result = subprocess.run(cmd)
    except Exception as e:
        console.print(f"  [Live Record Error] {e}")
        return None

    if result.returncode != 0:
        console.print(f"  [Live Record Error] N_m3u8DL-RE failed with code {result.returncode}")
        return None

    # Identify the final output file created by this run.
    candidates = [
        p for p in out_dir.iterdir()
        if p.is_file()
        and p.name.startswith(save_name)
        and p not in before
        and p.suffix.lower() in (".mp4", ".mkv", ".mov")
    ]

    if not candidates:
        # Fallback: sometimes the output file existed with the same name
        # if previous runs partially succeeded.
        candidates = [
            p for p in out_dir.iterdir()
            if p.is_file()
            and p.name.startswith(save_name)
            and p.suffix.lower() in (".mp4", ".mkv", ".mov")
        ]

    if not candidates:
        return None

    # Choose newest by mtime.
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    output_file = candidates[0]

    if created_at:
        try:
            utils.edit_creation_date(str(output_file), created_at)
        except Exception:
            pass

    return output_file


def _streamlink_list_streams(hls_url: str) -> dict:
    """
    Return Streamlink's discovered stream map for a URL via --json.
    """
    streamlink = BINARIES.get("streamlink", "streamlink")
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
    )
    auth = COMMON_HEADERS.get("Authorization", "")
    cmd = [
        streamlink,
        "--json",
        "--http-header",
        f"User-Agent={ua}",
        "--http-header",
        "Referer=https://weverse.io/",
    ]
    if auth:
        cmd.extend(["--http-header", f"Authorization={auth}"])
    cmd.append(hls_url)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode != 0:
            return {}
        data = json.loads(res.stdout or "{}")
        return data.get("streams", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


_streamlink_help_cache: str | None = None


def _streamlink_supports(flag: str) -> bool:
    """Return True if the installed Streamlink supports a CLI flag."""
    global _streamlink_help_cache
    if _streamlink_help_cache is None:
        streamlink = BINARIES.get("streamlink", "streamlink")
        try:
            res = subprocess.run(
                [streamlink, "--help"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            _streamlink_help_cache = (res.stdout or "") + "\n" + (res.stderr or "")
        except Exception:
            _streamlink_help_cache = ""
    return flag in (_streamlink_help_cache or "")


def record_ongoing_live_streamlink(
    hls_url: str,
    output_path: Path,
) -> Path | None:
    """
    Record an ongoing livestream using Streamlink.

    Uses quality selection logic:
      - prefer 1080p_alt if present
      - else best
    """
    if not hls_url:
        return None

    streamlink = BINARIES.get("streamlink", "streamlink")
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Streamlink writes raw stream bytes; the output extension does not
    # guarantee the container. Record to a temp transport stream and remux
    # to a real Matroska file afterward.
    temp_path = output_path.with_suffix(".ts")

    streams = _streamlink_list_streams(hls_url)
    quality = "1080p_alt" if "1080p_alt" in streams else "best"

    auth = COMMON_HEADERS.get("Authorization", "")
    cmd: list[str] = [
        streamlink,
        "--http-header",
        f"User-Agent={ua}",
        "--http-header",
        "Referer=https://weverse.io/",
        "--http-header",
        f"Authorization={auth}" if auth else "",
        "--stream-timeout",
        "120",
        "-o",
        str(temp_path),
        "--hls-live-restart",
        hls_url,
        quality,
    ]
    # Remove empty auth header token if missing
    cmd = [c for c in cmd if c != ""]

    # Streamlink option compatibility:
    # - Newer versions: --stream-segmented-queue-deadline
    # - Older versions: --hls-segment-queue-threshold
    if _streamlink_supports("--stream-segmented-queue-deadline"):
        cmd[1:1] = ["--stream-segmented-queue-deadline", "15"]
    elif _streamlink_supports("--hls-segment-queue-threshold"):
        cmd[1:1] = ["--hls-segment-queue-threshold", "15"]

    console.print(f"  [Live Record] Streamlink quality={quality}")
    try:
        res = subprocess.run(cmd)
        if res.returncode != 0 or not temp_path.exists():
            return None
    except Exception as e:
        console.print(f"  [Live Record Error] Streamlink failed: {e}")
        return None

    # Remux into a proper MKV so mkvpropedit works reliably.
    ffmpeg = BINARIES.get("ffmpeg", "ffmpeg")
    remux_tmp = output_path.with_name(f"{output_path.stem}.tmp.mkv")
    cmd_remux = [ffmpeg, "-y", "-i", str(temp_path), "-c", "copy", "-f", "matroska", str(remux_tmp)]
    try:
        r2 = subprocess.run(cmd_remux, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r2.returncode != 0 or not remux_tmp.exists():
            console.print(f"  [Live Record Error] FFmpeg remux failed: {(r2.stderr or '')[:300]}")
            return None
        if output_path.exists():
            output_path.unlink()
        remux_tmp.rename(output_path)
        try:
            temp_path.unlink()
        except Exception:
            pass
        return output_path
    except Exception as e:
        console.print(f"  [Live Record Error] Remux error: {e}")
        return None


def download_ongoing_live_subtitles_nm3u8dlre(
    hls_url: str,
    output_dir: Path,
    save_name: str,
    subtitle_langs: str = "kor|eng",
    live_take_count: int = 500,
    live_wait_time: int = 4,
) -> bool:
    """
    Use N_m3u8DL-RE to download subtitles for an ongoing live.

    This follows the command you provided, but pins output dir/name so
    files land next to the recording.
    """
    if not hls_url:
        return False

    n_m3u8 = BINARIES.get("n_m3u8dl_re", "N_m3u8DL-RE")
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        n_m3u8,
        "--save-dir",
        str(output_dir),
        "--save-name",
        f"{save_name}_subs",
        "-ss",
        f'lang="{subtitle_langs}":for=all',
        "--thread-count",
        "5",
        "--live-take-count",
        str(live_take_count),
        "--live-wait-time",
        str(live_wait_time),
        "--custom-range",
        "0-",
        "--download-retry-count",
        "7",
        "-H",
        "Accept: */*",
        "-H",
        "Origin: https://weverse.io/",
        "-H",
        "Referer: https://weverse.io/",
        "-H",
        f"User-Agent: {COMMON_HEADERS.get('User-Agent', '')}",
        "--live-keep-segments",
    ]

    auth = COMMON_HEADERS.get("Authorization", "")
    if auth:
        cmd.extend(["-H", f"Authorization: {auth}"])

    cmd.append(hls_url)

    console.print("  [Live Subs] Downloading subtitles via N_m3u8DL-RE...")
    try:
        res = subprocess.run(cmd)
        return res.returncode == 0
    except Exception as e:
        console.print(f"  [Live Subs Error] {e}")
        return False


def download_cvideo(video_id, path: str, date=None):
    vid_str = str(video_id)
    video_path = Path(path)
    ffmpeg_exe = BINARIES.get("ffmpeg", "ffmpeg")

    if vid_str.isdigit():
        req = f"/video/v2.1/vod/{vid_str}/playInfo?version=v3"
        try:
            data = run_extr(make_extractor(), req)
            if "playback" in data:
                parsed = xmltodict.parse(data["playback"])
                period = parsed.get("MPD", {}).get("Period", {})
                
                # Get Video URL
                adapt = period.get("AdaptationSet", [])
                if not isinstance(adapt, list): adapt = [adapt]
                v_set = next((s for s in adapt if s.get("@mimeType") == "video/mp4"), adapt[0])
                reps = v_set.get("Representation", [])
                if not isinstance(reps, list): reps = [reps]
                reps.sort(key=lambda x: int(x.get("@bandwidth", 0)), reverse=True)
                video_url = reps[0].get("BaseURL")
                
                if video_url:
                    # 1. Download
                    utils.download_file(video_url, path, date)
                    
                    # 2. Find actual file (Handles brackets/emojis without glob)
                    actual_video = video_path
                    for f in video_path.parent.iterdir():
                        if f.name.startswith(video_path.name) and f.suffix == ".mp4":
                            actual_video = f
                            break

                    # 3. Download Subtitles
                    downloaded_subs = []
                    sub_list = period.get("SupplementalProperty", {}).get("nvod:SubtitleSet", {}).get("nvod:Subtitle", [])
                    if isinstance(sub_list, dict): sub_list = [sub_list]
                    
                    for s in sub_list:
                        vtt_url = s.get("nvod:Source", {}).get("#text")
                        if vtt_url:
                            match = re.search(r"([a-z]{2}[_-][A-Z]{2})", vtt_url)
                            lang = match.group(1).replace("-", "_") if match else "und"
                            # Name sub to match the ACTUAL video filename
                            sub_name = f"{actual_video.stem}_{lang}"
                            utils.download_file(vtt_url, str(actual_video.parent / sub_name))
                            
                            f_vtt = actual_video.parent / f"{sub_name}.vtt"
                            if f_vtt.exists():
                                downloaded_subs.append({"path": f_vtt, "lang": lang})

                    # 4. Standard Muxing Call
                    if downloaded_subs:
                        console.print(f"  [Mux] Finalizing Standard VOD...")
                        muxed = mux_media_with_subtitles(actual_video, downloaded_subs, ffmpeg_exe)
                        if muxed and date:
                            utils.edit_creation_date(str(muxed), date)
                    return
        except Exception as e:
            console.print(f"  [Error] download_cvideo failed: {e}")
        

    # PATH B: Standard Weverse VODs (UUID-style IDs like 4-2640187)
    req = f"/cvideo/v1.0/cvideo-{vid_str}/playInfo?videoId={vid_str}"
    try:
        data      = run_extr(make_extractor(), req)
        play_info = data.get("playInfo", {})

        # Method 1 — adaptiveVideoUrl (primary)
        video_url = play_info.get("adaptiveVideoUrl")

        # Method 2 — video list (fallback)
        if not video_url:
            video_list = play_info.get("videos", {}).get("list", [])
            if video_list:
                video_list.sort(key=lambda x: get_safe_int(x, "bitrate"), reverse=True)
                video_url = video_list[0].get("source")

        if video_url:
            _save_video_url(vid_str, video_url)
            utils.download_file(video_url, path, date)
        else:
            console.print(f"  [Error] No video URL found for {vid_str}")

    except Exception as e:
        console.print(f"  [Error] Standard Weverse VOD failed for {vid_str}: {e}")


def get_official_video_url(wv_video_id: str, naver_video_id: str):
    """
    Get the best-quality video URL for an official channel video.

    Two-step flow:
      1. Fetch inKey from Weverse:
           /cvideo/v1.0/cvideo-{wv_video_id}/inKey?videoId={wv_video_id}
         Returns: { "inKey": "V126...", "serviceId": 2072 }

      2. Call the Naver neonplayer API:
           https://apis.naver.com/neonplayer/vodplay/v3/playback/{naver_id}
           ?key={inKey}&sid={serviceId}&...
         Returns JSON containing the MPD structure with signed CDN URLs.
    """
    try:
        # Step 1 — get inKey
        key_req  = f"/cvideo/v1.0/cvideo-{wv_video_id}/inKey?videoId={wv_video_id}"
        key_data = run_extr(make_extractor(), key_req, retries=3)
        in_key   = key_data["inKey"]
        sid      = key_data.get("serviceId", 2072)

        # Step 2 — fetch JSON manifest from neonplayer
        adi_encoded  = urllib.parse.quote('[{"adSystem":"null"}]')
        playback_url = (
            f"https://apis.naver.com/neonplayer/vodplay/v3/playback/{naver_video_id}"
            f"?key={in_key}&sid={sid}&devt=html5_pc&prv=N&lc=en&cpl=en"
            f"&adi={adi_encoded}&adu=%2F"
        )
        console.print(f"  [Neonplayer] -> {playback_url[:80]}...")

        req = urllib.request.Request(
            playback_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()

        # Strip UTF-8 BOM if present and decode
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        text = raw.decode("utf-8", errors="replace")

        # The neonplayer v3 API returns JSON, not XML.
        # Structure: { "MPD": [{ "Period": [{ "AdaptationSet": [...] }] }] }
        data      = json.loads(text)
        period    = data["MPD"][0]["Period"][0]
        adapt     = period.get("AdaptationSet", [])

        video_set = next(
            (s for s in adapt if s.get("@mimeType") == "video/mp4"), None
        )
        if not video_set:
            console.print("  [Error] No video/mp4 AdaptationSet found")
            return None

        reps = video_set.get("Representation", [])
        if isinstance(reps, dict): reps = [reps]
        best = sorted(reps, key=lambda r: int(r.get("@bandwidth", 0)), reverse=True)[0]
        url  = best.get("BaseURL")

        # BaseURL may be a JSON array or a plain string — unwrap if needed
        if isinstance(url, list): url = url[0]

        console.print(f"  [Video URL] -> {str(url)[:80]}...")
        return url

    except Exception as e:
        console.print(
            f"  [Error] Could not get official video URL "
            f"for {wv_video_id}/{naver_video_id}: {e}"
        )
        return None