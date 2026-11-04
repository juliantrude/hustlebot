# Ideas for the next version — multi-chain hustlebot

Status: Brainstorm, not decided. Input for a grilling session once the repo
restructure is done. Captured 2026-10-28.

Today one project has one chain, and each chain arms its own one-shot timer
(ADR 0003). These ideas turn hustlebot into a small operating system for
chains: many chains, one scheduler, one dashboard.

## 1. Parallel chains

Support several hustlebot chains (across projects, maybe several per project)
at the same time. Currently not foreseen anywhere: slug-per-project services,
one `.hustle/` per project, one monitor per project, each on its own port.

## 2. Dashboard becomes a web app

The per-project monitor grows into one central web app that shows all chains,
lets you see that they run side by side, and manage them (start, pause, stop,
reprioritise).

## 3. The web app as scheduler ("multitasking router")

Borrow the principles of an OS process scheduler. Chains are processes; the
shared resource is the subscription budget (session and week windows), not CPU
time. Chains take turns: the scheduler decides which chain gets the next run.
Natural time slice: one increment (one headless run).

## 4. Priorities

Priority levels 0, 1, 2, 3, ...

- **Prio 0** is exclusive: while any prio-0 chain is runnable, only prio-0
  chains run.
- **Prio n gets twice the share of prio n+1** (prio 1 : prio 2 : prio 3 =
  4 : 2 : 1). Every non-zero chain keeps making progress; nothing starves.

This is weighted fair-share scheduling (cf. stride / lottery scheduling).

## 5. True multitasking inside one chain

Opt-in per chain, aimed at finishing a project as fast as possible: break the
chain's work down (e.g. with Matt Pocock's "Wayfinder", to be checked), find
the tasks that can run in parallel, and hand those to subagents.

## Open questions for the grilling

- Who owns the timer? A central scheduler daemon replaces per-chain
  self-arming — a real change to ADR 0003.
- "Parallel" vs. "taking turns": do two chains ever run headless sessions at
  the same instant, or only interleave? Simultaneous runs burn the session
  window twice as fast.
- How do the budget guard and parking at limits work when several chains
  share one subscription?
- Where does the central app live: per machine, as its own service? What
  happens to the existing per-project monitors?
- Subagent parallelism: how to avoid git conflicts (worktrees per task?), and
  how does that fit ADR 0007 (direct to main)?
- Priority of plan-mode vs. charter-mode chains — same scheme?

## Grilling round 1 — answers (2026-11-04)

- **Timer owner:** a central hustlebot **master** instance owns all timers and
  the Claude Code resources. Agents no longer check usage between increments;
  the master polls session usage regularly. When an increment finishes and the
  next one becomes ready, the master is notified and sends the start signal —
  according to resources and priority. Supersedes self-arming (ADR 0003).
- **Chains become DAGs, not sequences.** Only dependent increments are chained;
  independent ones run in parallel. Example, increments A–J:
  `A→B→C→D`, `E→F`, `G`, `H`, `(D ∧ F)→I→J`. A, E, G, H start at once;
  I waits for D and F.
- **Per-increment model choice** and explicit dependencies → chain planning
  gets more detailed.
- **Concurrency budget in session points**, set by the user system-wide (all
  projects), sized to their subscription: Haiku 1, Sonnet 2, Opus 4, Fable 8.
  E.g. 20 points = 2 Fable + 1 Opus, or 5 Opus, or 20 Haiku. Faster burn is
  fine: token-per-result ratio stays the same, work just finishes sooner.
  Known risk: too many parallel agents exhaust the 5h window before any
  finishes.
- **Budget guard** lives in the master (polling); increments never start
  themselves.
- **Master runs locally** (Docker vs. systemd still open). Per-project
  monitors are dropped.
- **Charter mode** out of scope for now; integrate at the end.

## Grilling round 2 — answers (2026-11-04)

- **Terminology:** an increment runs as its own headless Claude Code
  **session** started by the master (not a subagent inside a session).
- **Limits:** stop starting new sessions above a threshold; a session that
  still hits the limit is parked and resumed in the same worktree next window.
  Thresholds to be tuned by experience. Twin goal: avoid hitting limits *and*
  avoid leaving budget unused — especially the weekly limit.
- **Priorities as CSS-grid `fr` weights:** a chain's weight is its share of the
  user's total session points among **active** chains. Weight 1 is the
  smallest, no upper bound; weight 2 gets twice the share of weight 1.
  Inactive chains are not in the pool. (Replaces the earlier 0/1/2/3 scheme.)
- **Git:** each session works in its own worktree on a short-lived branch;
  when verified, rebase onto main, re-verify, fast-forward. Rebase conflict =
  a resolution step of the same increment. Keeps ADR 0007's spirit (main is
  the only long-lived branch, one commit per increment).
- **Graph:** goal grilling produces the DAG incl. model per increment; the
  user confirms before start. The chain may restructure its own graph.

## Grilling round 3 — answers (2026-11-04)

- **Shares are guarantees, not caps (work-conserving):** points a chain cannot
  use flow to the others by the same weights. Only chains that are active
  *and* have a ready or running increment count in the pool.
- **Shares are averages over time (deficit round robin):** an increment larger
  than the chain's share may start once enough points are free; the chain then
  runs a deficit and waits until the others catch up.
- **No exclusive level.** Exclusivity = have only one chain active.
- **Deactivating a chain:** running sessions finish cleanly; no new ones start.
- **Graph self-restructuring bounds:** splitting and reordering allowed; new
  increments must serve the confirmed goal; switching to a more expensive
  model or significant graph growth needs user confirmation in the dashboard.

## Grilling round 4 — answers (2026-11-04)

- **Usage source:** combine (a) reading `/usage` from a real interactive
  session via a pseudo-terminal, (c) the master's own accounting from each
  session's JSON result, learning roughly what a point-hour costs in percent;
  (b) limit errors / reset timestamps of rejected runs as safety net.
  Prototype first: what is actually readable today?
- **Master is a normal program, not an agent.** New build in Python; existing
  shell scripts are not migrated, only proven parts (reset-timestamp parsing,
  exponential backoff) are carried over.
- **Sessions are child processes of the master:** `subprocess` runs
  `claude -p … --model … --output-format json` with `cwd` set to the
  increment's worktree. Process exit = increment finished; no separate
  notification channel. On master restart, orphaned worktrees are resumed.
- **Worktree caveat:** checked-in `CLAUDE.md` and `.claude/settings.json`
  (incl. hooks) are present in a worktree; gitignored files
  (`.claude/settings.local.json`, `.env`, …) are not. The master creates the
  worktree itself (`git worktree add`) and copies/links those files in.
  User-level `~/.claude` applies anyway. Auto-memory path for worktrees: verify
  in the prototype.
- **State split:** graph lives in the project repo (versioned); runtime
  control (running sessions, points, deficits) lives with the master
  (local SQLite).

## Grilling round 5 — answers (2026-11-04)

- **Master as systemd user service**, not Docker (needs the user's Claude
  login, git credentials, project dirs and `~/.claude` as they are).
- **Dashboard v1** (mock in `master/`, `python3 -m hustlebot_master.server --mock`):
  - Chains as wide cards, one below the other, sorted active first, then by
    weight.
  - Card, left to right: active switch, weight (`fr`) with share in points,
    status, project, title, progress, running sessions as model avatars
    (placeholders until real avatars are chosen), graph preview.
  - Preview = the same graph as dots (graph-engine `?view=mini`): dependencies
    and state only, no titles or models.
  - Click on a card: the graph full screen in graph-engine; one node type per
    model; click on a node shows its instructions in the right-hand panel.
    Done green, running blue, open grey, hard-blocked red.
  - Header: session and week usage, total session points (adjustable), points
    in use. Approvals (model upgrades, graph growth) with approve / reject.
- graph-engine 1.1.0 (local, not pushed): rootless dependency graphs, look
  `active` (blue), `?view=mini`.

## Prototype findings (2026-11-04, `master/prototypes/`)

- **`/usage` over a pseudo-terminal works** (`usage_pty.py`, pexpect + pyte):
  an invisible interactive session shows "Current session … 15% used ·
  Resets 9:10pm" and "Current week (all models) … 29% used · Resets Nov 7,
  10pm", parsed into JSON. Costs nothing (no prompt is sent; the session
  reports $0). Fragile by nature: it parses a screen, not an API.
- **Headless sessions in a worktree load the project fully**
  (`worktree_session.sh`): `CLAUDE.md`, checked-in `.claude/settings.json`
  hooks — and also the main checkout's gitignored `settings.local.json` (env
  and hooks), without copying it. Only non-Claude gitignored files (`.env`,
  …) still need to be copied in.
- **Auto memory is shared:** the memory dir is created under the main
  checkout's project path; transcripts are stored per worktree path.
- **Own accounting is feasible:** the JSON result has tokens per model
  (`modelUsage`, incl. cache read/creation) and `total_cost_usd`.
- Runner detail: start `claude -p` with stdin closed, else it waits 3 s for
  piped input.
