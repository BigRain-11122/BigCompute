#!/usr/bin/env python3
"""probe_registry_census.py - probe numbering single-source census (T190).

Why this exists
---------------
qa_smoke's probe numbers used to live only in prose: the numbered docstrings,
a hand-written verdict string, and the charter bullet list.  Nothing compared
them, so a reused number (no.20 was twice occupied; no.22 served two probe
families) or a probe added without a charter clause stayed invisible.

This tool makes the number registry the single source (PROBE_REGISTRY in
qa_smoke.py) and checks three faces:

  1. registry  - numbers unique, no gaps outside the reserved slots
  2. functions - every registry entry points at a defined probe function
  3. charter   - every registry number has a clause in section 3, and every
                 clause number is implemented or explicitly reserved

Verdicts: PROBE-REGISTRY-CLEAN / PROBE-REGISTRY-GAPS / PROBE-REGISTRY-UNVERIFIED
Exit:     0 clean, 1 gaps, 2 unverified (files unreadable / unparsable)

Read-only.  No network, no writes.  Source stays pure ASCII: the Chinese
markers used by the charter (yuliu = reserved) are written as unicode
escapes so this file stays pure ASCII.
"""
import argparse
import ast
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SMOKE = os.path.join(ROOT, "Tools", "qa_smoke.py")
DEFAULT_CHARTER = os.path.join(ROOT, "docs", "qa-smoke-test-charter.md")

RESERVED_WORD = "\u9884\u7559"          # reserved slot marker in the charter
SECTION_START = "## \u4e09\u3001"        # "## 3." heading
SECTION_END = "## \u56db\u3001"          # "## 4." heading
BASE_START = "## \u4e00\u3001"           # "## 1." heading (frozen four)
BASE_END = "## \u4e8c\u3001"             # "## 2." heading
BULLET_RE = re.compile(r"^\s*-\s*(\d+)\.", re.MULTILINE)
REGISTRY_NAME = "PROBE_REGISTRY"


def charter_numbers(text):
    """Return (extension, reserved, base) parsed from the charter.

    extension = numbered bullets in section 3; reserved = those marked as a
    reserved slot; base = the four frozen probes declared in section 1's
    table (they need no section-3 clause).
    """
    start = text.find(SECTION_START)
    end = text.find(SECTION_END)
    if start < 0 or end <= start:
        return None, None, None
    body = text[start:end]
    numbers, reserved = [], set()
    for line in body.splitlines():
        m = re.match(r"^\s*-\s*(\d+)\.", line)
        if not m:
            continue
        no = int(m.group(1))
        numbers.append(no)
        if RESERVED_WORD in line:
            reserved.add(no)

    base = []
    b_start = text.find(BASE_START)
    b_end = text.find(BASE_END)
    if b_start >= 0 and b_end > b_start:
        for line in text[b_start:b_end].splitlines():
            m = re.match(r"^\|\s*(\d+)\s*\|", line)
            if m:
                base.append(int(m.group(1)))
    return numbers, reserved, base


def clause_occurrences(text):
    """Return {no: [lineno, ...]} for every numbered clause in the whole file.

    charter_numbers() only reads section 3, so a clause that was copied into a
    later section (the "追加区" after section 5) kept its number and drifted
    out of sight: the same probe read as documented while two different
    versions of its rule coexisted.  Counting the whole document makes the
    copy visible.
    """
    counts = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^\s*-\s*(\d+)\.", line)
        if m:
            counts.setdefault(int(m.group(1)), []).append(lineno)
    return counts


def registry_records(source):
    """Return [(no, fn_name)] from the module-level PROBE_REGISTRY literal."""
    tree = ast.parse(source)
    node = None
    for item in tree.body:
        if isinstance(item, ast.Assign):
            for tgt in item.targets:
                if isinstance(tgt, ast.Name) and tgt.id == REGISTRY_NAME:
                    node = item.value
    if node is None or not isinstance(node, ast.List):
        return None, None
    records = []
    for elt in node.elts:
        if not isinstance(elt, ast.Tuple) or len(elt.elts) < 3:
            return None, None
        no_node, _, fn_node = elt.elts[0], elt.elts[1], elt.elts[2]
        if not isinstance(no_node, ast.Constant) or not isinstance(
                no_node.value, int):
            return None, None
        fn_name = fn_node.id if isinstance(fn_node, ast.Name) else None
        records.append((no_node.value, fn_name))
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    return records, defined


def census(smoke_path=None, charter_path=None):
    """Run the three-face comparison and return a machine-readable report."""
    smoke_path = smoke_path or DEFAULT_SMOKE
    charter_path = charter_path or DEFAULT_CHARTER
    report = {"smoke": smoke_path, "charter": charter_path,
              "problems": [], "registry": [], "reserved": []}
    try:
        with open(smoke_path, "r", encoding="utf-8") as f:
            src = f.read()
        with open(charter_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        report["verdict"] = "PROBE-REGISTRY-UNVERIFIED"
        report["problems"].append("unreadable: %r" % (e,))
        report["summary"] = "unreadable inputs"
        return report

    records, defined = registry_records(src)
    numbers, reserved, base = charter_numbers(text)
    if records is None:
        report["verdict"] = "PROBE-REGISTRY-UNVERIFIED"
        report["problems"].append("PROBE_REGISTRY literal not found in %s"
                                  % os.path.basename(smoke_path))
    if numbers is None:
        report["verdict"] = "PROBE-REGISTRY-UNVERIFIED"
        report["problems"].append("charter section 3 heading not found in %s"
                                  % os.path.basename(charter_path))
    if records is None or numbers is None:
        report["summary"] = "unparsable inputs"
        return report

    reg_nos = [no for no, _ in records]
    report["registry"] = reg_nos
    report["reserved"] = sorted(reserved)
    report["base"] = base

    seen = set()
    for no in reg_nos:
        if no in seen:
            report["problems"].append("duplicate probe no.%d" % no)
        seen.add(no)

    for no, fn_name in records:
        if not fn_name:
            report["problems"].append("no.%d entry is not a plain function "
                                      "name" % no)
        elif fn_name not in defined:
            report["problems"].append("no.%d -> %s is not defined"
                                      % (no, fn_name))

    top = max(reg_nos) if reg_nos else 0
    for no in range(1, top + 1):
        if no not in seen and no not in reserved:
            report["problems"].append("gap: no.%d has no probe and is not "
                                      "reserved" % no)

    charter_set = set(numbers)
    base_set = set(base)
    for no in reg_nos:
        if no not in charter_set and no not in base_set:
            report["problems"].append("no.%d has no charter clause" % no)
    for no in numbers:
        if no not in seen and no not in reserved:
            report["problems"].append("charter clause no.%d has no probe"
                                      % no)

    occurrences = clause_occurrences(text)
    report["duplicate_clauses"] = []
    for no in sorted(occurrences):
        lines = occurrences[no]
        if len(lines) > 1:
            report["duplicate_clauses"].append(no)
            report["problems"].append(
                "DUPLICATE-CLAUSE no.%d at %s"
                % (no, ",".join("L%d" % n for n in lines)))

    if report["problems"]:
        report["verdict"] = "PROBE-REGISTRY-GAPS"
    else:
        report["verdict"] = "PROBE-REGISTRY-CLEAN"
    report["summary"] = (
        "%s probes=%d base=%d charter_ext=%d reserved=%s dup=%d problems=%d"
        % (report["verdict"], len(reg_nos), len(base), len(numbers),
           report["reserved"] or "none", len(report["duplicate_clauses"]),
           len(report["problems"])))
    return report


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


SMOKE_FIXTURE = """\
def probe_one():
    return True, "ok"


def probe_two():
    return True, "ok"


PROBE_REGISTRY = [
    (1, "probe one", probe_one),
    (2, "probe two", probe_two),
]
"""

CHARTER_FIXTURE = """\
## \u4e00\u3001frozen four
| # | judge |
|---|---|
| 1 | first base probe |
## \u4e8c\u3001evidence
## \u4e09\u3001extension probes
- 2. second probe.
## \u56db\u3001execution
- 99. not a probe clause.
"""

EXT_ANCHOR = "## \u56db\u3001"

def _charter_with(extra_lines):
    """Insert extra bullet lines at the end of section 3."""
    head, _, tail = CHARTER_FIXTURE.partition(EXT_ANCHOR)
    return head + "".join(extra_lines) + EXT_ANCHOR + tail


def selftest():
    """Fixtures: clean / charter clause missing / duplicate number / gap."""
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print("[%s] S%s %s%s" % ("PASS" if ok else "FAIL",
                                 len(results), name,
                                 "" if ok else " :: " + detail))

    with tempfile.TemporaryDirectory() as tmp:
        smoke = os.path.join(tmp, "qa_smoke.py")
        charter = os.path.join(tmp, "charter.md")

        _write(smoke, SMOKE_FIXTURE)
        _write(charter, CHARTER_FIXTURE)
        rep = census(smoke, charter)
        check("clean pair -> CLEAN+exit0",
              rep["verdict"] == "PROBE-REGISTRY-CLEAN",
              rep.get("summary", ""))

        # missing charter clause for probe 2
        _write(charter, CHARTER_FIXTURE.replace("- 2. second probe.\n", ""))
        rep = census(smoke, charter)
        check("probe without charter clause -> GAPS",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("no.2 has no charter clause" in p
                      for p in rep["problems"]),
              str(rep["problems"]))

        # charter clause without probe, outside the reserved slot
        _write(charter, _charter_with(["- 3. ghost probe.\n"]))
        rep = census(smoke, charter)
        check("charter clause without probe -> GAPS",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("no.3 has no probe" in p for p in rep["problems"]),
              str(rep["problems"]))

        # reserved slot must not be reported as a gap
        _write(charter, _charter_with(
            ["- 3. %s slot for a future probe.\n" % RESERVED_WORD]))
        rep = census(smoke, charter)
        check("reserved slot -> CLEAN",
              rep["verdict"] == "PROBE-REGISTRY-CLEAN"
              and 3 in rep["reserved"],
              rep.get("summary", ""))

        # duplicate number
        _write(smoke, SMOKE_FIXTURE.replace(
            '(2, "probe two", probe_two)', '(1, "probe two", probe_two)'))
        _write(charter, CHARTER_FIXTURE)
        rep = census(smoke, charter)
        check("duplicate number -> GAPS",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("duplicate probe no.1" in p for p in rep["problems"]),
              str(rep["problems"]))

        # gap: registry declares 1 and 4
        _write(smoke, SMOKE_FIXTURE.replace(
            '(2, "probe two", probe_two)', '(4, "probe two", probe_two)'))
        rep = census(smoke, charter)
        check("numbering gap -> GAPS",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("gap: no.2" in p for p in rep["problems"])
              and any("gap: no.3" in p for p in rep["problems"]),
              str(rep["problems"]))

        # same clause copied into a second section: two rules, one number
        _write(charter, _charter_with(["- 2. second probe, older copy.\n"]))
        rep = census(smoke, charter)
        check("duplicate clause -> GAPS+DUPLICATE-CLAUSE",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and rep["duplicate_clauses"] == [2]
              and any("DUPLICATE-CLAUSE no.2" in p
                      for p in rep["problems"]),
              str(rep["problems"]))

        # unreadable input must not read as clean
        rep = census(os.path.join(tmp, "missing.py"), charter)
        check("missing file -> UNVERIFIED",
              rep["verdict"] == "PROBE-REGISTRY-UNVERIFIED",
              rep["verdict"])

    # live pair: real repo files must parse (CLEAN or GAPS both parse)
    live = census()
    check("live repo parses",
          live["verdict"] in ("PROBE-REGISTRY-CLEAN", "PROBE-REGISTRY-GAPS"),
          live.get("summary", ""))

    print("probe_registry_census selftest: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def main():
    ap = argparse.ArgumentParser(
        description="probe numbering single-source census (T190)")
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "selftest"])
    ap.add_argument("--smoke", default=None)
    ap.add_argument("--charter", default=None)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    if args.command == "selftest":
        return selftest()

    rep = census(args.smoke, args.charter)
    print("probe registry census: %s" % rep["summary"])
    for no in rep["registry"]:
        print("  no.%-2d registered" % no)
    for p in rep["problems"]:
        print("  PROBLEM %s" % p)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print("  evidence: %s" % args.json_out)
    if rep["verdict"] == "PROBE-REGISTRY-CLEAN":
        return 0
    if rep["verdict"] == "PROBE-REGISTRY-GAPS":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())