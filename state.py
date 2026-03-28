"""
state.py
Mutable runtime state shared across all modules.
Import and modify these directly:

    import state
    state.COMMUNITY_NAME = "BINI"
"""

COMMUNITY_NAME:  str  | None = None
COMMUNITY_ID:    str  | None = None
TARGET_ARTISTS:  list | None = None   # None = match all
DEBUG_MODE:      bool         = False
SKIP_MEMBERSHIP: bool         = False
DOWNLOAD_TYPE:   str          = "both"  # "photo" | "video" | "both"
SKIP_PUBLIC:     bool         = False
SAVE_TEXT:       bool         = False  # save text-only posts as .txt files
SAVE_COMMENTS:   bool         = False  # fetch and save artist comments
TEXT_ONLY:       bool         = False  # skip all media; only save .txt files

# Initialised from config.yaml; can be overridden at runtime via --no-history
from config import DOWNLOAD_HISTORY_ENABLED as _dh
DOWNLOAD_HISTORY_ENABLED: bool = _dh
del _dh