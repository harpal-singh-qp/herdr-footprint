#!/usr/bin/env python3
"""Largest context share among the agent panes of one space.

Reads `herdr pane list` JSON on stdin, prints a whole-number percentage, or
nothing when no pane reports one. argv[1] pins the context window for the
transcript fallback; 0 means infer it.

Two sources, in order:

1. A `context` metadata token on the pane. Plugins such as herdr-agent-usage
   publish one for Claude, Codex, OpenCode, Grok, Pi, omp, Cursor and direct
   API backends. Preferring it means this plugin covers every provider those
   plugins cover, and never has to track a transcript format it does not own.
2. Claude's own transcript, so a space still reports something useful when no
   usage plugin is installed.
"""
import glob
import json
import os
import re
import sys

WINDOW_SMALL = 200_000
WINDOW_LARGE = 1_000_000
TAIL_BYTES = 400_000

# Matches the percentage in values like "⚠️ 83% (827k)", "31%" or "83.5%".
# The lookbehind matters: without it "91.6%" matches the "6" and reports 6,
# because the digits nearest the sign win.
PERCENT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%")


def token_percent(pane):
    """Percentage from a pane's `context` token, if a usage plugin published one."""
    value = (pane.get("tokens") or {}).get("context")
    if not value:
        return 0
    match = PERCENT.search(str(value))
    if not match:
        return 0
    pct = round(float(match.group(1)))
    return pct if 0 <= pct <= 100 else 0


def transcript_percent(pane, forced):
    """Fallback: Claude's transcript, keyed by the pane's agent session id."""
    uuid = (pane.get("agent_session") or {}).get("value")
    if not uuid:
        return 0
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
        used = (usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0))
        if used:
            # The transcript never records the context window. A session that
            # has already passed 200k proves it is on the 1M window.
            window = forced or (WINDOW_LARGE if used > WINDOW_SMALL else WINDOW_SMALL)
            return round(100 * used / window)
    return 0


ACTIVE = ("working", "blocked")


def main():
    """Report the space's context share, preferring the agent actually running.

    A space holds many tabs, and old session tabs get left open. Taking the plain
    maximum meant a dormant tab sitting at 90% spoke for a space whose live work
    was at 33% - the number described a session you had already moved on from.

    So: if anything in the space is working or waiting on you, report the busiest
    of those. Only when nothing is live does the maximum stand in, so a space full
    of parked sessions still warns about the worst of them.
    """
    forced = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
    try:
        panes = json.load(sys.stdin)["result"]["panes"]
    except (ValueError, KeyError, TypeError):
        return

    live, dormant = [], []
    for pane in panes:
        pct = token_percent(pane) or transcript_percent(pane, forced)
        if not pct:
            continue
        (live if pane.get("agent_status") in ACTIVE else dormant).append(pct)

    best = max(live) if live else max(dormant, default=0)
    if best:
        print(best)


if __name__ == "__main__":
    main()
