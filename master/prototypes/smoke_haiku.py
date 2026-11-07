"""Smoke run: a real two-increment hustle with Haiku, from the store to the scratch repo's `main`.

    uv run python prototypes/smoke_haiku.py /tmp/hb-smoke

Wires the pieces the way the hustlemaster will: `Store` holds the plan, the MCP `Endpoint` serves the sessions,
`worktree.create` gives each increment its branch, `Runner.start` runs a real `claude -p --model haiku`, and
`merge.merge` rebases, re-verifies with the project's command and fast-forwards `main`.

A and B are independent and touch disjoint files, so they run in parallel and the second merge has to rebase onto
the first — the path a sequential pair would never take. The acceptance increment is an overseer and its skill does
not exist yet, so it is left out. The sessions get the packaged internal plugin (`PLUGIN_DIR`), so this also
checks the real worker skill. At most four real sessions (one retry each).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from hustlemaster import merge, worktree
from hustlemaster.access import Tokens
from hustlemaster.mcp_server import Endpoint
from hustlemaster.model import Increment
from hustlemaster.runner import PLUGIN_DIR, Runner
from hustlemaster.store import Store

VERIFY = "python3 -m unittest discover -q"

BRIEF = """A tiny Python module of polite phrases. Plain standard library, no packages.
Done when `python3 -m unittest discover -q` passes in the repository root."""

def plan() -> list[Increment]:
    return [
        Increment("A", "greet", "haiku", [], prompt=(
            "Create `greet.py` with `greet(name: str) -> str` returning `Hello, <name>!`, and `test_greet.py` with "
            "one unittest test case for it. Verify with `python3 -m unittest discover -q`.")),
        Increment("B", "farewell", "haiku", [], prompt=(
            "Create `farewell.py` with `farewell(name: str) -> str` returning `Goodbye, <name>.`, and "
            "`test_farewell.py` with one unittest test case for it. Verify with `python3 -m unittest discover -q`.")),
        Increment("ok", "acceptance", "haiku", [], after=["A", "B"], mode="overseer",
                  prompt="Check the Definition of Done against `main`."),
    ]


def scratch_repo(path: Path) -> None:
    path.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.name", "smoke"], ["config", "user.email", "smoke@x"]):
        subprocess.run(["git", *args], cwd=path, check=True)
    (path / "README.md").write_text("polite phrases\n")
    (path / ".gitignore").write_text("__pycache__/\n.env\n")
    (path / ".env").write_text("SMOKE=1\n")  # an ignored file the worktrees must get a copy of
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)


def main(base: Path) -> int:
    shutil.rmtree(base, ignore_errors=True)
    repo = base / "proj"
    scratch_repo(repo)
    store = Store(base / "hustlemaster.db")
    store.set_project(str(repo), verify=VERIFY)
    hid = store.submit(str(repo), "Polite phrases", BRIEF, plan(), author="user")
    store.set_active(hid, True)
    tokens = Tokens()
    endpoint = Endpoint(store, tokens).start()
    runner = Runner(endpoint.mcp_config(), tokens, PLUGIN_DIR, base / "runs")
    log: list[dict] = []
    notes: dict[str, str | None] = {}
    try:
        for _round in range(2):
            hustle = store.hustle(hid)
            batch = [i for i in hustle.ready() if i.mode == "worker"]
            if not batch:
                break
            started = []
            for inc in batch:
                wt = worktree.create(repo, base / "worktrees", hid, inc.id)
                assert (Path(wt.path) / ".env").exists(), "ignored file not copied"
                store.set_state(hid, inc.id, "running")
                started.append((inc, wt, runner.start(hustle, inc, wt.path, allow_fable=False, note=notes.get(inc.id))))
            for inc, wt, bot in started:
                out = bot.wait(timeout=600)
                store.note_peak_context(hid, inc.id, out.peak_context)
                entry = {"increment": inc.id, "status": out.status, "error": out.error, "peak": out.peak_context,
                         "turns": out.num_turns, "denials": len(out.permission_denials), "tokens": out.tokens,
                         "evidence": out.report.evidence if out.report else None}
                if out.status == "done":
                    m = merge.merge(repo, wt, verify=store.project(str(repo)).verify)
                    entry["merge"], entry["merge_detail"] = m.status, m.detail
                    if m.status == merge.MERGED:
                        store.set_state(hid, inc.id, "done")
                    else:
                        store.count_attempt(hid, inc.id)
                        store.set_state(hid, inc.id, "open")
                        notes[inc.id] = m.note()
                else:
                    store.count_attempt(hid, inc.id)
                    store.set_state(hid, inc.id, "open")
                log.append(entry)
    finally:
        endpoint.stop()

    for entry in log:
        print(json.dumps(entry))
    hustle = store.hustle(hid)
    states = {i.id: (i.state, i.attempts, i.peak_context) for i in hustle.increments}
    commits = subprocess.run(["git", "log", "--format=%h %s", "main"], cwd=repo, capture_output=True, text=True).stdout
    check = subprocess.run(VERIFY.split(), cwd=repo, capture_output=True, text=True)
    left = [wt.branch for wt in worktree.listed(repo)]
    print("states (state, attempts, peak):", states)
    print("main:\n" + commits)
    print("check on main:", check.returncode, check.stderr.strip().splitlines()[-1:] if check.stderr else "")
    print("worktrees left:", left)
    ok = (states["A"][0] == states["B"][0] == "done" and len(commits.splitlines()) == 3
          and check.returncode == 0 and not left)
    print("SMOKE", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve()))
