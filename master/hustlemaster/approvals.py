"""When a plan change needs the user's yes (ADR 0015), judged against the last confirmed plan.

Two things need one: an increment that now costs more points than its hustle's share, and growth of more than
25 % in session points summed over the plan. Growth is the plain sum, so a split whose parts cost no more together
adds nothing and needs no approval by construction. A new increment counts as costing nothing at the baseline:
a split part on a stronger model than the hustle's share must not slip past the upgrade rule.

Pure: the store records the judgement and holds the increments it names.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import POINTS, Hustle, Increment

GROWTH = 25  # percent of session points over the baseline that need an approval
STEPS = ("haiku", "sonnet", "opus", "fable")  # one model step up is one position here

# Why an increment waits for an approval: its own upgrade, or it is new in a change that grew the plan too much.
UPGRADE = "approval"
PROVISIONAL = "provisional"


@dataclass
class Judgement:
    growth: float  # percent of session points over the baseline; negative when the plan shrank
    upgrades: dict[str, tuple[str | None, str]] = field(default_factory=dict)  # id → (baseline model or None, model)
    new: list[str] = field(default_factory=list)  # increments not in the baseline

    @property
    def needed(self) -> bool:
        return bool(self.upgrades) or self.growth > GROWTH

    def waiting(self) -> dict[str, str]:
        """The increments that may not start until the approval: only these, the rest of the hustle goes on."""
        held = {i: PROVISIONAL for i in self.new} if self.growth > GROWTH else {}
        return held | {i: UPGRADE for i in self.upgrades}

    def reasons(self) -> list[str]:
        found = [f"{i}: {old or 'new'} → {new} costs more than the hustle's share"
                 for i, (old, new) in self.upgrades.items()]
        if self.growth > GROWTH:
            found.append(f"the plan grew by {self.growth:.0f} % in session points (more than {GROWTH} %)")
        return found


def points(increments: list[Increment]) -> int:
    return sum(POINTS[i.model] for i in increments)


def share(hustle: Hustle, hustles: list[Hustle], total: int) -> float:
    """The points the hustle is guaranteed when every active hustle competes. Not the scheduler's momentary share:
    that one rises whenever others are idle, and an approval must not depend on who happens to be idle."""
    weights = sum(h.weight for h in hustles if h.active and h.id != hustle.id) + hustle.weight
    return total * hustle.weight / weights


def judge(baseline: list[Increment], increments: list[Increment], share: float) -> Judgement:
    before = {i.id: i.model for i in baseline}
    base = points(baseline)
    growth = (points(increments) - base) / base * 100 if base else 0.0
    upgrades = {
        i.id: (before.get(i.id), i.model) for i in increments
        if POINTS[i.model] > share and POINTS[i.model] > POINTS.get(before.get(i.id), 0)
    }
    return Judgement(growth, upgrades, [i.id for i in increments if i.id not in before])


def automatic(judgement: Judgement, mode: str, growth_limit: int) -> bool:
    """Whether the auto-approve setting says yes: `all`, or `limited` to some growth and one model step up."""
    if mode == "all":
        return True
    if mode != "limited":
        return False
    one_step = all(old is not None and STEPS.index(new) - STEPS.index(old) <= 1
                   for old, new in judgement.upgrades.values())
    return one_step and judgement.growth <= growth_limit
