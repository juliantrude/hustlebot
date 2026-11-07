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
  --output-format stream-json --verbose --permission-mode bypassPermissions`, stdin closed
  (otherwise it waits 3 s for piped input), `cwd` set to the increment's worktree, the
  stream written to a file in the run's directory. The process ending is the signal that
  the increment finished. The session must edit and run the project's checks unattended,
  as the old loop always did with `--dangerously-skip-permissions`; the worktree bounds
  where it works, its token what it may do to plans.
- **Input:** the hustlemaster assembles the prompt from the hustle's brief and the
  increment's prompt and passes it directly. Nothing about the increment is written into
  the repo. The internal skills come with `--plugin-dir`, the session's MCP access with
  `--mcp-config` and `--strict-mcp-config` (ADR 0014).
- **Output:** exactly one commit on the worktree branch, and a structured report block
  in the session's last message: a fenced `hustlebot-report` block holding a JSON object
  with status (`done`, `failed`, `too-big`, `blocked`), verification evidence, and
  proposed plan changes. The hustlemaster reads the block and the tokens per model from
  the stream's result event, and the peak context from the stream's assistant messages
  (see Consequences for why not from the result). No usable block, an error result or a
  non-zero exit count as a failed attempt.
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
- **The peak context is not in the JSON result** (revised while building the runner, one
  real Haiku run with four tool calls): the result's `usage` is summed over all turns
  (93,525 tokens) and its `iterations` hold only the last call (23,551). The last call
  would understate the peak after a compaction. Each assistant message in `stream-json`
  carries its own call's usage, so the runner reads the stream and takes the largest
  context of the main session's calls (subagents have their own context). The stream also
  carries `rate_limit_event`s with the 5-hour and 7-day utilization and reset times — an
  input for the usage reader (ADR 0012).
