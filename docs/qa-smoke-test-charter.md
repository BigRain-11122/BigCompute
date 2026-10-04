# BigCompute QA Smoke Test Charter v1.0

> 令源=集团 orders 2026-09-28 L254（QA Smoke Test 自验令）。落盘=G1 补建件（O-2026-0930-027 全集团 SOP 建制令承接·M49 步②·2026-10-04 哨兵轮）。
> 补建前=六处引用悬空（mandate 收尾步/qa_smoke.py docstring/R-31/HQ-FEEDBACK/tech T11/explore E37·R-47 在册）；判据早已冻结于 mandate+实装——本件=落盘即引用归一，零新判据零新立法。
> 轻量闸：≤200 行/25KB（O-20260930-027 SOP 范本判据）。

## 一、四条清单（判据冻结·mandate 收尾步同文）

| # | 判据 | 实测口径 |
|---|---|---|
| 1 | GPU 采集器跑通出数 | `python Tools/gpu_idle_collector.py report` 零退出+当日有样本+日均对账行出数（30% 唯一点名阈值·C-20260929-02） |
| 2 | Ollama 真响应 | 本地 11434 `/api/generate` 真请求真响应（qwen2.5:7b-instruct·eval_count tok/s 实测记录） |
| 3 | 三队列 ≥3 条 | state/queue/main.md、tech.md、explore.md 各 ≥3 条待办行 |
| 4 | 成本账本在维护 | `python Tools/cost_ledger.py selftest` PASS（口径 B 结算唯一） |

## 二、证据形态（R-31 适配·BC-F-20260928-03 声明在案）

- 证据=qa/smoke-<轮时标>.log（真实命令输出全文+自判行）+ 同名 .png。
- 本司 CLI 工具族无 GUI 游戏窗可截 → png=log 内容 .NET 渲染零编辑（渲染法）；已声明呈集团 patrol 定谳。
- 红线：造假=诚实律 P1（渲染=真实输出非伪造）；缺项=下轮第一优先级修+日清记。

## 三、扩展探针（实装内含·非四条判据·失照样 FAIL）

- 5. rounds.log UTF-8 解码完整性（tech T25/BC-P-16：shell Add-Content GBK 乱码复发=当轮暴露，append 走 round_append 唯一）。
- 6. resident QA serve 8792 /health（E41·O-20260930-1645+1656 CEO 观察窗后端：CEO 可见面静默死=当轮暴露不待点击）。

## 四、执行律

- 执行位=每轮收尾步 QA 自验先行（mandate §每轮固定步 8）；实装=Tools/qa_smoke.py（tech T11 一键化：探针→log+png 产出+自判行）；exit 0=全过/1=有缺。
- 周日轮附加底座自检：cost_ledger selftest + Tools/fulfillment/test_pipeline.py 双跑，非零退出=按 errors.md E2 立修复单（结果入轮账本行）。
- 采集人=OSLoop 引擎部（无人值守·静默律）·判据定谳=集团 patrol。

## 五、引用链归一（本件落盘后全活）

| 引用方 | 位置 |
|---|---|
| mandate 收尾步 | Tools/iteration_prompt.txt §每轮固定步 8 |
| 实装 docstring | Tools/qa_smoke.py L3-4 |
| R-31 证据形态适配 | docs/risk-register.md |
| 回执 | HQ-FEEDBACK.md（BC-F-20260928-03 首验 4/4 PASS） |
| tech T11 一键化 | state/queue/tech.md |
| explore E37 长假探针 | state/queue/explore.md |
| 盘点清单 G1 行 | docs/ops/sop-inventory-gap-list-v1.md |

（v1.0 完·2026-10-04 OSLoop 哨兵轮·M49 步②）
