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
  reminder-gap  scan every known rail for a reminder field: declared in schema
            (EVIDENCED) vs written-but-undeclared (UNDECLARED-IN-LIVE) vs
            absent everywhere (MISSING-IN-SCHEMA). §六 L59 requires the renewal
            reminder to be recorded on the subscription rail.
  guard-census  every rail must carry a class (real / drill / non-money) and
            every real rail must pin mock=false, live=true and <flag>=false;
            a missing guard is named here rather than surfacing later as a
            contaminated reading (T202 / BC-P-193).
  selftest  schema self-checks + fixture pass/fail assertions
  dump      print the schemas as JSON

Usage: python Tools/rails_schema_check.py check|census|c1|reminder-gap|guard-census|selftest|dump
"""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]

RAIL_PATHS = {
    "mock_order": ROOT / "state" / "mock-order-ledger.jsonl",
    "aftersale": ROOT / "state" / "aftersale-ledger.jsonl",
    "aftersale_mock": ROOT / "state" / "mock-aftersale-ledger.jsonl",
    "batch_card": ROOT / "docs" / "ops" / "batch-pool-stock-v1.jsonl",
    "distr": ROOT / "state" / "distr-ledger.jsonl",
    "delivery": ROOT / "state" / "delivery-ledger.jsonl",
    "gacha_draw": ROOT / "state" / "gacha-draw-ledger.jsonl",
    "gacha_draw_mock": ROOT / "state" / "mock-gacha-draw-ledger.jsonl",
}

# Idempotency keys: repeats bypass dedup and corrupt reconciliation.
# An order may legitimately carry several refund rows, so aftersale dedups on
# the refund event id, not on order_id.
UNIQUE_KEYS = {
    "mock_order": "order_id", "batch_card": "batch_id", "distr": "tx_id",
    "delivery": "event_id", "aftersale": "aftersale_id",
    "aftersale_mock": "aftersale_id",
    "gacha_draw": "draw_id", "gacha_draw_mock": "draw_id",
}

# Absent-by-design rails (J4 planned state) are not violations.
# gacha_draw (real) is absent until the blind-box SKU is live; the drill rail
# is not optional — a missing drill rail means the audit has no data surface.
OPTIONAL_RAILS = {"distr", "delivery", "aftersale", "gacha_draw"}

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
            # M200/M202: 渠道与口径由 regen 从 lane 派生——演练行也要能回答
            # 「哪条线卖出去了多少」，否则三线分面在数据面永远 unassigned。
            # b2b=直签声明值（读侧映射 private/b2b 派生行，不新增消费线）。
            "channel": {"type": "string",
                        "enum": ["livestream", "store", "private", "b2b"]},
            "settlement_caliber": {"type": "string", "minLength": 1},
            "channel_note": {"type": "string", "minLength": 2},
            "route_group": {
                "enum": ["resident-qa-8792", "batch-pool", "direct-outbox"],
            },
            # 成本演练标记（M114 普查新抓）：state/mock-order-ledger.jsonl 里有三行
            # 带这四个键，写入脚本未入库，additionalProperties 一直静默放行。
            # 这四个键的语义是「这张单的成本数是编的」，属诚实标记而非业务数据，
            # 因此声明保留而不是抹掉——写入者来源记在 PRODUCERS 里如实可见。
            "amount_mtok": {"type": "number", "minimum": 0},
            "cost_rate_cny_per_mtok": {"type": "number", "minimum": 0},
            "cost_simulated": {"type": "boolean"},
            "denominator_simulated": {"type": "boolean"},
        },
        "additionalProperties": True,
    },
    # After-sale / refund rails (M114). Two separate rails so a drill row can
    # never be mistaken for a real refund:
    #   aftersale       real refunds     (refund_simulated=false, live=true)
    #   aftersale_mock  synthetic drills (refund_simulated=true, live=false)
    # refund_amount is the money actually returned; the calibration drill
    # (Tools/refund_rate_calibration.py) sums it per order and compares against
    # price*refund_rate. Without this pair of rails it can only answer
    # REFUND-CANNOT-JUDGE ("refunds not recorded" vs "no refunds").
    "aftersale": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "aftersale_id", "order_id", "date", "refund_amount",
            "refund_reason", "refund_simulated", "live", "caliber_note",
        ],
        "properties": {
            "aftersale_id": {"type": "string", "pattern": "^AF-[0-9]{8}-[0-9]+$"},
            # a real refund never points at a MOCK- order
            "order_id": {"type": "string", "pattern": "^(?!MOCK-).+"},
            "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
            "refund_amount": {"type": "number", "exclusiveMinimum": 0},
            "refund_reason": {"type": "string", "minLength": 3},
            "refund_simulated": {"const": False},
            "live": {"const": True},
            # T202：additionalProperties 会静默放行 `mock: true`（演练行混进真轨），
            # 真轨因此欠一条诚实闸。不设 required（平台导出未必带这列），
            # 但只要出现就必须是 false——钱与演练的分界不能靠写入侧自觉。
            "mock": {"const": False},
            "caliber_note": {"type": "string", "minLength": 2},
        },
        "additionalProperties": True,
    },
    "aftersale_mock": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "aftersale_id", "order_id", "date", "refund_amount",
            "refund_reason", "refund_simulated", "live", "mock",
            "caliber_note",
        ],
        "properties": {
            "aftersale_id": {"type": "string", "pattern": "^MOCK-AF-"},
            "order_id": {"type": "string", "pattern": "^MOCK-"},
            "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
            "refund_amount": {"type": "number", "exclusiveMinimum": 0},
            "refund_reason": {"type": "string", "minLength": 3},
            "refund_simulated": {"const": True},
            "live": {"const": False},
            "mock": {"const": True},
            "caliber_note": {"type": "string", "minLength": 2},
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
    # Gacha draw rails (M116). §7.3 (1) "monthly sampled deviation" had a
    # detector but no data surface: gacha_probability_audit could only ever
    # answer PROB-AUDIT-CANNOT-JUDGE because no draw log existed. Two rails
    # again, so a drill row can never be counted as a real draw:
    #   gacha_draw       real draws     (draw_simulated=false, live=true)
    #   gacha_draw_mock  synthetic rows (draw_simulated=true, live=false)
    # `user` is structurally anonymized (pattern ^anon-): a raw account id
    # cannot be written here at all, so the draw rail never becomes a PII rail.
    "gacha_draw": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "draw_id", "user", "sku_id", "rarity", "price", "month",
            "draw_simulated", "live", "caliber_note",
        ],
        "properties": {
            "draw_id": {"type": "string", "pattern": "^GD-[0-9]{8}-[0-9]+$"},
            "user": {"type": "string", "pattern": "^anon-"},
            "sku_id": {"type": "string", "minLength": 3},
            "rarity": {"type": "string", "minLength": 2},
            # money per draw in CNY; 0 is not allowed (a free draw is not a sale)
            "price": {"type": "number", "exclusiveMinimum": 0},
            "month": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"},
            "draw_simulated": {"const": False},
            "live": {"const": True},
            # drill rows carry mock=true; the real rail must refuse them even
            # though additionalProperties would otherwise let the key through
            "mock": {"const": False},
            "caliber_note": {"type": "string", "minLength": 2},
        },
        "additionalProperties": True,
    },
    "gacha_draw_mock": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "draw_id", "user", "sku_id", "rarity", "price", "month",
            "draw_simulated", "live", "mock", "caliber_note",
        ],
        "properties": {
            "draw_id": {"type": "string", "pattern": "^MOCK-GD-"},
            "user": {"type": "string", "pattern": "^anon-"},
            "sku_id": {"type": "string", "minLength": 3},
            "rarity": {"type": "string", "minLength": 2},
            "price": {"type": "number", "exclusiveMinimum": 0},
            "month": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"},
            "draw_simulated": {"const": True},
            "live": {"const": False},
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
        # 写入脚本曾未入库（T135 普查漏网·M114 复查抓出·M133 收口）：现由
        # unit_cost_drill.regen_mock_rows 推导（design rate 0.60 CNY/Mtok），
        # 可复跑、可普查；值仍是演练设计值，不是结算价。
        "amount_mtok": "unit_cost_drill.regen_mock_rows (M133)",
        "cost_rate_cny_per_mtok": "unit_cost_drill.regen_mock_rows (M133)",
        "cost_simulated": "unit_cost_drill.regen_mock_rows (M133)",
        "denominator_simulated": "unit_cost_drill.regen_mock_rows (M133)",
        # M200: channel/caliber 同为 regen 派生（lane -> 渠道表 + 口径 B），
        # 理由同 M133——值必须可复跑，历史行不手改。
        "channel": "unit_cost_drill.regen_mock_rows (M200)",
        "settlement_caliber": "unit_cost_drill.regen_mock_rows (M200)",
        "channel_note": "unit_cost_drill.regen_mock_rows (M200)",
    },
    "aftersale": {
        "aftersale_id": "manual:平台后台退款流水导出（计划态未接·M114 schema 先行）",
        "order_id": "manual:平台后台退款流水导出（计划态未接·M114 schema 先行）",
        "date": "manual:平台后台退款流水导出（计划态未接·M114 schema 先行）",
        "refund_amount": "manual:平台后台退款流水导出（计划态未接·M114 schema 先行）",
        "refund_reason": "manual:平台后台退款流水导出（计划态未接·M114 schema 先行）",
        "refund_simulated": "manual:平台后台退款流水导出（计划态未接·M114 schema 先行）",
        "live": "manual:平台后台退款流水导出（计划态未接·M114 schema 先行）",
        "mock": "manual:平台后台退款流水导出（计划态未接·M114 schema 先行）",
        "caliber_note": "manual:平台后台退款流水导出（计划态未接·M114 schema 先行）",
    },
    "aftersale_mock": {
        "aftersale_id": "state/mock-aftersale-ledger.jsonl (M114 drill rows)",
        "order_id": "state/mock-aftersale-ledger.jsonl (M114 drill rows)",
        "date": "state/mock-aftersale-ledger.jsonl (M114 drill rows)",
        "refund_amount": "state/mock-aftersale-ledger.jsonl (M114 drill rows)",
        "refund_reason": "state/mock-aftersale-ledger.jsonl (M114 drill rows)",
        "refund_simulated": "state/mock-aftersale-ledger.jsonl (M114 drill rows)",
        "live": "state/mock-aftersale-ledger.jsonl (M114 drill rows)",
        "mock": "state/mock-aftersale-ledger.jsonl (M114 drill rows)",
        "caliber_note": "state/mock-aftersale-ledger.jsonl (M114 drill rows)",
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
    "gacha_draw": {
        "draw_id": "manual:平台后台抽卡流水导出（计划态未接·M116 schema 先行）",
        "user": "manual:平台后台抽卡流水导出（计划态未接·M116 schema 先行）",
        "sku_id": "manual:平台后台抽卡流水导出（计划态未接·M116 schema 先行）",
        "rarity": "manual:平台后台抽卡流水导出（计划态未接·M116 schema 先行）",
        "price": "manual:平台后台抽卡流水导出（计划态未接·M116 schema 先行）",
        "month": "manual:平台后台抽卡流水导出（计划态未接·M116 schema 先行）",
        "draw_simulated": "manual:平台后台抽卡流水导出（计划态未接·M116 schema 先行）",
        "live": "manual:平台后台抽卡流水导出（计划态未接·M116 schema 先行）",
        "mock": "manual:平台后台抽卡流水导出（计划态未接·M116 schema 先行）",
        "caliber_note": "manual:平台后台抽卡流水导出（计划态未接·M116 schema 先行）",
    },
    "gacha_draw_mock": {
        "draw_id": "gacha_probability_audit.rails_drill",
        "user": "gacha_probability_audit.rails_drill",
        "sku_id": "gacha_probability_audit.rails_drill",
        "rarity": "gacha_probability_audit.rails_drill",
        "price": "gacha_probability_audit.rails_drill",
        "month": "gacha_probability_audit.rails_drill",
        "draw_simulated": "gacha_probability_audit.rails_drill",
        "live": "gacha_probability_audit.rails_drill",
        "mock": "gacha_probability_audit.rails_drill",
        "caliber_note": "gacha_probability_audit.rails_drill",
    },
}


# Rails that exist on disk with real writers but whose field contract is not
# expressed in SCHEMAS yet. Listed explicitly so a GREEN census cannot be read
# as "every rail is gated" (T136 step 2).
OUT_OF_CENSUS = {
    "cost": {
        "path": "state/cost-ledger.jsonl",
        "writer": "cost_ledger.py add / quota-* commands",
        "reason": "real cost rail (caliber B settlement), 30+ field contract; schema migration deferred to the T-23 window",
    },
    "cost_l1": {
        "path": "state/cost-l1-ledger.jsonl",
        "writer": "cost_ledger.py L1 lane commands",
        "reason": "co-tenant L1 consumption mirror; field contract owned by the L1 lane producer",
    },
    "borrow_windows": {
        "path": "state/borrow-windows.jsonl",
        "writer": "manual BC-P-62 ledger (gpu_idle_collector reads it)",
        "reason": "hand-maintained borrow window ledger, E23 predesign (J4 planned state)",
    },
    "clean_window_log": {
        "path": "state/clean-window-log.jsonl",
        "writer": "clean_window_probe.py",
        "reason": "probe sample log, not business rows",
    },
    "vram_window_log": {
        "path": "state/vram-window-log.jsonl",
        "writer": "vram_window_probe.py",
        "reason": "probe sample log (E17 window history), not business rows",
    },
}


def census_coverage(schemas=None, out_of_census=None) -> list[str]:
    """Bookkeeping for the coverage claim: gated rails and out-of-census rails
    must be disjoint and every exclusion must carry a reason."""
    schemas = SCHEMAS if schemas is None else schemas
    out_of_census = OUT_OF_CENSUS if out_of_census is None else out_of_census
    problems: list[str] = []
    for kind in sorted(set(schemas) & set(out_of_census)):
        problems.append(f"rail '{kind}' is both gated and listed out-of-census")
    for kind, entry in sorted(out_of_census.items()):
        if not entry.get("reason"):
            problems.append(f"out-of-census rail '{kind}' has no reason")
    return problems


# T202 (BC-P-193) honesty-guard census. `additionalProperties: true` keeps
# schema evolution cheap, but it also lets a drill row walk into a real rail
# unless the row's own honesty flags are pinned by `const`. Every schema must
# therefore declare a class here:
#   real      — holds real money; owes three guards (mock:false, live:true,
#               <flag>:false). Absent with no other class = red gate.
#   drill     — a MOCK rail by construction, guards pinned the other way.
#   non-money — carries no cash column; the reason must be written down.
# A rail with no class is reported, not silently passed: the whole point is
# that "no guard" must never look like "not applicable".
REAL_RAIL_GUARDS = {
    "aftersale": {"flag": "refund_simulated", "mock": False, "live": True},
    "gacha_draw": {"flag": "draw_simulated", "mock": False, "live": True},
}

DRILL_RAILS = {"mock_order", "aftersale_mock", "gacha_draw_mock", "delivery"}

NON_MONEY_RAILS = {
    "batch_card": "batch pool work card: no price/refund/token column at all",
    "distr": "attribution rail: `live` is a planned-state marker, not a cash guard",
}


def guard_status(kind: str, schemas=None) -> list[str]:
    """Return the missing/wrong guards for one real rail (empty = guarded)."""
    schemas = SCHEMAS if schemas is None else schemas
    spec = REAL_RAIL_GUARDS.get(kind)
    if spec is None:
        return []
    props = schemas.get(kind, {}).get("properties", {})
    expected = {"mock": spec["mock"], "live": spec["live"], spec["flag"]: False}
    gaps = []
    for field, value in expected.items():
        rule = props.get(field)
        if not isinstance(rule, dict) or "const" not in rule:
            gaps.append(f"{field} (no const)")
        elif rule["const"] is not value:
            gaps.append(f"{field} (const={rule['const']!r}, expected {value!r})")
    return gaps


def guard_census(schemas=None, real=None, drills=None, non_money=None) -> list[str]:
    """Every declared rail must carry exactly one class, and real rails must
    own all three honesty guards. Missing guard = GUARD-MISSING, named."""
    schemas = SCHEMAS if schemas is None else schemas
    real = REAL_RAIL_GUARDS if real is None else real
    drills = DRILL_RAILS if drills is None else drills
    non_money = NON_MONEY_RAILS if non_money is None else non_money
    problems: list[str] = []
    classes = (("real", real), ("drill", drills), ("non-money", non_money))
    for kind in sorted(schemas):
        hits = [name for name, members in classes if kind in members]
        if not hits:
            problems.append(
                f"GUARD-UNCLASSIFIED rail '{kind}' has no class "
                "(real/drill/non-money) — unguarded is not the same as exempt")
        elif len(hits) > 1:
            problems.append(f"rail '{kind}' is classified twice: {hits}")
        elif kind in real:
            for gap in guard_status(kind, schemas):
                problems.append(f"GUARD-MISSING rail '{kind}' field {gap}")
    for kind in sorted((set(real) | set(drills) | set(non_money)) - set(schemas)):
        problems.append(f"rail '{kind}' is classified but not declared in SCHEMAS")
    for kind, reason in sorted(non_money.items()):
        if not reason:
            problems.append(f"non-money rail '{kind}' has no reason")
    return problems


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
    for kind, entry in sorted(OUT_OF_CENSUS.items()):
        print(f"out-of-census {kind}: {entry['path']} ({entry['writer']}) - {entry['reason']}")
    problems.extend(census_coverage())
    for line in problems:
        print(f"CENSUS {line}")
    verdict = "RAILS-CENSUS-GREEN" if not problems else "RAILS-CENSUS-RED"
    print(f"VERDICT={verdict} problems={len(problems)} rows={total_rows} rails={','.join(covered)}")
    return 0 if not problems else 1


REMINDER_KEY_RE = re.compile(r"remind", re.IGNORECASE)


def _rail_relpath(kind: str) -> str:
    path = RAIL_PATHS.get(kind)
    if path is None:
        return "-"
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:  # pragma: no cover - ROOT is the parent of every rail
        return path.as_posix()


def cmd_guard_census(json_out=None) -> int:
    """T202: assert every rail is classified and every real rail owns its three
    honesty guards — before a drill row has a chance to pollute a real reel."""
    problems = guard_census()
    findings = []
    for kind in sorted(SCHEMAS):
        if kind in REAL_RAIL_GUARDS:
            gaps = guard_status(kind)
            status = "GUARD-MISSING" if gaps else "GUARDED"
            spec = REAL_RAIL_GUARDS[kind]
            detail = ", ".join(gaps) or (
                f"mock=false, live=true, {spec['flag']}=false")
        elif kind in DRILL_RAILS:
            status, detail = "DRILL-MOCK", "drill rail: mock/simulated consts by construction"
        elif kind in NON_MONEY_RAILS:
            status, detail = "NON-MONEY", NON_MONEY_RAILS[kind]
        else:
            status, detail = "UNCLASSIFIED", "no class declared in guard census"
        findings.append({"rail": kind, "status": status, "detail": detail,
                         "path": _rail_relpath(kind)})
        print("%-16s %-15s %s" % (kind, status, detail))
    for line in problems:
        print("GUARD {}".format(line))
    verdict = "GUARD-CENSUS-GREEN" if not problems else "GUARD-CENSUS-GAPS"
    guarded = sum(1 for f in findings if f["status"] == "GUARDED")
    print("VERDICT=%s rails=%d guarded=%d problems=%d" % (
        verdict, len(findings), guarded, len(problems)))
    if json_out:
        payload = {
            "verdict": verdict,
            "counts": {"rails": len(findings), "guarded": guarded,
                       "problems": len(problems)},
            "real_rails": sorted(REAL_RAIL_GUARDS),
            "drill_rails": sorted(DRILL_RAILS),
            "non_money_rails": sorted(NON_MONEY_RAILS),
            "problems": problems,
            "findings": findings,
        }
        with open(json_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        print("json=%s" % json_out)
    return 0 if not problems else 1

# Rails that hold subscription-cycle rows owe a reminder field (§六 L59:
# 续费提醒触发记录入流水轨). Kept explicit so a NEW rail cannot silently be
# exempt — the scan reports "no subscription rail declared" instead.
SUBSCRIPTION_RAILS = {"mock_order"}


def reminder_fields(mapping) -> list[str]:
    """Keys in a schema `properties` map or a live row that look like reminders."""
    return sorted(k for k in mapping if REMINDER_KEY_RE.search(str(k)))


def reminder_gap_scan():
    """Per-rail reminder-field presence: declared vs live-written.

    States:
      EVIDENCED             declared in schema and (if live rows exist) written
      UNDECLARED-IN-LIVE    written by a producer but not declared -> contract bypass
      DECLARED-UNWRITTEN    declared but no live row carries it yet (planned)
      MISSING-IN-SCHEMA     neither declared nor written
    """
    findings = []
    known = [(kind, path, True) for kind, path in RAIL_PATHS.items()]
    for kind, entry in sorted(OUT_OF_CENSUS.items()):
        known.append((kind, ROOT / entry["path"], False))

    for kind, path, gated in known:
        declared = reminder_fields(
            SCHEMAS.get(kind, {}).get("properties", {})) if gated else []
        live_written, live_rows = [], 0
        if path.exists():
            rows = load_rows(path)
            live_rows = len(rows)
            seen = set()
            for row in rows:
                seen.update(reminder_fields(row))
            live_written = sorted(seen)

        if declared and live_written:
            status = "EVIDENCED"
        elif live_written and not declared:
            status = "UNDECLARED-IN-LIVE"
        elif declared:
            status = "DECLARED-UNWRITTEN"
        else:
            status = "MISSING-IN-SCHEMA"

        findings.append({
            "rail": kind,
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "gated": gated,
            "live_rows": live_rows,
            "declared": declared,
            "live_written": live_written,
            "status": status,
        })
    return findings


def cmd_reminder_gap(json_out=None) -> int:
    findings = reminder_gap_scan()
    undeclared = [f for f in findings if f["status"] == "UNDECLARED-IN-LIVE"]
    evidenced = [f for f in findings if f["status"] == "EVIDENCED"]
    missing = [f for f in findings if f["status"] == "MISSING-IN-SCHEMA"]
    subscription_declared = sorted(
        f["rail"] for f in findings
        if f["gated"] and f["rail"] in SUBSCRIPTION_RAILS)

    for f in findings:
        print("%-12s %-22s gated=%s rows=%d declared=%s live=%s (%s)" % (
            f["rail"], f["status"], "yes" if f["gated"] else "no",
            f["live_rows"], ",".join(f["declared"]) or "-",
            ",".join(f["live_written"]) or "-", f["path"]))

    if undeclared:
        verdict = "REMINDER-SCHEMA-RED"
    elif missing:
        verdict = "REMINDER-SCHEMA-GAP"
    else:
        verdict = "REMINDER-SCHEMA-EVIDENCED"
    print("subscription rails declared: %s" % (
        ",".join(subscription_declared) or "NONE (订阅流水轨尚未在 SCHEMAS 定义)"))
    print("VERDICT=%s evidenced=%d undeclared=%d missing=%d rails=%d" % (
        verdict, len(evidenced), len(undeclared), len(missing), len(findings)))

    if json_out:
        payload = {
            "verdict": verdict,
            "anchor": "§六 L59 续费提醒触发记录入流水轨",
            "subscription_rails": subscription_declared,
            "counts": {"evidenced": len(evidenced),
                       "undeclared": len(undeclared),
                       "missing": len(missing)},
            "findings": findings,
        }
        with open(json_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        print("json=%s" % json_out)
    # A reported gap is the honest result, not an infrastructure failure; only
    # a live write bypassing the declared contract is a red gate.
    return 1 if undeclared else 0


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
    # M114: the aftersale pair must keep drill money and real money apart.
    good_aftersale_mock = {
        "aftersale_id": "MOCK-AF-20261019-1", "order_id": "MOCK-20261019-R1",
        "date": "2026-10-19", "refund_amount": 4.0,
        "refund_reason": "selftest drill", "refund_simulated": True,
        "live": False, "mock": True,
        "caliber_note": "synthetic aftersale drill row, not a real refund",
    }
    checks.append(("good aftersale_mock row accepted",
                   validate_rows("aftersale_mock", [good_aftersale_mock]) == []))
    checks.append(("aftersale_mock rejects real order id",
                   validate_rows("aftersale_mock",
                                 [dict(good_aftersale_mock, order_id="R-1")]) != []))
    checks.append(("aftersale_mock rejects live=true",
                   validate_rows("aftersale_mock",
                                 [dict(good_aftersale_mock, live=True)]) != []))
    good_aftersale = {
        "aftersale_id": "AF-20261019-1", "order_id": "R-20261019-9",
        "date": "2026-10-19", "refund_amount": 12.5,
        "refund_reason": "user request", "refund_simulated": False,
        "live": True, "caliber_note": "real refund rail row",
    }
    checks.append(("good aftersale row accepted",
                   validate_rows("aftersale", [good_aftersale]) == []))
    checks.append(("aftersale rejects MOCK order id",
                   any("order_id" in str(e) for e in validate_rows(
                       "aftersale", [dict(good_aftersale, order_id="MOCK-X")]))))
    checks.append(("dup aftersale_id caught",
                   len(find_duplicate_keys(
                       "aftersale_mock",
                       [good_aftersale_mock, good_aftersale_mock])) == 1))
    checks.append(("census green on aftersale fixtures",
                   census("aftersale", [good_aftersale]) == []
                   and census("aftersale_mock", [good_aftersale_mock]) == []))
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
    # M116 gacha draw rails: a drill row must not satisfy the real rail, and a
    # raw account id must be structurally impossible on either rail.
    good_draw_mock = {
        "draw_id": "MOCK-GD-20261104-1", "user": "anon-selftest",
        "sku_id": "SKU-MOCK-CARD", "rarity": "common", "price": 2.0,
        "month": "2026-11", "draw_simulated": True, "live": False,
        "mock": True, "caliber_note": "synthetic draw drill row, not a sale",
    }
    checks.append(("good gacha_draw_mock row accepted",
                   validate_rows("gacha_draw_mock", [good_draw_mock]) == []))
    checks.append(("gacha_draw rejects MOCK draw id",
                   validate_rows("gacha_draw", [dict(
                       good_draw_mock, draw_id="GD-20261104-1",
                       draw_simulated=False, live=True)]) != []))
    checks.append(("gacha_draw rejects raw user id (PII guard)",
                   validate_rows("gacha_draw", [dict(
                       good_draw_mock, draw_id="GD-20261104-1", user="u_88231",
                       draw_simulated=False, live=True)]) != []))
    checks.append(("gacha_draw_mock rejects simulated=false",
                   validate_rows("gacha_draw_mock",
                                 [dict(good_draw_mock, draw_simulated=False)]) != []))
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
    # T136 coverage bookkeeping: gated vs explicitly excluded, never both.
    checks.append(("coverage: gated and out-of-census disjoint",
                   census_coverage() == []))
    checks.append(("coverage catches double-listed rail",
                   any("both gated" in p for p in census_coverage(
                       schemas={"dup": {}}, out_of_census={"dup": {"reason": "x"}}))))
    checks.append(("coverage catches exclusion without reason",
                   any("no reason" in p for p in census_coverage(
                       schemas={}, out_of_census={"x": {}}))))
    # M113 ② reminder-field scan: the matcher must see a reminder key, and an
    # undeclared live write must be distinguished from an absent field.
    checks.append(("reminder matcher sees reminder_at/reminders",
                   reminder_fields({"reminder_at": 1, "reminders": [], "ts": 2})
                   == ["reminder_at", "reminders"]))
    checks.append(("reminder matcher ignores unrelated keys",
                   reminder_fields({"order_id": "x", "remind_me": 0}) == ["remind_me"]))
    fixture_findings = reminder_gap_scan()
    checks.append(("reminder-gap scan covers every known rail",
                   {f["rail"] for f in fixture_findings}
                   == set(RAIL_PATHS) | set(OUT_OF_CENSUS)))
    checks.append(("reminder-gap statuses restricted to known set",
                   all(f["status"] in ("EVIDENCED", "UNDECLARED-IN-LIVE",
                                       "DECLARED-UNWRITTEN", "MISSING-IN-SCHEMA")
                       for f in fixture_findings)))
    checks.append(("reminder-gap: no declared rail field yet => no EVIDENCED",
                   not any(f["status"] == "EVIDENCED" for f in fixture_findings)))
    # T202 honesty guards: a real rail must refuse a drill row outright, and the
    # census must name a missing guard instead of waiting for a polluting row.
    checks.append(("guard census green on declared schemas", guard_census() == []))
    checks.append(("aftersale rejects mock=true drill row",
                   validate_rows("aftersale", [dict(good_aftersale, mock=True)]) != []))
    checks.append(("gacha_draw rejects mock=true drill row",
                   validate_rows("gacha_draw", [dict(
                       good_draw_mock, draw_id="GD-20261104-2",
                       draw_simulated=False, live=True, mock=True)]) != []))
    stripped = {"aftersale": {"type": "object", "properties": {
        "refund_simulated": {"const": False}, "live": {"const": True}}}}
    missing_guards = guard_census(schemas=stripped)
    checks.append(("guard census names missing mock guard",
                   any("GUARD-MISSING" in p and "'aftersale'" in p and "mock" in p
                       for p in missing_guards)))
    unclassified = guard_census(schemas={"mystery_rail": {"properties": {}}})
    checks.append(("guard census names unclassified rail",
                   any("GUARD-UNCLASSIFIED" in p and "mystery_rail" in p
                       for p in unclassified)))
    checks.append(("guard census catches wrong const value",
                   any("live (const=False" in p for p in guard_census(
                       schemas={"aftersale": {"properties": {
                           "mock": {"const": False},
                           "live": {"const": False},
                           "refund_simulated": {"const": False}}}}))))
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
    if cmd == "reminder-gap":
        json_out = argv[2] if len(argv) > 2 else None
        return cmd_reminder_gap(json_out)
    if cmd == "guard-census":
        json_out = argv[2] if len(argv) > 2 else None
        return cmd_guard_census(json_out)
    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "dump":
        print(json.dumps(SCHEMAS, indent=2, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))