# BigCompute 席位4 商业市场证据包 v1（O-2026-0930-028 云端本地双轨制定谳令·10-07 治理日回访窗件）

- 提交面：10-07 治理日三令同窗回访（O-025 工作流迭代令/O-027 SOP 建制令/O-028 双轨令）·席位4=商业市场面（BigCompute+BigDomain 功能制·本司侧）
- 数据时点：2026-10-05 12:46-12:5x（gpu_idle_collector 三命令+轮账本/烟测档/git log 直采）·10-07 回访窗开口时按 §五 J1 复刷
- **v1.1 复刷数据节**（2026-10-06 12:45:33 四段直出·api=0 轮 127/烟测 128 件全过 126/3 日滚动 n=243 avg=2.4% FLAG/画像 12:45:36 复测逐字吻合）=docs/ops/governance-day-1007-report-package-v1.md 件一〔10-07 窗开口 J1 再复刷 ≤12h·数字变动随包升版〕
- 红线：零新机制零新台账·全部数字=真实命令输出直采或 state/qa 既有件行级引用（指针逐条在件）·判负/等待面如实

## 一 本地承接率面（O-028 ①本地优先轨承接实证·P-09 零云生成链）

- 轮账本 api 字段全史聚合（12:46 直采）：144 行中可解析 143 行=api=0 轮 **121**（84.6%）+api>0 轮 22（合计 **76** 次外呼）；22 轮全为只读类外呼（OSS 收获轮 GitHub API 元数据/官方源核验 web_fetch/行业基准 XBRL 取证〔E42-E44 声明 UA 通道〕）——**零 token 生成发射**（生成路径序=池内直用→本地→云端最后+草稿占位禁云端·mandate v2.6 三径闸现役·云发射态变更先过三径闸）。
- 云端成本轨零生成态三证：state/cloud-cost-ledger.jsonl rows=0（J4 零接线零编造）；attribution 审计 CLEAN-ZERO-STATE violations=0（state/cloud-attribution-audit-20260930-184715.json）；cloud-entry 结构性三必填+--task-ref required=True（T37·O-014 执法相位）。
- 本地生成在役面：Ollama qwen2.5:7b-instruct Q4_K_M GPU 常驻（keep_alive:-1·qa_smoke 每轮真响应+常驻巡检）；净窗吞吐基线 mean≈94 tok/s（E28 series n=9 mean 93.93/CV 5.8%+E36 预审计 n=17 mean 94.6/CV 6.5% 双证在册）；8792 居民问答服务端在役（E40/E41·schtasks 5min 保活+health 200·CEO 观测窗第一检查面）。
- 结论：生成/产出本地承接率 **100%**（零云端 token 生成·零云端成本行）——O-028 ①「质量达标前提下一律本地」本司面承接在役。

## 二 质量对照面（O-017 ④ 判据增补：质量对照不降=TJ 通过率+正式件产出节奏环比·10-07 同窗回访）

- 测试通过率（TJ 生态口径本司映射=烟测族+机检 selftest 族·本司产线含 Tuanjie 批模式工程件）：
  - 烟测族机检（T48·12:5x 直采）：qa/smoke-*.log **121 件=全过判定 120**（119 件标准「smoke verdict: N/N PASS」+1 件早期格式 4/4 PASS〔smoke-20260928-0929〕）+探针级部分判负 1（smoke-20261003-0003=5/6〔gpu collector 探针〕·当轮如实落档·次轮 00:27 复跑恢复=缺项即修律执法）——**全过率 120/121·整体判负 0**。
  - 机检 selftest 族全绿在册：fulfillment 44/44（09-28）·cost_ledger 44 检（09-30 T37 回归）·compliance_gate 59/59 锚（09-30 M29）·sop_coverage GREEN violations=0（10-05 12:16·state/sop-coverage-20261005-121645.json）·round_score 47/47（09-30 M28）·Tuanjie 批模式件=bake readiness 探针 4/4+bake spike scaffold 8/8（09-29/09-30）。
- 正式件产出节奏环比（12:4x git log 直采）：09-29 起日 commits=**27/41/7/11/14/16/4**（10-05 计至午班）·日双轮+哨兵轮全勤；周产出分 130（2 分件×47+1 分×36·T41 10-01 实测·C-02 ③ 周产出分 ≥3 判据远超）。
- 结论：省的只有浪费面（O-017 ② 结构优化四项目标承接）·质量档未降（烟测/机检全绿面在证）·速度未省（轮次全勤）——「不省质量、不省速度」本司面实证。

## 三 满载行面（rolling 30min+loadline·10-05 12:46 直采）

- loadline：busy>=30%=**0.0%**/quiet<10%=**100.0%**（n=51·avg 0.0%）｜in_flight=**0/6 卡**（备货池 DRY-RUN 至 10-05 复核窗·M46 呈批包 v1.1 在案·M48 等裁定）｜GREEN-IDLE 点名=FLAG（3 日滚动 10-03..05 n=243 avg=2.0% vs 30% 唯一点名线〔C-02〕）｜7 日对标 n=616 avg=5.2% BELOW-BENCH（R-C1 50% 结构线=差距线·非点名线）｜rolling 30min=IDLE。
- 门控型待机声明：商业物理件未通（BLUEPRINT §五.5·开店物理件 CEO 面）=保护态豁免如实（非违规闲置）；真派活激活面=10-05 复核窗呈批待 CEO 裁定（M46 四决策项①）。
- P-32 三字段：cpu_util_pct=4/total_ram_gb=93.6/prod_lanes=6（alive 2 常驻+standby 4 lane 备货）。
- 机面可借双探（D-20261004-04）：**NOT-BORROWABLE[VRAM|COTENANT]**（vram_free 1980MiB<6144MiB 线+编辑器共租 1）——他司借算挂牌面=机面不可借如实·不挂牌（R-41 分机行字段）。

## 四 机器画像数据源面（M50 ① 工程面在册·O-028 画像×模型档×任务三映射底座）

- machine_profile 实测双证：10-04 首测（state/m50-machine-profile-20261004.txt）+10-05 12:46 复测=ram_total 93.6GB/vram_total 12282MiB/32 核/ram_avail 47.6GB/双利用率 cpu 0.7%·gpu 0.0%——与 O-028 画像三档「大内存档 bm-a RAM 93.6GB/VRAM 12GB」逐字/档位双吻合。
- 数据源承接=CPH4 双轨路由正典件（写手债归 @Biggame A 机窗·O-010③ 在案）·T44 borrowable 列/T46 profile 命令=同源双探·P-32 前置对齐毕。

## 五 鲜度与回访判据（预注册）

- J1 鲜度：10-07 回访窗开口时 §一/§三/§四 数据面复刷一遍（collector 三命令+轮账本/烟测/git 三 tally·鲜度 ≤12h·M47 同律）——数字变动即升 v1.1·零变动如实注记。
- J2 对账：轮账本 gpu 字段（bm-a 分机行实测）+api 字段周轮聚合=周日轮对账在役·本件引用面同源零双轨。
- J3 呈报：随 10-07 治理日三令同窗呈报（O-025 工作流迭代周报面/O-027 盘点清单 v1.0/O-028 本件）·席位4 表态面证据直引本件。
- J4 红线：本件=证据引用面零新采集机制·M48 CEO 裁定未至=满载行呈批消费步维持等待态如实。

> 验证声明：本件全部数字=2026-10-05 12:46-12:5x 真实命令输出直采或 state/qa 既有件行级引用；烟测部分判负 1 件如实注记（非全绿宣称）；零转述编造。
