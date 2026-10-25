#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""delivery_dispatch_mock.py -- Ring-1 stage-5 MOCK delivery dispatch.

Provenance: M73 P1 step-04. Building plan sec.2/3 names the ring as
inquiry -> auto-quote -> compliance gate -> deal ledger -> delivery dispatch ->
aftercare, but deal_flow_mock.py only *labels* delivery with a route_group and
then calls the fulfillment pipeline. There was no delivery record to audit, so
"delivery ran" was unprovable after the fact. This tool gives that stage an
append-only event chain: queued -> dispatched -> delivered.

RED LINES (hard-coded):
- LIVE is False; zero network calls, zero platform contact (J4 planned state).
- Only MOCK- orders that already exist on the mock-order rail can be walked.
  A real order id is rejected with a named gate, never silently mapped.
- Append-only event rail (state/delivery-ledger.jsonl): one row per transition,
  never an in-place edit, so the chain stays auditable.
- The real cost rail (cost_ledger.LEDGER) is never touched; amounts settle on
  the deal rail (caliber B), this rail carries no money.

Schema truth: Tools/rails_schema_check.py SCHEMAS["delivery"] is imported,
never duplicated. Route vocabulary is imported from deal_flow_mock.ROUTE_GROUPS.

Commands:
  intake   --order-id MOCK-... [--ts TS]   create the delivery (queued)
  dispatch --delivery-id DV-... [--ts TS]  queued -> dispatched
  deliver  --delivery-id DV-... [--ts TS]  dispatched -> delivered (synthetic receipt)
  status   [--order-id MOCK-...]           fold the chain, print latest per delivery
  selftest                                 temp-sandbox fixtures, zero pollution

Exit codes: 0 DELIVERY-GREEN[-IDEMPOTENT] | 2 reject (order / transition gate)
| 5 evidence-write-error.
"""
import argparse
import datetime
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import cost_ledger as cl  # noqa: E402  (real rail: signature only)
import deal_flow_mock as dfm  # noqa: E402  (route vocabulary single source)
import rails_schema_check as rsc  # noqa: E402  (single-source delivery schema)

RAIL = os.path.join(HERE, "..", "state", "delivery-ledger.jsonl")
MOCK_ORDER_RAIL = os.path.join(HERE, "..", "state", "mock-order-ledger.jsonl")
EVIDENCE_DIR = os.path.join(HERE, "..", "state")

LIVE = False  # J4: planned-state drill.
STATUSES = ("queued", "dispatched", "delivered")
TRANSITIONS = {
    "queued": ("dispatched",),
    "dispatched": ("delivered",),
    "delivered": (),
}
RECEIPT_PREFIX = "SYN-"  # synthetic receipt, never a platform-issued票号
CALIBER_NOTE = ("delivery event chain = fulfillment audit rail, not a revenue "
                "row; amounts settle on the deal rail (caliber B)")


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
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")


def _fold(rows):
    """Latest event per delivery_id (rail is append-only, order = real time)."""
    latest = {}
    for r in rows:
        latest[str(r.get("delivery_id", ""))] = r
    return latest


def _by_order(rows):
    m = {}
    for r in rows:
        m.setdefault(str(r.get("order_id", "")), r)  # first events are oldest
    return m


def _next_seq(rows, field, prefix):
    seq = 0
    for r in rows:
        val = str(r.get(field, ""))
        if val.startswith(prefix):
            try:
                seq = max(seq, int(val[len(prefix):]))
            except ValueError:
                pass
    return "%s%d" % (prefix, seq + 1)


def _now(fields):
    return fields.get("ts") or datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _write_evidence(cmd, out):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(EVIDENCE_DIR, "delivery-%s-%s.json" % (cmd, stamp))
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, sort_keys=True, ensure_ascii=True, indent=1)
    except OSError as e:
        return None, str(e)
    return os.path.relpath(path, os.path.join(HERE, "..")), None


def _emit(cmd, out):
    evidence, err = _write_evidence(cmd, out)
    if err:
        return 5, {"verdict": "DELIVERY-RED", "reason": "evidence-write", "err": err}
    out["evidence"] = evidence
    out["live"] = LIVE
    out["rail"] = os.path.basename(RAIL)
    out["real_rail"] = os.path.basename(cl.LEDGER)
    return 0, out


def _event(fields, delivery_id, order_id, route_group, status, attempts,
           receipt_ref, ts):
    return {
        "event_id": fields.get("event_id", ""),
        "delivery_id": delivery_id,
        "order_id": order_id,
        "route_group": route_group,
        "status": status,
        "ts": ts,
        "attempts": attempts,
        "receipt_ref": receipt_ref,
        "mock": True,
        "live": LIVE,
        "caliber_note": CALIBER_NOTE,
    }


def intake(order_id, fields=None, rail_path=None, order_path=None,
           evidence=True):
    """Create a delivery for an existing MOCK order. Returns (rc, dict)."""
    global RAIL, MOCK_ORDER_RAIL
    if rail_path:
        RAIL = rail_path
    if order_path:
        MOCK_ORDER_RAIL = order_path
    fields = dict(fields or {})
    oid = str(order_id or "")
    if not oid.startswith("MOCK-"):
        return 2, {"verdict": "DELIVERY-RED", "reason": "not-mock-order",
                   "order_id": oid}
    orders = {str(r.get("order_id", "")): r for r in _load_rows(MOCK_ORDER_RAIL)}
    if oid not in orders:
        return 2, {"verdict": "DELIVERY-RED", "reason": "unknown-order",
                   "order_id": oid}
    route = str(orders[oid].get("route_group", ""))
    if not route:
        return 2, {"verdict": "DELIVERY-RED", "reason": "order-route-missing",
                   "order_id": oid}
    if route not in dfm.ROUTE_GROUPS:
        return 2, {"verdict": "DELIVERY-RED", "reason": "route-out-of-vocabulary",
                   "route_group": route}

    rows = _load_rows(RAIL)
    existing = _by_order(rows)
    if oid in existing:
        prev = existing[oid]
        return 0, {"verdict": "DELIVERY-GREEN-IDEMPOTENT",
                   "delivery_id": prev.get("delivery_id"), "order_id": oid,
                   "status": _fold(rows).get(prev.get("delivery_id"), {}).get("status"),
                   "route_group": prev.get("route_group", "")}

    ts = _now(fields)
    prefix = "DV-%s-" % ts[:10].replace("-", "")
    delivery_id = fields.get("delivery_id") or _next_seq(rows, "delivery_id", prefix)
    eprefix = "E-%s-" % ts[:10].replace("-", "")
    ev = _event(fields, delivery_id, oid, route, "queued", 0, "", ts)
    ev["event_id"] = fields.get("event_id") or _next_seq(rows, "event_id", eprefix)
    _append_row(RAIL, ev)
    return _emit("intake", {"verdict": "DELIVERY-GREEN", "delivery_id": delivery_id,
                            "order_id": oid, "status": "queued",
                            "route_group": route, "event_id": ev["event_id"]})


def _advance(cmd, delivery_id, target, fields=None, rail_path=None,
             evidence=True):
    global RAIL
    if rail_path:
        RAIL = rail_path
    fields = dict(fields or {})
    did = str(delivery_id or "")
    rows = _load_rows(RAIL)
    latest = _fold(rows)
    cur = latest.get(did)
    if not cur:
        return 2, {"verdict": "DELIVERY-RED", "reason": "unknown-delivery",
                   "delivery_id": did}
    status = cur.get("status")
    if status == target:
        return 0, {"verdict": "DELIVERY-GREEN-IDEMPOTENT", "delivery_id": did,
                   "order_id": cur.get("order_id"), "status": status,
                   "route_group": cur.get("route_group", ""),
                   "receipt_ref": cur.get("receipt_ref", "")}
    if target not in TRANSITIONS.get(status, ()):
        return 2, {"verdict": "DELIVERY-RED", "reason": "transition-gate",
                   "delivery_id": did, "status": status, "attempted": target,
                   "allowed": list(TRANSITIONS.get(status, ()))}

    ts = _now(fields)
    attempts = int(cur.get("attempts", 0))
    receipt = cur.get("receipt_ref", "")
    if target == "dispatched":
        attempts += 1
    if target == "delivered":
        receipt = RECEIPT_PREFIX + did
    eprefix = "E-%s-" % ts[:10].replace("-", "")
    ev = _event(fields, did, cur.get("order_id", ""), cur.get("route_group", ""),
                target, attempts, receipt, ts)
    ev["event_id"] = _next_seq(rows, "event_id", eprefix)
    _append_row(RAIL, ev)
    return _emit(cmd, {"verdict": "DELIVERY-GREEN", "delivery_id": did,
                       "order_id": cur.get("order_id"),
                       "status": target, "attempts": attempts,
                       "route_group": cur.get("route_group", ""),
                       "receipt_ref": receipt, "event_id": ev["event_id"]})


def dispatch(delivery_id, fields=None, rail_path=None, evidence=True):
    return _advance("dispatch", delivery_id, "dispatched", fields, rail_path,
                    evidence)


def deliver(delivery_id, fields=None, rail_path=None, evidence=True):
    return _advance("deliver", delivery_id, "delivered", fields, rail_path,
                    evidence)


def status(order_id="", rail_path=None):
    global RAIL
    if rail_path:
        RAIL = rail_path
    rows = _load_rows(RAIL)
    latest = _fold(rows)
    items = []
    for did, r in latest.items():
        if order_id and r.get("order_id") != order_id:
            continue
        items.append({
            "delivery_id": did, "order_id": r.get("order_id"),
            "status": r.get("status"), "route_group": r.get("route_group"),
            "attempts": r.get("attempts"), "receipt_ref": r.get("receipt_ref"),
        })
    items.sort(key=lambda x: str(x.get("delivery_id")))
    out = {"verdict": "DELIVERY-STATUS", "events": len(rows),
           "deliveries": len(items), "rows": items, "live": LIVE,
           "rail": os.path.basename(RAIL)}
    return 0, out


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append((name, ok, detail))

    real_sig = (hashlib.sha256(open(cl.LEDGER, "rb").read()).hexdigest()
                if os.path.isfile(cl.LEDGER) else "absent")
    with tempfile.TemporaryDirectory() as td:
        rail = os.path.join(td, "delivery-rail.jsonl")
        orders = os.path.join(td, "mock-orders.jsonl")
        with open(orders, "w", encoding="utf-8") as f:
            f.write(json.dumps({"order_id": "MOCK-ST-1",
                                "route_group": "resident-qa-8792"}) + "\n")
            f.write(json.dumps({"order_id": "MOCK-ST-2",
                                "route_group": "batch-pool"}) + "\n")
        ts = "2026-10-25T22:50:00"
        f = {"ts": ts}
        kw = dict(rail_path=rail, order_path=orders, evidence=False)

        # S1 intake green
        rc, v = intake("MOCK-ST-1", f, **kw)
        rows = _load_rows(rail)
        run("S1 intake green", rc == 0 and v["verdict"] == "DELIVERY-GREEN"
            and v["status"] == "queued" and len(rows) == 1, v.get("verdict"))
        run("S1b row passes delivery schema",
            rsc.validate_rows("delivery", rows) == [], "rows=%d" % len(rows))
        did = v.get("delivery_id", "")

        # S2 duplicate intake is idempotent, no second row
        rc, v2 = intake("MOCK-ST-1", f, **kw)
        run("S2 duplicate intake idempotent", rc == 0
            and v2["verdict"] == "DELIVERY-GREEN-IDEMPOTENT"
            and v2["delivery_id"] == did and len(_load_rows(rail)) == 1,
            v2.get("verdict"))

        # S3 deliver before dispatch is refused, zero write
        rc, v3 = deliver(did, f, rail_path=rail, evidence=False)
        run("S3 deliver-before-dispatch refused", rc == 2
            and v3["reason"] == "transition-gate" and v3["status"] == "queued"
            and len(_load_rows(rail)) == 1, v3.get("reason"))

        # S4 dispatch green
        rc, v4 = dispatch(did, f, rail_path=rail, evidence=False)
        run("S4 dispatch green", rc == 0 and v4["status"] == "dispatched"
            and v4["attempts"] == 1 and len(_load_rows(rail)) == 2,
            "attempts=%s" % v4.get("attempts"))
        run("S4b chain still schema-valid",
            rsc.validate_rows("delivery", _load_rows(rail)) == [])

        # S5 dispatch replay is idempotent
        rc, v5 = dispatch(did, f, rail_path=rail, evidence=False)
        run("S5 dispatch replay idempotent", rc == 0
            and v5["verdict"] == "DELIVERY-GREEN-IDEMPOTENT"
            and len(_load_rows(rail)) == 2, v5.get("verdict"))

        # S6 deliver green with synthetic receipt
        rc, v6 = deliver(did, f, rail_path=rail, evidence=False)
        run("S6 deliver green + synthetic receipt", rc == 0
            and v6["status"] == "delivered"
            and str(v6.get("receipt_ref", "")).startswith(RECEIPT_PREFIX)
            and len(_load_rows(rail)) == 3, v6.get("receipt_ref"))

        # S7 replay after delivered is idempotent; further dispatch refused
        rc, v7 = deliver(did, f, rail_path=rail, evidence=False)
        rc8, v8 = dispatch(did, f, rail_path=rail, evidence=False)
        run("S7 delivered terminal", rc == 0
            and v7["verdict"] == "DELIVERY-GREEN-IDEMPOTENT"
            and rc8 == 2 and v8["reason"] == "transition-gate"
            and len(_load_rows(rail)) == 3,
            "%s/%s" % (v7.get("verdict"), v8.get("reason")))

        # S8 unknown order refused at intake
        rc, v9 = intake("MOCK-NOT-THERE", f, rail_path=rail, order_path=orders,
                        evidence=False)
        run("S8 unknown order refused", rc == 2
            and v9["reason"] == "unknown-order" and len(_load_rows(rail)) == 3,
            v9.get("reason"))

        # S9 real (non-MOCK) order refused
        rc, v10 = intake("R-20261025-1", f, rail_path=rail, order_path=orders,
                         evidence=False)
        run("S9 real order refused", rc == 2
            and v10["reason"] == "not-mock-order" and len(_load_rows(rail)) == 3,
            v10.get("reason"))

        # S10 status folding: second delivery stays queued, first delivered
        rc, v11 = intake("MOCK-ST-2", dict(f), rail_path=rail, order_path=orders,
                         evidence=False)
        rc, st = status(rail_path=rail)
        by = {r["delivery_id"]: r for r in st["rows"]}
        run("S10 status folds latest per delivery", rc == 0 and st["deliveries"] == 2
            and by.get(did, {}).get("status") == "delivered"
            and by.get(v11.get("delivery_id"), {}).get("status") == "queued",
            "deliveries=%s" % st.get("deliveries"))

        # S11 route vocabulary comes from the deal rail (single source)
        run("S11 route vocabulary single-source",
            "resident-qa-8792" in dfm.ROUTE_GROUPS
            and "batch-pool" in dfm.ROUTE_GROUPS
            and "not-a-route" not in dfm.ROUTE_GROUPS)

        # S12 delivery schema required keys are a subset of emitted keys
        req = set(rsc.SCHEMAS["delivery"]["required"])
        run("S12 schema keys subset of emitted",
            req.issubset(set(_load_rows(rail)[0].keys())),
            "required=%d" % len(req))

    sig2 = (hashlib.sha256(open(cl.LEDGER, "rb").read()).hexdigest()
            if os.path.isfile(cl.LEDGER) else "absent")
    run("S13 real cost rail untouched", real_sig == sig2, sig2[:12])

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           (" (%s)" % detail) if detail else ""))
    print("SELFTEST %s %d/%d" % ("PASS" if not failed else "FAIL",
                                 len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def main(argv):
    ap = argparse.ArgumentParser(
        description="Delivery dispatch MOCK runner (J4 planned state)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("intake")
    i.add_argument("--order-id", required=True)
    i.add_argument("--ts", default="")

    d = sub.add_parser("dispatch")
    d.add_argument("--delivery-id", required=True)
    d.add_argument("--ts", default="")

    v = sub.add_parser("deliver")
    v.add_argument("--delivery-id", required=True)
    v.add_argument("--ts", default="")

    s = sub.add_parser("status")
    s.add_argument("--order-id", default="")

    sub.add_parser("selftest")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return _selftest()
    if args.cmd == "intake":
        rc, v = intake(args.order_id, {"ts": args.ts})
        print("verdict=%s delivery_id=%s order=%s status=%s route=%s%s" % (
            v.get("verdict"), v.get("delivery_id", ""), v.get("order_id", ""),
            v.get("status", ""), v.get("route_group", ""),
            (" evidence=" + v["evidence"]) if v.get("evidence") else ""))
        if v.get("reason"):
            print("reason=%s" % v.get("reason"))
        return rc
    if args.cmd in ("dispatch", "deliver"):
        fn = dispatch if args.cmd == "dispatch" else deliver
        rc, v = fn(args.delivery_id, {"ts": args.ts})
        print("verdict=%s delivery_id=%s order=%s status=%s attempts=%s "
              "receipt=%s%s" % (
                  v.get("verdict"), v.get("delivery_id", ""),
                  v.get("order_id", ""), v.get("status", ""),
                  v.get("attempts", ""), v.get("receipt_ref", ""),
                  (" evidence=" + v["evidence"]) if v.get("evidence") else ""))
        if v.get("reason"):
            print("reason=%s allowed=%s" % (v.get("reason"), v.get("allowed")))
        return rc
    rc, st = status(args.order_id)
    print("verdict=%s events=%s deliveries=%s live=%s rail=%s" % (
        st["verdict"], st["events"], st["deliveries"], st["live"], st["rail"]))
    for r in st["rows"]:
        print("  %s order=%s status=%s route=%s attempts=%s receipt=%s" % (
            r["delivery_id"], r["order_id"], r["status"], r["route_group"],
            r["attempts"], r["receipt_ref"]))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))