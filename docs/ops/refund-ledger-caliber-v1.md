# 退款/售后账务口径表 v1（M151）

> 日期 2026-11-15 · 依据 `store-ledger-wiring-predesign-v1.md` §四/§五 · 计划态零真实交易
> 本件只定「行落在哪一轨、由谁判」，不重算退款金额——金额口径已由 §四 + 两个既有工具定死。

## 一、三轨落点（唯一）

| 轨 | 文件 | 键 | 幂等 | 谁写 |
|---|---|---|---|---|
| 收入轨 | `state/cost-ledger.jsonl` | `order_id` | `add` 单键（不改） | 成交流水 |
| 履约轨 | 管线 `ledger.jsonl`（替换点 4 落位） | `(order_id, cost_item)` | 双键 | 履约管线 |
| **退货款对账轨** | `state/refund-ledger.jsonl` | `refund_id`（售后单号） | 单键 | 退款事件 |

硬规则三条：

1. 退款**不写收入行、不逐笔冲销**——`add` 的 `refund-rate` 预提是估值，实退进对账轨，靠月末 join 对账，不回头改历史收入行。
2. 对账轨行**不得**出现在收入轨（带 `refund_id` 即越轨），收入轨行**不得**出现在对账轨（带 `gross/net/sku` 无 `refund_id` 即越轨）。
3. `cost_item` 履约行**不得**住收入轨——`add` 的 `order_id` 单键会把同单履约行判 duplicate 而漏记收入（§五 实证）。

## 二、金额口径（引用不重算）

- 对客退款=**原价基准 × 未消费比例**（§四·锚=消保法 53 条）。
- 成本口径（`balance_mtok × unit_cost_b`）**仅内部核算**，用于对客退款=系统性少退（§四 勘正·实测差 172 倍）。
- 有效期问：`refund_id` 独立于 `order_id`，一单可多笔售后，每笔一行；重复 `refund_id` 即重复退款。

## 三、工具分工（禁重复造轮）

| 工具 | 只回答一个问题 |
|---|---|
| `Tools/refund_rate_calibration.py` | 预提率 vs 实退率偏差是否 >2pp（超线只出提案，不改数） |
| `Tools/quota_prepaid_liability.py` | 成本口径对客退款少退多少（逐单点名） |
| `Tools/refund_track_check.py`（本件新增） | 行**放在哪一轨**：越轨/重复/无主/超退/成本口径/坏行 |

判据（`refund_track_check.py run`）：GREEN=零 findings；AMBER=仅成本口径类；RED=任一 high；`NO-DATA`=两轨零行（开店前如实态，禁印 0.00 当读数）。
一命令：`python Tools/refund_track_check.py run`（`selftest` 29/29 夹具，含 BOM 与「恰好退满」边界）。

## 四、月末校准（与 §四 同律，不新增）

实退率=当月售后单/当月订单 → 偏差 >2pp=下月预提率调整**提案**呈决策轮。本件只确保校准的**分母来自收入轨、分子来自对账轨**，两轨由第三节工具守门，避免用串账后的数字做校准。

## 五、验证声明

- 实读源：predesign §四/§五、`cost_ledger.py add` 参数面、两既有工具 docstring（引用不复制）。
- 工具实跑：`selftest 29/29 PASS`；真账本 `verdict=NO-DATA`（两文件均未创建=计划态）；负控（越轨+成本口径）`REFUND-TRACK-RED rc=1`。证据 `state/refund-track-20261115.txt`。
- 零真实交易、零定价动作、零改既有工具。
