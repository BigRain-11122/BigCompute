# R-20260930-serve-concurrency-parallelism — 常驻 serve 并发基线首件（议程族 A 续窗·T-20260926-22 承接）

> 立项三问（P-65 ①）：**消费方**=BC-P-04 MaaS 试点（工单并发上限条款面）/BC-P-12 双态 SLA 承诺结构（第三判据=并发档位）/批池 lane B 文本重批执行面（激活后并发收益预期）/BC-P-02 算力透明凭证直播展示（并发负载可视化）。**单位经济**=并发聚合 ≈1.6-1.9× 单流→同一常驻位（同电费同折旧）产能上界近翻倍→口径 B 单 token 成本带下移潜力（族 B 交叉面·N1 后窗并排双报）。**合规**=纯本机推理零数据出域（AIGC 同例）·只读探针零派活（DRY-RUN 观察期合规）。

## 一、官方锚与现役配置实证

- **本机 user 级 OLLAMA_NUM_PARALLEL=2 显式在位**（PowerShell User/Machine/shell 三面实测 2026-09-30 13:1x·ollama 0.34.4）——**归因收口（E32·09-30 13:3x）**：设置者=CPH4 infra 件 R-20260924-infra-3-llm.md（L12 配置行+L112-120 配置表+L120 实施路径·L114 理由「防队首阻塞；7b KV 448MiB（q8_0 后 224MiB）可承受」）；全集团 rg 唯一提及=该件（E31 候选归因「MiniGame GPU 产线」判负修正）；user 级四键齐=KEEP_ALIVE=15m/NUM_PARALLEL=2/MAX_LOADED_MODELS=2/**CONTEXT_LENGTH=4096**（=「常驻位 4096」根因锚·CPH4 L118「显式钉住防版本默认漂移」+官方默认值同 4096→R-20260928「静默缩窗发现」定谳=有意钉住非意外）；CPH4 spec「机器级」（L120）vs 实测 user 级差=CPH4 域注记（功能面实证有效：E31 双槽激活+qa 常驻 15m）；FLASH_ATTENTION=1/KV_CACHE_TYPE=q8_0 spec 行未在 registry=默认 f16/auto 生效面如实。
- **官方原文级锚 ✅（E32·docs.ollama.com/faq 实抓 09-30 13:3x）**：①「By default, Ollama uses a context window size of 4096 tokens. This can be overridden with the OLLAMA_CONTEXT_LENGTH environment variable.」②**「Parallel request processing for a given model results in increasing the context size by the number of parallel requests. For example, a 2K context with 4 parallel requests will result in an 8K context and additional memory allocation.」**（并行=扩窗非分窗·每并发请求各享完整 ctx 窗）③「OLLAMA_NUM_PARALLEL - The maximum number of parallel requests each model will process at the same time, default 1. Required RAM will scale by OLLAMA_NUM_PARALLEL * OLLAMA_CONTEXT_LENGTH.」；keep_alive 机制锚 ✅ 复用 R-20260928-inference-serving-standard（URL核验件）。

## 二、实测数据（Tools/serve_concurrency_probe.py·qwen2.5:7b-instruct Q4_K_M 常驻 num_ctx 4096·num_predict 64·keep_alive:-1 每调用）

| 跑次 | 态 | 基线单流 tok/s | n=2 聚合 | n=2 单流降级 | n=4 聚合 | n=4 单流降级 |
|---|---|---|---|---|---|---|
| 13:17 | 净窗带 | 78.7 | 142.6（1.81×） | 1.02× | 147.8（1.88×） | 1.00× |
| 13:18 | 净窗带 | 84.9 | 156.3（1.84×） | 1.04× | 157.3（1.85×） | 1.04× |
| 13:19 | 竞争带 | 30.6 | 49.5（1.61×） | 1.21× | 53.6（1.75×） | 1.12× |

判读四条：
1. **真并行坐实**（非服务端串行排队）：n=2 单流降级仅 1.02-1.21×+聚合 1.61-1.84×→NUM_PARALLEL=2 双槽生效。
2. **槽位上界饱和**：n=4 聚合≈n=2（净窗带 157.3≈156.3）→超 2 槽即排队·零聚合增益。
3. **并发收益跨双态稳健**（新发现）：竞争带聚合 1.61-1.75×→竞争窗单流降级 0.32×（R-39）经并发口径缓和至 0.55×（30.6→53.6）——与 E28/R-39 双态结构正交复合·第三判据数据双态齐。
4. **常驻态零损伤**：三轮 ollama ps Forever 全程维持；首跑判定面大小写 bug（「FOREVER」≠「Forever」）误报 LOST=自审步捕获即修（v0.3 复跑 Forever-maintained 实证·数据面不受判定 bug 影响）。

## 三、SLA 承诺结构第三判据（建议级·BC-P-12 更新候选）

- 并发档位承诺：c=1 单流基线带（E28 净窗基线 93.93 tok/s·CV 5.8% 同口径）；c=2 聚合带 1.8-1.9×（净窗）；c≥3 排队不增（超槽=延迟线性升吞吐平）。
- 月末产能面口径：**N1 窗前冻结维持（BC-P-15）**——本件仅预注册 N1 后窗并排双报（单流/聚合分列·防口径混用）·N1 当窗仍以单流净窗基线为唯一产能口径。

## 四、预注册判据 K1-K4

- K1：NUM_PARALLEL=4 调优对比试验窗候选（KV 显存 2×→4× 代价实测·随 7b 维护窗/净窗·让路律+DRY-RUN 后）。
- K2：BC-P-12 承诺结构更新=并发档位维度入提案（本窗回流 BC-P-27）。
- K3：MaaS 试点工单并发上限条款候选（单工单并发 ≤2=现役槽位·consumer_plan 指名 O-1820 ③ 同律）。
- K4：月末产能面并发口径并排双报（N1 后窗生效·单流/聚合分列）。

## 五、应用表（P-65 ②）

| 应用位 | 状态 |
|---|---|
| BC-P-04 MaaS 试点 SLA 条款 | 数据前置毕（观察位·K3） |
| BC-P-12 双态 SLA 承诺结构 | 第三判据候选（观察位·K2） |
| 批池 lane B 文本重批执行面 | 激活后并发收益预期 1.8×（观察位） |
| E28/E25 产能窗基线 | N1 后双报预注册（K4） |
| R-20260928 U240 常驻标准五项 | 第六项候选=并发档位（建议级） |
| 风险面 | 无新行（R-33/R-39 既有覆盖） |

## 六、验证声明（P-65 ③）

- 内证：本机三轮实测 JSON 在盘（state/serve-concurrency-20260930-1317/1318/1319.json·eval_count/eval_duration 引擎侧出数）；探针 selftest 7/7；QA 5/5 同轮在案。
- 外证：OLLAMA_NUM_PARALLEL 官方原文级 ⬜（docs 迁移 404×2·三抓止损）·要点级 🟡 如实标注；keep_alive 锚复用在册已核验件。
- 建议级标注：SLA 承诺结构/K1 调优窗/配置归因均为建议级；数值承诺冻结权随 CEO/试点批；N1 前口径冻结维持。

## 七、E32 续窗增量（09-30 13:3x 窗·归因收口+ctx 并发语义定谳）

- **三面互证链**：官方 FAQ 原文级锚（并行=扩窗·RAM 按 N×ctx 规模化）×CPH4 infra KV 数学（7b 56KiB/tok→224MiB/槽@4096·L114「KV 448MiB」=2 槽×224MiB 逐位吻合）×本件 §二 行为实证（n=2 聚合 1.61-1.88×·单流降级仅 1.02-1.21×）——语义定谳=**每并发请求各享完整 4096 ctx 窗·KV 显存按槽位数 N× 增量**。
- **商业面定谳（K3/BC-P-04 工单条款语义锚）**：并发承载不缩每请求 ctx 窗（E16 B 端报告成品体量 2305-8397 tokens 并发无缩窗风险）；代价面=KV 显存 N×（K1 调优窗成本式）；**单请求 >4096 例外路径=per-request num_ctx 8192 离峰档**（CPH4 L72「7b 的 P3 离峰任务可 per-request num_ctx 8192」·14b 禁 8192 照 §2.2）→提案 BC-P-28（ctx 预算工单条款候选·与 BC-P-27 并发上限条款互补分立）。
- 证据：state/parallel-config-provenance-20260930.json（registry 四键实读+归因链+官方原文三引+交叉验证）；explore E32 行（新增即耗）。
