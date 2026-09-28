# R-20260928 推理服务化（方向族 A·U240 口径）：serve 常驻标准与量化部署矩阵 v0.1

> 溯源：议程滚动件族 A 首 R-（T-20260926-22·12:43 午班窗首对象）+O-20260926-2253 ①「机队各机模型自配：serve 常驻标准+分发规范」+赋能目录 §二.3/§四服务项 #3+MiniGame GPU 产线台账口径（ComfyUI 8188+Ollama 常驻军团 U240·只读引用）。
> 范围边界：本件=serve 常驻标准（keep_alive 常驻口径+缺口修复路径+常驻探针）与量化部署矩阵（档位×显存包络×上下文面）；分发通道纪律=R-20260927-model-distribution-discipline（族 D·不重做）；成本锚=口径 B（族 B R-20260928-compute-cost-economics·不重做）；真发放随 P-67 试点三前置不变。
> 状态：🟡 草案级 v0.1（标准条目=定谳候选；机队扩面实测 ⬜ 随各机自领回填·如实不造）。

## 立项三问（P-65 行程标准）

1. **为谁而研（消费方点名）**：本司服务承接面（赋能目录 §四服务项 #3 交付物）+机队各 GPU 机（U240 serve 常驻标准消费方·fleet-allocations §八「serve 7b 常驻=全 GPU 机」）+MiniGame GPU 产线（Ollama 常驻军团）+CPH4 local-llm-pipeline Phase1（全机 serve 常驻化·P-08 对接面）；直接消费=本机常驻态标准执行（本轮已跑 1 次）+机队扩面自领（⬜）。
2. **仓内已有什么（重复即违法）**：qa_smoke 探针（Ollama api/generate 真响应+ollama ps 巡检·每轮例行）+ollama list 四模型在盘（7b-instruct/14b/30b/bge-m3）+fleet-allocations §八 分工口径+族 B 常驻功耗基线（32.92W idle 实测）——**缺**=常驻机制成文（keep_alive 语义/卸载风险/保活路径）与量化档部署矩阵（本件补此两环·零重做）。
3. **判据预注册**：Q5 四条（常驻标准五项自证/机队扩面回填/量化档实测对比/常驻探针零缺位周）。

## 待答问题

- [x] Q1 serve 常驻官方机制语义 ——已答（keep_alive 默认 5m+load/unload 语义+tok/s 口径）
- [x] Q2 常驻态缺口与修复路径 ——已答（12:43 实测缺口坐实+修复路径验证 1 次成功）
- [x] Q3 U240 serve 常驻标准（五项）——已答（定谳候选）
- [x] Q4 量化部署矩阵 ——已答（官方阶梯+本机实测档位+显存包络）
- [x] Q5 判据 ——已答（四条）

## 发现

### Q1 官方机制语义【URL核验 2026-09-28】

- **GitHub ollama/ollama 官方 docs/api.md 逐字**：`keep_alive`: "controls how long the model will stay loaded into memory following the request (default: `5m`)"（generate/chat/embed 三端点同句）；load 语义："If an empty prompt is provided, the model will be loaded into memory."；unload 语义："If an empty prompt is provided and the `keep_alive` parameter is set to `0`, a model will be unloaded from memory."；tok/s 口径："To calculate how fast the response is generated in tokens per second (token/s), divide `eval_count` / `eval_duration` * `10^9`."（=qa_smoke 探针同式）。docs.ollama.com 两 URL 已迁移 404（guides/quantization、features/keep-alive 均判负·GitHub 官方仓文档为现行有效源）。
- **ollama.com/library/qwen2.5 官方页**：阶梯 0.5b=398MB/1.5b=986MB/3b=1.9GB/7b=4.7GB/14b=9.0GB/32b=20GB/72b=47GB·全系 "32K context window"；许可原文 "all models except the 3B and 72B are released under the Apache 2.0 license, while the 3B and 72B models are under the Qwen license"（**7b=Apache 2.0 ✓**·3b/72b=Qwen license 注记入矩阵红线）。
- **默认量化档官方文档级内证**：api.md /api/tags 官方示例响应 `"quantization_level": "Q4_K_M"`（deepseek-r1:latest 与 llama3.2:latest 双例）=library 默认 tag 的量化档=Q4_K_M。

### Q2 常驻态缺口与修复路径（12:43 本机实测）

1. **缺口坐实**：12:43 `ollama ps` 输出=空（无 loaded model）——对照 03:1x 族 B 件内证「qwen2.5:7b-instruct 5.1 GB·100% GPU·UNTIL=Forever」=常驻态已丢失。机制归因=官方默认 keep_alive 5m：上轮探针调用后 5min 无请求即卸载，**常驻非默认自持**（U240 常驻口径的执行缺口·非设备故障）。
2. **修复路径验证 1 次**【内证 2026-09-28 12:46·实跑输出在案】：`Invoke-RestMethod POST /api/generate -Body '{"model":"qwen2.5:7b-instruct","keep_alive":-1}'` → 响应 `{"done":true,"done_reason":"load"}`（空 prompt=官方 load 语义）→ `ollama ps` 复显 `qwen2.5:7b-instruct 5.1 GB 100% GPU CONTEXT 4096 UNTIL=Forever`=常驻态恢复。第一次尝试经 shell 双层转义 JSON 损坏报错（{"error":"invalid character..."}·如实记·标准路径不受影响）。
3. **副发现（上下文面）**：常驻后 ollama ps 显示 CONTEXT **4096**≠模型原生 32768（ollama show：context length 32768）——加载默认 num_ctx=4096。常驻标准须显式配 num_ctx（部署矩阵一维·防「32K 模型按 4K 用」的静默缩窗）。

### Q3 U240 serve 常驻标准（五项·定谳候选）

| # | 标准项 | 判据 | 本机现状 |
|---|---|---|---|
| 1 | 服务自启 | Ollama 服务开机自启+端口 11434 可达 | ✓ 在役（12:43 API 可达实证） |
| 2 | 模型预载 | 常驻模型档案登记+加载动作可复跑（空 prompt load） | ✓ 修复路径验证 1 次（Q2） |
| 3 | keep_alive=-1 | 常驻调用必带 keep_alive:-1（或服务级 OLLAMA_KEEP_ALIVE=-1） | ✓ 已恢复 Forever（12:46）·**常态化⬜=探针脚本固化挂 tech T15** |
| 4 | 常驻探针 | 每轮 ollama ps 巡检（qa_smoke 四探针之一）+常驻丢失=即载即记 | ✓ 探针例行（丢而复得即本轮实证） |
| 5 | 档案登记 | 机×模型×量化档×num_ctx 四元组入档（本件 §部署矩阵=档案面） | ✓ 本机 1 行（下表）·机队扩面 ⬜ |

### Q4 量化部署矩阵（官方阶梯×本机实测）

**本机在盘四模型实测**【ollama list/show 12:43 实跑】：

| 模型 | 量化档 | 体积 | 原生上下文 | 常驻显存 | 许可 | 用途档 |
|---|---|---|---|---|---|---|
| qwen2.5:7b-instruct | **Q4_K_M**（show 实证） | 4.7GB | 32768 | 5.1GB（ps 实测·100% GPU） | Apache 2.0 | **主力常驻档**（U240 标准位） |
| qwen2.5:14b | ⬜（show 待采·体积 9.0GB） | 9.0GB | 32768 | ⬜ | Apache 2.0 | 大任务升档位（按需加载·非默认常驻） |
| qwen3-coder:30b | ⬜（体积 18GB） | 18GB | ⬜ | ⬜（>本机 12GB 显存=须 CPU offload） | ⬜ | 代码重批专用（offload 档·禁常驻位） |
| bge-m3:latest | ⬜（嵌入档） | 1.2GB | ⬜ | ⬜ | ⬜ | 检索嵌入 lane（CPH4 Phase1 bge-m3 对齐） |

**档位选型律（设计值·定值权=本司工程面+集团调度口径）**：默认 tag=Q4_K_M（官方文档级内证）=精度-体积平衡位；升档 Q8_0/FP16 只进大任务按需位（显存包络 1.7-2×）；30b 级大件在 12GB 卡=CPU offload 档禁占常驻位（吞吐塌陷面）；3b/72b=Qwen license 档入分发须许可门复验（Q1 许可原文）。显存包络判据=常驻显存（ps size）≤卡显存 70%（本机 5.1/12.28=41.6% ✓·双模型并驻上限设计值=2 个 7b 档）。

### Q5 判据（预注册·回填窗）

1. **常驻标准五项自证清单**（Q3 表）本机全绿后方可宣布「U240 常驻标准本机达标」（第 3 项常态化探针固化=tech T15 承接）；
2. **机队扩面回填**：各 GPU 机按 fleet-allocations §八 自领→逐机补 Q4 矩阵行（show 采档+ps 采常驻显存）——达档机 ≥3 台=标准成文 v1 升版窗；
3. **量化档实测对比**：qwen2.5:7b Q4_K_M vs Q8_0 同题三指标（延迟 tok/s/常驻显存/功耗）——进 tech T15 工程件·对比差超带呈工程面复核；
4. **常驻探针零缺位周**：连续 7 轮 qa_smoke ollama ps 巡检记录常驻态在位（丢而复得计入=探针有效性自证）——观察窗随 T15 落地起算。

## 应用表（P-65 强制·结论应用律）

| 消费面 | 应用 | 判据/观察位 |
|---|---|---|
| 赋能目录 §四服务项 #3 | 交付物=本件（常驻标准+量化矩阵）·§四状态行回填 | 本轮 |
| tech T15 工程件（新立） | keep_alive 常态化探针固化+量化档实测对比 | 判据 3/4 |
| qa_smoke 探针面 | ollama ps 巡检语义升级：常驻丢失=即载（keep_alive:-1）+记行 | 本轮实证 |
| E12 单包实测锚件 | num_ctx 配置面进实测锚（4096≠32K 静默缩窗防） | 观察位 |
| 机队各 GPU 机（U240 扩面） | Q3 五项+Q4 矩阵逐机自领回填 | 判据 2·⬜ |
| BC-P-04 MaaS 试点提案（新立） | 局域网 serve 实时计费面的技术依据=本件常驻标准 | 提案面 |

## 验证声明（P-65）

- **外证**：GitHub ollama/ollama 官方 docs/api.md 逐字引句（keep_alive 默认 5m/load/unload 语义/tok/s 口径/Q4_K_M 官方示例）+ollama.com/library/qwen2.5 官方页（阶梯体积/32K 上下文/Apache 2.0 许可带原文）【URL核验 2026-09-28】。
- **内证**：本机 12:43-12:46 实跑三链（ollama ps 空=缺口坐实→Invoke-RestMethod keep_alive:-1 done_reason=load→ps 复显 5.1GB/100% GPU/Forever）+ollama show qwen2.5:7b-instruct（Q4_K_M/32768/Apache 2.0）+nvidia-smi（4070S 12282MiB/power 34.00W/idle 5%）——命令输出在案。
- **内知 ⬜**：14b/30b/bge-m3 三模型 show 量化档待采；机队他机扩面全 ⬜；OLLAMA_KEEP_ALIVE 服务级环境变量未实测（本轮走请求级 keep_alive:-1 路径）；30b offload 吞吐未测。
- **建议级标注**：五项标准/档位选型律/显存 70% 包络/双模型并驻上限=设计值非实况·定值权=本司工程面+集团调度口径；商用 SLA 面随开店物理件后另立。

## ⬜ 未验项（如实不造）

机队扩面 Q4 矩阵 ⬜；14b/30b/bge-m3 量化档 show 采集 ⬜；Q8_0 对比实测 ⬜；服务级 OLLAMA_KEEP_ALIVE=-1 环境变量面 ⬜；num_ctx 显式配置面 ⬜（当前常驻位=4096 默认）；30b CPU offload 吞吐 ⬜。

## 落点

任务板 T-20260926-22（族 A 首 R- ✅）+赋能目录 §四服务项 #3 回填+议程滚动件窗轮转账行+风险台账维护注记+BLUEPRINT §七 消化台账行。
