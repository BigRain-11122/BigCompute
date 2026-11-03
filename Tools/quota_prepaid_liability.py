"""Quota prepaid liability drill (queue M110).

A quota-type SKU sells an allowance, so the money is a prepayment and whatever
the buyer did not consume is still owed back to them. Our own store-ledger
predesign (§四) wrote the refund as

    refund = balance_mtok * unit_cost_b_cny + unconsumed service pro-rata

which is a *cost* basis. Consumer prepayment rules (service-continuity-terms
anchor 2, drawn from 消费者权益保护法 第五十三条) say the unperformed part is
refunded on the *price* the buyer actually paid. Those two are not the same
number, and the cost basis is always the smaller one, so using it under-refunds
the buyer.

This drill makes that gap a number instead of an argument, and refuses to let a
cost-basis figure be presented as the user-facing refund.

Read-only over the revenue rails; nothing is booked.
"""

import argparse
import json
import os
import sys

import unit_cost_caliber as caliber

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "state")
REVENUE_RAIL = os.path.join(STATE, "cost-ledger.jsonl")
MOCK_RAIL = os.path.join(STATE, "mock-order-ledger.jsonl")

PRICE_BASIS = "PRICE-BASIS"   # consumer-facing: unused share of what was paid
COST_BASIS = "COST-BASIS"     # internal only: unused allowance at our cost
GAP_FLAG = "PREPAID-REFUND-COST-BASIS-UNDERPAYS"


def load_rows(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def drill_row(row, unused_ratio, unit_cost_b):
    """Refund comparison for one quota-type order. Pure function."""
    mtok = float(row.get("amount_mtok") or 0.0)
    net = float(row.get("net_price") or 0.0)
    price_refund = round(net * unused_ratio, 4)
    cost_refund = round(mtok * unused_ratio * unit_cost_b, 4)
    return {
        "order_id": row.get("order_id"),
        "amount_mtok": mtok,
        "net_price_cny": round(net, 4),
        "unused_ratio": unused_ratio,
        "unused_mtok": round(mtok * unused_ratio, 6),
        "price_basis_refund_cny": price_refund,
        "cost_basis_refund_cny": cost_refund,
        "gap_cny": round(price_refund - cost_refund, 4),
        "buyer_facing_basis": PRICE_BASIS,
        "internal_basis": COST_BASIS,
        "gap_flag": (GAP_FLAG if cost_refund < price_refund else None),
    }


def run(rows, unused_ratio=0.4, unit_cost_b=0.6):
    quota_rows = [r for r in rows if caliber.classify_row(r) == caliber.CLASS_QUOTA]
    details = []
    skipped = []
    for r in rows:
        cls = caliber.classify_row(r)
        if cls == caliber.CLASS_QUOTA:
            if r.get("amount_mtok") is None:
                skipped.append({"order_id": r.get("order_id"),
                                "reason": "quota row without amount_mtok"})
                continue
            details.append(drill_row(r, unused_ratio, unit_cost_b))
        else:
            skipped.append({"order_id": r.get("order_id"),
                            "reason": "not a quota-type row (%s)" % cls})
    total_price = round(sum(d["price_basis_refund_cny"] for d in details), 4)
    total_cost = round(sum(d["cost_basis_refund_cny"] for d in details), 4)
    gap = round(total_price - total_cost, 4)
    return {
        "rows_in": len(rows),
        "quota_rows": len(quota_rows),
        "drilled": len(details),
        "skipped": skipped,
        "unused_ratio": unused_ratio,
        "unit_cost_b_cny_per_mtok": unit_cost_b,
        "total_price_basis_refund_cny": total_price,
        "total_cost_basis_refund_cny": total_cost,
        "gap_cny": gap,
        "verdict": ("PREPAID-LIABILITY-GAP" if gap > 0
                    else "PREPAID-LIABILITY-CONSISTENT"),
        "rule": ("consumer refund follows the price actually paid "
                 "(service-continuity-terms anchor 2); cost basis is internal "
                 "reporting only and must never be quoted as the refund"),
        "details": details,
    }


def render(rep):
    lines = ["QUOTA PREPAID LIABILITY DRILL  verdict=%s" % rep["verdict"]]
    lines.append("rows_in=%d quota_rows=%d drilled=%d unused_ratio=%.2f "
                 "unit_cost_b=%.2f"
                 % (rep["rows_in"], rep["quota_rows"], rep["drilled"],
                    rep["unused_ratio"], rep["unit_cost_b_cny_per_mtok"]))
    for d in rep["details"]:
        lines.append("  %s mtok=%.4f unused=%.4f price_refund=%.4f "
                     "cost_refund=%.4f gap=%.4f%s"
                     % (d["order_id"], d["amount_mtok"], d["unused_mtok"],
                        d["price_basis_refund_cny"], d["cost_basis_refund_cny"],
                        d["gap_cny"], "  " + GAP_FLAG if d["gap_flag"] else ""))
    lines.append("total price_basis=%.4f cost_basis=%.4f gap=%.4f"
                 % (rep["total_price_basis_refund_cny"],
                    rep["total_cost_basis_refund_cny"], rep["gap_cny"]))
    if rep["skipped"]:
        lines.append("skipped=%d (non-quota or missing allowance)" % len(rep["skipped"]))
    lines.append("rule: " + rep["rule"])
    return "\n".join(lines)


def _selftest():
    checks = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    quota = {"order_id": "Q1", "lane": "subscription", "sku": "seat.quota",
             "amount_mtok": 1.0, "net_price": 100.0}
    gen = {"order_id": "G1", "lane": "gacha", "sku": "card", "amount_mtok": 0,
           "net_price": 50.0, "tokens_local": 10}
    d = drill_row(quota, 0.4, 0.6)
    chk("S1 price basis uses the paid price", abs(d["price_basis_refund_cny"] - 40.0) < 1e-9)
    chk("S2 cost basis uses allowance times unit cost",
        abs(d["cost_basis_refund_cny"] - 0.24) < 1e-9, str(d["cost_basis_refund_cny"]))
    chk("S3 gap is price minus cost and flagged",
        abs(d["gap_cny"] - 39.76) < 1e-9 and d["gap_flag"] == GAP_FLAG)

    rep = run([quota, gen], 0.4, 0.6)
    chk("S4 generation rows are skipped, not refunded",
        rep["drilled"] == 1 and len(rep["skipped"]) == 1, str(rep["skipped"]))
    chk("S5 total gap surfaces the underpay risk",
        rep["verdict"] == "PREPAID-LIABILITY-GAP" and rep["gap_cny"] > 0)

    no_amt = {"order_id": "Q2", "lane": "subscription", "sku": "seat.quota",
              "net_price": 10.0}
    rep2 = run([no_amt], 0.4, 0.6)
    chk("S6 quota row without allowance is skipped with a reason",
        rep2["drilled"] == 0 and "amount_mtok" in rep2["skipped"][0]["reason"])

    rep_zero = run([quota], 0.0, 0.6)
    chk("S7 zero unused ratio means nothing is owed",
        rep_zero["gap_cny"] == 0.0
        and rep_zero["verdict"] == "PREPAID-LIABILITY-CONSISTENT")

    chk("S8 determinism", run([quota], 0.4, 0.6) == run([quota], 0.4, 0.6))
    text = render(rep)
    chk("S9 render states the rule and the verdict",
        "PREPAID-LIABILITY-GAP" in text and "price actually paid" in text)

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           (" | " + detail) if (not ok and detail) else ""))
    print("SELFTEST %d/%d %s" % (len(checks) - len(failed), len(checks),
                                 "PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="Quota prepaid liability drill (M110)")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--revenue", default=REVENUE_RAIL)
    r.add_argument("--mock", default=MOCK_RAIL)
    r.add_argument("--unused-ratio", type=float, default=0.4)
    r.add_argument("--unit-cost-b", type=float, default=0.6)
    r.add_argument("--json-out", default=None)
    r.add_argument("--text-out", default=None)
    sub.add_parser("selftest")
    args = p.parse_args(argv)

    if args.cmd == "selftest":
        return _selftest()
    if args.cmd != "run":
        p.print_help()
        return 2

    reports = {}
    for name, path in (("real", args.revenue), ("mock", args.mock)):
        rep = run(load_rows(path), args.unused_ratio, args.unit_cost_b)
        rep["rail"] = name
        reports[name] = rep
        print("[%s]" % name)
        print(render(rep))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(reports, fh, sort_keys=True, indent=2, ensure_ascii=True)
            fh.write("\n")
    if args.text_out:
        with open(args.text_out, "w", encoding="utf-8") as fh:
            for name in ("real", "mock"):
                fh.write("[%s]\n%s\n" % (name, render(reports[name])))
    return 0


if __name__ == "__main__":
    sys.exit(main())