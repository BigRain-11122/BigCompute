#!/usr/bin/env python3
"""Scan-root census: can a scanning probe count itself, or its own artifacts?

Origin (T160, 2026-10-29): T159 fixed the *denominator* side of one probe
(l1_kpi_probe counted its own DEFAULT_PORT constant). The same failure shape
exists on the numerator side and in every other tool that walks a directory:
if a probe's scan root covers the probe's own source or the files the probe
writes, the reading moves with the instrument's own text.

Examples of the failure (none of them present today, all of them possible):
  - a tool that walks Tools/*.py to count model names, without skipping itself
  - a tool that writes its report into the directory it measures "newest file"
  - a health probe whose own output file satisfies its own freshness check

This census enumerates the repo's scanning probes, records the literal that
defines each scan root, and decides whether that root can contain the probe
or its artifacts.

States
  ROOT-SEPARATED  root may contain the probe/artifacts, but an explicit guard
                  removes it (self-skip, exclude set, or writer path differs
                  from scanned path)
  STRUCTURAL-NA   by construction the probe cannot enumerate itself: external
                  root, caller-supplied root, filename-marker match, input-only
                  consumer, or a subject set it is a legitimate member of
  NEEDS-EXCLUDE   root covers the probe or its artifacts and no guard exists
  UNVERIFIED      the declared root literal is gone from the file (registry
                  drifted; re-read the code before trusting the row)

Commands
  run [--json PATH]   census + evidence JSON (exit 1 if any NEEDS-EXCLUDE)
  selftest            fixture-based self test

Read-only: greps source text and prints. It never edits anything.
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

# Root classes
SELF_DIR = "SELF_DIR"          # walks the directory that holds the probe
REPO_WALK = "REPO_WALK"        # walks the whole repository
EXTERNAL = "EXTERNAL"          # walks a fixed root outside this repo
ARGV = "ARGV"                  # root comes from the caller
SUBJECT = "SUBJECT"            # root is the subject population; probe is a member
INPUT_ONLY = "INPUT_ONLY"      # reads another tool's artifacts, writes elsewhere

SCAN_KEYWORDS = ("os.walk", "rglob", "glob.glob", "os.listdir", "iterdir", "iglob")

REGISTRY = [
    {
        "id": "KPI-B",
        "probe": "l1_kpi_probe.py fallback census",
        "file": "Tools/l1_kpi_probe.py",
        "scan_literal": "def census_rows(",
        "root_class": SELF_DIR,
        "guard": 'os.path.abspath(r.get("file", "")) != me',
        "note": "T159: its own DEFAULT_PORT constant was once counted as a row",
    },
    {
        "id": "RATIO-CENSUS-SWEEP",
        "probe": "ratio_denominator_census.py sweep",
        "file": "Tools/ratio_denominator_census.py",
        "scan_literal": "for name in sorted(os.listdir(tool_dir)):",
        "root_class": SELF_DIR,
        "guard": "if os.path.abspath(path) == SELF:",
        "note": "candidate sweep skips the census itself",
    },
    {
        "id": "MODEL-REF-DRIFT",
        "probe": "model_ref_drift_check.py scan_tools",
        "file": "Tools/model_ref_drift_check.py",
        "scan_literal": "glob.glob(tools_glob, recursive=True)",
        "root_class": SELF_DIR,
        "guard": "if os.path.basename(path) == os.path.basename(__file__):",
        "note": "its BARE_BLOCKLIST/model literals would otherwise self-report",
    },
    {
        "id": "L1-REF-CENSUS",
        "probe": "l1_ref_census.py scan",
        "file": "Tools/l1_ref_census.py",
        "scan_literal": "for dirpath, dirnames, filenames in os.walk(root):",
        "root_class": SELF_DIR,
        "guard": "if os.path.basename(name) == os.path.basename(__file__):",
        "note": "scans this repo Tools plus group Tools; skips itself by name",
    },
    {
        "id": "STATUS-EXPORT",
        "probe": "status_export_check.py compose latest-artifact",
        "file": "Tools/status_export_check.py",
        "scan_literal": "for dirpath, dirnames, filenames in os.walk(base):",
        "root_class": SELF_DIR,
        "guard": '"docs/status-export.json", "docs/status-export-overrides.json",',
        "note": "walks state/qa/docs/Tools and excludes its own two outputs",
    },
    {
        "id": "SOP-COVERAGE",
        "probe": "sop_coverage_check.py F3 tools drift",
        "file": "Tools/sop_coverage_check.py",
        "scan_literal": 'actual_tools = len(glob.glob(os.path.join(repo, "Tools", "*.py")))',
        "root_class": SUBJECT,
        "guard": "",
        "note": "every Tools/*.py must be declared; the probe is a legitimate "
                "member of the population it counts, and DECLARED_TOOLS drift "
                "surfaces any change instead of hiding it",
    },
    {
        "id": "STORM-LEFTOVER",
        "probe": "storm_leftover_check.py conflict-copy scan",
        "file": "Tools/storm_leftover_check.py",
        "scan_literal": "for dirpath, dirnames, filenames in os.walk(root):",
        "root_class": REPO_WALK,
        "match": "filename-marker",
        "note": "matches only filenames carrying the conflict marker; the "
                "probe's own outputs cannot carry it",
    },
    {
        "id": "MODEL-IMPACT",
        "probe": "model_impact_probe.py code_hits",
        "file": "Tools/model_impact_probe.py",
        "scan_literal": "CONSUMER_REPOS = [",
        "root_class": EXTERNAL,
        "note": "fixed list of consumer repos outside this repo (T160 origin)",
    },
    {
        "id": "BATCH-TOKEN",
        "probe": "batch_token_estimate.py walk",
        "file": "Tools/batch_token_estimate.py",
        "scan_literal": "for root, _dirs, files in os.walk(args.dir):",
        "root_class": ARGV,
        "note": "root is the caller's; the tool writes nothing",
    },
    {
        "id": "HOLIDAY-READY",
        "probe": "holiday_readiness_check.py activity freshness",
        "file": "Tools/holiday_readiness_check.py",
        "scan_literal": 'glob.glob(os.path.join(ROOT, "qa", "smoke-*.log"))',
        "root_class": SELF_DIR,
        "guard_kind": "PATH-SEPARATION",
        "scanned_literal": 'glob.glob(os.path.join(ROOT, "qa", "smoke-*.log"))',
        "writer_literals": ['os.path.join(STATE, "holiday-readiness-%s.json" % ts)'],
        "note": "reads qa/smoke-*.log; writes state/holiday-readiness-*.json",
    },
    {
        "id": "REVIEW-PKG",
        "probe": "review_pkg_refresh.py trash census",
        "file": "Tools/review_pkg_refresh.py",
        "scan_literal": "for dirpath, _dirs, files in os.walk(trash_dir):",
        "root_class": INPUT_ONLY,
        "note": "counts trash contents as report input; writes to docs/ops",
    },
    {
        "id": "IDLE-C1C4",
        "probe": "idle_selfcheck.py latest_c1c4_evidence",
        "file": "Tools/idle_selfcheck.py",
        "scan_literal": "names = sorted(n for n in os.listdir(d)",
        "root_class": INPUT_ONLY,
        "note": "reads c1c4_patrol artifacts; writes heartbeat/rounds only",
    },
    {
        "id": "SERVE-CONTRACT-AUDIT",
        "probe": "serve_contract_audit.py rolling inventory baseline",
        "file": "Tools/serve_contract_audit.py",
        "scan_literal": "cands = sorted(glob.glob(os.path.join(state_dir, "
                        '"serve-contract-audit-*.json")))',
        "root_class": SELF_DIR,
        "match": "baseline-chain",
        "note": "reads its own previous artifact as the rolling baseline and "
                "writes a new one under the same pattern; baseline is read "
                "before the current run writes, so a run never grades itself",
    },
    {
        "id": "SERVE-RESTORE-MANIFEST",
        "probe": "serve_restore_manifest.py audit input",
        "file": "Tools/serve_restore_manifest.py",
        "scan_literal": 'cands = sorted(glob.glob(os.path.join(STATE, "serve-contract-audit-*.json")),',
        "root_class": INPUT_ONLY,
        "note": "consumes serve_contract_audit artifacts; writes manifest/log",
    },
    {
        "id": "BAKE-ACCEPT",
        "probe": "bake_accept_check.py auto_dirs",
        "file": "Tools/bake_accept_check.py",
        "scan_literal": 'os.listdir(root) if d.startswith("bake-spike-")',
        "root_class": INPUT_ONLY,
        "note": "lists bake run dirs to read metrics; prints, writes nothing",
    },
    {
        "id": "CITY3D-READINESS",
        "probe": "city3d_bake_readiness_probe.py pack scan",
        "file": "Tools/city3d_bake_readiness_probe.py",
        "scan_literal": 'files = [p for p in pack_dir.rglob("*") if p.is_file()]',
        "root_class": EXTERNAL,
        "note": "asset packs live in the City3D project, not this repo",
    },
    {
        "id": "CITY3D-SPIKE",
        "probe": "city3d_bake_spike.py dest scan",
        "file": "Tools/city3d_bake_spike.py",
        "scan_literal": 'for p in dest.rglob("*") if p.is_file()',
        "root_class": EXTERNAL,
        "note": "counts the labbench bake output tree, outside this repo",
    },
    {
        "id": "KNOWLEDGE-SYNC",
        "probe": "knowledge_sync_audit.py installed/canonical census",
        "file": "Tools/knowledge_sync_audit.py",
        "scan_literal": "installed = sorted(os.listdir(inst_dir)) if os.path.isdir(inst_dir) else []",
        "root_class": INPUT_ONLY,
        "note": "reads .codely-cli/skills and docs/research; writes state json",
    },
    {
        "id": "MODEL-BLOB-MANIFEST",
        "probe": "model_blob_manifest.py dedup walk",
        "file": "Tools/model_blob_manifest.py",
        "scan_literal": "for dirpath, _dirs, files in os.walk(mdir):",
        "root_class": EXTERNAL,
        "note": "walks a model directory supplied by the caller, outside this repo",
    },
]


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
    if not _has(text, row["scan_literal"]):
        return "UNVERIFIED", "scan root literal gone: %r" % row["scan_literal"]

    if row.get("match") == "filename-marker":
        return ("STRUCTURAL-NA",
                "filename-marker match cannot hit the probe or its artifacts")

    if row.get("match") == "baseline-chain":
        return ("STRUCTURAL-NA",
                "reads its own prior artifact as baseline before writing; "
                "a run cannot grade itself")

    if row.get("guard") and _has(text, row["guard"]):
        return "ROOT-SEPARATED", "guard: %s" % row["guard"]

    if row.get("guard_kind") == "PATH-SEPARATION":
        scanned = row.get("scanned_literal", "")
        writers = row.get("writer_literals", [])
        if not _has(text, scanned):
            return "UNVERIFIED", "scanned path literal gone: %r" % scanned
        clash = [w for w in writers if w.strip() == scanned.strip() and _has(text, w)]
        if clash:
            return "NEEDS-EXCLUDE", "probe writes into the path it scans"
        missing = [w for w in writers if not _has(text, w)]
        if missing:
            return "UNVERIFIED", "writer literal gone: %r" % missing[0]
        return "ROOT-SEPARATED", "writer path differs from scanned path"

    cls = row["root_class"]
    if cls == EXTERNAL:
        return "STRUCTURAL-NA", "root is outside this repo"
    if cls == ARGV:
        return "STRUCTURAL-NA", "root is caller-supplied; probe writes nothing"
    if cls == INPUT_ONLY:
        return "STRUCTURAL-NA", "reads another tool's artifacts, writes elsewhere"
    if cls == SUBJECT:
        return "STRUCTURAL-NA", "probe is a legitimate member of its subject set"
    return "NEEDS-EXCLUDE", "scan root may cover the probe with no guard"


def sweep_candidates(tool_dir=None, registry=None):
    """Informational: scanners on disk that this census does not know about."""
    tool_dir = tool_dir or HERE
    known = set(os.path.basename(r["file"]) for r in (registry or REGISTRY))
    out = []
    if not os.path.isdir(tool_dir):
        return out
    for name in sorted(os.listdir(tool_dir)):
        if not name.endswith(".py") or name in known:
            continue
        path = os.path.join(tool_dir, name)
        if os.path.abspath(path) == SELF:
            continue  # the census is not one of its own rows
        text = _read(path)
        if not text:
            continue
        hits = [k for k in SCAN_KEYWORDS if k in text]
        if hits:
            out.append({"file": "Tools/" + name, "scan_keywords": hits})
    return out


def run_census(registry=None, base=None):
    registry = REGISTRY if registry is None else registry
    base = REPO if base is None else base
    rows = []
    for row in registry:
        path = os.path.join(base, row["file"].replace("/", os.sep))
        verdict, why = judge_row(row, _read(path))
        rows.append({
            "id": row["id"],
            "probe": row["probe"],
            "file": row["file"],
            "root_class": row["root_class"],
            "scan_literal": row["scan_literal"],
            "verdict": verdict,
            "rationale": why,
        })
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    overall = "SCAN-ROOT-FIX" if counts.get("NEEDS-EXCLUDE") else "SCAN-ROOT-CLEAN"
    if overall == "SCAN-ROOT-CLEAN" and counts.get("UNVERIFIED"):
        # T162: unreadable file / drifted literal is not a green, so the
        # banner must not read CLEAN while a row could not be judged.
        overall = "SCAN-ROOT-UNVERIFIED"
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "repo": REPO,
        "rows": rows,
        "counts": counts,
        "verdict": overall,
        "sweep_candidates": sweep_candidates(registry=registry),
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
        print("%-16s %-20s %-12s %s" % (r["verdict"], r["id"],
                                        r["root_class"], r["file"]))
        print("    %s" % r["rationale"])
    print("VERDICT=%s %s" % (rep["verdict"],
                             " ".join("%s=%d" % kv for kv in sorted(rep["counts"].items()))))
    print("sweep_candidates=%d (informational)" % len(rep["sweep_candidates"]))
    for c in rep["sweep_candidates"]:
        print("    unregistered scanner: %s %s" % (c["file"], ",".join(c["scan_keywords"])))
    return 0 if rep["verdict"] == "SCAN-ROOT-CLEAN" else 1


def cmd_selftest(_args):
    import tempfile
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    bad = {"id": "F1", "probe": "x", "file": "x.py",
           "scan_literal": "for f in os.walk(d):", "root_class": SELF_DIR,
           "guard": "", "note": ""}
    head = "for f in os.walk(d):\n"

    # S1 scanned self dir, no guard -> NEEDS-EXCLUDE
    v, _ = judge_row(bad, head)
    check("S1 self dir + no guard -> NEEDS-EXCLUDE", v == "NEEDS-EXCLUDE")

    # S2 same + guard -> ROOT-SEPARATED
    v, why = judge_row(dict(bad, guard="!= me"), head + "!= me\n")
    check("S2 guard -> ROOT-SEPARATED", v == "ROOT-SEPARATED" and "!= me" in why)

    # S3 path separation
    sep = {"id": "F2", "probe": "y", "file": "y.py",
           "scan_literal": 'glob(os.path.join(R, "qa", "s-*.log"))',
           "root_class": SELF_DIR, "guard_kind": "PATH-SEPARATION",
           "scanned_literal": 'glob(os.path.join(R, "qa", "s-*.log"))',
           "writer_literals": ['os.path.join(S, "rep-%s.json" % ts)'], "note": ""}
    body = 'glob(os.path.join(R, "qa", "s-*.log"))\n' \
           'os.path.join(S, "rep-%s.json" % ts)\n'
    check("S3 separated paths -> ROOT-SEPARATED",
          judge_row(sep, body)[0] == "ROOT-SEPARATED")
    shared = 'glob(os.path.join(R, "qa", "s-*.log"))\n'
    check("S3b shared path -> NEEDS-EXCLUDE",
          judge_row(dict(sep, writer_literals=['glob(os.path.join(R, "qa", "s-*.log"))']),
                    shared)[0] == "NEEDS-EXCLUDE")

    # S4 drift is never a silent pass
    check("S4 missing root literal -> UNVERIFIED",
          judge_row(bad, "def other(): pass\n")[0] == "UNVERIFIED")
    check("S4b unreadable -> UNVERIFIED", judge_row(bad, None)[0] == "UNVERIFIED")
    check("S4c missing writer literal -> UNVERIFIED",
          judge_row(sep, 'glob(os.path.join(R, "qa", "s-*.log"))\n')[0] == "UNVERIFIED")

    # S5 structural classes
    for cls, name in ((EXTERNAL, "S5 external"), (ARGV, "S5b argv"),
                      (INPUT_ONLY, "S5c input-only"), (SUBJECT, "S5d subject set")):
        check("%s -> STRUCTURAL-NA" % name,
              judge_row(dict(bad, root_class=cls), head)[0] == "STRUCTURAL-NA")
    check("S5e filename-marker -> STRUCTURAL-NA",
          judge_row(dict(bad, root_class=REPO_WALK, match="filename-marker"),
                    head)[0] == "STRUCTURAL-NA")
    check("S5f baseline-chain -> STRUCTURAL-NA",
          judge_row(dict(bad, match="baseline-chain"), head)[0] == "STRUCTURAL-NA")

    # S6 the real registry resolves and is clean
    rep = run_census()
    check("S6 registry files readable",
          all(r["verdict"] != "UNVERIFIED" for r in rep["rows"]))
    check("S6b no NEEDS-EXCLUDE in repo", rep["verdict"] == "SCAN-ROOT-CLEAN")
    ids = [r["id"] for r in rep["rows"]]
    check("S6c registry ids unique", len(ids) == len(set(ids)))

    # S7 sweep determinism / self exclusion / new-scanner detection
    a = sweep_candidates()
    b = sweep_candidates()
    check("S7 sweep deterministic", a == b)
    check("S7b sweep excludes the census itself",
          all(not c["file"].endswith("scan_root_census.py") for c in a))
    with tempfile.TemporaryDirectory() as td7:
        with open(os.path.join(td7, "brand_new_scanner.py"), "w", encoding="utf-8") as f:
            f.write("import os\nfor r, d, fs in os.walk('.'):\n    pass\n")
        found = sweep_candidates(tool_dir=td7, registry=REGISTRY)
    check("S7c unregistered scanner is surfaced",
          any(c["file"].endswith("brand_new_scanner.py") for c in found))

    # S8 aggregation flips on a NEEDS-EXCLUDE row (fixture on disk so the
    # literal is really found -- an unreadable file would be UNVERIFIED)
    with tempfile.TemporaryDirectory() as td8:
        with open(os.path.join(td8, "x.py"), "w", encoding="utf-8") as f:
            f.write(head)
        rep2 = run_census([bad], base=td8)
    check("S8 aggregation -> SCAN-ROOT-FIX",
          rep2["verdict"] == "SCAN-ROOT-FIX"
          and rep2["rows"][0]["verdict"] == "NEEDS-EXCLUDE")

    # S11 an UNVERIFIED row must not ride under a CLEAN banner (T162)
    with tempfile.TemporaryDirectory() as td11:
        rep_unv = run_census([bad], base=td11)  # file absent -> UNVERIFIED
    check("S11 aggregation -> SCAN-ROOT-UNVERIFIED",
          rep_unv["verdict"] == "SCAN-ROOT-UNVERIFIED"
          and rep_unv["rows"][0]["verdict"] == "UNVERIFIED")

    # S9 json round-trip
    try:
        json.loads(json.dumps(rep, ensure_ascii=False))
        ok = True
    except (TypeError, ValueError):
        ok = False
    check("S9 json serializable", ok)

    # S10 a guard literal living in another file must not count
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "z.py"), "w", encoding="utf-8") as f:
            f.write(head)
        check("S10 guard must live in the subject file",
              judge_row(dict(bad, guard="!= me"), head)[0] == "NEEDS-EXCLUDE")

    failed = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print("%s %s" % ("PASS" if ok else "FAIL", name))
    print("selftest %d/%d %s" % (len(checks) - len(failed), len(checks),
                                 "PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="census all scanning probes")
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