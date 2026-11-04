"""Chains, increments and the session points of a model."""

from __future__ import annotations

from dataclasses import dataclass, field

# Session points per model: what one running session of it costs from the user's total.
POINTS = {"haiku": 1, "sonnet": 2, "opus": 4, "fable": 8}

# Increment states. "open" covers both waiting for a dependency and ready to start.
STATES = ("done", "running", "open", "blocked")


@dataclass
class Increment:
    id: str
    title: str
    model: str
    instructions: list[str]
    after: list[str] = field(default_factory=list)  # ids this increment depends on
    state: str = "open"


@dataclass
class Chain:
    id: str
    project: str
    title: str
    active: bool
    weight: int  # its share among the active chains, like a CSS grid `fr`
    increments: list[Increment]

    def running(self) -> list[Increment]:
        return [i for i in self.increments if i.state == "running"]

    def ready(self) -> list[Increment]:
        done = {i.id for i in self.increments if i.state == "done"}
        return [i for i in self.increments if i.state == "open" and set(i.after) <= done]

    def points_in_use(self) -> int:
        return sum(POINTS[i.model] for i in self.running())

    def status(self) -> str:
        if all(i.state == "done" for i in self.increments):
            return "done"
        if not self.active:
            return "paused" if not self.running() else "stopping"
        if self.running():
            return "running"
        if self.ready():
            return "waiting"  # runnable, waiting for free points
        if any(i.state == "blocked" for i in self.increments):
            return "blocked"
        return "waiting"


def shares(chains: list[Chain], total: int) -> dict[str, float]:
    """Guaranteed points per chain: the total split by weight among the chains that are active and have
    something ready or running. Inactive or fully blocked chains hold no share."""
    pool = [c for c in chains if c.active and (c.running() or c.ready())]
    weights = sum(c.weight for c in pool)
    return {c.id: (total * c.weight / weights if weights else 0.0) for c in pool}
