"""What makes a plan acceptable: the checks every submitted or changed plan must pass (ADR 0009, 0010, 0014)."""

from __future__ import annotations

from .model import MODES, POINTS, Increment


class PlanError(ValueError):
    """A plan was rejected; `problems` lists every reason, so a planner can fix them in one go."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def problems(increments: list[Increment], *, allow_fable: bool, total_points: int) -> list[str]:
    found: list[str] = []
    if not increments:
        return ["the plan has no increments"]

    ids = [i.id for i in increments]
    found += [f"duplicate increment id {d!r}" for d in sorted({i for i in ids if ids.count(i) > 1})]
    known = set(ids)

    for inc in increments:
        if not inc.id:
            found.append("an increment has an empty id")
        if inc.model not in POINTS:
            found.append(f"{inc.id}: unknown model {inc.model!r}")
        elif inc.model == "fable" and not allow_fable:
            found.append(f"{inc.id}: Fable is not allowed (allow_fable is off)")
        elif POINTS[inc.model] > total_points:
            found.append(f"{inc.id}: {inc.model} costs {POINTS[inc.model]} points, more than the total of {total_points}")
        if inc.mode not in MODES:
            found.append(f"{inc.id}: unknown mode {inc.mode!r}")
        if not inc.prompt.strip():
            found.append(f"{inc.id}: empty prompt")
        found += [f"{inc.id}: depends on unknown increment {d!r}" for d in inc.after if d not in known]
        if inc.id in inc.after:
            found.append(f"{inc.id}: depends on itself")

    cycle = _cycle(increments)
    if cycle:
        found.append("dependency cycle: " + " → ".join(cycle))
    elif not _acceptance(increments):
        found.append("no acceptance increment: an overseer increment that depends on all others")
    return found


def check(increments: list[Increment], *, allow_fable: bool, total_points: int) -> None:
    found = problems(increments, allow_fable=allow_fable, total_points=total_points)
    if found:
        raise PlanError(found)


def _cycle(increments: list[Increment]) -> list[str]:
    deps = {i.id: [d for d in i.after if d != i.id] for i in increments}
    colour: dict[str, int] = {}  # 1 on the current path, 2 finished
    path: list[str] = []

    def visit(node: str) -> list[str]:
        colour[node] = 1
        path.append(node)
        for dep in deps.get(node, []):
            if colour.get(dep) == 1:
                return path[path.index(dep):] + [dep]
            if dep in deps and dep not in colour and (found := visit(dep)):
                return found
        path.pop()
        colour[node] = 2
        return []

    for node in deps:
        if node not in colour and (found := visit(node)):
            return found
    return []


def _ancestors(increments: list[Increment]) -> dict[str, set[str]]:
    deps = {i.id: set(i.after) for i in increments}
    memo: dict[str, set[str]] = {}

    def of(node: str) -> set[str]:
        if node not in memo:
            memo[node] = set()
            for dep in deps.get(node, ()):
                memo[node] |= {dep} | of(dep)
        return memo[node]

    return {node: of(node) for node in deps}


def _acceptance(increments: list[Increment]) -> bool:
    # Transitive dependencies count: an acceptance increment after the last milestone overseer still waits for everything.
    ancestors = _ancestors(increments)
    others = {i.id for i in increments}
    return any(i.mode == "overseer" and ancestors[i.id] == others - {i.id} for i in increments)
