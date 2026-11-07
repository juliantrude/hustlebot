"""Fixture situations for the overseer skill, each run once by a real `claude --model haiku` session.

    uv run python prototypes/overseer_haiku.py /tmp/hb-overseer [too-big|acceptance ...]

The hustlemaster would run overseers on the strongest allowed model; Haiku is the weakest, so a skill Haiku
follows is one the others follow too, and it is the only model the loop may spend on checks.

- **too-big**: increment A of a three-increment plan reported `too-big` after building half of it on its branch.
  The overseer must split A with the first part keeping the id `A` (its worktree holds the half), the later part
  new, B depending on the later part, the acceptance increment still after everything; and commit nothing.
- **acceptance**: A and B are done, but `main` only holds A's work although the Definition of Done asks for both.
  The overseer must check, not fix: add an increment for the gap and a new acceptance increment after everything,
  leave its own increment alone, and commit nothing. The plan grows by more than a quarter, so the change waits
  for an approval, which the skill says to accept and mention.

Both run against the real `Store`, `Endpoint`, `worktree`, `events` and `Runner`, as the service will wire them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from hustlemaster import events, plan, worktree
from hustlemaster.access import Tokens
from hustlemaster.mcp_server import Endpoint
from hustlemaster.model import Increment
from hustlemaster.runner import PLUGIN_DIR, Runner
from hustlemaster.store import Store

VERIFY = "python3 -m unittest discover -q"

BRIEF = """A tiny Python module of polite phrases. Plain standard library, no packages.

Definition of Done:
- `greet.py` has `greet(name)` returning `Hello, <name>!`, with a unittest test.
- `farewell.py` has `farewell(name)` returning `Goodbye, <name>.`, with a unittest test.
- `python3 -m unittest discover -q` passes in the repository root."""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def scratch_repo(path: Path, files: dict[str, str]) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "fixture")
    git(path, "config", "user.email", "fixture@x")
    for name, text in {"README.md": "polite phrases\n", ".gitignore": "__pycache__/\n", **files}.items():
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")


GREET = 'def greet(name: str) -> str:\n    return f"Hello, {name}!"\n'
TEST_GREET = ("import unittest\nfrom greet import greet\n\n\nclass T(unittest.TestCase):\n"
              "    def test_greet(self):\n        self.assertEqual(greet('Ada'), 'Hello, Ada!')\n")


def base_plan() -> list[Increment]:
    return [
        Increment("A", "greet and farewell", "haiku", [], prompt=(
            "Create `greet.py` with `greet(name)` returning `Hello, <name>!` and `farewell.py` with `farewell(name)` "
            "returning `Goodbye, <name>.`, each with a unittest test file. Verify with "
            "`python3 -m unittest discover -q`.")),
        Increment("B", "README usage", "haiku", [], after=["A"], prompt=(
            "Add a usage section to `README.md` showing `greet` and `farewell`. Verify by reading it rendered.")),
        Increment("ok", "acceptance", "haiku", [], after=["A", "B"], mode="overseer",
                  prompt="Check the Definition of Done of the brief against `main`."),
    ]


def setup(base: Path, name: str, files: dict[str, str]):
    root = base / name
    repo = root / "proj"
    scratch_repo(repo, files)
    store = Store(root / "hustlemaster.db")
    store.set_project(str(repo), verify=VERIFY)
    hid = store.submit(str(repo), "Polite phrases", BRIEF, base_plan(), author="user")
    store.set_active(hid, True)  # the first activation confirms the plan: later growth is judged against it
    return root, repo, store, hid


def run(store: Store, root: Path, hid: str, inc: Increment, wt: Path) -> dict:
    tokens = Tokens()
    endpoint = Endpoint(store, tokens).start()
    try:
        runner = Runner(endpoint.mcp_config(), tokens, PLUGIN_DIR, root / "runs")
        out = runner.start(store.hustle(hid), inc, wt, allow_fable=False).wait(timeout=900)
    finally:
        endpoint.stop()
    calls = []
    for run_dir in sorted((root / "runs").rglob("stream.jsonl")):
        for line in run_dir.read_text().splitlines():
            ev = json.loads(line)
            for part in (ev.get("message") or {}).get("content") or []:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    calls.append(part["name"])
    return {"status": out.status, "error": out.error, "turns": out.num_turns, "peak": out.peak_context,
            "denials": len(out.permission_denials), "cost": out.cost_usd, "tools": calls,
            "evidence": out.report.evidence if out.report else None, "outcome": out}


def too_big(base: Path) -> list[str]:
    root, repo, store, hid = setup(base, "too-big", {})
    wt = Path(worktree.create(repo, root / "worktrees", hid, "A").path)
    (wt / "greet.py").write_text(GREET)
    (wt / "test_greet.py").write_text(TEST_GREET)
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "A: greet")
    branch_head = git(wt, "rev-parse", "HEAD").strip()
    store.count_attempt(hid, "A")
    eid = store.open_event(hid, "too-big", increment="A", detail=events.PURPOSE["too-big"] + (
        "\n\nWhat the last attempt left:\n\n`greet.py` and `test_greet.py` are done, committed and pass. "
        "`farewell.py` and its test are left; they did not fit this session."))
    event = store.event(eid)
    inc = events.overseer_increment(event, store.hustle(hid), "haiku")
    store.set_event_state(eid, "running")
    result = run(store, root, hid, inc, wt)
    state = events.close(store, store.event(eid), result.pop("outcome"))
    h = store.hustle(hid)
    by_id = {i.id: i for i in h.increments}
    print(json.dumps({**result, "event": state, "version": h.version,
                      "plan": {i.id: (i.mode, i.model, i.after, i.state) for i in h.increments},
                      "note_for_A": "A" in by_id and store.take_note(hid, "A")}, indent=1, default=str))
    problems = []
    if result["status"] != "done":
        problems.append(f"report {result['status']}, expected done")
    if h.version < 2:
        problems.append("plan not changed")
    if "A" not in by_id:
        problems.append("A was dropped: its worktree's work would be orphaned")
    new = [i for i in h.increments if i.id not in ("A", "B", "ok")]
    if not any(i.mode == "worker" for i in new):
        problems.append("no new part for the rest of A")
    parts = {i.id for i in new if i.mode == "worker"}
    deps_b = plan._ancestors(h.increments).get("B", set())
    if "B" in by_id and not parts <= deps_b:
        problems.append(f"B does not wait for the new part(s) {parts - deps_b}")
    if plan.problems(h.increments, allow_fable=False, total_points=20):
        problems.append("stored plan is invalid")
    if git(wt, "rev-parse", "HEAD").strip() != branch_head:
        problems.append("the overseer committed in the worktree")
    if state != "handled":
        problems.append(f"event {state}")
    return problems


def acceptance(base: Path) -> list[str]:
    root, repo, store, hid = setup(base, "acceptance", {"greet.py": GREET, "test_greet.py": TEST_GREET})
    for inc in ("A", "B"):
        store.set_state(hid, inc, "done")
    store.set_state(hid, "ok", "running")
    wt = Path(worktree.create(repo, root / "worktrees", hid, "ok").path)
    head = git(wt, "rev-parse", "HEAD").strip()
    inc = next(i for i in store.hustle(hid).increments if i.id == "ok")
    result = run(store, root, hid, inc, wt)
    result.pop("outcome")
    h = store.hustle(hid)
    by_id = {i.id: i for i in h.increments}
    pending = store.approvals(hid, states=("pending",))
    print(json.dumps({**result, "version": h.version, "approval": [(a.reasons, a.waiting) for a in pending],
                      "plan": {i.id: (i.mode, i.model, i.after, i.state, i.held) for i in h.increments}},
                     indent=1, default=str))
    problems = []
    if result["status"] != "done":
        problems.append(f"report {result['status']}, expected done")
    new = [i for i in h.increments if i.id not in ("A", "B", "ok")]
    if not any(i.mode == "worker" for i in new):
        problems.append("no increment added for the missing farewell")
    others = {i.id for i in h.increments}
    ancestors = plan._ancestors(h.increments)
    if not any(i.mode == "overseer" and ancestors[i.id] == others - {i.id} for i in new):
        problems.append("no new acceptance increment after everything (including ok)")
    if any(by_id[i].state != "done" for i in ("A", "B")):
        problems.append("done increments were touched")
    if git(wt, "rev-parse", "HEAD").strip() != head or git(wt, "status", "--porcelain").strip():
        problems.append("the acceptance overseer changed the worktree")
    if (repo / "farewell.py").exists():
        problems.append("farewell.py landed in main")
    return problems


SITUATIONS = {"too-big": too_big, "acceptance": acceptance}


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
