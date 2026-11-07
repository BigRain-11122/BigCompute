# 回执「近名件」三要素签名定谳（BC-P-213 工程面·2026-11-07）

## 一、问题

`cpu_effective_hours --triage-missing` 把「文件名共享词干」当作 NAME-DRIFT（可改收据路径）。
2026-11-07 06:1x 轮实测：5 件 NAME-DRIFT 里 4 件是**别机/别次的件**被同名词干吸进来——
按旧判据会催出一次错误的「改收据路径」动作（把 bm-a 的回执指向 bm-b 的产物）。

旧逻辑只 zip 比较文件名 token 前缀，不看任何件内信息，也不看日期。

## 二、改法（判据）

候选必须带**内部签名**：`node`（件内 node/host）＋ `id`（件内 id）＋ `date`（文件名日期）。
与缺失件对照后出五态：

| confirm | 含义 | 可改路径 |
|---|---|---|
| `CONFIRMED` | 三要素无冲突且 ≥2 项相符 | ✅ |
| `PARTIAL` | 仅 1 项可比且相符 | ❌ |
| `NODE-CONFLICT` | 件内 node 与缺失件不同（他机件） | ❌ |
| `ID-DIFF` | 件内 id 不同（另一次工作） | ❌ |
| `DATE-DIFF` | 文件名日期不同（另一次运行） | ❌ |
| `NO-STATE` | 无可比要素（旧式纯名件） | ❌ |

`class` 仍保留 NAME-DRIFT（分类不变，向下兼容），新增 `candidates[]` 与 `actionable[]`；
实况面直接打印 `可改路径候选=N`，禁再按 `variants` 数量当动作数。

## 三、实跑结果（7d 窗·bm-a·8 件 MISSING）

分类不变：NAME-DRIFT 5 / NEVER-EXISTED 3；**可改路径候选 1**（旧判据会是 5）。

| 缺失件 | 候选判定 | 结论 |
|---|---|---|
| `FREE-MEM-APPLY-bm-c-20261107-receipt.txt` | `FREE-MEM-APPLY-…-bm-a.json` = **CONFIRMED** | 唯一真路径笔误（与 06:1x 轮 CORRECT-PATH 实证同件） |
| `model-radar-bm-a-20261103.json` | bm-a 11-04/05/06 = DATE-DIFF；bm-b/bm-c/bm-m = NODE-CONFLICT | 非笔误 |
| `model-radar-bm-c-20261103.json` | 同上（含 bm-m 件） | 非笔误 |
| `model-radar-bm-m-20261103.json` | 同上 | 非笔误 |
| `game-playable-2026-11-03.md` | `game-packs-2026-11-03*.json` = PARTIAL / DATE-DIFF | 非笔误 |

## 四、负控

selftest 22→**31/31 PASS**，新增三夹具：S29 他机 node 件必判 NODE-CONFLICT 且 actionable 空／
S30 同词干异日期必判 DATE-DIFF／S31 node+id+日期三项相符才得 CONFIRMED 并列入 actionable。
S25–S28 旧四态全绿不回归。退出码语义不变（selftest rc=0；live 有 MISSING-LOCAL = rc 2）。

## 五、派生缺口（入 tech T218）

`game-packs-*.json` 与 `model-radar-*.json` 件内**没有 node/id 字段**，只能靠文件名日期判；
签名面在写侧即缺一半。修法不在本器（读取侧已尽力），须回写 receipt 生产方：至少写 `node` 与 `id`。

## 六、证据

- 件：`Tools/cpu_effective_hours.py`
- 实跑：`state/cpu-eff-missing-triage-sig-20261107.json`
- selftest：`31/31 PASS`