# BigCompute QA Smoke Test Charter v1.0

> 令源=集团 orders 2026-09-28 L254（QA Smoke Test 自验令）。落盘=G1 补建件（O-2026-0930-027 全集团 SOP 建制令承接·M49 步②·2026-10-04 哨兵轮）。
> 补建前=六处引用悬空（mandate 收尾步/qa_smoke.py docstring/R-31/HQ-FEEDBACK/tech T11/explore E37·R-47 在册）；判据早已冻结于 mandate+实装——本件=落盘即引用归一，零新判据零新立法。
> 轻量闸：≤200 行/25KB（O-20260930-027 SOP 范本判据）。

## 一、四条清单（判据冻结·mandate 收尾步同文）

| # | 判据 | 实测口径 |
|---|---|---|
| 1 | GPU 采集器跑通出数 | `python Tools/gpu_idle_collector.py report` 零退出+当日有样本+日均对账行出数（30% 唯一点名阈值·C-20260929-02） |
| 2 | Ollama 真响应 | 本地 11434 `/api/generate` 真请求真响应（qwen3:4b-instruct〔Qwen v3 final 切换后 flash slot 常驻档·O-20261015-1335/1500·旧 qwen2.5 家族已删·判据本体不变仅模型注记随切换令同步〕·eval_count tok/s 实测记录） |
| 3 | 三队列 ≥3 条 | state/queue/main.md、tech.md、explore.md 各 ≥3 条待办行 |
| 4 | 成本账本在维护 | `python Tools/cost_ledger.py selftest` PASS（口径 B 结算唯一） |

## 二、证据形态（R-31 适配·BC-F-20260928-03 声明在案）

- 证据=qa/smoke-<轮时标>.log（真实命令输出全文+自判行）+ 同名 .png。
- 本司 CLI 工具族无 GUI 游戏窗可截 → png=log 内容 .NET 渲染零编辑（渲染法）；已声明呈集团 patrol 定谳。
- 红线：造假=诚实律 P1（渲染=真实输出非伪造）；缺项=下轮第一优先级修+日清记。

## 三、扩展探针（实装内含·非四条判据·失照样 FAIL）

- 5. rounds.log UTF-8 解码完整性（tech T25/BC-P-16：shell Add-Content GBK 乱码复发=当轮暴露，append 走 round_append 唯一）。
- 6. resident QA serve 8792 /health（E41·O-20260930-1645+1656 CEO 观察窗后端：CEO 可见面静默死=当轮暴露不待点击）。
- 7.（预留位维持：云端 attribution 审计探针——T37·随首笔实缴云账单 J4 窗接线·勿预接线。）
- 8. git-tracked 台账尾行完整性（BC-P-45/T51/T95：七台账尾换行断言+合并行嗅探+尾区空行嗅探——写侧 round_append J4/J5 之外的损伤面当轮暴露；合成负例夹具自检=`qa_smoke --selftest`〔T96〕）。
- 9. L1 coder 车道 :8090 活体（T106·O-20261015-1335/1500 Qwen v3 final 迁移后：GET /v1/models 只读探活〔零生成零 GPU 负载·PASS=200+≥1 模型在册〕——四消费车道〔BigLife 年轮/MV 分镜/8792 后端/BigMoney advisor〕主干动脉死=当轮暴露不待消费方失败）。**墓碑豁免（D-20261029-11 ①，10-29 19:0x 落）**：`:8090` 经 10-29 12:00 终审落 (b) 登记退役——无监听（含工件缺失）判 PASS + `TOMBSTONE-N/A` 标记，不再逐轮 P1 复报；继任 KPI=Tools/l1_kpi_probe.py `reach` 对 `L1_ENDPOINT`（默认 :11434）。未退役车道原判据一字不改。
- 16. resident QA 8792 /ask 功能面（T153·10-28 实锚：探针 6 只读 /health，health 200 status=ok 而 /ask 503 后端拒连——CEO 观测窗功能死而本司 QA 数日报 PASS 的假绿面）：真实点击路径〔白名单锚民→人设提示→LLM 后端→AIGC 标识门〕端到端跑一次；后端端口无监听时跳过 POST（零浪费生成）并判 `HEALTH-OK-BACKEND-DOWN`；一答案缺失/无标识门/5xx/超时/白名单漂移一律 FAIL 不放宽。判据=selftest 全分支夹具 + live 按类判定。
- 17. 扫描根自指普查（T161·10-29 立）：跑 `scan_root_census.run_census()`，**按行判定不信 banner**——NEEDS-EXCLUDE/UNVERIFIED/无法识别的行 verdict 一律 FAIL（普查 banner 只有 NEEDS-EXCLUDE 翻转的缺口已由 T162 补为 SCAN-ROOT-UNVERIFIED，但探针仍须判行，两层各守一面）；报告为空=FAIL（禁用「没行可判」当通过）。判据=selftest 三态夹具（S1 全绿 PASS／S2 NEEDS-EXCLUDE FAIL／S3 CLEAN banner + UNVERIFIED FAIL／S4 空报告 FAIL／S6 未知 verdict FAIL）+ live 实跑 SCAN-ROOT-CLEAN。

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
