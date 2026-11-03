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

D-20261102-03 (CEO 决策轮 00:00 班, @BigCompute) makes the default target set a
mandate rather than a caller choice: the seven index families that a round reads
as instructions (tasks / queue-main / proposals / HQ-FEEDBACK / decisions /
orders / reflection-journal) are ALWAYS scanned, including the three group-level
ledgers this repo only reads. Two consequences are encoded here:

  - a missing EXTERNAL target is reported as ABSENT-EXTERNAL and does not turn
    this repo's verdict red (other nodes legitimately lack the group tree);
  - damage found in an EXTERNAL target is reported as EXTERNAL-DAMAGED and is
    NOT repairable from here (cross-repo write is forbidden), so it never
    silently counts as green either.

Half-merged ledgers (T195, 2026-11-03 live evidence): the group `docs/orders.md`
tail carried a complete git conflict block (L304-310) and all three encoding
signatures stayed silent -- encoding damage and merge damage are different
faults. So a second file-level signature is checked here:

  - a conflict block is claimed only when an opener `<<<<<<<` AND a closer
    `>>>>>>>` both sit at column 0 of the SAME file, so a lone `=======` (a
    legitimate setext underline) never reds;
  - markers inside a fenced code block are recorded as quoted, not damage --
    documentation that shows the marker shapes is not a half-merged ledger;
  - an unpaired marker is reported as an orphan (visible advisory), because
    "half a block" is neither proved damage nor proved clean.

Cross-repo targets stay read-only: this tool only reports, never rewrites.

--rounds-line emits the one-line summary the night round appends (D-03 ③).

Commands:
  run [path ...] [--json-out PATH] [--rounds-line]   census (default: DEFAULT_TARGETS)
  selftest                                           fixtures; exit 0 = all pass

Exit codes: 0 TEXT-INTEGRITY-CLEAN, 7 NEEDS-FIX (local), 3 unreadable/bad usage.
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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def group_root():
    """FluxGroup root; overridable for a machine that keeps it elsewhere."""
    env = os.environ.get("FLUXGROUP_ROOT")
    if env:
        return env
    return os.path.normpath(os.path.join(REPO_ROOT, os.pardir, os.pardir))


# D-20261102-03 ①: the seven families a round reads as instructions. Fixed here
# on purpose -- a caller must not be able to narrow the default away.
REQUIRED_TARGETS = [
    ("tasks", "tasks/TASKS.md"),
    ("queue-main", "state/queue/main.md"),
    ("proposals", "state/proposals.md"),
    ("hq-feedback", "HQ-FEEDBACK.md"),
    ("decisions", os.path.join(group_root(), "docs", "decisions.md")),
    ("orders", os.path.join(group_root(), "docs", "orders.md")),
    ("reflection-journal", os.path.join(group_root(), "cph4", "reflection-journal.md")),
]
REQUIRED_CATEGORIES = [c for c, _ in REQUIRED_TARGETS]

# Extra local surfaces that are also read every round; kept from the 10-11 tool.
SUPPLEMENTARY_TARGETS = [
    "state/queue/tech.md",
    "state/queue/explore.md",
    "state/runbook.md",
    "state/heartbeat.txt",
    "docs/status-export.json",
]

DEFAULT_TARGETS = [p for _, p in REQUIRED_TARGETS] + SUPPLEMENTARY_TARGETS

# Back-compat alias (earlier rounds and notes refer to LIVING_TARGETS).
LIVING_TARGETS = DEFAULT_TARGETS


def is_external(path):
    """True only for files in the group tree that this repo reads but cannot write.

    Everything else (including temp fixtures) is treated as local, so a damaged
    local file always reds the verdict.
    """
    ap = os.path.abspath(path).lower()
    gr = os.path.abspath(group_root()).lower()
    rp = os.path.abspath(REPO_ROOT).lower()
    return ap.startswith(gr + os.sep) and not ap.startswith(rp + os.sep)

SKIP_EXTS = {".py", ".ps1", ".psm1"}

QMARK = "QMARK-DAMAGE"
FFFD = "FFFD-DAMAGE"
DOUBLE = "DOUBLE-DAMAGE"
NOT_UTF8 = "NOT-UTF8"
CONFLICT = "CONFLICT-MARKER"

CM_OPEN = "<<<<<<<"
CM_CLOSE = ">>>>>>>"
CM_SEP = "======="
CM_BASE = "|||||||"
CM_MARKERS = (CM_OPEN, CM_CLOSE, CM_SEP, CM_BASE)
FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")


def _fence_mask(lines):
    """True for every line inside (or delimiting) a fenced code block."""
    mask = []
    inside = False
    for line in lines:
        if FENCE_RE.match(line):
            mask.append(True)
            inside = not inside
            continue
        mask.append(inside)
    return mask


def conflict_blocks(lines, fenced=None):
    """-> (blocks, orphans, quoted); blocks carry their marker line numbers.

    Only an opener/closer PAIR at column 0 of the same file is damage. One
    `=======` alone is a setext heading underline, and a marker quoted inside a
    fence is documentation -- both must stay clean or the census becomes noise.
    """
    if fenced is None:
        fenced = _fence_mask(lines)
    blocks, orphans, quoted = [], 0, 0
    stack = []
    for i, line in enumerate(lines, 1):
        if not any(line.startswith(m) for m in CM_MARKERS):
            continue
        if fenced[i - 1]:
            quoted += 1
            continue
        if line.startswith(CM_OPEN):
            stack.append(i)
        elif line.startswith(CM_CLOSE):
            if stack:
                start = stack.pop()
                markers = [n for n in range(start, i + 1)
                           if any(lines[n - 1].startswith(m) for m in CM_MARKERS)]
                blocks.append({"start": start, "end": i, "marker_lines": markers})
            else:
                orphans += 1
    # An opener with no closer (including every still-open nested one) is
    # neither proved damage nor proved clean -> counted as an orphan.
    orphans += len(stack)
    blocks.sort(key=lambda b: (b["start"], b["end"]))
    return blocks, orphans, quoted


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
        ext = is_external(p)
        if os.path.splitext(p)[1].lower() in SKIP_EXTS:
            rows.append({"path": p, "external": ext, "skipped": "code-file"})
            continue
        try:
            raw = open(p, "rb").read()
        except OSError as e:
            if ext and not os.path.exists(p):
                rows.append({"path": p, "external": True, "status": "ABSENT-EXTERNAL"})
            else:
                rows.append({"path": p, "external": ext, "status": NOT_UTF8,
                             "error": str(e)})
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            rows.append({"path": p, "external": ext, "status": NOT_UTF8,
                         "error": "%s at byte %d" % (e.reason, e.start)})
            continue
        lines = text.splitlines()
        fenced = _fence_mask(lines)
        damaged_map = {}
        for i, line in enumerate(lines, 1):
            hits = classify_line(line)
            if hits:
                damaged_map[i] = {"line": i, "classes": list(hits),
                                  "qmark_runs": len(QMARK_RUN_RE.findall(line)),
                                  "preview": line.strip()[:110]}
        blocks, orphans, quoted = conflict_blocks(lines, fenced)
        for b in blocks:
            for n in b["marker_lines"]:
                if n in damaged_map:
                    if CONFLICT not in damaged_map[n]["classes"]:
                        damaged_map[n]["classes"].append(CONFLICT)
                else:
                    damaged_map[n] = {"line": n, "classes": [CONFLICT],
                                      "qmark_runs": 0,
                                      "preview": lines[n - 1].strip()[:110]}
        damaged = [damaged_map[k] for k in sorted(damaged_map)]
        rows.append({
            "path": p, "external": ext,
            "status": "DAMAGED" if damaged else "CLEAN",
            "bytes": len(raw), "lines": len(lines), "damaged_lines": damaged,
            "conflict_blocks": len(blocks),
            "conflict_marker_lines": len({n for b in blocks for n in b["marker_lines"]}),
            "conflict_orphans": orphans,
            "conflict_quoted": quoted,
        })
    return rows


def summarize(rows):
    readable = [r for r in rows
                if "skipped" not in r and "error" not in r
                and r.get("status") != "ABSENT-EXTERNAL"]
    absent_ext = [r for r in rows if r.get("status") == "ABSENT-EXTERNAL"]
    bad_local = [r for r in readable if r["status"] == "DAMAGED" and not r.get("external")]
    bad_ext = [r for r in readable if r["status"] == "DAMAGED" and r.get("external")]
    unreadable_local = [r for r in rows if "error" in r and not r.get("external")]
    unreadable_ext = [r for r in rows if "error" in r and r.get("external")]

    if bad_local or unreadable_local:
        verdict = "TEXT-INTEGRITY-NEEDS-FIX"
    elif bad_ext or unreadable_ext:
        # Never silently green: damage exists, it is just not ours to repair.
        verdict = "TEXT-INTEGRITY-EXTERNAL-DAMAGED"
    else:
        verdict = "TEXT-INTEGRITY-CLEAN"

    scanned_paths = {os.path.abspath(r["path"]) for r in readable}
    covered = [c for c, p in REQUIRED_TARGETS
               if os.path.abspath(p) in scanned_paths]
    conflict_files = [r for r in readable if r.get("conflict_blocks")]
    return {
        "verdict": verdict,
        "files_scanned": len(readable),
        "files_damaged": len(bad_local),
        "damaged_lines": sum(len(r["damaged_lines"]) for r in bad_local),
        "external_files_damaged": len(bad_ext),
        "external_damaged_lines": sum(len(r["damaged_lines"]) for r in bad_ext),
        "files_with_conflict_blocks": len(conflict_files),
        "conflict_blocks": sum(r.get("conflict_blocks", 0) for r in readable),
        "conflict_marker_lines": sum(r.get("conflict_marker_lines", 0) for r in readable),
        "conflict_orphans": sum(r.get("conflict_orphans", 0) for r in readable),
        "conflict_quoted": sum(r.get("conflict_quoted", 0) for r in readable),
        "files_unreadable": len(unreadable_local),
        "external_unreadable": len(unreadable_ext),
        "absent_external": [r["path"] for r in absent_ext],
        "categories_covered": covered,
        "categories_required": REQUIRED_CATEGORIES,
        "categories_covered_n": len(covered),
        "categories_required_n": len(REQUIRED_CATEGORIES),
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

    # S11 (D-20261102-03 ④) the default target set covers all seven mandated
    # families -- and the three group ledgers are in it, not behind a flag.
    run("S11 default-covers-7",
        len(REQUIRED_CATEGORIES) == 7
        and REQUIRED_CATEGORIES == [c for c, _ in REQUIRED_TARGETS]
        and all(p in DEFAULT_TARGETS for _, p in REQUIRED_TARGETS),
        "categories=" + ",".join(REQUIRED_CATEGORIES))

    # S11b: the seven required entries resolve to the expected file names
    expect = {"tasks": "TASKS.md", "queue-main": "main.md",
              "proposals": "proposals.md", "hq-feedback": "HQ-FEEDBACK.md",
              "decisions": "decisions.md", "orders": "orders.md",
              "reflection-journal": "reflection-journal.md"}
    ok_names = all(os.path.basename(p) == expect[c] for c, p in REQUIRED_TARGETS)
    run("S11b default-filenames", ok_names, str([os.path.basename(p) for _, p in REQUIRED_TARGETS]))

    # S12 external damage is visible but never counted as this repo's damage
    s = summarize([{"path": os.path.join(group_root(), "docs", "x.md"),
                    "external": True, "status": "DAMAGED", "lines": 2,
                    "damaged_lines": [{"line": 1}]}])
    run("S12 external-visible-not-local",
        s["verdict"] == "TEXT-INTEGRITY-EXTERNAL-DAMAGED"
        and s["files_damaged"] == 0 and s["external_files_damaged"] == 1,
        s["verdict"])

    # S13 an absent external target is reported, not treated as a failure
    s = summarize([{"path": os.path.join(group_root(), "docs", "nope.md"),
                    "external": True, "status": "ABSENT-EXTERNAL"}])
    run("S13 absent-external-ok",
        s["verdict"] == "TEXT-INTEGRITY-CLEAN" and len(s["absent_external"]) == 1,
        s["verdict"])

    # C1 (T195) a real half-merged ledger -- opener + closer at column 0 with a
    # diff3 base marker in between -- is damage, and every marker line is named.
    body = ("# 台账\n"
            "| 行 | 内容 |\n"
            "<<<<<<< Updated upstream\n"
            "| 新 | 上游版本 |\n"
            "||||||| parent of d2a66071\n"
            "=======\n"
            "| 旧 | 本地版本 |\n"
            ">>>>>>> Stashed changes\n")
    fd, tmp = tempfile.mkstemp(suffix=".md")
    os.write(fd, body.encode("utf-8"))
    os.close(fd)
    rows = census([tmp])
    os.unlink(tmp)
    r = rows[0]
    marks = [d["line"] for d in r["damaged_lines"] if CONFLICT in d["classes"]]
    run("C1 conflict-block-damage",
        r["status"] == "DAMAGED" and r.get("conflict_blocks") == 1
        and marks == [3, 5, 6, 8] and r.get("conflict_orphans") == 0,
        "blocks=%s marks=%s orphans=%s" % (r.get("conflict_blocks"), marks, r.get("conflict_orphans")))
    s = summarize(rows)
    run("C1b conflict-reds-verdict",
        s["verdict"] == "TEXT-INTEGRITY-NEEDS-FIX" and s["conflict_blocks"] == 1,
        s["verdict"])

    # C2 a lone '=======' is a setext heading underline, not a conflict
    fd, tmp = tempfile.mkstemp(suffix=".md")
    os.write(fd, "标题\n=======\n正文\n".encode("utf-8"))
    os.close(fd)
    r = census([tmp])[0]
    os.unlink(tmp)
    run("C2 lone-separator-clean",
        r["status"] == "CLEAN" and r.get("conflict_blocks") == 0
        and r.get("conflict_orphans") == 0,
        "status=%s orphans=%s" % (r["status"], r.get("conflict_orphans")))

    # C3 markers quoted inside a fenced block are documentation -> quoted only
    fd, tmp = tempfile.mkstemp(suffix=".md")
    os.write(fd, ("冲突标记形如：\n```\n<<<<<<< HEAD\n=======\n>>>>>>> other\n```\n").encode("utf-8"))
    os.close(fd)
    r = census([tmp])[0]
    os.unlink(tmp)
    run("C3 fenced-quote-clean",
        r["status"] == "CLEAN" and r.get("conflict_blocks") == 0
        and r.get("conflict_quoted") == 3,
        "status=%s quoted=%s" % (r["status"], r.get("conflict_quoted")))

    # C4 an unpaired opener is neither proved damage nor proved clean
    fd, tmp = tempfile.mkstemp(suffix=".md")
    os.write(fd, "正文\n<<<<<<< HEAD\n未合完\n".encode("utf-8"))
    os.close(fd)
    r = census([tmp])[0]
    os.unlink(tmp)
    run("C4 orphan-visible-not-damage",
        r["status"] == "CLEAN" and r.get("conflict_blocks") == 0
        and r.get("conflict_orphans") == 1,
        "status=%s orphans=%s" % (r["status"], r.get("conflict_orphans")))

    # C5 nested blocks (the live 2026-11-03 shape: a stash block wrapping a
    # HEAD block) pair by stack, so the nesting is not misread as two orphans
    fd, tmp = tempfile.mkstemp(suffix=".md")
    os.write(fd, ("<<<<<<< Updated upstream\n"
                  "a\n"
                  "<<<<<<< HEAD\n"
                  "b\n"
                  ">>>>>>> d2a66071\n"
                  ">>>>>>> Stashed changes\n").encode("utf-8"))
    os.close(fd)
    r = census([tmp])[0]
    os.unlink(tmp)
    run("C5 nested-blocks-pair",
        r.get("conflict_blocks") == 2 and r.get("conflict_orphans") == 0
        and r["status"] == "DAMAGED",
        "blocks=%s orphans=%s" % (r.get("conflict_blocks"), r.get("conflict_orphans")))

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        _print_safe("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))
    _print_safe("SELFTEST %d/%d %s" % (passed, len(checks), "PASS" if passed == len(checks) else "FAIL"))
    return 0 if passed == len(checks) else 1


def rounds_line(s):
    """One line for state/rounds.log (D-20261102-03 ③). Deterministic."""
    return ("textintegrity:%s cats=%d/%d files=%d damaged_files=%d damaged_lines=%d"
            " ext_damaged=%d ext_absent=%d unreadable=%d conflict_blocks=%d conflict_orphans=%d"
            % ("CLEAN" if s["verdict"] == "TEXT-INTEGRITY-CLEAN" else
               ("EXTERNAL-DAMAGED" if s["verdict"] == "TEXT-INTEGRITY-EXTERNAL-DAMAGED"
                else "NEEDS-FIX"),
               s["categories_covered_n"], s["categories_required_n"],
               s["files_scanned"], s["files_damaged"], s["damaged_lines"],
               s["external_files_damaged"], len(s["absent_external"]),
               s["files_unreadable"], s.get("conflict_blocks", 0),
               s.get("conflict_orphans", 0)))


def main(argv=None):
    ap = argparse.ArgumentParser(description="line-level damage census for living index files")
    ap.add_argument("command", choices=["run", "selftest"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--json-out")
    ap.add_argument("--rounds-line", action="store_true",
                    help="also print the one-line rounds.log summary (D-03 ③)")
    args = ap.parse_args(argv)

    if args.command == "selftest":
        return _selftest()

    paths = args.paths or DEFAULT_TARGETS
    s = summarize(census(paths))
    for r in s["rows"]:
        if "skipped" in r:
            _print_safe("SKIP  %s (%s)" % (r["path"], r["skipped"]))
            continue
        if r.get("status") == "ABSENT-EXTERNAL":
            _print_safe("ABSENT %-30s (external target not on this node)" % r["path"])
            continue
        if "error" in r:
            _print_safe("ERR   %s  %s%s" % (r["path"], r["error"],
                                            "  [external/read-only]" if r.get("external") else ""))
            continue
        _print_safe("%-5s %-32s lines=%-5d damaged=%d%s"
                    % (r["status"], os.path.basename(r["path"]), r["lines"],
                       len(r["damaged_lines"]), "  [external/read-only]" if r.get("external") else ""))
        if r.get("conflict_blocks") or r.get("conflict_orphans") or r.get("conflict_quoted"):
            _print_safe("        conflict: blocks=%d markers=%d orphans=%d quoted=%d"
                        % (r.get("conflict_blocks", 0), r.get("conflict_marker_lines", 0),
                           r.get("conflict_orphans", 0), r.get("conflict_quoted", 0)))
        for d in r["damaged_lines"][:6]:
            _print_safe("        L%-5d %-22s q=%-3d %s"
                        % (d["line"], ",".join(d["classes"]), d["qmark_runs"], d["preview"]))
        if len(r["damaged_lines"]) > 6:
            _print_safe("        ... %d more damaged lines" % (len(r["damaged_lines"]) - 6))
    _print_safe("VERDICT %s files_scanned=%d files_damaged=%d damaged_lines=%d"
                " ext_damaged_files=%d ext_absent=%d unreadable=%d cats=%d/%d"
                % (s["verdict"], s["files_scanned"], s["files_damaged"],
                   s["damaged_lines"], s["external_files_damaged"],
                   len(s["absent_external"]), s["files_unreadable"],
                   s["categories_covered_n"], s["categories_required_n"]))
    _print_safe("CONFLICT files=%d blocks=%d markers=%d orphans=%d quoted=%d"
                % (s["files_with_conflict_blocks"], s["conflict_blocks"],
                   s["conflict_marker_lines"], s["conflict_orphans"],
                   s["conflict_quoted"]))
    if args.rounds_line:
        _print_safe("ROUNDS-LINE " + rounds_line(s))
    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(s, fh, ensure_ascii=False, indent=2)
        _print_safe("JSON  %s" % args.json_out)
    return 7 if s["verdict"] == "TEXT-INTEGRITY-NEEDS-FIX" else 0


if __name__ == "__main__":
    sys.exit(main())