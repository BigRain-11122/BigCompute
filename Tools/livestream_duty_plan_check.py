"""Duty-plan gate for the Bilibili attended city livestream (M146).

Why this file exists:
`docs/ops/livestream-duty-plan-v1.md` states five commitments that a human
reader can nod along to and a tired operator can quietly break: a guard roster,
a co-guest time boundary, an always-on AIGC badge, a >=7 day gray ramp, and a
stop-on-sight list for the unattended-broadcast profile the platform actually
enforces.  Prose cannot catch "the steady phase now starts at D8 but the roster
file still says D4", or "co-guest window 20:00-21:30 escaped outside the
19:00-23:30 broadcast window after an edit".

So the numeric half lives in `livestream-duty-roster-v1.json`, this module
recomputes every promise from that data (window coverage, shift caps, deputy
counts, gray-day continuity, co-guest bounds, badge cadence), and the markdown
is checked only for the sections that must exist.  `check` is the gate; it
writes a timestamped evidence file and exits non-zero on RED.

Honest boundary: this proves internal consistency and the presence of the
required sections. It cannot prove the roster has real humans behind it, that
the platform filing is done, or that a duty person is actually awake at 21:40.
Those remain physical/operational gates listed in section 6 of the plan.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN_MD = ROOT / "docs" / "ops" / "livestream-duty-plan-v1.md"
ROSTER = ROOT / "docs" / "ops" / "livestream-duty-roster-v1.json"
STATE_DIR = ROOT / "state"
TS_FMT = "%Y%m%d-%H%M%S"

REQUIRED_SECTIONS = (
    "值守班表形态",
    "连麦时段边界",
    "AIGC 角标位",
    "灰度节奏",
    "无人值守子形态排除判据",
)
REQUIRED_SUBFORMS = (
    "pure_unattended",
    "auto_loop_replay",
    "no_live_response",
    "no_filing",
    "unmanned_night",
)
MIN_GRAY_DAYS = 7
MAX_CO_GUEST_SESSION_MIN = 10


def hm_to_min(value):
    """'HH:MM' -> minutes past midnight. Raises ValueError on malformed input."""
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError("bad time %r" % (value,))
    hh, mm = value.split(":")
    hh, mm = int(hh), int(mm)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("bad time %r" % (value,))
    return hh * 60 + mm


def _require(obj, key, where, findings):
    if not isinstance(obj, dict) or key not in obj:
        findings.append(("MISSING-FIELD", "%s.%s" % (where, key)))
        return None
    return obj[key]


def validate(roster, md_text):
    """Return (findings, n_checks). findings == [] means GREEN."""
    findings = []
    n = 0

    def chk(cond, code, where):
        nonlocal n
        n += 1
        if not cond:
            findings.append((code, where))

    chk(isinstance(roster, dict), "ROSTER-NOT-OBJECT", "roster")
    if not isinstance(roster, dict):
        return findings, n

    chk(roster.get("schema") == "livestream-duty-roster/v1",
        "SCHEMA-MISMATCH", "roster.schema")
    chk(roster.get("platform") == "bilibili", "PLATFORM-NOT-BILIBILI", "roster.platform")
    chk(roster.get("mode") == "attended_only", "MODE-NOT-ATTENDED", "roster.mode")

    cons = roster.get("constraints") or {}
    max_shift = _require(cons, "max_shift_min", "constraints", findings)
    min_primary = _require(cons, "min_primary_per_shift", "constraints", findings)
    min_deputy = _require(cons, "min_deputy_per_shift", "constraints", findings)
    buffer_min = _require(cons, "handover_buffer_min", "constraints", findings)
    chk(isinstance(max_shift, int) and 0 < max_shift <= 240,
        "SHIFT-CAP-ABOVE-240", "constraints.max_shift_min")
    chk(isinstance(min_primary, int) and min_primary >= 1,
        "PRIMARY-FLOOR-ZERO", "constraints.min_primary_per_shift")
    chk(isinstance(min_deputy, int) and min_deputy >= 1,
        "DEPUTY-FLOOR-ZERO", "constraints.min_deputy_per_shift")
    chk(isinstance(buffer_min, int) and buffer_min >= 15,
        "HANDOVER-BUFFER-BELOW-15", "constraints.handover_buffer_min")

    # --- AIGC badge: always on, self-proof at least hourly -------------------
    badge = cons.get("aigc_badge") or {}
    chk(badge.get("always_on") is True, "BADGE-NOT-ALWAYS-ON", "constraints.aigc_badge")
    interval = badge.get("self_proof_interval_min")
    chk(isinstance(interval, int) and 0 < interval <= 60,
        "BADGE-PROOF-SPARSE", "constraints.aigc_badge.self_proof_interval_min")

    # --- Unattended subform exclusions --------------------------------------
    subs = cons.get("excluded_subforms") or []
    chk(isinstance(subs, list) and len(subs) >= 5, "EXCLUSIONS-UNDER-5", "constraints.excluded_subforms")
    missing_sub = [s for s in REQUIRED_SUBFORMS if s not in subs]
    chk(not missing_sub, "EXCLUSIONS-INCOMPLETE", "constraints.excluded_subforms")

    # --- Phases: windows, coverage, shift caps, staffing --------------------
    phases = roster.get("phases") or []
    chk(isinstance(phases, list) and len(phases) >= 2,
        "PHASES-UNDER-2", "roster.phases")

    all_days = []
    first_span = None
    last_span = None
    for idx, ph in enumerate(phases):
        where = "phases[%d]" % idx
        days = ph.get("days") or []
        chk(bool(days), "PHASE-NO-DAYS", where + ".days")
        all_days.extend(days)

        bc = ph.get("broadcast") or []
        try:
            bc_start, bc_end = hm_to_min(bc[0]), hm_to_min(bc[1])
            ok_win = bc_start < bc_end
        except (ValueError, IndexError):
            bc_start = bc_end = None
            ok_win = False
        chk(ok_win, "BROADCAST-WINDOW-BAD", where + ".broadcast")
        if ok_win:
            span = bc_end - bc_start
            if first_span is None:
                first_span = span
            last_span = span

        shifts = ph.get("shifts") or []
        chk(bool(shifts), "PHASE-NO-SHIFTS", where + ".shifts")
        parsed = []
        for s_idx, s in enumerate(shifts):
            sw = "%s.shifts[%d]" % (where, s_idx)
            try:
                st, en = hm_to_min(s.get("start")), hm_to_min(s.get("end"))
            except (ValueError, AttributeError):
                findings.append(("SHIFT-TIME-BAD", sw))
                n += 1
                continue
            chk(st < en, "SHIFT-END-BEFORE-START", sw)
            chk(en - st <= (max_shift if isinstance(max_shift, int) else 240),
                "SHIFT-OVER-CAP", sw)
            chk((s.get("primary") or 0) >= (min_primary if isinstance(min_primary, int) else 1),
                "SHIFT-PRIMARY-SHORT", sw)
            chk((s.get("deputy") or 0) >= (min_deputy if isinstance(min_deputy, int) else 1),
                "SHIFT-DEPUTY-SHORT", sw)
            parsed.append((st, en))

        if ok_win and parsed and isinstance(buffer_min, int):
            parsed.sort()
            chk(parsed[0][0] <= bc_start - buffer_min,
                "SHIFT-START-LATE", where + ".shifts")
            chk(parsed[-1][1] >= bc_end + buffer_min,
                "SHIFT-END-EARLY", where + ".shifts")
            gaps = [b[0] - a[1] for a, b in zip(parsed, parsed[1:])]
            chk(all(g == 0 for g in gaps), "SHIFT-GAPS", where + ".shifts")

    # --- Gray ramp: days consecutive from 1, >=7 distinct, widening window ---
    chk(bool(all_days), "NO-GRAY-DAYS", "roster.phases")
    if all_days:
        ordered = sorted(set(all_days))
        chk(ordered == list(range(1, len(ordered) + 1)),
            "GRAY-DAYS-NOT-CONSECUTIVE", "roster.phases[].days")
        chk(len(ordered) >= MIN_GRAY_DAYS,
            "GRAY-DAYS-UNDER-7", "roster.phases[].days")
    if first_span is not None and last_span is not None:
        chk(last_span > first_span, "GRAY-NOT-WIDENING", "roster.phases[].broadcast")

    # --- Co-guest boundary: after 7 gray days, inside steady window, capped --
    cg = cons.get("co_guest") or {}
    after = cg.get("enabled_after_gray_days")
    chk(isinstance(after, int) and after >= MIN_GRAY_DAYS,
        "CO-GUEST-OPENS-EARLY", "constraints.co_guest.enabled_after_gray_days")
    cg_win = cg.get("window") or []
    try:
        cg_start, cg_end = hm_to_min(cg_win[0]), hm_to_min(cg_win[1])
        cg_ok = cg_start < cg_end
    except (ValueError, IndexError):
        cg_start = cg_end = None
        cg_ok = False
    chk(cg_ok, "CO-GUEST-WINDOW-BAD", "constraints.co_guest.window")
    daily_max = cg.get("daily_max_min")
    if cg_ok and isinstance(daily_max, int):
        chk(cg_end - cg_start <= daily_max,
            "CO-GUEST-WINDOW-OVER-DAILY", "constraints.co_guest")
    session_cap = cg.get("max_session_min")
    chk(isinstance(session_cap, int) and session_cap <= MAX_CO_GUEST_SESSION_MIN,
        "CO-GUEST-SESSION-OVER-10", "constraints.co_guest.max_session_min")
    if cg_ok and phases:
        try:
            steady_bc = phases[-1].get("broadcast") or []
            sb_start, sb_end = hm_to_min(steady_bc[0]), hm_to_min(steady_bc[1])
            chk(sb_start <= cg_start and cg_end <= sb_end,
                "CO-GUEST-OUTSIDE-BROADCAST", "constraints.co_guest.window")
        except (ValueError, IndexError):
            findings.append(("CO-GUEST-BROADCAST-UNPARSED", "phases[-1].broadcast"))
            n += 1

    # --- Markdown: required sections + honest plan-only framing -------------
    chk(isinstance(md_text, str) and bool(md_text.strip()), "PLAN-MD-EMPTY", "plan.md")
    for sec in REQUIRED_SECTIONS:
        chk(sec in (md_text or ""), "PLAN-SECTION-MISSING", sec)
    chk("非开播" in (md_text or ""), "PLAN-NOT-MARKED-PLAN-ONLY", "plan.md")
    chk("排除" in (md_text or "") and "停播" in (md_text or ""),
        "PLAN-MISSING-STOP-ACTION", "plan.md")

    return findings, n


def _load():
    roster = json.loads(ROSTER.read_text(encoding="utf-8"))
    md_text = PLAN_MD.read_text(encoding="utf-8")
    return roster, md_text


def cmd_check():
    roster, md_text = _load()
    findings, n = validate(roster, md_text)
    verdict = "DUTY-PLAN-GREEN" if not findings else "DUTY-PLAN-RED"
    lines = ["%s  checks=%d  findings=%d" % (verdict, n, len(findings))]
    for code, where in findings:
        lines.append("  %-32s %s" % (code, where))
    body = "\n".join(lines)
    print(body)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = STATE_DIR / ("livestream-duty-plan-check-%s.txt" % datetime.now().strftime(TS_FMT))
    out.write_text(body + "\n", encoding="utf-8")
    print("evidence: %s" % out.relative_to(ROOT))
    return 0 if not findings else 1


# --- selftest ---------------------------------------------------------------

def _base_roster():
    return {
        "schema": "livestream-duty-roster/v1",
        "plan_id": "selftest",
        "platform": "bilibili",
        "mode": "attended_only",
        "constraints": {
            "max_shift_min": 240,
            "min_primary_per_shift": 1,
            "min_deputy_per_shift": 1,
            "handover_buffer_min": 15,
            "aigc_badge": {"always_on": True, "self_proof_interval_min": 60},
            "co_guest": {
                "enabled_after_gray_days": 7,
                "window": ["20:00", "21:30"],
                "max_session_min": 10,
                "daily_max_min": 120,
            },
            "excluded_subforms": list(REQUIRED_SUBFORMS),
        },
        "phases": [
            {"phase": "g1", "days": [1, 2, 3], "broadcast": ["20:00", "22:00"],
             "shifts": [{"start": "19:45", "end": "21:00", "primary": 1, "deputy": 1},
                        {"start": "21:00", "end": "22:15", "primary": 1, "deputy": 1}]},
            {"phase": "g2", "days": [4, 5, 6, 7], "broadcast": ["19:00", "23:00"],
             "shifts": [{"start": "18:45", "end": "21:00", "primary": 1, "deputy": 1},
                        {"start": "21:00", "end": "23:15", "primary": 1, "deputy": 1}]},
            {"phase": "s8", "days": [8], "broadcast": ["19:00", "23:30"],
             "shifts": [{"start": "18:45", "end": "21:15", "primary": 1, "deputy": 1},
                        {"start": "21:15", "end": "23:45", "primary": 1, "deputy": 1}]},
        ],
    }


def _base_md():
    return "# plan\n" + "\n".join(REQUIRED_SECTIONS) + "\n非开播 排除 停播\n"


def selftest():
    checks = []

    def chk(name, cond, detail=None):
        checks.append((name, bool(cond), detail))

    r, m = _base_roster(), _base_md()
    f, n = validate(r, m)
    chk("S1 baseline roster passes", f == [] and n > 20, (f, n))

    def mutate(fn):
        import copy
        rr = copy.deepcopy(_base_roster())
        mm = _base_md()
        fn(rr, mm)
        return validate(rr, mm)[0]

    def codes(fs):
        return {c for c, _w in fs}

    chk("S2 badge not always on is caught",
        "BADGE-NOT-ALWAYS-ON" in codes(mutate(
            lambda rr, mm: rr["constraints"]["aigc_badge"].update({"always_on": False}))))
    chk("S3 sparse proof interval is caught",
        "BADGE-PROOF-SPARSE" in codes(mutate(
            lambda rr, mm: rr["constraints"]["aigc_badge"].update({"self_proof_interval_min": 120}))))
    def _short_ramp(rr, _mm):
        rr["phases"] = rr["phases"][:2]
        rr["phases"][0]["days"] = [1, 2, 3]
        rr["phases"][1]["days"] = [4, 5, 6]

    chk("S4 gray ramp under 7 days is caught",
        "GRAY-DAYS-UNDER-7" in codes(mutate(_short_ramp)))
    chk("S5 non-consecutive gray days are caught",
        "GRAY-DAYS-NOT-CONSECUTIVE" in codes(mutate(
            lambda rr, mm: rr["phases"][1].update({"days": [5, 6, 7]}))))
    chk("S6 shift over the 240 cap is caught",
        "SHIFT-OVER-CAP" in codes(mutate(
            lambda rr, mm: rr["phases"][2]["shifts"].__setitem__(
                1, {"start": "21:15", "end": "23:45", "primary": 1, "deputy": 1})
            or rr["constraints"].update({"max_shift_min": 120}))))
    chk("S7 missing deputy is caught",
        "SHIFT-DEPUTY-SHORT" in codes(mutate(
            lambda rr, mm: rr["phases"][0]["shifts"][0].update({"deputy": 0}))))
    chk("S8 roster gap inside the window is caught",
        "SHIFT-GAPS" in codes(mutate(
            lambda rr, mm: rr["phases"][1]["shifts"][0].update({"end": "20:30"}))))
    chk("S9 roster not covering window head is caught",
        "SHIFT-START-LATE" in codes(mutate(
            lambda rr, mm: rr["constraints"].update({"handover_buffer_min": 30}))))
    chk("S10 co-guest opening before 7 gray days is caught",
        "CO-GUEST-OPENS-EARLY" in codes(mutate(
            lambda rr, mm: rr["constraints"]["co_guest"].update({"enabled_after_gray_days": 3}))))
    def _cg_outside(rr, _mm):
        rr["constraints"]["co_guest"]["window"] = ["23:30", "23:50"]
        rr["constraints"]["co_guest"]["daily_max_min"] = 180

    chk("S11 co-guest window outside broadcast is caught",
        "CO-GUEST-OUTSIDE-BROADCAST" in codes(mutate(_cg_outside)))
    chk("S12 co-guest session over 10 min is caught",
        "CO-GUEST-SESSION-OVER-10" in codes(mutate(
            lambda rr, mm: rr["constraints"]["co_guest"].update({"max_session_min": 15}))))
    chk("S13 excluded subform list under 5 is caught",
        "EXCLUSIONS-UNDER-5" in codes(mutate(
            lambda rr, mm: rr["constraints"].update({"excluded_subforms": ["pure_unattended"]}))))
    chk("S14 missing required section is caught",
        "PLAN-SECTION-MISSING" in codes(
            validate(_base_roster(), "# plan\n非开播 排除 停播\n")[0]),
        "section-drop")
    chk("S15 plan not marked plan-only is caught",
        "PLAN-NOT-MARKED-PLAN-ONLY" in codes(
            validate(_base_roster(), "\n".join(REQUIRED_SECTIONS) + "\n排除 停播\n")[0]))
    chk("S16 widening requirement catches a narrowing ramp",
        "GRAY-NOT-WIDENING" in codes(mutate(
            lambda rr, mm: rr["phases"][0].update({"broadcast": ["19:00", "23:30"]}))))
    chk("S17 malformed time is reported, not raised",
        "SHIFT-TIME-BAD" in codes(mutate(
            lambda rr, mm: rr["phases"][0]["shifts"][0].update({"start": "9:45"}))))
    chk("S18 real repo artefacts pass the gate",
        True if not (PLAN_MD.exists() and ROSTER.exists())
        else validate(*_load())[0] == [],
        "repo")

    ok = sum(1 for _n, c, _d in checks if c)
    for name, cond, detail in checks:
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  <%r>" % (detail,)))
    print("SELFTEST %d/%d PASS" % (ok, len(checks)))
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "selftest":
        sys.exit(selftest())
    if cmd == "check":
        sys.exit(cmd_check())
    print("usage: livestream_duty_plan_check.py [check|selftest]")
    sys.exit(2)
