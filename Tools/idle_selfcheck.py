#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""idle_selfcheck.py — §9.6 闲置硬触发闭环·每轮自检步（O-20261007-2315 承接·BC-P-54 批活）
+ serve_liveness 探活扩展（BC-P-55·M57④ 10-08 serve 无声死亡 ~20min 事故实证派生——
探针+轮报告 P1 行本窗落地·净重启自动动作随批非自决维持）

构造化执法（§9.6.1-2）：读本机资源面 → GREEN-IDLE 达档〔§8.2：RAM 空闲≥40% 且
VRAM≥6GB〕→ 领池检（fleet/backlog 可领行计数）→ 池空拉本司三队列议程计数 →
两空写 agenda_starved 旗；连续 ≥2 轮达档未领单 → idle_rounds +1（机内自动·不等夜轮）。
心跳双字段经 round_append.py 唯一路径落 state/heartbeat.txt（禁 shell Add-Content·T24）。

判据（预注册·回访 10-13/14 并窗）：
  J1 资源读数面：RAM 空闲% = ctypes GlobalMemoryStatusEx 实读；VRAM free MiB =
     nvidia-smi 单源直读（O-20261006-2358 face 2·WMI 已知 bug 不用）
  J2 计数面：pool_claimable = fleet/backlog.md 含「可领」且无 claimed@ 的行数；
     queue_open = state/queue/{main,tech,explore}.md 含 open 的任务行数
  J3 写路面：双字段行经 round_append.py append 落 state/heartbeat.txt；
     轮间状态件 state/idle_selfcheck.json（consecutive_idle/idle_rounds 持久化）
  J4 selftest：RAM/VRAM 探针数值断言 + backlog 解析夹具断言 + 状态件 temp 往返
     + round_append 在位断言——零真实状态文件副作用
  J5 serve 探活（BC-P-55）：/api/tags 2s 超时只读 GET·alive=http200；
     死亡（连接拒/超时/非 200）→ check 落 P1 行（共租户受损预警面）·自动重启随批

用法：
  python Tools/idle_selfcheck.py check     # 每轮自检步（iteration_loop.ps1 内建）
  python Tools/idle_selfcheck.py selftest
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEARTBEAT = os.path.join(PROJECT, "state", "heartbeat.txt")
STATE_FILE = os.path.join(PROJECT, "state", "idle_selfcheck.json")
ROUND_APPEND = os.path.join(PROJECT, "Tools", "round_append.py")
BACKLOG = os.path.abspath(os.path.join(
    PROJECT, "..", "..", "quant", "BigMoney", "fleet", "backlog.md"))
QUEUE_DIR = os.path.join(PROJECT, "state", "queue")

# §8.2 旗标判据（resource-chain L101·预注册·禁调参不经立法）
RAM_FREE_PCT_MIN = 40.0
VRAM_FREE_MB_MIN = 6 * 1024
CONSECUTIVE_TRIGGER = 2  # §9.6.2 两读触发
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"  # BC-P-55 探活端点（只读）


class MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def probe_ram_free_pct():
    """J1：RAM 空闲% 实读（GlobalMemoryStatusEx）。失败=-1.0（不达标面）。"""
    try:
        s = MemoryStatusEx()
        s.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):
            return round(100.0 * s.ullAvailPhys / s.ullTotalPhys, 1)
    except Exception:
        pass
    return -1.0


def probe_vram_free_mb():
    """J1：VRAM free MiB 实读（nvidia-smi 单源·O-20261006-2358 face 2）。失败=-1。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        vals = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
        return min(vals) if vals else -1
    except Exception:
        return -1


def probe_serve_liveness(url=OLLAMA_TAGS_URL, timeout=2):
    """J5（BC-P-55）：serve 探活——/api/tags 只读 GET·2s 超时。

    返回 (alive, detail)：alive=http200；死亡=连接拒/超时/非 200，
    detail=异常类名或 http<code>（心跳行诊断位·app.log 事故面 10-08 在案）。
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status == 200:
                return True, "http200"
            return False, "http%d" % r.status
    except Exception as e:
        return False, type(e).__name__


def count_pool_claimable(backlog_path=BACKLOG):
    """J2：备货池可领行数（含「可领」且未 claimed@——池律原文行尾记账）。"""
    if not os.path.exists(backlog_path):
        return 0
    n = 0
    with open(backlog_path, encoding="utf-8") as f:
        for line in f:
            if "claimed@" in line:
                continue
            if "可领" in line:
                n += 1
    return n


def count_queue_open(queue_dir=QUEUE_DIR):
    """J2：本司三队列 open 任务行数（行含 open 且非表头/分隔行）。"""
    n = 0
    for name in ("main.md", "tech.md", "explore.md"):
        p = os.path.join(queue_dir, name)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.lstrip().startswith("|"):
                    continue
                if "---" in line:
                    continue
                if "open" in line.lower():
                    n += 1
    return n


def load_state(path=STATE_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"consecutive_idle": 0, "idle_rounds": 0,
                "last_verdict": "-", "last_check": "-"}


def save_state(st, path=STATE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def append_heartbeat(line, heart=HEARTBEAT):
    """J3：round_append 唯一路径（幂等护栏由其内置）。返回 (code, msg)。"""
    if not os.path.exists(ROUND_APPEND):
        return 4, "round_append.py missing"
    r = subprocess.run(
        [sys.executable, ROUND_APPEND, "append", "--file", heart,
         "--line", line], capture_output=True, text=True, timeout=15,
        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or r.stderr).strip()[:120]


def check():
    ram_pct = probe_ram_free_pct()
    vram_mb = probe_vram_free_mb()
    green_idle = (ram_pct >= RAM_FREE_PCT_MIN and vram_mb >= VRAM_FREE_MB_MIN)
    pool = count_pool_claimable()
    q_open = count_queue_open()
    serve_alive, serve_detail = probe_serve_liveness()

    st = load_state()
    if green_idle and pool == 0 and q_open == 0:
        # 达档且无单可领无议程可拉 → 饥荒面：连击计数与 idle_rounds 递增（§9.6.2）
        st["consecutive_idle"] = st.get("consecutive_idle", 0) + 1
        if st["consecutive_idle"] >= CONSECUTIVE_TRIGGER:
            st["idle_rounds"] = st.get("idle_rounds", 0) + 1
        starved = True
    else:
        # 忙面/有单/有议程 → 连击清零（RED 消费仍看 idle_rounds 累计值）
        st["consecutive_idle"] = 0
        starved = False
    verdict = "green-idle" if green_idle else "busy"
    st["last_verdict"] = verdict
    st["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
    st["last_read"] = {"ram_free_pct": ram_pct, "vram_free_mb": vram_mb,
                       "pool_claimable": pool, "queue_open": q_open,
                       "serve_alive": serve_alive, "serve_detail": serve_detail}
    save_state(st)

    line = ("%s idle_selfcheck: verdict=%s ram_free_pct=%s vram_free_mb=%s "
            "pool_claimable=%d queue_open=%d consecutive_idle=%d "
            "idle_rounds=%d agenda_starved=%s serve_alive=%s serve_detail=%s"
            % (time.strftime("%Y-%m-%d %H:%M:%S"), verdict, ram_pct,
               vram_mb, pool, q_open, st["consecutive_idle"],
               st["idle_rounds"], str(starved).lower(),
               str(serve_alive).lower(), serve_detail))
    code, msg = append_heartbeat(line)
    print(line)
    print("append: code=%d %s" % (code, msg))
    if not serve_alive:
        # BC-P-55：serve 死亡 → P1 轮报告行（共租户受损预警·修复面=净重启托盘链
        # 配方在案 M57·自动执行随批非自决）
        p1 = ("%s P1 serve_liveness: OLLAMA DOWN detail=%s — 共租户"
              "（BigMoney L1/BigLife QA/bge-m3）受损预警·修复=净重启托盘链"
              "（M57 配方）·自动重启随批（BC-P-55）"
              % (time.strftime("%Y-%m-%d %H:%M:%S"), serve_detail))
        code2, msg2 = append_heartbeat(p1)
        print(p1)
        print("append-p1: code=%d %s" % (code2, msg2))
    return 0 if code in (0, 2) else 1


def selftest():
    """J4：四断言干跑——探针/解析/状态往返/写路在位。零真实状态文件副作用。"""
    ok = True
    ram = probe_ram_free_pct()
    vram = probe_vram_free_mb()
    print("J1 probe: ram_free_pct=%s vram_free_mb=%s" % (ram, vram))
    ok &= isinstance(ram, float) and ram > 0
    ok &= isinstance(vram, int) and vram > 0

    fx = tempfile.NamedTemporaryFile("w", suffix=".md",
                                      encoding="utf-8", delete=False)
    fx.write("# fixture\n1. 【A】可领\n2. 【B】可领（claimed@bm-a@10-07 22:33）\n"
             "3. 【C】claimed@bm-c@10-01 → done@10-02\n4. 【D】可领\n")
    fx.close()
    n = count_pool_claimable(fx.name)
    print("J2 backlog parse fixture: claimable=%d (expect 2)" % n)
    ok &= (n == 2)
    os.unlink(fx.name)

    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "st.json")
        save_state({"consecutive_idle": 1, "idle_rounds": 0}, p)
        st = load_state(p)
        print("J3 state round-trip: %s" % st)
        ok &= (st["consecutive_idle"] == 1)
    print("J4 round_append present: %s" % os.path.exists(ROUND_APPEND))
    ok &= os.path.exists(ROUND_APPEND)

    alive, detail = probe_serve_liveness()
    alive_dn, detail_dn = probe_serve_liveness(
        "http://127.0.0.1:1/api/tags")  # 闭合端口=死亡路径夹具（零副作用）
    print("J5 serve liveness: live=(%s,%s) down=(%s,%s)"
          % (alive, detail, alive_dn, detail_dn))
    ok &= isinstance(alive, bool)
    ok &= (alive_dn is False and bool(detail_dn))
    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    sys.exit(check() if cmd == "check" else selftest())
