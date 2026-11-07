"""Hustles, increments and the session points of a model."""

from __future__ import annotations

from dataclasses import dataclass, field

# Session points per model: what one running session of it costs from the user's total.
POINTS = {"haiku": 1, "sonnet": 2, "opus": 4, "fable": 8}

# Increment states. "open" covers both waiting for a dependency and ready to start.
STATES = ("done", "running", "open", "blocked")

# A worker implements one increment; an overseer may change its hustle's plan (ADR 0015).
MODES = ("worker", "overseer")


@dataclass
class Increment:
    id: str
    title: str
    model: str
    instructions: list[str]
    after: list[str] = field(default_factory=list)  # ids this increment depends on
    state: str = "open"
    prompt: str = ""  # self-contained, including how to verify it
    mode: str = "worker"
    attempts: int = 0
    peak_context: int = 0  # tokens; sizing feedback for the next plans (ADR 0009)
    # why it may not start although its dependencies are done: "overseer", "paused" (the user), or an approval
    held: str = ""


@dataclass
class Hustle:
    id: str
    project: str
    title: str
    active: bool
    weight: int  # its share among the active hustles, like a CSS grid `fr`
    increments: list[Increment]
    brief: str = ""  # north star, Definition of Done and hustle-specific agreements
    version: int = 0  # current plan version
    confirmed_version: int | None = None  # set by the first activation; growth is measured against it

    def running(self) -> list[Increment]:
        return [i for i in self.increments if i.state == "running"]

    def ready(self) -> list[Increment]:
        done = {i.id for i in self.increments if i.state == "done"}
        return [i for i in self.increments if i.state == "open" and not i.held and set(i.after) <= done]

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

