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

Commands:
  list      print all cards (lane summary + per-card lines)
  validate  run J1-J5 against the registry, exit 1 on any violation
  selftest  offline fixtures for every check (no registry needed)

Usage:
  python Tools/batch_pool.py validate
  python Tools/batch_pool.py list
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
          "consumer_plan/J5 dry-run all PASS)")
    return 0


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
    # determinism: same rows -> same problems
    r1 = check_rows([_fixture(), _fixture()])[0]
    r2 = check_rows([_fixture(), _fixture()])[0]
    assert r1 == r2, "determinism"
    print("selftest PASS (J1-J5 + determinism, %d checks)"
          % 7)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_l = sub.add_parser("list", help="print all pool cards")
    p_l.add_argument("--registry", default=REGISTRY)
    p_v = sub.add_parser("validate", help="machine-check the stocking sheet")
    p_v.add_argument("--registry", default=REGISTRY)
    sub.add_parser("selftest", help="offline fixture checks")
    args = ap.parse_args()
    if args.cmd == "list":
        return cmd_list(args.registry)
    if args.cmd == "validate":
        return cmd_validate(args.registry)
    return cmd_selftest()


if __name__ == "__main__":
    sys.exit(main())
