# ADR 0012 — How the hustlemaster reads the subscription usage

Status: Proposed
Date: 2026-11-06

## Context

The hustlemaster's budget decisions (ADR 0008) need the 5h and weekly usage in percent
and their reset times. Headless `/usage` does not report them reliably.

## Decision

Three sources, combined:

1. **`/usage` through a pseudo-terminal:** the hustlemaster starts an invisible
   interactive Claude Code session, types `/usage`, renders the screen with a terminal
   emulator and parses "Current session … N% used · Resets …" and "Current week (all
   models) …". Verified in `master/prototypes/usage_pty.py` (pexpect + pyte); costs
   nothing, no prompt is sent.
2. **Own accounting:** every session's JSON result has its tokens per model (`modelUsage`,
   incl. cache reads and writes) and `total_cost_usd`. The hustlemaster learns roughly
   what a point-hour costs in percent and estimates between readings and before a start.
3. **Safety net:** limit errors and reset timestamps of rejected runs, with the
   exponential backoff that `hustle-session.sh` already uses.

### As built (2026-11-07, `master/hustlemaster/usage.py`)

- **The unit of accounting is dollars, not point-hours.** Between two readings of the same
  window (resets within an hour of each other) the hustlemaster learns percent per dollar
  of `total_cost_usd` finished in between, smoothed over samples, and extrapolates from
  the last reading with what finished since. Point-hours would count sessions waiting on
  a slow check as spending; `total_cost_usd` already weighs the models and the cache
  reads, which is what the subscription meters. Below $0.50 between two readings the
  difference says nothing (the screen shows whole percents) and nothing is learned.
- **A fourth reading: the session stream.** Every `stream-json` session emits a
  `rate_limit_event` with `unifiedWindows.five_hour/seven_day` `utilization` (a fraction;
  0.49 in a stream matched 49 % on the screen minutes later) and `resetsAt` as epoch. It
  arrives after the session's first call, so the reading is dated at the session's start.
- **The pty session runs in a folder the user already trusts in Claude Code** (one of the
  projects). The trust question is detected and reported, never answered: answering it
  writes to the user's configuration. A real read takes about 4 s.
- **Safety net:** a `rejected` window in the stream or a limit error text (epoch after
  `|`, an ISO time, or "resets 6:30am (Europe/Berlin)") blocks starts until that reset
  plus two minutes; without a reset it backs off 15 min doubling to 4 h like
  `hustle-session.sh`. A session that finishes without a limit clears it, and so does a
  screen reading below 100 % in both windows — the screen is free, a probing session is
  not. The block is not persisted; after a restart the first rejected start sets it again.

## Consequences

- Source 1 parses a screen, not an API, and breaks when the layout changes; source 2 keeps
  the hustlemaster working meanwhile, source 3 keeps it safe.
- The reading runs on the hustlemaster's schedule, never inside a working session.
- Headless sessions bill Fable usage credits without asking on plans where Fable is not
  included; the `allow_fable` setting (ADR 0014) is the guard, not this reading.
