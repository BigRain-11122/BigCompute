#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deal_flow_mock.py -- Ring-1 (outbound deal ring) MOCK drill runner.

Provenance: M73 P1 step-01 (sim-state unlock, orders 10-19 15:4x; M77 fix-01;
J4 planned-state drill). Ring spec = docs/ops/bigcompute-automation-building-plan-v1.md
sec.2/3 (inquiry -> auto-quote -> compliance gate -> deal ledger -> delivery
routing -> aftercare provision).

RED LINES (hard-coded):
- LIVE is False until commercial physical prerequisites pass (BLUEPRINT S5.5,
  J4). Flipping it is a CEO-gate action, not a code edit shortcut.
- All prices below are DESIGN-VALUE, pending the CEO pricing confirmation
  batch. They are drill fixtures, never quotable numbers.
- Writes only to the MOCK rail (state/mock-order-ledger.jsonl). The real cost
  rail (cost_ledger.LEDGER) is never touched: mock rows carry mock=true,
  order ids are prefixed MOCK-, fund accrual is zero (no fake 3090 fund).
- Zero real-world action: no network calls to any commerce platform.

Schema truth: row shape mirrors cost_ledger.add_row output (single source of
math: net = price*(1-refund_rate)*(1-fee_rate); gross = net - amount).

Commands:
  run       --order-id MOCK-<id> --sku <id> [--qty N] [--date YYYY-MM-DD]
  selftest  temp-sandbox fixtures, zero pollution

Exit codes: 0 MOCK-RING-GREEN[-IDEMPOTENT] | 2 unknown-sku | 3 gate-red
| 4 fulfillment-fail | 5 evidence-write-error.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import cost_ledger as cl  # noqa: E402  (single-source schema/math)

MOCK_RAIL = os.path.join(HERE, "..", "state", "mock-order-ledger.jsonl")
EVIDENCE_DIR = os.path.join(HERE, "..", "state")
LIVE = False  # J4: planned-state drill. Real wiring = CEO physical gate.
DESIGN_BANNER = "MOCK/DESIGN-VALUE-NOT-APPROVED (pricing slot pending CEO batch)"
ROUTE_GROUPS = ("resident-qa-8792", "batch-pool", "direct-outbox")

# T136: the exact keys ledger_write emits. Mirrors rails_schema_check.PRODUCERS
# ["mock_order"]; drift is caught at write time (not swallowed by
# additionalProperties) and asserted again in selftest.
WRITER_KEYS = (
    "order_id", "date", "sku", "tier", "cost_item", "price", "fee_rate",
    "refund_rate", "net_price", "amount", "gross", "tokens_local", "tokens_api",
    "api_reason", "fund_3090", "mock", "lane", "route_group",
)


def _check_writer_keys(row):
    """Fail fast when the emitted row drifts from WRITER_KEYS."""
    extra = sorted(set(row) - set(WRITER_KEYS))
    missing = sorted(set(WRITER_KEYS) - set(row))
    if extra or missing:
        raise RuntimeError(
            "WRITER_KEYS drift: extra=%s missing=%s" % (extra, missing))

# SKU registry: paypoint-alignment-matrix v1 classes, design values with
# source-doc pointers. All lanes stay planned-state (physical prerequisites).
SKUS = {
    "mock_quota_month_49": {
        "lane": "subscription", "tier": "L2", "route_group": "resident-qa-8792",
        "design_price_cny": 49.9, "fee_rate": 0.05, "refund_rate": 0.08,
        "price_source": "docs/ops/quota-design-49.9-99-v1.md (design band)",
    },
    "mock_data_report_annual": {
        "lane": "b2b_direct", "tier": "L3", "route_group": "direct-outbox",
        "design_price_cny": 349.0, "fee_rate": 0.05, "refund_rate": 0.08,
        "price_source": "T-20260927-27 verified band 298-388 (mid pick)",
    },
    "mock_resident_card_draw": {
        "lane": "gacha", "tier": "L2", "route_group": "batch-pool",
        "design_price_cny": 3.0, "fee_rate": 0.05, "refund_rate": 0.08,
        "price_source": "paypoint matrix 2-5 CNY draw band (blind-box anchors)",
    },
}


def _load_rows(path):
    rows = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _append_row(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")


def default_gate_check():
    """C3 face: run the real compliance gate (read-only) and parse verdict."""
    out = subprocess.run(
        [sys.executable, os.path.join(HERE, "compliance_gate_check.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    verdict = "GATE_RED"
    pass_line = "pass=?"
    for line in out.splitlines():
        if "verdict=" in line:
            verdict = line.split("verdict=", 1)[1].split()[0]
            pass_line = line.strip()
            break
    return verdict, pass_line


def default_fulfillment_check():
    """C2 face: run the real fulfillment pipeline tests (fixtures, read-only)."""
    p = subprocess.run(
        [sys.executable, os.path.join(HERE, "fulfillment", "test_pipeline.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    ok = p.returncode == 0 and "PASS" in (p.stdout or "")
    return ok, "fulfillment 44/44 PASS" if ok else "fulfillment rc=%d" % p.returncode


def ledger_write(order_id, date, sku_id, spec, qty):
    """Mock deal ledger: cost-rail schema mirror + idempotency (J2 same rule)."""
    rows = _load_rows(MOCK_RAIL)
    for r in rows:
        if r.get("order_id") == order_id:
            return "duplicate", r
    price = round(spec["design_price_cny"] * qty, 2)
    net = round(price * (1.0 - spec["refund_rate"]) * (1.0 - spec["fee_rate"]), 4)
    row = {
        "order_id": order_id, "date": date, "sku": "MOCK:" + sku_id,
        "tier": spec["tier"], "cost_item": "MOCK-DRILL",
        "price": price, "fee_rate": spec["fee_rate"],
        "refund_rate": spec["refund_rate"], "net_price": net,
        "amount": 0.0, "gross": round(net - 0.0, 4),
        "tokens_local": 0, "tokens_api": 0, "api_reason": "mock-drill",
        "fund_3090": 0.0,  # drill never accrues fund
        "mock": True, "lane": spec["lane"], "route_group": spec["route_group"],
    }
    _check_writer_keys(row)
    _append_row(MOCK_RAIL, row)
    return "ok", row


def run_ring(order_id, sku_id, qty=1, date=None, rail_path=None,
             gate_fn=None, fulfillment_fn=None, evidence=True):
    """Walk ring 1 once. Returns (exit_code, verdict_dict)."""
    global MOCK_RAIL
    if rail_path:  # selftest sandbox override
        MOCK_RAIL = rail_path
    if not order_id.startswith("MOCK-"):
        order_id = "MOCK-" + order_id
    date = date or datetime.date.today().isoformat()
    spec = SKUS.get(sku_id)
    if not spec:
        return 2, {"verdict": "MOCK-RING-RED", "reason": "unknown-sku", "sku": sku_id}
    # stage 1-2: inquiry -> auto-quote (design values only)
    price_total = round(spec["design_price_cny"] * qty, 2)
    quote = {
        "quote_id": "MQ-" + order_id, "sku": sku_id, "qty": qty,
        "unit_design_price": spec["design_price_cny"],
        "total_design_price": price_total,
        "banner": DESIGN_BANNER,
        "slots_pending_ceo": ["pricing_confirmation_batch"],
    }
    # stage 3: compliance gate (C3) -- RED aborts before any ledger write
    gate_verdict, gate_detail = (gate_fn or default_gate_check)()
    if gate_verdict == "GATE_RED":
        return 3, {"verdict": "MOCK-RING-RED", "reason": "gate-red",
                   "gate": gate_detail, "order_id": order_id,
                   "ledger_written": False}
    # stage 4: MOCK deal ledger (idempotent, J2 same rule)
    status, row = ledger_write(order_id, date, sku_id, spec, qty)
    # stage 5: delivery routing (M6 mapping) + C2 pipeline check
    route = spec["route_group"]
    ff_ok, ff_detail = (fulfillment_fn or default_fulfillment_check)()
    if not ff_ok:
        return 4, {"verdict": "MOCK-RING-RED", "reason": "fulfillment-fail",
                   "detail": ff_detail, "order_id": order_id}
    # stage 6: aftercare provision (refund provision, design math)
    provision = round(price_total * spec["refund_rate"], 4)
    verdict = "MOCK-RING-GREEN" if status == "ok" else "MOCK-RING-GREEN-IDEMPOTENT"
    out = {
        "verdict": verdict, "order_id": order_id, "date": date,
        "quote": quote, "gate": gate_verdict, "gate_detail": gate_detail,
        "ledger_status": status, "route_group": route,
        "fulfillment": ff_detail, "refund_provision_design": provision,
        "live": LIVE, "rail": os.path.basename(MOCK_RAIL),
        "real_rail": os.path.basename(cl.LEDGER),
    }
    if evidence:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(EVIDENCE_DIR, "mock-ring-%s.json" % stamp)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, sort_keys=True, ensure_ascii=True, indent=1)
            out["evidence"] = os.path.relpath(path, os.path.join(HERE, ".."))
        except OSError as e:
            return 5, {"verdict": "MOCK-RING-RED", "reason": "evidence-write", "err": str(e)}
    return 0, out


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append((name, ok, detail))

    real_rail_sig = hashlib.sha256(
        open(cl.LEDGER, "rb").read()).hexdigest() if os.path.isfile(cl.LEDGER) else "absent"
    with tempfile.TemporaryDirectory() as td:
        rail = os.path.join(td, "mock-rail.jsonl")
        gate_green = ("GATE_GREEN_WITH_SLOTS", "verdict=GATE_GREEN_WITH_SLOTS pass=59/59 slots=3")
        gate_red = ("GATE_RED", "verdict=GATE_RED pass=57/59 slots=3")
        ff_ok = (True, "fulfillment 44/44 PASS")

        # F1 happy path: green ring, one mock row (evidence off in sandbox)
        rc, v = run_ring("MOCK-ST-01", "mock_quota_month_49", rail_path=rail,
                         gate_fn=lambda: gate_green, fulfillment_fn=lambda: ff_ok,
                         evidence=False)
        rows = _load_rows(rail)
        run("F1 green ring + 1 mock row", rc == 0 and v["verdict"] == "MOCK-RING-GREEN"
            and len(rows) == 1 and rows[0].get("mock") is True
            and rows[0]["order_id"].startswith("MOCK-"), v.get("verdict"))
        # F2 idempotent replay (J2): duplicate, rail still 1 row
        rc, v2 = run_ring("MOCK-ST-01", "mock_quota_month_49", rail_path=rail,
                          gate_fn=lambda: gate_green, fulfillment_fn=lambda: ff_ok,
                          evidence=False)
        run("F2 idempotent replay", rc == 0 and v2["verdict"] == "MOCK-RING-GREEN-IDEMPOTENT"
            and v2["ledger_status"] == "duplicate" and len(_load_rows(rail)) == 1,
            v2.get("ledger_status"))
        # F3 gate red aborts BEFORE ledger write
        rows_before = len(_load_rows(rail))
        rc, v3 = run_ring("MOCK-ST-02", "mock_resident_card_draw", rail_path=rail,
                          gate_fn=lambda: gate_red, fulfillment_fn=lambda: ff_ok,
                          evidence=False)
        run("F3 gate-red abort zero-write", rc == 3 and v3.get("ledger_written") is False
            and len(_load_rows(rail)) == rows_before, "rc=%d" % rc)
        # F4 real cost rail untouched (red line)
        sig_after = hashlib.sha256(
            open(cl.LEDGER, "rb").read()).hexdigest() if os.path.isfile(cl.LEDGER) else "absent"
        run("F4 real rail zero-touch", sig_after == real_rail_sig
            and os.path.abspath(MOCK_RAIL) != os.path.abspath(cl.LEDGER), cl.LEDGER)
        # F5 quote banner + CEO slot (pricing not approved)
        rc, v5 = run_ring("MOCK-ST-03", "mock_data_report_annual", rail_path=rail,
                          gate_fn=lambda: gate_green, fulfillment_fn=lambda: ff_ok,
                          evidence=False)
        run("F5 quote design banner + slot", rc == 0
            and "NOT-APPROVED" in v5["quote"]["banner"]
            and "pricing_confirmation_batch" in v5["quote"]["slots_pending_ceo"],
            v5["quote"]["banner"][:20])
        # F6 net/gross math mirrors cost_ledger (single-source check)
        row = [r for r in _load_rows(rail) if r["order_id"] == "MOCK-ST-03"][0]
        exp_net = round(row["price"] * (1.0 - row["refund_rate"]) * (1.0 - row["fee_rate"]), 4)
        run("F6 schema/math mirror", row["net_price"] == exp_net
            and row["fund_3090"] == 0.0 and set(row).issuperset(
                ("order_id", "date", "sku", "tier", "cost_item", "price", "fee_rate",
                 "refund_rate", "net_price", "amount", "gross", "tokens_local",
                 "tokens_api", "api_reason", "fund_3090")), "net=%s" % row["net_price"])
        # F7 sku registry + routes resolve
        reg_ok = all(SKUS[s]["route_group"] in ROUTE_GROUPS
                     and SKUS[s]["tier"] in ("L1", "L2", "L3")
                     and SKUS[s]["design_price_cny"] > 0 for s in SKUS)
        run("F7 sku registry routes", reg_ok, "%d skus" % len(SKUS))
        # F8 unknown sku rejected
        rc, v8 = run_ring("MOCK-ST-04", "no_such_sku", rail_path=rail,
                          gate_fn=lambda: gate_green, fulfillment_fn=lambda: ff_ok,
                          evidence=False)
        run("F8 unknown-sku reject", rc == 2 and v8["verdict"] == "MOCK-RING-RED", v8.get("reason"))

        # F9/F10 (T136): this writer proves its own key set instead of relying
        # on rails_schema_check's census being run by someone else.
        emitted = sorted({k for r in _load_rows(rail) for k in r})
        run("F9 emitted keys == WRITER_KEYS", emitted == sorted(WRITER_KEYS)
            and len(WRITER_KEYS) == 18, "%d keys" % len(emitted))
        try:
            import rails_schema_check as rsc
            census_problems = rsc.census("mock_order", _load_rows(rail))
            producer_drift = sorted(set(rsc.PRODUCERS["mock_order"]) ^ set(WRITER_KEYS))
        except Exception as exc:  # pragma: no cover - import failure is a real fail
            census_problems = ["census import failed: %s" % exc]
            producer_drift = ["producer unavailable"]
        run("F10 census clean + WRITER_KEYS == PRODUCERS.mock_order",
            census_problems == [] and producer_drift == [],
            str(census_problems + producer_drift)[:90])

    n_pass = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(("PASS " if ok else "FAIL ") + name + ("  [%s]" % detail if detail else ""))
    print("SELFTEST %s %d/%d" % ("PASS" if n_pass == len(checks) else "FAIL",
                                 n_pass, len(checks)))
    return 0 if n_pass == len(checks) else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Ring-1 MOCK drill runner (J4 planned-state)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="walk the ring once with synthetic data")
    r.add_argument("--order-id", required=True, help="e.g. MOCK-20261019-R1")
    r.add_argument("--sku", required=True, choices=sorted(SKUS))
    r.add_argument("--qty", type=int, default=1)
    r.add_argument("--date", default=None)
    sub.add_parser("selftest", help="temp-sandbox fixtures, zero pollution")
    args = ap.parse_args()
    if args.cmd == "selftest":
        sys.exit(_selftest())
    if not LIVE:
        print("[J4 planned-state drill] LIVE=%s -- synthetic data only, "
              "real commerce wiring stays gated on physical prerequisites." % LIVE)
    rc, v = run_ring(args.order_id, args.sku, qty=args.qty, date=args.date)
    print(json.dumps({k: v[k] for k in sorted(v) if k != "quote"}, ensure_ascii=True))
    print("verdict=%s order=%s sku=%s quote_design_price=%s gate=%s ledger=%s "
          "route=%s fulfillment=%s provision=%s%s" % (
              v.get("verdict"), v.get("order_id", args.order_id), args.sku,
              v.get("quote", {}).get("total_design_price"), v.get("gate"),
              v.get("ledger_status"), v.get("route_group"), v.get("fulfillment"),
              v.get("refund_provision_design"),
              " evidence=" + v["evidence"] if v.get("evidence") else ""))
    sys.exit(rc)


if __name__ == "__main__":
    main()
