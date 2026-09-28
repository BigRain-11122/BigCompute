# 履约管线 × 小店 SKU 十类映射表 v1.0（对接面演练件·计划态）

> 溯源：P1 队列 M6；源件=docs/ops/paypoint-alignment-matrix-v1.md §二（在册付费点 10 类·实读引用）+ Tools/fulfillment/README.md+pipeline.py（骨架基线·引用不复制）；任务板关联=T-20260924-03/T-20260924-11（42→44 测试现役）。
> 状态律：商业物理件未通前（BLUEPRINT §五.5）=计划态建设期——本件全为对接面设计值+合成数据演练，零真实交易动作（红线 2 维持）。

## 一、管线基线速查（引用不复制）

- 状态机：`pending → generating → done | failed（重试 ≤3 次）| manual_queue（ attempts 耗尽人工兜底）| refund_flagged（确认单 >24h 未解决→平台退款·权利先行：退款优先于再花费算力）`。
- 三适配器（开店=只换三件零改核心）：`OrderSource.poll()`（拉单·平台订单号→`order_id`=全链幂等键·支付态→`confirm_flag`）／`Generator.generate()`（产出 `Artifact`+`TokenUsage` 四字段 tier/tokens_local/tokens_api/api_reason）／`Deliverer.deliver()`（幂等交付→`Receipt`）。
- 账本：每次履约 2 行（`cost_item`=tokens+cost）·(order_id, cost_item) 幂等·schema 与 cost_ledger 同族·口径 B 结算唯一（A 禁结算=R-24 同律）。
- SLA 面：端到端 ≤30min 目标/P95 2h（`created_at→delivered_at` 直测零新增管线）。

## 二、十类 × 管线四站映射（对齐矩阵 §二 10 类全录·不删行）

| 付费点 | 履约类 | 进管线 | OrderSource 路由 | Generator 路由 | Deliverer 路由 | 账本轨 | 管线内合规叠加 | 现役态 |
|---|---|---|---|---|---|---|---|---|
| 算力包 19.9（入城门票双语义） | 配额发放型 | ✓ | 小店/平台内拉单 | L0 直通（无生成·quota-issue 调用即履约） | 配额入账+N3 透明凭证面（口径 A 呈现/B 披露分列） | 履约成本行（ledger_hook）+配额权利行（quota-issue/consume·月度预算闸）**双轨分立·tx_id 同键** | AIGC 标识（若含生成内容） | 待 M2-M3·物理件 blocked |
| 会员三档 19.9/49.9/99（29.9 入门档待 CEO 裁） | 订阅型 | ✓ | 平台内支付 | L0 直通（权益开通查表） | 权益激活+欢迎回执 | 订阅流水轨（H2 用例 44/44 在册·续费新 order_id 分立） | 自动续费合规=显著提醒+默认勾选禁令（service-continuity-terms v0.1） | 同上·定价确认权=CEO |
| 大厅增值 ¥9.9 加急 | 配额加急型 | ✓ | 平台内 | L0 直通 | 加急生效回执 | quota 加急轨 | 稀缺真实律（真实队列负载·禁虚构） | 随 M2 |
| 策略围观 9.9/19.9 | 场次型 | ✓ | 平台内 | 回测任务（L2 本地批处理池·BC-P-01 活源） | 场次入场回执 | 围观场次记账 | 非投顾提示常驻 | 随 M2·非投顾闸 |
| 身份 9.9-199 | 权利型 | ✓ | 平台内 | L1 查表（规格解码·近零边际） | 身份凭证发放 | 身份数据面行 | AIGC 标识叠加（凭证含生成元素时） | 随 M2 |
| B 端入驻 9,800-19,800/年 | 合同直签型 | ✗（非拉单面） | 直签合同（商务部） | — | — | B 端合同轨+数据授权条款行 | 合同六件（docs/legal） | 待 Phase 2 |
| 广告三档 2,000-10,000 | 合同直签型 | ✗ | 直签 | — | — | 广告位记账 | 成交面 SOP（56 号令原价口径） | 随参观端 M4 |
| 场地包场 | 合同直签型 | ✗ | 直签 | — | — | 同上 | 同上 | 随参观端 |
| 小店摆件三档（9.9 摆件/中档角色/高阶房间） | 生成型（全链） | ✓ | **抖音小店拉单（README 替换点 1·官方订单列表查询 API）** | L1 规格查表→L2 qwen2.5:7b 结构化蓝图→L3 GLM 预算闸回退（≤0.05 元/单）→Unity batch 渲染 PNG（摆件/角色）/MP4（房间） | Plan B=平台 IM 短链（order_id 哈希键）发货+平台上标记发货；Plan A=背包发放（BigDomain 账号体系落地后） | 履约成本行（ledger_hook 2 行·TokenUsage 如实回填=tokens 三列对齐） | AIGC 标识=办法第四条(五) 起始画面+第八条协议样式+第九条导出日志 ≥6 个月 | 小店筹备·类目/资质物理件 blocked |
| 打赏（直播间） | 非订单型 | ✗（无履约义务·平台结算对账） | 平台打赏通道 | — | — | 平台结算对账 | 打赏域限=不兑换功能性权益律 | 直播=BigStream PLAN P5 待署名 |

## 三、演练结论（三族群+门禁实证）

- **三族群**：①全链生成型 1 类（摆件）=管线主对象（Generator 是真实负载）；②短路履约型 5 类（算力包/会员/加急/围观/身份）=进管线但 Generator=L0/L1 直通或回测任务（履约=权利面动作非生成物）；③管线外型 4 类（B 端/广告/包场/打赏）=直签或平台结算轨，不进拉单-履约链（管线只接「平台内/小店电子订单」语义）。
- **合成数据演练实跑（2026-09-28 13:4x 本窗·state/sku_drill_m6.py·运行态 gitignored）**：六类进管线各 1 单+1 摆件未确认单 → `RUN1 delivered=6`/未确认单保持 `pending`（confirm 门禁=未支付零算力花费实证）/`RUN2 delivered=0·skipped_done=6`（幂等重放零新增）/账本 `rows=12·unique(order_id,cost_item)=12`（6 单 ×tokens/cost 两行·零重录）→ **M6 DRILL: PASS**（exit 0）。
- **适配器改造面=开店只动三处**（README 替换点 1/2/3）：OrderSource→抖音官方订单列表查询 API 拉单（order_id/confirm_flag/created_at 三映射）；Generator→L1/L2/L3 三问路由+Unity batch；Deliverer→Plan B 短链 IM 发货。核心编排（重试 ≤3/人工兜底/24h 退款扫/账本钩）零改。
- `sku` 字段口径：管线 `Order.sku` 为自由串（本演练用品类码前缀 QP/MB/RUSH/WG/SF/BZ 仅为可读性设计值·非编码正典——正式 SKU 码随开店类目批由商品运营部落）。

## 四、预注册判据与验证声明

- 判据（开店后回填）：①首批真实订单按本表走链零改核心（只换三适配器+ledger_path 一路径）；②六类管线内订单状态机全流转与 44/44 测试基线一致；③摆件生成单 TokenUsage 四字段如实→账本 tokens 三列对账零差。
- 验证声明：2026-09-28 落盘——两源件实读（对齐矩阵 §二 10 类+fulfillment README/pipeline.py 签名段）；演练=纯合成数据零网络零真实交易；管线外四类=按出口通道语义分类非能力缺失（直签合同面另有合同轨）；数值面全 ⬜ 随开店物理件（M1 清单在册）；本件=接产对齐设计件零新立法零定价动作。
