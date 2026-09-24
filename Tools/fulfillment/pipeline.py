#!/usr/bin/env python3
"""Fulfillment pipeline orchestration core (MVP skeleton, account-agnostic).

Design basis: docs/research/R-20260924-tech-pipeline.md, "MVP pipeline
design draft" section. This module is the account-independent skeleton:
all platform/account specifics live behind three adapter interfaces
(OrderSource / Generator / Deliverer). When real accounts arrive, only
the adapters get swapped - this core is not rewritten.

Slice of the nine-step data flow implemented here:
  poll orders (pull mode) -> idempotent intake -> generation ->
  delivery with receipt -> group rolling ledger rows.

Failure policy (rights first):
  - generation/delivery failures retry with a total attempt cap of
    DEFAULT_MAX_ATTEMPTS (<= 3 per the design doc); exhausted orders
    park in the manual fallback queue;
  - any confirmed order unresolved longer than DEFAULT_REFUND_HOURS
    (24h hard cap) is flagged for platform refund BEFORE any more
    compute is spent on it.

Idempotency: order_id is the single key. Re-polling or re-running adds
zero new tasks, zero duplicate receipts and zero duplicate ledger rows.

Status vocabulary extends the design-doc state machine:
  pending -> generating -> done | failed | manual_queue | refund_flagged
(manual_queue = retries exhausted, awaiting a human; refund_flagged =
the doc's "refunded" terminal marker raised for platform-side refund).

Discipline: zero network calls, zero secrets, zero popups. ASCII only.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import ledger_hook

SCHEMA_VERSION = 1

STATUS_PENDING = "pending"
STATUS_GENERATING = "generating"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_MANUAL = "manual_queue"
STATUS_REFUND = "refund_flagged"

# statuses that still owe the buyer a result (refund sweep watches these)
REFUNDABLE_STATUSES = (
    STATUS_PENDING,
    STATUS_GENERATING,
    STATUS_FAILED,
    STATUS_MANUAL,
)
# statuses eligible for a generation attempt
WORKING_STATUSES = (STATUS_PENDING, STATUS_FAILED)

DEFAULT_MAX_ATTEMPTS = 3   # design doc: auto-retry <= 3, then human queue
DEFAULT_REFUND_HOURS = 24  # design doc: 24h hard cap -> proactive refund


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    if not value:
        return utc_now()
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def sha16(text: str) -> str:
    return hashlib.sha1(text.encode("ascii", "replace")).hexdigest()[:16]


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("ascii", "replace")).hexdigest()


def sanitize_name(text: str) -> str:
    allowed = set(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789-_"
    )
    cleaned = "".join(ch if ch in allowed else "_" for ch in text)
    return cleaned or "unnamed"


def atomic_write_json(path: str, data: Any) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directory or ".")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ---------------------------------------------------------------------------
# order model (task-table row, design doc component #4)
# ---------------------------------------------------------------------------

@dataclass
class Order:
    """One fulfillment task. order_id is the platform order id and the
    single idempotency key used everywhere (intake, delivery, ledger).
    created_at proxies payment time while confirm_flag is True."""

    order_id: str
    sku: str
    qty: int = 1
    status: str = STATUS_PENDING
    created_at: str = field(default_factory=lambda: iso(utc_now()))
    confirm_flag: bool = True
    attempts: int = 0
    artifact_id: str = ""
    receipt_id: str = ""
    refund_reason: str = ""
    last_error: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "sku": self.sku,
            "qty": self.qty,
            "status": self.status,
            "created_at": self.created_at,
            "confirm_flag": self.confirm_flag,
            "attempts": self.attempts,
            "artifact_id": self.artifact_id,
            "receipt_id": self.receipt_id,
            "refund_reason": self.refund_reason,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Order":
        order = Order(order_id=str(data["order_id"]),
                     sku=str(data.get("sku", "")))
        order.qty = int(data.get("qty", 1))
        order.status = str(data.get("status", STATUS_PENDING))
        order.created_at = str(data.get("created_at", ""))
        order.confirm_flag = bool(data.get("confirm_flag", True))
        order.attempts = int(data.get("attempts", 0))
        order.artifact_id = str(data.get("artifact_id", ""))
        order.receipt_id = str(data.get("receipt_id", ""))
        order.refund_reason = str(data.get("refund_reason", ""))
        order.last_error = str(data.get("last_error", ""))
        order.updated_at = str(data.get("updated_at", ""))
        return order

    def age_seconds(self, now: datetime) -> float:
        return max(0.0, (now - parse_iso(self.created_at)).total_seconds())


class OrderStore:
    """Local task table persisted as orders.json (atomic writes)."""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self.path = os.path.join(state_dir, "orders.json")
        self.orders: Dict[str, Order] = {}
        os.makedirs(state_dir, exist_ok=True)
        self.load()

    def load(self) -> None:
        self.orders = {}
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for item in data.get("orders", []):
            order = Order.from_dict(item)
            self.orders[order.order_id] = order

    def save(self) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "orders": [order.to_dict() for order in self.orders.values()],
        }
        atomic_write_json(self.path, payload)

    def get(self, order_id: str) -> Optional[Order]:
        return self.orders.get(order_id)

    def all(self) -> List[Order]:
        return [self.orders[key] for key in sorted(self.orders)]

    def manual_queue(self) -> List[Order]:
        return [o for o in self.all() if o.status == STATUS_MANUAL]

    def refund_flagged(self) -> List[Order]:
        return [o for o in self.all() if o.status == STATUS_REFUND]


# ---------------------------------------------------------------------------
# adapter 1: order source (pull mode)
# ---------------------------------------------------------------------------

class OrderSource(ABC):
    """Pull-mode order source (design doc solution 1: a timer on a
    hosted cloud function polls the platform order-list API every 2-5
    minutes; no public inbound surface, naturally idempotent).

    Real adapter: wrap the store open-platform "order list query" API,
    map each platform order to an Order (order_id = platform order id,
    confirm_flag = paid state, created_at = payment time). poll() may
    legitimately return the same orders again and again - intake
    dedups by order_id.
    """

    @abstractmethod
    def poll(self) -> List[Order]:
        """Return the currently visible open orders (pull mode)."""
        raise NotImplementedError


class MockOrderSource(OrderSource):
    """Built-in mock: emits 3 deterministic test orders on every poll.
    Order ids/skus/quantities are fixed; created_at stamps at
    construction time so tests never depend on wall-clock ages."""

    def __init__(self, orders: Optional[List[Order]] = None):
        if orders is None:
            stamp = iso(utc_now())
            orders = [
                Order("MO-20260924-0001", "PIXEL-PET-BLUE", 1,
                      created_at=stamp),
                Order("MO-20260924-0002", "PIXEL-ROOM-PINK", 2,
                      created_at=stamp),
                Order("MO-20260924-0003", "PIXEL-BADGE-GOLD", 1,
                      created_at=stamp),
            ]
        self._orders = list(orders)

    def poll(self) -> List[Order]:
        return list(self._orders)


# ---------------------------------------------------------------------------
# adapter 2: generator
# ---------------------------------------------------------------------------

@dataclass
class TokenUsage:
    """LLM token accounting for the group rolling ledger (three-question
    router classification: L1 deterministic lookup / L2 local model /
    L3 cloud API)."""

    tier: str = "L1"
    tokens_local: int = 0
    tokens_api: int = 0
    api_reason: str = "no_api_used"


class GenerationError(Exception):
    """Raised by a Generator when an artifact cannot be produced."""


@dataclass
class Artifact:
    """Placeholder artifact produced by a Generator. Deterministic for
    a given order input, so replays and audits are byte-stable."""

    artifact_id: str
    order_id: str
    kind: str
    template: str
    params: Dict[str, Any]
    checksum: str
    usage: TokenUsage = field(default_factory=TokenUsage)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "order_id": self.order_id,
            "kind": self.kind,
            "template": self.template,
            "params": self.params,
            "checksum": self.checksum,
            "usage": {
                "tier": self.usage.tier,
                "tokens_local": self.usage.tokens_local,
                "tokens_api": self.usage.tokens_api,
                "api_reason": self.usage.api_reason,
            },
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Artifact":
        usage_raw = data.get("usage", {})
        usage = TokenUsage(
            tier=str(usage_raw.get("tier", "L1")),
            tokens_local=int(usage_raw.get("tokens_local", 0)),
            tokens_api=int(usage_raw.get("tokens_api", 0)),
            api_reason=str(usage_raw.get("api_reason", "")),
        )
        return Artifact(
            artifact_id=str(data.get("artifact_id", "")),
            order_id=str(data.get("order_id", "")),
            kind=str(data.get("kind", "")),
            template=str(data.get("template", "")),
            params=dict(data.get("params", {})),
            checksum=str(data.get("checksum", "")),
            usage=usage,
        )


class Generator(ABC):
    """Asset generator interface.

    Real adapter (design doc steps 3-4): the token router - L1 lookup
    tables for deterministic params, L2 local Ollama qwen2.5:7b for
    structured JSON blueprints, L3 cloud GLM as a budget-gated fallback
    (0.05 CNY per order cap) - followed by Unity batch template
    assembly rendering PNG (props/characters) or MP4 (rooms). The
    contract stays generate(order) -> Artifact; fill TokenUsage so the
    ledger rows stay truthful.
    """

    @abstractmethod
    def generate(self, order: Order) -> Artifact:
        raise NotImplementedError


class StubGenerator(Generator):
    """Deterministic template assembly; no model, no network. The
    artifact is a pure function of (order_id, sku, qty), so identical
    inputs always yield identical artifact ids and checksums.

    fail_order_ids injects failures for specific order ids - used by
    the tests to rehearse the failure path."""

    def __init__(self, fail_order_ids: Optional[List[str]] = None):
        self.fail_order_ids = set(fail_order_ids or [])

    def generate(self, order: Order) -> Artifact:
        if order.order_id in self.fail_order_ids:
            raise GenerationError("stub_failure_injected: %s"
                                  % order.order_id)
        params = {"sku": order.sku, "qty": order.qty}
        template = "tpl_" + order.sku
        # design doc: rooms render as MP4, props/characters as PNG
        kind = "mp4_placeholder" if "ROOM" in order.sku else "png_placeholder"
        canonical = json.dumps(
            {"order_id": order.order_id, "params": params,
             "template": template},
            sort_keys=True,
        )
        return Artifact(
            artifact_id="art_" + sha16(order.order_id),
            order_id=order.order_id,
            kind=kind,
            template=template,
            params=params,
            checksum=sha256_hex(canonical),
            usage=TokenUsage(
                tier="L1",
                tokens_local=0,
                tokens_api=0,
                api_reason="stub_template_assembly_no_model",
            ),
        )


# ---------------------------------------------------------------------------
# adapter 3: deliverer
# ---------------------------------------------------------------------------

@dataclass
class Receipt:
    """Delivery credential record (design doc plan B: the platform
    order id is the entitlement; links carry an order-id hash)."""

    receipt_id: str
    order_id: str
    channel: str
    credential: str
    artifact_id: str
    artifact_checksum: str
    delivered_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "order_id": self.order_id,
            "channel": self.channel,
            "credential": self.credential,
            "artifact_id": self.artifact_id,
            "artifact_checksum": self.artifact_checksum,
            "delivered_at": self.delivered_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Receipt":
        return Receipt(
            receipt_id=str(data.get("receipt_id", "")),
            order_id=str(data.get("order_id", "")),
            channel=str(data.get("channel", "")),
            credential=str(data.get("credential", "")),
            artifact_id=str(data.get("artifact_id", "")),
            artifact_checksum=str(data.get("artifact_checksum", "")),
            delivered_at=str(data.get("delivered_at", "")),
        )


class Deliverer(ABC):
    """Delivery channel interface.

    Real adapter plan B (no account dependency): send the rendered
    image plus a short link keyed by the order-id hash via platform IM,
    then mark the e-voucher shipped on the platform. Real adapter plan A
    (after the domain account system lands): grant the asset into the
    buyer's in-game backpack. The contract stays deliver(order,
    artifact) -> Receipt and must be idempotent per order_id.
    """

    @abstractmethod
    def deliver(self, order: Order, artifact: Artifact) -> Receipt:
        raise NotImplementedError


class StubDeliverer(Deliverer):
    """Writes one local record file per order under deliveries_dir.
    Idempotent: an existing record is returned as-is, never rewritten."""

    def __init__(self, deliveries_dir: str):
        self.deliveries_dir = deliveries_dir
        os.makedirs(deliveries_dir, exist_ok=True)

    def _path(self, order_id: str) -> str:
        return os.path.join(self.deliveries_dir,
                            sanitize_name(order_id) + ".json")

    def deliver(self, order: Order, artifact: Artifact) -> Receipt:
        path = self._path(order.order_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return Receipt.from_dict(json.load(fh))
        receipt = Receipt(
            receipt_id="rcpt_" + sha16(order.order_id),
            order_id=order.order_id,
            channel="stub_local_file",
            credential="stub://delivery/"
                       + sha16(order.order_id + ":credential"),
            artifact_id=artifact.artifact_id,
            artifact_checksum=artifact.checksum,
            delivered_at=iso(utc_now()),
        )
        atomic_write_json(path, receipt.to_dict())
        return receipt


# ---------------------------------------------------------------------------
# orchestration core
# ---------------------------------------------------------------------------

@dataclass
class RunReport:
    """Counters for one run_once pass (printed, not persisted)."""

    polled: int = 0
    new_orders: int = 0
    confirm_updates: int = 0
    refunds_flagged: int = 0
    attempts: int = 0
    delivered: int = 0
    manual_queued: int = 0
    skipped_done: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "polled": self.polled,
            "new_orders": self.new_orders,
            "confirm_updates": self.confirm_updates,
            "refunds_flagged": self.refunds_flagged,
            "attempts": self.attempts,
            "delivered": self.delivered,
            "manual_queued": self.manual_queued,
            "skipped_done": self.skipped_done,
        }


class FulfillmentPipeline:
    """Orchestration core - the only piece that stays untouched when
    real accounts replace the mock adapters."""

    def __init__(
        self,
        source: OrderSource,
        generator: Generator,
        deliverer: Deliverer,
        state_dir: str,
        ledger_path: Optional[str] = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        refund_hours: int = DEFAULT_REFUND_HOURS,
    ):
        self.source = source
        self.generator = generator
        self.deliverer = deliverer
        self.store = OrderStore(state_dir)
        self.state_dir = state_dir
        self.artifacts_dir = os.path.join(state_dir, "artifacts")
        os.makedirs(self.artifacts_dir, exist_ok=True)
        self.ledger_path = (ledger_path
                            or os.path.join(state_dir, "ledger.jsonl"))
        self.max_attempts = int(max_attempts)
        self.refund_hours = int(refund_hours)

    # -- step 1: idempotent intake --------------------------------------
    def _intake(self, now: datetime) -> Tuple[int, int, int]:
        polled = 0
        new_orders = 0
        confirm_updates = 0
        for raw in self.source.poll():
            polled += 1
            existing = self.store.get(raw.order_id)
            if existing is None:
                raw.status = STATUS_PENDING
                raw.updated_at = iso(now)
                self.store.orders[raw.order_id] = raw
                new_orders += 1
            elif raw.confirm_flag and not existing.confirm_flag:
                # payment confirmation observed in a later poll
                existing.confirm_flag = True
                existing.updated_at = iso(now)
                confirm_updates += 1
        return polled, new_orders, confirm_updates

    # -- step 2: rights-first refund sweep -------------------------------
    def _sweep_refunds(self, now: datetime) -> int:
        """Flag every confirmed order unresolved for more than
        refund_hours for platform refund - runs BEFORE processing so no
        further compute is spent on overdue orders (rights first:
        refund beats generation)."""
        deadline = timedelta(hours=self.refund_hours)
        flagged = 0
        for order in self.store.all():
            if (order.status in REFUNDABLE_STATUSES
                    and order.confirm_flag
                    and now - parse_iso(order.created_at) > deadline):
                order.status = STATUS_REFUND
                order.refund_reason = ("auto_refund_unresolved_over_%dh"
                                       % self.refund_hours)
                order.updated_at = iso(now)
                flagged += 1
        return flagged

    # -- step 3: one generation+delivery attempt -------------------------
    def _fulfill_once(self, order: Order, now: datetime) -> str:
        order.status = STATUS_GENERATING
        order.updated_at = iso(now)
        try:
            artifact = self.generator.generate(order)
            receipt = self.deliverer.deliver(order, artifact)
        except Exception as exc:  # adapter failure -> one counted attempt
            order.attempts += 1
            order.last_error = "%s: %s" % (type(exc).__name__, exc)
            order.updated_at = iso(now)
            if order.attempts >= self.max_attempts:
                order.status = STATUS_MANUAL
            else:
                order.status = STATUS_FAILED
            return order.status
        order.artifact_id = artifact.artifact_id
        order.receipt_id = receipt.receipt_id
        order.status = STATUS_DONE
        order.last_error = ""
        order.updated_at = iso(now)
        try:
            atomic_write_json(
                os.path.join(self.artifacts_dir,
                             sanitize_name(order.order_id) + ".json"),
                artifact.to_dict(),
            )
        except Exception as exc:
            # delivery already succeeded; an audit-record hiccup must
            # not trigger a refund-retry loop
            print("[pipeline] artifact record warning for %s: %s"
                  % (order.order_id, exc))
        try:
            ledger_hook.record_fulfillment(self.ledger_path, order,
                                           artifact)
        except Exception as exc:
            # ledger trouble must not undo a completed delivery
            print("[pipeline] ledger warning for %s: %s"
                  % (order.order_id, exc))
        return STATUS_DONE

    def _process(self, now: datetime) -> Tuple[int, int, int, int]:
        attempts = 0
        delivered = 0
        manual_queued = 0
        skipped_done = 0
        for order in self.store.all():
            if not order.confirm_flag:
                continue  # unpaid: never spend compute on unconfirmed orders
            if order.status not in WORKING_STATUSES:
                if order.status == STATUS_DONE:
                    skipped_done += 1
                continue
            while (order.status in WORKING_STATUSES
                   and order.attempts < self.max_attempts):
                result = self._fulfill_once(order, now)
                attempts += 1
                if result == STATUS_DONE:
                    delivered += 1
                elif result == STATUS_MANUAL:
                    manual_queued += 1
        return attempts, delivered, manual_queued, skipped_done

    def run_once(self, now: Optional[datetime] = None) -> RunReport:
        """One orchestration pass: poll -> intake -> refund sweep ->
        fulfill -> persist. Safe to re-run any number of times."""
        moment = now or utc_now()
        report = RunReport()
        (report.polled,
         report.new_orders,
         report.confirm_updates) = self._intake(moment)
        report.refunds_flagged = self._sweep_refunds(moment)
        (report.attempts,
         report.delivered,
         report.manual_queued,
         report.skipped_done) = self._process(moment)
        self.store.save()
        return report
