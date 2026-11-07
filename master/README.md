# hustlebot v2 — the hustlemaster

One background service per machine that runs several **hustles** in parallel.
A hustle is a goal planned as a dependency graph of **increments**, each with
its own model. The **hustlemaster** stores the plans, reads the subscription
usage, shares session points between the hustles by weight, starts one
headless Claude Code session — a **hustlebot** — per increment in its own git
worktree, and fast-forwards `main` with every verified result. A web dashboard
shows and steers all of it.

This replaces the old loop (one `GOAL.md` chain per project, one increment at a
time, see the [root README](../README.md)). The design is in
[ADR 0008–0015](../docs/adr/) and the words are defined in
[`CONTEXT.md`](../CONTEXT.md).

```
 you ── /hustle (Claude Code in the project) ──MCP──┐
                                                    ▼
                     ┌──────────── hustlemaster (systemd user unit) ────────────┐
                     │ store (SQLite)  scheduler  usage reader  MCP 127.0.0.1    │
                     │      │              │                                    │
                     │      ▼              ▼                                    │
                     │  worktree ── claude -p (worker / overseer) ── report      │
                     │      │                                         │         │
                     │      └── rebase · check · fast-forward main ◄──┘         │
                     │ web app: control view · graph view · cinema view          │
                     └───────────────────────────────────────────────────────────┘
```

## Install

Linux with systemd, [`uv`](https://docs.astral.sh/uv/) and Claude Code logged
in to a subscription. The hustlemaster runs from a checkout, with
[graph-engine](https://github.com/juliantrude/graph-engine) (1.2.0 or later)
checked out beside it:

```sh
git clone https://github.com/juliantrude/hustlebot
git clone https://github.com/juliantrude/graph-engine
cd hustlebot
uv run --project master --no-dev python -m hustlemaster.install
loginctl enable-linger "$USER"   # keep it running after logout
```

Or, from a Claude Code session in the checkout:
`claude --plugin-dir master/user-plugin`, then `/hustlebot:install`.

The installer is idempotent (run it again after a pull):

| Creates | Where |
|---|---|
| home: store, runs, worktrees, tokens | `~/.local/share/hustlemaster/` (0700) |
| systemd user unit `hustlemaster.service` | `~/.config/systemd/user/` |
| MCP entry `hustlemaster` for your interactive sessions | user scope, `claude mcp list` |
| `/hustle` and `/hustlebot:install` (plugin `hustlebot@hustlemaster`) | read in place from `master/user-plugin/` |

Flags: `--bind ADDR` and `--port N` for the dashboard (default
`127.0.0.1:8770`), `--mcp-port N` (default 8771, always on 127.0.0.1),
`--claude PATH`, `--no-start`, `--no-systemd`, `--no-plugin`.

If the old loop's `hustlebot` plugin is still installed, the installer leaves
it alone and skips `/hustle` (two plugins of one name would be ambiguous); see
[Migrating](#migrating-from-the-old-loop).

**A hustle host and planning clients** ([ADR 0016](../docs/adr/0016-hustle-host-and-remote-planning.md)).
On a dedicated machine, install as above with `--bind` for the dashboard and
`--mcp-bind 0.0.0.0 --mcp-name <host>` for a second MCP endpoint (port 8772,
interactive token only), both behind a TLS reverse proxy. Projects live by
name under `--projects` (default `~/hustles`). On each machine you plan from,
install only `/hustle` and the MCP entry, with the host's `interactive-token`:

```sh
uv run --project master --no-dev python -m hustlemaster.install \
  --client https://<host>/mcp --token <interactive-token>
```

Logs: `journalctl --user -u hustlemaster -f`. Restarting the unit does not
stop running sessions: the next process adopts them.

## Use

**Plan a hustle.** In the project, run `/hustle`. It grills you about the goal,
writes a brief (north star, Definition of Done, agreements), cuts the work into
increments — one task each, disjoint files where they may run in parallel,
dependencies only where one needs another — picks a model per increment
(Haiku for mechanical work, Sonnet by default, Opus for design and hard
debugging, Fable only if allowed), adds an acceptance increment that checks the
Definition of Done against `main`, settles the project's check command and
whether the hustlemaster may push, and submits the plan over MCP.

**Activate it** in the dashboard. A submitted hustle arrives inactive so you
can look at the plan first; the first activation confirms it.

**The dashboard** is at `http://127.0.0.1:8770` (or the address you bound). It
asks for the token in `~/.local/share/hustlemaster/dashboard-token`.

- **Control view** (`/`): a card per hustle with the active switch, weight,
  share and points in use, progress and a live load strip. The energy core
  shows the 5-hour and weekly usage; drag the knobs on its rings to set the
  5-hour stop and the weekly target. Total points, Fable, auto-approve, and the
  approvals waiting for you are below it.
- **Graph view** (`/hustles/<id>`): the plan full screen. Click an increment for
  its prompt; change prompt or model, pause it, or delete it.
- **Cinema view** (`/cinema`): no controls, for an always-on display — the
  active hustles as beams of light flowing into their plans.

**Change a running hustle** in the graph view, or ask Claude in the project:
your interactive sessions may read every hustle and change plans over MCP.

## How it works

- **Session points** measure what a running session costs: Haiku 1, Sonnet 2,
  Opus 4, Fable 8. You set the machine's total (default 20). Each active hustle
  with work gets a share by its weight (like CSS `fr`); points it cannot use go
  to the others, and freed points go to the hustle furthest below its share.
  ([ADR 0010](../docs/adr/0010-session-points-and-weights.md))
- **The budget.** The hustlemaster reads `/usage` through an invisible terminal
  every ten minutes, reads the usage each session reports, and learns what a
  dollar of session cost moves the percentages. It starts nothing above the
  5-hour stop or above the weekly target line, nor a session that would not fit
  the window. A session cut off by a limit is parked and resumed in its
  worktree after the reset, without costing an attempt.
  ([ADR 0008](../docs/adr/0008-central-master-owns-scheduling.md),
  [0012](../docs/adr/0012-reading-the-usage.md))
- **One hustlebot per increment** runs `claude -p` in a fresh worktree (with
  your gitignored files copied, minus dependencies and build output), with the
  internal worker or overseer skill and an MCP token bound to its hustle. It
  makes exactly one commit and ends with a report. The hustlemaster rebases it
  onto `main`, runs the project's check, fast-forwards `main` and removes the
  worktree; it pushes only if the project allows it.
  ([ADR 0011](../docs/adr/0011-session-per-increment-in-a-worktree.md))
- **Overseers** are hustlebots that may change the plan. The acceptance
  increment is one. Others start on events — an increment failed twice, a
  worker reported it too big, a merge conflict stayed, a worker proposed a
  change — and split, upgrade, rewrite or block the increment.
  ([ADR 0015](../docs/adr/0015-hustlebot-modes-failures-approvals.md))
- **Approvals.** A model upgrade beyond the hustle's share, or more than 25 %
  growth in plan points since the last confirmation, waits for you in the
  control view; only the affected increments wait meanwhile. Auto-approve can
  take `limited` growth (one model step at most) or `all`; every automatic
  approval is listed and the latest can be revoked.
- **Rights.** Workers read their hustle and propose changes; overseers change
  their hustle's plan; your interactive sessions read all hustles, submit and
  change plans. Approvals, weights, points, thresholds, Fable and auto-approve
  are dashboard-only — no agent can raise its own budget.
  ([ADR 0014](../docs/adr/0014-plans-live-in-the-hustlemaster.md))
- **Fable** is off by default: headless sessions bill Fable usage credits
  without asking. While it is off no Fable increment can be planned, and those
  planned before it was switched off wait until it is on again.

## Security

- Hustlebots run with `--permission-mode bypassPermissions`: unattended agents
  that edit, run your project's commands and commit on their own, each in its
  own worktree. Point the hustlemaster only at projects where you accept that,
  and put the boundaries into the brief or the project's `CLAUDE.md`.
- The dashboard can steer hustles and grant approvals. It always asks for its
  token; bind it to `127.0.0.1` or a private interface, behind a reverse proxy
  with TLS if it leaves the machine. A new `dashboard-token` file logs every
  browser out.
- The MCP endpoint listens on 127.0.0.1 only. Each session gets its own token,
  revoked when it ends; your interactive entry carries `interactive-token`.

## Migrating from the old loop

The old loop and the hustlemaster can run side by side; nothing in the old
loop changes until you remove it.

1. Install the hustlemaster as above (`/hustle` is skipped while the old plugin
   is installed; use `claude --plugin-dir master/user-plugin` for it meanwhile).
2. In each project with a running chain, start the v2 `/hustle` and choose to
   convert the `GOAL.md`: checked increments are left out, the open ones become
   a plan with dependencies and models, the file stays untouched. It tells you
   to disarm the old chain (`.hustle/bin/hustle-scheduler.sh disarm`).
3. When no chain is left: `claude plugin uninstall hustlebot@juliantrude`,
   remove `.hustle/` from the projects, and run the installer again so it adds
   `/hustle`.

## Development

```sh
cd master
uv run pytest -q          # ~320 tests; the view tests drive /usr/bin/chromium
```

Tests run against `tests/fake_claude.py`, which replays recorded session
streams. `prototypes/` holds the probes and the real-Haiku smoke runs that
verified the undocumented behaviours (they spend a few cents each), and
`acceptance_check.py`, which boots the real service process with the fake
`claude`, submits a hustle over MCP, drives the views with Playwright and
checks the merged `main` (`uv run python prototypes/acceptance_check.py`).
