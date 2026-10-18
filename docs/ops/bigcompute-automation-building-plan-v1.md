# BigCompute 九部门自动化建设案 v0.9 呈报稿（九司方案呈报令 @BigCompute 承接·M73 步①+②+③）

> 令源=orders 2026-10-18 20:48/20:54/21:11 三行（九司自动化扩面→全量梳理→方案呈报令）·正身=cph4/fleet/GROUP-AUTOMATION-PLAN-v1.md ⑤·统一骨架=现状五要素对照→缺口→建设内容→验收判据→排期。**呈报窗=建设案细化 ≤10-25（随委员会三合一·P1 交面即可）·首线实装验收 ≤10-31**。红线=商业物理件未通前禁一切真实交易动作（对外成交环=计划态簿记）；发布决定权=CEO 保留面。

## 一、五要素现状对照（世界一流基线）
| 要素 | 现状（在役实证） | 判 |
|---|---|---|
| ①循环驱动 | OSLoop 日双轮（12:43 午班/22:43 夜班）+哨兵触发轮（orders 变更 15min 扫描唤醒·P0 ack ≤30min）；iteration_loop v2 活性门控预算（25min 墙钟击杀已根治·10-18 判例）·10-18 连轮 exit=0 实录 | ✓ |
| ②自领活+自愈 | 三线队列（main/tech/explore 各 ≥10 条）+idle_selfcheck 15min 自检（J1-J11）+任务自愈族（CoderServe PT10M/ResidentQA 5min/GPU-IdleWatch 15min/OrderSentinel/CleanWindowProbe）+夜腿 AUTO-REVIVE（D-20261018-01 扩腿·T117 留真面首观察 PASS） | ✓（业务单自领面=P2 环内化） |
| ③质量闸在环内 | 批池 validate J1-J6（POOL_GREEN·月窗回访 ~11-0x）+合规硬门 paypoint-compliance-map v1（15 付费点×五列·59/59 锚·机检 compliance_gate_check·A 档上线前过表=硬门）+QA smoke 每轮 8 探针+账本 selftest 族全绿 | ✓（P1 成交环内闸=步② 形式化） |
| ④日进化 | 日清区每轮 ≤3 行压缩态+结果三行数字接线（C-20261018-05·已上线/已交付 CEO/在制）+判例→闸升格链（检查单→lint→QC 检项→脚本断言）在役 | 🟡（好/坏总结制度化=P2 补位） |
| ⑤定位律 | 轮账本 queue/gpu 行+status-export 三行（当前活/最近实物/下个里程碑）+任务板路径直给 | 🟡（业务指标直连=P1 环内接线） |

## 二、P1 现状盘点（既有资产→两环接线清点）
### 环 1 对外成交环（询价→自动报价定价→成交簿记→交付调度）
| 环节 | 既有资产 | 接线态 |
|---|---|---|
| 询价入口 | SKU 十类（paypoint-alignment-matrix v0.1）·订阅两档定价包（pricing-confirmation-package-29.9 v1+quota-design 49.9-99 v1）·B 端直签价目带（b2b-custom-report-pricing-band v1） | 计划态（商户号/小店类目=CEO 物理件） |
| 自动报价 | 计价双口径（口径 A 呈现/口径 B 结算唯一）·quota_design.py 参数化计算器（selftest）·vat_break_even.py 税负临界（25/25） | ✓ 机制齐·数值槽 ⬜ 随 CEO 定价确认批 |
| 合规闸 | compliance_gate_check.py 机检（59/59 绿+3 ⬜ 槽随定价批）+法务六件（官方原文级锚 12+ 项·核验批件在册） | ✓（A 档上线前过表硬门） |
| 成交簿记 | cost_ledger.py 记账轨五轨（配额/订单/云端/借算/L1·tx_id 幂等+月度预算闸+selftest 全绿）+store-ledger-wiring-predesign v1（15 字段映射）+全链路 dry-run 6/6 PASS（E11） | ✓ 机制齐·真接线随开店物理件 |
| 交付调度 | fulfillment pipeline 44/44 PASS（三族群路由）+SKU×四站映射 v1（M6）+8792 居民问答服务端（5min 保活）+批池派活面（DRY-RUN 观察态） | ✓ 骨架齐·开店只换三适配器 |
| 售后/月末 | 退款 provision 轨+续费提醒触发可审计+month_end_collect.py S1-S7 七源一命令（selftest 27/27·N1=10-31 22:43 夜班） | ✓ |

### 环 2 粉丝私域内容分发环
| 环节 | 既有资产 | 接线态 |
|---|---|---|
| 内容源 | 算力透明凭证（N3·BC-P-02 回流在册）·B 端数据报告族（N7 脱敏标准+去标识化速查 v1）·行业基准四源取证（E42/E43）·成本经济学 R- 系列（对外叙事面） | ✓ 源在册 |
| 平台矩阵 | 社群运营部（七平台矩阵+漏斗四环节+三岗卡·T-17）·B 站账号运营线（bm-b 车道） | 🟡（企微自动化=T-18 blocked 物理件 CEO 面） |
| 发布/承接 | 账号写操作=CEO 保留面（合规介入位=发布前·发布决定权 CEO）——环自动化止于备稿+队列+簿记·发布留人工 | 红线注记 |
| 归因记账 | attribution 必填在役（cloud/l1 轨·cloud_attribution_audit CLEAN-ZERO-STATE）；UTM/私域流水轨=§七 预设计毕（distr rail 15 字段+归因闸 G1-G3）·实装随 P1 实装窗 | ✓ 预设计（10-19） |

## 三、P1 两环环图（骨架 v0.1）
```
环 1 对外成交环
[询价入口] 小店 SKU/订阅档/B 端直签
   ↓ 参数提取（sku_id/规格/数量）
[自动报价] quota_design+vat_break_even → 报价单（口径 A 呈现·数值槽随 CEO 定价批）
   ↓ [合规闸] compliance_gate_check → 非 GATE_GREEN 禁放行（A 档硬门）
[成交簿记] cost_ledger order/issue/consume（tx_id 幂等·口径 B·月度预算闸）
   ↓ [交付调度] fulfillment 四站三族群路由 → 8792/批池/直签外发
[售后] refund provision+续费提醒 → [月末归集] month_end_collect S1-S7
   ↺ 单位成本 v1 → 定价包回填（环内闭环）

环 2 粉丝私域内容分发环
[内容源] 透明凭证/数据报告/基准叙事（本司正典只读引用·零新采集）
   ↓ [素材适配] 七平台档位适配（格式/长度/标签）
[发布队列]（发布=CEO 保留面·环止于备稿+队列）
   ↓ [私域承接] 评论区/私信（客服自动化=P2）
   ↓ [归因记账] 流水轨（UTM·attribution 必填）→ [反哺] 询价入口 → 环 1
```
### 环内巡检步（步② 细化·P1 实装期入轮·每轮机械面·断言档位律=全命令可机械核验）
| 检 | 命令 | 过线判据 |
|---|---|---|
| C1 簿记零断账 | python Tools/ledger_continuity_probe.py check | LEDGER-CONTINUITY-GREEN 或 PLANNED-STATE（J4 演练态）；RED=断链当轮 P1 修 |
| C2 交付链 | python Tools/fulfillment/test_pipeline.py | 44/44 PASS |
| C3 合规闸 | python Tools/compliance_gate_check.py（机检门） | GATE_GREEN 全锚（A 档上线前过表=硬门） |
| C4 账本底座 | python Tools/cost_ledger.py selftest | PASS |

- 巡检步入位=P1 实装窗（10-26..10-31）起 OSLoop 收尾步 QA 面并跑 C1-C4，verdict 入轮账本 queue 行；断链（任一 RED/FAIL）=当轮修复第一优先级。
- **C1 探针正身=Tools/ledger_continuity_probe.py**（10-18 落地）：五轨对账=行可解析/键唯一〔order_id+tx_id 幂等执法完整性·绕路 append 即 RED〕/date-month 一致/金额非负/quota 消费按时间重放无负余额/口径 B 唯一/pending-bill 一致性〔pending 态不得带 cost_b_cny〕/cloud-l1 attribution 结构必填——import cost_ledger 零 schema 重复（单一正身）；selftest 9 夹具全绿；**真实五轨首跑=LEDGER-CONTINUITY-GREEN violations=0**（rail_rows cost0/quota0/cloud0/borrow0/l1=1·J4 计划态如实）·证据=state/ledger-continuity-20261018-234732.json。

## 四、缺口清单
1. 物理件（CEO 面·零催办）：商户号/小店类目/账号 → 成交环真接线 GATE（未通=J4 演练态）
2. 定价确认批（CEO 面）：两档数值落值 → 报价单数值槽+映射表 ⬜ 槽清零
3. 环内闸形式化：成交环巡检步+簿记零断账探针——✅ 10-18 提前毕（§三巡检步表 C1-C4+探针 selftest 9/9+真实五轨首跑 GREEN violations=0·缺口 3 探针面收口·巡检步入轮随 P1 实装窗）
4. 私域归因字段：UTM/流水轨 schema 加性扩展预设计——✅ 10-19 步③ 毕（§七·实装随 P1 实装窗）
5. 客服自动化+渠道管理环+九部门全环联通=P2（≤11-15·另窗）
6. 企微物理件=T-18 blocked 维持如实

## 五、验收判据（预注册）
- **J1** P1 两环 72h 无人值守自续（断链自愈·巡检步零人工）
- **J2** 成交簿记零断账（每单 tx_id 幂等+轨对账 0 差·dry-run 演练面同判）
- **J3** 环内闸 GATE_GREEN 100%（合规过表+预算闸+QA 探针全绿）
- **J4** 物理件未通期间=计划态簿记演练（合成数据·零真实交易）——真接线验收随开店窗重跑 J1-J3

## 六、排期（分期交·P1 面即可）
| 窗 | 交付 | 判 |
|---|---|---|
| 步① 10-18（本窗） | 现状盘点+两环环图骨架（本件 v0.1） | ✓ 本轮落盘 |
| 步② 10-18（提前） | 环内巡检步+簿记零断账探针落地（§三表 C1-C4+探针 9/9+真实首跑 GREEN） | ✓ 本轮落盘 |
| 步③ ≤10-24 | 私域归因字段+簿记轨加性预设计（缺口 4）→v0.9 呈报稿 | ✓ 10-19 提前毕（§七） |
| ≤10-25 | v1.0 呈报（随委员会三合一） | 呈报窗 |
| 10-26..10-31 | P1 实装（物理件到件即接线·未到=J4 演练态）→10-31 验收读数 | 实装窗 |
| P2 ≤11-15 | 渠道管理环+客服自动化+九部门全环联通 | 另窗 |

## 七、步③ 预设计：私域归因轨（distr rail）加性扩展（缺口 4·2026-10-19 落盘）

> 性质=预设计件（E21/E23 同模式）：schema+闸+判据冻结先行；实装=P1 实装窗（10-26..10-31）首日 cost_ledger 加性扩展两命令（BC-P-93 lane 候选·needs-based）；物理件未通=J4 计划态（合成演练行 only·零真实流水）。

### 字段 schema（15 字段·分轨 state/distr-ledger.jsonl·零碰既有五轨）
| # | 字段 | 必填 | 语义/律 |
|---|---|---|---|
| 1 | tx_id | ✓ | 分发批次号（幂等键·D-<yyyymmdd>-<seq>·重复 append=拒绝） |
| 2 | ts | ✓ | 落账时间戳 |
| 3 | consumer | ✓ | 消费方（attribution 单字段必填同律·内容线/部门名） |
| 4 | lane | ✓ | 分发渠道（七平台矩阵枚举·T-17 对齐：douyin/bilibili/wechat/qq 群族/xiaohongshu/kuaishou 等） |
| 5 | content_ref | ✓ | 素材件指针（本司正典只读引用·透明凭证/数据报告族） |
| 6 | publish_state | ✓ | queued→published（发布=CEO 保留面：环止于备稿+队列·常态终态=queued） |
| 7 | utm_source | ✓ | 归因键 1（G1） |
| 8 | utm_medium | ✓ | 归因键 2（G1） |
| 9 | utm_campaign | ✓ | 归因键 3（G1） |
| 10 | audience_seg | ○ | 受众段（漏斗四环节对齐） |
| 11 | conv_tx_ref | ○ | 成交轨 tx_id 外键（环 2→环 1 反哺机械面·⬜ 直至首笔真实转化） |
| 12 | attribution_status | ✓ | pending→attributed |
| 13 | reach_count | ○ | 触达数（回执面·合成演练=合成值标注） |
| 14 | receipt_basis | ✓ | 合成演练/真实流水回执（J4 计划态=合成演练 only） |
| 15 | caliber_note | ✓ | 流水轨=行为归因面非成本面·不与口径 B 混列（分离律·月末 S1-S7 不并账） |

### 归因闸三条（attribution gate·cloud-entry 结构性必填同律扩面）
- G1 结构必填：consumer/lane/tx_id/utm_source/utm_medium/utm_campaign 六键非空·缺一=拒绝落账
- G2 幂等：tx_id 唯一（同键重复 append=拒绝·orders 轨同构）
- G3 J4 计划态：真实流水回执前=合成演练行 only（首笔真实归因行随账号物理件+发布授权窗）

### 判据（预注册·实装窗验收）
- J5 归因闸 G1-G3 selftest 夹具全绿（合成 dry-run·temp 账本零污染）
- J6 conv_tx_ref 外键存在性检查入巡检步 C1 探针（首笔转化前=零外键行常态·C1 扩第六轨随实装窗=BC-P-92 同窗接线）

## 验证声明
既有资产=本仓在役件与 state/ 证据件直引（判据行内注）；五要素判定=10-18 轮账本/心跳/QA 实录；⬜ 项如实不造（数值/物理件/账号类随 CEO 窗）。
步② 机械面=Tools/ledger_continuity_probe.py（selftest 9/9+真实五轨首跑 LEDGER-CONTINUITY-GREEN violations=0·l1=1 行余四轨计划态零行=J4 如实·证据 state/ledger-continuity-20261018-234732.json）。
步③ 预设计面=本件 §七（distr rail 15 字段 schema+归因闸 G1-G3+五轨分离律+J5/J6 预注册·实装=BC-P-93 lane 候选随 P1 实装窗·预设计=设计值零编造行）。
