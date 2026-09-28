# 模型分发 manifest 工程预设计 v1.0（T9 前置件·零分发动作）

> 溯源：议程滚动件 2026-09-29 03:29 窗（二轮循环·族 D 增量）+R-20260927-model-distribution-discipline §Q3/Q4/Q6（L0-L3 通道分层+manifest 单一事实源+预注册判据——本件=其**工程化预设计**·零重立法）+tech T9（manifest 纪律工程面·随首批分发）+O-20260928-1815 ④（跨机远程通道 Tailscale 组网·CEO 装机授权已生效→首批分发窗逼近=分发日就绪度前置件）。
> 范围边界：manifest v1 正式台账落点=docs/ops/model-manifest.md（R- §Q4 口径·**仍随首批分发立件不变**）；本件=schema 预注册+SHA256 校验命令面+本机 draft 草案快照（§四=draft 非正式行·J4 分立律）。零下载·零分发动作·零跨仓写。

## §一 缺口定谳

- R- §Q4 已定纪律（manifest 单一事实源+`Get-FileHash` 判据级校验+两件分工：fleet-allocations=「哪台机配什么」/manifest=「模型件本体是什么」），但 schema 字段面/层摘要结构/校验双通道/与 O-20260928-1820 ③ consumer_plan 咬合未工程化——首批分发日若无 spec 即临场造格式=纪律执行面缺口。
- 时间窗：Tailscale 组网物理件在途（bm-a MSI 暂存待 CEO 提权·bm-b/bm-c 照单自装）→L2 机队直拷主通道启用在即·manifest 先行=分发日零格式争议（J1）。

## §二 manifest schema 预注册（字段表 v1）

| 字段 | 型 | 必填 | 语义/纪律 |
|---|---|---|---|
| model_id | str | ✓ | registry 全名（如 registry.ollama.ai/library/qwen2.5/7b-instruct）=单一事实源键·行级幂等 |
| display_name | str | ✓ | ollama list 名（含量化档后缀） |
| quant | str | ✓ | 量化档（Q4_K_M/Q8_0/…·族 A 量化部署矩阵互引不重列） |
| parameter_size | str | ✓ | 参数量级（7b/14b/30b…） |
| size_bytes | int | ✓ | 主权重层字节数（B 级实测非 GiB 约数·与 layers 求和分列） |
| config_sha256 | str | ✓ | config 层摘要 |
| layers_sha256 | map | ✓ | 逐层摘要（model/system/template/license/params）——ollama manifest JSON 原生结构直采=单一事实源复用零重算 |
| min_vram_gb | float | ✓ | 加载 VRAM 上界+headroom 注记（≥1.5GB 判据同源·R-20260928-multi-node-scheduling §三） |
| 适用机 | list | ✓ | fleet 指针互引（不重做 fleet-allocations） |
| 用途 consumer_plan | str | ✓ | **O-20260928-1820 ③ 同律：入册无消费者=门拒**（哪票/哪线吃产出） |
| 获取通道 | enum | ✓ | L0 官方 registry/L1 本机盘内/L2 机队直拷/L3 HTTP 直链——**禁 git 含 LFS**（R- §Q3） |
| 落地路径 | str | ✓ | blobs 目录/manifest 路径 |
| license_ref | str | ✓ | 许可指针（族 A 量化矩阵许可带互引） |
| distributed_at | date | ⬜ | 首批分发日回填（draft 行恒空） |
| status | enum | ✓ | draft/active/retired（draft 行不入正式台账·J4） |

## §三 SHA256 校验命令面（判据级）

1. **ollama blob 命名=内容 SHA256 内证（2026-09-29 03:2x 本轮实跑）**：最小 blob 68B `Get-FileHash -Algorithm SHA256` 摘要==文件名（hash_match=True）→L2 分发落地后校验**双通道**：①`Get-FileHash -Algorithm SHA256` 全量重算（判据级·慢路径）②ollama manifest 层摘要对表（快路径·零重算）——不匹配即删重取（R- §Q4 纪律原文）。
2. **git 通道判负维持·当前史预验（R- §Q6 判据 2 预验·同窗实跑）**：`git rev-list --objects --all \| git cat-file --batch-check` 实测=**339 blobs·max 128,700B·>50MiB 零超线**（R-26 依据正典·维持项·非终验——全史滚动复扫随首批分发日执行）。

## §四 本机 dry-run 草案台账（draft·bm-a L1 层快照·2026-09-29 03:2x 实读）

> 数据源=ollama list+manifests JSON 实读+blobs 目录实读（零转述）；draft 五行全=L0 官方源获取（**无集团分发行=如实**·正式 v1 随首批分发立件·J4）。

| display_name | quant | size_bytes | model 层 sha256（前8+后6） | config sha256（前8+后6） | 通道 | 用途 consumer_plan |
|---|---|---|---|---|---|---|
| qwen2.5:7b-instruct | Q4_K_M | 4,683,073,952 | 2bada8a7…3933730 | 2f15b321…e9ae419d | L0 | U240 serve 常驻现役（每轮 qa_smoke 探针消费在案·族 A 标准承载） |
| qwen2.5:7b-instruct-q8_0 | Q8_0 | 8,098,525,600 | 3603045b…971cba7a | 751c87ba…50488ca4 | L0 | BC-P-07 精品档服务位评估（三指标对比随 10-05 复核窗·拉取预置毕） |
| qwen2.5:14b | Q4_K_M 默认档 | 8,988,110,688 | 2049f567…370f9a54 | db59b814…b34c6df3 | L0 | 深度档在盘资产·无在飞消费票如实（候选面=Bonsai 27B 对比基准/深度线·随 O-2175 窗） |
| qwen3-coder:30b | Q4_K_M 默认档 | 18,556,688,736 | 1194192c…87863006a | 24a94682…484d318 | L0 | 编码辅助线在盘资产·无在飞消费票如实（候选消费面 ⬜·未接票） |
| bge-m3:latest | F16（⬜ 随 v1 核验） | 1,157,671,200 | daec91ff…01e3062c | 0c4c9c2a…303e276 | L0 | 检索嵌入线在盘资产·无在飞消费票如实（候选面=N7 数据报告检索增强 ⬜） |

- **家族层共享发现**：qwen2.5 三模型（7b/Q8_0/14b）system/template/license 三层 blob 完全共享（66b9ea09…/eb440283…/832dd9e0…）=**blob 级台账天然 dedup 成立**——L2 直拷工程面按 blob 清单而非模型清单拷贝（分发带宽节约结构面·T9 落地时采纳）。
- 完整逐层摘要（license/params/system/template 各层全量 hex）已在盘=manifests JSON 单一事实源可再采·本表=主权重层+config 双摘要最小集（快照省token律）；min_vram_gb/许可带明细=族 A 量化矩阵互引不在本件重列。

## §五 判据 J1-J4 预注册（T9 工程窗回填）

- **J1 manifest 先行律**：首批分发日 manifest v1 先立件后启分发（无 manifest 不启分发=R- §Q4 纪律执行化）。
- **J2 SHA256 机器验**：每次 L2/L3 落地双通道校验（§三）·exit 码判据·不一致即删重取。
- **J3 字段全覆盖**：§二字段缺一即拒行·consumer_plan 必填（O-20260928-1820 ③「入池无消费者=门拒」同律）。
- **J4 草案/正式分立**：本件 §四=draft 快照·正式 v1=首批分发日立件·draft 行永不充当分发行（零编造）。

## §六 承接链

- **tech T9**：工程落地窗=首批分发日（工具化=cost_ledger 同款 CLI 风格或清单式起步·行级追加·本件=spec 输入）。
- **fleet-allocations**：互引不重做（R- §Q4 分工维持）。
- **BC-P-11 MaaS 扩面**：manifest=服务面模型清单消费方（族 A/D 共 manifest 单一事实源=R- §Q5 防双建维持）。
- **R-26**：缓解推进 2 行内注记同步（git 通道判负正典在册+本仓 339 blob 零超线预验）。

## §七 验证声明

本件全部数据=本机实跑（ollama list/manifests JSON 实读/blobs 目录 Get-FileHash/git rev-list 实测·时标 2026-09-29 03:2x）；零外部转述数据；零分发动作零下载；draft 快照五行全 L0 官方源获取（非集团分发行）；⬜ 项如实维持不造（bge-m3 量化档核验/未接票消费面）。
