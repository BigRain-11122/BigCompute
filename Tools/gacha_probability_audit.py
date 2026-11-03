"""Gacha disclosed-vs-observed probability audit (queue M112, drill only).

blind-box-gacha-compliance §7.3 ① requires a monthly sampling check: the
deviation between the disclosed tier probabilities (§7.1 p_pct, which must sum
to 100%) and the actual draw distribution has to stay within {threshold}%.
Until this file existed the criterion had no measuring instrument at all, while
the sibling §7.2 cap gate already had one.

Three ways a naive implementation lies, all refused here:

  * threshold unset -> must NOT silently pass. §7.3 keeps {threshold} blank
    until the CEO pricing confirmation lands, so an unset value can only mean
    "cannot judge", never "looks fine".
  * small sample -> must NOT cry wolf. A tier with 2 observations can show a
    huge deviation by pure luck; below the sample floor the audit reports
    INSUFFICIENT instead of a deviation verdict.
  * declared probabilities that do not sum to 100% -> the comparison itself is
    meaningless, so the audit refuses to compare at all.

Deviations are carried in basis points (integer) so the threshold boundary is
exact rather than a float coin flip.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "state")

BP = 10000  # 100.00% in basis points
DEFAULT_MIN_SAMPLE = 30
DEFAULT_MIN_TIER_COUNT = 5

OK = "OK"
OVER = "OVER-THRESHOLD"
SMALL_N = "SMALL-N"
UNDECLARED = "UNDECLARED"

V_OK = "PROB-AUDIT-OK"
V_DEVIATION = "PROB-AUDIT-DEVIATION"
V_CANNOT_JUDGE = "PROB-AUDIT-CANNOT-JUDGE"
V_INSUFFICIENT = "PROB-AUDIT-INSUFFICIENT"
V_BAD_DECLARED = "PROB-AUDIT-BAD-DECLARED"
V_BAD_DATA = "PROB-AUDIT-BAD-DATA"

EXIT = {
    V_OK: 0,
    V_DEVIATION: 2,
    V_CANNOT_JUDGE: 3,
    V_INSUFFICIENT: 4,
    V_BAD_DECLARED: 5,
    V_BAD_DATA: 5,
}


def to_bp(pct):
    """Exact basis points from a percentage. None for anything non-numeric."""
    try:
        value = float(pct)
    except (TypeError, ValueError):
        return None
    return int(round(value * 100))


def parse_declared(spec):
    """Parse 'common=70,rare=25,epic=5' into [(rarity, bp), ...].

    Bad shapes are reported, not guessed: a duplicated rarity or a non-numeric
    percentage makes the whole declaration unusable.
    """
    tiers = []
    problems = []
    for chunk in str(spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            problems.append("tier without '=': %r" % chunk)
            continue
        rarity, pct = chunk.split("=", 1)
        rarity = rarity.strip()
        if not rarity:
            problems.append("empty rarity in %r" % chunk)
            continue
        if any(r == rarity for r, _ in tiers):
            problems.append("duplicate rarity: %s" % rarity)
            continue
        bp = to_bp(pct.strip())
        if bp is None:
            problems.append("non-numeric p_pct for %s: %r" % (rarity, pct))
            continue
        if bp < 0:
            problems.append("negative p_pct for %s: %r" % (rarity, pct))
            continue
        tiers.append((rarity, bp))
    if problems:
        # Fail-closed: a declaration with any defect is unusable whole. Keeping
        # the tiers that happened to parse would hide the defect behind a
        # plausible-looking percentage table.
        return [], problems
    return tiers, problems


def load_draws(path, month=None):
    """Draw rows from jsonl. Each row is {rarity, month?} or a bare string."""
    draws = []
    skipped = 0
    if not os.path.isfile(path):
        return draws, skipped
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if isinstance(row, str):
                rarity, row_month = row, None
            elif isinstance(row, dict):
                rarity, row_month = row.get("rarity"), row.get("month")
            else:
                skipped += 1
                continue
            if month and row_month and row_month != month:
                continue
            if not isinstance(rarity, str) or not rarity.strip():
                skipped += 1
                continue
            draws.append(rarity.strip())
    return draws, skipped


def audit(tiers, draws, threshold_pp, min_sample=DEFAULT_MIN_SAMPLE,
          min_tier_count=DEFAULT_MIN_TIER_COUNT, bad_rows=0):
    """Compare disclosed tier probabilities against observed draw shares.

    tiers       = [(rarity, declared_bp), ...]
    draws       = [rarity, ...]
    threshold_pp= allowed absolute deviation in percentage points (None = unset)
    """
    result = {
        "verdict": None,
        "reason": "",
        "threshold_pp": threshold_pp,
        "min_sample": min_sample,
        "min_tier_count": min_tier_count,
        "total_draws": len(draws),
        "bad_rows": bad_rows,
        "tiers": [],
        "undeclared": [],
    }

    if not tiers:
        result["verdict"] = V_BAD_DECLARED
        result["reason"] = "no declared tiers; nothing to compare against"
        return result

    total_bp = sum(bp for _, bp in tiers)
    if total_bp != BP:
        result["verdict"] = V_BAD_DECLARED
        result["reason"] = (
            "declared probabilities sum to %.2f%%, not 100.00%%; §7.1 requires "
            "sum=100%% before any comparison is meaningful"
            % (total_bp / 100.0)
        )
        return result

    declared_names = {rarity for rarity, _ in tiers}
    counts = {rarity: 0 for rarity, _ in tiers}
    undeclared = {}
    for rarity in draws:
        if rarity in counts:
            counts[rarity] += 1
        else:
            undeclared[rarity] = undeclared.get(rarity, 0) + 1
    result["undeclared"] = [
        {"rarity": name, "count": n} for name, n in sorted(undeclared.items())
    ]

    total = len(draws)
    for rarity, declared_bp in tiers:
        observed = counts[rarity]
        observed_bp = int(round(observed * BP / float(total))) if total else 0
        dev_bp = observed_bp - declared_bp
        if total == 0:
            status = SMALL_N
        elif observed < min_tier_count:
            status = SMALL_N
        else:
            status = OK
        result["tiers"].append({
            "rarity": rarity,
            "declared_pct": declared_bp / 100.0,
            "observed_count": observed,
            "observed_pct": observed_bp / 100.0,
            "dev_pp": dev_bp / 100.0,
            "status": status,
        })

    if undeclared or bad_rows:
        result["verdict"] = V_BAD_DATA
        parts = []
        if undeclared:
            parts.append("undeclared rarity in draws: %s"
                         % ", ".join(sorted(undeclared)))
        if bad_rows:
            parts.append("%d unreadable row(s)" % bad_rows)
        result["reason"] = "; ".join(parts)
        return result

    threshold_bp = to_bp(threshold_pp)
    if threshold_bp is None:
        # Fail-closed: an unset threshold (§7.3 pending CEO pricing batch) means
        # the check cannot conclude, and must not be reported as a pass.
        result["verdict"] = V_CANNOT_JUDGE
        result["reason"] = (
            "threshold unset; §7.3 ① {threshold}% lands with the CEO pricing "
            "confirmation, so no pass/fail may be claimed"
        )
        return result

    if total < min_sample:
        result["verdict"] = V_INSUFFICIENT
        result["reason"] = "total draws %d < min_sample %d" % (total, min_sample)
        return result

    small = [row["rarity"] for row in result["tiers"] if row["status"] == SMALL_N]
    if small:
        result["verdict"] = V_INSUFFICIENT
        result["reason"] = (
            "tier observations below min_tier_count %d: %s"
            % (min_tier_count, ", ".join(small))
        )
        return result

    over = []
    for row in result["tiers"]:
        if abs(int(round(row["dev_pp"] * 100))) > threshold_bp:
            row["status"] = OVER
            over.append("%s %+.2fpp" % (row["rarity"], row["dev_pp"]))

    if over:
        result["verdict"] = V_DEVIATION
        result["reason"] = (
            "deviation exceeds ±%.2fpp: %s" % (threshold_pp, ", ".join(over))
        )
        return result

    result["verdict"] = V_OK
    result["reason"] = "all tiers within ±%.2fpp" % threshold_pp
    return result


def render(result):
    lines = []
    lines.append("verdict=%s  total_draws=%d  threshold=%s  min_sample=%d"
                 % (result["verdict"], result["total_draws"],
                    ("unset" if result["threshold_pp"] is None
                     else "%.2fpp" % result["threshold_pp"]),
                    result["min_sample"]))
    lines.append("%-14s %10s %10s %10s %10s  %s"
                 % ("rarity", "declared%", "observed", "observed%", "dev_pp",
                    "status"))
    for row in result["tiers"]:
        lines.append("%-14s %10.2f %10d %10.2f %+10.2f  %s"
                     % (row["rarity"], row["declared_pct"],
                        row["observed_count"], row["observed_pct"],
                        row["dev_pp"], row["status"]))
    for row in result["undeclared"]:
        lines.append("%-14s %10s %10d %10s %10s  %s"
                     % (row["rarity"], "-", row["count"], "-", "-", UNDECLARED))
    if result["reason"]:
        lines.append("reason: %s" % result["reason"])
    return "\n".join(lines)


def write_json(path, result):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


# ---------------------------------------------------------------- drill

def _lcg(seed):
    state = [seed & 0xFFFFFFFF]

    def nxt():
        state[0] = (1103515245 * state[0] + 12345) & 0x7FFFFFFF
        return state[0] / float(0x7FFFFFFF)

    return nxt


def _sample(tiers, n, seed):
    """Deterministic draws honouring the declared cumulative distribution."""
    nxt = _lcg(seed)
    cuts = []
    acc = 0
    for rarity, bp in tiers:
        acc += bp
        cuts.append((acc / float(BP), rarity))
    draws = []
    for _ in range(n):
        u = nxt()
        for edge, rarity in cuts:
            if u <= edge:
                draws.append(rarity)
                break
        else:
            draws.append(cuts[-1][1])
    return draws


DRILL_TIERS = [("common", 7000), ("rare", 2500), ("epic", 500)]


def drill():
    """Plant known cases and check the detector names exactly those."""
    cases = []

    honest = _sample(DRILL_TIERS, 4000, 11)
    cases.append(("honest-4000", dict(draws=honest, threshold_pp=3.0),
                  V_OK))

    # Hand-built skew: 80/15/5 over 400 draws = +10pp / -10pp / 0pp.
    skewed = (["common"] * 320 + ["rare"] * 60 + ["epic"] * 20)
    cases.append(("skew-400", dict(draws=skewed, threshold_pp=3.0),
                  V_DEVIATION))

    tiny = _sample(DRILL_TIERS, 12, 12)
    cases.append(("tiny-12", dict(draws=tiny, threshold_pp=3.0),
                  V_INSUFFICIENT))

    cases.append(("no-threshold", dict(draws=honest, threshold_pp=None),
                  V_CANNOT_JUDGE))

    bad_sum = [("common", 7000), ("rare", 2000)]
    cases.append(("bad-sum-90", dict(tiers=bad_sum, draws=honest,
                                     threshold_pp=3.0), V_BAD_DECLARED))

    stranger = honest + ["legendary"]
    cases.append(("undeclared-rarity", dict(draws=stranger, threshold_pp=3.0),
                  V_BAD_DATA))

    failures = 0
    print("gacha probability audit drill (planted vs detected)")
    for name, kwargs, expected in cases:
        tiers = kwargs.pop("tiers", DRILL_TIERS)
        res = audit(tiers=tiers, **kwargs)
        ok = res["verdict"] == expected
        failures += 0 if ok else 1
        print("%-20s expected=%-24s detected=%-24s %s"
              % (name, expected, res["verdict"], "PASS" if ok else "FAIL"))
        if not ok:
            print("   reason: %s" % res["reason"])
    print("drill: %s" % ("OK" if failures == 0 else "%d FAILURE(S)" % failures))
    return 0 if failures == 0 else 1


# ---------------------------------------------------------------- selftest

def selftest():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))
        print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                           (" :: " + detail) if (detail and not cond) else ""))

    tiers = [("a", 5000), ("b", 3000), ("c", 2000)]

    # S1 exact zero deviation passes
    draws = ["a"] * 50 + ["b"] * 30 + ["c"] * 20
    r = audit(tiers, draws, 5.0)
    check("S1 zero deviation -> OK", r["verdict"] == V_OK, r["reason"])

    # S2 deviation beyond threshold is named per tier
    draws = ["a"] * 60 + ["b"] * 25 + ["c"] * 15
    r = audit(tiers, draws, 5.0)
    over = [t["rarity"] for t in r["tiers"] if t["status"] == OVER]
    check("S2 over threshold detected", r["verdict"] == V_DEVIATION
          and over == ["a"], "%s %s" % (r["verdict"], over))

    # S3 exactly at threshold is not a violation (<= semantics)
    draws = ["a"] * 55 + ["b"] * 30 + ["c"] * 15
    r = audit(tiers, draws, 5.0)
    check("S3 boundary at threshold passes", r["verdict"] == V_OK, r["reason"])

    # S4 one basis point past the boundary trips
    draws = ["a"] * 56 + ["b"] * 29 + ["c"] * 15
    r = audit(tiers, draws, 5.0)
    check("S4 one bp past boundary trips", r["verdict"] == V_DEVIATION,
          r["reason"])

    # S5 unset threshold never passes, even with perfect data
    r = audit(tiers, ["a"] * 50 + ["b"] * 30 + ["c"] * 20, None)
    check("S5 unset threshold -> CANNOT-JUDGE",
          r["verdict"] == V_CANNOT_JUDGE, r["verdict"])

    # S6 declared sum != 100% refuses to compare
    r = audit([("a", 7000), ("b", 2000)], ["a"] * 90 + ["b"] * 10, 5.0)
    check("S6 bad declared sum -> BAD-DECLARED",
          r["verdict"] == V_BAD_DECLARED, r["verdict"])

    # S7 empty declaration
    r = audit([], ["a"], 5.0)
    check("S7 empty declaration -> BAD-DECLARED",
          r["verdict"] == V_BAD_DECLARED, r["verdict"])

    # S8 duplicate rarity in the spec is a parse problem
    parsed, problems = parse_declared("a=50,a=30,b=20")
    check("S8 duplicate rarity rejected", not parsed and problems, str(problems))

    # S9 non-numeric percentage rejected
    parsed, problems = parse_declared("a=abc,b=20")
    check("S9 non-numeric p_pct rejected", not parsed and problems,
          str(problems))

    # S10 total below sample floor -> INSUFFICIENT even if skewed
    r = audit(tiers, ["a"] * 9 + ["b"], 1.0)
    check("S10 total sample floor -> INSUFFICIENT",
          r["verdict"] == V_INSUFFICIENT, r["verdict"])

    # S11 per-tier floor -> INSUFFICIENT (does not cry wolf on 2 draws)
    draws = ["a"] * 60 + ["b"] * 38 + ["c"] * 2
    r = audit(tiers, draws, 1.0)
    check("S11 per-tier floor -> INSUFFICIENT",
          r["verdict"] == V_INSUFFICIENT, r["verdict"])

    # S12 undeclared rarity -> BAD-DATA, not silently folded in
    r = audit(tiers, ["a"] * 50 + ["b"] * 30 + ["c"] * 19 + ["z"], 5.0)
    check("S12 undeclared rarity -> BAD-DATA",
          r["verdict"] == V_BAD_DATA and r["undeclared"][0]["rarity"] == "z",
          r["verdict"])

    # S13 bad rows never inflate the denominator silently
    r = audit(tiers, ["a"] * 50 + ["b"] * 30 + ["c"] * 20, 5.0, bad_rows=2)
    check("S13 unreadable rows -> BAD-DATA",
          r["verdict"] == V_BAD_DATA, r["verdict"])

    # S14 zero draws -> INSUFFICIENT, not OK
    r = audit(tiers, [], 5.0)
    check("S14 zero draws -> INSUFFICIENT", r["verdict"] == V_INSUFFICIENT,
          r["verdict"])

    # S15 threshold 0 only accepts an exact match
    r = audit(tiers, ["a"] * 50 + ["b"] * 30 + ["c"] * 20, 0.0)
    check("S15 threshold 0 exact match passes", r["verdict"] == V_OK,
          r["reason"])
    r = audit(tiers, ["a"] * 51 + ["b"] * 30 + ["c"] * 19, 0.0)
    check("S15b threshold 0 any drift trips", r["verdict"] == V_DEVIATION,
          r["reason"])

    # S16 deviation arithmetic is exact in basis points
    r = audit([("a", 5000), ("b", 5000)], ["a"] * 1007 + ["b"] * 993, 1.0)
    dev = [t["dev_pp"] for t in r["tiers"]]
    check("S16 basis-point arithmetic exact",
          dev == [0.35, -0.35], str(dev))

    # S17 determinism
    a = audit(tiers, draws, 1.0)
    b = audit(tiers, list(draws), 1.0)
    check("S17 deterministic", a == b)

    # S18 render carries the verdict and every tier
    text = render(audit(tiers, ["a"] * 50 + ["b"] * 30 + ["c"] * 20, 5.0))
    check("S18 render complete",
          V_OK in text and all(t in text for t, _ in tiers), text[:80])

    failed = [name for name, ok, _ in checks if not ok]
    total = len(checks)
    print("selftest: %d/%d PASS" % (total - len(failed), total))
    return 0 if not failed else 1


# ---------------------------------------------------------------- cli

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Gacha disclosed-vs-observed probability audit (drill)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="audit a draw file against a declaration")
    run.add_argument("--declared", required=True,
                     help="e.g. common=70,rare=25,epic=5 (must sum to 100)")
    run.add_argument("--draws", required=True, help="jsonl draw records")
    run.add_argument("--threshold-pp", type=float, default=None,
                     help="allowed absolute deviation in percentage points; "
                          "omit to report CANNOT-JUDGE (fail-closed)")
    run.add_argument("--min-sample", type=int, default=DEFAULT_MIN_SAMPLE)
    run.add_argument("--min-tier-count", type=int,
                     default=DEFAULT_MIN_TIER_COUNT)
    run.add_argument("--month", default=None, help="filter rows by month")
    run.add_argument("--json-out", default=None)
    run.add_argument("--txt-out", default=None,
                     help="write the table as UTF-8 (console codepage may "
                          "mangle non-ASCII otherwise)")

    sub.add_parser("drill", help="planted cases, detector must name them")
    sub.add_parser("selftest", help="deterministic unit checks")

    fixture = sub.add_parser("fixture", help="write a synthetic draw file")
    fixture.add_argument("--out", required=True)
    fixture.add_argument("--n", type=int, default=1200)
    fixture.add_argument("--seed", type=int, default=77)
    fixture.add_argument("--month", default="2026-11")

    args = parser.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "drill":
        return drill()
    if args.cmd == "fixture":
        draws = _sample(DRILL_TIERS, args.n, args.seed)
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            for rarity in draws:
                fh.write(json.dumps({"rarity": rarity, "month": args.month},
                                    ensure_ascii=False) + "\n")
        print("fixture=%s rows=%d seed=%d month=%s source=drill-synthetic"
              % (args.out, len(draws), args.seed, args.month))
        return 0

    tiers, problems = parse_declared(args.declared)
    if problems:
        print("declaration problems: %s" % "; ".join(problems))
        return EXIT[V_BAD_DECLARED]
    draws, skipped = load_draws(args.draws, month=args.month)
    result = audit(tiers, draws, args.threshold_pp,
                   min_sample=args.min_sample,
                   min_tier_count=args.min_tier_count,
                   bad_rows=skipped)
    print(render(result))
    if args.txt_out:
        os.makedirs(os.path.dirname(args.txt_out) or ".", exist_ok=True)
        with open(args.txt_out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(result) + "\n")
    if args.json_out:
        write_json(args.json_out, result)
        print("json_out=%s" % args.json_out)
    return EXIT[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())