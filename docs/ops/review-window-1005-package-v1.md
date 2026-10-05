# 10-05 复核窗呈批包 v1.2（review-window-1005-package）

- 呈批对象：CEO 复核窗（2026-10-05）｜组装=10-04 12:43 午班（M46·runbook 在册复核面收口件·M45 轮内派生）
- 增补=10-04 16:5x 哨兵轮（M47 分步①）：16:37 CEO 令〔FluxVerse R0 修复批停滞 27h 升级——10-05 白班再空窗则 GPU HOLD 份额回收转配本复核窗〕接入决策项① 情景 C·令源=集团 orders.md 10-04 16:37 行；M47 数据面复跑刷新留 22:43 夜班末轮（窗开口时 ≤12h 新判据维持）
- **数据面刷新=10-05 22:43 夜班轮（v1.1→v1.2·BC-P-40 批活转化收口）**：四复核面数据行一命令复刷〔Tools/review_pkg_refresh.py·tech T49·selftest 6/6 PASS·证据 state/review-pkg-refresh-20261005-224x.txt〕——GPU 三尺+loadline+machine_borrowable〔22:47 实测〕/批池 validate〔22:47 复跑〕/_trash 复点〔22:47〕/净窗计数〔22:47〕全更新·**窗开口数据龄 0h（CEO 裁定至即呈·鲜度判据超额兑现）**·M47「白班开口超龄则午班一命令复刷」缺口=本工具结构性归零（未来复核窗复用件）
- 数据口径：既有 state/ 件直引+本轮既有命令复跑实测（gpu report/loadline／batch_pool validate／qa_smoke 22:34 6/6／净窗日志计数／_trash 复点）·零新采集机制·零派活·零定价动作·零真实交易
- 结构：四复核面×各一决策项（A/B 两态呈批）·数值 🟡=本轮实测复跑（22:5x）／⬜=待批如实分标

## ① GPU 观察期收口面（DRY-RUN 2026-09-28 → 10-05）

| 尺（三尺数据） | 本轮实测（22:47 复跑·v1.2） | 判定 |
|---|---|---|
| 30% 唯一点名阈值（C-20260929-02）×3 日滚动基线（10-03..10-05） | n=283 avg=2.1% | FLAG（远低点名线） |
| 7 日对标 09-29..10-05（R-C1 D-20260930-36 结构性失职线 <50%=差距线） | n=656 avg=5.1% | BELOW-BENCH（差距线仅参考·唯一点名线维持 30%·70%=方向性参考） |
| 当日 10-05 | n=91 avg=1.2% max=100%（max=他司产线瞬时段） | 保护态待机 |
| 滚动 30min 窗 | n=2 avg=0.0% | IDLE |
| loadline 满载行（T38/P-32） | busy≥30%=1.1%／quiet<10%=98.9%·cpu 1%·RAM 93.6GB·prod_lanes=6（alive 2 常驻+4 备货 lane） | 待机主导 |

- 观察期结论：DRY-RUN 8 日全程**零派活**（采集器 NO dispatch·派活日志=指针制）——日均低利用=保护态豁免面成立（商业物理件未通=P-20260928-02 ③·O-1820 意义性律「宁亮牌合法等待不造活凑数」·bm-a 自动派活黑名单=C-20260929-02 7.4 毕业呈批制）
- **D-20261004-04 双探判据应用（10-04 12:06 集团新法·本包接入）**：机面级可借 verdict=VRAM 余量 ≥6GB＋共租活跃面空双探——bm-a 最新样本（22:47 复跑·v1.2）：编辑器实例 1 个＋VRAM 余 4140MiB≈4.1GB<6GB → **机面不可借·不挂牌**（循环态 GREEN-IDLE ≠ 机面可借两态解耦·与 BigStream 判例同向）——**T44 探针已落地（10-04 12:4x）**：采集器 report/loadline 自带 machine_borrowable 列（12:04→12:4x→22:5x→22:47 四连证 NOT-BORROWABLE[VRAM|COTENANT]·余 5894→5904→5899→4140MiB+编辑器 1·证据 state/t44-borrow-probe-20261004.txt+state/review-pkg-refresh-20261005-224x.txt）·复核窗收据自此自动出列（BC-P-38 批活·tech T44 done）
- **决策项①**：A=真派活激活（10-05 观察期毕；激活前置=净窗+J5 头窗放行+黑名单解除面另呈批）｜B=延期观察（新窗+新判据呈批）｜**情景 C（10-04 16:37 CEO 令·供给面上行）**：FluxVerse R0 修复批 10-05 白班再空窗→其 GPU HOLD 份额回收转配本复核窗——A 态供给面相应上调（消费计划候选=批池 lane 扩容〔BC-P-39 提案在册〕·30% 唯一点名阈值/净窗双闸/机面可借双探/bm-a 黑名单判据均不随供给面变化维持）·B 态下回收份额处置随窗裁定

## ② 批池呈批面（batch-pool-stock-v1）

- validate 复跑（22:47·v1.2）：🟡 **POOL_GREEN**——J1-J6 全过〔含 J6 数据本地性门〕·6 批卡 4 lane（City3D/FluxVerse 3d_offline ×2=O-019 烘焙批＋O-007 判据帧批〔≥2 硬判据 ✓〕／吸嘟嘟按需支援席 ×2=O-013 取证批＋构建批／Lane-A-CPU ×1／Lane-B-Text ×1）·consumer_plan 全指名 ✓·J5 in-flight=0/6（DRY-RUN 至 10-05 头窗）
- **J6 数据本地性门已预接（BC-P-37 批活转化·10-04 12:43 午班落地）**：批卡可选 `data_deps` 字段→认领前在场断言·缺=DATA_NOT_LOCAL 红门（D-20261004-02① BigMoney F-01 四分片全崩集团判例前置吸收）·selftest 12 checks PASS·无 data_deps 卡零强加·池卡 data_deps 🟡 随激活窗按卡回填
- **决策项②**：A=批池激活（10-05 头窗起 J5 in-flight 门开·data_deps 按卡回填后过 J6）｜B=维持 DRY-RUN 备货态

## ③ _trash 7 天复核删除面（T-20260928-30·CEO 安全清理令①承接）

- 实测复验（22:47 复点·v1.2）：🟡 56 文件 63.4MB（与 09-28 移入账一致·移动未删）·.gitignore 行在位（git 零污染·untracked）
- 7 天窗：09-28 移入→**10-05 到点**
- **决策项③**：A=到点删除（释放 63.4MB·源=.codely-cli/auto-saves·保留正式产出原则不变）｜B=延期保留（新窗）

## ④ 净窗证据面（clean-window-log·T28 CleanWindowProbe）

- 15min tick 自 09-30 10:32 注册起：🟡 **549/549 全 BLOCKED（零净窗·22:47 计数·较 v1.1 的 452 增 97 tick 全 BLOCKED）**——双闸=Tuanjie 编辑器实例＋VRAM 余 <6GB（最新样本 22:34：编辑器 1＋余 4137MiB）
- M25/T26 烘焙实弹顺延判据：净窗一命令（run_bake.ps1 双路 CPU+GPU 各一跑）·非净窗自动 BLOCKED exit 3 机械执法·R-43 行在册·顺延合法性=O-027 夜班全面开工令使 22:43 由闲置窗反转为峰窗
- **决策项④**：净窗开启即自动执行面维持（无需批·机械执法在位）——本面呈报=证据面（BLOCKED 频率+双闸构成）

## 附：数据指针（全既有件直引）

state/gpu_idle_metrics.jsonl｜state/self-drive-metrics-*.json｜docs/ops/batch-pool-stock-v1.jsonl｜Tools/batch_pool.py（J1-J6·selftest 12）｜state/clean-window-log.jsonl｜qa/smoke-20261004-2234.log+png（22:43 轮 6/6）+qa/smoke-20261004-1217.log+png（12:43 轮）｜docs/_trash/（56 件 63.4MB）｜GPU 三尺/loadline 实测行=本包各面内嵌（Tools/gpu_idle_collector.py 2026-10-04 12:1x+12:4x T44+22:5x M47+**10-05 22:47 v1.2 一命令复刷**）｜**v1.2 刷新证据=state/review-pkg-refresh-20261005-224x.txt（Tools/review_pkg_refresh.py·tech T49）**
