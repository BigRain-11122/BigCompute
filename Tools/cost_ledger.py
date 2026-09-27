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

Quota rail (P-67 compute-quota accounting track, T-20260926-23; spec =
docs/ops/empowerment-catalog-v1.md section 3 + BC-F-20260925-01):
- unit = 1M tokens (amount_mtok); tokens_local/tokens_api/api_reason columns
  stay aligned with the group round-ledger 3-column standard.
- settlement caliber = internal cost anchor B ONLY; external anchor A
  (e.g. DeepSeek $0.15/1M) is display-only, never a settlement price (R-24).
- monthly budget gate: over-cap issuance is blocked unless --ceo-approved.
- accounting rail only: no real exchange before the 3 prerequisites.
  python Tools/cost_ledger.py quota-budget --month 2026-09 --cap-mtok 500
  python Tools/cost_ledger.py quota-issue --tx-id Q-0001 --consumer biglife \
      --amount-mtok 2.5 --unit-cost-b 1.23 --reason "P-67 reward"
  python Tools/cost_ledger.py quota-consume --tx-id Q-0002 --consumer biglife \
      --amount-mtok 0.8 --unit-cost-b 1.23 --reason "llm pipeline"
  python Tools/cost_ledger.py quota-summary [--month 2026-09]

Token-count face (T-20260926-23 metering; adopted candidate openai/tiktoken
via OSS harvest OH-20260927-bigcompute, MIT license, five gates PASS):
- estimation caliber ONLY: OpenAI BPE encodings are NOT the Qwen vocab;
  exact Qwen token counts come from Ollama eval_count. tiktoken counts
  feed the quota rail (caliber B accounting) as estimates, never as a
  claim of exact model tokens.
- offline cache: the tool pins TIKTOKEN_CACHE_DIR to state/tiktoken-cache
  and prefetches the BPE file on first use of an encoding (adoption round
  prefetched cl100k_base), so counting afterwards runs without network.
  python Tools/cost_ledger.py count --text "hello world"
  python Tools/cost_ledger.py count --file notes.md --encoding cl100k_base

Encoding rule: this file stays PURE ASCII (group coding law).
"""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "..", "state", "cost-ledger.jsonl")
QLEDGER = os.path.join(HERE, "..", "state", "quota-ledger.jsonl")
QBUDGET = os.path.join(HERE, "..", "state", "quota-budget.json")
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


# ------------------------------------------------------------ token count

TIKTOKEN_CACHE = os.path.join(HERE, "..", "state", "tiktoken-cache")
COUNT_NOTE = ("estimation caliber: openai BPE != qwen vocab; "
              "exact qwen counting = ollama eval_count")


def count_tokens(args):
    text = args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            text = fh.read()
    if not text:
        return {"status": "error_empty_input",
                "note": "provide --text or --file"}
    _ensure_state(TIKTOKEN_CACHE)
    os.environ["TIKTOKEN_CACHE_DIR"] = TIKTOKEN_CACHE
    try:
        import tiktoken  # adopted dep (MIT, OH-20260927-bigcompute)
        enc = tiktoken.get_encoding(args.encoding)
    except ImportError:
        return {"status": "error_unavailable",
                "note": "tiktoken not installed: pip install tiktoken"}
    n = len(enc.encode(text))
    return {
        "status": "ok",
        "tokens": n,
        "amount_mtok": round(n / 1000000.0, 6),
        "encoding": args.encoding,
        "counter": "openai/tiktoken",
        "note": COUNT_NOTE,
        "cache_dir": "state/tiktoken-cache",
    }


# ---------------------------------------------------------------- quota rail

def _month_of(date_str):
    return str(date_str)[:7]


def _load_budget(path=QBUDGET):
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _save_budget(data, path=QBUDGET):
    _ensure_state(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, sort_keys=True, ensure_ascii=True, indent=2)


def quota_budget(args, path=QBUDGET):
    data = _load_budget(path)
    prev = data.get(args.month)
    data[args.month] = args.cap_mtok
    _save_budget(data, path)
    return {"status": "ok", "month": args.month,
            "cap_mtok": args.cap_mtok, "prev_cap_mtok": prev}


def _quota_state(rows):
    """Per-consumer aggregation. Issued quota = reward-side liability at
    caliber B cost; consumed = realized cost. Kept separate on purpose."""
    per = {}
    for r in rows:
        if r.get("type") not in ("issue", "consume"):
            continue
        c = per.setdefault(r.get("consumer", "-"),
                           {"issued_mtok": 0.0, "consumed_mtok": 0.0,
                            "cost_issued_b_cny": 0.0, "cost_consumed_b_cny": 0.0})
        amt = r.get("amount_mtok", 0.0)
        if r.get("type") == "issue":
            c["issued_mtok"] += amt
            c["cost_issued_b_cny"] += r.get("cost_b_cny", 0.0)
        else:
            c["consumed_mtok"] += amt
            c["cost_consumed_b_cny"] += r.get("cost_b_cny", 0.0)
    for c in per.values():
        c["issued_mtok"] = round(c["issued_mtok"], 6)
        c["consumed_mtok"] = round(c["consumed_mtok"], 6)
        c["balance_mtok"] = round(c["issued_mtok"] - c["consumed_mtok"], 6)
        c["cost_issued_b_cny"] = round(c["cost_issued_b_cny"], 4)
        c["cost_consumed_b_cny"] = round(c["cost_consumed_b_cny"], 4)
    return per


def quota_issue(args, ledger=QLEDGER, budget=QBUDGET):
    rows = _load_rows(ledger)
    for r in rows:
        if r.get("tx_id") == args.tx_id:
            return {"status": "duplicate", "tx_id": args.tx_id, "row": r}
    month = _month_of(args.date)
    issued_mtd = round(sum(r.get("amount_mtok", 0.0) for r in rows
                           if r.get("type") == "issue"
                           and r.get("month") == month), 6)
    cap = _load_budget(budget).get(month)
    over = cap is not None and (issued_mtd + args.amount_mtok) > cap
    if over and not args.ceo_approved:
        return {"status": "blocked_over_budget", "month": month,
                "cap_mtok": cap, "issued_mtd_mtok": issued_mtd,
                "attempt_mtok": args.amount_mtok,
                "note": "monthly budget gate: over-cap issuance needs "
                        "--ceo-approved (BC-F-20260925-01)"}
    row = {
        "tx_id": args.tx_id,
        "date": args.date,
        "month": month,
        "type": "issue",
        "consumer": args.consumer,
        "amount_mtok": round(args.amount_mtok, 6),
        "unit_cost_b_cny": args.unit_cost_b_cny,
        "cost_b_cny": round(args.amount_mtok * args.unit_cost_b_cny, 4),
        "settlement_caliber": "B",
        "reason": args.reason,
        "ref": args.ref,
        "ceo_approved": bool(args.ceo_approved),
        "tokens_local": 0,
        "tokens_api": 0,
        "api_reason": "-",
    }
    _append_row(ledger, row)
    state = _quota_state(_load_rows(ledger)).get(args.consumer, {})
    out = {"status": "ok_ceo_approved_over_cap" if over else "ok", "row": row}
    out.update(state)
    return out


def quota_consume(args, ledger=QLEDGER):
    rows = _load_rows(ledger)
    for r in rows:
        if r.get("tx_id") == args.tx_id:
            return {"status": "duplicate", "tx_id": args.tx_id, "row": r}
    state = _quota_state(rows).get(args.consumer, {})
    bal = state.get("balance_mtok", 0.0)
    if args.amount_mtok > bal:
        return {"status": "insufficient_balance", "consumer": args.consumer,
                "balance_mtok": bal, "attempt_mtok": args.amount_mtok}
    row = {
        "tx_id": args.tx_id,
        "date": args.date,
        "month": _month_of(args.date),
        "type": "consume",
        "consumer": args.consumer,
        "amount_mtok": round(args.amount_mtok, 6),
        "unit_cost_b_cny": args.unit_cost_b_cny,
        "cost_b_cny": round(args.amount_mtok * args.unit_cost_b_cny, 4),
        "settlement_caliber": "B",
        "reason": args.reason,
        "ref": args.ref,
        "ceo_approved": False,
        "tokens_local": args.tokens_local,
        "tokens_api": args.tokens_api,
        "api_reason": args.api_reason,
    }
    _append_row(ledger, row)
    return {"status": "ok", "row": row,
            "balance_mtok": round(bal - args.amount_mtok, 6)}


def quota_summary(args, ledger=QLEDGER, budget=QBUDGET):
    rows = _load_rows(ledger)
    if args.month:
        rows = [r for r in rows if r.get("month") == args.month]
    if args.consumer:
        rows = [r for r in rows if r.get("consumer") == args.consumer]
    per = _quota_state(rows)
    totals = {
        "issued_mtok": round(sum(c["issued_mtok"] for c in per.values()), 6),
        "consumed_mtok": round(sum(c["consumed_mtok"] for c in per.values()), 6),
        "cost_issued_b_cny": round(sum(c["cost_issued_b_cny"] for c in per.values()), 4),
        "cost_consumed_b_cny": round(sum(c["cost_consumed_b_cny"] for c in per.values()), 4),
    }
    totals["balance_mtok"] = round(totals["issued_mtok"] - totals["consumed_mtok"], 6)
    month_face = {}
    if args.month:
        cap = _load_budget(budget).get(args.month)
        issued_mtd = round(sum(r.get("amount_mtok", 0.0) for r in rows
                               if r.get("type") == "issue"), 6)
        month_face = {
            "month": args.month,
            "cap_mtok": cap,
            "issued_mtd_mtok": issued_mtd,
            "remaining_mtok": None if cap is None else round(cap - issued_mtd, 6),
            "over_cap_ceo_approved_tx": sum(
                1 for r in rows
                if r.get("type") == "issue" and r.get("ceo_approved")),
        }
    return {
        "consumers": per,
        "totals": totals,
        "month_face": month_face,
        "settlement_caliber": "B_only (caliber A external anchor = display only)",
        "rails": "accounting rail only; real exchange needs 3 prerequisites",
    }


def _selftest():
    order_results, quota_results = [], []
    for run in (1, 2):
        tmp = os.path.join(tempfile.gettempdir(), "bc_ledger_selftest_%d.jsonl" % run)
        qtmp = os.path.join(tempfile.gettempdir(), "bc_quota_selftest_%d.jsonl" % run)
        qbtmp = os.path.join(tempfile.gettempdir(), "bc_quota_budget_%d.json" % run)
        for p in (tmp, qtmp, qbtmp):
            if os.path.isfile(p):
                os.remove(p)

        class A:
            pass
        a = A()
        a.order_id, a.date, a.sku, a.tier = "BC-T%03d" % run, "2026-09-24", "selftest_sku", "L3"
        a.cost_item, a.price, a.fee_rate, a.refund_rate = "llm", 9.9, 0.05, 0.08
        a.amount, a.tokens_local, a.tokens_api, a.api_reason = 0.007, 0, 2000, "selftest"
        a.fund_accrual = FUND_ACCRUAL_DEFAULT
        add_row(a, path=tmp)
        a.order_id = "BC-T%03d" % run          # duplicate replay -> no new row
        r2 = add_row(a, path=tmp)
        orows = _load_rows(tmp)
        order_ok = (
            len(orows) == 1
            and r2["status"] == "duplicate"
            and abs(orows[0]["net_price"] - 9.9 * 0.92 * 0.95) < 0.0001
            and abs(orows[0]["gross"] - (orows[0]["net_price"] - 0.007)) < 0.0001
        )
        order_results.append((order_ok, orows[0]))

        class Q:
            pass
        b = Q()
        b.month, b.cap_mtok = "2026-09", 10.0
        quota_budget(b, path=qbtmp)

        def issue(tx, amt, approved=False):
            qq = Q()
            qq.tx_id, qq.date, qq.consumer = tx, "2026-09-27", "biglife"
            qq.amount_mtok, qq.unit_cost_b_cny = amt, 1.25
            qq.reason, qq.ref, qq.ceo_approved = "selftest", "-", approved
            return quota_issue(qq, ledger=qtmp, budget=qbtmp)

        def consume(tx, amt):
            qq = Q()
            qq.tx_id, qq.date, qq.consumer = tx, "2026-09-27", "biglife"
            qq.amount_mtok, qq.unit_cost_b_cny = amt, 1.25
            qq.reason, qq.ref = "selftest", "-"
            qq.tokens_local, qq.tokens_api, qq.api_reason = 1000, 0, "selftest"
            return quota_consume(qq, ledger=qtmp)

        i1 = issue("Q1", 6.0)                    # within cap -> ok
        i2 = issue("Q2", 6.0)                    # 6+6 > 10 -> budget gate blocks
        i3 = issue("Q3", 4.0)                    # 6+4 == cap -> ok
        i4 = issue("Q4", 1.0)                    # 11 > 10 -> blocked
        i5 = issue("Q5", 1.0, approved=True)    # over cap, CEO approved
        i6 = issue("Q1", 6.0)                    # duplicate tx_id -> no new row
        c1 = consume("Q6", 3.0)                  # balance 11-3=8
        c2 = consume("Q7", 20.0)                 # overdraft rejected
        sargs = Q()
        sargs.month, sargs.consumer = "2026-09", None
        summ = quota_summary(sargs, ledger=qtmp, budget=qbtmp)
        qrows = _load_rows(qtmp)
        qbl = summ["consumers"]["biglife"]
        quota_ok = (
            i1["status"] == "ok"
            and i2["status"] == "blocked_over_budget"
            and i3["status"] == "ok"
            and i4["status"] == "blocked_over_budget"
            and i5["status"] == "ok_ceo_approved_over_cap"
            and i6["status"] == "duplicate"
            and c1["status"] == "ok" and abs(c1["balance_mtok"] - 8.0) < 1e-6
            and c2["status"] == "insufficient_balance"
            and len(qrows) == 4
            and abs(qrows[0]["cost_b_cny"] - 7.5) < 0.0001
            and qrows[-1]["tokens_local"] == 1000
            and abs(qbl["issued_mtok"] - 11.0) < 1e-6
            and abs(qbl["balance_mtok"] - 8.0) < 1e-6
            and abs(qbl["cost_issued_b_cny"] - 13.75) < 0.0001
            and abs(qbl["cost_consumed_b_cny"] - 3.75) < 0.0001
            and summ["month_face"]["over_cap_ceo_approved_tx"] == 1
        )
        quota_results.append((quota_ok, qrows, summ))
        for p in (tmp, qtmp, qbtmp):
            os.remove(p)
    # token-count face: estimation caliber; absence is visible, not a math fail
    class C:
        pass
    cface = []
    for run in (1, 2):
        c = C()
        c.text = "BigCompute quota rail token counting selftest 0123456789."
        c.file = None
        c.encoding = "cl100k_base"
        cface.append(count_tokens(c))
    if cface[0]["status"] == "error_unavailable":
        count_status, count_ok = "skip_no_tiktoken", True
    else:
        count_ok = (
            cface[0]["status"] == "ok"
            and cface[1]["status"] == "ok"
            and cface[0]["tokens"] == cface[1]["tokens"]
            and cface[0]["tokens"] > 0
            and abs(cface[0]["amount_mtok"]
                    - cface[0]["tokens"] / 1000000.0) < 1e-9
        )
        count_status = "ok" if count_ok else "fail"
    same = (
        json.dumps(
            {k: v for k, v in order_results[0][1].items() if k != "order_id"},
            sort_keys=True)
        == json.dumps(
            {k: v for k, v in order_results[1][1].items() if k != "order_id"},
            sort_keys=True)
        and json.dumps(quota_results[0][1], sort_keys=True)
        == json.dumps(quota_results[1][1], sort_keys=True)
        and json.dumps(quota_results[0][2], sort_keys=True)
        == json.dumps(quota_results[1][2], sort_keys=True)
    )  # order_id differs by design; determinism = identical math on all else
    math_ok = (order_results[0][0] and order_results[1][0]
               and quota_results[0][0] and quota_results[1][0])
    print("selftest: math=%s determinism=%s count=%s (orders+quota-gate+count) -> %s"
          % (math_ok, same, count_status,
             "PASS" if (math_ok and same and count_ok) else "FAIL"))
    return 0 if (math_ok and same and count_ok) else 1


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
    b = sub.add_parser("quota-budget")
    b.add_argument("--month", required=True)
    b.add_argument("--cap-mtok", dest="cap_mtok", type=float, required=True)
    qi = sub.add_parser("quota-issue")
    qi.add_argument("--tx-id", dest="tx_id", required=True)
    qi.add_argument("--consumer", required=True)
    qi.add_argument("--amount-mtok", dest="amount_mtok", type=float, required=True)
    qi.add_argument("--unit-cost-b", dest="unit_cost_b_cny", type=float, required=True)
    qi.add_argument("--reason", default="-")
    qi.add_argument("--ref", default="-")
    qi.add_argument("--date", default=None)
    qi.add_argument("--ceo-approved", dest="ceo_approved", action="store_true")
    qc = sub.add_parser("quota-consume")
    qc.add_argument("--tx-id", dest="tx_id", required=True)
    qc.add_argument("--consumer", required=True)
    qc.add_argument("--amount-mtok", dest="amount_mtok", type=float, required=True)
    qc.add_argument("--unit-cost-b", dest="unit_cost_b_cny", type=float, required=True)
    qc.add_argument("--reason", default="-")
    qc.add_argument("--ref", default="-")
    qc.add_argument("--date", default=None)
    qc.add_argument("--tokens-local", dest="tokens_local", type=int, default=0)
    qc.add_argument("--tokens-api", dest="tokens_api", type=int, default=0)
    qc.add_argument("--api-reason", dest="api_reason", default="-")
    qsu = sub.add_parser("quota-summary")
    qsu.add_argument("--month", default=None)
    qsu.add_argument("--consumer", default=None)
    ct = sub.add_parser("count")
    ct.add_argument("--text", default=None)
    ct.add_argument("--file", default=None)
    ct.add_argument("--encoding", default="cl100k_base")
    sub.add_parser("selftest")
    args = p.parse_args()
    if getattr(args, "date", None) is None and args.cmd in ("add", "quota-issue", "quota-consume"):
        import datetime
        args.date = datetime.date.today().isoformat()
    if args.cmd == "add":
        out = add_row(args)
    elif args.cmd == "fund":
        out = fund_status(args)
    elif args.cmd == "summary":
        out = summary(args)
    elif args.cmd == "quota-budget":
        out = quota_budget(args)
    elif args.cmd == "quota-issue":
        out = quota_issue(args)
    elif args.cmd == "quota-consume":
        out = quota_consume(args)
    elif args.cmd == "quota-summary":
        out = quota_summary(args)
    elif args.cmd == "count":
        out = count_tokens(args)
    else:
        sys.exit(_selftest())
    print(json.dumps(out, sort_keys=True, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
