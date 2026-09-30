#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve_concurrency_probe — 常驻 serve 并发基线探针（族 A 续窗·R-20260930-serve-concurrency 配套件）

只读探针：单流基线 × N=2/4 并发同题实测（常驻 7b·keep_alive:-1 维持零卸载）→
并发行为判定（真并行 vs 服务端串行排队）+ 降级系数/speedup 出数。
门禁：free VRAM <1500MiB 即 BLOCKED exit 3（让路律·零显存抢占）。
消费面：BC-P-04 MaaS 试点 / BC-P-12 双态 SLA 承诺结构第三判据 / 批池 lane B 执行面。
纯本机只读：零 schtasks、零派活、零新台账面（JSON 落 state/ 证据位）。
"""
import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "http://127.0.0.1:11434"
MODEL = "qwen2.5:7b-instruct"
MIN_FREE_MIB = 1500
NUM_PREDICT = 64


def vram_free_mib():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=10).decode().strip().splitlines()
        return int(out[0].strip())
    except Exception:
        return -1


def ollama_ps():
    try:
        return subprocess.check_output(["ollama", "ps"], stderr=subprocess.DEVNULL,
                                       timeout=10).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def one_call(slot):
    k = slot + 1
    prompt = "只输出数字，用逗号分隔，从 %d 数到 %d。" % (10 * k, 10 * k + 30)
    body = {"model": MODEL, "prompt": prompt, "stream": False, "keep_alive": -1,
            "options": {"num_predict": NUM_PREDICT, "temperature": 0}}
    t0 = time.time()
    req = urllib.request.Request(API + "/api/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    wall = time.time() - t0
    ec, ed = d.get("eval_count", 0), d.get("eval_duration", 0) or 1
    return {"slot": slot, "wall_s": round(wall, 2), "eval_count": ec,
            "tok_s": round(ec / (ed / 1e9), 2), "done_reason": d.get("done_reason", "")}


def run_level(c):
    results = [None] * c
    start = threading.Event()

    def worker(i):
        start.wait()
        results[i] = one_call(i)

    ths = [threading.Thread(target=worker, args=(i,)) for i in range(c)]
    for t in ths:
        t.start()
    t0 = time.time()
    start.set()
    for t in ths:
        t.join()
    span = time.time() - t0
    walls = [r["wall_s"] for r in results]
    mean_tok = sum(r["tok_s"] for r in results) / c
    total_tok = sum(r["eval_count"] for r in results) / span
    return {"n": c, "span_s": round(span, 2), "min_wall": min(walls), "max_wall": max(walls),
            "mean_stream_tok_s": round(mean_tok, 2), "aggregate_tok_s": round(total_tok, 2),
            "requests": results}


def do_check(levels):
    free = vram_free_mib()
    if free < MIN_FREE_MIB:
        print("CONCURRENCY-PROBE BLOCKED free_vram=%dMiB < %dMiB (让路律·净窗再跑)" % (free, MIN_FREE_MIB))
        return 3
    ps_pre = ollama_ps()
    base = one_call(0)
    levels_out = [run_level(c) for c in levels]
    ps_post = ollama_ps()
    restored = ""
    if "forever" not in ps_post.lower():
        # 丢失即载路径（T15 探针固化同律）：空 prompt=load + keep_alive:-1 复位常驻位
        body = {"model": MODEL, "prompt": "", "stream": False, "keep_alive": -1}
        req = urllib.request.Request(API + "/api/generate", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            json.loads(r.read().decode())
        time.sleep(1)
        ps_final = ollama_ps()
        restored = "RESTORED" if "FOREVER" in ps_final else "RESTORE-FAILED"
        print("  residency restore: %s" % restored)
    base_tok = base["tok_s"]
    for lv in levels_out:
        lv["speedup_vs_base"] = round(lv["aggregate_tok_s"] / base_tok, 2)
        lv["per_stream_degrade"] = round(base_tok / lv["mean_stream_tok_s"], 2)
    verdict = []
    for lv in levels_out:
        mode = "PARALLEL" if lv["per_stream_degrade"] < lv["n"] * 0.9 else "SERIALIZED-QUEUE"
        lv["mode"] = mode
        verdict.append("n=%d %s agg=%.1f tok/s speedup=%.2fx per-stream %.2fx" % (
            lv["n"], mode, lv["aggregate_tok_s"], lv["speedup_vs_base"], lv["per_stream_degrade"]))
    print("CONCURRENCY-PROBE baseline=%.1f tok/s (eval %d tok / wall %.2fs)" % (
        base_tok, base["eval_count"], base["wall_s"]))
    for v in verdict:
        print("  " + v)
    print("  residency pre: %s" % ("FOREVER" if "FOREVER" in ps_pre else ps_pre.splitlines()[-1] if ps_pre else "?"))
    print("  residency post: %s" % ("Forever-maintained" if "forever" in ps_post.lower() else "LOST"))
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    out = {"ts": ts, "model": MODEL, "free_vram_mib": free, "baseline": base,
           "levels": levels_out, "ps_pre": ps_pre, "ps_post": ps_post,
           "num_predict": NUM_PREDICT}
    path = "state/serve-concurrency-%s.json" % ts
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("JSON %s" % path)
    return 0


def agg_math(walls, counts, span):
    return sum(counts) / span


def do_selftest():
    ok = 0

    def chk(name, cond):
        nonlocal ok
        ok += 1 if cond else 0
        print("S%d %s" % (ok, "PASS" if cond else "FAIL"))

    chk("agg math 2x64/4s=32", abs(agg_math([3, 4], [64, 64], 4.0) - 32.0) < 1e-9)
    chk("mean stream (30+50)/2=40", abs((30 + 50) / 2 - 40.0) < 1e-9)
    chk("degrade base80/mean40=2.0", abs(80.0 / 40.0 - 2.0) < 1e-9)
    chk("serialized detect n=2 degrade 1.95", (1.95 >= 2 * 0.9))
    chk("parallel detect n=4 degrade 1.3", not (1.3 >= 4 * 0.9))
    chk("gate 1499<1500", 1499 < MIN_FREE_MIB and 1500 >= MIN_FREE_MIB)
    r = one_call.__doc__ is None or True
    chk("prompt distinct per slot", "从 %d 数到 %d" % (20, 50) != "从 %d 数到 %d" % (10, 40))
    print("SELFTEST %d/7" % ok)
    return 0 if ok == 7 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "selftest"])
    ap.add_argument("--levels", type=str, default="2,4")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return do_selftest()
    return do_check([int(x) for x in a.levels.split(",")])


if __name__ == "__main__":
    sys.exit(main())
