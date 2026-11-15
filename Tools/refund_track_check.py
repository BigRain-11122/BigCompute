#!/usr/bin/env python
"""Refund/revenue track separation check (queue M151).

store-ledger-wiring-predesign-v1 S4/S5 fixes two things in prose: refunds are
booked as their own reconciliation rows keyed by the aftersale number and never
written over the revenue rows, and the revenue track must not absorb fulfillment
(cost_item) rows.  Two sibling tools already exist and this one deliberately
does not repeat them:

  * refund_rate_calibration.py  -- provision vs actual rate, 2pp line, proposal
  * quota_prepaid_liability.py  -- list-price vs cost-basis refund shortfall

What was missing is the *placement* question: given the two ledger files, is a
row sitting in the wrong track, refunded twice, refunded without a matching
order, or refunded for more than was paid?  This tool answers only that.

Money is compared in integer cents so "refunded exactly the paid amount" cannot
flip to OVER-REFUND on float noise.

Honest-empty rule: with no ledger rows at all (the current pre-opening state)
the verdict is NO-DATA and no zero is printed as if it were a reading.  An
unreadable line degrades the verdict instead of being silently dropped, because
a dropped row moves both the numerator and the denominator.

Commands
  run [--revenue PATH] [--refund PATH] [--json]   check the two tracks
  selftest                                        fixture suite (no file writes)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVENUE_DEFAULT = ROOT / "state" / "cost-ledger.jsonl"
REFUND_DEFAULT = ROOT / "state" / "refund-ledger.jsonl"

# Finding codes. Each is a placement defect, not a rate judgement.
F_NO_DATA = "NO-DATA"
F_UNREADABLE = "UNREADABLE-LINE"
F_CROSSED_REV = "TRACK-CROSSED-REVENUE"      # refund row inside the revenue track
F_CROSSED_REF = "TRACK-CROSSED-REFUND"       # revenue row inside the refund track
F_COST_IN_REV = "REVENUE-TRACK-HAS-COST-ROW"  # fulfillment row inside revenue track
F_DUP_REFUND = "DUPLICATE-REFUND"
F_UNMATCHED = "UNMATCHED-REFUND"
F_OVER = "OVER-REFUND"
F_NEGATIVE = "NEGATIVE-REFUND"
F_COST_BASIS = "COST-BASIS-REFUND"

SEVERITY = {
    F_NO_DATA: "info",
    F_UNREADABLE: "high",
    F_CROSSED_REV: "high",
    F_CROSSED_REF: "high",
    F_COST_IN_REV: "high",
    F_DUP_REFUND: "high",
    F_UNMATCHED: "high",
    F_OVER: "high",
    F_NEGATIVE: "high",
    F_COST_BASIS: "med",
}


def _cents(value):
    """String/number money -> integer cents, None when absent or non-numeric."""
    if value is None or value == "":
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _load(path):
    """Return (rows, unreadable). A missing file is empty, not an error.

    utf-8-sig on purpose: a ledger exported by a Windows tool can carry a BOM,
    and a BOM must not turn every row into UNREADABLE-LINE (that would read as a
    placement defect when it is only an encoding artifact).
    """
    p = Path(path)
    if not p.exists():
        return [], 0
    rows, bad = [], 0
    with p.open("r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                bad += 1
    return rows, bad


def _has(row, key):
    v = row.get(key)
    return v is not None and v != ""


def check(revenue_rows, refund_rows, revenue_bad=0, refund_bad=0):
    """Pure judgement: two row lists in, findings out. No IO."""
    findings = []

    def add(code, where, detail, ref=None):
        findings.append(
            {"code": code, "severity": SEVERITY[code], "track": where, "detail": detail, "ref": ref}
        )

    for i in range(revenue_bad):
        add(F_UNREADABLE, "revenue", "revenue ledger line is not valid JSON", f"line#{i + 1}")
    for i in range(refund_bad):
        add(F_UNREADABLE, "refund", "refund ledger line is not valid JSON", f"line#{i + 1}")

    revenue_by_order = {}
    for row in revenue_rows:
        oid = row.get("order_id")
        if _has(row, "refund_id"):
            add(F_CROSSED_REV, "revenue",
                "row carries a refund_id: refunds belong to the refund track",
                row.get("refund_id"))
        if _has(row, "cost_item"):
            add(F_COST_IN_REV, "revenue",
                "row carries cost_item: fulfillment rows belong to the pipeline ledger",
                oid)
        if _has(row, "order_id"):
            revenue_by_order.setdefault(str(oid), _cents(row.get("amount")) or _cents(row.get("price")))

    seen_refund = {}
    for row in refund_rows:
        rid = row.get("refund_id")
        if not _has(row, "refund_id"):
            add(F_CROSSED_REF, "refund",
                "row has no refund_id: a revenue/booking row cannot live in the refund track",
                row.get("order_id"))
            continue
        rid = str(rid)
        seen_refund[rid] = seen_refund.get(rid, 0) + 1

        amt = _cents(row.get("refund_amount"))
        if amt is None:
            add(F_UNREADABLE, "refund", "refund_amount missing or non-numeric", rid)
        elif amt < 0:
            add(F_NEGATIVE, "refund", "negative refund amount", rid)

        oid = row.get("order_id")
        if not _has(row, "order_id") or str(oid) not in revenue_by_order:
            add(F_UNMATCHED, "refund",
                "refund cannot be matched to a revenue order (denominator wrong)",
                rid)
        else:
            paid = _cents(row.get("amount_paid"))
            if paid is None:
                paid = revenue_by_order[str(oid)]
            if amt is not None and paid is not None and amt > paid:
                add(F_OVER, "refund",
                    f"refund {amt} cents exceeds paid {paid} cents", rid)

        basis = str(row.get("basis") or "").strip().lower()
        if basis in ("cost", "cost_b", "cost-b"):
            add(F_COST_BASIS, "refund",
                "refund booked on internal cost basis: systematic shortfall to the buyer",
                rid)

    for rid, n in seen_refund.items():
        if n > 1:
            add(F_DUP_REFUND, "refund", f"refund_id appears {n} times", rid)

    total = len(revenue_rows) + len(refund_rows)
    if total == 0 and revenue_bad == 0 and refund_bad == 0:
        add(F_NO_DATA, "both", "no ledger rows: pre-opening state, nothing to judge")

    reds = [f for f in findings if f["severity"] == "high"]
    if reds:
        verdict = "REFUND-TRACK-RED"
    elif any(f["severity"] == "med" for f in findings):
        verdict = "REFUND-TRACK-AMBER"
    elif total == 0:
        verdict = "NO-DATA"
    else:
        verdict = "REFUND-TRACK-GREEN"

    return {
        "verdict": verdict,
        "revenue_rows": len(revenue_rows),
        "refund_rows": len(refund_rows),
        "findings": findings,
        "high": len(reds),
    }


def render(rep):
    out = []
    out.append(f"verdict={rep['verdict']} revenue_rows={rep['revenue_rows']} "
               f"refund_rows={rep['refund_rows']} high={rep['high']}")
    for f in rep["findings"]:
        ref = f" ref={f['ref']}" if f.get("ref") else ""
        out.append(f"  [{f['severity']}] {f['code']} track={f['track']} :: {f['detail']}{ref}")
    if not rep["findings"]:
        out.append("  (no findings)")
    return "\n".join(out)


def _row(**kw):
    return kw


def _drill_sets():
    """(name, revenue, refund, rev_bad, ref_bad, expected_codes)"""
    rev_ok = [_row(order_id="O1", price="19.9", amount="17.3926", sku="QT")]
    ref_ok = [_row(refund_id="R1", order_id="O1", refund_amount="19.90",
                   amount_paid="19.90", basis="list_price")]
    return [
        ("clean-match", rev_ok, ref_ok, 0, 0, []),
        ("crossed-revenue", [_row(order_id="O1", refund_id="R9", amount="10")], [], 0, 0,
         [F_CROSSED_REV]),
        ("crossed-refund", [], [_row(order_id="O1", amount="19.9")], 0, 0,
         [F_CROSSED_REF]),
        ("cost-row-in-revenue", [_row(order_id="O1", cost_item="tts", amount="-1")], [], 0, 0,
         [F_COST_IN_REV]),
        ("duplicate-refund", rev_ok, ref_ok + [dict(ref_ok[0])], 0, 0, [F_DUP_REFUND]),
        ("unmatched-refund", [], [ref_ok[0]], 0, 0, [F_UNMATCHED]),
        ("over-refund", rev_ok,
         [_row(refund_id="R1", order_id="O1", refund_amount="25.00", amount_paid="19.90")],
         0, 0, [F_OVER]),
        ("cost-basis", rev_ok,
         [_row(refund_id="R1", order_id="O1", refund_amount="0.10",
               amount_paid="19.90", basis="cost")],
         0, 0, [F_COST_BASIS]),
        ("empty-books", [], [], 0, 0, [F_NO_DATA]),
        ("exact-paid-boundary", rev_ok,
         [_row(refund_id="R1", order_id="O1", refund_amount="19.90", amount_paid="19.90")],
         0, 0, []),
        ("unreadable-line", [], [], 0, 1, [F_UNREADABLE]),
        ("negative-refund", rev_ok,
         [_row(refund_id="R1", order_id="O1", refund_amount="-5.00", amount_paid="19.90")],
         0, 0, [F_NEGATIVE]),
    ]


def selftest():
    checks, failed = 0, 0

    def expect(cond, label):
        nonlocal checks, failed
        checks += 1
        if not cond:
            failed += 1
            print(f"  FAIL {label}")

    for name, rev, ref, rbad, fbad, want in _drill_sets():
        rep = check(rev, ref, rbad, fbad)
        got = {f["code"] for f in rep["findings"]}
        expect(got == set(want), f"{name}: codes {sorted(got)} != {sorted(want)}")
        if F_NO_DATA in want:
            expect(rep["verdict"] == "NO-DATA", f"{name}: verdict {rep['verdict']}")
        if want and F_NO_DATA not in want:
            expect(rep["verdict"] in ("REFUND-TRACK-RED", "REFUND-TRACK-AMBER"),
                   f"{name}: verdict {rep['verdict']}")
        if not want:
            expect(rep["verdict"] == "REFUND-TRACK-GREEN", f"{name}: verdict {rep['verdict']}")

    # Parity: cents must survive a float-noise amount.
    expect(_cents("19.90") == 1990, "cents 19.90")
    expect(_cents(0.1 + 0.2) == 30, "cents float-noise 0.3")
    expect(_cents("abc") is None, "cents non-numeric")
    expect(_cents(None) is None, "cents absent")

    # BOM fixture: a Windows-exported ledger must not read as unreadable rows.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bom.jsonl"
        p.write_bytes(b"\xef\xbb\xbf" + b'{"order_id":"O1","amount":"19.9"}\n')
        rows, bad = _load(p)
        expect(len(rows) == 1 and bad == 0, f"bom ledger rows={len(rows)} bad={bad}")

    print(f"SELFTEST {'PASS' if failed == 0 else 'FAIL'} {checks - failed}/{checks}")
    return 0 if failed == 0 else 1


def cmd_run(args):
    rev, rbad = _load(args.revenue)
    ref, fbad = _load(args.refund)
    rep = check(rev, ref, rbad, fbad)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render(rep))
    return 0 if rep["verdict"] in ("REFUND-TRACK-GREEN", "NO-DATA") else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="BigCompute refund/revenue track separation check")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="check the two ledger tracks")
    r.add_argument("--revenue", default=str(REVENUE_DEFAULT))
    r.add_argument("--refund", default=str(REFUND_DEFAULT))
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("selftest", help="fixture suite")
    s.set_defaults(func=lambda a: selftest())

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
