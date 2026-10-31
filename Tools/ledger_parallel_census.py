#!/usr/bin/env python3
"""ledger_parallel_census.py - full-text census of fused (parallel) ledger rows (T177).

qa_smoke probe no.8 only sniffs the LAST 12 lines of each ledger, so a fused
row that lands in the middle of a long ledger stays invisible forever. T176
measured three such rows inside state/heartbeat.txt by hand; nothing guards
the rest of the file or the other six ledgers.

This census scans every line of every ledger registered in
ledger_markers.TAIL_PROBE_FILES (single source: row-head marker per file) and
reports the lines whose marker count is >= 2.

Rows that are historical and already confirmed by hand are registered in a
whitelist keyed by a content fingerprint (sha1 over whitespace-collapsed
line text), NOT by line number: the same line keeps its identity when rows
above it are appended. Anything not on the whitelist is a new fused row and
turns the census red (exit 2), so the next occurrence cannot hide in the
middle of the file.

Commands
  run [--json] [--json-out PATH] [--whitelist PATH] [--files F ...]
        [--preview-chars N] [--adopt-existing --reason TEXT]
  selftest

Exit codes: 0 all rows whitelisted or no fused row at all; 2 new fused rows
present; 3 engine/IO failure (fail-closed, never a silent pass).

Whitelist adoption is explicit (`--adopt-existing`) and prints what it took
in, so the baseline can never grow by accident.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledger_markers import HB_MARKER, TAIL_PROBE_FILES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WHITELIST = os.path.join(ROOT, "state", "ledger-parallel-whitelist.json")
WS_RE = re.compile(r"\s+")


def fingerprint(line):
    """Content identity of a row, independent of its line number."""
    norm = WS_RE.sub(" ", line.strip())
    return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()[:16]


def preview(line, limit=90):
    text = WS_RE.sub(" ", line.strip())
    return text[:limit]


def compile_table(files=None):
    wanted = set(files) if files else None
    table = []
    for rel, pattern in TAIL_PROBE_FILES:
        if wanted is not None and rel not in wanted:
            continue
        table.append((rel, re.compile(pattern)))
    return table


def load_whitelist(path):
    if not os.path.exists(path):
        return {}
    with io.open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = {}
    for entry in doc.get("entries", []):
        rows[(entry["file"], entry["sha"])] = entry
    return rows


def save_whitelist(path, entries):
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "historical fused rows confirmed by hand (T177); keyed by content "
                "fingerprint so appends above them do not invalidate the entry",
        "entries": sorted(entries, key=lambda e: (e["file"], e["sha"])),
    }
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def scan(table, whitelist, preview_chars=90, root=None):
    root = root or ROOT
    hits, errors = [], []
    scanned = 0
    for rel, rx in table:
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            errors.append({"file": rel, "error": "MISSING"})
            continue
        try:
            with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    scanned += 1
                    if not line.strip():
                        continue
                    markers = len(rx.findall(line))
                    if markers < 2:
                        continue
                    sha = fingerprint(line)
                    entry = whitelist.get((rel, sha))
                    hits.append({
                        "file": rel,
                        "line": lineno,
                        "markers": markers,
                        "sha": sha,
                        "preview": preview(line, preview_chars),
                        "whitelisted": entry is not None,
                        "reason": entry.get("reason") if entry else None,
                    })
        except OSError as exc:  # unreadable file must not read as clean
            errors.append({"file": rel, "error": "IO:%s" % exc})
    new_hits = [h for h in hits if not h["whitelisted"]]
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files_scanned": len(table) - len(errors),
        "lines_scanned": scanned,
        "hits": hits,
        "counts": {
            "parallel": len(hits),
            "whitelisted": len(hits) - len(new_hits),
            "new": len(new_hits),
        },
        "errors": errors,
    }


def verdict_of(report):
    if report["errors"]:
        return "PARALLEL-ENGINE-FAIL"
    if report["counts"]["new"]:
        return "PARALLEL-NEW"
    return "PARALLEL-CLEAN"


def fmt_report(report, verdict):
    counts = report["counts"]
    lines = [
        "ledger-parallel-census %s | files=%d lines=%d"
        % (verdict, report["files_scanned"], report["lines_scanned"]),
        "parallel=%d whitelisted=%d new=%d"
        % (counts["parallel"], counts["whitelisted"], counts["new"]),
    ]
    for hit in report["hits"]:
        flag = "WHITELIST" if hit["whitelisted"] else "NEW"
        lines.append("  [%s] %s:%d markers=%d %s"
                     % (flag, hit["file"], hit["line"], hit["markers"], hit["preview"]))
    for err in report["errors"]:
        lines.append("  [ERROR] %s %s" % (err["file"], err["error"]))
    return "\n".join(lines)


def adopt_existing(report, whitelist_path, reason):
    entries = list(load_whitelist(whitelist_path).values())
    known = {(e["file"], e["sha"]) for e in entries}
    added = 0
    for hit in report["hits"]:
        if (hit["file"], hit["sha"]) in known:
            continue
        entries.append({
            "file": hit["file"],
            "sha": hit["sha"],
            "line_at_adoption": hit["line"],
            "markers": hit["markers"],
            "preview": hit["preview"],
            "reason": reason,
            "adopted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        known.add((hit["file"], hit["sha"]))
        added += 1
    save_whitelist(whitelist_path, entries)
    return added


def run(args):
    table = compile_table(args.files)
    if not table:
        sys.stderr.write("no ledger selected\n")
        return 3
    whitelist = load_whitelist(args.whitelist)
    report = scan(table, whitelist, args.preview_chars)
    if args.adopt_existing:
        if report["errors"]:
            sys.stderr.write("refusing to adopt while files are unreadable\n")
            return 3
        added = adopt_existing(report, args.whitelist, args.reason)
        report["adopted"] = added
        whitelist = load_whitelist(args.whitelist)
        report = scan(table, whitelist, args.preview_chars)
        report["adopted"] = added
    verdict = verdict_of(report)
    report["verdict"] = verdict
    text = fmt_report(report, verdict)
    print(text)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("json-out=%s" % args.json_out)
    if verdict == "PARALLEL-ENGINE-FAIL":
        return 3
    return 2 if verdict == "PARALLEL-NEW" else 0


# ---------------------------------------------------------------- selftest
def _w(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def selftest():
    cases = []

    def check(name, ok, detail=""):
        cases.append((name, bool(ok), detail))

    fused = "09:00 \u8f6e\u5c3e\u5fc3\u8df3 ok 09:05 os-loop: round done exit=0\n"
    single = "09:10 \u8f6e\u5c3e\u65f6\u95f4\u6233 2026-11-01 09:10:11\n"
    cite_only = "\u8865\u8bb0 past orders 13:42:04 \u5df2\u6838\n"
    machine = "2026-11-01 09:12:03 os-loop: round done exit=0\n"

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(os.path.join(root, "state"))
        ledger = os.path.join(root, "state", "ledger.txt")

        # S1 clean file: single heads and citation-only rows are not fused.
        _w(ledger, single + cite_only + machine)
        # Fixtures use the production marker (single source, no literal copy).
        table = [("state/ledger.txt", re.compile(HB_MARKER))]
        rep = scan(table, {}, 90, root)
        check("S1 clean file yields zero hits", rep["counts"]["parallel"] == 0
              and verdict_of(rep) == "PARALLEL-CLEAN", rep["counts"])

        # S2 fused row is detected with the right marker count.
        _w(ledger, single + fused + machine)
        rep = scan(table, {}, 90, root)
        check("S2 fused row detected once", rep["counts"]["parallel"] == 1
              and rep["hits"][0]["markers"] == 2, rep["counts"])
        check("S2 verdict is NEW", verdict_of(rep) == "PARALLEL-NEW", verdict_of(rep))

        # S3 whitelist by fingerprint suppresses the confirmed row.
        sha = rep["hits"][0]["sha"]
        wl = {("state/ledger.txt", sha): {"reason": "confirmed", "sha": sha,
                                          "file": "state/ledger.txt"}}
        rep = scan(table, wl, 90, root)
        check("S3 whitelisted row no longer new",
              rep["counts"]["new"] == 0 and rep["counts"]["whitelisted"] == 1
              and verdict_of(rep) == "PARALLEL-CLEAN", rep["counts"])

        # S4 fingerprint survives appends above the row (no line-number key).
        _w(ledger, "09:20 \u8f6e\u5c3e ok\n" + single + fused + machine)
        rep = scan(table, wl, 90, root)
        check("S4 whitelist survives line drift",
              rep["counts"]["new"] == 0 and rep["hits"][0]["line"] == 3, rep["counts"])

        # S5 a second, different fused row is still red.
        _w(ledger, single + fused + "10:00 \u8f6e\u5c3e ok 10:02 idle_selfcheck x\n")
        rep = scan(table, wl, 90, root)
        check("S5 different fused row stays red",
              rep["counts"]["new"] == 1 and verdict_of(rep) == "PARALLEL-NEW",
              rep["counts"])

        # S6 missing ledger is an engine failure, never a clean pass.
        rep = scan([("state/nope.txt", table[0][1])], {}, 90, root)
        check("S6 missing file fails closed",
              verdict_of(rep) == "PARALLEL-ENGINE-FAIL" and rep["errors"], verdict_of(rep))

        # S7 whitelist round-trip through disk.
        wl_path = os.path.join(tmp, "wl.json")
        save_whitelist(wl_path, [{"file": "state/ledger.txt", "sha": sha,
                                  "reason": "confirmed", "adopted_at": "x"}])
        loaded = load_whitelist(wl_path)
        check("S7 whitelist round-trips", ("state/ledger.txt", sha) in loaded,
              len(loaded))

        # S8 adoption adds only unknown rows and prints the delta.
        _w(ledger, single + fused + "10:00 \u8f6e\u5c3e ok 10:02 idle_selfcheck x\n")
        report = scan(table, {}, 90, root)
        known_before = load_whitelist(wl_path)
        expected = sum(1 for h in report["hits"]
                       if (h["file"], h["sha"]) not in known_before)
        added = adopt_existing(report, wl_path, "selftest adoption")
        again = adopt_existing(report, wl_path, "selftest adoption")
        check("S8 adoption is idempotent",
              added == expected and again == 0, (added, expected, again))
        rep = scan(table, load_whitelist(wl_path), 90, root)
        check("S8 adopted rows read clean",
              verdict_of(rep) == "PARALLEL-CLEAN" and rep["counts"]["new"] == 0,
              rep["counts"])

        # S9 empty lines never count and preview is whitespace-collapsed.
        _w(ledger, "\n\n" + fused.replace("  ", "\t ") + "\n")
        rep = scan(table, {}, 90, root)
        check("S9 blank lines skipped, preview collapsed",
              rep["counts"]["parallel"] == 1 and "  " not in rep["hits"][0]["preview"],
              rep["hits"][0]["preview"] if rep["hits"] else None)

    failed = [c for c in cases if not c[1]]
    for name, ok, detail in cases:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           "" if ok else " -> %r" % (detail,)))
    print("selftest %d/%d %s" % (len(cases) - len(failed), len(cases),
                                 "PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="fused-row census over all ledgers (T177)")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--json", action="store_true")
    r.add_argument("--json-out")
    r.add_argument("--whitelist", default=DEFAULT_WHITELIST)
    r.add_argument("--files", nargs="*")
    r.add_argument("--preview-chars", type=int, default=90)
    r.add_argument("--adopt-existing", action="store_true")
    r.add_argument("--reason", default="confirmed historical fused row")
    sub.add_parser("selftest")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "run":
        return run(args)
    ap.print_help()
    return 3


if __name__ == "__main__":
    sys.exit(main())