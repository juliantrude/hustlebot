# CHARTER — _TODO: product name_

<!--
  Single source of truth for hustlebot CHARTER MODE. Human-owned and stable: it
  outlives individual plans. The Planner (/generate-plan) invents work against it;
  the Executor (/advance-goal) builds that work. The backlog itself lives in Plane,
  NOT in this file. No secrets here — the Plane API key lives in .hustle/secrets.
  Every `_TODO:` must be filled before the loop will start.
-->

## Mission / Product vision

<!-- One or two paragraphs: the product, its users, the outcome the loop drives
     toward. This is what the Planner-as-PO invents features against. -->
_TODO:_

## Hard constraints (guardrails — read every run)

<!-- The "never do X" list — the real guardrail for an unattended agent. Prod
     systems, public APIs/contracts, data, out-of-scope repos, spend, anything
     irreversible. Be concrete; ambiguity becomes a 3am guess. -->
- _TODO:_

## Quality bar ("good work" — the Done gate)

<!-- What an item must satisfy before the Executor may mark it Done: tests pass,
     boots, lint, acceptance criteria met. "Good finished work > work stopped at
     time X." -->
- _TODO:_

## Plane connection

- Base URL: _TODO: http://plane.fritz.box_
- Workspace slug: _TODO:_
- Auth: API key in `.hustle/secrets` as `PLANE_API_KEY` (not stored here).

## Project mapping (one executable item ⇒ one repo)

<!-- Each Plane project id → the one repo the Executor may touch for its items.
     Mark the Common project (epics/overview only, never an execution source). -->

| Plane project | project_id | Repo path |
|---|---|---|
| _TODO: NAME_ | _TODO: uuid_ | _TODO: /abs/path/to/repo_ |

- Common project (epics/overview only): _TODO: NAME (project_id)_

## Termination

<!-- An ISO datetime = a FEATURE-FREEZE date (soft: Planner stops inventing, the
     Executor drains remaining work, then a closing report + STATUS: COMPLETE).
     Or "infinite" = runs as PO mode until manual abort. To gracefully end an
     infinite run later, just write a date here. -->
Termination: _TODO: infinite | 2026-08-15T18:00_

## Feature approval

<!-- auto  = self-invented feature epics flow straight to build (holiday setting).
     review = self-invented epics wait in Backlog until a human moves them to Todo
              (non-blocking; human-groomed work still runs immediately). -->
Feature approval: review

## Model policy

- Planner model: `HUSTLE_PLAN_MODEL` in `.hustle/config` (e.g. opus).
- Executor default: `HUSTLE_MODEL` in `.hustle/config` (e.g. sonnet).
- Per-issue: the Planner labels each item `model:opus` or `model:sonnet`;
  unlabelled falls back to the Executor default.

## Sizing rule

- Each item completable AND verifiable in one fresh session window; one focused
  change, one service; a title needing "and" must be split.
- Resume guard N: 2   <!-- after N unsuccessful resumes an item is labelled too-big
                            and handed back to the Planner to split. -->

## Git workflow

<!-- direct-main = commit straight to main, one commit per item referencing the
     issue id, push origin main (solo bot, no prod). branch-mr = branch off dev +
     MR into dev (multi-dev / prod / CI-gated). -->
Git workflow: _TODO: direct-main | branch-mr_

## Budget

- session pause threshold: 85%
- weekly pause threshold: 90%

## Status

STATUS: READY

## Log

<!-- Append-only, dated. One line per notable planning/closeout event. -->
- _TODO: date_ — CHARTER.md created via /grill-charter
