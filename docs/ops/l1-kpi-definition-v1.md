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

- 全部读数由本地真实 socket / HTTP 调用取得，零估算零转述；selftest 覆盖 UP/DOWN/ABSENT 三态、空模型列表、N/A 分母、端口解析与噪声排除（10/10）。
- 未启动、未停止、未改指任何服务（承 D-18 ⑤ 本班不重启 :8090、不拉模型）。