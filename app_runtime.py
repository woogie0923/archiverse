"""
app_runtime.py
Central runtime orchestration for CLI and interactive execution flows.
"""
from dataclasses import dataclass
from typing import Any

import state
from api import make_extractor, run_extr
from interactive_menu import CHANGE_COMMUNITY, interactive_menu, select_community_menu
from live import get_key as live_get_key, process_lives
from ongoing_live import process_ongoing_lives, select_ongoing_live_options
from processors import (
    process_artist_posts,
    process_member_profiles,
    process_moments,
    process_official_media,
    process_official_media_menu,
    process_official_posts,
    process_single_post,
)
from rich.text import Text
from utils import console

ACTION_ORDER = [
    "profile", "moments", "artist", "official", "media",
    "media_menu", "live", "ongoing_live",
]
ARTIST_REQUIRED_ACTIONS = {"artist", "moments", "live", "media", "media_menu", "profile"}


def parse_target_artists(artists_arg):
    if not artists_arg:
        return None
    joined = " ".join(artists_arg).lower()
    if joined.strip() == "all":
        return None
    if "," in joined:
        return [a.strip() for a in joined.split(",") if a.strip()]
    return artists_arg


def any_action_selected(args) -> bool:
    return (
        args.live or args.moments or args.artist or args.profile
        or args.official or args.media or args.media_menu or args.post
        or args.ongoing_live_monitor or args.ongoing_live_now
    )


def build_status_url(community_id: str) -> str:
    return (
        f"/member/v1.0/community-{community_id}/me?"
        "appId=be4d79eb8fc7bd008ee82c8ec4ff6fd4&"
        "fields=memberId%2CcommunityId%2Cjoined%2CjoinedDate%2CprofileType%2C"
        "profileName%2CprofileImageUrl%2CprofileCoverImageUrl%2CprofileComment%2C"
        "myProfile%2Chidden%2Cblinded%2CmemberJoinStatus%2CfirstJoinAt%2C"
        "followCount%2Cfollowed%2ChasMembership%2Cbadges%2ChasOfficialMark%2C"
        "artistOfficialProfile%2CprofileSpaceStatus%2CavailableActions%2C"
        "shareUrl%2Ccommunity&language=en&os=WEB&platform=WEB&wpf=pc"
    )


def ongoing_live_kwargs_from_args(args, direct_match=None, skip_prompt=False) -> dict[str, Any]:
    return {
        "direct_match": direct_match,
        "poll_seconds": args.ongoing_live_poll,
        "record_all": args.ongoing_live_record_all,
        "subtitle_langs": args.ongoing_live_subs,
        "output_format": args.ongoing_live_output_format,
        "download_only": args.ongoing_live_download_only,
        "mux_subs": args.ongoing_live_mux_subs,
        "skip_monitor_prompt": skip_prompt,
    }


def wait_post_actions_choice() -> bool:
    console.print()
    console.rule(style="dim")
    console.print(
        Text(
            "  All done! Press [B] to return to menu or [Q] to exit.",
            style="bold",
        )
    )
    console.rule(style="dim")
    while True:
        k = live_get_key()
        if k == "b":
            return False
        if k in ("q", "quit"):
            return True


@dataclass
class AppRuntime:
    args: Any
    cfg: dict
    menu_list: list[str]
    communities_cli: list[str]

    def apply_state_from_args(self):
        state.DEBUG_MODE = self.args.debug
        state.SKIP_MEMBERSHIP = self.args.skip_membership
        state.SKIP_PUBLIC = self.args.skip_public
        state.DOWNLOAD_TYPE = self.args.type
        state.TEXT_ONLY = self.args.text_only
        state.SAVE_TEXT = self.args.text or self.args.text_only
        state.SAVE_COMMENTS = self.args.comments
        if self.args.no_history:
            state.DOWNLOAD_HISTORY_ENABLED = False
        state.TARGET_ARTISTS = parse_target_artists(self.args.artists)
        console.print(f"  [Config] Download Mode: {state.DOWNLOAD_TYPE.upper()}")

    def init_community(self, name: str, idx: int) -> bool:
        state.COMMUNITY_NAME = name
        try:
            if self.args.community_ids and idx < len(self.args.community_ids):
                state.COMMUNITY_ID = self.args.community_ids[idx]
            else:
                id_map = self.cfg.get("menu_community_ids") or {}
                if isinstance(id_map, dict) and name in id_map:
                    state.COMMUNITY_ID = str(id_map[name])
                else:
                    resp = run_extr(
                        make_extractor(),
                        f"/community/v1.0/communityIdUrlPathByUrlPathArtistCode?keyword={name}",
                    )
                    state.COMMUNITY_ID = resp["communityId"]
            run_extr(make_extractor(), build_status_url(state.COMMUNITY_ID))
            return True
        except Exception as e:
            console.print(f"\n[!] Critical error initialising {state.COMMUNITY_NAME}: {e}")
            return False

    def run_cli_actions_for_current_community(self):
        if self.args.live:
            direct_id = self.args.live if isinstance(self.args.live, str) else None
            process_lives(direct_id=direct_id, debug=self.args.debug)

        if self.args.ongoing_live_monitor:
            process_ongoing_lives(**ongoing_live_kwargs_from_args(
                self.args,
                direct_match=self.args.ongoing_live_now if isinstance(self.args.ongoing_live_now, str) else None,
                skip_prompt=self.args.ongoing_live_monitor_no_prompt,
            ))
        elif self.args.ongoing_live_now:
            process_ongoing_lives(**ongoing_live_kwargs_from_args(
                self.args,
                direct_match=self.args.ongoing_live_now if isinstance(self.args.ongoing_live_now, str) else None,
                skip_prompt=False,
            ))

        if self.args.moments:
            moment_id = self.args.moments if isinstance(self.args.moments, str) else None
            process_moments(direct_id=moment_id)
        if self.args.post:
            process_single_post(self.args.post)
        if self.args.artist:
            process_artist_posts()
        if self.args.profile:
            process_member_profiles()
        if self.args.official:
            process_official_posts(self.args.official)
        if self.args.media:
            media_id = self.args.media if isinstance(self.args.media, str) else None
            process_official_media(direct_id=media_id)
        if self.args.media_menu:
            process_official_media_menu()

    def _execute_selected_action(self, action_key: str, chosen_channels) -> Any:
        if action_key == "artist":
            return process_artist_posts()
        if action_key == "moments":
            return process_moments()
        if action_key == "live":
            return process_lives(debug=self.args.debug)
        if action_key == "ongoing_live":
            sel = select_ongoing_live_options()
            if sel in ("back", "quit"):
                return sel
            if isinstance(sel, dict):
                process_ongoing_lives(
                    direct_match=None,
                    poll_seconds=sel.get("poll_seconds", self.args.ongoing_live_poll),
                    record_all=sel.get("record_all", self.args.ongoing_live_record_all),
                    subtitle_langs=sel.get("subtitle_langs", self.args.ongoing_live_subs),
                    output_format=sel.get("output_format", self.args.ongoing_live_output_format),
                    download_only=sel.get("download_only", "both"),
                    mux_subs=sel.get("mux_subs", False),
                    skip_monitor_prompt=self.args.ongoing_live_monitor_no_prompt,
                )
            return None
        if action_key == "media":
            return process_official_media()
        if action_key == "media_menu":
            return process_official_media_menu()
        if action_key == "profile":
            return process_member_profiles()
        if action_key == "official":
            if chosen_channels:
                return process_official_posts(chosen_channels)
            console.print("  [Skip] No official channels selected.")
            return None
        return None

    def run_cli_mode(self):
        if not self.communities_cli:
            console.print("\n[!] Use -c COMMUNITY when using download/action flags.")
            return
        for idx, name in enumerate(self.communities_cli):
            if not self.init_community(name, idx):
                continue
            self.run_cli_actions_for_current_community()

    def run_interactive_mode(self):
        if not self.communities_cli and not self.menu_list:
            console.print(
                "\n[!] Run with -c COMMUNITY or set menu_communities in config.yaml "
                "to pick a community from the menu."
            )
            return

        cli_queue = list(self.communities_cli) if self.communities_cli else None
        next_cli_idx = 0

        while True:
            if cli_queue is not None:
                if next_cli_idx >= len(cli_queue):
                    break
                name = cli_queue[next_cli_idx]
                idx = next_cli_idx
                next_cli_idx += 1
            else:
                picked = select_community_menu([str(x) for x in self.menu_list])
                if picked is None:
                    return
                name = picked
                idx = 0

            if not self.init_community(name, idx):
                continue

            while True:
                menu_result, _ = interactive_menu(
                    state.COMMUNITY_ID,
                    can_change_community=bool(self.menu_list),
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
                chosen_actions = menu_result["actions"]
                chosen_channels = menu_result["channels"]
                has_artists = (state.TARGET_ARTISTS is None or len(state.TARGET_ARTISTS) > 0)
                quit_program = False
                back_to_menu = False

                for action_key in [k for k in ACTION_ORDER if k in chosen_actions]:
                    if action_key in ARTIST_REQUIRED_ACTIONS and not has_artists:
                        console.print(f"  [Skip] No artists selected for action: {action_key}")
                        result = None
                    else:
                        result = self._execute_selected_action(action_key, chosen_channels)

                    if result == "back":
                        back_to_menu = True
                        break
                    if result == "quit":
                        quit_program = True
                        break

                if quit_program:
                    return
                if not back_to_menu and wait_post_actions_choice():
                    return

    def run(self):
        self.apply_state_from_args()
        if any_action_selected(self.args):
            self.run_cli_mode()
            return
        self.run_interactive_mode()
