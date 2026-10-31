#!/usr/bin/env python3
"""qa_restart.py — resident QA 服务按端口/属主收敛重启（T183）。

背景：`schtasks /End` + `/Run` 打不到真实监听者——监听者是计划任务壳发起的
pythonw 子进程，单实例护栏又让「再起一个」自退，于是服务长期停在旧码。

一条命令完成：按端口找真监听者 → 校验命令行属本仓脚本 → 停掉 → 触发计划任务
→ 轮询 /health 直到版本变化。非本仓进程一律拒杀（fail-closed）。

用法：
  python Tools/qa_restart.py status
  python Tools/qa_restart.py restart [--port 8792] [--task BigCompute-ResidentQA]
  python Tools/qa_restart.py selftest
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SERVER_SCRIPT = HERE / "resident_qa_server.py"
EVIDENCE_DIR = ROOT / "state"

DEFAULT_PORT = 8792
DEFAULT_TASK = "BigCompute-ResidentQA"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _run(argv, timeout=30):
    kw = dict(capture_output=True, text=True, encoding="utf-8",
              errors="replace", timeout=timeout)
    if os.name == "nt":
        kw["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run(argv, **kw)


def health(port: int, timeout: float = 5.0):
    """读 /health；不可达返回 None（不抛）。"""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def parse_netstat(text: str, port: int):
    """netstat -ano 输出 → 该端口 LISTENING 的 PID 列表（去重保序）。"""
    pids = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto, local, state, pid = parts[0], parts[1], parts[3], parts[4]
        if proto.upper() != "TCP" or not state.upper().startswith("LISTENING"):
            continue
        if local.rsplit(":", 1)[-1] != str(port):
            continue
        if pid.isdigit() and int(pid) not in pids:
            pids.append(int(pid))
    return pids


def listeners(port: int):
    try:
        r = _run(["netstat", "-ano", "-p", "TCP"])
    except Exception:
        return []
    if r.returncode != 0:
        return []
    return parse_netstat(r.stdout or "", port)


def cmdline_of(pid: int) -> str:
    try:
        r = _run(["powershell", "-NoProfile", "-Command",
                  f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"])
    except Exception:
        return ""
    return (r.stdout or "").strip()


def is_ours(cmdline: str, script: Path | None = None) -> bool:
    """命令行是否由本仓 resident_qa_server.py 发起。"""
    if not cmdline:
        return False
    script = script or SERVER_SCRIPT
    norm = cmdline.replace("/", "\\").lower()
    return str(script).replace("/", "\\").lower() in norm


def classify(rows, script: Path | None = None):
    """rows=[(pid, cmdline)] → (ours_pids, foreign_pids)。"""
    ours, foreign = [], []
    for pid, cmd in rows:
        (ours if is_ours(cmd, script) else foreign).append(pid)
    return ours, foreign


def verdict_of(before, after, before_pids=None, after_pids=None) -> str:
    """重启结果判据：版本变化或监听者换代都算接管成功。

    代码未改时版本串不可能变，若只看版本会把成功的重启判成失败；
    此时比对监听 PID 是否换代（属主链换了即证明任务壳拿到了端口）。
    """
    if not after:
        return "NO-HEALTH"
    if not before:
        return "STARTED"
    if before.get("server") != after.get("server"):
        return "RESTARTED"
    if before_pids and after_pids and set(before_pids) != set(after_pids):
        return "PID-ROLLED"
    return "SAME-VER"


def _stop(pid: int) -> bool:
    try:
        r = _run(["taskkill", "/PID", str(pid), "/F"])
        return r.returncode == 0
    except Exception:
        return False


def _port_free(port: int, wait_s: float = 10.0) -> bool:
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not listeners(port):
            return True
        time.sleep(0.4)
    return False


def _write_evidence(payload: dict) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = EVIDENCE_DIR / f"qa-restart-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def cmd_status(port: int, task: str) -> int:
    before = health(port)
    pids = listeners(port)
    rows = [(pid, cmdline_of(pid)) for pid in pids]
    ours, foreign = classify(rows)
    print(f"port={port} listeners={pids} ours={ours} foreign={foreign}")
    print("health.server=" + ((before or {}).get("server") or "<unreachable>"))
    try:
        q = _run(["schtasks", "/query", "/tn", task, "/fo", "list"])
        for line in (q.stdout or "").splitlines():
            if line.startswith(("Status", "Last Run Time", "Last Result", "Task To Run")):
                print(line.strip())
    except Exception as exc:  # noqa: BLE001
        print(f"task query failed: {exc}")
    return 0 if (ours or before) else 3


def cmd_restart(port: int, task: str, timeout_s: float = 45.0) -> int:
    before = health(port)
    pids = listeners(port)
    rows = [(pid, cmdline_of(pid)) for pid in pids]
    ours, foreign = classify(rows)

    payload = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "port": port, "task": task,
        "before_ver": (before or {}).get("server"),
        "before_pids": pids, "ours": ours, "foreign": foreign,
    }

    if not pids:
        print(f"port {port}: no listener — will just trigger task")
    elif foreign:
        payload["verdict"] = "REFUSED-FOREIGN"
        ev = _write_evidence(payload)
        print(f"REFUSED: port {port} held by non-BigCompute process(es) {foreign}; "
              f"nothing killed. evidence={ev}")
        return 4

    for pid in ours:
        if not _stop(pid):
            payload["verdict"] = "STOP-FAILED"
            print(f"failed to stop pid {pid}; evidence={_write_evidence(payload)}")
            return 5

    if pids and not _port_free(port):
        payload["verdict"] = "PORT-STILL-BUSY"
        print(f"port {port} still listening after stop; evidence={_write_evidence(payload)}")
        return 5

    try:
        r = _run(["schtasks", "/Run", "/tn", task])
    except Exception as exc:  # noqa: BLE001
        payload["verdict"] = f"TASK-RUN-ERROR: {exc}"
        print(f"schtasks /Run failed: {exc}; evidence={_write_evidence(payload)}")
        return 6
    payload["task_run_rc"] = r.returncode

    after = None
    after_pids = []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        after = health(port)
        after_pids = listeners(port)
        ver_moved = not before or (after or {}).get("server") != before.get("server")
        pid_moved = bool(after_pids) and set(after_pids) != set(pids)
        if after and (ver_moved or pid_moved):
            break
        time.sleep(1.0)

    verdict = verdict_of(before, after, pids, after_pids)
    payload["after_ver"] = (after or {}).get("server")
    payload["after_pids"] = after_pids
    payload["verdict"] = verdict
    ev = _write_evidence(payload)

    print(f"before={payload['before_ver']} after={payload['after_ver']} "
          f"pids {pids}->{after_pids} verdict={verdict} evidence={ev}")
    if verdict in ("RESTARTED", "STARTED", "PID-ROLLED"):
        return 0
    if verdict == "SAME-VER":
        print("监听者与版本均未变：任务壳没拿到端口，另查是否有他处 pythonw 直起。")
    return 2


def selftest() -> int:
    checks = []

    def chk(name, cond):
        checks.append((name, bool(cond)))

    netstat = (
        "  TCP    127.0.0.1:8792         0.0.0.0:0              LISTENING       62916\n"
        "  TCP    127.0.0.1:8792         127.0.0.1:50123        ESTABLISHED     62916\n"
        "  TCP    127.0.0.1:8090         0.0.0.0:0              LISTENING       111\n"
        "  TCP    [::1]:8792             [::]:0                 LISTENING       62916\n"
    )
    chk("S1 netstat 只取 LISTENING + 指定端口", parse_netstat(netstat, 8792) == [62916])
    chk("S2 其他端口不串", parse_netstat(netstat, 8090) == [111])
    chk("S3 无关/坏行容错", parse_netstat("garbage\n\n  TCP  abc", 8792) == [])

    ours_cmd = '"C:\\Program Files\\Python314\\pythonw.exe" C:\\x\\compute\\BigCompute\\Tools\\resident_qa_server.py serve'
    foreign_cmd = '"C:\\Program Files\\Python314\\pythonw.exe" C:\\other\\app.py serve'
    fixture_script = Path(r"C:\x\compute\BigCompute\Tools\resident_qa_server.py")
    chk("S4 本仓脚本识别（反斜杠路径）", is_ours(ours_cmd, fixture_script))
    chk("S5 正斜杠路径同认",
        is_ours("python C:/x/compute/BigCompute/Tools/resident_qa_server.py serve", fixture_script))
    chk("S6 他仓进程不认", not is_ours(foreign_cmd, fixture_script))
    chk("S7 空命令行不认", not is_ours("", fixture_script))

    o, f = classify([(1, ours_cmd), (2, foreign_cmd), (3, ours_cmd)], fixture_script)
    chk("S8 混合面拆分正确", o == [1, 3] and f == [2])
    o2, f2 = classify([(9, foreign_cmd)], fixture_script)
    chk("S9 全外部=零可杀目标", o2 == [] and f2 == [9])

    chk("S10 版本变化=RESTARTED", verdict_of({"server": "v1.2"}, {"server": "v1.3"}) == "RESTARTED")
    chk("S11 版本不变=SAME-VER", verdict_of({"server": "v1.3"}, {"server": "v1.3"}) == "SAME-VER")
    chk("S12 起来前无服务=STARTED", verdict_of(None, {"server": "v1.3"}) == "STARTED")
    chk("S13 起来后不可达=NO-HEALTH", verdict_of({"server": "v1.3"}, None) == "NO-HEALTH")
    chk("S14 双不可达=NO-HEALTH", verdict_of(None, None) == "NO-HEALTH")
    chk("S15 同版本但监听者换代=PID-ROLLED",
        verdict_of({"server": "v1.3"}, {"server": "v1.3"}, [111], [222]) == "PID-ROLLED")
    chk("S16 同版本且同一监听者=SAME-VER",
        verdict_of({"server": "v1.3"}, {"server": "v1.3"}, [111], [111]) == "SAME-VER")
    chk("S17 无 PID 证据时不冒认换代",
        verdict_of({"server": "v1.3"}, {"server": "v1.3"}, [], []) == "SAME-VER")

    failed = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    print(f"selftest {len(checks) - len(failed)}/{len(checks)}")
    return 0 if not failed else 1


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("status", "restart"):
        sp = sub.add_parser(name)
        sp.add_argument("--port", type=int, default=DEFAULT_PORT)
        sp.add_argument("--task", default=DEFAULT_TASK)
        if name == "restart":
            sp.add_argument("--timeout", type=float, default=45.0)
    sub.add_parser("selftest")
    args = p.parse_args()

    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "status":
        return cmd_status(args.port, args.task)
    return cmd_restart(args.port, args.task, getattr(args, "timeout", 45.0))


if __name__ == "__main__":
    sys.exit(main())