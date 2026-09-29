# R-20260929-city3d-interior-bake-lane — City3D 室内烘焙批算力线（族 C 二轮·提前于 22:43 夜窗·O-029 承接）

> 溯源=O-20260929-029 机队全面并行令（今夜 P0 并行面 @BigCompute 切片=City3D 室内烘焙）+重构案 §四 @BigCompute=室内件烘焙批（AO+光照贴图离线算力）·交付判据=备货律 consumer_plan 指名（B-CITY3D-01 已 POOL_GREEN 在册）。本件=批承接的**预备性证据+管线预注册**（需求到达时承接·探针面非提前派活·bm-a 黑名单维持）。

## 〇 立项三问（P-65）
1. **消费方**=City3D 城市重构线（R2 建筑装配/R4 光影成色窗·FluxVerse 主体施工）——批卡 B-CITY3D-01 指名在册；触发窗=R2 样板间到达（ON_DEMAND）。
2. **单位经济**=离线烘焙=非实时批工作载（GPU/CPU 双路候选）·记账=工单号 tx_id 幂等入 cost_ledger 口径 B（借算/云轨同律）·工单计量面=烘焙时长×机档（成本三要素代入随首笔实测 ⬜ 禁事前编造）。
3. **合规**=资产只读引用（FluxVerse 仓零写入·探针只读扫描）·实弹面=labbench 隔离区（O-023 先例·零碰司仓）·bm-a 自动派活黑名单维持（本批=令牌派单手动承接·C-20260929-02 7.4）。

## 一 预备性探针实测（2026-09-29 19:35·Tools/city3d_bake_readiness_probe.py）
- selftest **4/4 PASS**；实测 **VERDICT=ASSETS_READY_FOR_BAKE_SPIKE**（state/bake-readiness-20260929-1935.txt·命令可复跑）。
- 室内四包=**23,821 文件/0.50GB**：AD-002=10,871/0.19GB·AD-008=5,198/0.20GB·AD-021=2,920/0.05GB·AD-035=4,832/0.06GB。
- 关键室内件族实锚（.prefab 计数）：**AD-021 模块壳** ExteriorWall_GroundFloor=12/InteriorWall=7/House_Door=3/Stairs=17；**AD-035 光池** Ceiling_Panel_Light=2；**AD-002 大空间** Base_Floor=2/Base_Wall=16/Wall_Door=4/Ceiling=45；**AD-008 壳** SM_Bld_Base=38/Wall_Door=5。
- **命名口径发现**（件名账对齐回执候选）：重构案 §一「AD-008 Base_Buildings」在包内实名=**SM_Bld_Base_\*** 系列——探针首轮零命中→实测查证=命名差非缺件→模式修正复跑全绿（如实记录·呈 City3D 线件名账注记候选）。

## 二 烘焙批管线定谳（建议级·判据预注册）
- 主径=**Tuanjie 批模式烘焙**（`-batchmode -executeMethod` 烘焙入口·labbench spike 项目隔离实弹·室内样板间=AD-021 模块壳围合+AD-035 灯板光池）。
- 双路对照=GPU lightmapper（bm-a 4070S 12GB 档·共卡纪律 headroom ≥1.5GB J2 同律）vs CPU progressive（32 核档）——首笔实测出**时长/峰值/质量**三指标 ⬜。
- 判据预注册：**J1** 单样板间烘焙 ≤30min；**J2** 贴图内存/显存峰值记录在案（让路律面）；**J3** AO+光照贴图产物交消费方验收（B-CITY3D-01 acceptance 口径）；**J4** tx_id 幂等入账（口径 B·同单重跑零新增）。
- **机械链预建毕（09-29 22:43 夜班轮）**：Tools/city3d_bake_spike.py（scaffold/selftest 8/8）+Tools/bake_spike_assets/（BakeSpike.cs 批模式入口+run_bake.ps1）+labbench 工程（AD-021/035 两包 7,752 文件逐包落位）——实弹=净窗一命令；**首窗窗检判负**（10 Tuanjie 实例夜班峰窗=O-027 令下 22:43 反转·净窗闸 BLOCKED exit 3 机械执法·J1/J2 指标窗污染判负顺延·R-43 承载）。

## 三 应用表（6 行·观察 3/6）
| # | 用途 | 承接面 | 状态 |
|---|---|---|---|
| 1 | R2/R4 室内烘焙批承接窗 | 批卡 B-CITY3D-01（ON_DEMAND） | 备货在册 |
| 2 | 资产在位性对账（R2 硬依赖前置） | Tools/city3d_bake_readiness_probe.py（现役探针） | ✓ 实测 |
| 3 | labbench 烘焙样板间实弹 | 队列 M25（22:43 夜窗候选） | 观察 |
| 4 | 烘焙工单记账接线 | cost_ledger tx_id 轨（J4） | 观察 |
| 5 | 件名账命名口径回执 | City3D 线注记候选（§一发现） | 观察 |
| 6 | GPU/CPU 双路成本对照 | 首笔实测后入族 B 换算链 | 观察 |

## 四 验证声明
- 实测面：selftest 4/4+实跑 ASSETS_READY 出数（§一=state/bake-readiness-20260929-1935.txt 直出·可复跑）；烘焙时长/质量/成本三指标**未实测**（编辑器零实例在跑+样板间未建）⬜——本件=预备性证据+预注册，**非烘焙能力宣称**（诚实律）。
- 建议级：§二 主径/判据=建议级；消费方验收权=City3D 线；数值冻结权=循环侧（P-65 同律）。
