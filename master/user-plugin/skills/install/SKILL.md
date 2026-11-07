---
name: install
description: Install or update the hustlemaster on this machine — the background service that runs the hustles — as a systemd user unit, together with the MCP entry interactive sessions reach it through and /hustle. Use when the user wants to set up hustlebot v2 or the hustlemaster, after pulling a new version of it, or to move its dashboard to another port or address.
---

# install — set the hustlemaster up on this machine

The hustlemaster runs from a checkout of the hustlebot repository (its
`master/` folder) as the systemd user unit `hustlemaster.service`. A tested,
idempotent installer does the work; run it, read its output to the user, and
handle failures. Do not write the unit, the MCP entry or plugin settings by
hand.

## 1. Find the checkout

The installer lives in `<master>/hustlemaster/install.py`. This skill is
normally read in place from `<master>/user-plugin/skills/install/`, so
`<master>` is three folders above this file's folder. Check that
`<master>/pyproject.toml` names the project `hustlemaster`. If it does not
(the plugin was installed from a copy), ask the user where their hustlebot
checkout is and use its `master/` folder.

## 2. Settle the address

Defaults: dashboard on `127.0.0.1:8770`, MCP endpoint on `127.0.0.1:8771`.
Ask only if the user has not said:

- **Where the dashboard should answer.** On a dedicated machine reached over
  the network, the address of a private interface (`--bind 10.0.0.5`) or
  `--bind 0.0.0.0` behind a reverse proxy. The dashboard can steer hustles and
  grant approvals; it always asks for the login token, but never bind it to a
  public interface.
- A different `--port` if 8770 is taken. The MCP endpoint always stays on
  127.0.0.1; change `--mcp-port` only if 8771 is taken.

## 3. Run the installer

```bash
uv run --project <master> --no-dev python -m hustlemaster.install [--bind ADDR] [--port N] [--mcp-port N]
```

It is safe to run again: tokens and the store are kept, the unit is
rewritten and the service restarted (running sessions survive the restart and
are adopted). It:

- creates the home `~/.local/share/hustlemaster/` (store, runs, worktrees,
  `interactive-token`, `dashboard-token`),
- writes `~/.config/systemd/user/hustlemaster.service`, which runs this
  checkout with `uv` and the current shell's `PATH`, then enables and
  restarts it,
- adds the user-scope MCP entry `hustlemaster` with the interactive token,
- installs `/hustle` from the checkout's own marketplace (`hustlebot@hustlemaster`).

Flags for special cases: `--no-start` (write and reload, start nothing),
`--no-systemd` (leave systemd alone), `--no-plugin`, `--claude PATH`.

## 4. Report

Pass on the installer's summary: the dashboard URL, **where the login token
is** (`cat` it only if the user asks — it opens the dashboard), and the log
command. Then:

- **`/hustle` skipped because another `hustlebot` is installed:** that is the
  old `GOAL.md` loop's plugin. Both answer to `hustlebot`, so the installer
  leaves it alone. Until the user removes the old one (only once no old loop
  runs any more), `/hustle` is available with
  `claude --plugin-dir <master>/user-plugin`.
- **Survive logout and reboot:** `loginctl enable-linger $USER`, once.
- **Check it runs:** `systemctl --user status hustlemaster` and the dashboard
  URL; new sessions see the hustlemaster's MCP tools (`list_hustles`, …).
- Next step: `/hustle` in a project to plan the first hustle.

## Failures

- `install failed: … not found on PATH` — install `uv` or `claude`, or pass
  `--claude`.
- `systemctl --user` fails — no user session bus (e.g. over plain `su`); log
  in properly or run with `--no-start` and start it later.
- The unit starts and stops: `journalctl --user -u hustlemaster -n 50`. A
  port in use shows there; re-run with another `--port`.
