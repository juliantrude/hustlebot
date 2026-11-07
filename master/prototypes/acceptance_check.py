"""The Definition of Done checked against a running hustlemaster, not an in-process one.

    uv run python prototypes/acceptance_check.py /tmp/hm-accept

Boots `python -m hustlemaster.server` as its own process with a scratch home and spare ports, the fake `claude`
of the tests in place of the real one. Then, as a user would: submits a hustle over MCP with the interactive token
(dashboard-only tools absent), logs in to the dashboard, activates the hustle with the switch, watches the control,
graph and cinema views while a session runs, waits until the acceptance overseer closes the hustle, and checks the
scratch repository's `main`. Screenshots land in the scratch folder; look at them.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from playwright.sync_api import sync_playwright

MASTER = Path(__file__).resolve().parents[1]
TESTS = MASTER / "tests"
sys.path.insert(0, str(TESTS))  # the same fake `claude` and helpers the tests use
from conftest import FakeClaude
from test_runner import report, sh
from test_service import WORK


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def mcp(port: int, token: str, *steps):
    async def go():
        out = []
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
            url = f"http://127.0.0.1:{port}/mcp"
            async with Client(streamable_http_client(url, http_client=http), mode="legacy") as client:
                for tool, args in steps:
                    if tool == "list":
                        out.append(sorted(t.name for t in (await client.list_tools()).tools))
                    else:
                        result = await client.call_tool(tool, args)
                        assert not result.is_error, result.content[0].text
                        out.append(json.loads(result.content[0].text))
        return out

    return anyio.run(go)


def state(port: int, token: str) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)


def wait(what, check, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            print(f"  ok: {what}")
            return
        time.sleep(0.2)
    raise SystemExit(f"timed out: {what}")


def main(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    repo, home = root / "proj", root / "home"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com")
    sh(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("proj\n")
    (repo / ".gitignore").write_text(".env\n")
    (repo / ".env").write_text("SECRET=copied\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", "init")

    (root / "fake").mkdir()
    fake = FakeClaude(root / "fake")
    step = {"run": WORK, "stream": "haiku-tools.jsonl", "text": report("done", "check passes")}
    fake.script([dict(step) for _ in range(4)])

    port, mcp_port = free_port(), free_port()
    log = open(root / "server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "hustlemaster.server", "--db", str(home / "hustlemaster.db"), "--home", str(home),
         "--port", str(port), "--mcp-port", str(mcp_port), "--claude", str(fake.path), "--projects", str(root)],
        env=fake.env(), cwd=MASTER, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        wait("interactive and dashboard tokens written",
             lambda: (home / "interactive-token").exists() and (home / "dashboard-token").exists())
        dash = (home / "dashboard-token").read_text().strip()
        wait("web app answers", lambda: _answers(port, dash))

        interactive = (home / "interactive-token").read_text().strip()
        tools, settings = mcp(mcp_port, interactive, ("list", {}), ("read_settings", {}))
        assert tools == ["change_plan", "list_hustles", "read_hustle", "read_settings", "submit_hustle"], tools
        print(f"  ok: interactive MCP tools {tools}, settings {settings}")
        plan = [
            {"id": "SLOW", "title": "Slow step", "prompt": "Write SLOW.txt.", "model": "haiku"},
            {"id": "C", "title": "Parallel step", "prompt": "Write C.txt.", "model": "sonnet"},
            {"id": "D", "title": "After C", "prompt": "Write D.txt.", "model": "haiku", "after": ["C"]},
            {"id": "accept", "title": "Acceptance", "prompt": "Check the DoD on main.", "model": "opus",
             "mode": "overseer", "after": ["SLOW", "D"]},
        ]
        (submitted,) = mcp(mcp_port, interactive, ("submit_hustle", {
            "project": repo.name, "title": "Acceptance hustle", "brief": "North star: four files.",
            "increments": plan, "push": "off", "verify": "test -f README.md"}))
        hid = submitted["hustle"]
        print(f"  ok: submitted {hid} over MCP")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path="/usr/bin/chromium")
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            base = f"http://127.0.0.1:{port}"
            page.goto(base + "/cinema")
            assert "/login" in page.url, page.url
            page.fill("input[name=token]", dash)
            page.press("input[name=token]", "Enter")
            page.wait_for_url(base + "/cinema")
            page.goto(base + "/")
            page.wait_for_selector("article.hustle")
            card = page.locator("article.hustle").first
            assert "inactive" in (card.get_attribute("class") or "")
            page.screenshot(path=root / "1-control-submitted.png")
            card.locator(".switch").click()
            wait("SLOW running", lambda: _inc(port, dash, hid, "SLOW") == "running")
            page.wait_for_timeout(1500)
            page.screenshot(path=root / "2-control-running.png")
            page.goto(f"{base}/hustles/{hid}")
            page.wait_for_timeout(1500)
            page.screenshot(path=root / "3-graph-running.png")
            page.goto(base + "/cinema")
            page.wait_for_timeout(2000)
            page.screenshot(path=root / "4-cinema-running.png")
            wait("hustle done", lambda: _status(port, dash, hid) == "done", timeout=90)
            page.goto(base + "/")
            page.wait_for_timeout(1500)
            page.screenshot(path=root / "5-control-done.png")
            browser.close()

        merged = sh(repo, "log", "--format=%s", "main").split()
        assert sorted(merged[:3]) == ["C", "D", "SLOW"] and merged[3] == "init", merged
        assert merged.index("D") < merged.index("C"), "D merged before C, its dependency"
        print(f"  ok: main = {merged}")
        assert sh(repo, "worktree", "list").count("\n") == 1, sh(repo, "worktree", "list")
        assert not sh(repo, "branch", "--list", "hustle/*").strip()
        print("  ok: no worktree or hustle branch left")
        calls = fake.calls()
        assert len(calls) == 4 and all(not c["stdin"] for c in calls)  # stdin closed
        models = {Path(c["cwd"]).name: c["argv"][c["argv"].index("--model") + 1] for c in calls}
        assert models == {"SLOW": "haiku", "C": "sonnet", "D": "haiku", "accept": "opus"}, models
        print(f"  ok: one session per increment, models {models}")
    finally:
        proc.terminate()
        proc.wait(timeout=30)
        log.close()
    print("acceptance check passed; screenshots in", root)


def _answers(port: int, token: str) -> bool:
    try:
        state(port, token)
        return True
    except OSError:
        return False


def _hustle(port, token, hid):
    return next(h for h in state(port, token)["hustles"] if h["id"] == hid)


def _inc(port, token, hid, inc):
    return next(i["state"] for i in _hustle(port, token, hid)["increments"] if i["id"] == inc)


def _status(port, token, hid):
    return _hustle(port, token, hid)["status"]


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/hm-accept"))
