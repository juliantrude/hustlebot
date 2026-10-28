# ADR 0005 — Feature approval gate (non-blocking)

Status: Accepted
Date: 2026-07-27

## Context

In PO mode (ADR 0004) the Planner invents features autonomously and the Executor
builds, commits and pushes them unattended. The user wants direction control
without blocking the loop while away (holiday), and accepts residual goal-drift
risk, mitigated by the initial charter grilling.

## Decision

A charter switch **`Feature approval: auto | review`**, default **`review`**.

- **`review` (default):** Planner-invented feature epics are created in the
  Common project in **Backlog** state (visible, label `bot-proposed`). They are
  NOT built until a human moves them to **Todo**. Non-blocking: while nobody
  approves, the Executor drains already-approved work and then parks — it does not
  build newly-invented scope. Human-groomed work still flows immediately;
  only *self-invented* scope waits for a nod.
- **`auto`:** invented feature epics flow straight to decomposition into Todo and
  execution. Control is purely async (watch Plane, hard-abort to stop). This is
  the intended holiday setting: the whole run can be started autonomous.

Goal-drift risk in `auto` is knowingly accepted; the initial charter grilling
(mission + hard constraints) is the mitigation.

## Consequences

- `review` vs `auto` separates "self-invented scope" (needs a nod) from
  "human-given scope" (runs immediately). Autonomy stays in *execution*; the human
  keeps *direction*.
- The Executor's "pull only from Todo" rule (ADR 0003) already enforces the gate:
  a Backlog epic's children aren't created/pulled until approval flips it.
