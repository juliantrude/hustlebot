# ADR 0009 — A hustle is a dependency graph with a model per increment

Status: Proposed (revised 2026-11-07 after the grilling)
Date: 2026-11-06

## Context

A chain is 100 % sequential today: the plan in `GOAL.md` is an ordered checklist and one
increment runs at a time. Much of that work does not depend on the previous step, so a
chain is slower than it needs to be. The model is chosen per run, not per increment.
"Chain" no longer fits; the unit is renamed **hustle** (glossary in `CONTEXT.md`).

## Decision

- A hustle's **plan** is a directed acyclic graph of **increments**. Only increments that
  depend on each other get a dependency: one needs another's result, or both change the
  same files. Independent ones run in parallel and should touch disjoint files.
  Example: A→B→C→D, E→F, G, H, (D and F)→I→J — A, E, G and H start at once.
- A hustle belongs to exactly **one repo**. Work across repos is several hustles; there
  are no dependencies between hustles for now.
- Every increment carries: a stable id, a title, a **self-contained prompt** (including
  how to verify it), its dependencies, a **model** (Haiku, Sonnet, Opus, Fable) and a
  **mode** (`worker` or `overseer`, ADR 0015). Overseer increments always run on the
  strongest allowed model (Fable if Fable is allowed, else Opus).
- The hustle itself carries a short **brief**: north star, Definition of Done and
  hustle-specific agreements (e.g. "never push"). There is no `GOAL.md` any more.
  Project-wide rules belong in the project's `CLAUDE.md`.
- **Sizing:** a planned increment is one task only (for parallelism) and should fit a
  session with at most about **600k tokens of context** — a guideline with headroom for
  misestimates, not a hard cap. Increments split off at runtime may plan up to the 1M
  context window. The hustlemaster records each increment's peak context as feedback.
- **Model choice** while planning: Haiku for mechanical work with a clear pattern, Sonnet
  as the default for implementation, Opus for design, hard debugging and anything where a
  wrong decision is expensive, Fable only where Opus is likely to fail or need several
  attempts, and only if the hustlemaster's `allow_fable` setting is on. When in doubt,
  the cheaper model; the overseer can upgrade after a failure.
- Every plan ends with an **acceptance increment** in overseer mode that depends on all
  others and checks the Definition of Done against `main`. Further overseer increments
  may be planned at milestones (e.g. to sharpen the rest of the plan after a foundation
  phase).
- The plan is **owned by the hustlemaster** (ADR 0014), not stored in the repo.
- A submitted hustle arrives **inactive**. Before its first activation the user can edit
  it in the graph view; the first activation confirms it and fixes the baseline that
  later growth is measured against (ADR 0015).
- Increment states: done, running, open (waiting or ready), blocked (needs a human).

## Consequences

- The planning skill `/hustle` finds dependencies, splits for parallelism, sizes and picks
  a model per increment, and submits the plan over MCP (ADR 0014). It also converts a
  running sequential `GOAL.md` into a plan (migration).
- The executor skill runs one given increment instead of the next unchecked line.
- The dashboard renders the plan with graph-engine (rootless graphs, the `active` look,
  `?view=mini`): one node type per model, state as colour.
