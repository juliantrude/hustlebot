---
name: grill-goal
description: Build a GOAL.md for the /advance-goal loop through a relentless grilling interview. Use when the user wants to set up a new long-running goal for the cross-session loop, or wants to sharpen/rewrite an existing GOAL.md.
---

# grill-goal — interview the user into a loop-ready GOAL.md

Run a grilling-style interview whose end product is a **`GOAL.md`** in the
current project root that the `/advance-goal` loop can execute unattended.

**The grilling method** (self-contained — no other skill required): interview
the user relentlessly about every aspect of the goal until you reach shared
understanding. Walk down each branch of the decision tree, resolving
dependencies between decisions one by one. Ask **one question at a time**,
always **with your recommended answer**, and wait for feedback before the next
question. If a question can be answered by exploring the codebase, explore the
codebase instead of asking.

## Why this needs grilling

The loop runs headless with no human in the loop: every ambiguity you leave in
GOAL.md becomes a guess made by an unattended agent with full permissions.
The interview's job is to remove those guesses **before** the chain starts.

## Procedure

1. **Explore first.** Before asking anything, read the project's docs, service
   specs / CLAUDE.md files, git remotes, and any existing GOAL.md. Derive every
   answer you can from the repo; only interview about what genuinely lives in
   the user's head.
2. **Grill** — walk the branches below in order. Stop a branch early when the
   answers make the rest derivable.
3. **Write GOAL.md** from `TEMPLATE.md` (same directory as this skill), filling
   every section — no `_TODO:` markers may survive (the loop's readiness guard
   refuses to start otherwise).
4. **Review pass:** show the user the draft, grill once more over anything that
   still smells vague, then set `STATUS: READY` and remind them how to start
   the chain: Linux `systemctl --user start hustle-<slug>.service`, macOS
   `.hustle/bin/hustle-session.sh &`, or interactively `/loop /advance-goal`.
   (If `.hustle/` doesn't exist yet, point them at `/loop-setup` first.)

## Interview branches (in order)

1. **North star & DoD** — What outcome, in one paragraph? What are the 2–5
   *checkable* completion criteria? Push back on anything unmeasurable
   ("improve X" → "X does Y, verified by Z").
2. **Scope boundary** — What exactly is in scope; what must the loop **never
   touch**? For out-of-scope-but-adjacent systems: what is the escalation path
   (e.g. change request work items, and who owns what)?
3. **Workflow rules** — Branching model, commit/verification bar, MR/PR
   etiquette, reviewer assignment, communication rules. Anything the user would
   correct in a human coworker's first week belongs here.
4. **Increment sizing** — Break the goal into `###` sections with `- [ ]`
   increments, ordered by dependency. Each increment must be completable +
   verifiable in one run (roughly 30–90 min); the first increment of an
   under-specified area should be "study spec, write PLAN.md" so the loop
   refines its own plan instead of you inventing detail now.
5. **Blocked-work policy** — When an increment is blocked externally: document
   where, escalate how, and what to work on instead. The DoD must say whether
   "fully blocked" counts as done.
6. **Budget & pacing** — Keep the default thresholds (session 85% / weekly 90%)
   unless the user wants otherwise; ask only if their subscription usage is
   shared with other work.
7. **Priorities between reactive and planned work** — e.g. "process items
   assigned to me on GitLab before plan increments". Make the order explicit.

## Format contract (do not deviate — tooling parses this)

The `/advance-goal` skill and the monitoring dashboard parse GOAL.md
mechanically:

- Section headers exactly: `## North star`, `## Definition of Done`,
  `## Working Agreements`, `## Plan`, `## Status`, `## Budget`, `## Log`.
- Checkboxes as `- [ ]` / `- [x]`; plan subsections as `### <name>`;
  wrapped checkbox lines are indented continuation lines.
- `## Status` must contain a `STATUS: <READY|...>` line, a `**Next action:**`
  line pointing at the first concrete step, and a `**Blockers:**` line.
- `## Log` is append-only, one dated line per iteration.
