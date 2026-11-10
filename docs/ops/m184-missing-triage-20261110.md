# M184 回执产物缺失逐条 triage（7 日窗·bm-a 本机）

- 生成：2026-11-10 22:5x ｜ 窗口=7 天 ｜ 源=`Tools/cpu_effective_hours.py --days 7 --triage-missing`
- 机读：`state/m184-missing-triage-20261110.json` ｜ 人读：`state/m184-missing-triage-20261110.txt` ｜ 原始载荷：`state/m184-triage-20261110.json`

## 一、结论先行

7 日窗内 `counted=95`、`missing_local=20`（占计入行 **21.1%**）。20 条逐条定位后：

| 态 | 条数 | 含义 | 归谁 |
|---|---|---|---|
| OFF-MAINLINE | **11** | 产物存在于 `backup/bm-a-20261110` 支线，主线上没有 | @HQ/bm-a 工具线 |
| NEVER-ANYWHERE | **6** | 任何 ref 都无此路径：声明的产物从未落盘 | 生产方（bm-a） |
| NAME-SUSPECT | **2** | 同日期不同词干件在盘，但内部签名不足判断是否同一件 | 生产方 + 人工复核 |
| PATH-CORRECTED | **1** | CONFIRMED 近名件在盘，改路径即可销账 | 本司 |

**7 日窗内不可核销占比 = 19/20 = 95.0%（占计入工时行 20.0%）**——即 20 条红里只有 1 条是路径笔误，其余 19 条不是「名字打错」可以销账的。

## 二、主因：一条备份支线，不是二十次笔误

11 条 OFF-MAINLINE 的 `present_in_hist=True` 且 `merge-base --is-ancestor <hist> HEAD` 全为**假**，`for-each-ref --contains` 全部落到同一支线 `backup/bm-a-20261110`：

- 支线 tip `1534147a0`（2026-11-10 21:29:29，提交语 `state(bm-c): node state snapshot before rebase`）
- `main..backup/bm-a-20261110` = **268 提交**；反向 1828；两线差异 **261 个文件**
- 涉及件族：`triage-{bigcompute,biggame,biglife,bigstream-mv,fluxverse}*.md`、`review-packet-kn-20261109.md`

读法：这些回执声明的工作**确实被提交过**，但提交落在 rebase 前的快照支线上，主线从未收到。因此「本机缺件」的准确含义是**主线缺件**，不是文件消失。

**本司不外推、不自决 cherry-pick**：把 268 个分叉提交里的哪一部分复放到主线，属集团主线 owner（@HQ / bm-a 工具线）的决定；本司只出这一行对账，经文档总线知会，不写集团仓。

## 三、六条「从未落盘」逐条点名

| 回执 | 声明的产物 | 盘上实况 |
|---|---|---|
| ART-PUSH-BMA-20261104 | `cph4/fleet/artifacts/bm-a/PUSHED.jsonl` | 该目录下只有 `MANIFEST.jsonl`（12,712B）；`PUSHED.jsonl` 在任何 ref 都不存在 |
| R-ART-PUSH-B-20261102 | `cph4/fleet/artifacts/bm-b/MANIFEST.jsonl` | 本机无 `artifacts/bm-b/`；且产物属 bm-b，执行机是 bm-a——**归属与执行机不同轴**，本机无法自证 |
| MV-ANCHOR-BATCH3-20261106 | `mv-album-001-jay/anchors/POOL-0018-0019-anchor-matrix.json` | 无（与 11-07 同名结论一致） |
| R-MODEL-RADAR-BMA-20261103 | `queue-receipts/model-radar-bm-a-20261103.json` | 该序列盘上从 **11-04** 起（11-03 无件） |
| R-MODEL-RADAR-BMC-20261103 | `queue-receipts/model-radar-bm-c-20261103.json` | 同上，且序列件内 node=bm-c 而回执执行机=bm-a |
| R-MODEL-RADAR-BMM-20261103 | `queue-receipts/model-radar-bm-m-20261103.json` | 同上，序列件内 node=bm-m 而回执执行机=bm-a |

两条可复用的教训：①**「声明了产物」不等于「产物落过盘」**——6 条从未存在，说明回执是在产物未生成时先写下的；②**model-radar 三连是同一形态**：执行机 bm-a 代跑三机的雷达，产物按被代跑机器命名，回执按执行机记账 → 跨机产物归属与执行机不同轴，任何「本机缺件」的判定对这类回执都不成立。

## 四、两条 NAME-SUSPECT（未定谳，需人工）

- `game-playable-2026-11-03.md` vs 在盘 `game-packs-2026-11-03[-r2].json` / `game-pack-scan-2026-11-03.json`：同日期、词干不同、候选 `PARTIAL`。
- `quant-quote-2026-11-07.md` vs 在盘 `quant-conclusion-2026-11-07.{md,json}`：同日、可能为改名，候选 `PARTIAL`。

本司不按「看起来像」改判——`PARTIAL` 不进可核销。

## 五、唯一可核销件

`FREE-MEM-APPLY-bm-c-20261107-receipt.txt` → 盘上 `FREE-MEM-APPLY-bm-c-20261107-bm-a.json`（`CONFIRMED`：文件名日期、件内 node、件内 id 三要素全对）。改路径即销账，不新造文件。

## 六、诚实边界

1. 分母只含 `MISSING-LOCAL` 20 条；跨机 `REMOTE-UNVERIF` **666 条**按跨机律不自决、不入分子分母。
2. 分类依据=`git cat-file -e`（HEAD / hist commit）+ `merge-base --is-ancestor` + `for-each-ref --contains`，可逐条复跑；`hist` 字段取自工时器输出，本司未重算其取法。
3. 「不可核销」判定只针对**工时核销**（工时器口径），不等于这些工作没做过——11 条恰恰证明做过、只是没进主线。
4. 工具的口径缺陷（见下）本轮只登记不修，避免与 M184 混成一件事。

## 七、派生

- **M186**：`cpu_effective_hours` 的 `IN-HISTORY-REMOVED` 未区分「hist 可达 HEAD」与「只在旁支」——本轮实测 11/20 全落在旁支，却被读成「主线曾有后被移出」。应拆 `OFF-MAINLINE`（带 ref 名）与 `IN-HEAD-HISTORY-REMOVED` 两态。
- **M187**：跨机产物归属轴——回执执行机与产物所有者不是同一台时（model-radar 三连、`R-ART-PUSH-B`），「本机缺件」这一判定本身不成立，应改判 `OWNER-ELSEWHERE`。
- **BC-P-232**：建议 @HQ 把 `backup/*` 支线 268 个分叉提交的收纳/丢弃做成一次显式决策，禁长期悬挂。
