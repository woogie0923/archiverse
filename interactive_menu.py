"""
Interactive TUI: community picker and main archive menu (filters, artists, actions).
"""
import json
from pathlib import Path

import state
from rich.text import Text
from utils import console
from api import make_extractor, run_extr, menu_status_board_renderable
from config import CFG, get_folder

CHANGE_COMMUNITY = "__CHANGE_COMMUNITY__"


def select_community_menu(communities: list[str]) -> str | None:
    """
    Keyboard menu: pick a community slug from config.
    Returns the chosen slug, or None if the user quits.
    """
    from live import get_key
    from rich.table import Table

    from menu_rich import cell, clear_menu_screen

    if not communities:
        return None

    cursor = 0
    while True:
        clear_menu_screen()
        console.print(Text("=== Select community ===", style="bold"))
        console.print("  [↑ ↓] move   [Enter] select   [Q] quit")
        console.rule(style="dim")
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(no_wrap=True)
        for i, slug in enumerate(communities):
            sel = i == cursor
            prefix = "→" if sel else " "
            table.add_row(cell(f"  {prefix}  {slug}", cursor=sel))
        console.print(table)
        console.rule(style="dim")
        key = get_key()
        if key == "up":
            cursor = max(0, cursor - 1)
        elif key == "down":
            cursor = min(len(communities) - 1, cursor + 1)
        elif key in ("s", "enter"):
            return communities[cursor]
        elif key in ("q", "quit"):
            return None


def interactive_menu(community_id: str, *, can_change_community: bool = False):
    """
    Interactive menu shown when -c is provided with no action flags.
    Sections (top to bottom): Filters, Artists, Official Channels, Archive, Actions.
    Navigate with arrow keys; Tab/→ advances section, ←/B goes back (Archive and Actions are separate steps).
    """
    from live import get_key

    artist_profiles = []
    _prev_debug = state.DEBUG_MODE
    try:
        state.DEBUG_MODE = False
        artists_data = run_extr(
            make_extractor(),
            f"/artistpedia/v1.0/community-{community_id}/highlight",
            retries=3,
        )
        artist_profiles = artists_data.get("artistProfiles", [])
    except Exception:
        pass
    finally:
        state.DEBUG_MODE = _prev_debug

    # Merged into artist_profiles so they appear in the menu with a label.
    # They use a synthetic artistOfficialProfile so the rest of the menu
    # code can treat them identically to current members.
    seen_ids = {a["memberId"] for a in artist_profiles}
    for entry in CFG.get("former_members", {}).get(state.COMMUNITY_NAME, []):
        mid  = entry.get("id", "").strip()
        name = entry.get("name", mid).strip()
        if mid and mid not in seen_ids:
            seen_ids.add(mid)
            artist_profiles.append({
                "memberId": mid,
                "artistOfficialProfile": {"officialName": f"{name} (former)"},
                "_former": True,
                "_display_name": name,
            })

    official_channels: list[dict] = []
    for entry in CFG.get("official_channels", {}).get(state.COMMUNITY_NAME, []):
        mid  = entry.get("id", "").strip()
        name = entry.get("name", mid).strip()
        if mid:
            official_channels.append({"memberId": mid, "profileName": name})

    try:
        from weverse_auth import get_refresh_token
        if get_refresh_token():
            auth_status_text = Text("  [Auth] Refresh token configured (auto-refresh enabled).", style="dim")
        else:
            auth_status_text = Text("  [Auth] Refresh token not set (using static auth_token only).", style="dim")
    except Exception:
        auth_status_text = Text("  [Auth] Refresh-token status unavailable.", style="dim")

    # Each filter is a list of (display_label, state_values) tuples.
    # Space cycles forward through options; the active index is stored.

    MEDIA_OPTS = [
        ("Both (photos & videos)", {"DOWNLOAD_TYPE": "both",  "TEXT_ONLY": False}),
        ("Photos only",            {"DOWNLOAD_TYPE": "photo", "TEXT_ONLY": False}),
        ("Videos only",            {"DOWNLOAD_TYPE": "video", "TEXT_ONLY": False}),
    ]
    TIER_OPTS = [
        ("All content",            {"SKIP_MEMBERSHIP": False, "SKIP_PUBLIC": False}),
        ("Membership only",        {"SKIP_MEMBERSHIP": False, "SKIP_PUBLIC": True}),
        ("Public only",            {"SKIP_MEMBERSHIP": True,  "SKIP_PUBLIC": False}),
    ]
    TEXT_OPTS = [
        ("No text saving",         {"SAVE_TEXT": False, "SAVE_COMMENTS": False, "TEXT_ONLY": False}),
        ("Save text",              {"SAVE_TEXT": True,  "SAVE_COMMENTS": False, "TEXT_ONLY": False}),
        ("Save text + comments",   {"SAVE_TEXT": True,  "SAVE_COMMENTS": True,  "TEXT_ONLY": False}),
        ("Text only (no media)",   {"SAVE_TEXT": True,  "SAVE_COMMENTS": True,  "TEXT_ONLY": True}),
    ]

    HISTORY_OPTS = [
        ("Use history", {"DOWNLOAD_HISTORY_ENABLED": True}),
        ("No history",  {"DOWNLOAD_HISTORY_ENABLED": False}),
    ]

    filter_idx = [0, 0, 0, 0]
    FILTERS    = [
        ("Media type", MEDIA_OPTS),
        ("Tier",       TIER_OPTS),
        ("Text",       TEXT_OPTS),
        ("History",    HISTORY_OPTS),
    ]

    # Archive tab: feed-style items. Actions tab: tooling (separate Tab/→ section).
    ARCHIVE_LAYOUT: list[tuple[str, str | None, str]] = [
        ("header", None, "Archive"),
        ("item", "profile", "Profile Pictures"),
        ("item", "moments", "Moments"),
        ("item", "artist", "Artist Posts"),
    ]
    if official_channels:
        ARCHIVE_LAYOUT.append(("item", "official", "Official Channel"))
    ARCHIVE_LAYOUT += [
        ("item", "media", "Media Tab"),
    ]
    ACTIONS_TOOL_LAYOUT: list[tuple[str, str | None, str]] = [
        ("header", None, "Actions"),
        ("item", "media_menu", "Media Categories"),
        ("item", "live", "Lives"),
        ("item", "ongoing_live", "Ongoing Lives"),
    ]
    _action_keys = [
        k for t, k, _ in ARCHIVE_LAYOUT + ACTIONS_TOOL_LAYOUT
        if t == "item" and k
    ]
    ACTION_LABELS = {
        k: lab
        for t, k, lab in ARCHIVE_LAYOUT + ACTIONS_TOOL_LAYOUT
        if t == "item" and k
    }
    _archive_item_keys = [k for t, k, _ in ARCHIVE_LAYOUT if t == "item" and k]
    _actions_tool_item_keys = [k for t, k, _ in ACTIONS_TOOL_LAYOUT if t == "item" and k]
    archive_item_total = max(0, len(ARCHIVE_LAYOUT) - 1)
    actions_item_total = max(0, len(ACTIONS_TOOL_LAYOUT) - 1)

    artist_sel  = [False] * len(artist_profiles)
    channel_sel = [False] * len(official_channels)
    action_sel  = {k: False for k in _action_keys}

    # ── Load persisted menu state ─────────────────────────────────────────
    def _menu_state_path() -> Path:
        cache_dir = get_folder("api_cache", community=state.COMMUNITY_NAME)
        return Path(cache_dir) / "menu_state.json"

    def _load_menu_state():
        p = _menu_state_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_menu_state(fi, asel, csel, asel_actions):
        p = _menu_state_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "filter_idx": fi,
            }
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    _saved = _load_menu_state()
    if _saved:
        _fi = _saved.get("filter_idx", [0, 0, 0, 0])
        if len(_fi) == len(filter_idx):
            filter_idx[:] = _fi

    SECTION_FILTERS  = 0
    SECTION_ARTISTS  = 1
    SECTION_CHANNELS = 2
    SECTION_ARCHIVE  = 3
    SECTION_ACTIONS  = 4
    NUM_SECTIONS     = 5

    section = SECTION_FILTERS
    cursor  = 0
    # Window scrolling for artists/channels must not follow cursor when another
    # section is active (shared cursor index would move the visible window).
    artist_win_focus = 0
    channel_win_focus = 0
    archive_win_focus = 0
    actions_tool_win_focus = 0

    def _section_len(s):
        if s == SECTION_FILTERS:  return len(FILTERS)
        if s == SECTION_ARTISTS:  return len(artist_profiles) + 1  # +1 for (All) row
        if s == SECTION_CHANNELS: return len(official_channels)
        if s == SECTION_ARCHIVE:  return archive_item_total
        if s == SECTION_ACTIONS:   return actions_item_total
        return 0

    from rich.live import Live
    from rich.table import Table
    from rich.padding import Padding
    from rich.console import Group

    from menu_rich import clear_menu_screen

    def _build_renderable():
        from rich.markup import escape

        nonlocal artist_win_focus, channel_win_focus, archive_win_focus, actions_tool_win_focus
        if section == SECTION_ARTISTS:
            artist_win_focus = cursor
        elif section == SECTION_CHANNELS:
            channel_win_focus = cursor
        elif section == SECTION_ARCHIVE:
            archive_win_focus = cursor
        elif section == SECTION_ACTIONS:
            actions_tool_win_focus = cursor

        try:
            th = int(console.size.height)
            tw = int(console.size.width)
        except Exception:
            th, tw = 24, 80
        th = max(15, th)
        tw = max(40, tw)

        item_w = max(16, tw - 16)
        rule_w = max(20, tw - 4)

        def _col_widths():
            return {
                "item_max": item_w,
            }

        cw = _col_widths()

        def _slice_window(total: int, focus: int, cap: int) -> tuple[int, int]:
            """Sliding window [start, end) so `focus` stays visible. cap==1 follows cursor."""
            if total <= 0 or cap <= 0:
                return 0, 0
            if total <= cap:
                return 0, total
            focus = max(0, min(focus, total - 1))
            half = cap // 2
            start = max(0, min(focus - half, total - cap))
            return start, start + cap

        # Summary is two compact rows (filters | artists+channels+actions), not one line per field.
        n_summary = 2
        n_nav = 4 + (1 if can_change_community else 0)
        footer_block = 1 + n_summary + n_nav
        filt_rows = 1 + len(FILTERS)
        archive_rows_full = len(ARCHIVE_LAYOUT)
        actions_tool_rows_full = len(ACTIONS_TOOL_LAYOUT)
        action_rows_full = archive_rows_full + actions_tool_rows_full
        title_h = 2 if th > 26 else 1

        art_data_total = 1 + len(artist_profiles) if artist_profiles else 1
        ch_total = len(official_channels) if official_channels else 1

        def _mid_used(a_cap: int, c_cap: int) -> int:
            return (1 + min(art_data_total, a_cap)) + (1 + min(ch_total, c_cap))

        # Try to keep at least this many rows visible (when data exists).
        MIN_ART_ROWS = 3
        MIN_ACT_ROWS = 3
        art_floor = min(MIN_ART_ROWS, art_data_total) if artist_profiles else art_data_total
        # Smaller / non-maximized windows: keep at least 3 action rows visible when possible.
        _roomy = th >= 38 and tw >= 100 and (th * tw) >= 4500
        act_floor = (
            min(MIN_ACT_ROWS, action_rows_full) if not _roomy else 1
        )

        pad_slop = 4
        footer_extra = 0

        def _reserve_total(
            *,
            compact_board: bool,
            action_rows: int,
            gap: int,
            sep_n: int,
            p_outer: int,
        ) -> int:
            st_h = 2 if compact_board else 7
            return (
                st_h
                + title_h
                + gap
                + sep_n
                + filt_rows
                + action_rows
                + footer_block
                + footer_extra
                + pad_slop
                + p_outer
            )

        compact_status = th < 32
        loose = th >= 26
        # Reserve vertical space for blank lines between major blocks (5 gaps: filter…actions).
        sep_n = 5
        # Blank line after title on short / non-maximized terminals.
        title_gap = 1 if (not loose or th < 28) else 0
        pad_outer = 2 if (loose or th >= 24) else 0
        pad_edge = (1, 2) if loose else ((1, 2) if th >= 24 else (0, 1))
        cell_pad = (0, 1, 0, 1)
        action_vis = action_rows_full

        def _mid_for(av: int) -> int:
            return th - _reserve_total(
                compact_board=compact_status,
                action_rows=av,
                gap=title_gap,
                sep_n=sep_n,
                p_outer=pad_outer,
            )

        mid_budget = _mid_for(action_vis)
        while mid_budget < 4 and action_vis > act_floor:
            action_vis -= 1
            mid_budget = _mid_for(action_vis)
        while mid_budget < 4 and loose:
            loose = False
            title_gap = 1 if th < 28 else 0
            pad_outer = 2 if th >= 24 else 0
            pad_edge = (1, 2) if th >= 24 else (0, 1)
            mid_budget = _mid_for(action_vis)
        while mid_budget < 4 and not compact_status:
            compact_status = True
            mid_budget = _mid_for(action_vis)
        while mid_budget < 4 and action_vis > act_floor:
            action_vis -= 1
            mid_budget = _mid_for(action_vis)

        mid_budget = max(0, _mid_for(action_vis))

        # Archive / Actions: like Artists / Channels — fixed section headers + windowed item rows.
        action_body_budget = max(0, action_vis - 2)
        action_body_full = archive_item_total + actions_item_total
        if action_body_full <= action_body_budget:
            archive_cap = archive_item_total
            actions_cap = actions_item_total
        elif action_body_budget == 0:
            archive_cap = 0
            actions_cap = 0
        else:
            # Floor of 1 visible item row per section when that section has items (MIN_ACT_ROWS=3 is too greedy for tight terminals).
            archive_floor = 1 if archive_item_total else 0
            actions_floor = 1 if actions_item_total else 0
            archive_cap = max(
                archive_floor,
                min(archive_item_total, max(1, action_body_budget * 55 // 100)),
            )
            actions_cap = max(
                actions_floor,
                min(actions_item_total, max(1, action_body_budget - archive_cap)),
            )
            archive_cap = min(archive_item_total, archive_cap)
            actions_cap = min(actions_item_total, actions_cap)
            while archive_cap + actions_cap > action_body_budget and archive_cap > archive_floor:
                archive_cap -= 1
            while archive_cap + actions_cap > action_body_budget and actions_cap > actions_floor:
                actions_cap -= 1
            # Do not steal below per-section floor (third loop used to drive caps to 0 and produced "Archive (1–0 of N)").
            while archive_cap + actions_cap > action_body_budget:
                if archive_cap >= actions_cap:
                    if archive_cap > archive_floor:
                        archive_cap -= 1
                    elif actions_cap > actions_floor:
                        actions_cap -= 1
                    else:
                        break
                else:
                    if actions_cap > actions_floor:
                        actions_cap -= 1
                    elif archive_cap > archive_floor:
                        archive_cap -= 1
                    else:
                        break

        if _mid_used(art_data_total, ch_total) <= mid_budget:
            art_cap = art_data_total
            ch_cap = ch_total
        else:
            art_cap = max(
                art_floor,
                min(art_data_total, max(1, mid_budget * 55 // 100)),
            )
            ch_cap = max(
                1,
                min(ch_total, max(1, mid_budget - 2 - art_cap)),
            )
            art_cap = min(art_data_total, art_cap)
            ch_cap = min(ch_total, ch_cap)
            while _mid_used(art_cap, ch_cap) > mid_budget and art_cap > art_floor:
                art_cap -= 1
            while _mid_used(art_cap, ch_cap) > mid_budget and ch_cap > 1:
                ch_cap -= 1
            while (
                _mid_used(art_cap, ch_cap) > mid_budget
                and art_cap > art_floor
                and ch_cap > 1
            ):
                if art_cap >= ch_cap:
                    art_cap -= 1
                else:
                    ch_cap -= 1

        mid_budget = max(0, mid_budget)

        # ── Filters ──────────────────────────────────────────────────────
        filter_table = Table(show_header=False, show_edge=False, box=None,
                             pad_edge=False, padding=cell_pad)
        filter_table.add_column("Sel", justify="right", no_wrap=True, width=3)
        filter_table.add_column("Item", overflow="ellipsis", no_wrap=True, max_width=cw["item_max"])

        active = (section == SECTION_FILTERS)
        hdr_style = "bold cyan" if active else "bold"
        filter_table.add_row(
            Text("►" if active else " ", style=hdr_style),
            Text("Filters  (Space to cycle)", style=hdr_style),
        )
        for i, (flabel, opts) in enumerate(FILTERS):
            chosen_label = opts[filter_idx[i]][0]
            is_cur = active and i == cursor
            st = "reverse bold" if is_cur else ""
            filter_table.add_row(
                Text("→" if is_cur else " ", style=st),
                Text(f"{flabel}: {chosen_label}", style=st),
            )

        # ── Artists (windowed) ───────────────────────────────────────────
        artist_table = Table(show_header=False, show_edge=False, box=None,
                             pad_edge=False, padding=cell_pad)
        artist_table.add_column("Sel", justify="right", no_wrap=True, width=3)
        artist_table.add_column("Item", overflow="ellipsis", no_wrap=True, max_width=cw["item_max"])

        active = (section == SECTION_ARTISTS)
        hdr_style = "bold cyan" if active else "bold"
        art_hdr = "Artists"
        if artist_profiles and art_data_total > art_cap:
            a0, a1 = _slice_window(art_data_total, artist_win_focus, art_cap)
            art_hdr = f"Artists  [dim]({a0 + 1}–{a1} of {art_data_total})[/dim]"
        artist_table.add_row(
            Text("►" if active else " ", style=hdr_style),
            Text.from_markup(art_hdr) if "[" in art_hdr else Text(art_hdr, style=hdr_style),
        )
        if not artist_profiles:
            is_cur = active and cursor == 0
            st = "reverse bold" if is_cur else ""
            artist_table.add_row(Text(" "), Text("(none)", style=st))
        else:
            a0, a1 = _slice_window(art_data_total, artist_win_focus, art_cap)
            for j in range(a0, a1):
                if j == 0:
                    all_on = all(artist_sel) and any(artist_sel)
                    chk = "[X]" if all_on else "[ ]"
                    is_cur = active and cursor == 0
                    st = "reverse bold" if is_cur else ""
                    artist_table.add_row(Text(chk, style=st), Text("(All)", style=st))
                else:
                    i = j - 1
                    is_cur = active and cursor == j
                    st = "reverse bold" if is_cur else ""
                    chk = "[X]" if artist_sel[i] else "[ ]"
                    name = artist_profiles[i]["artistOfficialProfile"]["officialName"]
                    artist_table.add_row(Text(chk, style=st), Text(escape(name), style=st))

        # ── Official Channels (windowed) ─────────────────────────────────
        channel_table = Table(show_header=False, show_edge=False, box=None,
                              pad_edge=False, padding=cell_pad)
        channel_table.add_column("Sel", justify="right", no_wrap=True, width=3)
        channel_table.add_column("Item", overflow="ellipsis", no_wrap=True, max_width=cw["item_max"])

        active = (section == SECTION_CHANNELS)
        hdr_style = "bold cyan" if active else "bold"
        ch_hdr = "Official Channels"
        if official_channels and ch_total > ch_cap:
            c0, c1 = _slice_window(ch_total, channel_win_focus, ch_cap)
            ch_hdr = f"Official Channels  [dim]({c0 + 1}–{c1} of {ch_total})[/dim]"
        channel_table.add_row(
            Text("►" if active else " ", style=hdr_style),
            Text.from_markup(ch_hdr) if "[" in ch_hdr else Text(ch_hdr, style=hdr_style),
        )
        if not official_channels:
            is_cur = active and cursor == 0
            st = "reverse bold" if is_cur else ""
            channel_table.add_row(Text(" "), Text("(none)", style=st))
        else:
            c0, c1 = _slice_window(ch_total, channel_win_focus, ch_cap)
            for i in range(c0, c1):
                is_cur = active and cursor == i
                st = "reverse bold" if is_cur else ""
                chk = "[X]" if channel_sel[i] else "[ ]"
                channel_table.add_row(
                    Text(chk, style=st),
                    Text(escape(official_channels[i]["profileName"]), style=st),
                )

        # ── Archive (fixed header + windowed items, like Artists) ─────
        archive_table = Table(show_header=False, show_edge=False, box=None,
                              pad_edge=False, padding=cell_pad)
        archive_table.add_column("Sel", justify="right", no_wrap=True, width=3)
        archive_table.add_column("Item", overflow="ellipsis", no_wrap=True, max_width=cw["item_max"])

        active_arc = (section == SECTION_ARCHIVE)
        hdr_arc = "bold cyan" if active_arc else "bold"
        if archive_item_total > archive_cap and archive_cap > 0:
            a0_arc, a1_arc = _slice_window(
                archive_item_total, archive_win_focus, archive_cap
            )
            if a1_arc > a0_arc:
                arc_hdr = (
                    f"Archive  [dim]({a0_arc + 1}–{a1_arc} of {archive_item_total})[/dim]"
                )
            else:
                arc_hdr = "Archive"
        else:
            a0_arc, a1_arc = 0, archive_item_total
            arc_hdr = "Archive"
        archive_table.add_row(
            Text("►" if active_arc else " ", style=hdr_arc),
            Text.from_markup(arc_hdr) if "[" in arc_hdr else Text(arc_hdr, style=hdr_arc),
        )
        for j in range(a0_arc, a1_arc):
            row_kind, key, label = ARCHIVE_LAYOUT[j + 1]
            is_cur = active_arc and cursor == j
            st = "reverse bold" if is_cur else ""
            chk = "[X]" if action_sel[key] else "[ ]"
            archive_table.add_row(Text(chk, style=st), Text(escape(label), style=st))

        # ── Actions (fixed header + windowed items, like Channels) ──────
        actions_tool_table = Table(show_header=False, show_edge=False, box=None,
                                   pad_edge=False, padding=cell_pad)
        actions_tool_table.add_column("Sel", justify="right", no_wrap=True, width=3)
        actions_tool_table.add_column("Item", overflow="ellipsis", no_wrap=True, max_width=cw["item_max"])

        active_at = (section == SECTION_ACTIONS)
        hdr_at = "bold cyan" if active_at else "bold"
        if actions_item_total > actions_cap and actions_cap > 0:
            a0_act, a1_act = _slice_window(
                actions_item_total, actions_tool_win_focus, actions_cap
            )
            if a1_act > a0_act:
                act_hdr = (
                    f"Actions  [dim]({a0_act + 1}–{a1_act} of {actions_item_total})[/dim]"
                )
            else:
                act_hdr = "Actions"
        else:
            a0_act, a1_act = 0, actions_item_total
            act_hdr = "Actions"
        actions_tool_table.add_row(
            Text("►" if active_at else " ", style=hdr_at),
            Text.from_markup(act_hdr) if "[" in act_hdr else Text(act_hdr, style=hdr_at),
        )
        for j in range(a0_act, a1_act):
            row_kind, key, label = ACTIONS_TOOL_LAYOUT[j + 1]
            is_cur = active_at and cursor == j
            st = "reverse bold" if is_cur else ""
            checked = bool(action_sel.get(key))
            chk = "[X]" if checked else "[ ]"
            actions_tool_table.add_row(Text(chk, style=st), Text(escape(label), style=st))

        # ── Summary & nav ─────────────────────────────────────────────────
        sel_artists  = [artist_profiles[i]["artistOfficialProfile"]["officialName"]
                        for i, v in enumerate(artist_sel) if v]
        sel_channels = [official_channels[i]["profileName"]
                        for i, v in enumerate(channel_sel) if v]
        sel_actions  = [ACTION_LABELS[k] for k in _action_keys if action_sel.get(k)]
        if not any(artist_sel) and artist_profiles:
            artists_summary = "(all — none explicitly selected)"
        elif all(artist_sel) and artist_profiles:
            artists_summary = "(all)"
        else:
            artists_summary = ", ".join(sel_artists) or "(none)"

        # Tight packing: small gaps between fields (no full-width columns — avoids huge spaces).
        gap = "  "
        media_s = escape(FILTERS[0][1][filter_idx[0]][0])
        tier_s = escape(FILTERS[1][1][filter_idx[1]][0])
        text_s = escape(FILTERS[2][1][filter_idx[2]][0])
        ch_disp = escape(
            ', '.join(sel_channels) or '(none)'
            if official_channels
            else '(none)'
        )
        act_s = escape(', '.join(sel_actions) or '(none)')
        art_s = escape(artists_summary)

        def _clip_line(s: str, max_w: int) -> str:
            if len(s) <= max_w:
                return s
            return s[: max_w - 1] + "…"

        row1 = _clip_line(
            f"  Media   : {media_s}{gap}Tier    : {tier_s}{gap}Text    : {text_s}",
            tw,
        )
        row2 = _clip_line(
            f"  Artists : {art_s}{gap}Channels: {ch_disp}{gap}Actions : {act_s}",
            tw,
        )
        summary_text = Text(row1 + "\n" + row2, style="dim")

        nav_lines = (
            "\n  Nav: ↑/↓ move   Tab/→ next section   ←/B prev section\n"
            "  Space toggle/cycle   A select all   S/Enter start   Q exit"
        )
        if can_change_community:
            nav_lines += "\n  C change community"
        nav_text = Text(escape(nav_lines), style="dim")

        menu_title = f"═══ {state.COMMUNITY_NAME} — Interactive Archive Menu ═══"
        if len(menu_title) > tw - 2:
            menu_title = (f"═══ {state.COMMUNITY_NAME} — Menu ═══")[: tw - 1]

        sep = [Text("")] if sep_n > 0 else []
        parts = [
            menu_status_board_renderable(compact=compact_status),
            auth_status_text,
            Text(menu_title, style="bold"),
        ]
        if title_gap:
            parts.append(Text(""))
        parts += sep + [filter_table]
        parts += sep + [artist_table]
        parts += sep + [channel_table]
        parts += sep + [archive_table]
        parts += sep + [actions_tool_table]
        parts += [Text("─" * rule_w, style="dim")]
        if footer_extra:
            parts.append(Text(""))
        parts += [summary_text]
        if footer_extra:
            parts.append(Text(""))
        parts += [nav_text]

        return Padding(Group(*parts), pad_edge)

    # Full clear on entry (submenus like Lives leave a full-screen layout) and on
    # exit (Live's transient restore is not always enough to drop prior output).
    clear_menu_screen()
    try:
        with Live(_build_renderable(), console=console, auto_refresh=False,
                  transient=True) as live:
            while True:
                live.update(_build_renderable(), refresh=True)
                key     = get_key()
                sec_len = _section_len(section)

                if key == "up":
                    if sec_len > 0:
                        cursor = max(0, cursor - 1)
                elif key == "down":
                    if sec_len > 0:
                        cursor = min(sec_len - 1, cursor + 1)
                elif key in ("right", "\t"):
                    section = (section + 1) % NUM_SECTIONS
                    cursor  = 0
                elif key in ("left", "b"):
                    section = (section - 1) % NUM_SECTIONS
                    cursor  = 0
                elif key == "space":
                    if section == SECTION_FILTERS:
                        filter_idx[cursor] = (filter_idx[cursor] + 1) % len(FILTERS[cursor][1])
                    elif section == SECTION_ARTISTS and artist_profiles:
                        if cursor == 0:
                            v = not all(artist_sel)
                            artist_sel[:] = [v] * len(artist_sel)
                        else:
                            artist_sel[cursor - 1] = not artist_sel[cursor - 1]
                    elif section == SECTION_CHANNELS and official_channels:
                        channel_sel[cursor] = not channel_sel[cursor]
                    elif section == SECTION_ARCHIVE and archive_item_total:
                        rk, ak, _ = ARCHIVE_LAYOUT[cursor + 1]
                        if rk == "item" and ak:
                            action_sel[ak] = not action_sel[ak]
                    elif section == SECTION_ACTIONS and actions_item_total:
                        rk, ak, _ = ACTIONS_TOOL_LAYOUT[cursor + 1]
                        if rk == "item" and ak:
                            action_sel[ak] = not action_sel[ak]
                elif key == "a":
                    if section == SECTION_ARTISTS and artist_profiles:
                        v = not all(artist_sel)
                        artist_sel[:] = [v] * len(artist_sel)
                    elif section == SECTION_CHANNELS and official_channels:
                        v = not all(channel_sel)
                        channel_sel[:] = [v] * len(channel_sel)
                    elif section == SECTION_ARCHIVE:
                        v = not all(action_sel[k] for k in _archive_item_keys)
                        for k in _archive_item_keys:
                            action_sel[k] = v
                    elif section == SECTION_ACTIONS:
                        v = not all(action_sel[k] for k in _actions_tool_item_keys)
                        for k in _actions_tool_item_keys:
                            action_sel[k] = v
                elif key in ("s", "enter"):
                    chosen_actions = [k for k in _action_keys if action_sel.get(k)]
                    if not chosen_actions:
                        continue
                    break
                elif key in ("q", "quit"):
                    return None, None
                elif key == "c" and can_change_community:
                    return CHANGE_COMMUNITY, None
    finally:
        clear_menu_screen()

    chosen_artist_names = [artist_profiles[i]["artistOfficialProfile"]["officialName"]
                           for i, v in enumerate(artist_sel) if v]
    # Persist current menu state for next run
    _save_menu_state(filter_idx, artist_sel, channel_sel,
                     {k: v for k, v in action_sel.items()})

    chosen_channel_ids  = [official_channels[i]["memberId"]
                           for i, v in enumerate(channel_sel) if v]
    chosen_action_keys  = [k for k in _action_keys if action_sel.get(k)]

    for i, (_, opts) in enumerate(FILTERS):
        for state_key, val in opts[filter_idx[i]][1].items():
            setattr(state, state_key, val)

    no_selection  = not any(artist_sel)
    all_selected  = all(artist_sel) if artist_profiles else False
    return {
        "artists":  None if (no_selection or all_selected) else chosen_artist_names,
        "channels": chosen_channel_ids,
        "actions":  chosen_action_keys,
    }, None
