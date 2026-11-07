#!/usr/bin/env python3
"""A stand-in for interactive `claude` in a pseudo-terminal: draws recorded screens of tests/fixtures/usage.

FAKE_TUI_MODE: `ready` (prompt, then the usage screen after `/usage` + Enter), `trust` (the trust question,
then waits), `die` (prints a line and exits), `mute` (prompt, then ignores what is typed).
FAKE_TUI_USAGE: the usage screen to draw, a file name in tests/fixtures/usage (default screen-ready.txt).
FAKE_TUI_LOG: if set, what was typed is appended there.
"""

import os
import sys
import time
from pathlib import Path

SCREENS = Path(__file__).resolve().parent / "fixtures" / "usage"
CLEAR = "\x1b[2J\x1b[H"


def draw(name: str) -> None:
    sys.stdout.write(CLEAR + (SCREENS / name).read_text())
    sys.stdout.flush()


mode = os.environ.get("FAKE_TUI_MODE", "ready")
if mode == "die":
    print("error: not logged in")
    sys.exit(1)
draw("screen-trust.txt" if mode == "trust" else "screen-startup.txt")
if mode == "trust":
    time.sleep(60)
    sys.exit(0)
for line in sys.stdin:  # the terminal is in cooked mode: Enter ends a line
    if log := os.environ.get("FAKE_TUI_LOG"):
        with open(log, "a") as f:
            f.write(line)
    if mode == "ready" and line.strip().endswith("/usage"):
        draw(os.environ.get("FAKE_TUI_USAGE", "screen-ready.txt"))
