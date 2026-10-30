#!/usr/bin/env python3
"""ledger_markers.py - single source for ledger row markers (T172).

The read side (qa_smoke probe no.8, merged-last-line sniff) and the write
side (round_append J5 dual-timestamp lint) answer the same question: "is
this one row or two rows fused together?". They used to answer it with
different rules: the read side moved to the combo marker in T171 while the
write side still counted ANY "HH:MM" occurrence, so a line that merely
cites a second-hand time (e.g. "orders 13:42:04 ...") was refused by the
writer yet accepted by the reader. T172 removes that asymmetry by moving
the marker here and having both sides import it (no literal duplication).

Marker shape (combo):
  (1) line-start anchor  ^\\?\\d{2}:\\d{1,2}x?  -- one per real entry head
  (2) time immediately followed by a known line-opening word -- catches the
      embedded head of a truly fused line, x-tolerant ("18:0x") and with an
      optional :SS for os-loop / idle_selfcheck stamps.

Openers are written as \\u escapes so this file stays pure ASCII (group
coding law). Evidence: state/t63-marker-eval-20261009.txt (J1/J2/J3 gates,
93 live false positives eliminated, 0 new false positives, 2 fresh x-form
gains) and state/t171-marker-live-20261030.txt (113 old-rule hits down to
3, all three genuinely fused).
"""
import re

HB_OPENERS = (u"\u8F6E\u4E2D\u523B\u75D5|\u8F6E\u5C3E\u5FC3\u8DF3|"
              u"\u8F6E\u5C3E\u65F6\u95F4\u6233|\u8865\u8BB0\u8F6E\u6BD5|"
              u"\u8F6E\u6BD5|\u8F6E\u5C3E|\u8F6E\u9996|\u8F6E\u8865|"
              u"\u65F6\u6807\u52D8\u6B63|"
              r"os-loop|idle_selfcheck|round_end|round_append|night_watch"
              r"|mid-round")
HB_MARKER = (r"(^\s*\\?\d{2}:\d{1,2}x?)"
             r"|(\d{2}:\d{1,2}x?(?::\d{2}x?)?\s*(?:" + HB_OPENERS + r"))")

HB_MARKER_RE = re.compile(HB_MARKER)
# Raw "HH:MM" shape, kept only for diagnostics in reject messages
# (never used as a verdict: that was the T172 asymmetry).
RAW_TS_RE = re.compile(r"\d{2}:\d{2}")


def count_markers(line):
    """Row-head markers in one line. None = marker engine failed (fail-closed)."""
    try:
        return len(HB_MARKER_RE.findall(line))
    except Exception:  # noqa: BLE001 - caller decides fail-closed
        return None


def count_raw_stamps(line):
    """Raw HH:MM occurrences - diagnostic only, never a verdict."""
    try:
        return len(RAW_TS_RE.findall(line))
    except Exception:  # noqa: BLE001
        return None