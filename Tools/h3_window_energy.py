#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""h3_window_energy.py — E57 H3 i2v 功耗采样窗能量积分器（族 B 增量·窗毕一命令出数）

消费 Tools/h3_power_sampler.py 的 1s JSONL（{"t": epoch_s, "w": W, "u": util%, "m": MiB}）：
梯形积分 → kWh 总量/活跃/安静三拆 + 断档检测 + 安静带日基线对照 delta（E62 方法论同源）。
窗毕回填面=state/gpu-energy-intensity-registry H3 行实测升级 + 月末单 S6 增量 + 借算计价例。

用法:
  python Tools/h3_window_energy.py report --jsonl state/h3-power-<label>.jsonl [--out state/x.json]
  python Tools/h3_window_energy.py selftest
预注册判据（J1-J3）:
  J1 只读积分零显存动作零 GPU 调用；J2 数值仅出自 jsonl 实测样本（断档>20s 段剔除并计数）；
  J3 delta 对照=口径 A 观察面（quiet_daily 默认 0.8063=E62 安静周基线·非结算面）。
"""
import argparse
import json
import os
import sys
import tempfile

QUIET_DAILY_KWH_DEFAULT = 0.8063  # E62 安静周基线（kWh/日·口径 A 观察对照）


def parse_jsonl(path):
    rows, bad = [], 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                if "t" in o and "w" in o:
                    rows.append((float(o["t"]), float(o["w"]), float(o.get("u", 0.0)), float(o.get("m", 0.0))))
            except (ValueError, KeyError, TypeError):
                bad += 1
    rows.sort(key=lambda r: r[0])
    return rows, bad


def integrate(rows, active_util):
    """梯形积分：返回 (kwh_total, kwh_active, kwh_idle, gaps, max_gap_s, span_s, stats)"""
    gaps, max_gap = 0, 0.0
    ws_total = ws_act = ws_idle = 0.0
    n_act = n_idle = 0
    peak = 0.0
    mem_min, mem_max = float("inf"), 0.0
    for i in range(len(rows) - 1):
        t0, w0, u0, m0 = rows[i]
        t1, w1, u1, m1 = rows[i + 1]
        dt = t1 - t0
        if dt <= 0:
            continue
        if dt > 20.0:  # J2 断档段剔除（>20s 不积分·只计数）
            gaps += 1
            max_gap = max(max_gap, dt)
            continue
        seg = (w0 + w1) / 2.0 * dt  # W·s
        ws_total += seg
        if u0 >= active_util:
            ws_act += seg
            n_act += 1
        else:
            ws_idle += seg
            n_idle += 1
        peak = max(peak, w0, w1)
        mem_min, mem_max = min(mem_min, m0, m1), max(mem_max, m0, m1)
    if rows:
        last = rows[-1]
        peak = max(peak, last[1])
        mem_min = min(mem_min, last[3]) if mem_min != float("inf") else last[3]
        mem_max = max(mem_max, last[3])
        span_s = rows[-1][0] - rows[0][0] + 1.0  # 首尾样本各代表 1s
    else:
        span_s = 0.0
    kwh = lambda ws: ws / 3600.0 / 1000.0
    stats = {
        "samples": len(rows),
        "mean_w": (sum(r[1] for r in rows) / len(rows)) if rows else 0.0,
        "peak_w": peak,
        "mem_mib_min": mem_min if mem_min != float("inf") else 0.0,
        "mem_mib_max": mem_max,
        "seg_active": n_act,
        "seg_idle": n_idle,
    }
    return kwh(ws_total), kwh(ws_act), kwh(ws_idle), gaps, max_gap, span_s, stats


def cmd_report(args):
    rows, bad = parse_jsonl(args.jsonl)
    if len(rows) < 2:
        print("REPORT-FAIL: <2 samples")
        return 1
    kt, ka, ki, gaps, max_gap, span_s, st = integrate(rows, args.active_util)
    hours = span_s / 3600.0
    quiet_exp = args.quiet_daily / 24.0 * hours
    out = {
        "tool": "h3_window_energy.py",
        "source": args.jsonl,
        "span_hours": round(hours, 4),
        "kwh_total": round(kt, 6),
        "kwh_active": round(ka, 6),
        "kwh_idle_band": round(ki, 6),
        "mean_w": round(st["mean_w"], 2),
        "peak_w": round(st["peak_w"], 2),
        "mem_mib": [round(st["mem_mib_min"], 0), round(st["mem_mib_max"], 0)],
        "gaps_gt20s": gaps,
        "max_gap_s": round(max_gap, 1),
        "bad_lines": bad,
        "quiet_expected_kwh": round(quiet_exp, 6),
        "delta_vs_quiet_kwh": round(kt - quiet_exp, 6),
        "quiet_daily_baseline": args.quiet_daily,
        "caliber_note": "口径 A 观察对照·非结算；delta 方法论=E62 借用窗同源",
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("WROTE %s" % args.out)
    return 0


def cmd_selftest(_args):
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "fx.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"t": 100.0, "w": 100.0, "u": 50.0, "m": 1000.0}\n')
            f.write('{"t": 101.0, "w": 100.0, "u": 10.0, "m": 1000.0}\n')
            f.write('{"t": 111.0, "w": 100.0, "u": 10.0, "m": 1000.0}\n')  # 10s idle 段
            f.write('not json\n')
        rows, bad = parse_jsonl(p)
        assert len(rows) == 3 and bad == 1, "parse fail"
        kt, ka, ki, gaps, max_gap, span_s, st = integrate(rows, 30)
        # 段分类按左样本 u0：段1(1s·u0=50→active)100Ws + 段2(10s·u0=10→idle)1000Ws = 1100 Ws
        expect = 1100.0 / 3600.0 / 1000.0
        assert abs(kt - expect) < 1e-9, "kwh math %r" % kt
        assert abs(ka - 100.0 / 3600.0 / 1000.0) < 1e-12, "active split"
        assert abs(ki - 1000.0 / 3600.0 / 1000.0) < 1e-12, "idle split"
        assert gaps == 0 and abs(span_s - 12.0) < 1e-9, "span/gap"
        assert st["peak_w"] == 100.0, "peak"
        # 断档剔除分支
        with open(p, "a", encoding="utf-8") as f:
            f.write('{"t": 200.0, "w": 100.0, "u": 50.0, "m": 1000.0}\n')
        rows, _ = parse_jsonl(p)
        kt2, _, _, gaps2, max_gap2, _, _ = integrate(rows, 30)
        assert gaps2 == 1 and abs(max_gap2 - 89.0) < 1e-9, "gap branch"
        assert abs(kt2 - kt) < 1e-12, "gap seg excluded"
    print("SELFTEST PASS 6/6")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--jsonl", required=True)
    r.add_argument("--out")
    r.add_argument("--active-util", type=float, default=30.0)
    r.add_argument("--quiet-daily", type=float, default=QUIET_DAILY_KWH_DEFAULT)
    r.set_defaults(fn=cmd_report)
    s = sub.add_parser("selftest")
    s.set_defaults(fn=cmd_selftest)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
