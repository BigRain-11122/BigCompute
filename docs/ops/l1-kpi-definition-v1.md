# L1 车道继任 KPI 定义 v1（D-20261028-18 ③·@BigCompute 出定义）

> 出件=BigCompute·2026-10-28 19:0x·依据=D-20261028-18（T2 自决·dispatched @BigCompute 定义·@HQ 探针接线）
> 测量件=`Tools/l1_kpi_probe.py`（selftest 10/10 PASS·只读·不启停任何服务）
> 前身退役：「L1 消费率 ≥20%」在车道 DARK 期分母为 0（D-18 ④）= 科学上不可测，随 10-29 12:00 终审与车道同步退役或复活。

## 一、KPI-A：L1_ENDPOINT 可达性（三态读数）

测量命令：`python Tools/l1_kpi_probe.py reach [--endpoint 8090]`（报告面：`report`）

| 态 | 判据（机器可判） | 语义 |
|---|---|---|
| **UP** | TCP accept **且** `GET /v1/models` 返回 200 **且** 响应体含 ≥1 个模型 id（`data[]`/`models[]` 中带 `id`/`name` 的项） | 车道在线且真能回答 |
| **DOWN** | 端点属「应有」（显式配置/env 值/用户指名）**或**有监听，但模型 API 不回答（传输失败/非 200/模型列表为空） | 部署在册但坏了 |
| **ABSENT** | 无任何显式端点配置（`L1_ENDPOINT` 未设），且代码默认口（127.0.0.1:8090）无监听 | 本机未部署该车道——不是故障读数 |

口径纪律：
- 默认口无监听但**有人显式指名**（env 或参数）= **DOWN**，不是 ABSENT——指名即「应有」，缺位要报。
- 200 但模型列表为空 = **DOWN**。空壳服务比无服务更容易骗过看门人（T148 教训：health-only 绿挡死的窗口）。
- 端点解析：`8090` / `:8090` / `host:port` / `http(s)://host:port/path` 四式；env `L1_ENDPOINT` 优先。

## 二、KPI-B：回退成功率（FALLBACK-OK 作用域）

测量命令：`python Tools/l1_kpi_probe.py fallback`

- **分母** = 普查（`Tools/l1_ref_census.py`）中 `status=FALLBACK-OK` 且 `domain=OWN` 的引用行。`FIXTURE`/`DIAGNOSTIC`/`MESSAGE-REF`/`DOC-REF` 由普查上游已排除，不重复减。
- **分子** = 其中「作用域内至少一个候选端点实测 UP」的行。
- **候选端点** = ①env 覆盖值若是该行 `ENV:*` 引导来源且已设→用它；②否则代码默认口；③同一**外层函数**内出现的**端口形态**字面量（`:8090`／`port=11434`／`"8090"` 三式；裸 4 位数字不算端口=年份/计数噪声已排除）。
- **判定**：全活=GREEN／零活=RED／混合=PARTIAL／分母 0=`N/A-NO-SCOPE`（**禁写 0%**）。
- 候选全 ABSENT 的行**留在分母**且记不活：D-18 ②「DARK 期禁计入消费率」只免掉消费率，不免掉「没有活的回退目标」这个真发现。

## 三、本机实况读数（2026-10-28 19:07·证据 `state/l1-kpi-probe-20261028-190754.json`）

| 项 | 读数 |
|---|---|
| KPI-A | `127.0.0.1:8090` = **ABSENT**（env 未设·默认口无监听·与 D-10 车道 DARK 判定一致） |
| KPI-B | **0.4 = PARTIAL（2/5）** |
| 活的回退 | `qa_smoke.py:79`（`:11434` UP）／`resident_qa_server.py:36`（`RESIDENT_QA_LLM_URL`→`:11434` UP） |
| 不活的 3 行 | `cost_ledger.py:1429`、`p0_moe_measure.py:35`、`qa_smoke.py:772`——作用域内只有 `:8090`，无第二端点 |

## 四、与 10-29 12:00 终审的接面

- 走 (a) 35B 取回起火：KPI-A 应转 **UP**；KPI-B 分母不变，分子随 :8090 UP 自动升（5/5=GREEN 预期）。
- 走 (b) 登记墓碑态：KPI-A 恒 **ABSENT** 为合法读数；KPI-B 成为唯一活指标——**3 行无第二端点者是必须改指的对象**，本表第四节即改指名单。
- 探针接线给 @HQ 工具域：`report` 子命令已产出机读 JSON（键 `kpi_a_reachability.state` / `kpi_b_fallback.verdict`），可直接入心跳。

## 五、验证声明

- 全部读数由本地真实 socket / HTTP 调用取得，零估算零转述；selftest 覆盖 UP/DOWN/ABSENT 三态、空模型列表、N/A 分母、端口解析与噪声排除（18/18，2026-10-28 22:4x 起含 §六 角色分段八项 S11-S18）。
- 未启动、未停止、未改指任何服务（承 D-18 ⑤ 本班不重启 :8090、不拉模型）。

## 六、三行缺口的角色标注（2026-10-28 22:4x·T158 预备勘察·非改指）

T158 原记「三行作用域内只有 `:8090` 单端点」。逐行读码后定性：**三行都不是「本可回退」的消费调用点**。

| 行 | 角色 | 依据 |
|---|---|---|
| `cost_ledger.py:1429` | RECORD-FIELD | `l1-record --endpoint` 的默认值只是写进账本行的字段，文件内无任何从该字面量发出的请求 |
| `p0_moe_measure.py:35` | DIAGNOSTIC-TARGET | V100 P0 基准测量器的 `--base-url`（CLI 可覆盖）——被测对象就是这条车道，加回退会静默测到别的车道 |
| `qa_smoke.py:772` | DIAGNOSTIC-TARGET | `CODER_PORT` 是车道存活探针的报告对象；回退会把「车道死了」变成「探到了别的车道」＝把探针存在的意义反转 |

因此给这三行「补第二端点」不是改进而是有害：探针类行一旦回退，复现的正是 T153/BC-P-132 那类假绿。真正的处置差异是：

- `cost_ledger.py:1429` 只在标签语义上受影响（改标签、或登记「不接回退」的理由）；
- 两个探针靶**应当**保持单端点，需要改的是判据口径——探靶应有独立读数，而不是被计入「消费回退率」。

### 六.1 分段读数（加性·主判据未变）

`fallback` 命令新增第二读面，主行与默认退出码口径**逐字不变**：

```
KPI-B fallback rate=0.4 verdict=PARTIAL live=2/5 port=8090        <- 主行（未变）
KPI-B segments consumption rate=1.0 verdict=GREEN live=2/2 excluded=3
```

- 主判据仍按全量 FALLBACK-OK 行（2/5=0.4 PARTIAL），10-29 终审前**不改分母**；
- 消费段（只算真正的出站调用行）= 2/2：`qa_smoke.py:79`、`resident_qa_server.py:36` 均经 `:11434` 实测 UP；
- 三行被排除者的角色与理由逐行打印；`--segmented` 才把退出码切到消费段（默认关＝不预判终审）。

### 六.2 轮内自捉缺陷（仪器自指）

本轮角色表落在模块层，普查的模块级 `FAILOVER-KW` 作用域判据因此把本文件 `DEFAULT_PORT = 8090` 翻成一条 FALLBACK-OK 行（5 行→6 行）。定谳＝**KPI-B 不能因被测仪器自己的注释文本而变**：`census_rows` 增自指排除（本文件行一律剔除），selftest S18 钉死。修正后行数回到 5，主判据 0.4 与 19:07 轮读数逐字一致。

### 六.3 跨工具词表归一（10-29 00:5x 令牌轮·产品增量）

`qa_smoke.py` 探针 no.9（charter §三.9·L1 coder 车道 :8090 活体）此前把三种失败都写成 `LANE-DOWN`，其中「无监听」一档把 §一 的 **ABSENT（未部署·非故障读数）** 与 **DOWN（该up而未up）** 混成了一个词——同一台机器同一时刻，KPI 探针读 ABSENT、QA 探针读 LANE-DOWN，12:00 终审要在两个词之间人工换算。

本轮把 no.9 的分类器接到探针 no.13 的工件证据上，新增第四类（**判据未动**，四类全 FAIL，charter §三.9 冻结）：

| 类 | 判据 | 对 §一 词表 |
|---|---|---|
| FOREIGN-PORT | 非 lane 镜像占住端口 | DOWN（不可绑定） |
| LANE-DOWN | lane 镜像在听但不答 | DOWN |
| LANE-DOWN | 无监听且工件在位（应该 up） | DOWN |
| **LANE-ABSENT** | 无监听**且** no.13 报 `LANE-ARTIFACT-MISSING` | **ABSENT（D-18 ③ 非故障读数）** |

实跑一致（2026-10-29 00:5x 同窗两工具）：

```
l1_kpi_probe reach  ->  KPI-A state=ABSENT
qa_smoke no.9       ->  L1 coder serve 8090 LANE-ABSENT: no listener on :8090 ... and a
                        lane prerequisite is gone (LANE-ARTIFACT-MISSING model=...gguf, exe=...)
```

`selftest_coder_class` 3→**5/5 PASS**（新增 D=工件缺→LANE-ABSENT、E=工件在位→LANE-DOWN，钉死两档不可互串）。**未做**：charter 的 PASS 判据未改一毫（10-29 12:00 终审后按裁决再动），工件缺失也不因「已知」而降级。