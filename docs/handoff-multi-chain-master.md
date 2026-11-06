# Handoff — multi-chain master

Written 2026-11-06 at the end of the session that designed it. Read this first, then the
ADRs. Next step: a grilling session on the open questions below, then a `GOAL.md` and the
implementation (recommended: hustlebot itself, Opus for the design-heavy increments).

## Where things are

| What | Where |
|---|---|
| Decisions | `docs/adr/0008` – `0013` (Proposed) |
| Grilling log, rounds 1–5, prototype findings | `docs/ideas/next-version.md` |
| Frontend prototype (accepted by the user) | `master/` — `python3 -m hustlebot_master.server --mock` from `master/`, then http://127.0.0.1:8765/ and `/cinema` |
| Usage-over-pty prototype | `master/prototypes/usage_pty.py` (`uv run --with pexpect --with pyte python usage_pty.py <project>`) |
| Worktree-session prototype | `master/prototypes/worktree_session.sh <scratch-dir>` |
| Graph rendering | graph-engine 1.1.0 (`../graph-engine`, tagged `v1.1.0`): rootless graphs, look `active`, `?view=mini`, `testing.render(view="mini")` |
| Design files | self-hosted Penpot (`~/Work/penpot`, README there; start with `docker compose -p penpot up -d`); older copy in Figma. The prototype wins where they differ. |

## What the prototype already defines

- `master/hustlebot_master/model.py` — `Chain`, `Increment`, `POINTS` (Haiku 1, Sonnet 2,
  Opus 4, Fable 8), `shares()` (fr weights over active chains with ready/running work).
- `master/hustlebot_master/sim.py` — a stand-in scheduler: neediest chain relative to its
  share first, borrowing free points; per-chain history of 60 ticks; usage burning with the
  points in use. It is **not** deficit round robin yet and has no thresholds — the real
  scheduler (ADR 0010, 0008) replaces it.
- `master/hustlebot_master/server.py` — `/api/state` is the contract the views read: per
  chain `share`, `points_in_use`, `sessions` (model, points, started, ends), `history`,
  `increments` (id, after, state, model), `graph_version`; global `total_points`,
  `points_in_use`, `usage` (session/week percent + resets), `tick`, `tick_seconds`,
  `approvals`. POST routes: `/api/settings`, `/api/chains/<id>` (active, weight),
  `/api/approvals/<id>`.
- `master/hustlebot_master/graph.py` — chain → graph-engine document (node type per model,
  states done/running/open/blocked, instructions in the panel).
- `master/dashboard/` — control view (`index.html`, `app.js`, `load.js`, `flow.js`) and
  cinema view (`cinema.html`, `cinema.js`).

## Open questions for the grilling

1. **Graph file format in the repo.** Does it replace `GOAL.md` or extend it (e.g. a
   `## Plan` section with ids and `after:` lists, or a separate `GRAPH.json`/YAML)? Who may
   edit it while the chain runs — the user, the chain, both — and how are edits merged with
   the master's runtime state?
2. **Review in the graph view.** View and confirm only, or also edit nodes, dependencies
   and models there? What does "confirm" write back?
3. **Failure handling.** An increment fails verification or its rebase cannot be
   resolved: how many retries, when does the node turn red (blocked), what does the chain
   do meanwhile? How does this relate to ADR 0003's resume guard (N=2, `too-big`)?
4. **Thresholds and pacing.** At which 5h/week usage does the master stop starting? How
   does it use up the weekly budget towards the reset without hitting the limit? Fixed
   numbers first, learned later?
5. **Planning skill.** How `/grill-goal` (and later `/generate-plan`) finds dependencies,
   splits work for parallelism (the user mentioned Matt Pocock's "Wayfinder", to be
   checked), sizes increments to fit one session, and picks a model per increment.
6. **What `/advance-goal` receives.** The master hands one increment to a session: as the
   prompt, as a file in the worktree, as arguments? What does the session report back
   besides its exit code (the JSON result, a status line, a commit)?
7. **Approvals.** Exact triggers for "more expensive model" and "significant growth";
   what happens to the chain while an approval is pending.
8. **Migration.** Existing chains (sequential `GOAL.md`, per-project timers and monitors):
   convert, finish on the old loop, or both side by side for a while?
9. **Charter mode.** Out of scope so far; decide whether the Planner becomes a special
   increment kind under the master or stays a separate phase.
10. **Scale of the views.** How many chains run at once in practice; the cinema view
    spreads lanes evenly and gets tight beyond about six.

## Suggested milestones (after the grilling)

1. Graph file format + loader + validation, and `/advance-goal` executing one given
   increment.
2. Session runner: worktree, child process, JSON result, rebase + fast-forward, resume of
   orphaned worktrees.
3. Usage reader (pty + own accounting + safety net) and thresholds.
4. Scheduler: points, fr weights, work-conserving deficit round robin; SQLite state.
5. Planning skill: graph with dependencies and model per increment; review flow.
6. Master service: systemd user unit, API behind the existing views; remove mock and
   simulation; remove per-project scheduler and monitors.
7. Approvals and self-restructuring of graphs.
8. Migration of existing chains; pin graph-engine `v1.1.0`.

## Lessons from the prototypes

- `claude -p` waits 3 s for stdin unless it is closed.
- A headless session in a worktree loads the project's settings, hooks and local settings
  of the main checkout; copy only non-Claude gitignored files.
- Navigating an existing iframe adds browser-history entries: create a fresh iframe, or use
  `contentWindow.location.replace()`.
- Moving an iframe in the DOM reloads it: order cards with CSS `order`, never re-append.
- The views are tested with Playwright driving the system Chromium
  (`uv run --with playwright`, `executable_path="/usr/bin/chromium"`); no browser download needed.
