#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""money_entry_census.py -- money-face writer census (BC-P-195, 2026-11-05)

Why: T-20261104-86 caught `quota-issue -0.5` printing quota out of thin air.
The fix landed in cost_ledger, but the class is wider -- every tool that
appends/mutates a money row can carry the same sign-flip hole. Nobody can
answer "which ledger writers are guarded" by eye once there are 90 tools.

What it does (static, read-only):
  1. discovers writer functions in Tools/*.py -- a function whose body calls
     `_append_row(`/`append_row(` or rewrites a ledger (`open(<x>, "w"` ...
     note: that second form is reported but only asked to be declared, not
     forced to carry a guard, since read-side rewrites exist).
  2. compares the discovered set with DECLARED below, in BOTH directions:
     undeclared writer = a new money face with no guard review (RED),
     stale declaration = manifest rot (RED).
  3. for each declared entry, checks its guard tokens still exist in the
     file (RED when a guard was renamed or deleted silently).

Judge is "declared and still guarded", never "looks fine".
"""
import argparse
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = HERE

# (file, function) -> guard tokens that MUST appear in the file source.
# Tokens are literal substrings; they name the refusal branch, so renaming
# or deleting it turns this RED instead of silently passing.
DECLARED = {
    ("cost_ledger.py", "add_row"): [
        '_need_positive("price"', '_need_non_negative("amount"'],
    ("cost_ledger.py", "quota_issue"): ['"status": "invalid_amount"'],
    ("cost_ledger.py", "quota_consume"): ['"status": "invalid_amount"'],
    ("cost_ledger.py", "cloud_entry"): ['_need_positive("amount_mtok"'],
    ("cost_ledger.py", "borrow_entry"): ['_need_positive("tokens_mtok"'],
    ("cost_ledger.py", "cloud_bill"): ['_need_positive("unit_cost_b"'],
    ("cost_ledger.py", "borrow_settle"): ['_need_non_negative("unit_cost_b"'],
    ("cost_ledger.py", "l1_settle"): ['_need_non_negative("unit_cost_b"'],
    ("cost_ledger.py", "l1_record"): ['"status": "invalid_usage"'],
    ("deal_flow_mock.py", "ledger_write"): ['qty <= 0'],
}

# Rail writers that are not money faces. The reason is written down rather
# than implied; anything else that writes a rail must be declared guarded.
NON_MONEY = {
    ("cost_ledger.py", "_selftest"): "test harness on temp rails",
    ("delivery_dispatch_mock.py", "intake"): "delivery events: no amounts",
    ("delivery_dispatch_mock.py", "_advance"): "delivery state flip: no amounts",
    ("distr_flow_mock.py", "run_row"): "distribution ring: amounts settle on the deal rail",
    ("gacha_cap_gate.py", "cmd_attempt"): "CLI wrapper; the price guard lives upstream in attempt(), this only persists the verdict row",
    ("gacha_cap_gate.py", "run_drill"): "drill harness on a temp ledger",
    ("gacha_probability_audit.py", "rails_drill"): "drill-rail generator: synthetic rows, immediately contract-checked by _rail_errors + real-rail rejection",
}


def _source(path):
    # utf-8-sig: two Tools files carry a BOM; decoding it as plain utf-8
    # would fail ast.parse on a perfectly valid file (instrument bug, not a
    # file defect -- caught by this census on its first live run).
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def _writer_calls(tree):
    """Yield (func_name, lineno, end_lineno) for functions that write a rail.

    Two write shapes count: append helpers (`_append_row`/`append_row`) and
    in-place rewrites (`open(<ledger>, "w"`), which is how month-end settle
    flips rows without appending.
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        writes = False
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name in ("_append_row", "append_row"):
                writes = True
                break
            if name == "open" and any(
                    isinstance(a, ast.Constant) and a.value in ("w", "w+", "wt")
                    for a in sub.args):
                # Only a rewrite of a rail counts -- evidence/JSON dumps
                # (`open(path, "w")`) are not money faces, and treating them
                # as such buries the real ones in noise.
                arg0 = sub.args[0] if sub.args else None
                label = ""
                if isinstance(arg0, ast.Name):
                    label = arg0.id
                elif isinstance(arg0, ast.Attribute):
                    label = arg0.attr
                elif isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    label = arg0.value
                if "ledger" in label.lower() or "rail" in label.lower():
                    writes = True
                    break
        if writes:
            out.append((node.name, node.lineno, node.end_lineno))
    return out


def discover(files):
    """Returns ({(file, func): (lineno, end_lineno)}, [parse_error, ...])."""
    found = {}
    parse_errors = []
    for fn in files:
        path = os.path.join(TOOLS_DIR, fn)
        try:
            tree = ast.parse(_source(path))
        except (OSError, SyntaxError) as exc:
            parse_errors.append("%s: %s" % (fn, exc))
            continue
        for name, line, end in _writer_calls(tree):
            found[(fn, name)] = (line, end)
    return found, parse_errors


def _func_text(src, span):
    line, end = span
    return "\n".join(src.splitlines()[line - 1:end])


def judge(declared, found, sources, parse_errors=()):
    """Pure: returns (problems, rows). Guard tokens are matched inside the
    writer's OWN body (birth site), never file-wide -- a refusal branch that
    lives in another function does not guard this one."""
    problems = []
    rows = []
    for err in parse_errors:
        problems.append("PARSE-FAIL %s" % err)
    disc = dict(found)

    for key, span in sorted(disc.items()):
        if key in NON_MONEY:
            rows.append((key, "NON-MONEY", NON_MONEY[key]))
            continue
        if key not in declared:
            problems.append("UNDECLARED-WRITER %s:%s @L%d (new rail writer, "
                            "guard it or declare it non-money)"
                            % (key[0], key[1], span[0]))
            rows.append((key, "UNDECLARED", "L%d" % span[0]))
            continue
        src = sources.get(key[0], "")
        body = _func_text(src, span)
        missing = [tok for tok in declared[key] if tok not in body]
        if missing:
            problems.append("GUARD-MISSING %s:%s token(s)=%s (guard must be "
                            "inside this function)" % (key[0], key[1], missing))
            rows.append((key, "GUARD-MISSING", str(missing)))
        else:
            rows.append((key, "GUARDED", "%d token(s)" % len(declared[key])))

    for key in sorted(set(declared) - set(disc)):
        problems.append("STALE-DECLARATION %s:%s (no such writer in source)"
                        % key)
        rows.append((key, "STALE", "-"))
    return problems, rows


def run(files=None):
    files = files or sorted(f for f in os.listdir(TOOLS_DIR)
                            if f.endswith(".py") and not f.startswith("_"))
    found, parse_errors = discover(files)
    sources = {}
    for fn in set(list(DECLARED.keys()) + [(f, None) for f in files]):
        name = fn[0]
        if name not in sources:
            try:
                sources[name] = _source(os.path.join(TOOLS_DIR, name))
            except OSError:
                sources[name] = ""
    problems, rows = judge(DECLARED, found, sources, parse_errors)
    verdict = "MONEY-GUARD-CLEAN" if not problems else "MONEY-GUARD-GAPS"
    return verdict, problems, rows, len(files), len(found)


def render(verdict, problems, rows, scanned, writers):
    lines = ["verdict=%s scanned=%d writers=%d declared=%d"
             % (verdict, scanned, writers, len(DECLARED))]
    lines.append("domain=Tools/*.py (top level, non-underscore) "
                 "not_scanned=subdirs,live rails (read-only census)")
    for key, state, detail in rows:
        lines.append("  %-8s %s:%s  %s" % (state, key[0], key[1], detail))
    for p in problems:
        lines.append("PROBLEM " + p)
    return "\n".join(lines)


# ------------------------------------------------------------------ fixtures

def _selftest():
    checks = []

    def ck(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    found = {("x.py", "writer_a"): (1, 1), ("x.py", "writer_b"): (2, 2)}
    declared = {("x.py", "writer_a"): ["GUARD-TOKEN"],
                ("x.py", "writer_b"): ["GUARD-TOKEN"]}
    srcs = {"x.py": "GUARD-TOKEN\nGUARD-TOKEN"}
    probs, rows = judge(declared, found, srcs)
    ck("S1 both declared+guarded -> clean", probs == [], str(probs))
    ck("S2 rows carry GUARDED", all(r[1] == "GUARDED" for r in rows))

    probs, rows = judge(declared, found, {"x.py": "nothing here\nnothing here"})
    ck("S3 guard token deleted -> two GUARD-MISSING",
       len([p for p in probs if p.startswith("GUARD-MISSING")]) == 2, str(probs))

    # birth-site rule: a token elsewhere in the file must NOT satisfy it
    probs, rows = judge(declared, found,
                        {"x.py": "GUARD-TOKEN\nnothing here"})
    ck("S3b token in another function does not guard this one",
       any("GUARD-MISSING x.py:writer_b" in p for p in probs)
       and not any("GUARD-MISSING x.py:writer_a" in p for p in probs), str(probs))

    probs, rows = judge(declared, {("x.py", "writer_a"): (1, 1)},
                        {"x.py": "GUARD-TOKEN\nGUARD-TOKEN"})
    ck("S4 decl for vanished writer -> STALE-DECLARATION",
       any(p.startswith("STALE-DECLARATION x.py:writer_b") for p in probs), str(probs))

    probs, rows = judge({("x.py", "writer_a"): ["G"]},
                        {("x.py", "writer_a"): (1, 1), ("x.py", "writer_new"): (9, 9)},
                        {"x.py": "G"})
    ck("S5 undeclared new writer -> UNDECLARED-WRITER named",
       any("writer_new" in p and p.startswith("UNDECLARED-WRITER") for p in probs),
       str(probs))

    probs, rows = judge({("x.py", "writer_a"): ["G"]}, {},
                        {"x.py": "G"}, ["x.py: bad"])
    ck("S6 parse failure is visible and red",
       any(p.startswith("PARSE-FAIL") for p in probs), str(probs))

    # real source: every declared token must actually exist today
    src = _source(os.path.join(TOOLS_DIR, "cost_ledger.py"))
    ck("S7 cost_ledger add_row guard present",
       '_need_positive("price"' in src and '_need_non_negative("amount"' in src)

    # discovery must see append-based AND rewrite-based writers
    real, _errs = discover(["cost_ledger.py"])
    ck("S8 discovery finds add_row(append) + cloud_bill(rewrite)",
       ("cost_ledger.py", "add_row") in real
       and ("cost_ledger.py", "cloud_bill") in real, str(sorted(real))[:120])

    # a deleted-guard simulation on a synthetic tree, not on disk
    tree = ast.parse("def w(a):\n    _append_row('p', {'x': 1})\n")
    ck("S9 ast writer detection", _writer_calls(tree) == [("w", 1, 2)],
       str(_writer_calls(tree)))
    tree2 = ast.parse("def w(ledger_path):\n    with open(ledger_path, 'w') as fh:\n        fh.write('x')\n")
    ck("S9b rewrite shape detected", _writer_calls(tree2) == [("w", 1, 3)],
       str(_writer_calls(tree2)))

    ck("S10 determinism", judge(declared, found, srcs)[1] == judge(declared, found, srcs)[1])

    n_pass = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(("PASS " if ok else "FAIL ") + name + ("  [%s]" % detail if detail else ""))
    print("SELFTEST %s %d/%d" % ("PASS" if n_pass == len(checks) else "FAIL",
                                 n_pass, len(checks)))
    return 0 if n_pass == len(checks) else 1


def main():
    p = argparse.ArgumentParser(description="money-face writer census")
    p.add_argument("cmd", nargs="?", default="run", choices=["run", "selftest"])
    p.add_argument("--json-out", dest="json_out", default=None)
    p.add_argument("--txt-out", dest="txt_out", default=None)
    args = p.parse_args()
    if args.cmd == "selftest":
        sys.exit(_selftest())
    verdict, problems, rows, scanned, writers = run()
    text = render(verdict, problems, rows, scanned, writers)
    print(text)
    payload = {
        "verdict": verdict,
        "scanned_files": scanned,
        "writers": writers,
        "declared": len(DECLARED),
        "problems": problems,
        "rows": [{"file": k[0], "function": k[1], "state": s, "detail": d}
                 for k, s, d in rows],
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    if args.txt_out:
        with open(args.txt_out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    sys.exit(0 if verdict == "MONEY-GUARD-CLEAN" else 1)


if __name__ == "__main__":
    main()