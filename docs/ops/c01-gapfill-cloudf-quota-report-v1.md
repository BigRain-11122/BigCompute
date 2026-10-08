# C-01 缺口面补呈件：cloudF 聚合面 + 单位配额产出对照（BigCompute/bm-a 面）v1.0

> 承接=D-20261007-06① 拍板转办（C-20260929-01 判据①「cloudF 集团聚合面数据未呈」+判据⑤「单位配额产出对照 vs 生效日重开基线零呈报」→补呈窗 ≤10-14·@BigCompute/bm-c cloudF 面）。
> 本件=BigCompute/bm-a 面；bm-c（BigMoney）面=其 F-20261008-01 cloudF 聚合行已 presented（D-20261008-05③ 收讫·候 10-14 并卷·本件只读引用零代填）。
> 组装日=2026-10-08 12:4x（午班槽）·回执=BC-F-20261008-07·零新台账零新采集（纯只读复用既有件+两命令实跑）。

## §一 cloudF 聚合面一行（缺口①回填）

**cloudF 聚合行（bm-a/BigCompute）：`machine=bm-a | cloud_entries=0 | attribution_audit=CLEAN-ZERO-STATE | api_full_history=76_calls_all_readonly_zero_cloud_generation | monthly_cloud_ledger=2026-09 rows=0/amount_mtok=0/billed=0 | verdict=云端生成面零发射·记账轨零行·三径闸在役`**

| 证 | 数据（实跑/实读） | 证据指针 |
|---|---|---|
| ①attribution 审计 | `rows=0 violations=0 reason_notes_missing=0 verdict=CLEAN-ZERO-STATE`（12:45:28 实跑·O-2026-0930-014 执法面） | state/cloud-attribution-audit-20261008-124528.json |
| ②轮账本 api 全史聚合 | 168 轮·165 轮带 api 字段·api 累计 **76 次全只读外呼**（web fetch/检索类·零云生成）·22 轮 api>0·local 累计 563·10-05→10-08 增量=0（P-09 维持） | state/rounds.log（api 字段正则聚合·M50 §一同法复跑） |
| ③月度云账面 | 2026-09：cloud rows=0·amount_mtok=0·实缴账单 0（J4 首笔实缴账单接线窗前零行=结构性零态非漏记） | state/month-end-202609.json s2_cloud |

bm-c 面（跨司只读引用）：bigmoney F-20261008-01 已呈现其 cloudF 聚合行（278 单分实体+attribution CLEAN）——两行并卷即 C-01 判据①「集团聚合面」呈现面收口。

## §二 单位配额产出对照 vs 生效日重开基线（缺口⑤回填）

**本司三数（对照面主体）：**

| 面 | 数值 | 口径与证据 |
|---|---|---|
| 月度自用 tokens | **666**（2026-09 实测） | Ollama eval_count 口径·n_logs=84·month-end-202609.json s5_selfuse；2026-10 归集=10-31 N1 一命令窗 |
| 商业配额 tokens | **0**（计划态） | s1_quota issued/consumed_mtok=0·真发放记账轨随 P-67 试点/开店物理件（T2 blocked 如实） |
| 单位配额产出比 | **不可算（分母=0 如实）** | 商业配额消费为 0→产出/配额比无定义；禁编造分母·对照以本地产能基线呈现（下行） |

**本地产能 SLA 基线（E28 系列·C-20260929-01 生效日 09-29 重开·滚动窗实跑）：**

| 系列节点 | 净窗 mean | n | 备注 |
|---|---|---|---|
| 生效日重开首件（09-29 14:19） | 93.93 tok/s | 9 | E28 立件·CV 5.8% |
| 九月归集快照（09-30 22:49） | 92.67 tok/s | 24 | N1 采拧面 |
| 首全量重算（10-01 00:36） | 96.15 tok/s | 24 | BC-P-15 双判据升级后 |
| **十月滚动窗（10-08 12:49 本日实跑）** | **100.61 tok/s** | **77** | CV 9.3%·P10 89.81·span 09-28→10-08 12:32·竞争窗 74.62=0.74x |

- 换算注记（建议级·非结算）：净窗 100.61 tok/s → 1 Mtok 本地产能时间 ≈ 2.76h（9,939s）——口径 B 月度归集面与配额轨对账基础值。
- 判读：C-01 判据⑤原缺口=「对照零呈报」→本节即对照面首呈：**计划态（商业 0·分母如实为零）+ 自用 666 tok/月 + 生效日重开基线滚动至 100.61 tok/s（数据龄 0h）** 三面齐呈；商业配额开通后单位配额产出=「配额消费 Mtok × 实际产出」vs 基线产能窗对账，接线面=M7 记账轨+M44 税负行同环。

## §三 判据与呈报面

- **预注册收口判据**：J1 cloudF 行含三证指针全=✓；J2 对照段含三数（自用/商业/基线）+分母态如实=✓；J3 bm-c 面只读引用零代填=✓；J4 零新台账零新采集（纯只读复用+两命令实跑：cloud_attribution_audit audit+serve_sla_baseline series）=✓。
- **数据鲜度**：①12:45:28 实跑（本日）·②全史聚合（含至 10-08 轮）·③09-30 归集件·基线=12:49 实跑（数据龄 0h）——呈报窗 10-14 开口时如需复刷=一命令面（audit 重跑+series 重跑）。
- **BC-P-52 判负路径①触发**：数据面纯摘录已够→零工具合法（cloud_attribution_audit.py report 扩展 lane 关闭·判负留痕合法）。
- 呈报面：HQ-FEEDBACK 回执 BC-F-20261008-07（本日呈出）→补呈窗 ≤10-14 兜底维持（HQ 核销随决策轮）。
