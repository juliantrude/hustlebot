"""Merging increments into `main` in scratch repos: one commit, rebase, re-verification, fast-forward, push, and
what a restarted hustlemaster finds in the worktrees it left behind."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from hustlemaster import merge, worktree
from hustlemaster.access import Tokens
from hustlemaster.merge import CONFLICT, MAIN_BUSY, MERGED, NO_COMMIT, VERIFY_FAILED, Merge
from hustlemaster.model import Increment
from hustlemaster.runner import Runner
from hustlemaster.store import Store

MCP_CONFIG = {"mcpServers": {}}


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", message)
    return sh(repo, "rev-parse", "HEAD").strip()


def report(status="done"):
    return f"Done.\n\n```hustlebot-report\n{json.dumps({'status': status, 'evidence': 'ok'})}\n```\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com")
    sh(repo, "config", "user.name", "t")
    commit(repo, {".gitignore": ".env\n", "README.md": "proj\n", "app.txt": "one\ntwo\nthree\n"}, "init")
    (repo / ".env").write_text("TOKEN=1\n")
    return repo


@pytest.fixture
def wts(tmp_path: Path) -> Path:
    return tmp_path / "wts"


def log(repo: Path, rev: str = "main") -> list[str]:
    return sh(repo, "log", "--format=%s", rev).split("\n")[:-1]


def gone(repo: Path, wt: worktree.Worktree) -> bool:
    return not wt.path.exists() and not sh(repo, "branch", "--list", wt.branch).strip()


def test_one_commit_fast_forwards_main_and_removes_the_worktree(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    sha = commit(wt.path, {"a.txt": "A\n"}, "A: add a")
    result = merge.merge(repo, wt)
    assert result == Merge(MERGED, sha)
    assert sh(repo, "rev-parse", "main").strip() == sha
    assert (repo / "a.txt").read_text() == "A\n"  # the main checkout's files moved with it
    assert sh(repo, "status", "--porcelain").strip() == ""
    assert gone(repo, wt)


def test_a_moved_main_is_rebased_onto_and_verified_there(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    commit(wt.path, {"a.txt": "A\n"}, "A: add a")
    commit(repo, {"user.txt": "mine\n"}, "user: own work")
    # the check sees the rebased tree: the user's file is there, and the copied ignored files still are
    result = merge.merge(repo, wt, verify="test -f user.txt && test -f a.txt && test -f .env")
    assert result.status == MERGED
    assert log(repo) == ["A: add a", "user: own work", "init"]
    assert sh(repo, "rev-list", "--merges", "main").strip() == ""


def test_several_commits_become_one_with_all_messages(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    commit(wt.path, {"a.txt": "A\n"}, "A: add a")
    commit(wt.path, {"a.txt": "A2\n"}, "A: fix a")
    commit(repo, {"user.txt": "mine\n"}, "user: own work")
    result = merge.merge(repo, wt)
    assert result.status == MERGED
    assert log(repo) == ["A: add a", "user: own work", "init"]
    assert "A: fix a" in sh(repo, "log", "-1", "--format=%B", "main")
    assert (repo / "a.txt").read_text() == "A2\n"


def test_no_commit_is_a_failed_attempt_and_keeps_the_worktree(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    (wt.path / "a.txt").write_text("never committed\n")
    commit(repo, {"user.txt": "mine\n"}, "user: own work")
    result = merge.merge(repo, wt)
    assert result.status == NO_COMMIT and NO_COMMIT in merge.COUNTS_AS_ATTEMPT
    assert "left no commit" in result.note()
    assert wt.path.exists() and (wt.path / "a.txt").exists()
    assert log(repo) == ["user: own work", "init"]


def test_a_conflict_leaves_branch_and_worktree_as_they_were(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    mine = commit(wt.path, {"app.txt": "one\nTWO by A\nthree\n"}, "A: change two")
    commit(repo, {"app.txt": "one\nTWO by user\nthree\n"}, "user: change two")
    main = sh(repo, "rev-parse", "main").strip()
    result = merge.merge(repo, wt)
    assert result.status == CONFLICT and result.conflicts == ["app.txt"]
    assert sh(repo, "rev-parse", "main").strip() == main
    assert sh(wt.path, "rev-parse", "HEAD").strip() == mine
    assert sh(wt.path, "status", "--porcelain").strip() == ""
    assert not (Path(sh(wt.path, "rev-parse", "--git-dir").strip()) / "rebase-merge").exists()
    assert "`app.txt`" in result.note() and "git rebase main" in result.note()


def test_the_same_increments_session_resolves_the_conflict_then_it_merges(repo, wts, tmp_path, fake_claude):
    store = Store()
    hid = store.submit(str(repo), "t", "brief", [
        Increment("A", "change two", "haiku", [], prompt="Change line two."),
        Increment("accept", "acceptance", "sonnet", [], after=["A"], prompt="check", mode="overseer"),
    ], author="user")
    hustle = store.hustle(hid)
    inc = hustle.increments[0]
    wt = worktree.create(repo, wts, hid, "A")
    commit(wt.path, {"app.txt": "one\nTWO by A\nthree\n"}, "A: change two")
    commit(repo, {"app.txt": "one\nTWO by user\nthree\n"}, "user: change two")
    conflict = merge.merge(repo, wt)
    assert conflict.status == CONFLICT

    # what a resolving session does: rebase, keep both sides, continue — one commit on top of main
    fix = ("git rebase main >/dev/null 2>&1; printf 'one\\nTWO by user and A\\nthree\\n' > app.txt && git add app.txt"
           " && GIT_EDITOR=true git rebase --continue")
    fake_claude.script([{"run": fix, "stream": "haiku-tools.jsonl", "text": report()}])
    runner = Runner(MCP_CONFIG, Tokens(), tmp_path, tmp_path / "runs", env=fake_claude.env())
    bot = runner.start(hustle, inc, wt.path, allow_fable=False, note=conflict.note())
    assert bot.wait(30).status == "done"
    assert "# This attempt" in (bot.dir / "prompt.md").read_text()
    assert "`app.txt`" in fake_claude.calls()[0]["argv"][1]

    result = merge.merge(repo, wt, verify="grep -q 'by user and A' app.txt")
    assert result.status == MERGED
    assert log(repo) == ["A: change two", "user: change two", "init"]


def test_a_failing_check_keeps_main_and_judges_only_the_commit(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    commit(wt.path, {"a.txt": "A\n"}, "A: add a")
    # leftovers outside the commit are not what goes into main, so they must not sway the check
    (wt.path / "stray.txt").write_text("untracked\n")
    (wt.path / "README.md").write_text("changed, not committed\n")
    assert merge.merge(repo, wt, verify="test ! -e stray.txt && grep -qx proj README.md").status == MERGED

    wt = worktree.create(repo, wts, "h", "B")
    commit(wt.path, {"b.txt": "B\n"}, "B: add b")
    main = sh(repo, "rev-parse", "main").strip()
    result = merge.merge(repo, wt, verify="echo 2 failed; exit 1")
    assert result.status == VERIFY_FAILED and "exited 1" in result.detail and "2 failed" in result.detail
    assert "2 failed" in result.note()
    assert sh(repo, "rev-parse", "main").strip() == main and wt.path.exists()


def test_a_check_that_hangs_is_cut_off(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    commit(wt.path, {"a.txt": "A\n"}, "A: add a")
    started = time.monotonic()
    result = merge.merge(repo, wt, verify="sleep 30", verify_timeout=0.5)
    assert result.status == VERIFY_FAILED and "did not finish" in result.detail
    assert time.monotonic() - started < 10


def test_local_changes_in_the_way_wait_without_costing_an_attempt(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    commit(wt.path, {"app.txt": "one\ntwo by A\nthree\n"}, "A: change two")
    (repo / "app.txt").write_text("the user is editing this\n")
    main = sh(repo, "rev-parse", "main").strip()
    result = merge.merge(repo, wt)
    assert result.status == MAIN_BUSY and MAIN_BUSY not in merge.COUNTS_AS_ATTEMPT
    assert result.note() is None
    assert sh(repo, "rev-parse", "main").strip() == main
    assert (repo / "app.txt").read_text() == "the user is editing this\n"
    sh(repo, "checkout", "app.txt")
    assert merge.merge(repo, wt).status == MERGED


def test_unrelated_local_changes_do_not_block(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    commit(wt.path, {"a.txt": "A\n"}, "A: add a")
    (repo / "README.md").write_text("the user is editing this\n")
    assert merge.merge(repo, wt).status == MERGED
    assert (repo / "README.md").read_text() == "the user is editing this\n"


def test_main_moves_as_a_ref_when_no_checkout_has_it(repo, wts):
    sh(repo, "checkout", "-q", "-b", "elsewhere")
    wt = worktree.create(repo, wts, "h", "A")
    sha = commit(wt.path, {"a.txt": "A\n"}, "A: add a")
    assert merge.merge(repo, wt).status == MERGED
    assert sh(repo, "rev-parse", "main").strip() == sha
    assert sh(repo, "branch", "--show-current").strip() == "elsewhere" and not (repo / "a.txt").exists()


def test_push_only_when_the_project_pushes(repo, wts, tmp_path):
    remote = tmp_path / "remote.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    sh(repo, "remote", "add", "origin", str(remote))
    sh(repo, "push", "-q", "origin", "main")

    wt = worktree.create(repo, wts, "h", "A")
    commit(wt.path, {"a.txt": "A\n"}, "A: add a")
    quiet = merge.merge(repo, wt)
    assert (quiet.status, quiet.pushed) == (MERGED, None)
    assert sh(remote, "log", "--format=%s", "main").split("\n")[0] == "init"

    wt = worktree.create(repo, wts, "h", "B")
    sha = commit(wt.path, {"b.txt": "B\n"}, "B: add b")
    loud = merge.merge(repo, wt, push=True)
    assert (loud.status, loud.pushed, loud.push_error) == (MERGED, True, None)
    assert sh(remote, "rev-parse", "main").strip() == sha


def test_a_failed_push_does_not_undo_the_merge(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    sha = commit(wt.path, {"a.txt": "A\n"}, "A: add a")
    result = merge.merge(repo, wt, push=True)  # there is no origin
    assert (result.status, result.commit, result.pushed) == (MERGED, sha, False)
    assert result.push_error and sh(repo, "rev-parse", "main").strip() == sha


def test_a_restart_after_the_fast_forward_finishes_the_merge(repo, wts):
    wt = worktree.create(repo, wts, "h", "A")
    sha = commit(wt.path, {"a.txt": "A\n"}, "A: add a")
    sh(repo, "merge", "-q", "--ff-only", wt.branch)  # the hustlemaster died right after this
    result = merge.merge(repo, wt)
    assert (result.status, result.commit) == (MERGED, sha)
    assert gone(repo, wt)


def test_parallel_merges_of_one_repo_line_up(repo, wts):
    trees = []
    for name in "ABCD":
        wt = worktree.create(repo, wts, "h", name)
        commit(wt.path, {f"{name}.txt": name}, f"{name}: add")
        trees.append(wt)
    results = [None] * len(trees)

    def run(i):
        results[i] = merge.merge(repo, trees[i])

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(trees))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert [r.status for r in results] == [MERGED] * 4
    assert sorted(log(repo)[:4]) == ["A: add", "B: add", "C: add", "D: add"]
    assert sh(repo, "rev-list", "--merges", "main").strip() == ""


# --- after a restart ----------------------------------------------------------------------------------------------


def test_orphans_are_merged_resumed_or_left_running(repo, wts, tmp_path, fake_claude):
    store = Store()
    work = [Increment(i, i, "haiku", [], prompt=i) for i in "ABCDE"]
    hid = store.submit(str(repo), "t", "brief", work + [
        Increment("accept", "acceptance", "sonnet", [], after=list("ABCDE"), prompt="check", mode="overseer"),
    ], author="user")
    hustle = store.hustle(hid)
    runs = tmp_path / "runs"
    runner = Runner(MCP_CONFIG, Tokens(), tmp_path, runs, env=fake_claude.env())
    fake_claude.script([
        {"exit": 1},                                                        # A: first attempt failed …
        {"stream": "haiku-tools.jsonl", "text": report()},                  # … the resume reported done
        {"stream": "haiku-tools.jsonl", "text": report("failed")},          # B: reported failure
        {"stream": "haiku-tools.jsonl", "text": report()},                  # E: cut off, see below
        {"sleep": 30, "stream": "haiku-tools.jsonl", "text": report()},     # D: still at work
    ])
    by = {i.id: i for i in hustle.increments}
    trees = {i: worktree.create(repo, wts, hid, i) for i in "ABCDE"}
    runner.start(hustle, by["A"], trees["A"].path, allow_fable=False).wait(30)
    runner.start(hustle, by["A"], trees["A"].path, allow_fable=False).wait(30)
    runner.start(hustle, by["B"], trees["B"].path, allow_fable=False).wait(30)
    # C: the worktree was made, the session never started
    cut = runner.start(hustle, by["E"], trees["E"].path, allow_fable=False)
    cut.wait(30)
    # the limit ended it mid-stream: no result event
    (cut.dir / "stream.jsonl").write_text((cut.dir / "stream.jsonl").read_text().splitlines()[0] + "\n")
    busy = runner.start(hustle, by["D"], trees["D"].path, allow_fable=False)
    stranger = worktree.create(repo, wts, "other-hustle", "X")
    try:
        found = {o.increment: o for o in merge.orphans(repo, [hustle], runs)}
        assert {i: o.action for i, o in found.items()} == {
            "A": merge.MERGE, "B": merge.RESUME, "C": merge.RESUME, "D": merge.RUNNING, "E": merge.RESUME,
        }
        assert found["A"].run.name == "2" and found["C"].run is None
        assert found["A"].worktree == trees["A"] and stranger not in [o.worktree for o in found.values()]
    finally:
        busy.stop(grace=1)
    # a dead session whose pid now belongs to someone else is not running
    other = subprocess.Popen(["sleep", "30"])
    try:
        (busy.dir / "pid").write_text(str(other.pid))
        assert {o.increment: o.action for o in merge.orphans(repo, [hustle], runs)}["D"] == merge.RESUME
    finally:
        other.kill()
        other.wait()
