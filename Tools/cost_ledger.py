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

Cloud-cost collection lane (BC-P-10 / R-36; predesign =
docs/ops/cloud-cost-lane-predesign-v1.md; separate rail file per the
split-file rule; wiring window = first real paid cloud bill (J4); unit
price enters ONLY via month-end bill backfill (J2), never at entry time):
  python Tools/cost_ledger.py cloud-budget --month 2026-10 --cap-mtok 500
  python Tools/cost_ledger.py cloud-entry --tx-id CL-0001 --consumer bigcompute \
      --lane openrouter --task-ref TASK-7 --amount-mtok 1.2 --reason "city3d"
  python Tools/cost_ledger.py cloud-bill --month 2026-10 --lane openrouter \
      --unit-cost-b 2.5 --ref BILL-202610-001
  python Tools/cost_ledger.py cloud-summary [--month 2026-10]
  python Tools/cost_ledger.py borrow-budget --month 2026-10 --cap-mtok 200
  python Tools/cost_ledger.py borrow-entry --tx-id WO-0001 --borrower-dept biglife \
      --machine-id bm-a --work-type batch_inference --tokens-mtok 1.5 \
      --quota-debit-mtok 1.5 --verdict-source idlewatch-log-L1
  python Tools/cost_ledger.py borrow-settle --month 2026-10 --unit-cost-b 2.0 --ref ME-202610
  python Tools/cost_ledger.py borrow-summary [--month 2026-10]

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
CLEDGER = os.path.join(HERE, "..", "state", "cloud-cost-ledger.jsonl")
CBUDGET = os.path.join(HERE, "..", "state", "cloud-budget.json")
BLEDGER = os.path.join(HERE, "..", "state", "borrow-cost-ledger.jsonl")
BBUDGET = os.path.join(HERE, "..", "state", "borrow-budget.json")
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


# ------------------------------------------------------- cloud-cost lane
# Cloud-cost collection rail (BC-P-10 / R-36; predesign =
# docs/ops/cloud-cost-lane-predesign-v1.md). Separate rail file
# (split-file rule, R-34/M7 parity). J2: unit price is ONLY legal from the
# month-end paid bill backfill; cloud-entry structurally exposes no
# unit-price flag so nothing can be fabricated at entry time. J4: no real
# wiring before the first paid cloud bill receipt exists.

def cloud_budget(args, path=CBUDGET):
    data = _load_budget(path)
    prev = data.get(args.month)
    data[args.month] = args.cap_mtok
    _save_budget(data, path)
    return {"status": "ok", "month": args.month,
            "cap_mtok": args.cap_mtok, "prev_cap_mtok": prev}


def cloud_entry(args, ledger=CLEDGER, budget=CBUDGET):
    rows = _load_rows(ledger)
    for r in rows:
        if r.get("tx_id") == args.tx_id:
            return {"status": "duplicate", "tx_id": args.tx_id, "row": r}
    month = _month_of(args.date)
    used_mtd = round(sum(r.get("amount_mtok", 0.0) for r in rows
                         if r.get("month") == month), 6)
    cap = _load_budget(budget).get(month)
    over = cap is not None and (used_mtd + args.amount_mtok) > cap
    if over and not args.ceo_approved:
        return {"status": "blocked_over_cloud_budget", "month": month,
                "cap_mtok": cap, "used_mtd_mtok": used_mtd,
                "attempt_mtok": args.amount_mtok,
                "note": "monthly cloud-lane budget gate: over-cap entry "
                        "needs --ceo-approved (cloud-cost-lane-predesign s4)"}
    row = {
        "tx_id": args.tx_id,
        "date": args.date,
        "month": month,
        "type": "cloud",
        "consumer": args.consumer,
        "lane": args.lane,
        "task_ref": args.task_ref,
        "amount_mtok": round(args.amount_mtok, 6),
        "unit_cost_b_cny": None,       # J2: filled only by cloud-bill
        "cost_b_cny": None,
        "receipt_status": "pending_bill",
        "settlement_caliber": "B",
        "reason": args.reason,
        "ref": None,                   # bill pointer, set by cloud-bill
        "ceo_approved": bool(args.ceo_approved),
        "tokens_local": args.tokens_local,
        "tokens_api": args.tokens_api,
        "api_reason": args.api_reason,
    }
    _append_row(ledger, row)
    return {"status": "ok_ceo_approved_over_cap" if over else "ok", "row": row,
            "month_used_mtok": round(used_mtd + args.amount_mtok, 6),
            "month_cap_mtok": cap}


def cloud_bill(args, ledger=CLEDGER):
    """Month-end backfill: the real paid bill is the ONLY legal unit-price
    source (J2); flips pending_bill -> billed for month+lane in place."""
    rows = _load_rows(ledger)
    hit = 0
    for r in rows:
        if (r.get("type") == "cloud" and r.get("month") == args.month
                and r.get("lane") == args.lane
                and r.get("receipt_status") == "pending_bill"):
            r["unit_cost_b_cny"] = round(args.unit_cost_b, 6)
            r["cost_b_cny"] = round(r.get("amount_mtok", 0.0)
                                    * args.unit_cost_b, 4)
            r["receipt_status"] = "billed"
            r["ref"] = args.ref
            hit += 1
    if hit:
        _ensure_state(ledger)
        with open(ledger, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True, ensure_ascii=True) + "\n")
    return {"status": "ok" if hit else "no_pending_rows",
            "month": args.month, "lane": args.lane,
            "unit_cost_b_cny": round(args.unit_cost_b, 6),
            "ref": args.ref, "billed_rows": hit}


def cloud_summary(args, ledger=CLEDGER, budget=CBUDGET):
    rows = _load_rows(ledger)
    if args.month:
        rows = [r for r in rows if r.get("month") == args.month]
    per = {}
    for r in rows:
        if r.get("type") != "cloud":
            continue
        key = (r.get("consumer", "-"), r.get("lane", "-"))
        c = per.setdefault(key, {"amount_mtok": 0.0, "billed_mtok": 0.0,
                                  "cost_b_cny": 0.0, "pending_rows": 0,
                                  "billed_rows": 0})
        amt = r.get("amount_mtok", 0.0)
        c["amount_mtok"] += amt
        if r.get("receipt_status") == "billed":
            c["billed_mtok"] += amt
            c["cost_b_cny"] += r.get("cost_b_cny") or 0.0
            c["billed_rows"] += 1
        else:
            c["pending_rows"] += 1
    face = {}
    for key, c in per.items():
        face["%s|%s" % key] = {
            "amount_mtok": round(c["amount_mtok"], 6),
            "billed_mtok": round(c["billed_mtok"], 6),
            "cost_b_cny": round(c["cost_b_cny"], 4),
            "pending_rows": c["pending_rows"],
            "billed_rows": c["billed_rows"],
        }
    month_face = {}
    if args.month:
        cap = _load_budget(budget).get(args.month)
        used = round(sum(r.get("amount_mtok", 0.0) for r in rows
                         if r.get("type") == "cloud"), 6)
        month_face = {
            "month": args.month,
            "cap_mtok": cap,
            "used_mtok": used,
            "remaining_mtok": None if cap is None else round(cap - used, 6),
        }
    return {
        "consumer_lane": face,
        "totals": {
            "rows": len([r for r in rows if r.get("type") == "cloud"]),
            "amount_mtok": round(sum(c["amount_mtok"] for c in per.values()), 6),
            "cost_billed_b_cny": round(sum(c["cost_b_cny"]
                                           for c in per.values()), 4),
            "pending_bill_rows": sum(c["pending_rows"] for c in per.values()),
        },
        "month_face": month_face,
        "settlement_caliber": "B_only (unit price via month-end paid bill only, J2)",
        "wiring": "accounting rail only; real cloud entry waits for the "
                  "first paid bill receipt (J4)",
    }


# ---------------------------------------------------------------------------
# borrow-compute order lane (fleet work-order rail; docs/ops/
# borrow-compute-lane-predesign-v1.md, E23). Cross-company borrowed compute
# MUST hit caliber-B books keyed by the fleet work-order tx_id
# (multi-node-scheduling R- J3: unlogged borrow = P1 violation). J2: lender
# cost is ONLY backfilled via month-end retroactive settle; borrow-entry
# structurally exposes no unit-price flag so nothing can be fabricated at
# entry time. J4: accounting rail only -- real borrow entry waits for T-28
# activation approval (10-05 review) + first real work-order voucher.

def borrow_budget(args, path=BBUDGET):
    data = _load_budget(path)
    prev = data.get(args.month)
    data[args.month] = args.cap_mtok
    _save_budget(data, path)
    return {"status": "ok", "month": args.month,
            "cap_mtok": args.cap_mtok, "prev_cap_mtok": prev}


def borrow_entry(args, ledger=BLEDGER, budget=BBUDGET):
    rows = _load_rows(ledger)
    for r in rows:
        if r.get("tx_id") == args.tx_id:
            return {"status": "duplicate", "tx_id": args.tx_id, "row": r}
    month = _month_of(args.date)
    used_mtd = round(sum(r.get("tokens_mtok", 0.0) for r in rows
                         if r.get("month") == month), 6)
    cap = _load_budget(budget).get(month)
    over = cap is not None and (used_mtd + args.tokens_mtok) > cap
    if over and not args.ceo_approved:
        return {"status": "blocked_over_borrow_budget", "month": month,
                "cap_mtok": cap, "used_mtd_mtok": used_mtd,
                "attempt_mtok": args.tokens_mtok,
                "note": "monthly borrow-lane budget gate: over-cap entry "
                        "needs --ceo-approved (borrow-compute-lane-"
                        "predesign s4; D-20260925-10)"}
    row = {
        "tx_id": args.tx_id,
        "date": args.date,
        "month": month,
        "type": "borrow",
        "lender_dept": args.lender_dept,
        "borrower_dept": args.borrower_dept,
        "machine_id": args.machine_id,
        "work_type": args.work_type,
        "tokens_mtok": round(args.tokens_mtok, 6),
        "quota_debit_mtok": round(args.quota_debit_mtok, 6),
        "settlement_caliber": "B",
        "cost_b_cny": None,       # J2: month-end retroactive settle only
        "receipt_status": "pending_settlement",
        "timeout_rule": args.timeout_rule,
        "return_rule": args.return_rule,
        "guardrails": "fleet-protocol s3.2: no-preempt-owner-priority; "
                      "no-dual-machine-same-stem; "
                      "no-write-into-borrower-core-domain",
        "verdict_source": args.verdict_source,
        "reason": args.reason,
        "ref": None,              # settle pointer, set by borrow-settle
        "ceo_approved": bool(args.ceo_approved),
    }
    _append_row(ledger, row)
    return {"status": "ok_ceo_approved_over_cap" if over else "ok", "row": row,
            "month_used_mtok": round(used_mtd + args.tokens_mtok, 6),
            "month_cap_mtok": cap}


def borrow_settle(args, ledger=BLEDGER):
    """Month-end retroactive settle: the ONLY legal lender-cost backfill
    channel (J2); flips pending_settlement -> settled for the month in place."""
    rows = _load_rows(ledger)
    hit = 0
    for r in rows:
        if (r.get("type") == "borrow" and r.get("month") == args.month
                and r.get("receipt_status") == "pending_settlement"):
            r["cost_b_cny"] = round(r.get("tokens_mtok", 0.0)
                                    * args.unit_cost_b, 4)
            r["receipt_status"] = "settled"
            r["ref"] = args.ref
            hit += 1
    if hit:
        _ensure_state(ledger)
        with open(ledger, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True, ensure_ascii=True) + "\n")
    return {"status": "ok" if hit else "no_pending_rows",
            "month": args.month,
            "unit_cost_b_cny": round(args.unit_cost_b, 6),
            "ref": args.ref, "settled_rows": hit}


def borrow_summary(args, ledger=BLEDGER, budget=BBUDGET):
    """Borrow-side monthly rollup: borrower x machine (lender collection vs
    borrower debit reconciliation face, predesign s5)."""
    rows = _load_rows(ledger)
    if args.month:
        rows = [r for r in rows if r.get("month") == args.month]
    per = {}
    for r in rows:
        if r.get("type") != "borrow":
            continue
        key = (r.get("borrower_dept", "-"), r.get("machine_id", "-"))
        c = per.setdefault(key, {"tokens_mtok": 0.0, "settled_mtok": 0.0,
                                  "cost_b_cny": 0.0, "pending_rows": 0,
                                  "settled_rows": 0})
        amt = r.get("tokens_mtok", 0.0)
        c["tokens_mtok"] += amt
        if r.get("receipt_status") == "settled":
            c["settled_mtok"] += amt
            c["cost_b_cny"] += r.get("cost_b_cny") or 0.0
            c["settled_rows"] += 1
        else:
            c["pending_rows"] += 1
    face = {}
    for key, c in per.items():
        face["%s|%s" % key] = {
            "tokens_mtok": round(c["tokens_mtok"], 6),
            "settled_mtok": round(c["settled_mtok"], 6),
            "cost_b_cny": round(c["cost_b_cny"], 4),
            "pending_rows": c["pending_rows"],
            "settled_rows": c["settled_rows"],
        }
    month_face = {}
    if args.month:
        cap = _load_budget(budget).get(args.month)
        used = round(sum(r.get("tokens_mtok", 0.0) for r in rows
                         if r.get("type") == "borrow"), 6)
        month_face = {
            "month": args.month,
            "cap_mtok": cap,
            "used_mtok": used,
            "remaining_mtok": None if cap is None else round(cap - used, 6),
        }
    return {
        "borrower_machine": face,
        "totals": {
            "rows": len([r for r in rows if r.get("type") == "borrow"]),
            "tokens_mtok": round(sum(c["tokens_mtok"] for c in per.values()), 6),
            "cost_settled_b_cny": round(sum(c["cost_b_cny"]
                                            for c in per.values()), 4),
            "pending_settlement_rows": sum(c["pending_rows"] for c in per.values()),
        },
        "month_face": month_face,
        "settlement_caliber": "B_only (lender cost via month-end retroactive settle only, J2)",
        "wiring": "accounting rail only; real borrow entry waits for T-28 "
                  "activation approval (10-05 review) + first real "
                  "work-order voucher (J4)",
    }


def _selftest():
    order_results, quota_results, cloud_results, borrow_results = [], [], [], []
    for run in (1, 2):
        tmp = os.path.join(tempfile.gettempdir(), "bc_ledger_selftest_%d.jsonl" % run)
        qtmp = os.path.join(tempfile.gettempdir(), "bc_quota_selftest_%d.jsonl" % run)
        qbtmp = os.path.join(tempfile.gettempdir(), "bc_quota_budget_%d.json" % run)
        ctmp = os.path.join(tempfile.gettempdir(), "bc_cloud_selftest_%d.jsonl" % run)
        cbtmp = os.path.join(tempfile.gettempdir(), "bc_cloud_budget_%d.json" % run)
        btmp = os.path.join(tempfile.gettempdir(), "bc_borrow_selftest_%d.jsonl" % run)
        bbtmp = os.path.join(tempfile.gettempdir(), "bc_borrow_budget_%d.json" % run)
        for p in (tmp, qtmp, qbtmp, ctmp, cbtmp, btmp, bbtmp):
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

        # cloud lane synthetic dry-run (predesign J1-J3: additive, unit
        # price only via bill backfill, tx_id replay zero-new)
        cb = Q()
        cb.month, cb.cap_mtok = "2026-09", 10.0
        cloud_budget(cb, path=cbtmp)

        def centry(tx, amt, approved=False):
            qq = Q()
            qq.tx_id, qq.date, qq.consumer = tx, "2026-09-28", "bigcompute"
            qq.lane, qq.task_ref = "openrouter", "TASK-1"
            qq.amount_mtok = amt
            qq.reason = "selftest"
            qq.ceo_approved = approved
            qq.tokens_local, qq.tokens_api, qq.api_reason = 0, 500, "cloud trial"
            return cloud_entry(qq, ledger=ctmp, budget=cbtmp)

        e1 = centry("C1", 6.0)                   # within cap -> ok
        e2 = centry("C2", 3.0)                   # 9 <= 10 -> ok
        e3 = centry("C3", 2.0)                   # 11 > 10 -> budget gate blocks
        e4 = centry("C3", 2.0, approved=True)    # over cap, CEO approved
        e5 = centry("C1", 6.0)                   # duplicate tx_id -> no new row
        crows_pre = _load_rows(ctmp)
        j2_ok = all(r.get("unit_cost_b_cny") is None
                    and r.get("cost_b_cny") is None
                    and r.get("receipt_status") == "pending_bill"
                    for r in crows_pre)
        cbill = Q()
        cbill.month, cbill.lane = "2026-09", "openrouter"
        cbill.unit_cost_b, cbill.ref = 2.0, "BILL-202609-001"
        b1 = cloud_bill(cbill, ledger=ctmp)
        crows = _load_rows(ctmp)
        csargs = Q()
        csargs.month = "2026-09"
        csumm = cloud_summary(csargs, ledger=ctmp, budget=cbtmp)
        cl = csumm["consumer_lane"]["bigcompute|openrouter"]
        cloud_ok = (
            e1["status"] == "ok"
            and e2["status"] == "ok"
            and e3["status"] == "blocked_over_cloud_budget"
            and e4["status"] == "ok_ceo_approved_over_cap"
            and e5["status"] == "duplicate"
            and len(crows_pre) == 3
            and j2_ok
            and b1["billed_rows"] == 3
            and all(r.get("receipt_status") == "billed" for r in crows)
            and all(r.get("ref") == "BILL-202609-001" for r in crows)
            and abs(crows[0]["cost_b_cny"] - 12.0) < 0.0001
            and abs(cl["amount_mtok"] - 11.0) < 1e-6
            and abs(cl["cost_b_cny"] - 22.0) < 0.0001
            and cl["pending_rows"] == 0 and cl["billed_rows"] == 3
            and abs(csumm["totals"]["amount_mtok"] - 11.0) < 1e-6
            and abs(csumm["totals"]["cost_billed_b_cny"] - 22.0) < 0.0001
            and csumm["totals"]["pending_bill_rows"] == 0
            and csumm["month_face"]["cap_mtok"] == 10.0
            and abs(csumm["month_face"]["used_mtok"] - 11.0) < 1e-6
        )
        cloud_results.append((cloud_ok, crows, csumm))

        # borrow lane: fleet work-order rail (predesign s6 wiring-round
        # prerequisite) -- J3 tx_id replay zero-new, monthly budget gate,
        # J2 lender cost only via month-end retroactive settle
        bb = Q()
        bb.month, bb.cap_mtok = "2026-09", 10.0
        borrow_budget(bb, path=bbtmp)

        def bentry(tx, amt, approved=False):
            qq = Q()
            qq.tx_id, qq.date = tx, "2026-09-28"
            qq.lender_dept, qq.borrower_dept = "bigcompute", "biglife"
            qq.machine_id, qq.work_type = "bm-a", "batch_inference"
            qq.tokens_mtok = amt
            qq.quota_debit_mtok = amt
            qq.timeout_rule, qq.return_rule = "30min", "on-complete"
            qq.verdict_source, qq.reason = "idlewatch-log-L1", "selftest"
            qq.ceo_approved = approved
            return borrow_entry(qq, ledger=btmp, budget=bbtmp)

        f1 = bentry("W1", 6.0)                   # within cap -> ok
        f2 = bentry("W2", 3.0)                   # 9 <= 10 -> ok
        f3 = bentry("W3", 2.0)                   # 11 > 10 -> budget gate blocks
        f4 = bentry("W3", 2.0, approved=True)    # over cap, CEO approved
        f5 = bentry("W1", 6.0)                   # duplicate tx_id -> no new row
        brows_pre = _load_rows(btmp)
        bj2_ok = all(r.get("cost_b_cny") is None
                     and r.get("receipt_status") == "pending_settlement"
                     and r.get("settlement_caliber") == "B"
                     for r in brows_pre)
        bset = Q()
        bset.month, bset.unit_cost_b, bset.ref = "2026-09", 2.0, "SETTLE-202609-001"
        s1 = borrow_settle(bset, ledger=btmp)
        brows = _load_rows(btmp)
        bsargs = Q()
        bsargs.month = "2026-09"
        bsumm = borrow_summary(bsargs, ledger=btmp, budget=bbtmp)
        bl = bsumm["borrower_machine"]["biglife|bm-a"]
        borrow_ok = (
            f1["status"] == "ok"
            and f2["status"] == "ok"
            and f3["status"] == "blocked_over_borrow_budget"
            and f4["status"] == "ok_ceo_approved_over_cap"
            and f5["status"] == "duplicate"
            and len(brows_pre) == 3
            and bj2_ok
            and s1["settled_rows"] == 3
            and all(r.get("receipt_status") == "settled" for r in brows)
            and all(r.get("ref") == "SETTLE-202609-001" for r in brows)
            and abs(brows[0]["cost_b_cny"] - 12.0) < 0.0001
            and abs(bl["tokens_mtok"] - 11.0) < 1e-6
            and abs(bl["cost_b_cny"] - 22.0) < 0.0001
            and bl["pending_rows"] == 0 and bl["settled_rows"] == 3
            and abs(bsumm["totals"]["tokens_mtok"] - 11.0) < 1e-6
            and abs(bsumm["totals"]["cost_settled_b_cny"] - 22.0) < 0.0001
            and bsumm["totals"]["pending_settlement_rows"] == 0
            and bsumm["month_face"]["cap_mtok"] == 10.0
            and abs(bsumm["month_face"]["used_mtok"] - 11.0) < 1e-6
        )
        borrow_results.append((borrow_ok, brows, bsumm))
        for p in (tmp, qtmp, qbtmp, ctmp, cbtmp, btmp, bbtmp):
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
        and json.dumps(cloud_results[0][1], sort_keys=True)
        == json.dumps(cloud_results[1][1], sort_keys=True)
        and json.dumps(cloud_results[0][2], sort_keys=True)
        == json.dumps(cloud_results[1][2], sort_keys=True)
        and json.dumps(borrow_results[0][1], sort_keys=True)
        == json.dumps(borrow_results[1][1], sort_keys=True)
        and json.dumps(borrow_results[0][2], sort_keys=True)
        == json.dumps(borrow_results[1][2], sort_keys=True)
    )  # order_id differs by design; determinism = identical math on all else
    math_ok = (order_results[0][0] and order_results[1][0]
               and quota_results[0][0] and quota_results[1][0]
               and cloud_results[0][0] and cloud_results[1][0]
               and borrow_results[0][0] and borrow_results[1][0])
    print("selftest: math=%s determinism=%s count=%s (orders+quota-gate+count+cloud+borrow) -> %s"
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
    cbp = sub.add_parser("cloud-budget")
    cbp.add_argument("--month", required=True)
    cbp.add_argument("--cap-mtok", dest="cap_mtok", type=float, required=True)
    ce = sub.add_parser("cloud-entry")
    ce.add_argument("--tx-id", dest="tx_id", required=True)
    ce.add_argument("--consumer", required=True)
    ce.add_argument("--lane", required=True)
    ce.add_argument("--task-ref", dest="task_ref", default="-")
    ce.add_argument("--amount-mtok", dest="amount_mtok", type=float, required=True)
    ce.add_argument("--reason", default="-")
    ce.add_argument("--date", default=None)
    ce.add_argument("--ceo-approved", dest="ceo_approved", action="store_true")
    ce.add_argument("--tokens-local", dest="tokens_local", type=int, default=0)
    ce.add_argument("--tokens-api", dest="tokens_api", type=int, default=0)
    ce.add_argument("--api-reason", dest="api_reason", default="-")
    cb2 = sub.add_parser("cloud-bill")
    cb2.add_argument("--month", required=True)
    cb2.add_argument("--lane", required=True)
    cb2.add_argument("--unit-cost-b", dest="unit_cost_b", type=float, required=True)
    cb2.add_argument("--ref", required=True)
    csu = sub.add_parser("cloud-summary")
    csu.add_argument("--month", default=None)
    bbp = sub.add_parser("borrow-budget")
    bbp.add_argument("--month", required=True)
    bbp.add_argument("--cap-mtok", dest="cap_mtok", type=float, required=True)
    be = sub.add_parser("borrow-entry")
    be.add_argument("--tx-id", dest="tx_id", required=True)
    be.add_argument("--lender-dept", dest="lender_dept", default="BigCompute")
    be.add_argument("--borrower-dept", dest="borrower_dept", required=True)
    be.add_argument("--machine-id", dest="machine_id", required=True)
    be.add_argument("--work-type", dest="work_type", required=True)
    be.add_argument("--tokens-mtok", dest="tokens_mtok", type=float, required=True)
    be.add_argument("--quota-debit-mtok", dest="quota_debit_mtok", type=float, default=0.0)
    be.add_argument("--timeout-rule", dest="timeout_rule", default="-")
    be.add_argument("--return-rule", dest="return_rule", default="-")
    be.add_argument("--verdict-source", dest="verdict_source", default="-")
    be.add_argument("--reason", default="-")
    be.add_argument("--date", default=None)
    be.add_argument("--ceo-approved", dest="ceo_approved", action="store_true")
    bs = sub.add_parser("borrow-settle")
    bs.add_argument("--month", required=True)
    bs.add_argument("--unit-cost-b", dest="unit_cost_b", type=float, required=True)
    bs.add_argument("--ref", required=True)
    bsu = sub.add_parser("borrow-summary")
    bsu.add_argument("--month", default=None)
    ct = sub.add_parser("count")
    ct.add_argument("--text", default=None)
    ct.add_argument("--file", default=None)
    ct.add_argument("--encoding", default="cl100k_base")
    sub.add_parser("selftest")
    args = p.parse_args()
    if getattr(args, "date", None) is None and args.cmd in ("add", "quota-issue", "quota-consume", "cloud-entry", "borrow-entry"):
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
    elif args.cmd == "cloud-budget":
        out = cloud_budget(args)
    elif args.cmd == "cloud-entry":
        out = cloud_entry(args)
    elif args.cmd == "cloud-bill":
        out = cloud_bill(args)
    elif args.cmd == "cloud-summary":
        out = cloud_summary(args)
    elif args.cmd == "borrow-budget":
        out = borrow_budget(args)
    elif args.cmd == "borrow-entry":
        out = borrow_entry(args)
    elif args.cmd == "borrow-settle":
        out = borrow_settle(args)
    elif args.cmd == "borrow-summary":
        out = borrow_summary(args)
    elif args.cmd == "count":
        out = count_tokens(args)
    else:
        sys.exit(_selftest())
    print(json.dumps(out, sort_keys=True, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
