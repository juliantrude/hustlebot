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


@pytest.mark.parametrize("mode", SKILLS)
def test_skill_is_where_the_prompt_points(mode: str):
    meta, body = skill(mode)
    assert meta["name"] == SKILLS[mode].split(":")[1]
    assert meta["description"]
    assert body.strip()


@pytest.mark.parametrize("mode", SKILLS)
def test_skill_names_every_report_status(mode: str):
    # the overseer names `too-big` only to rule it out, but a status left unexplained gets guessed at
    _, body = skill(mode)
    for status in REPORT_STATUSES:
        assert f"`{status}`" in body, status


@pytest.mark.parametrize("mode", SKILLS)
def test_skill_names_only_its_roles_tools(mode: str):
    _, body = skill(mode)
    named = {tool for tool in ALL_TOOLS if re.search(rf"\b{tool}\b", body)}
    assert named == RIGHTS[mode], (named - RIGHTS[mode], RIGHTS[mode] - named)


def test_worker_skill_asks_for_exactly_one_commit_and_no_push():
    _, body = skill("worker")
    assert "exactly one" in body.lower()
    assert "never push" in body.lower()


def test_overseer_skill_knows_both_modes_and_the_plan_rules():
    _, body = skill("overseer")
    text = " ".join(body.lower().split())
    # how the runner and `events.overseer_increment` tell the two situations apart
    assert "`event-`" in body and "an event in this hustle needs an overseer" in text
    assert "acceptance mode" in text and "definition of done" in text
    # what `Store.change_plan` and `plan.check` demand, and what keeps a split's worktree with its work
    assert "pass **every** increment" in text
    assert "first part keeps the original id" in text
    assert "depends, directly or through others, on every other increment" in text
    assert "approval" in text
    assert "commit nothing in acceptance mode" in text


def test_overseer_skill_tells_the_situations_the_prompt_creates():
    # every event kind `events.record` / `sync_proposals` can open must be one the skill tells how to decide
    from hustlemaster.events import PURPOSE

    _, body = skill("overseer")
    event_mode = body[body.index("## 2. Event mode"):body.index("## 3.")]
    for kind in PURPOSE:
        assert f"`{kind}`" in event_mode, kind


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
