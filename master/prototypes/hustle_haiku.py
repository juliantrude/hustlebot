"""Fixture situations for the user-facing `/hustle` skill, each run once by a real `claude --model haiku` session.

    uv run python prototypes/hustle_haiku.py /tmp/hb-hustle [convert|plan ...]

The session gets the user plugin with `--plugin-dir` (it replaces the installed `hustlebot` by name), the
hustlemaster's MCP endpoint with the interactive token, and only read tools besides it: planning must not write.
The user's answers are in the prompt, as a user who says "no pushing, just submit" would give them.

- **convert**: a sequential `GOAL.md` with one increment done, two independent ones and a README one. The plan
  must leave out the done one, run the two independent ones in parallel, carry the `**Next action:**` detail into
  its increment, drop the old loop's rules from the brief, end with an acceptance overseer on Opus (Fable is off),
  and settle push and the check. `GOAL.md` stays as it is.
- **plan**: a new goal with two independent parts, given with its Definition of Done. Two parallel workers on
  cheap models, self-contained prompts, the acceptance overseer after both.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from hustlemaster import plan
from hustlemaster.access import Tokens
from hustlemaster.mcp_server import TOKEN_ENV, Endpoint
from hustlemaster.store import Store

USER_PLUGIN = Path(__file__).resolve().parent.parent / "user-plugin"
CHECK = "python3 -m unittest discover -q"
TOKEN = "interactive-fixture-token"
READ_TOOLS = ["Read", "Glob", "Grep", "Bash(git:*)", "Bash(ls:*)", "Bash(cat:*)"]
INTERACTIVE = ["list_hustles", "read_hustle", "submit_hustle", "change_plan", "read_settings"]

GREET = 'def greet(name: str) -> str:\n    return f"Hello, {name}!"\n'
TEST_GREET = ("import unittest\nfrom greet import greet\n\n\nclass T(unittest.TestCase):\n"
              "    def test_greet(self):\n        self.assertEqual(greet('Ada'), 'Hello, Ada!')\n")

GOAL_MD = """# GOAL

## North star

A tiny Python module of polite phrases, plain standard library.

## Definition of Done

- [ ] `greet.py`: `greet(name)` returns `Hello, <name>!`, with a unittest test.
- [ ] `farewell.py`: `farewell(name)` returns `Goodbye, <name>.`, with a unittest test.
- [ ] `shout.py`: `shout(text)` returns the text upper-cased with `!` appended, with a unittest test.
- [ ] `README.md` has a usage section showing all three functions.
- [ ] `python3 -m unittest discover -q` passes.

## Working Agreements

**Priority order per iteration:**
1. If the previous iteration left failing tests, fix that first.
2. Then take the next unchecked increment from `## Plan`, in order.

**Git workflow:** one commit per completed increment on `main`. Never push.

**Style:** standard library only; type hints on every function.

## Plan

- [x] `greet.py` with `greet(name)` and `test_greet.py`.
- [ ] `farewell.py` with `farewell(name)` and `test_farewell.py`.
- [ ] `shout.py` with `shout(text)` and `test_shout.py`.
- [ ] README usage section for greet, farewell and shout.

## Status

STATUS: READY
**Next action:** `farewell.py` — return exactly `Goodbye, <name>.` with a full stop, not an exclamation mark; test with `python3 -m unittest discover -q`.
**Blockers:** none

## Budget

- session pause threshold: 85%

## Log

- 2026-11-01 — greet done (`abc1234`): `greet.py` and `test_greet.py`, check passes.
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def scratch_repo(path: Path, files: dict[str, str]) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "fixture")
    git(path, "config", "user.email", "fixture@x")
    for name, text in {"README.md": "# polite\n\nPolite phrases.\n", ".gitignore": "__pycache__/\n",
                       "greet.py": GREET, "test_greet.py": TEST_GREET, **files}.items():
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")


def run(root: Path, repo: Path, request: str) -> tuple[Store, dict]:
    store = Store(root / "hustlemaster.db")
    endpoint = Endpoint(store, Tokens(interactive=TOKEN)).start()
    config = root / "mcp.json"
    config.write_text(json.dumps(endpoint.mcp_config()))
    allowed = [f"mcp__hustlemaster__{t}" for t in INTERACTIVE] + READ_TOOLS
    argv = ["claude", "-p", f"/hustlebot:hustle {request}", "--model", "haiku",
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(config), "--strict-mcp-config",
            "--allowedTools", ",".join(allowed), "--plugin-dir", str(USER_PLUGIN)]
    try:
        with open(root / "stream.jsonl", "wb") as out:
            subprocess.run(argv, cwd=repo, env={**os.environ, TOKEN_ENV: TOKEN}, stdin=subprocess.DEVNULL,
                           stdout=out, stderr=subprocess.DEVNULL, timeout=900)
    finally:
        endpoint.stop()
    calls, result = [], {}
    for line in (root / "stream.jsonl").read_text().splitlines():
        ev = json.loads(line)
        if ev.get("type") == "result":
            result = ev
        message = ev.get("message")
        for part in (message.get("content") or [] if isinstance(message, dict) else []):
            if isinstance(part, dict) and part.get("type") == "tool_use":
                calls.append(part["name"])
    denials = [d.get("tool_name") for d in result.get("permission_denials") or []]
    return store, {"turns": result.get("num_turns"), "cost": result.get("total_cost_usd"), "tools": calls,
                   "denials": denials, "answer": (result.get("result") or "")[-600:]}


def common(store: Store, repo: Path, head: str, info: dict) -> tuple[list[str], object]:
    hustles = store.hustles()
    print(json.dumps(info, indent=1))
    if len(hustles) != 1:
        return [f"{len(hustles)} hustles submitted, expected 1"], None
    h = hustles[0]
    project = store.project(str(repo))
    print(json.dumps({"title": h.title, "active": h.active, "push": project.push, "verify": project.verify,
                      "brief": h.brief,
                      "plan": {i.id: (i.mode, i.model, i.after, i.title) for i in h.increments}}, indent=1))
    for i in h.increments:
        print(f"--- {i.id} prompt:\n{i.prompt}")
    problems = []
    if h.project != str(repo):
        problems.append(f"project {h.project!r}")
    if project.push or project.verify != CHECK:
        problems.append(f"project settings push={project.push} verify={project.verify!r}")
    if plan.problems(h.increments, allow_fable=False, total_points=20):
        problems.append("stored plan is invalid")
    accept = [i for i in h.increments if i.mode == "overseer"]
    ancestors = plan._ancestors(h.increments)
    others = {i.id for i in h.increments}
    if not any(i.model == "opus" and ancestors[i.id] == others - {i.id} for i in accept):
        problems.append("no acceptance overseer on opus after everything")
    if any(t in ("Write", "Edit", "NotebookEdit") for t in info["denials"] + info["tools"]):
        problems.append("the planning session tried to write")
    if git(repo, "rev-parse", "HEAD").strip() != head or git(repo, "status", "--porcelain").strip():
        problems.append("the repository changed")
    return problems, h


def parallel(h, a: str, b: str) -> bool:
    ancestors = plan._ancestors(h.increments)
    return a not in ancestors[b] and b not in ancestors[a]


def find(h, needle: str):
    return [i for i in h.increments if i.mode == "worker" and needle in (i.title + " " + i.prompt).lower()]


def convert(base: Path) -> list[str]:
    root = base / "convert"
    repo = root / "proj"
    scratch_repo(repo, {"GOAL.md": GOAL_MD})
    head = git(repo, "rev-parse", "HEAD").strip()
    store, info = run(root, repo, (
        "Convert this project's GOAL.md into a hustle and submit it. My answers, do not ask: no pushing; the check "
        f"command is `{CHECK}`; submit right away without showing me the plan first."))
    problems, h = common(store, repo, head, info)
    if h is None:
        return problems
    farewell, shout, readme = find(h, "farewell.py"), find(h, "shout.py"), find(h, "readme")
    workers = [i for i in h.increments if i.mode == "worker"]
    if any("greet" in i.title.lower() and "readme" not in i.title.lower() for i in workers):
        problems.append("the done greet increment is in the plan")
    if not farewell or not shout or not readme:
        problems.append("an open increment is missing")
    elif not parallel(h, farewell[0].id, shout[0].id):
        problems.append("farewell and shout do not run in parallel")
    if farewell and "goodbye" not in farewell[0].prompt.lower():
        problems.append("the Next action detail did not reach the farewell prompt")
    brief = h.brief.lower()
    if "type hints" not in brief:
        problems.append("the style agreement was lost")
    if "priority order" in brief or "one commit per" in brief:
        problems.append("the old loop's rules are in the brief")
    if (repo / "GOAL.md").read_text() != GOAL_MD:
        problems.append("GOAL.md changed")
    return problems


def new_goal(base: Path) -> list[str]:
    root = base / "plan"
    repo = root / "proj"
    scratch_repo(repo, {})
    head = git(repo, "rev-parse", "HEAD").strip()
    store, info = run(root, repo, (
        "Add `farewell(name)` returning `Goodbye, <name>.` in `farewell.py` and `shout(text)` returning the text "
        "upper-cased with `!` appended in `shout.py`, each with a unittest test file like `test_greet.py`. Done "
        f"when both exist with tests and `{CHECK}` passes. My answers, do not ask: no further agreements; the "
        f"check command is `{CHECK}`; no pushing; submit right away without showing me the plan first."))
    problems, h = common(store, repo, head, info)
    if h is None:
        return problems
    farewell, shout = find(h, "farewell.py"), find(h, "shout.py")
    if not farewell or not shout:
        problems.append("a part is missing")
    elif farewell[0] is shout[0]:
        problems.append("both parts in one increment")
    elif not parallel(h, farewell[0].id, shout[0].id):
        problems.append("the two parts do not run in parallel")
    if any(i.model not in ("haiku", "sonnet") for i in h.increments if i.mode == "worker"):
        problems.append("a worker on an expensive model")
    if any(CHECK not in i.prompt for i in farewell + shout):
        problems.append("a worker prompt does not name the check")
    if "definition of done" not in h.brief.lower():
        problems.append("the brief has no Definition of Done")
    return problems


SITUATIONS = {"convert": convert, "plan": new_goal}


def main(base: Path, names: list[str]) -> int:
    shutil.rmtree(base, ignore_errors=True)
    failed = False
    for name in names or SITUATIONS:
        print(f"== {name}")
        problems = SITUATIONS[name](base)
        print(f"{name}:", "PASSED" if not problems else "FAILED: " + "; ".join(problems))
        failed |= bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve(), sys.argv[2:]))
