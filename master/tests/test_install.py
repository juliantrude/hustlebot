"""The installer (ADR 0013): a systemd user unit, the interactive MCP entry and `/hustle`, all in a scratch `HOME`.

The `claude mcp`/`claude plugin` commands are real (they only write configuration, no session starts); systemd is
replaced by a recorder, because a user manager reads the real `HOME`'s units."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import socket
import stat
import subprocess
from pathlib import Path

import pytest

from hustlemaster import install as inst
from hustlemaster.access import Tokens
from hustlemaster.mcp_server import Endpoint
from hustlemaster.store import Store

MASTER = Path(__file__).resolve().parent.parent
CLAUDE = shutil.which("claude")
needs_claude = pytest.mark.skipif(CLAUDE is None, reason="needs the claude CLI")


def scratch(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    return {"HOME": str(home), "PATH": "/usr/bin:/bin:" + str(Path(CLAUDE or "/x").parent)}


def claude_json(env) -> dict:
    return json.loads((Path(env["HOME"]) / ".claude.json").read_text())


def plugins(env) -> list[dict]:
    out = subprocess.run([CLAUDE, "plugin", "list", "--json"], env=env, cwd=env["HOME"], capture_output=True,
                         text=True, check=True).stdout
    return json.loads(out)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def exec_start(unit: str) -> list[str]:
    line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    return shlex.split(line.removeprefix("ExecStart=").replace("%%", "%"))


# the unit

def test_layout_follows_home_and_xdg(tmp_path):
    plain = inst.layout({"HOME": "/h"})
    assert plain.home == Path("/h/.local/share/hustlemaster") and plain.db == plain.home / "hustlemaster.db"
    assert plain.unit == Path("/h/.config/systemd/user/hustlemaster.service")
    xdg = inst.layout({"HOME": "/h", "XDG_DATA_HOME": "/d", "XDG_CONFIG_HOME": "/c"})
    assert xdg.home == Path("/d/hustlemaster") and xdg.unit == Path("/c/systemd/user/hustlemaster.service")


def test_unit_runs_this_checkout_and_spares_the_sessions(tmp_path):
    where = inst.Layout(tmp_path / "data dir", tmp_path / "u")
    text = inst.unit_text(uv="/usr/bin/uv", master=MASTER, where=where, claude="/opt/c 100%/claude",
                          path="/a:/b", port=9000, bind="0.0.0.0", mcp_port=9001)
    argv = exec_start(text)
    assert argv[:6] == ["/usr/bin/uv", "run", "--project", str(MASTER), "--no-dev", "python"]
    opts = dict(zip(argv[8::2], argv[9::2]))
    assert opts == {"--db": str(where.db), "--home": str(where.home), "--port": "9000", "--bind": "0.0.0.0",
                    "--mcp-port": "9001", "--claude": "/opt/c 100%/claude", "--projects": "~/hustles"}  # spaces and % survive systemd
    assert '"/opt/c 100%%/claude"' in text  # systemd would read a bare % as a specifier
    assert "KillMode=process" in text.splitlines()  # a restart must not kill running hustlebots
    assert 'Environment="PATH=/a:/b"' in text or "Environment=PATH=/a:/b" in text
    assert "WantedBy=default.target" in text


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="needs systemd-analyze")
def test_systemd_accepts_the_unit(tmp_path):
    where = inst.Layout(tmp_path / "data", tmp_path / "hustlemaster.service")
    uv = shutil.which("uv") or "/usr/bin/uv"
    where.unit.write_text(inst.unit_text(uv=uv, master=MASTER, where=where, claude="/usr/bin/true",
                                         path="/usr/bin", port=8770, bind="127.0.0.1", mcp_port=8771))
    proc = subprocess.run(["systemd-analyze", "--user", "verify", str(where.unit)], capture_output=True, text=True)
    complaints = [line for line in proc.stderr.splitlines() if "hustlemaster.service" in line]
    assert proc.returncode == 0 and not complaints, proc.stderr


# the whole installation in a scratch HOME

@needs_claude
def test_install_into_a_scratch_home(tmp_path):
    env = scratch(tmp_path)
    report = inst.install(MASTER, env=env, systemd=False)
    where = inst.layout(env)
    assert where.unit.is_file() and exec_start(where.unit.read_text())[0] == shutil.which("uv", path=env["PATH"])
    assert stat.S_IMODE(where.home.stat().st_mode) == 0o700
    for token in (where.interactive_token, where.dashboard_token):
        assert stat.S_IMODE(token.stat().st_mode) == 0o600 and len(token.read_text().strip()) > 30
    assert where.interactive_token.read_text() != where.dashboard_token.read_text()

    entry = claude_json(env)["mcpServers"]["hustlemaster"]
    assert entry == {"type": "http", "url": "http://127.0.0.1:8771/mcp",
                     "headers": {"Authorization": f"Bearer {where.interactive_token.read_text().strip()}"}}
    [plugin] = plugins(env)
    assert plugin["id"] == "hustlebot@hustlemaster" and plugin["enabled"]
    assert Path(plugin["readFromFolder"]) == MASTER / "user-plugin"  # in place: a pull updates /hustle
    assert str(where.dashboard_token) in report.text() and "http://127.0.0.1:8770/" in report.text()
    assert not report.skipped[1:]  # only the systemd step was left out


@needs_claude
def test_install_again_keeps_tokens_and_moves_the_entry(tmp_path):
    env = scratch(tmp_path)
    inst.install(MASTER, env=env, systemd=False)
    where = inst.layout(env)
    tokens = where.interactive_token.read_text(), where.dashboard_token.read_text()
    where.db.write_text("")  # the store must survive a reinstall
    again = inst.install(MASTER, env=env, systemd=False, mcp_port=9999, port=9998, bind="::1")
    assert any(d.startswith("/hustle (") for d in again.done)  # its own earlier install is no reason to skip
    assert (where.interactive_token.read_text(), where.dashboard_token.read_text()) == tokens and where.db.exists()
    servers = claude_json(env)["mcpServers"]
    assert list(servers) == ["hustlemaster"] and servers["hustlemaster"]["url"] == "http://127.0.0.1:9999/mcp"
    assert "--port 9998" in where.unit.read_text() and len(plugins(env)) == 1


@needs_claude
def test_installed_entry_reaches_the_endpoint(tmp_path):
    # the entry Claude Code reads is the one the hustlemaster accepts: `claude mcp list` connects with its token
    env = scratch(tmp_path)
    port = free_port()
    inst.install(MASTER, env=env, systemd=False, plugin=False, mcp_port=port)
    store = Store()
    endpoint = Endpoint(store, Tokens(inst.layout(env).interactive_token.read_text().strip()), port).start()
    try:
        out = subprocess.run([CLAUDE, "mcp", "list"], env=env, cwd=env["HOME"], capture_output=True, text=True,
                             timeout=60).stdout
    finally:
        endpoint.stop()
    line = next(line for line in out.splitlines() if line.startswith("hustlemaster:"))
    assert "Connected" in line, out


@needs_claude
def test_old_hustlebot_plugin_is_left_alone(tmp_path):
    # the old loop's plugin is also named `hustlebot`; installing beside it would give two `/hustlebot:hustle`
    env = scratch(tmp_path)
    old = tmp_path / "old-marketplace"
    shutil.copytree(MASTER / "user-plugin", old / "plugin")
    (old / ".claude-plugin").mkdir()
    (old / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"name": "juliantrude", "owner": {"name": "x"}, "plugins": [{"name": "hustlebot", "source": "./plugin"}]}))
    for args in (["plugin", "marketplace", "add", str(old)], ["plugin", "install", "hustlebot@juliantrude"]):
        subprocess.run([CLAUDE, *args], env=env, cwd=env["HOME"], capture_output=True, check=True)
    report = inst.install(MASTER, env=env, systemd=False)
    assert [p["id"] for p in plugins(env)] == ["hustlebot@juliantrude"]
    assert any("hustlebot@juliantrude" in s and "--plugin-dir" in s for s in report.skipped)
    assert "hustlemaster" in claude_json(env)["mcpServers"]  # the MCP entry is installed all the same


# systemd and failures

class Recorder:
    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        code = 1 if self.fail and self.fail in args else 0
        out = "[]" if args[1:3] == ["plugin", "list"] else ""
        return subprocess.CompletedProcess(args, code, out, "boom" if code else "")


def test_systemd_is_reloaded_enabled_and_restarted(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    run = Recorder()
    report = inst.install(MASTER, env=env, claude="/usr/bin/true", run=run)
    systemctl = [c[2:] for c in run.calls if c[0] == "systemctl"]
    assert systemctl == [["daemon-reload"], ["enable", "hustlemaster.service"], ["restart", "hustlemaster.service"]]
    assert run.calls.index(["systemctl", "--user", "daemon-reload"]) < next(
        i for i, c in enumerate(run.calls) if c[1:3] == ["mcp", "add"])
    assert any("enabled and (re)started" in d for d in report.done)

    run = Recorder()
    inst.install(MASTER, env=env, claude="/usr/bin/true", start=False, run=run)
    assert [c[2:] for c in run.calls if c[0] == "systemctl"] == [["daemon-reload"]]


def test_failures_say_what_failed(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    with pytest.raises(inst.InstallError, match="restart.*boom"):
        inst.install(MASTER, env=env, claude="/usr/bin/true", run=Recorder(fail="restart"))
    with pytest.raises(inst.InstallError, match="not found"):
        inst.install(MASTER, env={**env, "PATH": str(tmp_path)}, claude="/usr/bin/true", run=Recorder())
    with pytest.raises(inst.InstallError, match="checkout"):
        inst.install(tmp_path, env=env, claude="/usr/bin/true", run=Recorder())


def test_install_skill_names_only_flags_the_installer_takes():
    skill = (MASTER / "user-plugin" / "skills" / "install" / "SKILL.md").read_text()
    # flags of `uv run`, `systemctl` and claude are not the installer's
    named = set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", skill)) - {"--project", "--no-dev", "--user", "--plugin-dir"}
    taken = {opt for action in inst.parser()._actions for opt in action.option_strings}
    assert named and named <= taken, named - taken
    assert "python -m hustlemaster.install" in skill and "dashboard-token" in skill


@needs_claude
def test_client_install_points_at_the_hustle_host(tmp_path):
    # ADR 0016: a planning machine gets /hustle and the entry for the host's external endpoint, nothing else
    env = scratch(tmp_path)
    report = inst.install_client("https://hustle.example.org/mcp", "secret-token\n", MASTER, env=env)
    entry = claude_json(env)["mcpServers"]["hustlemaster"]
    assert entry == {"type": "http", "url": "https://hustle.example.org/mcp",
                     "headers": {"Authorization": "Bearer secret-token"}}
    assert not inst.layout(env).home.exists() and not inst.layout(env).unit.exists()
    assert [p["id"] for p in plugins(env)] == ["hustlebot@hustlemaster"] and not report.skipped
    with pytest.raises(inst.InstallError):
        inst.install_client("hustle.example.org", "t", MASTER, env=env)


def test_unit_opens_the_external_endpoint_only_when_asked(tmp_path):
    where = inst.layout({"HOME": str(tmp_path)})
    plain = exec_start(inst.unit_text(uv="/uv", master=MASTER, where=where, claude="/c", path="/bin", port=1,
                                      bind="0.0.0.0", mcp_port=2))
    assert "--mcp-bind" not in plain and plain[plain.index("--projects") + 1] == "~/hustles"
    opened = exec_start(inst.unit_text(uv="/uv", master=MASTER, where=where, claude="/c", path="/bin", port=1,
                                       bind="0.0.0.0", mcp_port=2, mcp_bind="0.0.0.0", mcp_external_port=3,
                                       mcp_names=["hustle.example.org"]))
    assert opened[opened.index("--mcp-bind") + 1:] == ["0.0.0.0", "--mcp-external-port", "3", "--mcp-name",
                                                        "hustle.example.org"]
