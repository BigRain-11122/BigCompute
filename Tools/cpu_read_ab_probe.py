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
# M118 预注册判据（跑之前固定，禁事后调参）
SPAWN_PERTURB_PP = 5.0   # spawn 期间 psutil 抬升中位数 >=5pp 判「采样受自身干扰」
SEMANTIC_TOL_PP = 8.0    # 语义臂容差：实测值与该语义预测值差 <=8pp 视为命中
SEP_PP = 20.0            # 可分辨距离：满载臂双通道差 >=20pp 才允许判语义


def _now():
    return time.perf_counter()


def read_shell_cpu(cmd_text=None):
    """PowerShell 子进程读 LoadPercentage。返回 (value, elapsed_s, err)。

    cmd_text 可覆盖查询语句（M118 mock 臂／干扰臂用），默认=真 CIM 查询。
    """
    t0 = _now()
    question = cmd_text or ("(Get-CimInstance Win32_Processor | "
                            "Measure-Object -Property LoadPercentage -Average).Average")
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command", question,
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


def busy_spin(cores):
    """在进程内制造可控负载：cores 条线程各占满一个核（psutil/WMI 都看得到 CPU 时间）。

    返回 (threads, stop_event)。调用方必须 stop.set()。
    """
    import threading

    stop = threading.Event()

    def _burn():
        x = 0
        while not stop.is_set():
            for i in range(20000):
                x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        return x

    threads = []
    for _ in range(int(cores)):
        t = threading.Thread(target=_burn, daemon=True)
        t.start()
        threads.append(t)
    return threads, stop


def judge_semantic(idle, loaded, ncpu):
    """M118：判定 LoadPercentage 的语义是「逐核最大」还是「全核平均」。

    预测（预注册，跑之前写死）：
      PER-CORE-MAX ：1 核满载时 shell≈100，psutil≈100/ncpu，两者差 >=SEP_PP
      PER-CORE-MEAN：两者都≈100/ncpu，差 <SEP_PP
    可分辨性前置：满载臂双通道差 <SEP_PP 时禁判语义（数据没分辨力）=INCONCLUSIVE
    """
    if not isinstance(ncpu, int) or ncpu < 1:
        return {"verdict": "INCONCLUSIVE", "reason": "ncpu unknown"}
    smax = loaded.get("shell_median_pct")
    pmax = loaded.get("psutil_median_pct")
    if smax is None or pmax is None:
        return {"verdict": "INCONCLUSIVE", "reason": "loaded arm unreadable"}
    spread = abs(smax - pmax)
    pred_mean = 100.0 / ncpu
    if spread < SEP_PP:
        return {
            "verdict": "INCONCLUSIVE",
            "reason": "channels too close on the loaded arm; semantics not separable",
            "loaded_spread_pp": round(spread, 2),
            "separation_need_pp": SEP_PP,
            "ncpu": ncpu,
        }
    hit_max = abs(smax - 100.0) <= SEMANTIC_TOL_PP and abs(pmax - pred_mean) <= SEMANTIC_TOL_PP
    hit_mean = abs(smax - pred_mean) <= SEMANTIC_TOL_PP and abs(pmax - pred_mean) <= SEMANTIC_TOL_PP
    if hit_max and not hit_mean:
        verdict, mapping = "PER-CORE-MAX", "REQUIRED"
    elif hit_mean:
        verdict, mapping = "PER-CORE-MEAN", "NOT-REQUIRED"
    else:
        verdict, mapping = "INCONCLUSIVE", "UNDETERMINED"
    return {
        "verdict": verdict,
        "mapping": mapping,
        "ncpu": ncpu,
        "loaded_shell_pct": smax,
        "loaded_psutil_pct": pmax,
        "loaded_spread_pp": round(spread, 2),
        "predicted_mean_pct": round(pred_mean, 2),
        "idle_delta_pp": idle.get("delta_pp"),
        "rule": "shell 为逐核最大语义时，与全核平均跨机不可比 -> 阈值必须点名通道",
    }


def judge_spawn_perturb(pre_vals, post_vals, elapsed_s):
    """M118：spawn 自身是否污染读数（预注册阈值 SPAWN_PERTURB_PP）。"""
    diffs = [p - q for p, q in zip(post_vals, pre_vals)
             if isinstance(p, (int, float)) and isinstance(q, (int, float))]
    if not diffs:
        return {"verdict": "READ-UNAVAILABLE", "reason": "no paired psutil readings"}
    med = statistics.median(diffs)
    verdict = "SPAWN-PERTURB-MATERIAL" if med >= SPAWN_PERTURB_PP else "SPAWN-PERTURB-NEGLIGIBLE"
    return {
        "verdict": verdict,
        "median_delta_pp": round(med, 2),
        "threshold_pp": SPAWN_PERTURB_PP,
        "rounds": len(diffs),
        "spawn_median_s": round(statistics.median(elapsed_s), 4) if elapsed_s else None,
        "note": "此臂只测 spawn 自身；判 MATERIAL 则采样必须先于/脱离 spawn 窗口",
    }


def judge_semantic_motion(low, high, ncpu, k_low, k_high):
    """M118 第二臂（2026-11-04 本轮预注册，跑之前写死）。

    第一臂（单核 vs 静置）在共租户机器上不可分辨（ambient 已 >1/ncpu）。
    本臂不比绝对值，比「加更多核时读数动不动」：
      PER-CORE-MAX  ：shell 一旦有一核饱和就到顶，加核不再变化（motion≈0）
                     psutil 必须继续上行（motion≈100*(k_high-k_low)/ncpu）
      PER-CORE-MEAN ：两者都继续上行，且幅度同量级
    可分辨前置：psutil 侧运动量 >=SEP_PP，否则数据没分辨力。
    """
    s_lo, s_hi = low.get("shell_median_pct"), high.get("shell_median_pct")
    p_lo, p_hi = low.get("psutil_median_pct"), high.get("psutil_median_pct")
    if None in (s_lo, s_hi, p_lo, p_hi) or not isinstance(ncpu, int) or ncpu < 1:
        return {"verdict": "INCONCLUSIVE", "reason": "arm unreadable or ncpu unknown"}
    ds, dp = s_hi - s_lo, p_hi - p_lo
    scale = 100.0 * (k_high - k_low) / ncpu
    base = {
        "ncpu": ncpu, "k_low": k_low, "k_high": k_high,
        "shell_motion_pp": round(ds, 2), "psutil_motion_pp": round(dp, 2),
        "expected_scale_pp": round(scale, 2),
        "separation_need_pp": SEP_PP, "semantic_tol_pp": SEMANTIC_TOL_PP,
    }
    if dp < SEP_PP:
        base.update({"verdict": "INCONCLUSIVE", "mapping": "UNDETERMINED",
                     "reason": "psutil did not move enough; no discrimination power"})
        return base
    if abs(ds) <= SEMANTIC_TOL_PP and abs(dp - scale) <= SEMANTIC_TOL_PP:
        base.update({"verdict": "PER-CORE-MAX", "mapping": "REQUIRED",
                     "rule": "shell 对加核不敏感=已看单核最大；与全核平均不可比"})
    elif abs(ds - scale) <= SEMANTIC_TOL_PP and abs(dp - scale) <= SEMANTIC_TOL_PP:
        base.update({"verdict": "PER-CORE-MEAN", "mapping": "NOT-REQUIRED",
                     "rule": "两通道随加核等比上行=语义一致"})
    else:
        base.update({"verdict": "INCONCLUSIVE", "mapping": "UNDETERMINED",
                     "reason": "motions match neither prediction"})
    return base


def cmd_semantic2(args):
    """M118 第二臂：两个负载档（k_low/k_high）各采一轮，比运动量。"""
    import psutil as _ps

    ncpu = _ps.cpu_count(logical=True)
    arms = {}
    errs = []
    for k in (args.busy_low, args.busy_high):
        sv, pv, ss, pp_ = [], [], [], []
        errs += _sample(sv, pv, ss, pp_, args.rounds, args.interval,
                        busy_cores=k, warmup_s=args.warmup)
        arms[f"k{k}"] = judge(sv, pv, ss, pp_)
    decision = judge_semantic_motion(arms[f"k{args.busy_low}"], arms[f"k{args.busy_high}"],
                                     ncpu, args.busy_low, args.busy_high)
    out = {
        "arm": "motion",
        "verdict": decision["verdict"],
        "mapping": decision.get("mapping"),
        "decision": decision,
        "arms": arms,
        "errors": errs,
        "prereg": {"semantic_tol_pp": SEMANTIC_TOL_PP, "separation_pp": SEP_PP,
                   "spawn_perturb_pp": SPAWN_PERTURB_PP,
                   "registered_at": "2026-11-04 this round, before the run"},
    }
    _emit(out, args.json_out)
    return 0 if decision["verdict"] != "INCONCLUSIVE" else 4


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


def _sample(shell_vals, ps_vals, shell_s, ps_s, rounds, interval, cmd_text=None,
            busy_cores=0, warmup_s=0.0):
    """跑 rounds 次双通道同帧采样；busy_cores>0 时进程内先起合成负载。"""
    stop = None
    threads = []
    if busy_cores:
        threads, stop = busy_spin(busy_cores)
        if warmup_s:
            time.sleep(warmup_s)  # 让负载进入区间均值
    errs = []
    try:
        for i in range(rounds):
            sv, st, se = read_shell_cpu(cmd_text=cmd_text)
            pv, pt, pe = read_psutil_cpu(interval=interval)
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
    finally:
        if stop is not None:
            stop.set()
            for t in threads:
                t.join(timeout=1.0)
    return errs


def cmd_perturb(args):
    """M118 干扰臂：spawn 自身是否把读数抬起来（成对前/后取差）。"""
    import psutil as _ps
    _ps.cpu_percent(0)  # 预热：首调只返回 0 无意义
    pre_vals, post_vals, elapsed = [], [], []
    for _ in range(args.rounds):
        pre = _ps.cpu_percent(0)
        _v, st, _e = read_shell_cpu()
        post = _ps.cpu_percent(0)
        pre_vals.append(pre)
        post_vals.append(post)
        elapsed.append(st)
    out = judge_spawn_perturb(pre_vals, post_vals, elapsed)
    out.update({
        "ncpu": _ps.cpu_count(logical=True),
        "pre_rows": [round(v, 2) for v in pre_vals],
        "post_rows": [round(v, 2) for v in post_vals],
        "comment": "pre/post 都是本进程 cpu_percent 差分读数（0=自上次调用以来）",
    })
    _emit(out, args.json_out)
    return 0 if out["verdict"] in ("SPAWN-PERTURB-MATERIAL", "SPAWN-PERTURB-NEGLIGIBLE") else 4


def cmd_semantic(args):
    """M118 语义臂：静置 + 单核满载两次采样，判定逐核最大 vs 全核平均。"""
    import psutil as _ps

    ncpu = _ps.cpu_count(logical=True)

    idle_shell, idle_ps, idle_s, idle_ps_s = [], [], [], []
    idle_errs = _sample(idle_shell, idle_ps, idle_s, idle_ps_s,
                        args.rounds, args.interval)
    idle = judge(idle_shell, idle_ps, idle_s, idle_ps_s)

    load_shell, load_ps, load_s, load_ps_s = [], [], [], []
    load_errs = _sample(load_shell, load_ps, load_s, load_ps_s,
                        args.rounds, args.interval,
                        busy_cores=args.busy_cores, warmup_s=args.warmup)
    loaded = judge(load_shell, load_ps, load_s, load_ps_s)

    verdict = judge_semantic(idle, loaded, ncpu)
    out = {
        "verdict": verdict["verdict"],
        "mapping": verdict.get("mapping"),
        "ncpu": ncpu,
        "busy_cores": args.busy_cores,
        "idle_arm": idle,
        "loaded_arm": loaded,
        "decision": verdict,
        "errors": idle_errs + load_errs,
        "prereg": {
            "semantic_tol_pp": SEMANTIC_TOL_PP,
            "separation_pp": SEP_PP,
            "spawn_perturb_pp": SPAWN_PERTURB_PP,
        },
    }
    _emit(out, args.json_out)
    return 0 if verdict["verdict"] != "INCONCLUSIVE" else 4


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

    # ---- M118 语义臂/干扰臂（纯函数夹具，不跑真实负载）----
    idle = {"delta_pp": 43.4}
    r = judge_semantic(idle, {"shell_median_pct": 99.0, "psutil_median_pct": 6.2}, 16)
    ok("S12 单核满载 shell≈100/psutil≈1/n -> PER-CORE-MAX",
       r["verdict"] == "PER-CORE-MAX" and r["mapping"] == "REQUIRED", r["verdict"])

    r = judge_semantic(idle, {"shell_median_pct": 6.5, "psutil_median_pct": 6.2}, 16)
    ok("S13 双臂都≈1/n 且分不开 -> INCONCLUSIVE 禁判语义",
       r["verdict"] == "INCONCLUSIVE", r["verdict"])

    r = judge_semantic(idle, {"shell_median_pct": 60.0, "psutil_median_pct": 6.2}, 16)
    ok("S14 分离够但都不命中预测 -> INCONCLUSIVE/UNDETERMINED",
       r["verdict"] == "INCONCLUSIVE" and r["mapping"] == "UNDETERMINED", r["verdict"])

    r = judge_semantic(idle, {"shell_median_pct": 6.5, "psutil_median_pct": 6.2}, 16)
    ok("S15 不过分离闸就报缺口不报结论", r.get("separation_need_pp") == SEP_PP, r.get("separation_need_pp"))

    r = judge_semantic(idle, {"shell_median_pct": 50.0, "psutil_median_pct": 45.0}, 0)
    ok("S16 ncpu 未知 -> INCONCLUSIVE 不猜", r["verdict"] == "INCONCLUSIVE", r["verdict"])

    r = judge_spawn_perturb([10.0, 10.0, 10.0], [20.0, 20.0, 20.0], [0.5, 0.5, 0.5])
    ok("S17 spawn 抬升 10pp -> MATERIAL", r["verdict"] == "SPAWN-PERTURB-MATERIAL", r["verdict"])
    r = judge_spawn_perturb([10.0, 10.0], [12.0, 13.0], [0.5, 0.5])
    ok("S18 spawn 抬升 2.5pp -> NEGLIGIBLE", r["verdict"] == "SPAWN-PERTURB-NEGLIGIBLE", r["verdict"])
    r = judge_spawn_perturb([], [], [])
    ok("S19 无配对读数 -> READ-UNAVAILABLE 不用 0 填", r["verdict"] == "READ-UNAVAILABLE", r["verdict"])

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
    pp = sub.add_parser("perturb", help="M118 干扰臂：spawn 自身是否抬读数")
    pp.add_argument("--rounds", type=int, default=20)
    pp.add_argument("--json-out", default=None)
    sp = sub.add_parser("semantic", help="M118 语义臂：逐核最大 vs 全核平均")
    sp.add_argument("--rounds", type=int, default=8)
    sp.add_argument("--interval", type=float, default=1.0)
    sp.add_argument("--busy-cores", type=int, default=1)
    sp.add_argument("--warmup", type=float, default=1.5)
    sp.add_argument("--json-out", default=None)
    s2 = sub.add_parser("semantic2", help="M118 第二臂：两负载档运动量判语义")
    s2.add_argument("--rounds", type=int, default=6)
    s2.add_argument("--interval", type=float, default=1.0)
    s2.add_argument("--busy-low", type=int, default=4)
    s2.add_argument("--busy-high", type=int, default=16)
    s2.add_argument("--warmup", type=float, default=1.5)
    s2.add_argument("--json-out", default=None)
    sub.add_parser("selftest")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "perturb":
        return cmd_perturb(args)
    if args.cmd == "semantic":
        return cmd_semantic(args)
    if args.cmd == "semantic2":
        return cmd_semantic2(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())