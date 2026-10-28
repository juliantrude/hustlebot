# hustlebot — a cross-session goal loop for Claude Code

<img src="assets/working.svg" alt="The hustlebot sits at a screen and types; code scrolls past and the cursor blinks." width="520">

Claude Code hustles toward a long-term goal **across many sessions**: one
small, verified increment per headless run, self-scheduling the next run —
including parking itself until your subscription's session/week limit resets
and resuming automatically. Progress lives in a human-readable `GOAL.md`; a
zero-token web dashboard shows what's done, what's next, and how far the
Definition of Done is.

Works on **Linux** (systemd user timers) and **macOS** (launchd; beta — see
caveats). Runs on a regular Claude subscription; no API key needed.

```
GOAL.md  ←→  /advance-goal (skill, 1 increment per run)
                 │  arms one-shot timer (systemd/launchd)
                 ▼
        .hustle/bin/hustle-session.sh   ← fired by the timer, self-deleting
                 │
                 ├── hustle-scheduler.sh   platform-abstract one-shot scheduling
                 └── hustle-monitor.py     web dashboard on :8787 (zero tokens)
```

<img width="1157" height="432" alt="image" src="https://github.com/user-attachments/assets/9ca64b72-88f0-4aef-a9ca-3bab88d457b3" />

## Install

> **Renamed in 0.3.0:** the plugin was called `hustle` and is now `hustlebot`,
> so the command is `/hustlebot:hustle` instead of `/hustle:hustle`. If you
> installed the old one, run `/plugin uninstall hustle@trude-claude` and
> install again below. **Nothing in your projects changes** — `.hustle/`, the
> `HUSTLE_*` config keys and the `hustle-<slug>` services keep their names, so
> running chains are unaffected and need no migration.

```
/plugin marketplace add juliantrude/skills
/plugin install hustlebot@trude-claude
```

## Use

One command, in the project you want the loop in:

```
/hustlebot:hustle
```

It orchestrates everything: installs `.hustle/` into the project (if missing),
runs the **grilling interview** that turns your fuzzy goal into a loop-ready
`GOAL.md` (if missing), does a pre-flight check, and ignites the chain. Then
watch it work at `http://<host>:8787`.

The pieces are also available individually:

| Skill | Purpose |
|---|---|
| `/hustlebot:hustle` | top-level orchestrator: install → plan → pre-flight → ignite |
| `/grill-goal` | just the interview that builds/sharpens `GOAL.md` |
| `/loop-setup` | just the per-project installation |
| `/advance-goal` | the runtime skill the chain calls each run (also usable interactively via `/loop /advance-goal`) |

Kill switch: `.hustle/bin/hustle-scheduler.sh disarm` (stops the chain after
the current run; a running run finishes).

## How it paces itself

Every run does one increment, then checks `claude -p "/usage"`:

| Budget | Action |
|---|---|
| healthy | next run in ~2 min |
| session ≥ 85 % | park until the session reset (+2 min buffer) |
| week ≥ 90 % | park until the weekly reset |
| goal complete | stop — nothing scheduled |

The dashboard puts a face on that table — the avatar shows the chain's state at
a glance, so you can tell from across the room whether it is working, waiting,
or parked at a limit:

| <img src="assets/waiting.svg" width="230" alt="The bot lifts a mug of coffee."> | <img src="assets/sleeping.svg" width="230" alt="The bot sleeps in a hammock under a crescent moon, Zzz drifting up."> | <img src="assets/searching.svg" width="230" alt="The bot scans the horizon with binoculars, a question mark bobbing overhead."> |
|:--:|:--:|:--:|
| **waiting** — between runs | **sleeping** — parked at a limit | **searching** — monitor unreachable |

A safety net re-arms a +15 min fallback if a run crashes before scheduling its
successor; all state is in `GOAL.md`, so killed or missed runs are harmless.

## ⚠️ Security — read this once

- By default the loop runs with `--dangerously-skip-permissions`
  (`HUSTLE_SKIP_PERMISSIONS="true"` in `.hustle/config`): an **unattended
  agent with full tool access** that will edit, commit, push, and talk to your
  issue tracker on its own. Only point it at projects where you accept that,
  and write the boundaries into `GOAL.md` (the `/grill-goal` interview asks).
- The monitor binds to `0.0.0.0:8787` with **no auth** — anyone on your LAN can
  read the dashboard including log excerpts. Set `HUSTLE_BIND="127.0.0.1"` in
  `.hustle/config` if that's not what you want.

## Configuration (`.hustle/config`)

| Key | Default | Meaning |
|---|---|---|
| `HUSTLE_MODEL` / `HUSTLE_EFFORT` | `sonnet` / `high` | model + effort for headless runs |
| `HUSTLE_SKIP_PERMISSIONS` | `true` | `false` = headless runs use default permission checks (will block on anything not allowlisted) |
| `HUSTLE_PORT` / `HUSTLE_BIND` | `8787` / `0.0.0.0` | monitor listen address |

## Platform caveats

- **Linux:** run `loginctl enable-linger $USER` once, or the chain dies at
  logout. Reboots are fine (`Persistent=true` catches up missed fires).
- **macOS (beta):** scheduling via launchd `StartCalendarInterval`. Fires
  missed during **sleep** are delivered on wake; fires missed while **powered
  off** are lost — restart the chain manually (`hustle-session.sh &`). The
  launchd backend has not had wide testing yet; issues welcome.

## Anatomy of a run

1. take the run lock (single-flight), disarm the timer that fired us
2. ensure the monitor is up
3. `claude -p "/advance-goal"` → the skill picks the next unchecked increment
   from `GOAL.md`, does it, verifies it, checks it off, updates the log +
   next-action pointer
4. the skill arms the next one-shot based on `/usage`
5. the launcher publishes `status.json` for the dashboard and re-arms a
   fallback if needed

`GOAL.md` is the whole contract: `## Plan` (checkbox increments in `###`
streams), `## Definition of Done`, `## Working Agreements` (rules the agent
re-reads every run), `## Status` (`STATUS:` flag + `**Next action:**`),
`## Budget` (threshold overrides), `## Log` (one line per iteration).

## Credits

The relentless-interview ("grilling") method that `/grill-goal` uses to turn a
fuzzy goal into a precise GOAL.md is adapted from the grilling skill by
[Matt Pocock](https://github.com/mattpocock).
