"""Unit-cost monthly aggregation drill (queue M99).

Caliber B only (settlement caliber B = sole settlement face). The drill answers
three questions with numbers, not prose:

  1. what does one month of revenue-rail rows actually sum to
     (price / refund provision / platform fee / net / fund accrual)
  2. what is the unit cost denominator (business output tokens) and, if it is
     non-zero, the unit cost itself
  3. did this drill touch the ledgers at all (zero re-booking proof)

Design rules:
  * read-only. Both rails are hashed before and after; a byte change is a
    violation. The drill never appends, never rewrites.
  * mock rows (mock:true / MOCK-* order ids / MOCK-DRILL cost item) are drill
    data, they must stay out of the real revenue rail. Contamination is a
    violation, not a silent filter.
  * self-use L1 probe tokens are NOT a commercial denominator
    (month_end_collect S1 rule: self-use eval_count never enters settlement).
    They are reported on a separate advisory line.
  * denominator == 0 is legal and must render UNIT-COST-BLOCKED, never a
    computed 0.0 or a ZeroDivisionError.
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys
import tempfile

import unit_cost_caliber as caliber

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "state")
REVENUE_RAIL = os.path.join(STATE, "cost-ledger.jsonl")
MOCK_RAIL = os.path.join(STATE, "mock-order-ledger.jsonl")
L1_RAIL = os.path.join(STATE, "cost-l1-ledger.jsonl")

DRILL_NOTE = ("drill data only; caliber B settlement; self-use l1 tokens excluded "
              "from the commercial denominator")

UNIT_COST_OK = "UNIT-COST-CALCULATED"
UNIT_COST_BLOCKED = "UNIT-COST-BLOCKED"

# M133: the four cost keys on mock rows had no writer at all -- PRODUCERS listed
# them as "manual: ... (untracked cost drill)", i.e. values existed on disk but
# no code path could reproduce them. They are now derived from these two
# declared design values (never a settlement price -- see unit_cost_caliber).
MOCK_DESIGN_COST_RATE = 0.60   # CNY per Mtok, design value (M107)
MOCK_DESIGN_QUOTA_MTOK = 0.42  # M12 quota band lower bound, design value (M66)

# M200: the three drill rows were the only "order-shaped" data in the company and
# carried neither a channel nor a caliber tag, so every three-line x caliber
# question read "unassigned". Derived here for the same reason as the cost keys:
# one rerun reproduces them, and no historical row is hand-edited.
# Channel vocabulary is the three selling lines of revenue_caliber (store/
# livestream/private). M202: a direct-signed annual report states `channel=b2b`
# in the field itself -- the reader maps it to the private/b2b derived line.
# channel_note stays as a human note only; it no longer carries the decision,
# so renaming or dropping it cannot silently move the row back to private.
MOCK_CHANNEL_BY_LANE = {
    "subscription": "store",   # quota monthly card -> store shelf
    "gacha": "store",          # ornament gacha -> store shelf
    "b2b_direct": "b2b",       # annual report, direct-signed -> declared channel
}
MOCK_CHANNEL_NOTE = {"b2b_direct": "b2b-direct-signed"}
MOCK_SETTLEMENT_CALIBER = "B"  # caliber B (internal cost anchor) is the sole settlement caliber
MOCK_CHANNEL_KEYS = ("channel", "settlement_caliber", "channel_note")
MOCK_DRILL_KEYS = ("amount", "amount_mtok", "cost_rate_cny_per_mtok",
                   "cost_simulated", "denominator_simulated") + MOCK_CHANNEL_KEYS


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


def _sha256(path):
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _month_of(row):
    date = str(row.get("date", ""))
    return date[:7] if len(date) >= 7 else "unknown"


def is_mock_row(row):
    if row.get("mock") is True:
        return True
    if str(row.get("cost_item", "")).upper().startswith("MOCK"):
        return True
    return str(row.get("order_id", "")).upper().startswith("MOCK-")


def _row_math(row):
    price = float(row.get("price", 0.0) or 0.0)
    refund_rate = float(row.get("refund_rate", 0.0) or 0.0)
    fee_rate = float(row.get("fee_rate", 0.0) or 0.0)
    net = round(price * (1.0 - refund_rate) * (1.0 - fee_rate), 4)
    refund = round(price * refund_rate, 4)
    fee = round(price * (1.0 - refund_rate) * fee_rate, 4)
    return net, refund, fee


def aggregate(rows, month=None, mock_only=False):
    """Aggregate revenue rows into a month face. Pure function, no IO.

    Denominator comes from unit_cost_caliber (single source, BC-P-184): a
    quota-type row is measured by the allowance it granted, a generation-type
    row by the usage it consumed, and a row that cannot be classified or lacks
    its field is BLOCKED, never silently zero.
    """
    violations = []
    seen = {}
    buckets = {}
    bucket_rows = {}
    per_class = {}
    for idx, row in enumerate(rows):
        if mock_only and not is_mock_row(row):
            continue
        oid = str(row.get("order_id", ""))
        if oid in seen:
            violations.append("duplicate-order-id %s at rows %d/%d"
                              % (oid, seen[oid], idx))
        else:
            seen[oid] = idx
        mon = _month_of(row)
        if mon == "unknown":
            violations.append("missing-date row %d order_id=%s" % (idx, oid))
            continue
        b = buckets.setdefault(mon, {
            "orders": 0, "price_cny": 0.0, "net_cny": 0.0,
            "refund_provision_cny": 0.0, "platform_fee_cny": 0.0,
            "cost_cny": 0.0, "fund_3090_cny": 0.0,
            "tokens_local": 0, "tokens_api": 0,
        })
        bucket_rows.setdefault(mon, []).append(row)
        net, refund, fee = _row_math(row)
        stored_net = round(float(row.get("net_price", 0.0) or 0.0), 4)
        if abs(net - stored_net) > 1e-6:
            violations.append(
                "net-math-mismatch order_id=%s stored=%.4f recomputed=%.4f"
                % (oid, stored_net, net))
        b["orders"] += 1
        b["price_cny"] += float(row.get("price", 0.0) or 0.0)
        b["net_cny"] += stored_net
        b["refund_provision_cny"] += refund
        b["platform_fee_cny"] += fee
        b["cost_cny"] += float(row.get("amount", 0.0) or 0.0)
        b["fund_3090_cny"] += float(row.get("fund_3090", 0.0) or 0.0)
        b["tokens_local"] += int(row.get("tokens_local", 0) or 0)
        b["tokens_api"] += int(row.get("tokens_api", 0) or 0)
        cls = caliber.classify_row(row)
        c = per_class.setdefault((mon, cls), {"orders": 0, "net_cny": 0.0,
                                             "cost_cny": 0.0})
        c["orders"] += 1
        c["net_cny"] += stored_net
        c["cost_cny"] += float(row.get("amount", 0.0) or 0.0)

    face = {}
    for mon, b in sorted(buckets.items()):
        if month and mon != month:
            continue
        denom = caliber.aggregate_denominator(bucket_rows[mon])
        entry = {
            "month": mon,
            "orders": b["orders"],
            "price_cny": round(b["price_cny"], 4),
            "net_cny": round(b["net_cny"], 4),
            "refund_provision_cny": round(b["refund_provision_cny"], 4),
            "platform_fee_cny": round(b["platform_fee_cny"], 4),
            "cost_cny": round(b["cost_cny"], 4),
            "gross_cny": round(b["net_cny"] - b["cost_cny"], 4),
            "fund_3090_cny": round(b["fund_3090_cny"], 4),
            "tokens_local": b["tokens_local"],
            "tokens_api": b["tokens_api"],
            "denominator_tokens": b["tokens_local"] + b["tokens_api"],
            "quota_mtok": denom["quota_mtok"],
            "generation_mtok": denom["generation_mtok"],
            "denominator_mtok": denom["denominator_mtok"],
            "quota_blocked_rows": denom["quota_blocked_rows"],
            "generation_blocked_rows": denom["generation_blocked_rows"],
            "unknown_sku_class_rows": denom["unknown_rows"],
            "denominator_block_reasons": denom["block_reasons"],
            "unit_cost_state": caliber.unit_cost_state(denom),
            "caliber_source": "unit_cost_caliber (BC-P-184)",
            "cost_face": ("COST-FACE-UNEXERCISED"
                          if (b["cost_cny"] == 0 and b["orders"] > 0)
                          else "COST-FACE-EXERCISED"),
            "by_class": {},
        }
        for (mon_key, cls), c in sorted(per_class.items()):
            if mon_key != mon:
                continue
            net_c = round(c["net_cny"], 4)
            cost_c = round(c["cost_cny"], 4)
            gross_c = round(net_c - cost_c, 4)
            entry["by_class"][cls] = {
                "orders": c["orders"],
                "net_cny": net_c,
                "cost_cny": cost_c,
                "gross_cny": gross_c,
                "gross_margin_pct": (round(100.0 * gross_c / net_c, 2)
                                     if net_c else None),
            }
        if entry["denominator_mtok"] > 0:
            entry["unit_cost_cny_per_mtok"] = round(
                b["cost_cny"] / entry["denominator_mtok"], 4)
            entry["unit_cost_reason"] = (
                "-" if entry["unit_cost_state"] == "UNIT-COST-CALCULATED"
                else "partial: some rows unmeasurable")
            if b["cost_cny"] == 0:
                entry["unit_cost_note"] = (
                    "cost side is zero on this rail, so 0.0 is the cost input "
                    "and not a measured unit cost")
        else:
            entry["unit_cost_cny_per_mtok"] = None
            entry["unit_cost_reason"] = ("denominator=0 (no measurable "
                                         "commercial output tokens)")
        face[mon] = entry
    return face, violations


def run_drill(revenue_path=REVENUE_RAIL, mock_path=MOCK_RAIL,
              l1_path=L1_RAIL, month=None):
    paths = [revenue_path, mock_path, l1_path]
    before = {os.path.basename(p): _sha256(p) for p in paths}

    revenue_rows = _load_rows(revenue_path)
    mock_rows = _load_rows(mock_path)
    l1_rows = _load_rows(l1_path)

    violations = []
    for idx, row in enumerate(revenue_rows):
        if is_mock_row(row):
            violations.append("mock-contamination real rail row %d order_id=%s"
                              % (idx, row.get("order_id")))

    real_face, real_violations = aggregate(revenue_rows, month=month)
    mock_face, mock_violations = aggregate(mock_rows, month=month, mock_only=True)
    violations.extend(real_violations)
    violations.extend("mock rail: " + v for v in mock_violations)

    after = {os.path.basename(p): _sha256(p) for p in paths}
    changed = [k for k in before if before[k] != after[k]]
    for name in changed:
        violations.append("rail-mutated %s" % name)

    self_use_tokens = sum(int(r.get("tokens_local", 0) or 0) for r in l1_rows)
    self_use_cost = round(sum(float(r.get("cost_b_cny", 0.0) or 0.0)
                              for r in l1_rows), 8)

    def _simulated(rows):
        return sum(1 for r in rows
                   if r.get("denominator_simulated") or r.get("cost_simulated"))

    simulated = {"mock": _simulated(mock_rows),
                 "real": _simulated(revenue_rows)}

    total_orders = sum(v["orders"] for v in real_face.values())
    blocked = [m for m, v in real_face.items()
               if v["unit_cost_state"] == UNIT_COST_BLOCKED]
    verdict = "UNIT-COST-DRILL-GREEN" if not violations else "UNIT-COST-DRILL-FAIL"

    return {
        "verdict": verdict,
        "note": DRILL_NOTE,
        "month_filter": month or "-",
        "rails": {
            "revenue_rows": len(revenue_rows),
            "mock_rows": len(mock_rows),
            "l1_rows": len(l1_rows),
            "sha_unchanged": not changed,
            "sha_before": before,
            "sha_after": after,
        },
        "real_revenue_face": real_face,
        "mock_revenue_face": mock_face,
        "real_orders": total_orders,
        "unit_cost_blocked_months": blocked,
        "cost_face_unexercised": {
            "real": [m for m, v in real_face.items()
                     if v["cost_face"] == "COST-FACE-UNEXERCISED"],
            "mock": [m for m, v in mock_face.items()
                     if v["cost_face"] == "COST-FACE-UNEXERCISED"],
        },
        "simulated_rows": simulated,
        "self_use_l1": {
            "advisory_only": True,
            "tokens_local": self_use_tokens,
            "cost_b_cny": self_use_cost,
            "note": "self-use QA probe tokens are not a commercial denominator",
        },
        "violations": violations,
        "checked_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }


def render(report):
    lines = []
    lines.append("UNIT-COST DRILL  verdict=%s  month=%s"
                 % (report["verdict"], report["month_filter"]))
    rails = report["rails"]
    lines.append("rails  revenue=%d  mock=%d  l1=%d  sha_unchanged=%s"
                 % (rails["revenue_rows"], rails["mock_rows"],
                    rails["l1_rows"], rails["sha_unchanged"]))
    lines.append("real revenue rail face:")
    if not report["real_revenue_face"]:
        lines.append("  (no rows -- planned-state, nothing booked yet)")
    for mon, v in sorted(report["real_revenue_face"].items()):
        lines.append("  %s orders=%d price=%.4f net=%.4f refund_prov=%.4f "
                     "fee=%.4f cost=%.4f gross=%.4f tokens=%d "
                     "denom_mtok=%.6f quota_mtok=%.6f gen_mtok=%.6f "
                     "quota_blocked=%d unit_cost=%s value=%s"
                     % (mon, v["orders"], v["price_cny"], v["net_cny"],
                        v["refund_provision_cny"], v["platform_fee_cny"],
                        v["cost_cny"], v["gross_cny"], v["denominator_tokens"],
                        v["denominator_mtok"], v["quota_mtok"],
                        v["generation_mtok"], v["quota_blocked_rows"],
                        v["unit_cost_state"],
                        ("%.4f" % v["unit_cost_cny_per_mtok"]
                         if v["unit_cost_cny_per_mtok"] is not None else "n/a")))
        for why in v["denominator_block_reasons"]:
            lines.append("    blocked: " + why)
        if v.get("unit_cost_note"):
            lines.append("    note: " + v["unit_cost_note"])
        for cls, c in sorted(v.get("by_class", {}).items()):
            lines.append("    class %-10s orders=%d net=%.4f cost=%.4f "
                         "gross=%.4f margin=%s%%"
                         % (cls, c["orders"], c["net_cny"], c["cost_cny"],
                            c["gross_cny"],
                            "-" if c["gross_margin_pct"] is None
                            else c["gross_margin_pct"]))
    lines.append("mock rail face (drill data, excluded from settlement):")
    for mon, v in sorted(report["mock_revenue_face"].items()):
        lines.append("  %s orders=%d price=%.4f net=%.4f refund_prov=%.4f "
                     "fee=%.4f cost=%.4f gross=%.4f tokens=%d "
                     "denom_mtok=%.6f quota_mtok=%.6f gen_mtok=%.6f "
                     "quota_blocked=%d unit_cost=%s value=%s"
                     % (mon, v["orders"], v["price_cny"], v["net_cny"],
                        v["refund_provision_cny"], v["platform_fee_cny"],
                        v["cost_cny"], v["gross_cny"], v["denominator_tokens"],
                        v["denominator_mtok"], v["quota_mtok"],
                        v["generation_mtok"], v["quota_blocked_rows"],
                        v["unit_cost_state"],
                        ("%.4f" % v["unit_cost_cny_per_mtok"]
                         if v["unit_cost_cny_per_mtok"] is not None else "n/a")))
        for why in v["denominator_block_reasons"]:
            lines.append("    blocked: " + why)
        if v.get("unit_cost_note"):
            lines.append("    note: " + v["unit_cost_note"])
        for cls, c in sorted(v.get("by_class", {}).items()):
            lines.append("    class %-10s orders=%d net=%.4f cost=%.4f "
                         "gross=%.4f margin=%s%%"
                         % (cls, c["orders"], c["net_cny"], c["cost_cny"],
                            c["gross_cny"],
                            "-" if c["gross_margin_pct"] is None
                            else c["gross_margin_pct"]))
    su = report["self_use_l1"]
    lines.append("self-use l1 (advisory, not settlement): tokens=%d cost_b=%.8f"
                 % (su["tokens_local"], su["cost_b_cny"]))
    sim = report.get("simulated_rows", {})
    lines.append("simulated denominator rows: mock=%d real=%d "
                 "(design values, not measurements)"
                 % (sim.get("mock", 0), sim.get("real", 0)))
    unex = report.get("cost_face_unexercised", {})
    if unex.get("real") or unex.get("mock"):
        lines.append("COST-FACE-UNEXERCISED months: real=%s mock=%s "
                     "(zero COGS on every row -- unit cost 0.0 means nothing)"
                     % (unex.get("real"), unex.get("mock")))
    if report["violations"]:
        lines.append("violations (%d):" % len(report["violations"]))
        for v in report["violations"]:
            lines.append("  - " + v)
    else:
        lines.append("violations=0")
    return "\n".join(lines)


# ------------------------------------------------------------- sensitivity

SENSITIVITY_BLOCKED = "SENSITIVITY-BLOCKED"
SENSITIVITY_OK = "SENSITIVITY-OK"
MARGIN_LINES = (90.0, 80.0, 70.0)


def sensitivity(face, net_cny, rates):
    """How gross margin moves with the unit cost, holding prices fixed.

    Returns the break-even rate (margin 0), the rate that still holds each
    margin line, and one point per supplied rate. Prices and denominators come
    from the month face; only the rate is varied.
    """
    denom = face["denominator_mtok"]
    out = {"month": face["month"], "net_cny": net_cny,
           "denom_mtok": denom, "points": []}
    if denom <= 0 or net_cny <= 0:
        out["state"] = SENSITIVITY_BLOCKED
        out["reason"] = "denominator or net revenue is zero"
        return out
    out["state"] = SENSITIVITY_OK
    out["breakeven_rate_cny_per_mtok"] = round(net_cny / denom, 4)
    out["rate_at_margin_cny_per_mtok"] = {
        str(int(m)): round(net_cny * (1.0 - m / 100.0) / denom, 4)
        for m in MARGIN_LINES}
    for rate in rates:
        cost = round(denom * rate, 4)
        out["points"].append({
            "rate_cny_per_mtok": rate,
            "cost_cny": cost,
            "gross_cny": round(net_cny - cost, 4),
            "margin_pct": round(100.0 * (net_cny - cost) / net_cny, 4),
        })
    return out


def sensitivity_rails(revenue_rows, mock_rows, rates, month=None):
    out = []
    real_face, _ = aggregate(revenue_rows, month=month)
    for mon, v in sorted(real_face.items()):
        s = sensitivity(v, v["net_cny"], rates)
        s["rail"] = "real"
        out.append(s)
    mock_face, _ = aggregate(mock_rows, month=month, mock_only=True)
    for mon, v in sorted(mock_face.items()):
        s = sensitivity(v, v["net_cny"], rates)
        s["rail"] = "mock"
        out.append(s)
    return out


def render_sensitivity(items):
    lines = ["UNIT-COST SENSITIVITY (prices fixed, rate varied)"]
    if not items:
        lines.append("  (no month face with rows)")
    for s in items:
        lines.append("%s %s %s net=%.4f denom_mtok=%.6f"
                     % (s["rail"], s["month"], s["state"],
                        s["net_cny"], s["denom_mtok"]))
        if s["state"] != SENSITIVITY_OK:
            lines.append("  blocked: " + s.get("reason", "-"))
            continue
        lines.append("  breakeven_rate=%.4f  rate_at_margin=%s"
                     % (s["breakeven_rate_cny_per_mtok"],
                        s["rate_at_margin_cny_per_mtok"]))
        for p in s["points"]:
            lines.append("  rate=%.2f cost=%.4f gross=%.4f margin=%.4f%%"
                         % (p["rate_cny_per_mtok"], p["cost_cny"],
                            p["gross_cny"], p["margin_pct"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- selftest

def _mock_denominator_mtok(row):
    """Quota rows carry amount_mtok; generation rows carry tokens_local."""
    if row.get("amount_mtok") is not None:
        try:
            return float(row["amount_mtok"]), "quota"
        except (TypeError, ValueError):
            return None, "bad-amount-mtok"
    tokens = row.get("tokens_local")
    try:
        tokens = float(tokens)
    except (TypeError, ValueError):
        return None, "missing-denominator"
    if tokens <= 0:
        return None, "missing-denominator"
    return tokens / 1000000.0, "generation"


def regen_mock_rows(rows, rate=MOCK_DESIGN_COST_RATE):
    """Return (rows_with_cost_keys, issues). Pure, no IO.

    Only the drill keys are written. Every other key is copied verbatim, so a
    rerun cannot silently move prices, dates or order ids.

    M202 contract: the channel a row belongs to is carried by the `channel`
    field only. `channel_note` is a human-readable note; readers must not
    decide the channel from its text, so writers are free to rename or omit it.
    """
    out, issues = [], []
    for row in rows:
        new = dict(row)
        mtok, kind = _mock_denominator_mtok(row)
        if mtok is None:
            issues.append("%s %s (no amount_mtok and tokens_local<=0)"
                          % (new.get("order_id"), kind))
            out.append(new)
            continue
        lane = row.get("lane")
        channel = MOCK_CHANNEL_BY_LANE.get(lane)
        if channel is None:
            # Unknown lane means we cannot say which line sold it; refuse rather
            # than default to "store" and quietly mislabel the face.
            issues.append("%s unknown lane=%r (no channel derivation)"
                          % (new.get("order_id"), lane))
            out.append(new)
            continue
        new["cost_rate_cny_per_mtok"] = rate
        new["amount"] = round(mtok * rate, 4)
        new["cost_simulated"] = True
        new["denominator_simulated"] = True
        new["channel"] = channel
        new["settlement_caliber"] = MOCK_SETTLEMENT_CALIBER
        note = MOCK_CHANNEL_NOTE.get(lane)
        if note:
            new["channel_note"] = note
        else:
            new.pop("channel_note", None)
        out.append(new)
    return out, issues


def render_mock_rail(rows):
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)


def regen_mock_rail(path=MOCK_RAIL, rate=MOCK_DESIGN_COST_RATE):
    """Write the derived cost keys onto the mock rail; idempotent by construction."""
    rows = _load_rows(path)
    new_rows, issues = regen_mock_rows(rows, rate)
    expected = render_mock_rail(new_rows)
    with open(path, "r", encoding="utf-8") as fh:
        before = fh.read()
    before_sha = _sha256(path)
    if issues:
        return {"verdict": "MOCK-RAIL-REJECTED", "rail": path,
                "rows": len(rows), "issues": issues,
                "sha256_before": before_sha, "sha256_after": before_sha}
    changed_other = []
    for old, new in zip(rows, new_rows):
        for key in sorted(set(old) | set(new)):
            if key in MOCK_DRILL_KEYS:
                continue
            if old.get(key) != new.get(key):
                changed_other.append("%s:%s" % (old.get("order_id"), key))
    if expected == before:
        return {"verdict": "MOCK-RAIL-NO-CHANGE", "rail": path,
                "rows": len(rows), "issues": [], "changed_other_keys": [],
                "sha256_before": before_sha, "sha256_after": before_sha}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(expected)
    os.replace(tmp, path)
    after_sha = _sha256(path)
    again, _ = regen_mock_rows(_load_rows(path), rate)
    idempotent = render_mock_rail(again) == expected
    return {"verdict": ("MOCK-RAIL-REGEN-OK" if (idempotent and not changed_other)
                        else "MOCK-RAIL-NOT-IDEMPOTENT"),
            "rail": path, "rows": len(rows), "issues": [],
            "changed_other_keys": changed_other,
            "sha256_before": before_sha, "sha256_after": after_sha}


def _fixture_rows():
    base = [
        {"order_id": "MOCK-A", "date": "2026-10-19", "price": 49.9,
         "refund_rate": 0.08, "fee_rate": 0.05, "net_price": 43.6126,
         "amount": 1.0, "fund_3090": 10.0, "tokens_local": 0,
         "tokens_api": 0, "cost_item": "MOCK-DRILL", "mock": True},
        {"order_id": "MOCK-B", "date": "2026-10-20", "price": 3.0,
         "refund_rate": 0.08, "fee_rate": 0.05, "net_price": 2.622,
         "amount": 0.0, "fund_3090": 10.0, "tokens_local": 200000,
         "tokens_api": 0, "cost_item": "MOCK-DRILL", "mock": True},
    ]
    return base


def _selftest():
    checks = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    rows = _fixture_rows()
    face, viol = aggregate(rows, month=None, mock_only=False)
    oct_face = face.get("2026-10")
    chk("S1 month grouping", oct_face is not None and oct_face["orders"] == 2)
    chk("S2 net math identity", abs(oct_face["net_cny"] - 46.2346) < 1e-6,
        "net=%.4f" % oct_face["net_cny"])
    chk("S3 refund+fee split",
        abs(oct_face["refund_provision_cny"] - 4.232) < 1e-6
        and abs(oct_face["platform_fee_cny"] - 2.4334) < 1e-4,
        "refund=%.4f fee=%.4f" % (oct_face["refund_provision_cny"],
                                  oct_face["platform_fee_cny"]))
    chk("S4 denominator counted", oct_face["denominator_tokens"] == 200000)
    measured = [dict(rows[1]), dict(rows[1])]
    measured[1]["order_id"] = "MOCK-C"
    measured[0]["tokens_local"] = 100000
    measured[1]["tokens_local"] = 100000
    measured[0]["amount"] = 0.5
    measured[1]["amount"] = 0.5
    mface0, _ = aggregate(measured, month=None, mock_only=False)
    mo = mface0["2026-10"]
    chk("S5 unit cost computed when denominator>0",
        mo["unit_cost_state"] == UNIT_COST_OK
        and abs(mo["unit_cost_cny_per_mtok"] - 5.0) < 1e-6,
        "state=%s unit=%s" % (mo["unit_cost_state"],
                              mo["unit_cost_cny_per_mtok"]))

    zero_rows = [dict(rows[0])]
    zface, _ = aggregate(zero_rows, month=None, mock_only=False)
    z = zface["2026-10"]
    chk("S6 denominator zero -> BLOCKED (no ZeroDivisionError)",
        z["unit_cost_state"] == UNIT_COST_BLOCKED and z["unit_cost_cny_per_mtok"] is None)

    mixed = [
        {"order_id": "MIX-Q", "date": "2026-10-21", "price": 9.9,
         "refund_rate": 0.08, "fee_rate": 0.05, "net_price": 8.6526,
         "amount": 0.5, "fund_3090": 0.0, "lane": "subscription",
         "sku": "seat.quota.month", "tokens_local": 0, "tokens_api": 0},
        {"order_id": "MIX-G", "date": "2026-10-21", "price": 3.0,
         "refund_rate": 0.08, "fee_rate": 0.05, "net_price": 2.622,
         "amount": 0.5, "fund_3090": 0.0, "lane": "gacha",
         "sku": "card.draw", "tokens_local": 500000, "tokens_api": 0},
    ]
    mface, _ = aggregate(mixed, month=None, mock_only=False)
    m = mface["2026-10"]
    chk("S14 quota row without amount_mtok makes the month PARTIAL",
        m["unit_cost_state"] == "UNIT-COST-PARTIAL"
        and m["quota_blocked_rows"] == 1, str(m["unit_cost_state"]))
    chk("S15 generation-only denominator still prices the month",
        abs(m["denominator_mtok"] - 0.5) < 1e-9
        and abs(m["unit_cost_cny_per_mtok"] - 2.0) < 1e-6,
        "denom=%s unit=%s" % (m["denominator_mtok"],
                              m["unit_cost_cny_per_mtok"]))
    mixed[0]["amount_mtok"] = 0.25
    m2 = aggregate(mixed, month=None, mock_only=False)[0]["2026-10"]
    chk("S16 quota allowance joins the denominator once granted",
        abs(m2["quota_mtok"] - 0.25) < 1e-9
        and abs(m2["denominator_mtok"] - 0.75) < 1e-9
        and m2["unit_cost_state"] == "UNIT-COST-CALCULATED",
        str(m2["denominator_mtok"]))
    zero_cost = aggregate([{"order_id": "Z1", "date": "2026-10-22",
                            "price": 1.0, "refund_rate": 0.0,
                            "fee_rate": 0.0, "net_price": 1.0,
                            "amount": 0.0, "fund_3090": 0.0,
                            "lane": "gacha", "sku": "d",
                            "tokens_local": 1000, "tokens_api": 0}])[0]["2026-10"]
    chk("S17 zero cost side is labelled, not read as a measured unit cost",
        zero_cost["unit_cost_cny_per_mtok"] == 0.0
        and "unit_cost_note" in zero_cost, str(zero_cost.get("unit_cost_note")))
    chk("S18 all-zero COGS month is marked COST-FACE-UNEXERCISED",
        zero_cost["cost_face"] == "COST-FACE-UNEXERCISED",
        zero_cost["cost_face"])
    priced = aggregate([{"order_id": "Z2", "date": "2026-10-22",
                         "price": 1.0, "refund_rate": 0.0, "fee_rate": 0.0,
                         "net_price": 1.0, "amount": 0.3, "fund_3090": 0.0,
                         "lane": "gacha", "sku": "d",
                         "tokens_local": 500000, "tokens_api": 0}])[0]["2026-10"]
    chk("S19 priced month is COST-FACE-EXERCISED with a real unit cost",
        priced["cost_face"] == "COST-FACE-EXERCISED"
        and abs(priced["unit_cost_cny_per_mtok"] - 0.6) < 1e-6,
        "%s %s" % (priced["cost_face"], priced["unit_cost_cny_per_mtok"]))
    mix = aggregate([
        {"order_id": "CQ", "date": "2026-10-23", "price": 100.0,
         "refund_rate": 0.0, "fee_rate": 0.0, "net_price": 100.0,
         "amount": 10.0, "fund_3090": 0.0, "lane": "subscription",
         "sku": "seat.quota", "amount_mtok": 1.0},
        {"order_id": "CG", "date": "2026-10-23", "price": 50.0,
         "refund_rate": 0.0, "fee_rate": 0.0, "net_price": 50.0,
         "amount": 25.0, "fund_3090": 0.0, "lane": "gacha",
         "sku": "card", "tokens_local": 1000},
    ])[0]["2026-10"]
    chk("S20 per-class split isolates quota vs generation economics",
        abs(mix["by_class"]["quota"]["gross_margin_pct"] - 90.0) < 0.01
        and abs(mix["by_class"]["generation"]["gross_margin_pct"] - 50.0) < 0.01,
        str(mix["by_class"]))
    sens = sensitivity({"month": "2026-10", "denominator_mtok": 1.0},
                       100.0, [0.5, 10.0, 100.0])
    chk("S21 sensitivity solves breakeven and margin lines",
        sens["state"] == SENSITIVITY_OK
        and abs(sens["breakeven_rate_cny_per_mtok"] - 100.0) < 1e-6
        and abs(sens["rate_at_margin_cny_per_mtok"]["90"] - 10.0) < 1e-6,
        str(sens["rate_at_margin_cny_per_mtok"]))
    chk("S22 sensitivity points follow net minus denom times rate",
        abs(sens["points"][0]["cost_cny"] - 0.5) < 1e-9
        and abs(sens["points"][0]["margin_pct"] - 99.5) < 1e-6,
        str(sens["points"][0]))
    blocked_sens = sensitivity({"month": "2026-10", "denominator_mtok": 0.0},
                               100.0, [0.5])
    chk("S23 zero denominator blocks sensitivity instead of dividing",
        blocked_sens["state"] == SENSITIVITY_BLOCKED)

    dup_rows = [dict(rows[0]), dict(rows[0])]
    _, dviol = aggregate(dup_rows, month=None, mock_only=False)
    chk("S7 duplicate order id detected",
        any(v.startswith("duplicate-order-id MOCK-A") for v in dviol), str(dviol))

    bad_rows = [dict(rows[1])]
    bad_rows[0]["net_price"] = 99.0
    _, bviol = aggregate(bad_rows, month=None, mock_only=False)
    chk("S8 net math drift detected",
        any(v.startswith("net-math-mismatch") for v in bviol), str(bviol))

    tmpdir = tempfile.mkdtemp(prefix="unitcost-selftest-")
    try:
        rev = os.path.join(tmpdir, "revenue.jsonl")
        with open(rev, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"order_id": "MOCK-X", "date": "2026-10-01",
                                 "price": 1.0, "refund_rate": 0.0,
                                 "fee_rate": 0.0, "net_price": 1.0,
                                 "amount": 0.0, "fund_3090": 0.0,
                                 "tokens_local": 0, "tokens_api": 0,
                                 "cost_item": "MOCK-DRILL", "mock": True},
                                sort_keys=True) + "\n")
        mock = os.path.join(tmpdir, "mock.jsonl")
        shutil.copyfile(rev, mock)
        l1 = os.path.join(tmpdir, "l1.jsonl")
        with open(l1, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "l1", "tokens_local": 21,
                                 "amount_mtok": 0.000021,
                                 "cost_b_cny": None}, sort_keys=True) + "\n")
        report = run_drill(rev, mock, l1)
        chk("S9 mock contamination in real rail detected",
            any(v.startswith("mock-contamination") for v in report["violations"]),
            str(report["violations"]))
        chk("S10 rails unchanged (sha proof)", report["rails"]["sha_unchanged"] is True)
        chk("S11 self-use l1 excluded from denominator",
            report["self_use_l1"]["tokens_local"] == 21
            and all(v["denominator_tokens"] == 0
                    for v in report["real_revenue_face"].values()))
        report2 = run_drill(rev, mock, l1)
        a = json.loads(json.dumps(report, sort_keys=True))
        b = json.loads(json.dumps(report2, sort_keys=True))
        a.pop("checked_at")
        b.pop("checked_at")
        chk("S12 determinism (same input -> same report)", a == b)
        text = render(report)
        chk("S13 render names verdict", "UNIT-COST-DRILL-" in text)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    drill_rows = [
        {"order_id": "MOCK-Q", "date": "2026-10-19", "price": 49.9,
         "refund_rate": 0.08, "fee_rate": 0.05, "net_price": 43.6126,
         "amount": 0.0, "fund_3090": 0.0, "lane": "subscription",
         "tokens_local": 0, "tokens_api": 0, "amount_mtok": 0.42,
         "cost_item": "MOCK-DRILL", "mock": True},
        {"order_id": "MOCK-G", "date": "2026-10-19", "price": 3.0,
         "refund_rate": 0.08, "fee_rate": 0.05, "net_price": 2.622,
         "amount": 0.0, "fund_3090": 0.0, "lane": "gacha",
         "tokens_local": 1200, "tokens_api": 0,
         "cost_item": "MOCK-DRILL", "mock": True},
        {"order_id": "MOCK-X", "date": "2026-10-19", "price": 1.0,
         "amount": 0.0, "tokens_local": 0, "tokens_api": 0,
         "cost_item": "MOCK-DRILL", "mock": True},
        {"order_id": "MOCK-Y", "date": "2026-10-19", "price": 2.0,
         "amount": 0.0, "fund_3090": 0.0, "lane": "mystery",
         "tokens_local": 0, "tokens_api": 0, "amount_mtok": 0.1,
         "cost_item": "MOCK-DRILL", "mock": True},
        {"order_id": "MOCK-Z", "date": "2026-10-20", "price": 349.0,
         "amount": 0.0, "fund_3090": 0.0, "lane": "b2b_direct",
         "tokens_local": 0, "tokens_api": 0, "amount_mtok": 0.18,
         "cost_item": "MOCK-DRILL", "mock": True},
    ]
    regen1, regen_issues = regen_mock_rows(drill_rows)
    regen2, _ = regen_mock_rows(regen1)
    chk("S24 regen is idempotent",
        render_mock_rail(regen1) == render_mock_rail(regen2))
    chk("S25 quota row cost = amount_mtok x design rate",
        abs(regen1[0]["amount"] - 0.252) < 1e-9
        and abs(regen1[0]["cost_rate_cny_per_mtok"] - 0.6) < 1e-9,
        "amount=%s" % regen1[0]["amount"])
    chk("S26 generation row cost = tokens_local/1e6 x rate",
        abs(regen1[1]["amount"] - 0.0007) < 1e-9,
        "amount=%s" % regen1[1]["amount"])
    chk("S27 no denominator -> issue named, no cost keys invented",
        any("MOCK-X" in i for i in regen_issues)
        and regen1[2]["amount"] == 0.0
        and "cost_simulated" not in regen1[2], str(regen_issues))
    chk("S28 regen leaves every other key untouched",
        regen1[0]["price"] == 49.9 and regen1[0]["net_price"] == 43.6126
        and regen1[1]["date"] == "2026-10-19")
    chk("S29 lane -> channel/caliber derived (store rows)",
        regen1[0].get("channel") == "store"
        and regen1[0].get("settlement_caliber") == "B"
        and regen1[1].get("channel") == "store",
        "row0=%s cal=%s row1=%s" % (regen1[0].get("channel"),
                                    regen1[0].get("settlement_caliber"),
                                    regen1[1].get("channel")))
    chk("S30 unknown lane -> issue named, no channel invented",
        any("MOCK-Y" in i for i in regen_issues)
        and "channel" not in regen1[3]
        and "settlement_caliber" not in regen1[3], str(regen_issues))
    chk("S31 b2b_direct -> declared channel=b2b (note is annotation only)",
        regen1[4].get("channel") == "b2b"
        and regen1[4].get("channel_note") == "b2b-direct-signed"
        and regen1[0].get("channel_note") is None,
        "row4=%s note=%s" % (regen1[4].get("channel"),
                             regen1[4].get("channel_note")))
    chk("S31b channel survives note removal (decision is the field, not the text)",
        regen_mock_rows([{k: v for k, v in drill_rows[4].items() if k != "channel_note"}])[0][0]
        .get("channel") == "b2b")
    chk("S32 channel fill does not promote drill rows to real",
        all(r.get("mock") is True for r in regen1))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           (" | " + detail) if (not ok and detail) else ""))
    print("SELFTEST %d/%d %s" % (len(checks) - len(failed), len(checks),
                                 "PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unit-cost monthly drill (M99)")
    sub = parser.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="run the drill against live rails (read-only)")
    run.add_argument("--month", default=None, help="YYYY-MM filter")
    run.add_argument("--revenue", default=REVENUE_RAIL)
    run.add_argument("--mock", default=MOCK_RAIL)
    run.add_argument("--l1", default=L1_RAIL)
    run.add_argument("--json-out", default=None)
    run.add_argument("--text-out", default=None)
    run.add_argument("--print-json", action="store_true")

    sub.add_parser("selftest", help="in-memory fixtures, no live file writes")

    re_gen = sub.add_parser("regen",
                            help="rewrite the derived cost keys on the mock rail")
    re_gen.add_argument("--mock", default=MOCK_RAIL)
    re_gen.add_argument("--rate", default=MOCK_DESIGN_COST_RATE)
    re_gen.add_argument("--json-out", default=None)

    sens = sub.add_parser("sensitivity",
                          help="gross margin versus unit cost, prices fixed")
    sens.add_argument("--rates", default="0.4,0.5,0.6,0.8",
                      help="comma separated CNY per Mtok")
    sens.add_argument("--month", default=None)
    sens.add_argument("--revenue", default=REVENUE_RAIL)
    sens.add_argument("--mock", default=MOCK_RAIL)
    sens.add_argument("--json-out", default=None)
    sens.add_argument("--text-out", default=None)

    args = parser.parse_args(argv)
    if args.cmd == "selftest":
        return _selftest()
    if args.cmd == "regen":
        try:
            rate = float(args.rate)
        except (TypeError, ValueError):
            print("rate must be a number")
            return 2
        result = regen_mock_rail(args.mock, rate)
        text = "%s rows=%d rate=%.4f" % (result["verdict"], result["rows"], rate)
        if result["issues"]:
            text += " issues=" + ";".join(result["issues"])
        if result.get("changed_other_keys"):
            text += " changed_other=" + ",".join(result["changed_other_keys"])
        print(text)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(result, fh, sort_keys=True, indent=2,
                          ensure_ascii=True)
                fh.write("\n")
        return 0 if result["verdict"] in ("MOCK-RAIL-REGEN-OK",
                                          "MOCK-RAIL-NO-CHANGE") else 3
    if args.cmd == "sensitivity":
        try:
            rates = [float(x) for x in str(args.rates).split(",") if x.strip()]
        except ValueError:
            print("rates must be comma separated numbers")
            return 2
        items = sensitivity_rails(_load_rows(args.revenue),
                                  _load_rows(args.mock), rates,
                                  month=args.month)
        text = render_sensitivity(items)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump({"rates": rates, "items": items}, fh, sort_keys=True,
                          indent=2, ensure_ascii=True)
                fh.write("\n")
        if args.text_out:
            with open(args.text_out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        print(text)
        return 0
    if args.cmd != "run":
        parser.print_help()
        return 2

    report = run_drill(args.revenue, args.mock, args.l1, month=args.month)
    text = render(report)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, sort_keys=True, indent=2, ensure_ascii=True)
            fh.write("\n")
    if args.text_out:
        with open(args.text_out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if args.print_json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True))
    else:
        print(text)
    return 0 if report["verdict"] == "UNIT-COST-DRILL-GREEN" else 3


if __name__ == "__main__":
    sys.exit(main())