# 收据更正/补产映射表（BC-P-211 步 2 / 2026-11-07 06:2x 令牌轮）

## 一句话

上一轮（`docs/ops/receipt-missing-triage-20261107.md`）按「同目录近名件」给出 NAME-DRIFT 5 件、处置=「让出件机更正收据路径（非补产）」。本轮把这些近名件**核到字节级**，结论**翻转**：5 件里只有 **1 件**能确证是同一次工作的产物，**4 件只是文件名字面相近**，内容属于别的日期或别的机——那 4 件应算**真缺件**（补产或作废收据），不能当笔误糊过去。

## 判定判据（三要素，缺一不可）

| # | 判据 | 为什么必须有 |
|---|---|---|
| 1 | 文件名日期与收据日期相交 | 基础筛，但**单独用会出假阳** |
| 2 | 件内声明 `node` 必须等于收据 `node` | 只对日期时，`model-radar-bm-b-20261103.json`（bm-b 的 11-03 件）会被认成 bm-a/bm-c 的同一次工作 |
| 3 | 件内 `id`（若有）与收据 id 一致 | 最强证据，但多数件不带 id，故退化为 1+2 |

本脚本首版只用了判据 1，结果 4 条假阳（CORRECT-PATH=4）；加判据 2 后收敛为 CORRECT-PATH=1 / REPRODUCE-OR-VOID=7。**首版就是错的那一版，如实留痕。**

## 逐条处置（8 件）

| 收据 id | 收据声称的路径 | 处置 | 依据 |
|---|---|---|---|
| `FREE-MEM-APPLY-bm-c-20261107` | `queue-receipts/FREE-MEM-APPLY-bm-c-20261107-receipt.txt` | **CORRECT-PATH** | 实有件 `queue-receipts/FREE-MEM-APPLY-bm-c-20261107-bm-a.json`，773B，sha256 `23271800…a635`，mtime 2026-11-07 00:20:14，件内 `id` 与收据 id **逐字相同**、`node`=bm-a 与收据 node 一致 |
| `R-MODEL-RADAR-BMA-20261103` | `queue-receipts/model-radar-bm-a-20261103.json` | REPRODUCE-OR-VOID | 盘上仅 11-04/05/06 三件（均为 bm-a，非本日）；唯一的 11-03 件内部 `node`=**bm-b** |
| `R-MODEL-RADAR-BMC-20261103` | `queue-receipts/model-radar-bm-c-20261103.json` | REPRODUCE-OR-VOID | bm-c 只有 11-04 起；11-03 那件是 bm-b 的 |
| `R-MODEL-RADAR-BMM-20261103` | `queue-receipts/model-radar-bm-m-20261103.json` | REPRODUCE-OR-VOID | bm-m 只有 11-04/05，无 11-03 |
| `biggame-playable-2026-11-03` | `queue-receipts/game-playable-2026-11-03.md` | REPRODUCE-OR-VOID | 近名件 `game-packs-2026-11-03.json`（186B，`root:gaming`）日期对上但**是另一份工作**（game-packs ≠ game-playable，且 .md 与 .json 两种产物形态）；收据 id 带机名变体，盘上只有 `biggame-playable-2026-11-03-{bm-a,bm-b,bm-m}.json` 相关名 |
| `ART-PUSH-BMA-20261104` | `artifacts/bm-a/PUSHED.jsonl` | REPRODUCE-OR-VOID | 无近名件、全分支无历史 |
| `R-ART-PUSH-B-20261102` | `artifacts/bm-b/MANIFEST.jsonl` | REPRODUCE-OR-VOID | 同上 |
| `MV-ANCHOR-BATCH3-20261106` | `mv-album-001-jay/anchors/POOL-0018-0019-anchor-matrix.json` | REPRODUCE-OR-VOID | 同上 |

**净处置：1 件改路径即可销账，7 件须由出件机补产或作废收据。**

## 一条必须点名的矛盾

`FREE-MEM-APPLY-**bm-c**-20261107` 是 bm-a 出的件（收据 node=bm-a、产物 node=bm-a）。要么 id 里的 `bm-c` 是「操作对象」（对 bm-c 做内存清理、由 bm-a 代跑），要么 id 写错机名。**本司无法从盘上判定是哪一种**——@HQ 分诊时请一并确认，别把它直接记成「bm-c 干了这个活」。

## 边界（如实）

- 本司只读集团仓，**未改任何收据、未补任何产物**（禁跨仓写）；本表仅作派单依据。
- `REPRODUCE-OR-VOID` 不等于「出件机没干活」：它只说明收据里那个路径指向的件，在「日期+node+id」三判据下找不到同一次工作的证据；产出物可能只在本机未上链。
- 工时口径不变：8 件仍不计入 net（`missing_local` 桶），改路径后是否回收工时由 @HQ 按实际产物决定。
- 一次性核验脚本置于 `.codely-cli/tmp/`，**不入库**；机读结果 `state/receipt-path-correction-20261107.json` 入库。

## 可复跑

```
python Tools/cpu_effective_hours.py --days 7 --triage-missing ^
  --json-out state/cpu-eff-missing-triage-20261107.json
# 近名件字节级核验（一次性）：.codely-cli/tmp/missing-map-20261107.py
```