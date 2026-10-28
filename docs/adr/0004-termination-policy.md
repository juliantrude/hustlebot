# ADR 0004 — Termination policy: soft deadline & PO-mode infinite

Status: Accepted
Date: 2026-07-27

## Context

The charter-bounded loop (ADR 0001) needs an explicit end condition. The user
wants either a deadline date or truly open-ended operation, and values finishing
good work over stopping punctually.

## Decision

**It is always PO mode.** The Planner always behaves like a real product owner:
it invents new features to evolve the software and **may research** (web/docs) to
do so. The termination field only decides *when a feature freeze kicks in*.

`CHARTER.md` carries `Termination: <ISO datetime>` (a **feature-freeze date**)
**or** `Termination: infinite` (no freeze date).

**Deadline = feature freeze, soft/drain — not a hard stop.** When now > the
freeze date:
- the **Planner stops inventing/generating new work** (feature freeze);
- the **Executor keeps draining** already-released Todo / In-Progress items and
  finishes them properly;
- the chain becomes terminal only once the mapped projects' Todo/In-Progress are
  empty *after* the freeze date → the Planner writes a closing report (below) and
  the `STATUS: COMPLETE` sentinel, chain stops.

Rationale: "good finished work > work stopped at time X."

**Infinite = no freeze date.** Runs as PO mode indefinitely.

**Graceful exit from infinite:** the human (or another Claude) writes an ISO date
into `CHARTER.md`'s `Termination` field. That converts the run to the normal
feature-freeze → drain → closing-report → terminal path. Hard abort
(`hustle-scheduler.sh disarm` + stop the unit) remains the emergency brake, but
"set a date" is the clean shutdown.

**Closing report.** As the final terminal step (after drain, before the chain
stops), the Planner writes a report covering everything implemented during its
run, sourced from Plane Done items in the run window + `git log` across mapped
repos, grouped by service/epic. Location: a **Plane Page** in the workspace
(primary — the pages API allows `POST` only, i.e. write-once, read via the UI,
which suits a one-shot report), with automatic fallback to a **"Closing Report"
issue in the Common project** if the page POST fails (e.g. rich-text body format).
No periodic/interim reports — the user browses Done issues on demand to save
tokens.

**Anti-drift guard.** Even in PO mode the Planner stays within the charter's
mission and hard constraints. If it genuinely finds no charter-justified work, it
**parks** (long re-check) and notifies — it does not manufacture out-of-scope
busywork.

## Consequences

- Autonomous feature invention raises the stakes on (a) the charter's hard
  constraints as the real guardrail and (b) whether invented features get any
  human touchpoint before the Executor builds them → decided in ADR 0005.
- The terminal condition is no longer "plan checkboxes all done" but "deadline
  passed AND Plane backlog drained" (or manual abort). The wrapper still honors
  the `STATUS: COMPLETE` sentinel.
- The Planner needs a research capability (web access) in PO mode.
