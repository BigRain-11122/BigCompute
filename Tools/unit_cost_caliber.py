"""Single source for the unit-cost denominator caliber (BC-P-184 / queue M105).

Three "token" faces exist in this repo and they are not the same axis. Before
this module each consumer carried its own idea of the denominator, so two
reports could disagree while both being "correct":

  face          source                              counts                          consumer
  ------------  ----------------------------------  ------------------------------  ---------------------------
  quota_rail    state/quota-ledger.jsonl            issued/consumed quota tokens    month_end_collect S1
                (cost_ledger quota-summary)         (rights face, not usage)        -> commercial tokens_mtok
  l1_rail       state/cost-l1-ledger.jsonl          local inference actually run    cost_ledger l1-settle
                (cost_ledger l1-summary)            (self-use metering)             / l1-summary
  revenue_row   state/cost-ledger.jsonl rows        per-order fulfillment usage     unit_cost_drill

Caliber decision (this module is the only place that decides it):

  * a revenue row is classified quota-type when the product sold a *right* to
    a token allowance; its denominator is the allowance that was granted
    (amount_mtok on the row), not the usage that happened afterwards.
  * otherwise it is generation-type and its denominator is the fulfillment
    usage actually consumed (tokens_local + tokens_api), because a generation
    order is paid for by what the pipeline produced.
  * a row that cannot be classified is unknown and never silently lands in
    either bucket.
  * a quota-type row with no amount_mtok is unmeasurable, not zero: it renders
    as BLOCKED so a missing field can never masquerade as a cheap unit cost.
  * l1 self-use metering never enters the commercial denominator
    (month_end_collect S1 rule); it is an advisory face only.

Nothing here writes. Import it instead of re-deriving the arithmetic.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "state")

CLASS_QUOTA = "quota"
CLASS_GENERATION = "generation"
CLASS_UNKNOWN = "unknown"

QUOTA_LANES = frozenset(["subscription", "quota", "membership", "member"])
QUOTA_SKU_HINTS = ("quota", "membership", "subscription", "allowance")

DENOM_OK = "DENOM-OK"
DENOM_BLOCKED = "DENOM-BLOCKED"
DENOM_UNKNOWN = "DENOM-UNKNOWN"


def classify_row(row):
    """Return one of CLASS_QUOTA / CLASS_GENERATION / CLASS_UNKNOWN."""
    explicit = str(row.get("sku_type", "")).strip().lower()
    if explicit in (CLASS_QUOTA, CLASS_GENERATION):
        return explicit
    lane = str(row.get("lane", "")).strip().lower()
    if lane in QUOTA_LANES:
        return CLASS_QUOTA
    sku = str(row.get("sku", "")).strip().lower()
    for hint in QUOTA_SKU_HINTS:
        if hint in sku:
            return CLASS_QUOTA
    if lane or sku or row.get("cost_item") or row.get("tokens_local") is not None:
        return CLASS_GENERATION
    return CLASS_UNKNOWN


def row_denominator(row):
    """Return the denominator of one revenue row as a dict.

    keys: sku_class, mtok, state, reason
    mtok is always a float; a BLOCKED row carries 0.0 plus a reason string so
    callers can report it without inventing a value.
    """
    cls = classify_row(row)
    if cls == CLASS_UNKNOWN:
        return {"sku_class": cls, "mtok": 0.0, "state": DENOM_UNKNOWN,
                "reason": "row has no lane/sku/cost_item to classify"}

    if cls == CLASS_QUOTA:
        raw = row.get("amount_mtok")
        if raw is None:
            return {"sku_class": cls, "mtok": 0.0, "state": DENOM_BLOCKED,
                    "reason": "quota-type row without amount_mtok"}
        try:
            mtok = float(raw)
        except (TypeError, ValueError):
            return {"sku_class": cls, "mtok": 0.0, "state": DENOM_BLOCKED,
                    "reason": "quota-type row amount_mtok not numeric"}
        if mtok < 0:
            return {"sku_class": cls, "mtok": 0.0, "state": DENOM_BLOCKED,
                    "reason": "quota-type row amount_mtok negative"}
        return {"sku_class": cls, "mtok": mtok, "state": DENOM_OK,
                "reason": "-"}

    tokens = 0
    for key in ("tokens_local", "tokens_api"):
        try:
            tokens += int(row.get(key, 0) or 0)
        except (TypeError, ValueError):
            return {"sku_class": cls, "mtok": 0.0, "state": DENOM_BLOCKED,
                    "reason": "generation-type row %s not numeric" % key}
    if tokens <= 0:
        return {"sku_class": cls, "mtok": 0.0, "state": DENOM_BLOCKED,
                "reason": "generation-type row without fulfillment tokens"}
    return {"sku_class": cls, "mtok": tokens / 1000000.0, "state": DENOM_OK,
            "reason": "-"}


def aggregate_denominator(rows):
    """Sum denominators per class, keeping blocked rows visible."""
    out = {
        "quota_mtok": 0.0,
        "generation_mtok": 0.0,
        "quota_rows": 0,
        "generation_rows": 0,
        "quota_blocked_rows": 0,
        "generation_blocked_rows": 0,
        "unknown_rows": 0,
        "block_reasons": [],
        "denominator_mtok": 0.0,
    }
    for idx, row in enumerate(rows):
        d = row_denominator(row)
        if d["sku_class"] == CLASS_QUOTA:
            out["quota_rows"] += 1
        elif d["sku_class"] == CLASS_GENERATION:
            out["generation_rows"] += 1
        else:
            out["unknown_rows"] += 1
        if d["state"] == DENOM_BLOCKED:
            key = "quota_blocked_rows" if d["sku_class"] == CLASS_QUOTA \
                else "generation_blocked_rows"
            out[key] += 1
            out["block_reasons"].append("row %d: %s" % (idx, d["reason"]))
        elif d["state"] == DENOM_UNKNOWN:
            out["block_reasons"].append("row %d: %s" % (idx, d["reason"]))
        if d["sku_class"] == CLASS_QUOTA:
            out["quota_mtok"] += d["mtok"]
        elif d["sku_class"] == CLASS_GENERATION:
            out["generation_mtok"] += d["mtok"]
        out["denominator_mtok"] += d["mtok"]
    out["quota_mtok"] = round(out["quota_mtok"], 6)
    out["generation_mtok"] = round(out["generation_mtok"], 6)
    out["denominator_mtok"] = round(out["denominator_mtok"], 6)
    return out


def unit_cost_state(agg):
    """CALCULATED / PARTIAL / BLOCKED for a month face."""
    measurable = agg["denominator_mtok"] > 0
    blocked = agg["quota_blocked_rows"] + agg["generation_blocked_rows"] \
        + agg["unknown_rows"]
    if not measurable:
        return "UNIT-COST-BLOCKED"
    if blocked:
        return "UNIT-COST-PARTIAL"
    return "UNIT-COST-CALCULATED"


# ---------------------------------------------------------------- selftest

def _selftest():
    checks = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    chk("S1 quota lane classified as quota",
        classify_row({"lane": "subscription", "sku": "x"}) == CLASS_QUOTA)
    chk("S2 plain lane classified as generation",
        classify_row({"lane": "gacha", "sku": "draw", "tokens_local": 10})
        == CLASS_GENERATION)
    chk("S3 explicit sku_type wins over lane",
        classify_row({"sku_type": "generation", "lane": "subscription",
                      "sku": "x"}) == CLASS_GENERATION)
    chk("S4 empty row is unknown", classify_row({}) == CLASS_UNKNOWN)

    d = row_denominator({"lane": "subscription", "sku": "q"})
    chk("S5 quota row without amount_mtok is BLOCKED not zero",
        d["state"] == DENOM_BLOCKED and d["mtok"] == 0.0, str(d))
    d = row_denominator({"lane": "subscription", "sku": "q",
                         "amount_mtok": 1.5})
    chk("S6 quota row uses granted allowance",
        d["state"] == DENOM_OK and abs(d["mtok"] - 1.5) < 1e-9, str(d))
    d = row_denominator({"lane": "gacha", "sku": "d", "tokens_local": 300000,
                         "tokens_api": 200000})
    chk("S7 generation row sums both token kinds",
        abs(d["mtok"] - 0.5) < 1e-9, str(d))
    d = row_denominator({"lane": "gacha", "sku": "d", "tokens_local": 0})
    chk("S8 generation row with no usage is BLOCKED",
        d["state"] == DENOM_BLOCKED, str(d))

    agg = aggregate_denominator([
        {"lane": "subscription", "sku": "q1"},
        {"lane": "gacha", "sku": "d", "tokens_local": 1000000},
        {},
    ])
    chk("S9 aggregate keeps blocked and unknown visible",
        agg["quota_blocked_rows"] == 1 and agg["unknown_rows"] == 1
        and abs(agg["generation_mtok"] - 1.0) < 1e-9, str(agg))
    chk("S10 mixed month renders PARTIAL",
        unit_cost_state(agg) == "UNIT-COST-PARTIAL", unit_cost_state(agg))
    chk("S11 clean month renders CALCULATED",
        unit_cost_state(aggregate_denominator(
            [{"lane": "gacha", "sku": "d", "tokens_local": 5}]))
        == "UNIT-COST-CALCULATED")
    chk("S12 empty month renders BLOCKED",
        unit_cost_state(aggregate_denominator([])) == "UNIT-COST-BLOCKED")
    a = aggregate_denominator([{"lane": "gacha", "sku": "d", "tokens_local": 7}])
    b = aggregate_denominator([{"lane": "gacha", "sku": "d", "tokens_local": 7}])
    chk("S13 determinism", a == b)

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           (" | " + detail) if (not ok and detail) else ""))
    print("SELFTEST %d/%d %s" % (len(checks) - len(failed), len(checks),
                                 "PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())