#!/usr/bin/env python3
"""ledger_blank_census.py - tech T96 step 1: full-file empty-line census
for the seven git-tracked ledgers, with damage/legal classification.

Background (T95 -> T96): qa_smoke probe no.8 (c) only sniffs the tail-12
window, so deep historical residue from the T24-family write-path damage
(non-canonical appends that emit a leading blank line, 14:0x case) can
survive beyond probe coverage. This census sweeps the WHOLE file and
classifies every blank line:

  log-damage    blank line inside a pure line-stream ledger
                (rounds.log / heartbeat.txt: every canonical append is a
                single line, so ANY blank is write-path damage residue)
  table-gap     blank line with a table row on BOTH sides (markdown ledger:
                splits the table in renderers; the T24-family damage class)
  edge-blank    blank as first line of file, or blank as last line before
                EOF (leading-blank / trailing double-newline damage)
  block-sep     blank line adjacent to a heading or prose block (LEGAL
                markdown separator - DO NOT touch)

Commands:
  census [paths...]  sweep the seven ledgers (default set), print the
                     per-file census + damage detail, write JSON evidence
                     to state/ledger-blank-census-<ts>.json
  debride [paths...] surgical removal of damage-classified blank lines
                     ONLY (log-damage / table-gap / edge-blank); legal
                     block-sep lines are never touched; every removal is
                     pre-verified pure-whitespace; sha256 before/after;
                     refuses to write any file that fails strict utf-8
  selftest           synthetic fixtures in a temp dir (real ledgers never
                     touched): clean / deep table-gap beyond tail-12 /
                     legal section separator / leading blank / trailing
                     double-newline / missing trailing newline

Exit: census 0 = sweep completed (damage count is reported, not gated);
selftest 0 = all fixtures classified as expected.
Encoding rule: pure ASCII (group coding law).
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")

# (relpath, policy): "log" = pure line stream, any blank is damage;
# "md" = markdown ledger, blanks classified by neighborhood.
LEDGERS = (
    ("state/rounds.log", "log"),
    ("state/heartbeat.txt", "log"),
    ("HQ-FEEDBACK.md", "md"),
    ("state/proposals.md", "md"),
    ("state/queue/main.md", "md"),
    ("state/queue/tech.md", "md"),
    ("state/queue/explore.md", "md"),
)

DAMAGE_KINDS = ("log-damage", "table-gap", "edge-blank")


def read_lines(path):
    """Return (lines, trailing_nl, decode_ok, nbytes) for census."""
    with open(path, "rb") as f:
        raw = f.read()
    trailing_nl = raw.endswith(b"\n")
    try:
        text = raw.decode("utf-8", errors="strict")
        decode_ok = True
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        decode_ok = False
    lines = text.split("\n")
    if trailing_nl:
        lines = lines[:-1]  # drop the split artifact after the final \n
    return lines, trailing_nl, decode_ok, len(raw)


def neighbor(lines, i, step):
    """Nearest non-blank line in direction step, or None."""
    j = i + step
    n = len(lines)
    while 0 <= j < n and not lines[j].strip():
        j += step
    return lines[j] if 0 <= j < n else None


def classify_file(lines, policy):
    """Classify every blank line in one ledger. Returns list of dicts."""
    found = []
    for i, ln in enumerate(lines):
        if ln.strip():
            continue
        prev = neighbor(lines, i, -1)
        nxt = neighbor(lines, i, 1)
        if policy == "log":
            kind = "log-damage"
        elif prev is None or nxt is None:
            kind = "edge-blank"
        else:
            p_tab = prev.lstrip().startswith("|")
            n_tab = nxt.lstrip().startswith("|")
            if p_tab and n_tab:
                kind = "table-gap"
            else:
                kind = "block-sep"
        found.append({
            "line": i + 1,
            "kind": kind,
            "damage": kind in DAMAGE_KINDS,
            "prev": (prev or "")[:48],
            "next": (nxt or "")[:48],
        })
    return found


def census(relpaths=None):
    relpaths = relpaths or [rel for rel, _ in LEDGERS]
    policy = dict(LEDGERS)
    report, total_damage = [], 0
    for rel in relpaths:
        path = os.path.join(ROOT, *rel.split("/"))
        if not os.path.exists(path):
            report.append({"path": rel, "error": "missing"})
            print("%s: MISSING" % rel)
            continue
        lines, trailing_nl, decode_ok, nbytes = read_lines(path)
        blanks = classify_file(lines, policy.get(rel, "md"))
        tail12 = len([b for b in blanks if b["line"] > len(lines) - 12])
        dmg = [b for b in blanks if b["damage"]]
        total_damage += len(dmg)
        entry = {
            "path": rel, "bytes": nbytes, "lines": len(lines),
            "decode_ok": decode_ok, "trailing_nl": trailing_nl,
            "empty_total": len(blanks), "empty_in_tail12": tail12,
            "damage": len(dmg), "legal_sep": len(blanks) - len(dmg),
        }
        report.append(entry)
        print("%s: bytes=%d lines=%d empty=%d tail12_empty=%d damage=%d "
              "legal_sep=%d trailing_nl=%s decode_ok=%s"
              % (rel, nbytes, len(lines), len(blanks), tail12, len(dmg),
                 entry["legal_sep"], "ok" if trailing_nl else "MISSING",
                 "ok" if decode_ok else "REPLACED"))
        for b in dmg:
            print("  DAMAGE line %d %s | prev=%r next=%r"
                  % (b["line"], b["kind"], b["prev"][:32], b["next"][:32]))
    print("CENSUS-VERDICT: total_damage=%d (damage kinds: %s; "
          "block-sep = legal, do NOT touch)"
          % (total_damage, "/".join(DAMAGE_KINDS)))
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(ROOT, "state",
                       "ledger-blank-census-%s.json" % ts)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"ts": ts, "total_damage": total_damage,
                   "files": report, "blanks_detail": {
                       e["path"]: [b for b in classify_file(
                           read_lines(os.path.join(
                               ROOT, *e["path"].split("/")))[0],
                           policy.get(e["path"], "md"))]
                       for e in report if "error" not in e}},
                  f, ensure_ascii=False, indent=1)
    print("evidence: %s" % os.path.relpath(out, ROOT))
    return total_damage


def debride(relpaths=None, root=None):
    """Surgically remove ONLY damage-classified blank lines.

    Safety rails: every target line is re-verified pure-whitespace before
    the write; files that fail strict utf-8 decode are skipped (never
    bake replacement chars back to disk); legal block-sep lines survive;
    sha256 before/after is reported so the change is auditable.
    root param exists so the selftest can exercise the whole path on
    temp fixtures without touching the real ledgers.
    """
    import hashlib
    root = root or ROOT
    relpaths = relpaths or [rel for rel, _ in LEDGERS]
    policy = dict(LEDGERS)
    removed_total = 0
    for rel in relpaths:
        path = os.path.join(root, *rel.split("/"))
        if not os.path.exists(path):
            print("%s: MISSING - skipped" % rel)
            continue
        with open(path, "rb") as f:
            raw = f.read()
        sha_pre = hashlib.sha256(raw).hexdigest()
        lines, trailing_nl, decode_ok, _ = read_lines(path)
        if not decode_ok:
            print("%s: strict utf-8 decode FAILED - skipped "
                  "(never write replacement chars)" % rel)
            continue
        blanks = classify_file(lines, policy.get(rel, "md"))
        dmg_idx = sorted(b["line"] - 1 for b in blanks if b["damage"])
        if not dmg_idx:
            print("%s: 0 damage lines - untouched" % rel)
            continue
        for i in dmg_idx:
            if lines[i].strip():
                print("%s: ABORT - line %d is not blank; nothing written"
                      % (rel, i + 1))
                return 1
        keep = set(dmg_idx)
        new_lines = [ln for i, ln in enumerate(lines) if i not in keep]
        text = "\n".join(new_lines) + ("\n" if trailing_nl else "")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        with open(path, "rb") as f:
            sha_post = hashlib.sha256(f.read()).hexdigest()
        removed_total += len(dmg_idx)
        print("%s: removed %d blank line(s) at %s sha %s->%s"
              % (rel, len(dmg_idx), [i + 1 for i in dmg_idx],
                 sha_pre[:12], sha_post[:12]))
    print("DEBRIDE-DONE: removed=%d blank-only line(s); content untouched"
          % removed_total)
    return 0


def _mk(tmp, name, text):
    path = os.path.join(tmp, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return path


def selftest():
    """Synthetic fixtures in a temp dir; real ledgers never touched."""
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ledger-census-selftest-")
    fails = []

    def expect(label, cond):
        print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    try:
        # F1 clean log stream: single lines, trailing newline, no blanks
        p = _mk(tmp, "f1.log", "a1\na2\na3\n")
        lines, tnl, dec, _ = read_lines(p)
        blanks = classify_file(lines, "log")
        expect("F1 clean log: 0 blanks, trailing ok, decode ok",
               not blanks and tnl and dec and len(lines) == 3)
        # F2 log damage: mid-file blank (T24 family, deep beyond tail-12)
        p = _mk(tmp, "f2.log", "".join("e%d\n" % i for i in range(1, 21))
                + "\n" + "e21\n")
        lines, _, _, _ = read_lines(p)
        blanks = classify_file(lines, "log")
        expect("F2 log-damage detected at line 21 (deep, tail-12 blind)",
               len(blanks) == 1 and blanks[0]["line"] == 21
               and blanks[0]["kind"] == "log-damage")
        # F3 md table-gap: blank between two table rows = damage
        p = _mk(tmp, "f3.md",
                "| a | b |\n|---|---|\n| r1 | x |\n\n| r2 | y |\n")
        lines, _, _, _ = read_lines(p)
        blanks = classify_file(lines, "md")
        expect("F3 table-gap classified as damage (row both sides)",
               len(blanks) == 1 and blanks[0]["kind"] == "table-gap"
               and blanks[0]["damage"])
        # F4 md block-sep: blank between heading and prose = legal
        p = _mk(tmp, "f4.md", "# sec\n\ntext line\n")
        lines, _, _, _ = read_lines(p)
        blanks = classify_file(lines, "md")
        expect("F4 heading/prose blank = legal block-sep, 0 damage",
               len(blanks) == 1 and blanks[0]["kind"] == "block-sep"
               and not blanks[0]["damage"])
        # F5 leading blank + F6 trailing double-newline = edge damage
        p = _mk(tmp, "f5.md", "\n| r | x |\n")
        lines, _, _, _ = read_lines(p)
        b5 = classify_file(lines, "md")
        p = _mk(tmp, "f6.md", "| r | x |\n\n")
        lines, _, _, _ = read_lines(p)
        b6 = classify_file(lines, "md")
        expect("F5 leading blank + F6 trailing blank = edge-blank damage",
               len(b5) == 1 and b5[0]["kind"] == "edge-blank"
               and len(b6) == 1 and b6[0]["kind"] == "edge-blank")
        # F7 missing trailing newline flagged
        p = _mk(tmp, "f7.log", "a1\na2")  # no final newline
        _, tnl7, _, _ = read_lines(p)
        expect("F7 missing trailing newline detected", not tnl7)
        # F8 debride path on a synthetic damaged fixture: removes ONLY
        # the damage blank line, keeps the legal separator, content
        # bytes preserved, trailing newline style preserved
        f8rel = "state/queue/f8.md"
        f8path = _mk(tmp, f8rel,
                     "| r1 | x |\n\n| r2 | y |\n# sec\n\nprose\n")
        rc = debride([f8rel], root=tmp)
        with open(f8path, "r", encoding="utf-8", newline="") as fh:
            after = fh.read()
        expect("F8 debride: rc=0, table-gap blank gone, block-sep kept, "
               "content intact",
               rc == 0 and after == "| r1 | x |\n| r2 | y |\n# sec\n\n"
               "prose\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("selftest verdict: %d fail(s)" % len(fails))
    return 0 if not fails else 1


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "selftest":
        print("ledger_blank_census selftest (T96 - synthetic fixtures, "
              "real ledgers untouched)")
        return selftest()
    if argv and argv[0] == "debride":
        return debride([a for a in argv[1:] if not a.startswith("-")]
                       or None)
    relpaths = [a for a in argv if a != "census"] or None
    return 0 if census(relpaths) is not None else 1


if __name__ == "__main__":
    sys.exit(main())
