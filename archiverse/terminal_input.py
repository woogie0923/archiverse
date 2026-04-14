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


def debug_keys(n: int = 5) -> None:
    """Press n keys and print the exact bytes received via setraw + read(1).
    Run this to diagnose what escape sequences your terminal actually sends.
    Usage: python -c "from archiverse.terminal_input import debug_keys; debug_keys()"
    """
    import select
    import termios
    import time
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    print(f"Press {n} keys (arrows, enter, etc). Ctrl+C to quit early.\r")
    count = 0
    try:
        tty.setraw(fd)
        while count < n:
            ch = sys.stdin.read(1)
            if ch == "\x03":
                break
            seq = [ch]
            # Drain any remaining bytes that arrive within 100 ms
            deadline = time.monotonic() + 0.1
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                ready, _, _ = select.select([sys.stdin], [], [], min(0.05, left))
                if ready:
                    c = sys.stdin.read(1)
                    if c:
                        seq.append(c)
                        deadline = time.monotonic() + 0.05  # extend slightly
                else:
                    break
            parts = " + ".join(repr(c) for c in seq)
            raw = " ".join(f"\\x{ord(c):02x}" for c in seq)
            print(f"  key {count+1}: {parts}  [{raw}]\r")
            count += 1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    print("Done.\r")


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
    import termios
    import tty

    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return None

    old = termios.tcgetattr(fd)
    try:
        # Phase 1: block indefinitely until the user presses something.
        # VMIN=1, VTIME=0 — standard raw mode, waits forever for 1 byte.
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if not ch:
            return None

        if ch == "\x1b":
            # Phase 2: switch to VMIN=0, VTIME=1 (100 ms kernel timeout).
            # This lets the kernel buffer the rest of the escape sequence
            # before handing it to us, so each read(1) reliably gets the
            # next byte without needing select() loops or Python-side timers.
            attrs = termios.tcgetattr(fd)
            attrs[6][termios.VMIN] = 0   # don't require a minimum byte count
            attrs[6][termios.VTIME] = 1  # wait up to 100 ms (units of 0.1 s)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)

            ch2 = sys.stdin.read(1)
            if not ch2:
                return None  # bare Escape — nothing arrived within 100 ms
            if ch2 in ("[", "O"):
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