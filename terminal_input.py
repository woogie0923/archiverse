"""
Single-key reads for interactive TUI menus (Windows + POSIX: Linux, macOS).

Uses msvcrt on Windows and raw tty mode on POSIX. Arrow keys and Escape are
handled without blocking when the user presses a bare Escape (common on Unix).
"""
from __future__ import annotations

import os
import sys

try:
    import msvcrt
except ImportError:
    msvcrt = None


def get_key() -> str | None:
    """Read one logical key and return a normalised token (e.g. 'up', 'enter')."""
    if msvcrt:
        return _get_key_windows()
    return _get_key_posix()


def _get_key_windows() -> str | None:
    ch = msvcrt.getch()
    if ch in (b"\x00", b"\xe0"):
        ch = msvcrt.getch()
        return {b"H": "up", b"P": "down", b"K": "left", b"M": "right"}.get(ch)
    if ch == b"\r":
        return "enter"
    if ch == b" ":
        return "space"
    if ch == b"\x1b":
        return "quit"
    try:
        return ch.decode("utf-8").lower()
    except Exception:
        return None


def _get_key_posix() -> str | None:
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return None

    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if not ch:
            return None
        if ch == "\x1b":
            # Arrow keys: ESC [ A/B/C/D. Bare ESC: wait briefly; if nothing follows, treat as quit.
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if not ready:
                return "quit"
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(ch3)
            # SS3 arrows (some terminals): ESC O A / etc.
            if ch2 == "O":
                ch3 = sys.stdin.read(1)
                return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(ch3)
            return None
        if ch in ("\r", "\n"):
            return "enter"
        if ch == " ":
            return "space"
        if ch == "\x03":  # Ctrl+C in raw mode
            return "quit"
        if ch == "\x7f":
            return "backspace"
        return ch.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
