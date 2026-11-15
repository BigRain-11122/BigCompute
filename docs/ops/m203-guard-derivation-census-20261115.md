# M203 自检守卫名单派生性普查（2026-11-15 12:1x 午班轮·产品优先律 2 分件）

**一句话**：建只读普查器 `Tools/guard_list_census.py`（selftest 15/15 PASS）并实跑全树——
398 个函数内字符串集合里 **17 个与同文件源集合重复**（13 双份 / 3 子集 / 1 超前），
按上下文逐条 triage 后**真收口范围=4 个文件**（1 个 P1 契约面 + 3 个 P2），
另 1 条属夹具输入、2 条属刻意场景或假阳，如实留痕不改。本轮**零判定改动**（M203 判据原文）。

## 一、为什么需要这个仪器

T241 实锤过一种失守形状：`frame_qc_spec.selftest()` 手抄了四常量名，代码里长到第五个，
测试仍是绿的——**新增成员当天无人守，闸还是绿的**。
T239 的 `threshold_census.py` 只扫模块级数值字面量，看不见名单型守卫
（`CONSUMER_FILES`、探针编号表、schema enum、扫描根名单）。本件补这个缺口。

## 二、方法（AST 只读，不执行被扫代码）

- **源集合**＝模块作用域里「≥2 个字符串常量的 tuple/list/set」赋值；
- **副本候选**＝任一函数作用域内同形状的字面集合（不下钻嵌套 def，按函数归位）；
- **派生证据**＝函数按名字读取同文件源集合（T241 要求的正形）；
- 判定（逐站点）：`DOUBLE-COPY`（成员集逐字相等）／`SUBSET-DRIFT`（严格子集＝源新增成员无人守）／
  `SUPERSET-DRIFT`（副本比源多，两侧已各自演化）／`STANDALONE`（同文件无源可比，只作观察）。

复跑：`python Tools\guard_list_census.py run`（json 落 `state/guard-list-census-*.json`，退出码 3＝有候选）。

## 三、实跑真数（2026-11-15 12:15）

```
scanned=109 parsed=109  source_files=58  func_literals=398
CANDIDATES double_copy=13 subset=3 superset=1 standalone=381 derived_files=47
VERDICT=GUARD-CENSUS-FOUND
```

- **已派生的正例 47 个文件**（含 T241 修完的 `frame_qc_spec.py`，selftest 里作真树锚点 S12）；
- **381 条 STANDALONE** 只入 json 不入缺口：同文件没有可比源集合，判不了；
- 候选 17 站点落在 **7 个文件**。

## 四、逐条 triage 与处置结论

| 文件 | 站点 | 源集合 | 判定 | 处置 |
|---|---|---|---|---|
| `lane_gate.py` | :395 `set(s) == {3 字面}` | `CONSUMED_FIELDS:58` | **真漂移（P1）** | 该行注释自陈「这份表就是与 idle_selfcheck 的契约」——消费侧新增第 4 个心跳字段时本条断言不会知道。改为 `set(s) == set(CONSUMED_FIELDS)`，判据不变、覆盖随源走 |
| `first_order_consent_drill.py` | :150/:155/:163/:169/:175/:192/:198 `synthetic_orders` + :288 `order`（8 站点） | `REQUIRED_TERMS:42` | **真漂移（P2）** | 演练单按字面写必备条款键；源里加第四项条款，演练单不会长，演练照样绿。改为从 `REQUIRED_TERMS` 派生 |
| `deal_flow_mock.py` | :278 `issuperset((15 名…))` | `WRITER_KEYS:54` | **真漂移（P2·轻）** | 现注释说明「逐键断言而非集合相等」是有意（M133），但名单仍是副本：`WRITER_KEYS` 加键后本条不覆盖。改 `set(WRITER_KEYS)` 保留原意 |
| `city3d_bake_spike.py` | :107 传参 `["AD-021","AD-035"]`、:126 断言同形 | `DEFAULT_PACKS:28` | **中（P2）** | 传参与断言都用字面表；模块默认改了测试仍测旧对。改 `list(DEFAULT_PACKS)` |
| `knowledge_sync_audit.py` | :130 `carriers = [...]` | `KNOWLEDGE_CARRIERS:23` | **夹具输入，非守卫副本** | 这是给 fixture 仓声明"要造哪几个台账"，属构造数据；不改，留观察 |
| `revenue_caliber.py` | :243 `["net","gross"]`、:603 同、:602 `["gmv","net"]` | `REVENUE_CALIBERS:47`／`CALIBERS:39` | **刻意场景取值** | S8 混算检测必须喂具体组合（gmv+net 该判 True，net+gross 该判 False），派生反而测不出边界。不改 |
| `dispatch_precheck.py` | :347 `for mode in ("cpu","gpu","exempt")` | `VALID_LANES:69` | **假阳** | 这是「判决模式」词表，不是 lane 词表；`exempt` 本就不是 lane。不改，注记以免后人误收口 |

**真收口范围＝4 个文件**（`lane_gate` P1 + 另三个 P2），已转 M206 行分窗执行；
本周不批量改测试——M203 判据原文即「先出真数再定收口范围」。

## 五、诚实边界

1. **成员集相等＝重复的证据，不是语义相同的证明**：两张表可以同名同字面却守不同的问题
   （`dispatch_precheck` 的 mode 词表就是反例），最终取舍留给人，这也是本件只报不改的原因。
2. **只比同文件**：跨模块的守卫（测试 A 抄了模块 B 的常量表）不在射程——没有 import 图的意图，
   强算会把全树每份独立词表都判成副本。
3. **`STANDALONE` 不等于没问题**：它只说明"本仪器判不了"，不是合格单。
4. 未接入 `qa_smoke`：先按 M203 出真数与收口范围，是否升格为每轮探针由下一轮决定（提案面在案）。
