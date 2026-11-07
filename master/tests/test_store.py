"""The hustle store: plans are validated, versioned and survive a restart; progress outlives plan edits."""

from __future__ import annotations

import pytest

from hustlemaster.model import Increment
from hustlemaster.plan import PlanError
from hustlemaster.store import Settings, Store


def inc(id, after=(), model="sonnet", mode="worker"):
    return Increment(id=id, title=id, model=model, instructions=[], after=list(after), prompt=f"do {id}", mode=mode)


def valid_plan():
    # A→B, C, then the acceptance increment after both branches
    return [inc("A"), inc("B", ["A"]), inc("C", model="haiku"), inc("ok", ["B", "C"], model="opus", mode="overseer")]


@pytest.fixture
def store():
    s = Store()
    yield s
    s.close()


def rejected(store, increments):
    with pytest.raises(PlanError) as err:
        store.submit("/repo", "t", "brief", increments, author="user")
    return err.value.problems


def test_submit_stores_an_inactive_hustle_at_version_1(store):
    hid = store.submit("/repo", "Ship it", "north star", valid_plan(), author="user")
    h = store.hustle(hid)
    assert (h.project, h.title, h.brief, h.active, h.weight, h.version) == ("/repo", "Ship it", "north star", False, 1, 1)
    assert h.confirmed_version is None
    assert [(i.id, i.after, i.model, i.mode, i.prompt) for i in h.increments] == [
        ("A", [], "sonnet", "worker", "do A"),
        ("B", ["A"], "sonnet", "worker", "do B"),
        ("C", [], "haiku", "worker", "do C"),
        ("ok", ["B", "C"], "opus", "overseer", "do ok"),
    ]
    assert all(i.state == "open" and i.attempts == 0 for i in h.increments)
    assert [i.id for i in h.ready()] == ["A", "C"]
    assert store.project("/repo").push is False


def test_cycle_is_rejected(store):
    plan = [inc("A", ["C"]), inc("B", ["A"]), inc("C", ["B"]), inc("ok", ["C"], mode="overseer")]
    assert any(p.startswith("dependency cycle: ") and "A" in p and "C" in p for p in rejected(store, plan))
    assert rejected(store, [inc("A", ["A"]), inc("ok", ["A"], mode="overseer")])[0] == "A: depends on itself"


def test_unknown_dependency_is_rejected(store):
    plan = valid_plan()
    plan[1].after.append("ghost")
    assert "B: depends on unknown increment 'ghost'" in rejected(store, plan)


def test_missing_acceptance_increment_is_rejected(store):
    msg = "no acceptance increment: an overseer increment that depends on all others"
    assert msg in rejected(store, [inc("A"), inc("B", ["A"])])
    # an overseer that misses one branch is a milestone, not the acceptance increment
    assert msg in rejected(store, [inc("A"), inc("C"), inc("ok", ["A"], mode="overseer")])
    # transitive dependencies suffice: a milestone overseer in the middle, the acceptance increment at the end
    store.submit("/repo", "t", "b", [inc("A"), inc("m", ["A"], mode="overseer"), inc("ok", ["m"], mode="overseer")], author="user")


def test_fable_is_rejected_while_not_allowed(store):
    plan = valid_plan()
    plan[0].model = "fable"
    assert "A: Fable is not allowed (allow_fable is off)" in rejected(store, plan)
    store.update_settings(allow_fable=True)
    assert store.submit("/repo", "t", "b", plan, author="user")


def test_increment_costing_more_than_the_total_is_rejected(store):
    store.update_settings(total_points=3)
    assert rejected(store, valid_plan()) == ["ok: opus costs 4 points, more than the total of 3"]


def test_other_malformed_plans_are_rejected(store):
    assert rejected(store, []) == ["the plan has no increments"]
    plan = valid_plan() + [inc("A")]
    plan[2].model, plan[2].mode, plan[2].prompt = "gpt", "boss", " "
    found = rejected(store, plan)
    assert {"duplicate increment id 'A'", "C: unknown model 'gpt'", "C: unknown mode 'boss'", "C: empty prompt"} <= set(found)


def test_rejected_plan_leaves_nothing_behind(store):
    hid = store.submit("/repo", "t", "b", valid_plan(), author="user")
    with pytest.raises(PlanError):
        store.change_plan(hid, [inc("A")], author="overseer")
    assert store.hustle(hid).version == 1
    assert len(store.hustles()) == 1


def test_every_change_is_a_new_version_and_old_ones_stay_readable(store):
    hid = store.submit("/repo", "t", "b", valid_plan(), author="user")
    split = [inc("A"), inc("B1", ["A"]), inc("B2", ["B1"]), inc("C", model="haiku"), inc("ok", ["B2", "C"], model="opus", mode="overseer")]
    assert store.change_plan(hid, split, author="overseer", note="split B") == 2
    assert [i.id for i in store.hustle(hid).increments] == ["A", "B1", "B2", "C", "ok"]
    assert [i.id for i in store.hustle(hid, version=1).increments] == ["A", "B", "C", "ok"]
    assert [(v.version, v.author, v.note) for v in store.versions(hid)] == [(1, "user", "submitted"), (2, "overseer", "split B")]


def test_progress_belongs_to_the_increment_not_the_version(store):
    hid = store.submit("/repo", "t", "b", valid_plan(), author="user")
    store.set_state(hid, "A", "done")
    store.count_attempt(hid, "B")
    store.count_attempt(hid, "B")
    store.note_peak_context(hid, "B", 300_000)
    store.note_peak_context(hid, "B", 120_000)
    edited = valid_plan()
    edited[1].prompt, edited[1].model = "do B, better", "opus"
    store.change_plan(hid, edited, author="user")
    b = {i.id: i for i in store.hustle(hid).increments}
    assert b["A"].state == "done"
    assert (b["B"].prompt, b["B"].model, b["B"].attempts, b["B"].peak_context) == ("do B, better", "opus", 2, 300_000)
    with pytest.raises(ValueError):
        store.set_state(hid, "A", "finished")
    with pytest.raises(KeyError):
        store.count_attempt(hid, "ghost")


def test_first_activation_confirms_the_baseline(store):
    hid = store.submit("/repo", "t", "b", valid_plan(), author="user")
    store.change_plan(hid, valid_plan(), author="user", note="edited before activation")
    store.set_active(hid, True)
    assert store.hustle(hid).confirmed_version == 2
    store.change_plan(hid, valid_plan(), author="overseer")
    store.set_active(hid, False)
    store.set_active(hid, True)
    assert store.hustle(hid).confirmed_version == 2  # reactivation is not a confirmation
    store.confirm(hid)
    assert store.hustle(hid).confirmed_version == 3


def test_weight_and_project_switches(store):
    hid = store.submit("/repo", "t", "b", valid_plan(), author="user")
    store.set_weight(hid, 3)
    assert store.hustle(hid).weight == 3
    with pytest.raises(ValueError):
        store.set_weight(hid, 0)
    with pytest.raises(KeyError):
        store.set_weight("nope", 2)
    store.set_project("/repo", push=True)
    store.set_project("/repo", copy_excludes=["*.sqlite"])
    store.set_project("/repo", verify="uv run pytest -q")
    p = store.project("/repo")
    assert (p.push, p.copy_excludes, p.verify) == (True, ["*.sqlite"], "uv run pytest -q")
    assert store.set_project("/repo", verify="").verify is None
    assert store.project("/repo").push is True



def test_submit_settles_the_project_switches_only_when_given(store):
    # `/hustle` settles push and the check while planning (ADR 0011); a later hustle without them keeps them
    first = store.submit("/repo", "t", "b", valid_plan(), author="user", push=True, verify="make check")
    assert (store.project("/repo").push, store.project("/repo").verify) == (True, "make check")
    second = store.submit("/repo", "u", "b", valid_plan(), author="user")
    assert (store.project("/repo").push, store.project("/repo").verify) == (True, "make check")
    store.submit("/repo", "v", "b", valid_plan(), author="user", push=False)
    assert (store.project("/repo").push, store.project("/repo").verify) == (False, "make check")
    assert store.hustle(first) and store.hustle(second)  # rewriting the project row keeps its hustles
    with pytest.raises(PlanError):
        store.submit("/other", "w", "b", [inc("A")], author="user", push=True)
    assert store.project("/other") is None  # a rejected plan settles nothing

def test_settings_defaults_and_validation(store):
    assert store.settings() == Settings()
    assert Settings().total_points == 20 and Settings().allow_fable is False
    store.update_settings(total_points=12, auto_approve="limited", auto_approve_growth=10)
    s = store.settings()
    assert (s.total_points, s.auto_approve, s.auto_approve_growth, s.session_threshold) == (12, "limited", 10, 85)
    for bad in ({"total_points": 0}, {"session_threshold": 101}, {"auto_approve": "sometimes"}):
        with pytest.raises(ValueError):
            store.update_settings(**bad)
    assert store.settings().total_points == 12


def test_everything_survives_a_restart(tmp_path):
    path = tmp_path / "hustlemaster.sqlite"
    first = Store(path)
    first.update_settings(allow_fable=True)
    hid = first.submit("/repo", "t", "b", valid_plan(), author="user")
    first.set_state(hid, "A", "running")
    first.set_active(hid, True)
    first.close()
    second = Store(path)
    h = second.hustle(hid)
    assert second.settings().allow_fable is True
    assert h.active and h.confirmed_version == 1 and h.increments[0].state == "running"
    assert h.points_in_use() == 2
    second.close()


def test_store_is_shared_safely_between_threads(tmp_path):
    # the dashboard, the MCP endpoint and the runner each use the store from their own thread
    import threading

    s = Store(tmp_path / "h.db")
    hid = s.submit("/repo", "t", "b", valid_plan(), author="user")
    errors = []

    def work(n):
        try:
            for k in range(20):
                s.propose(hid, f"{n}-{k}", author="worker")
                s.change_plan(hid, valid_plan(), author="overseer")
        except Exception as err:  # noqa: BLE001 - any failure fails the test below
            errors.append(err)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(s.proposals(hid)) == 120
    assert [v.version for v in s.versions(hid)] == list(range(1, 122))
