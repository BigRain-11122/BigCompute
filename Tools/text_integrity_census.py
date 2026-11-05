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
# T212: a table row whose cell count disagrees with its header is a different
# defect from a corrupted line -- the bytes are intact, the row is unreadable.
COLUMN_OVER = "COLUMN-OVERFLOW"
COLUMN_UNDER = "COLUMN-UNDERFLOW"

TABLE_ROW_RE = re.compile(r"^\s*\|")
TABLE_DELIM_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
TABLE_BASELINE_DEFAULT = os.path.join(REPO_ROOT, "state", "table-column-baseline.json")

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


def split_table_cells(line):
    """GFM cell split: only an UNESCAPED '|' separates cells.

    `\\|` is a literal pipe inside a cell (that is why M130's rows with a raw
    pipe in the prose lost their columns). Leading/trailing optional pipes do
    not create empty cells.
    """
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, buf, i = [], [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf))
    return cells


DATE_LIKE_RE = re.compile(r"^[\d\-:/ xX]+$")
ID_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._\-]*\d[A-Za-z0-9._\-]*$")


def table_row_id(cells):
    """Row identity for the legacy registry: first id-looking cell token.

    Line numbers drift as append-only ledgers grow, so the baseline keys on the
    row id. Ledgers whose first cell is a timestamp (decisions.md) must fall
    through to the next cell, or four different rows collapse into one key.
    """
    for cell in cells:
        s = cell.replace("*", "").replace("`", "").strip()
        if not s or DATE_LIKE_RE.match(s):
            continue
        tok = s.split()[0]
        if ID_TOKEN_RE.match(tok):
            return tok[:40]
    for cell in cells:
        s = cell.replace("*", "").replace("`", "").strip()
        if s:
            return s.split()[0][:40]
    return ""


def table_file_key(path):
    """Stable file key for the registry (repo-relative, or group-relative)."""
    ap = os.path.abspath(path)
    if is_external(path):
        return "group/" + os.path.relpath(ap, group_root()).replace(os.sep, "/")
    rp = os.path.abspath(REPO_ROOT)
    if ap.lower().startswith(rp.lower() + os.sep):
        return os.path.relpath(ap, rp).replace(os.sep, "/")
    return "abs/" + os.path.basename(ap)


def table_row_defects(lines, fenced=None):
    """-> list of row-level column defects (empty = every row matches its header).

    A table starts at a pipe row immediately followed by a delimiter row; every
    following contiguous pipe row is a body row and must have the header's cell
    count. Rows inside a fence are documentation examples and are skipped.
    """
    if fenced is None:
        fenced = _fence_mask(lines)
    out = []
    i = 0
    while i < len(lines) - 1:
        if fenced[i] or not TABLE_ROW_RE.match(lines[i]):
            i += 1
            continue
        delim = lines[i + 1]
        if fenced[i + 1] or not TABLE_DELIM_RE.match(delim) or "-" not in delim:
            i += 1
            continue
        want = len(split_table_cells(lines[i]))
        j = i + 2
        while j < len(lines) and not fenced[j] and TABLE_ROW_RE.match(lines[j]):
            cells = split_table_cells(lines[j])
            if len(cells) != want:
                out.append({
                    "line": j + 1,
                    "cells": len(cells),
                    "expected": want,
                    "kind": COLUMN_OVER if len(cells) > want else COLUMN_UNDER,
                    "row_id": table_row_id(cells),
                    "preview": lines[j].strip()[:110],
                })
            j += 1
        i = j
    return out


def load_table_baseline(path=TABLE_BASELINE_DEFAULT):
    """Declared legacy column defects -> {(file_key, row_id): note}.

    Legacy rows are enumerated one by one, never absorbed by a count, so a new
    malformed row cannot ride in on yesterday's total. A missing registry means
    nothing is declared -- every such row is new and must be looked at.
    """
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}, path
    out = {}
    for item in data.get("declared") or []:
        f, rid = item.get("file"), item.get("row_id")
        if f and rid is not None:
            out[(f, rid)] = item.get("note", "")
    return out, path


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


def census(paths, baseline=None):
    if baseline is None:
        baseline, _ = load_table_baseline()
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
        table_bad = table_row_defects(lines, fenced)
        fkey = table_file_key(p)
        for d in table_bad:
            d["file"] = fkey
            d["declared"] = (fkey, d["row_id"]) in baseline
            d["note"] = baseline.get((fkey, d["row_id"]), "")
        rows.append({
            "path": p, "external": ext,
            "status": "DAMAGED" if damaged else "CLEAN",
            "bytes": len(raw), "lines": len(lines), "damaged_lines": damaged,
            "conflict_blocks": len(blocks),
            "conflict_marker_lines": len({n for b in blocks for n in b["marker_lines"]}),
            "conflict_orphans": orphans,
            "conflict_quoted": quoted,
            "table_mismatch": table_bad,
            "table_mismatch_n": len(table_bad),
            "table_mismatch_new": [d for d in table_bad if not d["declared"]],
        })
    return rows


def summarize(rows, baseline=None):
    if baseline is None:
        baseline, _ = load_table_baseline()
    readable = [r for r in rows
                if "skipped" not in r and "error" not in r
                and r.get("status") != "ABSENT-EXTERNAL"]
    absent_ext = [r for r in rows if r.get("status") == "ABSENT-EXTERNAL"]
    bad_local = [r for r in readable if r["status"] == "DAMAGED" and not r.get("external")]
    bad_ext = [r for r in readable if r["status"] == "DAMAGED" and r.get("external")]
    unreadable_local = [r for r in rows if "error" in r and not r.get("external")]
    unreadable_ext = [r for r in rows if "error" in r and r.get("external")]

    # T212: column defects are counted on their own axis -- a row can be
    # perfectly encoded and still unreadable, and neither family may hide
    # inside the other's number.
    tbl_all, tbl_new_local, tbl_new_ext, tbl_declared = [], [], [], 0
    for r in readable:
        for d in r.get("table_mismatch") or []:
            entry = dict(d, path=r["path"], external=bool(r.get("external")))
            tbl_all.append(entry)
            if d.get("declared"):
                tbl_declared += 1
            elif r.get("external"):
                tbl_new_ext.append(entry)
            else:
                tbl_new_local.append(entry)
    seen = {(r.get("table_file_key") or table_file_key(r["path"]), d["row_id"])
            for r in readable for d in (r.get("table_mismatch") or [])}
    stale = [{"file": f, "row_id": rid, "note": n}
             for (f, rid), n in sorted(baseline.items()) if (f, rid) not in seen]

    if bad_local or unreadable_local or tbl_new_local:
        verdict = "TEXT-INTEGRITY-NEEDS-FIX"
    elif bad_ext or unreadable_ext or tbl_new_ext:
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
        "table_rows_mismatch": len(tbl_all),
        "table_mismatch_declared": tbl_declared,
        "table_mismatch_new": tbl_new_local,
        "table_mismatch_new_external": tbl_new_ext,
        "table_mismatch_stale": stale,
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

    # --- T212: table column defects (a row can be readable bytes yet unreadable
    # as a row). Q1 answered by construction: judgement is per row against the
    # file's own header anchor; the file level only aggregates.
    def _write(body, suffix=".md"):
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.write(fd, body.encode("utf-8"))
        os.close(fd)
        return path

    # G1 overflow: an unescaped pipe in the prose splits a cell away
    p = _write("| ID | 任务 | 判据 | 状态 |\n|---|---|---|---|\n"
               "| M1 | 甲 | 乙|丙 | **done** |\n")
    r = census([p])[0]
    os.unlink(p)
    run("G1 column-overflow",
        r["table_mismatch_n"] == 1 and r["table_mismatch"][0]["kind"] == COLUMN_OVER
        and r["table_mismatch"][0]["cells"] == 5 and r["table_mismatch"][0]["expected"] == 4,
        str([(d["cells"], d["expected"], d["kind"]) for d in r["table_mismatch"]]))

    # G2 underflow: the row is short, so the state cell never lands anywhere
    p = _write("| ID | 任务 | 判据 | 状态 |\n|---|---|---|---|\n"
               "| M2 | 甲 | 乙 |\n")
    r = census([p])[0]
    os.unlink(p)
    run("G2 column-underflow",
        r["table_mismatch_n"] == 1 and r["table_mismatch"][0]["kind"] == COLUMN_UNDER,
        str([(d["cells"], d["expected"], d["kind"]) for d in r["table_mismatch"]]))

    # G3 Q2 answer: `\|` is a literal pipe, not a separator -> row stays 4 cells
    p = _write("| ID | 任务 | 判据 | 状态 |\n|---|---|---|---|\n"
               "| M3 | 甲\\|乙 | 丙 | **done** |\n")
    r = census([p])[0]
    os.unlink(p)
    run("G3 escaped-pipe-not-separator",
        r["table_mismatch_n"] == 0 and split_table_cells("| a \\| b | c |") == [" a | b ", " c "],
        "n=%d cells=%r" % (r["table_mismatch_n"], split_table_cells("| a \\| b | c |")))

    # G4 a fenced example stays documentation
    p = _write("```\n| ID | 任务 |\n|---|---|\n| X | 甲 | 乙 | 丙 |\n```\n")
    r = census([p])[0]
    os.unlink(p)
    run("G4 fenced-table-ignored", r["table_mismatch_n"] == 0, "n=%d" % r["table_mismatch_n"])

    # G5 declared legacy row does not red; an undeclared one does (no silent
    # absorption by count -- the registry enumerates rows one by one)
    p = _write("| ID | 任务 | 判据 | 状态 |\n|---|---|---|---|\n"
               "| M9 | 甲 | 乙|丙 | x |\n")
    fkey = table_file_key(p)
    base = {(fkey, "M9"): "drill"}
    s = summarize(census([p], base), base)
    os.unlink(p)
    run("G5 declared-legacy-clean",
        s["verdict"] == "TEXT-INTEGRITY-CLEAN" and s["table_mismatch_declared"] == 1
        and not s["table_mismatch_new"],
        "%s declared=%d new=%d" % (s["verdict"], s["table_mismatch_declared"],
                                   len(s["table_mismatch_new"])))

    p = _write("| ID | 任务 | 判据 | 状态 |\n|---|---|---|---|\n"
               "| M9 | 甲 | 乙|丙 | x |\n| M10 | 甲 | 乙|丙 | x |\n")
    fkey = table_file_key(p)
    base = {(fkey, "M9"): "drill"}
    s = summarize(census([p], base), base)
    os.unlink(p)
    run("G6 undeclared-row-reds",
        s["verdict"] == "TEXT-INTEGRITY-NEEDS-FIX" and len(s["table_mismatch_new"]) == 1,
        "%s new=%d" % (s["verdict"], len(s["table_mismatch_new"])))

    # G7 Q3 answer: the two families never share a counter -- a column defect
    # must not inflate damaged_lines, and content damage must not read as a
    # column defect.
    p = _write("| ID | 任务 | 判据 | 状态 |\n|---|---|---|---|\n"
               "| M4 | 甲 | 乙|丙 | x |\n")
    s = summarize(census([p], {}), {})
    os.unlink(p)
    run("G7 families-separate",
        s["damaged_lines"] == 0 and len(s["table_mismatch_new"]) == 1,
        "damaged_lines=%d table_new=%d" % (s["damaged_lines"], len(s["table_mismatch_new"])))

    # G8 a fixed row leaves a stale declaration visible instead of silent
    p = _write("| ID | 任务 | 判据 | 状态 |\n|---|---|---|---|\n"
               "| M5 | 甲 | 乙 | x |\n")
    fkey = table_file_key(p)
    base = {(fkey, "M5"): "drill"}
    s = summarize(census([p], base), base)
    os.unlink(p)
    run("G8 stale-declaration-visible",
        s["verdict"] == "TEXT-INTEGRITY-CLEAN" and len(s["table_mismatch_stale"]) == 1,
        "stale=%d" % len(s["table_mismatch_stale"]))

    # G9 a pipe row without a header above it is not a table -> no opinion
    p = _write("正文\n| a | b | c | d | e |\n")
    r = census([p])[0]
    os.unlink(p)
    run("G9 headerless-row-ignored", r["table_mismatch_n"] == 0, "n=%d" % r["table_mismatch_n"])

    # G10 a ledger whose first cell is a timestamp must key on the row id, or
    # several different rows collapse into one registry key (real defect found
    # on decisions.md: four rows shared row_id "2026-11-05")
    cells = split_table_cells("| 2026-11-05 06:0x | D-20261105-07 **标题** | x |")
    run("G10 date-cell-falls-through",
        table_row_id(cells) == "D-20261105-07"
        and table_row_id(split_table_cells("| M47 | 10-05 |")) == "M47",
        "date_first=%r id_first=%r"
        % (table_row_id(cells), table_row_id(split_table_cells("| M47 | 10-05 |"))))

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        _print_safe("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))
    _print_safe("SELFTEST %d/%d %s" % (passed, len(checks), "PASS" if passed == len(checks) else "FAIL"))
    return 0 if passed == len(checks) else 1


def rounds_line(s):
    """One line for state/rounds.log (D-20261102-03 ③). Deterministic."""
    return ("textintegrity:%s cats=%d/%d files=%d damaged_files=%d damaged_lines=%d"
            " ext_damaged=%d ext_absent=%d unreadable=%d conflict_blocks=%d conflict_orphans=%d"
            " table_mismatch=%d table_new=%d"
            % ("CLEAN" if s["verdict"] == "TEXT-INTEGRITY-CLEAN" else
               ("EXTERNAL-DAMAGED" if s["verdict"] == "TEXT-INTEGRITY-EXTERNAL-DAMAGED"
                else "NEEDS-FIX"),
               s["categories_covered_n"], s["categories_required_n"],
               s["files_scanned"], s["files_damaged"], s["damaged_lines"],
               s["external_files_damaged"], len(s["absent_external"]),
               s["files_unreadable"], s.get("conflict_blocks", 0),
               s.get("conflict_orphans", 0),
               s.get("table_rows_mismatch", 0),
               len(s.get("table_mismatch_new") or [])
               + len(s.get("table_mismatch_new_external") or [])))


def main(argv=None):
    ap = argparse.ArgumentParser(description="line-level damage census for living index files")
    ap.add_argument("command", choices=["run", "selftest"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--json-out")
    ap.add_argument("--rounds-line", action="store_true",
                    help="also print the one-line rounds.log summary (D-03 ③)")
    ap.add_argument("--table-baseline", default=TABLE_BASELINE_DEFAULT,
                    help="declared legacy table-column defects (T212)")
    args = ap.parse_args(argv)

    if args.command == "selftest":
        return _selftest()

    baseline, baseline_path = load_table_baseline(args.table_baseline)
    paths = args.paths or DEFAULT_TARGETS
    s = summarize(census(paths, baseline), baseline)
    s["table_baseline_path"] = baseline_path
    s["table_baseline_declared"] = len(baseline)
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
        if r.get("table_mismatch_n"):
            new_n = len(r.get("table_mismatch_new") or [])
            _print_safe("        table: rows_mismatch=%d new=%d%s"
                        % (r["table_mismatch_n"], new_n,
                           "  [external/read-only]" if r.get("external") else ""))
            for d in r["table_mismatch"][:6]:
                _print_safe("        T%-5d %-16s cells=%d want=%d %s%s"
                            % (d["line"], d["kind"], d["cells"], d["expected"],
                               d.get("row_id", "?"),
                               " (declared)" if d.get("declared") else " (NEW)"))
            if r["table_mismatch_n"] > 6:
                _print_safe("        ... %d more mismatched rows"
                            % (r["table_mismatch_n"] - 6))
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
    _print_safe("TABLE rows_mismatch=%d declared=%d new_local=%d new_ext=%d stale=%d"
                % (s["table_rows_mismatch"], s["table_mismatch_declared"],
                   len(s["table_mismatch_new"]),
                   len(s["table_mismatch_new_external"]),
                   len(s["table_mismatch_stale"])))
    _print_safe("TABLE-BASELINE %s declared=%d" % (baseline_path, len(baseline)))
    for d in s["table_mismatch_new"][:10]:
        _print_safe("  NEW-ROW %s L%d %s cells=%d want=%d"
                    % (table_file_key(d["path"]), d["line"], d["kind"],
                       d["cells"], d["expected"]))
    if args.rounds_line:
        _print_safe("ROUNDS-LINE " + rounds_line(s))
    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(s, fh, ensure_ascii=False, indent=2)
        _print_safe("JSON  %s" % args.json_out)
    return 7 if s["verdict"] == "TEXT-INTEGRITY-NEEDS-FIX" else 0


if __name__ == "__main__":
    sys.exit(main())