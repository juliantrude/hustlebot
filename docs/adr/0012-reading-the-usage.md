# ADR 0012 — How the master reads the subscription usage

Status: Proposed
Date: 2026-11-06

## Context

The master's budget decisions (ADR 0008) need the 5h and weekly usage in percent and
their reset times. Headless `/usage` does not report them reliably.

## Decision

Three sources, combined:

1. **`/usage` through a pseudo-terminal:** the master starts an invisible interactive
   Claude Code session, types `/usage`, renders the screen with a terminal emulator and
   parses "Current session … N% used · Resets …" and "Current week (all models) …".
   Verified in `master/prototypes/usage_pty.py` (pexpect + pyte); costs nothing, no prompt
   is sent.
2. **Own accounting:** every session's JSON result has its tokens per model (`modelUsage`,
   incl. cache reads and writes) and `total_cost_usd`. The master learns roughly what a
   point-hour costs in percent and estimates between readings.
3. **Safety net:** limit errors and reset timestamps of rejected runs, with the
   exponential backoff that `hustle-session.sh` already uses.

## Consequences

- Source 1 parses a screen, not an API, and breaks when the layout changes; source 2 keeps
  the master working meanwhile, source 3 keeps it safe.
- The reading runs on the master's schedule, never inside a working session.
