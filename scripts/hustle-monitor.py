#!/usr/bin/env python3
"""Tiny stdlib-only monitoring dashboard for the advance-goal loop.

Portable: no systemd dependency — "running" comes from the run.pid file,
"next fire" from hustle-scheduler.sh (which speaks systemd on Linux and launchd
on macOS). Zero tokens at runtime: everything is local file parsing.

Layout (HUSTLE_HOME = <project>/.hustle):
  config       KEY=VALUE settings (HUSTLE_PROJECT, HUSTLE_PORT, HUSTLE_BIND, ...)
  bin/         this script + hustle-scheduler.sh + hustle-session.sh
  status.json  written by hustle-session.sh after each run
  run.log      chain log
  run.pid      pid of the active run (if any)
"""
import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HUSTLE_HOME = Path(os.environ.get("HUSTLE_HOME", Path(__file__).absolute().parents[1]))
ASSETS_DIR = HUSTLE_HOME / "assets"

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def read_config() -> dict:
    cfg = {}
    try:
        for ln in (HUSTLE_HOME / "config").read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    except Exception:
        pass
    return cfg


CFG = read_config()
PROJECT = Path(CFG.get("HUSTLE_PROJECT", HUSTLE_HOME.parent))
PORT = int(os.environ.get("HUSTLE_PORT", CFG.get("HUSTLE_PORT", "8787")))
BIND = os.environ.get("HUSTLE_BIND", CFG.get("HUSTLE_BIND", "0.0.0.0"))
LOGLINES = int(CFG.get("HUSTLE_LOGLINES", "60"))

STATUS_JSON = HUSTLE_HOME / "status.json"
LOG_FILE = HUSTLE_HOME / "run.log"
GOAL_FILE = PROJECT / "GOAL.md"

CHECKBOX = re.compile(r"^\s*-\s*\[( |x|X)\]\s+(.*)$")


def run_active() -> bool:
    try:
        pid = int((HUSTLE_HOME / "run.pid").read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def armed_next() -> str:
    try:
        out = subprocess.run(
            ["bash", str(HUSTLE_HOME / "bin" / "hustle-scheduler.sh"), "next"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return out
    except Exception:
        return ""


def read_status_json() -> dict:
    try:
        return json.loads(STATUS_JSON.read_text())
    except Exception:
        return {}


def tail(path: Path, n: int) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return "(no log yet)"


def parse_goal() -> dict:
    """Parse GOAL.md into DoD, plan sections, status fields and the goal log."""
    try:
        lines = GOAL_FILE.read_text().splitlines()
    except Exception:
        return {}

    section = ""
    dod, plan, goal_log = [], [], []
    status_flag, next_action, blockers = "", "", ""
    cur = None        # current plan subsection
    last_item = None  # for wrapped checkbox continuation lines

    for ln in lines:
        if ln.startswith("## "):
            section = ln[3:].strip().lower()
            cur = last_item = None
            continue
        if ln.startswith("### ") and section.startswith("plan"):
            cur = {"name": ln[4:].strip(), "items": []}
            plan.append(cur)
            last_item = None
            continue

        m = CHECKBOX.match(ln)
        if m:
            item = {"done": m.group(1).lower() == "x", "text": m.group(2).strip()}
            if section.startswith("definition"):
                dod.append(item)
                last_item = item
            elif section.startswith("plan"):
                if cur is None:
                    cur = {"name": "Plan", "items": []}
                    plan.append(cur)
                cur["items"].append(item)
                last_item = item
            continue

        # Wrapped continuation of the previous checkbox (indented plain line).
        if (last_item is not None and ln[:1].isspace() and ln.strip()
                and not ln.strip().startswith(("<!--", "#"))):
            last_item["text"] += " " + ln.strip()
            continue
        last_item = None

        s = ln.strip()
        if section == "status":
            if s.startswith("STATUS:"):
                status_flag = s.split(":", 1)[1].strip()
            elif s.lower().startswith("**next action:**"):
                next_action = s[len("**Next action:**"):].strip()
            elif s.lower().startswith("**blockers:**"):
                blockers = s[len("**Blockers:**"):].strip()
            elif next_action and not blockers and s and not s.startswith(("<!--", "**")):
                next_action += " " + s  # wrapped continuation line
        elif section == "log":
            if s.startswith("- ") and not s.startswith("- <!--"):
                goal_log.append(s[2:])

    next_up = []
    for sec in plan:
        for it in sec["items"]:
            if not it["done"] and len(next_up) < 3:
                next_up.append({"section": sec["name"], "text": it["text"]})

    for sec in plan:
        sec["done"] = sum(1 for i in sec["items"] if i["done"])
        sec["total"] = len(sec["items"])

    plan_done = sum(s["done"] for s in plan)
    plan_total = sum(s["total"] for s in plan)
    return {
        "status_flag": status_flag,
        "next_action": next_action,
        "blockers": blockers,
        "dod": dod,
        "dod_done": sum(1 for i in dod if i["done"]),
        "dod_total": len(dod),
        "plan": plan,
        "plan_done": plan_done,
        "plan_total": plan_total,
        "next_up": next_up,
        "goal_log": goal_log[-10:],
    }


# One svg per tone compute_state() can produce. "warn" (blocked / not_ready /
# stopped) has no dedicated art — it reuses "searching", the same scene the
# browser falls back to when it can't reach the monitor at all, since both
# mean "something needs a human's attention" and the two can never be shown
# at once (an unreachable monitor can't report a tone in the first place).
SCENE_BY_TONE = {
    "run": "working",
    "wait": "waiting",
    "hold": "sleeping",
    "done": "done",
    "idle": "idle",
    "warn": "searching",
}


def compute_state() -> dict:
    running = run_active()
    st = read_status_json()
    armed = armed_next()
    phase = st.get("phase", "unknown")

    if running:
        headline, tone = "Working", "run"
    elif phase == "complete":
        headline, tone = "Goal reached", "done"
    elif phase == "on_hold":
        headline, tone = "Paused – session limit", "hold"
    elif phase == "blocked":
        headline, tone = "Blocked (last increment)", "warn"
    elif phase == "not_ready":
        headline, tone = "GOAL.md not filled in yet", "warn"
    elif armed:
        headline, tone = "Waiting for next run", "wait"
    elif phase == "unknown":
        headline, tone = "No runs yet", "idle"
    else:
        headline, tone = "No chain scheduled (stopped?)", "warn"

    return {
        "headline": headline,
        "tone": tone,
        "scene": SCENE_BY_TONE[tone],
        "running": running,
        "phase": phase,
        "pid": os.getpid(),
        "session_pct": st.get("session_pct", ""),
        "week_pct": st.get("week_pct", ""),
        "reset": st.get("reset", ""),
        "note": st.get("note", ""),
        "last_exit": st.get("last_exit", ""),
        "updated": st.get("updated", ""),
        "next_run": armed,
        "server_time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((BIND, port))
            return True
        except OSError:
            return False


def probe_holder(port: int) -> dict:
    """Ask whoever is already bound to `port` for its status via its own API."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1.5) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def find_free_port(start: int, tries: int = 200) -> int:
    port = start
    for _ in range(tries):
        if port_available(port):
            return port
        port += 1
    raise RuntimeError(f"no free port found near {start}")


def resolve_port() -> int:
    """Arbitrate HUSTLE_PORT with whatever else already holds it.

    A monitor left behind by a completed chain is stale and gets retired so
    the port can be reclaimed; a monitor for a chain that's still working
    keeps its port untouched and we fall back to the next free one instead
    of fighting it for the bind (or crash-looping under systemd, as before).
    """
    if port_available(PORT):
        return PORT

    holder = probe_holder(PORT)
    if holder.get("phase") == "complete" and holder.get("pid"):
        try:
            os.kill(int(holder["pid"]), signal.SIGTERM)
        except Exception:
            pass
        for _ in range(20):
            if port_available(PORT):
                print(f"port {PORT} was held by a completed chain (pid {holder['pid']}) "
                      "— retired it, claiming the port", flush=True)
                return PORT
            time.sleep(0.15)

    fallback = find_free_port(PORT + 1)
    print(f"port {PORT} is held by another chain (phase={holder.get('phase', 'unknown')}) "
          f"— falling back to {fallback}", flush=True)
    return fallback


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_asset(self, name: str):
        f = ASSETS_DIR / name
        try:
            data = f.read_bytes()
        except Exception:
            self._send(404, "not found", "text/plain")
            return
        self._send(200, data, MIME_TYPES.get(f.suffix, "application/octet-stream"))

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("", "/"):
            self._send_asset("app.html")
        elif path == "/api/status":
            self._send(200, json.dumps(compute_state()), "application/json")
        elif path == "/api/goal":
            self._send(200, json.dumps(parse_goal()), "application/json")
        elif path == "/api/log":
            self._send(200, tail(LOG_FILE, LOGLINES), "text/plain; charset=utf-8")
        elif "/" not in path[1:] and ".." not in path and "." in path:
            self._send_asset(path[1:])
        else:
            self._send(404, "not found", "text/plain")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = resolve_port()
    try:
        srv = ThreadingHTTPServer((BIND, port), Handler)
    except OSError:                      # lost a race for the port; try again
        port = find_free_port(port + 1)
        srv = ThreadingHTTPServer((BIND, port), Handler)
    print(f"hustle monitor on http://{BIND}:{port}  (project: {PROJECT})", flush=True)
    srv.serve_forever()
