"""Failures and event overseers (ADR 0015): what becomes of an increment after a session, which event overseer is
due, and how its end is written back — in the store, in simulated hustles and against the fake `claude`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hustlemaster import events, merge as merging, worktree
from hustlemaster.access import Tokens
from hustlemaster.events import DONE, MERGE, OVERSEER, PARK, RESUME, RETRY, Step, settle
from hustlemaster.model import Increment
from hustlemaster.runner import Outcome, Report, Runner
from hustlemaster.scheduler import Session, Start, choose
from hustlemaster.store import Settings, Store

from test_runner import MCP_CONFIG, report, sh  # noqa: F401  (fixtures below reuse the runner's)
from test_scheduler import hustle as sim_hustle


def outcome(status="done", evidence="pytest passed", error=None) -> Outcome:
    rep = None if error else Report(status, evidence, [])
    return Outcome(status if not error else "failed", rep, error, 0, {}, 0)


CONFLICT = merging.Merge(merging.CONFLICT, conflicts=["a.py"])
VERIFY_FAILED = merging.Merge(merging.VERIFY_FAILED, detail="1 failed")


# --- settle: the table of ADR 0015 --------------------------------------------------------------------------------


@pytest.mark.parametrize("attempts, out, merge, parked, expected", [
    (0, outcome("failed"), None, True, Step(PARK)),  # the limit cut it off: no attempt
    (1, outcome("failed"), None, True, Step(PARK)),
    (0, outcome("done"), None, False, Step(MERGE)),
    (0, outcome("done"), merging.Merge(merging.MERGED, commit="abc"), False, Step(DONE)),
    (1, outcome("done"), merging.Merge(merging.MAIN_BUSY), False, Step(RETRY)),  # the user's fault, no attempt
])
def test_steps_that_cost_no_attempt(attempts, out, merge, parked, expected):
    assert settle(attempts, out, merge, parked=parked) == expected


def test_a_first_failure_resumes_and_tells_the_next_session_why():
    step = settle(0, outcome("failed", "tests: 2 failed in test_api.py"))
    assert (step.action, step.attempt) == (RESUME, True)
    assert "2 failed in test_api.py" in step.note


def test_a_session_without_report_resumes_with_the_reason():
    assert "no report block" in settle(0, outcome(error="no report block")).note


def test_the_second_failure_starts_the_event_overseer():
    assert settle(1, outcome("failed")).action == OVERSEER
    assert settle(1, outcome("failed")).event == "failed"


def test_merge_failures_resume_with_the_merge_note_then_go_to_the_overseer():
    first = settle(0, outcome("done"), CONFLICT)
    assert (first.action, first.attempt, first.note) == (RESUME, True, CONFLICT.note())
    assert (settle(1, outcome("done"), CONFLICT).action, settle(1, outcome("done"), CONFLICT).event) == \
        (OVERSEER, "conflict")
    assert settle(1, outcome("done"), VERIFY_FAILED).event == "failed"
    assert settle(0, outcome("done"), merging.Merge(merging.NO_COMMIT)).action == RESUME


@pytest.mark.parametrize("status, kind", [("too-big", "too-big"), ("blocked", "failed")])
def test_reports_a_resume_cannot_fix_go_to_the_overseer_at_once(status, kind):
    step = settle(0, outcome(status, "needs a schema migration first"))
    assert (step.action, step.attempt, step.event) == (OVERSEER, True, kind)
    assert "schema migration" in step.note


# --- the store side -----------------------------------------------------------------------------------------------


@pytest.fixture
def store() -> Store:
    return Store()


def plan(*specs: tuple[str, list[str]]) -> list[Increment]:
    incs = [Increment(i, f"do {i}", "haiku", [], after=after, prompt=f"Implement {i}.") for i, after in specs]
    return incs + [Increment("accept", "acceptance", "sonnet", [], after=[i.id for i in incs], prompt="check",
                             mode="overseer")]


def submit(store: Store, *specs) -> str:
    hid = store.submit("/p", "h", "brief", plan(*specs), author="user")
    store.set_active(hid, True)
    return hid


def inc(store: Store, hid: str, iid: str) -> Increment:
    return next(i for i in store.hustle(hid).increments if i.id == iid)


def ready(store: Store, hid: str) -> list[str]:
    return [i.id for i in store.hustle(hid).ready()]


def test_a_resume_counts_the_attempt_and_leaves_its_note_once(store):
    hid = submit(store, ("A", []))
    store.set_state(hid, "A", "running")
    assert events.record(store, hid, "A", settle(0, outcome("failed", "lint failed"))) is None
    assert (inc(store, hid, "A").state, inc(store, hid, "A").attempts) == ("open", 1)
    assert "lint failed" in store.take_note(hid, "A")
    assert store.take_note(hid, "A") is None


def test_merge_and_retry_leave_the_increment_out_of_the_ready_set(store):
    hid = submit(store, ("A", []))
    store.set_state(hid, "A", "running")
    for step in (Step(MERGE), Step(RETRY)):
        events.record(store, hid, "A", step)
        assert inc(store, hid, "A").state == "running"
    events.record(store, hid, "A", Step(DONE))
    assert inc(store, hid, "A").state == "done"


def test_an_event_holds_its_increment_and_its_dependants_while_the_rest_goes_on(store):
    hid = submit(store, ("A", []), ("B", ["A"]), ("C", []))
    store.set_state(hid, "A", "running")
    events.record(store, hid, "A", settle(0, outcome("failed")))
    event_id = events.record(store, hid, "A", settle(1, outcome("failed", "import error")))
    event = store.event(event_id)
    assert (event.kind, event.increment, event.state) == ("failed", "A", "open")
    assert "import error" in event.detail and "(2 so far)" in event.detail
    assert inc(store, hid, "A").held == "overseer"
    assert ready(store, hid) == ["C"]


def test_a_handled_event_frees_the_increment_with_fresh_attempts_and_the_overseers_word(store):
    hid = submit(store, ("A", []), ("B", ["A"]))
    event = store.event(events.record(store, hid, "A", settle(1, outcome("failed"))))
    store.set_event_state(event.id, "running")
    assert events.close(store, event, outcome("done", "rewrote the prompt: use the v2 client")) == "handled"
    assert (inc(store, hid, "A").attempts, inc(store, hid, "A").held, ready(store, hid)) == (0, "", ["A"])
    assert "use the v2 client" in store.take_note(hid, "A")


@pytest.mark.parametrize("status, state", [("blocked", "handled"), ("failed", "failed"), ("too-big", "failed")])
def test_an_overseer_that_cannot_help_blocks_only_the_increment_and_its_dependants(store, status, state):
    hid = submit(store, ("A", []), ("B", ["A"]), ("C", []))
    event = store.event(events.record(store, hid, "A", settle(0, outcome("too-big"))))
    assert events.close(store, event, outcome(status, "needs an API key only the user has")) == state
    assert inc(store, hid, "A").state == "blocked"
    assert ready(store, hid) == ["C"]
    assert store.hustle(hid).status() == "waiting"  # C can still run


def test_an_overseer_cut_off_at_the_limit_keeps_its_event_open(store):
    hid = submit(store, ("A", []))
    event = store.event(events.record(store, hid, "A", settle(0, outcome("too-big"))))
    store.set_event_state(event.id, "running")
    assert events.close(store, event, outcome(error="limit"), parked=True) == "open"
    assert (store.event(event.id).state, inc(store, hid, "A").held) == ("open", "overseer")


def test_an_overseer_that_split_the_increment_away_leaves_no_note(store):
    hid = submit(store, ("A", []))
    event = store.event(events.record(store, hid, "A", settle(0, outcome("too-big"))))
    store.change_plan(hid, plan(("A1", []), ("A2", ["A1"])), author="overseer", note="split A")
    assert events.close(store, event, outcome("done", "split A")) == "handled"
    assert ready(store, hid) == ["A1"]


def test_open_proposals_become_one_event_and_are_decided_with_it(store):
    hid = submit(store, ("A", []))
    p1 = store.propose(hid, "add a migration step", author="worker", increment="A")
    p2 = store.propose(hid, "B should use sonnet", author="worker")
    event_id = events.sync_proposals(store, hid)
    event = store.event(event_id)
    assert (event.kind, event.increment, event.proposals) == ("proposal", None, [p1, p2])
    assert "#1 by worker (increment A): add a migration step" in event.detail
    assert events.sync_proposals(store, hid) is None  # taken, not open any more
    assert ready(store, hid) == ["A"]  # a proposal holds no increment
    events.close(store, event, outcome("done"))
    assert [p.state for p in store.proposals(hid)] == ["handled", "handled"]


def test_a_failed_proposal_overseer_drops_its_proposals(store):
    hid = submit(store, ("A", []))
    store.propose(hid, "x", author="worker")
    event = store.event(events.sync_proposals(store, hid))
    events.close(store, event, outcome(error="crashed"))
    assert [p.state for p in store.proposals(hid)] == ["dropped"]


def test_events_survive_a_restart(tmp_path):
    first = Store(tmp_path / "h.db")
    hid = submit(first, ("A", []))
    events.record(first, hid, "A", settle(0, outcome("too-big")))
    first.close()
    again = Store(tmp_path / "h.db")
    assert [e.kind for e in again.events(hid, states=("open",))] == ["too-big"]
    assert inc(again, hid, "A").held == "overseer"


def test_unknown_kinds_and_states_are_refused(store):
    hid = submit(store, ("A", []))
    with pytest.raises(ValueError):
        store.open_event(hid, "panic")
    event_id = store.open_event(hid, "failed", increment="A")
    with pytest.raises(ValueError):
        store.set_event_state(event_id, "gone")


# --- which overseer is due, on which model ------------------------------------------------------------------------


@pytest.mark.parametrize("allow_fable, total, model", [(True, 20, "fable"), (False, 20, "opus"), (True, 3, "sonnet"),
                                                        (False, 1, "haiku")])
def test_overseers_run_on_the_strongest_allowed_model_that_fits_the_total(allow_fable, total, model):
    assert events.overseer_model(Settings(total_points=total, allow_fable=allow_fable)) == model


def test_one_overseer_per_active_hustle_oldest_event_first(store):
    a, b, off = submit(store, ("A", []), ("A2", [])), submit(store, ("B", [])), submit(store, ("X", []))
    store.set_active(off, False)
    first = store.open_event(a, "too-big", increment="A")
    store.open_event(a, "too-big", increment="A2")
    store.open_event(b, "failed", increment="B")
    store.open_event(off, "failed", increment="X")
    due = events.due(store.hustles(), store.events(), [], Settings())
    assert due == [Start(a, events.key(first), "opus"), Start(b, events.key(first + 2), "opus")]


def test_no_second_overseer_while_one_changes_the_plan(store):
    hid = submit(store, ("A", []), ("B", []))
    first = store.open_event(hid, "too-big", increment="A")
    store.open_event(hid, "failed", increment="B")
    assert events.due(store.hustles(), store.events(), [Session(hid, events.key(first), "opus")], Settings()) == []
    store.set_event_state(first, "running")  # e.g. right after a restart, before it is resumed
    assert events.due(store.hustles(), store.events(), [], Settings()) == []
    store.set_event_state(first, "handled")
    assert [s.increment for s in events.due(store.hustles(), store.events(), [], Settings())] == \
        [events.key(first + 1)]
    store.set_state(hid, "accept", "running")  # the planned acceptance overseer
    assert events.due(store.hustles(), store.events(), [], Settings()) == []


def test_event_keys_round_trip():
    assert events.event_id(events.key(17)) == 17
    assert events.event_id("A") is None and events.event_id("event-x") is None


# --- scheduling event overseers -----------------------------------------------------------------------------------


def test_a_due_overseer_goes_first_in_its_hustle_and_counts_against_its_share():
    a, b = sim_hustle("a"), sim_hustle("b")
    d = choose([a, b], 8, overseers=[Start("a", "event-1", "opus")])
    assert d.starts[0] == Start("a", "event-1", "opus")
    assert sum(4 if s.model == "opus" else 1 for s in d.starts if s.hustle == "a") == 4  # a's share is 4
    assert len([s for s in d.starts if s.hustle == "b"]) == 4


def test_a_hustle_whose_only_work_waits_for_its_overseer_still_gets_a_share():
    held = sim_hustle("w", models="haiku")
    held.increments[0].held = "overseer"
    d = choose([held, sim_hustle("b")], 8, overseers=[Start("w", "event-1", "opus")])
    assert d.starts[0] == Start("w", "event-1", "opus")


def test_an_overseer_bigger_than_the_free_points_holds_them():
    d = choose([sim_hustle("a", states="running " + "open " * 9), sim_hustle("b", states="running " * 2)], 4,
               overseers=[Start("a", "event-1", "opus")])
    assert (d.starts, d.waiting) == ([], Start("a", "event-1", "opus"))


# --- a simulated hustle -------------------------------------------------------------------------------------------


def test_simulation_a_stubborn_increment_goes_to_the_overseer_while_the_hustle_goes_on(store):
    """B fails until an overseer rewrites it; A, C and D (after B) and the acceptance are driven like the service
    will: choose, run, settle, record, overseers through `due`."""
    hid = submit(store, ("A", []), ("B", []), ("C", ["A"]), ("D", ["B"]))
    settings = Settings(total_points=6)
    fixed = False
    log = []
    for _ in range(20):
        hustles = store.hustles()
        open_events = store.events(states=("open", "running"))
        due = events.due(hustles, open_events, [], settings)
        decision = choose(hustles, settings.total_points, overseers=due)
        if not decision.starts:
            break
        for start in decision.starts:  # every session of a tick finishes before the next tick
            if (eid := events.event_id(start.increment)) is not None:
                event = store.event(eid)
                store.set_event_state(eid, "running")
                log.append(f"overseer {event.increment}")
                fixed = True
                events.close(store, event, outcome("done", "rewrote B's prompt"))
                continue
            current = inc(store, hid, start.increment)
            store.set_state(hid, current.id, "running")
            failing = current.id == "B" and not fixed
            log.append(f"{current.id} {'failed' if failing else 'done'}")
            out = outcome("failed" if failing else "done")
            step = settle(current.attempts, out)
            if step.action == MERGE:
                step = settle(current.attempts, out, merging.Merge(merging.MERGED, commit="c"))
            events.record(store, hid, current.id, step)
    assert all(i.state == "done" for i in store.hustle(hid).increments)
    assert log.count("B failed") == 2 and log.index("overseer B") > log.index("B failed")
    # A and C were done before B was fixed: the failure held nothing else back
    assert log.index("C done") < log.index("overseer B") < log.index("D done")


# --- against the fake `claude` ------------------------------------------------------------------------------------


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


def test_two_failed_sessions_then_an_overseer_in_the_same_worktree(fake_claude, repo, store, tmp_path):
    hid = store.submit(str(repo), "h", "North star: hello.", plan(("A", [])), author="user")
    store.set_active(hid, True)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    runner = Runner(MCP_CONFIG, Tokens(), plugin, tmp_path / "runs", env=fake_claude.env())
    fake_claude.script([
        {"stream": "haiku-tools.jsonl", "text": report("failed", "pytest: ImportError in hello.py"),
         "run": "echo wip > wip.txt"},
        {"stream": "haiku-tools.jsonl", "text": report("failed", "still the ImportError")},
        {"stream": "haiku-tools.jsonl", "text": report("done", "split nothing; rewrote the prompt")},
    ])
    wt = worktree.create(repo, tmp_path / "wts", hid, "A")

    for expected in (RESUME, OVERSEER):
        current = inc(store, hid, "A")
        out = runner.start(store.hustle(hid), current, wt.path, allow_fable=False,
                           note=store.take_note(hid, "A")).wait(30)
        step = settle(current.attempts, out)
        assert step.action == expected
        events.record(store, hid, "A", step)

    settings = store.settings()
    [due] = events.due(store.hustles(), store.events(), [], settings)
    event = store.event(events.event_id(due.increment))
    overseer = events.overseer_increment(event, store.hustle(hid), due.model)
    # the affected increment's worktree already exists: `create` hands it back with its work
    place = worktree.create(repo, tmp_path / "wts", hid, event.increment or overseer.id)
    assert (place.path / "wip.txt").exists()
    store.set_event_state(event.id, "running")
    out = runner.start(store.hustle(hid), overseer, place.path, allow_fable=settings.allow_fable).wait(30)
    assert events.close(store, event, out) == "handled"

    calls = fake_claude.calls()
    assert "ImportError in hello.py" in calls[1]["argv"][1]  # the resumed session learnt why
    last = calls[2]["argv"]
    assert calls[2]["cwd"] == str(wt.path)
    assert last[last.index("--model") + 1] == "opus"
    assert "mcp__hustlemaster__change_plan" in last[last.index("--allowedTools") + 1]
    assert "overseer mode" in last[1] and "Implement A." in last[1] and "still the ImportError" in last[1]
    assert (inc(store, hid, "A").attempts, ready(store, hid)) == (0, ["A"])
    assert "rewrote the prompt" in store.take_note(hid, "A")
