#!/usr/bin/env python3
"""cost_ledger.py - BigCompute compute-cost ledger (Data Dept first tool).

Design source: docs/research/R-20260924-unit-economics.md (wave 5).
- 14-field JSONL ledger at state/cost-ledger.jsonl (state dir is gitignored:
  financial data stays local; the tool is the tracked artifact).
- Net price formula from wave 5:  net = P * (1 - r) * (1 - f)
  where P = list price, r = refund-rate provision, f = platform fee rate.
- "3090 fund": accrues a fixed amount per order (default 10.0 CNY) so the
  CEO loop ("19.9 revenue -> buy a second 3090 -> new residents in the city")
  becomes a visible ledger line. Default card target: 3500.0 CNY.
- token columns are compatible with the group round-ledger standard line:
  tokens: local=N api=N api_reason=<one-line|->

Usage (run from repo root):
  python Tools/cost_ledger.py add --order-id BC-0001 --sku deco_9.9 \
      --price 9.9 --fee-rate 0.05 --refund-rate 0.08 --cost-item llm \
      --tier L3 --amount 0.007 --tokens-api 2000 --api-reason fulfillment
  python Tools/cost_ledger.py fund
  python Tools/cost_ledger.py summary [--month 2026-09]
  python Tools/cost_ledger.py selftest

Encoding rule: this file stays PURE ASCII (group coding law).
"""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "..", "state", "cost-ledger.jsonl")
FUND_ACCRUAL_DEFAULT = 10.0
FUND_TARGET_DEFAULT = 3500.0


def _ensure_state(path):
    d = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(d):
        os.makedirs(d)


def _load_rows(path):
    rows = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _append_row(path, row):
    _ensure_state(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")


def add_row(args, path=LEDGER):
    net = round(args.price * (1.0 - args.refund_rate) * (1.0 - args.fee_rate), 4)
    gross = round(net - args.amount, 4)
    row = {
        "order_id": args.order_id,
        "date": args.date,
        "sku": args.sku,
        "tier": args.tier,            # L1 deterministic / L2 local / L3 cloud
        "cost_item": args.cost_item,
        "price": round(args.price, 2),
        "fee_rate": args.fee_rate,
        "refund_rate": args.refund_rate,
        "net_price": net,
        "amount": round(args.amount, 4),   # cost of goods for this order
        "gross": gross,
        "tokens_local": args.tokens_local,
        "tokens_api": args.tokens_api,
        "api_reason": args.api_reason,
        "fund_3090": round(args.fund_accrual, 2),
    }
    # idempotency: one order_id one row
    for r in _load_rows(path):
        if r.get("order_id") == args.order_id:
            return {"status": "duplicate", "order_id": args.order_id, "row": r}
    _append_row(path, row)
    return {"status": "ok", "row": row}


def fund_status(args, path=LEDGER):
    rows = _load_rows(path)
    total = round(sum(r.get("fund_3090", 0.0) for r in rows), 2)
    orders = len(rows)
    target = args.fund_target
    return {
        "orders": orders,
        "fund_cny": total,
        "target_cny": target,
        "progress_pct": round(100.0 * total / target, 2) if target else 0.0,
        "orders_to_next_card": max(0, int((target - total) / FUND_ACCRUAL_DEFAULT)),
    }


def summary(args, path=LEDGER):
    rows = _load_rows(path)
    if args.month:
        rows = [r for r in rows if str(r.get("date", "")).startswith(args.month)]
    out = {
        "orders": len(rows),
        "net_cny": round(sum(r.get("net_price", 0.0) for r in rows), 2),
        "cost_cny": round(sum(r.get("amount", 0.0) for r in rows), 4),
        "gross_cny": round(sum(r.get("gross", 0.0) for r in rows), 2),
        "fund_3090_cny": round(sum(r.get("fund_3090", 0.0) for r in rows), 2),
        "tokens_local": sum(r.get("tokens_local", 0) for r in rows),
        "tokens_api": sum(r.get("tokens_api", 0) for r in rows),
    }
    return out


def _selftest():
    results = []
    for run in (1, 2):
        tmp = os.path.join(tempfile.gettempdir(), "bc_ledger_selftest_%d.jsonl" % run)
        if os.path.isfile(tmp):
            os.remove(tmp)
        class A:
            pass
        a = A()
        a.order_id, a.date, a.sku, a.tier = "BC-T%03d" % run, "2026-09-24", "selftest_sku", "L3"
        a.cost_item, a.price, a.fee_rate, a.refund_rate = "llm", 9.9, 0.05, 0.08
        a.amount, a.tokens_local, a.tokens_api, a.api_reason = 0.007, 0, 2000, "selftest"
        a.fund_accrual = FUND_ACCRUAL_DEFAULT
        r1 = add_row(a, path=tmp)
        a.order_id = "BC-T%03d" % run          # duplicate replay -> no new row
        r2 = add_row(a, path=tmp)
        rows = _load_rows(tmp)
        ok = (
            len(rows) == 1
            and r2["status"] == "duplicate"
            and abs(rows[0]["net_price"] - 9.9 * 0.92 * 0.95) < 0.0001
            and abs(rows[0]["gross"] - (rows[0]["net_price"] - 0.007)) < 0.0001
        )
        results.append((run, ok, rows[0]))
        os.remove(tmp)
    same = json.dumps(
        {k: v for k, v in results[0][2].items() if k != "order_id"}, sort_keys=True
    ) == json.dumps(
        {k: v for k, v in results[1][2].items() if k != "order_id"}, sort_keys=True
    )  # order_id differs by design; determinism = identical math on all other fields
    math_ok = results[0][1] and results[1][1]
    print("selftest: math=%s determinism=%s -> %s"
          % (math_ok, same, "PASS" if (math_ok and same) else "FAIL"))
    return 0 if (math_ok and same) else 1


def main():
    p = argparse.ArgumentParser(description="BigCompute compute-cost ledger")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--order-id", required=True)
    a.add_argument("--date", default=None)
    a.add_argument("--sku", required=True)
    a.add_argument("--tier", choices=["L1", "L2", "L3"], default="L2")
    a.add_argument("--cost-item", default="-")
    a.add_argument("--price", type=float, required=True)
    a.add_argument("--fee-rate", type=float, default=0.05)
    a.add_argument("--refund-rate", type=float, default=0.08)
    a.add_argument("--amount", type=float, default=0.0)
    a.add_argument("--tokens-local", type=int, default=0)
    a.add_argument("--tokens-api", type=int, default=0)
    a.add_argument("--api-reason", default="-")
    a.add_argument("--fund-accrual", type=float, default=FUND_ACCRUAL_DEFAULT)
    f = sub.add_parser("fund")
    f.add_argument("--fund-target", type=float, default=FUND_TARGET_DEFAULT)
    s = sub.add_parser("summary")
    s.add_argument("--month", default=None)
    sub.add_parser("selftest")
    args = p.parse_args()
    if getattr(args, "date", None) is None and args.cmd == "add":
        import datetime
        args.date = datetime.date.today().isoformat()
    if args.cmd == "add":
        out = add_row(args)
    elif args.cmd == "fund":
        out = fund_status(args)
    elif args.cmd == "summary":
        out = summary(args)
    else:
        sys.exit(_selftest())
    print(json.dumps(out, sort_keys=True, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
