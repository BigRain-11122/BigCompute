"""Gacha monthly spending cap gate (queue M101, drill only).

blind-box-gacha-compliance §7.2 requires: monthly aggregate {user, month,
sum_cny} against monthly_cap_cny -> block the payment (blocked_over_cap) +
second confirmation + hand off to human support; the only bypass is the
explicit --ceo-approved channel, same shape as the quota monthly budget gate
(BC-F-20260925-01 ②).

This is the drill, not the live store. It runs on a synthetic draw sequence and
nothing is booked on a real rail. The interesting part is that it refuses to
fail open in two ways that a naive "< cap" comparison gets wrong:

  * cap unset  -> must NOT default to "allow". The value is still waiting on the
                  CEO pricing confirmation, and per §7.1 an unset value means
                  the SKU may not go on sale at all. An approval cannot conjure
                  a cap that was never set.
  * exact cap  -> a draw that lands exactly on the cap is charged; the next one
                  is blocked because the projected total would exceed it.

Money is compared in integer cents so the boundary is exact instead of relying
on float luck.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "state")
DEFAULT_LEDGER = os.path.join(STATE, "gacha-cap-drill-ledger.jsonl")

# Drill design value only. §7.1 keeps monthly_cap_cny blank until the CEO
# pricing confirmation; every drill row therefore carries cap_source so a real
# value can never hide behind a design one.
DESIGN_CAP_CNY = 200.00
DESIGN_CAP_SOURCE = "drill-design-value"

ALLOW = "ALLOW"
BLOCKED_OVER_CAP = "BLOCKED_OVER_CAP"
BLOCKED_CAP_UNSET = "BLOCKED_CAP_UNSET"
BLOCKED_BAD_AMOUNT = "BLOCKED_BAD_AMOUNT"
BYPASS_CEO_APPROVED = "BYPASS_CEO_APPROVED"
DUPLICATE = "DUPLICATE"

CHARGED = (ALLOW, BYPASS_CEO_APPROVED)


def to_cents(value):
    """Exact cents from a CNY number. Returns None for anything non-numeric."""
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


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


def month_sum_cents(rows, user, month):
    """Charged spend for one user in one month. Blocked attempts never count."""
    total = 0
    for row in rows:
        if row.get("user") != user or row.get("month") != month:
            continue
        if row.get("verdict") in CHARGED and row.get("charged") is True:
            total += int(row.get("price_cents") or 0)
    return total


def decide(rows, draw, cap_cny):
    """Gate one draw against the monthly cap. Pure function.

    draw = {draw_id, user, month, price_cny, ceo_approved}
    """
    draw_id = draw.get("draw_id")
    user = draw.get("user")
    month = draw.get("month")
    price = to_cents(draw.get("price_cny"))

    if any(row.get("draw_id") == draw_id for row in rows):
        return {
            "draw_id": draw_id,
            "verdict": DUPLICATE,
            "charged": False,
            "reason": "draw_id already recorded; replay is not charged twice",
        }

    if price is None or price <= 0:
        return {
            "draw_id": draw_id,
            "verdict": BLOCKED_BAD_AMOUNT,
            "charged": False,
            "reason": "price must be a positive amount",
        }

    cap = to_cents(cap_cny)
    # Unset cap is a hard stop, checked before the approval channel: a value
    # waiting on the CEO is not something an operator can waive.
    if cap is None or cap <= 0:
        return {
            "draw_id": draw_id,
            "verdict": BLOCKED_CAP_UNSET,
            "charged": False,
            "reason": "monthly_cap_cny not set; §7.1 keeps the SKU unsellable "
                      "until the pricing confirmation lands",
        }

    spent = month_sum_cents(rows, user, month)
    projected = spent + price

    if projected > cap:
        if draw.get("ceo_approved"):
            return {
                "draw_id": draw_id,
                "verdict": BYPASS_CEO_APPROVED,
                "charged": True,
                "spent_cents": spent,
                "price_cents": price,
                "projected_cents": projected,
                "cap_cents": cap,
                "over_cap_cents": projected - cap,
                "reason": "explicit CEO-approved channel; overage recorded",
            }
        return {
            "draw_id": draw_id,
            "verdict": BLOCKED_OVER_CAP,
            "charged": False,
            "spent_cents": spent,
            "price_cents": price,
            "projected_cents": projected,
            "cap_cents": cap,
            "over_cap_cents": projected - cap,
            "requires": ["second_confirmation", "handoff_to_support"],
            "reason": "projected monthly spend would exceed the cap",
        }

    return {
        "draw_id": draw_id,
        "verdict": ALLOW,
        "charged": True,
        "spent_cents": spent,
        "price_cents": price,
        "projected_cents": projected,
        "cap_cents": cap,
        "remaining_cents": cap - projected,
    }


def make_row(draw, outcome, cap_source):
    row = dict(outcome)
    row["user"] = draw.get("user")
    row["month"] = draw.get("month")
    row["price_cny"] = draw.get("price_cny")
    row["ceo_approved"] = bool(draw.get("ceo_approved"))
    row["cap_source"] = cap_source
    row["synthetic"] = True
    return row


def append_row(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def render(outcome):
    parts = [outcome["verdict"]]
    for key in ("spent_cents", "price_cents", "projected_cents",
                "cap_cents", "remaining_cents", "over_cap_cents"):
        if key in outcome:
            parts.append("%s=%.2f" % (key, outcome[key] / 100.0))
    if outcome.get("requires"):
        parts.append("requires=" + ",".join(outcome["requires"]))
    if outcome.get("reason"):
        parts.append("reason=" + outcome["reason"])
    return " ".join(parts)


def cmd_attempt(args):
    path = args.ledger
    rows = load_rows(path)
    draw = {
        "draw_id": args.draw_id,
        "user": args.user,
        "month": args.month,
        "price_cny": args.price,
        "ceo_approved": args.ceo_approved,
    }
    outcome = decide(rows, draw, args.cap)
    if outcome["verdict"] != DUPLICATE:
        append_row(path, make_row(draw, outcome, args.cap_source))
    print(render(outcome))
    return 0 if outcome["verdict"] in CHARGED else 2


def cmd_summary(args):
    rows = load_rows(args.ledger)
    spent = month_sum_cents(rows, args.user, args.month)
    cap = to_cents(args.cap)
    print("user=%s month=%s spent=%.2f cap=%s rows=%d" % (
        args.user, args.month, spent / 100.0,
        ("%.2f" % (cap / 100.0)) if cap else "UNSET", len(rows)))
    return 0


def run_drill(path, out_json, out_txt):
    """Synthetic sequence: boundary, overage, bypass, unset cap, replay."""
    if os.path.isfile(path):
        os.remove(path)

    cap = DESIGN_CAP_CNY
    steps = [
        ("DA-1", "anon-a", "2026-11", 60.00, False),
        ("DA-2", "anon-a", "2026-11", 60.00, False),
        ("DA-3", "anon-a", "2026-11", 60.00, False),   # 180.00, under
        ("DA-4", "anon-a", "2026-11", 40.00, False),   # 220.00 -> blocked
        ("DA-5", "anon-a", "2026-11", 20.00, False),   # 200.00 exactly -> charged
        ("DA-6", "anon-a", "2026-11", 40.00, True),    # approved bypass
        ("DA-7", "anon-a", "2026-11", 40.00, False),   # replay -> DUPLICATE check below
        ("DB-1", "anon-b", "2026-11", 50.00, False),   # other user untouched
        ("DA-8", "anon-a", "2026-12", 200.00, False),  # other month untouched
        ("DA-9", "anon-a", "2026-12", 0.01, False),    # next month over exact cap
        ("DC-1", "anon-c", "2026-11", 100.00, False),  # exact boundary
        ("DC-2", "anon-c", "2026-11", 100.00, False),  # lands on cap -> charged
        ("DC-3", "anon-c", "2026-11", 0.01, False),    # one cent over -> blocked
        ("DD-1", "anon-d", "2026-11", 0.00, False),    # bad amount
    ]

    results = []
    for draw_id, user, month, price, approved in steps:
        draw = {"draw_id": draw_id, "user": user, "month": month,
                "price_cny": price, "ceo_approved": approved}
        rows = load_rows(path)
        outcome = decide(rows, draw, cap)
        results.append(dict(outcome, user=user, month=month,
                            price_cny=price, ceo_approved=approved))
        if outcome["verdict"] != DUPLICATE:
            append_row(path, make_row(draw, outcome, DESIGN_CAP_SOURCE))

    # replay the first draw to prove idempotency
    rows = load_rows(path)
    replay = decide(rows, {"draw_id": "DA-1", "user": "anon-a",
                           "month": "2026-11", "price_cny": 60.00}, cap)
    results.append(dict(replay, user="anon-a", month="2026-11",
                        price_cny=60.00, replay_of="DA-1"))

    # unset cap: same spend, no configured value -> fail closed, no bypass
    unset = decide(rows, {"draw_id": "DE-1", "user": "anon-e",
                          "month": "2026-11", "price_cny": 1.00,
                          "ceo_approved": True}, None)
    results.append(dict(unset, user="anon-e", month="2026-11",
                        price_cny=1.00, ceo_approved=True,
                        case="unset-cap-with-approval"))

    totals = {
        "anon-a/2026-11": month_sum_cents(rows, "anon-a", "2026-11") / 100.0,
        "anon-b/2026-11": month_sum_cents(rows, "anon-b", "2026-11") / 100.0,
        "anon-a/2026-12": month_sum_cents(rows, "anon-a", "2026-12") / 100.0,
        "anon-c/2026-11": month_sum_cents(rows, "anon-c", "2026-11") / 100.0,
    }

    verdicts = [r["verdict"] for r in results]
    payload = {
        "cap_cny": cap,
        "cap_source": DESIGN_CAP_SOURCE,
        "ledger": os.path.relpath(path, HERE),
        "steps": len(results),
        "verdict_counts": {v: verdicts.count(v) for v in sorted(set(verdicts))},
        "totals_cny": totals,
        "results": results,
    }
    with open(out_json, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

    lines = ["GACHA-CAP-DRILL cap=%.2f source=%s steps=%d" % (
        cap, DESIGN_CAP_SOURCE, len(results))]
    for r in results:
        lines.append("  %-4s %s" % (r.get("draw_id"), render(r)))
    lines.append("totals: " + " ".join(
        "%s=%.2f" % (k, v) for k, v in sorted(totals.items())))
    with open(out_txt, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")

    print(lines[0])
    print("  allow=%d blocked_over_cap=%d bypass=%d unset=%d duplicate=%d bad=%d" % (
        payload["verdict_counts"].get(ALLOW, 0),
        payload["verdict_counts"].get(BLOCKED_OVER_CAP, 0),
        payload["verdict_counts"].get(BYPASS_CEO_APPROVED, 0),
        payload["verdict_counts"].get(BLOCKED_CAP_UNSET, 0),
        payload["verdict_counts"].get(DUPLICATE, 0),
        payload["verdict_counts"].get(BLOCKED_BAD_AMOUNT, 0)))
    print("  anon-a/2026-11 spent=%.2f anon-c/2026-11 spent=%.2f" % (
        totals["anon-a/2026-11"], totals["anon-c/2026-11"]))
    return 0


def selftest():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    def rows_of(*pairs):
        return [{"user": u, "month": m, "price_cents": int(p * 100),
                 "verdict": ALLOW, "charged": True, "draw_id": "x%d" % i}
                for i, (u, m, p) in enumerate(pairs)]

    base = {"draw_id": "S1", "user": "u", "month": "2026-11",
            "price_cny": 10.00, "ceo_approved": False}

    # S1 under cap allowed
    out = decide([], base, 100.00)
    check("S1 under cap -> ALLOW", out["verdict"] == ALLOW and out["charged"])
    check("S1 remaining exact", out.get("remaining_cents") == 9000)

    # S2 exact boundary charged
    rows = rows_of(("u", "2026-11", 90.00))
    out = decide(rows, dict(base, draw_id="S2", price_cny=10.00), 100.00)
    check("S2 lands exactly on cap -> ALLOW",
          out["verdict"] == ALLOW and out.get("remaining_cents") == 0)

    # S3 one cent over blocked with both follow-ups
    out = decide(rows, dict(base, draw_id="S3", price_cny=10.01), 100.00)
    check("S3 one cent over -> BLOCKED_OVER_CAP",
          out["verdict"] == BLOCKED_OVER_CAP and not out["charged"])
    check("S3 requires confirm+handoff",
          out.get("requires") == ["second_confirmation", "handoff_to_support"])
    check("S3 overage named", out.get("over_cap_cents") == 1)

    # S4 unset cap fails closed, even with approval
    for cap in (None, 0, "abc", ""):
        out = decide([], dict(base, draw_id="S4", ceo_approved=True), cap)
        check("S4 cap=%r -> BLOCKED_CAP_UNSET" % (cap,),
              out["verdict"] == BLOCKED_CAP_UNSET and not out["charged"])

    # S5 approved channel books the overage
    out = decide(rows, dict(base, draw_id="S5", price_cny=20.00,
                            ceo_approved=True), 100.00)
    check("S5 approval bypasses over-cap and records overage",
          out["verdict"] == BYPASS_CEO_APPROVED and out["charged"]
          and out.get("over_cap_cents") == 1000)

    # S6 duplicate not charged twice
    dup_rows = [{"draw_id": "S6", "user": "u", "month": "2026-11",
                 "price_cents": 500, "verdict": ALLOW, "charged": True}]
    out = decide(dup_rows, dict(base, draw_id="S6"), 100.00)
    check("S6 replay -> DUPLICATE not charged",
          out["verdict"] == DUPLICATE and not out["charged"])

    # S7 bad amounts rejected
    for bad in (0, -5, None, "nan"):
        out = decide([], dict(base, draw_id="S7", price_cny=bad), 100.00)
        check("S7 price=%r -> BLOCKED_BAD_AMOUNT" % (bad,),
              out["verdict"] == BLOCKED_BAD_AMOUNT)

    # S8 user and month isolation
    mixed = rows_of(("u", "2026-11", 90.00), ("v", "2026-11", 90.00),
                    ("u", "2026-12", 90.00))
    check("S8 other user/month do not consume this cap",
          month_sum_cents(mixed, "u", "2026-11") == 9000)
    out = decide(mixed, dict(base, draw_id="S8", price_cny=10.00), 100.00)
    check("S8 isolated spend still allowed", out["verdict"] == ALLOW)

    # S9 blocked rows never count toward spend
    with_blocked = [{"user": "u", "month": "2026-11", "price_cents": 9999,
                     "verdict": BLOCKED_OVER_CAP, "charged": False,
                     "draw_id": "b1"}]
    check("S9 blocked attempts are not spend",
          month_sum_cents(with_blocked, "u", "2026-11") == 0)

    # S10 cents conversion is exact enough at the boundary
    check("S10 cent conversion exact",
          to_cents(0.1) + to_cents(0.2) == to_cents(0.3) == 30)

    # S11 determinism
    a = decide(rows, dict(base, draw_id="S11"), 100.00)
    b = decide(rows, dict(base, draw_id="S11"), 100.00)
    check("S11 deterministic", a == b)

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           (" :: " + detail) if (detail and not ok) else ""))
    print("selftest: %d/%d PASS" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gacha monthly cap gate (drill)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_attempt = sub.add_parser("attempt", help="gate one draw and record it")
    p_attempt.add_argument("--draw-id", required=True)
    p_attempt.add_argument("--user", required=True)
    p_attempt.add_argument("--month", required=True)
    p_attempt.add_argument("--price", required=True)
    p_attempt.add_argument("--cap", default=None)
    p_attempt.add_argument("--cap-source", default=DESIGN_CAP_SOURCE)
    p_attempt.add_argument("--ceo-approved", action="store_true")
    p_attempt.add_argument("--ledger", default=DEFAULT_LEDGER)
    p_attempt.set_defaults(func=cmd_attempt)

    p_sum = sub.add_parser("summary", help="monthly aggregate for one user")
    p_sum.add_argument("--user", required=True)
    p_sum.add_argument("--month", required=True)
    p_sum.add_argument("--cap", default=None)
    p_sum.add_argument("--ledger", default=DEFAULT_LEDGER)
    p_sum.set_defaults(func=cmd_summary)

    p_drill = sub.add_parser("drill", help="run the synthetic cap sequence")
    p_drill.add_argument("--ledger", default=DEFAULT_LEDGER)
    p_drill.add_argument("--json-out", default=None)
    p_drill.add_argument("--txt-out", default=None)
    p_drill.set_defaults(func=None)

    sub.add_parser("selftest", help="run fixtures").set_defaults(func=None)

    args = parser.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "drill":
        stamp = args.json_out or os.path.join(STATE, "gacha-cap-drill.json")
        txt = args.txt_out or os.path.join(STATE, "gacha-cap-drill.txt")
        return run_drill(args.ledger, stamp, txt)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())