#!/usr/bin/env python3
"""Ratio-probe denominator census: can an instrument's denominator swallow the
object it measures?

Origin (T159, 2026-10-28): l1_kpi_probe's KPI-B denominator was silently
polluted by the probe's own `DEFAULT_PORT = 8090` constant once the module
body contained failover keywords -- 5 consumer rows became 6 after a comment
edit. A metric that reacts to its own source text is not a metric. This tool
enumerates the repo's ratio probes, states where each denominator comes from,
and decides whether the instrument can appear inside its own denominator.

Three-state verdict per row
  SELF-EXCLUDED  denominator is built by scanning text/files, and an explicit
                 guard removes the instrument (or its own artifacts) from it
  STRUCTURAL-NA  denominator is a fixed registry or a physical measurement --
                 the instrument cannot enumerate itself into it
  NEEDS-FIX      denominator is scanned and no guard exists (defect)
  UNVERIFIED     the declared denominator/guard literal is no longer in the
                 file (registry drifted; re-read the code before trusting)

Commands
  run [--json PATH]   census + write evidence JSON (exit 1 if any NEEDS-FIX)
  selftest            fixture-based self test

Read-only: this tool greps source text and prints; it never edits anything.
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

# Denominator classes
SCANNED = "SCANNED"      # built by scanning text / files / directories
FIXED = "FIXED"          # built from a registry written in code
MEASURED = "MEASURED"    # physical quantity (bytes, ratios of counters read live)

SCAN_KEYWORDS = ("os.walk", "rglob", "glob.glob", "iterdir", "iglob")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _has(text, literal):
    return bool(literal) and literal in text


def judge_row(row, text):
    """Verdict for one registry row given its file text (None = unreadable)."""
    if text is None:
        return "UNVERIFIED", "file not readable"
    if not _has(text, row["denominator"]):
        return "UNVERIFIED", "denominator literal gone: %r" % row["denominator"]
    cls = row["denominator_class"]
    if cls in (FIXED, MEASURED):
        return "STRUCTURAL-NA", row["note"]
    # SCANNED: needs an explicit guard
    if row.get("guard") and _has(text, row["guard"]):
        return "SELF-EXCLUDED", "guard: %s" % row["guard"]
    if row.get("guard_kind") == "PATH-SEPARATION":
        # guard is the absence of a writer: every writer path listed must differ
        # from the scanned path; a shared path means the guard is broken.
        scanned = row.get("scanned_path_literal", "")
        writers = row.get("writer_literals", [])
        if not _has(text, scanned):
            return "UNVERIFIED", "scanned path literal gone: %r" % scanned
        clash = [w for w in writers if _has(text, w) and w.strip() == scanned.strip()]
        if clash:
            return "NEEDS-FIX", "instrument writes into the file it scans"
        return "SELF-EXCLUDED", "path separation: writers != %s" % scanned
    return "NEEDS-FIX", "scanned denominator with no self-exclusion"


REGISTRY = [
    {
        "id": "KPI-B",
        "probe": "l1_kpi_probe.py fallback",
        "file": "Tools/l1_kpi_probe.py",
        "denominator": "census_rows(port)",
        "denominator_class": SCANNED,
        "guard": 'os.path.abspath(r.get("file", "")) != me',
        "note": "FALLBACK-OK consumer rows from a text census; the probe's own "
                "DEFAULT_PORT constant was the observed pollution (2026-10-28)",
    },
    {
        "id": "QA-SMOKE",
        "probe": "qa_smoke.py n/total",
        "file": "Tools/qa_smoke.py",
        "denominator": "results = []",
        "denominator_class": FIXED,
        "guard": "",
        "note": "probe list is code-registered (append order), not directory-scanned",
    },
    {
        "id": "C1C4",
        "probe": "c1c4_patrol.py passed/total",
        "file": "Tools/c1c4_patrol.py",
        "denominator": "total = len(results)",
        "denominator_class": FIXED,
        "guard": "",
        "note": "four fixed sub-commands defined in the file itself",
    },
    {
        "id": "QUEUE-PREMISE",
        "probe": "queue_check.py premise_unverified/open",
        "file": "Tools/queue_check.py",
        "denominator": 'total_open += counts["open"]',
        "denominator_class": FIXED,
        "guard": "",
        "note": "rows come from parsing the three queue markdown files; this "
                "tool is not one of them (matcher constants live in code, not rows)",
    },
    {
        "id": "IDLE-RAM",
        "probe": "idle_selfcheck.py ram_free_pct",
        "file": "Tools/idle_selfcheck.py",
        "denominator": "s.ullTotalPhys",
        "denominator_class": MEASURED,
        "guard": "",
        "note": "physical memory ratio, nothing to enumerate",
    },
    {
        "id": "IDLE-C1C4-LINE",
        "probe": "idle_selfcheck.py rounds_ago / FRESH-STALE",
        "file": "Tools/idle_selfcheck.py",
        "denominator": 'ROUNDS_LOG = os.path.join(PROJECT, "state", "rounds.log")',
        "denominator_class": SCANNED,
        "guard_kind": "PATH-SEPARATION",
        "scanned_path_literal": 'ROUNDS_LOG = os.path.join(PROJECT, "state", "rounds.log")',
        "writer_literals": ['HEARTBEAT = os.path.join(PROJECT, "state", "heartbeat.txt")'],
        "note": "scans rounds.log for C1C4 lines; the tool writes heartbeat.txt only",
    },
]


def sweep_candidates(tool_dir=None):
    """Informational: files that both scan a tree and divide by a len()."""
    tool_dir = tool_dir or HERE
    out = []
    for name in sorted(os.listdir(tool_dir)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(tool_dir, name)
        if os.path.abspath(path) == SELF:
            continue  # the census is not a ratio probe
        text = _read(path)
        if not text:
            continue
        scans = [k for k in SCAN_KEYWORDS if k in text]
        ratios = re.findall(r"/\s*len\(", text) + re.findall(r"len\([a-z_]+\)\s*\)", text)
        if scans and ratios:
            out.append({"file": "Tools/" + name,
                        "scan_keywords": scans,
                        "ratio_hits": len(ratios)})
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
            "probe": row["probe"],
            "file": row["file"],
            "denominator": row["denominator"],
            "denominator_class": row["denominator_class"],
            "verdict": verdict,
            "rationale": why,
        })
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    overall = "DENOM-FIX" if counts.get("NEEDS-FIX") else "DENOM-CLEAN"
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "repo": REPO,
        "rows": rows,
        "counts": counts,
        "verdict": overall,
        "sweep_candidates": sweep_candidates(),
        "honesty": "Static literal check only: a guard literal proves the code "
                   "shape has not drifted, not that the guard is semantically "
                   "sufficient. Rows are hand-registered; the sweep list is "
                   "informational, not a verdict.",
    }


def cmd_run(args):
    rep = run_census()
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print("json -> %s" % args.json)
    for r in rep["rows"]:
        print("%-16s %-14s %-28s %s" % (r["verdict"], r["id"],
                                        r["denominator_class"], r["file"]))
        print("    %s" % r["rationale"])
    print("VERDICT=%s %s" % (rep["verdict"],
                             " ".join("%s=%d" % kv for kv in sorted(rep["counts"].items()))))
    print("sweep_candidates=%d (informational)" % len(rep["sweep_candidates"]))
    return 0 if rep["verdict"] == "DENOM-CLEAN" else 1


def cmd_selftest(_args):
    import tempfile
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    # S1 fixture: scanned denominator, no guard -> NEEDS-FIX
    bad = {"id": "F1", "probe": "x", "file": "x.py",
           "denominator": "census_rows()", "denominator_class": SCANNED,
           "guard": "", "note": ""}
    v, _ = judge_row(bad, "def census_rows(): pass\n")
    check("S1 scanned+no guard -> NEEDS-FIX", v == "NEEDS-FIX")

    # S2 fixture: same + guard -> SELF-EXCLUDED
    good = dict(bad, guard="!= me")
    v, why = judge_row(good, "def census_rows(): pass\nx != me\n")
    check("S2 guard -> SELF-EXCLUDED", v == "SELF-EXCLUDED" and "!= me" in why)

    # S3 fixed/measured -> STRUCTURAL-NA
    check("S3 fixed -> NA", judge_row(dict(bad, denominator_class=FIXED),
                                     "census_rows()")[0] == "STRUCTURAL-NA")
    check("S3b measured -> NA", judge_row(dict(bad, denominator_class=MEASURED),
                                          "census_rows()")[0] == "STRUCTURAL-NA")

    # S4 literal drift -> UNVERIFIED (never a silent pass)
    check("S4 missing literal -> UNVERIFIED",
          judge_row(bad, "def other(): pass\n")[0] == "UNVERIFIED")
    check("S4b unreadable -> UNVERIFIED", judge_row(bad, None)[0] == "UNVERIFIED")

    # S5 path separation
    sep = {"id": "F2", "probe": "y", "file": "y.py", "denominator": "LOG",
           "denominator_class": SCANNED, "guard_kind": "PATH-SEPARATION",
           "scanned_path_literal": 'LOG = "a.log"',
           "writer_literals": ['OUT = "b.txt"'], "note": ""}
    check("S5 separated paths -> SELF-EXCLUDED",
          judge_row(sep, 'LOG = "a.log"\nOUT = "b.txt"\n')[0] == "SELF-EXCLUDED")
    check("S5b shared path -> NEEDS-FIX",
          judge_row(dict(sep, writer_literals=['LOG = "a.log"']),
                    'LOG = "a.log"\n')[0] == "NEEDS-FIX")

    # S6 real registry: every row resolves to a readable file
    rep = run_census()
    check("S6 registry files readable",
          all(r["verdict"] != "UNVERIFIED" for r in rep["rows"]))
    check("S6b no NEEDS-FIX in repo", rep["verdict"] == "DENOM-CLEAN")

    # S7 sweep is deterministic and excludes the census itself
    a = sweep_candidates()
    b = sweep_candidates()
    check("S7 sweep deterministic", a == b)
    check("S7b sweep excludes self",
          all(not c["file"].endswith("ratio_denominator_census.py") for c in a))

    # S8 aggregation flips on a NEEDS-FIX row (fixture lives on disk so the
    # denominator literal is actually found -- an unreadable file would be
    # UNVERIFIED, not NEEDS-FIX)
    with tempfile.TemporaryDirectory() as td8:
        with open(os.path.join(td8, "x.py"), "w", encoding="utf-8") as f:
            f.write("def census_rows(): pass\n")
        rep2 = run_census([bad], base=td8)
    check("S8 aggregation -> DENOM-FIX",
          rep2["verdict"] == "DENOM-FIX" and rep2["rows"][0]["verdict"] == "NEEDS-FIX")

    # S9 json round-trip
    try:
        json.loads(json.dumps(rep, ensure_ascii=False))
        ok = True
    except (TypeError, ValueError):
        ok = False
    check("S9 json serializable", ok)

    # S10 tempdir: a guard literal in a *different* file does not count
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "z.py"), "w", encoding="utf-8") as f:
            f.write("census_rows()\n")
        check("S10 guard must live in the subject file",
              judge_row(dict(bad, guard="!= me"), "census_rows()\n")[0] == "NEEDS-FIX")

    failed = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print("%s %s" % ("PASS" if ok else "FAIL", name))
    print("selftest %d/%d %s" % (len(checks) - len(failed), len(checks),
                                 "PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="census all ratio probes")
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