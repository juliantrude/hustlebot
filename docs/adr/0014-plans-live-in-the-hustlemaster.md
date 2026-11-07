# ADR 0014 — Plans live in the hustlemaster, reached over MCP

Status: Proposed
Date: 2026-11-07

## Context

ADR 0009 first put the plan into the project repo. Over a project's life many hustles
run; plan files would pile up, every restructuring would be a commit on `main`, and
parallel worktrees editing one file would conflict on almost every rebase. Planning
happens in the repo, by the project's Claude Code; execution belongs to the hustlemaster.

## Decision

- `/hustle` plans in the project and **submits** the plan to the hustlemaster. From then
  on the hustlemaster owns it: every change is stored as a new version in its database,
  whoever made it (the user, an overseer, a worker's proposal). An export as a file
  exists on demand. The repo receives only the increments' commits.
- The hustlemaster serves an **MCP server** over HTTP on localhost, in the same process
  as the dashboard. Rights by caller:

  | Caller | May |
  |---|---|
  | worker hustlebot | read its hustle, propose plan changes |
  | overseer hustlebot | read and change its hustle's plan, within the bounds of ADR 0015 |
  | the user's interactive sessions | read all hustles, submit new ones, change plans |
  | dashboard only | approvals, weights, total points, thresholds, `allow_fable`, auto-approve |

  No agent can grant its own approval or raise its own budget, not even from the user's
  session.
- Every hustlebot gets its own `--mcp-config` with a per-session bearer token (in a
  variable with our own name; Claude Code blanks `ANTHROPIC_*` names in headers), so the
  hustlemaster knows the hustle and the role. Interactive sessions use a fixed entry
  shipped with the plugin.
- **Packaging:** the installed `hustlebot` plugin contains only the user-facing skill
  `/hustle` and the MCP entry. The internal skills (worker, overseer) live with the
  hustlemaster and are passed to each hustlebot with `--plugin-dir`; they never appear
  in interactive sessions. Their names are optimised for agents, not users.
- `allow_fable` is a hustlemaster setting. `/hustle` reads it over MCP; the hustlemaster
  refuses to start a Fable increment while it is off.

## Consequences

- Supersedes the "graph lives in the project repo" bullet of ADR 0009.
- Editing a running hustle: the user edits prompts and models in the graph view, or asks
  Claude in the project for structural changes, which it makes over MCP. A running
  increment finishes; a changed version applies to the next attempt, a deleted one has
  its result discarded.

### As built (2026-11-07, `master/user-plugin/`, `submit_hustle`)

- **`/hustle` beside the old loop.** The user-facing plugin is `master/user-plugin/`
  (named `hustlebot`, skill `skills/hustle/SKILL.md`), not the repository root. The root
  plugin still carries the old loop (`/hustle` → `GOAL.md` chain, `/advance-goal`), which
  drives work until the migration; replacing its `skills/hustle/` now would strand the
  running chains. At the migration the marketplace entry points at `master/user-plugin/`
  (or its contents move to the root) and the old skills go. Until then both are named
  `hustlebot`; a session sees the v2 skill only when given `--plugin-dir master/user-plugin`.
- **`submit_hustle` also takes `push` and `verify`.** ADR 0011 has `/hustle` settle both
  while planning, and they are project settings, not dashboard-only ones, so the
  interactive role sets them with the submission (`Store.submit(push=, verify=)`, applied
  only if the plan passes; left out, the project keeps what it has).
- The skill has three entries: a new goal (grill, cut, show, submit), converting a
  sequential `GOAL.md` (checked increments are left out, the old loop's per-run, git and
  budget rules are dropped from the brief, the check and push are settled anyway, the file
  stays untouched), and reading or changing an existing hustle via `change_plan`.
- Verified with `master/prototypes/hustle_haiku.py`: real Haiku sessions, read-only tools
  plus the interactive MCP tools, convert a fixture `GOAL.md` and plan a fixture goal; both
  submitted valid plans with parallel independent increments and an Opus acceptance
  overseer. The first conversion run dropped `verify` (it skipped the interview, where the
  check is settled); §5 and §6 of the skill now say so explicitly.
