#!/usr/bin/env python3
"""Execute a reclaim, itemised, with every fence re-checked at the moment of action.

The scan that classified an item may be minutes old. In those minutes a container
may have started against a volume, a worktree may have gained uncommitted work, or
a branch may have stopped being merged. So nothing here trusts the scan: each
operation re-derives the fence that justified it, and refuses if it no longer holds.

Rules this module will not break:

1. No blanket prune. Every operation names its target. The single exception is
   docker's build cache, which exposes no per-layer delete; that row is labelled
   as one unit precisely because it can only be handled as one.
2. A git bundle is written before any worktree is removed, so the branch survives
   even when the checkout does not.
3. Anything BLOCKED carries no target at all and cannot reach this module.
4. Never sudo. An operation that would need it is out of scope, not escalated.
"""
import json
import os
import shutil
import subprocess
import time

BACKUP_DIR = os.path.expanduser("~/.local/state/herdr/plugins/footprint/bundles")
ARTIFACT_NAMES = ("node_modules", "target", "dist", "build", ".next", ".turbo", "vendor")


def run(cmd, timeout=300):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def _first_line(text, limit=90):
    return (text.splitlines() or [""])[0][:limit]


def _docker_json():
    ok, out = run(["docker", "system", "df", "-v", "--format", "{{json .}}"], timeout=60)
    if not ok:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _image_still_unused(image_id):
    d = _docker_json()
    if d is None:
        return False, "docker is not answering"
    for img in d.get("Images", []):
        if img.get("ID") == image_id:
            if str(img.get("Containers", "0")) not in ("0", "N/A", ""):
                return False, "a container now uses it"
            return True, ""
    return False, "no longer present"


def _volume_still_unused(name):
    d = _docker_json()
    if d is None:
        return False, "docker is not answering"
    for vol in d.get("Volumes", []):
        if vol.get("Name") == name:
            if int(vol.get("Links") or 0) > 0:
                return False, "a container now uses it"
            return True, ""
    return False, "no longer present"


def _worktree_still_safe(repo, path, branch, cwd):
    if not os.path.isdir(path):
        return False, "already gone"
    if os.path.realpath(path) == os.path.realpath(repo):
        return False, "this is the main checkout"
    if cwd and os.path.realpath(cwd).startswith(os.path.realpath(path)):
        return False, "you are standing in it"
    ok, out = run(["git", "-C", path, "status", "--porcelain"])
    if not ok:
        return False, "cannot read its status"
    if out.strip():
        return False, "it has uncommitted changes now"
    if branch:
        for base in ("origin/main", "origin/master", "main", "master"):
            ok, _ = run(["git", "-C", repo, "rev-parse", "--verify", "-q", base])
            if not ok:
                continue
            ok, merged = run(["git", "-C", repo, "branch", "--merged", base])
            names = {l.strip().lstrip("* ").strip() for l in merged.splitlines()}
            if branch not in names:
                return False, f"branch is no longer merged into {base}"
            break
    return True, ""


def _bundle(repo, branch):
    """Write a delta-only bundle of a branch before its worktree goes.

    An empty bundle means the branch holds nothing its base does not, so there is
    nothing to lose and nothing worth writing.
    """
    if not branch:
        return True, "detached HEAD, nothing to bundle"
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
    except OSError as exc:
        return False, f"cannot create backup dir: {exc}"

    base = None
    for candidate in ("origin/main", "origin/master", "main", "master"):
        ok, _ = run(["git", "-C", repo, "rev-parse", "--verify", "-q", candidate])
        if ok:
            base = candidate
            break

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(BACKUP_DIR,
                       f"{os.path.basename(repo)}-{branch.replace('/', '-')}-{stamp}.bundle")
    cmd = ["git", "-C", repo, "bundle", "create", out, branch]
    if base:
        cmd += ["--not", base]
    ok, msg = run(cmd)
    if not ok:
        lowered = msg.lower()
        if "no commits" in lowered or "empty bundle" in lowered:
            return True, "nothing unique to bundle"
        return False, f"bundle failed: {_first_line(msg) or 'unknown'}"
    return True, f"bundled to {os.path.basename(out)}"


def _remove_tree(path):
    try:
        shutil.rmtree(path)
        return True
    except OSError:
        return False


def perform(target, cwd=None):
    """Act on one classified item. Returns (ok, message)."""
    op = target.get("op")

    if op == "docker_image":
        ok, why = _image_still_unused(target["id"])
        if not ok:
            return False, f"skipped — {why}"
        ok, msg = run(["docker", "rmi", target["id"]])
        return ok, "removed" if ok else _first_line(msg)

    if op == "docker_volume":
        ok, why = _volume_still_unused(target["name"])
        if not ok:
            return False, f"skipped — {why}"
        ok, msg = run(["docker", "volume", "rm", target["name"]])
        return ok, "removed" if ok else _first_line(msg)

    if op == "build_cache":
        ok, msg = run(["docker", "builder", "prune", "-f"], timeout=600)
        return ok, "pruned" if ok else _first_line(msg)

    if op == "worktree":
        repo, path, branch = target["repo"], target["path"], target.get("branch")
        ok, why = _worktree_still_safe(repo, path, branch, cwd)
        if not ok:
            return False, f"skipped — {why}"
        ok, note = _bundle(repo, branch)
        if not ok:
            return False, note
        ok, msg = run(["git", "-C", repo, "worktree", "remove", path])
        if not ok:
            return False, _first_line(msg)
        return True, f"removed · {note}"

    if op == "artifacts":
        root = target["path"]
        if not os.path.isdir(root):
            return False, "skipped — worktree is gone"
        gone = 0
        for dirpath, dirnames, _ in os.walk(root):
            for name in list(dirnames):
                if name in ARTIFACT_NAMES:
                    gone += _remove_tree(os.path.join(dirpath, name))
                    dirnames.remove(name)
        return gone > 0, f"removed {gone} directories" if gone else "nothing removed"

    if op == "transcripts":
        root, days = target["root"], target["days"]
        cutoff = time.time() - days * 86400
        gone = 0
        for dirpath, _, names in os.walk(root):
            for name in names:
                full = os.path.join(dirpath, name)
                try:
                    if os.stat(full).st_mtime < cutoff:
                        os.remove(full)
                        gone += 1
                except OSError:
                    continue
        return gone > 0, f"removed {gone} files" if gone else "nothing removed"

    return False, f"no handler for {op!r}"
