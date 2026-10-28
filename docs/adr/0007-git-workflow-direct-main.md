# ADR 0007 — Executor git workflow: direct-main (switchable)

Status: Accepted
Date: 2026-07-27

## Context

For the first target (final_lift) the bot is the sole developer, there is no prod
environment yet, and CI is still being built (`feature/cicd-pipeline`). The
team-core default is branch-off-`dev` + MR, but that overhead buys little for a
solo, no-prod, no-reviewer setup.

## Decision

**Charter switch `Git-Workflow: direct-main | branch-mr`.** For final_lift:
**direct-main**.

- The Executor commits directly to `main` and pushes to `origin/main`, so the user
  sees everything remotely and Plane Done ↔ git history stay aligned.
- **One commit per work item**, message referencing the Plane issue id
  (`WORKOUT-42: …`), so each change is a clean, individually revertable unit
  mapped 1:1 to an issue — post-hoc review/rollback stays easy despite no MR gate.
- Flip to `branch-mr` once prod, CI-gating, or a second developer appears — no
  skill rewrite needed.

The real safety is the **verification bar before Done** (ADR 0003), not branch
isolation: verify → commit + push → issue to Done. Unchanged in both modes.

Consequence: no MR ⇒ no reviewer-assignment question in direct-main mode.

## Notification (deferred)

No active notification rule for now. `blocked` / `too-big` items are surfaced only
in Plane; the user browses on demand. A push/tag ping for those two exception
states is a possible later addition.
