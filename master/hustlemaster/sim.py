"""A fake clock for the mock: sessions start, run and finish so the dashboard has something to show.

One tick stands for one minute of scheduler time and passes every TICK_SECONDS. Each tick finished sessions
free their points, then `scheduler.choose` hands out the free points (without a budget: the mock burns its usage
on its own). Every hustle keeps the last HISTORY ticks of what ran in it, for the trail on its card. A hustle that is done starts over after a pause, so the demo never
runs dry.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass

from . import scheduler
from .model import POINTS, Hustle

TICK_SECONDS = 2.0
HISTORY = 60
RESTART_AFTER = 6  # ticks a finished hustle rests before its increments open again
DURATION = {"haiku": (2, 5), "sonnet": (3, 8), "opus": (5, 12), "fable": (8, 16)}  # ticks per session


@dataclass
class Session:
    hustle: str
    increment: str
    model: str
    started: int  # tick
    ends: int  # tick


class Sim:
    def __init__(self, hustles: list[Hustle], total_points: int, seed: int = 7) -> None:
        self.hustles = hustles
        self.total_points = total_points
        self.tick = 0
        self.sessions: list[Session] = []
        self.history: dict[str, list[list[dict]]] = {c.id: [] for c in hustles}
        self.events: list[dict] = []  # started / done, newest last
        self.rest: dict[str, int] = {}
        self.rng = random.Random(seed)
        # usage in percent of the 5h and the week window; burns with the points in use, the 5h window resets at 100
        self.usage = {"session": {"percent": 46.0, "resets": "in 2h 40m"}, "week": {"percent": 63.0, "resets": "Mon 09:00"}}
        self.lock = threading.Lock()
        # the mock's "running" increments become real sessions, half-way through
        for hustle in hustles:
            for inc in hustle.running():
                low, high = DURATION[inc.model]
                self.sessions.append(Session(hustle.id, inc.id, inc.model, -low, self.rng.randint(1, high - low)))

    def running(self, hustle_id: str) -> list[Session]:
        return [s for s in self.sessions if s.hustle == hustle_id]

    def points_in_use(self, hustle_id: str | None = None) -> int:
        return sum(POINTS[s.model] for s in self.sessions if hustle_id is None or s.hustle == hustle_id)

    def step(self) -> None:
        with self.lock:
            self.tick += 1
            self._finish()
            self._restart()
            self._schedule()
            self._burn()
            for hustle in self.hustles:
                column = [{"model": s.model, "points": POINTS[s.model], "increment": s.increment} for s in self.running(hustle.id)]
                self.history[hustle.id] = (self.history[hustle.id] + [column])[-HISTORY:]
            self.events = [e for e in self.events if e["tick"] > self.tick - 5]

    def _finish(self) -> None:
        for s in [s for s in self.sessions if s.ends <= self.tick]:
            self.sessions.remove(s)
            hustle = next(c for c in self.hustles if c.id == s.hustle)
            next(i for i in hustle.increments if i.id == s.increment).state = "done"
            self.events.append({"tick": self.tick, "kind": "done", "hustle": s.hustle, "increment": s.increment, "model": s.model})

    def _restart(self) -> None:
        for hustle in self.hustles:
            if not hustle.active or not all(i.state == "done" for i in hustle.increments):
                continue
            self.rest[hustle.id] = self.rest.get(hustle.id, 0) + 1
            if self.rest[hustle.id] >= RESTART_AFTER:
                del self.rest[hustle.id]
                for inc in hustle.increments:
                    inc.state = "open"

    def _schedule(self) -> None:
        sessions = [scheduler.Session(s.hustle, s.increment, s.model) for s in self.sessions]
        by_id = {c.id: c for c in self.hustles}
        for start in scheduler.choose(self.hustles, self.total_points, sessions=sessions).starts:
            next(i for i in by_id[start.hustle].increments if i.id == start.increment).state = "running"
            low, high = DURATION[start.model]
            self.sessions.append(Session(start.hustle, start.increment, start.model, self.tick, self.tick + self.rng.randint(low, high)))
            self.events.append({"tick": self.tick, "kind": "started", "hustle": start.hustle, "increment": start.increment, "model": start.model})

    def _burn(self) -> None:
        used = self.points_in_use()
        self.usage["session"]["percent"] = round(self.usage["session"]["percent"] + used * 0.04, 1)
        self.usage["week"]["percent"] = round(min(100.0, self.usage["week"]["percent"] + used * 0.004), 1)
        if self.usage["session"]["percent"] >= 100:
            self.usage["session"]["percent"] = 0.0

    def run_forever(self) -> None:
        def loop() -> None:
            while True:
                time.sleep(TICK_SECONDS)
                self.step()

        threading.Thread(target=loop, daemon=True).start()
