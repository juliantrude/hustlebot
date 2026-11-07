"""Run the installer's unit under the real systemd user manager, with a scratch home and spare ports.

    uv run python prototypes/unit_check.py /tmp/hm-unit [usage]

The unit is generated into a scratch `HOME` (nothing reaches the real `~/.claude` or `~/.config`), then copied
with only its command's `--claude`/`--usage-cwd` and the fake's variable changed into the runtime unit folder
(`/run/user/<uid>/systemd/user`, gone after a reboot, removed at the end): the user manager runs it exactly as
an installed unit, as the user.

- default: the fake `claude` runs a slow increment; the unit is restarted while it runs, the session must survive
  (`KillMode=process`), and the new process must adopt it, merge it and finish the hustle.
- `usage`: the real `claude` with `--usage-cwd` set to this repository (trusted): the `/usage` screen must be
  read under systemd (the `forkpty()` in a threaded process), seen in `/api/state`. No session starts.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from hustlemaster import install as inst
from hustlemaster.model import Increment
from hustlemaster.store import Store

MASTER = Path(__file__).resolve().parent.parent
TESTS = MASTER / "tests"
PORT, MCP_PORT = 8774, 8775
UNIT = "hm-unit-check"
RUNTIME = Path(f"/run/user/{os.getuid()}/systemd/user")
REPORT = 'Did it.\n\n```hustlebot-report\n{"status": "done", "evidence": "file there", "proposals": []}\n```\n'
WORK = """n=$(basename "$PWD"); case $n in accept) exit 0;; esac
echo "$n" > "$n.txt" && git add "$n.txt" && git commit -qm "$n"
"""


def sh(*args, **kw) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def unit_file(text: str, argv_extra: list[str], claude: str | None = None, env: tuple[str, ...] = ()) -> None:
    """The installed unit as it is, with a check's changes to its command and environment, in the runtime unit
    folder: the user manager loads it like an installed one, and it is gone after a reboot."""
    lines = []
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            argv = shlex.split(line.removeprefix("ExecStart=").replace("%%", "%"))
            if claude:
                argv[argv.index("--claude") + 1] = claude
            line = "ExecStart=" + " ".join(inst._quote(a) for a in argv + argv_extra)
        lines.append(line)
        if line.startswith("Environment=PYTHONUNBUFFERED"):
            lines += [f"Environment={inst._quote(e)}" for e in env]
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / f"{UNIT}.service").write_text("\n".join(lines) + "\n")
    sh("systemctl", "--user", "daemon-reload")


def systemctl(*args: str) -> str:
    return subprocess.run(["systemctl", "--user", *args, UNIT], capture_output=True, text=True).stdout.strip()


def stop() -> None:
    systemctl("stop")


def remove() -> None:
    stop()
    (RUNTIME / f"{UNIT}.service").unlink(missing_ok=True)
    sh("systemctl", "--user", "daemon-reload")


def state(token: str) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/state", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def wait(what, check, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if value := check():
                return value
        except OSError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"FAIL: timed out waiting for {what}\n" + journal())


def journal() -> str:
    return subprocess.run(["journalctl", "--user", "-u", UNIT, "--no-pager", "-o", "cat", "-n", "60"],
                          capture_output=True, text=True).stdout


def main() -> None:
    root = Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else "restart"
    shutil.rmtree(root, ignore_errors=True)
    (root / "home").mkdir(parents=True)
    env = {"HOME": str(root / "home"), "PATH": os.environ["PATH"]}
    report = inst.install(MASTER, env=env, systemd=False, plugin=False, port=PORT, mcp_port=MCP_PORT)
    where = inst.layout(env)
    text = where.unit.read_text()
    token = where.dashboard_token.read_text().strip()
    print(report.text())

    if mode == "usage":
        unit_file(text, ["--usage-cwd", str(MASTER.parent)])
        systemctl("start")
        try:
            usage = wait("the usage screen", lambda: (u := state(token)["usage"])["session"]["known"] and u, 90)
            print("usage read under systemd:", json.dumps(usage)[:300])
            print("--- journal ---\n" + journal())
        finally:
            remove()
        return

    repo = root / "proj"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "t"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        sh("git", "-C", str(repo), *args)
    script = root / "fake" / "script.json"
    script.parent.mkdir()
    shutil.copytree(TESTS / "fixtures" / "streams", script.parent / "streams")
    script.write_text(json.dumps([
        {"run": WORK, "stream": "streams/haiku-tools.jsonl", "text": REPORT, "sleep": 20},  # SLOW
        {"run": WORK, "stream": "streams/haiku-tools.jsonl", "text": REPORT},  # accept
    ]))
    store = Store(where.db)
    plan = [Increment("SLOW", "slow", "haiku", [], prompt="Write SLOW."),
            Increment("accept", "acceptance", "sonnet", [], after=["SLOW"], mode="overseer", prompt="Check.")]
    hid = store.submit(str(repo), "unit check", "North star.", plan, author="user", verify="git log -1")
    store.set_active(hid, True)
    # a missing folder: the usage read fails before anything runs, so it never eats one of the fake's steps
    unit_file(text, ["--usage-cwd", str(root / "no-such-folder")], claude=str(TESTS / "fake_claude.py"),
              env=(f"FAKE_CLAUDE_SCRIPT={script}",))
    systemctl("start")
    pid = None
    try:
        pid_file = wait("SLOW to start", lambda: next(where.home.glob("runs/*/SLOW/*/pid"), None))
        pid = int(pid_file.read_text())
        main_pid = systemctl("show", "-P", "MainPID")
        print(f"SLOW runs as pid {pid} beside the hustlemaster (pid {main_pid}); restarting the unit")
        systemctl("restart")  # what a second install does
        alive = Path(f"/proc/{pid}").exists()
        print(f"after restart: session alive = {alive}, hustlemaster pid {systemctl('show', '-P', 'MainPID')}")
        if not alive:
            raise SystemExit("FAIL: restarting the unit killed the session")
        hustle = wait("the hustle to finish", lambda: next(
            (h for h in state(token)["hustles"] if h["id"] == hid and h["status"] == "done"), None), 90)
        log = sh("git", "-C", str(repo), "log", "--format=%s", "main").split()
        print("hustle status:", hustle["status"], "· main:", log)
        print("fake calls:", len((script.parent / "calls.jsonl").read_text().splitlines()))
        print("--- journal ---\n" + journal())
        if log != ["SLOW", "init"]:
            raise SystemExit("FAIL: SLOW not merged")
        print("OK")
    finally:
        remove()
        if pid and Path(f"/proc/{pid}").exists():
            os.killpg(pid, 15)  # the runner gave the session its own process group


if __name__ == "__main__":
    main()
