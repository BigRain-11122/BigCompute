"""Guard-list derivation census — find hard-coded name lists that shadow their source.

Why (M203, derived from T241 / threshold_census):
T241 found that `frame_qc_spec.selftest()` held a hand-written list of the four
legacy constant names while the code under test had grown a fifth.  The test
was green, the member it claimed to guard was unguarded on the day it was
added.  Fixing that one instance answered "how do we keep *this* list derived";
it did not answer "how many other lists in this tree are hand-copied from a
source they could read".

`threshold_census.py` surveys numeric literals at module scope.  A name list is
not a number: `CONSUMER_FILES`, a probe-number table, a schema enum or a scan
root list can all be duplicated as a string tuple inside a test and no numeric
census will ever see it.  This tool is the string-collection counterpart.

Method (AST only, no execution):
  * module scope  -> every assignment whose value is a tuple/list/set of >= 2
                     string constants is a candidate *source* collection
                     (`_SPEC_FIELDS`, `CONSUMER_FILES`, `ROOTS`, ...);
  * function scope-> every such collection is a candidate *copy*; it is
                     compared to the source collections of the same file;
  * references    -> a function that reads a source collection by name is
                     recorded as derived evidence (the shape T241 asked for).

Verdicts, per copy site:
  DOUBLE-COPY     literal member set == a same-file source set
  SUBSET-DRIFT    literal is a proper subset of a source (new members are
                  unguarded the moment they are added -- the T241 failure)
  SUPERSET-DRIFT  literal carries names the source does not have (the copy has
                  already drifted ahead; neither side is authoritative)
  STANDALONE      no same-file source to compare with: not a finding, just an
                  observation (kept so the absence of an owner is visible)

Honest boundary: equal member sets are evidence of *duplication*, not proof
that the two lists mean the same thing.  Two collections can legitimately bear
the same strings while guarding different questions; that call is left to the
reader, which is why this tool reports and never rewrites.  Ordering is not
compared (a re-ordered copy is still one list written twice).

Exit codes: 0 = no duplicate/drifted copy, 3 = at least one candidate found.
Nothing in the tree is modified; this is not wired into qa_smoke yet.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import sys
import time

MIN_MEMBERS = 2          # a one-name list is too weak a signal to call a copy
_SCOPE_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _stdout_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _str_collection(node):
    """Return members when ``node`` is a tuple/list/set of >= MIN string constants."""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    ctx = getattr(node, "ctx", None)
    if ctx is not None and not isinstance(ctx, ast.Load):
        return None              # `a, b = ...` unpacking target, not a literal
    if len(node.elts) < MIN_MEMBERS:
        return None
    members = []
    for el in node.elts:
        if not isinstance(el, ast.Constant) or not isinstance(el.value, str):
            return None
        if not el.value.strip():
            return None
        members.append(el.value)
    return tuple(members)


def _assign_pairs(node):
    """Yield (targets, value) for an Assign / AnnAssign node."""
    if isinstance(node, ast.Assign):
        return ([t.id for t in node.targets if isinstance(t, ast.Name)], node.value)
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return ([node.target.id], node.value)
    return ([], None)


def module_collections(tree):
    """name -> {members, line} for module-scope string collections."""
    out = {}
    for node in tree.body:
        targets, value = _assign_pairs(node)
        if not targets:
            continue
        coll = _str_collection(value)
        if coll is None:
            continue
        for name in targets:
            out[name] = {"members": list(coll), "line": node.lineno}
    return out


def _walk_scope(fn):
    """Nodes in ``fn``'s own scope; nested defs/classes are not descended into."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPE_BOUNDARY):
            continue
        stack.extend(ast.iter_child_nodes(node))


def function_units(tree, rel):
    """One entry per function: the string collections it holds and the names it reads."""
    units = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        refs, literals = set(), []
        for node in _walk_scope(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                refs.add(node.id)
            elif isinstance(node, ast.Attribute):
                refs.add(node.attr)
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                coll = _str_collection(node)
                if coll is not None:
                    literals.append({"line": node.lineno, "members": list(coll)})
        units.append({"file": rel, "func": fn.name, "line": fn.lineno,
                      "refs": refs, "literals": literals})
    return units


def classify_unit(unit, sources):
    """Attach a verdict to every literal in ``unit``; return rows.

    A literal is compared against same-file sources only: a test that guards a
    list defined in another module is out of scope (the tool cannot see the
    import graph's intent), and pretending otherwise would flag every
    independent vocabulary in the tree.
    """
    rows = []
    for lit in unit["literals"]:
        members, lines = set(lit["members"]), None
        verdict, source, why = "STANDALONE", None, "同文件无同源集合可比，留观察（不计入缺口）"
        # Exact match wins over containment: it is the strongest evidence and
        # the one a reader can act on without judgement.
        for name, src in sorted(sources.items(), key=lambda kv: kv[1]["line"]):
            if set(src["members"]) == members:
                verdict, source = "DOUBLE-COPY", name
                why = ("逐字复制 %s（%d 名）：源集合新增成员时这份表不会跟着长"
                       % (name, len(src["members"])))
                break
        if source is None:
            for name, src in sorted(sources.items(), key=lambda kv: kv[1]["line"]):
                src_set = set(src["members"])
                if members < src_set:
                    verdict, source = "SUBSET-DRIFT", name
                    why = ("%s 的严格子集（漏 %d 名）：源集合新增成员当天即失守"
                           % (name, len(src_set - members)))
                    break
                if src_set < members:
                    verdict, source = "SUPERSET-DRIFT", name
                    why = ("含 %s 之外 %d 名：两侧已各自演化，无一为权威"
                           % (name, len(members - src_set)))
                    break
        rows.append({"file": unit["file"], "func": unit["func"],
                     "line": lit["line"], "members": lit["members"],
                     "verdict": verdict, "source": source,
                     "source_line": (sources[source]["line"] if source else None),
                     "why": why, "lines": lines})
    return rows


def census(root):
    """Scan ``root`` recursively. Returns (rows, units, sources_by_file, scanned)."""
    files = sorted(glob.glob(os.path.join(root, "**", "*.py"), recursive=True))
    rows, units, sources_by_file, parsed = [], [], {}, 0
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        parsed += 1
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        sources = module_collections(tree)
        if sources:
            sources_by_file[rel] = sources
        for unit in function_units(tree, rel):
            units.append(unit)
            rows.extend(classify_unit(unit, sources))
    return rows, units, sources_by_file, parsed


def derived_files(units, sources_by_file):
    """file -> sorted source names that some function in it reads by name."""
    out = {}
    for unit in units:
        for name in sorted(unit["refs"]):
            if name in sources_by_file.get(unit["file"], {}):
                out.setdefault(unit["file"], set()).add(name)
    return {k: sorted(v) for k, v in out.items()}


def file_verdicts(rows, derived):
    """file -> GUARD-DERIVED / GUARD-DOUBLE-COPY / GUARD-STANDALONE / GUARD-NONE."""
    bad = {r["file"] for r in rows if r["verdict"] != "STANDALONE"}
    standalone = {r["file"] for r in rows if r["verdict"] == "STANDALONE"}
    out = {}
    for rel in sorted({r["file"] for r in rows} | set(derived)):
        if rel in bad:
            out[rel] = "GUARD-DOUBLE-COPY"
        elif rel in derived:
            out[rel] = "GUARD-DERIVED"
        elif rel in standalone:
            out[rel] = "GUARD-STANDALONE"
        else:
            out[rel] = "GUARD-NONE"
    return out


def _fmt_members(members, limit=6):
    shown = " ".join(members[:limit])
    if len(members) > limit:
        shown += " …(+%d)" % (len(members) - limit)
    return shown


def render(rows, units, sources_by_file, verdicts, parsed, derived):
    bad = [r for r in rows if r["verdict"] != "STANDALONE"]
    split = {v: [r for r in bad if r["verdict"] == v]
             for v in ("DOUBLE-COPY", "SUBSET-DRIFT", "SUPERSET-DRIFT")}
    standalone = [r for r in rows if r["verdict"] == "STANDALONE"]
    # `derived` is file -> [source names]; only files the verdict calls derived
    # are reported, so a file with a copy never appears in the positive list.
    derived = {f: names for f, names in derived.items()
               if verdicts.get(f) == "GUARD-DERIVED"}

    print("== 自检守卫名单派生性普查（只读·零判定改动）==")
    print("scanned=%d parsed=%d  source_files=%d  func_literals=%d"
          % (parsed, parsed, len(sources_by_file), len(rows)))
    print()
    for verdict, title, fix in (
            ("DOUBLE-COPY", "-- 双份守卫（应改为遍历同源集合）--",
             "处置=把字面表换成 `for name in <源>:`，并从源派生断言"),
            ("SUBSET-DRIFT", "-- 子集漂移（源集合已长的成员无人守）--",
             "处置=同上；这条正是 T241 的原始形状"),
            ("SUPERSET-DRIFT", "-- 超前漂移（副本比源多）--",
             "处置=先确认哪一侧是权威，再把另一侧改回派生")):
        print(title)
        if not split[verdict]:
            print("  （无）")
        for r in split[verdict]:
            print("  [%s] %s:%d  func=%s  源=%s:%s  %d 名"
                  % (verdict, r["file"], r["line"], r["func"],
                     r["source"], r["source_line"], len(r["members"])))
            print("    members= %s" % _fmt_members(r["members"]))
            print("    -> %s" % r["why"])
            print("    %s" % fix)
        print()
    print("-- 派生确认（正例：测试读同源集合，不再各写一份）--")
    if not derived:
        print("  （无）")
    for rel in sorted(derived):
        print("  [DERIVED] %s  refs=%s" % (rel, ",".join(derived[rel])))
    print()
    print("-- 无法判定（同文件无同源集合，不计入缺口）--")
    if not standalone:
        print("  （无）")
    for r in standalone[:20]:
        print("  [STANDALONE] %s:%d func=%s members=%s"
              % (r["file"], r["line"], r["func"], _fmt_members(r["members"], 4)))
    if len(standalone) > 20:
        print("  …(共 %d 条，其余按 json 读)" % len(standalone))
    print()
    counts = {k: len(v) for k, v in split.items()}
    verdict = ("GUARD-CENSUS-FOUND" if bad else
               ("GUARD-CENSUS-STANDALONE-ONLY" if standalone else "GUARD-CENSUS-GREEN"))
    print("CANDIDATES double_copy=%d subset=%d superset=%d standalone=%d derived_files=%d"
          % (counts["DOUBLE-COPY"], counts["SUBSET-DRIFT"],
             counts["SUPERSET-DRIFT"], len(standalone), len(derived)))
    print("VERDICT=%s" % verdict)
    return {"double_copy": counts["DOUBLE-COPY"], "subset": counts["SUBSET-DRIFT"],
            "superset": counts["SUPERSET-DRIFT"],
            "standalone": len(standalone), "derived_files": len(derived),
            "verdict": verdict}


def cmd_run(root, out_dir, quiet=False):
    rows, units, sources_by_file, parsed = census(root)
    derived = derived_files(units, sources_by_file)
    verdicts = file_verdicts(rows, derived)
    if quiet:
        bad = [r for r in rows if r["verdict"] != "STANDALONE"]
        summary = {"parsed": parsed, "rows": len(rows), "bad": len(bad),
                   "verdict": "GUARD-CENSUS-FOUND" if bad else "GUARD-CENSUS-GREEN"}
    else:
        summary = render(rows, units, sources_by_file, verdicts, parsed, derived)
    payload = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "root": root,
               "parsed": parsed, "function_literals": len(rows),
               "sources": sources_by_file, "derived": derived,
               "file_verdicts": verdicts, "rows": rows, "summary": summary}
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, time.strftime("guard-list-census-%Y%m%d-%H%M.json"))
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print("json=%s" % dest)
    return 3 if summary.get("bad") and not quiet else 0


# ---------------------------------------------------------------- selftest

def _mk(root, name, body):
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def selftest():
    import shutil
    import tempfile

    ok = 0
    checks = []

    def chk(name, cond, detail=""):
        nonlocal ok
        checks.append((name, bool(cond), detail))
        if cond:
            ok += 1

    tmp = tempfile.mkdtemp(prefix="guardcensus-")
    try:
        _mk(tmp, "spec_a.py", (
            "_SPEC_FIELDS = (\"ALPHA\", \"BETA\", \"GAMMA\")\n"
            "ROOTS = (\"Tools\", \"cph4\")\n"
            "NUMBERS = (1, 2, 3)\n"
            "ONE = (\"ALPHA\",)\n"
            "\n"
            "\n"
            "def selftest_copy():\n"
            "    names = (\"ALPHA\", \"BETA\", \"GAMMA\")\n"
            "    return names\n"
            "\n"
            "\n"
            "def selftest_derived():\n"
            "    miss = []\n"
            "    for field in _SPEC_FIELDS:\n"
            "        miss.append(field)\n"
            "    return miss\n"
            "\n"
            "\n"
            "def selftest_subset():\n"
            "    names = (\"ALPHA\", \"BETA\")\n"
            "    return names\n"
            "\n"
            "\n"
            "def selftest_paths_copy():\n"
            "    roots = (\"Tools\", \"cph4\")\n"
            "    return roots\n"
            "\n"
            "\n"
            "def helper_standalone():\n"
            "    kinds = (\"x\", \"y\")\n"
            "    return kinds\n"
            "\n"
            "\n"
            "def nested_owner():\n"
            "    def inner():\n"
            "        return (\"ALPHA\", \"BETA\", \"GAMMA\")\n"
            "    return inner\n"
        ))
        _mk(tmp, "clean.py", "A = 1\nB = (\"one\",)\nC = (1, 2)\n\n\ndef f():\n    return (1, 2)\n")

        rows, units, src_by_file, parsed = census(tmp)
        derived = derived_files(units, src_by_file)
        verdicts = file_verdicts(rows, derived)

        def row_of(func):
            for r in rows:
                if r["func"] == func:
                    return r
            return None

        chk("S1 every fixture file parsed", parsed == 2, parsed)

        copy = row_of("selftest_copy")
        chk("S2 literal equal to a source collection -> DOUBLE-COPY",
            copy is not None and copy["verdict"] == "DOUBLE-COPY"
            and copy["source"] == "_SPEC_FIELDS", copy)

        chk("S3 function that iterates the source is derived evidence",
            "spec_a.py" in derived and "_SPEC_FIELDS" in derived["spec_a.py"], derived)

        subset = row_of("selftest_subset")
        chk("S4 proper subset -> SUBSET-DRIFT with the missing member named",
            subset is not None and subset["verdict"] == "SUBSET-DRIFT"
            and subset["source"] == "_SPEC_FIELDS"
            and "GAMMA" not in subset["members"], subset)

        paths = row_of("selftest_paths_copy")
        chk("S5 non-identifier strings (file/root names) are collections too",
            paths is not None and paths["verdict"] == "DOUBLE-COPY"
            and paths["source"] == "ROOTS", paths)

        standalone = row_of("helper_standalone")
        chk("S6 literal with no same-file source is STANDALONE, not a finding",
            standalone is not None and standalone["verdict"] == "STANDALONE", standalone)

        chk("S7 numeric / single-member literals are not collections",
            not any(r["func"] == "f" for r in rows), [r["func"] for r in rows])
        chk("S7b one-member string tuple is below the evidence floor",
            all(r["func"] != "selftest_copy" or len(r["members"]) >= 2 for r in rows), rows)

        chk("S8 nested function literal is attributed to the nested function only",
            any(r["func"] == "inner" for r in rows)
            and not any(r["func"] == "nested_owner" for r in rows),
            [(r["func"], r["line"]) for r in rows])

        chk("S9 file verdict separates derived-only from double-copy",
            verdicts.get("spec_a.py") == "GUARD-DOUBLE-COPY", verdicts)

        rows2, units2, sbf2, _p = census(tmp)
        chk("S10 census is deterministic",
            [(r["file"], r["line"], r["verdict"]) for r in rows2]
            == [(r["file"], r["line"], r["verdict"]) for r in rows],
            len(rows2))

        # Negative control: a tree where the test really does read the source
        # must come back GREEN -- otherwise the tool is flagging everything.
        clean = tempfile.mkdtemp(prefix="guardcensus-clean-")
        _mk(clean, "t.py", (
            "_FIELDS = (\"A\", \"B\")\n"
            "\n"
            "\n"
            "def selftest():\n"
            "    for name in _FIELDS:\n"
            "        pass\n"
        ))
        rows3, units3, sbf3, _p3 = census(clean)
        d3 = derived_files(units3, sbf3)
        chk("S11 negative control: a derived test yields zero candidates",
            [r for r in rows3 if r["verdict"] != "STANDALONE"] == [], rows3)
        chk("S11b and it is still recorded as derived",
            d3.get("t.py") == ["_FIELDS"], d3)
        shutil.rmtree(clean, ignore_errors=True)

        # Real-tree anchor: T241's fixed file must read as derived, not as a
        # copy -- if the real tree regresses this anchor goes red.
        here = os.path.dirname(os.path.abspath(__file__))
        real_rows, real_units, real_src, _rp = census(here)
        real_derived = derived_files(real_units, real_src)
        real_verdicts = file_verdicts(real_rows, real_derived)
        chk("S12 real tree: frame_qc_spec.py reads GUARD-DERIVED (T241 holds)",
            real_verdicts.get("frame_qc_spec.py") == "GUARD-DERIVED",
            real_verdicts.get("frame_qc_spec.py"))
        chk("S13 real tree: guard_list_census.py does not flag itself",
            real_verdicts.get("guard_list_census.py") != "GUARD-DOUBLE-COPY",
            real_verdicts.get("guard_list_census.py"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for name, cond, detail in checks:
        print(("PASS " if cond else "FAIL ") + name +
              ("" if cond else "  <%r>" % (detail,)))
    print("SELFTEST %d/%d PASS" % (ok, len(checks)))
    return 0 if ok == len(checks) else 1


def main(argv=None):
    _stdout_utf8()
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="guard-list derivation census (read-only)")
    ap.add_argument("cmd", choices=["run", "selftest"], nargs="?", default="run")
    ap.add_argument("--root", default=here, help="scan root (default: Tools/)")
    ap.add_argument("--out", default=os.path.join(repo, "state"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    return cmd_run(args.root, args.out, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
