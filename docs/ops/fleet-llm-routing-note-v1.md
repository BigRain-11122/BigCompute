# 机队 LLM 路由消费方须知 v1.0（bm-c ollama 端点退役迁移窗）

> 依据：CEO 令 2026-10-17 09:5x「bm-c 生成视觉-only·大模型的调用去 A 机，马上整」（orders a80c687 前置行实读）+ E75/E76 勘注链（R-20260929-fleet-inference-serving-expansion §一/§八）+ BC-P-84 批活转化（判据三问过·2026-10-17 11:0x 轮·E77）。本件=消费方一页件：只读引用实测锚；路由正典权归 CPH4（resource-chain §9.1 只读注记·禁代改）。

## 一、路由终点速查

| 任务档 | 终点机器 | 端点 | 适配面 |
|---|---|---|---|
| 重件（代码/长上下文/深知识） | bm-a | `http://bm-a:8090/v1`（OpenAI 兼容·MagicDNS；IP=100.110.185.62〔2026-10-17 tailscale ip 实测〕） | 循环班 L1 档·代码生成·长文档分析 |
| 轻件（短摘要/分类） | bm-b | bm-b 4b CPU 档（bili-reply 线同型·qwen3.8:4b·num_gpu=0 零 VRAM） | 短摘要·分类件 |

bm-c ollama 端点（:11434·E75 行文「1434 端点」）已退役——一切调用面停止指向 bm-c；27B 深判承接移交 bm-d 大脑 lane（预备·未落地）。

## 二、重档端点契约（bm-a :8090）

1. **形态**：llama.cpp llama-server b11429（OpenAI 兼容 `/v1/chat/completions`）·0.0.0.0 绑定·tailnet 直达；`model` 字段以 `GET /v1/models` 返回 id 为准（当前=模型文件全路径形态 Qwen3.6-35B-A3B-UD-IQ4_XS.gguf）。
2. **模型**：Qwen3.6-35B-A3B（UD-IQ4_XS 17.7G·`--n-cpu-moe` CPU 卸载·~2.9G VRAM 档·双槽并发 2）。
3. **ctx 窗**：服务端固定双槽 8k——**llama.cpp 族端点不支持请求内改窗**。〔勘正注记：R- §二之3「跨机调用必带 num_ctx 声明」契约行面向 Ollama 族端点（:11434 族·静默缩窗 4096≠32768 防再发）；:8090 非该族——BC-P-84 原案并提两族，本件按端点族分立。〕长文由消费方自行切分。
4. **提速位**：请求带 `chat_template_kwargs: {"enable_thinking": false}`（思考模型默认关思考提速；硬题可开）——本窗实测请求可接受（200）。
5. **吞吐锚（双带实测·消费方排程依据）**：
   - 独占窗锚 **36.7 tok/s**（2026-10-14 live·55tok/1.5s·thinking off·state/t92-coder-probe-20261014.txt·与部署回执 36-38 字/s 档一致）；
   - 共租饱和窗实测 **0.57-0.59 tok/s E2E**（2026-10-17 双跑·20 tok 完成 ~35s·小请求 E2E 延迟主导·state/e77-l1-live-probe-20261017.json + -cold.json）——成因=bm-a 共租 CPU 饱和（同窗 idle_selfcheck py_cpu=90-100% 实测）+四文本车道（BigLife 年轮/MV 分镜/8792 city QA 后端/BigMoney advisor·M71 迁移链）并发共享双槽。**批处理消费方遇饱和窗退避重试（间隔 ≥60s·≤2 次）；交互单发预期首响应可达 ~35s 级。**
6. **守护**：bm-a-CoderServe PT10M 看门狗（PID 锁+路径闸·死亡自动复活）；CEO 让路窗（machine-state pause）合法下线·resume 自愈——消费方遇 502/超时先判让路窗再报障。
7. **计量面**：响应自带 usage 回执（prompt_tokens/completion_tokens=唯一 token 源）——本司 L1 计量轨在役（l1-record·月闸 cap 200 Mtok·--ceo-approved 豁免位）；机队消费方如需纳入集团计量面，usage 回执随台账通道传 BigCompute。

## 三、轻档边界（bm-b 4b）

- bm-b 4b CPU 档（qwen3.8:4b·CPU-only num_gpu=0·零 VRAM·bili-reply 线现役同型）——短摘要/分类件适配；重件勿投（CPU 档吞吐边界）。
- 出处=R- §一 bm-b 行（E76 复评：GPU 档 7b 常驻建议废止·轻件承接=CPU 4b 档·双前提失效链）。

## 四、恒定纪律（消费方必读）

1. **让路律恒定**：CEO 用机让路（pause）合法下线一切本机大模型服务——消费方不判事故、不重复报障。
2. **headroom**：重档 ~2.9G VRAM 档与共租产线共存——禁独占假设；重件批与 GPU 产线互让（让路序=CEO 用机＞机队视频产线＞批池）。
3. **禁跨仓写**：本件=只读引用他司正典；路由法变更权归 CPH4/委员会，变更随集团 orders 令。
4. **时效**：本件锚时点=2026-10-17；端点/模型/吞吐带任一变更，本件随令复刷（v1.x 滚动）。

## 五、验证声明

- 全部数字=实测锚直引（t92 探针 10-14+本窗双跑 10-17）零编造；饱和窗成因归因=同窗 py_cpu 实测+四车道并发事实（M71 迁移链），未做对照归因实验（诚实注记）。
- 判负路径：若后续复测证明 ~0.6 带为看门狗重启竞态等瞬态（非共租饱和），本节按 BC-P-74 复拍判定律复验后修订。
- 落点=docs/ops/fleet-llm-routing-note-v1.md·BC-P-84 converted（2026-10-17）·消费面=各机循环班迁移窗+本司赋能目录 §二.3 承接面。
