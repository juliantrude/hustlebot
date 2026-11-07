# ADR 0013 — hustlemaster runtime and dashboard

Status: Proposed (revised 2026-11-07 after the grilling)
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
- The per-project monitors are removed.

## Consequences

- The prototype in `master/` (stdlib server, mock data, a scheduler simulation in
  `sim.py`) is the reference for the views; the real hustlemaster replaces mock and
  simulation behind the same `/api/state`.
- The design also exists in a self-hosted Penpot (`~/Work/penpot`); the prototype is the
  source of truth where they differ.
