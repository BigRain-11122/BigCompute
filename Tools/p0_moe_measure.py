#!/usr/bin/env python3
"""V100-EXPANSION-SPEC P0 MoE-unload acceptance measurement (T89 step-4, pre-registered).

Target: live 35B coder serve (:8090, llama.cpp --n-cpu-moe, CEO-deployed 2026-10-14 12:5x).
Pre-registered metrics: chinese-chars/sec + tok/s (dual), fixed Chinese question set,
thinking disabled for speed (deployment note). Acceptance line: >= 30 chars/sec.
Zero server-side action: inference-only HTTP calls, no restart, no config touch.

Usage:
  python Tools/p0_moe_measure.py [--base-url http://127.0.0.1:8090] [--out state/v100-p0-night-measure-20261014.json]
"""
import argparse, json, time, urllib.request, datetime, platform, sys

FIXED_QUESTIONS = [
    {"id": "Q1-factual-short", "prompt": "用一句话解释什么是显存带宽。"},
    {"id": "Q2-code-medium", "prompt": "写一个 Python 函数判断字符串是否是回文，并给一行说明。"},
    {"id": "Q3-factual-medium", "prompt": "用三句话概括 DDR5 内存相对 DDR3 的主要技术进步。"},
]

DDR5_NOTE = "bm-a 2x48GB DDR5-5600 dual-channel, theoretical ~89.6GB/s (preflight 2026-10-14 step-1); bandwidth denominator note per pre-registration."


def http_json(url, payload=None, timeout=600):
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8090")
    ap.add_argument("--out", default="state/v100-p0-night-measure-20261014.json")
    ap.add_argument("--accept-charset-sec", type=float, default=30.0)
    args = ap.parse_args()

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    health = http_json(args.base_url + "/health", timeout=10)
    try:
        models = http_json(args.base_url + "/v1/models", timeout=10)
        model_id = models["data"][0]["id"] if models.get("data") else "default"
    except Exception:
        model_id = "default"

    results = []
    for q in FIXED_QUESTIONS:
        t0 = time.perf_counter()
        resp = http_json(args.base_url + "/v1/chat/completions", {
            "model": model_id,
            "messages": [{"role": "user", "content": q["prompt"]}],
            "max_tokens": 512,
            "temperature": 0.7,
            "chat_template_kwargs": {"enable_thinking": False},
            "stream": False,
        })
        wall = time.perf_counter() - t0
        content = resp["choices"][0]["message"]["content"] or ""
        usage = resp.get("usage", {}) or {}
        cjk = sum(1 for c in content if "\u4e00" <= c <= "\u9fff")
        chars_per_sec = (cjk / wall) if wall > 0 else 0.0
        ctok = usage.get("completion_tokens")
        results.append({
            "id": q["id"], "prompt": q["prompt"], "wall_sec": round(wall, 3),
            "answer_chars_total": len(content), "answer_cjk_chars": cjk,
            "cjk_chars_per_sec": round(chars_per_sec, 2),
            "completion_tokens": ctok, "prompt_tokens": usage.get("prompt_tokens"),
            "tok_per_sec": round((ctok / wall), 2) if ctok and wall else None,
        })
        print(f"{q['id']}: wall={wall:.1f}s cjk={cjk} c/s={chars_per_sec:.1f} tok/s={results[-1]['tok_per_sec']}")

    avg = sum(r["cjk_chars_per_sec"] for r in results) / len(results)
    verdict = "PASS" if avg >= args.accept_charset_sec else "FAIL"
    doc = {
        "measure_id": "v100-p0-night-measure-20261014", "measured_at": ts,
        "purpose": "T89 step-4 pre-registered P0 MoE-unload acceptance measurement (V100-EXPANSION-SPEC-20261014 s4 P0)",
        "target": {"base_url": args.base_url, "model_id": model_id, "health": health},
        "form_factor": "live CEO-deployed 35B coder serve (llama.cpp nightly b11429 --n-cpu-moe, UD-IQ4_XS 17.7G, GPU ~3G resident, watchdog bm-a-CoderServe)",
        "bandwidth_denominator_note": DDR5_NOTE,
        "thinking": "disabled via chat_template_kwargs.enable_thinking=false (deployment speed note)",
        "results": results, "avg_cjk_chars_per_sec": round(avg, 2),
        "acceptance_line_charset_sec": args.accept_charset_sec, "verdict": verdict,
        "host": platform.node(),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"AVG {avg:.1f} c/s -> {verdict} | evidence={args.out}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
