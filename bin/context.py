#!/usr/bin/env python3
"""Largest context share among the Claude panes of one space.

Reads `herdr pane list` JSON on stdin, prints a whole-number percentage, or
nothing when no pane has a readable transcript. argv[1] pins the context
window; 0 means infer it.
"""
import glob
import json
import os
import sys

WINDOW_SMALL = 200_000
WINDOW_LARGE = 1_000_000
TAIL_BYTES = 400_000


def session_tokens(uuid):
    """Context tokens of the most recent recorded turn: input + both cache halves."""
    hits = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{uuid}.jsonl"))
    if not hits:
        return 0
    try:
        with open(hits[0], "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - TAIL_BYTES))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return 0
    for line in reversed(lines):
        try:
            usage = (json.loads(line).get("message") or {}).get("usage") or {}
        except ValueError:
            continue
        total = (usage.get("input_tokens", 0)
                 + usage.get("cache_read_input_tokens", 0)
                 + usage.get("cache_creation_input_tokens", 0))
        if total:
            return total
    return 0


def main():
    forced = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
    try:
        panes = json.load(sys.stdin)["result"]["panes"]
    except (ValueError, KeyError, TypeError):
        return
    best = 0
    for pane in panes:
        uuid = (pane.get("agent_session") or {}).get("value")
        if not uuid:
            continue
        used = session_tokens(uuid)
        if not used:
            continue
        # The transcript never records the context window. A session that has
        # already passed 200k proves it is on the 1M window, so infer it.
        window = forced or (WINDOW_LARGE if used > WINDOW_SMALL else WINDOW_SMALL)
        best = max(best, round(100 * used / window))
    if best:
        print(best)


if __name__ == "__main__":
    main()
