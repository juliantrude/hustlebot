---
name: hustle
description: Top-level entry point for the hustle loop — orchestrates everything from zero to a running cross-session goal chain in the current project. Use when the user wants to start hustling on a long-running goal, says "/hustle", "set up the loop here", or wants Claude to keep working on something across sessions.
---

# hustle — from zero to a running goal chain

You orchestrate the full lifecycle. Walk these phases **in order**, skipping
any phase that is already satisfied. Tell the user which phase you're in.

## Phase 1 — Install (skip if `<project>/.hustle/` exists)

Follow the `/loop-setup` skill: run `scripts/hustle-setup.sh <project-dir>`
from this plugin's root. It creates `.hustle/` (runtime scripts + config), the
platform scheduling (systemd unit on Linux), and smoke-tests the scheduler.
If the smoke test fails, stop and debug before going further — a broken
scheduler means a chain that silently dies.

## Phase 2 — Plan (skip if a ready GOAL.md or CHARTER.md already exists)

First pick the **mode** with the user (unless one is already set up):

- **plan mode** — a fixed goal the loop executes and then stops. Follow the
  `/grill-goal` skill → a loop-ready `GOAL.md` (`STATUS: READY`).
- **charter mode** — a self-directed PO+Dev loop against a **Plane** backlog that
  keeps setting itself new goals until a feature-freeze date or manual abort.
  Follow the `/grill-charter` skill → a `CHARTER.md`, the Plane key in
  `.hustle/secrets`, and `HUSTLE_MODE="charter"` in `.hustle/config`.

Explore the project first, then grill one question at a time (recommended answer
included). If a GOAL.md/CHARTER.md exists but looks half-finished (unfilled
`_TODO:`), treat it as input to the interview, not as ready.

## Phase 3 — Pre-flight check (never skip)

Confirm with the user before igniting — this is the last human checkpoint
before an unattended agent starts committing and pushing on its own:

1. Show a 3-line summary: the north star / charter mission, what the first run
   does (plan mode: the first increment; charter mode: a **Planner** run that
   seeds the Plane backlog), and the configured model/effort from `.hustle/config`
   (charter mode also draws on `.hustle/secrets` for the Plane key).
2. Point out the permission mode (`HUSTLE_SKIP_PERMISSIONS` in the config) and
   what it means.
3. On Linux: check `loginctl show-user $USER --property=Linger`; if linger is
   off, tell the user the chain dies at logout and give them the
   `loginctl enable-linger $USER` command.
4. Check for a peer chain — every chain on the machine draws on the same
   subscription, so two at once burn the budget twice as fast and race each
   other into the session limit:

   ```bash
   .hustle/bin/hustle-session.sh --check-peers   # 0 = clear, 3 = one is working
   ```

   On exit 3 the log names the project. **Do not ignite.** Tell the user which
   chain is working and ask whether to stop it (`systemctl --user stop
   hustle-<their-slug>.service` plus `hustle-scheduler.sh disarm` in that
   project) or to wait. A finished peer needs no question — the check retires
   its leftover monitor by itself.

   Headless runs make the same check and simply park for 5 minutes; only here,
   with a human present, is it worth asking.

   The same check logs `[peer] stale unit …` for any chain whose project
   directory is gone. Surface those to the user with the `hustle-setup.sh
   --uninstall <path>` line from the log — but never run it for them
   unasked: an unmounted disk is indistinguishable from a deleted project,
   and the units may belong to work they still want.
5. Ask explicitly: start now?

## Phase 4 — Ignite

- Linux: `systemctl --user start hustle-<slug>.service`
- macOS: `nohup .hustle/bin/hustle-session.sh >/dev/null 2>&1 &`

(`<slug>` is `HUSTLE_SLUG` from `.hustle/config`.)

Then verify ignition instead of assuming it:
- within ~30s the run should be visible (Linux: `systemctl --user is-active
  hustle-<slug>.service` = `activating`; both platforms: `.hustle/run.pid`
  exists and the pid is alive),
- the monitor should answer on `http://localhost:<HUSTLE_PORT>/api/status`.

Report to the user: monitor URL (with the machine's LAN address if
determinable), how the pacing works (one increment → ~2 min gap → next run;
parks at session/week limits and resumes on reset), and the kill switch
(`.hustle/bin/hustle-scheduler.sh disarm`).

## If the user asks for status instead of setup

Don't re-run phases — read `GOAL.md` + `.hustle/status.json`, check whether a
run is active (`run.pid`) and what's armed (`hustle-scheduler.sh next`), and
summarize. Point at the monitor URL for the live view.
