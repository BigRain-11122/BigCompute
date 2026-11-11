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

> 逐条细则已按探针族拆入 `docs/qa-probes-20261112-a.md`（5-18）与 `docs/qa-probes-20261112-b.md`（19-33）；编号与判据仍在下方清单与探针表，禁在附录新增编号。

- 5. rounds.log UTF-8 解码完整性｜细节=docs/qa-probes-20261112-a.md·p5
- 6. resident QA serve 8792 /health｜细节=docs/qa-probes-20261112-a.md·p6
- 7. 预留位维持｜细节=docs/qa-probes-20261112-a.md·p7
- 8. git-tracked 台账尾行完整性｜细节=docs/qa-probes-20261112-a.md·p8
- 9. L1 coder 车道 :8090 活体｜细节=docs/qa-probes-20261112-a.md·p9
- 10. 供给双口径对读｜细节=docs/qa-probes-20261112-a.md·p10
- 11. runbook 字节帽｜细节=docs/qa-probes-20261112-a.md·p11
- 12. 成本轨字段契约普查｜细节=docs/qa-probes-20261112-a.md·p12
- 13. 车道前置件在位性+格式外形｜细节=docs/qa-probes-20261112-a.md·p13
- 14. CEO 实况面机械闸｜细节=docs/qa-probes-20261112-a.md·p14
- 15. 台账编码健康｜细节=docs/qa-probes-20261112-a.md·p15
- 16. resident QA 8792 /ask 功能面｜细节=docs/qa-probes-20261112-a.md·p16
- 17. 扫描根自指普查｜细节=docs/qa-probes-20261112-a.md·p17
- 18. 心跳 token 守卫｜细节=docs/qa-probes-20261112-a.md·p18
- 19. 一线索引文本完好性｜细节=docs/qa-probes-20261112-b.md·p19
- 20. 台账并行｜细节=docs/qa-probes-20261112-b.md·p20
- 21. 服务代码-进程版本漂移｜细节=docs/qa-probes-20261112-b.md·p21
- 22. 回执产物律｜细节=docs/qa-probes-20261112-b.md·p22
- 23. serve 瞬态判别｜细节=docs/qa-probes-20261112-b.md·p23
- 24. 探针登记表三方对账｜细节=docs/qa-probes-20261112-b.md·p24
- 25. 写侧编码声明普查｜细节=docs/qa-probes-20261112-b.md·p25
- 26. 共享台账行丢失普查｜细节=docs/qa-probes-20261112-b.md·p26
- 27. 售后轨可判性｜细节=docs/qa-probes-20261112-b.md·p27
- 28. 植入集完整性普查｜细节=docs/qa-probes-20261112-b.md·p28
- 29. 钱面写入守卫普查｜细节=docs/qa-probes-20261112-b.md·p29
- 30. 点火链自身回归｜细节=docs/qa-probes-20261112-b.md·p30
- 31. 渲染帧质检读数不许烂在回执里｜细节=docs/qa-probes-20261112-b.md·p31
- 32. 派工判决必须自证「是否现测」｜细节=docs/qa-probes-20261112-b.md·p32
- 33. `unknown` 的收敛读数必须仍然对得上盘｜细节=docs/qa-probes-20261112-b.md·p33

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

**条款正文单一真源=§三**（T200·11-03 去双份）：第 19 / 22 / 23 / 26 条此前在本区各复制了一份且措辞已漂移，现只保留 §三 正文，本区不再承载条款；probe_registry_census 增 DUPLICATE-CLAUSE 断言，同号多份即红。
