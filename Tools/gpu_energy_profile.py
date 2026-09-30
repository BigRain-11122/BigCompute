#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gpu_energy_profile.py — GPU 能耗实测序列件（族 B 成本经济学增量·explore E39）

从 GPU-IdleWatch 采集器样本 (state/gpu-util/samples.jsonl, 15min tick) 的
power_w 序列做时序积分，出日均 kWh 实测面（估算口径=采样积分法）。

口径注记（诚实律·Q3 铁律同源）：
- power.draw = GPU 板卡功耗面，非整机 wall 功耗；整机电费要素须另配插线功耗计物理件。
- 电费结算唯一合法链 = 实测/估算 kWh × 实缴账单单价（单价禁事前编造）。
- 采样积分法 = 15min 分辨率梯形积分；gap > GAP_CAP 秒的区间跳过不计（防停机窗虚增）。

命令：
- report [--days N]  解析样本 → 日表+均值 → state/gpu-energy-profile-<ts>.json
- selftest           合成夹具回归（数学/跳gap/分日/分桶/确定性）
纯只读：不写样本、零 GPU 动作、零新采集。
"""
import argparse
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "state", "gpu-util", "samples.jsonl")
OUT_DIR = os.path.join(ROOT, "state")
MACHINE = "bm-a"          # C-02 分机行律：本采集面=bm-a 实测
GAP_CAP_S = 20 * 60       # 15min tick 的 1.33× 容差
UTIL_IDLE = 10.0          # idle/active 分桶阈值（%）

EST_NOTE = ("estimation: 15min sample trapezoid integral; power.draw=GPU board "
            "power (not wall); electricity cost = measured kWh x billed unit price "
            "only (Q3 iron rule: unit price source = paid utility bill)")


def parse_ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


def load_rows(path):
    rows = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    rows.append((parse_ts(r["ts"]), float(r["util_pct"]),
                                 float(r["power_w"])))
                except (KeyError, ValueError):
                    continue
    rows.sort(key=lambda x: x[0])
    return rows


def integrate(rows):
    """相邻样本梯形积分。返回 (kwh_total, per_day{kwh, idle, active},
    n_intervals, n_skipped, peak_w, span_s)"""
    per_day = {}
    total = idle_e = active_e = 0.0
    n_int = n_skip = 0
    peak = 0.0
    span = 0.0
    for a, b in zip(rows, rows[1:]):
        dts = (b[0] - a[0]).total_seconds()
        span += dts if dts <= GAP_CAP_S else 0
        peak = max(peak, a[2], b[2])
        if dts <= 0 or dts > GAP_CAP_S:
            n_skip += 1
            continue
        n_int += 1
        e = (a[2] + b[2]) / 2.0 * dts / 3600.0 / 1000.0   # kWh
        total += e
        util_avg = (a[1] + b[1]) / 2.0
        if util_avg < UTIL_IDLE:
            idle_e += e
        else:
            active_e += e
        mid = a[0] + dt.timedelta(seconds=dts / 2.0)
        d = per_day.setdefault(mid.strftime("%Y-%m-%d"),
                               {"kwh": 0.0, "idle_kwh": 0.0, "active_kwh": 0.0})
        d["kwh"] += e
        if util_avg < UTIL_IDLE:
            d["idle_kwh"] += e
        else:
            d["active_kwh"] += e
    return total, per_day, n_int, n_skip, peak, span


def report(path=SAMPLES, days=None, out=True):
    rows = load_rows(path)
    if len(rows) < 2:
        print("energy: no usable sample pairs (n=%d) — nothing to integrate" % len(rows))
        return None
    total, per_day, n_int, n_skip, peak, span = integrate(rows)
    n_days = len(per_day)
    idle_sum = idle_kwh(per_day)
    active_sum = active_kwh(per_day)
    mean_daily = total / n_days if n_days else 0.0
    if days:                      # 仅显示面过滤；总量/均值恒取全窗（口径一致律）
        keep = sorted(per_day)[-days:]
        per_day = {k: per_day[k] for k in keep}
    res = {
        "machine": MACHINE,
        "generated": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "note": EST_NOTE,
        "samples": len(rows),
        "intervals_used": n_int,
        "intervals_skipped_gap": n_skip,
        "span_hours": round(span / 3600.0, 2),
        "coverage": "%s -> %s" % (rows[0][0].isoformat(), rows[-1][0].isoformat()),
        "kwh_total": round(total, 4),
        "kwh_idle": round(idle_sum, 4),
        "kwh_active": round(active_sum, 4),
        "mean_daily_kwh": round(mean_daily, 4),
        "peak_power_w": round(peak, 2),
        "per_day": {k: {kk: round(vv, 4) for kk, vv in v.items()}
                    for k, v in sorted(per_day.items())},
    }
    if out:
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        op = os.path.join(OUT_DIR, "gpu-energy-profile-%s.json" % ts)
        with open(op, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("energy: machine=%s samples=%d intervals=%d skipped=%d" %
              (MACHINE, len(rows), n_int, n_skip))
        print("  total=%.4f kWh over %d days -> mean_daily=%.4f kWh "
              "(idle=%.4f active=%.4f peak=%.1fW)" %
              (total, n_days, mean_daily, res["kwh_idle"], res["kwh_active"], peak))
        print("  json=%s" % op)
        res["json_path"] = op
    return res


def idle_kwh(per_day):
    return sum(v["idle_kwh"] for v in per_day.values())


def active_kwh(per_day):
    return sum(v["active_kwh"] for v in per_day.values())


def _mk(tmp, rows):
    p = os.path.join(tmp, "samples.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for t, u, w in rows:
            f.write(json.dumps({"ts": t.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "util_pct": u, "mem_used_mib": 0.0,
                                 "power_w": w}) + "\n")
    return p


def selftest():
    import tempfile
    ok = 0
    base = dt.datetime(2026, 9, 28, 0, 0, 0)
    with tempfile.TemporaryDirectory() as tmp:
        # S1 矩形数学：100W × 4 样本（3 区间×15min）= 0.075 kWh
        p = _mk(tmp, [(base + dt.timedelta(minutes=15 * i), 50.0, 100.0)
                      for i in range(4)])
        r = integrate(load_rows(p))
        assert abs(r[0] - 0.075) < 1e-9, "S1 rect math"
        ok += 1
        # S2 梯形：0W→100W 单区间 = 0.0125 kWh
        p = _mk(tmp, [(base, 50.0, 0.0), (base + dt.timedelta(minutes=15), 50.0, 100.0)])
        r = integrate(load_rows(p))
        assert abs(r[0] - 0.0125) < 1e-9, "S2 trapezoid"
        ok += 1
        # S3 gap 超帽跳过：60min 间隔不计能
        p = _mk(tmp, [(base, 50.0, 100.0), (base + dt.timedelta(minutes=60), 50.0, 100.0)])
        r = integrate(load_rows(p))
        assert r[0] == 0.0 and r[3] == 1, "S3 gap cap"
        ok += 1
        # S4 idle/active 分桶：util<10% 归 idle
        p = _mk(tmp, [(base, 5.0, 50.0), (base + dt.timedelta(minutes=15), 5.0, 50.0),
                      (base + dt.timedelta(minutes=30), 80.0, 200.0),
                      (base + dt.timedelta(minutes=45), 80.0, 200.0)])
        total, per_day, *_ = integrate(load_rows(p))
        d = list(per_day.values())[0]
        assert abs(d["idle_kwh"] - 0.0125) < 1e-9, "S4 idle"
        # 斜坡区间 avg util 42.5% 归 active：0.03125 + 平坦 0.05 = 0.08125
        assert abs(d["active_kwh"] - 0.08125) < 1e-9, "S4 active"
        ok += 1
        # S5 分日：跨日区间归中点日（23:53→00:08 中点 00:00:30 → 09-29）
        p = _mk(tmp, [(base.replace(hour=23, minute=53), 50.0, 100.0),
                      (base.replace(hour=23, minute=53) + dt.timedelta(minutes=15), 50.0, 100.0)])
        _, per_day, *_ = integrate(load_rows(p))
        assert list(per_day)[0] == "2026-09-29", "S5 day bucket"
        ok += 1
        # S6 确定性：两跑一致
        p = _mk(tmp, [(base + dt.timedelta(minutes=15 * i), 30.0, 80.0 + i)
                      for i in range(10)])
        r1 = integrate(load_rows(p))
        r2 = integrate(load_rows(p))
        assert r1[0] == r2[0] and r1[1] == r2[1], "S6 determinism"
        ok += 1
        # S7 样本不足守门：单样本零积分不报错
        p = _mk(tmp, [(base, 10.0, 30.0)])
        assert report(path=p, out=False) is None, "S7 guard"
        ok += 1
    print("selftest: %d/7 PASS" % ok)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report", "selftest"])
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--samples", default=SAMPLES)
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    r = report(path=a.samples, days=a.days)
    return 0 if r else 1


if __name__ == "__main__":
    sys.exit(main())
