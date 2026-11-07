# ADR 0011 — One headless hustlebot per increment, in its own worktree

Status: Proposed (revised 2026-11-07 after the grilling)
Date: 2026-11-06

## Context

Parallel increments of one project must not edit the same checkout at the same time.
Claude Code isolates parallel agents with git worktrees; merging back is the caller's
job. ADR 0007 has the Executor commit directly to `main`.

## Decision

- Each increment runs as its own headless Claude Code session, a **hustlebot**, started
  by the hustlemaster as a **child process**: `claude -p … --model <model>
  --output-format json`, stdin closed (otherwise it waits 3 s for piped input), `cwd` set
  to the increment's worktree. The process ending is the signal that the increment
  finished.
- **Input:** the hustlemaster assembles the prompt from the hustle's brief and the
  increment's prompt and passes it directly. Nothing about the increment is written into
  the repo. The internal skills come with `--plugin-dir`, the session's MCP access with
  `--mcp-config` and `--strict-mcp-config` (ADR 0014).
- **Output:** exactly one commit on the worktree branch, and a structured report block
  in the session's last message: status (`done`, `failed`, `too-big`, `blocked`),
  verification evidence, and proposed plan changes. The hustlemaster reads the block
  from the JSON result, and the tokens per model and the peak context from the same
  result.
- The hustlemaster creates the worktree itself (`git worktree add`) on a short-lived
  branch and copies the main checkout's gitignored files into it, except build output,
  installed dependencies (`node_modules`, `.venv`, `dist`, …) and files over 1 MB. A
  per-project list kept by the hustlemaster overrides the default.
- When the increment is verified: rebase onto `main`, verify again, fast-forward `main`,
  remove the worktree. A rebase conflict is resolved by the same increment's session
  first; it counts as one attempt (ADR 0015).
- **Pushing** is a per-project switch, set by `/hustle` while planning, default off, with
  an explicit question for public repos. When on, only the hustlemaster pushes, after
  each fast-forward of `main`; a hustlebot never pushes.
- On a hustlemaster restart, orphaned worktrees are found and their increments resumed.

## Consequences

- Amends ADR 0007: `main` stays the only long-lived branch and there is still one commit
  per increment; the way there runs through a worktree.
- Verified in a prototype (`master/prototypes/worktree_session.sh`): a headless session in
  a worktree loads the project's `CLAUDE.md`, the checked-in `.claude/settings.json` with
  its hooks, and even the main checkout's gitignored `settings.local.json`. Auto memory is
  shared with the main checkout.
- Verified with real Haiku sessions (`master/prototypes/runner_probe.py`, Claude Code
  2.1.314), the two behaviours Claude Code does not document:
  - **`--mcp-config` servers need no approval** in a `-p` session: the server shows up as
    `connected` (source `dynamic`) at once, and `${HUSTLEMASTER_TOKEN}` in the header is
    expanded from the environment — the session saw exactly the worker's tools.
    `--strict-mcp-config` also drops the MCP servers that installed plugins bring (the
    Figma plugin's server was gone), so a hustlebot reaches no MCP server but the
    hustlemaster.
  - **Calling those tools is still a permission question.** Without an allow rule the call
    is denied and listed under `permission_denials` in the result; with `--allowedTools
    mcp__hustlemaster__<tool>` it goes through. The runner therefore passes allow rules for
    the hustlemaster's tools, and has to settle the permission mode for the rest of the
    work (edits, shell) when it starts sessions.
  - **A `--plugin-dir` plugin loads next to the installed plugins** as `<name>@inline`, its
    skills namespaced `<name>:<skill>`; installed plugins and their skills stay available.
    **If its name equals an installed plugin's, it replaces that plugin for the session**:
    with an inline `hustlebot`, the installed `hustlebot@juliantrude` and its skills
    (`advance-goal`, `hustle`, …) were gone. The internal plugin is therefore named
    `hustlebot`, so a hustlebot sees the worker and overseer skills and never the loop or
    planning skills of the installed plugin.
