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
- 8. git-tracked 台账尾行完整性（BC-P-45/T51/T95：七台账尾换行断言+合并行嗅探+尾区空行嗅探——写侧 round_append J4/J5 之外的损伤面当轮暴露；合成负例夹具自检=`qa_smoke --selftest`〔T96〕）。**判据换装（T171/BC-P-63·2026-10-30·10-29 23:55 全禁令解除令后自决执行）**：heartbeat 合并行嗅探由「任意位置 HH:MM 计数」改为组合 marker（行首锚 ∨ 时间紧跟行首词·x 形容忍·秒段可选）——旧口径把单行内引用的二手时刻读成双行（10-08 18:06 红锚）；实锚修正=心跳历史 1372 行中旧口径标 113 行、新口径标 3 行且 3 行均为真融合行（无假阴性新增）；证据 `state/t63-marker-eval-20261009.txt`（评估三门 EVAL-PASS）+`state/t171-marker-live-20261030.txt`（换装后普查）；夹具新增 E/F/G 三态（二手时刻引用 PASS／真融合 FAIL／x 形行首 PASS）。**尾部卫生约定（T194·11-03 定 A 方案，零代码）**：本仓台账末行必须是行头形小结（或表格行），禁以空行/裸 prose 收尾；多行日清节允许正文成段，但收尾行须自带 `HH:MM 日清小结` 行头形，全文融合行归 no.20。实测对照：七台账 20:4x 复点=本仓 4 件全清、集团 reflection-journal 尾空行属外部只读面（不改、不判本司缺陷）。
- 9. L1 coder 车道 :8090 活体（T106·O-20261015-1335/1500 Qwen v3 final 迁移后：GET /v1/models 只读探活〔零生成零 GPU 负载·PASS=200+≥1 模型在册〕——四消费车道〔BigLife 年轮/MV 分镜/8792 后端/BigMoney advisor〕主干动脉死=当轮暴露不待消费方失败）。**墓碑豁免（D-20261029-11 ①，10-29 19:0x 落）**：`:8090` 经 10-29 12:00 终审落 (b) 登记退役——无监听（含工件缺失）判 PASS + `TOMBSTONE-N/A` 标记，不再逐轮 P1 复报；继任 KPI=Tools/l1_kpi_probe.py `reach` 对 `L1_ENDPOINT`（默认 :11434）。未退役车道原判据一字不改。
- 10. 供给双口径对读（T132/T167·12-4x 立）：心跳末条 `idle_selfcheck` 行须同含 `pool_effective` 与 `pool_ignitable` 两字段且 `pool_effective ≤ pool_ignitable`；缺任一字段／倒挂／出现弃用名 `pool_claimable=` 一律 FAIL。判据=selftest 八夹具（六原例 + 旧名回潮 + 缺候选标签）。
- 11. runbook 字节帽（T133·10-26 立）：`state/runbook.md` ≤ **2048B**，超限即 FAIL 并给压缩指令（「无人测的帽不是帽」——标题自称 <2KB 却零告警涨到 2499B）。判据=探针实跑 PASS + 超限可复现 FAIL。
- 12. 成本轨字段契约普查（T138·10-27 立）：跑 `cost_contract_census.census()`，verdict 必须 `COST-CONTRACT-GREEN`；`gaps` 非空或「green 但 gaps 非空」的自相矛盾一律 FAIL（禁靠 `.get` 默认值兜底读月末）。判据=selftest 四夹具（clean／gap 必红／自相矛盾必红／零轨必红）。
- 13. 车道前置件在位性+格式外形（T140/T141·10-27 立）：只读 `coder-serve.ps1` 的模型与 exe 两条常量路径，双 `Test-Path` 缺件即 FAIL 并指名；在位再验外形（gguf magic `GGUF`+≥1MiB／PE `MZ`+`PE\0\0`+≥64KiB），外形不合法即 `LANE-ARTIFACT-BAD` FAIL（R-53 截断件判例在判据内）；常量解析不到=UNVERIFIED 不假绿。判据=selftest 11 夹具。边界如实=只验格式位非加载测试。
- 14. CEO 实况面机械闸（T143·10-27 立）：`docs/status-export.json` 四问机械判〔strict UTF-8 可解码且 JSON 可解析／三键在且非空非占位无 U+FFFD／`updated_at` ≤48h／不落后轮账 >2h〕，任一不过即 FAIL。判据=selftest 八夹具含七条拒绝分支 + live `EXPORT-GREEN`。
- 15. 台账编码健康（T145·10-27 立）：两台账跑 `ledger_encoding_guard.classify()`，真双转义或 U+FFFD 即 FAIL；`LATIN-RUN` 作信息项不红门（合法重音与排版符不误报）。判据=`--selftest` 编码段 + live PASS。历史行零改写（T145 前提已被字节普查推翻，如实留档）。

- 16. resident QA 8792 /ask 功能面（T153·10-28 实锚：探针 6 只读 /health，health 200 status=ok 而 /ask 503 后端拒连——CEO 观测窗功能死而本司 QA 数日报 PASS 的假绿面）：真实点击路径〔白名单锚民→人设提示→LLM 后端→AIGC 标识门〕端到端跑一次；后端端口无监听时跳过 POST（零浪费生成）并判 `HEALTH-OK-BACKEND-DOWN`；一答案缺失/无标识门/5xx/超时/白名单漂移一律 FAIL 不放宽。判据=selftest 全分支夹具 + live 按类判定。
- 17. 扫描根自指普查（T161·10-29 立）：跑 `scan_root_census.run_census()`，**按行判定不信 banner**——NEEDS-EXCLUDE/UNVERIFIED/无法识别的行 verdict 一律 FAIL（普查 banner 只有 NEEDS-EXCLUDE 翻转的缺口已由 T162 补为 SCAN-ROOT-UNVERIFIED，但探针仍须判行，两层各守一面）；报告为空=FAIL（禁用「没行可判」当通过）。判据=selftest 三态夹具（S1 全绿 PASS／S2 NEEDS-EXCLUDE FAIL／S3 CLEAN banner + UNVERIFIED FAIL／S4 空报告 FAIL／S6 未知 verdict FAIL）+ live 实跑 SCAN-ROOT-CLEAN。
- 18. 心跳 token 守卫（T168·10-29 立／T170 批次 1 扩值域）：读心跳末条 `idle_selfcheck` 行，对 **18 个** token 逐个校验。数值面九个〔ram_free_pct/vram_free_mb/py_cpu/queue_open/consecutive_idle/idle_rounds/pool_rows_candidate/pool_effective/pool_ignitable〕查「在场 + 可解析为数值 + 区间合法」（百分比 0-100·计数 ≥0 且为整数）；枚举面九个（T170）查值域〔cpu_lane_idle/agenda_starved/serve_alive/breach_eff=true|false；task_face=ok|pause-exempt|probe-error|ALERT:*；model_drift=GREEN|UNVERIFIED|DRIFT(m=,a=)；lane8090=UP|DARK[…]；c1c4_line=STATE(…rounds_ago=…)；hq_line=STATE(…age_h=…)〕——只判形状域不重算语义。缺名/改名/非数/越界/域外一律 FAIL 并点名该 token。T167 只守供给三标签，其余 token 写侧改名或值漂移时读侧静默失效而心跳行仍显健康=本条要拦的面。判据=selftest 十五夹具（S1 健康行 PASS／S2 改名 FAIL／S3 越界 FAIL／S4 负计数 FAIL／S5 非数 FAIL／S6 非整数 FAIL／S7 空行 FAIL／S8 与 no.10 供给成对语义一致／S9 布尔域外 FAIL／S10 未知 task_face FAIL／S11 ALERT: 前缀放行／S12 畸形 model_drift FAIL／S13 不可解析车道态 FAIL／S14 c1c4_line 缺 rounds_ago FAIL／S15 hq_line 无括号载荷 FAIL）。守卫集单一源=`HEARTBEAT_GUARDED_TOKENS`，写侧 token 表与 UNGUARDED 清单由 `Tools/heartbeat_token_census.py` 机械普查（本批后 guarded 9→18、unguarded 16→7、stale=0）。

- 19. 一线索引文本完好性（M93／D-20261102-03·11-02 立）：跑 `text_integrity_census` 的同套七类默认目标，损坏行以 `loc:file:line`／`ext:file:line` 点名；**顾问档不红门**（历史损坏不该阻断产线，但必须当轮可见）；拿不到 summary=UNVERIFIED 判 FAIL 不假绿。判据=selftest 五夹具（clean／本地损坏点名／外部 ext 标签／无 summary FAIL／默认覆盖七类）。
- 20. 台账并行（融合）行全文普查（T177·11-01 立）：no.8 只嗅七台账**尾部 12 行**，中段的融合行永久不可见（T176 手测已在 state/heartbeat.txt 找到 3 行）。本条跑 `ledger_parallel_census`（台账表单一源=`ledger_markers.TAIL_PROBE_FILES`）对七台账**全文逐行**计数行头 marker，≥2 即融合行；历史已人工确认的行按**内容指纹**（sha1 前 16 位·行号漂移不影响）登记在 `state/ledger-parallel-whitelist.json`，白名单外一律 FAIL 并点名 file:line。文件缺失/不可读=FAIL（fail-closed，禁以「没读到」当通过）。判据=selftest 四态夹具（S1 干净 PASS／S2 白名单外融合行 FAIL／S3 入白名单后 PASS／S4 台账缺失 FAIL）+ live `PARALLEL-CLEAN`（11-01 首次实跑=parallel 3 全白名单，与 T176 手测清单逐行一致）。

- 21. 服务代码-进程版本漂移（T184·11-01 立；编号先查后用——no.20 已被 T177 占用）：T183 给了「一条命令把进程换到新码」，但没有任何东西发现「进程跑的还是旧码」——改完码忘重启时 `/health` 仍报旧版本而全家探针照样绿，漂移可静默数天。本条实读 `/health.server` 与 `Tools/resident_qa_server.py` 源码 `SERVER_VER` 字面量比对：同值 `VERSION-IN-SYNC`；不同判 **WARN（不红门）** 并给修复指针 `python Tools/qa_restart.py restart`（重启窗口内本就可能不一致，红了会被训练成无视）；源码读不到／服务无版本=**UNVERIFIED 不假绿**（服务死由 no.6 负责红，缺测不得升格为漂移）。判据=selftest 六夹具（V1 源码可解析／V2 同值 IN-SYNC／V3 旧版本 WARN 带修复指针／V4 源码不可读 UNVERIFIED／V5 服务静默 UNVERIFIED／V6 双侧缺失不误报）+ live 三态之一；证据 `state/t184-version-drift-20261101.txt`（live `VERSION-IN-SYNC v1.3` 19/19 PASS + 假端点实证漂移 WARN）。

- 22. 回执产物律（M96／BC-P-166·11-03 立）：跑 `cpu_effective_hours.evaluate` 的 7 日窗，`MISSING-LOCAL>0` 或 `COPY>0` 即 FAIL 并点名 `node/id`（回执声称的产物必须能在本机追到文件；跨机回执只到 REMOTE-UNVERIF 不得判缺陷）；`NO-DATA`／`UNVERIFIED` 一律 FAIL 不假绿；`no_deliverable` 只显不红。判据=selftest 4 夹具 + live 出真数。
- 23. serve 瞬态判别（T188·11-03 立）：跑 `serve_health` 的 settle 窗口三态——UP（tags 200 + ≥1 模型）绿；DEGRADED（能答干不了活：零模型／缺指定模型／生成失败）与 DOWN（窗口耗尽才判死）均红并点名 attempts 与理由；载荷读不到=UNVERIFIED 不假绿。单次抖动不得当死亡（10-14 更新器 22s 自愈判例）。判据=selftest 五夹具（`settle_s=20` 只调预算不改判据）。
- 24. 探针登记表三方对账（T190／BC-P-169·11-03 立）：`qa_smoke.PROBE_REGISTRY` 是探针编号唯一声明位——跑 `probe_registry_census.census()` 对三面〔registry 编号／函数定义／charter 条款号〕，重号、编号空档、有探针无条款、有条款无探针一律 FAIL；verdict 必须 `PROBE-REGISTRY-CLEAN`。本条的立行缘由=编号被复用两次（no.20 双占、no.22 双族）且 10-15/19/22/23 长期无条款而每轮照跑。判据=`probe_registry_census.py selftest` 八夹具。

- 25. 写侧编码声明普查（T193·11-03 立）：缺陷要拦在出生地，而写侧此前全靠习惯——`text_integrity_census`／no.19 都是事后读回，`round_append` 的 J8/J10 只护 python 追加这一条路，11-01 那批权威行就是别的写手写坏的。本条跑 `write_encoding_census.census()` 静态走写面：.py 文本写模式（w/a/x/r+）必须显式 `encoding=`，.ps1 的 Set-Content/Add-Content/Out-File/Export-Csv/Export-Clixml 必须带 `-Encoding`；`WRITE-ENCODING-CLEAN` 绿（并报扫描域 scanned/unjudged），`WRITE-ENCODING-RISK` 红并逐条点名 file:line，`WRITE-ENCODING-UNVERIFIED`（不可读／零扫描）一律红——「没读到」不是绿。判据=selftest 四夹具（S1 CLEAN 且报域／S2 RISK 点名 file:line／S3 UNVERIFIED 不假绿／S4 无载荷 FAIL）。
- 26. 共享台账行丢失普查（T198／T-20261103-60 派生·11-03 立）：`PRODUCTION-QUEUE/BACKLOG/LANE-REGISTRY.jsonl` 是整表覆盖写的共享台账，一行只在两个 commit 之间消失几分钟就足够让并发读者读到空档（11-03 13:10:44→13:10:53 实锚：`MV-NEWPROC-PACKET-20261103` 在 QUEUE 与 BACKLOG 同时消失 9 秒后被回填），而 `row_loss_census` 此前只能人手跑，要等下一轮复盘才看得见。本条每轮按**近 4 个 commit** 跑 `row_loss_census.census()`：**永久丢失**（消失且最新 commit 未回填）红并点名 `id older->newer`；**瞬态洞**（消失后又回填）**不红门但必须打印**——并发读者确实看到过洞，静默等于教它「没关系」；单目标可比 commit 不足 2 个记 `N/A`（既不判缺陷也不计入 scanned，禁把「没有历史可比」读成健康）；目标不可读与「全部目标皆 N/A」一律 FAIL。判据=selftest 七夹具（S1 FREE PASS 并报 window/scope／S2 TRANSIENT PASS 但点名 id 与两 commit／S3 FOUND FAIL 点名 PERMANENT／S4 可读性缺口 FAIL 点名 why／S5 历史不足=N/A 不计 scanned／S6 全 N/A FAIL／S7 无载荷 FAIL）+ live 出 verdict/window/scanned/n/a。

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
- 19. 一线索引文字完好性（M93 / D-20261102-03·11-02 立）：跑 	ext_integrity_census.run(DEFAULT_TARGETS)——默认目标固定为受令 7 类（tasks/queue-main/proposals/HQ-FEEDBACK/decisions/orders/reflection-journal），禁靠调用方传参收窄；判读为**顾问档**：本地/外部损坏行均以 loc:file:line、ext:file:line 点名上报且不红门（历史损坏不得阻断产线），但拿不到 summary（异常/不可读）=UNVERIFIED 一律 FAIL（读不到不得当绿）。判据=selftest 五夹具（S1 clean PASS／S2 本地损坏顾问档但点名／S3 外部损坏 ext 标签／S4 无 summary FAIL／S5 默认覆盖 7 类）+ live 出 cats=n/7 与损坏行清单。**11-03 T195 增补（两族损坏）**：编码损坏之外再认**半合并损坏**——同一文件内列首 `<<<<<<<` 与 `>>>>>>>` 成对出现即成冲突块（嵌套按栈配对，逐 marker 行点名，class=`CONFLICT-MARKER`）；单独 `=======`（setext 标题下划线）不判红，围栏代码块内引用的 marker 只记 quoted，未配对 marker 记 orphan（可见 advisory，既不证损坏也不证干净）。实锚=`docs/orders.md` 尾部半合并块长期无人报警而三件编码闸全绿。
- 22. 回执产物律（M96 / BC-P-166·11-03 立；编号先查后用——21 已被 T184 占用）：令的验收面是「queue-receipts 里的产物回执可逐条追到文件」，但 `Tools/cpu_effective_hours.py` 只是手跑命令，回执声称的产物掉盘/被复制顶替时无人当轮看见（M95 实锚：7 日窗口 5 条本机回执产物在 `D:\FleetWorker\review` 目录不存在）。本条按 7 日窗口跑同一 `evaluate()`：**MISSING-LOCAL>0 或 COPY-AS-PRODUCT>0 即 FAIL 并点名 node/id**（这些工时不可核销，不得读成已交付）；**无 deliverable 回执单列可见但不红门**（是「非工作回执」的证据，不是谎报产物）；无可核算回执 / 评估异常=FAIL（NO-DATA 与 UNVERIFIED 都不许当绿）——判据=selftest 四夹具（S1 clean PASS 且 no_deliverable 可见／S2 MISSING-LOCAL FAIL 点名／S3 COPY FAIL 点名／S4 无 summary FAIL）+ live 出 verdict/counted/net_h/三类计数。
- 23. serve 瞬态判别（T188 / T-20261103-56·11-03 立；编号先查后用=23 未被占用）：11-02 22:43 心跳把一次 URLError 直接写成「OLLAMA DOWN」，55 秒后进程在跑、真生成 46ms 成功——单次读失败被当成死亡，与 executor_liveness「读不到≠死」同律此前没有落到 serve 面。本条每轮跑 `serve_health.run(settle_s=20)`：**UP 绿**；**DEGRADED**（/api/tags 有答但零模型/缺指定模型/生成失败=能答干不了活）与 **DOWN**（settle 窗口耗尽才判死）**均红**并点名 attempts/理由；载荷读不到（无 verdict）=**UNVERIFIED 一律 FAIL**，禁拿「没读到」当绿。判据=selftest 五夹具（S1 UP PASS／S2 瞬态恢复仍 UP 且报 recovered_after_s／S3 窗口耗尽 DOWN 红且点名 attempts／S4 DEGRADED 红且理由可见／S5 无 summary FAIL）+ live 出三态之一。
- 26. 共享台账行丢失普查（T198 / T-20261103-60 派生·11-03 立；编号先查后用=26 未被占用）：`PRODUCTION-QUEUE/BACKLOG/LANE-REGISTRY.jsonl` 是整表覆盖写的共享台账，一行只在两个 commit 之间消失几分钟就足够让并发读者读到空档（11-03 13:10:44→13:10:53 实锚：`MV-NEWPROC-PACKET-20261103` 在 QUEUE 与 BACKLOG 同时消失 9 秒后被回填），而 `row_loss_census` 此前只能人手跑，下一轮复盘才看得见。本条每轮按**近 4 个 commit** 跑 `row_loss_census.census()`：**永久丢失**（消失且最新 commit 未回填）红并点名 `id older->newer`；**瞬态洞**（消失后又回填）**不红门但必须打印**——并发读者确实看到了洞，静默等于教它「没关系」；单目标可比 commit 不足 2 个=`N/A`（既不判缺陷也不计入 scanned，禁把「没有历史可比」读成健康——T-50 无效绿同族）；目标不可读（git show 失败等）与「全部目标皆 N/A」一律 FAIL。判据=selftest 七夹具（S1 FREE PASS 并报 window/scope／S2 TRANSIENT PASS 但点名 id 与两 commit／S3 FOUND FAIL 点名 PERMANENT／S4 不可读目标 FAIL 点名 why／S5 历史不足=N/A 不计 scanned／S6 全 N/A FAIL／S7 无载荷 FAIL）+ live 出 verdict/window/scanned/n/a。
