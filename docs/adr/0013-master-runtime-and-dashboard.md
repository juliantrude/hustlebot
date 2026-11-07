# ADR 0013 — hustlemaster runtime and dashboard

Status: Accepted (2026-11-07, the code matches; deviations are recorded as built)
Date: 2026-11-06

## Context

The hustlemaster (ADR 0008) needs a home, a store and a face. Today every project has its
own monitor (`scripts/hustle-monitor.py`) on its own port.

## Decision

- The hustlemaster is a **new Python program** (no migration of the shell scripts; proven
  parts such as reset-timestamp parsing and backoff are carried over). It runs as a
  **systemd user service**, not in Docker: it needs the user's Claude login, git
  credentials, project directories and `~/.claude` exactly as they are. The intended
  home is a dedicated VM; repos, the hustlemaster and the user's planning sessions (over
  SSH) all live there.
- **State:** plans, briefs, settings and runtime control (running sessions, points,
  history, approvals) live in a local SQLite database (ADR 0014).
- **Web app** with two views. It binds to a configurable address (a private interface on
  the VM, typically behind a reverse proxy) and requires a token login, because it can
  steer hustles and grant approvals. The MCP endpoint (ADR 0014) stays on localhost.
  - **Control view** (`/`): hustle cards sorted active first, then by weight; per card
    the active switch, weight in `fr` with share and points in use, status, project,
    title, progress, a live load strip, and a graph preview. The energy core shows the 5h
    and weekly usage; each ring carries a **curved threshold slider** (ADR 0008). Total
    points, `allow_fable` and the auto-approve mode are set here; approvals are accepted
    or rejected here. `/hustles/<id>` opens the plan full screen in graph-engine, with
    each increment's prompt in the side panel, and allows editing an increment's prompt
    and model, deleting it and pausing it. graph-engine is extended where that needs it.
  - **Cinema view** (`/cinema`): for an always-on OLED display; no controls, pure black,
    neon colours. Shows **only active hustles**; the threshold marks are shown, not
    their sliders. The core's beams bend into the hustle lanes, carry the last hour as a
    waveform and flow into each plan, where sparks hit the running increments. The scene
    drifts slowly against burn-in. Expected scale: up to 5–6 hustles.
- The per-project monitors are removed. (As built: they stay with the old loop, which drives running `GOAL.md`
  chains until the migration; they go with it.)

## Consequences

- The prototype in `master/` (stdlib server, mock data, a scheduler simulation in
  `sim.py`) is the reference for the views; the real hustlemaster replaces mock and
  simulation behind the same `/api/state`.
- The design also exists in a self-hosted Penpot (`~/Work/penpot`); the prototype is the
  source of truth where they differ.

## As built: plan editing in the graph view (`hustlemaster/editing.py`, graph-engine 1.2.0)

- **graph-engine 1.2.0** (branch `feat/editing` of `../graph-engine`, not pushed) got editing, in producer-neutral
  terms: a top-level `edit_url`, details rows with `edit` (a text area, or a select over `choices`) and node
  `actions` (with an optional two-click `confirm`). The page POSTs one JSON edit to `edit_url`, then loads the
  graph again and keeps the node selected; a refusal shows the server's reason and keeps the typed text.
  `edit_url` must be a path on the page's own server. hustlemaster pins it as a path dependency
  (`graph-engine>=1.2.0`, `../../graph-engine`).
- **The route** `POST /api/hustles/<id>/edit` exists only in the web app; the MCP endpoint has nothing like it.
  Prompt and model edits and deletions are plan changes by `user` and go through the plan validation (a Fable
  model while `allow_fable` is off, or deleting the acceptance increment, is refused with the reason).
- **A user's edit confirms the plan**: it is the user's own yes, so it becomes the baseline at once and needs no
  approval of their own — unless an overseer's change is pending. Then the edit is judged like any change: an
  approval only the user's explicit decision may settle is never settled as a side effect of an unrelated edit.
  Before the first activation nothing is confirmed; the activation does that.
- **What a state allows:** a done increment cannot change (a change would never run); a running one may change
  prompt and model (they apply to its next attempt) and be paused, not deleted. Editing a **blocked** increment
  reopens it with fresh attempts — "blocked until the user edits the plan" (ADR 0015) is exactly that edit.
- **Deleting** hands the increment's dependencies to its dependants, in its place, so the order it stood for
  remains.
- **Pause** is runtime control, not a plan change: a flag in `progress` that survives plan versions and makes the
  increment `held = "paused"` (an event overseer's hold outranks it). A running increment finishes its session;
  a paused one is not started again. The node title carries ⏸ (the canvas draws only titles); the panel names
  every hold.

## As built: token login and bind address (`hustlemaster/server.py`)

- **`--bind`** (default `127.0.0.1`; an IPv6 address works too) sets the web app's address only. The MCP endpoint
  binds `127.0.0.1` in its own code and never sees the flag; a test boots with `--bind 0.0.0.0` and finds MCP
  refused on the machine's outward address.
- **One token, its own file:** `dashboard-token` in the hustlemaster's home (0600, created on first start, like
  `interactive-token` but separate, so the browser never holds the token that agents' MCP entry carries). The
  start line names the file. A new file logs every browser out.
- **Everything is behind it** except `/login`: the views, their scripts and the graph-engine page too, so nothing
  about the hustles leaks. Without the token `/api/*` answers 401 (the views then go to the login themselves; the
  cinema view must not keep a stale picture for good) and pages answer 303 to `/login?next=<path>`, which leads
  back after the login. `next` is a path on this server only.
- **The login sets a cookie** holding the token: `HttpOnly`, `SameSite=Lax` (every change is a POST, which a
  cross-site page cannot send with it; `Strict` would log out whoever follows a link to the dashboard),
  `Max-Age` 400 days (an unattended cinema display), `Secure` when a proxy says `X-Forwarded-Proto: https`.
  The graph iframe and its `edit_url` POSTs carry it as same-origin requests. Scripts may send
  `Authorization: Bearer <token>` instead. Comparisons are constant-time; there is no rate limit, the token is
  256 bits.
- `serve()` without a token serves everything openly; only tests use that, `main` always passes one.

## As built: the systemd user unit (`hustlemaster/install.py`)

- **Where things live:** the home is `$XDG_DATA_HOME/hustlemaster` (`~/.local/share/hustlemaster`, 0700): the
  store `hustlemaster.db`, `runs/`, `worktrees/`, `interactive-token`, `dashboard-token`. The unit is
  `$XDG_CONFIG_HOME/systemd/user/hustlemaster.service`. The installer creates both tokens, so the MCP entry can be
  written before the first start and the login token's file named at once.
- **How it runs:** `uv run --project <checkout>/master --no-dev python -m hustlemaster.server …` from the checkout
  (graph-engine is a sibling path dependency, nothing is published to install from). `uv` and `claude` are the
  absolute paths found at install time, not resolved through symlinks (a version manager's `latest` keeps
  working after an update). `Environment=PATH=` is the installing shell's `PATH`, because projects' checks call
  their own tools. The usage reader uses the same `claude` as the sessions (`Service.claude`).
- **Sessions outlive the service:** `KillMode=process`. The default kills the whole control group, the running
  hustlebots with it; with `process` a stop or restart reaches the hustlemaster alone, the sessions go on and the
  next process adopts them (`merge.orphans`). systemd logs "left-over process … Ignoring" on the next start; that
  is the intended case. SIGTERM ends the process through `Service.stop()`, so merges under way finish
  (`TimeoutStopSec=300`). `Restart=on-failure`; `loginctl enable-linger` keeps it across logout.
- **Idempotent:** a second install keeps tokens and store, rewrites the unit, `daemon-reload`, `enable`,
  **`restart`** (so pulled code and a changed port take effect).
- Verified by `master/prototypes/unit_check.py` with the generated unit in the runtime unit folder: a fake
  session survived `systemctl --user restart` and was merged by the new process; with the real `claude`, the
  `/usage` screen was read under systemd (the `forkpty()` in the threaded service did not hang).
