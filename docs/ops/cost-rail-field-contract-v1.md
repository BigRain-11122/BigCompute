# 成本五轨字段契约清单 v1.0（T137·2026-10-26）

> 来源=机械产出，非手抄：`python Tools/cost_contract_census.py census`（selftest 12/12 PASS）。
> 证据件=`state/t137-cost-contract-20261026.json`（全量 JSON：写入键集/读取键集/月结读取键/缺口/遮蔽）。
> 定位=T136 out-of-census 边界里最大一块「未受门」的写入面——口径 B 结算正轨 + 四条计量轨。

## 一、方法（四层对读，全为运行时实跑）

| 层 | 手段 | 说明 |
|---|---|---|
| L1 写入面 | 临时目录实跑写命令（每命令两次＝幂等闸路径一并覆盖） | 零触碰 live 台账 |
| L2 读取面 | `_TraceDict` 环绕 `cost_ledger._load_rows`，记录聚合器真正触碰的行键 | 事实读取，非静态猜测 |
| L3 契约面 | AST 抽 `month_end_collect.parse_quota_summary/parse_cloud_summary/parse_l1_summary` 的 `.get("k")` 键 | 月结消费面 |
| L4 缺口 | L3 键 ⊄ 真实 summary 输出键 | 缺失即静默回落 0（本轮实测 **gaps=[]**） |

## 二、写入面字段清单（六命令五轨）

| 轨 | 命令 | 键数 | 字段 |
|---|---|---|---|
| cost | `add_row` | 15 | order_id, date, sku, tier, cost_item, price, fee_rate, refund_rate, net_price, amount, gross, tokens_local, tokens_api, api_reason, fund_3090 |
| quota | `quota_issue` / `quota_consume` | 15 | tx_id, date, month, type, consumer, amount_mtok, unit_cost_b_cny, cost_b_cny, settlement_caliber, reason, ref, ceo_approved, tokens_local, tokens_api, api_reason（两命令**行契约完全一致**，仅取值不同） |
| cloud | `cloud_entry` | 18 | 上列 + lane, task_ref, receipt_status（`unit_cost_b_cny`/`cost_b_cny` 入口恒 null=J2 结构门） |
| borrow | `borrow_entry` | 20 | tx_id, date, month, type, lender_dept, borrower_dept, machine_id, work_type, tokens_mtok, quota_debit_mtok, settlement_caliber, cost_b_cny(null), receipt_status, timeout_rule, return_rule, guardrails, verdict_source, reason, ref, ceo_approved |
| l1 | `l1_record` | 21 | tx_id, date, month, type, consumer, lane, endpoint, model, prompt_tokens, completion_tokens, amount_mtok, unit_cost_b_cny(null), cost_b_cny(null), receipt_status, settlement_caliber, reason, ref, ceo_approved, tokens_local, tokens_api, api_reason |

结算面（`borrow_settle`/`l1_settle`/`cloud_bill`）不新增键，只就地翻转 `receipt_status` 并回填 `cost_b_cny`/`ref`/`unit_cost_b_cny`——本轮实测二次调用不扩键集。

## 三、月结读取面（L3）与缺口

| 轨 | month_end 读取键 | 真实 summary 是否齐备 |
|---|---|---|
| quota | totals: issued_mtok / consumed_mtok / cost_issued_b_cny / cost_consumed_b_cny；top: rails / settlement_caliber | ✓ 全齐 |
| cloud | totals: rows / amount_mtok / cost_billed_b_cny / pending_bill_rows；top: wiring | ✓ 全齐 |
| l1 | totals: amount_mtok / cost_converted_b_cny / pending_conversion_rows / converted_rows；top: consumer_model / month_face / settlement_caliber / lane_note | ✓ 全齐 |

**gaps=[]（VERDICT=COST-CONTRACT-GREEN）**：月结读的每个键都真实存在于生产者输出，无一处靠 `.get` 默认值兜底。

## 四、遮蔽面（写入但 summary 不读·如实登记，非缺陷）

| 轨 | 写入→读取 | summary 不读的键 |
|---|---|---|
| cost | 15→7 | api_reason, cost_item, date, fee_rate, price, refund_rate, sku, tier |
| quota | 15→5 | api_reason, ceo_approved, date, month, reason, ref, settlement_caliber, tokens_api, tokens_local, unit_cost_b_cny |
| cloud | 18→6 | api_reason, ceo_approved, cost_b_cny, date, month, reason, ref, settlement_caliber, task_ref, tokens_api, tokens_local, unit_cost_b_cny |
| borrow | 20→6 | ceo_approved, cost_b_cny, date, guardrails, lender_dept, month, quota_debit_mtok, reason, ref, return_rule, settlement_caliber, timeout_rule, verdict_source, work_type |
| l1 | 21→6 | api_reason, ceo_approved, completion_tokens, cost_b_cny, date, endpoint, lane, month, prompt_tokens, reason, ref, settlement_caliber, tokens_api, tokens_local, unit_cost_b_cny |

读法：遮蔽≠死字段。`date/month` 用于月窗过滤（在过滤谓词里触碰，不进 summary 聚合行），`receipt_status`/`unit_cost_b_cny`/`cost_b_cny` 在结算命令面消费，`api_reason`/`ceo_approved` 归 SLI-8 与预算闸（`sli_line`/`quota_summary.month_face`）。T136 已把 cost 轨整体列入 `OUT_OF_CENSUS`（理由=字段契约归生产者），本清单即该理由的兑现件。

## 五、迁移到 SCHEMAS 的判据预注册（J1–J4·未执行即未达标）

- **J1** 字段清单件与本清单逐键一致（写入键集作为 `PRODUCERS` 单一来源，禁手抄漂移）。
- **J2** `rails_schema_check.SCHEMAS` 增 `cost` 五轨（cost/quota/cloud/borrow/l1）声明，`census()` 对五轨 `VERDICT=RAILS-CENSUS-GREEN problems=0`。
- **J3** `OUT_OF_CENSUS` 中 `cost` 条目退场（cost_l1 镜像轨保留排除并给出理由行），`census_coverage()` 返回空。
- **J4** 负例可复现：注入未声明键 → 必红；删一条 producer → 必红。判负路径=`cost_ledger` 写入面跨 8 命令过多无法单点声明时，退为「按命令分轨声明」而非放弃受门。

## 六、复现

```
python Tools/cost_contract_census.py selftest   # 12/12 PASS
python Tools/cost_contract_census.py census     # JSON 面（verdict/gaps/occlusion）
```