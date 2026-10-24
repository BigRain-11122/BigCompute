# 模型集基线刷新政策 v1（BC-P-103 ②）

> 立档 2026-10-24（M80 承接·M79 baseline-diff 落地同窗派生）。适用件=`Tools/model_ref_drift_check.py baseline-diff`；基线件=`state/model-ref-drift-<date>-<time>.json`（现役默认基线=2026-10-16 19:19:16 扫描档，live=3：bge-m3:latest / qwen3.8:27b / qwen3:4b-instruct）。

## 一、为什么要有这条政策

基线是「应该存在什么」的记录。基线一旦被随手刷新成「现在有什么」，告警面就永远绿色——10-14 九模型消失事故（M71）与 10-24 单模现状（M79 实测 missing=2）都是无人告警才被人工发现。**刷基线=消音**，所以必须区分「有意变更」与「能力缺失」。

## 二、两类变更，两种处置

| 变更来源 | 判据 | 处置 |
|---|---|---|
| 有意变更（下载/升级/退役计划） | 有 CEO 令、委员会裁定或明确工单指向该模型集变更 | 变更执行后**同窗重立基线**（新基线件 + 旧件保留不删），并在 rounds.log 记 `modeldrift:baseline-refresh reason=<令号/工单>` |
| 能力缺失（模型消失、盘面删除、服务端清单缩水） | 无对应令/工单 | **维持告警不刷基线**；按级登记（缺生产必需模型=P1 入日清）；修复=恢复模型，不是改基线 |

判据冲突时按「无令即缺失」从严：没有出处就不许刷新。

## 三、新基线怎么落

1. 取现役清单：`python Tools/model_ref_drift_check.py check --out state/model-ref-drift-<date>-<time>.json`（只读 GET :11434/api/tags）。
2. 人工核对每条 live tag 都是**预期存在**的（不是「剩下什么就记什么」）。
3. 新件即成为默认基线：`baseline-diff --baseline state/model-ref-drift-<新>-<时间>.json`。
4. 旧基线件保留在 state/（历史对照，禁删）。

## 四、告警接线（同批 BC-P-103 ①③）

- ① 轮收尾：`python Tools/model_ref_drift_check.py baseline-diff --rounds-line` → 输出单行 `ROUNDS-LINE modeldrift:status=... baseline=N live=N missing=N added=N`，摘入 rounds.log 的 queue/gpu 同段（advisory，exit 0，零云）。
- ③ `Tools/idle_selfcheck.py check` 心跳行尾 `model_drift=DRIFT(m=2,a=0)` —— informational 读数，不触发 P1、不阻断自检（fail-open → `UNVERIFIED(err)`）。
- 告警面唯一入口=`baseline-diff` 显式命令的 `WARN MODEL-DRIFT` 行与退出码（`--strict` 才非零）。

## 五、验证声明

- 本节所述的字段与命令均在本轮实跑出数：`ROUNDS-LINE modeldrift:status=DRIFT baseline=3 live=1 missing=2 added=0`（证据 `state/model-ref-baseline-diff-20261024-122422.json`）；心跳行 `model_drift=DRIFT(m=2,a=0)`（`state/heartbeat.txt` 2026-10-24 12:24:26 行）。
- 自测：`model_ref_drift_check.py selftest` 15/15 PASS（S13/S14 为摘要行形状断言）；`idle_selfcheck.py selftest` PASS（J12 三态夹具）。
- 未验证面：此次 missing=2（bge-m3:latest、qwen3.8:27b）来源未定谳——按第二节判为「能力缺失」档，维持告警；恢复动作归模型供应方窗，不在本政策内自决。