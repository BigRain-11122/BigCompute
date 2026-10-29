#!/usr/bin/env python3
"""heartbeat_token_census.py - tech T168: write side -> guard side census.

idle_selfcheck emits one heartbeat row per tick; qa_smoke asserts a subset
of its tokens. T166/T167 pinned only the three supply labels, so any other
token renamed on the write side leaves its readers comparing against
nothing while the row still looks healthy. This census extracts the emitted
token table from the writer by AST (not by eye), reads the guard set the QA
suite declares, and reports both directions:

  GUARDED      emitted and asserted by a qa_smoke probe
  UNGUARDED    emitted with no probe side (advisory; needs-based backfill)
  STALE-GUARD  declared guard that the writer no longer emits = the guard
               is fiction, so this is a defect (exit 1)

Per-token consumer counts are listed so a backfill can be ordered by blast
radius instead of alphabetically.

Usage:
  python Tools/heartbeat_token_census.py run [--out PATH] [--strict]
  python Tools/heartbeat_token_census.py selftest
Exit 0 = census ran (FULL/PARTIAL); 1 = STALE-GUARD, or UNGUARDED under
--strict. Advisory scope: this proves the readers' token names still exist,
it does not re-derive the gate arithmetic.
Encoding rule: this file stays PURE ASCII (group coding law).
"""
import argparse
import ast
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WRITER = os.path.join(HERE, "idle_selfcheck.py")
QA = os.path.join(HERE, "qa_smoke.py")
STATE = os.path.join(ROOT, "state")

TOKEN_EQ_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=")
FACES_EXCLUDED = ("idle_selfcheck.py", "qa_smoke.py",
                  "heartbeat_token_census.py")


def heartbeat_format_literal(path=WRITER):
    """Extract the check() heartbeat format string from the writer source."""
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "check":
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if not any(getattr(t, "id", None) == "line" for t in stmt.targets):
                continue
            val = stmt.value
            if (isinstance(val, ast.BinOp) and isinstance(val.left,
                                                          ast.Constant)
                    and isinstance(val.left.value, str)):
                return val.left.value
    raise RuntimeError("heartbeat format literal not found in %s" % path)


def tokens_in(text):
    return TOKEN_EQ_RE.findall(text or "")


def supply_sub_tokens():
    """Tokens the writer adds through supply_label()'s own format string."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import idle_selfcheck as isc
    return tokens_in(isc.supply_label(0, 0))


def emitted_tokens(path=WRITER):
    """Ordered emitted token table (writer format + supply_label sub-face).

    Order is the writer's literal order; sub-face tokens are appended when
    the single construction point is interpolated into a bare %s slot, so
    membership is exact while position is approximate (documented, the
    census never depends on order).
    """
    out = []
    for name in tokens_in(heartbeat_format_literal(path)) + supply_sub_tokens():
        if name not in out:
            out.append(name)
    return out


def guarded_tokens():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import qa_smoke as qs
    return [name for name, _ in qs.HEARTBEAT_GUARDED_TOKENS]


def consumers(token, tools_dir=None):
    """Reader faces referencing the token, writer/QA/tool itself excluded."""
    tools_dir = tools_dir or HERE
    hits = {}
    for name in sorted(os.listdir(tools_dir)):
        if not name.endswith(".py") or name in FACES_EXCLUDED:
            continue
        path = os.path.join(tools_dir, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        n = text.count(token)
        if n:
            hits[name] = n
    return hits


def classify(emitted, guarded, consumers_map=None):
    """Pure split: guarded / unguarded / stale-guard + verdict."""
    guarded = list(guarded)
    emitted = list(emitted)
    unguarded = [t for t in emitted if t not in guarded]
    stale = [t for t in guarded if t not in emitted]
    if stale:
        verdict = "HEARTBEAT-GUARD-STALE"
    elif unguarded:
        verdict = "HEARTBEAT-GUARD-PARTIAL"
    else:
        verdict = "HEARTBEAT-GUARD-FULL"
    return {"verdict": verdict,
            "emitted": emitted,
            "guarded": guarded,
            "unguarded": unguarded,
            "stale_guard": stale,
            "consumers": consumers_map or {}}


def run_census(out=None, strict=False):
    emitted = emitted_tokens()
    guarded = guarded_tokens()
    cmap = {t: consumers(t) for t in emitted}
    rep = classify(emitted, guarded, cmap)
    rep["generated_at"] = datetime.datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S")
    rep["writer"] = os.path.relpath(WRITER, ROOT).replace("\\", "/")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=True, indent=1, sort_keys=True)
    return rep


def print_report(rep):
    print("heartbeat token census %s (T168)" % rep["generated_at"])
    print("VERDICT=%s emitted=%d guarded=%d unguarded=%d stale=%d"
          % (rep["verdict"], len(rep["emitted"]), len(rep["guarded"]),
             len(rep["unguarded"]), len(rep["stale_guard"])))
    if rep["stale_guard"]:
        print("STALE-GUARD (writer no longer emits, guard is fiction): %s"
              % " ".join(rep["stale_guard"]))
    if rep["unguarded"]:
        print("UNGUARDED (advisory, ordered by reader faces):")
        for tok in sorted(rep["unguarded"],
                          key=lambda t: (-len(rep["consumers"].get(t) or {}),
                                         t)):
            faces = rep["consumers"].get(tok) or {}
            print("  %-22s readers=%d %s" % (
                tok, len(faces),
                " ".join("%s:%d" % (k, v) for k, v in
                         sorted(faces.items(), key=lambda kv: (-kv[1],
                                                               kv[0]))[:4])))


def selftest():
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    fmt = heartbeat_format_literal()
    toks = tokens_in(fmt)
    check("S1 writer literal parses (tokens=%d)" % len(toks), len(toks) >= 20)
    check("S2 literal carries threshold tokens",
          "ram_free_pct" in toks and "lane8090" in toks)

    sub = supply_sub_tokens()
    check("S3 supply sub-face resolved",
          sub == ["pool_rows_candidate", "pool_effective"])

    emitted = emitted_tokens()
    check("S4 emitted table deduped and joins sub-face",
          len(emitted) == len(set(emitted))
          and "pool_rows_candidate" in emitted)

    guards = guarded_tokens()
    covered = [t for t in guards if t in emitted]
    check("S5 guard set targets real emitted tokens (covered=%d)"
          % len(covered), len(covered) >= 6)

    rep = classify(["a", "b"], ["b", "c"], {"a": {"x.py": 1}})
    check("S6 stale guard wins over partial",
          rep["verdict"] == "HEARTBEAT-GUARD-STALE"
          and rep["unguarded"] == ["a"] and rep["stale_guard"] == ["c"])

    rep2 = classify(["a", "b"], ["b"])
    check("S7 subset guard -> PARTIAL",
          rep2["verdict"] == "HEARTBEAT-GUARD-PARTIAL"
          and rep2["unguarded"] == ["a"] and not rep2["stale_guard"])

    rep3 = classify(["a"], ["a"])
    check("S8 full coverage -> FULL", rep3["verdict"] == "HEARTBEAT-GUARD-FULL")

    faces = consumers("ram_free_pct")
    check("S9 reader scan returns other faces (%d)" % len(faces),
          isinstance(faces, dict) and len(faces) >= 1)

    print("selftest verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "selftest":
        return selftest()
    ap = argparse.ArgumentParser(
        description="T168 heartbeat write-side -> guard census")
    ap.add_argument("cmd", nargs="?", default="run", choices=["run"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when unguarded tokens exist")
    args = ap.parse_args(argv)
    out = args.out
    if out is None:
        os.makedirs(STATE, exist_ok=True)
        out = os.path.join(STATE, "heartbeat-token-census-%s.json"
                           % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    rep = run_census(out=out)
    print_report(rep)
    print("report=%s" % os.path.relpath(out, ROOT).replace("\\", "/"))
    if rep["stale_guard"]:
        return 1
    if args.strict and rep["unguarded"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())