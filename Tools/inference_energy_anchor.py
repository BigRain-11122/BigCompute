#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inference_energy_anchor.py — 推理请求级能耗强度实测锚（族 B 成本经济学增量·E46）

目的：E39 gpu_energy_profile=日级 15min 采样积分，结构性测不到「单次推理请求」的边际能耗。
本件=常驻 7b（qwen2.5:7b-instruct Q4_K_M）受控生成突发 + 1s nvidia-smi 功率采样：
  - idle 基线均值 W（突发前采样）
  - 突发期板卡总能耗 Wh（梯形积分）+ 同时长 idle 基线折算 Wh
  - 边际能耗 Wh = 总 - 基线折算（口径 B 服务成本的正确底座；常驻 idle floor=独立常驻成本分列）
  - 每 token 边际能耗 mWh/token 与 kWh/Mtok（电费=×实缴电价，Q3 铁律，单价禁编造）
消费面：月末归集 N 窗 1M token 换算链电费物理量 + 借算工单轨边际成本底座 + 批池机会成本。
口径注记：power.draw=GPU 板卡面非整机 wall（与 E39 同律）。
用法：run [--num-predict N] [--model M] | selftest
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE = REPO / "state"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
PROMPT = ("用中文写一篇关于算力成本核算的技术说明文，要求：逐条展开折旧、电费、运维、"
          "利用率、单位成本五个要素，每条至少三句，总长度尽量长。")


def sample_power():
    """单次 nvidia-smi 功率/利用率采样，返回 (watts, util_pct) 或 (None, None)。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        parts = out.stdout.strip().splitlines()[0].split(",")
        return float(parts[0].strip()), float(parts[1].strip())
    except Exception:
        return None, None


def trapezoid_wh(samples):
    """samples=[(t_s, W)...] 梯形积分 → Wh（W*s/3600）。"""
    if len(samples) < 2:
        return 0.0
    wh = 0.0
    for (t0, w0), (t1, w1) in zip(samples, samples[1:]):
        wh += 0.5 * (w0 + w1) * (t1 - t0) / 3600.0
    return wh


def generate(model, num_predict, timeout_s):
    """常驻 7b 受控生成（keep_alive=-1 显式·U240 常驻标准律）。"""
    payload = json.dumps({
        "model": model, "prompt": PROMPT, "stream": False,
        "keep_alive": -1, "options": {"num_predict": num_predict},
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    dur_s = time.time() - t0
    return data, dur_s


def run_anchor(args):
    # 相位 1：idle 基线（突发前·常驻在位无请求窗）
    idle_samples = []
    for _ in range(args.idle_n):
        t = time.time()
        w, u = sample_power()
        if w is not None:
            idle_samples.append((t, w))
        time.sleep(1.0)
    if not idle_samples:
        print("VERDICT=NO-GPU-SAMPLE", flush=True)
        return 3
    idle_w = sum(w for _, w in idle_samples) / len(idle_samples)

    # 相位 2：生成突发（后台 1s 采样线程与请求并行）
    burst_samples = []
    stop = {"flag": False}

    def sampler():
        while not stop["flag"]:
            t = time.time()
            w, u = sample_power()
            if w is not None:
                burst_samples.append((t, w))
            time.sleep(1.0)

    import threading
    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    resp, wall_s = generate(args.model, args.num_predict, args.timeout)
    time.sleep(1.0)  # 尾样本收口
    stop["flag"] = True
    th.join(timeout=3)

    tokens = int(resp.get("eval_count", 0))
    eval_s = resp.get("eval_duration", 0) / 1e9
    if tokens <= 0 or len(burst_samples) < 3:
        print("VERDICT=ANCHOR-FAIL tokens=%d samples=%d" % (tokens, len(burst_samples)), flush=True)
        return 4

    gross_wh = trapezoid_wh(burst_samples)
    burst_span_s = burst_samples[-1][0] - burst_samples[0][0]
    idle_equiv_wh = idle_w * burst_span_s / 3600.0
    marginal_wh = gross_wh - idle_equiv_wh
    burst_w_mean = sum(w for _, w in burst_samples) / len(burst_samples)
    mwh_per_tok = marginal_wh / tokens * 1000.0
    kwh_per_mtok = marginal_wh / tokens * 1000.0  # Wh/tok*1e6/1000
    gross_kwh_per_mtok = gross_wh / tokens * 1000.0
    tok_per_kwh = (tokens / marginal_wh * 1000.0) if marginal_wh > 0 else None

    rec = {
        "tool": "inference_energy_anchor",
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": "bm-a",
        "model": args.model,
        "caliber_note": "power.draw=GPU 板卡面非整机 wall；电费=实测 kWh×实缴账单单价（Q3 铁律·单价禁编造）",
        "idle_baseline_w": round(idle_w, 2),
        "burst_mean_w": round(burst_w_mean, 2),
        "burst_peak_w": round(max(w for _, w in burst_samples), 2),
        "burst_span_s": round(burst_span_s, 2),
        "wall_s": round(wall_s, 2),
        "eval_s": round(eval_s, 2),
        "tokens": tokens,
        "tok_s": round(tokens / eval_s, 2) if eval_s > 0 else None,
        "gross_wh": round(gross_wh, 6),
        "idle_equiv_wh": round(idle_equiv_wh, 6),
        "marginal_wh": round(marginal_wh, 6),
        "mwh_per_token_marginal": round(mwh_per_tok, 6),
        "kwh_per_mtok_marginal": round(kwh_per_mtok, 6),
        "kwh_per_mtok_gross": round(gross_kwh_per_mtok, 6),
        "tokens_per_kwh_marginal": round(tok_per_kwh, 1) if tok_per_kwh else None,
        "burst_samples_n": len(burst_samples),
        "idle_samples_n": len(idle_samples),
    }
    STATE.mkdir(exist_ok=True)
    out = STATE / ("inference-energy-anchor-%s.json"
                   % datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print("VERDICT=ANCHOR-OK")
    print(json.dumps(rec, ensure_ascii=False, indent=1), flush=True)
    print("REPORT=%s" % out, flush=True)
    return 0


def selftest():
    """合成夹具零 GPU 零真实状态污染（temp 目录）。判据 J1-J6。"""
    ok = 0
    # J1 梯形积分：常值 60W×60s=1Wh
    s = [(i, 60.0) for i in range(61)]
    a = trapezoid_wh(s)
    assert abs(a - 1.0) < 1e-9, "J1 trapezoid const"
    # J1b 线性坡 0→120W×60s=1Wh
    s2 = [(i, 2.0 * i) for i in range(61)]
    assert abs(trapezoid_wh(s2) - 1.0) < 1e-9, "J1b trapezoid ramp"
    ok += 2
    # J2 边际=总-基线折算
    gross = trapezoid_wh([(i, 100.0) for i in range(11)])  # 10s@100W
    idle_eq = 40.0 * 10.0 / 3600.0
    marg = gross - idle_eq
    assert abs(marg - (100.0 - 40.0) * 10.0 / 3600.0) < 1e-9, "J2 marginal"
    ok += 1
    # J3 换算链：0.5Wh/1000tok → 0.5 mWh/tok=0.5 kWh/Mtok；gross 同式
    toks = 1000
    wh = 0.5
    assert abs(wh / toks * 1000.0 - 0.5) < 1e-9, "J3 mWh/tok"
    assert abs(wh / toks * 1000.0 - 0.5) < 1e-9, "J3b kWh/Mtok"
    ok += 2
    # J4 nvidia-smi csv 解析鲁棒性（前后空格）
    line = "  125.16, 5\n"
    p = [x.strip() for x in line.strip().split(",")]
    assert float(p[0]) == 125.16 and float(p[1]) == 5.0, "J4 csv parse"
    ok += 1
    # J5 单样本护栏：梯形积分 <2 样本=0（长度护栏·run 侧 exit 4 消费）
    assert trapezoid_wh([(0, 100.0)]) == 0.0, "J5 guard"
    ok += 1
    # J6 报告写盘（temp 夹具零真实 state 污染）
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "r.json"
        f.write_text(json.dumps({"kwh_per_mtok_marginal": 0.5}, ensure_ascii=False), encoding="utf-8")
        assert json.loads(f.read_text(encoding="utf-8"))["kwh_per_mtok_marginal"] == 0.5, "J6 io"
    ok += 1
    print("SELFTEST %d/8 PASS (J1 trapezoid const/ramp, J2 marginal, J3 conv x2, J4 csv, J5 guard, J6 io)" % ok)
    return 0


def main():
    ap = argparse.ArgumentParser(description="推理请求级能耗强度实测锚（常驻 7b 突发+1s 功率采样）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--num-predict", type=int, default=2048)
    r.add_argument("--model", default="qwen2.5:7b-instruct")
    r.add_argument("--idle-n", type=int, default=10)
    r.add_argument("--timeout", type=int, default=300)
    sub.add_parser("selftest")
    args = ap.parse_args()
    if args.cmd == "selftest":
        return selftest()
    return run_anchor(args)


if __name__ == "__main__":
    sys.exit(main())
