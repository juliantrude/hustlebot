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
        self.file.write_text(json.dumps(steps))

    def calls(self) -> list[dict]:
        log = self.file.parent / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def env(self) -> dict[str, str]:
        return {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_CLAUDE_SCRIPT": str(self.file)}


@pytest.fixture
def fake_claude(tmp_path: Path) -> FakeClaude:
    return FakeClaude(tmp_path)
