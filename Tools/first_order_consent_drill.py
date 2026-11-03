#!/usr/bin/env python3
"""First-order "before the consumer accepts the service" prompt drill (M113 ①).

778 号令第十条第二款 has two halves:

    「应当在消费者接受服务前 和 自动展期、自动续费等日期前，以显著方式提请消费者注意。」

M102's renewal_reminder_drill.py judges the SECOND half (each renewal date).
This drill judges the FIRST half: at first order, the auto-renew terms must be
prominently surfaced *before* the consumer accepts the service.

The evidence is not a boolean we assert about ourselves — it is a recorded
prompt (accept_prompt_evidence) that must be ordered before acceptance:

  prompted_at   when the terms were shown
  accepted_at   when the consumer accepted / the order was placed
  significant   whether the terms were presented prominently
  channel       where (a significant channel must be nameable)
  terms_shown   which points were shown: auto_renew / lead_days / cancel_path

States are deliberately separated. A missing timestamp is CANNOT-JUDGE, never a
silent pass; a non-auto-renew order carries no obligation (NOT-AUTO-RENEW);
and a prompt that lands on or after acceptance is PROMPT-NOT-BEFORE-ACCEPT,
because the statute says 接受服务前.

Usage:
  python Tools/first_order_consent_drill.py drill [--as-of YYYY-MM-DD]
  python Tools/first_order_consent_drill.py selftest
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "state")

REQUIRED_TERMS = ("auto_renew", "lead_days", "cancel_path")

OK = "OK"
NOT_AUTO_RENEW = "NOT-AUTO-RENEW"
NO_PROMPT_EVIDENCE = "NO-PROMPT-EVIDENCE"
PROMPT_NOT_BEFORE = "PROMPT-NOT-BEFORE-ACCEPT"
PROMPT_NOT_SIGNIFICANT = "PROMPT-NOT-SIGNIFICANT"
TERMS_INCOMPLETE = "TERMS-INCOMPLETE"
CANNOT_JUDGE = "CANNOT-JUDGE"

RED = (NO_PROMPT_EVIDENCE, PROMPT_NOT_BEFORE, PROMPT_NOT_SIGNIFICANT,
       TERMS_INCOMPLETE, CANNOT_JUDGE)
BLUE = (OK, NOT_AUTO_RENEW)


def parse_dt(value):
    """ISO-8601 date or datetime -> datetime, else None. Deliberately strict."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            return datetime.datetime.combine(
                datetime.date.fromisoformat(text), datetime.time(0, 0))
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def judge(order, as_of=None):
    """Verdict for one first order. Pure function."""
    order_id = order.get("order_id")

    if order.get("auto_renew") is not True:
        return {"order_id": order_id, "verdict": NOT_AUTO_RENEW,
                "reason": "manual renewal; the pre-acceptance prompt duty does not apply"}

    evidence = order.get("accept_prompt_evidence")
    if not isinstance(evidence, dict) or not evidence:
        return {"order_id": order_id, "verdict": NO_PROMPT_EVIDENCE,
                "reason": "no accept_prompt_evidence record; §三.2 前半句 unproven"}

    prompted = parse_dt(evidence.get("prompted_at"))
    accepted = parse_dt(order.get("accepted_at"))
    if prompted is None:
        return {"order_id": order_id, "verdict": CANNOT_JUDGE,
                "reason": "prompted_at missing or unparseable; refusing to assume"}
    if accepted is None:
        return {"order_id": order_id, "verdict": CANNOT_JUDGE,
                "reason": "accepted_at missing or unparseable; can't prove ordering"}

    # A naive timestamp next to an offset one is not "close enough": comparing
    # them raises, and guessing an offset would invent evidence.
    if (prompted.tzinfo is None) != (accepted.tzinfo is None):
        return {"order_id": order_id, "verdict": CANNOT_JUDGE,
                "reason": "prompted_at and accepted_at disagree on timezone; "
                          "ordering is unprovable as written"}

    if evidence.get("significant") is not True:
        return {"order_id": order_id, "verdict": PROMPT_NOT_SIGNIFICANT,
                "reason": "terms were shown but not prominently (显著方式)"}

    channel = evidence.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        return {"order_id": order_id, "verdict": CANNOT_JUDGE,
                "reason": "prompt channel unnamed; prominence can't be evidenced"}

    if prompted >= accepted:
        return {"order_id": order_id, "verdict": PROMPT_NOT_BEFORE,
                "prompted_at": prompted.isoformat(),
                "accepted_at": accepted.isoformat(),
                "reason": "prompt did not precede acceptance (statute: 接受服务前)"}

    shown = evidence.get("terms_shown")
    shown_set = set(shown) if isinstance(shown, list) else set()
    missing = [t for t in REQUIRED_TERMS if t not in shown_set]
    if missing:
        return {"order_id": order_id, "verdict": TERMS_INCOMPLETE,
                "missing_terms": missing,
                "reason": "prompt omits points the consumer must see: " + ",".join(missing)}

    return {"order_id": order_id, "verdict": OK,
            "lead_minutes": int((accepted - prompted).total_seconds() // 60),
            "channel": channel,
            "reason": "terms shown prominently before acceptance with all required points"}


def summarize(results):
    counts = {}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    red = [r["order_id"] for r in results if r["verdict"] in RED]
    return {
        "rows": len(results),
        "counts": counts,
        "red": red,
        "verdict": "FIRST-ORDER-CONSENT-RED" if red else "FIRST-ORDER-CONSENT-GREEN",
    }


def synthetic_orders():
    """Planted set: every state appears, so a missed obligation is visible."""
    return [
        {"order_id": "FO-OK", "auto_renew": True,
         "accepted_at": "2026-11-04T10:00:00+08:00",
         "accept_prompt_evidence": {
             "prompted_at": "2026-11-04T09:58:00+08:00", "significant": True,
             "channel": "in-app-checkout-banner",
             "terms_shown": ["auto_renew", "lead_days", "cancel_path"]}},
        {"order_id": "FO-OK-SAME-MINUTE", "auto_renew": True,
         "accepted_at": "2026-11-04T10:00:30+08:00",
         "accept_prompt_evidence": {
             "prompted_at": "2026-11-04T10:00:00+08:00", "significant": True,
             "channel": "sms", "terms_shown": ["auto_renew", "lead_days", "cancel_path"]}},
        {"order_id": "FO-NO-EVIDENCE", "auto_renew": True,
         "accepted_at": "2026-11-04T10:00:00+08:00"},
        {"order_id": "FO-AFTER-ACCEPT", "auto_renew": True,
         "accepted_at": "2026-11-04T10:00:00+08:00",
         "accept_prompt_evidence": {
             "prompted_at": "2026-11-04T10:05:00+08:00", "significant": True,
             "channel": "in-app-checkout-banner",
             "terms_shown": ["auto_renew", "lead_days", "cancel_path"]}},
        {"order_id": "FO-AT-ACCEPT", "auto_renew": True,
         "accepted_at": "2026-11-04T10:00:00+08:00",
         "accept_prompt_evidence": {
             "prompted_at": "2026-11-04T10:00:00+08:00", "significant": True,
             "channel": "in-app-checkout-banner",
             "terms_shown": ["auto_renew", "lead_days", "cancel_path"]}},
        {"order_id": "FO-NOT-SIGNIFICANT", "auto_renew": True,
         "accepted_at": "2026-11-04T10:00:00+08:00",
         "accept_prompt_evidence": {
             "prompted_at": "2026-11-04T09:00:00+08:00", "significant": False,
             "channel": "terms-page-link",
             "terms_shown": ["auto_renew", "lead_days", "cancel_path"]}},
        {"order_id": "FO-TERMS-PARTIAL", "auto_renew": True,
         "accepted_at": "2026-11-04T10:00:00+08:00",
         "accept_prompt_evidence": {
             "prompted_at": "2026-11-04T09:50:00+08:00", "significant": True,
             "channel": "in-app-checkout-banner",
             "terms_shown": ["auto_renew"]}},
        {"order_id": "FO-TERMS-ABSENT", "auto_renew": True,
         "accepted_at": "2026-11-04T10:00:00+08:00",
         "accept_prompt_evidence": {
             "prompted_at": "2026-11-04T09:50:00+08:00", "significant": True,
             "channel": "in-app-checkout-banner"}},
        {"order_id": "FO-NO-CHANNEL", "auto_renew": True,
         "accepted_at": "2026-11-04T10:00:00+08:00",
         "accept_prompt_evidence": {
             "prompted_at": "2026-11-04T09:50:00+08:00", "significant": True,
             "channel": "",
             "terms_shown": ["auto_renew", "lead_days", "cancel_path"]}},
        {"order_id": "FO-BAD-TIME", "auto_renew": True,
         "accepted_at": "2026-11-04T10:00:00+08:00",
         "accept_prompt_evidence": {
             "prompted_at": "11/04/2026 09:50", "significant": True,
             "channel": "in-app-checkout-banner",
             "terms_shown": ["auto_renew", "lead_days", "cancel_path"]}},
        {"order_id": "FO-MANUAL", "auto_renew": False,
         "accepted_at": "2026-11-04T10:00:00+08:00"},
    ]


EXPECTED = {
    "FO-OK": OK,
    "FO-OK-SAME-MINUTE": OK,
    "FO-NO-EVIDENCE": NO_PROMPT_EVIDENCE,
    "FO-AFTER-ACCEPT": PROMPT_NOT_BEFORE,
    "FO-AT-ACCEPT": PROMPT_NOT_BEFORE,
    "FO-NOT-SIGNIFICANT": PROMPT_NOT_SIGNIFICANT,
    "FO-TERMS-PARTIAL": TERMS_INCOMPLETE,
    "FO-TERMS-ABSENT": TERMS_INCOMPLETE,
    "FO-NO-CHANNEL": CANNOT_JUDGE,
    "FO-BAD-TIME": CANNOT_JUDGE,
    "FO-MANUAL": NOT_AUTO_RENEW,
}


def run_drill(as_of_str, json_out, txt_out):
    as_of = parse_dt(as_of_str)
    if as_of is None:
        print("as-of must be YYYY-MM-DD")
        return 2

    results = [judge(order, as_of) for order in synthetic_orders()]
    mismatches = []
    for row in results:
        want = EXPECTED.get(row["order_id"])
        if want != row["verdict"]:
            mismatches.append({"order_id": row["order_id"],
                               "expected": want, "actual": row["verdict"]})

    summary = summarize(results)
    drill_verdict = ("FIRST-ORDER-CONSENT-DRILL-OK" if not mismatches
                     else "FIRST-ORDER-CONSENT-DRILL-DETECTOR-DRIFT")
    payload = {
        "as_of": as_of.strftime("%Y-%m-%d"),
        "anchor": "778号令第十条第二款 前半句（消费者接受服务前）",
        "required_terms": list(REQUIRED_TERMS),
        "drill_verdict": drill_verdict,
        "data_verdict": summary["verdict"],
        "summary": summary,
        "expectation_mismatches": mismatches,
        "results": results,
    }
    with open(json_out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

    lines = ["FIRST-ORDER-CONSENT-DRILL as_of=%s rows=%d %s data=%s" % (
        as_of.strftime("%Y-%m-%d"), summary["rows"], drill_verdict,
        summary["verdict"])]
    for row in results:
        extra = ""
        if row.get("lead_minutes") is not None:
            extra += " lead=%dmin" % row["lead_minutes"]
        if row.get("missing_terms"):
            extra += " missing=" + ",".join(row["missing_terms"])
        lines.append("  %-20s %-24s%s" % (row["order_id"], row["verdict"], extra))
    lines.append("counts: " + " ".join(
        "%s=%d" % (k, v) for k, v in sorted(summary["counts"].items())))
    lines.append("planted-expectation mismatches: %d" % len(mismatches))
    for m in mismatches:
        lines.append("  MISMATCH %s expected=%s actual=%s" % (
            m["order_id"], m["expected"], m["actual"]))

    with open(txt_out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(lines[0])
    print(lines[-1])
    return 0 if not mismatches else 3


def selftest():
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    as_of = datetime.date(2026, 11, 4)

    def order(**kw):
        base = {"order_id": "S", "auto_renew": True,
                "accepted_at": "2026-11-04T10:00:00+08:00",
                "accept_prompt_evidence": {
                    "prompted_at": "2026-11-04T09:58:00+08:00",
                    "significant": True, "channel": "in-app-checkout-banner",
                    "terms_shown": ["auto_renew", "lead_days", "cancel_path"]}}
        base.update(kw)
        return base

    def with_ev(**kw):
        ev = dict(order()["accept_prompt_evidence"])
        ev.update(kw)
        return order(accept_prompt_evidence=ev)

    # S1 happy path and lead minutes
    r = judge(order(), as_of)
    check("S1 prompt before acceptance -> OK", r["verdict"] == OK)
    check("S1 lead = 2 minutes", r.get("lead_minutes") == 2)

    # S2 acceptance ordering is strict: same instant is NOT before
    r = judge(with_ev(prompted_at="2026-11-04T10:00:00+08:00"), as_of)
    check("S2 prompt at acceptance instant -> PROMPT-NOT-BEFORE", r["verdict"] == PROMPT_NOT_BEFORE)
    r = judge(with_ev(prompted_at="2026-11-04T10:00:01+08:00"), as_of)
    check("S2 prompt after acceptance -> PROMPT-NOT-BEFORE (no PK grace)", r["verdict"] == PROMPT_NOT_BEFORE)

    # S3 no evidence / empty evidence
    check("S3 no evidence -> NO-PROMPT-EVIDENCE",
          judge({"order_id": "x", "auto_renew": True,
                 "accepted_at": "2026-11-04T10:00:00+08:00"}, as_of)["verdict"] == NO_PROMPT_EVIDENCE)
    check("S3 empty evidence dict -> NO-PROMPT-EVIDENCE",
          judge(order(accept_prompt_evidence={}), as_of)["verdict"] == NO_PROMPT_EVIDENCE)
    check("S3 evidence not a dict -> NO-PROMPT-EVIDENCE",
          judge(order(accept_prompt_evidence="shown"), as_of)["verdict"] == NO_PROMPT_EVIDENCE)

    # S4 prominence must be explicitly true, not "probably fine"
    for flag in (False, None, "true", 1):
        r = judge(with_ev(significant=flag), as_of)
        check("S4 significant=%r -> PROMPT-NOT-SIGNIFICANT" % (flag,),
              r["verdict"] == PROMPT_NOT_SIGNIFICANT)

    # S5 channel must be nameable, otherwise prominence is unprovable
    for ch in ("", "   ", None):
        r = judge(with_ev(channel=ch), as_of)
        check("S5 channel=%r -> CANNOT-JUDGE" % (ch,), r["verdict"] == CANNOT_JUDGE)

    # S6 required terms each individually matter
    for term in REQUIRED_TERMS:
        shown = [t for t in REQUIRED_TERMS if t != term]
        r = judge(with_ev(terms_shown=shown), as_of)
        check("S6 missing %s -> TERMS-INCOMPLETE" % term,
              r["verdict"] == TERMS_INCOMPLETE and r.get("missing_terms") == [term])
    check("S6 terms_shown absent -> TERMS-INCOMPLETE",
          judge(with_ev(terms_shown=None), as_of)["verdict"] == TERMS_INCOMPLETE)

    # S7 unparseable / missing timestamps refuse to judge
    for bad in (None, "", "11/04/2026 09:50", "2026-11-04"):
        r = judge(with_ev(prompted_at=bad), as_of)
        check("S7 prompted_at=%r -> CANNOT-JUDGE" % (bad,), r["verdict"] == CANNOT_JUDGE)
    r = judge(order(accepted_at=None), as_of)
    check("S7 accepted_at missing -> CANNOT-JUDGE", r["verdict"] == CANNOT_JUDGE)
    r = judge(order(accepted_at="2026-11-04T10:00:00"), as_of)
    check("S7 naive vs offset -> CANNOT-JUDGE", r["verdict"] == CANNOT_JUDGE)

    # S8 non-auto-renew carries no obligation (and is not green)
    r = judge(order(auto_renew=False), as_of)
    check("S8 manual renewal -> NOT-AUTO-RENEW", r["verdict"] == NOT_AUTO_RENEW)
    check("S8 auto_renew truthy string is not True",
          judge(order(auto_renew="yes"), as_of)["verdict"] == NOT_AUTO_RENEW)

    # S9 summarize keeps red and blue apart
    s = summarize([{"order_id": "a", "verdict": OK},
                   {"order_id": "b", "verdict": NO_PROMPT_EVIDENCE},
                   {"order_id": "c", "verdict": NOT_AUTO_RENEW}])
    check("S9 red names only the miss", s["verdict"] == "FIRST-ORDER-CONSENT-RED"
          and s["red"] == ["b"])
    s2 = summarize([{"order_id": "a", "verdict": OK},
                    {"order_id": "c", "verdict": NOT_AUTO_RENEW}])
    check("S9 blue-only -> GREEN", s2["verdict"] == "FIRST-ORDER-CONSENT-GREEN")

    # S10 determinism
    a = judge(order(), as_of)
    b = judge(order(), as_of)
    check("S10 deterministic", a == b)

    # S11 the planted set covers every state exactly as declared
    planted = {o["order_id"]: judge(o, as_of)["verdict"] for o in synthetic_orders()}
    check("S11 planted set matches EXPECTED", planted == EXPECTED)

    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print("%s %s" % ("PASS" if ok else "FAIL", name))
    print("selftest: %d/%d PASS" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="First-order pre-acceptance prompt drill")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_drill = sub.add_parser("drill", help="run the planted synthetic set")
    p_drill.add_argument("--as-of", default="2026-11-04")
    p_drill.add_argument("--json-out",
                         default=os.path.join(STATE, "first-order-consent-drill.json"))
    p_drill.add_argument("--txt-out",
                         default=os.path.join(STATE, "first-order-consent-drill.txt"))

    sub.add_parser("selftest", help="run fixtures")

    args = parser.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    return run_drill(args.as_of, args.json_out, args.txt_out)


if __name__ == "__main__":
    sys.exit(main())