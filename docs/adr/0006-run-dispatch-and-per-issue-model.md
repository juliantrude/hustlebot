# ADR 0006 — Run dispatch & per-issue model assignment

Status: Accepted
Date: 2026-07-27

## Context

Each headless run must launch either the Planner (strong model, e.g. Opus) or the
Executor (fast model, e.g. Sonnet). The model is a CLI flag chosen *before* the
claude process starts, so the shell wrapper must know the phase up front. The
backlog now lives in Plane (ADR 0002), so "is there work?" is an API query, not a
grep. The user also wants **per-issue** model choice: the Planner decides, task by
task, whether Opus or Sonnet should execute it (dynamic senior/junior routing).

## Decision

**Marker-based handoff (the LLM decides, the wrapper only maps marker → model).**

- The wrapper reads `.hustle/next-phase`; absent → `planner` (first run bootstraps
  planning). Values:
  - `planner` → `claude -p "/generate-plan" --model $HUSTLE_PLAN_MODEL`
  - `executor:<model>` → `claude -p "/advance-goal" --model <model>`
- The skill that just ran writes the next marker (it already has Plane access):
  - **Executor** finishes its item, checks Plane for more pullable Todo/In-Progress
    in mapped projects. More work → `executor:<model>` where `<model>` is read from
    the **next** item's model label; queue empty → `planner`; frozen+drained →
    terminal (`STATUS: COMPLETE`).
  - **Planner** after generating/decomposing → `executor:<model>` for the first
    item; nothing to plan → park or terminal.

**Model is a property of the issue, set by the Planner.** Each issue gets a Plane
label `model:opus` / `model:sonnet` (PO-style complexity/risk triage). The marker
carries the next item's model. No label → fallback to `HUSTLE_MODEL`.

## Consequences

- Bash stays dumb (marker → `--model`); Plane queries + reasoning stay in the LLM,
  where API access already is. Matches "Opus plans, Sonnet iterates" via two
  separate `claude -p` processes.
- Per-issue model is a **look-ahead** peek at the end of the previous run. If the
  human re-prioritizes between runs, the next item's model may be momentarily
  stale; self-corrects the following cycle. Acceptable in a single-chain backlog.
- New config: `HUSTLE_PLAN_MODEL` (planner) alongside `HUSTLE_MODEL` (executor
  default/fallback).
- Rejected: wrapper-side Plane dispatch (option A) — freshest model pick, but pulls
  Plane logic, the mapping, and the API key into the shell for marginal gain.
