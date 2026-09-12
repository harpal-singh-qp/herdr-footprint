#!/usr/bin/env python3
"""Prove the reclaim actions act, and that their fences refuse.

Everything here happens inside directories this test creates and owns. Nothing
touches a real repository, a real image, or a real transcript store. The docker
operations are deliberately not covered: exercising them would mean deleting
images on whatever machine runs the suite, which is not a trade any test should
make. Their fences are pure functions over `docker system df` output and are
covered by the parser tests instead.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIT_ENV = {**os.environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "bin", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(*args, **kw):
    return subprocess.run(["git", *args], env=GIT_ENV, capture_output=True, **kw)


def test_artifacts(a):
    work = tempfile.mkdtemp()
    os.makedirs(os.path.join(work, "node_modules", "pkg"))
    os.makedirs(os.path.join(work, "src"))
    open(os.path.join(work, "node_modules", "pkg", "f"), "w").write("x")
    open(os.path.join(work, "src", "main.py"), "w").write("keep me")

    ok, _ = a.perform({"op": "artifacts", "path": work})
    check("artifacts: reports success", ok)
    check("artifacts: build dir removed",
          not os.path.exists(os.path.join(work, "node_modules")))
    # The whole point of naming directories rather than globbing: source survives.
    check("artifacts: source untouched",
          os.path.exists(os.path.join(work, "src", "main.py")))

    ok, _ = a.perform({"op": "artifacts", "path": "/nonexistent/path"})
    check("artifacts: refuses a path that is gone", not ok)


def test_transcripts(a):
    work = tempfile.mkdtemp()
    old = os.path.join(work, "old.jsonl")
    new = os.path.join(work, "new.jsonl")
    open(old, "w").write("o")
    open(new, "w").write("n")
    past = time.time() - 200 * 86400
    os.utime(old, (past, past))

    ok, _ = a.perform({"op": "transcripts", "root": work, "days": 90})
    check("transcripts: reports success", ok)
    check("transcripts: stale file removed", not os.path.exists(old))
    # Deleting a resumable session would be the worst failure this tool could have.
    check("transcripts: recent file kept", os.path.exists(new))


def build_repo():
    repo = tempfile.mkdtemp()
    git("init", "-q", "-b", "main", repo)
    open(os.path.join(repo, "a"), "w").write("1")
    git("-C", repo, "add", "-A")
    git("-C", repo, "commit", "-qm", "initial")
    return repo


def test_worktree_fences(a):
    repo = build_repo()
    tree = tempfile.mkdtemp() + "-wt"
    git("-C", repo, "worktree", "add", "-q", "-b", "feat", tree)
    target = {"op": "worktree", "repo": repo, "path": tree, "branch": "feat"}

    # 1. Uncommitted work is the fence most likely to have appeared since the scan.
    open(os.path.join(tree, "dirty.txt"), "w").write("uncommitted")
    ok, msg = a.perform(target)
    check("worktree: refuses one with uncommitted changes", not ok)
    check("worktree: says why it refused", "uncommitted" in msg)
    check("worktree: left it in place", os.path.isdir(tree))

    # 2. Standing in it is the fence that stops you deleting the ground you are on.
    os.remove(os.path.join(tree, "dirty.txt"))
    ok, msg = a.perform(target, cwd=os.path.join(tree, ""))
    check("worktree: refuses the one you are standing in", not ok)
    check("worktree: still in place", os.path.isdir(tree))

    # 3. The main checkout is never removable, whatever else is true.
    ok, _ = a.perform({"op": "worktree", "repo": repo, "path": repo, "branch": "main"})
    check("worktree: refuses the main checkout", not ok)

    # 4. Clean, not occupied: removed, and bundled first.
    #
    # This is the case that caught the "+" bug. Git marks a branch checked out in
    # another worktree with "+" rather than "*", so a marker-stripping parse read
    # every worktree branch as un-merged and refused to touch any of them. A
    # worktree branch is the only kind this tool ever sees, so the bug applied to
    # all of them.
    merged = git("-C", repo, "branch", "--merged", "main", "--format=%(refname:short)")
    listed = {l.strip() for l in merged.stdout.decode().splitlines() if l.strip()}
    check("worktree: a checked-out branch parses without its marker", "feat" in listed)

    ok, msg = a.perform(target)
    check("worktree: removes a clean one", ok)
    check("worktree: gone from disk", not os.path.isdir(tree))
    check("worktree: wrote a bundle or said why not",
          "bundle" in msg.lower() or "nothing unique" in msg.lower())
    if "bundled to" in msg:
        name = msg.split("bundled to ")[-1].strip()
        path = os.path.join(a.BACKUP_DIR, name)
        check("worktree: the bundle exists and is non-empty",
              os.path.isfile(path) and os.path.getsize(path) > 0)
        verified = git("bundle", "verify", path).returncode == 0
        check("worktree: git can read the bundle back", verified)


def test_unknown_op(a):
    ok, msg = a.perform({"op": "not_a_real_operation"})
    check("unknown operation is refused, not guessed at", not ok and "no handler" in msg)


def main():
    a = load("actions")
    print("\nreclaim actions")
    test_artifacts(a)
    test_transcripts(a)
    test_worktree_fences(a)
    test_unknown_op(a)
    if failures:
        print(f"\n  {len(failures)} failed: {', '.join(failures)}\n")
        return 1
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
