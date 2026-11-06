# ADR 0008 — A central master owns scheduling and the budget

Status: Proposed
Date: 2026-11-06

## Context

Today one project runs one chain, and each chain arms its own one-shot timer and checks
the budget itself between increments (ADR 0003). With several chains running at once,
possibly several per project, self-arming cannot work: each chain would see the same
subscription and decide alone, nobody would weigh one chain against another, and the
budget guard would race. The headless `/usage` report is also unreliable inside a
session (see `scripts/hustle-session.sh`, section 6b), so the in-skill budget check
never saw the limit reliably.

## Decision

- A single **master** per machine owns every timer and the Claude Code resources. It is
  a normal program, not an agent (ADR 0013).
- The master polls the subscription usage on its own schedule (ADR 0012). Sessions no
  longer check usage, and increments never start themselves.
- When an increment finishes, the master learns it from the child process ending
  (ADR 0011) and decides what starts next, by readiness, free session points and weight
  (ADR 0010).
- The master stops starting new sessions above a usage threshold. A session that still
  runs into the limit is parked and resumed in the same worktree in the next window.
  Thresholds are tuned by experience; the twin goal is never to hit a limit and never to
  leave budget unused, the weekly budget above all.
- Deactivating a chain stops new sessions; running ones finish cleanly.

## Consequences

- Supersedes the self-arming timer and the in-skill budget guard of ADR 0003 for the new
  loop. The work-item lifecycle and the "done only after verification" bar stay.
- `/advance-goal` changes role: it no longer picks its increment, checks the budget or
  arms the next run. The master hands it exactly one increment and its context.
- `hustle-scheduler.sh` and the per-project systemd timers go away once the master runs.
- Charter mode is out of scope for now; how the Planner fits under the master is decided
  later.
