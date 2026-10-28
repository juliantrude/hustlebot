# GOAL

<!--
  Single source of truth for the /advance-goal loop (driven headless via the
  one-shot systemd chain, or interactively via `/loop /advance-goal`).
  The loop reads and rewrites this file every iteration.
-->

## North star

<!-- One paragraph: the outcome, the authoritative specs it is measured
     against, and any standing duty (e.g. "keep assigned review work
     processed"). -->

## Definition of Done

<!-- 2–5 checkable criteria. Each must be verifiable without judgment calls.
     State explicitly whether "fully blocked externally" counts as done. -->
- [ ] …
- [ ] …

## Working Agreements (read every iteration — these override convenience)

**Priority order per iteration:**
1. <!-- reactive work first? e.g. items assigned to the user on GitLab -->
2. Then take the next unchecked increment from `## Plan`.

**Git workflow:**
<!-- branching model, verification bar before commits, MR etiquette,
     reviewer assignment (and how to pick), description requirements -->

**Out-of-scope systems:**
<!-- what must never be modified; escalation path per system with owners -->

**Etiquette:**
<!-- communication rules: comment quality, language, tone -->

**References:**
<!-- allowed sources to copy from, canonical docs, ordering rationale -->

## Plan

<!-- One increment per iteration; check off only when done AND verified.
     ### section per work stream, ordered by dependency. Each increment
     completable+verifiable in one run (~30–90 min). Under-specified areas
     start with a "study spec, write PLAN.md" increment. -->

### <stream 1>
- [ ] …
- [ ] …

### Closeout
- [ ] Final sweep: everything at spec or documented-blocked; DoD checkboxes above updated

## Status

STATUS: READY
**Next action:** <!-- the first concrete step, precise enough for a cold start -->
**Blockers:** none

## Budget

- session pause threshold: 85%
- weekly pause threshold: 90%

## Log

<!-- Append-only, dated. One line per iteration. Newest at the bottom. -->
- <date> — GOAL.md created via /grill-goal; nothing started yet
