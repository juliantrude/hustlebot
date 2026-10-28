#!/usr/bin/env bash
# Haltepunkt-Watcher: waits for a human answer in Memos without running a model.
#
# A GOAL.md Haltepunkt can stay open for days. A model run every 15 min would
# only find "no answer yet", at full price. So when the skill stops at a
# Haltepunkt it writes .hustle/watch.json, the wrapper hands over to this
# script (its own systemd unit), and this script polls the named memos with
# plain HTTP. The moment a comment from someone other than the loop's own
# Memos account appears, it marks watch.json "triggered" and starts the chain.
# The run that follows (always Opus, see hustle-session.sh) judges whether the
# answer is the one the Haltepunkt waits for:
#   yes -> the skill deletes watch.json and the chain carries on;
#   no  -> the skill removes "triggered", the wrapper starts this watcher again.
#
# watch.json (written by the skill, extended here):
#   {"haltepunkt": "H1", "memos": ["memos/<id>", ...],
#    "since": "<RFC3339>",            # ignore comments up to here
#    "expect": "<what answer lifts the Haltepunkt>",
#    "seen_until": "<RFC3339>",       # set here: newest comment already handed over
#    "triggered": "<RFC3339>"}        # set here, removed by the skill
#
# The Memos token is read at runtime from the memos MCP entry in ~/.claude.json
# -- the same one the loop uses -- and never printed or written anywhere.
#
#   hustle-watch.sh           poll until an answer arrives (the unit runs this)
#   hustle-watch.sh --check   one poll, print the verdict, change nothing
set -uo pipefail

HUSTLE_HOME="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$HUSTLE_HOME/config"
WATCH_JSON="$HUSTLE_HOME/watch.json"
LOG="$HUSTLE_HOME/run.log"
INTERVAL="${HUSTLE_WATCH_INTERVAL:-120}"

log(){ echo "$(date '+%Y-%m-%dT%H:%M:%S%z') [watch] $*" >> "$LOG"; }

# One poll. Prints one line and exits:
#   0 "new <n> <newest>"  -- n new foreign comments, newest createTime
#   1 "none"              -- nothing new
#   2 "error <reason>"    -- could not ask (network, auth, bad watch.json)
# With mark=1 a hit is recorded in watch.json (seen_until + triggered).
poll() { # mark
  python3 - "$WATCH_JSON" "${HUSTLE_MEMOS_URL:-}" "$1" <<'PY'
import json, os, sys, urllib.request, datetime

watch_path, base, mark = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
try:
    watch = json.load(open(watch_path))
    memos = watch["memos"]
except Exception as e:
    print(f"error watch.json unreadable ({type(e).__name__})"); sys.exit(2)

try:
    mcp = json.load(open(os.path.expanduser("~/.claude.json")))["mcpServers"]["memos"]
    auth = mcp["headers"]["Authorization"]
    base = base or mcp["url"].rsplit("/mcp", 1)[0]
except Exception:
    print("error no memos MCP entry in ~/.claude.json"); sys.exit(2)

def get(path):
    req = urllib.request.Request(base + path, headers={"Authorization": auth})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))

try:
    who = get("/api/v1/auth/me")
    me = (who.get("user") or who)["name"]
    cutoff = max(ts(t) for t in (watch.get("since"), watch.get("seen_until")) if t)
    new = []
    for memo in memos:
        for c in get(f"/api/v1/{memo}/comments").get("memos", []):
            if c.get("creator") != me and ts(c["createTime"]) > cutoff:
                new.append(c["createTime"])
except Exception as e:
    print(f"error {type(e).__name__}: {str(e)[:80]}"); sys.exit(2)

if not new:
    print("none"); sys.exit(1)
newest = max(new, key=ts)
if mark:
    watch["seen_until"] = newest
    watch["triggered"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    tmp = watch_path + ".tmp"
    json.dump(watch, open(tmp, "w"), indent=2, ensure_ascii=False)
    os.replace(tmp, watch_path)
print(f"new {len(new)} {newest}")
PY
}

if [ "${1:-}" = "--check" ]; then
  [ -f "$WATCH_JSON" ] || { echo "no watch.json — no Haltepunkt open"; exit 0; }
  poll 0; exit $?
fi

[ -f "$WATCH_JSON" ] || { log "no watch.json — nothing to wait for"; exit 0; }
# Started with an answer already handed over but never judged (the run died,
# or the machine rebooted before it): hand it over again instead of waiting.
if python3 -c 'import json,sys;sys.exit(0 if json.load(open(sys.argv[1])).get("triggered") else 1)' "$WATCH_JSON" 2>/dev/null; then
  log "answer from before is still unjudged — starting the chain"
  systemctl --user start --no-block "hustle-${HUSTLE_SLUG}.service" \
    || log "[error] could not start hustle-${HUSTLE_SLUG}.service"
  exit 0
fi
log "waiting for an answer ($(python3 -c 'import json,sys;w=json.load(open(sys.argv[1]));print(w.get("haltepunkt","?"), " ".join(w.get("memos",[])))' "$WATCH_JSON" 2>/dev/null)), every ${INTERVAL}s"

last_error=""
while [ -f "$WATCH_JSON" ]; do
  out="$(poll 1)"; rc=$?
  case "$rc" in
    0)
      log "answer arrived (${out#new }) — starting the chain"
      systemctl --user start --no-block "hustle-${HUSTLE_SLUG}.service" \
        || log "[error] could not start hustle-${HUSTLE_SLUG}.service"
      exit 0 ;;
    2)
      # Log a failure once, not every two minutes: Memos is down for hours at
      # most, and a log full of identical lines hides the one that matters.
      [ "$out" = "$last_error" ] || log "poll failed: ${out#error } — retrying"
      last_error="$out" ;;
    *)
      [ -n "$last_error" ] && log "polling works again"
      last_error="" ;;
  esac
  sleep "$INTERVAL"
done
log "watch.json gone — Haltepunkt lifted elsewhere, watcher stops"
