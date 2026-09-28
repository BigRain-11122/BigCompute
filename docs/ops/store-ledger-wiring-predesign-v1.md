# 小店流水 → cost_ledger 开店接线预设计 v0.1（M7·计划态）

> 溯源：P1 队列 M7；依据件实读=Tools/cost_ledger.py（add 15 字段/quota 轨四命令·源码级）+ Tools/fulfillment/README.md（替换点 1/4）+ docs/ops/fulfillment-sku-mapping-v1.md（M6·十类×四站）+ docs/research/R-20260924-unit-economics（net 公式 wave 5）+ docs/ops/store-opening-checklist-v1.md（M1 门禁五行）。
> 状态律：商业物理件未通前（BLUEPRINT §五.5）=计划态建设期——本件全为设计值+合成数据演练，零真实交易（红线 2 维持）；定价/费率确认权=CEO（零定价动作）。

## 一、接线总览（三面）

| 面 | 流向 | 承接工具/轨 |
|---|---|---|
| 订单收入面 | 小店订单流水 → 收入行（15 字段） | cost_ledger `add` |
| 履约成本面 | 管线每履约 2 行（tokens/cost） | ledger_hook → 分轨文件（§五裁决） |
| 配额权利面 | 算力包/会员配额商品 → 发放/消费 | cost_ledger `quota-issue`/`quota-consume`/`quota-budget` |

**数据源通道（E14 判负注记承接）**：官方 API 文档站 web_fetch 判负（客户端渲染壳零文本·13:48 轮）→ 接线时字段核验通道=商家后台导出（M5 平台后台 8 项 P1-P8 执行单）或官方 SDK；本件字段名全为**设计值**，正式字段名以后台导出为准（⬜·禁转述充数=M5 补核纪律）。

## 二、订单收入面字段映射表（小店流水 → `add` 15 字段）

| # | cost_ledger 字段 | 小店流水取值源 | 取值规则 | 现役态 |
|---|---|---|---|---|
| 1 | order_id | 平台订单号 | 全链幂等键=平台订单号原样（README 替换点 1 同键·一单一行） | ✓ 设计值（字段名 ⬜ P 系核验） |
| 2 | date | 支付完成时间 | ISO 日期（支付完成日=收入确认日） | ⬜ 字段名待核验 |
| 3 | sku | 商品编码/SKU ID | M6 十类映射表路由（品类码前缀 QP/MB/RUSH/WG/SF/BZ=设计值·正式 SKU 码随开店类目批落） | 🟡 前缀在册·正式码 ⬜ |
| 4 | tier | Generator 路由结果 | L1 查表/L2 本地/L3 云回退（M6 §二三问路由） | ✓ 设计值 |
| 5 | cost_item | — | 收入行固定 `revenue`（与履约轨 tokens/cost 分立=§五幂等裁决前提） | ✓ 本件立 |
| 6 | price | 买家实付金额 | 单位元·平台结算单为准 | ⬜ 字段名待核验 |
| 7 | fee_rate | 平台技术服务费（率） | 默认 0.05（wave 5 设计值）→ 开店后按后台账单实率校准 | 🟡 |
| 8 | refund_rate | 退款预提率 | 默认 0.08（wave 5 provision）→ 月末实退率回溯校准（§四） | 🟡 |
| 9 | net_price | （计算列） | net = price×(1−refund_rate)×(1−fee_rate)·工具内算 | ✓ |
| 10 | amount | 口径 B 该单算力成本 | 生成型=实耗 tokens×unit_cost_b；配额型=发放 amount_mtok×unit_cost_b（月末归集回溯口径=结算唯一） | ✓ 口径 B 唯一（A 禁结算=R-24） |
| 11 | gross | （计算列） | net−amount·工具内算 | ✓ |
| 12-14 | tokens_local/tokens_api/api_reason | 履约 TokenUsage 四字段之三 | Generator 如实回填（M6 §四判据③对账零差） | ✓ |
| 15 | fund_3090 | —（固定计提） | 默认 10.0/单·CEO 3090 基金循环 | ✓ |

## 三、配额权利面映射（算力包/会员/加急 → quota 轨）

- **issue**：tx_id 派生键=`Q-<平台订单号>`（同键贯通·幂等同源）；consumer=买家脱敏标识（平台匿名 ID·N7 数据分级 L4 出口闸同律·禁真实 PII 入账）；amount_mtok=SKU 配额面（M12 Q_max 随 CEO 定价批落值·落值前禁真发）；unit_cost_b_cny=口径 B 单位成本 v1（⬜ 首月末归集·暂用估算带）。
- **consume**：履约实耗→`quota-consume`（tx_id=`Q-C-<履约流水号>`独立键）；余额透支拒付门禁现役（selftest 在案）。
- **budget**：`quota-budget` 月度 cap=当月可发配额总量（M12 落值窗承接）→ 超发 `blocked_over_budget`（`--ceo-approved` 豁免留痕=与真金额闸同构）。
- **订阅族**（会员三档）：订阅流水轨 H2 用例在册（44/44·续费新 order_id 分立）；退款联动=service-continuity-terms「未消费余额按比例退还」（§四）。

## 四、退款/售后面对齐

- **provision 制**：net_price 公式内含 refund_rate 预提（wave 5）→ 实退事件不逐笔冲销收入行。
- **月末校准**：实退率=当月售后单/当月订单（平台售后导出 ⬜）→ 偏差 >2pp=下月 refund_rate 调整提案呈决策轮（非本件自裁）。
- **订阅按比例退还**：退款额=剩余配额 balance_mtok×unit_cost_b_cny+未履约部分按比例 → 记 refund 对账行（独立售后单号键·只入对账面不入收入轨）。
- **24h 退款扫**：管线 `refund_flagged`（确认单 >24h）→ 权利先行=退款优先于算力再花费（M6 §一状态机引用·权利第一）。

## 五、幂等键三轨裁决（本件核心发现）

- **三轨现状**：`add`=order_id 单键幂等；履约 ledger_hook=(order_id, cost_item) 双键；quota=tx_id 单键。
- **冲突面**：收入行与履约行若同文件 → `add` 的 order_id 查重把履约行误判 duplicate（M6 演练账本 6 单 12 行双行制实证）→ 收入漏记。
- **裁决（推荐案）**：**分轨双文件**——收入行=state/cost-ledger.jsonl（`add` 直写）；履约行=管线 ledger.jsonl（README 替换点 4 落位时指分轨文件）→ 月末归集 join 键=order_id 三轨对账（收入行 amount ↔ 履约 cost 行零差断言）。零代码变更·`add` 幂等键维持。
- **备选（代码面·不推荐）**：`add` 幂等键扩为 (order_id, cost_item)——动现役工具+selftest 基线·成本>收益·E11 dry-run 承接评估位。
- **tx_id 总对齐链**：平台订单号=order_id=quota-issue tx_id 派生源；售后单号=refund 对账行独立键；consume tx=履约流水号派生（全链一单可溯）。

## 六、预注册判据（开店后回填）

1. §二映射表 15 行 ⬜ 全回填（商家后台导出/官方 SDK 通道核验·M5 P1-P8 执行单对照）后才允许首单真实入账。
2. E11 全链 dry-run 六面断言 PASS=开店首日记账就绪自证（BC-P-03 判据③同源·本件 §五=对账 join 面依据）。
3. 首月对账：收入行 amount vs 履约轨 cost 行零差+net/fee/refund 三率与平台账单一致（差异如实入日清）。
4. 替换点 4 落位时以 §五 分轨裁决为档·改动过科学判断闸。

## 七、映射可执行性微演练（M7-1·新增即耗·合成数据）

脚本=state/ledger_map_drill_m7.py（temp 账本·零污染真实 state/cost-ledger.jsonl·运行态 gitignored 同 M6 律）·2026-09-28 15:0x 实跑（exit 0）：

```
ADD DD20260928M7A -> ok
ADD DD20260928M7B -> ok
QUOTA-ISSUE Q-DD20260928M7B -> ok
QUOTA-REPLAY -> duplicate
M7 MAP DRILL: revenue_rows=2 tip_skipped=1 replay_new=0 math=True quota_tx=ok/duplicate -> PASS
```

断言四面=①管线内 2 样例入收入行+管线外型（打赏）零行边界（M6 §二第 10 行语义）②`add` 幂等重放零新增 ③net/gross 数学精确（9.9→net 8.6526/19.9→net 17.3926）④quota tx_id 派生键 `Q-DD20260928M7B` 入账+重放 duplicate。**M7 MAP DRILL: PASS**。

## 八、验证声明

- 实读源=cost_ledger.py（add_row/quota_issue 函数体+argparse 段）+fulfillment README+M6 件（引用不复制）；E14 web 判负留痕维持（正式字段名以后台导出为准·禁转述充数）。
- 零真实交易·零定价动作；费率默认值=wave 5 设计值随开店校准；本件=预设计零新立法。
