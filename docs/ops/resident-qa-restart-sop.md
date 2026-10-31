# Resident QA 服务重启 SOP v1（T183）

对象：`Tools/resident_qa_server.py`（默认 `127.0.0.1:8792`），计划任务 `BigCompute-ResidentQA`。

## 为什么需要专门一条命令

监听者通常是**计划任务壳发起的 pythonw 子进程**，不是任务本身。
`schtasks /End` 结束的是任务实例，打不到真监听者；`schtasks /Run` 再起一个，
又被单实例护栏挡回自退 —— 结果是「看起来重启了，实际还在跑旧码」。
2026-10-31 T180 一轮两跳才落版本，就是这个坑。

## 用法

```powershell
python Tools\qa_restart.py status                 # 谁在听、健康面版本、任务状态
python Tools\qa_restart.py restart                # 按端口收敛重启（默认 8792）
python Tools\qa_restart.py restart --port 8792 --timeout 40
python Tools\qa_restart.py selftest                # 17 条夹具
```

## 步骤与安全闸

1. `netstat -ano` 取该端口 **LISTENING** 的 PID（ESTABLISHED 不算）。
2. 逐个读 `Win32_Process.CommandLine`，命令行必须含本仓 `Tools\resident_qa_server.py`。
3. **有任一外部属主即整体拒杀**（`REFUSED-FOREIGN`，exit 4，零进程被动）——
   这是让路序的机器化：他司进程不因本司重启而中断。
4. 停本仓监听者 → 端口转空 → `schtasks /Run` 触发任务壳 → 轮询 `/health`。
5. 落证据 `state/qa-restart-<时间戳>.json`（含前后版本、前后 PID、判定）。

## 判定（exit code）

| verdict | 含义 | exit |
|---|---|---|
| `RESTARTED` / `STARTED` | 版本串变了（改了码的重启） | 0 |
| `PID-ROLLED` | 版本没变但监听者换代（未改码的纯重启） | 0 |
| `SAME-VER` | 版本与监听者都没变 → 任务壳没拿到端口 | 2 |
| `NO-HEALTH` | 起完但 `/health` 不可达 | 2 |
| `REFUSED-FOREIGN` | 端口被他司进程持有，未动手 | 4 |
| `STOP-FAILED` / `PORT-STILL-BUSY` | 停不下来/端口未释放 | 5 |

只看版本串会把「成功但未改码」的重启误判为失败，故引入 PID 换代判定；
无 PID 证据时不冒认换代（如实降级为 `SAME-VER`）。