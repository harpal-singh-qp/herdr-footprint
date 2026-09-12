#!/usr/bin/env bash
# reclaim-pane.sh — the [[panes]] entrypoint.
#
# The pane's cwd decides which worktree is fenced as "you are standing in it",
# so it is passed through rather than inferred.

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
export HERDR_PANE_CWD="${HERDR_PANE_CWD:-$PWD}"
exec python3 "$SELF_DIR/pane.py"
