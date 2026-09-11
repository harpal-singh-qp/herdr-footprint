#!/usr/bin/env bash
# reclaim-pane.sh — the [[panes]] entrypoint for the reclaim report.
#
# An overlay pane closes when its command exits, so this holds the pane open and
# waits for a key instead of returning after one render.

. "$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/lib.sh"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# The pane's own cwd decides which worktree is fenced as "you are standing in it".
export HERDR_PANE_CWD="${HERDR_PANE_CWD:-$PWD}"

while :; do
  clear
  printf '  scanning…\n'
  report=$(python3 "$SELF_DIR/reclaim.py" 2>&1)
  clear
  printf '%s\n' "$report"
  printf '\n\033[2m  r refresh · q quit\033[0m '
  IFS= read -rsn1 key || exit 0
  case "$key" in
    q|Q|$'\e') exit 0 ;;
    *) ;;   # anything else rescans
  esac
done
