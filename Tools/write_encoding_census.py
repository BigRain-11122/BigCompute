#!/usr/bin/env python3
"""write_encoding_census.py - write-side encoding declaration census (T188).

Why this exists
---------------
Damage has always been caught *after* it lands: text_integrity_census (T-50)
and qa probe no.19 read the ledgers back, and round_append's J8/J10 gates
cover exactly one writer (the python append path).  Every other writer -
helper scripts, state drills, ps1 launchers - is trusted by habit, and the
habit is exactly what broke the authoritative rows on 2026-11-01.

This tool walks the write faces statically and asks one question per writer:
does it say which encoding it writes in?

  .py   open()/io.open()/codecs.open()/Path.write_text()/Path.open() in text
        write mode ("w"/"a"/"x"/"r+") must pass encoding=.
        Binary modes ("wb") are skipped - no text encoding applies.
  .ps1  Set-Content/Add-Content/Out-File/Export-Csv/Export-Clixml must pass
        -Encoding (PowerShell 5.1 defaults to ANSI/GBK, 7 to UTF-8, so a
        missing flag means "whatever the host is").

A writer whose write mode is not a literal is reported UNJUDGED rather than
counted clean: not knowing is not the same as knowing.

Verdicts: WRITE-ENCODING-CLEAN / WRITE-ENCODING-RISK / WRITE-ENCODING-UNVERIFIED
Exit:     0 clean, 1 risk, 2 unverified (tree unreadable / nothing scanned)

Read-only: opens nothing for writing, no network.
"""
import argparse
import ast
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF_NAME = "write_encoding_census.py"

SKIP_DIRS = {".git", ".codely-cli", "__pycache__", "node_modules",
             "_trash", "auto-saves", "tool-outputs", "media"}
PY_EXT = ".py"
PS1_EXT = ".ps1"

WRITE_MODE_CHARS = set("wax+")
PS_WRITERS = ("Set-Content", "Add-Content", "Out-File", "Export-Csv",
              "Export-Clixml")
PS_WRITER_RE = re.compile(r"\b(?:%s)\b" % "|".join(PS_WRITERS),
                          re.IGNORECASE)
PS_ENCODING_RE = re.compile(r"-Encoding\b", re.IGNORECASE)


# ---------------------------------------------------------------- python side
def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return "%s.%s" % (base, node.attr) if base else node.attr
    return ""


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kw(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _mode_of(call, fname):
    """Return (mode_literal_or_None, mode_is_known_binary)."""
    if fname.endswith("write_text"):
        return "w", False
    if fname.endswith("write_bytes"):
        return "wb", True
    mode_node = _kw(call, "mode")
    if mode_node is None and len(call.args) >= 2:
        mode_node = call.args[1]
    if mode_node is None:
        return "r", False  # open(path) defaults to read
    val = _const_str(mode_node)
    return val, False


def py_hits(path, source):
    """Return list of hit dicts for one python file."""
    hits = []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return [{"file": path, "line": getattr(e, "lineno", 0) or 0,
                 "kind": "UNREADABLE", "detail": "syntax: %s" % e.msg}]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = _dotted(node.func)
        last = fname.split(".")[-1] if fname else ""
        if last not in ("open", "write_text", "write_bytes"):
            continue
        if last == "write_text" and not fname.startswith(("Path", "pathlib",
                                                         "os")) and \
                "." not in fname:
            continue
        mode, known_binary = _mode_of(node, fname)
        line = getattr(node, "lineno", 0)
        if known_binary or (mode and "b" in mode):
            continue
        if mode is None:
            hits.append({"file": path, "line": line, "kind": "UNJUDGED",
                         "detail": "%s(mode=<non-literal>)" % last})
            continue
        if not (set(mode) & WRITE_MODE_CHARS):
            continue
        if last == "write_text":
            if _kw(node, "encoding") is None:
                hits.append({"file": path, "line": line, "kind": "NO-ENCODING",
                             "detail": "Path.write_text() without encoding="})
            continue
        if _kw(node, "encoding") is None:
            hits.append({"file": path, "line": line, "kind": "NO-ENCODING",
                         "detail": "open(mode=%r) without encoding=" % mode})
    return hits


# ------------------------------------------------------------ powershell side
def ps_hits(path, source):
    """Return list of hit dicts for one ps1 file (logical lines)."""
    hits = []
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        start = i
        logical = lines[i]
        while logical.rstrip().endswith("`") and i + 1 < len(lines):
            i += 1
            logical = logical.rstrip()[:-1] + " " + lines[i]
        i += 1
        if logical.lstrip().startswith("#"):
            continue
        if not PS_WRITER_RE.search(logical):
            continue
        if PS_ENCODING_RE.search(logical):
            continue
        hits.append({"file": path, "line": start + 1, "kind": "NO-ENCODING",
                     "detail": "%s without -Encoding"
                               % PS_WRITER_RE.search(logical).group(0)})
    return hits


# ------------------------------------------------------------------- census
def iter_sources(root):
    """Yield (abs_path, rel_path, ext) for scannable files."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1].lower()
            if ext not in (PY_EXT, PS1_EXT):
                continue
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, root)
            yield abs_path, rel, ext


def census(root=None, json_out=None):
    root = root or ROOT
    scanned, skipped_bin, unreadable, hits, self_excluded = 0, 0, [], [], []
    for abs_path, rel, ext in iter_sources(root):
        if os.path.basename(rel) == SELF_NAME:
            self_excluded.append(rel)
            continue
        try:
            with open(abs_path, "r", encoding="utf-8-sig") as f:
                source = f.read()
        except (OSError, UnicodeDecodeError) as e:
            unreadable.append("%s (%s)" % (rel, type(e).__name__))
            continue
        scanned += 1
        file_hits = py_hits(rel, source) if ext == PY_EXT else ps_hits(rel,
                                                                      source)
        for h in file_hits:
            if h["kind"] == "UNREADABLE":
                unreadable.append("%s:%d (%s)" % (h["file"], h["line"],
                                                  h["detail"]))
            else:
                hits.append(h)
    risk = [h for h in hits if h["kind"] == "NO-ENCODING"]
    unjudged = [h for h in hits if h["kind"] == "UNJUDGED"]
    if scanned == 0 or unreadable:
        verdict = "WRITE-ENCODING-UNVERIFIED"
    elif risk:
        verdict = "WRITE-ENCODING-RISK"
    else:
        verdict = "WRITE-ENCODING-CLEAN"
    payload = {
        "verdict": verdict,
        "scanned": scanned,
        "self_excluded": len(self_excluded),
        "unreadable": unreadable,
        "risk": risk,
        "unjudged": unjudged,
        "summary": ("verdict=%s scanned=%d risk=%d unjudged=%d "
                    "unreadable=%d self_excluded=%d"
                    % (verdict, scanned, len(risk), len(unjudged),
                       len(unreadable), len(self_excluded))),
    }
    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def _emit_scope(payload):
    try:
        import scope_proof
    except Exception:  # noqa: BLE001 - scope proof is advisory, not verdict
        return
    scope_proof.emit(
        "tools_py_ps1 scanned=%d self_excluded=%d"
        % (payload["scanned"], payload["self_excluded"]),
        not_scanned=["binary/dir-skipped (%s)" % ",".join(sorted(SKIP_DIRS))],
        unreadable=payload["unreadable"],
    )
    print(scope_proof.coverage_line(payload["scanned"], 0,
                                    len(payload["unreadable"])))


def cmd_run(args):
    payload = census(json_out=args.json_out)
    for h in payload["risk"]:
        print("RISK   %s:%d  %s" % (h["file"], h["line"], h["detail"]))
    for h in payload["unjudged"]:
        print("UNJUDG %s:%d  %s" % (h["file"], h["line"], h["detail"]))
    for u in payload["unreadable"]:
        print("UNREAD %s" % u)
    print(payload["summary"])
    _emit_scope(payload)
    if payload["verdict"] == "WRITE-ENCODING-RISK":
        return 1
    return 0 if payload["verdict"] == "WRITE-ENCODING-CLEAN" else 2


# ------------------------------------------------------------------ selftest
def cmd_selftest():
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print("[%s] %s" % ("PASS" if cond else "FAIL", name))

    with tempfile.TemporaryDirectory() as tmp:
        def reset():
            for dirpath, dirnames, filenames in os.walk(tmp):
                for name in filenames:
                    os.unlink(os.path.join(dirpath, name))

        def write(rel, text):
            reset()
            p = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return p

        # S1 python: explicit encoding -> clean
        write("a.py", "open('x','w',encoding='utf-8').write('1')\n")
        rep = census(tmp)
        check("S1 py with encoding= is CLEAN (%s)" % rep["verdict"],
              rep["verdict"] == "WRITE-ENCODING-CLEAN"
              and rep["scanned"] == 1)

        # S2 python: missing encoding -> risk with file:line
        write("b.py", "open('x','w')\n")
        rep = census(tmp)
        check("S2 py write without encoding= is RISK",
              rep["verdict"] == "WRITE-ENCODING-RISK"
              and rep["risk"][0]["file"].endswith("b.py")
              and rep["risk"][0]["line"] == 1)

        # S3 python: binary mode is not a text-encoding question
        write("b.py", "open('x','wb')\n")
        rep = census(tmp)
        check("S3 binary mode skipped", rep["verdict"] == "WRITE-ENCODING-CLEAN")

        # S4 read mode is untouched
        write("b.py", "open('x','r')\n")
        rep = census(tmp)
        check("S4 read mode ignored", rep["verdict"] == "WRITE-ENCODING-CLEAN")

        # S5 ps1: missing -Encoding -> risk
        write("c.ps1", "Set-Content -Path x.txt -Value hi\n")
        rep = census(tmp)
        check("S5 ps1 writer without -Encoding is RISK",
              rep["verdict"] == "WRITE-ENCODING-RISK"
              and rep["risk"][0]["file"].endswith("c.ps1"))

        # S6 ps1: -Encoding present -> clean
        write("c.ps1", "Add-Content -Path x.txt -Value hi -Encoding utf8\n")
        rep = census(tmp)
        check("S6 ps1 writer with -Encoding is CLEAN",
              rep["verdict"] == "WRITE-ENCODING-CLEAN")

        # S7 ps1: backtick continuation still sees the flag on the next line
        write("c.ps1", "Out-File -FilePath x.txt `\n  -Encoding utf8\n")
        rep = census(tmp)
        check("S7 continued line with -Encoding is CLEAN",
              rep["verdict"] == "WRITE-ENCODING-CLEAN")

        # S8 non-literal mode -> UNJUDGED, never silently clean
        write("d.py", "m='w'\nopen('x', m)\n")
        rep = census(tmp)
        check("S8 non-literal mode is UNJUDGED (not clean)",
              len(rep["unjudged"]) == 1
              and rep["verdict"] == "WRITE-ENCODING-CLEAN")

        # S9 unreadable file -> UNVERIFIED, not clean
        p = write("bad.py", "x = 1\n")
        with open(p, "wb") as f:
            f.write(b"\xff\xfe\x00broken")
        rep = census(tmp)
        check("S9 unreadable file -> UNVERIFIED",
              rep["verdict"] == "WRITE-ENCODING-UNVERIFIED"
              and len(rep["unreadable"]) == 1)

        # S10 self-exclusion: the scanner must not judge its own text
        write("a.py", "open('x','w',encoding='utf-8').write('1')\n")
        write(SELF_NAME, "open('x','w')\n")
        with open(os.path.join(tmp, "a.py"), "w", encoding="utf-8") as f:
            f.write("open('x','w',encoding='utf-8').write('1')\n")
        rep = census(tmp)
        check("S10 scanner excludes itself by name",
              rep["verdict"] == "WRITE-ENCODING-CLEAN"
              and rep["self_excluded"] == 1 and rep["scanned"] == 1)

        # S11 determinism: two runs agree
        write("b.py", "open('y','a')\n")
        a = census(tmp)
        b = census(tmp)
        check("S11 deterministic across runs",
              a["summary"] == b["summary"] and a["risk"] == b["risk"])

    print("write_encoding_census selftest: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="census the working tree")
    run.add_argument("--json-out", default=None)
    sub.add_parser("selftest", help="offline fixtures")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return cmd_selftest()
    if args.cmd in (None, "run"):
        if args.cmd is None:
            args.json_out = None
        return cmd_run(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())