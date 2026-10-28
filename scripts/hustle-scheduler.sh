#!/usr/bin/env bash
# Platform-abstract one-shot scheduler for the advance-goal loop.
# Linux backend: a transient-named systemd user timer (survives reboot, catches
#   up missed fires via Persistent=true).
# macOS backend: a launchd LaunchAgent plist with StartCalendarInterval
#   (fires missed times after sleep/wake; runs missed while powered off are
#   lost — the safety net in hustle-session.sh re-arms on the next manual start).
#
# Usage:
#   hustle-scheduler.sh arm --in <seconds>
#   hustle-scheduler.sh arm --at "YYYY-MM-DD HH:MM[:SS]"
#   hustle-scheduler.sh disarm          # remove the pending one-shot (idempotent)
#   hustle-scheduler.sh next            # print next fire time, or nothing if none
#
# All date math goes through python3 (GNU `date -d` doesn't exist on macOS).
set -uo pipefail

HUSTLE_HOME="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$HUSTLE_HOME/config"

OS="$(uname -s)"
UNIT_DIR="${HOME}/.config/systemd/user"
SERVICE="hustle-${HUSTLE_SLUG}.service"
TIMER="hustle-${HUSTLE_SLUG}-next.timer"
LABEL="com.hustle.${HUSTLE_SLUG}.next"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

ts_normalize() { # --in N | --at "ts"  ->  "YYYY-MM-DD HH:MM:SS" (local), or fail
  python3 - "$1" "$2" <<'PY'
import sys, datetime
mode, val = sys.argv[1], sys.argv[2]
now = datetime.datetime.now()
if mode == "--in":
    t = now + datetime.timedelta(seconds=int(val))
else:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            t = datetime.datetime.strptime(val, fmt); break
        except ValueError:
            t = None
    if t is None:
        sys.exit("cannot parse timestamp: " + val)
    if t <= now:
        sys.exit("timestamp is in the past: " + val)
print(t.strftime("%Y-%m-%d %H:%M:%S"))
PY
}

arm_linux() {
  local when="$1"
  mkdir -p "$UNIT_DIR"
  cat > "${UNIT_DIR}/${TIMER}" <<EOF
[Unit]
Description=One-shot restart of advance-goal (${HUSTLE_SLUG}), armed $(date)

[Timer]
Unit=${SERVICE}
OnCalendar=${when}
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl --user daemon-reload
  # `enable --now` only starts a timer that is stopped. A timer that already
  # fired stays "active" with no future elapse, so --now is a no-op and the
  # OnCalendar we just wrote is silently ignored -- the chain then looks armed
  # while nothing is scheduled. Restart unconditionally to load the new time.
  if ! systemctl --user enable "$TIMER" >/dev/null 2>&1; then
    echo "error: failed to enable ${TIMER}" >&2; return 1
  fi
  if ! systemctl --user restart "$TIMER" >/dev/null 2>&1; then
    echo "error: failed to restart ${TIMER}" >&2; return 1
  fi
  local next
  next="$(systemctl --user show "$TIMER" --property=NextElapseUSecRealtime --value 2>/dev/null)"
  if [ -z "$next" ] || [ "$next" = "n/a" ]; then
    echo "error: ${TIMER} enabled but no future fire scheduled" >&2; return 1
  fi
  echo "armed ${TIMER} -> ${when} (fires: ${next})"
}

arm_darwin() {
  local when="$1"
  mkdir -p "${HOME}/Library/LaunchAgents"
  python3 - "$PLIST" "$LABEL" "$HUSTLE_HOME/bin/hustle-session.sh" "$when" <<'PY'
import sys, plistlib, datetime
plist, label, session, when = sys.argv[1:5]
t = datetime.datetime.strptime(when, "%Y-%m-%d %H:%M:%S")
data = {
    "Label": label,
    "ProgramArguments": ["/bin/bash", session],
    "StartCalendarInterval": {
        "Year": t.year, "Month": t.month, "Day": t.day,
        "Hour": t.hour, "Minute": t.minute,
    },
    "RunAtLoad": False,
}
with open(plist, "wb") as f:
    plistlib.dump(data, f)
PY
  launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true
  if ! launchctl bootstrap "gui/$(id -u)" "$PLIST" >/dev/null 2>&1; then
    # older macOS fallback
    launchctl load "$PLIST" >/dev/null 2>&1 || { echo "error: launchctl load failed" >&2; return 1; }
  fi
  echo "armed ${LABEL} -> ${when}"
}

disarm_linux() {
  systemctl --user disable --now "$TIMER" >/dev/null 2>&1 || true
  rm -f "${UNIT_DIR}/${TIMER}"
  systemctl --user daemon-reload
}

disarm_darwin() {
  launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 \
    || launchctl unload "$PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST"
}

next_linux() {
  local next
  next="$(systemctl --user show "$TIMER" --property=NextElapseUSecRealtime --value 2>/dev/null)"
  if [ -n "$next" ] && [ "$next" != "n/a" ]; then echo "$next"; fi
}

next_darwin() {
  [ -f "$PLIST" ] || return 0
  python3 - "$PLIST" <<'PY'
import sys, plistlib, datetime
with open(sys.argv[1], "rb") as f:
    d = plistlib.load(f)
c = d.get("StartCalendarInterval", {})
try:
    t = datetime.datetime(c["Year"], c["Month"], c["Day"], c["Hour"], c["Minute"])
except KeyError:
    sys.exit(0)
if t > datetime.datetime.now():
    print(t.strftime("%a %Y-%m-%d %H:%M:%S"))
PY
}

cmd="${1:-}"
case "$cmd" in
  arm)
    mode="${2:-}"; val="${3:-}"
    [ "$mode" = "--in" ] || [ "$mode" = "--at" ] || { echo "usage: $0 arm --in <sec> | arm --at \"ts\"" >&2; exit 2; }
    when="$(ts_normalize "$mode" "$val")" || exit 2  # reason already on stderr
    if [ "$OS" = "Darwin" ]; then arm_darwin "$when"; else arm_linux "$when"; fi
    ;;
  disarm)
    if [ "$OS" = "Darwin" ]; then disarm_darwin; else disarm_linux; fi
    ;;
  next)
    if [ "$OS" = "Darwin" ]; then next_darwin; else next_linux; fi
    ;;
  *)
    echo "usage: $0 arm --in <sec> | arm --at \"ts\" | disarm | next" >&2; exit 2
    ;;
esac
