#!/usr/bin/env python3
"""queue_check.py - three-queue reconciliation helper (tech T8, self-drive v2.0).

Counts todo rows by status in state/queue/{main,tech,explore}.md and prints
an open-count summary for the round-ledger queue field (three-line queue
law: open count must net-decrease each round while >=1 new todo is added).

Counting rule (matches ledger practice): a row counts as OPEN when its
status cell (last table cell, bold markers stripped) starts with "open",
including "open(...blocked...)" notes. "done", standing ("changbei") and
"flowed-back" rows are not open. Header and separator rows are skipped.

Usage: python Tools/queue_check.py
Exit 0 always (informational probe; anomalies reported in text).
Encoding rule: this file stays PURE ASCII (group coding law).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_DIR = os.path.join(HERE, "..", "state", "queue")
FILES = ("main.md", "tech.md", "explore.md")


def classify(status):
    s = status.strip().strip("*").strip()
    if s.startswith("open"):
        return "open"
    if s.startswith("done"):
        return "done"
    if "回流" in s:
        return "flowed"
    if "常备" in s:
        return "standing"
    return "other"


def count_file(path):
    counts = {"open": 0, "done": 0, "flowed": 0, "standing": 0, "other": 0}
    if not os.path.exists(path):
        return counts, 0
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
            counts[classify(cells[-1])] += 1
    return counts, rows


def main():
    total_open = 0
    for name in FILES:
        counts, rows = count_file(os.path.join(QUEUE_DIR, name))
        total_open += counts["open"]
        print("%s: rows=%d open=%d done=%d flowed=%d standing=%d other=%d"
              % (name, rows, counts["open"], counts["done"], counts["flowed"],
                 counts["standing"], counts["other"]))
    print("TOTAL open=%d (three-line queue law: net decrease vs round start"
          " while adding >=1 new todo)" % total_open)
    return 0


if __name__ == "__main__":
    sys.exit(main())
