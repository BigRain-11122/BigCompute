# 回执签名义务交付单（M155 · 2026-11-07 夜班轮）

**给谁：** 集团 dispatcher 与各机 produce 侧（本司只读集团仓，不代写对方文件）。
**要什么：** 每张回执顶层自带 `node` + `id` 两个字段——一行成本，消费侧立刻能归机、能对账。
**为什么：** T218–T221 四轮普查实读——804 张 7 日窗回执里 **148 张 node+id 双缺**（18.4%），
产物侧签名率 0/115；读侧 overlay 提级试过=0 提升，缺口在写侧。

## 一、写法（两字段，二选一）

```json
{"node": "bm-a", "id": "ART-PUSH-BMA-20261104", ...}
```

md/txt 回执写在前 15 行：

```
node: bm-a
id: ART-PUSH-BMA-20261104
```

`node` 只认机队节点名（bm-a/bm-b/bm-c/bm-m）；`id` = 回执文件名主干。
把任务名（DASHENG）、占位符（unknown）、主机名（junshengsundemacbook-pro.local）写进 node，
读侧会单列 ODD-NODE 不计入机器分母（T220 实测 7 例）——**这是静默丢账，不算签了**。

## 二、148 件分布（按文件名前缀·实读）

| 前缀 | 件数 | 建议归属域 | 文件名能否认出机器 |
|---|---|---|---|
| MV | 47 | BigStream（MV 产线） | 否 |
| LORA | 18 | BigStream（训练面） | 否 |
| VOICE | 14 | BigStream（配音/语料） | 部分（5/14） |
| ASSET | 11 | FluxVerse / City3D | 部分（1/11） |
| CHAR | 9 | BigStream（角色定版） | 部分（1/9） |
| FACE | 8 | BigStream（选角/面孔） | 否 |
| DRAMA | 7 | BigStream（漫剧） | 否 |
| QUALITY | 7 | HQ（QC 面） | 部分（1/7） |
| 其余 14 类 | 27 | 见 state/m155-sig-obligations-20261107.json | 否 |

**路由现状（本轮真发现）：148 件里 135 件（91.2%）在文件名上认不出生产机器**——
只有 13 件能靠 `-bm-x` 尾巴认领（bm-c 5 / bm-m 3 / bm-a 3 / bm-b 2）。
所以这张单**不能按机器下发**，只能按前缀交域；具体到机的认领要集团 dispatcher 侧定。

## 三、验证（任何一台机都能跑）

```
python Tools/receipt_signer.py census                  # 全局签名率与缺口
python Tools/receipt_signer.py resolve --receipt X      # 单张三态定位
python Tools/receipt_signer.py deliverables --days 7    # 产物侧签名
```

判据：补完签名后 `census` 的 BOTH-MISSING 应逐批下降，且不产生新的 ODD-NODE 行。

## 四、边界

- 本司只读集团仓，未改任何他机回执；本件=义务单，不是修复件。
- 前缀→域是**建议**口径，不代替集团 dispatcher 的归属裁定。
- 数据源：`state/receipt-sig-census-20261107.json`（T218）+ `state/m155-sig-obligations-20261107.json`（本轮分流）。