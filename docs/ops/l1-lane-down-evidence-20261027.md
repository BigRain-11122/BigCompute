# L1 :8090 停摆根因证据件（T139·2026-10-27 03:16-03:18 实测）

> 目的：把「:8090 车道 DOWN + CoderServe 每 5min 退 2」从「疑模型缺位」升级为**已实证的根因 + 时间线**，一页交模型供应窗/ HQ 工具域裁决。
> 纪律：本司**只读取证，零重启、零重拉、零改脚本**（M71 判例·跨域修法不归本司）。全部数值为本轮 03:16-03:18 实测。

## 一、exit 2 语义（源码直读·只读）

被调脚本=`C:\Users\sjs20\Desktop\FluxGroup\Tools\coder-serve.ps1`（2451B·mtime 2026-10-25 10:59:59，非本司仓）。

| 分支 | 源码行 | 判据 | 落日志 |
|---|---|---|---|
| 模型缺位 | L15 `if (-not (Test-Path $model))` → **L19 `exit 2`** | `C:\Users\sjs20\models\qwen36-35b\Qwen3.6-35B-A3B-UD-IQ4_XS.gguf` 不存在 | `media\logs\coder-serve-MISSING.log` 追加 `MISSING_MODEL <path>` |
| 可执行件缺位 | L21 `if (-not (Test-Path $exe))` → **L25 `exit 2`** | `C:\Users\sjs20\models\llamacpp-b11429\llama-server.exe` 不存在 | 同上追加 `MISSING_EXE <path>` |

**exit 2 只有这两个来源**（脚本内无其他 exit 2；L11 `exit 0`=暂停旗标，L13 `exit 0`=端口已活，L34 `exit 0`=启动成功）。故「每 5min 退 2」在语义上唯一指向**前置件缺位**，与端口/模型加载/显存无关。

## 二、三面实测（本轮 03:16-03:18）

| 面 | 实测 | 判定 |
|---|---|---|
| ① 模型 blob | `C:\Users\sjs20\models\qwen36-35b\...IQ4_XS.gguf` → **ABSENT**；且父目录 `C:\Users\sjs20\models` **整目录不存在**（Get-ChildItem 空） | 缺位·主因 |
| ① 附 有界搜索 | `C:\Users\sjs20` 与 `D:\AI` 递归 3 层 `*.gguf` → **0 命中**；`D:\AI\models` 亦不存在 | 非「挪了位置」本地可寻 |
| ② 可执行件 | `C:\Users\sjs20\models\llamacpp-b11429\llama-server.exe` → **ABSENT** | 缺位·次因（模型分支先命中故日志未体现） |
| ③ 端口 8090 | `Get-NetTCPConnection -LocalPort 8090 -State Listen` → **零监听者** | 非端口冲突（BC-P-101 旧判断「eCloud 占位」本轮不成立） |
| ③ health | `GET http://127.0.0.1:8090/health` → **连接被拒** | 车道确实 DOWN |
| 日志体量 | `coder-serve-MISSING.log` **341 行 / 33,759B**，`MISSING_MODEL=341`、`MISSING_EXE=0`，日期集 `{10-25, 10-26, 10-27}` | 41.8h 连续缺位 |
| 任务态 | `bm-a-CoderServe` State=**Ready**，LastRunTime 2026-10-27 03:16:48，**LastTaskResult=2**，NextRun 03:21:47 | 看门狗正常在跑、正常 fail-loud（非假绿） |
| 磁盘 | C: 可用 **938GB** | 恢复两件（~18GB+exe）物理可行 |
| 暂停旗标 | `FluxGroup\.codely-cli\engine-tick\keepwarm.pause` → **不存在** | 车道停摆不属 CEO 让路窗（非豁免态） |
| 兜底 | ollama `/api/tags` live = `qwen3:4b-instruct` 单模型 | 降级可用（D-20261026-27 ③ 口径） |

## 三、时间线重建（由日志与文件时间戳拼合）

| 时点 | 事件 | 依据 |
|---|---|---|
| 2026-10-20 19:26:50 | 最后一次成功启动 `launched pid=30964` | `coder-serve-launch.log` 尾行（共 28 行） |
| 2026-10-21 03:02:43 | 该进程输出停止（进程死亡时刻） | `coder-serve-20261020-192650.err.log` LastWriteTime |
| （10-21 03:02 → 10-25 09:26 之间） | **两件产物从磁盘消失**；期间看门狗要么被暂停旗标挡住（exit 0），要么复启失败而日志尚未生成 | 无 MISSING 行·推断段（**本轮不臆断具体删除动作者**） |
| 2026-10-25 09:26:52 | **首条 `MISSING_MODEL`** —— 车道自此永久起不来 | MISSING.log 第 1 行 |
| 2026-10-26 22:3x | QA 探针首次翻 `LANE-DOWN`（此前为 `DARK[OCCUPIED]`） | 前轮日清 P1（旧进程/占用者终亡才暴露） |
| 2026-10-27 03:16:50 | 第 341 条 MISSING_MODEL（本轮取证时点） | MISSING.log 尾行 |

**核心结论**：22:3x 的「DOWN 状态迁移」**不是新故障**，而是**旧监听者消失后才暴露的既有故障**——真实缺位起点是 10-25 09:26:52，比状态面首见早 **~37.6h**。

## 四、与既往判断的差异（订正留痕）

1. **BC-P-101（10-24）**称「端口被 eCloud.exe 占用 + 权限 + exe 双缺失」——本轮实测**无任何监听者**，故「端口占用」已不成立，仅「前置件缺失」成立。
2. **D-20261026-27（10-26 23:3x）**记 `Test-Path=False`——本轮把首次缺位时标前推至 **10-25 09:26:52**（早 1.5 天），并给出连续 341 次机读证据。
3. 本轮**不判**「谁删了产物」：`models` 整目录消失属跨域事件，归 HQ 工具域/模型供应窗按需事后核（禁本司推测点名）。

## 五、处置建议（呈模型供应窗 / HQ 工具域·本司零动作）

恢复清单（两件，路径必须与脚本内常量逐字一致，否则继续 exit 2）：

1. `C:\Users\sjs20\models\qwen36-35b\Qwen3.6-35B-A3B-UD-IQ4_XS.gguf`（约 18GB·寻源闸：hf-mirror / GitHub release）
2. `C:\Users\sjs20\models\llamacpp-b11429\llama-server.exe`（llama.cpp b11429 对应版本）

前置已具备：磁盘 938GB 可用（>25GB 门槛·D-20261026-27 ②）、暂停旗标未挂（无让路冲突）、看门狗 Ready 每 5min 自愈。
恢复后验收三判据（缺一不算闭环）：① 8090 出现监听者 ② `GET /health` 200 ③ 本司 `qa_smoke` 探针 no.9 由 FAIL 转 PASS（连续 3 次）。若夜间重取，限 19:00-09:00 窗（D-20261026-27 ③）。

## 六、盲区发现（派生 T140）

看门狗日志把缺位写得很清楚（341 行），但**集团/本司探针只观测端口**：端口在线时可发现「掉线」，产物缺位而进程未起时**没有任何一面在 1h 内报警**——本案盲区 **41.8h**（10-25 09:26 → 10-27 03:16 全靠人工/QA 顺带发现）。派生待办：只读在位性探针（双 `Test-Path`）接一轮内既有探针位，缺位即当日 P1，不再等端口翻转。

## 七、证据原文（本轮命令输出摘要）

```
ABSENT  C:\Users\sjs20\models\qwen36-35b\Qwen3.6-35B-A3B-UD-IQ4_XS.gguf
ABSENT  C:\Users\sjs20\models\llamacpp-b11429\llama-server.exe
C:\Users\sjs20\models exists=False        D:\AI\models exists=False
port 8090 Listen -> (none)                HEALTH ERR 无法连接到远程服务器
MISSING-LOG lines=341 size=33759 mtime=2026-10-27 03:16:50
first: 2026-10-25 09:26:52 MISSING_MODEL ...
last : 2026-10-27 03:16:50 MISSING_MODEL ...
bm-a-CoderServe Ready LastRun=2026-10-27 03:16:48 LastResult=2 Next=03:21:47
C: FreeGB=938   pause_flag_exists=False   ollama=qwen3:4b-instruct
```

（证据原件：`FluxGroup\media\logs\coder-serve-MISSING.log`、`coder-serve-launch.log`、`coder-serve-20261020-192650.err.log`、`Tools\coder-serve.ps1`；本轮为只读取证，未改任何跨域文件。）