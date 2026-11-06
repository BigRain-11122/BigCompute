# 收据「件不在盘」逐条定性（BC-P-211 / 2026-11-07 03:2x 夜班）

## 一句话

smoke no.22 连轮红，红的原因一直是 7d 窗口内 8 张收据声称的交付件不在盘；红只说明「不在」，不说明「该怎么办」。本轮把 git 全分支历史与同目录近名件作为判据，把 8 件分到三类处置，红从「一团红」变成 8 条可派单。

## 做法（工具，可复跑）

`python Tools/cpu_effective_hours.py --days 7 --triage-missing --json-out state/cpu-eff-missing-triage-20261107.json`

- 判据一：`git -C <集团仓> log --all --oneline -1 -- <deliverable>` 非空 = 该件**曾有提交**（可找回）。
- 判据二：同目录存在共享词干前缀（按共享 token 数降序）的近名件 = **名字漂移**候选。
- 两类都不成立 = **从未存在**（收据声称的产物在任一分支上都没落过库）。
- selftest 22→**28/28 PASS**（S25–S28 四条新夹具：无近名件→NEVER-EXISTED / 有近名件→NAME-DRIFT 并列出 / 有历史→IN-HISTORY-REMOVED / 非 MISSING-LOCAL 不入表）。

## 实跑结论（窗口 7d·root=集团仓·host=bm-a·8 件）

| 分类 | 件数 | 含义与处置 |
|---|---|---|
| IN-HISTORY-REMOVED | 0 | 无「删了但可找回」件——8 件的路径在任何分支上都没有提交记录 |
| NAME-DRIFT | 5 | 收据写的路径与真实产物名对不上；派单=让出件机更正收据路径（非补产） |
| NEVER-EXISTED | 3 | 声称的产物从未入过库；派单=出件机补产或作废收据，二选一 |

逐条：

- NAME-DRIFT `FREE-MEM-APPLY-bm-c-20261107`：收据写 `...-receipt.txt`，同目录实有 `FREE-MEM-APPLY-bm-c-20261107-bm-a.json`。
- NAME-DRIFT `R-MODEL-RADAR-BMA/BMC/BMM-20261103`：三张收据写 `model-radar-bm-{a,c,m}-20261103.json`，同目录实有 11-04 起的同名件（11-03 那批未落）；bm-c/bm-m 的近名件已按机名对齐。
- NAME-DRIFT `biggame-playable-2026-11-03`：收据写 `game-playable-2026-11-03.md`，同目录实有 `biggame-playable-2026-11-03-{bm-a,bm-b,bm-m}.json` 与 `game-packs-2026-11-03.json`。
- NEVER-EXISTED `ART-PUSH-BMA-20261104`（`artifacts/bm-a/PUSHED.jsonl`）、`R-ART-PUSH-B-20261102`（`artifacts/bm-b/MANIFEST.jsonl`）、`MV-ANCHOR-BATCH3-20261106`（`anchors/POOL-0018-0019-anchor-matrix.json`）：路径所在目录存在但目标件从未入过库。

## 边界（如实）

- 本司只读集团仓，**不代改收据、不代补产物**（禁跨仓写）；上表仅作派单依据。
- 「从未存在」不等于出件机没干活：它只说明收据里的路径指向的件在两个判据下都无痕迹——可能产出物用了别的名字（已被 NAME-DRIFT 捕获），也可能只在本机未上链。
- 工时不因本报告变化：`missing_local` 仍不计入 net，8 件继续为 0 工时。