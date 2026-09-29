#!/usr/bin/env python3
"""storm_leftover_check.py - sync-storm conflict-copy detector (tech T23).

2026-09-29 15:35 an unknown file-sync service swept the group and renamed
canonical files into conflict copies named '<stem><MARKER><timestamp>.<ext>'
(107 copies group-wide; this repo a heavy-hit zone - see
orders/O-20260929-1830-HQ-CPH4.md). CPH4 restored 16/16 files, but the
new group tripwire FluxGroup-IntegritySentinel watches scheduler flips +
git deletion lines only, and state/ is gitignored: a dead canonical under
state/ is invisible to every git-level tripwire (proof: state/proposals.md
stayed dead ~3h post-restore until a manual pass found it). This tool
fills that blind spot from the local side. Report-only, never deletes
(T18 detection-only precedent; cleanup stays an explicit human/round
action).

  check     scan repo for conflict copies; classify each as
            ALIVE  -> canonical exists, copy is garbage (safe to clean)
            DEAD   -> canonical missing, RECOVERY NEEDED (restore the
                      newest copy by rename)
  selftest  synthetic fixture (alive / dead / multi-copy / non-conflict)
            + canonical-name derivation regression on the real
            2026-09-29 storm filenames.

Exit: check -> 0 clean, 1 any DEAD canonical; selftest -> 0 PASS.
Encoding rule: this file stays PURE ASCII (group coding law).
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
# Conflict-copy marker embedded by the 2026-09-29 sync storm, as unicode
# escapes to keep this file pure ASCII. Literal form: the CJK runes for
# "conflict-file" followed by one ASCII space.
MARKER = "\u7684\u51b2\u7a81\u6587\u4ef6 "
EXCLUDE_DIRS = {".git", "_trash", ".codely-cli", "auto-saves"}


def split_conflict(filename):
    """Return (canonical_name, tail) for a conflict copy, else None."""
    i = filename.find(MARKER)
    if i <= 0:
        return None
    stem = filename[:i]
    tail = filename[i + len(MARKER):]
    if "." not in tail:
        return None
    ext = tail.rsplit(".", 1)[-1]
    if not ext:
        return None
    return stem + "." + ext, tail


def scan(root):
    """Walk repo (or fixture dir); return conflict-copy findings."""
    findings = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            parsed = split_conflict(fn)
            if not parsed:
                continue
            canonical, tail = parsed
            cpath = os.path.join(dirpath, canonical)
            findings.append({"copy": os.path.join(dirpath, fn),
                             "canonical": cpath, "tail": tail,
                             "alive": os.path.exists(cpath)})
    return findings


def rel(path):
    return os.path.relpath(path, ROOT)


def cmd_check():
    findings = scan(ROOT)
    dead = [f for f in findings if not f["alive"]]
    for f in sorted(findings, key=lambda x: x["copy"]):
        state = ("ALIVE  garbage, safe to clean" if f["alive"]
                 else "DEAD   RECOVERY NEEDED -> restore this copy")
        print("%s | %s" % (state, rel(f["copy"])))
    print("storm leftover check: %d conflict copies, %d dead canonicals"
          % (len(findings), len(dead)))
    if not findings:
        print("clean: no conflict copies under repo")
    return 1 if dead else 0


# Regression set: the four real 2026-09-29 storm names from this repo
# (fullwidth colons as escapes; canonical names must derive exactly).
REAL_STORM_NAMES = [
    ("proposals\u7684\u51b2\u7a81\u6587\u4ef6 "
     "2026-09-29 15\uff1a35\uff1a18.837846.md", "proposals.md"),
    ("sentinel\u7684\u51b2\u7a81\u6587\u4ef6 "
     "2026-09-29 15\uff1a35\uff1a18.850961.snapshot", "sentinel.snapshot"),
    ("sentinel\u7684\u51b2\u7a81\u6587\u4ef6 "
     "2026-09-29 15\uff1a44\uff1a02.723346.snapshot", "sentinel.snapshot"),
    ("cost_ledger.cpython-314\u7684\u51b2\u7a81\u6587\u4ef6 "
     "2026-09-29 15\uff1a36\uff1a14.604114.pyc",
     "cost_ledger.cpython-314.pyc"),
]


def cmd_selftest():
    ok = True
    print("[1] canonical-name derivation regression (real storm names)")
    for idx, (raw, want) in enumerate(REAL_STORM_NAMES, 1):
        got = split_conflict(raw)
        good = got is not None and got[0] == want
        ok = ok and good
        print("    case %d: canonical=%-28s %s"
              % (idx, got[0] if got else "None",
                 "ok" if good else "MISMATCH"))
    print("[2] fixture scan classification")
    tmp = tempfile.mkdtemp(prefix="storm-selftest-")
    try:
        def w(name):
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                f.write("x\n")
        m = MARKER.strip()
        w("a.md")                                   # alive canonical
        w("a" + m + " 2026-09-29 15.35.18.000001.md")
        w("b" + m + " 2026-09-29 15.35.18.000002.md")   # dead canonical
        w("c" + m + " 2026-09-29 15.35.18.000003.md")   # dead, 2 copies
        w("c" + m + " 2026-09-29 15.44.02.000004.md")
        w("keep.txt")                               # non-conflict, ignored
        findings = scan(tmp)
        alive = [f for f in findings if f["alive"]]
        dead = [f for f in findings if not f["alive"]]
        checks = [
            ("total findings == 4", len(findings) == 4),
            ("alive == 1 (a.md)", len(alive) == 1
             and os.path.basename(alive[0]["canonical"]) == "a.md"),
            ("dead == 3 (b.md + c.md x2)", len(dead) == 3),
            ("dead canonicals derive b.md/c.md",
             {os.path.basename(f["canonical"]) for f in dead}
             == {"b.md", "c.md"}),
            ("keep.txt ignored", all("keep" not in f["copy"]
                                     for f in findings)),
        ]
        for label, good in checks:
            ok = ok and good
            print("    %-38s %s" % (label, "ok" if good else "FAIL"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("selftest: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check":
        return cmd_check()
    if cmd == "selftest":
        return cmd_selftest()
    print("usage: python Tools/storm_leftover_check.py check|selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
