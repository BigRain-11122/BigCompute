#!/usr/bin/env python3
"""Marker literal census: is every "is this one row or two rows" rule imported
from the single source, or has someone grown a local copy again?

Origin (T174, 2026-10-31): T172 moved the heartbeat row-head marker into
Tools/ledger_markers.py so the QA read side and the round writer could not
disagree. That fixed two files, not the class: a third consumer
(ledger_tail_fix) still carried its own clock-shape literal, and its seven-file
marker table was documented as a "mirror" of qa_smoke's when in fact the two
tables differed (the fixer's queue markers had no trailing cell anchor, so a
legal body-text mention of "| T85 |" read as a second row head). Each copy
drifts on its own schedule and the obligation goes asymmetric again.

What this tool checks
  SOURCE rows   the single source still defines the marker / table literals
  IMPORT rows   each consumer imports it (import literal + names present) and
                carries neither a local redefinition nor a raw clock literal
  DIFF rows     a declared *different* semantic (documented reason) keeps its
                literal; if the literal is gone the row is UNVERIFIED, never a
                silent pass
  sweep         any Tools/**.py holding a clock-shape literal that is not a
                registered file is reported as UNREGISTERED

Verdict banner (worst wins): MARKER-FIX > MARKER-UNREGISTERED >
MARKER-UNVERIFIED > MARKER-CLEAN.

Commands
  run [--json PATH]   census + optional evidence JSON
  selftest            fixture self test

Read-only: greps source text, edits nothing.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SELF = os.path.abspath(__file__)

# Clock-shape source literals. Kept here as data so the sweep is auditable;
# this file is excluded from its own sweep (T160 lesson: an instrument must
# not be part of what it measures).
SHAPE_TOKENS = (
    "\\d{2}:\\d{2}",
    "\\d{2}:\\d{1,2}x",
    "\\d{4}-\\d{2}-\\d{2}",
)


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def judge_row(row, text):
    """Verdict for one registry row given its file text (None = unreadable)."""
    if text is None:
        return "UNVERIFIED", "file not readable"
    kind = row["class"]
    if kind == "SOURCE":
        if row["literal"] not in text:
            return "UNVERIFIED", "source literal gone: %r" % row["literal"]
        return "SOURCE-OK", row["note"]
    if kind == "IMPORT":
        if row["import_literal"] not in text:
            return "UNVERIFIED", "import literal gone: %r" % row["import_literal"]
        missing = [n for n in row["names"] if n not in text]
        if missing:
            return "UNVERIFIED", "imported name(s) gone: %s" % ", ".join(missing)
        for lit in row.get("no_redefine", ()):
            if lit in text:
                return "NEEDS-FIX", "local copy is back: %r" % lit
        raw = row.get("forbid_raw")
        if raw and raw in text:
            return "NEEDS-FIX", "raw clock literal is back: %r" % raw
        return "IMPORT-OK", row["note"]
    if kind == "DIFF":
        if row["literal"] not in text:
            return "UNVERIFIED", "declared literal gone: %r" % row["literal"]
        return "DIFF-OK", row["note"]
    return "UNVERIFIED", "unknown row class %r" % kind


def _no_redefine(*names):
    return tuple(n + " = (" for n in names)


REGISTRY = [
    {
        "id": "SRC-HB",
        "file": "Tools/ledger_markers.py",
        "class": "SOURCE",
        "literal": "HB_MARKER = (",
        "note": "heartbeat row-head combo marker (T172)",
    },
    {
        "id": "SRC-TAIL",
        "file": "Tools/ledger_markers.py",
        "class": "SOURCE",
        "literal": "TAIL_PROBE_FILES = (",
        "note": "seven-ledger marker table (T174)",
    },
    {
        "id": "SRC-RAW-DIAG",
        "file": "Tools/ledger_markers.py",
        "class": "SOURCE",
        "literal": 'RAW_TS_RE = re.compile(r"\\d{2}:\\d{2}")',
        "note": "raw shape kept for diagnostics only; never a verdict",
    },
    {
        "id": "QA-SMOKE",
        "file": "Tools/qa_smoke.py",
        "class": "IMPORT",
        "import_literal": "from ledger_markers import",
        "names": ("HB_OPENERS", "HB_MARKER", "TAIL_PROBE_FILES"),
        "no_redefine": _no_redefine("TAIL_PROBE_FILES", "SEVEN_FILES"),
        "note": "read side of the tail probe imports the source table",
    },
    {
        "id": "TAIL-FIX",
        "file": "Tools/ledger_tail_fix.py",
        "class": "IMPORT",
        "import_literal": "from ledger_markers import",
        "names": ("HB_MARKER", "TAIL_PROBE_FILES", "count_markers"),
        "no_redefine": _no_redefine("TAIL_PROBE_FILES", "SEVEN_FILES"),
        "forbid_raw": "\\d{2}:\\d{2}",
        "note": "fixer judged with the combo marker, not any HH:MM pair",
    },
    {
        "id": "ROUND-APPEND",
        "file": "Tools/round_append.py",
        "class": "IMPORT",
        "import_literal": "from ledger_markers import",
        "names": ("count_markers", "count_raw_stamps"),
        "forbid_raw": "\\d{2}:\\d{2}",
        "note": "write side J5 lint (T172); raw shape stays diagnostic-only",
    },
    {
        "id": "SDM-ROUNDS-DATE",
        "file": "Tools/self_drive_metrics.py",
        "class": "DIFF",
        "literal": "m = re.match(r'(\\d{4}-\\d{2}-\\d{2}) ', raw)",
        "note": "rounds.log head reader that needs the date VALUE for the "
                "168h window filter, not a head count; drift is caught here",
    },
    {
        "id": "SDM-PROPOSAL-DATE",
        "file": "Tools/self_drive_metrics.py",
        "class": "DIFF",
        "literal": "(\\d{4}-\\d{2}-\\d{2})\\s*\\|', raw)",
        "note": "proposals.md row date extractor (same reason: value, not count)",
    },
    {
        "id": "LANE-GATE",
        "file": "Tools/lane_gate.py",
        "class": "DIFF",
        "literal": 'LINE_RE = re.compile(r"^(',
        "note": "parses one full ISO stamp to compute row age; x-forms are "
                "unusable for age arithmetic, so this is a different question "
                "from 'how many heads are on this line'",
    },
]


def sweep(base=None, registry=None, skip=None):
    """Files holding clock-shape literals that no registry row claims."""
    base = REPO if base is None else base
    registry = REGISTRY if registry is None else registry
    skip = {SELF} if skip is None else set(skip) | {SELF}
    claimed = {os.path.join(base, r["file"].replace("/", os.sep)) for r in registry}
    tool_dir = os.path.join(base, "Tools")
    out = []
    for root, _dirs, files in os.walk(tool_dir):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            if os.path.abspath(path) in skip or path in claimed:
                continue
            text = _read(path)
            if not text:
                continue
            hits = [t for t in SHAPE_TOKENS if t in text]
            if hits:
                out.append({
                    "file": os.path.relpath(path, base).replace(os.sep, "/"),
                    "shape_tokens": hits,
                })
    return out


def run_census(registry=None, base=None):
    registry = REGISTRY if registry is None else registry
    base = REPO if base is None else base
    rows = []
    for row in registry:
        path = os.path.join(base, row["file"].replace("/", os.sep))
        text = _read(path)
        verdict, why = judge_row(row, text)
        rows.append({
            "id": row["id"],
            "file": row["file"],
            "class": row["class"],
            "verdict": verdict,
            "rationale": why,
        })
    unregistered = sweep(base=base, registry=registry)
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    if counts.get("NEEDS-FIX"):
        overall = "MARKER-FIX"
    elif unregistered:
        overall = "MARKER-UNREGISTERED"
    elif counts.get("UNVERIFIED"):
        overall = "MARKER-UNVERIFIED"
    else:
        overall = "MARKER-CLEAN"
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "repo": REPO,
        "rows": rows,
        "counts": counts,
        "unregistered": unregistered,
        "verdict": overall,
        "honesty": "Static literal check only. An IMPORT-OK row proves the "
                   "consumer still imports the source names and has not "
                   "re-inlined a copy -- not that it uses them correctly at "
                   "every call site. Rows are hand-registered by design.",
    }


def cmd_run(args):
    rep = run_census()
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print("json -> %s" % args.json)
    for r in rep["rows"]:
        print("%-12s %-14s %-10s %s" % (r["verdict"], r["id"], r["class"],
                                        r["file"]))
        print("    %s" % r["rationale"])
    for u in rep["unregistered"]:
        print("UNREGISTERED   %s  %s" % (u["file"], ",".join(u["shape_tokens"])))
    print("VERDICT=%s %s unregistered=%d"
          % (rep["verdict"], " ".join("%s=%d" % kv for kv in sorted(rep["counts"].items())),
             len(rep["unregistered"])))
    return 0 if rep["verdict"] == "MARKER-CLEAN" else 1


def cmd_selftest(_args):
    import tempfile
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    imp = {"id": "F1", "file": "x.py", "class": "IMPORT",
           "import_literal": "from ledger_markers import",
           "names": ("HB_MARKER",), "no_redefine": _no_redefine("SEVEN_FILES"),
           "forbid_raw": "\\d{2}:\\d{2}", "note": ""}
    good = "from ledger_markers import HB_MARKER\n"
    check("S1 clean import -> IMPORT-OK", judge_row(imp, good)[0] == "IMPORT-OK")

    # S2 a local table copy is the defect this census exists to catch
    v, why = judge_row(imp, good + "SEVEN_FILES = (\n('a', r'x'),\n)\n")
    check("S2 local copy -> NEEDS-FIX",
          v == "NEEDS-FIX" and "SEVEN_FILES" in why)

    # S3 re-inlined raw clock rule
    v, _ = judge_row(imp, good + 'RE = re.compile(r"\\d{2}:\\d{2}")\n')
    check("S3 raw clock literal -> NEEDS-FIX", v == "NEEDS-FIX")

    # S4 import drift never passes silently
    check("S4 import gone -> UNVERIFIED",
          judge_row(imp, "HB_MARKER = 'x'\n")[0] == "UNVERIFIED")
    check("S4b name gone -> UNVERIFIED",
          judge_row(imp, "from ledger_markers import count_markers\n")[0] == "UNVERIFIED")
    check("S4c unreadable -> UNVERIFIED", judge_row(imp, None)[0] == "UNVERIFIED")

    # S5 source literal drift
    src = {"id": "F2", "file": "l.py", "class": "SOURCE",
           "literal": "TAIL_PROBE_FILES = (", "note": ""}
    check("S5 source present -> SOURCE-OK",
          judge_row(src, "TAIL_PROBE_FILES = (\n)\n")[0] == "SOURCE-OK")
    check("S5b source gone -> UNVERIFIED", judge_row(src, "x = 1\n")[0] == "UNVERIFIED")

    # S6 different-semantic row: literal must stay, absence is UNVERIFIED
    diff = {"id": "F3", "file": "g.py", "class": "DIFF",
            "literal": 'LINE_RE = re.compile(r"^(',
            "note": ""}
    check("S6 diff literal present -> DIFF-OK",
          judge_row(diff, 'LINE_RE = re.compile(r"^(\\d{4}")\n')[0] == "DIFF-OK")
    check("S6b diff literal gone -> UNVERIFIED", judge_row(diff, "x = 1\n")[0] == "UNVERIFIED")

    # S7 sweep finds an unregistered clock literal and is deterministic
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "Tools"))
        with open(os.path.join(td, "Tools", "rogue.py"), "w", encoding="utf-8") as f:
            f.write('P = re.compile(r"\\d{2}:\\d{2}")\n')
        with open(os.path.join(td, "Tools", "claimed.py"), "w", encoding="utf-8") as f:
            f.write('P = re.compile(r"\\d{2}:\\d{2}")\n')
        reg = [{"id": "C1", "file": "Tools/claimed.py", "class": "SOURCE",
                "literal": "P = re.compile(", "note": ""}]
        u1 = sweep(base=td, registry=reg)
        u2 = sweep(base=td, registry=reg)
        names = [x["file"] for x in u1]
        check("S7 unregistered = only the rogue file",
              names == ["Tools/rogue.py"])
        check("S7b sweep deterministic", u1 == u2)

    # S8 aggregation: NEEDS-FIX wins the banner
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "Tools"))
        for name, body in (("x.py", "from ledger_markers import HB_MARKER\n"
                                     "SEVEN_FILES = (\n)\n"),):
            with open(os.path.join(td, "Tools", name), "w", encoding="utf-8") as f:
                f.write(body)
        rep8 = run_census([dict(imp, file="Tools/x.py")], base=td)
    check("S8 aggregation -> MARKER-FIX",
          rep8["verdict"] == "MARKER-FIX"
          and rep8["rows"][0]["verdict"] == "NEEDS-FIX")

    # S9 aggregation: an unregistered clock literal beats a clean registry
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "Tools"))
        with open(os.path.join(td, "Tools", "x.py"), "w", encoding="utf-8") as f:
            f.write(good)
        with open(os.path.join(td, "Tools", "rogue.py"), "w", encoding="utf-8") as f:
            f.write('P = re.compile(r"\\d{4}-\\d{2}-\\d{2}")\n')
        rep9 = run_census([dict(imp, file="Tools/x.py")], base=td)
    check("S9 aggregation -> MARKER-UNREGISTERED",
          rep9["verdict"] == "MARKER-UNREGISTERED"
          and rep9["rows"][0]["verdict"] == "IMPORT-OK")

    # S10 aggregation: readable + clean -> MARKER-CLEAN
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "Tools"))
        with open(os.path.join(td, "Tools", "x.py"), "w", encoding="utf-8") as f:
            f.write(good)
        rep10 = run_census([dict(imp, file="Tools/x.py")], base=td)
    check("S10 aggregation -> MARKER-CLEAN", rep10["verdict"] == "MARKER-CLEAN")

    # S11 the live registry resolves on the real repo
    live = run_census()
    check("S11 live rows readable",
          all(r["verdict"] != "UNVERIFIED" for r in live["rows"]))
    check("S11b live has no NEEDS-FIX",
          live["counts"].get("NEEDS-FIX", 0) == 0)
    check("S11c live sweep clean", live["unregistered"] == [])

    # S12 json round-trip
    try:
        json.loads(json.dumps(live, ensure_ascii=False))
        ok = True
    except (TypeError, ValueError):
        ok = False
    check("S12 json serializable", ok)

    failed = [n for n, ok_ in checks if not ok_]
    for name, ok_ in checks:
        print("%s %s" % ("PASS" if ok_ else "FAIL", name))
    print("selftest %d/%d %s" % (len(checks) - len(failed), len(checks),
                                 "PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="census marker literals")
    p_run.add_argument("--json", default="")
    p_run.set_defaults(func=cmd_run)

    p_st = sub.add_parser("selftest", help="fixture self test")
    p_st.set_defaults(func=cmd_selftest)

    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())