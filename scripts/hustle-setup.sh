#!/usr/bin/env bash
# Install the hustle loop into a project.
#
#   hustle-setup.sh [--dev] [project-dir]        (default: current directory)
#   hustle-setup.sh --uninstall [project-dir]    remove the scheduling units
#
# Creates <project>/.hustle/ with the runtime scripts, a config file and
# (on Linux) the systemd user service. Idempotent: re-running refreshes the
# scripts but leaves an existing config untouched.
#
# --dev symlinks .hustle/bin/* at the plugin sources instead of copying them,
# so edits to the scripts are visible on the running dashboard without
# re-running setup.
set -euo pipefail

DEV_MODE=false
UNINSTALL=false
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --dev) DEV_MODE=true ;;
    --uninstall) UNINSTALL=true ;;
    *) ARGS+=("$arg") ;;
  esac
done

if [ "$UNINSTALL" = true ]; then
  RAW="${ARGS[0]:-$PWD}"
  PROJECT="$(cd "$RAW" 2>/dev/null && pwd || echo "${RAW%/}")"
else
  PROJECT="$(cd "${ARGS[0]:-$PWD}" && pwd)"
fi
PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HUSTLE_HOME="$PROJECT/.hustle"
SLUG="$(basename "$PROJECT" | tr -cs 'a-zA-Z0-9' '-' | sed 's/^-//;s/-$//')"
OS="$(uname -s)"

# --- uninstall --------------------------------------------------------------
# The missing counterpart to installation. Without it every throwaway project
# -- including the scratch directories used to verify this very script --
# leaves systemd units behind that point at a path which no longer exists.
# Project files are left alone: this removes the scheduling, not the work.
if [ "$UNINSTALL" = true ]; then
  UNIT_DIR="$HOME/.config/systemd/user"
  removed=0
  for unit in "hustle-${SLUG}.service" "hustle-${SLUG}-next.timer" "hustle-monitor-${SLUG}.service"; do
    if [ -e "$UNIT_DIR/$unit" ] || systemctl --user cat "$unit" >/dev/null 2>&1; then
      systemctl --user disable --now "$unit" >/dev/null 2>&1 || true
      rm -f "$UNIT_DIR/$unit"
      echo "removed $unit"
      removed=$((removed + 1))
    fi
  done
  [ -d "$PROJECT/.hustle" ] && pkill -f "^python3 ${PROJECT}/.hustle/bin/hustle-monitor\.py" 2>/dev/null
  systemctl --user daemon-reload 2>/dev/null || true
  systemctl --user reset-failed 2>/dev/null || true
  if [ "$removed" -eq 0 ]; then
    echo "no units found for slug '${SLUG}' (project: $PROJECT)"
  else
    echo
    echo "uninstalled the chain for: $PROJECT"
    echo "  $PROJECT/.hustle/ and GOAL.md were left untouched — delete them yourself if you want them gone."
  fi
  exit 0
fi

command -v python3 >/dev/null || { echo "error: python3 is required (macOS: xcode-select --install)"; exit 1; }
command -v claude  >/dev/null || { echo "error: claude CLI not found in PATH"; exit 1; }

mkdir -p "$HUSTLE_HOME/bin"
rm -f "$HUSTLE_HOME/bin/hustle-scheduler.sh" "$HUSTLE_HOME/bin/hustle-session.sh" "$HUSTLE_HOME/bin/hustle-monitor.py"
rm -rf "$HUSTLE_HOME/assets"
if [ "$DEV_MODE" = true ]; then
  ln -s "$PLUGIN_DIR/scripts/hustle-scheduler.sh" "$HUSTLE_HOME/bin/hustle-scheduler.sh"
  ln -s "$PLUGIN_DIR/scripts/hustle-session.sh" "$HUSTLE_HOME/bin/hustle-session.sh"
  ln -s "$PLUGIN_DIR/scripts/hustle-monitor.py" "$HUSTLE_HOME/bin/hustle-monitor.py"
  ln -s "$PLUGIN_DIR/assets" "$HUSTLE_HOME/assets"
  echo "dev mode: symlinked .hustle/bin/* and .hustle/assets at $PLUGIN_DIR"
else
  cp "$PLUGIN_DIR/scripts/hustle-scheduler.sh" "$PLUGIN_DIR/scripts/hustle-session.sh" \
     "$PLUGIN_DIR/scripts/hustle-monitor.py" "$HUSTLE_HOME/bin/"
  cp -r "$PLUGIN_DIR/assets" "$HUSTLE_HOME/assets"
fi
chmod +x "$HUSTLE_HOME/bin/hustle-scheduler.sh" "$HUSTLE_HOME/bin/hustle-session.sh" "$HUSTLE_HOME/bin/hustle-monitor.py"

# Runtime state never belongs in the project's VCS.
echo "*" > "$HUSTLE_HOME/.gitignore"

if [ ! -f "$HUSTLE_HOME/config" ]; then
  # Pick a free port so a second project on this host doesn't collide with
  # (and crash-loop against) an existing monitor's fixed 8787.
  FREE_PORT="$(python3 - <<'PY'
import socket
port = 8787
while port < 8987:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            print(port)
            break
        except OSError:
            port += 1
PY
)"
  cat > "$HUSTLE_HOME/config" <<EOF
# advance-goal loop configuration (sourced by bash, parsed by the monitor)
HUSTLE_PROJECT="$PROJECT"
HUSTLE_SLUG="$SLUG"
# Mode: "plan" = execute a GOAL.md and stop (classic); "charter" = self-directed
# PO+Dev loop against a Plane backlog, bounded by CHARTER.md. /grill-charter sets
# this to "charter" and fills the Plane keys below.
HUSTLE_MODE="plan"
# Executor model (also the per-issue fallback in charter mode).
HUSTLE_MODEL="sonnet"
# Planner model for charter mode (the strong model that writes the backlog).
HUSTLE_PLAN_MODEL="opus"
HUSTLE_EFFORT="high"
# The loop runs unattended: without this it cannot use tools headlessly.
# Understand what that means before flipping it — see the plugin README.
HUSTLE_SKIP_PERMISSIONS="true"
HUSTLE_PORT="${FREE_PORT:-8787}"
HUSTLE_BIND="0.0.0.0"
EOF
  echo "wrote $HUSTLE_HOME/config (port ${FREE_PORT:-8787})"
else
  echo "kept existing $HUSTLE_HOME/config"
fi

# Secrets live apart from config (the monitor parses/surfaces config). Scaffold a
# 0600 file so the path + perms exist; /grill-charter fills PLANE_API_KEY. Sourced
# by hustle-session.sh into the run env; uses `export` so `claude -p` inherits it.
if [ ! -f "$HUSTLE_HOME/secrets" ]; then
  cat > "$HUSTLE_HOME/secrets" <<'EOF'
# Secrets sourced into the headless run env — NEVER commit, never display.
# Charter mode needs the Plane API key here (filled by /grill-charter):
# export PLANE_API_KEY="plane_api_..."
EOF
  chmod 600 "$HUSTLE_HOME/secrets"
  echo "wrote $HUSTLE_HOME/secrets (0600)"
else
  chmod 600 "$HUSTLE_HOME/secrets" 2>/dev/null || true
  echo "kept existing $HUSTLE_HOME/secrets"
fi
CONFIGURED_PORT="$(grep -oE '^HUSTLE_PORT="?[0-9]+' "$HUSTLE_HOME/config" | grep -oE '[0-9]+$' || echo 8787)"

if [ "$OS" != "Darwin" ]; then
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  # The monitor gets its OWN unit: anything nohup'ed from inside the oneshot
  # run would live in the oneshot's cgroup and be killed when the run ends
  # (KillMode=control-group). Wants= pulls it up with every run instead.
  cat > "$UNIT_DIR/hustle-monitor-${SLUG}.service" <<EOF
[Unit]
Description=hustle monitor dashboard for ${SLUG}
After=network.target

[Service]
ExecStart=/usr/bin/env python3 $HUSTLE_HOME/bin/hustle-monitor.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  cat > "$UNIT_DIR/hustle-${SLUG}.service" <<EOF
[Unit]
Description=advance-goal run for ${SLUG}
Wants=network-online.target hustle-monitor-${SLUG}.service
After=network-online.target

[Service]
Type=oneshot
ExecStart=$HUSTLE_HOME/bin/hustle-session.sh
TimeoutStartSec=7200
EOF
  systemctl --user daemon-reload
  # Enable the monitor so it also comes up at login, not only when a run pulls
  # it in via Wants=. Once the goal is reached no run ever fires again, and an
  # un-enabled monitor would take the finished dashboard down with the next
  # reboot -- exactly the result you most want to still be able to look at.
  systemctl --user enable "hustle-monitor-${SLUG}.service" >/dev/null 2>&1 || true
  echo "wrote systemd units hustle-${SLUG}.service + hustle-monitor-${SLUG}.service"
fi

# Smoke-test the scheduler round trip (arm in the future, read back, disarm).
if "$HUSTLE_HOME/bin/hustle-scheduler.sh" arm --in 3600 >/dev/null \
   && [ -n "$("$HUSTLE_HOME/bin/hustle-scheduler.sh" next)" ]; then
  "$HUSTLE_HOME/bin/hustle-scheduler.sh" disarm
  echo "scheduler smoke test: OK"
else
  "$HUSTLE_HOME/bin/hustle-scheduler.sh" disarm || true
  echo "scheduler smoke test: FAILED — check the output above" >&2
  exit 1
fi

cat <<EOF

hustle installed for: $PROJECT  (slug: $SLUG)

Next steps:
  1. Define the work (in a Claude Code session in this project):
       plan mode    -> /grill-goal      (a GOAL.md the loop executes, then stops)
       charter mode -> /grill-charter    (a CHARTER.md + Plane backlog, self-directed)
  2. Kick off the chain:
EOF
if [ "$OS" = "Darwin" ]; then
  echo "       $HUSTLE_HOME/bin/hustle-session.sh &"
else
  cat <<EOF
       systemctl --user start hustle-${SLUG}.service
     (survive logout/reboot: loginctl enable-linger \$USER)
EOF
fi
cat <<EOF
  3. Watch:                          http://<this-host>:$CONFIGURED_PORT
  Kill switch:                       $HUSTLE_HOME/bin/hustle-scheduler.sh disarm
EOF
