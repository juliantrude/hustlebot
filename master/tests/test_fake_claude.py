"""The fake `claude` is what every runner test stands on, so it is tested like production code."""

from __future__ import annotations

import json
import subprocess


def run(fake, *args, cwd, stdin=subprocess.DEVNULL):
    return subprocess.run(["claude", *args], cwd=cwd, env=fake.env(), stdin=stdin, capture_output=True, text=True)


def test_replays_recorded_results_in_order(fake_claude, tmp_path):
    fake_claude.script([{"result": "haiku-ok.json"}, {"result": "haiku-ok.json", "text": "second", "exit": 1}])
    first = run(fake_claude, "-p", "hi", "--output-format", "json", cwd=tmp_path)
    second = run(fake_claude, "-p", "again", cwd=tmp_path)
    assert first.returncode == 0
    assert json.loads(first.stdout)["result"] == "OK"
    assert json.loads(first.stdout)["modelUsage"]["claude-haiku-5-5"]["contextWindow"] == 1000000
    assert second.returncode == 1
    assert json.loads(second.stdout)["result"] == "second"


def test_records_argv_cwd_and_stdin(fake_claude, tmp_path):
    fake_claude.script([{"result": "haiku-ok.json"}, {"result": "haiku-ok.json"}])
    work = tmp_path / "work"
    work.mkdir()
    run(fake_claude, "-p", "prompt text", "--model", "haiku", cwd=work)
    subprocess.run(["claude", "-p", "x"], cwd=work, env=fake_claude.env(), input="piped", capture_output=True, text=True)
    calls = fake_claude.calls()
    assert calls[0] == {"argv": ["-p", "prompt text", "--model", "haiku"], "cwd": str(work), "stdin": ""}
    assert calls[1]["stdin"] == "piped"


def test_run_fakes_the_work_in_the_session_cwd(fake_claude, tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    commit = "echo done > a.txt && git add a.txt && git -c user.name=t -c user.email=t@t commit -qm 'increment A'"
    fake_claude.script([{"result": "haiku-ok.json", "run": commit}])
    assert run(fake_claude, "-p", "do A", cwd=repo).returncode == 0
    log = subprocess.run(["git", "log", "--format=%s"], cwd=repo, capture_output=True, text=True, check=True)
    assert log.stdout.strip() == "increment A"


def test_more_calls_than_steps_fail_loudly(fake_claude, tmp_path):
    fake_claude.script([{"result": "haiku-ok.json"}])
    run(fake_claude, "-p", "one", cwd=tmp_path)
    extra = run(fake_claude, "-p", "two", cwd=tmp_path)
    assert extra.returncode == 3
    assert "only 1 steps" in extra.stderr


def test_a_crash_without_result(fake_claude, tmp_path):
    fake_claude.script([{"exit": 1, "stderr": "API Error: overloaded"}])
    out = run(fake_claude, "-p", "x", cwd=tmp_path)
    assert (out.returncode, out.stdout, out.stderr.strip()) == (1, "", "API Error: overloaded")
