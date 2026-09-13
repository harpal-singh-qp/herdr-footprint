#!/usr/bin/env python3
"""Prove which agent speaks for a space.

A space holds many tabs and old session tabs get left open, so "the context of
this space" is a choice, not a lookup. The rule is: the busiest agent that is
actually live, falling back to the busiest of all when nothing is.

Getting this wrong is not a crash. It is a number that quietly describes a
session you already moved on from, which is worse, because you believe it.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT = os.path.join(ROOT, "bin", "context.py")
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f" — got {got!r}, want {want!r}"))
    if not ok:
        failures.append(label)


def pane(status, context, uuid=None):
    p = {"agent_status": status, "tokens": {}}
    if context is not None:
        p["tokens"]["context"] = context
    if uuid:
        p["agent_session"] = {"value": uuid}
    return p


def ask(panes, window="0"):
    out = subprocess.run([sys.executable, CONTEXT, window],
                         input=json.dumps({"result": {"panes": panes}}),
                         capture_output=True, text=True)
    return out.stdout.strip()


def main():
    print("\ncontext per space")

    # The case that prompted the rule: a parked tab at 90% must not speak for a
    # space whose live session is at 33%.
    check("a working agent outranks a dormant one with more context",
          ask([pane("idle", "⚠️ 90% (901k)"), pane("working", "⛁ 33% (326k)")]), "33")

    # Waiting on you is still a live session, not a parked one.
    check("blocked counts as live", ask([pane("idle", "80%"), pane("blocked", "21%")]), "21")

    # Several live agents: the busiest of them, since that is the one near a wall.
    check("busiest of the live agents wins",
          ask([pane("working", "40%"), pane("blocked", "55%"), pane("idle", "99%")]), "55")

    # Nothing live: the maximum still stands in, so a parked space warns you.
    check("with nothing live, the highest still reports",
          ask([pane("idle", "80%"), pane("done", "12%")]), "80")

    check("a space with no agents reports nothing", ask([pane("unknown", None)]), "")
    check("no panes at all is survivable", ask([]), "")

    # The percentage is parsed out of whatever the publishing plugin formats.
    check("percentage is read from a decorated value",
          ask([pane("working", "⛁ 7% (74k)")]), "7")
    # "91.6%" used to report 6: the digits nearest the sign won, so a decimal
    # percentage was read as its fractional part.
    check("a decimal percentage is not read as its fraction",
          ask([pane("working", "83.5%")]), "84")
    check("a decorated decimal parses whole",
          ask([pane("working", "⛁ 91.6% (916k)")]), "92")

    if failures:
        print(f"\n  {len(failures)} failed: {', '.join(failures)}\n")
        return 1
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
