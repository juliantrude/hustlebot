"""Prototype: read /usage from an interactive Claude Code session through a pseudo-terminal.

    uv run --with pexpect --with pyte python usage_pty.py [project_dir]

Starts `claude` invisibly in a virtual 160x50 terminal, types /usage, renders the screen with a terminal
emulator (pyte) and prints what it shows, then the parsed percentages. Sends no prompt to the model.
"""

import re
import sys
import time

import pexpect
import pyte

COLS, ROWS = 160, 50
args = [a for a in sys.argv[1:] if not a.startswith("--")]
cwd = args[0] if args else "."

screen = pyte.Screen(COLS, ROWS)
stream = pyte.ByteStream(screen)
child = pexpect.spawn("claude", cwd=cwd, dimensions=(ROWS, COLS), env={"TERM": "xterm-256color", **__import__("os").environ})


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            stream.feed(child.read_nonblocking(65536, timeout=0.2))
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF:
            break


def text():
    return "\n".join(line.rstrip() for line in screen.display if line.strip())


pump(8)  # startup
child.send("/usage")
pump(1)
child.send("\r")
pump(6)
shown = text()


def parse(screen_text):
    """{"session": {...}, "week": {...}, ...}: every "Current …" block with its percent and reset."""
    out, key = {}, None
    for line in screen_text.splitlines():
        line = line.strip()
        if line.startswith("Current "):
            name = line[len("Current "):]
            key = "session" if name == "session" else "week" if name == "week (all models)" else name
            out[key] = {}
        elif key and (m := re.search(r"(\d+)% used", line)):
            out[key]["percent"] = int(m.group(1))
        elif key and line.startswith("Resets "):
            out[key]["resets"] = line[len("Resets "):]
            key = None
    return out


if "--raw" in sys.argv:
    print(shown)
print(__import__("json").dumps(parse(shown), indent=1))
child.send("\x1b")
pump(0.5)
child.sendcontrol("c")
child.sendcontrol("c")
child.close(force=True)
