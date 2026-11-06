# ADR 0011 — One headless session per increment, in its own worktree

Status: Proposed
Date: 2026-11-06

## Context

Parallel increments of one project must not edit the same checkout at the same time.
Claude Code isolates parallel agents with git worktrees; merging back is the caller's
job. ADR 0007 has the Executor commit directly to `main`.

## Decision

- Each increment runs as its own headless Claude Code **session**, started by the master
  as a **child process**: `claude -p … --model <model> --output-format json`, stdin closed
  (otherwise it waits 3 s for piped input), `cwd` set to the increment's worktree. The
  process ending is the signal that the increment finished; its JSON result carries the
  outcome and the tokens per model.
- The master creates the worktree itself (`git worktree add`) on a short-lived branch.
- When the increment is verified: rebase onto `main`, verify again, fast-forward `main`,
  remove the worktree. A rebase conflict is a resolution step of the same increment, not
  a failure of the chain.
- On a master restart, orphaned worktrees are found and their increments resumed.

## Consequences

- Amends ADR 0007: `main` stays the only long-lived branch and there is still one commit
  per increment; the way there runs through a worktree.
- Verified in a prototype (`master/prototypes/worktree_session.sh`): a headless session in
  a worktree loads the project's `CLAUDE.md`, the checked-in `.claude/settings.json` with
  its hooks, and even the main checkout's gitignored `settings.local.json`. Auto memory is
  shared with the main checkout. Only other gitignored files (`.env`, …) must be copied in.
- Conflicts are rarer because dependent increments run in order; they remain possible for
  independent increments touching the same file.
