---
name: grill-charter
description: Build a CHARTER.md for hustlebot charter mode through a relentless grilling interview, wire up the Plane connection, and switch the project into charter mode. Use to set up the self-directed PO+Dev loop, or to sharpen an existing CHARTER.md.
---

# grill-charter — interview the user into a loop-ready CHARTER.md

End product: a **`CHARTER.md`** in the project root that hustlebot's **charter
mode** runs against — the Planner (`/generate-plan`) invents work from it, the
Executor (`/advance-goal`) builds that work from the **Plane** backlog. Unlike
`/grill-goal` (which writes the whole plan), you write the **mandate**; the plan
is generated later by the Planner.

**The grilling method:** interview relentlessly, one question at a time, each with
your recommended answer, resolving the decision tree branch by branch. Wait for
feedback before the next question. Anything you can settle by exploring the repo or
Plane, settle it yourself instead of asking.

## Why this needs grilling

Charter mode runs headless with full permissions and, in `infinite`/`auto`,
**invents and builds features on its own**. Every fuzzy line in the mission or a
missing hard constraint becomes an unattended guess. The charter is the only thing
replacing the human review that plan mode's grilling gives every increment — so
the **mission and hard constraints are the branches to grill hardest**. Residual
goal-drift risk in `auto` mode is real and accepted; tight constraints are the
mitigation.

## Procedure

1. **Explore first.**
   - Read the project's repos, their `CLAUDE.md`s, git remotes and default
     branches (is there a `dev`? a prod env? CI?), and any existing `CHARTER.md`.
   - **Connect to Plane** to ground the mapping: get the API key from the user
     (or `.hustle/secrets` if already set), then
     `GET $PLANE/api/v1/workspaces/<slug>/projects/` with header
     `X-API-Key: $PLANE_API_KEY` and list the real projects + ids. Propose the
     `project_id → repo` mapping from name similarity and let the user correct it.
     (Never print the key.)
2. **Grill** the branches below in order; stop a branch early when the answers make
   the rest derivable.
3. **Write `CHARTER.md`** from `CHARTER.template.md` (same directory), filling every
   `_TODO:` — none may survive (the Planner's readiness guard refuses to start
   otherwise).
4. **Wire up the runtime:**
   - Put the Plane API key in `.hustle/secrets` as `export PLANE_API_KEY="…"`
     (`chmod 600`; the file is gitignored). Do **not** put it in `CHARTER.md` or
     `.hustle/config`.
   - Set `HUSTLE_MODE="charter"` in `.hustle/config` (and confirm `HUSTLE_PLAN_MODEL`
     / `HUSTLE_MODEL` are the models the user wants for Planner / Executor).
   - If `.hustle/` doesn't exist yet, run `/loop-setup` first.
5. **Review pass:** show the draft, grill once more over anything vague (especially
   constraints), then confirm how to start: Linux `systemctl --user start
   hustle-<slug>.service`, macOS `.hustle/bin/hustle-session.sh &`, or `/loop
   /advance-goal`. The first run will be a **Planner** run that seeds the backlog.

## Interview branches (in order)

1. **Mission / product vision** — What is the product, who uses it, what outcome
   does the loop drive? Grill until the Planner could invent sensible features
   from it alone. (Recommend: one tight paragraph, concrete, not aspirational mush.)
2. **Hard constraints** — What must the loop **never** touch or do? Prod, public
   APIs/contracts, data, spend, out-of-scope repos, anything irreversible. Push for
   specifics. (This is the guardrail — grill hardest here.)
3. **Quality bar** — What must be true before an item is Done (tests/boot/lint/
   acceptance)? (Recommend the project's existing verification bar.)
4. **Plane connection & mapping** — Confirm base URL + workspace slug; walk the
   real project list you fetched; agree the `project_id → repo` mapping and which
   project is **Common** (epics/overview only).
5. **Termination** — A feature-freeze **date** or **infinite**? (Recommend
   `infinite` if open-ended; note they can drop a date in later for a graceful
   wind-down.) Explain: deadline = soft freeze + drain + closing report.
6. **Feature approval** — `review` (self-invented epics wait in Backlog for a nod)
   or `auto` (build them straight away)? (Recommend `review` as default; `auto`
   for an unattended holiday run — the whole point being it can run without you.)
7. **Model policy** — Planner vs Executor default model; confirm the per-issue
   `model:opus`/`model:sonnet` routing is wanted. (Recommend Opus planner / Sonnet
   executor.)
8. **Sizing & resume guard** — Confirm the sizing rule and `N` (recommend 2).
9. **Git workflow** — `direct-main` (solo bot, no prod) or `branch-mr` (prod/CI/
   multi-dev)? Derive the default from what you found in step 1.
10. **Budget** — Keep defaults (session 85% / weekly 90%) unless the subscription
    is shared with other work.

## Format contract (tooling parses this)

Keep the `CHARTER.template.md` section headers and these lines intact — the Planner
and Executor read them mechanically: `Termination:`, `Feature approval:`,
`Git workflow:`, the Plane `Base URL:` / `Workspace slug:` lines, the project
mapping table, `Resume guard N:`, and a `## Status` block whose `STATUS:` line the
launcher checks (`READY`, later `COMPLETE`). No `_TODO:` markers may remain.
