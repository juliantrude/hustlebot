"""Screenshots of the views for the README: three hustles running at once on a real hustlemaster process.

    uv run python prototypes/readme_shots.py /tmp/hm-shots

Boots the service like `acceptance_check.py` does, with the fake `claude` of the tests, whose sessions only
sleep and commit a file. The hustles, weights and models are invented to fill the views; nothing is spent.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from acceptance_check import MASTER, FakeClaude, _answers, free_port, mcp, report, sh, state, wait

WORK = """n=$(basename "$PWD")
case $n in accept*) exit 0;; esac
sleep $((RANDOM % 30 + 20))
echo "$n" > "$n.txt" && git add "$n.txt" && git commit -qm "$n"
"""


def inc(id, title, model="sonnet", after=(), mode="worker"):
    return {"id": id, "title": title, "prompt": f"{title}.", "model": model, "after": list(after), "mode": mode}


HUSTLES = [
    ("shop-api", "Checkout v2", 2, [
        inc("schema", "Order schema", "haiku"),
        inc("cart", "Cart service", after=["schema"]),
        inc("payment", "Payment flow", "opus", ["schema"]),
        inc("tax", "Tax rules", after=["schema"]),
        inc("mails", "Order mails", "haiku", ["schema"]),
        inc("client", "Checkout client", after=["cart", "payment"]),
        inc("accept", "Acceptance", "opus", ["client", "tax", "mails"], "overseer"),
    ]),
    ("web-app", "Web login", 1, [
        inc("form", "Login form"),
        inc("session", "Session cookie", "haiku"),
        inc("errors", "Error states", "haiku"),
        inc("e2e", "Login e2e test", after=["form", "session"]),
        inc("accept", "Acceptance", "opus", ["e2e", "errors"], "overseer"),
    ]),
    ("mobile", "Offline sync", 1, [
        inc("store", "Local store"),
        inc("queue", "Sync queue", "opus", ["store"]),
        inc("conflict", "Conflict view", after=["queue"]),
        inc("accept", "Acceptance", "opus", ["conflict"], "overseer"),
    ]),
]


def main(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    for name, *_ in HUSTLES:
        repo = root / name
        repo.mkdir()
        sh(repo, "init", "-q", "-b", "main")
        sh(repo, "config", "user.email", "t@example.com")
        sh(repo, "config", "user.name", "t")
        (repo / "README.md").write_text(f"{name}\n")
        sh(repo, "add", "-A")
        sh(repo, "commit", "-qm", "init")

    (root / "fake").mkdir()
    fake = FakeClaude(root / "fake")
    fake.script([{"run": WORK, "stream": "haiku-tools.jsonl", "text": report("done", "check passes")} for _ in range(60)])

    home = root / "home"
    port, mcp_port = free_port(), free_port()
    log = open(root / "server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "hustlemaster.server", "--db", str(home / "hustlemaster.db"), "--home", str(home),
         "--port", str(port), "--mcp-port", str(mcp_port), "--claude", str(fake.path), "--projects", str(root)],
        env=fake.env(), cwd=MASTER, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        wait("tokens written", lambda: (home / "interactive-token").exists() and (home / "dashboard-token").exists())
        dash = (home / "dashboard-token").read_text().strip()
        wait("web app answers", lambda: _answers(port, dash))
        interactive = (home / "interactive-token").read_text().strip()
        ids = []
        for name, title, weight, plan in HUSTLES:
            (out,) = mcp(mcp_port, interactive, ("submit_hustle", {
                "project": name, "title": title, "brief": title, "increments": plan, "push": "off"}))
            ids.append((out["hustle"], weight))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path="/usr/bin/chromium")
            page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1.5)
            base = f"http://127.0.0.1:{port}"
            page.goto(base + "/login")
            page.fill("input[name=token]", dash)
            page.press("input[name=token]", "Enter")
            page.goto(base + "/")
            page.wait_for_selector("article.hustle")
            for hid, weight in ids:
                page.evaluate("""([id, w]) => fetch(`/api/hustles/${id}`, {method: "POST",
                    headers: {"Content-Type": "application/json"}, body: JSON.stringify({weight: w, active: true})})""",
                              [hid, weight])
            # the moment worth showing: the first increments merged, their dependents fanned out at once
            wait("dependents running", lambda: _count(port, dash, "done") >= 2 and _count(port, dash, "running") >= 4,
                 timeout=180)
            page.wait_for_timeout(8000)
            page.wait_for_timeout(1500)
            page.screenshot(path=root / "control.png", clip={"x": 0, "y": 0, "width": 1600, "height": 560})
            page.goto(f"{base}/hustles/{ids[0][0]}")
            page.wait_for_timeout(2000)
            page.screenshot(path=root / "graph.png")
            page.goto(base + "/cinema")
            page.wait_for_timeout(3000)
            page.screenshot(path=root / "cinema.png")
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=30)
        log.close()
    print("screenshots in", root)


def _count(port, token, what):
    return sum(i["state"] == what for h in state(port, token)["hustles"] for i in h["increments"])


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/hm-shots"))
