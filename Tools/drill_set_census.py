#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Drill planted-set census.

Every drill in this repo proves itself the same way: a planted set that is
known to contain defects must be named exactly by the detector.  That proof
silently dies if the planted set is edited down to "all good" cases: the
drill still prints OK / zero deviation, and nobody notices.

This tool reads each drill's *actual* planted set (import when the set is
declarative, execute into a temp dir when it is procedural, parse the AST
when it is local to a function) and compares it with a pinned manifest.
No drill is modified and no drill's pass line is touched.

Verdicts
  DRILL-SET-CLEAN       every drill carries at least the pinned case count
                        and the pinned defect count
  DRILL-SET-SHRUNK      a planted set got smaller than the pinned minimum
  DRILL-SET-DEGRADED    a planted set lost its defect-bearing cases
  DRILL-SET-UNREADABLE  the set could not be read (import/AST/exec failure)

Exit: 0 clean, 2 shrunk/degraded, 3 unreadable.

Only the CSV/JSON evidence files are produced explicitly as UTF-8; we never
print Chinese through the console codepage.
"""

import argparse
import ast
import importlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CLEAN = "DRILL-SET-CLEAN"
SHRUNK = "DRILL-SET-SHRUNK"
DEGRADED = "DRILL-SET-DEGRADED"
UNREADABLE = "DRILL-SET-UNREADABLE"
NA = "N/A-NO-PLANTED-SET"

# Pinned manifest: measured 2026-11-04 (round T-20261104-85).  Bumping a
# number here is a deliberate act with an evidence pointer, not a reflex.
MANIFEST = {
    "renewal_reminder_drill": {"mode": "expected_map",
                               "min_cases": 10, "min_defects": 5},
    "first_order_consent_drill": {"mode": "expected_map",
                                  "min_cases": 11, "min_defects": 8},
    "refund_rate_calibration": {"mode": "refund_set",
                                "min_cases": 5, "min_defects": 2},
    "gacha_cap_gate": {"mode": "exec_verdicts",
                       "min_cases": 16, "min_defects": 3},
    "gacha_probability_audit": {"mode": "ast_cases",
                                "min_cases": 6, "min_defects": 4},
    "unit_cost_drill": {"mode": "declared_na",
                        "reason": "data-face drill; asserts on real/mock rails "
                                  "and the absence of fabricated denominators, "
                                  "not on a planted defect set"},
}


def judge(actual, spec):
    """Pure comparison.  `actual` is None when the set is unreadable.

    actual = {"cases": int, "defects": int, "unexpected": [id..]}
    """
    if spec.get("mode") == "declared_na":
        return NA
    if actual is None:
        return UNREADABLE
    if actual.get("cases", -1) < spec["min_cases"]:
        return SHRUNK
    if actual.get("defects", -1) < spec["min_defects"]:
        return DEGRADED
    if actual.get("unexpected"):
        return DEGRADED
    return CLEAN


def _expected_map(module_name, builder, id_key):
    mod = importlib.import_module(module_name)
    rows = getattr(mod, builder)()
    expected = getattr(mod, "EXPECTED")
    red = set(getattr(mod, "RED"))
    ids = [row[id_key] for row in rows]
    defects = sum(1 for value in expected.values() if value in red)
    return {
        "cases": len(ids),
        "asserted": len(expected),
        "defects": defects,
        "unexpected": sorted(set(ids) - set(expected)),
        "no_expectation": sorted(set(expected) - set(ids)),
    }


def _refund_set(module_name):
    mod = importlib.import_module(module_name)
    _rev, _aft, expected = mod._drill_set()
    ok_value = getattr(mod, "CAL_OK")
    return {
        "cases": len(expected),
        "asserted": len(expected),
        "defects": sum(1 for value in expected.values() if value != ok_value),
    }


def _ast_cases(module_name, func_name):
    mod = importlib.import_module(module_name)
    path = os.path.join(HERE, module_name + ".py")
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            func = node
            break
    if func is None:
        return None
    cases = []
    for node in ast.walk(func):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"):
            cases.append(node.args[0] if node.args else None)
    defects = 0
    for case in cases:
        if not isinstance(case, ast.Tuple) or len(case.elts) < 3:
            continue
        verdict = case.elts[2]
        name = getattr(verdict, "attr", None) or getattr(verdict, "id", None)
        if name is None:
            continue
        if name != "V_OK":
            defects += 1
    return {"cases": len(cases), "asserted": len(cases), "defects": defects}


def _exec_verdicts(module_name):
    """Run a procedural drill into a temp dir and read its own payload."""
    mod = importlib.import_module(module_name)
    with tempfile.TemporaryDirectory(prefix="drill-census-") as tmp:
        ledger = os.path.join(tmp, "ledger.jsonl")
        out_json = os.path.join(tmp, "payload.json")
        out_txt = os.path.join(tmp, "payload.txt")
        payload = mod.run_drill(ledger, out_json, out_txt)
        if not isinstance(payload, dict):
            with open(out_json, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
    results = payload.get("results") or []
    verdicts = [row.get("verdict") for row in results]
    distinct = sorted({v for v in verdicts if v})
    allow = set(mod.CHARGED)
    defects = sum(1 for v in verdicts if v not in allow)
    return {"cases": len(verdicts), "asserted": len(verdicts),
            "defects": defects, "distinct_verdicts": distinct}


def scan(bump_defects=0):
    manifest = dict(MANIFEST)
    if bump_defects:
        manifest = {name: dict(spec) for name, spec in MANIFEST.items()}
        for spec in manifest.values():
            if "min_defects" in spec:
                spec["min_defects"] += bump_defects
    rows = []
    for name, spec in sorted(manifest.items()):
        mode = spec["mode"]
        try:
            if mode == "expected_map":
                if name == "renewal_reminder_drill":
                    actual = _expected_map(name, "synthetic_subs", "sub_id")
                else:
                    actual = _expected_map(name, "synthetic_orders", "order_id")
            elif mode == "refund_set":
                actual = _refund_set(name)
            elif mode == "ast_cases":
                actual = _ast_cases(name, "drill")
            elif mode == "exec_verdicts":
                actual = _exec_verdicts(name)
            else:  # declared_na
                actual = None
        except Exception as exc:  # noqa: BLE001 - census must report, not crash
            rows.append({"drill": name, "mode": mode, "verdict": UNREADABLE,
                         "error": "%s: %s" % (type(exc).__name__, exc),
                         "actual": None})
            continue
        verdict = judge(actual, spec)
        if mode == "declared_na":
            verdict = NA
        rows.append({"drill": name, "mode": mode, "verdict": verdict,
                     "min_cases": spec.get("min_cases"),
                     "min_defects": spec.get("min_defects"),
                     "actual": actual})
    return rows


def overall(rows):
    verdicts = [row["verdict"] for row in rows]
    if UNREADABLE in verdicts:
        return UNREADABLE
    if SHRUNK in verdicts:
        return SHRUNK
    if DEGRADED in verdicts:
        return DEGRADED
    return CLEAN


def render(rows):
    lines = ["drill planted-set census",
             "domain=Tools/*drill*.py + gacha_*/Tools; graded=declared sets",
             "manifest pinned=2026-11-04"]
    for row in rows:
        actual = row.get("actual") or {}
        lines.append(
            "%-26s %-22s cases=%s/%s defects=%s/%s%s"
            % (row["drill"], row["verdict"],
               actual.get("cases", "?"), row.get("min_cases", "-"),
               actual.get("defects", "?"), row.get("min_defects", "-"),
               (" err=%s" % row["error"]) if row.get("error") else ""))
        if actual.get("unexpected"):
            lines.append("    planted-but-not-asserted: %s"
                         % ", ".join(actual["unexpected"]))
        if actual.get("no_expectation"):
            lines.append("    asserted-but-not-planted: %s"
                         % ", ".join(actual["no_expectation"]))
    lines.append("VERDICT=%s drills=%d" % (overall(rows), len(rows)))
    return "\n".join(lines)


def _write(path, text):
    if not path:
        return
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")


def _selftest():
    checks = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    spec = {"mode": "expected_map", "min_cases": 4, "min_defects": 2}
    chk("S1 exact set is clean",
        judge({"cases": 4, "defects": 2, "unexpected": []}, spec) == CLEAN)
    chk("S2 shrunk set is named",
        judge({"cases": 3, "defects": 2, "unexpected": []}, spec) == SHRUNK)
    chk("S3 defect-free set is degraded even when count matches",
        judge({"cases": 4, "defects": 0, "unexpected": []}, spec) == DEGRADED)
    chk("S4 one defect short is degraded",
        judge({"cases": 4, "defects": 1, "unexpected": []}, spec) == DEGRADED)
    chk("S5 planted case without expectation is degraded",
        judge({"cases": 4, "defects": 2, "unexpected": ["X"]}, spec) == DEGRADED)
    chk("S6 unreadable is not clean",
        judge(None, spec) == UNREADABLE)
    chk("S7 declared N/A is explicit and not counted clean",
        judge(None, {"mode": "declared_na"}) == NA)
    chk("S8 overall escalates unreadable over shrunk",
        overall([{"verdict": SHRUNK}, {"verdict": UNREADABLE}]) == UNREADABLE)
    chk("S9 overall names shrunk when no unreadable",
        overall([{"verdict": CLEAN}, {"verdict": SHRUNK}]) == SHRUNK)
    chk("S10 overall clean needs every drill clean or N/A",
        overall([{"verdict": CLEAN}, {"verdict": NA}]) == CLEAN)
    chk("S11 render always prints verdict line",
        "VERDICT=" in render([{"drill": "x", "verdict": NA, "actual": None}]))
    chk("S12 manifest itself is non-empty and every entry typed",
        bool(MANIFEST) and all("mode" in s for s in MANIFEST.values()))

    failures = 0
    for name, ok, detail in checks:
        failures += 0 if ok else 1
        print("%-4s %s%s" % ("PASS" if ok else "FAIL", name,
                             ("" if ok else ("  " + detail))))
    print("selftest: %d/%d %s" % (len(checks) - failures, len(checks),
                                  "OK" if failures == 0 else "FAILED"))
    return 0 if failures == 0 else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json-out")
    ap.add_argument("--txt-out")
    ap.add_argument("--bump-defects", type=int, default=0,
                    help="negative control: raise every min_defects by N; "
                         "the run must stop being CLEAN")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    rows = scan(args.bump_defects)
    text = render(rows)
    print(text)
    _write(args.txt_out, text)
    _write(args.json_out, json.dumps(
        {"verdict": overall(rows), "rows": rows}, ensure_ascii=False, indent=2))
    return {"DRILL-SET-CLEAN": 0, "DRILL-SET-SHRUNK": 2,
            "DRILL-SET-DEGRADED": 2, "DRILL-SET-UNREADABLE": 3}[overall(rows)]


if __name__ == "__main__":
    sys.exit(main())