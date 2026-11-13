# 三线营收口径统一表 v1（由 Tools/revenue_caliber.py 生成，勿手改）

## 一、四个口径的定义（互斥，禁相加）

| 口径 | 算法 | 能不能叫「收入」 |
|---|---|---|
| GMV | Σ price（下单金额，未扣费未扣退款） | **不能** |
| NET | Σ net_price（扣平台费 + 退款预提） | 能，须与 GROSS 分列 |
| COGS | Σ amount（口径 B 内部成本锚） | 不能（成本） |
| GROSS | Σ gross（= NET − COGS） | 能，须与 NET 分列 |

## 二、渠道 × 口径矩阵（真实行，演练行不计入；`private/b2b` = 直签从私域拆出的派生行，不并入 private 合计）

| 渠道 | GMV 下单金额 | NET 净额 | COGS 成本 | GROSS 毛利 |
|---|---|---|---|---|
| （无营收行） | — | — | — | — |

## 二之二、演练面渠道分布（drill，**不入结算、不并入上表**）

| 渠道 | GMV 下单金额 | NET 净额 | COGS 成本 | GROSS 毛利 |
|---|---|---|---|---|
| store | 52.9000 | 46.2346 | 0.2527 | 45.9819 |
| private/b2b | 349.0000 | 305.0260 | 0.1080 | 304.9180 |

## 三、本轮读数（账本级，非估算）

- verdict=`REV-NO-REAL-DATA`
- 扫到的行：12（其中演练行 10、真实营收行 **0**）
- 非营收轨带金额字段的行：0（单独计数，不并入营收）
- 拆行读数：`private/b2b` 由 `channel_note` 拆出——实面 无行；演练面 1 行 / GMV 349.0000。**该行金额不进 private 合计**（行内仍保留 `channel=private` 原值可追）
- **真实成交轨 0 行 → 营收面 NO-DATA**：此处不印 0.00，因为「没有成交」与「成交额为 0」是两件事。

## 四、扫描域自证（哪些文件读过、哪些不在盘）

| rail | 文件 | 期望形态 | 状态 | 行数 |
|---|---|---|---|---|
| deal | `state/cost-ledger.jsonl` | real | ABSENT | — |
| deal | `state/mock-order-ledger.jsonl` | drill | SCANNED | 3 |
| quota | `state/quota-ledger.jsonl` | real | SCANNED | 2 |
| distr | `state/distr-ledger.jsonl` | real | SCANNED | 1 |
| delivery | `state/delivery-ledger.jsonl` | drill | SCANNED | 3 |
| aftersale | `state/mock-aftersale-ledger.jsonl` | drill | SCANNED | 3 |

## 五、findings（每条都点名对象）

无（FINDINGS none）

> 口径律：GMV 永不称收入；NET/GROSS 各自单列，禁相加成「总收入」；distr 归因行与 delivery 履约事件不是营收行。本表只读账本，不含任何估算或外推。
