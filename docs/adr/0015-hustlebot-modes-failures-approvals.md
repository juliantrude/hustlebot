# ADR 0015 — Worker and overseer, failures and approvals

Status: Accepted (2026-11-07, the code matches; deviations are recorded as built)
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

## As built: failures and event overseers (`hustlemaster/events.py`)

- **Two failures, not three.** "After two unsuccessful resumes" read literally means three failed attempts; the
  event list says "failed twice" and ADR 0003's guard handed off after two. Built: the second failed attempt of
  an increment starts the event overseer (`FAILURES = 2`). Failed means a report `failed`, no usable report, or a
  merge that counts as an attempt (`no-commit`, `conflict`, `verify-failed`); the second of those is a
  `conflict` event when it was a conflict. A limit cut-off (parked) and a merge blocked by the user's own changes
  (`main-busy`) cost no attempt.
- **No resume for what a resume cannot fix.** A worker's `too-big` report starts the overseer at once, and so does
  a worker's `blocked` report (as a `failed` event): another attempt would only say the same again.
- **One event overseer per hustle at a time**, oldest event first, and none while a planned overseer increment of
  the hustle runs: overseers change the plan, two at once would write over each other's plan versions. Open
  proposals are gathered into one `proposal` event.
- **An overseer holds the free points.** If a due event overseer does not fit, its hustle's smaller increments may
  not go ahead of it (they could, for plan increments, ADR 0010): in a busy hustle the overseer would otherwise
  never see enough free points, and the work it is about would wait forever.
- **Strongest allowed model that the total can carry:** Fable if allowed, else Opus; with fewer total points than
  that, the strongest model that fits at all.
- **How it ends.** Reported `done`: the event is handled, the increment starts over with fresh attempts and is
  told what the overseer said (unless the overseer split it away). Reported `blocked`: the increment is marked
  blocked, only its dependants wait. Anything else fails the event and blocks the increment as well — the
  overseer is the last automatic resort — and drops its proposals. Cut off at the limit, the event stays open.
- **Proposals in a report** repeat what the worker sent with `propose_change`. They are stored only when none of
  the session's own proposals arrived over MCP (its calls failed, or it forgot), so nothing is lost and nothing
  is decided twice. Each sync gathers what is open, so proposals arriving while a proposal overseer runs form the
  next event; a proposal overseer works in its own worktree at `main`, removed when it ends.
- **A planned overseer increment** (the acceptance) ends `done` without a commit: it is done without a merge.
  Only if it committed (e.g. resolved something in its worktree) is it merged like a worker.
- While an event is open or running its increment is **held** (`Increment.held`), so it is not ready even when
  its dependencies are done. The approvals increment uses the same field for pending approvals.

## As built: approvals (`hustlemaster/approvals.py`, `Store.approve` / `reject` / `revoke`)

- **Judged on every plan change after the first activation**, the whole plan against the confirmed baseline
  (`confirmed_version`), so growth adds up across changes until the next confirmation. Before the first
  activation nothing is judged: the activation confirms whatever the plan is then. A newer change supersedes a
  pending approval and is judged again as a whole; at most one approval per hustle is pending.
- **Growth is the plain sum** of session points over the plan. A split whose parts cost no more together adds
  nothing, so the split exemption holds by construction; a haiku increment split into two haiku parts does cost
  more and counts.
- **The share is the guarantee**: total points × weight over the weights of all active hustles (the judged one
  counted as active), not the scheduler's momentary share, which rises whenever others are idle. A **new**
  increment above the share counts as an upgrade from nothing: a split part on a stronger model must not slip
  past the upgrade rule.
- **What waits:** an increment upgraded beyond the share (`held = "approval"`); when the growth is too much, also
  every increment new since the baseline (`held = "provisional"`). An open or running event overseer takes
  precedence (`held = "overseer"`). Dependants wait by their dependencies; the rest of the hustle goes on.
- **Decisions** are dashboard only (ADR 0014). Approve confirms the current plan as the new baseline. Reject blocks
  the waiting increments that are still open, leaving the baseline where it was: reverting the model would hand
  the increment back to the one that already failed it twice. Revoke takes back an automatic approval — only the
  latest confirmation, since a later one built on it — moves the baseline back and judges the plan again for the
  user, without auto-approve; what already ran stays done.
- **Auto-approve** `limited`: growth up to `auto_approve_growth` % and every upgrade at most one model step
  (haiku → sonnet → opus → fable); a new increment above the share is no step and is left to the user. Automatic
  approvals are recorded (`approvals`, `automatic = 1`) like the user's.
- An overseer's `change_plan` over MCP answers with the pending approval, if any, so it knows what will not run.
