"""Who gets free session points next (ADR 0010).

The user's total of session points is shared among the hustles that are active and have an increment ready or
running, in proportion to their weights (like CSS grid `fr`). Shares are guarantees, not caps: points one hustle
cannot use go to the others. Whenever points are free they go to the hustle furthest below its share, measured as
points in use over share, so a hustle that had to wait is served before one that borrowed. Nothing is remembered
between calls: a hustle that borrowed never pays back, running sessions are never interrupted.

Within a hustle an increment the limit cut off goes first (its worktree holds work in progress), then the plan
order; a smaller increment of the same hustle may go ahead of one that does not fit. If none of the neediest
hustle's ready increments fits, the free points are held for it and nobody else starts: were they handed to smaller
increments elsewhere, an increment bigger than what happens to be free would never start. A hustle without ready
work, or whose ready work the budget holds back, cannot use points, and they go to the next one.

Every start has to pass the budget (ADR 0008): a hold on the 5h window, the week or a limit stops all starts, a
`fit` hold only that model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .budget import Budget, Verdict
from .model import POINTS, Hustle, Increment

GLOBAL_HOLDS = ("limit", "session", "week")  # verdicts that hold back every model, not just the one asked about


@dataclass(frozen=True)
class Session:
    """A running hustlebot: a worker or overseer increment, or an event overseer outside the plan."""

    hustle: str
    increment: str
    model: str


@dataclass(frozen=True)
class Start:
    hustle: str
    increment: str
    model: str
    parked: bool = False  # cut off at the limit before: call `Budget.resume` for its session's note


@dataclass
class Decision:
    starts: list[Start] = field(default_factory=list)
    held: Verdict | None = None  # the budget's last refusal: why ready work did not start
    waiting: Start | None = None  # the neediest hustle's next increment, for which the free points are held


def running_sessions(hustles: Iterable[Hustle]) -> list[Session]:
    """The sessions the plans say are running; event overseers are not in any plan and must be added by the caller."""
    return [Session(h.id, i.id, i.model) for h in hustles for i in h.running()]


def in_use(sessions: Iterable[Session], hustle: str | None = None) -> int:
    return sum(POINTS[s.model] for s in sessions if hustle is None or s.hustle == hustle)


def shares(hustles: Iterable[Hustle], total: int, sessions: Iterable[Session] | None = None) -> dict[str, float]:
    """Guaranteed points per hustle in the pool: active, with an increment ready or a session running. Inactive or
    fully blocked hustles hold no share."""
    hustles = list(hustles)
    busy = {s.hustle for s in (running_sessions(hustles) if sessions is None else sessions)}
    pool = [h for h in hustles if h.active and (h.ready() or h.id in busy)]
    weights = sum(h.weight for h in pool)
    return {h.id: total * h.weight / weights for h in pool} if weights else {}


def choose(hustles: list[Hustle], total: int, *, sessions: Iterable[Session] | None = None,
           budget: Budget | None = None, settings=None, now: datetime | None = None) -> Decision:
    """The increments to start now, in order. Pure: nothing is started, no state is changed.

    `sessions` are the running ones, by default those the plans mark running. Without a `budget` only points
    count, which is what the simulation uses."""
    sessions = list(running_sessions(hustles) if sessions is None else sessions)
    by_id = {h.id: h for h in hustles}
    share = shares(hustles, total, sessions)
    decision = Decision()
    taken: set[tuple[str, str]] = set()  # chosen this call; the plans still say open
    unfit: set[str] = set()  # models the budget's fit check refused this call

    while True:
        free = total - in_use(sessions)
        if free <= 0:
            return decision

        def neediness(hustle_id: str) -> tuple[float, float]:
            used = in_use(sessions, hustle_id)
            return used / share[hustle_id], -(share[hustle_id] - used)

        started = None
        for hustle_id in sorted(share, key=neediness):
            waiting = None
            for inc, parked in _candidates(by_id[hustle_id], budget, taken):
                if inc.model in unfit:
                    continue
                if budget is not None:
                    verdict = budget.check(inc.model, settings, running=[s.model for s in sessions], now=now)
                    if not verdict.ok:
                        decision.held = verdict
                        if verdict.reason in GLOBAL_HOLDS:
                            return decision
                        unfit.add(inc.model)
                        continue
                if POINTS[inc.model] > free:
                    waiting = waiting or Start(hustle_id, inc.id, inc.model, parked)
                    continue
                started = Start(hustle_id, inc.id, inc.model, parked)
                break
            if started:
                break
            if waiting:
                # The free points are held for it: if other hustles took them, it would never see enough free.
                decision.waiting = waiting
                return decision
        if started is None:
            return decision
        decision.starts.append(started)
        taken.add((started.hustle, started.increment))
        sessions.append(Session(started.hustle, started.increment, started.model))


def _candidates(hustle: Hustle, budget: Budget | None, taken: set) -> list[tuple[Increment, bool]]:
    ready = [i for i in hustle.ready() if (hustle.id, i.id) not in taken]
    marked = [(i, bool(budget and budget.is_parked(hustle.id, i.id))) for i in ready]
    return sorted(marked, key=lambda pair: not pair[1])  # stable: plan order within parked and within the rest
