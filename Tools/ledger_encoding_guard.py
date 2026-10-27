#!/usr/bin/env python3
"""ledger_encoding_guard.py - tech T145: ledger encoding census + write-side
mojibake guard for state/rounds.log (and the other single-line ledgers).

Why this exists (T145 premise, then corrected by byte-level evidence):
T144 observed that compose() of status-export could only "see" ASCII-ish
segments of rounds.log and assumed the Chinese was already destroyed by an
early GBK round-trip (U+FFFD + Latin-1 noise). The 2026-10-27 21:3x byte
census refutes that for the current file:

  state/rounds.log  386,303 B  strict-UTF-8 = OK   U+FFFD = 0
  324 lines, 318/318 lines carry CJK
  chars in U+0080..U+00FF = 37, ALL of them legitimate typography
  (U+00A7 section sign, U+00B7 middle dot, U+00D7 times, U+00A5 yen)

So there is no frozen mojibake to repair, and history must stay read-only
(T145 step (3)). What remains is the REGRESSION risk: PowerShell
Add-Content defaults to ANSI(GBK) on zh-CN hosts, so one careless shell
append silently injects真 mojibake into a UTF-8 ledger. This tool makes
that class mechanically visible and blocks it at the canonical writer.

Detection model (two distinct classes, never conflated):
  UTF8-DOUBLE   text that is UTF-8 bytes mis-decoded as Latin-1/cp1252,
                then re-saved as UTF-8 ("ä¸­æ–‡" for 中文). Detected by
                re-encoding a run of U+0080..U+00FF chars as Latin-1 and
                decoding the bytes as UTF-8; if that yields >=2 CJK chars
                the run is真 damage. HARD flag.
  LATIN-RUN     any run of U+0080..U+00FF that does NOT satisfy the above.
                Informational only: this is where §, ·, ×, ¥ live. A file
                with nothing but LATIN-RUNs is healthy.

Commands:
  census [path ...]   read-only sweep (default: the single-line ledgers)
  guard --line TEXT   verdict for one candidate append line (exit 0 ok / 6 suspect)
  guard --file PATH   verdict for a whole file
  selftest            fixtures; exit 0 = all pass

Exit codes: 0 ok, 6 MOJIBAKE-SUSPECT, 3 unreadable input, 4 bad usage.
"""
import argparse
import io
import json
import sys

CJK_LO, CJK_HI = 0x4E00, 0x9FFF
LAT_LO, LAT_HI = 0x80, 0xFF
DEFAULT_TARGETS = [
    "state/rounds.log",
    "state/heartbeat.txt",
]


def _print_safe(text):
    """Console codec on zh-CN hosts is GBK; printing must never abort a report."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, "replace").decode(enc, "replace"))


def _cjk_count(s):
    return sum(1 for c in s if CJK_LO <= ord(c) <= CJK_HI)


def decode_strict(raw):
    """bytes -> (text, None) on strict UTF-8, else (None, reason)."""
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as e:
        return None, "%s at byte %d" % (e.reason, e.start)


def latin_runs(text):
    """Maximal runs of U+0080..U+00FF as (start, end) index pairs."""
    runs, start = [], None
    for i, c in enumerate(text):
        if LAT_LO <= ord(c) <= LAT_HI:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(text)))
    return runs


def _double_decoded(run_text):
    """Latin-1 re-encode then UTF-8 decode; returns recovered text or None."""
    try:
        raw = run_text.encode("latin-1")
    except UnicodeEncodeError:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def classify(text):
    """-> dict(status, utf8_double, latin_runs, fffd, cjk_chars)"""
    fffd = text.count("\ufffd")
    doubles = []
    for a, b in latin_runs(text):
        run = text[a:b]
        if len(run) < 2:
            continue
        back = _double_decoded(run)
        if back and _cjk_count(back) >= 2:
            doubles.append({"at": a, "run": run, "recovered": back})
    status = "MOJIBAKE-SUSPECT" if (doubles or fffd) else "ENCODING-OK"
    return {
        "status": status,
        "utf8_double": doubles,
        "latin_runs": len(latin_runs(text)),
        "fffd": fffd,
        "cjk_chars": _cjk_count(text),
    }


def census(paths):
    rows = []
    for p in paths:
        try:
            raw = open(p, "rb").read()
        except OSError as e:
            rows.append({"path": p, "status": "UNREADABLE", "error": str(e)})
            continue
        text, err = decode_strict(raw)
        if text is None:
            rows.append({"path": p, "status": "NOT-UTF8", "error": err, "bytes": len(raw)})
            continue
        cls = classify(text)
        lines = text.splitlines()
        with_cjk = sum(1 for l in lines if _cjk_count(l) > 0)
        rows.append({
            "path": p,
            "status": cls["status"],
            "bytes": len(raw),
            "lines": len(lines),
            "lines_with_cjk": with_cjk,
            "fffd": cls["fffd"],
            "utf8_double_runs": len(cls["utf8_double"]),
            "latin_runs": cls["latin_runs"],
            "cjk_chars": cls["cjk_chars"],
            "samples": cls["utf8_double"][:3],
        })
    return rows


def guard_line(line):
    """Write-side predicate for one candidate append line."""
    return classify(line)


def verdict_exit(status):
    return 0 if status == "ENCODING-OK" else 6


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # A: healthy Chinese line with the legitimate typography that tripped T144
    clean = "2026-10-27 21:40 轮：§二 · 门控 × 上限 ¥29.9（中文正文实读）"
    c = classify(clean)
    run("A clean-cjk-with-legit-latin", c["status"] == "ENCODING-OK" and c["latin_runs"] >= 1,
        "latin_runs=%d" % c["latin_runs"])

    # B: true UTF-8-as-Latin-1 double encoding must be flagged
    doubled = "中文测试".encode("utf-8").decode("latin-1")
    c = classify("prefix " + doubled + " suffix")
    run("B utf8-double-flagged", c["status"] == "MOJIBAKE-SUSPECT" and len(c["utf8_double"]) == 1,
        "runs=%d" % len(c["utf8_double"]))

    # C: recovery is faithful (the detector reports what the text should say)
    run("C recovery-text", c["utf8_double"][0]["recovered"] == "中文测试",
        repr(c["utf8_double"][0]["recovered"]) if c["utf8_double"] else "none")

    # D: U+FFFD replacement chars alone are damage even without latin runs
    c = classify("中文\ufffd\ufffd正文")
    run("D fffd-flagged", c["status"] == "MOJIBAKE-SUSPECT" and c["fffd"] == 2,
        "fffd=%d" % c["fffd"])

    # E: pure ASCII is clean and carries zero latin runs
    c = classify("plain ascii only 2026-10-27")
    run("E ascii-clean", c["status"] == "ENCODING-OK" and c["latin_runs"] == 0)

    # F: latin-1 encoded accented name (legit foreign name) must NOT be flagged
    c = classify("Ren\u00e9e M\u00fcller 参与评审 · 结论 OK")
    run("F legit-accents-not-flagged", c["status"] == "ENCODING-OK",
        "runs=%d doubles=%d" % (c["latin_runs"], len(c["utf8_double"])))

    # G: strict decode rejects GBK bytes (the Add-Content default-ANSI class)
    gbk_bytes = "中文".encode("gbk")
    text, err = decode_strict(gbk_bytes)
    run("G gbk-bytes-not-utf8", text is None and err is not None, str(err))

    # H: single stray latin char cannot form a signature -> honest limitation
    c = classify("a\u00e4b")
    run("H single-char-not-flagged", c["status"] == "ENCODING-OK" and c["latin_runs"] == 1)

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        _print_safe("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))
    _print_safe("SELFTEST %d/%d %s" % (passed, len(checks), "PASS" if passed == len(checks) else "FAIL"))
    return 0 if passed == len(checks) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="ledger encoding census + mojibake guard (T145)")
    sub = ap.add_subparsers(dest="cmd")

    p_cen = sub.add_parser("census", help="read-only sweep of single-line ledgers")
    p_cen.add_argument("paths", nargs="*", default=None)

    p_guard = sub.add_parser("guard", help="verdict for one line or one file")
    p_guard.add_argument("--line", default=None)
    p_guard.add_argument("--file", default=None)

    sub.add_parser("selftest", help="fixtures")

    args = ap.parse_args(argv)
    if args.cmd == "census":
        paths = args.paths or DEFAULT_TARGETS
        rows = census(paths)
        for r in rows:
            _print_safe(json.dumps(r, ensure_ascii=False))
        worst = 6 if any(r["status"] in ("MOJIBAKE-SUSPECT", "NOT-UTF8", "UNREADABLE") for r in rows) else 0
        _print_safe("CENSUS %s files=%d" % ("MOJIBAKE-SUSPECT" if worst else "ENCODING-OK", len(rows)))
        return worst
    if args.cmd == "guard":
        if args.line is not None:
            c = guard_line(args.line)
            _print_safe(json.dumps({"mode": "line", "status": c["status"],
                                    "utf8_double": len(c["utf8_double"]),
                                    "fffd": c["fffd"]}, ensure_ascii=False))
            return verdict_exit(c["status"])
        if args.file is not None:
            raw = open(args.file, "rb").read()
            text, err = decode_strict(raw)
            if text is None:
                _print_safe("VERDICT=NOT-UTF8 %s" % err)
                return 6
            c = classify(text)
            _print_safe("VERDICT=%s utf8_double=%d fffd=%d latin_runs=%d files=%s"
                        % (c["status"], len(c["utf8_double"]), c["fffd"], c["latin_runs"], args.file))
            return verdict_exit(c["status"])
        ap.error("guard needs --line or --file")
    if args.cmd == "selftest":
        return _selftest()
    ap.print_help()
    return 4


if __name__ == "__main__":
    sys.exit(main())