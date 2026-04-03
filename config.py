"""
config.py
Loads config.yaml and exposes a single CFG dict used by all modules.
"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("[Error] PyYAML is not installed. Run: pip install pyyaml")
    sys.exit(1)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load() -> dict:
    if not _CONFIG_PATH.exists():
        print(f"[Error] config.yaml not found at {_CONFIG_PATH}")
        sys.exit(1)
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CFG: dict = _load()

AUTH_TOKEN      : str   = CFG.get("auth_token", "")
WVD_DEVICE_PATH : str   = CFG.get("wvd_device_path", "")
MEDIA_FOLDER    : str   = CFG.get("media_folder", "Media")
STOP_THRESHOLD  : int   = int(CFG.get("stop_threshold", 200))
PAGED_SLEEP     : float = float(CFG.get("paged_sleep", 2))
SHORT_SLEEP     : float = float(CFG.get("short_sleep", 0.5))
DOWNLOAD_SLEEP  : float = float(CFG.get("download_sleep", 3.0))
DATE_FORMAT     : str   = CFG.get("date_format", "%Y.%m.%d %H-%M-%S")
DATE_FORMATS    : dict  = CFG.get("date_formats", {})
DATE_SEP        : str   = CFG.get("date_separator", ".")
TIME_SEP        : str   = CFG.get("time_separator", "-")

CACHE_ENABLED            : bool  = bool(CFG.get("cache_enabled", True))
DOWNLOAD_HISTORY_ENABLED : bool  = bool(CFG.get("download_history_enabled", True))
TIMEZONE                 : str   = CFG.get("timezone", "Asia/Seoul")

# Bracket style for {tier} and {post_id} placeholders in filename templates.
# "square" -> [Public]  [1-171362020]
# "curly"  -> {Public}  {1-171362020}
# "none"   -> Public    1-171362020   (no brackets)
TIER_BRACKET   : str = CFG.get("tier_bracket",   "square")
POSTID_BRACKET : str = CFG.get("postid_bracket", "square")

_raw_base = CFG.get("base_dir", "").strip()
BASE_DIR: str = _raw_base if _raw_base else "."

BINARIES: dict = CFG.get("binaries", {
    "ffmpeg":      "ffmpeg",
    "ffprobe":     "ffprobe",
    "n_m3u8dl_re": "N_m3u8DL-RE",
    "streamlink":  "streamlink",
})

FOLDERS: dict = CFG.get("folders", {})

FILENAME_TEMPLATES: dict = CFG.get("filename_templates", {
    "default": "{community} {artist} {date} [{post_id}]",
    "lives":   "{community} {artist} {date} {title} [{post_id}]",
})

WEVERSE_API_BASE = "https://global.apis.naver.com/weverse/wevweb"
WEVERSE_HMAC_KEY = b"1b9cb6378d959b45714bec49971ade22e6e24e42"

COMMON_HEADERS: dict = {
    "Authorization": f"Bearer {AUTH_TOKEN}",
    "Origin": "https://weverse.io",
    "Referer": "https://weverse.io/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def get_folder(key: str, **kwargs) -> str:
    """
    Resolve a folder path template from config.yaml.

    Automatically injects {base} and {media} so callers only need to
    pass community/artist/tier/channel as relevant.
    
    """
    template = FOLDERS.get(key, "")
    kwargs.setdefault("base",  BASE_DIR)
    kwargs.setdefault("media", MEDIA_FOLDER)
    return template.format(**kwargs)