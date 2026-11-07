"""Prototype: the two behaviours ADR 0011 left open, observed with real Haiku sessions.

    uv run python prototypes/runner_probe.py /tmp/hb-probe

(a) Does a `-p` session ask for approval of servers from `--mcp-config`, does it expand
    `${HUSTLEMASTER_TOKEN}` in the header, and does it need `--allowedTools` to call them?
(b) How does a `--plugin-dir` plugin sit next to the user's installed plugins, also when its
    name collides with an installed one?

Each run uses `--output-format stream-json --verbose`, because the init message lists the MCP
servers with their status, the plugins and the skills: that is observed, not asked of the model.
Three runs, nothing outside the scratch directory is written.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from hustlemaster.access import Tokens
from hustlemaster.mcp_server import TOKEN_ENV, Endpoint
from hustlemaster.model import Increment
from hustlemaster.store import Store


def plan() -> list[Increment]:
    return [Increment("A", "probe", "haiku", [], prompt="probe"),
            Increment("ok", "accept", "haiku", [], after=["A"], prompt="accept", mode="overseer")]


def plugin(base: Path, name: str, word: str) -> Path:
    root = base / f"plugin-{name}"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": "0.0.1"}))
    skill = root / "skills" / "probe-worker"
    skill.mkdir(parents=True)
    skill.joinpath("SKILL.md").write_text(
        "---\nname: probe-worker\ndescription: Internal probe skill of the hustlemaster.\n---\n\n"
        f"The probe word is {word}.\n")
    return root


def run(name: str, cwd: Path, args: list[str], prompt: str, env: dict, out: Path) -> dict:
    cmd = ["claude", "-p", prompt, "--model", "haiku", "--output-format", "stream-json", "--verbose", *args]
    proc = subprocess.run(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    (out / f"{name}.jsonl").write_text(proc.stdout)
    (out / f"{name}.stderr").write_text(proc.stderr)
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip().startswith("{")]
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    hustle_tools = sorted(t for t in init.get("tools", []) if t.startswith("mcp__"))
    return {
        "run": name,
        "exit": proc.returncode,
        "mcp_servers": init.get("mcp_servers"),
        "mcp_tools": hustle_tools,
        "plugins": init.get("plugins"),
        "probe_skills": [s for s in init.get("skills", []) if "probe" in s or "advance-goal" in s],
        "skills_total": len(init.get("skills", [])),
        "permission_denials": result.get("permission_denials"),
        "result": result.get("result"),
    }


def main(base: Path) -> None:
    shutil.rmtree(base, ignore_errors=True)
    repo = base / "proj"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("probe\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    title = "PROBE-" + secrets.token_hex(3).upper()
    store = Store()
    hustle = store.submit(str(repo), title, "probe brief", plan(), author="user")
    tokens = Tokens()
    endpoint = Endpoint(store, tokens).start()
    try:
        config = base / "mcp.json"
        config.write_text(json.dumps(endpoint.mcp_config()))
        env = {**os.environ, TOKEN_ENV: tokens.issue("worker", hustle, "A")}
        mcp = ["--mcp-config", str(config), "--strict-mcp-config"]
        ask = "Call the read_hustle tool of the hustlemaster MCP server and reply with only the hustle's title."
        word = "WOMBAT-" + secrets.token_hex(2).upper()
        reports = [
            # (a) without any permission flag: is the server connected, is the call allowed?
            run("a-no-allow", repo, mcp, ask, env, base),
            # (a) with the tool allowed, plus (b) a plugin whose name collides with the installed `hustlebot`
            run("b-allow-collide", repo,
                [*mcp, "--allowedTools", "mcp__hustlemaster__read_hustle", "--plugin-dir", str(plugin(base, "hustlebot", word))],
                ask + " Then, on a second line, the probe word from the probe-worker skill, or NONE.", env, base),
            # (b) a plugin with its own name next to the installed ones
            run("c-own-name", repo, [*mcp, "--plugin-dir", str(plugin(base, "hustlemaster-internal", word))],
                "Reply with only the probe word from the probe-worker skill, or NONE.", env, base),
        ]
    finally:
        endpoint.stop()
    print(f"expected title {title}, probe word {word}")
    for r in reports:
        print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
