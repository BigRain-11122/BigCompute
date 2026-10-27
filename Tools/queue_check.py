#!/usr/bin/env python3
"""queue_check.py - three-queue reconciliation helper (tech T8, self-drive v2.0).

Counts todo rows by status in state/queue/{main,tech,explore}.md and prints
an open-count summary for the round-ledger queue field (three-line queue
law: open count must net-decrease each round while >=1 new todo is added).

Counting rule (matches ledger practice): a row counts as OPEN when its
status cell (last table cell, bold markers stripped) starts with "open",
including "open(...blocked...)" notes. "done", standing ("changbei") and
"flowed-back" rows are not open. Header and separator rows are skipped.

T146 premise gate (advisory, never blocks): an OPEN row that asserts damage
as established fact ("already corrupted / already lost / already hardened")
must carry a runnable command or a file-level evidence pointer. Rows that
assert damage but name no evidence are listed as premise=UNVERIFIED, so the
next reader does not inherit an unproven premise as truth. Two backfilled
cases: tech T145 (premise later disproved by byte census) and tech T139
("eCloud owns the port" later measured as zero listeners).

Usage: python Tools/queue_check.py [--selftest]
Exit 0 always (informational probe; anomalies reported in text).
Encoding rule: this file stays PURE ASCII (Chinese markers are \\u escapes).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_DIR = os.path.join(HERE, "..", "state", "queue")
FILES = ("main.md", "tech.md", "explore.md")

# Damage asserted as fact (not a hypothesis).
ASSERTION_TERMS = (
    "\u5df2\u635f\u574f",      # already corrupted
    "\u5df2\u4e22\u5931",      # already lost
    "\u5df2\u88ab\u7834\u574f",  # already broken (passive)
    "\u5df2\u56fa\u5316",      # already hardened in place
    "\u5df2\u6c61\u67d3",      # already polluted
    "\u5df2\u622a\u65ad",      # already truncated
    "\u5df2\u5931\u6548",      # already invalid
)

EVIDENCE_HINTS = (
    "state/", "docs/", "qa/", "cph4/", "Tools/", "http",
    "\u8bc1\u636e",  # evidence
    "\u5b9e\u6d4b",  # measured
    "\u666e\u67e5",  # census
    "\u5b57\u8282",  # byte-level
    "\u884c\u53f7",  # line number
    "\u6307\u9488",  # pointer
    "\u65e5\u5fd7",  # log
    ".json", ".log", ".txt", ".md",
)


def classify(status):
    s = status.strip().strip("*").strip()
    if s.startswith("open"):
        return "open"
    if s.startswith("done"):
        return "done"
    if "\u56de\u6d41" in s:
        return "flowed"
    if "\u5e38\u5907" in s:
        return "standing"
    return "other"


def premise_flagged(text):
    """True when the row asserts damage but names no evidence.

    Returns (flagged, hit_terms).
    """
    hits = [t for t in ASSERTION_TERMS if t in text]
    if not hits:
        return False, []
    has_evidence = any(h in text for h in EVIDENCE_HINTS)
    return (not has_evidence), hits


def scan_file(path):
    """Return (counts, rows, flagged) for one queue file.

    flagged is a list of (row_id, hit_terms) for OPEN rows whose damage
    claim carries no evidence pointer.
    """
    counts = {"open": 0, "done": 0, "flowed": 0, "standing": 0, "other": 0}
    flagged = []
    if not os.path.exists(path):
        return counts, 0, flagged
    rows = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.startswith("| ") or "---" in line:
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not cells or cells[0] == "#":
                continue
            rows += 1
            kind = classify(cells[-1])
            counts[kind] += 1
            if kind == "open":
                bad, hits = premise_flagged(line)
                if bad:
                    flagged.append((cells[0], hits))
    return counts, rows, flagged


def count_file(path):
    counts, rows, _flagged = scan_file(path)
    return counts, rows


def main():
    total_open = 0
    total_flagged = 0
    for name in FILES:
        counts, rows, flagged = scan_file(os.path.join(QUEUE_DIR, name))
        total_open += counts["open"]
        total_flagged += len(flagged)
        print("%s: rows=%d open=%d done=%d flowed=%d standing=%d other=%d"
              % (name, rows, counts["open"], counts["done"], counts["flowed"],
                 counts["standing"], counts["other"]))
        for row_id, hits in flagged:
            print("  premise=UNVERIFIED %s: %s (no evidence pointer)"
                  % (row_id, ", ".join(hits)))
    print("TOTAL open=%d (three-line queue law: net decrease vs round start"
          " while adding >=1 new todo)" % total_open)
    print("TOTAL premise=UNVERIFIED %d (T146: assert-damage rows naming no"
          " evidence are listed, never blocked)" % total_flagged)
    return 0


def selftest():
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    d = tempfile.mkdtemp(prefix="qc146_")
    hdr = "| # | todo | pointer | status |\n|---|---|---|---|\n"

    # S1: open row asserts damage, no evidence -> flagged
    p1 = os.path.join(d, "s1.md")
    with open(p1, "w", encoding="utf-8") as f:
        f.write(hdr + "| A1 | row \u5df2\u635f\u574f | x | open |\n")
    c, r, fl = scan_file(p1)
    check("S1 assert-no-evidence flagged", len(fl) == 1 and fl[0][0] == "A1")
    check("S1 counts open=1", c["open"] == 1 and r == 1)

    # S2: same assertion but with a state/ pointer -> not flagged
    p2 = os.path.join(d, "s2.md")
    with open(p2, "w", encoding="utf-8") as f:
        f.write(hdr + "| A2 | row \u5df2\u635f\u574f | state/x.json | open |\n")
    c, r, fl = scan_file(p2)
    check("S2 evidence pointer clears", len(fl) == 0 and c["open"] == 1)

    # S3: done rows are out of scope even with the assertion
    p3 = os.path.join(d, "s3.md")
    with open(p3, "w", encoding="utf-8") as f:
        f.write(hdr + "| A3 | row | x | **done** \u5df2\u635f\u574f |\n")
    c, r, fl = scan_file(p3)
    check("S3 done row not flagged", len(fl) == 0 and c["done"] == 1)

    # S4: open row with no damage claim -> clean
    p4 = os.path.join(d, "s4.md")
    with open(p4, "w", encoding="utf-8") as f:
        f.write(hdr + "| A4 | row | x | open |\n")
    c, r, fl = scan_file(p4)
    check("S4 plain open row clean", len(fl) == 0 and c["open"] == 1)

    # S5: multiple assertions reported together, header/separator skipped
    p5 = os.path.join(d, "s5.md")
    with open(p5, "w", encoding="utf-8") as f:
        f.write(hdr + "| A5 | row \u5df2\u622a\u65ad + \u5df2\u5931\u6548 | x | open |\n")
    c, r, fl = scan_file(p5)
    check("S5 both terms captured",
          len(fl) == 1 and len(fl[0][1]) == 2 and r == 1)

    # S6: missing file is not a crash
    c, r, fl = scan_file(os.path.join(d, "nope.md"))
    check("S6 missing file tolerated",
          r == 0 and c["open"] == 0 and fl == [])

    # S7: evidence via path prefix alone (qa/) counts
    p7 = os.path.join(d, "s7.md")
    with open(p7, "w", encoding="utf-8") as f:
        f.write(hdr + "| A7 | row \u5df2\u4e22\u5931 | qa/x.log | open |\n")
    c, r, fl = scan_file(p7)
    check("S7 qa/ path clears", len(fl) == 0)

    ok = all(v for _n, v in checks)
    for name, v in checks:
        print("%s %s" % ("PASS" if v else "FAIL", name))
    print("SELFTEST %s (%d/%d)" % ("PASS" if ok else "FAIL",
                                   sum(1 for _n, v in checks if v),
                                   len(checks)))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())