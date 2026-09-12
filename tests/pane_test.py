#!/usr/bin/env python3
"""Prove the pane's navigation, which is invisible until the list outgrows the screen.

Only pure logic is covered here - the viewport arithmetic and the escape-sequence
decoder. Both were wrong in ways that could not be seen on a short list or a
terminal tall enough to show everything, which is exactly the kind of bug worth a
test rather than an eyeball.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f" — got {got!r}, want {want!r}"))
    if not ok:
        failures.append(label)


def load():
    spec = importlib.util.spec_from_file_location("pane", os.path.join(ROOT, "bin", "pane.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_viewport(p):
    # (cursor, top, height, count) -> (cursor, top)
    check("top of a long list", p.clamp_view(0, 0, 10, 48), (0, 0))
    check("jump to bottom scrolls the window", p.clamp_view(47, 0, 10, 48), (47, 38))
    check("jump to top scrolls back", p.clamp_view(0, 38, 10, 48), (0, 0))
    check("stepping below the window follows", p.clamp_view(10, 0, 10, 48), (10, 1))
    check("stepping above the window follows", p.clamp_view(5, 8, 10, 48), (5, 5))
    # Page-down near the end must stop at the last row, not run past it.
    check("paging past the end clamps", p.clamp_view(99, 0, 10, 48), (47, 38))
    check("a list shorter than the window does not scroll", p.clamp_view(2, 0, 10, 4), (2, 0))
    check("an empty list is survivable", p.clamp_view(0, 0, 10, 0), (0, 0))


def test_sequences(p):
    # Arrow keys are three bytes and PageUp is four. Reading a fixed two leaves
    # the tail in the buffer, where it arrives later as a phantom keypress.
    check("up arrow", p.SEQUENCES.get("[A"), "up")
    check("down arrow", p.SEQUENCES.get("[B"), "down")
    check("page up", p.SEQUENCES.get("[5~"), "pgup")
    check("page down", p.SEQUENCES.get("[6~"), "pgdn")
    check("home, both encodings", (p.SEQUENCES.get("[H"), p.SEQUENCES.get("OH")), ("home", "home"))
    check("end, both encodings", (p.SEQUENCES.get("[F"), p.SEQUENCES.get("OF")), ("end", "end"))


def test_filter(p):
    safe_actionable = (p.SAFE, "docker image", "a", 1, "r", {"op": "docker_image", "id": "x"})
    blocked = (p.BLOCKED, "docker volume", "b", 2, "r", None)
    rows = [safe_actionable, blocked]
    check("unfiltered shows everything", len(p.visible(rows, False)), 2)
    # Hiding blocked rows is what makes a long list tickable; a row with no
    # target must never survive the filter.
    check("filtered hides what cannot be acted on", p.visible(rows, True), [safe_actionable])


def main():
    p = load()
    print("\npane navigation")
    test_viewport(p)
    test_sequences(p)
    test_filter(p)
    if failures:
        print(f"\n  {len(failures)} failed: {', '.join(failures)}\n")
        return 1
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
