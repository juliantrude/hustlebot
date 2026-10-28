#!/usr/bin/env bash
# One run of the advance-goal chain. Fired by the one-shot scheduler
# (hustle-scheduler.sh) or started manually to kick the chain off.
#
# Lifecycle:
#   1. take the run lock (portable mkdir lock — no flock on macOS)
#   2. disarm the one-shot that fired us (self-deleting "cron")
#   3. make sure the monitor dashboard is up
#   3b. at an open Haltepunkt (.hustle/watch.json): no run, hand over to the
#       watcher (hustle-watch.sh), which restarts the chain on an answer
#   4. run ONE headless increment of /advance-goal
#      -> the skill itself arms the NEXT one-shot before exiting
#   5. publish status.json for the monitor
#   6. safety net: stop if goal complete; re-arm +15m if nothing is scheduled
#
# All state lives in <project>/.hustle/ + <project>/GOAL.md; a missed or
# crashed run is harmless — the next start resumes from GOAL.md.
set -uo pipefail

HUSTLE_HOME="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$HUSTLE_HOME/config"

# Secrets (e.g. PLANE_API_KEY for charter mode) live apart from config: the
# monitor parses and can surface `config`, so anything sensitive goes in a 0600
# `secrets` file that is only sourced into the run env, never displayed. The file
# uses `export` so the headless `claude -p` subprocess inherits the key.
# shellcheck disable=SC1091
[ -f "$HUSTLE_HOME/secrets" ] && source "$HUSTLE_HOME/secrets"

BIN="$HUSTLE_HOME/bin"
LOG="$HUSTLE_HOME/run.log"
STATUS_JSON="$HUSTLE_HOME/status.json"
LOCKDIR="$HUSTLE_HOME/lock"
RUN_OUT="$HUSTLE_HOME/run.out"

log(){ echo "$(date '+%Y-%m-%dT%H:%M:%S%z') $*" >> "$LOG"; }

# --- per-increment model (plan mode) -----------------------------------------
# A GOAL.md increment may carry `[opus]`, `[sonnet]` or `[haiku]`. The run is
# started with the model of the FIRST unchecked increment under `## Plan`, so a
# cheap model builds the plumbing and the strong one does the judgement calls,
# without anyone flipping HUSTLE_MODEL by hand. No tag -> HUSTLE_MODEL. Which
# full id a tag means is config (HUSTLE_MODEL_OPUS/_SONNET/_HAIKU), so a model
# bump is a config change, not a GOAL.md rewrite. The skill itself still checks
# whether it was started with the right model (GOAL.md decides what to do on a
# mismatch); the wrapper only reads the tag.
pick_plan_model() { # [goal-file] -> prints the model id
  local goal="${1:-$HUSTLE_PROJECT/GOAL.md}" tag=""
  [ -f "$goal" ] && tag="$(python3 - "$goal" <<'PY'
import re, sys
plan, block = False, None
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    if line.startswith("## "):
        if plan:
            break
        plan = line.strip() == "## Plan"
        continue
    if not plan:
        continue
    if re.match(r"\s*- \[[ xX]\]", line):
        if block is not None:
            break
        if re.match(r"\s*- \[ \]", line):
            block = line
    elif block is not None:
        if line.strip() == "" or line.startswith("#"):
            break
        block += line
m = re.search(r"`?\[(opus|sonnet|haiku)\]`?", block or "")
print(m.group(1) if m else "")
PY
)"
  case "$tag" in
    opus)   echo "${HUSTLE_MODEL_OPUS:-claude-opus-5-5}" ;;
    sonnet) echo "${HUSTLE_MODEL_SONNET:-claude-sonnet-5-5}" ;;
    haiku)  echo "${HUSTLE_MODEL_HAIKU:-claude-haiku-4-5-20251001}" ;;
    *)      echo "${HUSTLE_MODEL:-sonnet}" ;;
  esac
}

# `--pick-model [goal-file]` prints the model the next run would use and exits
# before any side effect (lock, scheduler, monitor) -- a dry run for GOAL.md.
if [ "${1:-}" = "--pick-model" ]; then
  pick_plan_model "${2:-}"; exit 0
fi

# --- peer chains ------------------------------------------------------------
# Every chain on this machine draws on the same Claude subscription. Two of
# them running at once burn the budget twice as fast and race each other into
# the session limit, so a run yields to a peer that is already working.
#
# Peers are discovered from their own files rather than a shared registry: a
# registry outlives the crashes it is supposed to describe, and stale entries
# are exactly the failure we are trying to avoid.
#
# Two generations are recognised. Chains installed before the hustlebot rename
# use different unit names, a different script path and a different state file,
# and an unrecognised peer is worse than no check at all -- it is a chain that
# keeps working while this one assumes the machine is idle.
#
#   generation   session unit          session script                         state file
#   hustlebot    hustle-<slug>         <proj>/.hustle/bin/hustle-session.sh   .hustle/status.json
#   legacy       advance-goal          <proj>/scripts/advance-goal-session.sh .advance-goal-status.json
#
# list-unit-files, not list-units: an idle chain's oneshot unit is not loaded
# between runs, so list-units only ever reports chains that happen to be awake.
# Monitor units are skipped -- their ExecStart is the interpreter, not a project.
peer_chains() {
  systemctl --user list-unit-files 'hustle-*.service' 'advance-goal*.service' \
            --no-legend --no-pager 2>/dev/null \
    | awk '{print $1}' | grep -vE '^(hustle-monitor-|advance-goal-monitor)' | while read -r unit; do
        local exec_path root
        exec_path="$(systemctl --user show "$unit" -p ExecStart --value 2>/dev/null \
                     | grep -oE 'path=[^ ;]+' | head -1 | cut -d= -f2-)"
        case "$exec_path" in
          */.hustle/bin/hustle-session.sh)
            root="${exec_path%/.hustle/bin/hustle-session.sh}"
            printf '%s\t%s\t%s\t%s\n' "$unit" "$root" \
                   "$root/.hustle/status.json" "$root/.hustle/run.pid" ;;
          */scripts/advance-goal-session.sh)
            root="${exec_path%/scripts/advance-goal-session.sh}"
            printf '%s\t%s\t%s\t%s\n' "$unit" "$root" \
                   "$root/.advance-goal-status.json" "$root/.advance-goal-run.pid" ;;
        esac
      done | grep -v "	${HUSTLE_PROJECT}	"
}

peer_phase() {
  sed -n 's/.*"phase"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1
}

# A run is in progress if its pid file points at a live process, or -- for any
# systemd-driven chain, whatever its layout -- if the session unit is active.
# The unit check is what makes this work for generations that have no pid file.
peer_running() {
  local pidfile="$1" unit="$2" pid
  pid="$(cat "$pidfile" 2>/dev/null)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then return 0; fi
  [ "$(systemctl --user is-active "$unit" 2>/dev/null)" = "active" ]
}

# Retire what a finished chain left behind: its monitor keeps holding a port
# long after the work is done (that is how a stale dashboard ends up being the
# one you are looking at).
retire_peer() {
  local unit="$1" root="$2" monitor_unit
  case "$unit" in
    hustle-*)       monitor_unit="hustle-monitor-${unit#hustle-}" ;;
    advance-goal.*) monitor_unit="advance-goal-monitor.service" ;;
    *)              monitor_unit="" ;;
  esac
  [ -n "$monitor_unit" ] && systemctl --user stop "$monitor_unit" >/dev/null 2>&1
  # A pid file records the pid at launch and goes stale the moment systemd
  # restarts the unit, so match on the path the process was started from too.
  # Both patterns are anchored inside that project, so they cannot hit anyone else.
  pkill -f "^python3 ${root}/.hustle/bin/hustle-monitor\.py" 2>/dev/null
  pkill -f "^python3 ${root}/scripts/advance-goal-monitor\.py" 2>/dev/null
  return 0
}

# 0 = clear to run, 3 = a peer is actively working. Units whose project is gone
# are reported, never removed: an unmounted disk looks exactly like a deleted
# project, and an unattended run must not delete someone else's setup over it.
check_peers() {
  local unit root state pidfile blocked=0
  while IFS="$(printf '\t')" read -r unit root state pidfile; do
    [ -n "$root" ] || continue
    if [ ! -d "$root" ]; then
      log "[peer] stale unit ${unit} -> ${root} (project is gone; remove with: hustle-setup.sh --uninstall ${root})"
      continue
    fi
    if peer_running "$pidfile" "$unit"; then
      log "[peer] active chain in ${root} — yielding"
      blocked=1
    elif [ "$(peer_phase "$state")" = "complete" ]; then
      log "[peer] finished chain in ${root} — retiring its leftovers"
      retire_peer "$unit" "$root"
    fi
  done < <(peer_chains)
  [ "$blocked" -eq 0 ] || return 3
}

# `--check-peers` lets the interactive /hustle skill ask the same question
# before igniting, where there is a human to answer it.
if [ "${1:-}" = "--check-peers" ]; then
  if check_peers; then echo "clear"; exit 0; else echo "blocked"; exit 3; fi
fi

# --- portable single-flight lock (mkdir is atomic everywhere) ---------------
take_lock() {
  if mkdir "$LOCKDIR" 2>/dev/null; then echo $$ > "$LOCKDIR/pid"; return 0; fi
  local holder; holder="$(cat "$LOCKDIR/pid" 2>/dev/null || true)"
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    return 1                       # a live run holds the lock
  fi
  rm -rf "$LOCKDIR"                # stale lock from a dead run
  mkdir "$LOCKDIR" 2>/dev/null && echo $$ > "$LOCKDIR/pid"
}
release_lock(){ rm -f "$HUSTLE_HOME/run.pid"; rm -rf "$LOCKDIR"; }

if ! take_lock; then log "[skip] previous run still active"; exit 0; fi
trap release_lock EXIT
echo $$ > "$HUSTLE_HOME/run.pid"

# --- 2. self-delete the one-shot that launched us ---------------------------
"$BIN/hustle-scheduler.sh" disarm >/dev/null 2>&1 || true

# --- 2b. yield to a peer chain ----------------------------------------------
# Blocked runs are parked, not cancelled: the peer is usually minutes from
# finishing, and a chain that quietly died because another one happened to be
# busy is worse than one that waits.
if ! check_peers; then
  log "[peer] parking this run — retrying in 5 min"
  "$BIN/hustle-scheduler.sh" arm --in 300 >> "$LOG" 2>&1 || log "[error] peer re-arm failed"
  exit 0
fi

# --- 3. ensure the monitor is running ---------------------------------------
# Linux: the monitor MUST run as its own systemd unit. A nohup'ed child of this
# script lives in the oneshot service's cgroup and gets killed the moment the
# run ends (KillMode=control-group) — it would only ever live during a run.
# macOS: no cgroups; a detached nohup daemon survives fine.
if [ "$(uname -s)" != "Darwin" ] && command -v systemctl >/dev/null 2>&1; then
  systemctl --user start "hustle-monitor-${HUSTLE_SLUG}.service" 2>/dev/null \
    || log "[monitor] could not start hustle-monitor-${HUSTLE_SLUG}.service"
else
  mpid="$(cat "$HUSTLE_HOME/monitor.pid" 2>/dev/null || true)"
  if [ -z "$mpid" ] || ! kill -0 "$mpid" 2>/dev/null; then
    nohup python3 "$BIN/hustle-monitor.py" >> "$HUSTLE_HOME/monitor.log" 2>&1 &
    echo $! > "$HUSTLE_HOME/monitor.pid"
    log "[monitor] started (pid $!)"
  fi
fi

cd "$HUSTLE_PROJECT" || { log "[error] cd $HUSTLE_PROJECT failed"; exit 1; }

# --- 3b. Haltepunkt: wait without a model -----------------------------------
# When the skill stops at a GOAL.md Haltepunkt it writes .hustle/watch.json and
# arms nothing. The waiting -- often days -- is done by hustle-watch.sh in its
# own unit, polling Memos with plain HTTP; a model run every 15 min would only
# find "no answer yet" at full price. The watcher marks watch.json "triggered"
# when an answer arrives and starts this script. A run that finds watch.json
# WITHOUT "triggered" (a stray timer, a manual start) does no work and hands
# back to the watcher.
WATCH_JSON="$HUSTLE_HOME/watch.json"
WATCH_UNIT="hustle-watch-${HUSTLE_SLUG}.service"
watch_triggered(){
  python3 -c 'import json,sys;sys.exit(0 if json.load(open(sys.argv[1])).get("triggered") else 1)' \
    "$WATCH_JSON" 2>/dev/null
}
start_watcher(){
  systemctl --user start "$WATCH_UNIT" 2>/dev/null || log "[watch] could not start ${WATCH_UNIT}"
}
if [ -f "$WATCH_JSON" ] && ! watch_triggered; then
  log "[watch] Haltepunkt open, no new answer — no run, the watcher keeps waiting"
  start_watcher
  exit 0
fi

# --- 4. one headless run: pick skill + model --------------------------------
# plan mode   -> always the Executor (/advance-goal), unchanged.
# charter mode-> a `next-phase` marker written by the PREVIOUS run routes between
#   the Planner (/generate-plan, strong model) and the Executor (/advance-goal,
#   model taken per-issue from the marker). The wrapper only maps marker -> flags;
#   the skills do the deciding, because they are the ones with Plane access.
#   Absent marker (first run / crash) -> Planner, which bootstraps the backlog.
perm_flag=""
[ "${HUSTLE_SKIP_PERMISSIONS:-true}" = "true" ] && perm_flag="--dangerously-skip-permissions"

CMD="/advance-goal"
RUN_MODEL="${HUSTLE_MODEL:-sonnet}"
[ "${HUSTLE_MODE:-plan}" = "plan" ] && RUN_MODEL="$(pick_plan_model)"
if [ "${HUSTLE_MODE:-plan}" = "charter" ]; then
  phase=""
  [ -f "$HUSTLE_HOME/next-phase" ] && phase="$(tr -d '[:space:]' < "$HUSTLE_HOME/next-phase" 2>/dev/null)"
  case "$phase" in
    ""|planner)   CMD="/generate-plan"; RUN_MODEL="${HUSTLE_PLAN_MODEL:-opus}" ;;
    executor)     CMD="/advance-goal";  RUN_MODEL="${HUSTLE_MODEL:-sonnet}" ;;
    executor:*)   CMD="/advance-goal";  RUN_MODEL="${phase#executor:}" ;;
    *)            log "[warn] unrecognised next-phase '$phase' — defaulting to planner"
                  CMD="/generate-plan"; RUN_MODEL="${HUSTLE_PLAN_MODEL:-opus}" ;;
  esac
fi
[ -n "$RUN_MODEL" ] || RUN_MODEL="${HUSTLE_MODEL:-sonnet}"
# Judging a human answer at a Haltepunkt decides whether days of work go one
# way or the other -- that is the strong model's job, whatever the next
# increment's tag says.
if [ -f "$WATCH_JSON" ] && watch_triggered; then
  RUN_MODEL="${HUSTLE_MODEL_OPUS:-claude-opus-5-5}"
fi

log "[start] ${CMD} run (model=${RUN_MODEL} effort=${HUSTLE_EFFORT:-high} mode=${HUSTLE_MODE:-plan})"
claude -p "$CMD" --model "$RUN_MODEL" --effort "${HUSTLE_EFFORT:-high}" \
  $perm_flag > "$RUN_OUT" 2>&1
rc=$?
cat "$RUN_OUT" >> "$LOG"
log "[end] exit=${rc}"

# --- 5. publish status for the dashboard ------------------------------------
status_field(){ echo "$1" | grep -oE "$2=[^|]*" | head -1 | cut -d= -f2- ; }
SLINE="$(grep -a 'STATUS|' "$RUN_OUT" | tail -1 || true)"
next_fire(){ "$BIN/hustle-scheduler.sh" next 2>/dev/null; }
write_status(){ # phase note
  local phase="$1" note="$2"
  note="${note//\\/}"; note="${note//\"/\'}"
  cat > "$STATUS_JSON" <<EOF
{
  "updated": "$(date '+%Y-%m-%dT%H:%M:%S%z')",
  "phase": "${phase:-unknown}",
  "session_pct": "$(status_field "$SLINE" session)",
  "week_pct": "$(status_field "$SLINE" week)",
  "reset": "$(status_field "$SLINE" reset)",
  "next_fire": "$(next_fire)",
  "last_exit": ${rc:-0},
  "note": "${note}"
}
EOF
}

# --- 6a. goal finished -> stop the chain ------------------------------------
# The terminal sentinel is `STATUS: COMPLETE` in the mode's source-of-truth file:
# GOAL.md in plan mode, CHARTER.md in charter mode (written by the Planner once
# the feature-freeze date has passed and the Plane backlog is drained).
SENTINEL_FILE="$HUSTLE_PROJECT/GOAL.md"
[ "${HUSTLE_MODE:-plan}" = "charter" ] && SENTINEL_FILE="$HUSTLE_PROJECT/CHARTER.md"
if grep -qE '^STATUS: *COMPLETE' "$SENTINEL_FILE" 2>/dev/null; then
  log "[done] ${SENTINEL_FILE##*/} COMPLETE — chain stops"
  write_status "complete" "goal reached"
  exit 0
fi

# --- 6a'. Haltepunkt (still) open -> hand over to the watcher ----------------
# watch.json without "triggered" after a run means: the skill has just stopped
# at a Haltepunkt, or it judged the answer and the Haltepunkt stays. Either way
# nothing is to run until the next answer, so no timer -- not even the +15m
# fallback below -- only the watcher. With "triggered" still set the run never
# got to judge it (limit, crash): fall through, the normal retry handles it.
if [ -f "$WATCH_JSON" ] && ! watch_triggered; then
  "$BIN/hustle-scheduler.sh" disarm >/dev/null 2>&1 || true
  log "[watch] Haltepunkt open — chain sleeps, the watcher waits for an answer"
  start_watcher
  write_status "blocked" "Haltepunkt — waiting for an answer in Memos (watcher)"
  exit 0
fi

# --- 6b. self-heal with usage-limit backoff ---------------------------------
# A run that armed nothing either crashed or hit a usage limit. The in-skill
# /usage budget check cannot see the limit on every plan/CLI version (headless
# `/usage` does not reliably emit the "week … % used · resets <date>" report),
# so we must NOT assume the skill parked itself. A flat +15m retry would hammer
# the API every 15 minutes for the days a *weekly* limit lasts.
#
# Instead: treat "run produced its STATUS| line" as success (reset the counter),
# and "armed nothing" as a failure that backs off exponentially — 15m, 30m, 1h,
# 2h, capped at 4h. That rides out a weekly limit with a handful of cheap
# failing probes and resumes on its own within the cap after the reset, without
# needing to parse the limit at all. If the run output happens to name a machine
# -readable reset timestamp, we arm exactly for it instead.
FAILFILE="$HUSTLE_HOME/fail-count"
[ -n "$SLINE" ] && echo 0 > "$FAILFILE"      # skill completed its protocol

if [ -z "$(next_fire)" ]; then
  fc=$(( $(cat "$FAILFILE" 2>/dev/null || echo 0) + 1 )); echo "$fc" > "$FAILFILE"
  backoff=$(( 900 * (1 << (fc - 1)) )); [ "$backoff" -gt 14400 ] && backoff=14400
  reset_ts="$(grep -aoiE 'reset[^0-9]{0,14}[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}' "$RUN_OUT" 2>/dev/null \
              | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}' | head -1 | tr 'T' ' ')"
  if [ -n "$reset_ts" ] && "$BIN/hustle-scheduler.sh" arm --at "$reset_ts" >> "$LOG" 2>&1; then
    log "[safety-net] run armed nothing (fail #$fc) — armed for detected reset ${reset_ts}"
  else
    log "[safety-net] run armed nothing (fail #$fc) — backoff +$((backoff/60))m"
    "$BIN/hustle-scheduler.sh" arm --in "$backoff" >> "$LOG" 2>&1 || log "[error] fallback arm failed"
  fi
fi

write_status "$(status_field "$SLINE" phase)" "$(status_field "$SLINE" note)"
exit 0
