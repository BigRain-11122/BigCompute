# 月末归集执行单（month-end cost collection sheet）v1.4

> v1.4（2026-10-26 22:4x·tech T127 双轨并入轮）：§二 增 **S8 私域分发/归因轨**（state/distr-ledger.jsonl·M87）与 **S9 交付调度轨**（state/delivery-ledger.jsonl·M88 append-only 事件链）——P1 两环落成后的月末可审计面补口，机械链分母随采源集动态扩展（基准 7+S8/S9 各 1）；10-26 22:4x 实跑值=S8 rows 1·attribution pending=1·conv 命中 0·lane bilibili·live 0；S9 events 3·交付单 1·终态 delivered=1·route resident-qa-8792·live 0·**机械链 9/9 就绪**·selftest 33/33·证据 state/month-end-202610.json+state/t127-monthend-s8s9-20261026.txt。

> v1.3（2026-10-17 13:0x·tech T113 L1 轨并入轮）：§二 增 **S7 L1 本地推理轨源**（month_end_collect.py s7_l1·`:8090` usage 回执=唯一 token 源·J2 同律·T93）——2026-10-15 v3 final 迁移后自用消费面由 Ollama 轨转 L1 轨计量·N1 10-31 月末一命令面必含此源（T75 S6 同模式先例）；10-17 13:0x 实跑值=rows 1·amount 0.000021 Mtok（pending 1/converted 0）·月闸 cap=200.0 used=0.000021·单位价恒空=口径 B（l1-settle=N1 唯一回填通道）·selftest 27/27·机械链 7/7·证据 state/month-end-202610.json。
> v1.2（2026-10-11 12:0x·E63 安静带精化轮）：§三 电费要素分解行增**安静带校准候选基线**（10-01..07 排除 10-08 测试窗日=0.8063→借用日 delta 10-08 +0.5314/10-09 +0.8614/10-10 +0.568·三日 +1.9608）=月末人工归因第二候选——S6 工具默认「月首 8 日」约定值维持·证据 state/e63-quietband-recalc-20261011.txt。
> v1.1（2026-10-11 00:3x·E62 收线回净轮）：§二 增 S6 GPU 能耗剖面源（gpu_energy_profile.py·10-10 23:1x 工程件+E62 回净实证）+§三 电费要素分解行——工具面 month_end_collect.py S6 并入微步=tech T75（N1 窗 10-31 前有界承接·零跳窗）。

> 定位：议程三轮循环**族 B 增量**（explore E25·新增即耗）。承接=R-20260928-compute-cost-economics 换算链「**月末归集回溯口径=结算唯一**」+「试点期回溯定价法防编造产能基准」的**执行面首件**；M2 定价确认包（pricing-confirmation-package-29.9-v1.md）「单位成本 v1 ⬜ 待首月末归集」的前置程序件。
> 状态：**计划态执行单·零真实交易·零编造**。成本三要素（原值/电价/利用率）全 ⬜ 随物理凭证（Q3 铁律维持）。首窗=**2026-09-30 22:43 夜班轮**（首月末·预注册）。

## 一、触发窗与节律

- 触发=每月末日末轮执行（首窗 2026-09-30 22:43）；月=自然月；归集粒度=司×轨×月（O-1820 ④ 同律）。
- 执行者=OSLoop 队列步（25min 预算内机械跑·命令全只读零新台账）。

## 二、归集源清单（九源·全只读·S6=v1.1 增·S7=v1.3 增·S8/S9=v1.4 增）

| # | 源 | 命令/指针 | 首窗预演值（2026-09-29 12:2x 实跑·真实只读） |
|---|---|---|---|
| S1 | 配额轨 | `python Tools/cost_ledger.py quota-summary` | totals 全 0（issued/consumed/cost_issued/cost_consumed=0·计划态零真发）·rails=「real exchange needs 3 prerequisites」 |
| S2 | 云端轨 | `python Tools/cost_ledger.py cloud-summary` | rows=0·pending_bill_rows=0·J4 零接线维持（真接线随首笔实缴云账单） |
| S3 | 借算轨 | docs/ops/borrow-compute-lane-predesign-v1.md §七 | state/borrow-* 不存在=零生成（工程落地随 T-28 激活窗 10-05 呈批后·预注册门不跳） |
| S4 | GPU 采集器 | `python Tools/gpu_idle_collector.py report` | n=49 avg=8.3% max=100% kpi=FLAG（<30% 唯一点名阈值挂 3 日滚动基线 09-27..29 n=107 avg=13.1%·C-20260929-02）·machine=bm-a 分机行 |
| S5 | serve 自用面 | qa/smoke-*.log eval_count 序列+E12 锚（state/e12-anchor） | 自用面=**非商业面**注记常驻（计量真值=Ollama eval_count L0·tiktoken=估算口径注记） |
| S6 | GPU 能耗剖面（v1.1 增） | `python Tools/gpu_energy_profile.py report --days 31 --baseline-range 2026-10-01,2026-10-08` | 10 月窗实测（10-11 00:33 实跑）：14 日 12.9621 kWh·日均 0.9259·安静带基线〔10-01..08〕0.8727→借用日 delta 10-09 +0.795/10-10 +0.5016（板卡面·**口径 A 对照面非结算**）·证据 state/gpu-energy-profile-20261011-003321.json |
| S7 | L1 本地推理轨（v1.3 增·T93） | `python Tools/cost_ledger.py l1-summary --month <月>`（工具面=month_end_collect.py s7_l1 并入·tech T113） | 10 月窗实测（10-17 13:0x 实跑）：rows=1·amount 0.000021 Mtok（pending 1/converted 0）·月闸 cap=200.0 used=0.000021（**口径 B·usage 回执=唯一 token 源 J2 同律**·单位价恒空=l1-settle=N1 唯一回填通道）·源不可用=降级行如实 N2·证据 state/month-end-202610.json |
| S8 | 私域分发/归因轨（v1.4 增·M87 distr） | `state/distr-ledger.jsonl`（工具面=month_end_collect.py s8_distr 只读·tech T127） | 10 月窗实测（10-26 22:4x 实跑）：rows=1·attribution pending=1·conv 关联命中 0·lane bilibili·live 行 0（publish queued 1·reach 0）·**非收入行=结算归口径 B**·live 0 且 publish 恒 queued=计划态如实面 |
| S9 | 交付调度轨（v1.4 增·M88 stage-5） | `state/delivery-ledger.jsonl`（工具面=month_end_collect.py s9_delivery 只读·append-only 末行状态即单终态） | 10 月窗实测（10-26 22:4x 实跑）：events=3·交付单 1·终态 delivered=1·route resident-qa-8792·max attempts 1·live 行 0·**非收入行=结算归口径 B** |

## 三、月末行格式（轮账本月末附加行）

- 商业面：tokens_mtok=0（计划态如实）·禁任何估充。
- 自用面：eval_count 累计值+「**非商业产能·禁入结算产能基准**」标注（试点期回溯定价法同律）。
- 成本三要素：原值/电价/利用率 全 ⬜（物理凭证未到·Q3 铁律=电价唯一合法源实缴账单·OSS 锚点永不入结算式=E19 定谳）。
- 电费要素分解（v1.1 增·S6 源）：月 kWh 实测（S6）×实缴单价 ⬜+借用窗归因分列（10 月窗=10-09 +0.795/10-10 +0.5016 借用日 delta vs 安静带·内含 LoRA 训练 0.7948〔E59 单列〕+KF 批尾段 +0.0969〔23:04 中期快照 1.2774→终值 1.3743 差值法〕·borrow J4 未激活零记账维持 M48 ③ DRY-RUN）——**口径 A 对照面非结算**（结算唯一=口径 B·Q3 铁律）。安静带校准候选（v1.2 增·E63）：排除 10-08 测试窗日重算基线 0.8063→借用三日 delta +1.9608（上修 +0.1992）·月末人工归因第二候选基线。
- 单位成本 v1：结构式=M2 §二 核心式·落值双门见 §六 N3。

## 四、分级（计划态月 vs 试点后月）

- 计划态月（首窗）：**轻量月末行**（§三 式·全 ⬜ 如实）——产出=轮账本一行+本单 §五 对照。
- 试点后月：**全量合账报告**（司×轨×月·cloud lane §五+borrow lane §五 月度合账面·呈财务部/决策轮·BC-P-10 报告面承接）。

## 五、首月预演（2026-09-29 12:2x·真实只读实跑）

- S1-S5 五源机械链全通（§二 表·输出原文在案）；数据面=计划态零如实（配额/云端/借算三轨零行·GPU=门控型待机豁免态 DRY-RUN 至 10-05）。
- 判定：**链路就绪 5/5·数据面如实零**——首窗 09-30 仅复跑 §二 命令+落月末行·无新推导零编造。

## 六、预注册判据 N1-N3

- **N1** 月末轮必跑本单；漏跑=下轮第一优先级补+日清记（QA 自验同律）。
- **N2 零编造律**：任何 ⬜ 不得以估算/外部锚/OSS 数据回填（口径 A=呈现/对照/预估面专用·R-24/E19 同律）。
- **N3 单位成本 v1 落值双门**=成本三要素物理凭证齐+首月真实商业 tokens 归集（回溯定价法·防编造产能基准=R- 原判据；先到凭证后到商业量的次序=按首月真实窗口如实回溯）。

## 七、验证声明（外证/内知如实）

- 外证：R-20260928-compute-cost-economics 换算链节+M2 定价确认包「单位成本 v1 ⬜」行+§二 三命令实跑输出（本轮轮内 log）+E12/E16 计量锚在册+quota/cloud 轨 selftest 现役。
- 内知如实：本司计划态零真发/零云发射/零借算；GPU 日均=门控型待机豁免态（商业物理件未通=P-2026-09-28-02 ③ 保护态）。
- 建议级标注：月末行格式与分级=本司建议级预注册·财务部/决策轮可改（改行=追加注记不删行）。
