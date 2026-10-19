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

Commands:
  check     validate live rail files (read-only)
  selftest  schema self-checks + fixture pass/fail assertions
  dump      print the schemas as JSON

Usage: python Tools/rails_schema_check.py check|selftest|dump
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
}

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
            "fund_3090": {"type": "number", "minimum": 0},
            "refund_rate": {"type": "number", "minimum": 0, "maximum": 1},
            "fee_rate": {"type": "number", "minimum": 0, "maximum": 1},
            "tokens_local": {"type": "integer", "minimum": 0},
            "tokens_api": {"type": "integer", "minimum": 0},
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
}


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


def cmd_check() -> int:
    total_rows = 0
    all_errors: list[str] = []
    missing = []
    for kind, path in RAIL_PATHS.items():
        if not path.exists():
            missing.append(f"{kind}: file missing ({path.name})")
            continue
        rows = load_rows(path)
        total_rows += len(rows)
        errs = validate_rows(kind, rows)
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
    print(f"VERDICT=RAILS-SCHEMA-GREEN violations=0 rows={total_rows}")
    return 0


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
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    failed = sum(1 for _, ok in checks if not ok)
    print(f"selftest {len(checks) - failed}/{len(checks)} PASS")
    return 0 if failed == 0 else 1


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "check":
        return cmd_check()
    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "dump":
        print(json.dumps(SCHEMAS, indent=2, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))