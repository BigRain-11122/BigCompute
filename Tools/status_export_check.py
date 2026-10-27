#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""status_export_check.py -- CEO-facing status export mechanical gate (tech T143).

Mandate 产品优先律 clause 5 requires docs/status-export.json to carry three
lines (current activity / latest artifact / next milestone) and to be
refreshed whenever reality moves. Nothing measured that file: a stale or
half-written export still *renders* as a live face while the company is
actually idle, and a GBK-mangled write (the rounds.log T24 precedent) would
ship to the CEO as unreadable text with the JSON still parsing fine.

This gate answers four questions mechanically, read-only, no network:
  1. bytes decode as strict UTF-8 and parse as JSON (no mojibake/truncation)
  2. the three required keys exist, are non-empty strings, and carry no
     U+FFFD replacement characters
  3. updated_at parses and is within MAX_AGE_H hours of now
  4. the export is not behind the ledger: when rounds.log holds a round
     newer than updated_at by more than LAG_H hours, the face was not
     refreshed while the company did work -> STALE. Same MISSED shape that
     T129 found for HQ-FEEDBACK.md, applied to the CEO-facing export.

Verdicts: EXPORT-GREEN / EXPORT-STALE / EXPORT-MISSING / EXPORT-BAD.

Commands:
    python Tools/status_export_check.py check [--now "YYYY-MM-DD HH:MM"]
    python Tools/status_export_check.py selftest
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_PATH = os.path.join("docs", "status-export.json")
ROUNDS_PATH = os.path.join("state", "rounds.log")

REQUIRED_KEYS = ("current_activity", "latest_artifact", "next_milestone")
MAX_AGE_H = 48.0
LAG_H = 2.0

TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2})(?::(\d{2}))?")
PLACEHOLDER_TOKENS = ("tbd", "todo", "n/a", "...", "-")


def parse_ts(text):
    """Parse 'YYYY-MM-DD HH:MM' and the rounds.log 'HHx' hour mask.

    rounds.log writes hours like '06x' (the round log uses an hour mask
    rather than minutes). The mask is read as the *earliest* time the
    round could have started, so a masked hour never makes the export
    look fresher than it is.
    """
    if not isinstance(text, str):
        return None
    m = TS_RE.match(text.strip())
    if not m:
        return None
    y, mo, d, h = (int(m.group(i)) for i in range(1, 5))
    mi = int(m.group(5)) if m.group(5) else 0
    try:
        return datetime.datetime(y, mo, d, h, mi)
    except ValueError:
        return None


def latest_round_ts(text):
    """Newest timestamp in state/rounds.log (None when the log is empty)."""
    newest = None
    for line in (text or "").splitlines():
        ts = parse_ts(line)
        if ts and (newest is None or ts > newest):
            newest = ts
    return newest


def _bad_value(key, value):
    if not isinstance(value, str) or not value.strip():
        return "%s empty/non-string" % key
    if "\ufffd" in value:
        return "%s contains U+FFFD (mojibake)" % key
    if value.strip().lower() in PLACEHOLDER_TOKENS:
        return "%s is a placeholder (%r)" % (key, value.strip())
    return None


def export_verdict(raw_bytes, now=None, rounds_text="", exists=True):
    """Pure: verdict + detail for the export face.

    raw_bytes is None only when exists is False. Every failure mode gets
    its own verdict so a reader can tell "nobody refreshed it" from
    "somebody wrote garbage".
    """
    now = now or datetime.datetime.now()
    if not exists:
        return "EXPORT-MISSING", "%s does not exist" % EXPORT_PATH
    try:
        text = raw_bytes.decode("utf-8")
    except (UnicodeDecodeError, AttributeError) as e:
        return "EXPORT-BAD", "not strict UTF-8: %r" % e
    try:
        payload = json.loads(text)
    except ValueError as e:
        return "EXPORT-BAD", "JSON parse failed: %s" % e
    if not isinstance(payload, dict):
        return "EXPORT-BAD", "top level is %s, expected object" % type(payload).__name__
    problems = []
    for key in REQUIRED_KEYS:
        if key not in payload:
            problems.append("%s missing" % key)
            continue
        bad = _bad_value(key, payload.get(key))
        if bad:
            problems.append(bad)
    if problems:
        return "EXPORT-BAD", "; ".join(problems)
    updated = parse_ts(payload.get("updated_at"))
    if updated is None:
        return "EXPORT-BAD", "updated_at unparsable: %r" % (payload.get("updated_at"),)
    age_h = (now - updated).total_seconds() / 3600.0
    if age_h > MAX_AGE_H:
        return "EXPORT-STALE", ("updated_at=%s age=%.1fh > %.0fh cap"
                                % (updated.strftime("%Y-%m-%d %H:%M"), age_h, MAX_AGE_H))
    latest = latest_round_ts(rounds_text)
    if latest is not None:
        lag_h = (latest - updated).total_seconds() / 3600.0
        if lag_h > LAG_H:
            return "EXPORT-STALE", ("round %s is %.1fh newer than export %s "
                                    "(face not refreshed while work happened)"
                                    % (latest.strftime("%Y-%m-%d %H:%M"), lag_h,
                                       updated.strftime("%Y-%m-%d %H:%M")))
    return "EXPORT-GREEN", ("three lines present, updated_at=%s age=%.1fh"
                            % (updated.strftime("%Y-%m-%d %H:%M"), age_h))


def check(root=None, now=None):
    """Live read of the export face against the round ledger."""
    root = root or ROOT
    path = os.path.join(root, *EXPORT_PATH.split("/"))
    rounds = os.path.join(root, *ROUNDS_PATH.split("/"))
    rounds_text = ""
    if os.path.exists(rounds):
        try:
            with open(rounds, "rb") as f:
                rounds_text = f.read().decode("utf-8", "replace")
        except OSError:
            rounds_text = ""
    if not os.path.exists(path):
        return export_verdict(None, now=now, rounds_text=rounds_text, exists=False)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return "EXPORT-BAD", "unreadable: %r" % e
    return export_verdict(raw, now=now, rounds_text=rounds_text)


def selftest():
    """Six fixtures: one clean, five refusal branches (all must be red)."""
    now = datetime.datetime(2026, 10, 27, 21, 0)
    good = {
        "company": "BigCompute",
        "updated_at": "2026-10-27 20:30",
        "current_activity": "round in progress",
        "latest_artifact": "state/x.txt (2026-10-27 20:29)",
        "next_milestone": "N1 月末归集 10-31 22:43",
    }
    rounds_old = "2026-10-27 19x tokens: local=0 api=0\n"

    def enc(obj):
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    cases = []

    def case(label, raw, want, needle, rounds_text=rounds_old, exists=True):
        verdict, detail = export_verdict(raw, now=now, rounds_text=rounds_text,
                                         exists=exists)
        ok = verdict == want and (needle is None or needle in detail)
        cases.append(ok)
        print("  [%s] %s -> %s (%s)" % ("PASS" if ok else "FAIL", label,
                                        verdict, detail))

    case("A clean fresh export -> GREEN", enc(good), "EXPORT-GREEN", None)

    old = dict(good, updated_at="2026-10-24 12:00")
    case("B age 81h -> STALE", enc(old), "EXPORT-STALE", "cap")

    behind = dict(good, updated_at="2026-10-27 12:00")
    case("C round 19x newer than 12:00 export -> STALE",
         enc(behind), "EXPORT-STALE", "newer than export",
         rounds_text="2026-10-27 19x tokens: local=0\n")

    missing = dict(good)
    missing.pop("next_milestone")
    case("D missing next_milestone -> BAD", enc(missing), "EXPORT-BAD",
         "next_milestone missing")

    mojibake = dict(good, current_activity="轮：CEO 自检\ufffd回执")
    case("E U+FFFD in a line -> BAD", enc(mojibake), "EXPORT-BAD", "U+FFFD")

    gbk = json.dumps(dict(good, current_activity="轮中：中文"), ensure_ascii=False).encode("gbk")
    case("F GBK-encoded bytes -> BAD", gbk, "EXPORT-BAD", "UTF-8")

    case("G absent file -> MISSING", None, "EXPORT-MISSING", "does not exist",
         exists=False)

    # H guards the minute arm: a regex that only took the hour reported a
    # 20:30 export as 20:00, understating lag/age by up to 59 minutes.
    h_ok = (parse_ts("2026-10-27 20:30") == datetime.datetime(2026, 10, 27, 20, 30)
            and parse_ts("2026-10-27 06x") == datetime.datetime(2026, 10, 27, 6, 0))
    cases.append(h_ok)
    print("  [%s] H minutes preserved + hour mask floors to :00"
          % ("PASS" if h_ok else "FAIL"))

    print("status_export_check selftest: %d/%d PASS" % (sum(cases), len(cases)))
    return 0 if all(cases) else 1


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "selftest":
        return selftest()
    if cmd == "check":
        now = None
        if "--now" in argv:
            now = parse_ts(argv[argv.index("--now") + 1])
            if now is None:
                print("bad --now value")
                return 2
        verdict, detail = check(now=now)
        print("VERDICT=%s %s" % (verdict, detail))
        return 0 if verdict == "EXPORT-GREEN" else 1
    print("usage: %s [check [--now 'YYYY-MM-DD HH:MM'] | selftest]" % argv[0])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))