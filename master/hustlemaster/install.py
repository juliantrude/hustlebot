"""Install the hustlemaster on this machine (ADR 0013): a systemd user unit that runs it from this checkout, the MCP
entry the user's interactive sessions reach it through (ADR 0014), and `/hustle` (the user plugin, from a
directory marketplace in this checkout). Idempotent: run it again after pulling or to change the port or address.

Everything lands in the user's own places, found through `HOME` and the XDG variables, so a scratch `HOME` gets a
whole installation without touching the real one."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .access import token_file

MASTER = Path(__file__).resolve().parent.parent
UNIT = "hustlemaster.service"
MCP_NAME = "hustlemaster"
MARKETPLACE = "hustlemaster"  # master/.claude-plugin/marketplace.json
PLUGIN = f"hustlebot@{MARKETPLACE}"


class InstallError(Exception):
    pass


@dataclass(frozen=True)
class Layout:
    home: Path  # runs, worktrees, the store and both tokens
    unit: Path

    @property
    def db(self) -> Path:
        return self.home / "hustlemaster.db"

    @property
    def interactive_token(self) -> Path:
        return self.home / "interactive-token"

    @property
    def dashboard_token(self) -> Path:
        return self.home / "dashboard-token"


def layout(env: dict[str, str]) -> Layout:
    home = Path(env["HOME"])
    data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return Layout(data / "hustlemaster", config / "systemd" / "user" / UNIT)


def _quote(arg: str) -> str:
    """One word of an `ExecStart=`/`Environment=` line: `%` and `$` would be expanded by systemd."""
    arg = arg.replace("%", "%%").replace("$", "$$")
    if arg and not any(c in arg for c in ' \t"\'\\;'):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unit_text(*, uv: str, master: Path, where: Layout, claude: str, path: str, port: int, bind: str,
              mcp_port: int, projects: str = "~/hustles", mcp_bind: str | None = None, mcp_external_port: int = 8772,
              mcp_names: list[str] | None = None, usage_cwd: str | None = None) -> str:
    command = [uv, "run", "--project", str(master), "--no-dev", "python", "-m", "hustlemaster.server",
               "--db", str(where.db), "--home", str(where.home), "--port", str(port), "--bind", bind,
               "--mcp-port", str(mcp_port), "--claude", claude, "--projects", projects]
    if mcp_bind:
        command += ["--mcp-bind", mcp_bind, "--mcp-external-port", str(mcp_external_port)]
        for name in mcp_names or []:
            command += ["--mcp-name", name]
    if usage_cwd:
        command += ["--usage-cwd", usage_cwd]
    return f"""[Unit]
Description=hustlemaster: runs the hustles on this machine (hustlebot v2)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
WorkingDirectory={_quote(str(master))}
# the installing shell's PATH: the projects' checks call their own tools, the sessions call git and claude
Environment={_quote("PATH=" + path)}
Environment=PYTHONUNBUFFERED=1
ExecStart={" ".join(_quote(a) for a in command)}
Restart=on-failure
RestartSec=10
# Sessions outlive a restart and the next process adopts them (merge.orphans); the default would kill the whole
# control group, sessions included. SIGTERM reaches the hustlemaster alone and lets merges under way finish.
KillMode=process
TimeoutStopSec=300

[Install]
WantedBy=default.target
"""


@dataclass
class Report:
    where: Layout
    url: str
    mcp_url: str
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    client: bool = False

    def text(self) -> str:
        if self.client:  # nothing of the hustlemaster lives here
            return "\n".join([*(f"✔ {d}" for d in self.done), *(f"– {s}" for s in self.skipped), "",
                              f"MCP: {self.mcp_url}"])
        lines = [*(f"✔ {d}" for d in self.done), *(f"– {s}" for s in self.skipped), "",
                 f"dashboard:   {self.url}",
                 f"login token: {self.where.dashboard_token}",
                 f"MCP:         {self.mcp_url} (interactive token in {self.where.interactive_token})",
                 f"store:       {self.where.db}",
                 f"logs:        journalctl --user -u {UNIT} -f"]
        return "\n".join(lines)


def _which(name: str, env: dict[str, str], given: str | None) -> str:
    found = shutil.which(given or name, path=env.get("PATH"))
    if not found:
        raise InstallError(f"`{given or name}` not found on PATH")
    # not resolved: version managers point a stable path (mise's `latest`) at whatever is current
    return os.path.abspath(found)


def _run(run, args: list[str], env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess:
    proc = run(args, env=env, cwd=env["HOME"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and proc.returncode:
        raise InstallError(f"`{' '.join(args[:4])} …` failed ({proc.returncode}): "
                           f"{(proc.stderr or proc.stdout).strip()[-600:]}")
    return proc


def _foreign_hustlebot(run, claude: str, env: dict[str, str]) -> str | None:
    """Another installed plugin named `hustlebot` (the old loop's): both would answer `/hustlebot:hustle`."""
    proc = _run(run, [claude, "plugin", "list", "--json"], env, check=False)
    try:
        plugins = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return next((p["id"] for p in plugins if p.get("id", "").startswith("hustlebot@") and p["id"] != PLUGIN), None)


def _mcp_entry(run, claude: str, env: dict[str, str], url: str, token: str) -> None:
    # remove first: `mcp add` keeps an existing entry, and a new token or address must replace it
    _run(run, [claude, "mcp", "remove", "-s", "user", MCP_NAME], env, check=False)
    _run(run, [claude, "mcp", "add", "-s", "user", "-t", "http", MCP_NAME, url,
               "-H", f"Authorization: Bearer {token}"], env)


def _plugin(run, claude: str, env: dict[str, str], master: Path, report: Report) -> None:
    foreign = _foreign_hustlebot(run, claude, env)
    if foreign:
        report.skipped.append(f"/hustle not installed: {foreign} is installed and also answers to "
                              f"`hustlebot`; uninstall it at the migration and install again, or meanwhile "
                              f"start sessions with --plugin-dir {master / 'user-plugin'}")
    else:
        _run(run, [claude, "plugin", "marketplace", "add", str(master)], env)
        _run(run, [claude, "plugin", "install", PLUGIN], env)
        report.done.append(f"/hustle ({PLUGIN}, read in place from {master / 'user-plugin'})")


def install_client(url: str, token: str, master: Path = MASTER, *, env: dict[str, str] | None = None,
                   claude: str | None = None, plugin: bool = True, run=subprocess.run) -> Report:
    """A machine that only plans (ADR 0016): `/hustle` and the MCP entry for the hustle host's external endpoint,
    with the interactive token from there. No service, no store."""
    env = dict(os.environ if env is None else env)
    claude = _which("claude", env, claude)
    if not url.startswith(("https://", "http://")):
        raise InstallError("the MCP address must be a URL, e.g. https://hustle.example.org/mcp")
    report = Report(layout(env), "", url, client=True)
    _mcp_entry(run, claude, env, url, token.strip())
    report.done.append(f"MCP entry `{MCP_NAME}` → {url} (user scope)")
    if plugin:
        _plugin(run, claude, env, Path(master).resolve(), report)
    else:
        report.skipped.append("/hustle not installed (--no-plugin)")
    return report


def install(master: Path = MASTER, *, env: dict[str, str] | None = None, port: int = 8770, bind: str = "127.0.0.1",
            mcp_port: int = 8771, claude: str | None = None, uv: str | None = None, systemd: bool = True,
            start: bool = True, plugin: bool = True, projects: str = "~/hustles", mcp_bind: str | None = None,
            mcp_external_port: int = 8772, mcp_names: list[str] | None = None, usage_cwd: str | None = None,
            run=subprocess.run) -> Report:
    env = dict(os.environ if env is None else env)
    master = Path(master).resolve()
    if not (master / "hustlemaster" / "server.py").is_file():
        raise InstallError(f"{master} is not the hustlemaster's checkout (master/ of the hustlebot repository)")
    claude, uv = _which("claude", env, claude), _which("uv", env, uv)
    where = layout(env)
    host = f"[{bind}]" if ":" in bind else bind
    report = Report(where, f"http://{host}:{port}/", f"http://127.0.0.1:{mcp_port}/mcp")

    where.home.mkdir(parents=True, exist_ok=True)
    where.home.chmod(0o700)  # the runs keep prompts and streams, the tokens sit beside them
    interactive = token_file(where.interactive_token)
    token_file(where.dashboard_token)
    report.done.append(f"home {where.home}")

    where.unit.parent.mkdir(parents=True, exist_ok=True)
    where.unit.write_text(unit_text(uv=uv, master=master, where=where, claude=claude, path=env.get("PATH", ""),
                                    port=port, bind=bind, mcp_port=mcp_port, projects=projects, mcp_bind=mcp_bind,
                                    mcp_external_port=mcp_external_port, mcp_names=mcp_names, usage_cwd=usage_cwd))
    if mcp_bind:
        report.done.append(f"external MCP endpoint on {mcp_bind}:{mcp_external_port}, interactive token only")
    report.done.append(f"unit {where.unit}")
    if systemd:
        _run(run, ["systemctl", "--user", "daemon-reload"], env)
        if start:
            _run(run, ["systemctl", "--user", "enable", UNIT], env)
            # restart, not start: a second install must bring a changed unit or pulled code into effect
            _run(run, ["systemctl", "--user", "restart", UNIT], env)
            report.done.append(f"{UNIT} enabled and (re)started")
        else:
            report.skipped.append(f"{UNIT} not started (start it with: systemctl --user enable --now {UNIT})")
    else:
        report.skipped.append("systemd not told (no daemon-reload, nothing started)")

    _mcp_entry(run, claude, env, report.mcp_url, interactive)
    report.done.append(f"MCP entry `{MCP_NAME}` for interactive sessions (user scope)")

    if plugin:
        _plugin(run, claude, env, master, report)
    else:
        report.skipped.append("/hustle not installed (--no-plugin)")
    return report


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client", metavar="MCP_URL",
                        help="install only /hustle and the MCP entry for a hustle host at this address (ADR 0016); "
                             "the interactive token comes from --token or the HUSTLE_TOKEN variable")
    parser.add_argument("--token", help="with --client: the hustle host's interactive token")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--bind", default="127.0.0.1", help="the web app's address; the MCP endpoint stays local")
    parser.add_argument("--mcp-port", type=int, default=8771)
    parser.add_argument("--projects", default="~/hustles", help="where the projects' repositories live")
    parser.add_argument("--mcp-bind", help="also serve MCP on this address for other machines (interactive only)")
    parser.add_argument("--mcp-external-port", type=int, default=8772)
    parser.add_argument("--mcp-name", action="append", default=[],
                        help="a host name the external MCP endpoint answers to (the reverse proxy's); repeatable")
    parser.add_argument("--usage-cwd", help="a folder Claude Code trusts, for reading /usage (default: a project)")
    parser.add_argument("--claude", help="the claude executable; default: the one on PATH")
    parser.add_argument("--no-systemd", dest="systemd", action="store_false",
                        help="write the unit but leave systemd alone")
    parser.add_argument("--no-start", dest="start", action="store_false", help="reload systemd, start nothing")
    parser.add_argument("--no-plugin", dest="plugin", action="store_false", help="leave /hustle out")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.client:
            token = args.token or os.environ.get("HUSTLE_TOKEN")
            if not token:
                raise InstallError("--client needs the hustle host's interactive token (--token or HUSTLE_TOKEN)")
            report = install_client(args.client, token, claude=args.claude, plugin=args.plugin)
        else:
            report = install(port=args.port, bind=args.bind, mcp_port=args.mcp_port, claude=args.claude,
                             systemd=args.systemd, start=args.start, plugin=args.plugin, projects=args.projects,
                             mcp_bind=args.mcp_bind, mcp_external_port=args.mcp_external_port,
                             mcp_names=args.mcp_name, usage_cwd=args.usage_cwd)
    except InstallError as err:
        print(f"install failed: {err}", file=sys.stderr)
        return 1
    print(report.text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
