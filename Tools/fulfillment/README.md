# Tools/fulfillment -- MVP fulfillment pipeline skeleton (account-agnostic)

This is the orchestration skeleton for the order-to-delivery loop described in `docs/research/R-20260924-tech-pipeline.md` (MVP pipeline design draft): a pull-mode order source feeds an idempotent task table (`orders.json`, keyed by `order_id`), a deterministic generator assembles a placeholder artifact, a deliverer writes a local receipt, and every successful fulfillment appends schema-compatible rows to the group rolling ledger. The failure policy is rights-first: generation/delivery failures retry with a total attempt cap of 3, exhausted orders park in a manual fallback queue, and any confirmed order unresolved for more than 24 hours is flagged for platform refund before any further compute is spent on it. All platform/account specifics live behind three adapter interfaces (`OrderSource` / `Generator` / `Deliverer`), so going live with real accounts means swapping adapters only - zero refactor of this core.

## Run

```
python test_pipeline.py
```

Python 3.7+, stdlib only, zero network, zero secrets, zero popups. Exit code 0 = all checks pass. The test prints a PASS/FAIL line per check and a final summary.

## Files

- `pipeline.py` -- orchestration core: `Order` model (order_id / sku / qty / status / created_at / confirm_flag), adapter interfaces with Mock/Stub implementations, retry cap (<= 3), manual fallback queue, 24h refund sweep, JSON file state.
- `ledger_hook.py` -- group rolling ledger rows (JSONL, field-compatible with Tools/cost_ledger.py): 2 rows per fulfillment (`cost_item` "tokens" and "cost"), idempotent per (order_id, cost_item).
- `test_pipeline.py` -- end-to-end local test (happy path, idempotent replay, retry cap, refund flag, confirm gate, ledger schema).

## State layout (per state dir)

```
orders.json     task table: one row per order (status / attempts / receipt ids)
artifacts/      one JSON record per generated artifact (audit)
deliveries/     one JSON receipt per delivered order (stub channel)
ledger.jsonl    rolling ledger rows (default path)
```

## Real adapter replacement points (swap only these three + one path)

1. **OrderSource -> Douyin pull mode.** Implement `poll()` around the store open-platform "order list query" API (https://op.jinritemai.com/docs/api-docs/15/555), driven by a 2-5 min timer on a hosted cloud function (zero standing server, per the red line). Map: platform order id -> `order_id` (idempotency key), paid state -> `confirm_flag`, payment time -> `created_at`. Everything downstream is unchanged.
2. **Generator -> token router + Unity batch.** Route per the three-question policy: L1 deterministic lookup tables (SKU spec decode, palettes, template params), L2 local Ollama qwen2.5:7b for structured JSON blueprints, L3 cloud GLM only as a budget-gated fallback (0.05 CNY per order cap). Then Unity batch mode assembles prefabs/templates and renders PNG (props/characters) / MP4 (rooms). Fill the returned `Artifact`'s `TokenUsage` (tier / tokens_local / tokens_api / api_reason) so the ledger rows stay truthful.
3. **Deliverer -> real delivery channel.** Plan B (no account dependency): send the rendered image plus a short link keyed by the order-id hash via platform IM, then mark the e-voucher shipped on the platform. Plan A (after the domain account system lands): grant the asset into the buyer's in-game backpack. Keep `deliver()` idempotent per order_id and return a `Receipt`.
4. **Ledger path.** Point `ledger_path` (pipeline constructor) at the shared group rolling ledger file used by Tools/cost_ledger.py. The row schema already matches; per-order cost estimates switch on automatically once real `TokenUsage` flows in (price constants at the top of `ledger_hook.py`).

## SLA hooks

`refund_hours` (default 24) enforces the hard cap from the design doc; the Phase-1 targets (<= 30 min end-to-end, P95 2h) are measured from `created_at` to `Receipt.delivered_at` and need no extra plumbing.
