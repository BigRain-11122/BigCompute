#!/usr/bin/env python
"""Refund-rate back-calibration drill (M103).

The store-ledger predesign (docs/ops/store-ledger-wiring-predesign-v1.md S4)
says "if the actual refund rate deviates from the provisioned rate by more
than 2pp, next month must file a rate-adjustment proposal".  Until now that
sentence had no machine judgement behind it: nothing could answer "what is the
provision rate, what did we actually refund, and is the gap over the line".

This tool answers exactly that and nothing more:

  * provision = sum(price * refund_rate)   -- what we booked as a liability
  * actual    = sum(actual refund per order) -- what we really gave back
  * rates are both expressed on the same denominator (gross price), so the
    difference is directly in percentage points.

Three hard rules are baked in, each of which a naive implementation gets wrong:

  1. Threshold not set (None / 0 / negative / non-numeric) => CANNOT-JUDGE,
     never "calibrated".  An unset line must not read as a pass.
  2. Aftersale rows that cannot be matched to a revenue order are NOT silently
     dropped or treated as zero: they are reported as UNMATCHED and the verdict
     is degraded, because an unmatched refund means the denominator is wrong.
  3. Over the line the tool only emits a *proposal* (suggested rate = actual
     rate).  It never writes the rate back into any ledger -- changing a
     provision rate is a commercial decision, not a tool's call.

All money is handled in integer cents so the boundary case "exactly 2.00pp"
cannot flip on float noise.

Commands
  run     [--month YYYY-MM] [--revenue PATH] [--aftersale PATH]
          [--threshold-pp N] [--json-out PATH]
  drill   run against the built-in planted synthetic set (no IO to real rails)
  selftest
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "state")
REVENUE_RAIL = os.path.join(STATE, "cost-ledger.jsonl")
AFTERSALE_RAIL = os.path.join(STATE, "aftersale-ledger.jsonl")

# Predesign S4 line; a design value, not a commercial ruling.
DEFAULT_THRESHOLD_PP = 2.0

CAL_OK = "REFUND-CALIBRATED"
CAL_UNDER = "REFUND-UNDER-PROVISIONED"
CAL_OVER = "REFUND-OVER-PROVISIONED"
CAL_NO_DATA = "REFUND-NO-DATA"
CAL_CANNOT_JUDGE = "REFUND-CANNOT-JUDGE"
CAL_ROWS_BAD = "REFUND-ROWS-INCONSISTENT"


def _cents(value):
    """Money to integer cents.  Non-numeric => ValueError (caller judges)."""
    return int(round(float(value) * 100))


def _load_rows(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _month_of(row):
    date = str(row.get("date", ""))
    return date[:7] if len(date) >= 7 else "unknown"


def _is_mock(row):
    if row.get("mock") is True:
        return True
    if str(row.get("cost_item", "")).upper().startswith("MOCK"):
        return True
    return str(row.get("order_id", "")).upper().startswith("MOCK-")


def _threshold_or_none(value):
    """Return the threshold as float, or None when it is not a usable number.

    Zero and negative thresholds are rejected on purpose: a 0pp tolerance would
    make every month a violation, and a negative one would make none a
    violation -- both are data-entry errors, not calibration policies.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return num


def plan(revenue_rows, aftersale_rows, month=None, threshold_pp=None):
    """Pure function: provision vs actual, verdict, and an advisory proposal."""
    notes = []
    viol = []
    threshold = _threshold_or_none(threshold_pp)

    orders = {}
    order_seq = []
    for idx, row in enumerate(revenue_rows):
        if month and _month_of(row) != month:
            continue
        oid = str(row.get("order_id", "")).strip()
        if not oid:
            viol.append("row %d has no order_id" % idx)
            continue
        if oid in orders:
            viol.append("duplicate order_id %s at rows %d/%d"
                        % (oid, orders[oid]["idx"], idx))
            continue
        try:
            price_c = _cents(row.get("price", 0) or 0)
            rate = float(row.get("refund_rate", 0) or 0)
        except (TypeError, ValueError):
            viol.append("bad numeric on order %s" % oid)
            continue
        if price_c < 0:
            viol.append("negative price on order %s" % oid)
            continue
        if not (0.0 <= rate <= 1.0):
            viol.append("refund_rate out of [0,1] on order %s (%.4f)"
                        % (oid, rate))
            continue
        orders[oid] = {
            "idx": idx,
            "price_c": price_c,
            "rate": rate,
            "provision_c": int(round(price_c * rate)),
            "actual_c": 0,
            "matched_rows": 0,
        }
        order_seq.append(oid)

    unmatched = []
    for jdx, row in enumerate(aftersale_rows):
        if month and _month_of(row) != month:
            continue
        oid = str(row.get("order_id", "")).strip()
        if oid not in orders:
            unmatched.append({"row": jdx, "order_id": oid})
            continue
        try:
            refund_c = _cents(row.get("refund_amount", 0) or 0)
        except (TypeError, ValueError):
            viol.append("bad refund_amount on order %s" % oid)
            continue
        if refund_c < 0:
            viol.append("negative refund_amount on order %s" % oid)
            continue
        orders[oid]["actual_c"] += refund_c
        orders[oid]["matched_rows"] += 1

    for oid in order_seq:
        rec = orders[oid]
        if rec["actual_c"] > rec["price_c"]:
            viol.append("refund exceeds price on order %s (%d > %d cents)"
                        % (oid, rec["actual_c"], rec["price_c"]))

    gross_c = sum(orders[o]["price_c"] for o in order_seq)
    provision_c = sum(orders[o]["provision_c"] for o in order_seq)
    actual_c = sum(orders[o]["actual_c"] for o in order_seq)

    result = {
        "month": month,
        "orders": len(order_seq),
        "orders_with_aftersale": sum(1 for o in order_seq
                                     if orders[o]["matched_rows"]),
        "gross_cny": round(gross_c / 100.0, 4),
        "provision_cny": round(provision_c / 100.0, 4),
        "actual_refund_cny": round(actual_c / 100.0, 4),
        "provision_rate": None,
        "actual_rate": None,
        "deviation_pp": None,
        "threshold_pp": threshold,
        "unmatched_aftersale": unmatched,
        "violations": viol,
        "per_order": [],
        "proposal": None,
    }

    if viol and (gross_c <= 0 or any("exceeds price" in v for v in viol)):
        result["verdict"] = CAL_ROWS_BAD
        return result

    if gross_c <= 0:
        result["verdict"] = CAL_NO_DATA
        notes.append("no revenue rows for the selection")
        result["notes"] = notes
        return result

    prov_rate = provision_c / float(gross_c)
    act_rate = actual_c / float(gross_c)
    dev_pp = round((act_rate - prov_rate) * 100.0, 4)
    result["provision_rate"] = round(prov_rate, 6)
    result["actual_rate"] = round(act_rate, 6)
    result["deviation_pp"] = dev_pp

    for oid in order_seq:
        rec = orders[oid]
        if rec["price_c"] <= 0:
            continue
        result["per_order"].append({
            "order_id": oid,
            "provision_rate": round(rec["rate"], 6),
            "actual_rate": round(rec["actual_c"] / float(rec["price_c"]), 6),
            "deviation_pp": round(
                (rec["actual_c"] / float(rec["price_c"]) - rec["rate"]) * 100.0,
                4),
        })

    if unmatched:
        notes.append("%d aftersale row(s) reference unknown orders -- "
                     "denominator may be incomplete" % len(unmatched))

    if not aftersale_rows:
        # No aftersale rows at all is ambiguous: it can mean "nothing was
        # refunded" or "refunds are not recorded yet".  Reading the first
        # meaning would produce a confident over-provisioning verdict out of
        # missing data, so this refuses to judge instead.
        result["verdict"] = CAL_CANNOT_JUDGE
        notes.append("no aftersale rows: cannot distinguish 'no refunds' from "
                     "'refunds not recorded'")
        result["notes"] = notes
        return result

    if threshold is None:
        result["verdict"] = CAL_CANNOT_JUDGE
        notes.append("threshold not set (needs a positive number of pp)")
        result["notes"] = notes
        return result

    if abs(dev_pp) > threshold:
        result["verdict"] = CAL_UNDER if dev_pp > 0 else CAL_OVER
        result["proposal"] = {
            "action": "PROPOSE-RATE-CHANGE",
            "suggested_rate": round(act_rate, 6),
            "from_rate": round(prov_rate, 6),
            "deviation_pp": dev_pp,
            "threshold_pp": threshold,
            "note": "advisory only; changing the provision rate is a "
                    "commercial decision and is not applied here",
        }
    else:
        result["verdict"] = CAL_OK

    if unmatched:
        notes.append("verdict computed on matched orders only")
    result["notes"] = notes
    return result


def render(result):
    lines = []
    lines.append("month=%s orders=%d with_aftersale=%d mock_scope=%s"
                 % (result.get("month") or "all", result["orders"],
                    result["orders_with_aftersale"],
                    "n/a"))
    if result["provision_rate"] is None:
        lines.append("gross=%.4f provision=%.4f actual=%.4f"
                     % (result["gross_cny"], result["provision_cny"],
                        result["actual_refund_cny"]))
    else:
        lines.append("gross=%.4f provision=%.4f(%.4f%%) actual=%.4f(%.4f%%) "
                     "dev=%+.4fpp threshold=%spp"
                     % (result["gross_cny"], result["provision_cny"],
                        result["provision_rate"] * 100.0,
                        result["actual_refund_cny"],
                        result["actual_rate"] * 100.0,
                        result["deviation_pp"],
                        "%g" % result["threshold_pp"]
                        if result["threshold_pp"] is not None else "unset"))
    if result["per_order"]:
        worst = max(result["per_order"],
                    key=lambda r: abs(r["deviation_pp"]))
        lines.append("worst_order=%s dev=%+.2fpp"
                     % (worst["order_id"], worst["deviation_pp"]))
    for u in result["unmatched_aftersale"]:
        lines.append("UNMATCHED row=%d order_id=%s"
                     % (u["row"], u["order_id"] or "<blank>"))
    for v in result["violations"]:
        lines.append("VIOLATION %s" % v)
    for n in result.get("notes", []):
        lines.append("note: %s" % n)
    if result["proposal"]:
        p = result["proposal"]
        lines.append("PROPOSAL %s suggested=%.6f from=%.6f dev=%+.4fpp"
                     % (p["action"], p["suggested_rate"], p["from_rate"],
                        p["deviation_pp"]))
        lines.append("proposal_note: %s" % p["note"])
    lines.append("VERDICT=%s" % result["verdict"])
    return "\n".join(lines)


# ---------------------------------------------------------------- fixtures
# Planted synthetic set for `drill`.  Every row is annotated with the outcome
# the judge must produce; the drill's pass line is "detected == planted", not
# "no red anywhere" (the set deliberately contains a broken month).
def _drill_set():
    rev = [
        # month 2026-10: provision 5% on 100.00, actual 5.00 -> calibrated
        {"order_id": "DRILL-OK-1", "date": "2026-10-03", "price": 100.0,
         "refund_rate": 0.05},
        # 2026-11 calibrated group: 200.00 @5% =10.00 provision, actual 10.00
        {"order_id": "DRILL-OK-2", "date": "2026-11-02", "price": 120.0,
         "refund_rate": 0.05},
        {"order_id": "DRILL-OK-3", "date": "2026-11-05", "price": 80.0,
         "refund_rate": 0.05},
        # 2026-12 over-provisioned: provision 10% of 100 = 10.00, actual 5.00
        {"order_id": "DRILL-OVER-1", "date": "2026-12-01", "price": 100.0,
         "refund_rate": 0.10},
        # 2026-09 under-provisioned: provision 2% of 100 = 2.00, actual 6.00
        {"order_id": "DRILL-UNDER-1", "date": "2026-09-10", "price": 100.0,
         "refund_rate": 0.02},
        # 2027-01 exact boundary: 100.00 @5% = 5.00 provision, actual 7.00
        # -> +2.00pp exactly, must NOT be flagged
        {"order_id": "DRILL-EDGE-1", "date": "2027-01-10", "price": 100.0,
         "refund_rate": 0.05},
    ]
    aft = [
        {"order_id": "DRILL-OK-1", "date": "2026-10-20", "refund_amount": 5.0},
        {"order_id": "DRILL-OK-2", "date": "2026-11-09", "refund_amount": 6.0},
        {"order_id": "DRILL-OK-3", "date": "2026-11-15", "refund_amount": 4.0},
        {"order_id": "DRILL-OVER-1", "date": "2026-12-08", "refund_amount": 5.0},
        {"order_id": "DRILL-UNDER-1", "date": "2026-09-28", "refund_amount": 6.0},
        {"order_id": "DRILL-EDGE-1", "date": "2027-01-20", "refund_amount": 7.0},
    ]
    expected = {
        "2026-10": CAL_OK,
        "2026-11": CAL_OK,
        "2026-12": CAL_OVER,
        "2026-09": CAL_UNDER,
        "2027-01": CAL_OK,          # boundary inclusive
    }
    return rev, aft, expected


def drill(threshold_pp=DEFAULT_THRESHOLD_PP):
    rev, aft, expected = _drill_set()
    months = sorted(expected)
    rows = []
    mismatches = []
    for mon in months:
        res = plan(rev, aft, month=mon, threshold_pp=threshold_pp)
        want = expected[mon]
        rows.append({"month": mon, "verdict": res["verdict"],
                     "expected": want,
                     "deviation_pp": res["deviation_pp"],
                     "proposal": bool(res["proposal"])})
        if res["verdict"] != want:
            mismatches.append(mon)
    # the planted set must also surface an unmatched aftersale row
    extra = plan(rev, aft + [{"order_id": "DRILL-GHOST", "date": "2026-11-20",
                              "refund_amount": 1.0}],
                 month="2026-11", threshold_pp=threshold_pp)
    unmatched_ok = len(extra["unmatched_aftersale"]) == 1
    # unset threshold must never read as calibrated
    unset = plan(rev, aft, month="2026-11", threshold_pp=None)
    unset_ok = unset["verdict"] == CAL_CANNOT_JUDGE
    return {
        "threshold_pp": threshold_pp,
        "months": rows,
        "mismatches": mismatches,
        "unmatched_detected": unmatched_ok,
        "unset_threshold_verdict": unset["verdict"],
        "ok": not mismatches and unmatched_ok and unset_ok,
        "note": "planted synthetic set; the set contains broken months on "
                "purpose, so 'ok' means detected == planted",
    }


def _selftest():
    checks = []

    def ck(name, cond):
        checks.append((name, bool(cond)))

    rev, aft, _ = _drill_set()
    ok = plan(rev, aft, month="2026-11", threshold_pp=2.0)
    ck("S1 calibrated", ok["verdict"] == CAL_OK)
    ck("S2 provision math", ok["provision_cny"] == 10.0 and ok["actual_refund_cny"] == 10.0)
    ck("S3 rates on gross",
       ok["provision_rate"] == 0.05 and ok["actual_rate"] == 0.05)

    over = plan(rev, aft, month="2026-12", threshold_pp=2.0)
    ck("S4 over-provisioned", over["verdict"] == CAL_OVER)
    ck("S5 over emits proposal", over["proposal"] is not None)
    ck("S6 proposal suggests actual rate",
       over["proposal"]["suggested_rate"] == 0.05)

    under = plan(rev, aft, month="2026-09", threshold_pp=2.0)
    ck("S7 under-provisioned", under["verdict"] == CAL_UNDER)
    ck("S8 under dev signed", under["deviation_pp"] == 4.0)

    edge = plan(rev, aft, month="2027-01", threshold_pp=2.0)
    ck("S9 exact threshold not flagged",
       edge["deviation_pp"] == 2.0 and edge["verdict"] == CAL_OK)
    edge2 = plan(rev, aft, month="2027-01", threshold_pp=1.99)
    ck("S10 just below threshold flags", edge2["verdict"] == CAL_UNDER)

    for bad in (None, 0, -1, "abc", True):
        r = plan(rev, aft, month="2026-11", threshold_pp=bad)
        ck("S11 unset threshold cannot judge (%r)" % (bad,),
           r["verdict"] == CAL_CANNOT_JUDGE)

    empty = plan([], [], month="2026-11", threshold_pp=2.0)
    ck("S12 no data", empty["verdict"] == CAL_NO_DATA)

    unmatched = plan(rev, aft + [{"order_id": "GHOST", "date": "2026-11-20",
                                  "refund_amount": 1.0}],
                     month="2026-11", threshold_pp=2.0)
    ck("S13 unmatched surfaced", len(unmatched["unmatched_aftersale"]) == 1)
    ck("S14 unmatched degrades with note", any("unknown orders" in n
                                               for n in unmatched["notes"]))

    bad_rate = plan([{"order_id": "X", "date": "2026-11-01", "price": 10.0,
                      "refund_rate": 1.5}], [], month="2026-11",
                    threshold_pp=2.0)
    ck("S15 bad rate rejected", bad_rate["verdict"] == CAL_ROWS_BAD)

    over_refund = plan([{"order_id": "Y", "date": "2026-11-01", "price": 10.0,
                         "refund_rate": 0.1}],
                       [{"order_id": "Y", "date": "2026-11-02",
                         "refund_amount": 11.0}],
                       month="2026-11", threshold_pp=2.0)
    ck("S16 refund above price rejected", over_refund["verdict"] == CAL_ROWS_BAD)

    dup = plan([{"order_id": "Z", "date": "2026-11-01", "price": 10.0,
                 "refund_rate": 0.1},
                {"order_id": "Z", "date": "2026-11-01", "price": 10.0,
                 "refund_rate": 0.1}], [], month="2026-11", threshold_pp=2.0)
    ck("S17 duplicate order flagged", any("duplicate order_id" in v
                                          for v in dup["violations"]))
    ck("S18 duplicate counted once", dup["orders"] == 1)

    multi = plan([{"order_id": "M", "date": "2026-11-01", "price": 100.0,
                   "refund_rate": 0.05}],
                 [{"order_id": "M", "date": "2026-11-05", "refund_amount": 2.0},
                  {"order_id": "M", "date": "2026-11-09",
                   "refund_amount": 3.0}],
                 month="2026-11", threshold_pp=2.0)
    ck("S19 multiple aftersale rows summed",
       multi["actual_refund_cny"] == 5.0 and multi["verdict"] == CAL_OK)

    cents = plan([{"order_id": "C", "date": "2026-11-01", "price": 33.33,
                   "refund_rate": 0.03}],
                 [{"order_id": "C", "date": "2026-11-05",
                   "refund_amount": 1.0}],
                 month="2026-11", threshold_pp=2.0)
    ck("S20 cents arithmetic stable",
       cents["provision_cny"] == 1.0 and cents["actual_refund_cny"] == 1.0)

    no_aftersale = plan([{"order_id": "N", "date": "2026-11-01",
                          "price": 100.0, "refund_rate": 0.05}],
                        [], month="2026-11", threshold_pp=2.0)
    ck("S21 no aftersale rows refuses to judge",
       no_aftersale["verdict"] == CAL_CANNOT_JUDGE
       and any("not recorded" in n for n in no_aftersale["notes"]))

    same = plan(rev, aft, month="2026-12", threshold_pp=2.0)
    ck("S22 deterministic",
       same == plan(rev, aft, month="2026-12", threshold_pp=2.0))

    d = drill()
    ck("S23 drill detected==planted", d["ok"])
    ck("S24 drill has no mismatches", d["mismatches"] == [])
    ck("S25 drill unset threshold cannot judge",
       d["unset_threshold_verdict"] == CAL_CANNOT_JUDGE)

    failed = [n for n, c in checks if not c]
    total = len(checks)
    print("SELFTEST %d/%d PASS" % (total - len(failed), total))
    for n, c in checks:
        print("  %-45s %s" % (n, "PASS" if c else "FAIL"))
    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    return 0


def _cmd_run(args):
    rev = _load_rows(args.revenue)
    aft = _load_rows(args.aftersale)
    if not rev:
        print("revenue_rail=%s rows=0 (plan-state, nothing booked yet)"
              % args.revenue)
    if not aft:
        print("aftersale_rail=%s rows=0" % args.aftersale)
    res = plan(rev, aft, month=args.month, threshold_pp=args.threshold_pp)
    res["sources"] = {"revenue": args.revenue, "aftersale": args.aftersale,
                      "revenue_rows": len(rev), "aftersale_rows": len(aft)}
    text = render(res)
    print(text)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
    return 0 if res["verdict"] in (CAL_OK, CAL_UNDER, CAL_OVER) else 2


def _cmd_drill(args):
    res = drill(args.threshold_pp)
    for row in res["months"]:
        print("month=%s verdict=%s expected=%s dev=%s proposal=%s"
              % (row["month"], row["verdict"], row["expected"],
                 row["deviation_pp"], row["proposal"]))
    print("unmatched_detected=%s unset_threshold=%s"
          % (res["unmatched_detected"], res["unset_threshold_verdict"]))
    print("note: %s" % res["note"])
    print("REFUND-CALIBRATION-DRILL-%s" % ("OK" if res["ok"] else "MISMATCH"))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
    return 0 if res["ok"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="refund rate calibration drill")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="calibrate against rails")
    p_run.add_argument("--month", default=None)
    p_run.add_argument("--revenue", default=REVENUE_RAIL)
    p_run.add_argument("--aftersale", default=AFTERSALE_RAIL)
    p_run.add_argument("--threshold-pp", type=float,
                       default=DEFAULT_THRESHOLD_PP)
    p_run.add_argument("--json-out", default=None)
    p_run.set_defaults(func=_cmd_run)

    p_drill = sub.add_parser("drill", help="planted synthetic set")
    p_drill.add_argument("--threshold-pp", type=float,
                         default=DEFAULT_THRESHOLD_PP)
    p_drill.add_argument("--json-out", default=None)
    p_drill.set_defaults(func=_cmd_drill)

    p_self = sub.add_parser("selftest", help="run built-in assertions")
    p_self.set_defaults(func=lambda a: _selftest())

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())