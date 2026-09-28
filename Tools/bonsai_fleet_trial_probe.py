# -*- coding: utf-8 -*-
"""Ternary-Bonsai-2-27B 机队分发试用探针（BigCompute 切片：赋能目录摘要批对照）.

令=orders/O-20260928-1326-HQ-C（P-2026-09-28-07）；规格件=cph4/research/R-20260928-bonsai-fleet-trial.md
（§四 P2 赋能摘要批 / §五 回执四件套）。部署配方 §一 镜像：本地 llama.cpp fork（PrismML b10743）+
模型字节锚 5,946,648,928。弱机档要领（规格件 §三）：chat_template_kwargs.enable_thinking=false，
否则思考链吃满预算正文零输出。
默认 CPU 档（-ngl 0·显存零占）——GPU 窗被在役栈占用时按让路律不抢；VRAM 释放后以 --ngl 99 复跑 GPU 档。
对照档=现役 Ollama qwen2.5:7b-instruct（OpenAI 兼容同题对照）。产出=state/trial-bonsai-<ts>/probe.log。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # <repo>/Tools/.. = repo root
LAB = r"C:\Users\sjs20\Desktop\FluxGroup\.codely-cli\labbench\bonsai2"
MODEL = os.path.join(LAB, "Ternary-Bonsai-2-27B-PTQ1_0.gguf")
MODEL_BYTES = 5_946_648_928  # 规格件 §一.1 字节锚（不齐=重下禁带病上岗）
BIN = os.path.join(LAB, "bin")
EXE = os.path.join(BIN, "llama-server.exe")
OLLAMA = "http://127.0.0.1:11434"
Q7B = "qwen2.5:7b-instruct"

INSTR = "请用不超过60字概括以下算力赋能件的服务对象、能力要点和关键数值："
ITEMS = [
    ("p1-billing-dual-scope", "计价双口径：口径A为外锚呈现用，锚点DeepSeek $0.15/1M tokens，禁止直接当内部结算价；口径B为内部成本锚（机队折旧+电费+运维），是内部结算与配额发价的唯一口径。记账载体为1M token粒度配额，月度预算闸超发须CEO批。计量核数器为tiktoken估算口径，OpenAI BPE不等于Qwen词表，精确计数以Ollama eval_count为准。"),
    ("p2-serve-resident-std", "serve常驻标准（U240口径）共五项：服务自启、模型预载、keep_alive=-1、常驻探针、档案登记。keep_alive官方默认值为5分钟。实测发现Ollama静默缩窗：num_ctx实际4096而声明32768。量化部署矩阵默认档为Q4_K_M，qwen2.5:7b-instruct许可为Apache 2.0。"),
    ("p3-cost-anchor-method", "算力成本锚方法论三要素：折旧年限3年/5年双档、残值率5%设计值；电费锚为RTX 3070/4070S TDP 220W、本机idle实测32.92W、power.limit为242W；运维科目已立。换算链为月末归集回溯口径结算唯一，试点期采用回溯定价法。预注册判据四条。"),
]
LONGGEN = "请写一段约150字的《集团算力赋能目录》开篇引言，主题：算力司的使命是让每一次AI算力消耗都有经济对价，服务集团内部消费方，记账轨先行。"


def post(url, payload, timeout):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8"))
    return time.time() - t0, body


def bonsai(prompt, max_tokens, port, timeout=600):
    payload = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
               "temperature": 0.3, "chat_template_kwargs": {"enable_thinking": False}}
    wall, r = post(f"http://127.0.0.1:{port}/v1/chat/completions", payload, timeout)
    return wall, r["choices"][0]["message"]["content"], r.get("usage", {}).get("completion_tokens")


def q7b(prompt, max_tokens):
    payload = {"model": Q7B, "messages": [{"role": "user", "content": prompt}], "stream": False,
               "options": {"num_predict": max_tokens, "temperature": 0.3}}
    wall, r = post(f"{OLLAMA}/api/chat", payload, 180)
    tps = r.get("eval_count", 0) / (r.get("eval_duration", 1) / 1e9)
    return wall, r["message"]["content"], r.get("eval_count", 0), tps


def snapshot(log):
    try:
        smi = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        log(f"[coexist] nvidia-smi: {smi.stdout.strip()}")
    except Exception as e:
        log(f"[coexist] nvidia-smi fail: {e}")
    try:
        tl = subprocess.run(["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=10)
        for line in tl.stdout.splitlines():
            if "llama-server" in line:
                kb = int(line.rstrip('"').split('","')[-1].replace('"', "").replace(",", "").replace(" K", ""))
                log(f"[coexist] llama-server RAM = {kb/1024/1024:.2f} GiB")
    except Exception as e:
        log(f"[coexist] tasklist fail: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ngl", default="0", help="GPU layers; 0=CPU tier (default)")
    ap.add_argument("--port", type=int, default=8078)
    args = ap.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    outdir = os.path.join(ROOT, "state", f"trial-bonsai-{ts}")
    os.makedirs(outdir, exist_ok=True)
    logf = open(os.path.join(outdir, "probe.log"), "w", encoding="utf-8")

    def log(s):
        logf.write(s + "\n")
        logf.flush()

    sz = os.path.getsize(MODEL)
    log(f"[deploy] model_bytes={sz} anchor_match={'PASS' if sz == MODEL_BYTES else 'FAIL'}")
    print(f"byte_anchor={'PASS' if sz == MODEL_BYTES else 'FAIL'}")
    if sz != MODEL_BYTES:
        print("FATAL model byte mismatch")
        sys.exit(2)

    srv_log = open(os.path.join(outdir, "server.log"), "w", encoding="utf-8")
    t0 = time.time()
    proc = subprocess.Popen([EXE, "-m", MODEL, "-ngl", args.ngl, "--host", "127.0.0.1",
                             "--port", str(args.port), "-c", "4096"], cwd=BIN,
                            stdout=srv_log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    ok = False
    while time.time() - t0 < 300:
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health", timeout=3) as r:
                if json.loads(r.read().decode()).get("status") == "ok":
                    ok = True
                    break
        except Exception:
            pass
    load_s = time.time() - t0
    log(f"[deploy] health={'ok' if ok else 'FAIL'} load_s={load_s:.1f} ngl={args.ngl} port={args.port}")
    print(f"health={'ok' if ok else 'FAIL'} load_s={load_s:.1f}")
    if not ok:
        proc.terminate()
        print("FATAL server not healthy")
        sys.exit(3)

    try:
        snapshot(log)
        for iid, text in ITEMS:
            prompt = INSTR + text
            w, out, ctok = bonsai(prompt, 100, args.port)
            log(f"[bonsai] {iid} wall_s={w:.1f} ctok={ctok} tps={ctok / max(w, 1e-9):.2f}")
            log(f"[bonsai] {iid} OUT: {out}")
            print(f"bonsai {iid} done wall={w:.1f}s")
            w2, out2, n2, tps2 = q7b(prompt, 100)
            log(f"[q7b] {iid} wall_s={w2:.1f} ctok={n2} tps={tps2:.1f}")
            log(f"[q7b] {iid} OUT: {out2}")
            print(f"q7b {iid} done wall={w2:.1f}s")
        w, out, ctok = bonsai(LONGGEN, 300, args.port)
        log(f"[bonsai] longgen wall_s={w:.1f} ctok={ctok} tps={ctok / max(w, 1e-9):.2f}")
        log(f"[bonsai] longgen OUT: {out}")
        w2, out2, n2, tps2 = q7b(LONGGEN, 300)
        log(f"[q7b] longgen wall_s={w2:.1f} ctok={n2} tps={tps2:.1f}")
        log(f"[q7b] longgen OUT: {out2}")
        print("longgen done")
        snapshot(log)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
        log("[deploy] server stopped")
    print("outdir=" + outdir)


if __name__ == "__main__":
    main()
