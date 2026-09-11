#!/usr/bin/env bash
# poll.sh — supervisor for the collect loop.
#
# herdr's [[startup]] hook is one-shot, so --spawn detaches a long-running loop
# and returns immediately. A pidfile keeps a herdr restart from stacking pollers.

. "$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/lib.sh"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

mkdir -p "$STATE_DIR" 2>/dev/null

running() {
  [ -r "$PIDFILE" ] || return 1
  local pid; pid=$(cat "$PIDFILE" 2>/dev/null)
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

case "${1:---run}" in
  --spawn)
    running && { echo "already running ($(cat "$PIDFILE"))"; exit 0; }
    setsid nohup "$SELF_DIR/poll.sh" --run >/dev/null 2>&1 &
    echo "spawned"
    ;;
  --stop)
    running || { echo "not running"; exit 0; }
    kill "$(cat "$PIDFILE")" 2>/dev/null
    rm -f "$PIDFILE"
    echo "stopped"
    ;;
  --status)
    running && echo "running ($(cat "$PIDFILE"))" || echo "not running"
    ;;
  --once)
    bash "$SELF_DIR/collect.sh"
    ;;
  --run)
    running && exit 0
    printf '%s' "$$" >"$PIDFILE"
    trap 'rm -f "$PIDFILE"' EXIT
    log "poller start pid=$$ cadence=${FOOTPRINT_CADENCE_SEC}s remeasure=${FOOTPRINT_REMEASURE_SEC}s"
    while :; do
      bash "$SELF_DIR/collect.sh" || log "cycle failed"
      sleep "$FOOTPRINT_CADENCE_SEC"
    done
    ;;
  *)
    echo "usage: poll.sh [--spawn|--stop|--status|--once|--run]" >&2
    exit 2
    ;;
esac
