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
  3. charter   - every registry number has a clause in section 3 (or in any
                 later section: the clause set comes from one whole-file scan,
                 so a clause drifted out of section 3 stays visible), and every
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

# M198: a clause may be split out into an appendix file, leaving a pointer
# like "- 5. title｜detail=docs/qa-probes-x.md·p5" in section 3.  The pointer
# is prose until something resolves it: rename/delete the appendix, renumber
# it, or add a clause and forget the appendix, and nobody notices.
POINTER_SEP = "\uff5c"                  # fullwidth vertical bar
DETAIL_WORD = "\u7ec6\u8282"            # "detail"
MIDDOT = "\u00b7"
APPENDIX_GLOB = "qa-probes-*.md"
POINTER_RE = re.compile(
    r"^\s*-\s*(\d+)\.\s*(.*?)\s*" + POINTER_SEP + r"\s*"
    + DETAIL_WORD + r"=(\S+)" + MIDDOT + r"p(\d+)\s*$")

# T206: a clause keeps its number but may be rewritten in another shape.  The
# dash bullet was the only shape recognised, so a clause copied back as a
# table row, "N、", "N)" or "* N." stayed invisible -- the very duplicate
# T200 exists to catch, just in a different shell.  The table variant only
# counts outside section 1, whose numbered table is the frozen base list
# (parsed separately by charter_numbers) and would otherwise be counted twice.
CLAUSE_VARIANTS = (
    ("dash", re.compile(r"^\s*-\s*(\d+)\.")),
    ("star", re.compile(r"^\s*\*\s*(\d+)\.")),
    ("ideographic", re.compile(r"^\s*(\d+)\u3001")),
    ("paren", re.compile(r"^\s*(\d+)\)")),
    ("table", re.compile(r"^\|\s*(\d+)\s*\|")),
)
BASE_TABLE_VARIANT = "table"


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


def _base_line_range(text):
    """Return (first, last) 0-based line indexes of the section-1 base block.

    Table rows there are the frozen four probes, not extension clauses.  A
    missing block returns None, which only means "no base table to exclude"
    -- the caller still scans every line.
    """
    lines = text.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if line.startswith(BASE_START):
            start = i
        elif start is not None and line.startswith(BASE_END):
            end = i
            break
    if start is None:
        return None
    return (start, end if end is not None else len(lines))


def clause_scan(text):
    """Return [(no, lineno, kind)] for every numbered clause, any shape.

    One line yields at most one clause (first variant that matches).  Table
    rows inside section 1 are skipped: that table is the base list and
    counting it here would both double-count and invent "clauses" for the
    frozen four.
    """
    base = _base_line_range(text)
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for kind, rx in CLAUSE_VARIANTS:
            m = rx.match(line)
            if not m:
                continue
            if (kind == BASE_TABLE_VARIANT and base is not None
                    and base[0] + 1 <= lineno <= base[1]):
                break
            found.append((int(m.group(1)), lineno, kind))
            break
    return found


def clause_occurrences(text):
    """Return {no: [lineno, ...]} for every numbered clause in the whole file.

    charter_numbers() only reads section 3, so a clause that was copied into a
    later section (the "追加区" after section 5) kept its number and drifted
    out of sight: the same probe read as documented while two different
    versions of its rule coexisted.  Counting the whole document makes the
    copy visible.
    """
    counts = {}
    for no, lineno, _kind in clause_scan(text):
        counts.setdefault(no, []).append(lineno)
    return counts


def _norm(text):
    """Collapse whitespace so a title comparison is about words, not spacing."""
    return re.sub(r"\s+", " ", text or "").strip()


def charter_pointers(text):
    """Return [(no, title, path, clause_no, lineno)] from section 3.

    Only section 3 carries the "detail=<file>·pN" pointers; a pointer copied
    elsewhere is prose, and section 1's table has no pointers at all.
    """
    start = text.find(SECTION_START)
    end = text.find(SECTION_END)
    if start < 0 or end <= start:
        return []
    out = []
    for lineno, line in enumerate(text[start:end].splitlines(), 1):
        m = POINTER_RE.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2).strip(), m.group(3),
                        int(m.group(4)), lineno))
    return out


def appendix_clauses(path):
    """Return {clause_no: (lineno, text)} for the '- N.' lines of an appendix."""
    table = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                m = BULLET_RE.match(line)
                if m:
                    table[int(m.group(1))] = (lineno, line)
    except OSError:
        return None
    return table


def appendix_files(root):
    """Return appendix paths under <root>/docs (sorted)."""
    docs = os.path.join(root, "docs")
    try:
        names = sorted(os.listdir(docs))
    except OSError:
        return []
    return [os.path.join(docs, n) for n in names
            if n.startswith("qa-probes-") and n.endswith(".md")]


def refs_problems(text, root=None, require=None):
    """Resolve every section-3 detail pointer; return (problems, report).

    Three faces per pointer: the file is on disk, it holds a "- N." clause
    with the pointer's number, and that clause's text still contains the
    charter clause title.  A clause with no pointer is a problem too, and an
    appendix clause nobody cites is a rule without a reader.

    require=None means auto: enforce as soon as the charter has any pointer
    or the docs tree already holds appendix files (stripping the pointers
    must not switch the check off).
    """
    root = root or ROOT
    pointers = charter_pointers(text)
    numbers, _reserved, _base = charter_numbers(text)
    files = appendix_files(root)
    enforce = require
    if enforce is None:
        enforce = bool(pointers) or bool(files)
    report = {"enforced": bool(enforce), "pointers": len(pointers),
              "appendix_files": len(files), "appendix_clauses": 0,
              "resolved": 0, "reasons": {}}
    if not enforce:
        return [], report

    problems = []
    cited = set()
    tables = {}
    pointed = {no for no, _t, _p, _c, _l in pointers}

    for no in (numbers or []):
        if no not in pointed:
            problems.append("POINTER-MISSING no.%d clause has no detail "
                            "pointer" % no)

    for no, title, rel, cno, _lineno in pointers:
        full = os.path.join(root, *rel.replace("\\", "/").split("/"))
        name = os.path.basename(full)
        if not os.path.isfile(full):
            problems.append("POINTER-FILE no.%d -> %s not on disk"
                            % (no, rel))
            continue
        if cno != no:
            problems.append("POINTER-NUMBER no.%d points at %s p%d"
                            % (no, name, cno))
        if full not in tables:
            tables[full] = appendix_clauses(full)
        table = tables[full] or {}
        cited.add((name, cno))
        hit = table.get(cno)
        if hit is None:
            problems.append("POINTER-CLAUSE no.%d -> %s has no '- %d.' clause"
                            % (no, name, cno))
            continue
        want = _norm(title)
        if want and want not in _norm(hit[1]):
            problems.append("POINTER-TITLE no.%d %r not in %s L%d"
                            % (no, title, name, hit[0]))
            continue
        report["resolved"] += 1

    for full in files:
        if full not in tables:
            tables[full] = appendix_clauses(full)
    for full in sorted(tables):
        table = tables[full] or {}
        name = os.path.basename(full)
        report["appendix_clauses"] += len(table)
        for cno in sorted(table):
            if (name, cno) not in cited:
                problems.append("POINTER-ORPHAN %s p%d has no charter pointer"
                                % (name, cno))
    report["reasons"] = _count_prefixes(problems)
    return problems, report


def _count_prefixes(problems):
    counts = {}
    for p in problems:
        counts[p.split(" ", 1)[0]] = counts.get(p.split(" ", 1)[0], 0) + 1
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


def census(smoke_path=None, charter_path=None, root=None, require_refs=None):
    """Run the comparison and return a machine-readable report.

    root/require_refs carry the M198 detail-pointer face; both default to the
    live repo and auto-enforcement, so an existing caller keeps working.
    """
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

    # Clause coverage uses the whole-file scan, not just section 3 (T201):
    # a clause that lives only in a later section must still be counted, or
    # it silently makes its probe look undocumented.
    occurrences = clause_occurrences(text)
    clause_set = set(occurrences)
    base_set = set(base)
    variants = {}
    for _no, _lineno, kind in clause_scan(text):
        variants[kind] = variants.get(kind, 0) + 1
    report["clause_variants"] = variants
    report["clause_numbers"] = sorted(clause_set)
    for no in reg_nos:
        if no not in clause_set and no not in base_set:
            report["problems"].append("no.%d has no charter clause" % no)
    for no in sorted(clause_set):
        if no not in seen and no not in reserved:
            report["problems"].append("charter clause no.%d has no probe"
                                      % no)
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
        # M198: pointer resolution runs last so the numbering face keeps its
        # own verdict shape when the pointers are fine.
        refs, refs_rep = refs_problems(text, root, require_refs)
        report["refs"] = refs_rep
        for p in refs:
            report["problems"].append(p)
        if report["problems"]:
            report["verdict"] = "PROBE-REGISTRY-GAPS"
        else:
            report["verdict"] = "PROBE-REGISTRY-CLEAN"
    report["summary"] = (
        "%s probes=%d base=%d charter_ext=%d clauses=%d reserved=%s "
        "dup=%d refs=%d/%d problems=%d"
        % (report["verdict"], len(reg_nos), len(base), len(numbers),
           len(occurrences), report["reserved"] or "none",
           len(report["duplicate_clauses"]),
           report.get("refs", {}).get("resolved", 0),
           report.get("refs", {}).get("pointers", 0),
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
Plain prose clause placeholder.
"""

EXT_ANCHOR = "## \u56db\u3001"
DRIFT_ANCHOR = "Plain prose clause placeholder."

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
        rep = census(smoke, charter, tmp)
        check("clean pair -> CLEAN+exit0",
              rep["verdict"] == "PROBE-REGISTRY-CLEAN",
              rep.get("summary", ""))

        # missing charter clause for probe 2
        _write(charter, CHARTER_FIXTURE.replace("- 2. second probe.\n", ""))
        rep = census(smoke, charter, tmp)
        check("probe without charter clause -> GAPS",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("no.2 has no charter clause" in p
                      for p in rep["problems"]),
              str(rep["problems"]))

        # charter clause without probe, outside the reserved slot
        _write(charter, _charter_with(["- 3. ghost probe.\n"]))
        rep = census(smoke, charter, tmp)
        check("charter clause without probe -> GAPS",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("no.3 has no probe" in p for p in rep["problems"]),
              str(rep["problems"]))

        # reserved slot must not be reported as a gap
        _write(charter, _charter_with(
            ["- 3. %s slot for a future probe.\n" % RESERVED_WORD]))
        rep = census(smoke, charter, tmp)
        check("reserved slot -> CLEAN",
              rep["verdict"] == "PROBE-REGISTRY-CLEAN"
              and 3 in rep["reserved"],
              rep.get("summary", ""))

        # duplicate number
        _write(smoke, SMOKE_FIXTURE.replace(
            '(2, "probe two", probe_two)', '(1, "probe two", probe_two)'))
        _write(charter, CHARTER_FIXTURE)
        rep = census(smoke, charter, tmp)
        check("duplicate number -> GAPS",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("duplicate probe no.1" in p for p in rep["problems"]),
              str(rep["problems"]))

        # gap: registry declares 1 and 4
        _write(smoke, SMOKE_FIXTURE.replace(
            '(2, "probe two", probe_two)', '(4, "probe two", probe_two)'))
        rep = census(smoke, charter, tmp)
        check("numbering gap -> GAPS",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("gap: no.2" in p for p in rep["problems"])
              and any("gap: no.3" in p for p in rep["problems"]),
              str(rep["problems"]))

        # same clause copied into a second section: two rules, one number
        _write(charter, _charter_with(["- 2. second probe, older copy.\n"]))
        rep = census(smoke, charter, tmp)
        check("duplicate clause -> GAPS+DUPLICATE-CLAUSE",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and rep["duplicate_clauses"] == [2]
              and any("DUPLICATE-CLAUSE no.2" in p
                      for p in rep["problems"]),
              str(rep["problems"]))

        # unreadable input must not read as clean
        rep = census(os.path.join(tmp, "missing.py"), charter, tmp)
        check("missing file -> UNVERIFIED",
              rep["verdict"] == "PROBE-REGISTRY-UNVERIFIED",
              rep["verdict"])

        # clause drifted out of section 3: must still be counted (T201)
        _write(smoke, SMOKE_FIXTURE)
        _write(charter, CHARTER_FIXTURE.replace(
            DRIFT_ANCHOR, "- 99. drifted clause outside section 3."))
        rep = census(smoke, charter, tmp)
        check("clause only outside section 3 -> GAPS",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and 99 in rep.get("clause_numbers", [])
              and any("no.99 has no probe" in p for p in rep["problems"]),
              str(rep["problems"]))

        # same drifted clause with an implementing probe -> CLEAN, and the
        # probe itself must not be flagged "has no charter clause"
        _write(charter, CHARTER_FIXTURE.replace(
            DRIFT_ANCHOR, "- 3. clause living outside section 3."))
        drifted_smoke = SMOKE_FIXTURE.replace(
            "PROBE_REGISTRY = [",
            "def probe_three():\n    return True, \"ok\"\n\n\nPROBE_REGISTRY = ["
        ).replace(
            '(2, "probe two", probe_two),',
            '(2, "probe two", probe_two),\n    (3, "drifted", probe_three),')
        _write(smoke, drifted_smoke)
        rep = census(smoke, charter, tmp)
        check("drifted clause with probe -> CLEAN",
              rep["verdict"] == "PROBE-REGISTRY-CLEAN"
              and 3 in rep["registry"] and 3 in rep.get("clause_numbers", []),
              rep.get("summary", "") + " " + str(rep["problems"]))

        # T206: the same clause re-written in another shape must still be
        # seen.  Four shapes, each standing alone as "clause without probe".
        _write(smoke, SMOKE_FIXTURE)
        for label, clause in (
                ("table row", "| 99 | drifted table clause |"),
                ("ideographic", "99\u3001drifted ideographic clause"),
                ("paren", "99) drifted paren clause"),
                ("star bullet", "* 99. drifted star clause")):
            _write(charter, CHARTER_FIXTURE.replace(DRIFT_ANCHOR, clause))
            rep = census(smoke, charter, tmp)
            check("%s clause -> GAPS" % label,
                  rep["verdict"] == "PROBE-REGISTRY-GAPS"
                  and 99 in rep.get("clause_numbers", [])
                  and any("no.99 has no probe" in p
                          for p in rep["problems"]),
                  str(rep["problems"]))

        # T206: the section-1 numbered table is the frozen base list, not
        # extension clauses -- a base row number must not become a phantom
        # "clause without probe".
        _write(smoke, SMOKE_FIXTURE)
        _write(charter, CHARTER_FIXTURE.replace(
            "| 1 | first base probe |",
            "| 1 | first base probe |\n| 7 | frozen base row |"))
        rep = census(smoke, charter, tmp)
        check("base table row not counted as clause",
              rep["verdict"] == "PROBE-REGISTRY-CLEAN"
              and 7 not in rep.get("clause_numbers", [])
              and not any("no.7" in p for p in rep["problems"]),
              rep.get("summary", "") + " " + str(rep["problems"]))

        # ---- M198: the "detail=<appendix>·pN" pointers must resolve ----
        docs = os.path.join(tmp, "docs")
        os.makedirs(docs, exist_ok=True)
        appendix = os.path.join(docs, "qa-probes-t.md")
        pointer = ("- 2. second probe%s%s=docs/qa-probes-t.md%sp2\n"
                   % (POINTER_SEP, DETAIL_WORD, MIDDOT))
        ptr_charter = CHARTER_FIXTURE.replace("- 2. second probe.\n", pointer)
        _write(smoke, SMOKE_FIXTURE)

        _write(charter, ptr_charter)
        _write(appendix, "- 2. second probe (full rule).\n")
        rep = census(smoke, charter, tmp)
        check("resolvable pointer -> CLEAN",
              rep["verdict"] == "PROBE-REGISTRY-CLEAN"
              and rep["refs"]["resolved"] == 1,
              rep.get("summary", "") + " " + str(rep["problems"]))

        _write(charter, ptr_charter.replace("qa-probes-t.md",
                                            "qa-probes-gone.md"))
        rep = census(smoke, charter, tmp)
        check("pointer to a missing appendix -> GAPS+POINTER-FILE",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("POINTER-FILE" in p for p in rep["problems"]),
              str(rep["problems"]))

        _write(charter, ptr_charter)
        _write(appendix, "- 7. a clause that is not the pointer's number.\n")
        rep = census(smoke, charter, tmp)
        check("appendix lacks the clause -> GAPS+POINTER-CLAUSE",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("POINTER-CLAUSE" in p for p in rep["problems"]),
              str(rep["problems"]))

        _write(appendix, "- 2. an entirely different rule.\n")
        rep = census(smoke, charter, tmp)
        check("appendix title drifted -> GAPS+POINTER-TITLE",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("POINTER-TITLE" in p for p in rep["problems"]),
              str(rep["problems"]))

        # stripping the pointers must not switch the face off: the appendix
        # is still on disk, so a clause without a pointer is a problem.
        _write(charter, CHARTER_FIXTURE)
        _write(appendix, "- 2. second probe (full rule).\n")
        rep = census(smoke, charter, tmp)
        check("pointer stripped while appendix exists -> GAPS",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("POINTER-MISSING" in p for p in rep["problems"]),
              str(rep["problems"]))

        _write(charter, ptr_charter)
        _write(appendix, "- 2. second probe (full rule).\n"
                         "- 9. a rule nobody points at.\n")
        rep = census(smoke, charter, tmp)
        check("orphan appendix clause -> GAPS+POINTER-ORPHAN",
              rep["verdict"] == "PROBE-REGISTRY-GAPS"
              and any("POINTER-ORPHAN" in p for p in rep["problems"]),
              str(rep["problems"]))

        _write(appendix, "- 2. second probe (full rule).\n")
        rep = census(smoke, charter, tmp, require_refs=False)
        check("require_refs=False -> face off (opt-out is explicit)",
              rep["verdict"] == "PROBE-REGISTRY-CLEAN"
              and rep["refs"]["enforced"] is False,
              rep.get("summary", ""))

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
    ap.add_argument("--root", default=None,
                    help="repo root used to resolve appendix pointers")
    ap.add_argument("--require-refs", dest="require_refs",
                    action="store_true", default=None,
                    help="force the pointer face even with no pointers/files")
    args = ap.parse_args()

    if args.command == "selftest":
        return selftest()

    rep = census(args.smoke, args.charter, args.root, args.require_refs)
    print("probe registry census: %s" % rep["summary"])
    refs = rep.get("refs") or {}
    if refs.get("enforced"):
        print("  refs: resolved=%d/%d appendix_clauses=%d files=%d %s"
              % (refs.get("resolved", 0), refs.get("pointers", 0),
                 refs.get("appendix_clauses", 0),
                 refs.get("appendix_files", 0),
                 " ".join("%s=%d" % (k, v) for k, v in
                          sorted((refs.get("reasons") or {}).items()))))
    shapes = rep.get("clause_variants") or {}
    print("  shapes: %s" % (" ".join("%s=%d" % (k, shapes[k])
                                     for k in sorted(shapes)) or "none"))
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