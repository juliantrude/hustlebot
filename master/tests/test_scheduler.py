"""Who gets free session points (ADR 0010): fr weights over the hustles with work, work-conserving, the hustle
furthest below its share first, and every start through the budget (ADR 0008)."""

from __future__ import annotations

import random
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from hustlemaster.budget import Budget
from hustlemaster.model import POINTS, Hustle, Increment
from hustlemaster.runner import Outcome
from hustlemaster.scheduler import Session, Start, choose, in_use, running_sessions, shares
from hustlemaster.store import Settings
from hustlemaster.usage import SESSION, WEEK, Reading, Usage, Window

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 7, 4, 30, tzinfo=BERLIN)  # the week line stands at about 44 %
WEEK_RESETS = datetime(2026, 11, 10, 22, 0, tzinfo=BERLIN)
DEFAULTS = Settings()


def hustle(id, models="haiku " * 10, active=True, weight=1, after=None, states=None) -> Hustle:
    models = models.split()
    states = (states or "open " * len(models)).split()
    incs = [Increment(id=f"{id}{n}", title="x", model=m, instructions=[], state=s, after=list((after or {}).get(n, ())))
            for n, (m, s) in enumerate(zip(models, states))]
    return Hustle(id=id, project="p", title=id, active=active, weight=weight, increments=incs)


def per_hustle(starts: list[Start]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in starts:
        out[s.hustle] = out.get(s.hustle, 0) + POINTS[s.model]
    return out


def gate(session=40.0, week=30.0, rates=None, extra=None, costs=None) -> Budget:
    windows = {SESSION: Window(session, NOW + timedelta(hours=2)), WEEK: Window(week, WEEK_RESETS), **(extra or {})}
    b = Budget(Usage(reading=Reading(NOW, windows, "screen"), rates=dict(rates or {})))
    for model, cs in (costs or {}).items():
        b.costs[model] = deque(cs)
    return b


# --- shares -----------------------------------------------------------------------------------------------------


def test_shares_split_the_total_by_weight_among_active_hustles_with_ready_or_running_work():
    pool = [hustle("a", weight=2), hustle("b"), hustle("off", active=False), hustle("idle", states="done " * 10)]
    assert shares(pool, 12) == {"a": 8.0, "b": 4.0}


def test_a_running_event_overseer_keeps_its_hustle_in_the_pool():
    waiting = hustle("w", models="haiku", states="blocked")
    assert shares([hustle("a"), waiting], 10) == {"a": 10.0}
    assert shares([hustle("a"), waiting], 10, [Session("w", "event-overseer", "opus")]) == {"a": 5.0, "w": 5.0}


# --- choosing ---------------------------------------------------------------------------------------------------


def test_under_contention_free_points_follow_the_weights():
    d = choose([hustle("a", weight=1), hustle("b", weight=3)], 8)
    assert per_hustle(d.starts) == {"a": 2, "b": 6}
    assert d.held is None


def test_points_a_hustle_cannot_use_go_to_the_others():
    d = choose([hustle("a", models="haiku"), hustle("b")], 8)
    assert per_hustle(d.starts) == {"a": 1, "b": 7}


def test_the_hustle_furthest_below_its_share_is_served_first_and_borrowing_is_never_paid_back():
    a, b = hustle("a", states="open " * 10), hustle("b", states="running " * 7 + "open " * 3)
    # b borrowed 7 of its 4 points while a had nothing ready; a now gets the one free point, b keeps what it runs
    d = choose([a, b], 8)
    assert d.starts == [Start("a", "a0", "haiku")]
    # b's sessions finish one by one: every freed point goes to a until a reaches its share
    for n in range(3):
        b.increments[n].state = "done"
        a.increments[n].state = "running"
        assert [s.hustle for s in choose([a, b], 8).starts] == ["a"]
    a.increments[3].state = "running"
    b.increments[3].state = "done"
    assert [s.hustle for s in choose([a, b], 8).starts] == ["b"]  # a at 4 of 4, b at 3 of 4


def test_below_its_share_is_measured_relative_to_the_share():
    # shares 1.75 and 5.25: a holds 1 (57 %), b holds 4 (76 %) but is more points short (1.25 against 0.75)
    a = hustle("a", weight=1, states="running " + "open " * 9)
    b = hustle("b", weight=3, states="running " * 2 + "open " * 8, models="sonnet " * 10)
    assert choose([a, b], 7).starts == [Start("a", "a1", "haiku")]


def test_ties_go_to_the_hustle_with_the_larger_gap_then_to_list_order():
    d = choose([hustle("a", weight=1), hustle("b", weight=2)], 3)
    assert [s.hustle for s in d.starts][0] == "b"
    d = choose([hustle("a"), hustle("b")], 1)
    assert d.starts == [Start("a", "a0", "haiku")]


def test_only_increments_whose_dependencies_are_done_start():
    h = hustle("a", models="haiku haiku haiku", after={1: ["a0"], 2: ["a1"]})
    assert [s.increment for s in choose([h], 10).starts] == ["a0"]
    h.increments[0].state = "done"
    assert [s.increment for s in choose([h], 10).starts] == ["a1"]


def test_inactive_hustles_start_nothing_but_their_running_sessions_still_hold_points():
    stopping = hustle("s", models="opus opus", active=False, states="running open")
    d = choose([stopping, hustle("a")], 6)
    assert per_hustle(d.starts) == {"a": 2}


def test_event_overseers_count_against_their_hustle():
    a, b = hustle("a"), hustle("b")
    d = choose([a, b], 6, sessions=[Session("a", "event-overseer", "opus")])
    assert per_hustle(d.starts) == {"b": 2}  # a already uses 4 of its 3: b gets what is free


def test_an_increment_bigger_than_the_free_points_holds_them_for_itself():
    big = hustle("big", models="opus")
    small = hustle("small", states="running " + "open " * 9)
    d = choose([big, small], 4)  # big is furthest below its share; opus costs 4, 3 points are free
    assert (d.starts, d.waiting) == ([], Start("big", "big0", "opus"))
    small.increments[0].state = "done"
    d = choose([big, small], 4)
    assert d.starts[0] == Start("big", "big0", "opus")  # bigger than its share of 2, it starts once 4 are free


def test_a_hustle_whose_ready_work_the_budget_holds_back_holds_no_points():
    b = gate(extra={"week (Fable)": Window(100.0, WEEK_RESETS)})
    d = choose([hustle("f", models="fable"), hustle("h")], 8, sessions=[Session("h", "x", "haiku")],
               budget=b, settings=DEFAULTS, now=NOW)
    assert (per_hustle(d.starts), d.waiting, d.held.reason) == ({"h": 7}, None, "fit")


def test_a_smaller_increment_of_the_same_hustle_takes_points_its_bigger_neighbour_cannot():
    d = choose([hustle("a", models="opus haiku haiku")], 3)
    assert [s.increment for s in d.starts] == ["a1", "a2"]


def test_choose_changes_nothing():
    h = hustle("a")
    choose([h], 10)
    assert all(i.state == "open" for i in h.increments)


# --- budget -----------------------------------------------------------------------------------------------------


def test_a_parked_increment_goes_first_within_its_hustle():
    b = gate()
    b.parked[("a", "a3")] = NOW
    d = choose([hustle("a")], 2, budget=b, settings=DEFAULTS, now=NOW)
    assert d.starts == [Start("a", "a3", "haiku", parked=True), Start("a", "a0", "haiku")]


def test_a_hold_on_the_5h_window_starts_nothing():
    d = choose([hustle("a")], 10, budget=gate(session=90), settings=DEFAULTS, now=NOW)
    assert (d.starts, d.held.reason) == ([], "session")


def test_above_the_weekly_target_line_nothing_starts():
    d = choose([hustle("a")], 10, budget=gate(week=60), settings=DEFAULTS, now=NOW)
    assert (d.starts, d.held.reason) == ([], "week")


def test_a_model_whose_window_is_used_up_waits_while_other_models_start():
    b = gate(extra={"week (Opus)": Window(100.0, WEEK_RESETS)})
    d = choose([hustle("a", models="opus haiku"), hustle("b", models="opus sonnet")], 20,
               budget=b, settings=DEFAULTS, now=NOW)
    assert {s.increment for s in d.starts} == {"a1", "b1"}
    assert d.held.reason == "fit"


def test_the_fit_counts_the_sessions_chosen_in_the_same_call():
    # a haiku session costs $1, a dollar 10 % of the 5h window, the window is at 70: three fit, a fourth would not
    b = gate(session=70, rates={SESSION: 10.0}, costs={"haiku": [1.0]})
    d = choose([hustle("a")], 20, budget=b, settings=DEFAULTS, now=NOW)
    assert len(d.starts) == 3
    assert d.held.reason == "fit"


def test_running_sessions_count_in_the_fit():
    b = gate(session=70, rates={SESSION: 10.0}, costs={"haiku": [1.0]})
    a = hustle("a", states="running running " + "open " * 8)
    assert len(choose([a], 20, budget=b, settings=DEFAULTS, now=NOW).starts) == 1


def test_a_parked_increment_still_waits_for_the_budget():
    b = gate(session=95)
    b.parked[("a", "a0")] = NOW
    assert choose([hustle("a")], 10, budget=b, settings=DEFAULTS, now=NOW).starts == []


# --- simulated hustles over time --------------------------------------------------------------------------------


def simulate(hustles: list[Hustle], total: int, ticks: int, seed: int = 1) -> dict[str, float]:
    """Run sessions of random length; check every tick that no ready work that fits is left waiting, and return
    the mean points each hustle held."""
    rng = random.Random(seed)
    ends: dict[tuple[str, str], int] = {}
    held = {h.id: 0 for h in hustles}
    by_id = {h.id: h for h in hustles}
    for tick in range(ticks):
        for key in [k for k, end in ends.items() if end <= tick]:
            del ends[key]
            next(i for i in by_id[key[0]].increments if i.id == key[1]).state = "done"
        for start in choose(hustles, total).starts:
            next(i for i in by_id[start.hustle].increments if i.id == start.increment).state = "running"
            ends[(start.hustle, start.increment)] = tick + rng.randint(2, 6)
        sessions = running_sessions(hustles)
        assert in_use(sessions) <= total
        free = total - in_use(sessions)
        fits = any(POINTS[i.model] <= free for h in hustles if h.active for i in h.ready())
        assert not fits or choose(hustles, total).waiting, "points left idle with nobody waiting for them"
        for h in hustles:
            held[h.id] += in_use(sessions, h.id)
    return {k: v / ticks for k, v in held.items()}


def test_over_time_busy_hustles_hold_points_in_proportion_to_their_weights():
    hustles = [hustle("a", "haiku " * 400, weight=1), hustle("b", "sonnet " * 400, weight=2),
               hustle("c", "haiku sonnet " * 200, weight=1)]
    mean = simulate(hustles, 12, 300)
    assert mean["a"] == pytest.approx(3, abs=0.5)
    assert mean["b"] == pytest.approx(6, abs=0.5)
    assert mean["c"] == pytest.approx(3, abs=0.5)


def test_over_time_a_hustle_that_runs_dry_leaves_its_points_to_the_others():
    hustles = [hustle("a", "haiku " * 6), hustle("b", "haiku " * 600)]
    mean = simulate(hustles, 8, 200)
    assert mean["a"] + mean["b"] == pytest.approx(8, abs=0.3)  # work-conserving the whole time
    assert mean["b"] > 7


def test_over_time_a_fable_increment_ready_late_still_gets_all_eight_points():
    # the Fable increment becomes ready while the Haiku hustle fills every point; handing each freed point back to
    # Haiku would starve it for good
    hustles = [hustle("f", "haiku fable", after={1: ["f0"]}), hustle("h", "haiku " * 300)]
    simulate(hustles, 8, 40)
    assert hustles[0].increments[1].state in ("running", "done")


def test_finished_sessions_feed_the_fit_of_the_next_call():
    b = gate(session=70, rates={SESSION: 10.0})
    b.finished("a", "x", "haiku", Outcome("done", None, None, 0, {}, 0, cost_usd=1.0), now=NOW)
    assert len(choose([hustle("a")], 20, budget=b, settings=DEFAULTS, now=NOW).starts) < 10
