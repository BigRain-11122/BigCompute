"""Renewal reminder trigger drill (queue M102, drill only).

service-continuity-terms anchor 1 (778 号令 第十条第二款, ✅原文级 2026-10-02)
says an auto-renewing service must be flagged prominently *before the consumer
accepts* and *before the renewal date*. The document carries a reminder ledger
requirement too (§五: 续费提醒触发记录入流水轨, 提醒时点=可审计). Until now none
of that had a machine judgement — the reminders were "supposed to happen".

This drill makes "did the reminder actually fire in the window" a verdict. The
thing worth modelling carefully is that not every subscription owes a reminder
right now, and mixing those up produces the two usual lies:

  * counting "not due yet" as a miss (crying wolf every day), and
  * counting "manual renewal / cancelled" as ok (pretending an obligation was
    met when there was none).

So the states are separated: OK / NOT-YET-DUE / NOT-AUTO-RENEW / SKIPPED are
not obligations; MISSING / LATE / NO-CONSENT / CANCEL-PATH-TOO-DEEP are red;
and an unset lead window is CANNOT-JUDGE rather than a silent pass, because
{N} 日 is still a design value waiting on the pricing confirmation.

Dates are plain YYYY-MM-DD strings and the window is half-open
[renew_on - lead, renew_on): a reminder dated exactly on the renewal date is
late, not on time, because the law says 续费日期前.
"""

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "state")

# §三.2 / §四 提醒时点 is a design value ({N} 日) still pending the pricing
# confirmation, so every row says where its value came from.
DESIGN_LEAD_DAYS = 3
DESIGN_LEAD_SOURCE = "drill-design-value"
MAX_CANCEL_STEPS = 2          # §三.2: 取消路径 ≤2 步
LATE_GRACE_DAYS = 7           # a reminder this soon after the date is still "late", just useful to see

OK = "OK"
NOT_YET_DUE = "NOT-YET-DUE"
NOT_AUTO_RENEW = "NOT-AUTO-RENEW"
SKIPPED = "SKIPPED"
MISSING = "MISSING-REMINDER"
LATE = "LATE-REMINDER"
NO_CONSENT = "NO-CONSENT"
CANCEL_DEEP = "CANCEL-PATH-TOO-DEEP"
CANNOT_JUDGE = "CANNOT-JUDGE"

RED = (MISSING, LATE, NO_CONSENT, CANCEL_DEEP, CANNOT_JUDGE)
BLUE = (OK, NOT_YET_DUE, NOT_AUTO_RENEW, SKIPPED)


def parse_date(value):
    """YYYY-MM-DD -> date, or None. Deliberately strict."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def window_for(renew_on, lead_days):
    """Half-open reminder window [renew_on - lead, renew_on). None if unusable."""
    start = renew_on - datetime.timedelta(days=lead_days)
    return start, renew_on


def judge(sub, as_of, lead_days=None):
    """Verdict for one subscription. Pure function."""
    sub_id = sub.get("sub_id")
    lead = sub.get("reminder_lead_days", lead_days)

    if sub.get("status") in ("cancelled", "expired"):
        return {"sub_id": sub_id, "verdict": SKIPPED,
                "reason": "subscription not active; no reminder obligation"}

    if not sub.get("auto_renew"):
        return {"sub_id": sub_id, "verdict": NOT_AUTO_RENEW,
                "reason": "manual renewal; the auto-renew reminder duty does not apply"}

    if sub.get("consent_opt_in") is not True:
        return {"sub_id": sub_id, "verdict": NO_CONSENT,
                "reason": "auto-renew not actively opted in (§三.2 default-check ban, "
                          "our own design rule stricter than the statute)"}

    steps = sub.get("cancel_path_steps")
    if isinstance(steps, int) and steps > MAX_CANCEL_STEPS:
        return {"sub_id": sub_id, "verdict": CANCEL_DEEP,
                "reason": "cancel path takes %d steps; §三.2 requires <= %d"
                          % (steps, MAX_CANCEL_STEPS)}

    renew_on = parse_date(sub.get("renew_on"))
    if renew_on is None:
        return {"sub_id": sub_id, "verdict": CANNOT_JUDGE,
                "reason": "renew_on missing or not YYYY-MM-DD"}

    if not isinstance(lead, int) or lead <= 0:
        return {"sub_id": sub_id, "verdict": CANNOT_JUDGE,
                "reason": "reminder lead not set; §四 {N} 日 pending the pricing "
                          "confirmation, refusing to assume a default"}

    start, end = window_for(renew_on, lead)

    if as_of < start:
        return {"sub_id": sub_id, "verdict": NOT_YET_DUE,
                "window": [start.isoformat(), end.isoformat()],
                "reason": "reminder window has not opened yet"}

    fired = []
    for value in sub.get("reminders") or []:
        when = parse_date(value)
        if when is None:
            continue
        fired.append(when)

    on_time = [d for d in fired if start <= d < end]
    if on_time:
        first = min(on_time)
        return {"sub_id": sub_id, "verdict": OK,
                "window": [start.isoformat(), end.isoformat()],
                "reminded_on": first.isoformat(),
                "days_before_renewal": (end - first).days,
                "reason": "significant reminder delivered inside the window"}

    late = sorted(d for d in fired
                  if end <= d <= end + datetime.timedelta(days=LATE_GRACE_DAYS))
    if late:
        return {"sub_id": sub_id, "verdict": LATE,
                "window": [start.isoformat(), end.isoformat()],
                "reminded_on": late[0].isoformat(),
                "reason": "reminder arrived on/after the renewal date, not 续费日期前"}

    return {"sub_id": sub_id, "verdict": MISSING,
            "window": [start.isoformat(), end.isoformat()],
            "reason": "renewal window opened with no reminder on record"}


def summarize(results):
    counts = {}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    red = [r["sub_id"] for r in results if r["verdict"] in RED]
    return {
        "rows": len(results),
        "counts": counts,
        "red": red,
        "verdict": "RENEWAL-REMINDER-RED" if red else "RENEWAL-REMINDER-GREEN",
    }


def synthetic_subs():
    """Planted set: every verdict appears, so a missed obligation is visible.

    Judged as of 2026-11-04, so windows are already open: a row whose renew_on
    is 2026-11-06 with a 3-day lead opens on 11-03, which is why its reminder
    can legitimately sit at 11-03 and still be on time.
    """
    return [
        {"sub_id": "R-OK", "user": "anon-a", "auto_renew": True,
         "consent_opt_in": True, "cancel_path_steps": 2,
         "renew_on": "2026-11-06", "reminder_lead_days": DESIGN_LEAD_DAYS,
         "reminders": ["2026-11-03"]},
        {"sub_id": "R-OK-WINDOWSTART", "user": "anon-a", "auto_renew": True,
         "consent_opt_in": True, "cancel_path_steps": 1,
         "renew_on": "2026-11-07", "reminder_lead_days": DESIGN_LEAD_DAYS,
         "reminders": ["2026-11-04", "2026-11-05", "2026-11-06"]},
        {"sub_id": "R-MISS", "user": "anon-b", "auto_renew": True,
         "consent_opt_in": True, "cancel_path_steps": 2,
         "renew_on": "2026-11-05", "reminder_lead_days": DESIGN_LEAD_DAYS,
         "reminders": []},
        {"sub_id": "R-LATE", "user": "anon-b", "auto_renew": True,
         "consent_opt_in": True, "cancel_path_steps": 2,
         "renew_on": "2026-11-05", "reminder_lead_days": DESIGN_LEAD_DAYS,
         "reminders": ["2026-11-05"]},
        {"sub_id": "R-NOCONSENT", "user": "anon-c", "auto_renew": True,
         "consent_opt_in": False, "cancel_path_steps": 2,
         "renew_on": "2026-11-06", "reminder_lead_days": DESIGN_LEAD_DAYS,
         "reminders": ["2026-11-04"]},
        {"sub_id": "R-CANCEL3", "user": "anon-c", "auto_renew": True,
         "consent_opt_in": True, "cancel_path_steps": 3,
         "renew_on": "2026-11-06", "reminder_lead_days": DESIGN_LEAD_DAYS,
         "reminders": ["2026-11-04"]},
        {"sub_id": "R-MANUAL", "user": "anon-d", "auto_renew": False,
         "consent_opt_in": True, "cancel_path_steps": 2,
         "renew_on": "2026-11-06", "reminders": []},
        {"sub_id": "R-CANCELLED", "user": "anon-d", "auto_renew": True,
         "consent_opt_in": True, "cancel_path_steps": 2, "status": "cancelled",
         "renew_on": "2026-11-06", "reminder_lead_days": DESIGN_LEAD_DAYS,
         "reminders": []},
        {"sub_id": "R-UNSET-LEAD", "user": "anon-e", "auto_renew": True,
         "consent_opt_in": True, "cancel_path_steps": 2,
         "renew_on": "2026-11-06", "reminder_lead_days": None,
         "reminders": ["2026-11-04"]},
        {"sub_id": "R-FUTURE", "user": "anon-e", "auto_renew": True,
         "consent_opt_in": True, "cancel_path_steps": 2,
         "renew_on": "2026-12-10", "reminder_lead_days": DESIGN_LEAD_DAYS,
         "reminders": []},
    ]


EXPECTED = {
    "R-OK": OK,
    "R-OK-WINDOWSTART": OK,
    "R-MISS": MISSING,
    "R-LATE": LATE,
    "R-NOCONSENT": NO_CONSENT,
    "R-CANCEL3": CANCEL_DEEP,
    "R-MANUAL": NOT_AUTO_RENEW,
    "R-CANCELLED": SKIPPED,
    "R-UNSET-LEAD": CANNOT_JUDGE,
    "R-FUTURE": NOT_YET_DUE,
}


def run_drill(as_of_str, json_out, txt_out):
    as_of = parse_date(as_of_str)
    if as_of is None:
        print("as_of must be YYYY-MM-DD")
        return 2

    subs = synthetic_subs()
    results = [judge(sub, as_of) for sub in subs]

    mismatches = []
    for row in results:
        want = EXPECTED.get(row["sub_id"])
        if want != row["verdict"]:
            mismatches.append({"sub_id": row["sub_id"], "expected": want,
                               "actual": row["verdict"]})

    summary = summarize(results)
    # The planted set is *designed* to contain red rows, so a drill only passes
    # when the detector names exactly what was planted. Saying "RED" about the
    # fixture set would be the same kind of lie as saying "GREEN" about it.
    drill_verdict = ("RENEWAL-REMINDER-DRILL-OK" if not mismatches
                     else "RENEWAL-REMINDER-DRILL-DETECTOR-DRIFT")
    payload = {
        "as_of": as_of.isoformat(),
        "lead_days": DESIGN_LEAD_DAYS,
        "lead_source": DESIGN_LEAD_SOURCE,
        "max_cancel_steps": MAX_CANCEL_STEPS,
        "drill_verdict": drill_verdict,
        "data_verdict": summary["verdict"],
        "summary": summary,
        "expectation_mismatches": mismatches,
        "results": results,
    }
    with open(json_out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

    lines = ["RENEWAL-REMINDER-DRILL as_of=%s lead=%d(%s) rows=%d %s data=%s" % (
        as_of.isoformat(), DESIGN_LEAD_DAYS, DESIGN_LEAD_SOURCE,
        summary["rows"], drill_verdict, summary["verdict"])]
    for row in results:
        extra = ""
        if row.get("window"):
            extra += " window=%s..%s" % tuple(row["window"])
        if row.get("reminded_on"):
            extra += " reminded=%s(d-%d)" % (row["reminded_on"],
                                             row.get("days_before_renewal", 0))
        lines.append("  %-15s %-20s%s" % (row["sub_id"], row["verdict"], extra))
    lines.append("counts: " + " ".join(
        "%s=%d" % (k, v) for k, v in sorted(summary["counts"].items())))
    lines.append("planted-expectation mismatches: %d" % len(mismatches))
    for m in mismatches:
        lines.append("  MISMATCH %s expected=%s actual=%s" % (
            m["sub_id"], m["expected"], m["actual"]))

    with open(txt_out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(lines[0])
    print(lines[-1] if mismatches else lines[-1])
    return 0 if not mismatches else 3


def selftest():
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    as_of = datetime.date(2026, 11, 4)

    def sub(**kw):
        base = {"sub_id": "S", "auto_renew": True, "consent_opt_in": True,
                "cancel_path_steps": 2, "renew_on": "2026-11-06",
                "reminder_lead_days": 3, "reminders": []}
        base.update(kw)
        return base

    # S1 in-window reminder ok, exact first day counts (window 11-03..11-06)
    r = judge(sub(reminders=["2026-11-03"]), as_of)
    check("S1 reminder on window's first day -> OK", r["verdict"] == OK)
    check("S1 days_before_renewal = 3", r.get("days_before_renewal") == 3)

    # S2 window is half-open: renew date itself is late
    r = judge(sub(**{"renew_on": "2026-11-05"}, reminders=["2026-11-05"]), as_of)
    check("S2 reminder on renewal date -> LATE", r["verdict"] == LATE)

    # S3 the day before the renew date is still in window
    r = judge(sub(reminders=["2026-11-05"]), as_of)
    check("S3 day before renewal -> OK (d-1)", r["verdict"] == OK
          and r.get("days_before_renewal") == 1)

    # S4 a reminder one day before the window is not accepted
    r = judge(sub(reminders=["2026-11-02"]), as_of)
    check("S4 reminder before window -> MISSING", r["verdict"] == MISSING)

    # S5 window not open yet is not a miss
    r = judge(sub(**{"renew_on": "2026-11-20"}), as_of)
    check("S5 window closed -> NOT-YET-DUE", r["verdict"] == NOT_YET_DUE)

    # S6 unset lead refuses to judge (fail-closed), also for 0/negative
    for lead in (None, 0, -1, "3"):
        r = judge(sub(reminder_lead_days=lead), as_of)
        check("S6 lead=%r -> CANNOT-JUDGE" % (lead,), r["verdict"] == CANNOT_JUDGE)

    # S7 no consent / deep cancel path are red independently of reminders
    check("S7 consent false -> NO-CONSENT",
          judge(sub(consent_opt_in=False, reminders=["2026-11-04"]), as_of)["verdict"] == NO_CONSENT)
    check("S7 cancel 3 steps -> CANCEL-PATH-TOO-DEEP",
          judge(sub(cancel_path_steps=3, reminders=["2026-11-04"]), as_of)["verdict"] == CANCEL_DEEP)
    check("S7 cancel exactly 2 steps is fine",
          judge(sub(cancel_path_steps=2, reminders=["2026-11-04"]), as_of)["verdict"] == OK)

    # S8 obligations absent -> not ok, not red
    check("S8 manual renewal -> NOT-AUTO-RENEW",
          judge(sub(auto_renew=False), as_of)["verdict"] == NOT_AUTO_RENEW)
    check("S8 cancelled -> SKIPPED",
          judge(sub(status="cancelled"), as_of)["verdict"] == SKIPPED)

    # S9 garbage renew date cannot silently pass
    for bad in (None, "2026/11/10", "11-10-2026"):
        r = judge(sub(**{"renew_on": bad}, reminders=["2026-11-04"]), as_of)
        check("S9 renew_on=%r -> CANNOT-JUDGE" % (bad,), r["verdict"] == CANNOT_JUDGE)

    # S10 one on-time and one late -> still OK (obligation met once)
    r = judge(sub(reminders=["2026-11-05", "2026-11-06"]), as_of)
    check("S10 on-time + late -> OK", r["verdict"] == OK)

    # S11 summarize separates red from blue
    results = [{"sub_id": "a", "verdict": OK}, {"sub_id": "b", "verdict": MISSING},
               {"sub_id": "c", "verdict": NOT_YET_DUE}]
    s = summarize(results)
    check("S11 summarize red only names the miss",
          s["verdict"] == "RENEWAL-REMINDER-RED" and s["red"] == ["b"])
    s2 = summarize([{"sub_id": "a", "verdict": OK},
                    {"sub_id": "c", "verdict": NOT_YET_DUE}])
    check("S11 blue-only -> GREEN", s2["verdict"] == "RENEWAL-REMINDER-GREEN")

    # S12 determinism
    a = judge(sub(reminders=["2026-11-04"]), as_of)
    b = judge(sub(reminders=["2026-11-04"]), as_of)
    check("S12 deterministic", a == b)

    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print("%s %s" % ("PASS" if ok else "FAIL", name))
    print("selftest: %d/%d PASS" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Renewal reminder trigger drill")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_drill = sub.add_parser("drill", help="run the planted synthetic set")
    p_drill.add_argument("--as-of", default="2026-11-04")
    p_drill.add_argument("--json-out",
                         default=os.path.join(STATE, "renewal-reminder-drill.json"))
    p_drill.add_argument("--txt-out",
                         default=os.path.join(STATE, "renewal-reminder-drill.txt"))

    sub.add_parser("selftest", help="run fixtures")

    args = parser.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    return run_drill(args.as_of, args.json_out, args.txt_out)


if __name__ == "__main__":
    sys.exit(main())