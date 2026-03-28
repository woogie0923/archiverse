"""
archiverse.py
Entry point. Parses CLI arguments and dispatches to the appropriate
processor functions. Interactive TUI menus live in interactive_menu.py.
"""
import argparse

import state
from utils import console
from api import make_extractor, run_extr, print_status_board
from live import process_lives
from ongoing_live import process_ongoing_lives
from text_writer import save_post_text, embed_url_metadata  # noqa
from processors import (
    process_moments,
    process_artist_posts,
    process_official_posts,
    process_official_media,
    process_official_media_menu,
    process_member_profiles,
    process_single_post,
)

from interactive_menu import CHANGE_COMMUNITY, interactive_menu, select_community_menu


def main():
    parser = argparse.ArgumentParser(
        description="                           ALL-IN-ONE WEVERSE MEDIA DOWNLOADER",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
Prerequisites:
    - Install dependencies:  pip install -r requirements.txt
    - Log into Weverse using Firefox (or whichever browser is set in config.yaml)
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
        """,
    )

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
    g_action.add_argument("--ongoing-live-now", nargs="?", const="__LATEST__", default=None, metavar="MATCH",
                          help="Record a currently on-air live immediately. MATCH can be postId/videoId/shareUrl.")
    g_action.add_argument("--ongoing-live-poll", type=int, default=30, metavar="SECONDS",
                          help="Polling interval (seconds) for --ongoing-live-monitor. Default: 30.")
    g_action.add_argument("--ongoing-live-record-all", action="store_true",
                          help="When multiple lives are on-air, record all instead of only the newest.")
    g_action.add_argument("--ongoing-live-chat", action="store_true",
                          help="(Ignored for ongoing lives) Ongoing live chat is not downloaded.")
    g_action.add_argument("--ongoing-live-subs", type=str, default="eng|kor", metavar="LANGS",
                          help='Subtitle languages passed to N_m3u8DL-RE as -ss lang="LANGS":for=all. Default: eng|kor')
    g_action.add_argument("--ongoing-live-output-format", type=str, choices=["mp4", "mkv"], default="mp4",
                          help="Mux output format for ongoing live recording. Default: mp4.")
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

    state.DEBUG_MODE      = args.debug
    state.SKIP_MEMBERSHIP = args.skip_membership
    state.SKIP_PUBLIC     = args.skip_public
    state.DOWNLOAD_TYPE   = args.type
    state.TEXT_ONLY       = args.text_only
    state.SAVE_TEXT       = args.text or args.text_only
    state.SAVE_COMMENTS   = args.comments
    # Note: ongoing live chat is intentionally not supported.
    if args.no_history:
        state.DOWNLOAD_HISTORY_ENABLED = False

    console.print(f"  [Config] Download Mode: {state.DOWNLOAD_TYPE.upper()}")

    if args.artists:
        joined = " ".join(args.artists).lower()
        if joined.strip() == "all":
            state.TARGET_ARTISTS = None
        elif "," in joined:
            state.TARGET_ARTISTS = [a.strip() for a in joined.split(",") if a.strip()]
        else:
            state.TARGET_ARTISTS = args.artists
    else:
        state.TARGET_ARTISTS = None

    from config import CFG

    menu_list = CFG.get("menu_communities") or []
    if not isinstance(menu_list, list):
        menu_list = []
    communities_cli = list(args.communities) if args.communities else []

    any_action = (
        args.live or args.moments or args.artist or args.profile
        or args.official or args.media or args.media_menu or args.post
        or args.ongoing_live_monitor or args.ongoing_live_now
    )

    def _init_community(name: str, idx: int) -> bool:
        state.COMMUNITY_NAME = name
        try:
            if args.community_ids and idx < len(args.community_ids):
                state.COMMUNITY_ID = args.community_ids[idx]
            else:
                id_map = CFG.get("menu_community_ids") or {}
                if isinstance(id_map, dict) and name in id_map:
                    state.COMMUNITY_ID = str(id_map[name])
                else:
                    resp = run_extr(
                        make_extractor(),
                        f"/community/v1.0/communityIdUrlPathByUrlPathArtistCode?keyword={name}",
                    )
                    state.COMMUNITY_ID = resp["communityId"]

            status_url = (
                f"/member/v1.0/community-{state.COMMUNITY_ID}/me?"
                "appId=be4d79eb8fc7bd008ee82c8ec4ff6fd4&"
                "fields=memberId%2CcommunityId%2Cjoined%2CjoinedDate%2CprofileType%2C"
                "profileName%2CprofileImageUrl%2CprofileCoverImageUrl%2CprofileComment%2C"
                "myProfile%2Chidden%2Cblinded%2CmemberJoinStatus%2CfirstJoinAt%2C"
                "followCount%2Cfollowed%2ChasMembership%2Cbadges%2ChasOfficialMark%2C"
                "artistOfficialProfile%2CprofileSpaceStatus%2CavailableActions%2C"
                "shareUrl%2Ccommunity&language=en&os=WEB&platform=WEB&wpf=pc"
            )
            run_extr(make_extractor(), status_url)
            return True
        except Exception as e:
            console.print(f"\n[!] Critical error initialising {state.COMMUNITY_NAME}: {e}")
            return False

    # ── CLI with action flags: requires -c ─────────────────────────────────
    if any_action:
        if not communities_cli:
            console.print("\n[!] Use -c COMMUNITY when using download/action flags.")
            return

        for idx, name in enumerate(communities_cli):
            if not _init_community(name, idx):
                continue

            if args.live:
                direct_id = args.live if isinstance(args.live, str) else None
                process_lives(direct_id=direct_id, debug=args.debug)

            if args.ongoing_live_monitor:
                process_ongoing_lives(
                    direct_match=args.ongoing_live_now if isinstance(args.ongoing_live_now, str) else None,
                    poll_seconds=args.ongoing_live_poll,
                    record_all=args.ongoing_live_record_all,
                    subtitle_langs=args.ongoing_live_subs,
                    output_format=args.ongoing_live_output_format,
                )
            elif args.ongoing_live_now:
                process_ongoing_lives(
                    direct_match=args.ongoing_live_now if isinstance(args.ongoing_live_now, str) else None,
                    poll_seconds=args.ongoing_live_poll,
                    record_all=args.ongoing_live_record_all,
                    subtitle_langs=args.ongoing_live_subs,
                    output_format=args.ongoing_live_output_format,
                )

            if args.moments:
                moment_id = args.moments if isinstance(args.moments, str) else None
                process_moments(direct_id=moment_id)

            if args.post:
                process_single_post(args.post)

            if args.artist:
                process_artist_posts()

            if args.profile:
                process_member_profiles()

            if args.official:
                process_official_posts(args.official)

            if args.media:
                media_id = args.media if isinstance(args.media, str) else None
                process_official_media(direct_id=media_id)

            if args.media_menu:
                process_official_media_menu()

        return

    # ── Interactive menu (no action flags) ─────────────────────────────────
    if not communities_cli and not menu_list:
        console.print(
            "\n[!] Run with -c COMMUNITY or set menu_communities in config.yaml "
            "to pick a community from the menu."
        )
        return

    cli_queue = list(communities_cli) if communities_cli else None
    next_cli_idx = 0

    while True:
        if cli_queue is not None:
            if next_cli_idx >= len(cli_queue):
                break
            _name = cli_queue[next_cli_idx]
            _idx = next_cli_idx
            next_cli_idx += 1
        else:
            picked = select_community_menu([str(x) for x in menu_list])
            if picked is None:
                return
            _name = picked
            _idx = 0

        if not _init_community(_name, _idx):
            if cli_queue is None:
                continue
            continue

        while True:
            menu_result, _ = interactive_menu(
                state.COMMUNITY_ID,
                can_change_community=bool(menu_list),
            )
            if menu_result == CHANGE_COMMUNITY:
                cli_queue = None
                next_cli_idx = 0
                break
            if not menu_result:
                if cli_queue is None:
                    return
                break

            state.TARGET_ARTISTS = menu_result["artists"]
            chosen_actions  = menu_result["actions"]
            chosen_channels = menu_result["channels"]

            _has_artists = (state.TARGET_ARTISTS is None or len(state.TARGET_ARTISTS) > 0)

            ACTION_ORDER = [
                "artist", "moments", "live", "ongoing_live",
                "media", "media_menu", "profile", "official",
            ]

            quit_program = False
            back_to_menu = False
            _artist_actions = {"artist", "moments", "live", "media", "media_menu", "profile"}

            for action_key in [k for k in ACTION_ORDER if k in chosen_actions]:
                if action_key in _artist_actions and not _has_artists:
                    console.print(f"  [Skip] No artists selected for action: {action_key}")
                    result = None
                    continue
                if action_key == "artist":
                    result = process_artist_posts()
                elif action_key == "moments":
                    result = process_moments()
                elif action_key == "live":
                    result = process_lives(debug=args.debug)
                elif action_key == "ongoing_live":
                    process_ongoing_lives(
                        direct_match=None,
                        poll_seconds=args.ongoing_live_poll,
                        record_all=args.ongoing_live_record_all,
                        subtitle_langs=args.ongoing_live_subs,
                        output_format=args.ongoing_live_output_format,
                    )
                    result = None
                elif action_key == "media":
                    result = process_official_media()
                elif action_key == "media_menu":
                    result = process_official_media_menu()
                elif action_key == "profile":
                    result = process_member_profiles()
                elif action_key == "official":
                    if chosen_channels:
                        result = process_official_posts(chosen_channels)
                    else:
                        console.print("  [Skip] No official channels selected.")
                        result = None

                if result == "back":
                    back_to_menu = True
                    break
                elif result == "quit":
                    quit_program = True
                    break

            if quit_program:
                return

            if not back_to_menu:
                from live import get_key as _get_key
                from rich.text import Text
                from utils import console as _console

                _console.print()
                _console.rule(style="dim")
                _console.print(
                    Text(
                        "  All done! Press [B] to return to menu or [Q] to exit.",
                        style="bold",
                    )
                )
                _console.rule(style="dim")
                while True:
                    k = _get_key()
                    if k == "b":
                        break
                    elif k in ("q", "quit"):
                        quit_program = True
                        break
                if quit_program:
                    return


if __name__ == "__main__":
    try:
        main()
    finally:
        from utils import stop_progress
        stop_progress()