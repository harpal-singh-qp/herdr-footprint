#!/usr/bin/env python3
"""The reclaim pane: look at what can go, choose, confirm, then act.

Selection is deliberately awkward in two places, because both deserve to be.
BLOCKED rows cannot be selected at all - they have no target, so there is nothing
to press. And confirming requires typing a word, not pressing a key, so a stray
keystroke in a terminal you forgot was focused cannot delete anything.

Everything else is one keypress, because the point of the tool is that reclaiming
space should be easier than ignoring it.
"""
import importlib.util
import os
import sys
import termios
import tty

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


reclaim = _load("reclaim")
actions = _load("actions")

C = reclaim.C
SAFE, REVIEW, BLOCKED = reclaim.SAFE, reclaim.REVIEW, reclaim.BLOCKED


def read_key():
    """One keypress, with arrow keys folded onto their vi equivalents."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            nxt = sys.stdin.read(2)
            return {"[A": "k", "[B": "j", "[C": "l", "[D": "h"}.get(nxt, "esc")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_word(prompt):
    """A typed line. Confirmation must cost more than brushing the keyboard."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return sys.stdin.readline().strip()
    except (KeyboardInterrupt, EOFError):
        return ""


def scan(cwd):
    reclaim.NOTES.clear()
    rows = reclaim.fold_noise(
        reclaim.docker_rows() + reclaim.worktree_rows(cwd) + reclaim.transcript_rows())
    order = {SAFE: 0, REVIEW: 1, BLOCKED: 2}
    rows.sort(key=lambda r: (order[r[0]], -r[3]))
    totals = {SAFE: 0, REVIEW: 0, BLOCKED: 0}
    for cls, _, _, size, _, _ in rows:
        totals[cls] += size
    reclaim.record(rows, totals)
    return rows, totals


def draw(rows, totals, cursor, selected, status):
    width = max(40, reclaim.shutil.get_terminal_size((80, 24)).columns)
    sys.stdout.write("\033[2J\033[H")

    chosen = sum(rows[i][3] for i in selected)
    head = (f"{C['bold']}footprint{C['off']}  "
            f"{C[SAFE]}SAFE {reclaim.human(totals[SAFE])}{C['off']}  "
            f"{C[REVIEW]}REVIEW {reclaim.human(totals[REVIEW])}{C['off']}  "
            f"{C[BLOCKED]}BLOCKED {reclaim.human(totals[BLOCKED])}{C['off']}")
    if selected:
        head += f"   {C['bold']}{len(selected)} chosen · {reclaim.human(chosen)}{C['off']}"
    print(head + "\n")

    name_w = max(16, min(42, width - 42))
    detail_w = max(10, width - name_w - 20)

    for i, (cls, kind, name, size, reason, target) in enumerate(rows):
        # A blocked row gets no box at all rather than an unticked one: an empty
        # checkbox invites a click, and this is the one thing that cannot be ticked.
        if target is None:
            box = f"{C['dim']} –  {C['off']}"
        elif i in selected:
            box = f"{C[cls]}{C['bold']}[✓]{C['off']} "
        else:
            box = f"{C['dim']}[ ]{C['off']} "
        arrow = f"{C['bold']}❯{C['off']}" if i == cursor else " "
        detail = f"{kind} · {reason}"
        print(f"{arrow}{box}{C[cls]}{reclaim.human(size):>7}{C['off']}  "
              f"{reclaim.ellipsis(name, name_w):<{name_w}} "
              f"{C['dim']}{reclaim.ellipsis(detail, detail_w)}{C['off']}")

    for note in reclaim.NOTES:
        print(f"\n {C[REVIEW]}!{C['off']} {C['dim']}{note}{C['off']}")

    print(f"\n{C['dim']} {status}{C['off']}" if status else "")
    tick = f"{C['bold']}[✓]{C['off']}{C['dim']}"
    print(f"{C['dim']} ↑↓ move · space ticks {tick} · a all safe · n none · "
          f"d reclaim ticked · r rescan · q quit{C['off']}", end="")
    sys.stdout.flush()


def confirm(rows, selected):
    """A typed word, and a list of exactly what is about to happen."""
    sys.stdout.write("\033[2J\033[H")
    total = sum(rows[i][3] for i in selected)
    print(f"{C['bold']}About to delete {len(selected)} items · "
          f"{reclaim.human(total)}{C['off']}\n")
    for i in sorted(selected, key=lambda i: -rows[i][3]):
        cls, kind, name, size, _, _ = rows[i]
        print(f"  {C[cls]}{reclaim.human(size):>7}{C['off']}  {name}  "
              f"{C['dim']}{kind}{C['off']}")
    print(f"\n{C['dim']}  Every fence is re-checked now, not when the list was built;"
          f"\n  anything that changed is skipped and told to you."
          f"\n  A worktree's branch is bundled before its checkout goes.{C['off']}\n")
    return read_word(f"  Type {C['bold']}delete{C['off']} to go ahead, "
                     f"anything else to cancel: ") == "delete"


def execute(rows, selected, cwd):
    sys.stdout.write("\033[2J\033[H")
    print(f"{C['bold']}Reclaiming{C['off']}\n")
    freed = 0
    done = skipped = 0
    for i in sorted(selected, key=lambda i: -rows[i][3]):
        cls, kind, name, size, _, target = rows[i]
        sys.stdout.write(f"  {reclaim.ellipsis(name, 46):<46} … ")
        sys.stdout.flush()
        ok, msg = actions.perform(target, cwd=cwd)
        if ok:
            freed += size
            done += 1
            print(f"{C[SAFE]}{msg}{C['off']}")
        else:
            skipped += 1
            print(f"{C[REVIEW]}{msg}{C['off']}")
    print(f"\n  {C['bold']}{reclaim.human(freed)} reclaimed{C['off']} · "
          f"{done} done, {skipped} skipped")
    read_word("\n  Enter to rescan: ")
    return freed


def main():
    cwd = os.environ.get("HERDR_PANE_CWD") or os.getcwd()
    status = "scanning…"
    sys.stdout.write("\033[2J\033[H  scanning…\n")
    sys.stdout.flush()
    rows, totals = scan(cwd)
    cursor, selected = 0, set()

    while True:
        draw(rows, totals, cursor, selected, status)
        status = ""
        key = read_key()

        if key in ("q", "Q", "esc", "\x03"):
            sys.stdout.write("\033[2J\033[H")
            return
        if key == "j":
            cursor = min(cursor + 1, len(rows) - 1)
        elif key == "k":
            cursor = max(cursor - 1, 0)
        elif key in (" ", "x", "\r", "\n"):
            if rows and rows[cursor][5] is None:
                status = "that one is blocked — no box to tick; the row says why"
            elif rows:
                selected.symmetric_difference_update({cursor})
                # Ticking then moving on is the common case; save the extra keypress.
                cursor = min(cursor + 1, len(rows) - 1)
        elif key == "a":
            selected |= {i for i, r in enumerate(rows) if r[0] == SAFE and r[5]}
            status = "chose everything classed SAFE"
        elif key == "n":
            selected.clear()
        elif key == "r":
            status = "scanning…"
            draw(rows, totals, cursor, selected, status)
            rows, totals = scan(cwd)
            cursor, selected = 0, set()
            status = "rescanned"
        elif key == "d":
            if not selected:
                status = "nothing chosen — space to choose, a for all safe"
                continue
            if confirm(rows, selected):
                execute(rows, selected, cwd)
                rows, totals = scan(cwd)
                cursor, selected = 0, set()
                status = "rescanned after reclaiming"
            else:
                status = "cancelled, nothing was deleted"


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write("\033[2J\033[H")
