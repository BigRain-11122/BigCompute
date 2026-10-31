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
- 8. git-tracked 台账尾行完整性（BC-P-45/T51/T95：七台账尾换行断言+合并行嗅探+尾区空行嗅探——写侧 round_append J4/J5 之外的损伤面当轮暴露；合成负例夹具自检=`qa_smoke --selftest`〔T96〕）。**判据换装（T171/BC-P-63·2026-10-30·10-29 23:55 全禁令解除令后自决执行）**：heartbeat 合并行嗅探由「任意位置 HH:MM 计数」改为组合 marker（行首锚 ∨ 时间紧跟行首词·x 形容忍·秒段可选）——旧口径把单行内引用的二手时刻读成双行（10-08 18:06 红锚）；实锚修正=心跳历史 1372 行中旧口径标 113 行、新口径标 3 行且 3 行均为真融合行（无假阴性新增）；证据 `state/t63-marker-eval-20261009.txt`（评估三门 EVAL-PASS）+`state/t171-marker-live-20261030.txt`（换装后普查）；夹具新增 E/F/G 三态（二手时刻引用 PASS／真融合 FAIL／x 形行首 PASS）。
- 9. L1 coder 车道 :8090 活体（T106·O-20261015-1335/1500 Qwen v3 final 迁移后：GET /v1/models 只读探活〔零生成零 GPU 负载·PASS=200+≥1 模型在册〕——四消费车道〔BigLife 年轮/MV 分镜/8792 后端/BigMoney advisor〕主干动脉死=当轮暴露不待消费方失败）。**墓碑豁免（D-20261029-11 ①，10-29 19:0x 落）**：`:8090` 经 10-29 12:00 终审落 (b) 登记退役——无监听（含工件缺失）判 PASS + `TOMBSTONE-N/A` 标记，不再逐轮 P1 复报；继任 KPI=Tools/l1_kpi_probe.py `reach` 对 `L1_ENDPOINT`（默认 :11434）。未退役车道原判据一字不改。
- 16. resident QA 8792 /ask 功能面（T153·10-28 实锚：探针 6 只读 /health，health 200 status=ok 而 /ask 503 后端拒连——CEO 观测窗功能死而本司 QA 数日报 PASS 的假绿面）：真实点击路径〔白名单锚民→人设提示→LLM 后端→AIGC 标识门〕端到端跑一次；后端端口无监听时跳过 POST（零浪费生成）并判 `HEALTH-OK-BACKEND-DOWN`；一答案缺失/无标识门/5xx/超时/白名单漂移一律 FAIL 不放宽。判据=selftest 全分支夹具 + live 按类判定。
- 17. 扫描根自指普查（T161·10-29 立）：跑 `scan_root_census.run_census()`，**按行判定不信 banner**——NEEDS-EXCLUDE/UNVERIFIED/无法识别的行 verdict 一律 FAIL（普查 banner 只有 NEEDS-EXCLUDE 翻转的缺口已由 T162 补为 SCAN-ROOT-UNVERIFIED，但探针仍须判行，两层各守一面）；报告为空=FAIL（禁用「没行可判」当通过）。判据=selftest 三态夹具（S1 全绿 PASS／S2 NEEDS-EXCLUDE FAIL／S3 CLEAN banner + UNVERIFIED FAIL／S4 空报告 FAIL／S6 未知 verdict FAIL）+ live 实跑 SCAN-ROOT-CLEAN。
- 18. 心跳 token 守卫（T168·10-29 立／T170 批次 1 扩值域）：读心跳末条 `idle_selfcheck` 行，对 **18 个** token 逐个校验。数值面九个〔ram_free_pct/vram_free_mb/py_cpu/queue_open/consecutive_idle/idle_rounds/pool_rows_candidate/pool_effective/pool_ignitable〕查「在场 + 可解析为数值 + 区间合法」（百分比 0-100·计数 ≥0 且为整数）；枚举面九个（T170）查值域〔cpu_lane_idle/agenda_starved/serve_alive/breach_eff=true|false；task_face=ok|pause-exempt|probe-error|ALERT:*；model_drift=GREEN|UNVERIFIED|DRIFT(m=,a=)；lane8090=UP|DARK[…]；c1c4_line=STATE(…rounds_ago=…)；hq_line=STATE(…age_h=…)〕——只判形状域不重算语义。缺名/改名/非数/越界/域外一律 FAIL 并点名该 token。T167 只守供给三标签，其余 token 写侧改名或值漂移时读侧静默失效而心跳行仍显健康=本条要拦的面。判据=selftest 十五夹具（S1 健康行 PASS／S2 改名 FAIL／S3 越界 FAIL／S4 负计数 FAIL／S5 非数 FAIL／S6 非整数 FAIL／S7 空行 FAIL／S8 与 no.10 供给成对语义一致／S9 布尔域外 FAIL／S10 未知 task_face FAIL／S11 ALERT: 前缀放行／S12 畸形 model_drift FAIL／S13 不可解析车道态 FAIL／S14 c1c4_line 缺 rounds_ago FAIL／S15 hq_line 无括号载荷 FAIL）。守卫集单一源=`HEARTBEAT_GUARDED_TOKENS`，写侧 token 表与 UNGUARDED 清单由 `Tools/heartbeat_token_census.py` 机械普查（本批后 guarded 9→18、unguarded 16→7、stale=0）。

- 20. 台账并行（融合）行全文普查（T177·11-01 立）：no.8 只嗅七台账**尾部 12 行**，中段的融合行永久不可见（T176 手测已在 state/heartbeat.txt 找到 3 行）。本条跑 `ledger_parallel_census`（台账表单一源=`ledger_markers.TAIL_PROBE_FILES`）对七台账**全文逐行**计数行头 marker，≥2 即融合行；历史已人工确认的行按**内容指纹**（sha1 前 16 位·行号漂移不影响）登记在 `state/ledger-parallel-whitelist.json`，白名单外一律 FAIL 并点名 file:line。文件缺失/不可读=FAIL（fail-closed，禁以「没读到」当通过）。判据=selftest 四态夹具（S1 干净 PASS／S2 白名单外融合行 FAIL／S3 入白名单后 PASS／S4 台账缺失 FAIL）+ live `PARALLEL-CLEAN`（11-01 首次实跑=parallel 3 全白名单，与 T176 手测清单逐行一致）。

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

### 三.19 探针 2/16 争用感知预算（T178·2026-10-31 修订）

外部产线占满 GPU（实测 ComfyUI+Tuanjie Editor：util 99%、11782/12282 MiB）时，常驻 runner 被时间片切成事实不可用——8 token 一次生成 >300s。固定预算会把「调度事实」记成「设备故障」。修订=探针调用前读一次 `nvidia-smi`（`gpu_state`/`gpu_contention_verdict`；阈值 util≥90% 或 vram_free<1024MiB=CONTENDED），**只调预算与降级路径，判据不放宽**：

- 探针 2（ollama 真响应）：争用时 GPU 道预算 60s；失败后一次 CPU 道（`num_gpu:0`+`keep_alive:0`+8 token）取**真实答复**，PASS 标 `CPU-LANE-DEGRADED`（实测 15.8s 出 `1 + 1 = 2.`）；两道路均无答复=BOTH-LANES FAIL。
- 探针 16（8792 /ask）：争用时 40s→60s 单次（服务端后端同为 GPU 道，拉长预算只烧墙钟），FAIL 行必带 `gpu: GPU-CONTENDED(...)`，禁读成「8792 窗坏了」。
- GPU 读数不可得=GPU-UNREADABLE，**不**买额外预算也不判绿；selftest `selftest_gpu_contention` 七夹具（G1-G7）。

（v1.0 完·2026-10-04 OSLoop 哨兵轮·M49 步②·三.19 增补 2026-10-31 T178 轮）
