"""batch_pool.py - batch pool stocking registry validator (T-20260929-32).

CEO dispatches O-20260929-007 / O-20260929-013 / O-20260929-019 order
BigCompute to stock the batch pool (备货律: consumer_plan named, no
auto-dispatch; bm-a is on the auto-dispatch blacklist per C-20260929-02
7.4; DRY-RUN observation period runs to 2026-10-05).

Registry: docs/ops/batch-pool-stock-v1.jsonl (committed, CEO-visible).
This tool NEVER dispatches anything - it machine-checks the stocking sheet:
  J1  every card has all required fields non-empty
  J2  City3D/FluxVerse lane cards with category 3d_offline_render >= 2
      (O-20260929-007 hard criterion "3D 离线渲染批 >=2")
  J3  batch_id unique (tx_id idempotency basis)
  J4  consumer_plan non-empty on every card (备货律指名, O-1820 (3) same law)
  J5  no card is in-flight/running (DRY-RUN to 2026-10-05; STOCKED only)
  J6  data_deps local-presence gate (BC-P-37, group precedent
      D-20261004-02 (1) BigMoney F-01 four-shard crash): if a card
      declares data_deps (file/dir list), every path must exist locally
      at validation time; missing = DATA_NOT_LOCAL red gate. Cards with
      no data_deps field are unaffected (zero forced migration).

Commands:
  list      print all cards (lane summary + per-card lines)
  validate  run J1-J5 against the registry, exit 1 on any violation
  gate      pre-dispatch check for one card: consult Tools/lane_gate.py via
            Tools/dispatch_precheck.py (BC-P-144) and fail closed unless the
            card's lane is OPEN (or an explicit EXEMPT reason is given)
  review    monthly DRY-RUN observation review (M66): machine-readable
            snapshot of validate verdict + lane/status stock + consumer_plan
            coverage + dry-run age vs DRY_RUN_END. Writes state JSON.
  selftest  offline fixtures for every check (no registry needed)

Usage:
  python Tools/batch_pool.py validate
  python Tools/batch_pool.py list
  python Tools/batch_pool.py gate --batch-id B-CITY3D-01
  python Tools/batch_pool.py review
  python Tools/batch_pool.py selftest
"""
import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(ROOT, "docs", "ops", "batch-pool-stock-v1.jsonl")

REQUIRED_FIELDS = [
    "batch_id", "lane", "category", "consumer_plan", "order_ref",
    "workload", "resource_profile", "dispatch_mode", "status",
    "acceptance", "stocked_at",
]
IN_FLIGHT_STATUSES = {"IN_FLIGHT", "RUNNING", "DISPATCHED"}
CITY3D_LANE = "City3D/FluxVerse"
CITY3D_CATEGORY = "3d_offline_render"
CITY3D_MIN = 2  # O-20260929-007: >=2 stocked 3D offline render batches
DRY_RUN_END = "2026-10-05"  # J5 observation window end (M46 review package)


def load_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError("line %d: bad JSON: %s" % (lineno, e))
    return rows


def check_rows(rows):
    """Return (problems, info) - problems is a list of violation strings."""
    problems = []
    info = []
    ids = []
    lanes = {}
    city3d_3d = 0
    for i, row in enumerate(rows, 1):
        rid = row.get("batch_id") or "<line %d>" % i
        for field in REQUIRED_FIELDS:
            if not str(row.get(field, "") or "").strip():
                problems.append("J1 %s: missing/empty field '%s'" % (rid, field))
        if row.get("batch_id"):
            ids.append(row["batch_id"])
        if not str(row.get("consumer_plan", "") or "").strip():
            problems.append("J4 %s: consumer_plan empty (备货律指名)" % rid)
        if str(row.get("status", "")).upper() in IN_FLIGHT_STATUSES:
            problems.append(
                "J5 %s: status=%s is in-flight (DRY-RUN to 2026-10-05)"
                % (rid, row.get("status")))
        deps = row.get("data_deps")
        if deps:
            if not isinstance(deps, list):
                problems.append("J6 %s: data_deps must be a list" % rid)
            else:
                for dep in deps:
                    dp = str(dep)
                    target = dp if os.path.isabs(dp) else os.path.join(ROOT, dp)
                    if not os.path.exists(target):
                        problems.append(
                            "J6 %s: DATA_NOT_LOCAL: %s "
                            "(claim-before-dispatch presence assertion, "
                            "D-20261004-02 (1) precedent)" % (rid, dp))
        lane = str(row.get("lane", "") or "?")
        lanes[lane] = lanes.get(lane, 0) + 1
        if lane == CITY3D_LANE and row.get("category") == CITY3D_CATEGORY:
            city3d_3d += 1
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    for d in dupes:
        problems.append("J3 duplicate batch_id: %s (tx_id idempotency basis)" % d)
    if city3d_3d < CITY3D_MIN:
        problems.append(
            "J2 City3D/FluxVerse 3d_offline_render cards=%d < %d "
            "(O-20260929-007 hard criterion)" % (city3d_3d, CITY3D_MIN))
    info.append("cards=%d lanes=%s city3d_3d_offline=%d"
               % (len(rows), json.dumps(lanes, ensure_ascii=False), city3d_3d))
    return problems, info


def cmd_list(path):
    rows = load_rows(path)
    print("batch pool stocking registry: %s" % os.path.relpath(path, ROOT))
    lanes = {}
    for row in rows:
        lanes.setdefault(row.get("lane", "?"), []).append(row)
    for lane, cards in lanes.items():
        print("\n[%s] %d card(s)" % (lane, len(cards)))
        for c in cards:
            print("  - %s (%s) status=%s" % (
                c.get("batch_id"), c.get("category"), c.get("status")))
            print("    consumer_plan: %s" % c.get("consumer_plan"))
            print("    order_ref: %s | dispatch_mode: %s" % (
                c.get("order_ref"), c.get("dispatch_mode")))
    print("\ntotal=%d lanes=%d" % (len(rows), len(lanes)))
    return 0


def cmd_validate(path):
    if not os.path.exists(path):
        print("FAIL registry not found: %s" % path)
        return 1
    rows = load_rows(path)
    problems, info = check_rows(rows)
    for line in info:
        print("INFO " + line)
    if problems:
        for p in problems:
            print("FAIL " + p)
        print("VERDICT=POOL_FAIL (%d problem(s))" % len(problems))
        return 1
    print("VERDICT=POOL_GREEN (J1 fields/J2 city3d>=2/J3 unique/J4 "
          "consumer_plan/J5 dry-run/J6 data_deps-locality all PASS)")
    return 0


def cmd_gate(path, batch_id, consumer=None, exempt=None, as_json=False):
    """Pre-dispatch gate for one card (BC-P-144).

    The registry says what a card *is*, the lane gate says whether this box may
    run it *now*.  Neither answers alone: J5 only forbids in-flight status, and
    lane_gate never saw the pool.  Fail-closed on a closed/stale/unreadable
    lane, and never guess a lane for an unregistered category.
    """
    if not os.path.exists(path):
        print("FAIL registry not found: %s" % path)
        return 1
    rows = load_rows(path)
    card = next((r for r in rows if r.get("batch_id") == batch_id), None)
    if card is None:
        print("FAIL batch_id not found: %s" % batch_id)
        return 64
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import dispatch_precheck
    lane = dispatch_precheck.lane_for_category(card.get("category"))
    if lane is None:
        print("FAIL category '%s' has no pre-registered lane "
              "(BC-P-144: refuse to guess; pass --lane via dispatch_precheck)"
              % card.get("category"))
        return 64
    if str(card.get("status", "")).upper() in IN_FLIGHT_STATUSES:
        print("WARN %s status=%s is in-flight (J5); gate reads resources only"
              % (batch_id, card.get("status")))
    result, exit_code = dispatch_precheck.evaluate(
        lane, consumer or "batch-pool:%s" % batch_id, exempt)
    log_path = dispatch_precheck.append_snapshot(result)
    result["snapshot"] = os.path.relpath(log_path, ROOT)
    result["batch_id"] = batch_id
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("batch-gate: %s category=%s %s"
              % (batch_id, card.get("category"),
                 dispatch_precheck.render_line(result)))
        print("batch-gate: snapshot=%s" % result["snapshot"])
    return exit_code


def build_review(rows, today=None):
    """Assemble the M66 monthly review snapshot from stocked cards.

    Pure (no I/O, no clock unless today is omitted) so selftest can pin dates.
    `today` is a date or None. The dry-run age is the gap between DRY_RUN_END
    and today; a positive age with zero in-flight cards means the J5 gate is
    still the only thing holding every card at STOCKED.
    """
    import datetime
    if today is None:
        today = datetime.date.today()
    problems, info = check_rows(rows)
    lanes, statuses, modes = {}, {}, {}
    named = 0
    for row in rows:
        lanes[str(row.get("lane", "?"))] = \
            lanes.get(str(row.get("lane", "?")), 0) + 1
        st = str(row.get("status", "?")).upper() or "?"
        statuses[st] = statuses.get(st, 0) + 1
        md = str(row.get("dispatch_mode", "?") or "?")
        modes[md] = modes.get(md, 0) + 1
        if str(row.get("consumer_plan", "") or "").strip():
            named += 1
    in_flight = sum(statuses.get(s, 0) for s in IN_FLIGHT_STATUSES)
    end = datetime.date(*[int(x) for x in DRY_RUN_END.split("-")])
    age_days = (today - end).days
    overdue = age_days > 0
    findings = []
    if overdue and in_flight == 0:
        findings.append(
            "J5 dry-run window ended %s (%d days ago) but no card may leave "
            "STOCKED while J5 discards in-flight statuses; activation needs an "
            "explicit J5 revision, not a silent status edit"
            % (DRY_RUN_END, age_days))
    if named < len(rows):
        findings.append(
            "consumer_plan named on %d/%d cards (备货律指名)" % (named, len(rows)))
    if problems:
        findings.append("validate reported %d problem(s)" % len(problems))
    return {
        "cards": len(rows),
        "lanes": lanes,
        "statuses": statuses,
        "dispatch_modes": modes,
        "consumer_plan_named": named,
        "in_flight": in_flight,
        "dry_run_end": DRY_RUN_END,
        "dry_run_age_days": age_days,
        "dry_run_overdue": overdue,
        "j5_blocks_activation": bool(overdue and in_flight == 0),
        "problems": problems,
        "info": info,
        "findings": findings,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def cmd_review(path, as_json=False, write=True):
    if not os.path.exists(path):
        print("FAIL registry not found: %s" % path)
        return 1
    rows = load_rows(path)
    review = build_review(rows)
    review["registry"] = os.path.relpath(path, ROOT)
    print("REVIEW cards=%d lanes=%d named=%d in_flight=%d dry_run_end=%s "
          "overdue=%s j5_blocks_activation=%s"
          % (review["cards"], len(review["lanes"]),
             review["consumer_plan_named"], review["in_flight"],
             review["dry_run_end"], review["dry_run_overdue"],
             review["j5_blocks_activation"]))
    for f in review["findings"]:
        print("FINDING " + f)
    if as_json:
        print(json.dumps(review, ensure_ascii=False, indent=2))
    review_exit = 1 if review["problems"] else 0
    print("VERDICT=%s" % ("POOL-REVIEW-FAIL" if review_exit
                          else "POOL-REVIEW-GREEN"))
    if write:
        stamp = review["generated_at"].replace("-", "").replace(":", "") \
            .replace(" ", "-")
        out = os.path.join(ROOT, "state", "batch-pool-review-%s.json" % stamp)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(review, f, ensure_ascii=False, indent=2)
        print("evidence=%s" % os.path.relpath(out, ROOT))
    return review_exit


def _fixture(base=None, **over):
    row = {
        "batch_id": "B-T-01", "lane": CITY3D_LANE, "category":
        CITY3D_CATEGORY, "consumer_plan": "cp", "order_ref": "O-x",
        "workload": "w", "resource_profile": "r", "dispatch_mode":
        "ON_DEMAND", "status": "STOCKED", "acceptance": "a",
        "stocked_at": "t",
    }
    if base:
        row.update(base)
    row.update(over)
    return row


def cmd_selftest():
    # J1: missing field detected / valid fixture passes the field check
    bad = check_rows([_fixture(workload="")])[0]
    assert any("J1" in p and "workload" in p for p in bad), "J1 miss"
    # J2: one City3D 3d card fails, two pass
    assert any("J2" in p for p in check_rows([_fixture()])[0]), "J2 <2 miss"
    ok = [_fixture(), _fixture(base={"batch_id": "B-T-02"})]
    assert not any("J2" in p for p in check_rows(ok)[0]), "J2 >=2 false fail"
    # J3: duplicate id detected
    dup = check_rows([_fixture(), _fixture()])[0]
    assert any("J3" in p for p in dup), "J3 dup miss"
    # J4: empty consumer_plan detected
    bad_cp = check_rows([_fixture(consumer_plan="  ")])[0]
    assert any("J4" in p for p in bad_cp), "J4 miss"
    # J5: in-flight status detected, STOCKED passes
    bad_st = check_rows([_fixture(status="IN_FLIGHT")])[0]
    assert any("J5" in p for p in bad_st), "J5 miss"
    assert not any("J5" in p for p in check_rows([_fixture()])[0]), \
        "J5 false positive"
    # J6: no data_deps -> unaffected; existing dep passes; missing dep
    # red-gates; non-list type rejected
    pair = [_fixture(), _fixture(base={"batch_id": "B-T-02"})]
    assert not any("J6" in p for p in check_rows(pair)[0]), \
        "J6 false positive (no data_deps)"
    dep_ok = check_rows([_fixture(), _fixture(base={
        "batch_id": "B-T-02", "data_deps": ["Tools/batch_pool.py"]})])[0]
    assert not any("J6" in p for p in dep_ok), "J6 false positive (exists)"
    dep_bad = check_rows([_fixture(), _fixture(base={
        "batch_id": "B-T-02",
        "data_deps": ["state/no-such-dep-dir-xyz/"]})])[0]
    assert any("J6" in p and "DATA_NOT_LOCAL" in p for p in dep_bad), \
        "J6 miss (DATA_NOT_LOCAL)"
    dep_typ = check_rows([_fixture(), _fixture(base={
        "batch_id": "B-T-02", "data_deps": "Tools/batch_pool.py"})])[0]
    assert any("J6" in p and "list" in p for p in dep_typ), "J6 type miss"
    # determinism: same rows -> same problems
    r1 = check_rows([_fixture(), _fixture()])[0]
    r2 = check_rows([_fixture(), _fixture()])[0]
    assert r1 == r2, "determinism"
    # gate (BC-P-144): lane mapping is explicit and unknown categories are
    # refused rather than guessed
    import dispatch_precheck
    assert dispatch_precheck.lane_for_category(CITY3D_CATEGORY) == "gpu", \
        "gate lane map (gpu)"
    assert dispatch_precheck.lane_for_category("cpu_batch") == "cpu", \
        "gate lane map (cpu)"
    assert dispatch_precheck.lane_for_category("no_such_category") is None, \
        "gate unknown category must not be guessed"
    # review (M66): stock snapshot + dry-run age are pinned by fixtures
    import datetime
    pair = [_fixture(), _fixture(base={"batch_id": "B-T-02"})]
    rev = build_review(pair, today=datetime.date(2026, 10, 20))
    assert rev["cards"] == 2 and rev["consumer_plan_named"] == 2, "review counts"
    assert rev["in_flight"] == 0 and rev["statuses"].get("STOCKED") == 2, \
        "review status tally"
    assert rev["dry_run_age_days"] == 15 and rev["dry_run_overdue"], \
        "review dry-run age"
    assert rev["j5_blocks_activation"], "review must flag J5 blocker"
    assert any("J5 dry-run window ended" in f for f in rev["findings"]), \
        "review finding text"
    early = build_review(pair, today=datetime.date(2026, 10, 1))
    assert not early["dry_run_overdue"] and not early["j5_blocks_activation"], \
        "review pre-end must not flag blocker"
    flying = build_review(
        [_fixture(), _fixture(base={"batch_id": "B-T-02", "status": "RUNNING"})],
        today=datetime.date(2026, 10, 20))
    assert flying["in_flight"] == 1 and not flying["j5_blocks_activation"], \
        "review must not claim J5 blocks when a card is already in-flight"
    assert any("J5" in p for p in flying["problems"]), "review carries validate"
    r1 = build_review(pair, today=datetime.date(2026, 10, 20))
    r2 = build_review(pair, today=datetime.date(2026, 10, 20))
    r1.pop("generated_at")
    r2.pop("generated_at")
    assert r1 == r2, "review determinism"
    print("selftest PASS (J1-J6 + gate lane map + review + determinism, %d checks)"
          % 20)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_l = sub.add_parser("list", help="print all pool cards")
    p_l.add_argument("--registry", default=REGISTRY)
    p_v = sub.add_parser("validate", help="machine-check the stocking sheet")
    p_v.add_argument("--registry", default=REGISTRY)
    p_g = sub.add_parser("gate", help="pre-dispatch lane gate for one card")
    p_g.add_argument("--batch-id", required=True)
    p_g.add_argument("--registry", default=REGISTRY)
    p_g.add_argument("--consumer", help="who is dispatching (audit field)")
    p_g.add_argument("--exempt", help="exemption reason (artifact-arrival paths)")
    p_g.add_argument("--json", action="store_true")
    p_r = sub.add_parser("review", help="M66 monthly dry-run review snapshot")
    p_r.add_argument("--registry", default=REGISTRY)
    p_r.add_argument("--json", action="store_true")
    p_r.add_argument("--no-write", action="store_true",
                     help="print only, skip state/batch-pool-review-*.json")
    sub.add_parser("selftest", help="offline fixture checks")
    args = ap.parse_args()
    if args.cmd == "list":
        return cmd_list(args.registry)
    if args.cmd == "validate":
        return cmd_validate(args.registry)
    if args.cmd == "gate":
        return cmd_gate(args.registry, args.batch_id, args.consumer,
                        args.exempt, args.json)
    if args.cmd == "review":
        return cmd_review(args.registry, args.json, not args.no_write)
    return cmd_selftest()


if __name__ == "__main__":
    sys.exit(main())
