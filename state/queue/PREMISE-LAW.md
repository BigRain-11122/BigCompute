# 队列立行前提律（T146·2026-10-27 立）

**律**：任何新队列行若以事实断言落「已损坏 / 已丢失 / 已被破坏 / 已固化 / 已污染 / 已截断 / 已失效」类
损害前提，必须在同一行给出**可复跑命令**或**文件级证据指针**（state/、docs/、qa/、cph4/、Tools/ 路径，
或「证据 / 实测 / 普查 / 字节 / 行号 / 指针 / 日志」字样）；否则该行 premise 记 **UNVERIFIED**。

**机器面**：`python Tools/queue_check.py` 每次实跑列出
`premise=UNVERIFIED <行号>: <命中的断言词> (no evidence pointer)`。
只列名、不阻断（advisory 面）。selftest：`python Tools/queue_check.py --selftest`（8 夹具）。

**为什么要这条**：两案实证——立行前提被当成既定事实写进队列，下一轮把它当真相照做，
结果真产出变成修一个不存在的问题。

## 回填范例（两案）

1. **tech T145（10-27）**：立行时断言「rounds.log 历史行已被 GBK 固化破坏」。
   字节级普查后**前提全被推翻**（386,303B strict-UTF8 OK / U+FFFD=0 / 324/324 行含中文 /
   4,255 个拉丁区字符全是 § × ¥ 合法排版符）——真正产出从「修历史」转为「防写侧回归」
   （ledger_encoding_guard 判据单源 + round_append J8 闸 + qa_smoke 探针 no.15）。
   证据：`state/rounds-encoding-note.md`。

2. **tech T139（10-27）**：立行前提「eCloud.exe 占用 :8090」。三面实测后**前提不成立**
   （零监听者 + 产物 ABSENT + MISSING.log 341 行）——真实根因是前置件缺位，
   且首条 MISSING 时标比状态面首见早 ~37.6h。证据：`docs/ops/l1-lane-down-evidence-20261027.md`。

两案共同点：断言写得很确定，证据为 0；补一条可复跑命令的成本远低于后续返工。