# R-20260928-bonsai-empowerment-summary-trial — Ternary-Bonsai-2-27B 赋能摘要批对照试验回执（BigCompute 切片）

> 溯源：机队分发试用令 P-2026-09-28-07（orders/O-20260928-1326-HQ-C.md·集团 orders.md L272）；规格件=cph4/research/R-20260928-bonsai-fleet-trial.md（§四 P2 赋能摘要批/§五 回执四件套·引用不复制）。认领=13:29 哨兵唤醒轮（orders 13:24:38 落盘→本仓令件 commit 13:27→起跑 13:29·30min ack SLA 内=R-25 正向实证二十三连）；回执落盘 09-28 13:4x=回执窗 09-30 13:00 窗内提前 ~47h。

## 一、部署（四件套 1）
- 模型=Ternary-Bonsai-2-27B-PTQ1_0.gguf 字节锚实测 **5,946,648,928 = PASS**（os.path.getsize·规格件 §一.1 锚逐位吻合·不齐禁上岗律过）。
- 运行时=PrismML-Eng/llama.cpp fork（prism-b10743·Win CUDA 12.4·**MIT**〔GitHub 仓库页直读〕）bm-a 在盘件直用（禁 git/禁跨机传输·U187/U240 律）。
- 服起：`llama-server -ngl 0 --port 8078 -c 4096`→/health **ok·load 4.1s**（探针脚本自动起停·server.log 在档）。
- 弱机档要领承接（规格件 §三教训律）：全程显式 `chat_template_kwargs.enable_thinking=false`——六请求零思考链吃预算事故。

## 二、速度（四件套 2·tg128 实测对照包络）
| 档 | tg128 实测 | 摘要延迟（60 字档） | 包络对照 |
|---|---|---|---|
| Bonsai CPU（-ngl 0·本窗） | **3.20 tok/s**（longgen ctok=128 实生成） | 12.1-17.6 s/件（ctok 40-60） | CPH4 bm-a CPU 包络 2.7-3.3 tok/s 内 ✓（规格件 §三锚复证） |
| 现役 7b（Ollama qwen2.5:7b-instruct·GPU 常驻 Forever） | **89.3 tok/s**（ctok=126） | 0.7-0.9 s/件（ctok 51-72） | — |

- **档位不对称如实注记**：CPU 档 vs GPU 档非同档对照（句级延迟差 ~17×）——GPU 档重验（`--ngl 99` 复跑探针脚本）=22:43 夜轮 VRAM 释放窗承接（CPH4 GPU bench 锚 54.7 tok/s 预期带）。

## 三、共存（四件套 3·与在役栈同卡如实）
- 试验期间在役栈零扰动：Ollama 7b 常驻 5.1GB/100% GPU/Forever 不变（对照请求全程真响应）+三 Tuanjie 编辑器照跑+qa 烟测链零冲突。
- llama-server 进程 RAM 实测 6.56-7.99 GiB（tasklist 双进程行快照·与规格件 §三 8.3GB 同量级）；起服前本机空闲 RAM 49.8GB 充裕。
- **如实差异注记**：-ngl 0 下 fork 仍有 CUDA 上下文/融合算子初始化于 CUDA0（server.log 警告行「layer 0 … assigned to device CUDA0」在档）→ 显存增量实测 **+306~377 MiB**（10622→10999 MiB·他进程波动不能完全排除如实注记）·非规格件 §三「显存零占用」绝对零——共存判定仍=**可共驻（CPU 档）**。

## 四、质量（四件套 4·本域样本 ≥3 抽检·对错自评+引用级）
样本=赋能目录三真实件（docs/ops/empowerment-catalog-v1.md §三 计价双口径/§四服务项 #3 serve 常驻标准+族 B 成本锚方法论）·「60 字概括服务对象/能力/关键数值」同题对照：

| 赋能件 | Bonsai 要点自评（对源核） | 保真 | 7b 要点自评 | 保真 |
|---|---|---|---|---|
| p1 计价双口径 | 1M token 粒度✓/月度超发 CEO 审批✓/Ollama 精确计数✓/机队成本锚=口径 B✓（口径 A $0.15 锚漏·零错数） | ✓ | $0.15/1M（口径 A）✓/双口径✓/1M 粒度✓ | ✓ |
| p2 serve 常驻标准 | 五项列四✓/缩窗 4096÷32768✓/Q4_K_M✓/Apache 2.0✓ | ✓ | 五项全列✓/keep_alive 默认 5min✓/4096✓/Apache 2.0✓ | ✓ |
| p3 成本锚方法论 | 折旧 3/5 年✓/残值 5%✓/TDP 220W✓/idle 32.92W✓/power.limit 242W✓ | ✓ | 五数全对✓/月末归集✓/判据 4 条✓ | ✓ |

- 保真判定：**Bonsai 3/3 零错数（p1 漏一锚数·60 字内取舍非幻觉）、7b 3/3 全对**——要点保真平手；Bonsai 输出更密炼（零客套前言直入要点），7b 三段式格式合规更整齐。
- 原始探针全档=state/trial-bonsai-20260928-1332/（probe.log 全输出+时延+共存快照·server.log 服起日志·state/=运行态隔离面 gitignored·证据=盘上文件+本件引用）；探针脚本=Tools/bonsai_fleet_trial_probe.py（--ngl/--port 参数化=GPU 档复跑载体）。

## 五、结论三态：**observe**（继续观察）
- 判据依据：①质量=零错数平手（27B 智力优势在 60 字摘要档未显差=符合预期·摘要非 27B 强项）；②速度=CPU 档句级可用/批量不经济（3.20 vs 89 tok/s·~17×）；③**GPU 档重验未跑**（VRAM 被在役栈占用·让路律不抢）=adopt 判定关键缺口。
- adopt 前置（若 GPU 档重验达标）：过 O-2175 评估+fleet-allocations §8.4 改行（令件边界维持）；用途预判=本司重批摘要/长文 digest 档·非句级产线。
- 不判 reject 依据：零质量错数+MIT 许可+fork 活跃（809★/155 forks·release 在发）+字节锚齐+共驻零冲突。

## 验证声明
本件全部数字=本窗实跑（state/trial-bonsai-20260928-1332/probe.log 原始档逐行在）或直读引用（规格件 §三锚/CPH4 GPU bench/GitHub 许可页面）；零未跑先写；对照档硬件不对称（CPU vs GPU）已如实注记；防重复对照=同款试验单机一次（bm-a 一次·本切片独立于 CPH4 §三业务探针·七探针零重叠·无双机双批双产）。
