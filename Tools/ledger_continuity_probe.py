#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ledger_continuity_probe.py -- M73 step2 (building-plan gap 3): ledger
zero-break probe for the P1 deal loop (J2 acceptance face).

Continuity = the deal books (5 rails of cost_ledger.py) can always be
reconciled: parseable rows, unique keys (idempotency not bypassed),
date/month coherence, non-negative amounts, quota rails replay without
a negative running balance, caliber-B only, pending-bill coherence, and
attribution fields present where structurally required (cloud/l1).

Zero new schema: imports cost_ledger (single source of truth) for rail
paths, budget cap file and helpers. Verdicts:
  LEDGER-CONTINUITY-GREEN  exit 0  -- 0 violations
  LEDGER-CONTINUITY-RED    exit 1  -- N violations listed
  PLANNED-STATE            exit 0  -- all rails empty (J4 drill state)

Commands: check [--ledger-dir DIR] [--out FILE] | selftest
Evidence JSON written to state/ledger-continuity-<ts>.json (check).
[via BigCompute-OSLoop]
"""
import datetime
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import cost_ledger as cl  # noqa: E402  (single-source rails/schema)

RAILS = {
    "cost":   {"path": cl.LEDGER,   "key": "order_id",
               "required": ("order_id", "date", "sku", "tier", "cost_item",
                            "price", "fee_rate", "refund_rate", "net_price",
                            "amount", "gross", "tokens_local", "tokens_api",
                            "api_reason", "fund_3090")},
    "quota":  {"path": cl.QLEDGER,  "key": "tx_id",
               "required": ("tx_id", "date", "month", "type", "consumer",
                            "amount_mtok", "settlement_caliber"),
               "types": ("issue", "consume")},
    "cloud":  {"path": cl.CLEDGER,  "key": "tx_id",
               "required": ("tx_id", "date", "month", "type", "consumer",
                            "lane", "task_ref", "amount_mtok",
                            "receipt_status", "settlement_caliber")},
    "borrow": {"path": cl.BLEDGER,  "key": "tx_id",
               "required": ("tx_id", "date", "month", "type", "lender_dept",
                            "borrower_dept", "machine_id", "tokens_mtok",
                            "quota_debit_mtok", "receipt_status",
                            "settlement_caliber")},
    "l1":     {"path": cl.L1LEDGER, "key": "tx_id",
               "required": ("tx_id", "date", "month", "type", "consumer",
                            "endpoint", "model", "prompt_tokens",
                            "completion_tokens", "amount_mtok",
                            "receipt_status", "settlement_caliber")},
}
AMOUNT_FIELDS = ("amount_mtok", "tokens_mtok", "amount", "price",
                 "prompt_tokens", "completion_tokens", "fund_3090")
PENDING_STATUSES = ("pending_bill", "pending_settlement", "pending_conversion")


def _load(path):
    rows = []
    if not os.path.exists(path):
        return rows, []
    errs = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if isinstance(r, dict):
                    rows.append(r)
                else:
                    errs.append("parse:non-dict L%d" % i)
            except ValueError as e:
                errs.append("parse:bad-json L%d %s" % (i, e))
    return rows, errs


def _check_rail(name, spec, path, v):
    rows, errs = _load(path)
    for e in errs:
        v.append({"rail": name, "check": "parse", "detail": e})
    seen = {}
    replay = {}
    budget = None
    if name == "quota":
        try:
            budget = cl._load_budget(cl.QBUDGET)
        except Exception:
            budget = None
    for r in rows:
        rid = str(r.get(spec["key"], ""))
        if rid in seen:
            v.append({"rail": name, "check": "key-unique",
                      "detail": "%s=%s duplicated (idempotency bypass)" %
                                (spec["key"], rid)})
        seen[rid] = True
        for f in spec["required"]:
            if f not in r or r.get(f) in (None, ""):
                v.append({"rail": name, "check": "schema",
                          "detail": "%s=%s missing field %s" %
                                    (spec["key"], rid or "?", f)})
        d = str(r.get("date", ""))
        if d and (len(d) != 10 or d[4] != "-" or d[7] != "-"):
            v.append({"rail": name, "check": "date-format", "detail": d})
        elif d and "month" in r and r.get("month") != d[:7]:
            v.append({"rail": name, "check": "month-coherence",
                      "detail": "date=%s month=%s" % (d, r.get("month"))})
        for f in AMOUNT_FIELDS:
            if f in r and isinstance(r[f], (int, float)) and r[f] < 0:
                v.append({"rail": name, "check": "non-negative",
                          "detail": "%s=%s %s=%s" % (spec["key"], rid, f, r[f])})
        if r.get("settlement_caliber") not in (None, "B"):
            v.append({"rail": name, "check": "caliber-B",
                      "detail": "%s=%s caliber=%s" %
                                (spec["key"], rid, r.get("settlement_caliber"))})
        if name == "quota":
            if r.get("type") not in spec["types"]:
                v.append({"rail": name, "check": "known-type",
                          "detail": "%s=%s type=%s" %
                                    (spec["key"], rid, r.get("type"))})
                continue
            c = str(r.get("consumer", "?"))
            bal = replay.setdefault(c, 0.0)
            amt = r.get("amount_mtok", 0.0) or 0.0
            if r.get("type") == "issue":
                cap = (budget or {}).get(r.get("month")) if budget else None
                if cap is not None:
                    mtd = sum(x.get("amount_mtok", 0.0) for x in rows
                              if x.get("type") == "issue"
                              and x.get("month") == r.get("month")
                              and str(x.get("date", "")) <= d)
                    if mtd > cap and not r.get("ceo_approved"):
                        v.append({"rail": name, "check": "budget-gate",
                                  "detail": "%s month=%s mtd=%s>cap=%s "
                                            "no ceo_approved" %
                                            (rid, r.get("month"), mtd, cap)})
                bal += amt
            else:
                bal -= amt
                if bal < -1e-9:
                    v.append({"rail": name, "check": "replay-balance",
                              "detail": "%s consumer=%s running=%s after "
                                        "consume %s" % (rid, c, bal, amt)})
            replay[c] = bal
        if name in ("cloud", "borrow", "l1"):
            st = r.get("receipt_status")
            costed = isinstance(r.get("cost_b_cny"), (int, float))
            if st in PENDING_STATUSES and costed:
                v.append({"rail": name, "check": "pending-coherence",
                          "detail": "%s status=%s but cost_b_cny set" %
                                    (rid, st)})
    return len(rows)


def run_check(ledger_dir=None, out_path=None):
    v = []
    counts = {}
    total = 0
    for name, spec in RAILS.items():
        path = spec["path"]
        if ledger_dir:
            path = os.path.join(ledger_dir, os.path.basename(path))
        counts[name] = _check_rail(name, spec, path, v)
        total += counts[name]
    if total == 0:
        verdict = "PLANNED-STATE"
    elif v:
        verdict = "LEDGER-CONTINUITY-RED"
    else:
        verdict = "LEDGER-CONTINUITY-GREEN"
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    result = {"ts": ts, "verdict": verdict, "violations": v,
              "violation_count": len(v), "rail_rows": counts}
    if out_path is None:
        out_path = os.path.join(HERE, "..", "state",
                                "ledger-continuity-%s.json" % ts)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result, out_path


def _wf(tmp, rows):
    with open(tmp, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _selftest():
    ok = [0]

    def t(name, cond):
        ok[0] += 1 if cond else 0
        print("  %-34s %s" % (name, "PASS" if cond else "FAIL"))
        return cond

    base = {"tx_id": "T", "date": "2026-10-18", "month": "2026-10",
            "type": "issue", "consumer": "qa", "amount_mtok": 5.0,
            "settlement_caliber": "B"}
    # f1 clean quota rail -> GREEN
    d = tempfile.mkdtemp(prefix="bc_lcp_")
    _wf(os.path.join(d, "quota-ledger.jsonl"),
        [dict(base, tx_id="A1"), dict(base, tx_id="A2", type="consume",
                                     amount_mtok=2.0)])
    r, _ = run_check(ledger_dir=d, out_path=os.path.join(d, "ev.json"))
    t("clean-rails->GREEN", r["verdict"] == "LEDGER-CONTINUITY-GREEN")
    # f2 duplicate tx_id -> RED key-unique
    d = tempfile.mkdtemp(prefix="bc_lcp_")
    _wf(os.path.join(d, "quota-ledger.jsonl"),
        [dict(base, tx_id="A1"), dict(base, tx_id="A1")])
    r, _ = run_check(ledger_dir=d, out_path=os.path.join(d, "ev.json"))
    t("dup-tx_id->RED", r["verdict"] == "LEDGER-CONTINUITY-RED" and
      any(x["check"] == "key-unique" for x in r["violations"]))
    # f3 consume without issue -> RED replay-balance
    d = tempfile.mkdtemp(prefix="bc_lcp_")
    _wf(os.path.join(d, "quota-ledger.jsonl"),
        [dict(base, tx_id="B1", type="consume", amount_mtok=1.0)])
    r, _ = run_check(ledger_dir=d, out_path=os.path.join(d, "ev.json"))
    t("consume-no-issue->RED", r["verdict"] == "LEDGER-CONTINUITY-RED" and
      any(x["check"] == "replay-balance" for x in r["violations"]))
    # f4 missing field -> RED schema
    d = tempfile.mkdtemp(prefix="bc_lcp_")
    _wf(os.path.join(d, "quota-ledger.jsonl"),
        [{k: v2 for k, v2 in base.items() if k != "consumer"}])
    r, _ = run_check(ledger_dir=d, out_path=os.path.join(d, "ev.json"))
    t("missing-field->RED", any(x["check"] == "schema" for x in r["violations"]))
    # f5 caliber-A -> RED caliber-B
    d = tempfile.mkdtemp(prefix="bc_lcp_")
    _wf(os.path.join(d, "quota-ledger.jsonl"),
        [dict(base, tx_id="C1", settlement_caliber="A")])
    r, _ = run_check(ledger_dir=d, out_path=os.path.join(d, "ev.json"))
    t("caliber-A->RED", any(x["check"] == "caliber-B" for x in r["violations"]))
    # f6 cloud pending but costed -> RED pending-coherence
    d = tempfile.mkdtemp(prefix="bc_lcp_")
    _wf(os.path.join(d, "cloud-cost-ledger.jsonl"),
        [{"tx_id": "D1", "date": "2026-10-18", "month": "2026-10",
          "type": "cloud", "consumer": "qa", "lane": "l",
          "task_ref": "r", "amount_mtok": 1.0, "cost_b_cny": 0.5,
          "receipt_status": "pending_bill", "settlement_caliber": "B"}])
    r, _ = run_check(ledger_dir=d, out_path=os.path.join(d, "ev.json"))
    t("pending+costed->RED", any(x["check"] == "pending-coherence"
                                 for x in r["violations"]))
    # f7 empty dir -> PLANNED-STATE (J4 drill state)
    d = tempfile.mkdtemp(prefix="bc_lcp_")
    r, _ = run_check(ledger_dir=d, out_path=os.path.join(d, "ev.json"))
    t("empty->PLANNED-STATE", r["verdict"] == "PLANNED-STATE")
    # f8 cost rail keyed by order_id -> GREEN + dup -> RED
    d = tempfile.mkdtemp(prefix="bc_lcp_")
    order = {"order_id": "O1", "date": "2026-10-18", "sku": "s", "tier": "L1",
             "cost_item": "c", "price": 10.0, "fee_rate": 0.01,
             "refund_rate": 0.0, "net_price": 9.9, "amount": 9.9,
             "gross": 10.1, "tokens_local": 1, "tokens_api": 0,
             "api_reason": "-", "fund_3090": 0.1}
    _wf(os.path.join(d, "cost-ledger.jsonl"), [order])
    r, _ = run_check(ledger_dir=d, out_path=os.path.join(d, "ev.json"))
    t("cost-rail->GREEN", r["verdict"] == "LEDGER-CONTINUITY-GREEN")
    _wf(os.path.join(d, "cost-ledger.jsonl"), [order])
    r, _ = run_check(ledger_dir=d, out_path=os.path.join(d, "ev.json"))
    t("dup-order_id->RED", any(x["check"] == "key-unique" and
                               x["detail"].startswith("order_id")
                               for x in r["violations"]))
    print("selftest: %d/9 PASS" % ok[0])
    return 0 if ok[0] == 9 else 1


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "selftest":
        return _selftest()
    if sys.argv[1] == "check":
        d = None
        if "--ledger-dir" in sys.argv:
            d = sys.argv[sys.argv.index("--ledger-dir") + 1]
        out = None
        if "--out" in sys.argv:
            out = sys.argv[sys.argv.index("--out") + 1]
        r, p = run_check(ledger_dir=d, out_path=out)
        print("%s violations=%d rail_rows=%s" %
              (r["verdict"], r["violation_count"], r["rail_rows"]))
        print("evidence=%s" % os.path.abspath(p))
        return 0 if r["verdict"] != "LEDGER-CONTINUITY-RED" else 1
    print("usage: ledger_continuity_probe.py check|selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
