#!/usr/bin/env bash
# lib.sh — shared helpers for herdr-footprint. Sourced, never executed.
#
# No `set -e` anywhere in this plugin: a single failed measurement or CLI call
# must never kill the poll loop. Every helper returns a usable value or the
# placeholder, so a sidebar row can never vanish mid-session.

# This file is sourced, never executed: everything below is the surface its
# callers use, so "appears unused" is expected here and only here.
# shellcheck disable=SC2034
HERDR_BIN="${HERDR_BIN_PATH:-herdr}"
STATE_DIR="${HERDR_PLUGIN_STATE_DIR:-${HERDR_PLUGIN_ROOT:-$PWD}/.state}"
CACHE_DIR="$STATE_DIR/cache"
PIDFILE="$STATE_DIR/footprint.pid"
LOGFILE="$STATE_DIR/footprint.log"
PLACEHOLDER="--"

# Config, overridable from the environment or the plugin config file.
CONFIG_FILE="${HERDR_PLUGIN_CONFIG_DIR:-$STATE_DIR}/config.env"
# User-supplied, so the path cannot be constant.
# shellcheck source=/dev/null
[ -r "$CONFIG_FILE" ] && . "$CONFIG_FILE"
: "${FOOTPRINT_CADENCE_SEC:=60}"      # seconds between push cycles
: "${FOOTPRINT_REMEASURE_SEC:=900}"   # re-`du` a worktree at most this often
: "${FOOTPRINT_CONTEXT_WINDOW:=0}"    # 0 = infer from observed usage
: "${FOOTPRINT_DISK_ICON:=⛁}"
: "${FOOTPRINT_CTX_ICON:=◐}"

log() { printf '%s %s\n' "$(date +%FT%T)" "$*" >>"$LOGFILE" 2>/dev/null; }

# Bytes -> one significant decimal, the unit a human reads at a glance.
human_bytes() {
  local b=${1:-0}
  awk -v b="$b" 'BEGIN{
    split("B K M G T", u, " ")
    i = 1
    while (b >= 1024 && i < 5) { b /= 1024; i++ }
    printf (i <= 2 || b >= 100) ? "%.0f%s\n" : "%.1f%s\n", b, u[i]
  }'
}

# A space is measured at its git worktree root, not the pane's cwd: several
# spaces commonly sit in subdirectories of one checkout, and the worktree is
# the thing that actually occupies disk.
worktree_root() {
  local dir=$1
  [ -d "$dir" ] || return 1
  git -C "$dir" rev-parse --show-toplevel 2>/dev/null || printf '%s\n' "$dir"
}

cache_key() { printf '%s' "$1" | cksum | tr -d ' \t' ; }

# BSD and GNU disagree on both of these, and the manifest claims macOS. Resolve
# them once here: a silent failure would read as "0 bytes" and "just measured",
# neither of which is an error the caller would ever notice.
# Always prints an integer: an empty value would make the caller's $(( )) a
# syntax error, and 0 fails toward re-measuring rather than toward never doing so.
if stat -c %Y . >/dev/null 2>&1; then _stat_mtime() { stat -c %Y "$1" 2>/dev/null; }
else                                 _stat_mtime() { stat -f %m "$1" 2>/dev/null; }; fi
mtime() {
  local t; t=$(_stat_mtime "$1")
  case "$t" in ''|*[!0-9]*) printf '0' ;; *) printf '%s' "$t" ;; esac
}

# `du -sk` is POSIX; GNU's --block-size is not.
dir_bytes() {
  local kb
  kb=$(du -sk "$1" 2>/dev/null | cut -f1)
  case "$kb" in ''|*[!0-9]*) return 1 ;; esac
  printf '%s' "$(( kb * 1024 ))"
}
