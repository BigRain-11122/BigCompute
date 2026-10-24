# D-20261025-06 域内任务书面确认件（BigCompute 面）v1.0

- 决策：D-20261025-06（27 件 UNEXPECTED-DISABLED 分类裁定·确认死线 2026-10-26 06:00）
- 执行：BigCompute-OSLoop 哨兵触发轮 2026-10-25 06:1x（bm-a 本机只读实读）
- 证据：`state/d25-06-task-state-evidence-20261025.json`（Get-ScheduledTask 实读原文级·零启用零停用）
- 复现命令：`Get-ScheduledTask | ? {$_.TaskName -in @('HQ-Patrol-BigCompute','BmaIdleGuard','HQ-bm-a-Sentinel','HQ-OpenClaw-ModelDay','HQ-OpenClaw-ModelNight','HQ-OpenClawGateway','HQ-LocalOnlyOn')} | Select TaskName,State` + `Get-ScheduledTaskInfo`

## 一、一行书面确认（结论先行）

本司域在册 1 件 `HQ-Patrol-BigCompute`（Disabled）= **设计态确认**（已由 `HQ-Patrol-Group` 并轨：Group 版 Ready、2026-10-25 05:58 实跑 rc=0），建议入白名单 owner=@BigCompute、复核日 2026-11-08，**维持停用、不盲启**；本司自有六任务（OSLoop/OSLoop-PM/OrderSentinel/GPU-IdleWatch/CleanWindowProbe/ResidentQA）当前全 Ready/Running，**不在 27 件内**，无需白名单。

## 二、归属判定（按 action 实体实读，不按名字猜测）

| 任务 | 状态 | action 实体 | 判定 | 处置 |
|---|---|---|---|---|
| HQ-Patrol-BigCompute | Disabled | Tools/patrol-fleet-carrier.ps1 -Code bigcompute | 本司域·设计态确认 | 建议白名单（本件 proposal §entries） |
| HQ-LocalOnlyOn | Disabled | HQ 工具域·注册态从未运行 | 设计态确认（D-06 已定） | 非本司认领范围，如实转述 |
| BmaIdleGuard | Disabled | quant/bigmoney/Tools/bma_idleguard.pyw | **实为 quant 域** | 移交 @BigMoney（本司不认领不启用） |
| HQ-bm-a-Sentinel | Disabled | quant/bigmoney/Tools/sentinel_bm-a.py | **实为 quant 域** | 移交 @BigMoney（同上） |
| HQ-OpenClaw-ModelDay / ModelNight / Gateway | Disabled | FluxGroup/Tools/openclaw-*.ps1 | @HQ 工具域 | 本司无启用依据·维持停用 |

口径说明：D-06 ③ 的「Compute/Sentinel/IdleGuard → @BigCompute 与 @HQ 工具域」中，本司实际认领面只有 `HQ-Patrol-BigCompute`；Sentinel/IdleGuard 两件 action 实体落在 `quant/bigmoney/Tools/`，按实体归属归 quant，避免双头认领造成错启。

## 三、纪律声明

- 本轮**零启用、零停用、零任务改动**（纯只读），未触碰任何任务注册态与 XML。
- 白名单正典路径为集团 `Tools/task-expected-disabled.json`，当前**不存在**；本司只提交 proposal 片段（`state/d25-06-expected-disabled-proposal.json`）供工具域 owner 合并，不做跨仓写。
- 附：D-20261025-08（水位键四元组）本司 **N/A 如实登记**——代码面检索（*.py/*.ps1 全仓递归，排除 .codely-cli 存档）`watermark|水位` **命中 0 件**；散文提述仅见于台账/诊断件（queue main/explore、supply-floor 诊断件、round-score 证据件），均为转述集团 py_watermark 语义，**非读写代码点**——本司不存在需改格式的水位写点；如后续开创水位面，按四元组 {repo, commit_sha, blob_bytes, sha256} 建。
## 四、附录：白名单 proposal 片段（机器副本=state/d25-06-expected-disabled-proposal.json·本附录为 git 追踪正本）

```json
{
  "schema": "task-expected-disabled/v1-proposal",
  "proposed_by": "@BigCompute (BigCompute-OSLoop)",
  "decision": "D-20261025-06",
  "generated_at": "2026-10-25T06:12:57",
  "note": "PROPOSAL ONLY - this file lives in the BigCompute repo. The canonical whitelist is FluxGroup/Tools/task-expected-disabled.json and must be created/merged by the HQ tools owner (cross-repo write prohibited here).",
  "entries": [
    {
      "task_name": "HQ-Patrol-BigCompute",
      "owner": "@BigCompute",
      "reason": "superseded-by-HQ-Patrol-Group: HQ-Patrol-BigCompute disabled; HQ-Patrol-Group is Ready and ran rc=0 at 05:58 (fleet patrol folded into Group carrier)",
      "review_by": "2026-11-08"
    }
  ],
  "not_claimed": [
    {
      "task_name": "BmaIdleGuard",
      "actual_owner": "@BigMoney(quant)",
      "evidence": "action=quant/bigmoney/Tools/bma_idleguard.pyw"
    },
    {
      "task_name": "HQ-bm-a-Sentinel",
      "actual_owner": "@BigMoney(quant)",
      "evidence": "action=quant/bigmoney/Tools/sentinel_bm-a.py"
    },
    {
      "task_name": "HQ-OpenClaw-ModelDay",
      "actual_owner": "@HQ tools",
      "evidence": "action=FluxGroup/Tools/openclaw-model-switch.ps1 -Mode day"
    },
    {
      "task_name": "HQ-OpenClaw-ModelNight",
      "actual_owner": "@HQ tools",
      "evidence": "action=FluxGroup/Tools/openclaw-model-switch.ps1 -Mode night"
    },
    {
      "task_name": "HQ-OpenClawGateway",
      "actual_owner": "@HQ tools",
      "evidence": "action=FluxGroup/Tools/openclaw-gateway-runner.ps1"
    }
  ]
}
```
