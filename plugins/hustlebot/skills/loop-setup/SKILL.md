---
name: loop-setup
description: Install the advance-goal loop into the current project — copies the runtime scripts, writes the config and (on Linux) the systemd unit, and smoke-tests the scheduler. Use when the user wants to set up the cross-session goal loop in a project, or after updating the plugin.
---

# loop-setup — install the advance-goal loop into a project

Run the deterministic installer that ships with this plugin, then walk the user
through what happened. Do not hand-craft the installation yourself — the
installer is idempotent and tested; your job is to run it, interpret its
output, and handle failures.

## Steps

1. Locate the plugin root (two directories up from this SKILL.md) and run:

   ```bash
   bash <plugin-root>/scripts/hustle-setup.sh <project-dir>
   ```

   with `<project-dir>` = the project the user wants the loop in (default:
   current working directory — confirm with the user if ambiguous).

2. The installer will:
   - create `<project>/.hustle/` with `bin/` (runtime scripts), a
     `config` file (kept if it already exists) and a `.gitignore`,
   - on Linux: write the `hustle-<slug>.service` systemd user unit,
   - smoke-test the scheduler (arm → read back → disarm) and fail loudly if
     the platform backend doesn't work.

3. If the smoke test fails, debug the platform backend (`hustle-scheduler.sh`):
   Linux → systemd user session issues (`systemctl --user` reachable?);
   macOS → launchd bootstrap (`launchctl print gui/$(id -u)` reachable?).

4. Walk the user through the printed next steps:
   - define the work (required before the first run): `/grill-goal` for **plan
     mode** (a GOAL.md the loop executes then stops), or `/grill-charter` for
     **charter mode** (a CHARTER.md + Plane backlog, self-directed). The installer
     writes `.hustle/config` with `HUSTLE_MODE="plan"` by default and scaffolds a
     `0600 .hustle/secrets` file that `/grill-charter` fills with the Plane key,
     - the platform-specific kickoff command,
   - the monitor URL (`http://<host>:8787`) and the kill switch.

5. Mention the two standing caveats:
   - **Unattended permissions:** the config defaults to
     `HUSTLE_SKIP_PERMISSIONS="true"` — the loop commits/pushes autonomously.
     Point the user at the README's security section; they can set it to
     `false` and maintain an allowlist instead if they want a tighter leash.
   - **Boot persistence:** Linux needs `loginctl enable-linger $USER` for the
     chain to survive logout; on macOS, runs missed while powered off are lost
     until the next manual kickoff (sleep/wake is fine).

## Re-running after a plugin update

Same command — the installer refreshes `bin/` but never overwrites an existing
`config`. Remind the user that a running chain picks the new scripts up on its
next run automatically.
