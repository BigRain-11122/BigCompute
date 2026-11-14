"""Threshold census — find judgment constants that are copy-pasted across files.

Why (T239 / derivation of T238):
T238 collapsed the four magenta band literals into ``frame_qc_spec.py`` after
the writer and its "independent" witness turned out to share them *by copy*:
one wrong edit to two literals makes both verifications agree, and the second
implementation then proves only arithmetic, never the threshold.  That is one
instance of a general failure mode, so this tool surveys the whole tree and
reports, per candidate, a verdict instead of a number:

  SHOULD-SINGLE-SOURCE   same name, same value, defined in >=2 files
  NAME-COLLISION-KEEP    same name, different values (different quantities)
  KEEP-GENERIC           generic name where an equal value is coincidence
  SINGLE-SOURCE-OK       one definition; only counted, not listed

It never edits a threshold (T239 judgement: "禁改任何既有阈值").  Verdicts are
input to a later session, not an automatic rewrite.

Honest boundary: this is AST/textual evidence.  Two constants can share a name
and a value and still mean different things (degrees vs ratio); the tool shows
what it can see and leaves the semantic call to the reader.  Conversely a
threshold written as a bare comparison literal (``if x < 2.0``) is invisible
here — only module-level UPPER_CASE assignments are surveyed.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import sys
import time

# Names too generic for "same name+value in two files" to mean anything.
GENERIC_NAMES = {
    "TIMEOUT", "DEBUG", "PORT", "RETRIES", "SIZE", "COUNT", "WIDTH",
    "HEIGHT", "STEP", "MAX", "MIN", "LIMIT", "TOTAL", "ROWS", "COLS",
}

FRAME_QC_NAMES = ("MAGENTA_MIN_R", "MAGENTA_MIN_B", "MAGENTA_MAX_G",
                  "BG_TOL", "CLEAR_RGB_DEFAULT")


def _stdout_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _module_imports(tree):
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def collect(root):
    """Return (defs, imports, scanned) for every .py under ``root``."""
    files = sorted(glob.glob(os.path.join(root, "**", "*.py"), recursive=True))
    defs = {}
    imports = {}
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        imports[rel] = _module_imports(tree)
        lines = src.splitlines()
        for node in tree.body:          # module scope only: locals are not shared
            targets, value = [], None
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets, value = [node.target.id], node.value
            if (not targets or not isinstance(value, ast.Constant)
                    or isinstance(value.value, bool)
                    or not isinstance(value.value, (int, float))):
                continue
            line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            note = line.split("#", 1)[1].strip() if "#" in line else ""
            for name in targets:
                if not name.isupper() or len(name) < 3:
                    continue
                defs.setdefault(name, []).append(
                    {"file": rel, "line": node.lineno,
                     "value": value.value, "note": note})
    return defs, imports, len(files)


def classify(defs, imports):
    """Group cross-file constant names and attach a verdict to each."""
    rows, singles = [], 0
    for name in sorted(defs):
        items = sorted(defs[name], key=lambda d: (d["file"], d["line"]))
        files = sorted({i["file"] for i in items})
        if len(files) < 2:
            singles += 1
            continue
        values = sorted({round(float(i["value"]), 9) for i in items})
        if name in GENERIC_NAMES:
            verdict = "KEEP-GENERIC"
            why = "通用名，同值属巧合；不构成单源需求"
        elif len(values) == 1:
            verdict = "SHOULD-SINGLE-SOURCE"
            why = "同值同语义面跨 %d 文件各自定义：任一处改错，两侧一起错" % len(files)
        else:
            verdict = "NAME-COLLISION-KEEP"
            why = "同名不同值=%d 个量，就地保留；建议按语义改名以免误搜" % len(values)
        # A file that imports a module which also defines the name is a
        # consumer, not a second source — note it so the reader can tell.
        # Note it must scan *all* files, not just the defining ones: the
        # consumer by definition does not define the name.
        for item in items:
            stem = os.path.splitext(os.path.basename(item["file"]))[0]
            item["imported_by_sibling"] = sorted(
                other for other in imports
                if other != item["file"] and stem in imports[other])
        rows.append({"name": name, "verdict": verdict, "why": why,
                     "values": values, "files": files, "sites": items})
    return rows, singles


def frame_qc_regression(root):
    """T238's regression face: no consumer may re-declare the spec constants.

    A literal copy (`X = 200`) is a second source and judges red; an alias
    (`X = qc_spec.X`) is the sanctioned re-export shape and only gets listed,
    because the value still comes from the one owner.  Collapsing the two
    would punish the very pattern T238 asked for.
    """
    spec = os.path.join(root, "frame_qc_spec.py")
    if not os.path.exists(spec):
        return {"status": "MISSING-SPEC", "sites": [], "reexports": []}
    dupes, reexports = [], []
    for path in sorted(glob.glob(os.path.join(root, "**", "*.py"), recursive=True)):
        if os.path.abspath(path) == os.path.abspath(spec):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError):
            continue
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        for node in tree.body:
            targets, value = [], None
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets, value = [node.target.id], node.value
            for name in targets:
                if name not in FRAME_QC_NAMES:
                    continue
                where = "%s:%d %s" % (rel, node.lineno, name)
                if isinstance(value, ast.Constant):
                    dupes.append(where)
                else:
                    reexports.append(where)
    return {"status": "CLEAN" if not dupes else "DUPLICATED",
            "sites": dupes, "reexports": reexports}


def render(rows, singles, scanned, regression):
    should = [r for r in rows if r["verdict"] == "SHOULD-SINGLE-SOURCE"]
    keep = [r for r in rows if r["verdict"] == "NAME-COLLISION-KEEP"]
    generic = [r for r in rows if r["verdict"] == "KEEP-GENERIC"]
    print("== 判据常量跨文件普查（只读·不改任何阈值）==")
    print("scanned=%d files  const_names=%d  cross_file=%d  singles=%d"
          % (scanned, singles + len(rows), len(rows), singles))
    print()
    print("-- 应单源（同值同语义跨文件）--")
    if not should:
        print("  （无）")
    for r in should:
        print("  [SHOULD-SINGLE-SOURCE] %s = %s" % (r["name"], r["values"][0]))
        for s in r["sites"]:
            note = ("  # " + s["note"][:70]) if s["note"] else ""
            print("    %s:%d%s" % (s["file"], s["line"], note))
        print("    -> %s" % r["why"])
    print()
    print("-- 同名不同义（就地保留，建议改名）--")
    if not keep:
        print("  （无）")
    for r in keep:
        print("  [NAME-COLLISION-KEEP] %s 值=%s" % (r["name"], r["values"]))
        for s in r["sites"]:
            note = ("  # " + s["note"][:60]) if s["note"] else ""
            print("    %s:%d = %s%s" % (s["file"], s["line"], s["value"], note))
        print("    -> %s" % r["why"])
    print()
    print("-- 通用名同值（不构成单源需求）--")
    print("  " + (", ".join("%s=%s" % (r["name"], r["values"][0]) for r in generic)
                  if generic else "（无）"))
    print()
    print("-- frame_qc_spec 回归面（T238 承诺：消费者零重复定义）--")
    print("  status=%s%s" % (regression["status"],
                            "" if not regression["sites"]
                            else "  " + " ".join(regression["sites"])))
    if regression.get("reexports"):
        print("  reexport(合法单源形态)=%s" % " ".join(regression["reexports"]))
    print()
    print("CANDIDATES should_single=%d name_collision=%d generic=%d singles=%d"
          % (len(should), len(keep), len(generic), singles))
    return {"should_single": [r["name"] for r in should],
            "name_collision": [r["name"] for r in keep],
            "generic": [r["name"] for r in generic], "singles": singles}


def cmd_run(root, out_dir, quiet=False):
    defs, imports, scanned = collect(root)
    rows, singles = classify(defs, imports)
    regression = frame_qc_regression(root)
    if quiet:
        summary = {"scanned": scanned, "rows": len(rows), "singles": singles,
                   "regression": regression["status"]}
    else:
        summary = render(rows, singles, scanned, regression)
    payload = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "root": root,
               "scanned": scanned, "cross_file_names": len(rows),
               "singles": singles, "regression": regression,
               "rows": rows, "summary": summary}
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, time.strftime("threshold-census-%Y%m%d-%H%M.json"))
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print("json=%s" % dest)
    # 不是闸：只有 T238 回归面被破坏才判红。
    return 3 if regression["status"] == "DUPLICATED" else 0


# ---------------------------------------------------------------- selftest

def _mk(root, name, body):
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def selftest():
    import tempfile
    ok = 0
    checks = []

    def chk(name, cond, detail=""):
        nonlocal ok
        checks.append((name, bool(cond), detail))
        if cond:
            ok += 1

    tmp = tempfile.mkdtemp(prefix="thcensus-")
    try:
        _mk(tmp, "spec_a.py", "HEADROOM_MIB = 1536  # 1.5GiB\nMAX_AGE_MIN = 30.0\n"
                              "MAGENTA_MIN_R = 200\n")
        _mk(tmp, "spec_b.py", "HEADROOM_MIB = 1536  # same value as spec_a\n"
                              "MAX_AGE_MIN = 2880.0\nTIMEOUT = 30\n")
        _mk(tmp, "spec_c.py", "TIMEOUT = 30\nheadroom_mib = 1536\n")
        _mk(tmp, "consumer.py", "from spec_a import HEADROOM_MIB\nused = HEADROOM_MIB\n")

        defs, imports, scanned = collect(tmp)
        rows, singles = classify(defs, imports)
        by = {r["name"]: r for r in rows}

        chk("S1 scanned every fixture file", scanned == 4, scanned)
        chk("S2 same name+value in two files -> SHOULD-SINGLE-SOURCE",
            by.get("HEADROOM_MIB", {}).get("verdict") == "SHOULD-SINGLE-SOURCE",
            by.get("HEADROOM_MIB"))
        chk("S3 same name, different values -> NAME-COLLISION-KEEP",
            by.get("MAX_AGE_MIN", {}).get("verdict") == "NAME-COLLISION-KEEP",
            by.get("MAX_AGE_MIN"))
        chk("S4 generic name is not a single-source demand",
            by.get("TIMEOUT", {}).get("verdict") == "KEEP-GENERIC",
            by.get("TIMEOUT"))
        chk("S5 lowercase locals are not constants",
            "headroom_mib" not in defs, sorted(defs))
        chk("S6 single-file constant is counted, not listed",
            "MAGENTA_MIN_R" not in by and singles >= 1, (list(by), singles))
        chk("S7 definition site records the importer as its consumer",
            any("consumer.py" in (s["imported_by_sibling"] or [])
                for s in by["HEADROOM_MIB"]["sites"]
                if s["file"] == "spec_a.py"),
            by["HEADROOM_MIB"]["sites"])

        # S8-negative-control: the survey catches nothing that is not there.
        clean = tempfile.mkdtemp(prefix="thcensus-clean-")
        _mk(clean, "only.py", "A = 1\nB = 2.5\n")
        rows2, _ = classify(*collect(clean)[:2])
        chk("S8 negative control: a single-file tree yields no candidates",
            rows2 == [], rows2)
        # S9: the frame_qc regression face flags a re-declared literal but
        # accepts the alias/re-export shape (that is the sanctioned one).
        _mk(clean, "frame_qc_spec.py", "MAGENTA_MIN_R = 200\n")
        _mk(clean, "thief.py", "MAGENTA_MIN_R = 200\n")
        reg = frame_qc_regression(clean)
        chk("S9 literal copy of a spec constant is DUPLICATED",
            reg["status"] == "DUPLICATED" and reg["sites"], reg)
        os.remove(os.path.join(clean, "thief.py"))
        _mk(clean, "thief.py", "import frame_qc_spec as spec\n"
                               "MAGENTA_MIN_R = spec.MAGENTA_MIN_R\n")
        reg2 = frame_qc_regression(clean)
        chk("S9b alias re-export stays CLEAN and is listed as reexport",
            reg2["status"] == "CLEAN" and reg2["reexports"], reg2)
        os.remove(os.path.join(clean, "thief.py"))
        chk("S9c removing the copy clears the flag",
            frame_qc_regression(clean)["status"] == "CLEAN")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(clean, ignore_errors=True)

    for name, cond, detail in checks:
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  <%r>" % (detail,)))
    print("SELFTEST %d/%d PASS" % (ok, len(checks)))
    return 0 if ok == len(checks) else 1


def main(argv=None):
    _stdout_utf8()
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="judgment-constant cross-file census")
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
