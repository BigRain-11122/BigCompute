#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""h3_power_sampler.py - passive 1s GPU power sampler for H3 local video run windows (E57).

Why: E39 gpu_energy_profile = 15min tick daily integration (too coarse for minute-level
video bursts); E46 inference_energy_anchor = self-driven Ollama burst (wrong shape: H3
runs are launched by OTHER windows - this tool only attaches read-only, zero dispatch,
zero VRAM allocation). Output = power.draw time series + segment means (cold-load vs
inference) so E56 R-20261009-h3-local-video-economics E_elec row can drop the 242W
power.limit upper-bound proxy for measured means (cold/warm run banding = separate
labeled runs).

Calibers: power.draw = GPU board power, not whole-machine wall (E39/E46 same rule);
electricity cost = measured kWh x real paid bill unit price (Q3 iron rule, no invented
price). Attach discipline (E57 pre-registered): run only during another window's H3
run; never starts/stops any workload.

Usage:
  sample [--duration N] [--interval 1.0] [--label L] [--out FILE]   N=0 -> until Ctrl-C
  report --file F [--infer-util 40]
  selftest
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE = REPO / "state"
CALIBER = ("power.draw=GPU board power not wall; electricity cost = measured kWh x "
           "real paid bill unit price (Q3 iron rule, price never invented)")
E_PER_CLIP_NOTE = ("kwh_total = whole-window GPU board energy; valid as E_elec/clip only "
                   "when sampling spans exactly one clip run (attach at launch, stop at done)")


def query_gpu():
    """One nvidia-smi probe -> (power_w, util_pct, mem_mib) or None."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw,utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        p = [x.strip() for x in out.stdout.strip().splitlines()[0].split(",")]
        return float(p[0]), float(p[1]), float(p[2])
    except Exception:
        return None


def trapezoid_wh(samples):
    """samples=[(t_s, W)...] -> Wh trapezoid integral (W*s/3600)."""
    if len(samples) < 2:
        return 0.0
    wh = 0.0
    for (t0, w0), (t1, w1) in zip(samples, samples[1:]):
        wh += 0.5 * (w0 + w1) * (t1 - t0) / 3600.0
    return wh


def parse_rows(path):
    """JSONL rows {t,w,u,m} -> list; bad lines skipped, rows sorted by t."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            rows.append((float(r["t"]), float(r["w"]), float(r["u"]), float(r["m"])))
        except Exception:
            continue
    rows.sort(key=lambda x: x[0])
    return rows


def classify_segments(rows, infer_util):
    """Partition rows into (cold_load, inference, tail).

    cold_load = rows before first row with util >= infer_util;
    inference = rows from first to last such row (inclusive);
    tail = rows after last such row. No qualifying row -> all rows = cold_load."""
    idx = [i for i, r in enumerate(rows) if r[2] >= infer_util]
    if not idx:
        return rows, [], []
    first, last = idx[0], idx[-1]
    return rows[:first], rows[first:last + 1], rows[last + 1:]


def seg_stats(seg):
    """One segment -> {n, span_s, mean_w, max_w, kwh}; empty -> None."""
    if not seg:
        return None
    ws = [r[1] for r in seg]
    span = seg[-1][0] - seg[0][0]
    return {"n": len(seg), "span_s": round(span, 2), "mean_w": round(sum(ws) / len(ws), 2),
            "max_w": round(max(ws), 2), "kwh": round(trapezoid_wh([(r[0], r[1]) for r in seg]) / 1000.0, 8)}


def build_record(rows, infer_util, source, now=None):
    load, infer, tail = classify_segments(rows, infer_util)
    total = seg_stats(rows) or {"n": 0, "span_s": 0.0, "mean_w": None, "max_w": None, "kwh": 0.0}
    rec = {"tool": "h3_power_sampler", "ts": now or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
           "machine": "bm-a", "source_file": str(source), "rows_n": len(rows),
           "infer_util_threshold": infer_util, "caliber_note": CALIBER,
           "e_per_clip_note": E_PER_CLIP_NOTE,
           "seg_cold_load": seg_stats(load), "seg_inference": seg_stats(infer),
           "seg_tail": seg_stats(tail), "span_total_s": total["span_s"],
           "kwh_total": total["kwh"], "peak_w": total["max_w"]}
    return rec


def cmd_sample(args):
    STATE.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else STATE / ("h3-power-%s-%s.jsonl" % (
        args.label, datetime.now().strftime("%Y%m%d-%H%M%S")))
    n, t_end = 0, (time.time() + args.duration) if args.duration > 0 else None
    try:
        with out.open("a", encoding="utf-8") as fh:
            while t_end is None or time.time() < t_end:
                t0 = time.time()
                q = query_gpu()
                if q:
                    fh.write(json.dumps({"t": round(t0, 3), "w": q[0], "u": q[1], "m": q[2]}) + "\n")
                    fh.flush()
                    n += 1
                time.sleep(max(0.0, args.interval - (time.time() - t0)))
    except KeyboardInterrupt:
        print("VERDICT=SAMPLE-PARTIAL rows=%d" % n)
        print("OUT=%s" % out, flush=True)
        return 0
    print("VERDICT=SAMPLE-OK rows=%d" % n)
    print("OUT=%s" % out, flush=True)
    return 0 if n else 3


def cmd_report(args):
    rows = parse_rows(args.file)
    if not rows:
        print("VERDICT=NO-ROWS exit=2")
        return 2
    rec = build_record(rows, args.infer_util, args.file)
    STATE.mkdir(exist_ok=True)
    out = STATE / ("h3-power-report-%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    for key, name in (("seg_cold_load", "COLD-LOAD"), ("seg_inference", "INFERENCE"), ("seg_tail", "TAIL")):
        s = rec[key]
        if s:
            print("%s n=%d span=%.1fs mean=%.1fW max=%.1fW kwh=%.8f"
                  % (name, s["n"], s["span_s"], s["mean_w"], s["max_w"], s["kwh"]))
        else:
            print("%s n=0 (none)" % name)
    print("TOTAL rows=%d span=%.1fs kwh=%.8f peak=%.1fW"
          % (rec["rows_n"], rec["span_total_s"], rec["kwh_total"], rec["peak_w"] or 0.0))
    print("REPORT=%s" % out, flush=True)
    return 0


def selftest():
    """Synthetic fixtures, zero GPU, zero real-state writes. J1-J6."""
    import tempfile
    ok = 0
    # J1 trapezoid: 60W x 60s = 1Wh; ramp 0->120W x 60s = 1Wh
    assert abs(trapezoid_wh([(i, 60.0) for i in range(61)]) - 1.0) < 1e-9, "J1"
    assert abs(trapezoid_wh([(i, 2.0 * i) for i in range(61)]) - 1.0) < 1e-9, "J1b"
    ok += 2
    # J2 classify: 3 load (u=5) + 4 infer (u=95) + 2 tail (u=3)
    rows = [(float(i), 50.0 + i, 5.0 if i < 3 else (95.0 if i < 7 else 3.0), 100.0) for i in range(9)]
    load, infer, tail = classify_segments(rows, 40.0)
    assert (len(load), len(infer), len(tail)) == (3, 4, 2), "J2"
    assert load + infer + tail == rows, "J2b partition"
    # J2c no qualifying row -> all cold_load
    l2, i2, t2 = classify_segments(rows[:3], 40.0)
    assert (len(l2), len(i2), len(t2)) == (3, 0, 0), "J2c"
    ok += 3
    # J3 seg stats math: mean/max/span/kwh on 4x 100W @ 1s
    s = seg_stats([(float(i), 100.0, 95.0, 100.0) for i in range(4)])
    assert s["n"] == 4 and abs(s["span_s"] - 3.0) < 1e-9 and abs(s["mean_w"] - 100.0) < 1e-9, "J3"
    assert abs(s["kwh"] - round(300.0 / 3600.0 / 1000.0, 8)) < 1e-12, "J3b kwh"
    ok += 2
    # J4 parser robustness: bad line skipped, good line parsed
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "s.jsonl"
        f.write_text('{"t":1.0,"w":60.0,"u":5.0,"m":100.0}\nnot-json\n\n'
                     '{"t":2.0,"w":70.0,"u":95.0,"m":200.0}\n', encoding="utf-8")
        pr = parse_rows(f)
        assert len(pr) == 2 and pr[0][1] == 60.0 and pr[-1][2] == 95.0, "J4"
        ok += 1
        # J5 empty -> NO-ROWS semantics (report exit path)
        e = Path(td) / "e.jsonl"
        e.write_text("", encoding="utf-8")
        assert parse_rows(e) == [], "J5"
        ok += 1
        # J6 determinism: same rows -> identical record modulo ts
        r1 = build_record(rows, 40.0, "fixture", now="T")
        r2 = build_record(rows, 40.0, "fixture", now="T")
        assert r1 == r2, "J6"
        ok += 1
    print("SELFTEST %d/10 PASS (J1 x2, J2 x3, J3 x2, J4, J5, J6)" % ok)
    return 0


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="passive 1s power sampler for H3 run windows (E57)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--duration", type=float, default=0)
    s.add_argument("--interval", type=float, default=1.0)
    s.add_argument("--label", default="run")
    s.add_argument("--out", default=None)
    r = sub.add_parser("report")
    r.add_argument("--file", required=True)
    r.add_argument("--infer-util", type=float, default=40.0)
    sub.add_parser("selftest")
    args = ap.parse_args()
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "sample":
        return cmd_sample(args)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
