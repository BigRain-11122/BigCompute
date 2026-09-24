#!/usr/bin/env python3
"""End-to-end local test for the fulfillment MVP skeleton.

Scenarios (task acceptance list):
  A  mock orders -> generate -> deliver -> ledger rows on disk
  F  ledger rows match the group JSONL schema exactly
  B  idempotent replay: zero new orders / receipts / ledger rows
  C  failure retry cap: attempts <= 3, then manual fallback queue
  C+ manual-queue order aged past 24h escalates to the refund flag
  D  stale pending order: rights-first refund flag, zero compute spent
  E  unconfirmed orders never enter fulfillment (confirm gate)
  G  ledger hook idempotency units

Run:  python test_pipeline.py
Zero network, zero secrets, zero popups. Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import timedelta
from typing import List, Optional, Tuple

import ledger_hook
from pipeline import (
    FulfillmentPipeline,
    MockOrderSource,
    Order,
    STATUS_DONE,
    STATUS_MANUAL,
    STATUS_PENDING,
    STATUS_REFUND,
    StubDeliverer,
    StubGenerator,
    iso,
    utc_now,
)

RESULTS: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    if ok:
        print("[PASS] %s" % name)
    else:
        print("[FAIL] %s -- %s" % (name, detail))


def build_pipeline(state_dir: str, orders: Optional[List[Order]] = None,
                   fail_ids: Optional[List[str]] = None
                   ) -> FulfillmentPipeline:
    source = MockOrderSource(orders)
    generator = StubGenerator(fail_order_ids=fail_ids)
    deliverer = StubDeliverer(os.path.join(state_dir, "deliveries"))
    return FulfillmentPipeline(source, generator, deliverer, state_dir)


def scenario_a(base: str) -> None:
    print("\n[scenario] A: happy path - poll -> generate -> deliver -> ledger")
    state = os.path.join(base, "a_happy_path")
    pipeline = build_pipeline(state)
    report = pipeline.run_once()

    check("A1 poll sees the 3 built-in mock orders", report.polled == 3,
          "polled=%d" % report.polled)
    check("A2 intake registers 3 new orders (idempotent keys)",
          report.new_orders == 3, "new_orders=%d" % report.new_orders)
    check("A3 first pass delivers all 3", report.delivered == 3,
          "delivered=%d" % report.delivered)
    statuses = dict((o.order_id, o.status) for o in pipeline.store.all())
    check("A4 every order reaches status=done",
          len(statuses) == 3
          and all(s == STATUS_DONE for s in statuses.values()),
          str(statuses))
    receipts = os.listdir(os.path.join(state, "deliveries"))
    check("A5 3 delivery receipts written to disk", len(receipts) == 3,
          "files=%s" % sorted(receipts))
    arts = os.listdir(os.path.join(state, "artifacts"))
    check("A6 3 artifact records written to disk", len(arts) == 3,
          "files=%s" % sorted(arts))
    rows = ledger_hook.read_rows(pipeline.ledger_path)
    check("A7 ledger holds 2 rows per order (6 total)", len(rows) == 6,
          "rows=%d" % len(rows))

    expected = set(ledger_hook.FIELDS)
    malformed = [r for r in rows if set(r.keys()) != expected]
    check("F1 every ledger row carries exactly the 9 schema fields",
          not malformed, "malformed=%d" % len(malformed))
    wrong_tier = [r for r in rows if r.get("tier") != "L1"]
    check("F2 stub rows are classified tier=L1", not wrong_tier,
          str(wrong_tier[:1]))
    wrong_tokens = [r for r in rows
                    if r.get("tokens_local") != 0 or r.get("tokens_api") != 0]
    check("F3 stub rows carry the zero token trio", not wrong_tokens,
          str(wrong_tokens[:1]))
    per_order = {}
    for row in rows:
        per_order.setdefault(row["order_id"], set()).add(row["cost_item"])
    check("F4 each order has one tokens row and one cost row",
          all(items == set(["tokens", "cost"])
              for items in per_order.values()),
          str(per_order))
    nonzero = [r for r in rows if r.get("amount_cny") != 0.0]
    check("F5 stub fulfillment costs are 0.0 CNY", not nonzero,
          str(nonzero[:1]))

    print("\n[scenario] B: idempotent replay over the same state")
    replay = build_pipeline(state)
    report2 = replay.run_once()
    check("B1 replay intakes zero new orders", report2.new_orders == 0,
          "new_orders=%d" % report2.new_orders)
    check("B2 replay spends zero attempts and delivers nothing",
          report2.attempts == 0 and report2.delivered == 0,
          "attempts=%d delivered=%d"
          % (report2.attempts, report2.delivered))
    check("B3 replay flags no refunds", report2.refunds_flagged == 0,
          "refunds=%d" % report2.refunds_flagged)
    rows_after = ledger_hook.read_rows(replay.ledger_path)
    check("B4 replay adds zero ledger rows", len(rows_after) == 6,
          "rows=%d" % len(rows_after))
    receipts_after = os.listdir(os.path.join(state, "deliveries"))
    check("B5 replay adds zero receipts", len(receipts_after) == 3,
          "files=%d" % len(receipts_after))
    report3 = replay.run_once()
    check("B6 a third pass stays inert",
          report3.new_orders == 0 and report3.attempts == 0
          and report3.delivered == 0 and report3.refunds_flagged == 0,
          str(report3.to_dict()))


def scenario_c(base: str) -> None:
    print("\n[scenario] C: failure retry cap -> manual fallback queue")
    state = os.path.join(base, "c_retry_cap")
    order = Order("MO-FAIL-0001", "PIXEL-PET-RED", 1,
                  created_at=iso(utc_now()))
    pipeline = build_pipeline(state, [order], fail_ids=["MO-FAIL-0001"])
    report = pipeline.run_once()

    check("C1 failing order retried exactly 3 times (cap <= 3)",
          report.attempts == 3, "attempts=%d" % report.attempts)
    check("C2 nothing delivered for the failing order",
          report.delivered == 0, "delivered=%d" % report.delivered)
    check("C3 order parked in the manual queue",
          report.manual_queued == 1, "manual_queued=%d"
          % report.manual_queued)
    stored = pipeline.store.get("MO-FAIL-0001")
    check("C4 stored status=manual_queue", stored.status == STATUS_MANUAL,
          stored.status)
    check("C5 stored attempts counter == 3", stored.attempts == 3,
          "attempts=%d" % stored.attempts)
    queue_ids = [o.order_id for o in pipeline.store.manual_queue()]
    check("C6 manual queue lists the order",
          queue_ids == ["MO-FAIL-0001"], str(queue_ids))
    check("C7 no receipt file for the failed order",
          not os.path.exists(os.path.join(state, "deliveries",
                                          "MO-FAIL-0001.json")),
          "receipt exists unexpectedly")
    check("C8 no ledger rows for the failed fulfillment",
          ledger_hook.read_rows(pipeline.ledger_path) == [],
          "rows found")

    print("\n[scenario] C+: manual-queue order aged past the 24h cap")
    aged = pipeline.run_once(now=utc_now() + timedelta(hours=25))
    check("C9 aged manual-queue order is refund-flagged",
          aged.refunds_flagged == 1, "refunds=%d" % aged.refunds_flagged)
    check("C10 zero further attempts after the flag", aged.attempts == 0,
          "attempts=%d" % aged.attempts)
    stored = pipeline.store.get("MO-FAIL-0001")
    check("C11 status=refund_flagged with reason",
          stored.status == STATUS_REFUND
          and "auto_refund" in stored.refund_reason,
          "%s / %s" % (stored.status, stored.refund_reason))


def scenario_d(base: str) -> None:
    print("\n[scenario] D: rights-first refund on a stale pending order")
    state = os.path.join(base, "d_rights_first")
    stale = Order("MO-STALE-0001", "PIXEL-PET-BLUE", 1,
                  created_at=iso(utc_now() - timedelta(hours=25)))
    pipeline = build_pipeline(state, [stale])  # healthy generator
    report = pipeline.run_once()
    check("D1 stale pending order is refund-flagged at intake",
          report.refunds_flagged == 1,
          "refunds=%d" % report.refunds_flagged)
    check("D2 zero compute spent on the stale order (refund first)",
          report.attempts == 0 and report.delivered == 0,
          "attempts=%d delivered=%d"
          % (report.attempts, report.delivered))
    stored = pipeline.store.get("MO-STALE-0001")
    check("D3 status=refund_flagged", stored.status == STATUS_REFUND,
          stored.status)
    check("D4 no receipt for the refunded order",
          not os.path.exists(os.path.join(state, "deliveries",
                                          "MO-STALE-0001.json")),
          "receipt exists unexpectedly")


def scenario_e(base: str) -> None:
    print("\n[scenario] E: unconfirmed orders never enter fulfillment")
    state = os.path.join(base, "e_confirm_gate")
    unpaid = Order("MO-UNPAID-0001", "PIXEL-BADGE-GOLD", 1,
                   created_at=iso(utc_now()), confirm_flag=False)
    pipeline = build_pipeline(state, [unpaid])
    report = pipeline.run_once()
    check("E1 unconfirmed order is registered", report.new_orders == 1,
          "new_orders=%d" % report.new_orders)
    check("E2 unconfirmed order consumes zero compute",
          report.attempts == 0 and report.delivered == 0,
          "attempts=%d delivered=%d" % (report.attempts, report.delivered))
    stored = pipeline.store.get("MO-UNPAID-0001")
    check("E3 unconfirmed order stays pending",
          stored.status == STATUS_PENDING, stored.status)

    paid = Order("MO-UNPAID-0001", "PIXEL-BADGE-GOLD", 1,
                 created_at=unpaid.created_at, confirm_flag=True)
    pipeline2 = build_pipeline(state, [paid])
    report2 = pipeline2.run_once()
    check("E4 a later poll merges the payment confirmation",
          report2.confirm_updates == 1,
          "confirm_updates=%d" % report2.confirm_updates)
    stored2 = pipeline2.store.get("MO-UNPAID-0001")
    check("E5 the confirmed order then fulfills end-to-end",
          report2.delivered == 1 and stored2.status == STATUS_DONE,
          "delivered=%d status=%s" % (report2.delivered, stored2.status))


def scenario_g(base: str) -> None:
    print("\n[scenario] G: ledger hook idempotency units")
    ledger_path = os.path.join(base, "g_ledger_unit", "ledger.jsonl")
    order = Order("MO-LEDGER-0001", "PIXEL-PET-BLUE", 1)
    artifact = StubGenerator().generate(order)
    first = ledger_hook.record_fulfillment(ledger_path, order, artifact)
    second = ledger_hook.record_fulfillment(ledger_path, order, artifact)
    check("G1 first record writes 2 rows (tokens + cost)", first == 2,
          "written=%d" % first)
    check("G2 duplicate record writes 0 rows", second == 0,
          "written=%d" % second)
    dup = ledger_hook.append_row(ledger_path, order.order_id, order.sku,
                                 "tokens", "L1", 0, 0, "dup_probe", 0.0)
    check("G3 append_row dedups on (order_id, cost_item)", dup == 0,
          "written=%d" % dup)
    rows = ledger_hook.read_rows(ledger_path)
    check("G4 ledger still holds exactly 2 rows", len(rows) == 2,
          "rows=%d" % len(rows))


def main() -> int:
    print("=" * 72)
    print("fulfillment MVP skeleton - local end-to-end test")
    print("state root: temporary directory (cleaned up on exit)")
    print("=" * 72)
    base = tempfile.mkdtemp(prefix="fulfillment_test_")
    try:
        scenario_a(base)
        scenario_c(base)
        scenario_d(base)
        scenario_e(base)
        scenario_g(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    total = len(RESULTS)
    failures = [item for item in RESULTS if not item[1]]
    print("")
    print("=" * 72)
    print("detail: %d checks, %d failed" % (total, len(failures)))
    for name, ok, detail in RESULTS:
        if not ok:
            print("[FAIL] %s -- %s" % (name, detail))
    print("=" * 72)
    if failures:
        print("RESULT: FAIL (%d/%d checks failed)" % (len(failures), total))
        return 1
    print("RESULT: PASS (%d/%d checks passed)" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
