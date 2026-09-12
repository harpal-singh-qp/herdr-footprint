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


def docker_rows():
    raw = run(["docker", "system", "df", "-v", "--format", "{{json .}}"], timeout=60)
    if not raw:
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
                         size, "untagged layer, nothing references it"))
        else:
            rows.append((REVIEW, "docker image", name, size,
                         "no container uses it; may be a base you rebuild from"))

    for vol in d.get("Volumes", []):
        size = parse_size(vol.get("Size"))
        links = int(vol.get("Links") or 0)
        if links > 0:
            rows.append((BLOCKED, "docker volume", vol.get("Name", ""), size,
                         f"in use by {links} container(s)"))
        else:
            rows.append((REVIEW, "docker volume", vol.get("Name", ""), size,
                         "unused, but a volume is where data lives"))

    # Docker renders booleans as the strings "true"/"false", so test the text.
    # Shared layers are counted against several images; summing them inflates the
    # figure ~4.5x. Excluding them reproduces docker's own "RECLAIMABLE" number.
    idle_layers = [c for c in d.get("BuildCache", [])
                   if str(c.get("InUse", "false")).lower() != "true"
                   and str(c.get("Shared", "false")).lower() != "true"]
    cache = sum(parse_size(c.get("Size")) for c in idle_layers)
    if cache:
        rows.append((SAFE, "docker build cache", f"{len(idle_layers)} idle layers",
                     cache, "rebuildable by definition"))
    return rows


NOISE_FLOOR = 1024 ** 2  # below this an item is not worth its own line


def fold_noise(rows):
    """Collapse sub-megabyte items of one kind into a single counted row."""
    keep, small = [], {}
    for row in rows:
        cls, kind, _, size, _ = row
        if size < NOISE_FLOOR:
            bucket = small.setdefault((cls, kind), [0, 0])
            bucket[0] += 1
            bucket[1] += size
        else:
            keep.append(row)
    for (cls, kind), (count, total) in small.items():
        keep.append((cls, kind, f"{count} items under 1M", total, "too small to matter individually"))
    return keep


def git_repos():
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
        return [row, (cls, "build artifacts", label, art_bytes, reason)]

    if os.path.realpath(path) == os.path.realpath(repo):
        return with_artifacts((BLOCKED, "git worktree", label, size, "the main checkout"))
    if cwd and os.path.realpath(cwd).startswith(os.path.realpath(path)):
        return with_artifacts((BLOCKED, "git worktree", label, size, "you are standing in it"))
    if run(["git", "-C", path, "status", "--porcelain"]).strip():
        return with_artifacts((BLOCKED, "git worktree", label, size, "uncommitted changes"))
    if not branch:
        return with_artifacts((REVIEW, "git worktree", label, size, "detached HEAD, no branch to check"))
    if branch not in merged:
        return with_artifacts((BLOCKED, "git worktree", label, size, "branch not merged into the base"))

    idle = idle_days(path)
    if idle is not None and idle >= IDLE_DAYS:
        return with_artifacts((SAFE, "git worktree", label, size, f"merged and idle {idle}d"))
    return with_artifacts((REVIEW, "git worktree", label, size,
                           f"merged, but active {idle}d ago" if idle is not None else "merged"))


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


def transcript_rows():
    rows = []
    for label, path in (("claude", "~/.claude/projects"), ("codex", "~/.codex/sessions"),
                        ("opencode", "~/.local/share/opencode"), ("grok", "~/.grok")):
        full = os.path.expanduser(path)
        if not os.path.isdir(full):
            continue
        size = dir_size(full)
        if size > 10 * 1024**2:
            rows.append((REVIEW, "agent transcripts", f"{label} ({path})", size,
                         "your own history; resumable sessions live here"))
    return rows


def render(rows, elapsed):
    order = {SAFE: 0, REVIEW: 1, BLOCKED: 2}
    rows.sort(key=lambda r: (order[r[0]], -r[3]))
    totals = {SAFE: 0, REVIEW: 0, BLOCKED: 0}
    for cls, _, _, size, _ in rows:
        totals[cls] += size

    width = max(40, shutil.get_terminal_size((80, 24)).columns)
    tail = (f"   scanned in {elapsed:.1f}s · read-only, nothing was deleted"
            if width >= 80 else f"  {elapsed:.1f}s · read-only")
    print(f"{C['bold']}footprint · reclaimable space{C['off']}{C['dim']}{tail}{C['off']}\n")
    print(f"  {C[SAFE]}SAFE {human(totals[SAFE]):>8}{C['off']}   "
          f"{C[REVIEW]}REVIEW {human(totals[REVIEW]):>8}{C['off']}   "
          f"{C[BLOCKED]}BLOCKED {human(totals[BLOCKED]):>8}{C['off']}\n")

    # A plugin pane is often a narrow split. Fit the columns to it rather than
    # letting every row wrap into two unreadable ones.
    name_w = max(16, min(44, width - 34))
    detail_w = width - name_w - 13

    current = None
    for cls, kind, name, size, reason in rows:
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
        print(f"  {C['dim']}nothing found — no docker, no extra worktrees, no transcripts{C['off']}")
    if width >= 80:
        print(f"\n{C['dim']}  SAFE is rebuildable or merged-and-idle. REVIEW holds data worth")
        print("  a glance. BLOCKED shows why the space is not yours yet.")
        print(f"  v0.3 reclaims; v0.2 only looks.{C['off']}")
    else:
        print(f"\n{C['dim']}  SAFE: rebuildable. REVIEW: holds data.")
        print(f"  BLOCKED: why it is not yours yet.{C['off']}")


def main():
    start = time.time()
    cwd = os.environ.get("HERDR_PANE_CWD") or os.getcwd()
    rows = fold_noise(docker_rows() + worktree_rows(cwd) + transcript_rows())
    render(rows, time.time() - start)


if __name__ == "__main__":
    main()
