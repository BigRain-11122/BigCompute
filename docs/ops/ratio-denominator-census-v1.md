# 比率探针分母自指普查 v1（T159 · 2026-10-28 夜班）

> 出身：10-28 22:4x 夜班轮实锚——`l1_kpi_probe` 的 KPI-B 分母被本文件自己的
> `DEFAULT_PORT = 8090` 常量污染（5 行 → 6 行，仅因一段注释加了 fallback 字样）。
> 「一个会对自己源码文本做出反应的指标不是指标」。本轮把同型面一次普查完。
>
> 工具：`Tools/ratio_denominator_census.py`（只读静态普查 · selftest 15/15 PASS）
> 证据：`state/denom-census-20261028-2315.json`

## 一、判据三态

| 态 | 含义 |
|---|---|
| SELF-EXCLUDED | 分母由扫描文本/文件构成，且有显式守卫把仪器自身（或自身产物）剔出 |
| STRUCTURAL-NA | 分母是代码内固定注册表或物理测量量，仪器无法把自己枚举进去 |
| NEEDS-FIX | 分母由扫描构成且无守卫（缺陷） |
| UNVERIFIED | 声明的分母/守卫字面量已不在文件里（注册表漂移，须重读代码，禁默认通过） |

## 二、普查结果（VERDICT=DENOM-CLEAN · NEEDS-FIX=0）

| # | 探针 | 分母来源 | 类别 | 判定 | 依据 |
|---|---|---|---|---|---|
| 1 | `l1_kpi_probe` KPI-B | `census_rows(port)` | SCANNED | **SELF-EXCLUDED** | 守卫 `os.path.abspath(r.get("file","")) != me`（本工具行被剔） |
| 2 | `qa_smoke` n/total | `results = []` | FIXED | STRUCTURAL-NA | 检查项由代码按序 append 注册，非目录扫描 |
| 3 | `c1c4_patrol` passed/total | `total = len(results)` | FIXED | STRUCTURAL-NA | 四条子命令在文件内写死 |
| 4 | `queue_check` premise/open | `total_open += counts["open"]` | FIXED | STRUCTURAL-NA | 行来自三份队列 markdown，本工具不是队列文件 |
| 5 | `idle_selfcheck` ram_free_pct | `s.ullTotalPhys` | MEASURED | STRUCTURAL-NA | 物理内存比率，无可枚举对象 |
| 6 | `idle_selfcheck` rounds_ago/FRESH | `ROUNDS_LOG=state/rounds.log` | SCANNED | **SELF-EXCLUDED** | 路径分离：本工具只写 `heartbeat.txt`，扫的却是 `rounds.log` |

## 三、sweep 四候选（信息面·非判据）

工具顺带列出「既走树扫描又拿 len() 做分母」的文件四件，本轮逐件人工判读：

| 文件 | 扫描面 | 判读 |
|---|---|---|
| `model_impact_probe.py` | `os.walk` 外部消费仓（CONSUMER_REPOS） | 不扫本仓，自身文件不入分母；`code_hits` 是分子侧计数，不属本普查面 |
| `serve_restore_manifest.py` | `glob` `state/serve-contract-audit-*.json` | 本工具产出的是 manifest 而非 audit 件，不入分母 |
| `status_export_check.py` | `os.walk` 取队列行 | 比率项 `194` 行为可读字符比（纯文本比），与 walk 不同源 |
| `storm_leftover_check.py` | `os.walk` 取 marker 文件名 | 工具不产出 marker 文件，不入分母 |

结论：四件均非分母自指缺陷；其中 `model_impact_probe` 的分子侧扫描面另立待办（新补，见 tech 队列）。

## 四、可复跑实证

```
python Tools/ratio_denominator_census.py selftest     # 15/15 PASS
python Tools/ratio_denominator_census.py run --json state/denom-census-<ts>.json
# -> VERDICT=DENOM-CLEAN SELF-EXCLUDED=2 STRUCTURAL-NA=4
```

自测含反向用例：扫描分母无守卫 → NEEDS-FIX（S1）；路径重叠 → NEEDS-FIX（S5b）；
字面量漂移 → UNVERIFIED 而非静默通过（S4）；聚合在存在 NEEDS-FIX 时翻转（S8）。

## 五、诚实声明

- 本工具做的是**静态字面核验**：守卫字面量在 = 代码形状未漂移，**不等于**守卫语义充分。
  语义充分性只能靠实跑行为证据（如本轮 KPI-B 行数从 6 回 5 的实锚）。
- 注册表六行是人工登记的，不是自动发现；sweep 清单只是候选面，不构成判定。
- 未发现 NEEDS-FIX ≠ 无缺陷：仅代表注册表覆盖到的六个探针面干净。