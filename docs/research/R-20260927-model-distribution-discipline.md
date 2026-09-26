# R-20260927 模型分发规范（方向族 D·U240 通道纪律）：模型大件禁 git 通道的分发纪律 v0.1

> 溯源：议程滚动件 docs/research/agenda-compute-research.md 族 D 首 R-（T-20260926-22·SLA ≤09-27 22:49 内提前 ~22h）+O-20260926-2253-HQ-C ①「serve 常驻标准+模型分发规范=禁 git 通道的分发纪律」+赋能目录 §四 服务项 #2（docs/ops/empowerment-catalog-v1.md）。
> 范围边界：本件=**分发纪律**（怎么把模型大件安全送到机器上）；serve 常驻标准（跑起来后的口径）=族 A 交接面不在本件（防双建）；模型选型与量化矩阵=P-17 适配矩阵域不重做。
> 状态：🟡 草案级 v0.1（git 通道硬限与 LFS 配额面=官方原文级双锚已核【URL核验 2026-09-27】；机队实测面全部 ⬜ 随首批分发执行回填）。

## 待答问题

- [x] Q1 git 通道对模型大件是否结构性可行 ——已答（判负·硬限证据在案）
- [x] Q2 Git LFS 是否构成豁免通道 ——已答（判负·配额面证据在案）
- [x] Q3 分发通道分层怎么设计 ——已答（L0-L3 四层·禁 git 通道）
- [x] Q4 清单与校验纪律（manifest 单一事实源） ——已答（草案级）
- [x] Q5 与 U240 serve 常驻/机队模型自配的衔接 ——已答（族 A 交接=共 manifest·防双建）
- [x] Q6 判据（预注册） ——已答（四条·随首批分发回填）

## 发现

### Q1 git 通道结构性判负（官方硬限·URL核验 2026-09-27）

1. **GitHub 官方原文**（https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github ·访问 2026-09-27）：「If you attempt to add or update a file that is larger than **50 MiB**, you will receive a warning from Git」「**GitHub blocks files larger than 100 MiB**」；浏览器直传上限 **25 MiB**；仓储建议「We recommend repositories remain small, **ideally less than 1 GB**, and less than 5 GB is strongly recommended」。
2. **本司机队现役模型体量**（台账只读引用）：U240 档 qwen2.5:7b 级量化件≈数 GiB/件、sd_xl_base 级底模≈6-7 GiB、TTS 双包+53 声纹（赋能目录 §一）——全部远超 100 MiB 硬禁线，git 通道**结构死路非纪律选择**。
3. **history 不可逆污染**：大件一旦 commit+push，移除需改写 git 历史（GitHub 官方「Removing files from a repository's history」路径）——远程已通（origin=github.com:BigRain-11122/BigCompute.git·09-26 23:39 轮 pull 406530c 在案）=风险面已激活，防呆必须前置。

### Q2 Git LFS 豁免判负（配额面·URL核验 2026-09-27）

GitHub 官方 LFS 页（https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage ·访问 2026-09-27）：GitHub Free **带宽 10 GiB/月+存储 10 GiB**；「If you use more than your included quota of bandwidth per month without a payment method on file, **Git LFS support is disabled on your account until the next month**」。单件数 GiB 模型×机队多机复分发×月度轮转=配额即时穿底且 LFS 仍属 git 生态通道（指针文件+按量计费）——**判负：LFS 非本司豁免通道**。GitHub 官方对超限件的替代建议本身即「release 附件/文件共享服务」路径，与下述 L2/L3 分层同向。

### Q3 分发通道分层（纪律草案·git 通道禁承载模型大件）

| 层 | 通道 | 定位 | 纪律 |
|---|---|---|---|
| L0 | 官方 registry 拉取（ollama pull/HF/ModelScope） | 源头获取 | 首选；镜像源与供应链核验纪律=族 A 交接面 ⬜ |
| L1 | 本机盘内（已配维持） | 现状维持 | Ollama blobs/ComfyUI models 目录即本层 |
| L2 | 局域网机间直拷（共享盘/robocopy） | **机队分发主通道** | 同网段首选；大件不占外网带宽 |
| L3 | HTTP(S) 直链（HF/ModelScope/ollama.com） | L2 不可达回退 | 外网面·记录拉取时间与源 |
| 禁 | **git 通道（含 LFS）** | 模型大件 ⛔ | git 仓只存 manifest 文本（KB 级） |

### Q4 manifest 单一事实源（清单与校验纪律）

- 落点=docs/ops/model-manifest.md（v1 ⬜ 随 T-22 族 A 窗或首批分发立件·行级追加）：**模型名×版本×量化档×SHA256×大小×适用机×用途×获取通道×落地路径**。
- 校验纪律：L2/L3 落地后 `Get-FileHash -Algorithm SHA256` 对照 manifest=判据级；不匹配即删重取。
- 与集团件分工：fleet-allocations 承载「哪台机配什么」（P-17 适配矩阵域）；本 manifest 承载「模型件本体是什么」（指纹与溯源）——两件互引不重做。

### Q5 族 A 交接面（serve 常驻标准）

U240 serve 常驻标准（族 A·T-22 下窗排程）引用本 manifest 作为「机队模型自配」台账面：**分发纪律（族 D）+运行口径（族 A）共 manifest 单一事实源**，防双建。

### Q6 判据（预注册·首批分发执行时回填）

1. manifest v1 落盘，与现役机模型抽查（≥1 机）100% 对得上；
2. git 仓零 >50 MiB blob（`git rev-list --objects --all` 实测零超线）；
3. 一次 L2 直拷分发实测+SHA256 校验一致；
4. 纪律入正典（ops 件或 BLUEPRINT 引用）——本 R- 件升级候选。

## ⬜ 未验项（如实不造）

- 机队实测面全部 ⬜（L2 直拷/校验/manifest 对账=判据 1-3 执行时回填）；
- ollama/HF/ModelScope 存储目录结构与镜像源供应链纪律=族 A 窗 ⬜（本件只锚 git/LFS 官方两源）；
- 远程仓库服务端策略（如本仓托管方对 LFS 默认开关）⬜ 未实测——不依赖（判负已定·通道纪律不变）。

## 落点

任务板 T-20260926-22（族 D 首 R- ✅）+赋能目录 §四 服务项 #2 证据面+风险台账 R-26（模型大件误走 git 通道·本件=依据正典）。
