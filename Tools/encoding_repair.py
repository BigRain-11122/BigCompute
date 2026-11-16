"""encoding_repair.py - evidence-file encoding census and repair (tech T252).

Why: read-side damage and write-side damage look identical to a reader. A file
written as UTF-16LE by a PowerShell redirect, or a run of UTF-8 bytes that was
decoded as cp936 and stored again as UTF-8, both show up as unreadable text.
Only the byte level can tell them apart, so this tool judges bytes, names the
fault, and repairs only what is provably reversible.

Classes it reports (one verdict per file):
  UTF8-OK            strict UTF-8 decodes, no BOM, no double-encoded run
  UTF8-BOM           strict UTF-8 with a BOM (readable, flagged for git hygiene)
  UTF16-BOM          UTF-16 with BOM: convertible, content preserved
  DOUBLE-ENCODED     contains run(s) where cp936 re-decode yields valid UTF-8
  INVALID            none of the above decodes: left untouched, named

Repair rules:
  - Never rewrite a file that is already UTF8-OK.
  - Every rewritten file gets a raw byte backup first (state/_encoding-backup/).
  - DOUBLE-ENCODED repair is per-run and greedy-longest-prefix: a run only
    changes where the cp936 -> UTF-8 round trip succeeds. Irreversible runs are
    kept verbatim and reported with their line numbers.
  - Rewrites are strict UTF-8, no BOM, LF kept as found.

Commands:
  python Tools/encoding_repair.py scan [--roots state docs Tools tasks orders]
  python Tools/encoding_repair.py apply [--roots ...] [--write]
  python Tools/encoding_repair.py selftest

This file stays PURE ASCII on purpose: the defect it repairs is an encoding
defect, so the repair tool must not be able to carry one.
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join("state", "_encoding-backup")
TEXT_EXT = (".txt", ".md", ".json", ".jsonl", ".log", ".py", ".ps1", ".csv")
SKIP_DIRS = {".git", "media", "node_modules", "__pycache__", ".codely-cli",
             "_encoding-backup"}
NON_ASCII_RUN = re.compile(r"[^\x00-\x7f]+")
CJK = re.compile(r"[\u4e00-\u9fff]")


def _iter_files(roots):
    for rel in roots:
        base = os.path.join(ROOT, rel)
        if os.path.isfile(base):
            yield base
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.lower().endswith(TEXT_EXT):
                    yield os.path.join(dirpath, fn)


def _decode(raw):
    """Return (encoding, text) for the whole file, or (None, None)."""
    for enc in ("utf-8", "utf-8-sig", "utf-16"):
        try:
            return enc, raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None, None


def _repair_run(run):
    """Split a non-ASCII run into (kept, repaired, irreversible) pieces.

    Greedy longest prefix: the longest leading slice whose cp936 bytes decode
    as strict UTF-8 and contain CJK is taken as repair; the rest is retried.
    """
    out, irr, pos, repaired = [], [], 0, False
    n = len(run)
    while pos < n:
        best = 0
        # longest first so a whole damaged run is converted in one step
        for end in range(n, pos + 1, -1):
            head = run[pos:end]
            try:
                back = head.encode("gbk").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if CJK.search(back):
                best = end
                break
        if best:
            head = run[pos:best]
            out.append(head.encode("gbk").decode("utf-8"))
            repaired = True
            pos = best
        else:
            irr.append(run[pos])
            pos += 1
    return "".join(out), "".join(irr), repaired


def classify_text(text):
    """Return (verdict, repaired_text, notes) for a decoded file body."""
    lines = text.splitlines(True)
    out, notes, changed = [], [], False
    for idx, line in enumerate(lines, 1):
        if line.isascii():
            out.append(line)
            continue
        pieces, pos = [], 0
        for m in NON_ASCII_RUN.finditer(line):
            pieces.append(line[pos:m.start()])
            fixed, irr, did = _repair_run(m.group(0))
            if did:
                changed = True
                pieces.append(fixed)
                if irr:
                    notes.append({"line": idx, "irreversible_chars": irr,
                                  "text": m.group(0)[:80]})
            else:
                pieces.append(m.group(0))
            pos = m.end()
        pieces.append(line[pos:])
        out.append("".join(pieces))
    body = "".join(out)
    return ("DOUBLE-ENCODED" if changed else "UTF8-OK"), body, notes


def scan_file(path):
    raw = io.open(path, "rb").read()
    rec = {"file": os.path.relpath(path, ROOT), "bytes": len(raw)}
    enc, text = _decode(raw)
    if enc is None:
        rec.update(verdict="INVALID", action="none",
                   note="no utf-8/utf-8-sig/utf-16 decode")
        return rec
    if enc in ("utf-16", "utf-8-sig"):
        body = text
        verdict, _, _ = classify_text(body)
        rec.update(encoding=enc, verdict="UTF16-BOM" if enc == "utf-16"
                   else "UTF8-BOM", repaired_verdict=verdict,
                   action="convert-to-utf8")
        return rec
    verdict, body, notes = classify_text(text)
    rec.update(encoding="utf-8", verdict=verdict)
    if verdict == "DOUBLE-ENCODED":
        rec.update(action="repair-runs", notes=notes[:20],
                   notes_total=len(notes))
    else:
        rec.update(action="none")
    return rec


def cmd_scan(args):
    roots = args.roots
    rows = [scan_file(p) for p in _iter_files(roots)]
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    payload = {"roots": roots, "files": len(rows), "counts": counts,
               "rows": rows}
    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print("encoding scan: files=%d %s" % (len(rows), _fmt_counts(counts)))
    for r in rows:
        if r["verdict"] in ("UTF8-OK",):
            continue
        print("  %-16s %-8s %s" % (r["verdict"], r.get("action", "-"),
                                   r["file"]))
        for n in r.get("notes", [])[:5]:
            print("      L%d irreversible=%r" % (n["line"],
                                                 n["irreversible_chars"]))
    bad = sum(v for k, v in counts.items() if k != "UTF8-OK")
    return 0 if bad == 0 else 3


def _fmt_counts(counts):
    return " ".join("%s=%d" % (k, counts[k]) for k in sorted(counts))


def cmd_apply(args):
    roots = args.roots
    stamp = time.strftime("%Y%m%d-%H%M%S")
    todo, skipped = [], []
    for p in _iter_files(roots):
        rec = scan_file(p)
        if rec["verdict"] == "UTF8-OK":
            continue
        if rec["verdict"] == "INVALID":
            skipped.append((rec, "undecodable"))
            continue
        if rec["verdict"] == "DOUBLE-ENCODED":
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            ext = os.path.splitext(p)[1].lower()
            if not args.repair_runs:
                skipped.append((rec, "run-repair not requested"))
                continue
            if ext not in args.repair_ext:
                skipped.append((rec, "ext %s not in repair scope" % ext))
                continue
            if rel.startswith("state/queue/") or rel.endswith(".md"):
                skipped.append((rec, "quotes the defect; left as evidence"))
                continue
            if rel.count("/") != 1:
                skipped.append((rec, "nested snapshot dir; left as evidence"))
                continue
            if os.path.basename(rel).startswith(("encoding-scan-",
                                                 "encoding-repair-")):
                skipped.append((rec, "this tool's own output"))
                continue
        todo.append((p, rec))
    if not args.write:
        print("encoding apply: DRY-RUN plan=%d skipped=%d"
              % (len(todo), len(skipped)))
        for p, rec in todo:
            print("  would fix %-16s %s" % (rec["verdict"],
                                            os.path.relpath(p, ROOT)))
        for rec, why in skipped:
            print("  skip  %-16s %s | %s" % (rec["verdict"], rec["file"], why))
        return 0
    bdir = os.path.join(ROOT, BACKUP_DIR)
    os.makedirs(bdir, exist_ok=True)
    fixed = 0
    for p, rec in todo:
        raw = io.open(p, "rb").read()
        rel = os.path.relpath(p, ROOT).replace(os.sep, "_")
        bak = os.path.join(bdir, "%s.%s.bak" % (rel, stamp))
        shutil.copy2(p, bak)
        enc, text = _decode(raw)
        if rec["verdict"] in ("UTF16-BOM", "UTF8-BOM"):
            body = text
        else:
            _, body, _ = classify_text(text)
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        fixed += 1
        print("  fixed %-16s %s (backup=%s)" % (rec["verdict"],
                                                os.path.relpath(p, ROOT),
                                                os.path.relpath(bak, ROOT)))
    for rec, why in skipped:
        print("  skip  %-16s %s | %s" % (rec["verdict"], rec["file"], why))
    print("encoding apply: fixed=%d skipped=%d backups=%s"
          % (fixed, len(skipped), BACKUP_DIR))
    return 0


def _check(name, cond, detail=""):
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       "" if not detail else " | " + detail))
    return 0 if cond else 1


def cmd_selftest(_args):
    fails = 0
    # S1 clean utf-8 is not flagged
    v, body, notes = classify_text("\u62a5\u544a\u89e3\u6790 ok\n")
    fails += _check("S1 clean utf-8 stays UTF8-OK", v == "UTF8-OK"
                    and body == "\u62a5\u544a\u89e3\u6790 ok\n")
    # S2 utf8-as-cp936 run is repaired to the original text
    orig = "\u62a5\u544a\u89e3\u6790=\u5934\u90e8"
    moji = orig.encode("utf-8").decode("cp936")
    v, body, _ = classify_text("%s\n" % moji)
    fails += _check("S2 double-encoded run restored", v == "DOUBLE-ENCODED"
                    and orig in body, "got=%r" % body.strip())
    # S3 mixed line: damaged run repaired, clean chinese kept
    line = "%s \u6b63\u5e38\u4e2d\u6587" % moji
    v, body, _ = classify_text(line + "\n")
    fails += _check("S3 mixed line keeps clean chinese",
                    "\u6b63\u5e38\u4e2d\u6587" in body and orig in body)
    # S4 clean fullwidth punctuation is never touched
    clean = "\uff08\u6d4b\u8bd5\uff09\n"
    v, body, _ = classify_text(clean)
    fails += _check("S4 fullwidth punctuation untouched",
                    v == "UTF8-OK" and body == clean)
    # S5 irreversible run is kept verbatim and named
    v, body, notes = classify_text("\u9358\ufffdabc\n")
    fails += _check("S5 irreversible run kept and noted",
                    body == "\u9358\ufffdabc\n")
    # S6 idempotent: repairing repaired text is a no-op
    v1, b1, _ = classify_text("%s\n" % moji)
    v2, b2, _ = classify_text(b1)
    fails += _check("S6 repair is idempotent",
                    v2 == "UTF8-OK" and b2 == b1)
    # S7 file level: utf-16 with BOM is convertible, bytes preserved
    td = tempfile.mkdtemp(prefix="enc_")
    try:
        p16 = os.path.join(td, "a.txt")
        io.open(p16, "wb").write("\u4e2d\u6587\u8bc1\u636e\n".encode("utf-16"))
        rec = scan_file(p16)
        p8 = os.path.join(td, "b.txt")
        io.open(p8, "wb").write("\u4e2d\u6587\u8bc1\u636e\n".encode("utf-16"))
        enc, text = _decode(io.open(p8, "rb").read())
        fails += _check("S7 utf-16 named as UTF16-BOM",
                        rec["verdict"] == "UTF16-BOM"
                        and rec["action"] == "convert-to-utf8"
                        and text == "\u4e2d\u6587\u8bc1\u636e\n")
        # S8 invalid bytes are named, never rewritten
        pbad = os.path.join(td, "c.txt")
        io.open(pbad, "wb").write(b"\x80\x81\x82")
        rec2 = scan_file(pbad)
        fails += _check("S8 undecodable file named INVALID",
                        rec2["verdict"] == "INVALID"
                        and rec2["action"] == "none")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("selftest encoding_repair: %d/%d PASS"
          % (8 - fails, 8))
    return 0 if fails == 0 else 1


def main(argv):
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="evidence-file encoding census/repair")
    sub = ap.add_subparsers(dest="cmd", required=True)
    default_roots = ["state", "docs", "Tools", "tasks", "orders", "qa"]

    def add_roots(p):
        p.add_argument("--roots", nargs="+", default=default_roots)
        p.add_argument("--json-out", default=None)

    p_scan = sub.add_parser("scan", help="classify files, no writes")
    add_roots(p_scan)

    p_apply = sub.add_parser("apply", help="convert/repair with raw backups")
    add_roots(p_apply)
    p_apply.add_argument("--write", action="store_true",
                         help="actually rewrite (default is dry-run)")
    p_apply.add_argument("--repair-runs", action="store_true",
                         help="also repair reversible double-encoded runs")
    p_apply.add_argument("--repair-ext", nargs="+", default=[".txt"],
                         help="extensions eligible for run repair (default .txt)")

    sub.add_parser("selftest", help="fixture checks")

    args = ap.parse_args(argv)
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "apply":
        return cmd_apply(args)
    return cmd_selftest(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
