# 单位成本分母口径 v1（M105 / BC-P-184 承接件·2026-11-03）

> 溯源：M99 演练当场发现「谁算商业产出」三处各写一套；BC-P-184 提案判据「先查册 → 有第三套写法则单源化 → 同义则落指针」。
> 结论：**三处不是同一个轴**（不是谁写错了，是把三种不同东西都叫 tokens），因此做单源模块 + 明确分类律，而非把三处强行统一成一个数。

## 一、三面逐行比对（查册结果·实读源码）

| # | 面 | 源文件/命令 | 计的是什么 | 消费方 | 轴 |
|---|---|---|---|---|---|
| 1 | quota_rail | `state/quota-ledger.jsonl` → `cost_ledger.py quota-summary` | **已发放且已消费的配额 tokens**（权利面：卖出去的额度里用掉多少） | `month_end_collect` S1 → 商业 `tokens_mtok` | 权利 |
| 2 | l1_rail | `state/cost-l1-ledger.jsonl` → `cost_ledger.py l1-summary` | **本地推理实际跑的 token 量**（自用计量·含 QA 探针） | `l1-settle` / 口径 B 结算 | 自用计量 |
| 3 | revenue_row | `state/cost-ledger.jsonl` 每行 `tokens_local+tokens_api` | **单笔履约实耗** | `unit_cost_drill`（M99） | 单笔消耗 |

三面差异不是笔误：
- 一个配额型 SKU（会员/算力包）卖的是**使用权**，履约当天可能一个 token 都没跑——用「实耗」当分母得 0，用「发放额度」才有单位经济意义（预设计 §二 字段 10 原文即区分「生成型=实耗 / 配额型=发放 amount_mtok」）。
- 自用 L1 计量是内部成本，**不进商业分母**（month_end_collect S1 既有律），但它自己也需要一个结算面（`l1-settle`）。

## 二、单源化产物

新增 `Tools/unit_cost_caliber.py`（同源正身·纯函数·零 IO）：

- `classify_row(row)` → `quota` / `generation` / `unknown`
  判序：显式 `sku_type` 优先 → `lane ∈ {subscription,quota,membership,member}` → sku 含 quota/membership/subscription/allowance → 其余有 lane/sku/cost_item 即 generation → 都没有 = unknown（**不静默入桶**）
- `row_denominator(row)` → `{sku_class, mtok, state, reason}`
  配额型取 `amount_mtok`（**缺失 = BLOCKED，不是 0**）；生成型取 `(tokens_local+tokens_api)/1e6`（为 0 = BLOCKED）
- `aggregate_denominator(rows)` → 分面 token 数 + `quota_blocked_rows` / `generation_blocked_rows` / `unknown_rows` + 逐行阻塞理由
- `unit_cost_state(agg)` → `UNIT-COST-CALCULATED` / `UNIT-COST-PARTIAL` / `UNIT-COST-BLOCKED`

接入：
- `Tools/unit_cost_drill.py` 改为 import 本模块（**原来的 `tokens_local+tokens_api` 分母对配额型 SKU 是错的，本轮修掉**）；月面新增 `quota_mtok` / `generation_mtok` / `quota_blocked_rows` / `denominator_block_reasons`，渲染里逐行打印阻塞原因。
- `Tools/month_end_collect.py` `assemble()` 与 `Tools/cost_ledger.py` `l1_summary()` 各落一行指针注记（**不改计算**——两个面的数都是正确的，只是各自属于不同轴）。

## 三、实跑（2026-11-03 22:5x·证据 state/unit-cost-drill-*.txt/.json）

```
UNIT-COST DRILL  verdict=UNIT-COST-DRILL-GREEN  month=2026-10
rails  revenue=0  mock=3  l1=1  sha_unchanged=True
mock rail face: 2026-10 orders=3 price=401.9000 net=351.2606
  refund_prov=32.1520 fee=18.4874 cost=0.0000 gross=351.2606
  tokens=0 denom_mtok=0.000000 quota_mtok=0.000000 gen_mtok=0.000000
  quota_blocked=1 unit_cost=UNIT-COST-BLOCKED
    blocked: row 0: quota-type row without amount_mtok
    blocked: row 1: generation-type row without fulfillment tokens
    blocked: row 2: generation-type row without fulfillment tokens
selftest: unit_cost_caliber 13/13 PASS · unit_cost_drill 16/16 PASS
```

读数说明（如实）：
- 真收入轨仍 0 行（计划态未入账），MOCK 三行本身**缺 `amount_mtok` 与履约 tokens 字段**——这是 MOCK 演练数据的设计缺口，不是口径器误判；三行逐条点名阻塞原因，比笼统「分母为 0」有用。
- 两轨 sha 前后一致 = 零重记维持。

## 四、判据对照（BC-P-184 三条）

| 判据 | 结果 |
|---|---|
| ① 三处分母口径逐行比对出差异表 | ✓ 本文 §一 |
| ② 确有第三套写法 → 单源函数被消费方 import | ✓ `unit_cost_caliber` 已被 drill import；另两面落指针（不改数） |
| ③ 三处实为同义则落指针 + selftest 断言 | 🟡 部分适用：quota/l1 两面各自成立，指针注记落地；未改成 import 是**有意**的（改 `month_end_collect` 计算面会动 N1 结算链，收益低于风险，E11 同律） |

## 五、M106 补字段后的复跑（2026-11-03 22:5x·同日续作）

MOCK 三行补分母字段（**合成值·逐行标 `denominator_simulated: true`**）：

| 行 | 类型 | 补的分母 | 依据 |
|---|---|---|---|
| MOCK-20261019-R1 | quota（subscription） | `amount_mtok=0.42` | M12 配额设计带 0.42-0.85 Mtok/月 的下沿，取设计值 |
| MOCK-20261019-R2 | generation（gacha） | `tokens_local=1200` | 单张卡面生成的合成量级 |
| MOCK-20261020-R3 | generation（b2b_direct） | `tokens_local=180000` | 年度数据报告合成的量级 |

复跑结果：`denom_mtok=0.601200`（quota 0.420000 + gen 0.181200）、`quota_blocked=0`、`unit_cost=UNIT-COST-CALCULATED`、`simulated denominator rows: mock=3 real=0`。

**但单位成本读数是 0.0，且这是真的**：三行 MOCK 单的 `amount`（COGS）恒为 0，工具如实打 `note: cost side is zero on this rail, so 0.0 is the cost input and not a measured unit cost`。也就是说——分母面现在通了，**成本面还完全没被演练过**（派生 BC-P-186）。

## 五之二、M107 补合成 COGS（同日续作）

M106 暴露「成本侧恒 0」后补合成成本，换算单价取 **0.60 CNY/Mtok（设计值·非测量）**，逐行标 `cost_simulated: true` 与 `cost_rate_cny_per_mtok: 0.6`：

| 行 | 分母 | 合成 COGS | 换算 |
|---|---|---|---|
| R1（quota 0.42 Mtok） | 0.42 | 0.2520 | 0.42×0.6 |
| R2（1200 tok=0.0012 Mtok） | 0.0012 | 0.0007 | 0.0012×0.6（4 位取整） |
| R3（180000 tok=0.18 Mtok） | 0.18 | 0.1080 | 0.18×0.6 |

复跑：`cost=0.3607 gross=350.8999 denom_mtok=0.601200 unit_cost=UNIT-COST-CALCULATED value=0.6000`，`COST-FACE-UNEXERCISED` 行消失（转为 `COST-FACE-EXERCISED`）。

**循环性必须写明**：单位成本读出 0.6000 等于输入设计单价，这是**自证**——它证明的是「分母×单价→成本→毛利→单位成本」这条算术链闭合、毛利率公式可跑，**不证明经济性**。真实单位成本仍卡在 N3 门（凭证 ⬜ + 商业 tokens=0），与本次演练无关。

负向断言（selftest S18/S19）：全零 COGS 的月份必须打 `COST-FACE-UNEXERCISED`，非零才 `COST-FACE-EXERCISED`——防止将来账本又回到「静默 CALCULATED」的老路。

## 五之三、按 SKU 类分面毛利（M108·同日续作）

整月合计会把两种完全不同的生意混成一行，故 drill 现按 caliber 分类出分面：

```
class generation orders=2 net=307.6480 cost=0.1087 gross=307.5393 margin=99.96%
class quota      orders=1 net=43.6126  cost=0.2520 gross=43.3606  margin=99.42%
```

**读法与诚实边界**：两行毛利率都逼近 100%，不是「生意很好」，而是说明在 0.60 CNY/Mtok 这个设计成本下，**COGS 根本构不成约束**——单笔 3 元的卡面抽卡成本只有 0.0007 元。也就是说这批 MOCK 价格点与设计成本之间的关系尚未被校准过（价格高、成本假设低），真正决定毛利的是台数与销量，不是单位成本。这个结论直接喂 M2 定价确认包与 M109 敏感性扫描（找毛利率跌破 90/80/70% 所需的成本倍数），并提醒：**别拿 99% 毛利率去对外讲单位经济**。

## 六、剩余缺口（不在本轮自裁范围内）

1. **已由 M107 处理**：MOCK 成交环补合成 COGS（0.60 CNY/Mtok 设计单价·逐行标 `cost_simulated`），成本面演练走通；真实单位成本仍待凭证（N3 门）。
2. `cost-ledger.jsonl` 真轨 0 行是物理件未通的直接结果，单位成本 v1 落值仍 BLOCKED（N3 门：凭证 ⬜ + 商业 tokens=0），与本口径无关。
3. `l1-settle` 的单位价与 quota 面的口径换算（自用→商业）没有桥接件；若将来把自用算力外售，需要先立换算律再动账。

## 七、验证声明

- 实读源码：`cost_ledger.py`（l1_summary/quota_summary 函数体）、`month_end_collect.py`（assemble 商业面赋值行）、`store-ledger-wiring-predesign-v1.md` §二 字段 10。
- 改动面：新增 1 模块、drill 重构分母、两处指针注记、MOCK 三行补分母与合成 COGS（均已标注）；**未改任何结算数值、未改 BLUEPRINT 冻结节、零真实交易、零定价动作**。
- 回归：`cost_ledger selftest` PASS、`month_end_collect selftest 42/42 PASS`、`unit_cost_caliber 13/13`、`unit_cost_drill 20/20`、`rails_schema_check` RAILS-SCHEMA-GREEN rows=13。