# ADR 0008 — The hustlemaster owns scheduling and the budget

Status: Accepted (2026-11-07, the code matches; deviations are recorded as built)
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

### As built (2026-11-07, `master/hustlemaster/budget.py`)

- `Budget.check(model, settings, running)` asks, in order: the safety net (a limit turned a
  session away), the 5h threshold (at or above it, like the old loop's `≥ 85 %`), the weekly
  target line, and the fit. A refusal names when it should lift: the 5h reset, the moment
  the line catches up with the usage (or the weekly reset), or the reset the limit named.
- **The fit** is in dollars, the unit of ADR 0012: the mean `total_cost_usd` of the model's
  last ten finished sessions (else the mean per point of all models × its points), plus the
  same for every running session, times the learned percent per dollar of each window. The
  session waits if that would carry either window past 100 %. With no learned rate or no
  finished session there is nothing to estimate from and it may start; the safety net
  catches a miss. A per-model weekly window shown at 100 % holds back that model only.
- **Parking:** a session cut off by a limit does **not** count as an attempt (ADR 0015's
  two resumes are for the increment's own failures). Its worktree stays, the block holds
  every start until the next window, and the increment's next session gets a note to
  continue from what is in the worktree. Its cost is accounted for the usage but kept out
  of the cost history: a cut-off session says nothing about what a whole one costs.
- Cost history and parked increments live in memory. After a restart the orphan scan
  (ADR 0011) finds the worktree and resumes it; only the note is lost.

## Consequences

- Supersedes the self-arming timer and the in-skill budget guard of ADR 0003 for the new
  loop. The "done only after verification" bar stays.
- The executor skill no longer picks its increment, checks the budget or arms the next
  run. The hustlemaster hands it exactly one increment and its context (ADR 0011).
- `hustle-scheduler.sh` and the per-project systemd timers go away once the hustlemaster
  runs.
- Charter mode stays out of scope. When it comes, it reads a `VISION.md` in the repo, and
  overseer increments write the new increments (ADR 0015).
