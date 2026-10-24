#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GPU 释放阀核查件（D-20261024-16 ④ 本司承接面·只读）。

判据来源：D-20261024-16 GPU 争用仲裁常设序——
  ①优先级 = CEO 直令在飞 MV/i2v > 其它 GPU 生产批 > 年轮/篝记/LLM 批；
  ④反向约束 = MV 批收尾必须过 keepwarm.pause 释放阀，禁长占空转。
本件只读报告「当前 GPU 是否被长占而空转」与「阀是否已落」，不做任何杀进程/
写阀动作（释放=运营动作归轮内人判·D-16 司内自测禁新起守护进程）。

用法：
  python Tools/gpu_release_valve_check.py check [--valve PATH]
  python Tools/gpu_release_valve_check.py selftest
"""
import argparse
import json
import os
import subprocess
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_VALVE = os.path.abspath(os.path.join(
    PROJECT, "..", "..", ".codely-cli", "engine-tick", "keepwarm.pause"))
HOLDER_KEYS = ("comfyui", "llama-server", "tuanjie", "blender", "krea")
IDLE_UTIL_PCT = 5.0        # D-16 ② 同源（LLM 恢复线 util<30% 的「空转」更严档）
HELD_MEM_MB = 1500         # 占显存却空闲=长占判据（非「在飞批」）


def read_valve(path=DEFAULT_VALVE):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return (f.read() or "").strip()
    except Exception:
        return ""


def probe_gpu():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20)
        util, mem = r.stdout.strip().splitlines()[0].split(",")
        return float(util.strip()), int(float(mem.strip()))
    except Exception:
        return None, None


def probe_holders():
    """只读：GPU 相关常驻进程名清单（判「谁占着」用，不杀不改）。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=30)
        names = [n.strip() for n in (r.stdout or "").splitlines() if n.strip()]
    except Exception:
        return None
    hits = []
    for n in names:
        low = n.lower()
        if any(k in low for k in HOLDER_KEYS):
            if n not in hits:
                hits.append(n)
    return hits


def classify(util, mem_used_mb, holders, valve_text):
    """纯函数：四态判定（fixtures 直测）。

    PAUSED   = 阀已落（写 paused/pausedLite），GPU 应在空；
    UNKNOWN  = 探针不可读（fail-open·不误判）；
    FREE     = 无持有进程且显存占用低；
    HOLD-IDLE= 有持有进程/显存被占但 util 低于空转线 → RELEASE-SUGGESTED；
    OCCUPIED = util 高=批在飞（D-16 序放行·不催不杀）。
    """
    if valve_text:
        return "PAUSED", "RELEASE-SUGGESTED(阀已落·确认无在飞批)"
    if util is None or mem_used_mb is None or holders is None:
        return "UNKNOWN", "NO-ACTION(探针不可读·fail-open)"
    if not holders and mem_used_mb < HELD_MEM_MB:
        return "FREE", "NO-ACTION"
    if util < IDLE_UTIL_PCT:
        return "HOLD-IDLE", "RELEASE-SUGGESTED(长占空转·D-16 ④ 释放阀)"
    return "OCCUPIED", "NO-ACTION(批在飞·按 D-16 序放行)"


def cmd_check(args):
    valve = read_valve(args.valve)
    util, mem = probe_gpu()
    holders = probe_holders()
    state, action = classify(util, mem, holders, valve)
    row = {"ts": None, "state": state, "action": action,
           "valve": valve, "gpu_util_pct": util, "gpu_mem_used_mb": mem,
           "holders": holders, "valve_path": args.valve}
    import time
    row["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    print("VALVE-CHECK state=%s util=%s mem_mb=%s holders=%s action=%s"
          % (state, util, mem, ",".join(holders or []) or "-", action))
    print(json.dumps(row, ensure_ascii=False))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(row, f, ensure_ascii=False, indent=1)
    return 0


def cmd_selftest(_args):
    ok = True

    def case(name, got, want, extra=""):
        nonlocal ok
        good = got == want
        ok &= good
        print("S %s: %s (expect %s) %s" % (name, got, want, extra))
        return good

    case("S1 free", classify(1.0, 300, [], "")[0], "FREE")
    case("S2 hold-idle", classify(2.0, 5000, ["ComfyUI.exe"], "")[0],
         "HOLD-IDLE")
    case("S3 occupied", classify(88.0, 11000, ["python.exe"], "")[0],
         "OCCUPIED")
    case("S4 paused", classify(0.0, 0, [], "paused 2026-10-24T18:00:00")[0],
         "PAUSED")
    case("S5 unknown", classify(None, None, None, "")[0], "UNKNOWN")
    case("S6 boundary idle=5", classify(5.0, 5000, ["x"], "")[0], "OCCUPIED")
    case("S7 mem boundary", classify(1.0, 1500, ["x"], "")[0], "HOLD-IDLE")
    case("S8 action paused", classify(0, 0, [], "pausedLite")[1],
         "RELEASE-SUGGESTED(阀已落·确认无在飞批)")
    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="GPU release valve check (read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--valve", default=DEFAULT_VALVE)
    c.add_argument("--out", default=None)
    c.set_defaults(func=cmd_check)
    s = sub.add_parser("selftest")
    s.set_defaults(func=cmd_selftest)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())