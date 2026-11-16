# 乱码归因报告：T-20261117-133 判负（真凶在调用面）

- 时间：2026-11-17 06:2x 令牌轮（BigCompute-OSLoop）
- 工具：`Tools/mojibake_triage.py`（本轮新建，selftest 10/10 PASS）
- 立行：T-20261117-133「month_end_collect / gpu_idle_collector 源内中文标签双重编码」

## 结论

**源内没有双重编码。两个点名文件都是严格 UTF-8、零 BOM、零 mojibake 命中。**
乱码发生在 PowerShell 5.1 的 native-command 管道：Python 输出 UTF-8 字节，
PowerShell 按 GBK 解码显示（`MISREAD-ONLY`），若再用 `>` 重定向落盘还会被包成
UTF-16LE 外壳（`PS-REDIRECT-WRAPPED`）——两种现象肉眼同形，与「真写坏」无法区分，
这正是本件判据③④要拆开的东西。

## 三条实测证据

| # | 对象 | 判定 | 关键读数 |
|---|---|---|---|
| 1 | `Tools/month_end_collect.py` | `CLEAN` | 55823 B / 918 行 / UTF8_STRICT_OK / bad=0 reversible=0 irreversible=0 |
| 2 | `Tools/gpu_idle_collector.py` | `CLEAN` | 61502 B / 1333 行 / UTF8_STRICT_OK / bad=0 reversible=0 irreversible=0 |
| 3 | 同一份 selftest 输出的两种落盘 | 见下 | 内容相同，字节不同 |

同一命令 `month_end_collect.py selftest` 的两种落盘：

| 调用面 | 首字节 | 容器 | 判定 |
|---|---|---|---|
| `python ... > out.txt`（PowerShell 重定向） | `ff fe` | UTF-16LE | `PS-REDIRECT-WRAPPED`（内层 7 行可逆 / 27 行不可逆） |
| `subprocess.run(stdout=open(...,'wb'))`（直写） | `50 41 53 53` = `PASS` | UTF-8 | `CLEAN`，43 行，42/42 PASS |

### 证据 4：T-133 点名的第二处（gpu_idle_collector borrow-window 行）

同一份 `gpu_idle_collector.py report` 输出，PowerShell 显示端读到：

    鐢蜂富鎯呯鍩哄噯甯簱鐑у埗鐏?夜窗（O-20261012-2330 ③ bm-a 夜…

用 Python 直写落盘（`qa/gpu-report-20261117.txt`）后同一段：

    borrow-window declared: 2026-10-13..2026-10-13 consumer=BigStream mv0001 男主情绪基准帧库烧制夜窗（O-20261012-2330 ③ bm-a 夜…

全文件 `has_moji=False`、首字节 `mach`（machine=bm-a 前缀）。**同一批字节，两种读法**——
这正是 `MISREAD-ONLY` 的定义，也是 T-133「同一文件两种编码并存」观感的来源：
文件只有一种编码，眼睛被喂了两种。

两条路径的 **selftest 结论一致（42/42 PASS）** —— 说明工具判定面没有被编码问题污染，
被污染的只有证据输出的可读性。

## 判据对照（T-133 四条）

| 判据 | 结果 | 证据 |
|---|---|---|
| ① 逐行定位双重编码字面量出行号清单 | ✅ | `mojibake_triage.py triage <path>` 输出 `L<n>` 级清单（PS 产物 L2/L3/L6/L7/L20/L28 等 7 行可逆 + 27 行不可逆） |
| ② 按可逆规则还原，不可逆者保留并具名标注 | ✅ | 可逆行直接给出 `=> 还原文本`（如 L2 `瑙ｆ瀽` => `解析`）；不可逆行标 `IRREVERSIBLE` 保留 |
| ③ 改后 selftest 计数与判定不变且输出中文可读 | ✅ | 未改动任何被测文件；`qa/mojibake-selftest-20261117.txt` = 纯 UTF-8、42/42 PASS、中文可读 |
| ④ 普查输出零乱码行 | ✅ | 走 `explain` 给的调用面配方（`python -X utf8` + `Out-File -Encoding utf8`）即零乱码；工具自身 `sys.stdout.reconfigure(encoding="utf-8")` 已自保 |

## 后续处置（防第三次复发）

1. 本件判负，**不再对源文件做编码改动**——T252 已重扫一遍、本件又立一遍，两次都在治不存在的病。
2. 任何「看到乱码」的报障，先跑 `python -X utf8 Tools/mojibake_triage.py triage <path>`：
   - 判 `CLEAN` / `MISREAD-ONLY` → 改读取/调用面，不动文件；
   - 判 `WRITE-DAMAGED` → 才进 `Tools/encoding_repair.py scan`。
3. 落盘证据一律走直写或 `Out-File -Encoding utf8`，禁用裸 `>` 接 native 命令。
