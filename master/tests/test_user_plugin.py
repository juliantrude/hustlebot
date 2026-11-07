"""The user-facing plugin with `/hustle` (ADR 0014): what it tells the planning session must match what the
hustlemaster accepts, or plans get rejected or settings silently dropped."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import anyio
import pytest

from hustlemaster.access import RIGHTS, Tokens
from hustlemaster.mcp_server import build
from hustlemaster.model import POINTS
from hustlemaster.store import Store

MASTER = Path(__file__).resolve().parent.parent
USER_PLUGIN = MASTER / "user-plugin"
ALL_TOOLS = set().union(*RIGHTS.values())


def skill() -> tuple[dict[str, str], str]:
    text = (USER_PLUGIN / "skills" / "hustle" / "SKILL.md").read_text()
    match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    assert match, "SKILL.md has no frontmatter"
    meta = dict(line.split(": ", 1) for line in match.group(1).splitlines())
    return meta, match.group(2)


def flat(body: str) -> str:
    return " ".join(body.lower().split())


def tool_schemas() -> dict[str, dict]:
    async def go():
        return await build(Store(), Tokens()).list_tools()

    return {t.name: t.input_schema for t in anyio.run(go)}


def test_plugin_replaces_the_installed_one_by_name():
    manifest = json.loads((USER_PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "hustlebot"
    meta, body = skill()
    assert meta["name"] == "hustle" and meta["description"] and body.strip()


def test_skill_names_exactly_the_interactive_tools():
    _, body = skill()
    named = {tool for tool in ALL_TOOLS if re.search(rf"\b{tool}\b", body)}
    assert named == RIGHTS["interactive"], (named - RIGHTS["interactive"], RIGHTS["interactive"] - named)


def test_skill_passes_what_submit_hustle_takes():
    # a parameter the skill names but the tool lacks is dropped silently; one the tool needs must be named
    _, body = skill()
    submit = tool_schemas()["submit_hustle"]
    params = set(submit["properties"])
    section = body[body.index("## 5. Submit"):body.index("## 6.")]
    named = {p for p in params if f"`{p}`" in section}
    assert named == params, params - named
    item = submit["$defs"]["IncrementSpec"]["properties"]
    assert all(f"`{field}`" in section for field in item), set(item) - {f for f in item if f"`{f}`" in section}


def test_skill_knows_the_points_and_models():
    text = flat(skill()[1])
    for model, points in POINTS.items():
        assert f"{model} {points}" in text, model
        assert f"`{model}`" in text, model


def test_skill_plans_what_plan_check_demands():
    text = flat(skill()[1])
    assert "600k tokens" in text
    assert "mode `overseer`" in text and "id `accept`" in text
    assert "depending (directly or through others) on every other increment" in text
    assert "`allow_fable` is on" in text
    assert "self-contained prompt" in text and "disjoint files" in text
    assert "the checklist order is not one" in text


def test_skill_converts_a_goal_md_and_leaves_the_old_loop_alone():
    _, body = skill()
    convert = flat(body[body.index("## 6. Convert"):body.index("## 7.")])
    assert "checked increments (`- [x]`) are done" in convert
    assert "every unchecked increment (`- [ ]`) becomes an increment" in convert
    assert "`**next action:**`" in convert
    assert "leave `goal.md` as it is" in convert
    assert "hustle-scheduler.sh disarm" in convert
    # a conversion skips the interview, where the check is settled; without this line a Haiku run dropped `verify`
    assert "settle the check command (§2) and pushing (§4)" in convert
    # the old loop's skills are not part of the new flow
    assert not re.search(r"/(advance-goal|grill-goal|loop-setup|generate-plan)\b", body)


def test_skill_leaves_the_dashboard_its_rights():
    text = flat(skill()[1])
    assert "arrives **paused**" in text
    for setting in ("activation", "weights", "approvals", "total points", "`allow_fable`", "auto-approve"):
        assert setting in text.split("are the dashboard's alone")[0].rsplit("- ", 1)[1], setting
    assert "do not shrink the change to dodge the approval" in text
    assert "ask explicitly and recommend `off` or `branch`" in text


@pytest.mark.skipif(shutil.which("claude") is None, reason="no claude executable")
def test_claude_accepts_the_plugin():
    done = subprocess.run(["claude", "plugin", "validate", str(USER_PLUGIN)], capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
