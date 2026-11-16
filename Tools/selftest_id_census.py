#!/usr/bin/env python3
"""Census: duplicate selftest case ids across Tools/*.py.

Why this exists: evidence files and round ledgers cite case ids ("S44 green"),
so a label that names two different assertions makes every citation
unresolvable. T250 found 18 such duplicates inside pool_igniter.selftest and
had to skip to S81 for new fixtures just to avoid overlapping.

Read-only. Counts labels only - it does not judge whether the assertions are
any good, and it never edits a file. Scope is per selftest function: two
different tools may both own an "S1" and that is not a collision.
"""
import argparse
import ast
import json
import os
import re
import sys

LABEL_RE = re.compile(r"^([A-Z]{1,3}[0-9]{1,3}[a-z]?)(?=\s|$)")
SELFTEST_NAME_HINT = "selftest"
# Only strings passed as the first positional arg of a case recorder count as
# case ids. Assertion messages ("... , \"J1 miss\"") and inline tokens
# ("J1" in p) look similar but are not identities - counting them would flag
# every use of a rule name as a duplicate.
CASE_FUNCS = {
    "chk", "_chk", "check", "_check", "case", "_case",
    "record", "_record", "ok", "_ok", "expect", "_expect",
}


def label_of(text):
    """Return the case id a string literal starts with, or None.

    "S44 lightmap QC" -> "S44"; "S45b edge" -> "S45b"; "S1" -> "S1";
    "lightmap QC" -> None.
    """
    if not isinstance(text, str):
        return None
    m = LABEL_RE.match(text)
    return m.group(1) if m else None


def _selftest_scopes(tree):
    """Yield (scope_name, node) for every function whose name mentions selftest."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if SELFTEST_NAME_HINT in node.name.lower():
                out.append((node.name, node))
    return out


def _callee_name(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _labels_in(node):
    """Yield (label, lineno, text) for case ids inside a selftest function."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        if _callee_name(sub) not in CASE_FUNCS:
            continue
        if not sub.args:
            continue
        first = sub.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        lab = label_of(first.value)
        if lab:
            yield lab, getattr(first, "lineno", 0), first.value


def scan_source(path, text):
    """Scan one file's source. Returns a scope list plus parse status."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return {"file": path, "parse": "PARSE-ERROR", "why": str(exc),
                "scopes": []}
    scopes = []
    for name, node in _selftest_scopes(tree):
        entries = list(_labels_in(node))
        if not entries:
            continue
        scopes.append({
            "scope": name,
            "labels": len(entries),
            "cases": [{"label": lab, "line": ln, "text": txt[:90]}
                      for lab, ln, txt in entries],
        })
    return {"file": path, "parse": "ok", "why": "", "scopes": scopes}


def collect(root):
    """Walk .py files under root and scan each."""
    files = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs
                   if d not in (".git", ".codely-cli", "__pycache__", "media")]
        for name in sorted(names):
            if name.endswith(".py"):
                files.append(os.path.join(base, name))
    reports = []
    for path in sorted(files):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except OSError as exc:
            reports.append({"file": path, "parse": "UNREADABLE",
                            "why": str(exc), "scopes": []})
            continue
        reports.append(scan_source(path, text))
    return reports


def find_duplicates(reports):
    """Per scope, group labels; keep groups with more than one occurrence."""
    dupes = []
    for rep in reports:
        for scope in rep["scopes"]:
            buckets = {}
            for case in scope["cases"]:
                buckets.setdefault(case["label"], []).append(case)
            for label in sorted(buckets):
                group = buckets[label]
                if len(group) > 1:
                    dupes.append({
                        "file": rep["file"],
                        "scope": scope["scope"],
                        "label": label,
                        "count": len(group),
                        "cases": group,
                    })
    dupes.sort(key=lambda d: (d["file"], d["scope"], d["label"]))
    return dupes


def summarize(reports, dupes):
    scopes = [s for rep in reports for s in rep["scopes"]]
    labels = sum(s["labels"] for s in scopes)
    parse_errors = [rep for rep in reports if rep["parse"] != "ok"]
    dup_extra = sum(d["count"] - 1 for d in dupes)
    if parse_errors and not scopes:
        verdict = "SELFTEST-IDS-UNREADABLE"
    elif dupes:
        verdict = "DUPLICATE-SELFTEST-IDS"
    elif labels == 0:
        verdict = "NO-LABELS-FOUND"
    else:
        verdict = "SELFTEST-IDS-CLEAN"
    return {
        "verdict": verdict,
        "scanned_files": len(reports),
        "parse_errors": len(parse_errors),
        "scopes": len(scopes),
        "labels": labels,
        "dup_scopes": len({(d["file"], d["scope"]) for d in dupes}),
        "dup_ids": len(dupes),
        "dup_extra": dup_extra,
    }


def render(summary, dupes, limit=40):
    print("SELFTEST-ID-CENSUS %s scanned=%d scopes=%d labels=%d "
          "dup_scopes=%d dup_ids=%d dup_extra=%d parse_errors=%d"
          % (summary["verdict"], summary["scanned_files"], summary["scopes"],
             summary["labels"], summary["dup_scopes"], summary["dup_ids"],
             summary["dup_extra"], summary["parse_errors"]))
    print("  domain=Tools/**/*.py  not_scanned=.git .codely-cli media __pycache__")
    if not dupes:
        print("  duplicates=none")
        return
    for i, d in enumerate(dupes):
        if i >= limit:
            print("  ... (+%d more)" % (len(dupes) - limit))
            break
        rel = os.path.basename(d["file"])
        print("  DUPLICATE %s %s::%s x%d" % (rel, d["scope"], d["label"],
                                             d["count"]))
        for case in d["cases"]:
            print("      L%-5d %s" % (case["line"], case["text"]))


def run(root, json_out=None, limit=40):
    reports = collect(root)
    dupes = find_duplicates(reports)
    summary = summarize(reports, dupes)
    render(summary, dupes, limit=limit)
    if json_out:
        payload = dict(summary)
        payload["root"] = root
        payload["duplicates"] = dupes
        payload["parse_error_files"] = [
            {"file": rep["file"], "why": rep["why"]}
            for rep in reports if rep["parse"] != "ok"
        ]
        with open(json_out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print("  json=%s" % json_out)
    return summary


def selftest():
    import tempfile
    import shutil
    checks = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    tmp = tempfile.mkdtemp(prefix="selftest-id-census-")
    try:
        def write(name, body):
            path = os.path.join(tmp, name)
            os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            return path

        clean = write("clean.py", (
            "def selftest():\n"
            "    chk(\"S1 alpha\", True)\n"
            "    chk(\"S2 beta\", True)\n"
        ))
        rep = scan_source(clean, open(clean, encoding="utf-8").read())
        chk("S1 unique labels -> no duplicates", find_duplicates([rep]) == [])

        dup = write("dup.py", (
            "def selftest():\n"
            "    chk(\"S35 first meaning\", True)\n"
            "    chk(\"S35 second meaning\", True)\n"
        ))
        rep = scan_source(dup, open(dup, encoding="utf-8").read())
        d = find_duplicates([rep])
        chk("S2 same label twice in one scope -> one duplicate",
            len(d) == 1 and d[0]["label"] == "S35" and d[0]["count"] == 2, d)

        a = write("a.py", "def selftest_a():\n    chk(\"S1 a\", True)\n")
        b = write("b.py", "def selftest_b():\n    chk(\"S1 b\", True)\n")
        ra = scan_source(a, open(a, encoding="utf-8").read())
        rb = scan_source(b, open(b, encoding="utf-8").read())
        chk("S3 same label in different files/scopes -> not a collision",
            find_duplicates([ra, rb]) == [])

        none = write("none.py", "def selftest():\n    chk(\"alpha\", True)\n")
        rn = scan_source(none, open(none, encoding="utf-8").read())
        s = summarize([rn], find_duplicates([rn]))
        chk("S4 zero labels -> NO-LABELS-FOUND, not CLEAN",
            s["verdict"] == "NO-LABELS-FOUND" and s["labels"] == 0, s)

        broken = write("broken.py", "def selftest(:\n")
        rb2 = scan_source(broken, "def selftest(:\n")
        s2 = summarize([rb2], [])
        chk("S5 unparseable file -> UNREADABLE verdict, not clean",
            rb2["parse"] == "PARSE-ERROR" and s2["verdict"] == "SELFTEST-IDS-UNREADABLE",
            s2)

        suffix = write("suffix.py", (
            "def selftest():\n"
            "    chk(\"S45 base\", True)\n"
            "    chk(\"S45b variant\", True)\n"
        ))
        rs = scan_source(suffix, open(suffix, encoding="utf-8").read())
        chk("S6 S45 and S45b are distinct labels",
            find_duplicates([rs]) == []
            and {c["label"] for c in rs["scopes"][0]["cases"]} == {"S45", "S45b"})

        outside = write("outside.py", (
            "def helper():\n"
            "    chk(\"S1 not a selftest\", True)\n"
            "def selftest():\n"
            "    chk(\"S2 real\", True)\n"
        ))
        ro = scan_source(outside, open(outside, encoding="utf-8").read())
        chk("S7 labels outside selftest scope are ignored",
            len(ro["scopes"]) == 1 and ro["scopes"][0]["labels"] == 1, ro["scopes"])

        triple = write("triple.py", (
            "def selftest():\n"
            "    chk(\"S63 a\", True)\n"
            "    chk(\"S63 b\", True)\n"
            "    chk(\"S63 c\", True)\n"
        ))
        rt = scan_source(triple, open(triple, encoding="utf-8").read())
        dt = find_duplicates([rt])
        chk("S8 three occurrences -> dup_extra counts 2",
            len(dt) == 1 and dt[0]["count"] == 3
            and summarize([rt], dt)["dup_extra"] == 2)

        first = summarize([rt], dt)
        second = summarize([rt], find_duplicates([rt]))
        chk("S9 deterministic summary", first == second, (first, second))

        mixed = write("mixed.py", (
            "def selftest():\n"
            "    chk(\"Q1 one\", True)\n"
            "    chk(\"Q1 two\", True)\n"
            "    chk(\"S1 three\", True)\n"
        ))
        rm = scan_source(mixed, open(mixed, encoding="utf-8").read())
        dm = find_duplicates([rm])
        chk("S10 only the colliding label is reported",
            len(dm) == 1 and dm[0]["label"] == "Q1", dm)

        chk("S11 label regex rejects prose that merely starts with a capital",
            label_of("Lightmap QC") is None and label_of("S1 x") == "S1"
            and label_of("S1") == "S1" and label_of("SUPER") is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print("%-4s %s%s" % ("PASS" if ok else "FAIL", name,
                             "" if ok else "  <- %r" % (detail,)))
    print("\n%d/%d PASS" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    p_run = sub.add_parser("run", help="scan a tree")
    p_run.add_argument("root", nargs="?", default="Tools")
    p_run.add_argument("--json-out", default=None)
    p_run.add_argument("--limit", type=int, default=40)
    sub.add_parser("selftest", help="fixture suite")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd in (None, "run"):
        root = getattr(args, "root", "Tools")
        summary = run(root, json_out=getattr(args, "json_out", None),
                      limit=getattr(args, "limit", 40))
        if summary["verdict"] == "DUPLICATE-SELFTEST-IDS":
            return 2
        if summary["verdict"] in ("SELFTEST-IDS-UNREADABLE", "NO-LABELS-FOUND"):
            return 3
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
