# 借算工单轨（borrow-compute order lane）工程预设计 v1.0

> 定位：议程二轮循环**族 C 增量**（E23·新增即耗）·R-20260928-multi-node-scheduling §四 工程化先行件（E21 cloud lane / E22 model manifest 同模式）。承接链=T-20260928-28 派活面记账底座+tech T2 真发放记账轨的跨司借算分支前置+BC-P-01/BC-P-04 派活记账面。
> 状态：**计划态预设计·零代码变更·零真实借算**。激活随 T-28 派活面复核窗（10-05 呈批后）+首笔真实借算工单物理凭证双门（J4）。

## 一、缺口定谳

- R- §四.1 借算记账轨=「设计面 ✓·真发放 ⬜」；R- §五 **J3 判据：一切跨司借算落口径 B 台账（工单号 tx_id）·零记账借算=P1 违规**→派活面（T-28）激活在册（10-05 复核窗）而承载 lane 缺工具面=**激活即违规的结构性缺口**，须先行预注册（R-38 立行）。
- 集团 fleet-protocol §三.2 工单五字段+护栏三条已正典在册（R- §二 L15 转引·他司域只读引用）——本件=本司台账侧 schema 预注册，与工单面字段一一对齐零冲突；O-1855 ⑤「不建中央调度器」同律：**lane=纯记账轨·零调度零派活逻辑**。

## 二、borrow-entry schema 15 字段预注册

分轨文件：`state/borrow-cost-ledger.jsonl`（R-34 分轨双文件律同律·与主账本/cloud 轨零混写·state/ 运行态 gitignored）。

| # | 字段 | 语义 | 依据/同构 |
|---|---|---|---|
| 1 | ts | ISO 时标 | 全轨同律 |
| 2 | tx_id | **工单号幂等键**（fleet-protocol 工单号·重放=duplicate 零新增） | quota 轨同构·R- §四.1 |
| 3 | lender_dept | 借出方司（本司侧常量=BigCompute） | 工单五字段① |
| 4 | borrower_dept | 借入方司（四消费方名册·赋能目录 §一） | 工单五字段② |
| 5 | machine_id | 机器 id（bm-a/bm-b/bm-c） | 工单五字段③ |
| 6 | work_type | 产线类型（batch_inference=BC-P-01 批量池/realtime_api=BC-P-04/tts/image_postprocess 按 §8.1 产线矩阵） | R- §二 产线分工 |
| 7 | tokens_mtok | 计量（**Ollama eval_count=精确口径**·tiktoken=估算口径注记同律） | T4/E16 同律 |
| 8 | settle_caliber | 口径常量 **B**（结算唯一·口径 A 禁结算） | R-24 同例 |
| 9 | cost_b_cny | 借出方归集成本（**月末归集回溯口径回填**·entry 时恒空=J2） | R-20260928-compute-cost-economics 换算链 |
| 10 | quota_debit_mtok | 借入方配额扣减（quota-consume 对接） | R- §四.1「借入方配额扣减」 |
| 11 | receipt_status | pending_settlement → settled（月末归集回填态） | cloud-bill billed 同构 |
| 12 | timeout_rule | 超时判据（工单面镜像） | 工单五字段⑤ |
| 13 | return_rule | 归还判据（工单面镜像） | 工单五字段④ |
| 14 | guardrails | 护栏三条注记（不抢主归属高优/同 stem 禁双机双产/借算域禁入对方核心写域） | fleet-protocol §三.2 原文引用 |
| 15 | verdict_source | 派活信号源指针（GPU-IdleWatch dispatch-log 行/fleet GREEN-IDLE 判据引用） | §三 |

## 三、verdict 信号对接面（只读指针制·零自动执行）

- GPU-IdleWatch（T-28）：15min tick·滚动 30min<50% 判 IDLE→**借池候选信号**——采集器源码实读：dispatch-log 行格式 `| ts | IDLE window avg x% (n samples/m min) | 指针 | DRY-RUN observe only, NO dispatch |`·IDLE_PCT=50.0·DRY-RUN 标注四处（docstring/header/观察行/返回消息）。lane 只读引用该行，**零调度零触发**（T-12 DRY-RUN 修令 ③ 维持至 10-05）。
- fleet 借池判据 GREEN-IDLE（RAM ≥40% 且 VRAM ≥6GB 且无在途任务）归 fleet-audit 侧——本司采集器=证据源之一（R- §四.4 如实：非唯一源）。
- **计量真值=Ollama eval_count（L0）**；采集器利用率=信号面非计量面（两律分立防串用）。

## 四、月度借调预算闸同构

- 命令面：`blocked_over_borrow_budget` + `--ceo-approved` 豁免（quota/cloud 预算闸同律·法源=D-20260925-10 借调预算律）。
- 闸对象=月度借出方 Mtok 上限；借入方侧配额月度预算闸归 quota 轨现役（不双建）。

## 五、月度合账面

- 司×机×月聚合（O-1820 ④ 消耗+意义注记承接同律）：借出方归集（折旧+电费按 TDP/利用率·R-20260928-compute-cost-economics 换算链）↔ 借入方扣减对账行。

## 六、判据 J1-J4 预注册（接线轮判据=cloud lane E21 §六/§七 同律）

- **J1 加性零破坏**：分轨新文件·既有命令/既有行零变更。
- **J2 结构性禁编造**：entry 时 cost_b_cny 恒空；回填唯一通道=月末归集回溯（电价唯一合法源=实缴账单 Q3 铁律·折旧随物理凭证·R- B 族同律）。
- **J3 幂等同律**：tx_id=工单号·重放 duplicate 零新增（E11 六面预演轨 6/6 PASS 已验同构面）。
- **J4 零接线零编造**：首笔真实借算工单物理凭证前，state/borrow-* 零生成·零接线（cloud J4 同律）。
- **接线轮判据**：cost_ledger.py 加性扩展 borrow-entry/borrow-settle/borrow-summary 三命令+selftest 扩测 borrow 合成 dry-run PASS（temp 账本零污染）前置——工程落地随 T-28 激活窗（10-05 复核呈批后）。

## 七、零代码变更声明

本件=纯预设计：Tools/ 零改动·真实 state/ 零生成（state/borrow-* 不存在=J4 维持）。正式立件随接线轮。

## 八、验证声明（外证/内知如实）

- 外证：R-20260928-multi-node-scheduling §二 L15/§四.1/§五 J3（本司正典件·台账原文实读行号在案）+Tools/gpu_idle_collector.py 源码实读（L42-50/L154-160 签名证据）+E11 预演轨 6/6 PASS+quota/cloud 轨现役 selftest。
- 内知如实：本司零实测借算（计划态）；机队他机数据=台账转述非自测。
- 建议级标注：schema 字段名/预算闸参数=本司建议级预注册·正式立件随接线轮·与集团 fleet-protocol 工单面字段以彼方正典为准（改行权在彼·零越权）。
