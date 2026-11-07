# GOAL

<!--
  Single source of truth for the /advance-goal loop (driven headless via the
  one-shot systemd chain, or interactively via `/loop /advance-goal`).
  The loop reads and rewrites this file every iteration.
  The previous goal (dashboard multi-page monitor) is complete; see git history.
-->

## North star

Build hustlebot v2: the **hustlemaster**, one background service per machine that runs
several hustles in parallel. A hustle is a plan of increments with dependencies and a
model per increment; the hustlemaster stores the plans, reads the subscription usage,
shares session points by weight, starts one headless hustlebot per increment in its own
worktree, merges verified results into `main`, and shows everything in the control and
cinema views the prototype in `master/` already defines. The design is settled: ADR
0008–0015 in `docs/adr/` and the glossary in `CONTEXT.md` are the specification. Read
them before the first increment and whenever a decision seems open — it almost never is.

## Definition of Done

- [ ] The hustlemaster stores hustles (brief + plan) in SQLite, versions every plan change, and rejects invalid plans (cycles, unknown dependencies, missing acceptance increment, Fable while `allow_fable` is off, an increment costing more than the total points) — covered by tests
- [ ] The MCP server enforces the rights table of ADR 0014 per caller (worker, overseer, interactive) and refuses every dashboard-only action — covered by tests per role
- [ ] The runner starts a hustlebot in a fresh worktree with copied gitignored files, the assembled prompt, `--plugin-dir` and a per-session `--mcp-config`; parses the report block; rebases, re-verifies and fast-forwards `main`; resumes orphaned worktrees after a restart — tests against a fake `claude`, plus one real smoke run with Haiku in a scratch repo
- [ ] The usage reader combines the pty `/usage` reading, own accounting and the limit safety net; starts stop above the 5h threshold and above the weekly target line — tests with recorded screens and fixture results
- [ ] The scheduler is work-conserving with fr weights and the memoryless "furthest below share first" rule; overseers start on the events of ADR 0015; approvals and auto-approve work as specified — tests with simulated hustles
- [ ] The internal worker and overseer skills exist and run headless; `/hustle` plans a hustle (dependencies, sizing, model per increment, acceptance increment) and submits it over MCP, and converts a sequential `GOAL.md` into a plan — verified against fixtures
- [ ] The dashboard runs on the real API: ring sliders, approvals, settings (total points, `allow_fable`, auto-approve), plan editing in the graph view, token login, configurable bind address; the cinema view shows only active hustles — verified with Playwright against the running hustlemaster, mock and `sim.py` removed
- [ ] A systemd user unit and an install skill (successor of `loop-setup`) install the hustlemaster and the interactive MCP entry; verified against a scratch `HOME`
- [ ] README and ADRs describe what was built; ADR statuses moved from Proposed to Accepted where the code matches

## Working Agreements (read every iteration — these override convenience)

**Priority order per iteration:**
1. If the previous iteration left failing tests or a hustlemaster that does not start, fix that first.
2. Then take the next unchecked increment from `## Plan`, in order.

**Git workflow:**
- Work on the branch `feat/hustlemaster`, branched from `main`. Create it if missing; never commit to `main`.
- **Never push.** No `git push`, no PR, no remote operation. This is a public repository and the loop runs unattended.
- One commit per completed increment, message says what and why. Commit only after verification.
- `../graph-engine` may be extended where the graph view needs it (ADR 0013): work there on a branch `feat/editing`, never push, bump its version, and pin the new version here.

**The old loop drives this goal — do not break it:**
- Never modify `scripts/hustle-session.sh`, `scripts/hustle-scheduler.sh`, `skills/advance-goal/`, `.hustle/` or this file's format. Removing the old loop is **not** part of this goal; it happens by hand after the migration.
- The new code lives in `master/`. Rename the package `hustlebot_master` → `hustlemaster` in the first increment.
- A demo of the prototype runs as the systemd unit `hustlebot-demo` on port 8765. Leave it alone; use other ports for tests and never `pkill -f` broadly.

**Verification bar:**
- A structural check is not a verification. Tests must exercise behaviour; anything visible is rendered and looked at.
- Views are tested with Playwright driving the system Chromium (`uv run --with playwright`, `executable_path="/usr/bin/chromium"`); no browser download.
- Real `claude` calls: only Haiku, only tiny prompts, only in scratch repos under `/tmp`, at most a few per increment. Never Fable. Everything else runs against a fake `claude` executable that replays recorded JSON results.
- Never touch the user's real `~/.claude` configuration or installed plugins; installs are tested against a scratch `HOME`.
- If an increment cannot be verified, say so in `## Log` and leave the box unchecked.

**Design rules:**
- ADR 0008–0015 are binding. If code forces a deviation, write it into the ADR in the same commit and say why in `## Log`.
- Use the glossary terms of `CONTEXT.md` in code and docs: hustle, plan, increment, hustlebot, worker, overseer, hustlemaster. Not "chain" or "graph" for the unit of work.
- Dependencies via `uv` and a `pyproject.toml` in `master/`. Keep them few: pexpect, pyte, the MCP Python SDK, and test tools.
- Comments explain why, never what. Docs and UI in English.

**References:**
- `docs/handoff-multi-chain-master.md` — where things are, lessons from the prototypes.
- `docs/ideas/next-version.md` — the full grilling log.
- `master/hustlebot_master/server.py` — `/api/state` is the contract the views read.
- `master/prototypes/usage_pty.py`, `master/prototypes/worktree_session.sh` — proven building blocks.

## Plan

### Foundation
- [ ] Rename the package to `hustlemaster`, add `master/pyproject.toml` with `uv`, a test runner and a fake `claude` executable that replays recorded JSON results. Rename "chain" to "hustle" in model, API and views without changing behaviour; verify the prototype views still render (Playwright, mock mode, a port other than 8765).
- [ ] Hustle store: SQLite schema for hustles (brief, project, active, weight, push switch), plans with versions, increments (id, title, prompt, dependencies, model, mode, state, attempts, peak context), settings (total points, thresholds, `allow_fable`, auto-approve). Validation per the DoD. Tests.
- [ ] MCP server in the hustlemaster process on localhost, with per-session bearer tokens and the rights table of ADR 0014: read hustle, propose change, change plan, submit hustle, read settings. Tests per role, including refusals.

### Runner
- [ ] Verify the two undocumented behaviours first (ADR 0011): does a `claude -p` session ask for approval of `--mcp-config` servers, and how does a `--plugin-dir` plugin interact with installed plugins. One real Haiku run each in a scratch repo; record the findings in ADR 0011.
- [ ] Worktree lifecycle: create on a short-lived branch, copy gitignored files with the default excludes and the per-project override, remove after merge. Tests in scratch repos.
- [ ] Start a hustlebot: assemble prompt (brief + increment), `--model`, `--plugin-dir`, per-session `--mcp-config` + `--strict-mcp-config`, stdin closed, JSON output; parse the report block, tokens per model and peak context. Tests with the fake `claude`.
- [ ] Merge: rebase onto `main`, re-verify, fast-forward, optional push by the hustlemaster only; conflict handling counts as an attempt. Orphaned worktrees are resumed after a restart. Tests in scratch repos.
- [ ] One end-to-end smoke run with real Haiku: a two-increment hustle in a scratch repo, both merged into its `main`.

### Usage and scheduling
- [ ] Usage reader: port `usage_pty.py`, own accounting from results, safety net with reset parsing and backoff carried over from `hustle-session.sh`. Tests with recorded screens.
- [ ] Thresholds: 5h stop and the weekly target line; fit estimate before a start; parking at the limit and resuming in the next window. Tests.
- [ ] Scheduler: points, fr weights over active hustles with ready or running work, work-conserving, memoryless "furthest below share first"; replaces `sim.py`. Tests with simulated hustles.
- [ ] Failures and overseer events: two resumes per increment, then an event overseer (failed twice, too-big, unresolved conflict, proposal), blocked state. Tests.
- [ ] Approvals and auto-approve (ADR 0015): upgrade beyond share, growth > 25 % against the confirmed baseline, splits exempt, pending increments wait, provisional new increments; first activation confirms. Tests.

### Skills
- [ ] Internal worker skill: execute the given increment, verify, exactly one commit, report block. Packaged for `--plugin-dir`. Verified with one real Haiku run.
- [ ] Internal overseer skill: event mode (split, upgrade, rewrite, block) and acceptance mode (check the Definition of Done against `main`, add increments if needed), changes only over MCP. Verified against fixture situations.
- [ ] `/hustle`: grill the goal, decompose (one task per increment, ≤ 600k context, disjoint files for parallel work, dependencies only where needed), choose models (Fable only if allowed), add the acceptance increment, ask about pushing, submit over MCP. Second entry: convert a sequential `GOAL.md` into a plan. Verified against fixtures.

### Dashboard
- [ ] Serve the views from the real state: `/api/state` from the store and scheduler; remove `mock.py` and `sim.py`. Playwright check of both views.
- [ ] Curved threshold sliders on the core's rings in the control view; the cinema view shows the marks only and only active hustles. Render and look.
- [ ] Settings and approvals in the control view: total points, `allow_fable`, auto-approve mode, approve / reject / revoke. Playwright.
- [ ] Plan editing in the graph view: edit prompt and model, delete, pause an increment; extend graph-engine where needed (see Working Agreements). Playwright.
- [ ] Token login and configurable bind address; the MCP endpoint stays on localhost. Tests.

### Service and closeout
- [ ] systemd user unit for the hustlemaster and an install skill (successor of `loop-setup`) that also installs the interactive MCP entry and `/hustle`; verified against a scratch `HOME`.
- [ ] Final sweep: README for v2, ADRs updated to as-built and moved to Accepted where they match, DoD checked against the running system.

## Status

STATUS: READY
**Next action:** create `feat/hustlemaster`, then the first Foundation increment (package rename, `pyproject.toml`, fake `claude`).
**Blockers:** none

## Budget

- session pause threshold: 85%
- weekly pause threshold: 90%

## Log

- 2026-11-07 — GOAL.md created after the grilling of the multi-chain master (rounds 6–12 in `docs/ideas/next-version.md`, decisions in ADR 0008–0015). Replaces the completed dashboard goal.
