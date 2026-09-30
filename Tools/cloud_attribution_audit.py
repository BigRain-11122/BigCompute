#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cloud_attribution_audit.py - cloud-lane attribution empty-value violation
counter (tech T37; orders O-2026-0930-014 enforcement phase).

O-2026-0930-014 (CEO 2026-09-30 ~18:5x "决策委员会开始执行节省云端TOKEN
机制，尽量本地化解决") item 3 makes the attribution gate counting law:
"夜班起 attribution 空值=门禁违例计数（BigCompute M18 接线范式）". This
tool is that counter, mechanized: it scans state/cloud-cost-ledger.jsonl and
counts entries whose attribution fields (consumer / lane / task_ref = 消费方/
线/工单号 three-mandatory per mandate v2.6 三径闸, plus tx_id ledger key) are
missing, empty, or placeholder ("-" default). Data-level defense in depth on
top of the argparse structural gate in cost_ledger.py cloud-entry (task_ref
required=True per this round's alignment fix; structural layer = J1 of the
audit, this scan = J2).

Zero-state is the expected honest verdict while J4 holds (no real cloud
entries until the first paid cloud bill physical voucher): missing ledger
file => rows=0 violations=0 verdict=CLEAN-ZERO-STATE, exit 0.

Consumers: tonight 00:00 常务轮 all-fleet first report (O-014 item 4),
night-shift rounds.log bookkeeping, and any future cloud-lane review window.

NO DISPATCH / read-only on the real ledger (writes only its own evidence
JSON under state/). Exit codes: 0 CLEAN (incl. zero-state) / 1 VIOLATIONS.
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(PROJECT, "state", "cloud-cost-ledger.jsonl")
MACHINE = "bm-a"  # C-20260929-02 7.1 machine tag
COST_LEDGER_SRC = os.path.join(PROJECT, "Tools", "cost_ledger.py")
# 三径闸 three-mandatory attribution fields + tx_id ledger key
ATTR_FIELDS = ("tx_id", "consumer", "lane", "task_ref")
# placeholder sentinels that structurally mean "no attribution value"
EMPTY_MARKS = {"", "-", "none", "null", "n/a", "tbd"}


def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _is_empty(value):
    return value is None or str(value).strip().lower() in EMPTY_MARKS


def scan_rows(rows):
    """Pure classification: per-row attribution violations + reason advisory.

    reason is the 意义注记位 (O-1820 item 4 same law): a missing reason is
    counted as an advisory note gap, NOT a gate violation (tightening to
    required rides the J4 first-paid-bill wiring window per M18 notes).
    """
    violations = []
    notes_missing = 0
    for r in rows:
        rid = r.get("tx_id")
        rid_s = rid if isinstance(rid, str) and rid else "<missing-tx-id>"
        for f in ATTR_FIELDS:
            if _is_empty(r.get(f)):
                violations.append({"tx_id": rid_s, "field": f,
                                   "value": r.get(f)})
        if _is_empty(r.get("reason")):
            notes_missing += 1
    return violations, notes_missing


def audit_file(path):
    """Return (rows, violations, notes_missing, verdict).

    verdict: CLEAN-ZERO-STATE (ledger absent, J4 holds) / CLEAN / VIOLATIONS.
    """
    if not os.path.exists(path):
        return [], [], 0, "CLEAN-ZERO-STATE"
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                rows.append({"tx_id": "<undecodable-line>", "consumer": None,
                             "lane": None, "task_ref": None})
    violations, notes_missing = scan_rows(rows)
    verdict = "VIOLATIONS" if violations else ("CLEAN" if rows else "CLEAN-ZERO-STATE")
    return rows, violations, notes_missing, verdict


def cmd_audit(ledger=LEDGER):
    rows, violations, notes_missing, verdict = audit_file(ledger)
    ts = _now_iso()
    ev = {
        "ts": ts,
        "machine": MACHINE,
        "ledger": os.path.relpath(ledger, PROJECT).replace("\\", "/"),
        "rows": len(rows),
        "violation_count": len(violations),
        "violations": violations,
        "reason_notes_missing": notes_missing,
        "verdict": verdict,
        "source_orders": ["O-2026-0930-014", "C-20260929-01",
                           "P-2026-09-29-01", "M18"],
        "note": "attribution empty-value gate violation counter; read-only",
    }
    out_path = os.path.join(
        PROJECT, "state",
        "cloud-attribution-audit-%s.json"
        % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:  # utf-8 explicit (T24 law)
        json.dump(ev, f, sort_keys=True, ensure_ascii=False, indent=1)
    print("cloud-attribution-audit: machine=%s rows=%d violations=%d "
          "reason_notes_missing=%d verdict=%s (O-2026-0930-014 enforcement "
          "face; NO dispatch, read-only; evidence=%s)"
          % (MACHINE, len(rows), len(violations), notes_missing, verdict,
             os.path.basename(out_path)))
    return 0 if verdict in ("CLEAN", "CLEAN-ZERO-STATE") else 1


def cmd_selftest():
    ok = 0

    def check(name, cond):
        nonlocal ok
        ok += 1 if cond else 0
        print("%s %s" % ("PASS" if cond else "FAIL", name))
        return cond

    import tempfile

    # S1 pure row classification (三径闸 three-mandatory + tx_id key)
    clean_row = {"tx_id": "CL-0001", "consumer": "bigcompute",
                 "lane": "openrouter", "task_ref": "TASK-7", "reason": "city3d"}
    check("S1a clean row zero violations", scan_rows([clean_row]) == ([], 0))
    v, n = scan_rows([dict(clean_row, task_ref="-")])
    check("S1b task_ref placeholder is a violation",
          len(v) == 1 and v[0]["field"] == "task_ref")
    v, _ = scan_rows([dict(clean_row, consumer=None)])
    check("S1c missing consumer is a violation",
          len(v) == 1 and v[0]["field"] == "consumer")
    v, _ = scan_rows([dict(clean_row, lane="")])
    check("S1d empty lane is a violation",
          len(v) == 1 and v[0]["field"] == "lane")
    v, n = scan_rows([dict(clean_row, reason="-")])
    check("S1e missing reason is advisory note only, not a violation",
          v == [] and n == 1)
    v, _ = scan_rows([{"tx_id": "CL-2", "consumer": "x", "lane": "y"}])
    check("S1f absent task_ref key is a violation",
          len(v) == 1 and v[0]["field"] == "task_ref")

    # S2 end-to-end temp ledger scan (utf-8 jsonl read + counts)
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "cloud.jsonl")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(clean_row) + "\n")
            f.write(json.dumps(dict(clean_row, tx_id="CL-0002")) + "\n")
            f.write(json.dumps(dict(clean_row, tx_id="CL-0003",
                                    task_ref="-")) + "\n")
        rows, v, n, verdict = audit_file(tmp)
        check("S2a rows=3", len(rows) == 3)
        check("S2b exactly 1 violation located", len(v) == 1
              and v[0]["tx_id"] == "CL-0003")
        check("S2c verdict VIOLATIONS", verdict == "VIOLATIONS")
        # S5 read-only law: ledger bytes unchanged by scan
        before = hashlib.sha256(open(tmp, "rb").read()).hexdigest()
        audit_file(tmp)
        after = hashlib.sha256(open(tmp, "rb").read()).hexdigest()
        check("S5 read-only: ledger hash unchanged", before == after)
        # S4 determinism: two audits identical counts
        r1 = audit_file(tmp)
        r2 = audit_file(tmp)
        check("S4 deterministic counts", (len(r1[0]), len(r1[1]), r1[3])
              == (len(r2[0]), len(r2[1]), r2[3]))

    # S3 missing ledger => zero-state (J4) verdict + clean exit semantics
    with tempfile.TemporaryDirectory() as td:
        rows, v, n, verdict = audit_file(os.path.join(td, "absent.jsonl"))
        check("S3a missing file zero-state", (len(rows), v, n, verdict)
              == (0, [], 0, "CLEAN-ZERO-STATE"))
        check("S3b clean verdicts exit 0",
              (0 if verdict in ("CLEAN", "CLEAN-ZERO-STATE") else 1) == 0)

    # S6 structural layer in force: cost_ledger.py cloud-entry --task-ref
    # must be required (三径闸 工单号 three-mandatory alignment, this round's
    # fix) - source scan mirrors S4 style of clean_window_probe.
    src = open(COST_LEDGER_SRC, encoding="utf-8").read()
    task_line = [ln for ln in src.splitlines() if '"--task-ref"' in ln]
    check("S6a cost_ledger cloud-entry --task-ref required=True",
          len(task_line) == 1 and "required=True" in task_line[0])
    own = open(os.path.abspath(__file__), encoding="utf-8").read()
    forbidden = ("task" + "kill", "Start-" + "Process", "os." + "system")
    check("S6b no dispatch verb in own source",
          all(x not in own for x in forbidden))

    print("selftest: %s" % ("PASS" if ok == 15 else "FAIL"))
    return 0 if ok == 15 else 1


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="cloud attribution empty-value audit (O-2026-0930-014)")
    ap.add_argument("command", choices=["audit", "selftest"])
    a = ap.parse_args()
    return {"audit": cmd_audit, "selftest": cmd_selftest}[a.command]()


if __name__ == "__main__":
    sys.exit(main())
