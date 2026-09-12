#!/usr/bin/env bash
# smoke.sh — invariants that must hold before this plugin goes near anyone's machine.
#
# Deliberately covers the failure shapes that produce a WRONG ANSWER rather than
# an error: a missing tool read as "0 bytes", a stale cache read as "just
# measured", a size parser that silently drops a unit. Those are the ones a user
# would never report, because nothing looks broken.

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
pass=0 fail=0

# macOS has no `timeout`; coreutils ships it as `gtimeout` and is not installed by
# default. Bound the run where we can and just run it where we cannot - the point
# of these checks is the output, not the bound.
if   command -v timeout  >/dev/null 2>&1; then bounded() { timeout "$@"; }
elif command -v gtimeout >/dev/null 2>&1; then bounded() { gtimeout "$@"; }
else                                           bounded() { shift; "$@"; }; fi

ok()   { pass=$((pass+1)); printf '  \033[32mok\033[0m   %s\n' "$1"; }
bad()  { fail=$((fail+1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; }
is()   { [ "$2" = "$3" ] && ok "$1" || bad "$1 (got '$2', want '$3')"; }

printf '\nshell helpers\n'
# shellcheck disable=SC1091
HERDR_PLUGIN_STATE_DIR="$(mktemp -d)" . "$ROOT/bin/lib.sh"

is "human_bytes 0"          "$(human_bytes 0)"          "0B"
is "human_bytes 1023"       "$(human_bytes 1023)"       "1023B"
is "human_bytes 1048576"    "$(human_bytes 1048576)"    "1.0M"
is "human_bytes 2147483648" "$(human_bytes 2147483648)" "2.0G"
is "mtime of a missing file is 0, not empty" "$(mtime /nonexistent/x)" "0"
[ -n "$(dir_bytes "$ROOT")" ] && ok "dir_bytes returns a size" || bad "dir_bytes returned nothing"
dir_bytes /nonexistent/x >/dev/null 2>&1 && bad "dir_bytes should fail on a missing path" \
                                          || ok "dir_bytes fails on a missing path"
w=$(worktree_root "$ROOT"); is "worktree_root resolves a repo" "$w" "$ROOT"

printf '\nsize parsing\n'
python3 - "$ROOT" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("reclaim", sys.argv[1] + "/bin/reclaim.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
cases = [("0B", 0), ("512B", 512), ("1KB", 1024), ("594kB", 594 * 1024),
         ("1.5MB", int(1.5 * 1024**2)), ("2GB", 2 * 1024**3), ("", 0), ("garbage", 0)]
bad = 0
for text, want in cases:
    got = int(m.parse_size(text))
    print(f"  {'ok  ' if got == want else 'FAIL'} parse_size({text!r}) -> {got}")
    bad += got != want
# A dangling image must never be classed anything but SAFE, and an in-use volume
# must never be classed anything but BLOCKED: those two drive what v0.3 deletes.
print(f"  {'ok  ' if m.SAFE != m.BLOCKED else 'FAIL'} classes are distinct")
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && ok "parse_size handles every docker unit form" || bad "parse_size mis-parsed a unit"

printf '\nartifact detection\n'
art=$(python3 - "$ROOT" <<'PY2'
import importlib.util, sys, tempfile, os, subprocess
spec = importlib.util.spec_from_file_location("reclaim", sys.argv[1] + "/bin/reclaim.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "node_modules", "pkg"))
open(os.path.join(tmp, "node_modules", "pkg", "f"), "wb").write(b"x" * 200000)
os.makedirs(os.path.join(tmp, "dist"))
total, count, kinds = m.artifacts_in(tmp)
print("ok" if count == 2 and total > 0 and "node_modules" in kinds else f"FAIL {count} {total} {kinds}")
# A clean directory must yield nothing, not a zero-byte row.
print("ok" if m.artifacts_in(tempfile.mkdtemp()) == (0, 0, ()) else "FAIL empty dir")
subprocess.run(["rm", "-rf", tmp])
PY2
)
case "$art" in *FAIL*) bad "artifacts_in: $art" ;; *) ok "artifacts_in finds and sizes build dirs" ;; esac

printf '\ntranscript ageing\n'
age=$(python3 - "$ROOT" <<'PY2'
import importlib.util, os, sys, tempfile, time
spec = importlib.util.spec_from_file_location("reclaim", sys.argv[1] + "/bin/reclaim.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
tmp = tempfile.mkdtemp()
old = os.path.join(tmp, "old.jsonl"); new = os.path.join(tmp, "new.jsonl")
open(old, "wb").write(b"x" * 5000); open(new, "wb").write(b"y" * 3000)
past = time.time() - 200 * 86400
os.utime(old, (past, past))
stale, fresh, count = m.split_by_age(tmp, 90)
print("ok" if (stale, fresh, count) == (5000, 3000, 1) else f"FAIL {stale} {fresh} {count}")
# Nested files must be counted too: transcripts live several levels down.
sub = os.path.join(tmp, "a", "b"); os.makedirs(sub)
deep = os.path.join(sub, "deep.jsonl"); open(deep, "wb").write(b"z" * 1000)
os.utime(deep, (past, past))
stale2, _, count2 = m.split_by_age(tmp, 90)
print("ok" if (stale2, count2) == (6000, 2) else f"FAIL nested {stale2} {count2}")
PY2
)
case "$age" in *FAIL*) bad "split_by_age: $age" ;; *) ok "split_by_age separates stale from fresh, including nested" ;; esac

printf '\nscan history\n'
hist=$(python3 - "$ROOT" <<'PY2'
import importlib.util, json, os, sys, tempfile, time
sd = tempfile.mkdtemp(); os.environ["HERDR_PLUGIN_STATE_DIR"] = sd
spec = importlib.util.spec_from_file_location("reclaim", sys.argv[1] + "/bin/reclaim.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
rows = [(m.SAFE, "docker image", "a", 100, "r"), (m.BLOCKED, "git worktree", "b", 200, "r")]
m.record(rows, {m.SAFE: 100, m.REVIEW: 0, m.BLOCKED: 200})
print("ok" if len(open(m.HISTORY).readlines()) == 1 else "FAIL history not written")
print("ok" if len(json.load(open(m.SEEN))) == 2 else "FAIL first-seen not recorded")
# A run minutes later must not become the comparison point, or pressing r
# repeatedly would collapse the window and always show no change.
print("ok" if m.previous_totals() is None else "FAIL compared against a fresh record")
open(m.HISTORY, "a").write(json.dumps({"t": int(time.time() - 3*86400),
                                       "safe": 1, "review": 2, "blocked": 3}) + "\n")
print("ok" if (m.previous_totals() or {}).get("safe") == 1 else "FAIL did not find the aged record")
# A history that grows without bound is a bug in a long-lived plugin.
for _ in range(m.HISTORY_MAX + 40):
    m.record(rows, {m.SAFE: 1, m.REVIEW: 1, m.BLOCKED: 1})
print("ok" if len(open(m.HISTORY).readlines()) <= m.HISTORY_MAX else "FAIL history unbounded")
PY2
)
case "$hist" in *FAIL*) bad "history: $hist" ;; *) ok "history records, ages, compares and stays bounded" ;; esac

printf '\nscanner\n'
out=$(cd "$ROOT" && NO_COLOR=1 bounded 300 python3 bin/reclaim.py 2>&1)
case "$out" in *"reclaimable space"*) ok "scanner produces a report" ;; *) bad "no report: $out" ;; esac
case "$out" in *"read-only"*) ok "report states it deleted nothing" ;; *) bad "missing read-only notice" ;; esac

# The tools it shells out to may be absent. Absent must mean "no rows", never a
# crash. Resolve the harness's own binaries first, since PATH is about to go away.
empty=$(mktemp -d)
PYTHON=$(command -v python3)
out=$(cd "$ROOT" && NO_COLOR=1 PATH="$empty" "$PYTHON" bin/reclaim.py 2>&1)
case "$out" in *"reclaimable space"*) ok "survives with no docker, git or du on PATH" ;;
                                   *) bad "crashed without its tools: $out" ;; esac
rm -rf "$empty"

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
