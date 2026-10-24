# 供给地板 breach 主面诊断（M86·2026-10-24 夜班轮）

> 令源=D-20261024-14 ③（供给地板按 ready_ignitable 判 breach）+ M83 实证 `ignitable 2<3`。
> 本件结论全部来自机械实测，零推断值：`Tools/idle_selfcheck.py ignitable_face()`
> 对 `quant/BigMoney/fleet/backlog.md`（跨仓只读，本司零写）实算。

## 一、结论先行

**breach=True 成立，且比账面更严重：真实新供给 = 0，不是 2。**

| 口径 | 值 | 说明 |
|---|---|---|
| ready_total | 3 | 池台账「可领」且未 done@ 行 |
| ready_ignitable（集团同源） | 2 | 无主 or 本机 claim |
| ready_fresh（无主） | **0** | 无任何一行可被新领 |
| ready_self_active（本机近 3 日 claim） | 0 | — |
| ready_self_stale（本机陈旧自占用） | **2** | backlog#8、backlog#11 |
| effective_ignitable（fresh+self_active） | **0** | 真实可点火新供给 |
| ready_foreign_silent | 1 | backlog#10（bm-b，静默 2785min≈46.4h） |
| floor | 3 | 集团同值 |

即：`breach` 与 `breach_effective` **双 True**。旧口径把「本机 17 天前 claim 后零交付」
算成可点火，虚高 2 件；扣掉后供给地板实际为 0。

## 二、三条在册行逐条核验

| # | 行 | claim 时点 | claim 龄 | 归属与可执行性 |
|---|---|---|---|---|
| backlog#8 | 【FluxVerse】城内 DECISION 光脉冲族接线（P-34 转写） | claimed@bm-a@2026-10-07T22:33 | 17.0 天 | 交付物=**FluxVerse 仓代码接线** → 本司禁跨仓写（身份红线 1），本 lane 永远无法交付 |
| backlog#11 | 【BigDomain】19.9 旅程 v2 双案用户文案件（M2 预备） | claimed@bm-a@2026-10-07T22:33 | 17.0 天 | 交付物=**BigDomain 内容文案** → 同上禁跨仓写，本 lane 无法交付 |
| backlog#10 | 【BigMoney】T-94 千人题库烧批续片 | claimed@bm-b@2026-10-09T01:00（末次） | 静默 46.4h | 他机 claim，静默超阈 → 仅 takeover 候选；D-14 ② 要求独立活性佐证方可接管，本轮不接管 |

**根因（结构性，非测量误差）**：池里没有一件 BigCompute lane 自有交付物。
本司在 10-07 派单窗（O-20261007-1850）把两条**他司交付物**写进了本机 claim，
这既违反跨仓写红线，也占住了池位——账面看着有货，实际两件都动不了。
`ready_ignitable` 只看 claim owner，看不到「交付物归属司 ≠ claim 司」，故这一
类错配在旧口径下恒显示为「可点火」。

## 三、次生发现

1. **池水位低于维护线**：`ready_total=3`，而池律规定「池 <10 件=秘书处补货责任」
   （backlog.md 头部律）。当前不仅少，且少的三件里两件是死账。
2. **claim 无 TTL**：本机 10-07 claim 至今 17 天无人回收。池律只有 claim 与 done 两种
   终态，缺「超期自动回池」机制，死 claim 会永久占用供给计数。
3. **takeover 候选手动依赖**：backlog#10 静默 46h 仍挂 bm-b，按 D-14 ② 需独立佐证，
   本轮无佐证即不接管（fail-open 正确行为，但需有人盯）。

## 四、本轮已落动作

- `Tools/idle_selfcheck.py` 新增 J13b 诊断列（**不改 ready_ignitable/breach 的集团同源语义**，
  对齐律优先）：`ready_fresh / ready_self_active / ready_self_stale / self_stale_ids /
  self_age_unknown / effective_ignitable / breach_effective / self_stale_days`。
  阈值 `SELF_STALE_DAYS=3.0` 预注册（本机 claim 超 3 日未 done=陈旧自占用）。
- 心跳行尾新增 `pool_fresh= pool_self_stale= breach_eff=`（CEO/值守可见面）。
- selftest 扩断言：J13b 七项（fresh/stale 分列、stale 行号、effective 数学、
  ISO 与无年份时戳解析、垃圾时戳 None），全轮 `SELFTEST PASS`。

## 五、待裁决（本司零自决）

1. **回池/改派 backlog#8、#11**：写池台账属 BigMoney 仓（跨仓），本司只能申报。
   建议秘书处把两条改派至 FluxVerse/BigDomain 席，或回收后由他司重领。
2. **claim TTL 立法**：建议池律增「claim 超 N 日未 done 自动回池」（N 建议 7），
   否则死 claim 会持续污染供给地板读数。
3. **BigCompute lane 补货**：本司自有的可执行备货（成本账本/口径 B/审计探针族）
   应成卡入池；当前本 lane 在池中零卡，等于本机可点火面永远依赖他司交付物。

## 六、证据

- 实测 JSON：`state/supply-floor-20261024.json`（本轮落盘）
- 复算命令：`python -c "import sys;sys.path.insert(0,'Tools');import idle_selfcheck as m;print(m.ignitable_face())"`
- 自测：`python Tools/idle_selfcheck.py selftest` → `SELFTEST PASS`