# ADR 0010 — Session points, weights and fair sharing

Status: Accepted (2026-11-07, the code matches; deviations are recorded as built)
Date: 2026-11-06

## Context

With parallel increments across several hustles, the hustlemaster needs a limit on how
much runs at once and a rule for who gets the next slot. Models burn the subscription at
very different rates, so counting sessions is too coarse. Too many parallel sessions can
use up the 5h window before any of them finishes.

## Decision

- **Session points** measure what a running session costs: Haiku 1, Sonnet 2, Opus 4,
  Fable 8. The user sets the total for the machine across all projects, sized to the
  subscription (e.g. 20 = 2 Fable + 1 Opus, or 5 Opus, or 20 Haiku). Faster burn is fine:
  the ratio of tokens to finished work stays the same, the work just finishes sooner.
- **Weights** work like CSS grid `fr`: each hustle has a weight of 1 or more; its share
  is its weight divided by the sum of the weights in the pool. Only hustles that are
  **active and have a ready or running increment** are in the pool.
- Shares are **guarantees, not caps** (work-conserving): points a hustle cannot use go to
  the others.
- **No deficit accounting, no throttling afterwards.** When points become free, they go
  to the ready hustle that is currently furthest below its share. A hustle that borrowed
  never pays back; hustles that had to wait are below their share and so are served
  first. Running sessions are never interrupted. An increment bigger than its hustle's
  share starts once enough points are free. Memory over time is added only if a hustle
  turns out to be starved in practice.
- Weights and the total are changed only by the user, in the dashboard. Agents cannot
  request more weight: weights are zero-sum, and borrowing free points already happens
  automatically. An increment costing more than the total is a planning error.
- Overseer sessions cost points like any session and count against their hustle.
- There is no exclusive priority level. Exclusivity is having only one hustle active.

### As built (2026-11-07, `master/hustlemaster/scheduler.py`)

- `choose(hustles, total, sessions=, budget=, settings=)` returns the increments to start
  now and changes nothing; the caller starts them. `sessions` are the running ones,
  including event overseers outside the plan, which count against their hustle and keep
  it in the pool.
- "Furthest below its share" is points in use divided by share; ties go to the hustle
  more points short, then to list order. Within a hustle a parked increment (cut off at
  the limit) goes first, then the plan order; a smaller increment of the same hustle may
  go ahead of one that does not fit.
- **Clarified:** if none of the neediest hustle's ready increments fits the free points,
  the points are held for it and nothing else starts. With pure backfill an increment
  bigger than what happens to be free (a Fable increment beside a hustle full of Haiku
  work) would never start, and "starts once enough points are free" would never hold.
  "Points a hustle cannot use" therefore means: no ready increment, or ready increments
  the budget holds back. Still memoryless: the hold is recomputed on every call.
- Every start passes `Budget.check` (ADR 0008) with the running sessions plus those
  chosen in the same call. A hold on the limit, the 5h window or the week stops all
  starts; a `fit` hold skips that model only. The answer carries the last refusal
  (`held`) and the increment the points are held for (`waiting`) for the dashboard.

## Consequences

- The scheduler needs each hustle's points in use and the readiness of its plan; no
  history of deficits.
- The dashboard shows weight, share and points in use per hustle; the cinema view draws
  shares as beam thickness.
