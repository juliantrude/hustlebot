"""Starting hustlebots against the fake `claude`: the command line, the session's environment, the report and
what the session used, read back from recorded streams."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from hustlemaster import worktree
from hustlemaster.access import Tokens
from hustlemaster.mcp_server import TOKEN_ENV
from hustlemaster.model import Increment
from hustlemaster.runner import (REPORT_FORMAT, Runner, ReportError, allowed_tools, parse_report, read_run,
                                 read_stream)
from hustlemaster.store import Store

MCP_CONFIG = {"mcpServers": {"hustlemaster": {"type": "http", "url": "http://127.0.0.1:1/mcp",
                                              "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"}}}}


def report(status="done", evidence="pytest: 3 passed", proposals=()):
    block = json.dumps({"status": status, "evidence": evidence, "proposals": list(proposals)})
    return f"Did the work.\n\n```hustlebot-report\n{block}\n```\n"


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com")
    sh(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("proj\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", "init")
    return repo


@pytest.fixture
def store() -> Store:
    return Store()


def submit(store: Store, repo: Path, *increments: Increment) -> str:
    plan = list(increments) + [Increment("accept", "acceptance", "sonnet", [], after=[i.id for i in increments],
                                         prompt="check the Definition of Done", mode="overseer")]
    return store.submit(str(repo), "Tiny hustle", "North star: a README that says hello.", plan, author="user")


@pytest.fixture
def setup(fake_claude, repo, store, tmp_path):
    hustle_id = submit(store, repo, Increment("A", "Say hello", "haiku", [], prompt="Add hello to README.md."))
    tokens = Tokens()
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    runner = Runner(MCP_CONFIG, tokens, plugin, tmp_path / "runs", env=fake_claude.env())
    wt = worktree.create(repo, tmp_path / "wts", hustle_id, "A")
    return runner, tokens, store.hustle(hustle_id), wt


def by_id(hustle, increment_id):
    return next(i for i in hustle.increments if i.id == increment_id)


# --- the report block ---------------------------------------------------------------------------------------------

def test_the_last_report_block_counts():
    text = report("failed", "quoted early") + "more work\n" + report("done", "tests pass", ["split B"])
    assert parse_report(text).status == "done"
    assert parse_report(text).proposals == ["split B"]


def test_the_format_the_prompt_teaches_parses():
    assert parse_report(REPORT_FORMAT).status == "done"


@pytest.mark.parametrize("text, problem", [
    ("all done, no block", "no report block"),
    ("```hustlebot-report\n{status: done}\n```", "not JSON"),
    ("```hustlebot-report\n[\"done\"]\n```", "not an object"),
    ("```hustlebot-report\n{\"status\": \"finished\"}\n```", "not one of"),
    ("```hustlebot-report\n{\"status\": \"done\", \"proposals\": \"split\"}\n```", "proposals"),
    ("```hustlebot-report\n{\"status\": \"done\", \"evidence\": [1]}\n```", "evidence"),
])
def test_unusable_report_blocks_are_refused(text, problem):
    with pytest.raises(ReportError, match=problem):
        parse_report(text)


def test_a_report_with_only_a_status():
    assert parse_report("```hustlebot-report\n{\"status\": \"too-big\"}\n```").proposals == []


# --- the command line ---------------------------------------------------------------------------------------------

def test_tools_follow_the_rights_of_the_mode():
    assert allowed_tools("worker") == ["mcp__hustlemaster__propose_change", "mcp__hustlemaster__read_hustle"]
    assert allowed_tools("overseer") == ["mcp__hustlemaster__change_plan", "mcp__hustlemaster__read_hustle",
                                         "mcp__hustlemaster__read_settings"]


def test_start_runs_claude_in_the_worktree_with_the_session_setup(setup, fake_claude, tmp_path):
    runner, tokens, hustle, wt = setup
    seen = tmp_path / "token-seen"
    fake_claude.script([{"stream": "haiku-tools.jsonl", "text": report(), "run": f'printf %s "${TOKEN_ENV}" > {seen}'}])
    bot = runner.start(hustle, by_id(hustle, "A"), wt.path, allow_fable=False)
    outcome = bot.wait(30)

    call, = fake_claude.calls()
    argv = call["argv"]
    assert call["cwd"] == str(wt.path)
    assert call["stdin"] == ""  # closed, not an open pipe
    assert argv[0] == "-p" and argv[1] == (bot.dir / "prompt.md").read_text()
    assert argv[argv.index("--model") + 1] == "haiku"
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--allowedTools") + 1] == "mcp__hustlemaster__propose_change,mcp__hustlemaster__read_hustle"
    assert argv[argv.index("--plugin-dir") + 1] == str((tmp_path / "plugin").resolve())

    # the config holds the variable, the environment the token; the token was a worker's of this hustle and dies
    # with the session
    config = Path(argv[argv.index("--mcp-config") + 1])
    assert json.loads(config.read_text()) == MCP_CONFIG
    token = seen.read_text()
    assert token and token not in config.read_text()
    assert tokens.caller(token) is None
    assert outcome.status == "done" and outcome.error is None


def test_the_prompt_carries_brief_increment_skill_and_report_format(setup, fake_claude):
    runner, _, hustle, wt = setup
    fake_claude.script([{"stream": "haiku-tools.jsonl", "text": report()}])
    bot = runner.start(hustle, by_id(hustle, "A"), wt.path, allow_fable=False)
    bot.wait(30)
    text = (bot.dir / "prompt.md").read_text()
    for part in ("worker mode", "`hustlebot:work`", "# Hustle: Tiny hustle", "North star: a README that says hello.",
                 "# Increment A: Say hello", "Add hello to README.md.", "```hustlebot-report"):
        assert part in text


def test_an_overseer_gets_an_overseer_token_and_tools(setup, fake_claude, tmp_path):
    runner, tokens, hustle, wt = setup
    seen = tmp_path / "token-seen"
    fake_claude.script([{"stream": "haiku-tools.jsonl", "text": report(), "sleep": 1,
                         "run": f'printf %s "${TOKEN_ENV}" > {seen}'}])
    bot = runner.start(hustle, by_id(hustle, "accept"), wt.path, allow_fable=False)
    deadline = time.monotonic() + 10
    while not seen.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    caller = tokens.caller(seen.read_text())
    assert (caller.role, caller.hustle, caller.increment) == ("overseer", hustle.id, "accept")
    bot.wait(30)
    argv = fake_claude.calls()[0]["argv"]
    assert "mcp__hustlemaster__change_plan" in argv[argv.index("--allowedTools") + 1]
    assert "`hustlebot:oversee`" in argv[1]


def test_fable_is_refused_while_it_is_not_allowed(setup, fake_claude):
    runner, tokens, hustle, wt = setup
    fable = Increment("F", "big", "fable", [], prompt="think hard")
    with pytest.raises(PermissionError, match="allow_fable"):
        runner.start(hustle, fable, wt.path, allow_fable=False)
    assert fake_claude.calls() == []
    assert tokens._callers == {}


# --- what the session left ----------------------------------------------------------------------------------------

def test_outcome_of_a_recorded_session(setup, fake_claude):
    runner, _, hustle, wt = setup
    commit = "echo hello >> README.md && git commit -qam 'A: say hello'"
    fake_claude.script([{"stream": "haiku-tools.jsonl", "text": report("done", "grep hello README.md", ["add B"]),
                         "run": commit}])
    outcome = runner.start(hustle, by_id(hustle, "A"), wt.path, allow_fable=False).wait(30)
    assert outcome.status == "done"
    assert outcome.report.evidence == "grep hello README.md" and outcome.report.proposals == ["add B"]
    assert outcome.exit_code == 0
    # recorded run: four calls with 23217, 23323, 23434, 23551 tokens of context; summed usage would say 93525
    assert outcome.peak_context == 23551
    assert outcome.tokens == {"claude-haiku-5-5": {"input": 8, "output": 238, "cache_read": 78980,
                                                   "cache_creation": 14537}}
    assert outcome.num_turns == 4 and outcome.session_id
    assert outcome.rate_limit["unifiedWindows"]["five_hour"]["utilization"] == 0.47
    assert sh(wt.path, "log", "-1", "--format=%s").strip() == "A: say hello"


def assistant(context: int, parent: str | None = None) -> str:
    return json.dumps({"type": "assistant", "parent_tool_use_id": parent, "message": {"usage": {
        "input_tokens": 10, "cache_read_input_tokens": context - 10, "cache_creation_input_tokens": 0}}})


def result_event(text: str, **extra) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                       "usage": {"iterations": [{"input_tokens": 1, "cache_read_input_tokens": 29_999}]},
                       "modelUsage": {}, **extra})


def test_peak_context_is_the_largest_main_session_call():
    # 50k before a compaction, 30k after, a subagent's 900k does not count, and the result's last call is 30k
    lines = [assistant(50_000), assistant(30_000), assistant(900_000, parent="toolu_1"), result_event(report())]
    assert read_stream(lines, 0).peak_context == 50_000


def test_a_plain_json_result_falls_back_to_its_last_call(fake_claude, tmp_path):
    recorded = json.loads((Path(__file__).parent / "fixtures/results/haiku-ok.json").read_text())
    recorded["result"] = report()
    outcome = read_stream([json.dumps(recorded)], 0)
    assert outcome.status == "done" and outcome.peak_context == 2 + 9012 + 19543


@pytest.mark.parametrize("lines, exit_code, error", [
    ([], 1, "no result (exit 1)"),
    ([assistant(1000), '{"type": "assistant", "mess'], -15, "no result (exit -15)"),
    ([result_event("I am done.")], 0, "no report block"),
    ([result_event("API Error: overloaded", is_error=True, subtype="error_during_execution")], 1,
     "session ended with an error (error_during_execution): API Error: overloaded"),
    ([result_event(report())], 1, "report given, but claude exited 1"),
])
def test_without_a_usable_report_the_attempt_failed(lines, exit_code, error):
    outcome = read_stream(lines, exit_code)
    assert outcome.status == "failed"
    assert outcome.error == error


def test_a_reported_failure_keeps_its_report():
    outcome = read_stream([result_event(report("too-big", "needs three parts", ["split A into A1, A2, A3"]))], 0)
    assert (outcome.status, outcome.error) == ("too-big", None)
    assert outcome.report.proposals == ["split A into A1, A2, A3"]


def test_a_crash_leaves_stderr_and_a_failed_outcome(setup, fake_claude):
    runner, tokens, hustle, wt = setup
    fake_claude.script([{"exit": 1, "stderr": "API Error: overloaded"}])
    bot = runner.start(hustle, by_id(hustle, "A"), wt.path, allow_fable=False)
    outcome = bot.wait(30)
    assert (outcome.status, outcome.exit_code, outcome.error) == ("failed", 1, "no result (exit 1)")
    assert (bot.dir / "stderr.log").read_text().strip() == "API Error: overloaded"
    assert tokens._callers == {}


def test_each_attempt_gets_its_own_run_directory(setup, fake_claude):
    runner, _, hustle, wt = setup
    fake_claude.script([{"exit": 1}, {"stream": "haiku-tools.jsonl", "text": report()}])
    first = runner.start(hustle, by_id(hustle, "A"), wt.path, allow_fable=False)
    first.wait(30)
    second = runner.start(hustle, by_id(hustle, "A"), wt.path, allow_fable=False)
    second.wait(30)
    assert (first.dir.name, second.dir.name) == ("1", "2")
    assert first.dir.parent == second.dir.parent
    assert read_run(first.dir).status == "failed" and read_run(second.dir).status == "done"


def test_run_directories_stay_inside_runs_whatever_the_ids(setup, fake_claude, tmp_path):
    runner, _, hustle, wt = setup
    fake_claude.script([{"exit": 1}])
    sneaky = Increment("../../escape", "x", "haiku", [], prompt="x")
    bot = runner.start(hustle, sneaky, wt.path, allow_fable=False)
    bot.wait(30)
    assert bot.dir.resolve().is_relative_to((tmp_path / "runs").resolve())


def test_the_outcome_can_be_read_again_after_a_restart(setup, fake_claude):
    runner, _, hustle, wt = setup
    fake_claude.script([{"stream": "haiku-tools.jsonl", "text": report()}])
    bot = runner.start(hustle, by_id(hustle, "A"), wt.path, allow_fable=False)
    live = bot.wait(30)
    later = read_run(bot.dir)  # a new hustlemaster knows the directory, not the process
    assert (later.status, later.peak_context, later.tokens) == (live.status, live.peak_context, live.tokens)


def test_stop_ends_the_session_and_its_children(setup, fake_claude, tmp_path):
    runner, tokens, hustle, wt = setup
    pidfile = tmp_path / "child.pid"
    fake_claude.script([{"run": f"sleep 60 >/dev/null 2>&1 & echo $! > {pidfile}", "sleep": 60,
                         "stream": "haiku-tools.jsonl"}])
    bot = runner.start(hustle, by_id(hustle, "A"), wt.path, allow_fable=False)
    deadline = time.monotonic() + 10
    while not (pidfile.exists() and pidfile.read_text().strip()) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert bot.running()
    child = int(pidfile.read_text())
    outcome = bot.stop(grace=5)
    assert not bot.running()
    assert outcome.status == "failed" and outcome.exit_code < 0
    assert tokens._callers == {}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the session's child outlived it")
