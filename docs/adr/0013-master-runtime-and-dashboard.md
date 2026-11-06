# ADR 0013 — Master runtime and dashboard

Status: Proposed
Date: 2026-11-06

## Context

The master (ADR 0008) needs a home, a store and a face. Today every project has its own
monitor (`scripts/hustle-monitor.py`) on its own port.

## Decision

- The master is a **new Python program** (no migration of the shell scripts; proven parts
  such as reset-timestamp parsing and backoff are carried over). It runs as a **systemd
  user service**, not in Docker: it needs the user's Claude login, git credentials, project
  directories and `~/.claude` exactly as they are.
- **State:** the graph per chain lives in the project repo (ADR 0009); runtime control —
  running sessions, points, deficits, history — lives in a local SQLite database.
- **Web app on 127.0.0.1** with two views:
  - **Control view** (`/`): chain cards sorted active first, then by weight; per card the
    active switch, weight in `fr` with share and points in use, status, project, title,
    progress, a live load strip (one segment per session point, coloured by model, the
    last hour as a fading trail, the share as a dashed line, borrowed points outlined),
    and a graph preview. The energy core shows the 5h and weekly usage, its brightness
    follows the total session points, and beams as thick as each share flow into the
    chains. Total points are set there; approvals (model upgrades, graph growth) are
    accepted or rejected there. `/chains/<id>` opens the chain's graph full screen in
    graph-engine, with each increment's instructions in the side panel.
  - **Cinema view** (`/cinema`): for an always-on OLED display; no controls, pure black,
    neon colours. The core's beams bend into the chain lanes and run on as them, carry
    the last hour as a waveform and flow into each chain's graph, where sparks hit the
    running increments. The scene drifts slowly against burn-in.
- The per-project monitors are removed.

## Consequences

- The prototype in `master/` (stdlib server, mock data, a scheduler simulation in
  `sim.py`) is the reference for the views; the real master replaces mock and simulation
  behind the same `/api/state`.
- The design also exists in a self-hosted Penpot (`~/Work/penpot`); the prototype is the
  source of truth where they differ.
