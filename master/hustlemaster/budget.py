"""Whether the subscription has room for one more session now (ADR 0008), and what happens to one the limit cut off.

Four checks, in the order a human would ask them:

1. **The safety net.** A session was turned away by a limit: nothing starts until the reset it named.
2. **The 5h stop.** Above the user's session threshold nothing new starts until the window resets. Running sessions
   finish; they are why the threshold sits below 100.
3. **The weekly target line.** The week should reach the user's target at its reset, not before: a straight line
   from 0 at the start of the week to the target at the reset. Above the line nothing starts until the line has
   caught up. The twin goal is to leave nothing of the week unused, so the line ends near 100, not at a cap.
4. **The fit.** What a session of the model has cost so far, times the percent a dollar costs in each window, added
   to what the window already holds and what the running sessions are expected to add: if that crosses 100 the
   session would be cut off, so it waits for the reset. Without a learned rate or any finished session there is
   nothing to estimate from, and the session may start; the safety net catches a miss.

A session that still runs into the limit is **parked**: its worktree stays, the cut-off does not count as an attempt
(the increment did nothing wrong), and the next session of that increment, started once the window has reset, is
told to continue where the previous one stopped.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .model import POINTS
from .usage import SESSION, WEEK, Usage, _now, limit_hit

WEEK_LENGTH = timedelta(days=7)
LIMIT = 100.0
HISTORY = 10  # finished sessions per model whose cost predicts the next one

PARKED_NOTE = ("The previous session of this increment was cut off at the subscription's usage limit, not for "
               "anything it did. Its work is in this worktree; check what is there and continue from it.")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str  # `ok`, or what holds the start back: `limit`, `session`, `week`, `fit`
    until: datetime | None = None  # when the reason should be gone; None when unknown
    detail: str = ""


def target(week_resets: datetime, target_percent: float, at: datetime) -> float:
    """Where the weekly target line stands at `at`: 0 at the start of the week, `target_percent` at its reset."""
    elapsed = (at - (week_resets - WEEK_LENGTH)) / WEEK_LENGTH
    return target_percent * min(max(elapsed, 0.0), 1.0)


def catches_up(week_resets: datetime, target_percent: float, percent: float) -> datetime:
    """When the target line reaches `percent`; the reset if it never does this week."""
    if target_percent <= 0 or percent >= target_percent:
        return week_resets
    return week_resets - WEEK_LENGTH + WEEK_LENGTH * (percent / target_percent)


@dataclass
class Budget:
    """The start gate over a `Usage`, with what sessions of each model cost and which increments are parked."""

    usage: Usage = field(default_factory=Usage)
    costs: dict[str, deque] = field(default_factory=dict)  # model → recent `total_cost_usd` of its finished sessions
    parked: dict[tuple[str, str], datetime] = field(default_factory=dict)  # (hustle, increment) → cut off at
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def expected_cost(self, model: str) -> float | None:
        """Dollars a session of `model` is expected to cost: its own recent mean, else the mean dollars per point
        of every model scaled by its points, else unknown."""
        with self._lock:
            own = self.costs.get(model)
            if own:
                return sum(own) / len(own)
            per_point = [c / POINTS[m] for m, cs in self.costs.items() if m in POINTS for c in cs]
            if per_point and model in POINTS:
                return sum(per_point) / len(per_point) * POINTS[model]
            return None

    def check(self, model: str, settings, running: list[str] | tuple[str, ...] = (),
              now: datetime | None = None) -> Verdict:
        """May a session of `model` start now? `running` names the models of the sessions still running."""
        now = now or _now()
        if until := self.usage.blocked_until(now):
            return Verdict(False, "limit", until, "a session was turned away by a usage limit")
        session, week = self.usage.estimate(SESSION, now), self.usage.estimate(WEEK, now)
        if session and session.percent >= settings.session_threshold:
            return Verdict(False, "session", session.resets,
                           f"5h window at {session.percent:.0f} %, threshold {settings.session_threshold} %")
        if week and week.resets:
            line = target(week.resets, settings.week_target, now)
            if week.percent > line:
                return Verdict(False, "week", catches_up(week.resets, settings.week_target, week.percent),
                               f"week at {week.percent:.0f} %, target line at {line:.0f} %")
        return self._fit(model, running, now, {SESSION: session, WEEK: week})

    def _fit(self, model, running, now, estimates) -> Verdict:
        reading = self.usage.reading
        for label, w in (reading.windows.items() if reading else ()):
            if label not in (SESSION, WEEK) and model in label.lower() and w.percent >= LIMIT \
                    and not (w.resets and now >= w.resets):
                return Verdict(False, "fit", w.resets, f"{label} is used up")
        cost = self.expected_cost(model)
        if cost is None:
            return Verdict(True, "ok", detail="no finished session to estimate from")
        committed = cost + sum(self.expected_cost(m) or 0.0 for m in running)
        for name, est in estimates.items():
            rate = self.usage.rates.get(name)
            if est is None or rate is None:
                continue
            after = est.percent + committed * rate
            if after > LIMIT:
                return Verdict(False, "fit", est.resets,
                               f"{name} at {est.percent:.0f} % would reach {after:.0f} % with this and the "
                               f"running sessions")
        return Verdict(True, "ok")

    def finished(self, hustle: str, increment: str, model: str, outcome,
                 now: datetime | None = None) -> datetime | None:
        """Account a finished session; park its increment if a limit cut it off. Returns when the block lifts if
        it was parked, else None. A parked session's cost still counts: it spent, it just did not finish."""
        now = now or _now()
        with self._lock:
            self.usage.record(outcome, now)
            hit, _ = limit_hit(outcome, now)
            if hit:
                self.parked[(hustle, increment)] = now
                return self.usage.blocked_until(now) or now
            if outcome.cost_usd:
                self.costs.setdefault(model, deque(maxlen=HISTORY)).append(float(outcome.cost_usd))
            return None

    def is_parked(self, hustle: str, increment: str) -> bool:
        with self._lock:
            return (hustle, increment) in self.parked

    def resume(self, hustle: str, increment: str) -> str | None:
        """Called when the increment starts again: the note for its session if it was parked, and it is no
        longer parked."""
        with self._lock:
            return PARKED_NOTE if self.parked.pop((hustle, increment), None) is not None else None
