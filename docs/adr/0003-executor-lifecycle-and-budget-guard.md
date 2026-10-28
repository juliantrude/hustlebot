# ADR 0003 — Executor work-item lifecycle & budget guard

Status: Accepted
Date: 2026-07-27

## Context

With the backlog in Plane (ADR 0002), the Executor pulls one work item per run.
Two risks: (1) two runs grabbing the same item; (2) an item too big to finish in
one session budget window — which can never reach Done, since Done requires a
verified, complete item. A run that starts an item at 84% session usage can run
out mid-item.

## Decision

**State usage (real Plane states: backlog/unstarted=Todo/started=In Progress/
completed=Done/cancelled):**

- Planner puts execution-ready items in **Todo**; **Backlog** = groomed but not
  released. The Executor pulls only from **Todo** in mapped projects.
- **Pick = claim:** on selecting an item, immediately set **In Progress** + assign
  to the bot ("Claude Code") + comment "picked up, run <ts>". This state change is
  the lock that keeps the next run from grabbing the same item.
- **Done only after verification.** Blocker → comment + back to Todo; hard external
  block → label `blocked` + skip.

**Crash recovery (one-in-flight invariant):** each run first looks for an item
already **In Progress AND assigned to the bot** and resumes it before pulling a
new Todo. A crashed run thus cleans up after itself.

**Budget guard — check before claiming:** unlike today's advance-goal (budget
checked after the work), the Executor checks budget **before** picking. If
`session ≥ pause threshold`, it claims nothing new, arms the next run for the
reset, and exits. So an item is only started with real headroom.

**Item sizing is a correctness requirement.** The Planner sizes each item to be
completable + verifiable in one fresh session window (heuristic: one focused
change, one service; a title needing "and" must be split). Under-specified areas
start with a "study spec, split" item.

**Resume guard:** the Executor increments a resume counter each recovery. After
**N=2** unsuccessful resumes, it labels the item `too-big`, comments the actual
effort, and hands it back to the Planner to split — instead of burning budget
forever.

## Consequences

- The Executor's protocol reorders to: recover → budget pre-check → claim → work
  → verify/Done → arm next.
- The wrapper safety-net (`hustle-session.sh` re-arms +15m if nothing scheduled)
  still backstops a run that dies before arming; recovery re-picks the In-Progress
  item next time.
- Planner needs a feedback signal (resume counts / `too-big` labels) to size
  better over cycles.
