#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cpu_read_ab_probe — CPU 读数两条通道的 A/B 实测（OH-20261104 切片落地件）

背景
    Tools/idle_selfcheck.py J2 现役读 CPU 负载走 PowerShell 子进程
    (Get-CimInstance Win32_Processor -> LoadPercentage)。每轮 spawn 一个
    powershell.exe，Windows 上冷启 ~300-800ms；psutil（giampaolo/psutil,
    BSD-3-Clause, 11.3k stars）可同进程直读同一内核计数器。

本件只回答两个问题，不做替换决定：
    Q1 耗时：同进程读 vs 子进程读，单次开销差多少（多轮取中位数）
    Q2 一致性：两通道在静置机上是否读到同一量级（判定阈 15pp，超阈只报
       DIVERGENT，不臆断谁对——两者语义本就不同：LoadPercentage 为瞬时值，
       psutil.cpu_percent(interval) 为区间均值）

三条闭门律
    1. psutil 缺失 -> verdict=PSUTIL-ABSENT，不假绿也不判负（未安装≠不可用）
    2. 子进程失败 -> SHELL-FAILED 显式点名 rc/stderr 首行，禁静默当作 0%
    3. 任一通道读到 None -> READ-UNAVAILABLE，禁止用 0 填充后参与比较

用法
    python Tools/cpu_read_ab_probe.py run [--rounds 5] [--json-out PATH]
    python Tools/cpu_read_ab_probe.py selftest
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

DIVERGE_PP = 15.0  # 一致性判定阈（百分点），仅用于分类，不作正确性判决


def _now():
    return time.perf_counter()


def read_shell_cpu():
    """PowerShell 子进程读 LoadPercentage。返回 (value, elapsed_s, err)。"""
    t0 = _now()
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001 - 通道失败要显式上报
        return None, _now() - t0, f"spawn-error: {type(exc).__name__}"
    elapsed = _now() - t0
    if p.returncode != 0:
        first = (p.stderr or "").strip().splitlines()[:1]
        return None, elapsed, f"rc={p.returncode} {first[0] if first else ''}".strip()
    raw = (p.stdout or "").strip()
    try:
        return float(raw), elapsed, None
    except ValueError:
        return None, elapsed, f"unparsable: {raw[:40]!r}"


def read_psutil_cpu(interval=1.0):
    """同进程读区间均值。返回 (value, elapsed_s, err)。"""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return None, 0.0, "psutil-absent"
    t0 = _now()
    try:
        val = psutil.cpu_percent(interval=interval)
    except Exception as exc:  # noqa: BLE001
        return None, _now() - t0, f"psutil-error: {type(exc).__name__}"
    return float(val), _now() - t0, None


def judge(shell_vals, ps_vals, shell_s, ps_s):
    """纯函数：分类判定，不碰 IO。"""
    def _clean(xs):
        return [x for x in xs if isinstance(x, (int, float))]

    sc, pc = _clean(shell_vals), _clean(ps_vals)
    if not sc or not pc:
        return {
            "verdict": "READ-UNAVAILABLE",
            "reason": "one channel produced no numeric reading",
            "shell_n": len(sc), "psutil_n": len(pc),
        }
    shell_med = statistics.median(sc)
    ps_med = statistics.median(pc)
    delta = abs(shell_med - ps_med)
    speedup = (statistics.median(shell_s) / statistics.median(ps_s)) if ps_s else None
    verdict = "CONSISTENT" if delta <= DIVERGE_PP else "DIVERGENT"
    return {
        "verdict": verdict,
        "shell_median_pct": round(shell_med, 2),
        "psutil_median_pct": round(ps_med, 2),
        "delta_pp": round(delta, 2),
        "threshold_pp": DIVERGE_PP,
        "shell_median_s": round(statistics.median(shell_s), 4),
        "psutil_median_s": round(statistics.median(ps_s), 4),
        "speedup_x": round(speedup, 1) if speedup is not None else None,
        "note": "delta 超阈只说明两通道语义不同或采样窗不同，不判定谁错",
    }


def cmd_run(args):
    if read_psutil_cpu(0)[2]:  # 探测可用性（不做计时）
        out = {"verdict": "PSUTIL-ABSENT", "reason": "psutil not installed; slice not testable here"}
        _emit(out, args.json_out)
        return 3

    shell_vals, ps_vals, shell_s, ps_s, errs = [], [], [], [], []
    for i in range(args.rounds):
        sv, st, se = read_shell_cpu()
        pv, pt, pe = read_psutil_cpu(interval=args.interval)
        if se:
            errs.append(f"round{i+1} shell: {se}")
        if pe:
            errs.append(f"round{i+1} psutil: {pe}")
        if sv is not None:
            shell_vals.append(sv)
            shell_s.append(st)
        if pv is not None:
            ps_vals.append(pv)
            ps_s.append(pt)

    out = judge(shell_vals, ps_vals, shell_s, ps_s)
    out.update({
        "rounds": args.rounds,
        "interval_s": args.interval,
        "shell_rows": [round(v, 2) for v in shell_vals],
        "psutil_rows": [round(v, 2) for v in ps_vals],
        "errors": errs,
        "rule": "overhead ratio only compares channel cost; shell includes process spawn",
    })
    if errs and not shell_vals:
        out["verdict"] = "SHELL-FAILED"
    _emit(out, args.json_out)
    return 0 if out["verdict"] in ("CONSISTENT", "DIVERGENT") else 4


def _emit(out, json_out):
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if json_out:
        with open(json_out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")


# ---------------- selftest（纯函数夹具，不依赖本机真读数） ----------------

def selftest():
    cases = []

    def ok(name, cond, detail=""):
        cases.append((name, bool(cond), detail))

    r = judge([50.0], [52.0], [0.8], [1.0])
    ok("S1 两通道同量级 -> CONSISTENT", r["verdict"] == "CONSISTENT", r["verdict"])
    ok("S2 加速比按中位数", r["speedup_x"] == 0.8, r.get("speedup_x"))

    r = judge([80.0], [10.0], [0.9], [1.0])
    ok("S3 超阈 -> DIVERGENT", r["verdict"] == "DIVERGENT", r["verdict"])
    ok("S4 超阈不臆断谁错", "谁错" in r["note"])

    r = judge([10.0, 10.0], [10.0, 10.0], [1.0, 1.0], [1.0, 1.0])
    ok("S5 恰好 0pp 差 -> CONSISTENT", r["verdict"] == "CONSISTENT")

    r = judge([10.0], [25.0], [1.0], [1.0])
    ok("S6 恰好等于阈 -> CONSISTENT（<= 边界）", r["verdict"] == "CONSISTENT", r["delta_pp"])

    r = judge([], [12.0], [1.0], [1.0])
    ok("S7 单通道无读数 -> READ-UNAVAILABLE", r["verdict"] == "READ-UNAVAILABLE")
    ok("S8 无读数不填 0 参与比较", r["shell_n"] == 0)

    r = judge([None, "x"], [1.0], [1.0], [1.0])
    ok("S9 非数值不参与中位数", r["verdict"] == "READ-UNAVAILABLE")

    r = judge([30.0, 90.0], [30.0, 90.0], [0.5, 1.5], [0.9, 1.1])
    ok("S10 中位数抗单点离群", r["verdict"] == "CONSISTENT")

    ok("S11 阈值常量单源", DIVERGE_PP == 15.0)

    passed = sum(1 for _, c, _ in cases if c)
    for name, c, detail in cases:
        print(f"  [{'PASS' if c else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not c else ""))
    print(f"selftest {passed}/{len(cases)} PASS")
    return 0 if passed == len(cases) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="CPU reading channel A/B probe")
    sub = ap.add_subparsers(dest="cmd")
    rp = sub.add_parser("run")
    rp.add_argument("--rounds", type=int, default=5)
    rp.add_argument("--interval", type=float, default=1.0)
    rp.add_argument("--json-out", default=None)
    sub.add_parser("selftest")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "run":
        return cmd_run(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())