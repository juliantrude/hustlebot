#!/usr/bin/env bash
# Prototype: does a headless session in a git worktree load the project's CLAUDE.md, hooks and settings?
#   ./worktree_session.sh <scratch-dir>
# Builds a throwaway repo with a code word in CLAUDE.md, a SessionStart hook in the checked-in
# .claude/settings.json and an env var plus a second hook in the gitignored .claude/settings.local.json.
# Runs one Haiku session in a worktree, once without and once with settings.local.json copied in.
set -euo pipefail
base="$(realpath "$1")"; rm -rf "$base"; mkdir -p "$base"
repo="$base/proj"; mkdir -p "$repo/.claude"; cd "$repo"
git init -q -b main
echo "The code word of this project is PELICAN." > CLAUDE.md
echo ".claude/settings.local.json" > .gitignore
cat > .claude/settings.json <<'J'
{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo \"shared-hook ran in $PWD, HB_LOCAL=${HB_LOCAL:-unset}\" >> \"$CLAUDE_PROJECT_DIR/../markers.log\""}]}]}}
J
cat > .claude/settings.local.json <<'J'
{"env":{"HB_LOCAL":"from-local"},"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo \"local-hook ran in $PWD\" >> \"$CLAUDE_PROJECT_DIR/../markers.log\""}]}]}}
J
git add -A && git commit -qm init
run() {  # $1 = worktree name
  local wt="$base/$1"; git -C "$repo" worktree add -q -b "$1" "$wt"
  [ "${2:-}" = copy ] && cp "$repo/.claude/settings.local.json" "$wt/.claude/"
  (cd "$wt" && claude -p "Reply with only the code word from CLAUDE.md, or NONE." --model haiku \
     --output-format json > "$base/$1.json")
  echo "== $1: answer=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["result"])' "$base/$1.json")"
}
run wt-plain
run wt-copied copy
echo "== markers (markers.log is written next to each worktree):"
cat "$base"/markers.log 2>/dev/null || echo "(none)"
echo "== transcript dirs under ~/.claude/projects for this scratch dir:"
ls -d ~/.claude/projects/*"$(basename "$base")"* 2>/dev/null || echo "(none)"
