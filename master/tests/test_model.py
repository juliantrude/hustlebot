"""The prototype's hustle model; the rename from "chain" must not change what it decides."""

from __future__ import annotations

from hustlemaster.model import Hustle, Increment, shares


def hustle(id, active=True, weight=1, states="open"):
    incs = [Increment(id="A", title="a", model="opus", instructions=[], state=states.split()[0])]
    for n, state in enumerate(states.split()[1:]):
        incs.append(Increment(id=chr(66 + n), title="x", model="haiku", instructions=[], after=["A"], state=state))
    return Hustle(id=id, project="p", title=id, active=active, weight=weight, increments=incs)


def test_ready_waits_for_dependencies():
    assert [i.id for i in hustle("h", states="open open").ready()] == ["A"]
    assert [i.id for i in hustle("h", states="done open open").ready()] == ["B", "C"]


def test_status():
    assert hustle("h", states="done done").status() == "done"
    assert hustle("h", states="running open").status() == "running"
    assert hustle("h", states="blocked open").status() == "blocked"
    assert hustle("h", active=False, states="running").status() == "stopping"
    assert hustle("h", active=False).status() == "paused"


def test_shares_split_by_weight_among_active_hustles_with_work():
    pool = [hustle("a", weight=2), hustle("b"), hustle("off", active=False), hustle("stuck", states="blocked")]
    assert shares(pool, 12) == {"a": 8.0, "b": 4.0}


def test_points_in_use():
    assert hustle("h", states="running running running").points_in_use() == 4 + 1 + 1
