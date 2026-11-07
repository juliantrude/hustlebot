"""Approvals (ADR 0015): which plan changes need the user's yes, what waits meanwhile, and auto-approve — judged
alone, in the store, through an overseer's MCP gate and in simulated hustles the scheduler runs."""

from __future__ import annotations

import pytest

from hustlemaster import events
from hustlemaster.access import Caller, Gate
from hustlemaster.approvals import PROVISIONAL, UPGRADE, automatic, judge, share
from hustlemaster.events import settle
from hustlemaster.model import Hustle, Increment
from hustlemaster.scheduler import choose
from hustlemaster.store import Store

from test_events import outcome


def inc(id, model="haiku", after=(), mode="worker"):
    return Increment(id, id, model, [], after=list(after), prompt=f"do {id}", mode=mode)


def plan(*specs, accept="haiku"):
    """Increments from `id:model` specs, all independent, plus the acceptance increment after all of them."""
    incs = [inc(*s.split(":")) for s in specs]
    return incs + [inc("ok", accept, [i.id for i in incs], mode="overseer")]


# --- judge: the bounds of ADR 0015 ----------------------------------------------------------------------------------


def test_growth_up_to_25_percent_needs_nothing_and_beyond_holds_only_the_new_increments():
    base = plan("A", "B", "C")  # 4 points
    assert not judge(base, plan("A", "B", "C", "D"), share=10).needed  # +25 %
    grown = judge(base, plan("A", "B", "C", "D", "E"), share=10)  # +50 %
    assert (grown.needed, grown.growth, grown.waiting()) == (True, 50, {"D": PROVISIONAL, "E": PROVISIONAL})
    assert grown.reasons() == ["the plan grew by 50 % in session points (more than 25 %)"]


def test_a_split_whose_parts_cost_no_more_together_is_no_growth():
    base = plan("A:opus", "B")  # 4 + 1 + 1
    split = plan("A1:sonnet", "A2:sonnet", "B")
    assert judge(base, split, share=10).growth == 0
    assert judge(base, plan("A1:opus", "A2:sonnet", "B"), share=10).growth == pytest.approx(100 / 3)


def test_an_upgrade_beyond_the_share_holds_only_that_increment():
    base = plan("A:sonnet", "B", accept="opus")
    upgraded = judge(base, plan("A:opus", "B", accept="opus"), share=2)
    assert (upgraded.upgrades, upgraded.waiting()) == ({"A": ("sonnet", "opus")}, {"A": UPGRADE})
    assert upgraded.reasons()[0] == "A: sonnet → opus costs more than the hustle's share"
    # within the share, a downgrade, or an increment that already cost that much at the baseline: no approval
    assert judge(base, plan("A:opus", "B", accept="opus"), share=4).upgrades == {}
    assert judge(base, plan("A:haiku", "B", accept="opus"), share=1).upgrades == {}


def test_a_new_increment_above_the_share_counts_as_an_upgrade_from_nothing():
    base = plan("A:sonnet", "B:sonnet", "C:sonnet", "D:sonnet", accept="sonnet")  # 10 points
    split = judge(base, plan("A1:opus", "B:sonnet", "C:sonnet", "D:sonnet", accept="sonnet"), share=2)
    assert split.growth == 20 and split.upgrades == {"A1": (None, "opus")}
    assert split.waiting() == {"A1": UPGRADE}


def test_share_is_the_guarantee_among_active_hustles_whoever_is_idle():
    def h(id, weight, active=True):
        return Hustle(id, "/p", id, active, weight, [])

    mine = h("m", 1, active=False)  # not yet active: judged as if it were
    assert share(mine, [mine, h("a", 1), h("b", 2), h("off", 5, active=False)], 12) == 3
    assert share(h("m", 2), [h("m", 2)], 12) == 12


@pytest.mark.parametrize("mode, growth_limit, new, expected", [
    ("off", 100, plan("A:sonnet", "B"), False),
    ("all", 0, plan("A:fable", "B", "C", "D", "E"), True),
    ("limited", 50, plan("A:sonnet", "B"), True),  # +33 % and one step up
    ("limited", 25, plan("A:sonnet", "B"), False),  # growth beyond the limit
    ("limited", 100, plan("A:opus", "B"), False),  # two steps up
    ("limited", 100, plan("A", "B", "N:opus"), False),  # a new increment above the share is no step
])
def test_auto_approve_modes(mode, growth_limit, new, expected):
    judgement = judge(plan("A", "B"), new, share=1)
    assert judgement.needed
    assert automatic(judgement, mode, growth_limit) is expected


# --- the store: recorded, held, decided -----------------------------------------------------------------------------


@pytest.fixture
def store():
    s = Store()
    s.update_settings(total_points=4)
    yield s
    s.close()


def active(store, increments, weight=1) -> str:
    hid = store.submit("/p", "h", "brief", increments, author="user")
    store.set_weight(hid, weight)
    store.set_active(hid, True)
    return hid


def held(store, hid) -> dict[str, str]:
    return {i.id: i.held for i in store.hustle(hid).increments if i.held}


def ready(store, hid) -> list[str]:
    return [i.id for i in store.hustle(hid).ready()]


def test_changes_before_the_first_activation_need_no_approval(store):
    hid = store.submit("/p", "h", "brief", plan("A"), author="user")
    store.change_plan(hid, plan("A:opus", "B", "C", "D"), author="interactive")
    assert store.approvals(hid) == []
    store.set_active(hid, True)
    assert (store.hustle(hid).confirmed_version, ready(store, hid)) == (2, ["A", "B", "C", "D"])


def test_growth_waits_for_the_approval_while_the_rest_of_the_hustle_goes_on(store):
    hid = active(store, plan("A", "B", "C"))
    store.change_plan(hid, plan("A", "B", "C", "D", "E"), author="overseer:ok", note="two more checks")
    [pending] = store.approvals(hid, states=("pending",))
    assert (pending.version, pending.baseline, pending.automatic) == (2, 1, False)
    assert pending.waiting == {"D": PROVISIONAL, "E": PROVISIONAL}
    assert held(store, hid) == pending.waiting and ready(store, hid) == ["A", "B", "C"]

    store.approve(pending.id)
    assert store.approval(pending.id).state == "approved" and store.hustle(hid).confirmed_version == 2
    assert held(store, hid) == {} and ready(store, hid) == ["A", "B", "C", "D", "E"]


def test_growth_adds_up_since_the_last_confirmation(store):
    hid = active(store, plan("A", "B", "C", "D", "E", "F", "G"))  # 8 points
    store.change_plan(hid, plan("A", "B", "C", "D", "E", "F", "G", "H"), author="overseer")  # +12.5 %
    assert store.approvals(hid) == []
    store.change_plan(hid, plan("A", "B", "C", "D", "E", "F", "G", "H", "I", "J"), author="overseer")  # +37.5 %
    [pending] = store.approvals(hid)
    assert pending.waiting == {"H": PROVISIONAL, "I": PROVISIONAL, "J": PROVISIONAL}


def test_a_newer_change_supersedes_the_pending_approval_and_is_judged_as_a_whole(store):
    hid = active(store, plan("A", "B", "C"))
    store.change_plan(hid, plan("A", "B", "C", "D", "E"), author="overseer")
    first = store.approvals(hid)[0].id
    store.change_plan(hid, plan("A", "B", "C", "D"), author="overseer", note="E was not needed")
    assert store.approval(first).state == "superseded"
    assert store.approvals(hid, states=("pending",)) == [] and held(store, hid) == {}


def test_an_upgrade_beyond_the_share_holds_the_increment_and_its_dependants_only(store):
    other = active(store, plan("X"))  # two equal hustles: 2 of the 4 points each
    hid = active(store, [inc("A", "sonnet"), inc("B", after=["A"]), inc("C"), inc("ok", after=["B", "C"], mode="overseer")])
    store.change_plan(hid, [inc("A", "opus"), inc("B", after=["A"]), inc("C"), inc("ok", after=["B", "C"], mode="overseer")],
                      author="overseer:event", note="A failed twice on sonnet")
    [pending] = store.approvals(hid)
    assert pending.waiting == {"A": UPGRADE}  # the plan also grew 40 %, but nothing in it is new
    assert pending.reasons[0] == "A: sonnet → opus costs more than the hustle's share"
    assert ready(store, hid) == ["C"]
    assert store.approvals(other) == []


def test_a_rejected_change_blocks_what_waited_for_it(store):
    hid = active(store, plan("A", "B", "C"))
    store.change_plan(hid, plan("A", "B", "C", "D", "E"), author="overseer")
    store.set_state(hid, "D", "done")  # cannot happen while held; shows that only open increments are blocked
    [pending] = store.approvals(hid)
    store.reject(pending.id)
    states = {i.id: i.state for i in store.hustle(hid).increments}
    assert (states["D"], states["E"], store.hustle(hid).confirmed_version) == ("done", "blocked", 1)
    assert held(store, hid) == {}
    with pytest.raises(ValueError, match="rejected, not pending"):
        store.approve(pending.id)


def test_auto_approve_confirms_and_records_and_a_revoke_puts_it_before_the_user(store):
    store.update_settings(auto_approve="all")
    hid = active(store, plan("A", "B", "C"))
    store.change_plan(hid, plan("A", "B", "C", "D", "E"), author="overseer")
    [auto] = store.approvals(hid)
    assert (auto.state, auto.automatic, auto.decided is not None) == ("approved", True, True)
    assert store.hustle(hid).confirmed_version == 2 and held(store, hid) == {}

    store.revoke(auto.id)
    assert store.approval(auto.id).state == "revoked" and store.hustle(hid).confirmed_version == 1
    [pending] = store.approvals(hid, states=("pending",))
    assert (pending.automatic, pending.waiting) == (False, {"D": PROVISIONAL, "E": PROVISIONAL})
    assert held(store, hid) == pending.waiting
    with pytest.raises(ValueError, match="only automatic"):
        store.revoke(pending.id)


def test_only_the_latest_automatic_approval_can_be_revoked(store):
    store.update_settings(auto_approve="all")
    hid = active(store, plan("A", "B", "C"))
    store.change_plan(hid, plan("A", "B", "C", "D", "E"), author="overseer")
    store.change_plan(hid, plan("A", "B", "C", "D", "E", "F", "G", "H"), author="overseer")
    first, second = store.approvals(hid)
    with pytest.raises(ValueError, match="only the latest"):
        store.revoke(first.id)
    store.revoke(second.id)
    assert store.hustle(hid).confirmed_version == 2


def test_limited_auto_approve_takes_one_step_up_but_leaves_two_to_the_user(store):
    store.update_settings(auto_approve="limited", auto_approve_growth=60)
    other = active(store, plan("X"), weight=3)  # share of the hustle: 1 of the 4 points
    hid = active(store, plan("A", "B", "C"))
    store.change_plan(hid, plan("A:sonnet", "B", "C"), author="overseer")  # one step, +25 %
    assert [(a.state, a.automatic) for a in store.approvals(hid)] == [("approved", True)]
    store.change_plan(hid, plan("A:sonnet", "B:opus", "C"), author="overseer")  # two steps
    assert [(a.state, a.automatic) for a in store.approvals(hid)] == [("approved", True), ("pending", False)]
    assert held(store, hid) == {"B": UPGRADE} and store.approvals(other) == []


def test_after_its_event_overseer_an_upgraded_increment_waits_for_the_approval(store):
    hid = active(store, plan("A:sonnet", "B"), weight=1)
    active(store, plan("X"))
    event = store.event(events.record(store, hid, "A", settle(1, outcome("failed"))))
    store.set_event_state(event.id, "running")
    store.change_plan(hid, plan("A:opus", "B"), author="overseer:event", note="upgrade A")
    assert held(store, hid) == {"A": "overseer"}  # the overseer is still at it
    assert events.close(store, event, outcome("done", "upgraded A to opus")) == "handled"
    assert held(store, hid) == {"A": UPGRADE}


def test_an_overseer_learns_over_mcp_that_its_change_waits(store):
    hid = active(store, plan("A", "B", "C"))
    gate = Gate(store, Caller("overseer", hid, "ok"))
    answer = gate.change_plan(plan("A", "B", "C", "D", "E"), "two more")
    assert answer == {"version": 2, "approval": {
        "reasons": ["the plan grew by 50 % in session points (more than 25 %)"],
        "waiting": {"D": PROVISIONAL, "E": PROVISIONAL}}}
    assert gate.change_plan(plan("A", "B", "C", "D"), "fewer") == {"version": 3}


# --- simulated hustles: the scheduler never starts what waits -------------------------------------------------------


def test_the_scheduler_starts_nothing_that_waits_for_an_approval(store):
    store.update_settings(total_points=6)  # 3 points each
    a = active(store, plan("A1", "A2"))
    b = active(store, [inc("B1", "sonnet"), inc("ok", after=["B1"], mode="overseer")])
    store.change_plan(a, plan("A1", "A2", "N1", "N2", "N3"), author="overseer")  # +100 %, provisional
    store.change_plan(b, [inc("B1", "opus"), inc("ok", after=["B1"], mode="overseer")], author="overseer")  # > 3
    started = {(s.hustle, s.increment) for s in choose(store.hustles(), 6).starts}
    assert started == {(a, "A1"), (a, "A2")}  # B's only ready increment waits; its points go to A

    for approval in store.approvals(states=("pending",)):
        store.approve(approval.id)
    assert (b, "B1") in {(s.hustle, s.increment) for s in choose(store.hustles(), 6).starts}
    assert [i.id for i in store.hustle(a).ready()] == ["A1", "A2", "N1", "N2", "N3"]
