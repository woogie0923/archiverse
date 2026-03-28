"""
menu_rich.py
Shared Rich helpers for TUI menus (screen clear + row highlight styles).
"""
from __future__ import annotations

import os

from rich.text import Text


def clear_menu_screen() -> None:
    """
    Hard clear for full-screen keyboard menus.

    Rich's Console.clear() often does not reset the viewport on Windows
    (Cursor / ConPTY / some Windows Terminal setups), so each redraw stacks
    instead of replacing the prior frame. cls (Windows) / clear (Linux, macOS)
    avoids that.
    """
    if os.name == "nt":
        os.system("cls")
    else:
        os.system("clear")

# Cursor row highlight — matches prior ANSI reverse-video behavior.
ROW_HIGHLIGHT = "reverse bold"


def menu_row_style(is_cursor: bool) -> str:
    return ROW_HIGHLIGHT if is_cursor else ""


def cell(val, *, cursor: bool):
    """Wrap a string or Text in a Text node with optional cursor highlight."""
    s = val if isinstance(val, Text) else str(val)
    return Text(s, style=menu_row_style(cursor))
