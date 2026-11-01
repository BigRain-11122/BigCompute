#!/usr/bin/env python3
"""text_integrity_census.py - line-level damage census for the LIVING index files.

Why this exists (2026-11-01 22:4x, live evidence):
The 2026-11-01 18:1x round appended its results through a console round-trip
that flattened CJK to ASCII '?'. Five authoritative rows were destroyed:

  tasks/TASKS.md:53          T-20261101-49   qmark=39
  state/proposals.md:134     BC-P-128        qmark=34
  state/queue/main.md:97     M92             qmark=11
  HQ-FEEDBACK.md:490-492     18:2x 日清节点    qmark=15 + U+FFFD=3

Nothing reported it. `ledger_encoding_guard.py` already knows all three damage
signatures (utf8-double / U+FFFD / '?' runs) but its DEFAULT_TARGETS are only
`state/rounds.log` + `state/heartbeat.txt`, and in `census` the '?' count is
informational only (deliberate: immutable history must stay read-only and a
lone '?' is legitimate). So a corrupted queue row could sit unread for good.

This tool keeps that read-side precision/immutability stance and adds the
missing half: it scans the files that are READ AS INSTRUCTIONS EVERY ROUND
(board / queues / proposals / 日清 / runbook / status export) and reports the
exact file:line so a bad row can be located and repaired instead of guessed at.

Deliberately NOT in scope:
  - state/rounds.log is immutable history; it is scanned only when asked
    explicitly, and its findings are reported, never "fixed".
  - source code (.py/.ps1) contains legitimate `\\?{2,}` regex literals and is
    excluded by extension, which also kills the self-reference class of
    false positive (this file would otherwise flag itself).

Commands:
  run [path ...] [--json-out PATH]   census (default: LIVING_TARGETS)
  selftest                           fixtures; exit 0 = all pass

Exit codes: 0 TEXT-INTEGRITY-CLEAN, 7 NEEDS-FIX, 3 unreadable/bad usage.
"""
import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import ledger_encoding_guard as guard
except ImportError:  # standalone use keeps working with a local fallback
    guard = None

QMARK_RUN_RE = re.compile(r"\?{2,}")

# Files whose every line is read as an instruction/state by the next round.
LIVING_TARGETS = [
    "tasks/TASKS.md",
    "state/queue/main.md",
    "state/queue/tech.md",
    "state/queue/explore.md",
    "state/proposals.md",
    "HQ-FEEDBACK.md",
    "state/runbook.md",
    "state/heartbeat.txt",
    "docs/status-export.json",
]

SKIP_EXTS = {".py", ".ps1", ".psm1"}

QMARK = "QMARK-DAMAGE"
FFFD = "FFFD-DAMAGE"
DOUBLE = "DOUBLE-DAMAGE"
NOT_UTF8 = "NOT-UTF8"


def _print_safe(text):
    """Console codec on zh-CN hosts is GBK; printing must never abort a report."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, "replace").decode(enc, "replace"))


def classify_line(line):
    """-> list of damage classes for one line (empty list = healthy).

    '?' runs are damage ONLY when they replace CJK, i.e. the line has no CJK
    left at all. A line that still carries Chinese next to a '?{2,}' run is
    reported as QMARK-DAMAGE too, because the run itself is the leftover of
    flattened CJK -- but the two cases are distinguished in the detail field so
    a reviewer can tell "whole line lost" from "partial loss".
    """
    hits = []
    if "\ufffd" in line:
        hits.append(FFFD)
    if guard is not None:
        cls = guard.classify(line)
        if cls["utf8_double"]:
            hits.append(DOUBLE)
        has_cjk = cls["cjk_chars"] > 0
    else:
        has_cjk = any("\u4e00" <= c <= "\u9fff" for c in line)
    if QMARK_RUN_RE.search(line):
        hits.append(QMARK + ("" if has_cjk else "(no-cjk)"))
    return hits


def census(paths):
    rows = []
    for p in paths:
        if os.path.splitext(p)[1].lower() in SKIP_EXTS:
            rows.append({"path": p, "skipped": "code-file"})
            continue
        try:
            raw = open(p, "rb").read()
        except OSError as e:
            rows.append({"path": p, "status": NOT_UTF8, "error": str(e)})
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            rows.append({"path": p, "status": NOT_UTF8,
                         "error": "%s at byte %d" % (e.reason, e.start)})
            continue
        lines = text.splitlines()
        damaged = []
        for i, line in enumerate(lines, 1):
            hits = classify_line(line)
            if hits:
                damaged.append({"line": i, "classes": hits,
                                "qmark_runs": len(QMARK_RUN_RE.findall(line)),
                                "preview": line.strip()[:110]})
        rows.append({
            "path": p, "status": "DAMAGED" if damaged else "CLEAN",
            "bytes": len(raw), "lines": len(lines), "damaged_lines": damaged,
        })
    return rows


def summarize(rows):
    scanned = [r for r in rows if "skipped" not in r and "error" not in r]
    bad = [r for r in scanned if r["status"] == "DAMAGED"]
    unreadable = [r for r in rows if "error" in r]
    total_lines = sum(len(r["damaged_lines"]) for r in bad)
    verdict = "TEXT-INTEGRITY-CLEAN" if not bad and not unreadable else "TEXT-INTEGRITY-NEEDS-FIX"
    return {
        "verdict": verdict,
        "files_scanned": len(scanned),
        "files_damaged": len(bad),
        "damaged_lines": total_lines,
        "files_unreadable": len(unreadable),
        "rows": rows,
    }


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # T1 CJK flattened to '?' -> damage
    run("T1 qmark-damage", classify_line("| T-1 | ?????? D-20 ???? |") == [QMARK + "(no-cjk)"],
        str(classify_line("| T-1 | ?????? D-20 ???? |")))

    # T2 partial loss: CJK survives next to a '?' run -> still damage, and the
    # class stays distinguishable from the whole-line-lost form in T1
    run("T2 partial-qmark", classify_line("P2 三行：`lane8090` ?? DARK[DOWN]") == [QMARK],
        str(classify_line("P2 三行：`lane8090` ?? DARK[DOWN]")))

    # T3 lone '?' in English prose is not damage
    run("T3 lone-qmark-ok", classify_line("round done: is the lane dark?") == [])

    # T4 U+FFFD -> damage
    run("T4 fffd-damage", FFFD in classify_line("日清 \ufffd\u6b21 维持"))

    # T5 utf8-as-latin1 double encoding -> damage
    doubled = "中文测试".encode("utf-8").decode("latin-1")
    run("T5 double-damage", DOUBLE in classify_line("prefix " + doubled),
        str(classify_line("prefix " + doubled)))

    # T6 clean Chinese with legitimate typography is healthy
    run("T6 clean-cjk", classify_line("2026-11-01 22:43 §二 · 门控 × 上限 ¥29.9") == [])

    # T7 code files are skipped so regex literals cannot self-flag
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".py")
    os.write(fd, "RE = re.compile(r'\\?{2,}')\n".encode())
    os.close(fd)
    row = census([tmp])[0]
    os.unlink(tmp)
    run("T7 code-skip", row.get("skipped") == "code-file", str(row.get("skipped")))

    # T8 missing file is reported, never silently clean
    s = summarize(census([os.path.join("state", "__no_such_file__.md")]))
    run("T8 unreadable-visible", s["verdict"] == "TEXT-INTEGRITY-NEEDS-FIX" and s["files_unreadable"] == 1)

    # T9 verdict aggregates: one damaged line is enough to refuse CLEAN
    fd, tmp = tempfile.mkstemp(suffix=".md")
    os.write(fd, "正常行\n| M | ???? D-20 ???? |\n".encode("utf-8"))
    os.close(fd)
    s = summarize(census([tmp]))
    os.unlink(tmp)
    run("T9 aggregate", s["verdict"] == "TEXT-INTEGRITY-NEEDS-FIX" and s["damaged_lines"] == 1,
        "%s lines=%d" % (s["verdict"], s["damaged_lines"]))

    # T10 determinism: two runs over the same bytes agree
    fd, tmp = tempfile.mkstemp(suffix=".md")
    os.write(fd, "正常行\n?????\n".encode("utf-8"))
    os.close(fd)
    a = json.dumps(summarize(census([tmp]))["rows"], ensure_ascii=True, sort_keys=True)
    b = json.dumps(summarize(census([tmp]))["rows"], ensure_ascii=True, sort_keys=True)
    os.unlink(tmp)
    run("T10 determinism", a == b)

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        _print_safe("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))
    _print_safe("SELFTEST %d/%d %s" % (passed, len(checks), "PASS" if passed == len(checks) else "FAIL"))
    return 0 if passed == len(checks) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="line-level damage census for living index files")
    ap.add_argument("command", choices=["run", "selftest"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--json-out")
    args = ap.parse_args(argv)

    if args.command == "selftest":
        return _selftest()

    paths = args.paths or LIVING_TARGETS
    s = summarize(census(paths))
    for r in s["rows"]:
        if "skipped" in r:
            _print_safe("SKIP  %s (%s)" % (r["path"], r["skipped"]))
            continue
        if "error" in r:
            _print_safe("ERR   %s  %s" % (r["path"], r["error"]))
            continue
        _print_safe("%-5s %-32s lines=%-5d damaged=%d"
                    % (r["status"], r["path"], r["lines"], len(r["damaged_lines"])))
        for d in r["damaged_lines"][:6]:
            _print_safe("        L%-5d %-22s q=%-3d %s"
                        % (d["line"], ",".join(d["classes"]), d["qmark_runs"], d["preview"]))
        if len(r["damaged_lines"]) > 6:
            _print_safe("        ... %d more damaged lines" % (len(r["damaged_lines"]) - 6))
    _print_safe("VERDICT %s files_scanned=%d files_damaged=%d damaged_lines=%d unreadable=%d"
                % (s["verdict"], s["files_scanned"], s["files_damaged"],
                   s["damaged_lines"], s["files_unreadable"]))
    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(s, fh, ensure_ascii=False, indent=2)
        _print_safe("JSON  %s" % args.json_out)
    return 0 if s["verdict"] == "TEXT-INTEGRITY-CLEAN" else 7


if __name__ == "__main__":
    sys.exit(main())