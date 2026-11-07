# ADR 0008 — The hustlemaster owns scheduling and the budget

Status: Proposed (revised 2026-11-07 after the grilling, see `docs/ideas/next-version.md`)
Date: 2026-11-06

## Context

Today one project runs one chain, and each chain arms its own one-shot timer and checks
the budget itself between increments (ADR 0003). With several hustles running at once,
possibly several per project, self-arming cannot work: each would see the same
subscription and decide alone, nobody would weigh one against another, and the budget
guard would race. The headless `/usage` report is also unreliable inside a session (see
`scripts/hustle-session.sh`, section 6b), so the in-skill budget check never saw the
limit reliably.

## Decision

- A single background service per machine, the **hustlemaster**, owns every timer and the
  Claude Code resources. It is a normal program, not an agent (ADR 0013).
- The hustlemaster polls the subscription usage on its own schedule (ADR 0012). Sessions
  never check usage, and increments never start themselves. No session starts another
  session; that includes the overseer (ADR 0015).
- When an increment finishes, the hustlemaster learns it from the child process ending
  (ADR 0011) and decides what starts next, by readiness, free session points and weight
  (ADR 0010).
- **Thresholds are set by the user** in the control view, as two curved sliders on the
  energy core's rings (ADR 0013):
  - inner ring, 5h window: the hustlemaster starts nothing new above this usage
    (default 85 %);
  - outer ring, week: the usage the week should have reached at its reset (default 95 %).
    The hustlemaster draws a linear target line from the start of the week to that value
    at the reset and starts nothing new while the usage is above the line.
  Before a start it also estimates, from its own accounting (ADR 0012), whether the
  session fits the remaining window. A session that still runs into the limit is parked
  and resumed in the same worktree in the next window.
- Deactivating a hustle stops new sessions; running ones finish cleanly.
- No notifications for now: the hustlemaster is expected to run on a VM. Phone
  notifications come once a general channel between the user and the agents exists.

## Consequences

- Supersedes the self-arming timer and the in-skill budget guard of ADR 0003 for the new
  loop. The "done only after verification" bar stays.
- The executor skill no longer picks its increment, checks the budget or arms the next
  run. The hustlemaster hands it exactly one increment and its context (ADR 0011).
- `hustle-scheduler.sh` and the per-project systemd timers go away once the hustlemaster
  runs.
- Charter mode stays out of scope. When it comes, it reads a `VISION.md` in the repo, and
  overseer increments write the new increments (ADR 0015).
