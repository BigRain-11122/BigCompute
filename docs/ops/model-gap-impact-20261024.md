# 缺失模型影响面排查（M81）

日期：2026-10-24 13:0x 排程轮
探针：`Tools/model_impact_probe.py`（只读·selftest 9 checks PASS）
证据：`state/model-impact-20261024-1303.json`

## 一、事实面（实测）

| 面 | 结果 |
|---|---|
| 基线（10-16） | 3 个模型：bge-m3:latest / qwen3.8:27b / qwen3:4b-instruct |
| 现役 live（10-24 13:03） | 1 个：qwen3:4b-instruct |
| 缺失 | 2 个（bge-m3:latest、qwen3.8:27b） |
| 在位性复核 | 两模型 `POST /api/show` 均 HTTP-404（该端点不触发拉取） |
| resident QA :8792 | health 200，model=qwen3.6-35b；:8090 日间超时=CoderServe 工作日 AutoYield 暂停（10-15 CEO 令），非本次漂移 |

## 二、影响面（逐条读码定谳）

### bge-m3:latest — DEGRADED（能力面缺口，无活调用）

- 集团正典声明面：`cph4/resource-chain.md` 模型-机器-服务映射把 bge-m3 列为「RAG 检索 / 本地嵌入服务」在役能力。
- 代码面实测：本司、BigLife、BigMoney、MiniGame、BigDomain 全仓静态扫描**零活调用**；命中均为探针本体/历史试验件/轮次归档件（登记为 NO-IMPACT）。
- 结论：当前无流水线因缺 bge-m3 报错；但任何 RAG/嵌入任务现在不可用（无替档嵌入模型在场）。

### qwen3.8:27b — DEGRADED（兜底与清单面缺口）

| 命中 | 判定 | 依据 |
|---|---|---|
| MiniGame `tools/llm/local_coder.py` | DEGRADED | `DEFAULT_MODELS` 次选（主 qwen3.6-coder:35b）→ 仅失兜底 |
| MiniGame `tools/FleetJsonWrite.ps1` | DEGRADED | 机队 JSON 模型清单数组含该名（展示/记账面） |
| MiniGame `tools/llm/bench_bgc.py` | NO-IMPACT | heavy tier 由 `if present` 门控，缺席自动跳过 |
| MiniGame `tools/llm/research_digest.py` | NO-IMPACT | 仅注释提及可选升级 |
| BigLife / BigMoney / 本司归档脚本 | NO-IMPACT | 志言字符串，非调用路径 |

## 三、决议

1. 两模型结论均为 **DEGRADED**，不是 NEED-RESTORE：无任何活代码路径因缺模直接失败。
2. 恢复动作**不由本司自决**（M71 判例：禁自决重拉），归模型供应方窗呈批；呈批件需带本节两行影响面。
3. 基线维持不刷新（M80 政策：能力缺失=维持告警，不刷基线）。

## 四、复现

```
python Tools/model_impact_probe.py selftest
python Tools/model_impact_probe.py run
```