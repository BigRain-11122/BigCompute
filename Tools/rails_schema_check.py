#!/usr/bin/env python3
"""Declarative JSON-Schema gate for BigCompute JSONL rails.

Reuses the group-adopted `jsonschema` (python-jsonschema, MIT; group registry
cph4/README.md 「开源采用·JSON Schema 结构校验」 row, originally adopted by
BigLife). No ad-hoc field-by-field checks: rail shapes live here as schemas,
so a field rename or type drift surfaces as a schema violation instead of a
silent downstream misread.

Rails covered:
  mock_order  state/mock-order-ledger.jsonl   (M73 MOCK ring rails)
  batch_card  docs/ops/batch-pool-stock-v1.jsonl (T-20260929-32 batch pool)
  distr       state/distr-ledger.jsonl        (private-domain attribution,
              building-plan §七 15-field pre-design; file absent = PLANNED)
  delivery    state/delivery-ledger.jsonl     (ring-1 stage-5 delivery dispatch
              event chain, building-plan §二/三; file absent = PLANNED)

Commands:
  check     validate live rail files (read-only)
  census    write-side field census on live rails: written keys must be
            declared, declared keys must name a producer (BC-P-116 / T135)
  c1        C1 inspection aggregate: this gate + ledger_continuity_probe
            (single invocation for the P1 loop inspection step)
  selftest  schema self-checks + fixture pass/fail assertions
  dump      print the schemas as JSON

Usage: python Tools/rails_schema_check.py check|census|c1|selftest|dump
"""

from __future__ import annotations

import json
import sys
from importlib import metadata
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]

RAIL_PATHS = {
    "mock_order": ROOT / "state" / "mock-order-ledger.jsonl",
    "batch_card": ROOT / "docs" / "ops" / "batch-pool-stock-v1.jsonl",
    "distr": ROOT / "state" / "distr-ledger.jsonl",
    "delivery": ROOT / "state" / "delivery-ledger.jsonl",
}

# Idempotency keys: repeats bypass dedup and corrupt reconciliation.
UNIQUE_KEYS = {
    "mock_order": "order_id", "batch_card": "batch_id", "distr": "tx_id",
    "delivery": "event_id",
}

# Absent-by-design rails (J4 planned state) are not violations.
OPTIONAL_RAILS = {"distr", "delivery"}

SCHEMAS = {
    # MOCK rail: every row must be visibly mock (mock=true, MOCK- order id) and
    # carry the arithmetic inputs the ledger reconciliation needs.
    "mock_order": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "order_id", "sku", "price", "net_price", "gross", "lane",
            "date", "cost_item", "mock", "fund_3090", "refund_rate",
            "fee_rate", "tokens_local", "tokens_api",
        ],
        "properties": {
            "order_id": {"type": "string", "pattern": "^MOCK-"},
            "sku": {"type": "string", "minLength": 3},
            "price": {"type": "number", "exclusiveMinimum": 0},
            "net_price": {"type": "number", "minimum": 0},
            "gross": {"type": "number", "minimum": 0},
            "lane": {"type": "string", "minLength": 2},
            "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
            "cost_item": {"type": "string", "minLength": 2},
            "mock": {"const": True},
            # MOCK drill rail never touches the real 3090 purchase fund.
            "fund_3090": {"const": 0},
            "refund_rate": {"type": "number", "minimum": 0, "maximum": 1},
            "fee_rate": {"type": "number", "minimum": 0, "maximum": 1},
            "tokens_local": {"type": "integer", "minimum": 0},
            "tokens_api": {"type": "integer", "minimum": 0},
            # T135 写入侧普查：deal_flow_mock 一直在写这四键而 schema 从未声明，
            # additionalProperties 静默放行。补声明=把契约补给写入侧（不是放宽闸）。
            "tier": {"type": "string", "minLength": 1},
            # 演练单恒 0（与 fund_3090 同律：MOCK 轨不累计金额）
            "amount": {"type": "number", "minimum": 0},
            "api_reason": {"type": "string", "minLength": 2},
            "route_group": {
                "enum": ["resident-qa-8792", "batch-pool", "direct-outbox"],
            },
        },
        "additionalProperties": True,
    },
    # Batch pool cards: consumer_plan must name a consumer (J4), status is a
    # closed vocabulary, batch ids are scoped to the B- namespace.
    "batch_card": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "batch_id", "lane", "category", "consumer_plan", "order_ref",
            "workload", "resource_profile", "dispatch_mode", "status",
            "acceptance", "stocked_at",
        ],
        "properties": {
            "batch_id": {"type": "string", "pattern": "^B-[A-Za-z0-9_-]+$"},
            "lane": {"type": "string", "minLength": 2},
            "category": {"type": "string", "minLength": 3},
            "consumer_plan": {"type": "string", "minLength": 10},
            "order_ref": {"type": "string", "minLength": 4},
            "workload": {"type": "string", "minLength": 5},
            "resource_profile": {"type": "string", "minLength": 5},
            "dispatch_mode": {"type": "string", "minLength": 4},
            "status": {"enum": ["STOCKED", "IN_FLIGHT", "DONE", "PARKED"]},
            "acceptance": {"type": "string", "minLength": 5},
            "stocked_at": {"type": "string", "minLength": 10},
        },
        "additionalProperties": True,
    },
    # Private-domain attribution rail (building-plan §七, 15 fields). G1 gate:
    # the six attribution keys are structurally required, no partial rows.
    "distr": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "tx_id", "ts", "consumer", "lane", "content_ref", "publish_state",
            "utm_source", "utm_medium", "utm_campaign", "attribution_status",
            "receipt_basis", "caliber_note",
        ],
        "properties": {
            "tx_id": {"type": "string", "pattern": "^D-[0-9]{8}-[0-9]+$"},
            "ts": {"type": "string", "minLength": 10},
            "consumer": {"type": "string", "minLength": 2},
            "lane": {"type": "string", "minLength": 2},
            "content_ref": {"type": "string", "minLength": 3},
            "publish_state": {"enum": ["queued", "published"]},
            "utm_source": {"type": "string", "minLength": 1},
            "utm_medium": {"type": "string", "minLength": 1},
            "utm_campaign": {"type": "string", "minLength": 1},
            "audience_seg": {"type": "string"},
            "conv_tx_ref": {"type": "string"},
            "attribution_status": {"enum": ["pending", "attributed"]},
            "reach_count": {"type": "integer", "minimum": 0},
            # 真实发布面标记=计划态恒 False（month_end_collect S8 读它做 live 行计数；
            # 必须在此声明，否则解析侧字段成手抄关系·T134 单源对读闸）
            "live": {"type": "boolean"},
            "receipt_basis": {"type": "string", "minLength": 2},
            "caliber_note": {"type": "string", "minLength": 2},
        },
        "additionalProperties": True,
    },
    # Delivery dispatch event chain (ring-1 stage-5). Append-only: one row per
    # transition, deduped by event_id, so the status chain is auditable. Only
    # MOCK orders may be walked (J4); the mock const makes a real row fail here.
    "delivery": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "event_id", "delivery_id", "order_id", "route_group", "status",
            "ts", "attempts", "receipt_ref", "mock", "caliber_note",
        ],
        "properties": {
            "event_id": {"type": "string", "pattern": "^E-[0-9]{8}-[0-9]+$"},
            "delivery_id": {"type": "string", "pattern": "^DV-[0-9]{8}-[0-9]+$"},
            "order_id": {"type": "string", "pattern": "^MOCK-"},
            "route_group": {
                "enum": ["resident-qa-8792", "batch-pool", "direct-outbox"],
            },
            "status": {"enum": ["queued", "dispatched", "delivered"]},
            "ts": {"type": "string", "minLength": 10},
            "attempts": {"type": "integer", "minimum": 0},
            # 同 distr：month_end_collect S9 读 live 做计划态行计数（T134 单源对读闸）
            "live": {"type": "boolean"},
            "receipt_ref": {"type": "string"},
            "mock": {"const": True},
            "caliber_note": {"type": "string", "minLength": 2},
        },
        "additionalProperties": True,
    },
}


# Write-side census (BC-P-116 / T135): every declared field must name the tool
# that actually writes it. A declared key with no producer is a dead contract; a
# written key with no declaration is a silent bypass of `additionalProperties`.
# Readers (month_end_collect) are tracked separately by PARSER_KEYS, so the two
# faces — "who writes it" and "who reads it" — stay independently auditable.
PRODUCERS = {
    "mock_order": {
        "order_id": "deal_flow_mock.ledger_write",
        "sku": "deal_flow_mock.ledger_write",
        "tier": "deal_flow_mock.ledger_write",
        "price": "deal_flow_mock.ledger_write",
        "net_price": "deal_flow_mock.ledger_write",
        "gross": "deal_flow_mock.ledger_write",
        "amount": "deal_flow_mock.ledger_write",
        "lane": "deal_flow_mock.ledger_write",
        "date": "deal_flow_mock.ledger_write",
        "cost_item": "deal_flow_mock.ledger_write",
        "mock": "deal_flow_mock.ledger_write",
        "fund_3090": "deal_flow_mock.ledger_write",
        "refund_rate": "deal_flow_mock.ledger_write",
        "fee_rate": "deal_flow_mock.ledger_write",
        "tokens_local": "deal_flow_mock.ledger_write",
        "tokens_api": "deal_flow_mock.ledger_write",
        "api_reason": "deal_flow_mock.ledger_write",
        "route_group": "deal_flow_mock.ledger_write",
    },
    "batch_card": {
        "batch_id": "manual:docs/ops/batch-pool-stock-v1.jsonl",
        "lane": "manual:docs/ops/batch-pool-stock-v1.jsonl",
        "category": "manual:docs/ops/batch-pool-stock-v1.jsonl",
        "consumer_plan": "manual:docs/ops/batch-pool-stock-v1.jsonl",
        "order_ref": "manual:docs/ops/batch-pool-stock-v1.jsonl",
        "workload": "manual:docs/ops/batch-pool-stock-v1.jsonl",
        "resource_profile": "manual:docs/ops/batch-pool-stock-v1.jsonl",
        "dispatch_mode": "manual:docs/ops/batch-pool-stock-v1.jsonl",
        "status": "manual:docs/ops/batch-pool-stock-v1.jsonl",
        "acceptance": "manual:docs/ops/batch-pool-stock-v1.jsonl",
        "stocked_at": "manual:docs/ops/batch-pool-stock-v1.jsonl",
    },
    "distr": {
        "tx_id": "distr_flow_mock.run_row",
        "ts": "distr_flow_mock.run_row",
        "consumer": "distr_flow_mock.run_row",
        "lane": "distr_flow_mock.run_row",
        "content_ref": "distr_flow_mock.run_row",
        "publish_state": "distr_flow_mock.run_row",
        "utm_source": "distr_flow_mock.run_row",
        "utm_medium": "distr_flow_mock.run_row",
        "utm_campaign": "distr_flow_mock.run_row",
        "audience_seg": "distr_flow_mock.run_row",
        "conv_tx_ref": "distr_flow_mock.run_row",
        "attribution_status": "distr_flow_mock.run_row",
        "reach_count": "distr_flow_mock.run_row",
        "live": "distr_flow_mock.run_row",
        "receipt_basis": "distr_flow_mock.run_row",
        "caliber_note": "distr_flow_mock.run_row",
    },
    "delivery": {
        "event_id": "delivery_dispatch_mock._event",
        "delivery_id": "delivery_dispatch_mock._event",
        "order_id": "delivery_dispatch_mock._event",
        "route_group": "delivery_dispatch_mock._event",
        "status": "delivery_dispatch_mock._event",
        "ts": "delivery_dispatch_mock._event",
        "attempts": "delivery_dispatch_mock._event",
        "receipt_ref": "delivery_dispatch_mock._event",
        "mock": "delivery_dispatch_mock._event",
        "live": "delivery_dispatch_mock._event",
        "caliber_note": "delivery_dispatch_mock._event",
    },
}


def census(kind: str, rows: list[dict]) -> list[str]:
    """Two-way write-side census for one rail.

    - forward: every key actually written must be declared in the schema
      (`additionalProperties: true` would otherwise let a rename slip through)
    - backward: every declared key must name a producer in PRODUCERS
    """
    declared = set(SCHEMAS[kind]["properties"])
    problems: list[str] = []
    for idx, row in enumerate(rows, 1):
        undeclared = sorted(set(row) - declared)
        if undeclared:
            problems.append(f"{kind}[{idx}] undeclared write keys: {undeclared}")
    producers = PRODUCERS.get(kind, {})
    for field in sorted(declared - set(producers)):
        problems.append(f"{kind} declared field '{field}' has no producer")
    for field in sorted(set(producers) - declared):
        problems.append(f"{kind} producer '{field}' is not a declared field")
    return problems


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{lineno}: not JSON ({exc})") from exc
    return rows


def validate_rows(kind: str, rows: list[dict]) -> list[str]:
    validator = Draft202012Validator(SCHEMAS[kind])
    errors = []
    for idx, row in enumerate(rows, 1):
        for err in sorted(validator.iter_errors(row), key=lambda e: list(e.path)):
            where = "/".join(str(p) for p in err.path) or "<row>"
            errors.append(f"{kind}[{idx}] {row.get('order_id') or row.get('batch_id') or '?'} @{where}: {err.message}")
    return errors


def find_duplicate_keys(kind: str, rows: list[dict]) -> list[str]:
    key = UNIQUE_KEYS[kind]
    seen: dict[str, int] = {}
    dups = []
    for idx, row in enumerate(rows, 1):
        rid = str(row.get(key, ""))
        if rid in seen:
            dups.append(f"{kind}[{idx}] duplicate {key}={rid} (first at row {seen[rid]})")
        else:
            seen[rid] = idx
    return dups


def cmd_check() -> int:
    total_rows = 0
    all_errors: list[str] = []
    missing = []
    skipped = []
    for kind, path in RAIL_PATHS.items():
        if not path.exists():
            if kind in OPTIONAL_RAILS:
                skipped.append(kind)
                print(f"{kind}: absent (planned state, optional rail)")
                continue
            missing.append(f"{kind}: file missing ({path.name})")
            continue
        rows = load_rows(path)
        total_rows += len(rows)
        errs = validate_rows(kind, rows)
        errs.extend(find_duplicate_keys(kind, rows))
        all_errors.extend(errs)
        print(f"{kind}: {len(rows)} rows, {len(errs)} violations ({path.name})")
    for line in missing:
        print(f"MISSING {line}")
    print(f"jsonschema {metadata.version('jsonschema')}")
    if all_errors or missing:
        for line in all_errors:
            print(f"VIOLATION {line}")
        print(f"VERDICT=RAILS-SCHEMA-RED violations={len(all_errors)} missing={len(missing)} rows={total_rows}")
        return 1
    print(f"VERDICT=RAILS-SCHEMA-GREEN violations=0 rows={total_rows} skipped={len(skipped)}")
    return 0


def cmd_census() -> int:
    """Live write-side census across rail files present on disk (read-only)."""
    problems: list[str] = []
    covered = []
    total_rows = 0
    for kind, path in RAIL_PATHS.items():
        if not path.exists():
            print(f"{kind}: absent (planned state, skipped)")
            continue
        rows = load_rows(path)
        total_rows += len(rows)
        errs = census(kind, rows)
        problems.extend(errs)
        covered.append(kind)
        print(f"{kind}: {len(rows)} rows, {len(errs)} census problems ({path.name})")
    for line in problems:
        print(f"CENSUS {line}")
    verdict = "RAILS-CENSUS-GREEN" if not problems else "RAILS-CENSUS-RED"
    print(f"VERDICT={verdict} problems={len(problems)} rows={total_rows} rails={','.join(covered)}")
    return 0 if not problems else 1


def cmd_c1() -> int:
    """C1 inspection aggregate: structure gate + ledger continuity in one call."""
    schema_rc = cmd_check()
    print("-" * 20)
    try:
        import ledger_continuity_probe as lcp
    except Exception as exc:  # pragma: no cover - import failure is a hard fail
        print(f"C1: ledger_continuity_probe import failed: {exc}")
        print("VERDICT=C1-RED")
        return 1
    result, evidence = lcp.run_check()
    print("%s violations=%d rail_rows=%s" % (
        result["verdict"], result["violation_count"], result["rail_rows"]))
    print("evidence=%s" % Path(evidence).name)
    continuity_ok = result["verdict"] != "LEDGER-CONTINUITY-RED"
    ok = schema_rc == 0 and continuity_ok
    print(f"VERDICT={'C1-GREEN' if ok else 'C1-RED'}")
    return 0 if ok else 1


def cmd_selftest() -> int:
    checks: list[tuple[str, bool]] = []
    bad_row = {
        "order_id": "R1-NOT-MOCK", "sku": "X", "price": -1.0, "net_price": 0.0,
        "gross": 0.0, "lane": "", "date": "20261019", "cost_item": "",
        "mock": False, "fund_3090": 0.0, "refund_rate": 1.5, "fee_rate": -0.1,
        "tokens_local": -5, "tokens_api": 0,
    }
    checks.append(("bad mock row rejected", len(validate_rows("mock_order", [bad_row])) >= 8))
    good_batch = {
        "batch_id": "B-SELFTEST-01", "lane": "lane/x", "category": "selftest",
        "consumer_plan": "selftest consumer names itself", "order_ref": "O-TEST",
        "workload": "selftest workload", "resource_profile": "selftest profile",
        "dispatch_mode": "ON_DEMAND", "status": "STOCKED",
        "acceptance": "selftest acceptance", "stocked_at": "2026-10-20T03:00+08:00",
    }
    checks.append(("good batch card accepted", validate_rows("batch_card", [good_batch]) == []))
    bad_batch = dict(good_batch, batch_id="X-1", status="RUNNING", consumer_plan="short")
    bad_errors = validate_rows("batch_card", [bad_batch])
    checks.append(("bad batch card rejected (3 fields)", len(bad_errors) >= 3))
    checks.append(("deterministic rerun", validate_rows("batch_card", [bad_batch]) == bad_errors))
    checks.append(("schemas are draft 2020-12", all(s["$schema"].endswith("2020-12/schema") for s in SCHEMAS.values())))
    checks.append(("schemas compile", all(Draft202012Validator.check_schema(s) or True for s in SCHEMAS.values())))
    good_mock = {
        "order_id": "MOCK-20261020-S1", "sku": "MOCK:sku", "price": 1.0,
        "net_price": 0.9, "gross": 1.0, "lane": "subscription",
        "date": "2026-10-20", "cost_item": "MOCK-DRILL", "mock": True,
        "fund_3090": 0, "refund_rate": 0.0, "fee_rate": 0.01,
        "tokens_local": 0, "tokens_api": 0,
        # T135: the four keys deal_flow_mock has always written
        "tier": "T1", "amount": 0.0, "api_reason": "mock-drill",
        "route_group": "direct-outbox",
    }
    checks.append(("good mock row accepted", validate_rows("mock_order", [good_mock]) == []))
    checks.append(("mock fund_3090 non-zero rejected",
                   any("fund_3090" in str(e) for e in
                       validate_rows("mock_order", [dict(good_mock, fund_3090=0.1)]))))
    checks.append(("dup order_id caught",
                   len(find_duplicate_keys("mock_order", [good_mock, good_mock])) == 1))
    good_distr = {
        "tx_id": "D-20261020-1", "ts": "2026-10-20T06:10:00+08:00",
        "consumer": "bigstream", "lane": "bilibili",
        "content_ref": "media/clip-01.mp4", "publish_state": "queued",
        "utm_source": "bili", "utm_medium": "video", "utm_campaign": "c1",
        "audience_seg": "fans", "conv_tx_ref": "Q-1", "attribution_status": "pending",
        "reach_count": 0, "receipt_basis": "synthetic-drill",
        "caliber_note": "behavior-attribution rail, not cost rail (caliber B separate)",
    }
    checks.append(("good distr row accepted", validate_rows("distr", [good_distr]) == []))
    bad_distr = {k: v for k, v in good_distr.items() if k != "utm_campaign"}
    checks.append(("distr missing G1 key rejected",
                   any("utm_campaign" in str(e) for e in validate_rows("distr", [bad_distr]))))
    checks.append(("distr bad tx_id rejected",
                   validate_rows("distr", [dict(good_distr, tx_id="X-1")]) != []))
    checks.append(("dup distr tx_id caught",
                   len(find_duplicate_keys("distr", [good_distr, good_distr])) == 1))
    good_delivery = {
        "event_id": "E-20261025-1", "delivery_id": "DV-20261025-1",
        "order_id": "MOCK-20261019-R1", "route_group": "resident-qa-8792",
        "status": "queued", "ts": "2026-10-25T22:50:00", "attempts": 0,
        "receipt_ref": "", "mock": True,
        "caliber_note": "delivery audit rail, not a revenue row (caliber B)",
    }
    checks.append(("good delivery row accepted",
                   validate_rows("delivery", [good_delivery]) == []))
    checks.append(("delivery real order rejected",
                   validate_rows("delivery", [dict(good_delivery, order_id="R-1")]) != []))
    checks.append(("delivery bad status rejected",
                   validate_rows("delivery", [dict(good_delivery, status="shipped")]) != []))
    checks.append(("dup delivery event caught",
                   len(find_duplicate_keys("delivery", [good_delivery, good_delivery])) == 1))
    # T135 write-side census: declared <-> produced must line up both ways.
    checks.append(("census green on fixture rows",
                   census("mock_order", [good_mock]) == []
                   and census("distr", [good_distr]) == []
                   and census("delivery", [good_delivery]) == []
                   and census("batch_card", [good_batch]) == []))
    checks.append(("census catches undeclared write key",
                   any("undeclared write keys" in p and "mystery" in p
                       for p in census("distr", [dict(good_distr, mystery=1)]))))
    saved_producer = PRODUCERS["distr"].pop("utm_campaign")
    try:
        orphan = census("distr", [good_distr])
    finally:
        PRODUCERS["distr"]["utm_campaign"] = saved_producer
    checks.append(("census catches declared field without producer",
                   any("utm_campaign" in p and "no producer" in p for p in orphan)))
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    failed = sum(1 for _, ok in checks if not ok)
    print(f"selftest {len(checks) - failed}/{len(checks)} PASS")
    return 0 if failed == 0 else 1


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "check":
        return cmd_check()
    if cmd == "census":
        return cmd_census()
    if cmd == "c1":
        return cmd_c1()
    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "dump":
        print(json.dumps(SCHEMAS, indent=2, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))