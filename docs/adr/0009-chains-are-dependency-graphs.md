# ADR 0009 — Chains are dependency graphs with a model per increment

Status: Proposed
Date: 2026-11-06

## Context

A chain is 100 % sequential today: the plan in `GOAL.md` is an ordered checklist and one
increment runs at a time. Much of that work does not depend on the previous step, so a
chain is slower than it needs to be. The model is chosen per run, not per increment.

## Decision

- A chain is planned as a **directed acyclic graph** of increments. Only increments that
  depend on each other are chained; independent ones run in parallel. Example: A→B→C→D,
  E→F, G, H, (D and F)→I→J — A, E, G and H start at once, I waits for D and F.
- Every increment carries its own **model** (Haiku, Sonnet, Opus, Fable), chosen while
  planning by complexity and risk.
- The graph lives **in the project repo**, versioned, readable and editable by the user.
  Runtime state (what runs, points, deficits) lives with the master (ADR 0013).
- The goal grilling produces the graph and the user reviews and confirms it in the
  dashboard's graph view before the chain starts.
- The chain may **restructure its own graph** while working, within bounds: splitting and
  reordering are allowed; new increments must serve the confirmed goal; switching an
  increment to a more expensive model or growing the graph significantly needs the
  user's approval in the dashboard.
- Increment states: done, running, open (waiting or ready), blocked (hard, needs a human).

## Consequences

- The planning skill (`/grill-goal`, later `/generate-plan`) must find dependencies, split
  work so it can run in parallel, and propose a model per increment.
- `/advance-goal` executes one given increment of the graph instead of the next unchecked
  line.
- The file format of the graph is still open (handoff document).
- The dashboard renders the graph with graph-engine 1.1 (rootless dependency graphs, the
  `active` look, `?view=mini` previews): one node type per model, state as colour.
