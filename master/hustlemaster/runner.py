"""Starting a hustlebot (ADR 0011): one headless `claude -p` child process per increment, in its worktree.

The session gets everything on its command line and in its environment: the assembled prompt, the model, the
internal plugin (`--plugin-dir`), and an `--mcp-config` that reaches only the hustlemaster, with a per-session
token that decides its hustle and its rights (ADR 0014). Nothing about the increment is written into the repo.

Output is `stream-json`, not `json`: the result's `usage` is summed over all turns and its `iterations` hold only
the last call, so the peak context — the sizing signal — can only be read from the assistant messages, which
carry each call's own usage. The stream goes to a file in the run's directory, never a pipe: a pipe nobody reads
fills up and stalls the session, and the file survives a hustlemaster restart.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .access import RIGHTS, Tokens
from .mcp_server import TOKEN_ENV
from .model import Hustle, Increment
from .worktree import BRANCH_PREFIX, branch_name

REPORT_TAG = "hustlebot-report"
REPORT_STATUSES = ("done", "failed", "too-big", "blocked")
# The internal plugin is named like the installed one, so it replaces it for the session: a hustlebot sees its
# own skills and never the loop or planning skills (ADR 0011).
PLUGIN = "hustlebot"
SKILLS = {"worker": f"{PLUGIN}:work", "overseer": f"{PLUGIN}:oversee"}
MCP_SERVER = "hustlemaster"
# An unattended session must edit and run the project's checks without asking anyone; the old loop has always run
# with `--dangerously-skip-permissions` for the same reason. The worktree bounds where it works, the token bounds
# what it may do to plans.
PERMISSION_MODE = "bypassPermissions"

_REPORT = re.compile(rf"```{REPORT_TAG}[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)


@dataclass(frozen=True)
class Report:
    status: str
    evidence: str = ""
    proposals: list[str] = field(default_factory=list)


class ReportError(ValueError):
    pass


def parse_report(text: str) -> Report:
    """The last report block in the session's final message. A model may quote the format earlier on; the last
    block is the one it ended with."""
    blocks = _REPORT.findall(text or "")
    if not blocks:
        raise ReportError("no report block")
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError as err:
        raise ReportError(f"report block is not JSON: {err}") from None
    if not isinstance(data, dict):
        raise ReportError("report block is not an object")
    status = data.get("status")
    if status not in REPORT_STATUSES:
        raise ReportError(f"report status {status!r} is not one of {', '.join(REPORT_STATUSES)}")
    evidence = data.get("evidence", "")
    proposals = data.get("proposals", [])
    if not isinstance(evidence, str):
        raise ReportError("report evidence is not text")
    if not isinstance(proposals, list) or not all(isinstance(p, str) for p in proposals):
        raise ReportError("report proposals are not a list of text")
    return Report(status, evidence, proposals)


REPORT_FORMAT = f"""End your final message with exactly one report block, nothing after it:

```{REPORT_TAG}
{{"status": "done", "evidence": "how you verified it", "proposals": []}}
```

`status` is one of {", ".join(f"`{s}`" for s in REPORT_STATUSES)}. `evidence` names the checks you ran and what they
showed. `proposals` are plan changes you suggest for the hustle, as plain sentences; leave it empty if none."""


def prompt(hustle: Hustle, increment: Increment) -> str:
    return f"""You are a hustlebot in {increment.mode} mode. Follow the skill `{SKILLS[increment.mode]}`.

# Hustle: {hustle.title}

{hustle.brief.strip()}

# Increment {increment.id}: {increment.title}

{increment.prompt.strip()}

# Report

{REPORT_FORMAT}
"""


def allowed_tools(mode: str) -> list[str]:
    # `--strict-mcp-config` connects the server, but calling it is still a permission question (ADR 0011)
    return [f"mcp__{MCP_SERVER}__{action}" for action in sorted(RIGHTS[mode])]


def command(increment: Increment, text: str, *, plugin_dir: Path, mcp_config: Path, claude: str = "claude",
            permission_mode: str = PERMISSION_MODE) -> list[str]:
    # the prompt sits right behind `-p`: `--allowedTools` and `--plugin-dir` take several values and would swallow
    # a trailing positional
    return [
        claude, "-p", text,
        "--model", increment.model,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", permission_mode,
        "--mcp-config", str(mcp_config), "--strict-mcp-config",
        "--allowedTools", ",".join(allowed_tools(increment.mode)),
        "--plugin-dir", str(plugin_dir),
    ]


@dataclass
class Outcome:
    """What a finished session left: its report, or why there is none, and what it used."""

    status: str  # the report's status; `failed` when there is no usable report
    report: Report | None
    error: str | None  # why the report is missing or not to be trusted
    exit_code: int | None
    tokens: dict[str, dict[str, int]]  # per model id: input, output, cache_read, cache_creation
    peak_context: int  # the largest context one call of the main session carried, in tokens
    session_id: str | None = None
    num_turns: int = 0
    duration_ms: int = 0
    permission_denials: list = field(default_factory=list)
    api_error_status: int | None = None
    rate_limit: dict | None = None  # the stream's last `rate_limit_info`, for the usage reader (ADR 0012)


def _context(usage: dict) -> int:
    return sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def read_stream(lines: list[str], exit_code: int | None) -> Outcome:
    result: dict | None = None
    peak = 0
    rate_limit = None
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # a stream cut off mid-line when the process was killed
        kind = event.get("type")
        # subagents have their own context; the increment's size is the main session's
        if kind == "assistant" and not event.get("parent_tool_use_id"):
            peak = max(peak, _context(event.get("message", {}).get("usage") or {}))
        elif kind == "rate_limit_event":
            rate_limit = event.get("rate_limit_info")
        elif kind == "result":
            result = event
    if result is None:
        return Outcome("failed", None, f"no result (exit {exit_code})", exit_code, {}, peak, rate_limit=rate_limit)

    # a plain `json` result has no assistant messages; its last call is then the best available reading
    for it in (result.get("usage") or {}).get("iterations") or []:
        peak = max(peak, _context(it))
    tokens = {
        model: {"input": u.get("inputTokens", 0), "output": u.get("outputTokens", 0),
                "cache_read": u.get("cacheReadInputTokens", 0), "cache_creation": u.get("cacheCreationInputTokens", 0)}
        for model, u in (result.get("modelUsage") or {}).items()
    }
    report, error = None, None
    if result.get("is_error"):
        error = f"session ended with an error ({result.get('subtype')}): {result.get('result')}"
    else:
        try:
            report = parse_report(result.get("result", ""))
        except ReportError as err:
            error = str(err)
    if report is not None and exit_code not in (0, None):
        error = f"report given, but claude exited {exit_code}"
    status = report.status if report is not None and error is None else "failed"
    return Outcome(status, report, error, exit_code, tokens, peak,
                   session_id=result.get("session_id"), num_turns=result.get("num_turns", 0),
                   duration_ms=result.get("duration_ms", 0),
                   permission_denials=result.get("permission_denials") or [],
                   api_error_status=result.get("api_error_status"), rate_limit=rate_limit)


class Hustlebot:
    """One running session. The process ending is the signal that the increment finished."""

    def __init__(self, proc: subprocess.Popen, run_dir: Path, tokens: Tokens, token: str) -> None:
        self.proc = proc
        self.dir = run_dir
        self._tokens = tokens
        self._token = token

    def running(self) -> bool:
        return self.proc.poll() is None

    def wait(self, timeout: float | None = None) -> Outcome:
        self.proc.wait(timeout)
        return self._finish()

    def stop(self, grace: float = 10) -> Outcome:
        # the whole process group: the session's own children (a test run, a dev server) must not outlive it
        self._signal(signal.SIGTERM)
        try:
            self.proc.wait(grace)
        except subprocess.TimeoutExpired:
            self._signal(signal.SIGKILL)
            self.proc.wait()
        return self._finish()

    def _signal(self, sig: int) -> None:
        try:
            os.killpg(self.proc.pid, sig)
        except ProcessLookupError:
            pass

    def _finish(self) -> Outcome:
        # the token dies with the session, so a leftover config file opens nothing
        self._tokens.revoke(self._token)
        return read_run(self.dir, self.proc.returncode)


def read_run(run_dir: Path, exit_code: int | None = None) -> Outcome:
    """The outcome of a run from its directory, also for a session whose hustlemaster has restarted since."""
    stream = run_dir / "stream.jsonl"
    lines = stream.read_text().splitlines() if stream.exists() else []
    return read_stream(lines, exit_code)


class Runner:
    """Starts hustlebots. `mcp_config` is the endpoint's config (`Endpoint.mcp_config()`), `runs` the directory
    that keeps each run's prompt, config, stream and stderr."""

    def __init__(self, mcp_config: dict, tokens: Tokens, plugin_dir: Path | str, runs: Path | str, *,
                 claude: str = "claude", env: dict[str, str] | None = None,
                 permission_mode: str = PERMISSION_MODE) -> None:
        self.mcp_config = mcp_config
        self.tokens = tokens
        self.plugin_dir = Path(plugin_dir).resolve()
        self.runs = Path(runs).resolve()
        self.claude = claude
        self.env = env
        self.permission_mode = permission_mode

    def start(self, hustle: Hustle, increment: Increment, worktree: Path | str, *, allow_fable: bool) -> Hustlebot:
        if increment.model == "fable" and not allow_fable:
            # the plan was valid when it was stored; the setting may have been switched off since (ADR 0014)
            raise PermissionError(f"increment {increment.id} needs Fable, and allow_fable is off")
        run_dir = self._next_dir(hustle.id, increment.id)
        text = prompt(hustle, increment)
        (run_dir / "prompt.md").write_text(text)
        config = run_dir / "mcp.json"
        config.write_text(json.dumps(self.mcp_config))
        token = self.tokens.issue(increment.mode, hustle.id, increment.id)
        env = {**(self.env if self.env is not None else os.environ), TOKEN_ENV: token}
        argv = command(increment, text, plugin_dir=self.plugin_dir, mcp_config=config, claude=self.claude,
                       permission_mode=self.permission_mode)
        try:
            with open(run_dir / "stream.jsonl", "wb") as out, open(run_dir / "stderr.log", "wb") as err:
                # stdin closed: with an open one, `claude -p` waits for piped input first
                proc = subprocess.Popen(argv, cwd=worktree, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                        stderr=err, start_new_session=True)
        except BaseException:
            self.tokens.revoke(token)
            raise
        return Hustlebot(proc, run_dir, self.tokens, token)

    def _next_dir(self, hustle: str, increment: str) -> Path:
        # one directory per attempt, numbered, so a resumed increment keeps the earlier streams; ids are free
        # text, so they are made path-safe the way branch names are
        base = self.runs / branch_name(hustle, increment).removeprefix(BRANCH_PREFIX)
        base.mkdir(parents=True, exist_ok=True)
        n = 1 + max((int(p.name) for p in base.iterdir() if p.name.isdigit()), default=0)
        run_dir = base / str(n)
        run_dir.mkdir()
        return run_dir
