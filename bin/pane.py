#!/usr/bin/env python3
"""The reclaim pane: look at what can go, tick it, confirm, act.

The list is longer than any terminal, so it is drawn through a viewport that
follows the cursor rather than printed whole and left to the terminal's scrollback
- scrollback has no idea where the cursor is, which makes a long list unusable.

Selection is deliberately awkward in exactly two places. A BLOCKED row has no
checkbox, because it has no target and nothing to tick. And confirming requires
typing a word rather than pressing a key, so a stray keystroke in a terminal you
forgot was focused cannot delete anything. Everything else is one key.
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

CHROME_ROWS = 6  # header, blank, footer, status, and breathing room

# Escape sequences, folded onto names. Home/End/PageUp/PageDown matter here
# because the list is long; without them the only way up is holding k.
SEQUENCES = {
    "[A": "up", "[B": "down", "[C": "right", "[D": "left",
    "[H": "home", "[F": "end", "[1~": "home", "[4~": "end",
    "[5~": "pgup", "[6~": "pgdn", "OH": "home", "OF": "end",
}


def read_key():
    """One keypress. Escape sequences are read to their terminator, not to a
    fixed length: PageUp is four bytes and Up is three, and reading a fixed two
    leaves the tail in the buffer to arrive later as a phantom keypress."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch != "\x1b":
            return ch
        seq = sys.stdin.read(1)
        if seq not in ("[", "O"):
            return "esc"
        while True:
            nxt = sys.stdin.read(1)
            seq += nxt
            if nxt.isalpha() or nxt == "~" or len(seq) > 6:
                break
        return SEQUENCES.get(seq, "esc")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_word(prompt):
    """A typed line. Confirmation should cost more than brushing the keyboard."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
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


def clamp_view(cursor, top, height, count):
    """Keep the cursor inside the viewport, and the viewport inside the list.

    Pulled out of the draw loop so it can be tested: getting this wrong is what
    makes a long list unusable, and it is invisible until the list outgrows the
    terminal.
    """
    if count <= 0:
        return 0, 0
    cursor = max(0, min(cursor, count - 1))
    top = min(top, cursor)                    # cursor above the window: follow up
    top = max(top, cursor - height + 1)       # cursor below it: follow down
    top = max(0, min(top, max(0, count - height)))
    return cursor, top


def viewport_height():
    return max(3, reclaim.shutil.get_terminal_size((80, 24)).lines - CHROME_ROWS)


def draw(rows, totals, cursor, selected, status, top, actionable_only):
    width = max(40, reclaim.shutil.get_terminal_size((80, 24)).columns)
    height = viewport_height()
    sys.stdout.write("\033[H\033[J")

    ticked = [r for r in rows if reclaim.item_key(r) in selected]
    chosen = sum(r[3] for r in ticked)
    head = (f"{C['bold']}footprint{C['off']}  "
            f"{C[SAFE]}SAFE {reclaim.human(totals[SAFE])}{C['off']}  "
            f"{C[REVIEW]}REVIEW {reclaim.human(totals[REVIEW])}{C['off']}  "
            f"{C[BLOCKED]}BLOCKED {reclaim.human(totals[BLOCKED])}{C['off']}")
    if ticked:
        head += f"   {C['bold']}{len(ticked)} ticked · {reclaim.human(chosen)}{C['off']}"
    if actionable_only:
        head += f"   {C[REVIEW]}[blocked hidden]{C['off']}"
    print(head)

    position = f"{cursor + 1}/{len(rows)}" if rows else "0/0"
    above, below = top, max(0, len(rows) - top - height)
    marker = (f"{C['dim']}  ▲ {above} above{C['off']}" if above else "")
    marker += (f"{C['dim']}   ▼ {below} below{C['off']}" if below else "")
    print(f"{C['dim']}  {position}{C['off']}{marker}\n")

    name_w = max(16, min(42, width - 42))
    detail_w = max(10, width - name_w - 20)

    for i in range(top, min(top + height, len(rows))):
        cls, kind, name, size, reason, target = rows[i]
        # A blocked row gets no box rather than an unticked one: an empty checkbox
        # invites a tick, and that row is the one thing that cannot be ticked.
        if target is None:
            box = f"{C['dim']} –  {C['off']}"
        elif reclaim.item_key(rows[i]) in selected:
            box = f"{C[cls]}{C['bold']}[✓]{C['off']} "
        else:
            box = f"{C['dim']}[ ]{C['off']} "
        arrow = f"{C['bold']}❯{C['off']}" if i == cursor else " "
        detail = f"{kind} · {reason}"
        print(f"{arrow}{box}{C[cls]}{reclaim.human(size):>7}{C['off']}  "
              f"{reclaim.ellipsis(name, name_w):<{name_w}} "
              f"{C['dim']}{reclaim.ellipsis(detail, detail_w)}{C['off']}")

    print()
    if status:
        print(f"{C['dim']} {status}{C['off']}")
    print(f"{C['dim']} ↑↓ move · g/G top/bottom · PgUp/PgDn page · space ticks · "
          f"a all safe · n none · f hide blocked · d reclaim · r rescan · q quit{C['off']}",
          end="")
    sys.stdout.flush()


def confirm(rows, selected):
    sys.stdout.write("\033[H\033[J")
    total = sum(rows[i][3] for i in selected)
    print(f"{C['bold']}About to delete {len(selected)} items · "
          f"{reclaim.human(total)}{C['off']}\n")
    shown = sorted(selected, key=lambda i: -rows[i][3])
    for i in shown[:18]:
        cls, kind, name, size, _, _ = rows[i]
        print(f"  {C[cls]}{reclaim.human(size):>7}{C['off']}  {name}  "
              f"{C['dim']}{kind}{C['off']}")
    if len(shown) > 18:
        print(f"  {C['dim']}… and {len(shown) - 18} more{C['off']}")
    print(f"\n{C['dim']}  Every fence is re-checked now, not when the list was built;"
          f"\n  anything that changed is skipped and told to you."
          f"\n  A worktree's branch is bundled before its checkout goes.{C['off']}\n")
    return read_word(f"  Type {C['bold']}delete{C['off']} to go ahead, "
                     f"anything else to cancel: ") == "delete"


def execute(rows, selected, cwd):
    sys.stdout.write("\033[H\033[J")
    print(f"{C['bold']}Reclaiming{C['off']}\n")
    freed = done = skipped = 0
    for i in sorted(selected, key=lambda i: -rows[i][3]):
        _, _, name, size, _, target = rows[i]
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


def visible(all_rows, actionable_only):
    return [r for r in all_rows if r[5]] if actionable_only else all_rows


def main():
    cwd = os.environ.get("HERDR_PANE_CWD") or os.getcwd()
    sys.stdout.write("\033[H\033[J  scanning…\n")
    sys.stdout.flush()
    all_rows, totals = scan(cwd)
    actionable_only = False
    rows = visible(all_rows, actionable_only)
    cursor = top = 0
    selected = set()
    status = ""

    while True:
        height = viewport_height()
        cursor, top = clamp_view(cursor, top, height, len(rows))

        draw(rows, totals, cursor, selected, status, top, actionable_only)
        status = ""
        key = read_key()

        if key in ("q", "Q", "esc", "\x03"):
            sys.stdout.write("\033[H\033[J")
            return
        if key in ("j", "down"):
            cursor = min(cursor + 1, len(rows) - 1)
        elif key in ("k", "up"):
            cursor = max(cursor - 1, 0)
        elif key in ("g", "home"):
            cursor = 0
        elif key in ("G", "end"):
            cursor = max(0, len(rows) - 1)
        elif key in ("pgdn", "\x04"):
            cursor = min(cursor + height, len(rows) - 1)
        elif key in ("pgup", "\x15"):
            cursor = max(cursor - height, 0)
        elif key in (" ", "x", "\r", "\n"):
            if not rows:
                continue
            row = rows[cursor]
            if row[5] is None:
                status = "blocked — no box to tick; the row says why"
            else:
                selected.symmetric_difference_update({reclaim.item_key(row)})
                cursor = min(cursor + 1, len(rows) - 1)
        elif key == "a":
            selected |= {reclaim.item_key(r) for r in rows if r[0] == SAFE and r[5]}
            status = "ticked everything classed SAFE"
        elif key == "n":
            selected.clear()
        elif key == "f":
            actionable_only = not actionable_only
            rows = visible(all_rows, actionable_only)
            cursor = top = 0
            status = "showing only rows you can act on" if actionable_only else "showing everything"
        elif key == "r":
            all_rows, totals = scan(cwd)
            rows = visible(all_rows, actionable_only)
            cursor = top = 0
            selected.clear()
            status = "rescanned"
        elif key == "d":
            if not selected:
                status = "nothing ticked — space ticks a row, a ticks all SAFE"
                continue
            chosen = [i for i, r in enumerate(rows) if reclaim.item_key(r) in selected]
            if confirm(rows, set(chosen)):
                execute(rows, set(chosen), cwd)
                all_rows, totals = scan(cwd)
                rows = visible(all_rows, actionable_only)
                cursor = top = 0
                selected.clear()
                status = "rescanned after reclaiming"
            else:
                status = "cancelled, nothing was deleted"


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write("\033[H\033[J")
