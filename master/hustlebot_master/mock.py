"""Made-up chains, usage and approvals for the dashboard while the scheduler does not exist yet."""

from __future__ import annotations

from .model import Chain, Increment


def _inc(id, title, model, after="", state="open", text=None):
    return Increment(
        id=id,
        title=title,
        model=model,
        after=after.split() if after else [],
        state=state,
        instructions=text or [f"Implement: {title}.", "Verify with the project's tests, then commit."],
    )


def chains() -> list[Chain]:
    return [
        Chain(
            id="hb-master",
            project="hustlebot",
            title="Multi-chain master",
            active=True,
            weight=2,
            increments=[
                _inc("A", "Data model for chains", "opus", state="done"),
                _inc("B", "Graph file format in the repo", "sonnet", "A", "done"),
                _inc("C", "Session runner (child processes)", "opus", "B", "running",
                     ["Start `claude -p` per increment as a child process with cwd = its worktree.",
                      "Exit code + JSON result mark the increment finished.",
                      "Verify: a dummy increment runs, commits, and is detected as done."]),
                _inc("D", "Worktree per increment, rebase + fast-forward", "opus", "C"),
                _inc("E", "Usage reader prototype", "sonnet", state="done"),
                _inc("F", "Own accounting from JSON results", "sonnet", "E", "running"),
                _inc("G", "Dashboard mock", "haiku", state="done"),
                _inc("H", "systemd user unit", "haiku", state="running"),
                _inc("I", "Scheduler: weights + deficit round robin", "fable", "D F"),
                _inc("J", "End-to-end run over two projects", "opus", "I"),
                _inc("K", "Master README", "haiku", "A"),
                _inc("L", "Dashboard design pass", "sonnet", "G"),
            ],
        ),
        Chain(
            id="fl-sync",
            project="final_lift",
            title="Workout sync API",
            active=True,
            weight=1,
            increments=[
                _inc("A", "Sync endpoint skeleton", "sonnet", state="done"),
                _inc("B", "Conflict resolution rules", "opus", "A", "done"),
                _inc("C", "Offline queue on the client", "sonnet", "A", "running"),
                _inc("D", "Integration tests", "haiku", "B C"),
                _inc("E", "Changelog + docs", "haiku", "D"),
                _inc("F", "Push notifications", "sonnet", "A"),
                _inc("G", "Sync metrics", "haiku"),
                _inc("H", "Load test", "opus", "D F"),
            ],
        ),
        Chain(
            id="adm-graph",
            project="admiralty",
            title="Solution graph v2",
            active=True,
            weight=1,
            increments=[
                _inc("A", "New edge semantics", "opus", state="done"),
                _inc("B", "Migrate stored graphs", "sonnet", "A", "blocked",
                     ["Migrate all stored graphs to the new edge semantics.",
                      "BLOCKED: the savegame fixtures are missing in the repo; needs the user."]),
                _inc("C", "Render with graph-engine 1.1", "sonnet", "A", "done"),
                _inc("D", "Key Action ranking update", "opus", "B C"),
            ],
        ),
        Chain(
            id="ari-search",
            project="ariadne",
            title="Docs search",
            active=False,
            weight=3,
            increments=[
                _inc("A", "Index format", "sonnet", state="done"),
                _inc("B", "Indexer", "sonnet", "A"),
                _inc("C", "Query UI", "haiku", "A"),
                _inc("D", "Ranking", "opus", "B"),
            ],
        ),
        Chain(
            id="hb-docs",
            project="hustlebot",
            title="README overhaul",
            active=False,
            weight=1,
            increments=[
                _inc("A", "Install section", "haiku", state="done"),
                _inc("B", "Charter mode docs", "sonnet", "A", "done"),
            ],
        ),
    ]


APPROVALS = [
    {
        "id": "ap1",
        "chain": "hb-master",
        "text": "Split I into I1 (weights) and I2 (deficit round robin); I2 switches from Opus to Fable.",
    },
    {
        "id": "ap2",
        "chain": "fl-sync",
        "text": "Add 3 increments for a retry policy (graph grows from 5 to 8).",
    },
]
