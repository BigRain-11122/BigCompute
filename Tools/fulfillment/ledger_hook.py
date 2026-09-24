#!/usr/bin/env python3
"""Rolling ledger hook: fulfillment rows for the group cost ledger.

Every successful fulfillment appends JSONL rows to the group rolling
ledger file, format-compatible with the Tools/cost_ledger.py JSONL
(same row schema and field names):

  date / order_id / sku / cost_item / tier / tokens_local / tokens_api /
  api_reason / amount_cny

Per fulfillment two rows are written, both with the full schema:
  - cost_item "tokens": the token trio (tokens_local / tokens_api /
    api_reason) with the api token cost as amount_cny;
  - cost_item "cost": the total fulfillment cost estimate; the tier
    field carries the L1/L2/L3 routing classification of that run.

Idempotency: rows are keyed by (order_id, cost_item); replaying the
same fulfillment appends nothing.

Cost estimation defaults come from research R-20260924 (2026-09-24
snapshot): GLM-4.6 cloud at 2/8 CNY per 1M in/out tokens (blended 5.0
when the in/out split is unavailable); local 7b full-load generation
about 0.02-0.05 CNY per run; L1 deterministic assembly has no marginal
cost. Stub adapters carry zero tokens, so MVP rows are 0.0 CNY until
real adapters report usage.

Discipline: zero network, zero secrets. ASCII only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

FIELDS = (
    "date",
    "order_id",
    "sku",
    "cost_item",
    "tier",
    "tokens_local",
    "tokens_api",
    "api_reason",
    "amount_cny",
)

COST_ITEM_TOKENS = "tokens"
COST_ITEM_COST = "cost"

# price constants (research R-20260924, 2026-09-24 snapshot)
L3_CNY_PER_MTOK = 5.0       # GLM-4.6 blended (2 in / 8 out per 1M)
L2_LOCAL_CNY_PER_RUN = 0.03 # local 7b full-load run, electricity only
L1_LOCAL_CNY_PER_RUN = 0.0  # deterministic lookup/assembly: no model


def today_str(now: Optional[datetime] = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d")


def read_rows(ledger_path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.exists(ledger_path):
        return rows
    with open(ledger_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def has_row(ledger_path: str, order_id: str, cost_item: str) -> bool:
    for row in read_rows(ledger_path):
        if (row.get("order_id") == order_id
                and row.get("cost_item") == cost_item):
            return True
    return False


def append_row(
    ledger_path: str,
    order_id: str,
    sku: str,
    cost_item: str,
    tier: str,
    tokens_local: int,
    tokens_api: int,
    api_reason: str,
    amount_cny: float,
    date: Optional[str] = None,
) -> int:
    """Append one ledger row; idempotent per (order_id, cost_item).
    Returns 1 when a row was written, 0 when the key already exists."""
    if has_row(ledger_path, order_id, cost_item):
        return 0
    row = {
        "date": date or today_str(),
        "order_id": order_id,
        "sku": sku,
        "cost_item": cost_item,
        "tier": tier,
        "tokens_local": int(tokens_local),
        "tokens_api": int(tokens_api),
        "api_reason": api_reason,
        "amount_cny": round(float(amount_cny), 6),
    }
    directory = os.path.dirname(ledger_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    return 1


def estimate_api_cost_cny(tier: str, tokens_api: int) -> float:
    if tier != "L3" or not tokens_api:
        return 0.0
    return tokens_api / 1000000.0 * L3_CNY_PER_MTOK


def estimate_local_cost_cny(tier: str) -> float:
    if tier == "L2":
        return L2_LOCAL_CNY_PER_RUN
    return L1_LOCAL_CNY_PER_RUN


def _usage_fields(artifact: Any) -> Tuple[str, int, int, str]:
    """Extract (tier, tokens_local, tokens_api, api_reason) from an
    artifact's usage record; accepts an attribute object or a dict."""
    usage = getattr(artifact, "usage", None)
    if usage is None:
        return "L1", 0, 0, "no_usage_reported"
    if isinstance(usage, dict):
        return (
            str(usage.get("tier", "L1")),
            int(usage.get("tokens_local", 0)),
            int(usage.get("tokens_api", 0)),
            str(usage.get("api_reason", "")),
        )
    return (
        str(getattr(usage, "tier", "L1")),
        int(getattr(usage, "tokens_local", 0)),
        int(getattr(usage, "tokens_api", 0)),
        str(getattr(usage, "api_reason", "")),
    )


def record_fulfillment(ledger_path: str, order: Any, artifact: Any) -> int:
    """Write the tokens row and the cost row for one fulfilled order.

    Duck-typed on purpose: needs order.order_id / order.sku and an
    artifact carrying a usage record (attribute object or dict). Returns
    the number of rows appended (0 when both rows already exist)."""
    order_id = str(getattr(order, "order_id", ""))
    sku = str(getattr(order, "sku", ""))
    tier, tokens_local, tokens_api, api_reason = _usage_fields(artifact)
    api_cost = estimate_api_cost_cny(tier, tokens_api)
    total_cost = api_cost + estimate_local_cost_cny(tier)
    written = 0
    written += append_row(
        ledger_path, order_id, sku, COST_ITEM_TOKENS, tier,
        tokens_local, tokens_api, api_reason, api_cost,
    )
    written += append_row(
        ledger_path, order_id, sku, COST_ITEM_COST, tier,
        tokens_local, tokens_api, api_reason, total_cost,
    )
    return written
