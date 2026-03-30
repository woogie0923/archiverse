"""
archiverse.py
Entry point. Parses CLI arguments and dispatches to the appropriate
processor functions. Interactive TUI menus live in interactive_menu.py.
"""
import argparse

from text_writer import save_post_text, embed_url_metadata  # noqa
from app_runtime import AppRuntime

CLI_EPILOG = """
Prerequisites:
    - Install dependencies:  pip install -r requirements.txt
    - Set auth_token and wvd_device_path in config.yaml
    - Set binary paths (ffmpeg, N_m3u8DL-RE) in config.yaml if not on PATH

Features:
    - Downloads photos and videos from Artist Posts, Moments, Media tab, and Lives
    - Supports multiple/specific/all artists within a community
    - Supports membership-only content (requires valid auth_token + WVD)
    - User-defined passwords for secret posts (cached per community)
    - Interactive live stream menu with keyboard navigation
    - Subtitle muxing for live VODs

Usage Examples:
    python archiverse.py -c fromis9 --debug
    python archiverse.py -c stayc -a all --profile
    python archiverse.py -c RedVelvet -a IRENE SEULGI --moments
    python archiverse.py -c fromis9 --live 4-12345678
    python archiverse.py -c fromis9 -a "LEE SEO YEON" --artist --type photo
    python archiverse.py -c fromis9 --skip-membership --official 58afde0dbc1fccd94cd44eff91fa3673
    python archiverse.py -c fromis9 --media 4-223153860
    python archiverse.py -c APINK --media-menu
    python archiverse.py -c LESSERAFIM -a Chaewon --artist --text-only --comments --skip-public

Data Location Guide:
    - Community Name : slug from the URL  (e.g. 'stayc' from weverse.io/stayc)
    - Community ID   : found on the network tab by searching "communityId"
    - Member ID      : hex string in the artist profile URL
    - Post / Video ID: from the post/live/media URL

Folder structure is configurable in config.yaml.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="                           ALL-IN-ONE WEVERSE MEDIA DOWNLOADER",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=CLI_EPILOG,
    )
    return parser


def main():
    parser = build_parser()

    g_comm = parser.add_argument_group("Community Settings")
    g_comm.add_argument("-c", "--communities", nargs="*", default=None,
                        help="Community slug(s) from the Weverse URL. Omit to use menu_communities from config.yaml (picker).")
    g_comm.add_argument("-id", "--community_ids", nargs="+",
                        help="Optional: manually supply Community IDs if name lookup fails.")

    g_filter = parser.add_argument_group("Filtering")
    g_filter.add_argument("-a", "--artists", nargs="+",
                          help="Filter by artist name(s). Use '-a all' to include everyone.")

    g_action = parser.add_argument_group("Archive Actions")
    g_action.add_argument("--live", nargs="?", const=True, metavar="VIDEO_ID",
                          help="Open interactive Live menu. Provide a Video ID to download immediately.")
    g_action.add_argument("--ongoing-live-monitor", action="store_true",
                          help="Monitor on-air livestreams and record them until they go offline.")
    g_action.add_argument("--ongoing-live-monitor-no-prompt", action="store_true",
                          help="With --ongoing-live-monitor: when a recording finishes, keep monitoring without asking.")
    g_action.add_argument("--ongoing-live-now", nargs="?", const="__LATEST__", default=None, metavar="MATCH",
                          help="Record a currently on-air live immediately. MATCH can be postId/videoId/shareUrl.")
    g_action.add_argument("--ongoing-live-poll", type=int, default=30, metavar="SECONDS",
                          help="Polling interval (seconds) for --ongoing-live-monitor. Default: 30.")
    g_action.add_argument("--ongoing-live-record-all", action="store_true",
                          help="When multiple lives are on-air, record all instead of only the newest.")
    g_action.add_argument("--ongoing-live-chat", action="store_true",
                          help="(Ignored for ongoing lives) Ongoing live chat is not downloaded.")
    g_action.add_argument("--ongoing-live-subs", type=str, default="eng|kor", metavar="LANGS",
                          help='Post-recording subs via N_m3u8DL-RE: -ss lang="LANGS":for=all. '
                               'Use none (or no/off) to skip subtitles. Default: eng|kor.')
    g_action.add_argument(
        "--ongoing-live-output-format",
        nargs="?",
        const="mp4",
        default="mp4",
        choices=["mp4", "mkv"],
        help="Container after Streamlink + FFmpeg remux (ongoing lives). Default: mp4. "
             "Use the flag alone to keep the default.",
    )
    g_action.add_argument(
        "--ongoing-live-download-only",
        choices=["both", "video", "subs"],
        default="both",
        help="For --ongoing-live-monitor/--ongoing-live-now: download only video, only subtitles, or both.",
    )
    g_action.add_argument(
        "--ongoing-live-mux-subs",
        action="store_true",
        help="When downloading subtitles for ongoing lives, mux/embed them into the recorded video container after download.",
    )
    g_action.add_argument("--moments", nargs="?", const=True, metavar="POST_ID",
                          help="Archive Artist Moments. Provide a Post ID for a specific moment.")
    g_action.add_argument("--post", metavar="POST_ID",
                          help="Download a single post by ID (artist or official channel).")
    g_action.add_argument("--artist", action="store_true",
                          help="Archive Artist Posts (photos/videos).")
    g_action.add_argument("--profile", action="store_true",
                          help="Download artist profile pictures, covers, and official images.")
    g_action.add_argument("--media", nargs="?", const=True, metavar="POST_ID",
                          help="Archive the Official Media tab. Provide a Post ID for a specific item.")
    g_action.add_argument("--media-menu", action="store_true",
                          help="Open an interactive category browser for the Official Media tab.")
    g_action.add_argument("--official", nargs="+", metavar="MEMBER_ID",
                          help="Archive official agency/staff channel posts by Member ID.")
    g_action.add_argument("--skip-membership", action="store_true",
                          help="Skip all membership-only content.")
    g_action.add_argument("--skip-public", action="store_true",
                          help="Skip all public (non-membership) content. Download membership-only content only.")
    g_action.add_argument("--type", choices=["video", "photo", "both"], default="both",
                          help="What to download: 'video', 'photo', or 'both' (default).")
    g_action.add_argument("--text", action="store_true",
                          help="Save text-only posts (no media) as .txt files.")
    g_action.add_argument("--comments", action="store_true",
                          help="Fetch and save artist comments into the .txt files.")
    g_action.add_argument("--text-only", action="store_true",
                          help="Skip all media downloads. Only save text posts and artist comments as .txt files.\n"
                               "Implies --text. Combine with --comments to also fetch artist replies.")

    g_debug = parser.add_argument_group("Debug")
    g_debug.add_argument("--debug", action="store_true",
                         help="Print every API request URL for troubleshooting.")
    g_debug.add_argument("--no-history", action="store_true",
                         help="Ignore and do not update downloaded.json for this run. "
                              "Overrides download_history_enabled in config.yaml.")

    args = parser.parse_args()
    from config import CFG
    menu_list = CFG.get("menu_communities") or []
    if not isinstance(menu_list, list):
        menu_list = []
    communities_cli = list(args.communities) if args.communities else []
    runtime = AppRuntime(
        args=args,
        cfg=CFG,
        menu_list=[str(x) for x in menu_list],
        communities_cli=communities_cli,
    )
    runtime.run()


if __name__ == "__main__":
    try:
        main()
    finally:
        from utils import stop_progress
        stop_progress()