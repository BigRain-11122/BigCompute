#!/usr/bin/env python3
"""M167 wake judge for the BigCompute order sentinel (pure, testable).

Why this exists: the sentinel used to hash the WHOLE ledger files, so any
concurrent rewrite -- even one that only reflows or reorders existing rows --
looked exactly like "a new CEO order arrived" and burned a round. The judge
below answers the only question that matters:

    did the SET OF NON-BLANK LINES gain content?

  identical set                -> SKIP   (nothing new can possibly be read)
  one line edited in place     -> WAKE   (removed 1 + added 1 -- an edited
                                          order is NOT "no new content")
  new row appended             -> WAKE   (added N)

Safety bias: anything not provably identical wakes. A missed order costs far
more than an idle round, so the judge never guesses "probably unchanged".

The group ledgers were measured (2026-11-08) to contain real C0 control bytes
(\x08, \x0b) from earlier write-path damage. They are stripped before splitting,
otherwise line boundaries differ depending on who reads the file -- which is
exactly how the PowerShell version produced phantom add/remove pairs.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys

CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
LEDGERS = ("orders.md", "decisions.md")


def read_lines(path):
    """-> non-blank lines with control bytes and trailing space normalized."""
    if not os.path.exists(path):
        return ["MISSING:" + os.path.basename(path)]
    try:
        raw = io.open(path, "rb").read()
    except OSError:
        return ["UNREADABLE:" + os.path.basename(path)]
    text = raw.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = CTRL.sub("", text)
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def digest(lines):
    uniq = sorted(set(lines))
    blob = "\n".join(uniq).encode("utf-8")
    return hashlib.sha256(blob).hexdigest(), uniq


def file_hash(paths):
    """Same shape the PowerShell sentinel used: per-file SHA256 joined by '|'."""
    parts = []
    for p in paths:
        if os.path.exists(p):
            parts.append(hashlib.sha256(io.open(p, "rb").read()).hexdigest().upper())
        else:
            parts.append("MISSING:" + p)
    return "|".join(parts)


def judge(docs_dir, state_path):
    paths = [os.path.join(docs_dir, n) for n in LEDGERS]
    lines = []
    for p in paths:
        lines.extend(read_lines(p))
    sha, uniq = digest(lines)
    out = {"verdict": "SKIP", "added": 0, "removed": 0, "lines": len(uniq),
           "sha256": sha, "lines_uniq": uniq}
    if not os.path.exists(state_path):
        legacy = os.path.join(os.path.dirname(state_path) or ".", "sentinel.snapshot")
        if os.path.exists(legacy):
            prev = io.open(legacy, encoding="utf-8", errors="replace").read().strip()
            if prev and prev != file_hash(paths):
                # a change landed before the upgrade: do not swallow it
                out["verdict"] = "WAKE-TRANSITION"
                return out
        out["verdict"] = "BASELINE"
        return out
    try:
        state = json.load(io.open(state_path, encoding="utf-8"))
    except (OSError, ValueError):
        # an unreadable snapshot must not read as "nothing new"
        out["verdict"] = "BASELINE"
        return out
    if state.get("sha256") == sha:
        return out
    old = set(state.get("lines") or [])
    new = set(uniq)
    out["added"] = len(new - old)
    out["removed"] = len(old - new)
    if out["added"] == 0 and out["removed"] == 0:
        # same content, different digest is impossible; treat defensively
        out["verdict"] = "WAKE-TRANSITION"
    else:
        out["verdict"] = "WAKE"
    return out


def commit(state_path, result):
    payload = {"sha256": result["sha256"],
               "lines": result["lines_uniq"],
               "count": result["lines"]}
    tmp = state_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, state_path)


def line(result):
    return ("VERDICT %s added=%d removed=%d lines=%d sha=%s"
            % (result["verdict"], result["added"], result["removed"],
               result["lines"], result["sha256"][:12]))


def selftest():
    import tempfile

    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("[%s] %s %s" % ("PASS" if cond else "FAIL", name, detail)).rstrip())

    tmp = tempfile.mkdtemp(prefix="sent-")
    docs = os.path.join(tmp, "docs")
    os.makedirs(docs)
    orders = os.path.join(docs, "orders.md")
    decisions = os.path.join(docs, "decisions.md")
    state = os.path.join(tmp, "lineset.json")
    legacy = os.path.join(tmp, "sentinel.snapshot")
    with io.open(orders, "w", encoding="utf-8", newline="\n") as f:
        f.write("# orders\n| 2026-11-08 | alpha |\n")
    with io.open(decisions, "w", encoding="utf-8", newline="\n") as f:
        f.write("# decisions\n| D-1 | beta |\n")

    r = judge(docs, state)
    check("S1 first run without legacy hash -> BASELINE", r["verdict"] == "BASELINE", r["verdict"])
    commit(state, r)
    r = judge(docs, state)
    check("S2 unchanged -> SKIP", r["verdict"] == "SKIP", r["verdict"])

    # blank-line / whitespace churn must not wake
    with io.open(orders, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n   \n")
    r = judge(docs, state)
    check("S3 blank-line churn -> SKIP", r["verdict"] == "SKIP", r["verdict"])

    # a genuine new row must wake, with a count
    with io.open(orders, "a", encoding="utf-8", newline="\n") as f:
        f.write("| 2026-11-08 10:0x | @BigCompute | new order |\n")
    r = judge(docs, state)
    check("S4 new row -> WAKE added=1", r["verdict"] == "WAKE" and r["added"] == 1,
          "added=%d" % r["added"])
    commit(state, r)

    # an in-place edit is content change, not "same as before"
    body = io.open(decisions, encoding="utf-8").read().replace("beta", "beta-revised")
    io.open(decisions, "w", encoding="utf-8", newline="\n").write(body)
    r = judge(docs, state)
    check("S5 in-place edit -> WAKE added=1 removed=1",
          r["verdict"] == "WAKE" and r["added"] == 1 and r["removed"] == 1,
          "added=%d removed=%d" % (r["added"], r["removed"]))
    commit(state, r)

    # reordering the same rows is not new content
    lines = io.open(orders, encoding="utf-8").read().split("\n")
    io.open(orders, "w", encoding="utf-8", newline="\n").write("\n".join(reversed(lines)))
    r = judge(docs, state)
    check("S6 reorder only -> SKIP", r["verdict"] == "SKIP", r["verdict"])

    # control bytes in the ledger (measured in the real group files) must not
    # change the line set
    with io.open(orders, "a", encoding="utf-8", newline="\n") as f:
        f.write("| ctl | \u0008uild_30s \u000b tail |\n")
    r1 = judge(docs, state)
    commit(state, r1)
    r2 = judge(docs, state)
    check("S7 control bytes are normalized, not diffed every tick",
          r1["verdict"] == "WAKE" and r2["verdict"] == "SKIP",
          "%s -> %s" % (r1["verdict"], r2["verdict"]))

    # upgrade path: a change that landed before the switch is not swallowed
    tmp2 = tempfile.mkdtemp(prefix="sent2-")
    docs2 = os.path.join(tmp2, "docs")
    os.makedirs(docs2)
    for name, text in (("orders.md", "# orders\n| old |\n"), ("decisions.md", "# decisions\n")):
        io.open(os.path.join(docs2, name), "w", encoding="utf-8", newline="\n").write(text)
    io.open(os.path.join(tmp2, "sentinel.snapshot"), "w", encoding="utf-8").write("deadbeef")
    r = judge(docs2, os.path.join(tmp2, "lineset.json"))
    check("S8 upgrade with stale legacy hash -> WAKE-TRANSITION",
          r["verdict"] == "WAKE-TRANSITION", r["verdict"])

    # a corrupt snapshot must not read as "nothing new"
    io.open(os.path.join(tmp2, "lineset.json"), "w", encoding="utf-8").write("{not json")
    r = judge(docs2, os.path.join(tmp2, "lineset.json"))
    check("S9 corrupt snapshot -> BASELINE (never SKIP)", r["verdict"] == "BASELINE", r["verdict"])

    # missing ledger is named, not silently treated as empty
    r = judge(os.path.join(tmp2, "nope"), os.path.join(tmp2, "x.json"))
    check("S10 missing ledgers remain visible",
          any(l.startswith("MISSING:") for l in r["lines_uniq"]), str(r["lines"]))

    print("sentinel judge selftest: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", nargs="?", default="judge", choices=["judge", "selftest"])
    ap.add_argument("--docs", default="")
    ap.add_argument("--state", default="")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        return selftest()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs = a.docs or os.path.join(os.path.dirname(os.path.dirname(root)), "docs")
    state = a.state or os.path.join(root, "state", "sentinel.lineset.json")
    r = judge(docs, state)
    print(line(r))
    if a.commit:
        commit(state, r)
    return 0


if __name__ == "__main__":
    sys.exit(main())