---
name: advance-goal
description: The Executor of the hustle loop — advance one verified increment then budget-check and schedule the next run. In plan mode it works a GOAL.md; in charter mode it pulls one work item from the Plane backlog, does it, and moves it to Done. Driven headless by hustle-session.sh or by `/loop /advance-goal` across session-limit resets.
---

# advance-goal — one iteration of a cross-session goal loop

You are one iteration of a self-paced loop working toward a long-term goal.
The single source of truth is **`GOAL.md`** in the project root. Everything you
need to resume — the north-star goal, the increment plan, what's done, and the
explicit next action — lives there. Trust it over your memory of earlier turns.

## First: which mode?

Read `HUSTLE_MODE` from `.hustle/config` (default `plan`).
- **`plan`** (classic) → follow the **Plan-mode protocol** below, unchanged.
- **`charter`** → the backlog lives in **Plane**, not `GOAL.md`; the human-owned
  mandate is `CHARTER.md`. Skip the Plan-mode protocol and follow the
  **Charter-mode protocol** near the bottom instead, then stop.

## Plan-mode protocol (do these in order, every iteration)

### 1. Load state
- Read `GOAL.md`. If it does not exist, STOP and tell the user to create it
  (via `/grill-goal`) — do not invent a goal.
- **Readiness guard:** if `GOAL.md` still contains unfilled template placeholders
  (`_TODO:` markers) or `STATUS: NOT_STARTED` with no real plan, the goal is not
  ready. STOP, say so, and do NOT invent work or treat placeholder lines as
  increments. (In a headless run this simply means "nothing to do yet" — exit
  cleanly.)
- Follow any priority rules in GOAL.md's Working Agreements (e.g. process
  assigned review work before plan increments).
- Find the **next unchecked increment** (`- [ ]`) under `## Plan`, or use the
  explicit `**Next action:**` pointer under `## Status` if set.
- If every increment is checked and the goal's Definition of Done is met →
  write `STATUS: COMPLETE` at the top of `## Status`, tell the user the goal is
  done, and do **not** schedule anything. The loop ends here.

### 2. Do the work
- Execute exactly **one** increment — the smallest shippable step. Resist doing
  more; small idempotent steps survive interruption.
- Follow the project's own conventions (CLAUDE.md, Working Agreements in GOAL.md).

### 3. Verify
- Verify the increment the way the project expects (tests, boot checks, …).
  Prefer running the actual verification over assuming.
- If verification fails and you cannot fix it this iteration, do NOT check the
  box. Record the blocker in `## Status` and leave the increment for next time.

### 4. Persist progress (this is what makes the loop resumable)
Update `GOAL.md`:
- Check off (`- [x]`) the increment only if it's done AND verified.
- Append a dated line under `## Log` (e.g. `- 2026-07-15 14:20 — <what changed / result>`).
- Rewrite `**Next action:**` under `## Status` to the concrete next step, so a
  cold next iteration can start instantly.
- Record any blocker/decision that a future iteration must know.

### 5. Budget check — schedule the next run
Run the subscription usage report as a subprocess and parse it (ANSI-stripping
via python3 — portable across Linux/macOS):

```bash
claude -p "/usage" 2>&1 | python3 -c "import sys,re;print(re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b[()][AB0]','',sys.stdin.read()))" | grep -Ei 'session|week'
```

Output looks like:
```
Current session: 34% used · resets Jul 15, 10pm (Europe/Berlin)
Current week (all models): 61% used · resets Jul 18, 11pm (Europe/Berlin)
Current week (Sonnet): 3% used · resets Jul 18, 11pm (Europe/Berlin)
```

**Two carrier modes — decide with this test, never by intuition:**

```bash
test -x .hustle/bin/hustle-scheduler.sh && echo headless || echo loop
```

If it prints `headless`, you are in the one-shot chain and you **must** arm the
next run before you exit — regardless of how the run appears to have started.
The scheduler's existence is the entire test. Do not look for other scaffolding
and do not infer the mode from the absence of a wrapper: guessing here silently
drops the chain to the 15-minute crash fallback instead of its 2-minute pace.

- **Headless one-shot chain** (invoked via `claude -p "/advance-goal"` from
  `.hustle/bin/hustle-session.sh`): each run is independent and short-lived,
  and **you are responsible for arming the next run before you exit**. There is
  no persistent process and `ScheduleWakeup` does nothing here. Arm via the
  scheduler (path relative to the project root, which is your cwd):

  ```bash
  .hustle/bin/hustle-scheduler.sh arm --in 120                    # budget healthy → continue soon
  .hustle/bin/hustle-scheduler.sh arm --at "2026-07-15 22:02:00"  # exhausted → restart just after reset
  ```

  Convert the `/usage` "resets …" time to a `YYYY-MM-DD HH:MM:SS` timestamp and
  add ~2 minutes of buffer so you're safely past the reset. If the goal is
  COMPLETE, do **not** arm anything — write `STATUS: COMPLETE` in GOAL.md and
  the chain stops. (A safety net in hustle-session.sh re-arms a fallback if you
  ever forget, but arm it explicitly.)

  The scheduler **fails loudly** (non-zero exit) if the timer couldn't be armed
  or the target time already lies in the past. If `--at` fails, fall back to a
  relative `--in` (e.g. seconds until the reset + 120) and note it in the log.

- **Self-paced `/loop`** (driven by `/loop /advance-goal`): the process
  persists; instead of arming a timer you recommend a `ScheduleWakeup` delay.
  Use the hop logic below (a single wakeup is capped at 1 hour).

Decision table (thresholds are defaults — GOAL.md may override under `## Budget`).
The "arm" column is the headless one-shot action; the "hop" column is the
`/loop` fallback.

| Condition | Headless: arm next run | `/loop`: recommend wakeup |
|---|---|---|
| Goal COMPLETE | arm **nothing** (write `STATUS: COMPLETE`) | no wakeup — end loop |
| `week (all models)` ≥ **90%** | `arm --at "<weekly reset + 2min>"` — real work paused until the weekly reset | ~3000s hop, keep re-checking |
| `session` ≥ **85%** | `arm --at "<session reset + 2min>"` — restart at the next session window | ~3000s hop, keep re-checking |
| otherwise | `arm --in 120` — continue promptly | ~120–270s wakeup |

Notes:
- The weekly cap is the real ceiling; if `week` is exhausted, arming for the
  *session* reset is pointless — arm for the weekly reset instead.
- `/loop` only: `ScheduleWakeup` is clamped to ≤3600s, so you cannot sleep
  straight to a reset hours away — hop under an hour and let the next iteration
  re-check. (Headless has no such cap: `--at` schedules the exact reset time.)

### 6. Report
End your turn with a short human status line, e.g.:
`Increment 3/12 done ✓ · session 34% / week 61% · armed next run +2min`
or
`Session 88% used — no work this run, armed restart for 22:02 (reset).`

Then, as the **very last line**, emit one machine-readable status line that the
launcher parses for the monitoring dashboard. Pipe-delimited, `key=value`, no
spaces around `|`, exactly this shape:

```
STATUS|phase=<active|on_hold|complete|not_ready|blocked>|session=<int>|week=<int>|reset=<YYYY-MM-DD HH:MM or ->|note=<short text>
```

- `phase`: `active` = did an increment, budget healthy, next run armed soon;
  `on_hold` = budget exhausted, next run armed at the reset; `complete` = goal
  done, nothing armed; `not_ready` = GOAL.md still a template; `blocked` =
  increment couldn't be completed/verified this run.
- `session`/`week`: integer percent from `/usage` (omit the `%`).
- `reset`: the reset time you armed for when `on_hold`, else `-`.
- `note`: one short phrase, no `|` characters.

Example: `STATUS|phase=on_hold|session=88|week=61|reset=2026-07-15 22:00|note=session limit reached, restart armed`

## Charter-mode protocol (Plane backlog)

Same engine — one verified increment per run — but the work comes from **Plane**
and the mandate from **`CHARTER.md`** (in your cwd). The Plane API key is in the
env as **`$PLANE_API_KEY`** (never print it). API shape: see `/generate-plan`'s
quick reference (same base, header, endpoints). You are the **Executor** (Dev);
`/generate-plan` is the Planner (PO).

### C1. Load & guard
- Read `.hustle/config` (`HUSTLE_MODE`, `HUSTLE_MODEL` = your fallback model).
- Read `CHARTER.md`: Plane url + slug, **project mapping** (`project_id → repo`),
  **git workflow** (`direct-main | branch-mr`), the **quality bar** for Done, the
  **resume guard** `N` (default 2), hard **constraints**, budget thresholds.
- If `CHARTER.md` shows `STATUS: COMPLETE` or `$PLANE_API_KEY` is empty → stop
  (complete / blocked). Never act outside the mapped projects, and honour every
  hard constraint on every action.

### C2. Recover before pulling (one-in-flight invariant)
- Look for an issue in a **mapped** project that is **In Progress (started) AND
  assigned to you** (your user id from `GET /users/me/`). If one exists, that is
  your target — resume it. Count your prior "picked up" comments on it = the
  **resume count**.
- If resume count ≥ `N` → the item is mis-sized: label it `too-big`, comment the
  actual effort/what's left, move it **back to Todo** and unassign yourself, then
  hand off to the Planner (set marker `planner`, §C7) and stop — don't burn more
  budget on it.

### C3. Budget pre-check (before claiming anything new)
- Only if you have **no** recovered item: parse `/usage` (as in Plan-mode §5). If
  `session ≥ pause threshold` (or `week ≥ weekly threshold`) → claim **nothing**,
  arm the next run at the reset, set marker `executor`, and exit. An item is only
  started with real headroom, so it can actually finish this session.

### C4. Claim the next item
- Pick the highest-`priority` **Todo (unstarted)** issue across mapped projects
  (tie-break by lowest `sequence_id`). No Todo anywhere → set marker `planner`
  (time to plan), arm next run, exit.
- **Claim = lock:** PATCH it to **In Progress (started)**, add yourself to
  `assignees`, and comment "picked up, run &lt;UTC ts&gt;". Read its `model:` label
  only to inform the *next* marker, not this run.

### C5. Do exactly one increment
- Resolve the repo from the mapping (the issue's `project_id` → repo path). Work
  **only** in that repo. Do the smallest shippable step that satisfies the issue's
  acceptance criteria.
- Follow the repo's own conventions (its `CLAUDE.md`).

### C6. Verify, then commit & close
- Verify to the charter's quality bar (tests / boot checks — prefer running them).
  If it fails and you can't fix it this run: comment the blocker, move the issue
  **back to Todo** (hard external block → also label `blocked`), do **not** mark
  Done. Then §C7.
- If verified: commit per the charter git workflow —
  - `direct-main`: one commit on `main` whose message references the issue
    (`&lt;IDENTIFIER&gt;-&lt;sequence_id&gt;: …`), then `git push origin main`.
  - `branch-mr`: branch off `dev`, push, open an MR into `dev`.
  Then PATCH the issue to **Done (completed)** and comment a one-line summary.

### C7. Hand off (marker) + budget/arm
- **Marker** `.hustle/next-phase`: peek the next highest-priority Todo across
  mapped projects → `executor:<model from its model: label>` (bare `executor` if
  unknown); no Todo left → `planner`. E.g. `printf 'executor:opus' > .hustle/next-phase`.
- **Budget + arm:** identical to Plan-mode §5 (headless → `hustle-scheduler.sh
  arm …`; `/loop` → recommend a `ScheduleWakeup`).

### C8. Report
Human status line, then the same machine `STATUS|…` line as Plan-mode §6:
`phase=active` when you closed an item, `blocked` when an item couldn't be
verified, `on_hold` when budget-paused, `complete` when CHARTER.md is COMPLETE.

## Notes
- Keep each iteration cheap and focused; the loop's value is consistency, not
  heroics per turn.
- If `claude -p "/usage"` errors or returns nothing, assume budget is fine and
  arm a short next run (fail open, don't stall the goal).
- Never fabricate progress. In plan mode a checked box, in charter mode a Done
  issue, must correspond to verified work. Never move a human's issues except the
  one you claimed.
