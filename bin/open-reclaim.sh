#!/usr/bin/env bash
# open-reclaim.sh — action body: open the reclaim report as an overlay pane.
#
# The pane inherits the invoking context's directory so the scan can fence the
# worktree you are currently standing in, rather than offering it up.
herdr_bin="${HERDR_BIN_PATH:-herdr}"

cwd=$(printf '%s' "${HERDR_PLUGIN_CONTEXT_JSON:-}" | python3 -c '
import json, sys
try:
    ctx = json.load(sys.stdin)
except Exception:
    raise SystemExit
print(ctx.get("focused_pane_cwd") or ctx.get("workspace_cwd") or "")
' 2>/dev/null)
[ -n "$cwd" ] && [ -d "$cwd" ] || cwd="$HOME"

exec "$herdr_bin" plugin pane open --plugin "${HERDR_PLUGIN_ID:-footprint}" \
  --entrypoint reclaim --placement overlay --focus \
  --cwd "$cwd" --env "HERDR_PANE_CWD=$cwd"
