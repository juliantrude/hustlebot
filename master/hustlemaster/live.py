"""What the views read: the stored hustles, the sessions running right now, and a short memory of both.

The store knows plans and progress; which hustlebot runs since when, and what ran in the last hour, only the
running process knows. The service loop reports every start and finish here (`started`, `finished`); a clock
samples the running sessions once per tick into each hustle's history, which the load strips and the cinema lanes
draw. Nothing here is persisted: after a restart the history starts empty.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePath

from .budget import target
from .model import POINTS
from .scheduler import Session, in_use, shares
from .store import AUTO_APPROVE, Store
from .usage import SESSION, WEEK, Usage

TICK_SECONDS = 2.0
HISTORY = 60  # ticks of history per hustle
EVENTS = 5  # ticks a started/done event stays in the state
AUTO_APPROVED = 10  # automatic approvals listed, newest first


@dataclass(frozen=True)
class Running:
    session: Session
    started: int  # tick


class Live:
    def __init__(self, store: Store, usage: Usage | None = None) -> None:
        self.store = store
        self.usage = usage or Usage()
        self.tick = 0
        self.running: dict[tuple[str, str], Running] = {}
        self.history: dict[str, list[list[dict]]] = {}
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.push_now = None  # the service's, for the dashboard's Push button; without a service nothing pushes

    def started(self, hustle: str, increment: str, model: str) -> None:
        with self.lock:
            self.running[(hustle, increment)] = Running(Session(hustle, increment, model), self.tick)
            self.events.append({"tick": self.tick, "kind": "started", "hustle": hustle, "increment": increment,
                                "model": model})

    def finished(self, hustle: str, increment: str, kind: str = "done") -> None:
        with self.lock:
            run = self.running.pop((hustle, increment), None)
            if run is not None:
                self.events.append({"tick": self.tick, "kind": kind, "hustle": hustle, "increment": increment,
                                    "model": run.session.model})

    def sessions(self) -> list[Session]:
        with self.lock:
            return [r.session for r in self.running.values()]

    def step(self) -> None:
        with self.lock:
            self.tick += 1
            columns: dict[str, list[dict]] = {}
            for r in self.running.values():
                s = r.session
                columns.setdefault(s.hustle, []).append({"model": s.model, "points": POINTS[s.model],
                                                         "increment": s.increment})
            for hustle in set(self.history) | set(columns):
                self.history[hustle] = (self.history.get(hustle, []) + [columns.get(hustle, [])])[-HISTORY:]
            self.events = [e for e in self.events if e["tick"] > self.tick - EVENTS]

    def run_forever(self) -> None:
        def loop() -> None:
            while True:
                time.sleep(TICK_SECONDS)
                self.step()

        threading.Thread(target=loop, daemon=True).start()

    def summary(self) -> dict:
        hustles = self.store.hustles()
        settings = self.store.settings()
        with self.lock:
            running = list(self.running.values())
            sessions = [r.session for r in running]
            history = {h: list(cols) for h, cols in self.history.items()}
            events = list(self.events)
            tick = self.tick
        share = shares(hustles, settings.total_points, sessions)
        titles = {h.id: h for h in hustles}
        order = sorted(hustles, key=lambda h: (not h.active, -h.weight, h.title))
        return {
            "total_points": settings.total_points,
            "points_in_use": in_use(sessions),
            "model_points": POINTS,
            "tick": tick,
            "tick_seconds": TICK_SECONDS,
            "events": events,
            "usage": {SESSION: self._window(SESSION), WEEK: self._window(WEEK, settings.week_target)},
            "thresholds": {"session": settings.session_threshold, "week": settings.week_target},
            "settings": {"allow_fable": settings.allow_fable, "auto_approve": settings.auto_approve,
                         "auto_approve_growth": settings.auto_approve_growth, "auto_approve_modes": list(AUTO_APPROVE)},
            "approvals": [
                {"id": str(a.id), "hustle": a.hustle, "text": "; ".join(a.reasons)}
                for a in self.store.approvals(states=("pending",)) if a.hustle in titles
            ],
            "auto_approved": self._auto_approved(titles),
            "hustles": [
                {
                    "id": h.id,
                    "project": project_name(h.project),
                    "path": h.project,
                    "title": h.title,
                    "active": h.active,
                    "weight": h.weight,
                    "status": _status(h, in_use(sessions, h.id)),
                    "done": sum(i.state == "done" for i in h.increments),
                    "total": len(h.increments),
                    "share": round(share.get(h.id, 0), 1),
                    "points_in_use": in_use(sessions, h.id),
                    "sessions": [
                        {"increment": r.session.increment, "model": r.session.model,
                         "points": POINTS[r.session.model], "started": r.started}
                        for r in running if r.session.hustle == h.id
                    ],
                    "history": history.get(h.id, []),
                    # the card reloads its preview when this changes: on a new plan version or any state change
                    "graph_version": f"{h.version}:" + "".join(i.state[0] for i in h.increments),
                    "increments": [{"id": i.id, "after": i.after, "state": i.state, "model": i.model}
                                   for i in h.increments],
                    "push": self._push(h),
                }
                for h in order
            ],
        }

    def _push(self, hustle) -> dict | None:
        """What the card says about pushing (ADR 0016); None if the project does not push."""
        project = self.store.project(hustle.project)
        if project is None or not project.remote or project.push == "off":
            return None
        state = self.store.push_state(hustle.id)
        return {"mode": project.push, "remote": project.remote, "pending": state.pending is not None,
                "error": state.error, "last_ok": state.last_ok}

    def _auto_approved(self, hustles: dict) -> list[dict]:
        # Every automatic approval stays visible until revoked (ADR 0015); only the one the plan is confirmed on
        # can be taken back, since a later confirmation built on it. Revoking that one makes the one before it
        # the latest again.
        found = [a for a in self.store.approvals(states=("approved",)) if a.automatic and a.hustle in hustles]
        return [
            {"id": str(a.id), "hustle": a.hustle, "text": "; ".join(a.reasons), "when": _when(datetime.fromtimestamp(a.created, timezone.utc)),
             "revocable": hustles[a.hustle].confirmed_version == a.version}
            for a in sorted(found, key=lambda a: -a.id)[:AUTO_APPROVED]
        ]

    def _window(self, name: str, week_target: int | None = None) -> dict:
        estimate = self.usage.estimate(name)
        if estimate is None:
            return {"percent": 0, "resets": "unknown", "known": False}
        window = {"percent": round(estimate.percent, 1), "resets": _when(estimate.resets), "known": True}
        if week_target is not None and estimate.resets:
            # where the weekly target line stands now: the mark the week's usage should stay below
            window["line"] = round(target(estimate.resets, week_target, datetime.now(timezone.utc)), 1)
        return window


def project_name(path: str) -> str:
    # the store keys projects by their repository path; the views show its name
    return PurePath(path).name or path


def _status(hustle, points_in_use: int) -> str:
    # An event overseer is not in the plan, so the plan alone would call its hustle waiting.
    status = hustle.status()
    if points_in_use and status in ("waiting", "blocked", "paused"):
        return "running" if hustle.active else "stopping"
    return status


def _when(at: datetime | None) -> str:
    return at.astimezone().strftime("%a %d %b %H:%M") if at else "unknown"
