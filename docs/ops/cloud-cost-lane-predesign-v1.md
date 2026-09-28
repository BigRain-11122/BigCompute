# 云端算力成本归集轨预设计件 v1.0（cloud-cost-lane predesign·预设计先行·零代码变更）

> 依据=提案 BC-P-10（09-28 立·三问判据过）+O-20260928-1855 机队算力全面调用令+O-20260928-1820 ④「云端 token 同律」+City 3D 全面开工令（云算力并行全量调用）=需求实证三链。本件=预设计（M7 store-ledger-wiring-predesign 同法）：**零代码变更**，接线窗=首笔实缴云账单物理凭证（J4）。
> 消费方=本司财务部（月度集团算力成本合账=云端+本地）+CEO/决策轮（O-1820 ④ 算力账双列「消耗+意义注记」云端侧承接）。

## §一 缺口定谳
口径 B 本地三要素（折旧/电费/运维·R-20260928-compute-cost-economics）无云端 lane——集团云端/机队消耗 scaling 下「承载集团算力成本」使命的归集缺位=R-36 在册风险。本件=归集轨 schema 预注册（禁编造预接线·J4 律）。

## §二 记录 schema（加性扩展·分轨双文件律=R-34/M7 §五同律）
- 新命令 `cloud-entry`（独立账本文件 state/cloud-cost-ledger.jsonl·与 state/cost-ledger.jsonl 分轨·月末 join 合账=§五）·`cloud-summary` 聚合面
- 字段表（对齐 quota 轨 15 字段惯例·Tools/cost_ledger.py L254-295 实读）：

| 字段 | 取值 | 注 |
|---|---|---|
| tx_id | 云任务工单号 | 幂等键同律（重放=duplicate 零新增） |
| date / month | 任务日 / 归集月 | _month_of 同源 |
| type | cloud | 与 issue/consume/fund 分立 |
| consumer | 消费司 | 司×线×月 聚合粒度（O-1820 ④ 归集轴） |
| lane | 云线名（openrouter/gemini/…） | 实缴账单对账轴 |
| task_ref | 云任务单号/批次 | 账单行对账指针 |
| amount_mtok | 用量 | count/eval_count 计量同源（BPE≠Qwen 词表口径注记常驻） |
| unit_cost_b_cny | **实缴云账单回填 ⬜** | **唯一合法源=实缴账单·禁事前编造**（Q3 铁律云端同律） |
| cost_b_cny | amount×unit | settlement_caliber="B" 结算常量 |
| receipt_status | pending_bill → billed | 月末实缴账单回填后翻 billed |
| reason / ref | 缘由 / 账单指针 | ref=实缴账单编号或账单文件指针 |
| tokens_local / tokens_api / api_reason | 0 / N / 同律 | O-1820 ④ 消耗+意义注记双列承接 |
| ceo_approved | bool | 超预算豁免位同律 |

## §三 预估对照面（口径 A 专用·禁入结算式）
tokencost（MIT·2,006★）/genai-prices（MIT·384★）=云端单价**呈现/对照/预估**专用锚（OH-20260927-bigcompute §六 观察位·结构判负律=永不入结算式·R-24 同律）；gpuhunt（MPL-2.0·57★）=云 GPU 租价参考位。预估行 receipt_status 恒 ⬜ 禁翻 billed。

## §四 幂等与预算闸
- tx_id=云任务工单号幂等同律（重放 duplicate 零新增）
- 月度云 lane 预算行（quota-budget 同构）：超发 blocked_over_budget+--ceo-approved 豁免同律
- 回填流程=月末实缴账单 → unit_cost_b_cny 回填 → billed 落账（**月末归集回溯口径=R-20260928-compute-cost-economics §一换算链同律**）

## §五 月度合账报告面
cloud-summary（或 summary 扩 group_by lane）=司×线×月 聚合+本地口径 B 合账 join=财务部月度集团算力成本合账（云端+本地一表·O-1820 ④ 承接）。

## §六 预注册判据（接线轮预验）
- J1 加性零破坏：既有命令/行零变更·selftest 全绿维持（orders+quota-gate+count）
- J2 单价凭证律：unit_cost_b_cny 非实缴账单回填禁入·预估面恒 ⬜
- J3 幂等重放：tx_id 重放零新增（合成数据 dry-run·E11 全链预演同法=selftest 扩测前置）
- J4 接线窗律：首笔实缴云账单物理凭证到件前零接线零编造行

## §七 验证声明
零代码变更·纯预设计件（接线随 J4 窗·工程轮判据=selftest 扩测 cloud-entry 合成 dry-run 前置）。数据零出域·非投顾非交易面·口径 B 结算唯一维持（口径 A 呈现禁结算）。
