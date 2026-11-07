"""The service loop: one process that starts, settles, merges and recovers hustlebots — against the fake `claude` in
scratch repos. Every fake session runs the same shell step, which acts by the increment its worktree is for, so the
order in which parallel sessions reach the fake does not matter."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from hustlemaster import worktree
from hustlemaster.model import Increment
from hustlemaster.server import serve
from hustlemaster.service import RESTARTED_NOTE, Service
from hustlemaster.store import Store

from test_runner import report, sh

# B fails its first attempt; `accept` is a planned overseer and commits nothing; the rest commit a file named after
# themselves. `SLOW` sleeps first, so a test can catch it running.
WORK = """n=$(basename "$PWD")
case $n in
  SLOW) sleep 3;;
  B) test -f wip || { touch wip; exit 1; };;
  accept|event-*) exit 0;;
esac
echo "$n" > "$n.txt" && git add "$n.txt"
git diff --cached --quiet || git commit -qm "$n"
"""


def step(status="done", evidence="tests pass", proposals=(), sleep=0.0) -> dict:
    return {"run": WORK, "stream": "haiku-tools.jsonl", "text": report(status, evidence, proposals), "sleep": sleep}


def plan(*ids, model="haiku", accept="sonnet") -> list[Increment]:
    incs = [Increment(i, f"Step {i}", model, [], prompt=f"Implement {i}.") for i in ids]
    return incs + [Increment("accept", "acceptance", accept, [], after=list(ids), mode="overseer",
                             prompt="Check the Definition of Done against `main`.")]


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
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "hustles.db")


@pytest.fixture
def make(fake_claude, tmp_path, store):
    made = []

    def make(s: Store = store) -> Service:
        svc = Service(s, tmp_path / "home", env=fake_claude.env(), plugin_dir=tmp_path)
        made.append(svc)
        return svc

    yield make
    for svc in made:
        svc.stop()


def submit(store: Store, repo: Path, *ids, verify="git log -1", **kw) -> str:
    hid = store.submit(str(repo), "h", "North star: files.", plan(*ids, **kw), author="user", verify=verify)
    store.set_active(hid, True)
    return hid


def until(svc: Service, done, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        svc.step()
        svc.drain()
        if done():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def states(store: Store, hid: str) -> dict[str, tuple[str, int]]:
    return {i.id: (i.state, i.attempts) for i in store.hustle(hid).increments}


def commits(repo: Path) -> list[str]:
    return sh(repo, "log", "--format=%s", "main").split()


def test_parallel_workers_merge_and_the_acceptance_overseer_closes_the_hustle(make, fake_claude, store, repo):
    hid = submit(store, repo, "SLOW", "C")
    fake_claude.script([step(), step(), step()])
    svc = make()

    decision = svc.step()
    assert {s.increment for s in decision.starts} == {"SLOW", "C"}
    assert {s.increment for s in svc.live.sessions()} == {"SLOW", "C"}
    assert states(store, hid)["SLOW"] == ("running", 0)

    until(svc, lambda: store.hustle(hid).status() == "done")
    assert sorted(commits(repo)) == ["C", "SLOW", "init"]
    assert states(store, hid) == {"SLOW": ("done", 0), "C": ("done", 0), "accept": ("done", 0)}
    calls = fake_claude.calls()
    # the acceptance overseer started only after both, on its own model, and was done without a commit to merge
    assert Path(calls[2]["cwd"]).name == "accept"
    assert calls[2]["argv"][calls[2]["argv"].index("--model") + 1] == "sonnet"
    assert worktree.listed(repo) == [] and svc.live.sessions() == []
    assert {e["increment"] for e in svc.live.events if e["kind"] == "done"} == {"SLOW", "C", "accept"}
    assert store.hustle(hid).increments[0].peak_context > 0


def test_a_failing_increment_resumes_then_an_event_overseer_takes_it_over(make, fake_claude, store, repo):
    hid = submit(store, repo, "B")
    fake_claude.script([
        step(),  # B's first attempt dies without a result (WORK)
        step("failed", "the wip is still there"),  # B's second: no commit, reported failed
        step("done", "rewrote B's prompt"),  # the event overseer
        step(), step(),  # B once more, then the acceptance
    ])
    svc = make()
    until(svc, lambda: store.hustle(hid).status() == "done")

    calls = fake_claude.calls()
    assert "did not finish" in calls[1]["argv"][1]  # the resumed session learnt why
    overseer = calls[2]["argv"]
    assert calls[2]["cwd"] == calls[0]["cwd"]  # in B's worktree, with B's work
    assert overseer[overseer.index("--model") + 1] == "opus" and "overseer mode" in overseer[1]
    assert "rewrote B's prompt" in calls[3]["argv"][1]
    [event] = store.events(hid)
    assert (event.kind, event.state, event.increment) == ("failed", "handled", "B")
    assert states(store, hid)["B"] == ("done", 0) and "B" in commits(repo)


def test_report_proposals_are_kept_once(make, fake_claude, store, repo):
    hid = submit(store, repo, "SLOW", "C")
    fake_claude.script([step(proposals=["Add a CLI"]), step(proposals=["Add a CLI"]),
                        step("done", "added nothing"), step("done", "added nothing"), step()])
    svc = make()
    svc.step()
    # SLOW also sent its proposal over MCP while it ran; C only put it in its report
    store.propose(hid, "Add a CLI", author="worker:SLOW", increment="SLOW")
    until(svc, lambda: store.hustle(hid).status() == "done")

    kept = store.proposals(hid)
    assert sorted(p.author for p in kept) == ["worker:C", "worker:SLOW"]
    # each went to an overseer as it came in, the MCP one while SLOW still ran
    found = store.events(hid)
    assert [(e.kind, e.state) for e in found] == [("proposal", "handled")] * 2
    assert sorted(p for e in found for p in e.proposals) == sorted(p.id for p in kept)
    assert worktree.listed(repo) == []  # a proposal overseer's own worktree goes with it


def test_a_merge_the_users_checkout_refuses_waits_without_an_attempt(make, fake_claude, store, repo):
    hid = submit(store, repo, "C")
    fake_claude.script([step(), step()])
    (repo / "C.txt").write_text("the user's own\n")  # untracked, in the way of the fast-forward
    svc = make()
    until(svc, lambda: svc.retries or False)
    assert states(store, hid)["C"] == ("running", 0) and "C" not in commits(repo)

    (repo / "C.txt").unlink()
    until(svc, lambda: store.hustle(hid).status() == "done")
    assert states(store, hid)["C"] == ("done", 0) and "C" in commits(repo)


def test_a_restart_waits_for_running_sessions_merges_finished_ones_and_resumes_cut_off_ones(
        make, fake_claude, store, repo, tmp_path):
    hid = submit(store, repo, "SLOW", "B", "C")
    fake_claude.script([step() for _ in range(7)])
    old = make()
    old.step()  # SLOW, B and C start; then the process goes away without reaping them
    time.sleep(1)  # B died without a result, C finished, SLOW still runs
    event = store.open_event(hid, "proposal", detail="d")
    store.set_event_state(event, "running")

    new = make(Store(tmp_path / "hustles.db"))
    new.recover()
    assert set(k for _, k in new.jobs) == {"SLOW", "C"}  # adopted: one to wait for, one to merge
    assert store.event(event).state == "open"
    assert states(store, hid)["B"] == ("open", 0)  # cut off: no attempt

    until(new, lambda: all(states(store, hid)[i][0] == "done" for i in ("SLOW", "B", "C")))
    resumed = [c for c in fake_claude.calls() if Path(c["cwd"]).name == "B"][-1]
    assert RESTARTED_NOTE in resumed["argv"][1]
    assert sorted(commits(repo)) == ["B", "C", "SLOW", "init"]
    assert all(states(store, hid)[i][1] == 0 for i in ("SLOW", "B", "C"))


def test_a_failure_reported_before_the_restart_still_counts(make, fake_claude, store, repo, tmp_path):
    hid = submit(store, repo, "C")
    fake_claude.script([step("failed", "lint failed"), step(), step()])
    make().step()
    time.sleep(1)  # it reported; the process went away before settling it

    new = make(Store(tmp_path / "hustles.db"))
    new.recover()
    until(new, lambda: store.hustle(hid).status() == "done")
    assert states(store, hid)["C"] == ("done", 1)
    assert "lint failed" in fake_claude.calls()[1]["argv"][1]


def test_an_increment_that_cannot_start_is_blocked_not_retried(make, fake_claude, store, tmp_path):
    hid = submit(store, tmp_path / "gone", "C")  # the repository was removed after the plan was stored
    svc = make()
    svc.step()
    svc.step()
    assert states(store, hid)["C"] == ("blocked", 0)
    assert "could not start" in store.take_note(hid, "C")
    assert fake_claude.calls() == []


def test_fable_switched_off_holds_its_increments_until_switched_on(make, fake_claude, store, repo):
    store.update_settings(allow_fable=True)
    hid = submit(store, repo, "C", model="fable")
    store.update_settings(allow_fable=False)  # switched off after the plan was stored
    svc = make()
    svc.step()
    svc.step()
    assert states(store, hid)["C"] == ("open", 0)
    assert fake_claude.calls() == []

    fake_claude.script([step(), step()])
    store.update_settings(allow_fable=True)
    until(svc, lambda: store.hustle(hid).status() == "done")
    assert fake_claude.calls()[0]["argv"][fake_claude.calls()[0]["argv"].index("--model") + 1] == "fable"


def test_one_process_serves_the_views_while_the_loop_runs(make, fake_claude, store, repo):
    hid = submit(store, repo, "SLOW")
    fake_claude.script([step(), step()])
    svc = make()
    httpd, _ = serve(store, 0, live=svc.live)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        svc.start(every=0.05, usage_every=None)
        url = f"http://127.0.0.1:{httpd.server_port}/api/state"
        seen = set()
        deadline = time.monotonic() + 30
        while store.hustle(hid).status() != "done" and time.monotonic() < deadline:
            state = json.load(urllib.request.urlopen(url))
            seen |= {s["increment"] for h in state["hustles"] for s in h["sessions"]}
            time.sleep(0.05)
        assert store.hustle(hid).status() == "done"
        assert "SLOW" in seen
    finally:
        httpd.shutdown()


def test_the_usage_screen_comes_from_the_sessions_claude(tmp_path, store, monkeypatch):
    # the unit's PATH may lack the user's `claude`; the one the sessions are started with is known to work
    claude = tmp_path / "bin" / "my-claude"
    claude.parent.mkdir()
    claude.write_text(f"#!/bin/sh\nexec {sys.executable} {Path(__file__).parent / 'fake_tui.py'}\n")
    claude.chmod(0o755)
    monkeypatch.setenv("FAKE_TUI_MODE", "ready")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # no `claude` to be found by name
    svc = Service(store, tmp_path / "home", claude=str(claude), usage_cwd=tmp_path)
    try:
        svc.refresh_usage()
        assert svc.usage.estimate("session") is not None
    finally:
        svc.stop()
