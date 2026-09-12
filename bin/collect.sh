#!/usr/bin/env bash
# collect.sh — one measurement cycle: read the session, push one token set per space.
#
# Disk is expensive (a 13 GB checkout takes ~14s to walk), so exactly ONE space
# is re-measured per cycle — the stalest one. Every other space pushes its
# cached figure, which keeps the sidebar populated without ever walking the
# whole machine in a single tick.

. "$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/lib.sh"

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
mkdir -p "$CACHE_DIR" 2>/dev/null

# workspace_id -> first pane cwd. Panes are the only place herdr exposes a
# directory for a space; workspace.get carries no cwd of its own.
space_dir() {
  "$HERDR_BIN" pane list --workspace "$1" 2>/dev/null | python3 -c '
import json, sys
try:
    panes = json.load(sys.stdin)["result"]["panes"]
except Exception:
    sys.exit(0)
for p in panes:
    if p.get("cwd"):
        print(p["cwd"]); break
' 2>/dev/null
}

# Largest context share among the Claude panes in a space. Reported per space
# because that is the number that answers "can this space take another turn?".
space_context_pct() {
  "$HERDR_BIN" pane list --workspace "$1" 2>/dev/null \
    | python3 "$SELF_DIR/context.py" "$FOOTPRINT_CONTEXT_WINDOW" 2>/dev/null
}

now=$(date +%s)
stalest_id="" stalest_dir="" stalest_age=-1

spaces=$("$HERDR_BIN" workspace list 2>/dev/null | python3 -c '
import json, sys
try:
    for w in json.load(sys.stdin)["result"]["workspaces"]:
        print(w["workspace_id"])
except Exception:
    pass
' 2>/dev/null)
[ -n "$spaces" ] || { log "no workspaces"; exit 0; }

for id in $spaces; do
  dir=$(space_dir "$id")
  root=$(worktree_root "$dir") || root=""
  tokens=()

  if [ -n "$root" ]; then
    cache="$CACHE_DIR/$(cache_key "$root").size"
    if [ -r "$cache" ]; then
      bytes=$(cat "$cache" 2>/dev/null)
      age=$(( now - $(mtime "$cache") ))
    else
      bytes="" ; age=$(( FOOTPRINT_REMEASURE_SEC + 1 ))
    fi
    # Claim the single re-measure slot for the stalest space past its TTL.
    if [ "$age" -gt "$FOOTPRINT_REMEASURE_SEC" ] && [ "$age" -gt "$stalest_age" ]; then
      stalest_age=$age stalest_id=$id stalest_dir=$root
    fi
    if [ -n "$bytes" ]; then
      tokens+=(--token "disk=${FOOTPRINT_DISK_ICON} $(human_bytes "$bytes")")
    else
      tokens+=(--token "disk=${FOOTPRINT_DISK_ICON} $PLACEHOLDER")
    fi
  else
    tokens+=(--token "disk=${FOOTPRINT_DISK_ICON} $PLACEHOLDER")
  fi

  pct=$(space_context_pct "$id")
  tokens+=(--token "ctx=${FOOTPRINT_CTX_ICON} ${pct:-$PLACEHOLDER}${pct:+%}")

  # TTL of three cycles: a stopped poller fades its tokens instead of leaving
  # a stale number on screen forever. Capped at herdr's 24h metadata maximum.
  ttl=$(( FOOTPRINT_CADENCE_SEC * 3000 ))
  [ "$ttl" -gt 86400000 ] && ttl=86400000
  "$HERDR_BIN" workspace report-metadata "$id" --source footprint \
    "${tokens[@]}" --ttl-ms "$ttl" >/dev/null 2>&1 \
    || log "push failed for $id"
done

# The one expensive walk of this cycle, after every cheap token is already out.
if [ -n "$stalest_dir" ]; then
  bytes=$(dir_bytes "$stalest_dir")
  if [ -n "$bytes" ]; then
    printf '%s' "$bytes" >"$CACHE_DIR/$(cache_key "$stalest_dir").size" 2>/dev/null
    log "measured $stalest_id $stalest_dir -> $(human_bytes "$bytes")"
  fi
fi
