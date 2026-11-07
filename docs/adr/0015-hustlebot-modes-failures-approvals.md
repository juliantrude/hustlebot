# ADR 0015 — Worker and overseer, failures and approvals

Status: Proposed
Date: 2026-11-07

## Context

A hustle must restructure itself (split an increment that is too big, resolve a stuck
conflict, accept the result) without starting sessions behind the hustlemaster's back,
and without bypassing the user on cost.

## Decision

- A **hustlebot** is one session working in the project. It runs in one of two modes:
  **worker** (implements one increment) or **overseer** (privileged: may change its
  hustle's plan over MCP). Overseers always run on the strongest allowed model. Option
  "one overseer in the hustlemaster for all hustles" was rejected: an overseer must see
  the code. Option "workers start the overseer" was rejected: it would start sessions
  outside the point budget (ADR 0008).
- Overseers run in two ways, both started by the hustlemaster:
  - **planned**, as overseer increments in the plan (always the acceptance increment at
    the end, optionally at milestones);
  - **on events**, outside the plan: an increment failed twice, a worker reported
    `too-big`, a rebase conflict stayed unresolved, or a worker proposed a plan change.
    It runs in the affected worktree, otherwise in its own worktree at `main`.
- **Failures:** a failed attempt or one cut off at the limit resumes in the same
  worktree. After two unsuccessful resumes (ADR 0003's N=2, now per increment) the event
  overseer is started. It splits the increment (`too-big`), upgrades its model, rewrites
  its prompt, or marks it **blocked** (red) when a human is needed. The hustle keeps
  working on every other ready increment; only dependants wait.
- **Approvals** are needed for:
  - an increment's model upgrade that makes it cost more points than its hustle's share;
  - **growth**: more than +25 % session points (sum over the plan) since the last
    confirmation; splitting does not count as long as the parts cost no more together.
  While an approval is pending only the affected increment waits; new increments stay
  provisional and do not run.
- **Auto-approve**, one global setting: off; up to a percentage of growth and at most one
  model step up; or everything. Every automatic approval appears in the dashboard and can
  be revoked. A Fable increment while `allow_fable` is off is never possible.

## Consequences

- The overseer replaces ADR 0003's "hand back to the Planner" for too-big items.
- In charter mode (later) overseer increments also write new increments, guided by
  `VISION.md`.
