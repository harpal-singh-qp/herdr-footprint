#!/usr/bin/env python3
"""Scan the machine for reclaimable space and classify every item.

Read-only by construction: this module runs `docker system df`, `git`, and `du`,
and prints a report. It never deletes, prunes, or writes outside stdout.

Classification follows one rule each:

  SAFE     proven disposable - rebuildable by definition, or merged and idle
  REVIEW   provably unused, but holds something a human should glance at first
  BLOCKED  a fence failed; always shown WITH the reason, never silently dropped

A BLOCKED row is not a failure to classify. It is the answer: this is why you
cannot have that space back yet.
"""
import json
import os
import shutil
import re
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
IDLE_DAYS = 7
SAFE, REVIEW, BLOCKED = "SAFE", "REVIEW", "BLOCKED"

C = {
    SAFE: "\033[32m", REVIEW: "\033[33m", BLOCKED: "\033[31m",
    "dim": "\033[2m", "bold": "\033[1m", "off": "\033[0m", "head": "\033[36m",
}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = dict.fromkeys(C, "")


def run(cmd, timeout=30):
    """Never raises. A tool that is missing or slow yields no rows, not a crash."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def human(b):
    b = float(b or 0)
    for unit in ("B", "K", "M", "G", "T"):
        if b < 1024 or unit == "T":
            return f"{b:.0f}{unit}" if unit in ("B", "K") or b >= 100 else f"{b:.1f}{unit}"
        b /= 1024


SIZE = re.compile(r"^\s*([0-9.]+)\s*([KMGT]?)i?B?\s*$", re.I)
SCALE = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def ellipsis(text, width):
    text = str(text)
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def parse_size(text):
    """Docker prints '1.234GB', '512MB', '594kB', '0B'. Case varies. Returns bytes."""
    match = SIZE.match(text or "0B")
    if not match:
        return 0
    return float(match.group(1)) * SCALE[match.group(2).upper()]


NOTES = []


def docker_rows():
    if not shutil.which("docker"):
        NOTES.append("docker is not installed — images, volumes and build cache not checked")
        return []
    raw = run(["docker", "system", "df", "-v", "--format", "{{json .}}"], timeout=60)
    if not raw:
        NOTES.append("docker is installed but not responding — its space was not checked")
        return []
    try:
        d = json.loads(raw)
    except ValueError:
        return []
    rows = []

    for img in d.get("Images", []):
        size = parse_size(img.get("Size"))
        name = f"{img.get('Repository')}:{img.get('Tag')}"
        in_use = str(img.get("Containers", "0")) not in ("0", "N/A", "")
        if in_use:
            continue  # an image backing a live container is not reclaimable space
        if img.get("Repository") == "<none>":
            rows.append((SAFE, "docker image", f"dangling {img.get('ID','')[:12]}",
                         size, "untagged layer, nothing references it",
                         {"op": "docker_image", "id": img.get("ID", "")}))
        else:
            rows.append((REVIEW, "docker image", name, size,
                         "no container uses it; may be a base you rebuild from",
                         {"op": "docker_image", "id": img.get("ID", "")}))

    for vol in d.get("Volumes", []):
        size = parse_size(vol.get("Size"))
        links = int(vol.get("Links") or 0)
        if links > 0:
            rows.append((BLOCKED, "docker volume", vol.get("Name", ""), size,
                         f"in use by {links} container(s)", None))
        else:
            rows.append((REVIEW, "docker volume", vol.get("Name", ""), size,
                         "unused, but a volume is where data lives",
                         {"op": "docker_volume", "name": vol.get("Name", "")}))

    # Docker renders booleans as the strings "true"/"false", so test the text.
    # Shared layers are counted against several images; summing them inflates the
    # figure ~4.5x. Excluding them reproduces docker's own "RECLAIMABLE" number.
    idle_layers = [c for c in d.get("BuildCache", [])
                   if str(c.get("InUse", "false")).lower() != "true"
                   and str(c.get("Shared", "false")).lower() != "true"]
    cache = sum(parse_size(c.get("Size")) for c in idle_layers)
    if cache:
        # The only bulk operation here, because docker exposes no per-layer
        # delete. The row is already the unit, and the label says so.
        rows.append((SAFE, "docker build cache", f"{len(idle_layers)} idle layers",
                     cache, "rebuildable by definition; pruned as one unit",
                     {"op": "build_cache"}))
    return rows


NOISE_FLOOR = 1024 ** 2  # below this an item is not worth its own line


def fold_noise(rows):
    """Collapse sub-megabyte items of one kind into a single counted row."""
    keep, small = [], {}
    for row in rows:
        cls, kind, _, size, _, _ = row
        if size < NOISE_FLOOR:
            bucket = small.setdefault((cls, kind), [0, 0])
            bucket[0] += 1
            bucket[1] += size
        else:
            keep.append(row)
    for (cls, kind), (count, total) in small.items():
        # Folded rows lose their individual targets, so they are not deletable.
        keep.append((cls, kind, f"{count} items under 1M", total, "too small to matter individually", None))
    return keep


def git_repos():
    if not shutil.which("git"):
        NOTES.append("git is not installed — worktrees were not checked")
        return
    """Top-level directories under $HOME that are git repositories."""
    for entry in sorted(os.listdir(HOME)):
        path = os.path.join(HOME, entry)
        if os.path.isdir(os.path.join(path, ".git")):
            yield path


ARTIFACT_DIRS = ("node_modules", "target", "dist", "build", ".next", ".turbo", "vendor")
ARTIFACT_DEPTH = 5


def artifacts_in(path):
    """(total bytes, dir count, kinds) of rebuildable directories inside a worktree.

    `-prune` stops find descending into a match, so a 1.5 GB node_modules costs
    one stat rather than a walk of every package inside it.
    """
    expr = []
    for i, name in enumerate(ARTIFACT_DIRS):
        expr += (["-o"] if i else []) + ["-name", name]
    listing = run(["find", path, "-maxdepth", str(ARTIFACT_DEPTH), "-type", "d",
                   "("] + expr + [")", "-prune", "-print"], timeout=60)
    dirs = [d for d in listing.splitlines() if d]
    if not dirs:
        return 0, 0, ()
    total = sum(dir_size(d) for d in dirs)
    kinds = sorted({os.path.basename(d) for d in dirs})
    return total, len(dirs), tuple(kinds)


def worktree_rows(cwd):
    rows = []
    seen = set()
    for repo in git_repos():
        porcelain = run(["git", "-C", repo, "worktree", "list", "--porcelain"])
        if not porcelain:
            continue
        base = None
        for ref in ("origin/main", "origin/master", "main", "master"):
            if run(["git", "-C", repo, "rev-parse", "--verify", "-q", ref]).strip():
                base = ref
                break
        merged = set()
        if base:
            for line in run(["git", "-C", repo, "branch", "--merged", base]).splitlines():
                merged.add(line.strip().lstrip("* ").strip())

        path = branch = None
        for line in porcelain.splitlines() + [""]:
            if line.startswith("worktree "):
                path = line.split(" ", 1)[1]
                branch = None
            elif line.startswith("branch "):
                branch = line.split(" ", 1)[1].replace("refs/heads/", "")
            elif not line and path:
                if path not in seen:
                    seen.add(path)
                    rows.extend(classify_worktree(repo, path, branch, merged, cwd))
                path = branch = None
    return [r for r in rows if r]


def classify_worktree(repo, path, branch, merged, cwd):
    """One row for the worktree, plus at most one for its build artifacts."""
    if not os.path.isdir(path):
        return []
    size = dir_size(path)
    label = f"{os.path.basename(repo)}/{branch or os.path.basename(path)}"

    # Subtracted from the worktree row: `du` already counted these, and listing
    # both unsubtracted would report the same bytes twice in the totals.
    art_bytes, art_count, art_kinds = artifacts_in(path)
    size = max(0, size - art_bytes)

    def with_artifacts(row):
        if not art_bytes:
            return [row]
        # A worktree you cannot delete is one you are working in, where removing
        # node_modules stops a running dev server. Rebuildable, but not today.
        cls = REVIEW if row[0] == BLOCKED else SAFE
        reason = f"{art_count} dirs ({', '.join(art_kinds)}) — rebuildable"
        if cls is REVIEW:
            reason += "; the worktree is in use"
        return [row, (cls, "build artifacts", label, art_bytes, reason,
                      {"op": "artifacts", "path": path})]

    if os.path.realpath(path) == os.path.realpath(repo):
        return with_artifacts((BLOCKED, "git worktree", label, size, "the main checkout", None))
    if cwd and os.path.realpath(cwd).startswith(os.path.realpath(path)):
        return with_artifacts((BLOCKED, "git worktree", label, size, "you are standing in it", None))
    if run(["git", "-C", path, "status", "--porcelain"]).strip():
        return with_artifacts((BLOCKED, "git worktree", label, size, "uncommitted changes", None))
    if not branch:
        return with_artifacts((REVIEW, "git worktree", label, size, "detached HEAD, no branch to check",
                               {"op": "worktree", "repo": repo, "path": path, "branch": branch}))
    if branch not in merged:
        return with_artifacts((BLOCKED, "git worktree", label, size, "branch not merged into the base", None))

    idle = idle_days(path)
    if idle is not None and idle >= IDLE_DAYS:
        return with_artifacts((SAFE, "git worktree", label, size, f"merged and idle {idle}d",
                               {"op": "worktree", "repo": repo, "path": path, "branch": branch}))
    return with_artifacts((REVIEW, "git worktree", label, size,
                           f"merged, but active {idle}d ago" if idle is not None else "merged",
                           {"op": "worktree", "repo": repo, "path": path, "branch": branch}))


def idle_days(path):
    stamp = run(["git", "-C", path, "log", "-1", "--format=%ct"]).strip()
    if not stamp.isdigit():
        return None
    return int((time.time() - int(stamp)) / 86400)


def dir_size(path):
    """`du -sk` is POSIX; GNU's --block-size is not, and this claims macOS."""
    out = run(["du", "-sk", path], timeout=120)
    try:
        return int(out.split()[0]) * 1024
    except (IndexError, ValueError):
        return 0


TRANSCRIPT_STORES = (("claude", "~/.claude/projects"), ("codex", "~/.codex/sessions"),
                     ("opencode", "~/.local/share/opencode"), ("grok", "~/.grok"))
STALE_DAYS = int(os.environ.get("FOOTPRINT_TRANSCRIPT_DAYS") or 90)


def split_by_age(root, days):
    """(stale bytes, fresh bytes, stale file count) under a directory tree.

    A lumped "1.9G of history" is not a decision anyone can act on. Split at an
    age and it becomes one: the old half is the part you will never reopen.
    """
    cutoff = time.time() - days * 86400
    stale = fresh = count = 0
    for dirpath, _, names in os.walk(root, onerror=lambda e: None):
        for name in names:
            try:
                st = os.stat(os.path.join(dirpath, name))
            except OSError:
                continue
            if st.st_mtime < cutoff:
                stale += st.st_size
                count += 1
            else:
                fresh += st.st_size
    return stale, fresh, count


def transcript_rows():
    rows = []
    for label, path in TRANSCRIPT_STORES:
        full = os.path.expanduser(path)
        if not os.path.isdir(full):
            continue
        stale, fresh, count = split_by_age(full, STALE_DAYS)
        if stale:
            rows.append((REVIEW, "agent transcripts", f"{label} · older than {STALE_DAYS}d",
                         stale, f"{count} files you are unlikely to reopen",
                         {"op": "transcripts", "root": full, "days": STALE_DAYS}))
        if fresh:
            rows.append((BLOCKED, "agent transcripts", f"{label} · last {STALE_DAYS}d", fresh,
                         "recent sessions, still resumable", None))
    return rows


STATE_DIR = (os.environ.get("HERDR_PLUGIN_STATE_DIR")
             or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".state"))
HISTORY = os.path.join(STATE_DIR, "history.jsonl")
SEEN = os.path.join(STATE_DIR, "seen.json")
HISTORY_MAX = 500
COMPARE_AFTER_HOURS = 12
FORGET_DAYS = 30


def item_key(row):
    _, kind, name, _, _, _ = row
    return f"{kind}\u0000{name}"


def load_seen():
    try:
        with open(SEEN) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def record(rows, totals):
    """Append this scan's totals and remember when each item first appeared.

    The first-seen map is the point. A row that has been SAFE for three weeks and
    is still here is one you keep declining to act on - which is a fact about the
    rule, not about you, and the thing v0.3 most needs to know before it deletes.
    """
    now = int(time.time())
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(HISTORY, "a") as fh:
            fh.write(json.dumps({"t": now, "safe": totals[SAFE],
                                 "review": totals[REVIEW], "blocked": totals[BLOCKED]}) + "\n")
        with open(HISTORY) as fh:
            lines = fh.readlines()
        if len(lines) > HISTORY_MAX:
            with open(HISTORY, "w") as fh:
                fh.writelines(lines[-HISTORY_MAX:])

        seen = load_seen()
        keys = {item_key(r) for r in rows}
        for key in keys:
            seen.setdefault(key, now)
        cutoff = now - FORGET_DAYS * 86400
        seen = {k: v for k, v in seen.items() if k in keys or v > cutoff}
        with open(SEEN, "w") as fh:
            json.dump(seen, fh)
    except OSError:
        pass  # history is a nicety; never let it break a scan


def previous_totals():
    """The most recent scan older than COMPARE_AFTER_HOURS, so pressing r repeatedly
    does not collapse the comparison window to nothing."""
    try:
        with open(HISTORY) as fh:
            entries = [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError):
        return None
    cutoff = time.time() - COMPARE_AFTER_HOURS * 3600
    older = [e for e in entries if e.get("t", 0) < cutoff]
    return older[-1] if older else None


def delta_text(now_bytes, then_bytes):
    diff = now_bytes - then_bytes
    if abs(diff) < 50 * 1024**2:
        return ""
    return f" {'+' if diff > 0 else '-'}{human(abs(diff))}"


def ago(seconds):
    days = int(seconds / 86400)
    if days >= 1:
        return f"{days}d"
    return f"{max(1, int(seconds / 3600))}h"


def render(rows, elapsed):
    order = {SAFE: 0, REVIEW: 1, BLOCKED: 2}
    rows.sort(key=lambda r: (order[r[0]], -r[3]))
    totals = {SAFE: 0, REVIEW: 0, BLOCKED: 0}
    for cls, _, _, size, _, _ in rows:
        totals[cls] += size

    width = max(40, shutil.get_terminal_size((80, 24)).columns)
    tail = (f"   scanned in {elapsed:.1f}s · read-only, nothing was deleted"
            if width >= 80 else f"  {elapsed:.1f}s · read-only")
    print(f"{C['bold']}footprint · reclaimable space{C['off']}{C['dim']}{tail}{C['off']}\n")
    prev = previous_totals()
    if prev:
        span = ago(time.time() - prev["t"])
        d = (delta_text(totals[SAFE], prev.get("safe", 0)),
             delta_text(totals[REVIEW], prev.get("review", 0)),
             delta_text(totals[BLOCKED], prev.get("blocked", 0)))
    else:
        span, d = "", ("", "", "")
    print(f"  {C[SAFE]}SAFE {human(totals[SAFE]):>8}{C['off']}{C['dim']}{d[0]}{C['off']}   "
          f"{C[REVIEW]}REVIEW {human(totals[REVIEW]):>8}{C['off']}{C['dim']}{d[1]}{C['off']}   "
          f"{C[BLOCKED]}BLOCKED {human(totals[BLOCKED]):>8}{C['off']}{C['dim']}{d[2]}{C['off']}"
          + (f"{C['dim']}   vs {span} ago{C['off']}" if prev and any(d) else "") + "\n")

    # A plugin pane is often a narrow split. Fit the columns to it rather than
    # letting every row wrap into two unreadable ones.
    name_w = max(16, min(44, width - 34))
    detail_w = width - name_w - 13

    seen = load_seen()
    now = time.time()

    current = None
    for cls, kind, name, size, reason, target in rows:
        age = now - seen.get(item_key((cls, kind, name, size, reason, target)), now)
        if age >= 3 * 86400:
            reason = f"{reason} · here {ago(age)}"
        if cls != current:
            current = cls
            print(f"{C[cls]}{C['bold']}  {cls}{C['off']}")
        detail = f"{kind} · {reason}"
        if detail_w < 12:
            print(f"  {C[cls]}{human(size):>7}{C['off']}  {ellipsis(name, name_w)}")
            print(f"           {C['dim']}{ellipsis(detail, width - 12)}{C['off']}")
        else:
            print(f"  {C[cls]}{human(size):>7}{C['off']}  {ellipsis(name, name_w):<{name_w}} "
                  f"{C['dim']}{ellipsis(detail, detail_w)}{C['off']}")
    if not rows:
        print(f"  {C['dim']}nothing found{C['off']}")
    for note in NOTES:
        print(f"  {C[REVIEW]}!{C['off']} {C['dim']}{ellipsis(note, width - 5)}{C['off']}")
    if width >= 80:
        print(f"\n{C['dim']}  SAFE is rebuildable or merged-and-idle. REVIEW holds data worth")
        print("  a glance. BLOCKED shows why the space is not yours yet.")
        print(f"  space chooses · d acts · fences re-checked at that moment.{C['off']}")
    else:
        print(f"\n{C['dim']}  SAFE: rebuildable. REVIEW: holds data.")
        print(f"  BLOCKED: why it is not yours yet.{C['off']}")


def main():
    start = time.time()
    cwd = os.environ.get("HERDR_PANE_CWD") or os.getcwd()
    rows = fold_noise(docker_rows() + worktree_rows(cwd) + transcript_rows())
    totals = {SAFE: 0, REVIEW: 0, BLOCKED: 0}
    for cls, _, _, size, _, _ in rows:
        totals[cls] += size
    record(rows, totals)
    render(rows, time.time() - start)


if __name__ == "__main__":
    main()
