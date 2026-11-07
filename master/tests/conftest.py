from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"


class FakeClaude:
    """A `claude` on a private PATH that replays the steps given to `script()` (see fake_claude.py)."""

    def __init__(self, root: Path) -> None:
        self.bin = root / "bin"
        self.bin.mkdir()
        (self.bin / "claude").symlink_to(TESTS / "fake_claude.py")
        self.path = self.bin / "claude"
        self.file = root / "script.json"
        self.script([])

    def script(self, steps: list[dict]) -> None:
        # result names are fixture files; the fake resolves them relative to the script, absolute paths win
        for step in steps:
            if isinstance(step.get("result"), str):
                step["result"] = str(FIXTURES / "results" / step["result"])
            if step.get("stream"):
                step["stream"] = str(FIXTURES / "streams" / step["stream"])
        self.file.write_text(json.dumps(steps))

    def calls(self) -> list[dict]:
        log = self.file.parent / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def env(self) -> dict[str, str]:
        return {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_CLAUDE_SCRIPT": str(self.file)}


@pytest.fixture
def fake_claude(tmp_path: Path) -> FakeClaude:
    return FakeClaude(tmp_path)


def seed(store) -> dict[str, str]:
    """Three hustles in the shapes the views must show: one running with a pending approval, one blocked, one
    inactive. Returns their ids by short name."""
    from hustlemaster.model import Increment

    def inc(id, model="sonnet", after=(), mode="worker", title=None):
        return Increment(id=id, title=title or f"Step {id}", model=model, instructions=[], after=list(after),
                         prompt=f"Implement step {id}.\nVerify with the tests, then commit.", mode=mode)

    master = store.submit("/work/hustlebot", "Hustlemaster v2", "brief", [
        inc("A", title="Hustle store"), inc("B", after=["A"], title="Runner"), inc("C", after=["B"]),
        inc("D", "haiku"), inc("ok", "opus", ["C", "D"], "overseer", "Acceptance"),
    ], author="user")
    sync = store.submit("/work/final_lift", "Workout sync API", "brief", [
        inc("A"), inc("B", after=["A"]), inc("ok", "opus", ["B"], "overseer"),
    ], author="user")
    docs = store.submit("/work/ariadne", "Docs search", "brief", [
        inc("A", "haiku"), inc("ok", "opus", ["A"], "overseer"),
    ], author="user")
    store.set_active(master, True)
    store.set_weight(master, 2)
    store.set_active(sync, True)
    store.set_weight(docs, 3)
    for hustle, increment, state in [(master, "A", "done"), (master, "B", "running"), (master, "D", "running"),
                                     (sync, "A", "done"), (sync, "B", "blocked")]:
        store.set_state(hustle, increment, state)
    grown = store.hustle(master).increments + [inc("E", "opus", ["A"], title="Extra")]
    grown[-2], grown[-1] = grown[-1], grown[-2]  # the acceptance increment stays last
    grown[-1].after = ["C", "D", "E"]
    store.change_plan(master, grown, author="overseer", note="add E")
    return {"master": master, "sync": sync, "docs": docs}
