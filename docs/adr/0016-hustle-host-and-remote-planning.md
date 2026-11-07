# ADR 0016 — A hustle host, planned from elsewhere

Status: Accepted
Date: 2026-11-07

## Context

ADR 0013 put the hustlemaster, the repositories and the user's planning sessions on one machine. In practice the
hustlemaster runs on a dedicated VM (the hustle host) while the user plans on their own machine. A plan then has to
reach the host, the code it was made against too, and the results have to come back — also for public
repositories, where nothing half-done should go through the public remote.

## Decision

- The hustlemaster keeps **one repository per project** under one root on the hustle host (`--projects`, default
  `~/hustles/<name>`). A project is named, not given as a path. The repository accepts pushes to its checked-out
  `main` (`receive.denyCurrentBranch updateInstead`).
- A project has **at most one remote, and it is optional**. With a remote the hustlemaster clones and fetches from
  it, fast-forwarding its `main` where it can. Without one the user pushes their state straight into the repository
  on the hustle host over SSH and fetches the results from there. The hustle host never connects back to the user's
  machine: its outbound boundary stays closed, and a misled hustlebot cannot reach the user's other repositories.
- `submit_hustle` takes the project's name and the **commit** the plan was made against; it is refused until that
  commit is in `main` on the host, with the command that fixes it.
- **Pushing** is per project: `off` (default), `branch` (the host's `main` to the hustle's own branch
  `hustle/<id>` on the remote) or `main`. It needs a remote. Only the hustlemaster pushes. An increment is done once
  `main` on the host has moved; the push is a step after it, retried with a growing wait, and **never blocks the
  hustle**. The dashboard shows "not pushed since" and a Push button per hustle.
- **Required ignored files** (an `.env`, local config) are a project setting. They never travel through git or MCP;
  the user copies them once. A hustle cannot be activated, and an increment does not start, while one is missing.
- A second **external MCP endpoint** (`--mcp-bind`, port 8772) serves the user's sessions on other machines behind
  the TLS reverse proxy, at `<dashboard host>/mcp`. It takes only the interactive token and only the Host names
  given with `--mcp-name`; hustlebot session tokens work on the local endpoint alone.
- The installer has two modes: the **server** install on the hustle host, and a **client** install
  (`--client <url>`) that adds only `/hustle` and the MCP entry with the host's interactive token.
- **Project environment files** `<hustlemaster home>/env/<project>.env` (`KEY=value`) reach the sessions of that
  project as environment variables: secrets its checks need, such as a test user's login. They live outside every
  repository and are never shown.
- Hustlebots install nothing system-wide; a missing system tool makes the increment `blocked` with the reason.

## Consequences

- Amends ADR 0011 (pushing: a mode instead of a switch, and never blocking), ADR 0013 (planning sessions need not
  run on the host) and ADR 0014 (the interactive role is reachable from the network, behind TLS and its token).
- Uncommitted changes on the user's machine do not reach a hustle; `/hustle` says so.
- The user reviews results with an ordinary `git fetch` from the remote or from the host.
