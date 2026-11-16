#!/usr/bin/env python3
"""T251 one-off: rename duplicate selftest case ids, labels only.

Rule: inside a selftest scope, the first occurrence of a label keeps it; every
later occurrence gets a fresh number from the same letter prefix (S/T/J...),
allocated above the scope's highest number. Nothing else in the source moves:
only the label characters inside the string literal are rewritten.

Why numbers and not letter suffixes: labels already use one optional lowercase
letter (S45b, S7c), so appending another letter would produce labels the census
regex cannot read. Numbers stay valid and stay unique.

Usage:
    python unique_selftest_ids_t251.py --dry-run
    python unique_selftest_ids_t251.py --apply --map-out state/selftest-id-rename-map.json
"""
import argparse
import ast
import json
import os
import re
import sys

LABEL_RE = re.compile(r"^([A-Z]{1,3})([0-9]{1,3})([a-z]?)$")
LABEL_START_RE = re.compile(r"^([A-Z]{1,3}[0-9]{1,3}[a-z]?)(?=\s|$)")
CASE_FUNCS = {
    "chk", "_chk", "check", "_check", "case", "_case",
    "record", "_record", "ok", "_ok", "expect", "_expect",
}

DEFAULT_FILES = [
    "Tools/bake_accept_check.py",
    "Tools/first_order_consent_drill.py",
    "Tools/gacha_cap_gate.py",
    "Tools/ledger_parallel_census.py",
    "Tools/month_end_collect.py",
    "Tools/queue_check.py",
    "Tools/renewal_reminder_drill.py",
    "Tools/review_pkg_refresh.py",
    "Tools/row_loss_census.py",
]


def label_start(text):
    m = LABEL_START_RE.match(text)
    return m.group(1) if m else None


def _callee(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def scope_cases(node):
    """Yield (label, lineno, col_offset, end_col_offset, value) in source order."""
    out = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call) or _callee(sub) not in CASE_FUNCS:
            continue
        if not sub.args:
            continue
        first = sub.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        lab = label_start(first.value)
        if not lab:
            continue
        out.append((lab, first.lineno, first.col_offset,
                    getattr(first, "end_col_offset", None), first.value))
    out.sort(key=lambda t: (t[1], t[2]))
    return out


def plan_file(path, text):
    """Return (renames, notes). renames = [(label_old, label_new, lineno, col, value)]."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [], ["PARSE-ERROR %s" % exc]
    renames, notes = [], []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "selftest" not in node.name.lower():
            continue
        cases = scope_cases(node)
        if not cases:
            continue
        seen, existing = {}, {c[0] for c in cases}
        used_numbers = {}
        for lab in existing:
            m = LABEL_RE.match(lab)
            if m:
                used_numbers.setdefault(m.group(1), set()).add(int(m.group(2)))
        for lab, lineno, col, endcol, value in cases:
            if lab not in seen:
                seen[lab] = True
                continue
            m = LABEL_RE.match(lab)
            if not m:
                notes.append("SKIP unparseable label %r at L%d" % (lab, lineno))
                continue
            prefix = m.group(1)
            nums = used_numbers.setdefault(prefix, set())
            nxt = max(nums) + 1 if nums else 1
            while nxt in nums:
                nxt += 1
            nums.add(nxt)
            new = "%s%d" % (prefix, nxt)
            existing.add(new)
            renames.append((lab, new, lineno, col, value))
            notes.append("%s %s::%s -> %s  L%d  %s"
                         % (os.path.basename(path), node.name, lab, new,
                            lineno, value[:60]))
    return renames, notes


def apply_renames(text, renames):
    """Rewrite label chars inside string literals. renames are absolute spans."""
    lines = text.splitlines(keepends=True)
    by_line = {}
    for lab, new, lineno, col, value in renames:
        by_line.setdefault(lineno, []).append((col, lab, new))
    for lineno, edits in by_line.items():
        line = lines[lineno - 1]
        edits.sort(key=lambda e: e[0], reverse=True)
        for col, lab, new in edits:
            start = col + 1  # skip the opening quote
            if line[start:start + len(lab)] != lab:
                raise SystemExit("MISMATCH L%d col%d expected %r got %r"
                                 % (lineno, col, lab, line[start:start + 12]))
            line = line[:start] + new + line[start + len(lab):]
        lines[lineno - 1] = line
    return "".join(lines)


def read_text(path):
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    return raw.decode("utf-8-sig"), bom


def write_text(path, text, bom):
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    with open(path, "wb") as f:
        f.write(data)


def selftest():
    checks = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    src = (
        "def selftest():\n"
        "    chk(\"S1 alpha\", True)\n"
        "    chk(\"S1 beta\", True)\n"
        "    chk(\"S1 gamma\", True)\n"
    )
    renames, notes = plan_file("x.py", src)
    chk("S1 first occurrence keeps its label",
        [r[0] for r in renames] == ["S1", "S1"], renames)
    chk("S2 later occurrences get fresh numbers above the max",
        [r[1] for r in renames] == ["S2", "S3"], renames)
    out = apply_renames(src, renames)
    chk("S3 rewritten source has three distinct labels",
        out.count('"S1 ') == 1 and '"S2 ' in out and '"S3 ' in out, out)
    chk("S4 applied source still parses and keeps call count",
        out.count("chk(") == 3 and ast.parse(out) is not None)

    src2 = (
        "def selftest():\n"
        "    chk(\"S7c one\", True)\n"
        "    chk(\"S7c two\", True)\n"
        "    chk(\"S9 other\", True)\n"
    )
    r2, _ = plan_file("y.py", src2)
    chk("S5 suffixed label duplicate keeps prefix and skips taken numbers",
        len(r2) == 1 and r2[0][1] == "S10", r2)

    selfsrc = open(os.path.abspath(__file__), encoding="utf-8").read()
    r3, _ = plan_file("self.py", selfsrc)
    chk("S6 this file's own selftest is already unique", r3 == [], r3)

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print("%-4s %s%s" % ("PASS" if ok else "FAIL", name,
                             "" if ok else "  <- %r" % (detail,)))
    print("\n%d/%d PASS" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--map-out", default=None)
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    ap.add_argument("files", nargs="*", default=None)
    args = ap.parse_args(argv)

    if not (args.dry_run or args.apply):
        return selftest()

    root = args.root
    files = args.files or DEFAULT_FILES
    mapping, notes_all = [], []
    for rel in files:
        path = os.path.join(root, rel)
        text, bom = read_text(path)
        renames, notes = plan_file(path, text)
        notes_all.extend(notes)
        if not renames:
            continue
        for lab, new, lineno, col, value in renames:
            mapping.append({"file": rel.replace("\\", "/"), "old": lab,
                            "new": new, "line": lineno, "text": value[:80]})
        if args.apply:
            write_text(path, apply_renames(text, renames), bom)
            print("APPLIED %s (%d renames)" % (rel, len(renames)))
        else:
            print("WOULD RENAME %s (%d)" % (rel, len(renames)))

    print("\nrenames=%d files=%d mode=%s"
          % (len(mapping), len({m["file"] for m in mapping}),
             "apply" if args.apply else "dry-run"))
    if args.map_out:
        with open(args.map_out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print("map=%s" % args.map_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
