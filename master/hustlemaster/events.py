"""What happens after a session, and when an event overseer is started (ADR 0015).

A failed attempt resumes in the same worktree and its next session is told what went wrong. A session the limit
cut off is parked (ADR 0008) and costs no attempt. Once an increment has failed `FAILURES` times — ADR 0003's
resume guard, now per increment — an **event overseer** takes it over; so does a worker's `too-big` or `blocked`
report, since resuming cannot fix those, and a worker's proposal for the plan. The overseer works in the affected
increment's worktree (a proposal's gets its own at `main`), on the strongest allowed model, and its points count
against its hustle. Meanwhile the increment waits; the rest of the hustle goes on.

One event overseer per hustle at a time: overseers change the plan, and two at once would write over each other's
plan versions. The oldest event goes first.

The functions that decide are pure; `record`, `sync_proposals` and `close` write the decision to the store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import merge as merging
from .model import POINTS, Hustle, Increment
from .scheduler import Session, Start
from .store import Event, Settings, Store

FAILURES = 2  # failed attempts of an increment before an event overseer takes it over

DONE, MERGE, RESUME, PARK, RETRY, OVERSEER = "done", "merge", "resume", "park", "retry-merge", "overseer"
STRONGEST_FIRST = ("fable", "opus", "sonnet", "haiku")
EVENT_PREFIX = "event-"

PURPOSE = {
    "failed": "This increment's attempts failed ({attempts} so far). Find out why from its worktree and the notes below, then "
              "rewrite its prompt, upgrade its model, or split it, so the next attempt can succeed.",
    "too-big": "The worker reported this increment too big for one session. Split it into increments that each "
               "fit one session; keep what its worktree already holds for the first of them.",
    "conflict": "This increment's commit kept conflicting with `main` after {attempts} attempts. Resolve the "
                "conflict here so both sides' intent survives, or change the plan so the work no longer collides.",
    "proposal": "Workers proposed changes to the plan. Decide each: change the plan where the proposal serves the "
                "brief, leave it where it does not, and say why in your evidence.",
}


@dataclass(frozen=True)
class Step:
    """What becomes of an increment after one of its sessions."""

    action: str  # DONE, MERGE (report done, merge it next), RESUME, PARK, RETRY (the merge, later), OVERSEER
    attempt: bool = False  # the session used up one of the increment's attempts
    event: str | None = None  # the event kind when the action is OVERSEER
    note: str | None = None  # for the increment's next session


def settle(attempts: int, outcome, merge: merging.Merge | None = None, *, parked: bool = False) -> Step:
    """Decide after a worker session: `attempts` counts the increment's failed attempts before this one, `outcome`
    is the session's, `merge` the merge of its work once the report said done, `parked` what `Budget.finished`
    said."""
    if parked:
        return Step(PARK)
    if outcome.status == "too-big":
        return Step(OVERSEER, True, "too-big", _evidence(outcome))
    if outcome.status == "blocked":
        # the worker says it cannot go on alone; another attempt would only say so again
        return Step(OVERSEER, True, "failed", _evidence(outcome))
    if outcome.status == "done":
        if merge is None:
            return Step(MERGE)
        if merge.status == merging.MERGED:
            return Step(DONE)
        if merge.status not in merging.COUNTS_AS_ATTEMPT:
            return Step(RETRY)
        note, kind = merge.note(), "conflict" if merge.status == merging.CONFLICT else "failed"
    else:
        note, kind = f"The previous attempt did not finish. It said:\n\n{_evidence(outcome)}", "failed"
    if attempts + 1 >= FAILURES:
        return Step(OVERSEER, True, kind, note)
    return Step(RESUME, True, note=note)


def _evidence(outcome) -> str:
    if outcome.report is not None and outcome.error is None:
        return outcome.report.evidence or "(no evidence given)"
    return outcome.error or "(no report)"


def record(store: Store, hustle_id: str, increment: str, step: Step) -> int | None:
    """Write a worker step to the store; returns the event id if an overseer is now due."""
    if step.attempt:
        store.count_attempt(hustle_id, increment)
    if step.action == DONE:
        store.set_state(hustle_id, increment, "done")
        return None
    if step.action in (MERGE, RETRY):
        return None  # its work waits in the worktree for the merge; it does not start again
    store.set_state(hustle_id, increment, "open")
    if step.action == OVERSEER:
        attempts = next(i.attempts for i in store.hustle(hustle_id).increments if i.id == increment)
        return store.open_event(hustle_id, step.event, increment=increment,
                                detail=PURPOSE[step.event].format(attempts=attempts) + _notes(step.note))
    store.leave_note(hustle_id, increment, step.note)
    return None


def _notes(note: str | None) -> str:
    return f"\n\nWhat the last attempt left:\n\n{note.strip()}" if note else ""


def sync_proposals(store: Store, hustle_id: str) -> int | None:
    """Gather the hustle's open proposals into one proposal event; returns its id, None if there were none."""
    open_ = store.proposals(hustle_id, state="open")
    if not open_:
        return None
    listed = "\n".join(f"- #{p.id} by {p.author}" + (f" (increment {p.increment})" if p.increment else "")
                       + f": {p.text}" for p in open_)
    return store.open_event(hustle_id, "proposal", detail=f"{PURPOSE['proposal']}\n\n{listed}",
                            proposals=[p.id for p in open_])


def key(event_id: int) -> str:
    """The id an event overseer goes by where an increment id would stand: sessions, run directories, tokens."""
    return f"{EVENT_PREFIX}{event_id}"


def event_id(session_key: str) -> int | None:
    rest = session_key.removeprefix(EVENT_PREFIX)
    return int(rest) if rest != session_key and rest.isdigit() else None


def overseer_model(settings: Settings) -> str:
    """The strongest model allowed that the total points can carry at all."""
    for model in STRONGEST_FIRST:
        if (model != "fable" or settings.allow_fable) and POINTS[model] <= settings.total_points:
            return model
    return STRONGEST_FIRST[-1]


def due(hustles: Iterable[Hustle], events: Iterable[Event], sessions: Iterable[Session],
        settings: Settings) -> list[Start]:
    """The event overseers that may start now, for `scheduler.choose(overseers=…)`. Pure.

    An event the store says is running blocks its hustle even without a session in `sessions`: after a restart it
    is set back to open before it is started again."""
    hustles = list(hustles)
    active = {h.id for h in hustles if h.active}
    busy = {s.hustle for s in sessions if event_id(s.increment) is not None}
    busy |= {e.hustle for e in events if e.state == "running"}
    # a planned overseer increment changes the plan too
    busy |= {h.id for h in hustles for i in h.running() if i.mode == "overseer"}
    model = overseer_model(settings)
    found: dict[str, Start] = {}
    for event in sorted(events, key=lambda e: e.id):
        if event.state == "open" and event.hustle in active and event.hustle not in busy:
            found.setdefault(event.hustle, Start(event.hustle, key(event.id), model))
    return list(found.values())


def overseer_increment(event: Event, hustle: Hustle, model: str) -> Increment:
    """What `Runner.start` gets for an event overseer: the situation, the affected increment, and what to decide."""
    affected = next((i for i in hustle.increments if i.id == event.increment), None)
    about = ""
    if affected is not None:
        after = ", ".join(affected.after) or "nothing"
        about = (f"\n\n## The increment: {affected.id} — {affected.title}\n\nModel {affected.model}, depends on "
                 f"{after}. Its prompt:\n\n{affected.prompt.strip()}")
    prompt = (f"An event in this hustle needs an overseer ({event.kind}).\n\n{event.detail.strip()}{about}\n\n"
              "Change the plan only over the hustlemaster's MCP tools; read the hustle first, its plan may have "
              "moved on. If only a human can unblock this, change nothing and report `blocked`.")
    return Increment(id=key(event.id), title=f"Overseer: {event.kind}", model=model, instructions=[],
                     prompt=prompt, mode="overseer")


def close(store: Store, event: Event, outcome, *, parked: bool = False) -> str:
    """Write an event overseer's end to the store; returns the event's new state.

    Cut off at the limit, the event waits for the next window. Reported done, it is handled and its increment
    starts over. Reported blocked, a human is needed: the increment is marked blocked, and only its dependants
    wait. Any other end fails the event and blocks the increment too — an overseer is the last automatic resort,
    another one would most likely fail the same way — and its proposals are dropped."""
    if parked:
        store.set_event_state(event.id, "open")
        return "open"
    state = "handled" if outcome.status in ("done", "blocked") else "failed"
    store.set_event_state(event.id, state)
    known = {i.id for i in store.hustle(event.hustle).increments}
    if event.increment in known:
        if outcome.status == "done":
            store.leave_note(event.hustle, event.increment,
                             "An overseer reviewed this increment after its last attempt and may have changed it. "
                             f"It said:\n\n{_evidence(outcome)}\n\nContinue from what this worktree holds.")
        else:
            store.set_state(event.hustle, event.increment, "blocked")
    return state
