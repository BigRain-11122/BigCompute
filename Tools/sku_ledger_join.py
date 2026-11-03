"""SKU class x ledger field join drill (queue M100).

fulfillment-sku-mapping-v1.md declares ten payable classes and which rails they
should land on, but nothing ever checked that the declared fields actually show
up in any ledger row. This tool joins the declaration against real data and
prints, per class and per field, one of three honest states:

  EVIDENCED       a matching row carries this field with a value
  MISSING-IN-DATA rows of this class exist but none carries the field
  NO-ROWS         no row of this class exists at all (planned state)

The point is the third state: a class with no rows cannot be "joined", and
claiming the mapping works would be an assertion, not a measurement.

Read-only.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "state")
REVENUE_RAIL = os.path.join(STATE, "cost-ledger.jsonl")
MOCK_RAIL = os.path.join(STATE, "mock-order-ledger.jsonl")
QUOTA_RAIL = os.path.join(STATE, "quota-ledger.jsonl")
FULFILL_RAILS = [os.path.join(STATE, "fulfillment-ledger.jsonl")]

EVIDENCED = "EVIDENCED"
MISSING_IN_DATA = "MISSING-IN-DATA"
NO_ROWS = "NO-ROWS"

REVENUE_FIELDS = ["order_id", "date", "sku", "tier", "cost_item", "price",
                  "fee_rate", "refund_rate", "net_price", "amount", "gross",
                  "tokens_local", "tokens_api", "api_reason", "fund_3090"]
QUOTA_FIELDS = ["tx_id", "consumer", "amount_mtok", "unit_cost_b_cny",
                "month", "settlement_caliber"]
FULFILL_FIELDS = ["order_id", "cost_item", "tokens_local", "tokens_api",
                  "api_reason", "tier"]
CONTRACT_FIELDS = ["contract_no", "party", "amount", "term_start",
                   "term_end", "data_auth_clause"]
SETTLE_FIELDS = ["platform_settle_id", "amount", "gross_fee",
                 "net_settle", "date"]

# class -> (rail, field list, matcher description)
SPEC = [
    ("quota_pack_19.9", "quota", QUOTA_FIELDS + ["order_id"],
     {"sku_contains": ["quota"]}),
    ("membership_3tier", "quota", QUOTA_FIELDS + ["order_id"],
     {"lane_in": ["subscription", "membership"]}),
    ("rush_9.9", "quota", QUOTA_FIELDS + ["order_id"],
     {"sku_contains": ["rush"]}),
    ("watch_session", "revenue", REVENUE_FIELDS, {"lane_in": ["scene"]}),
    ("identity_right", "revenue", REVENUE_FIELDS,
     {"lane_in": ["identity"], "sku_contains": ["identity", "badge"]}),
    ("b2b_contract", "contract", CONTRACT_FIELDS,
     {"lane_in": ["b2b_direct", "b2b_contract"]}),
    ("ads_3tier", "contract", CONTRACT_FIELDS, {"lane_in": ["ads"]}),
    ("venue_booking", "contract", CONTRACT_FIELDS, {"lane_in": ["venue"]}),
    ("furniture_gen", "fulfillment", FULFILL_FIELDS, {"lane_in": ["gacha"]}),
    ("tip_stream", "settlement", SETTLE_FIELDS, {"lane_in": ["tip"]}),
]

FIELDS_BY_RAIL = {
    "revenue": REVENUE_FIELDS,
    "quota": QUOTA_FIELDS,
    "fulfillment": FULFILL_FIELDS,
    "contract": CONTRACT_FIELDS,
    "settlement": SETTLE_FIELDS,
}


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


def load_rails(revenue_path=REVENUE_RAIL, mock_path=MOCK_RAIL,
               quota_path=QUOTA_RAIL):
    rails = {"revenue": load_rows(revenue_path) + load_rows(mock_path),
             "quota": load_rows(quota_path),
             "fulfillment": []}
    for p in FULFILL_RAILS:
        rails["fulfillment"].extend(load_rows(p))
    rails["contract"] = []
    rails["settlement"] = []
    return rails


def matches(row, matcher):
    lane = str(row.get("lane", "")).strip().lower()
    sku = str(row.get("sku", "")).strip().lower()
    hits = []
    if "lane_in" in matcher:
        hits.append(lane in matcher["lane_in"])
    if "sku_contains" in matcher:
        hits.append(any(h in sku for h in matcher["sku_contains"]))
    return any(hits) if hits else False


def join(rails):
    """Join the declared classes against every rail we actually have.

    A class may not own a rail file of its own (quota orders physically land in
    the revenue rail), so row detection searches all rails; the declared rail
    only decides which field list is required.
    """
    all_rows = []
    for name in ("revenue", "quota", "fulfillment", "contract", "settlement"):
        all_rows.extend(rails.get(name, []))
    out = []
    for name, rail, fields, matcher in SPEC:
        rows = [r for r in all_rows if matches(r, matcher)]
        field_states = {}
        for f in fields:
            if not rows:
                field_states[f] = NO_ROWS
            elif any(r.get(f) is not None for r in rows):
                field_states[f] = EVIDENCED
            else:
                field_states[f] = MISSING_IN_DATA
        missing = [f for f, s in field_states.items() if s == MISSING_IN_DATA]
        out.append({
            "class": name,
            "rail": rail,
            "rows": len(rows),
            "class_state": (NO_ROWS if not rows else
                            (MISSING_IN_DATA if missing else EVIDENCED)),
            "fields_total": len(fields),
            "fields_evidenced": sum(1 for s in field_states.values() if s == EVIDENCED),
            "missing_fields": missing,
            "field_states": field_states,
        })
    return out


def summarise(items):
    return {
        "classes": len(items),
        "classes_with_rows": sum(1 for i in items if i["class_state"] != NO_ROWS),
        "classes_no_rows": sum(1 for i in items if i["class_state"] == NO_ROWS),
        "classes_missing_fields": sum(1 for i in items
                                      if i["class_state"] == MISSING_IN_DATA),
        "missing_field_total": sum(len(i["missing_fields"]) for i in items),
        "verdict": ("SKU-LEDGER-JOIN-GAPS" if any(
            i["class_state"] != EVIDENCED for i in items) else
            "SKU-LEDGER-JOIN-GREEN"),
    }


def render(items, summary):
    lines = ["SKU x LEDGER JOIN  verdict=%s" % summary["verdict"]]
    lines.append("classes=%d with_rows=%d no_rows=%d missing_fields=%d total_missing=%d"
                 % (summary["classes"], summary["classes_with_rows"],
                    summary["classes_no_rows"], summary["classes_missing_fields"],
                    summary["missing_field_total"]))
    for i in items:
        lines.append("  %-18s rail=%-11s rows=%d state=%s fields=%d/%d"
                     % (i["class"], i["rail"], i["rows"], i["class_state"],
                        i["fields_evidenced"], i["fields_total"]))
        if i["missing_fields"]:
            lines.append("    missing in data: " + ", ".join(i["missing_fields"]))
    return "\n".join(lines)


def _selftest():
    checks = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    rails = {
        "revenue": [{"order_id": "A", "lane": "gacha", "sku": "card.draw",
                     "cost_item": "cost", "tokens_local": 5, "tier": "L2"}],
        "quota": [{"order_id": "Q", "sku": "seat.quota", "amount_mtok": 0.4}],
        "fulfillment": [], "contract": [], "settlement": [],
    }
    items = {i["class"]: i for i in join(rails)}

    chk("S1 class with matching row is not NO-ROWS",
        items["furniture_gen"]["class_state"] != NO_ROWS,
        items["furniture_gen"]["class_state"])
    chk("S2 present field is EVIDENCED",
        items["furniture_gen"]["field_states"]["tokens_local"] == EVIDENCED)
    chk("S3 absent field on an existing row is MISSING-IN-DATA",
        items["furniture_gen"]["field_states"]["api_reason"] == MISSING_IN_DATA
        and "api_reason" in items["furniture_gen"]["missing_fields"])
    chk("S4 class with no rows is NO-ROWS on every field",
        items["ads_3tier"]["class_state"] == NO_ROWS
        and all(s == NO_ROWS for s in items["ads_3tier"]["field_states"].values()))
    chk("S5 quota class joins the quota rail", items["quota_pack_19.9"]["rows"] == 1)

    summary = summarise(list(items.values()))
    chk("S6 summary counts no-rows classes honestly",
        summary["classes_no_rows"] == sum(
            1 for i in items.values() if i["class_state"] == NO_ROWS),
        str(summary["classes_no_rows"]))
    chk("S7 any gap keeps the verdict non-green",
        summary["verdict"] == "SKU-LEDGER-JOIN-GAPS")
    chk("S8 determinism", join(rails) == join(rails))
    text = render(list(items.values()), summary)
    chk("S9 render names verdict and per-class state",
        "SKU-LEDGER-JOIN" in text and "furniture_gen" in text)

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           (" | " + detail) if (not ok and detail) else ""))
    print("SELFTEST %d/%d %s" % (len(checks) - len(failed), len(checks),
                                 "PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="SKU class x ledger join drill (M100)")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--revenue", default=REVENUE_RAIL)
    r.add_argument("--mock", default=MOCK_RAIL)
    r.add_argument("--quota", default=QUOTA_RAIL)
    r.add_argument("--json-out", default=None)
    r.add_argument("--text-out", default=None)
    sub.add_parser("selftest")
    args = p.parse_args(argv)

    if args.cmd == "selftest":
        return _selftest()
    if args.cmd != "run":
        p.print_help()
        return 2

    items = join(load_rails(args.revenue, args.mock, args.quota))
    summary = summarise(items)
    payload = {"summary": summary, "classes": items}
    text = render(items, summary)
    print(text)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, indent=2, ensure_ascii=True)
            fh.write("\n")
    if args.text_out:
        with open(args.text_out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0 if summary["verdict"] == "SKU-LEDGER-JOIN-GREEN" else 3


if __name__ == "__main__":
    sys.exit(main())