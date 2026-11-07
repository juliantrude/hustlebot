"""The internal plugin the runner hands every hustlebot: it must replace the installed `hustlebot`, carry the skills
the prompt names, speak the report format the runner parses, and ship inside the package."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from hustlemaster.access import RIGHTS
from hustlemaster.runner import PLUGIN, PLUGIN_DIR, REPORT_STATUSES, SKILLS, Runner

MASTER = Path(__file__).resolve().parent.parent
# every tool the hustlemaster serves; a skill that names one its role lacks would send the session into a refusal
ALL_TOOLS = set().union(*RIGHTS.values())


def skill(mode: str) -> tuple[dict[str, str], str]:
    plugin, name = SKILLS[mode].split(":")
    assert plugin == PLUGIN
    text = (PLUGIN_DIR / "skills" / name / "SKILL.md").read_text()
    match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    assert match, "SKILL.md has no frontmatter"
    meta = dict(line.split(": ", 1) for line in match.group(1).splitlines())
    return meta, match.group(2)


def test_plugin_is_named_like_the_installed_one():
    # the same name makes the inline plugin replace the installed one for the session (ADR 0011)
    manifest = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == PLUGIN == "hustlebot"


def test_worker_skill_is_where_the_prompt_points():
    meta, body = skill("worker")
    assert meta["name"] == SKILLS["worker"].split(":")[1]
    assert meta["description"]
    assert body.strip()


def test_worker_skill_names_every_report_status():
    _, body = skill("worker")
    for status in REPORT_STATUSES:
        assert f"`{status}`" in body, status


def test_worker_skill_names_only_worker_tools():
    _, body = skill("worker")
    named = {tool for tool in ALL_TOOLS if re.search(rf"\b{tool}\b", body)}
    assert named, "the worker skill names none of its tools"
    assert named <= RIGHTS["worker"], named - RIGHTS["worker"]


def test_worker_skill_asks_for_exactly_one_commit_and_no_push():
    _, body = skill("worker")
    assert "exactly one" in body.lower()
    assert "never push" in body.lower()


def test_runner_hands_the_packaged_plugin(tmp_path: Path):
    runner = Runner({"mcpServers": {}}, None, PLUGIN_DIR, tmp_path / "runs")
    assert runner.plugin_dir == PLUGIN_DIR
    assert PLUGIN_DIR.is_relative_to(MASTER / "hustlemaster")


@pytest.mark.skipif(shutil.which("claude") is None, reason="no claude executable")
def test_claude_accepts_the_plugin():
    done = subprocess.run(["claude", "plugin", "validate", str(PLUGIN_DIR)], capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.skipif(shutil.which("uv") is None, reason="no uv")
def test_wheel_carries_the_plugin(tmp_path: Path):
    # hatch ships non-Python files of the package only if nothing excludes them; an installed service needs them
    subprocess.run(["uv", "build", "--wheel", "-q", "-o", str(tmp_path)], cwd=MASTER, check=True, timeout=120)
    wheel = next(tmp_path.glob("*.whl"))
    names = set(zipfile.ZipFile(wheel).namelist())
    for path in PLUGIN_DIR.rglob("*"):
        if path.is_file():
            assert f"hustlemaster/plugin/{path.relative_to(PLUGIN_DIR)}" in names
