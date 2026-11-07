# 缺失件分诊接线回执签名 overlay（T219 收口件·2026-11-07）

## 一、做了什么

`Tools/cpu_effective_hours.py` 把回执签名 overlay（`Tools/receipt_signer.py` 的产物
`state/receipt-sig-overlay-*.json`）接进 `--triage-missing` 的三要素比对：

| 位置 | 变化 |
|---|---|
| `load_sig_overlay(root, path)` | 新增。按 `receipt` 名索引 overlay；只取 `confidence=STRONG`（逐字段再核 `node_conf`/`id_conf`）；读不到=空表并如实报 `missing`，不抛栈 |
| `_expected_for(item, overlay)` | 新增。期望签名=收据原文优先，**只补缺的那一半**；逐字段标源 `receipt`/`overlay` |
| `triage_missing(payload, root, hist_fn, overlay)` | 每候选出双读数 `confirm`（含 overlay）/`confirm_base`（仅收据原文）；item 增 `sig_src`/`expected`/`upgraded` |
| `report_triage(items, overlay_meta)` | 头行出 `overlay=<文件名> entries= strong= weak=`，汇总行出「靠 overlay 提级=N」，候选行出 `base=` 与 `↑overlay` |
| CLI | 增 `--sig-overlay PATH`；缺省自动发现（见下） |

## 二、同轮抓到的真缺陷：搜索面

首版只在 `--root` 的 `state/` 下找 overlay。默认 `--root` 是**集团根**
（`...\FluxGroup`），而 overlay 由本司产出、落在 `BigCompute\state\`，于是实跑
第一行就是：

```
MISSING-TRIAGE overlay=none (no receipt-sig-overlay-*.json under ...\FluxGroup\state)
```

overlay 明明有 656 条却在报 none——接线做成了假动作，与 D-20「读数没错、结论越界」
同族。改法=`--root` 的 `state/` 优先、本司仓 `state/` 兜底，先命中者为准（S38 钉死
搜索序），修后实跑读到 656 条。

## 三、实跑读数与负结果（诚实面）

```
verdict=ATTENTION counted=92 net=1.166h (4196s)
MISSING-TRIAGE overlay=receipt-sig-overlay-20261107.json entries=656 strong=656 weak=0
MISSING-TRIAGE 8 件 分类=NAME-DRIFT:5 NEVER-EXISTED:3 可改路径候选=1 靠 overlay 提级=0
```

**提级=0**。原因不是接线没跑，而是这 8 件缺失件的收据本身**已经带 node+id**
（`sig[node=receipt,id=receipt]`）——overlay 没有可补的空位。真正卡在 `PARTIAL`
的两条候选（`game-packs-2026-11-03*.json`）是**产物件自身没有内部签名**，而 overlay
的键是**回执名**，覆盖不到产物侧。

结论：T219 的假设（「overlay 已在册 656 条 → PARTIAL 有机会升 CONFIRMED」）在现网
数据上**不成立**，缺口在产物（deliverable）侧而非回执侧 → 派生提案 BC-P-215。

## 四、判据与证据

- selftest **38/38 PASS**（新增 S32–S38）：S32 收据缺 node/id + STRONG → CONFIRMED
  且同时记录 `confirm_base=PARTIAL`；S33 WEAK 不入判、维持 PARTIAL；S34 无 overlay 时
  逐字维持现状（禁凭空提级）；S35 逐字段标源；S36 取最新一件并分计 STRONG/WEAK；
  S37 显式坏路径 → 空表不抛栈；S38 root/state 优先命中。
- 实跑证据：`state/cpu-eff-triage-overlay-20261107.txt/.json`（含 overlay）；
  `state/cpu-eff-triage-base-20261107.txt/.json`（无 overlay 对照，分类与可改路径候选
  完全一致=「overlay 不改判、只补空位」的实证）。
- 边界：本司只读集团仓回执，不代改对方文件；未新增工具文件（工具清单 F3 不变）。