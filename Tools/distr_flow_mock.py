#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""distr_flow_mock.py -- Ring-2 (private-domain content distribution) MOCK runner.

Provenance: M73 P1 step-04 (building-plan sec.7 pre-design, 2026-10-19; P1
implementation window 10-26..10-31 opened early for the MOCK face by the
10-19 sim-state order). Ring spec = docs/ops/bigcompute-automation-building-plan-v1.md
sec.7 (15-field distr rail + attribution gates G1-G3 + judgments J5/J6).

RED LINES (hard-coded):
- LIVE is False until commercial physical prerequisites pass (BLUEPRINT S5.5,
  J4). Flipping it is a CEO-gate action, not a code edit shortcut.
- G3 planned state: publish_state is structurally forced to "queued" and
  receipt_basis to "synthetic-estimate". A "published"/real-receipt row is
  rejected, not downgraded silently -- real publishing is a CEO-gated action.
- Writes only to the MOCK rail (state/distr-ledger.jsonl). The real cost rail
  (cost_ledger.LEDGER) is never touched.
- Zero real-world action: no network calls to any content platform.

Schema truth: the 15-field shape lives in Tools/rails_schema_check.py
(SCHEMAS["distr"]) and is imported, never duplicated here. G1 (six attribution
keys non-empty) is a subset of the schema's required list and is checked
separately so the rejection carries a gate name.

Commands:
  run       --consumer NAME --lane <lane> --content-ref REF [--tx-id ID]
            [--utm-source S --utm-medium M --utm-campaign C]
            [--audience-seg SEG] [--conv-tx-ref ORDER] [--reach N] [--ts TS]
  selftest  temp-sandbox fixtures, zero pollution

Exit codes: 0 DISTR-RING-GREEN[-IDEMPOTENT] | 2 G1/G2 reject | 3 G3 reject
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
import cost_ledger as cl  # noqa: E402  (real rail: read-only signature)
import rails_schema_check as rsc  # noqa: E402  (single-source distr schema)

RAIL = os.path.join(HERE, "..", "state", "distr-ledger.jsonl")
MOCK_ORDER_RAIL = os.path.join(HERE, "..", "state", "mock-order-ledger.jsonl")
EVIDENCE_DIR = os.path.join(HERE, "..", "state")

LIVE = False  # J4: planned-state drill. Real publishing = CEO gate.
LANES = ("douyin", "bilibili", "wechat", "qq_group", "xiaohongshu", "kuaishou")
G1_KEYS = ("consumer", "lane", "tx_id", "utm_source", "utm_medium", "utm_campaign")
# T135 write-side census: the exact keys this writer emits. Kept next to the
# row builder so a field added below without a matching entry here fails
# selftest (S12/S13) instead of slipping through `additionalProperties`.
WRITER_KEYS = (
    "tx_id", "ts", "consumer", "lane", "content_ref", "publish_state",
    "utm_source", "utm_medium", "utm_campaign", "audience_seg", "conv_tx_ref",
    "attribution_status", "reach_count", "receipt_basis", "caliber_note",
    "live",
)
RECEIPT_SYNTHETIC = "synthetic-estimate"
CALIBER_NOTE = ("private-domain distribution row = attribution ledger, not a "
                "revenue row; amounts settle on the deal rail (caliber B); "
                "month-end S1-S7 aggregation only")


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
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")


def _next_tx_id(date_compact, rows):
    seq = 0
    prefix = "D-%s-" % date_compact
    for r in rows:
        tx = str(r.get("tx_id", ""))
        if tx.startswith(prefix):
            try:
                seq = max(seq, int(tx[len(prefix):]))
            except ValueError:
                pass
    return "%s%d" % (prefix, seq + 1)


def _conv_known(ref, order_rows):
    if not ref:
        return False
    for r in order_rows:
        if r.get("order_id") == ref:
            return True
    return False


def run_row(fields, rail_path=None, order_path=None, evidence=True):
    """Walk ring 2 once. Returns (exit_code, verdict_dict).

    fields keys: consumer, lane, content_ref, utm_source, utm_medium,
    utm_campaign, audience_seg, conv_tx_ref, reach, tx_id, ts.
    """
    global RAIL, MOCK_ORDER_RAIL
    if rail_path:  # selftest sandbox override
        RAIL = rail_path
    if order_path:
        MOCK_ORDER_RAIL = order_path

    ts = fields.get("ts") or datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    date_compact = ts[:10].replace("-", "")
    rows = _load_rows(RAIL)
    tx_id = fields.get("tx_id") or _next_tx_id(date_compact, rows)

    row = {
        "tx_id": tx_id,
        "ts": ts,
        "consumer": fields.get("consumer", ""),
        "lane": fields.get("lane", ""),
        "content_ref": fields.get("content_ref", ""),
        "publish_state": "queued",  # G3: planned state is structurally queued
        "utm_source": fields.get("utm_source", ""),
        "utm_medium": fields.get("utm_medium", ""),
        "utm_campaign": fields.get("utm_campaign", ""),
        "audience_seg": fields.get("audience_seg", ""),
        "conv_tx_ref": fields.get("conv_tx_ref", ""),
        "attribution_status": "pending",
        "reach_count": int(fields.get("reach") or 0),
        "receipt_basis": RECEIPT_SYNTHETIC,
        "caliber_note": CALIBER_NOTE,
        "live": LIVE,
    }

    # G1 structural gate: six attribution keys, no partial rows.
    missing = [k for k in G1_KEYS if not str(row.get(k, "")).strip()]
    if missing:
        return 2, {"verdict": "DISTR-RING-RED", "reason": "G1-missing",
                   "missing": missing, "tx_id": tx_id}
    if row["lane"] not in LANES:
        return 2, {"verdict": "DISTR-RING-RED", "reason": "G1-unknown-lane",
                   "lane": row["lane"], "allowed": list(LANES)}
    if not str(row["content_ref"]).strip() or len(str(row["content_ref"])) < 3:
        return 2, {"verdict": "DISTR-RING-RED", "reason": "G1-bad-content-ref",
                   "content_ref": row["content_ref"]}

    # G3 planned-state gate: real publish / real receipts are CEO-gated.
    if fields.get("publish_state") and fields["publish_state"] != "queued":
        return 3, {"verdict": "DISTR-RING-RED", "reason": "G3-publish-gated",
                   "publish_state": fields["publish_state"], "live": LIVE}
    if fields.get("receipt_basis") and fields["receipt_basis"] != RECEIPT_SYNTHETIC:
        return 3, {"verdict": "DISTR-RING-RED", "reason": "G3-receipt-gated",
                   "receipt_basis": fields["receipt_basis"], "live": LIVE}

    # J6 conversion linkage: attributed requires a real deal-rail reference.
    warnings = []
    conv_ref = row["conv_tx_ref"]
    if conv_ref:
        if _conv_known(conv_ref, _load_rows(MOCK_ORDER_RAIL)):
            row["attribution_status"] = "attributed"
        else:
            warnings.append("J6 conv_tx_ref=%s not on deal rail; kept pending" % conv_ref)
    elif fields.get("attribution_status") == "attributed":
        warnings.append("J6 attributed without conv_tx_ref; kept pending")

    # Schema truth: validate the row with the imported distr schema before write.
    errs = rsc.validate_rows("distr", [row])
    if errs:
        return 2, {"verdict": "DISTR-RING-RED", "reason": "schema-invalid",
                   "errors": errs[:5], "tx_id": tx_id}

    # G2 uniqueness: same tx_id appended twice is rejected (orders-rail rule).
    for r in rows:
        if r.get("tx_id") == tx_id:
            verdict = {"verdict": "DISTR-RING-GREEN-IDEMPOTENT", "reason": "G2-duplicate",
                       "tx_id": tx_id, "row": r, "live": LIVE,
                       "rail": os.path.basename(RAIL), "warnings": warnings}
            return 0, verdict

    _append_row(RAIL, row)
    out = {
        "verdict": "DISTR-RING-GREEN", "tx_id": tx_id, "lane": row["lane"],
        "consumer": row["consumer"], "content_ref": row["content_ref"],
        "publish_state": row["publish_state"],
        "attribution_status": row["attribution_status"],
        "conv_tx_ref": conv_ref, "reach_count": row["reach_count"],
        "receipt_basis": row["receipt_basis"], "live": LIVE,
        "rail": os.path.basename(RAIL),
        "real_rail": os.path.basename(cl.LEDGER), "warnings": warnings,
    }
    if evidence:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(EVIDENCE_DIR, "distr-ring-%s.json" % stamp)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, sort_keys=True, ensure_ascii=True, indent=1)
            out["evidence"] = os.path.relpath(path, os.path.join(HERE, ".."))
        except OSError as e:
            return 5, {"verdict": "DISTR-RING-RED", "reason": "evidence-write", "err": str(e)}
    return 0, out


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append((name, ok, detail))

    real_sig = (hashlib.sha256(open(cl.LEDGER, "rb").read()).hexdigest()
                if os.path.isfile(cl.LEDGER) else "absent")
    base = {
        "consumer": "BigCompute", "lane": "douyin",
        "content_ref": "state/empowerment-catalog-v1.md#sec1",
        "utm_source": "douyin", "utm_medium": "private-msg",
        "utm_campaign": "resident-card-1019", "audience_seg": "returning",
        "reach": 12, "ts": "2026-10-25T12:00:00",
    }
    with tempfile.TemporaryDirectory() as td:
        rail = os.path.join(td, "distr-rail.jsonl")
        orders = os.path.join(td, "mock-orders.jsonl")
        with open(orders, "w", encoding="utf-8") as f:
            f.write(json.dumps({"order_id": "MOCK-20261019-R1"}) + "\n")

        # S1 happy path: row written and schema-valid
        rc, v = run_row(dict(base, tx_id="D-20261025-1"), rail_path=rail,
                        order_path=orders, evidence=False)
        rows = _load_rows(rail)
        run("S1 green row written", rc == 0 and v["verdict"] == "DISTR-RING-GREEN"
            and len(rows) == 1, v.get("verdict"))
        run("S1b row passes distr schema", rsc.validate_rows("distr", rows) == [],
            "rows=%d" % len(rows))
        run("S1c publish forced queued", rows and rows[0]["publish_state"] == "queued"
            and rows[0]["receipt_basis"] == RECEIPT_SYNTHETIC)

        # S2 auto tx_id sequence
        rc, v = run_row(dict(base, tx_id=""), rail_path=rail, order_path=orders,
                        evidence=False)
        run("S2 auto tx_id seq", rc == 0 and v["tx_id"] == "D-20261025-2", v.get("tx_id"))

        # S3 G1 missing attribution key
        bad = dict(base, tx_id="D-20261025-9")
        bad.pop("utm_campaign")
        rc, v = run_row(bad, rail_path=rail, order_path=orders, evidence=False)
        run("S3 G1 missing key rejected", rc == 2 and v["reason"] == "G1-missing"
            and "utm_campaign" in v["missing"], v.get("reason"))
        run("S3b G1 reject wrote nothing", len(_load_rows(rail)) == 2)

        # S4 G1 unknown lane
        rc, v = run_row(dict(base, lane="myspace", tx_id="D-20261025-3"),
                        rail_path=rail, order_path=orders, evidence=False)
        run("S4 G1 unknown lane rejected", rc == 2 and v["reason"] == "G1-unknown-lane",
            v.get("reason"))

        # S5 G2 duplicate tx_id
        rc, v = run_row(dict(base, tx_id="D-20261025-1"), rail_path=rail,
                        order_path=orders, evidence=False)
        run("S5 G2 duplicate idempotent", rc == 0
            and v["verdict"] == "DISTR-RING-GREEN-IDEMPOTENT"
            and len(_load_rows(rail)) == 2, v.get("verdict"))

        # S6 G3 real publish rejected
        rc, v = run_row(dict(base, tx_id="D-20261025-4", publish_state="published"),
                        rail_path=rail, order_path=orders, evidence=False)
        run("S6 G3 published rejected", rc == 3 and v["reason"] == "G3-publish-gated",
            v.get("reason"))

        # S7 G3 real receipt basis rejected
        rc, v = run_row(dict(base, tx_id="D-20261025-5", receipt_basis="platform-api"),
                        rail_path=rail, order_path=orders, evidence=False)
        run("S7 G3 real receipt rejected", rc == 3 and v["reason"] == "G3-receipt-gated",
            v.get("reason"))

        # S8 J6 conversion linkage both ways
        rc, v = run_row(dict(base, tx_id="D-20261025-6",
                             conv_tx_ref="MOCK-20261019-R1"),
                        rail_path=rail, order_path=orders, evidence=False)
        ok_attr = rc == 0 and v["attribution_status"] == "attributed" and not v["warnings"]
        rc2, v2 = run_row(dict(base, tx_id="D-20261025-7",
                               conv_tx_ref="MOCK-NOT-THERE"),
                          rail_path=rail, order_path=orders, evidence=False)
        ok_pending = (rc2 == 0 and v2["attribution_status"] == "pending"
                      and v2["warnings"])
        run("S8 J6 conv linkage", ok_attr and ok_pending,
            "%s/%s" % (v.get("attribution_status"), v2.get("attribution_status")))

        # S9 determinism: same fixed input reproduces the same idempotent verdict
        rc3, v3 = run_row(dict(base, tx_id="D-20261025-1"), rail_path=rail,
                          order_path=orders, evidence=False)
        run("S9 deterministic idempotent", rc3 == 0
            and v3["verdict"] == "DISTR-RING-GREEN-IDEMPOTENT",
            v3.get("verdict"))

        # S10 schema single-source: G1 keys are inside the imported schema
        run("S10 G1 subset of imported schema",
            set(G1_KEYS).issubset(set(rsc.SCHEMAS["distr"]["required"])),
            "schema=%d required" % len(rsc.SCHEMAS["distr"]["required"]))

        # S12/S13 write-side census (T135): emitted keys == WRITER_KEYS, all
        # declared, and every declared field names a producer.
        written = set(_load_rows(rail)[0].keys())
        run("S12 emitted keys == WRITER_KEYS",
            written == set(WRITER_KEYS),
            "diff=%s" % sorted(written.symmetric_difference(set(WRITER_KEYS))))
        run("S13 census clean on live-shaped rows",
            rsc.census("distr", _load_rows(rail)) == [],
            "problems=%d" % len(rsc.census("distr", _load_rows(rail))))

    # S11 real cost rail untouched
    sig2 = (hashlib.sha256(open(cl.LEDGER, "rb").read()).hexdigest()
            if os.path.isfile(cl.LEDGER) else "absent")
    run("S11 real cost rail untouched", real_sig == sig2, sig2[:12])

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           (" (%s)" % detail) if detail else ""))
    print("SELFTEST %s %d/%d" % ("PASS" if not failed else "FAIL",
                                 len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def main(argv):
    ap = argparse.ArgumentParser(description="Distr-rail MOCK runner (J4 planned state)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--consumer", required=True)
    r.add_argument("--lane", required=True, choices=sorted(LANES))
    r.add_argument("--content-ref", required=True)
    r.add_argument("--tx-id", default="")
    r.add_argument("--utm-source", required=True)
    r.add_argument("--utm-medium", required=True)
    r.add_argument("--utm-campaign", required=True)
    r.add_argument("--audience-seg", default="")
    r.add_argument("--conv-tx-ref", default="")
    r.add_argument("--reach", type=int, default=0)
    r.add_argument("--ts", default="")

    sub.add_parser("selftest")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return _selftest()

    rc, v = run_row({
        "consumer": args.consumer, "lane": args.lane,
        "content_ref": args.content_ref, "utm_source": args.utm_source,
        "utm_medium": args.utm_medium, "utm_campaign": args.utm_campaign,
        "audience_seg": args.audience_seg, "conv_tx_ref": args.conv_tx_ref,
        "reach": args.reach, "tx_id": args.tx_id, "ts": args.ts,
    })
    print("verdict=%s tx_id=%s lane=%s attribution=%s conv=%s reach=%s "
          "publish=%s live=%s warnings=%d%s" % (
              v.get("verdict"), v.get("tx_id", ""), v.get("lane", ""),
              v.get("attribution_status", ""), v.get("conv_tx_ref", ""),
              v.get("reach_count", ""), v.get("publish_state", ""),
              v.get("live"), len(v.get("warnings") or []),
              (" evidence=" + v["evidence"]) if v.get("evidence") else ""))
    if v.get("reason") or v.get("missing"):
        print("reason=%s missing=%s errors=%s" % (
            v.get("reason"), v.get("missing"), v.get("errors")))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))