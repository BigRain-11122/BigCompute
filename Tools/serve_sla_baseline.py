#!/usr/bin/env python3
"""常驻 serve 吞吐 SLA 基线工具（算力议程四轮循环·族 A 推理服务化增量）

R-20260929-resident-serving-throughput-stability（R-39）手工双态分析的工程化：
- series : 解析 qa/smoke-*.log 的 tok/s 序列 × state/gpu-util/samples.jsonl 显存/功率序列
           → 净窗/竞争窗双态分类（BC-P-15 双判据·E36 预审计择律落地：
             ①显存判据 NET_MEM_THRESHOLD_MIB=7500（R-39 基线 6.6GB vs 竞争带 8.4-11.4GB 中分线）
             ②降级+热双证判据（计算型压制补盲：热=util≥30% 或 power≥80W 且 tok/s<50 才翻竞争——
               E36 实跑 4 热嫌疑 3 伪〔自探针采样伪影 util=100×2+孤立 power 峰·吞吐正常〕唯一真漂移
               =09-29 14:20 点 25.16 tok/s·纯热单证 flips=4 误伤 vs 双证 flips=1 全中）
           → 净窗基线 mean/CV/P10 + 竞争窗对照 + SLA 判读行 + JSON 落盘 state/
- selftest: 合成夹具断言（分类/双证翻类/伪阳性保护/数学/无样本窗豁免/确定性）·temp 夹具零真实状态污染

零 GPU 占用（纯日志面）·数据源=qa_smoke 每轮自产+GPU-IdleWatch 既有样本零新采集
消费方=月末归集执行单 S5 产能窗基线（E25·N1）+BC-P-12 双态 SLA 承诺结构+BC-P-04/11 数据前置
"""
import argparse
import json
import math
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QA_DIR = ROOT / "qa"
GPU_SAMPLES = ROOT / "state" / "gpu-util" / "samples.jsonl"
OUT_DIR = ROOT / "state"

NET_MEM_THRESHOLD_MIB = 7500.0  # R-39 双态分界中分线（基线 6.6GB / 竞争带起 8.4GB）
JOIN_WINDOW_MIN = 15             # smoke 点与 gpu 样本最近邻配对窗（分钟）
# BC-P-15 第二判据（E36 预审计三候选择律=「降级+热双证」·preaudit JSON flips=1 全中真漂移）
HOT_UTIL_PCT = 30.0              # 热嫌疑线（真净窗带 util 4-9% vs 漂移点 60% 中分）
HOT_POWER_W = 80.0               # 热嫌疑线（真净窗带 33-37W vs 漂移点 164.55W 中分）
DEGRADED_TOK_S = 50.0            # 降级带线（净窗 P10~82 vs 竞争带 29.69-60.99 下缘中分）

SMOKE_TS_RE = re.compile(r"BigCompute QA smoke test (\d{8}-\d{4})")
TOKS_RE = re.compile(r"eval_count=(\d+)\s+([\d.]+) tok/s")


def _pct(vals):
    """P10（最近邻秩法·n<10 时取最小值端·R-39 口径一致）"""
    if not vals:
        return None
    s = sorted(vals)
    if len(s) < 10:
        return s[0]
    k = max(0, math.ceil(0.10 * len(s)) - 1)
    return s[k]


def parse_smoke_points(qa_dir):
    """[(ts, tok_s, log_name)]——头行取时标·eval_count 行取 tok/s·缺匹配日志跳过如实计"""
    pts, n_logs = [], 0
    for log in sorted(qa_dir.glob("smoke-*.log")):
        n_logs += 1
        ts, tok = None, None
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = SMOKE_TS_RE.search(text)
        if m:
            ts = datetime.strptime(m.group(1), "%Y%m%d-%H%M")
        m = TOKS_RE.search(text)
        if m:
            tok = float(m.group(2))
        if ts and tok:
            pts.append((ts, tok, log.name))
    return pts, n_logs


def load_gpu_samples(path):
    samples = []
    if not path.exists():
        return samples
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
            samples.append((datetime.fromisoformat(d["ts"]),
                            float(d["util_pct"]),
                            float(d["mem_used_mib"]),
                            float(d["power_w"])))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return samples


def classify(points, samples, join_window_min=JOIN_WINDOW_MIN, threshold=NET_MEM_THRESHOLD_MIB):
    """最近邻样本配对双判据分类：net / contended / no_sample（无样本=豁免计数不入选）
    竞争态 = ①显存越线（mem>7500）或 ②降级+热双证（热=util≥30 或 power≥80，且 tok/s<50）。
    双证律防伪阳性：E36 实跑纯热单证 flips=4 含 3 采样伪影误伤（吞吐正常不翻）·双证 flips=1 全中。"""
    out = []
    for ts, tok, name in points:
        near = None
        for gts, util, mem, pw in samples:
            if abs((gts - ts).total_seconds()) <= join_window_min * 60:
                if near is None or abs((gts - ts).total_seconds()) < abs((near[0] - ts).total_seconds()):
                    near = (gts, util, mem, pw)
        if near is None:
            out.append({"ts": ts, "tok_s": tok, "log": name, "state": "no_sample",
                        "mem_mib": None, "util": None, "power": None})
        else:
            hot = (near[1] is not None and near[1] >= HOT_UTIL_PCT) or \
                  (near[3] is not None and near[3] >= HOT_POWER_W)
            state = "contended" if (near[2] > threshold or (hot and tok < DEGRADED_TOK_S)) else "net"
            out.append({"ts": ts, "tok_s": tok, "log": name, "state": state,
                        "mem_mib": near[2], "util": near[1], "power": near[3]})
    return out


def stats(vals):
    if not vals:
        return {"n": 0, "mean": None, "cv_pct": None, "p10": None, "min": None, "max": None}
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return {"n": len(vals), "mean": round(mean, 2),
            "cv_pct": round(100 * math.sqrt(var) / mean, 1) if mean else None,
            "p10": round(_pct(vals), 2), "min": min(vals), "max": max(vals)}


def build(qa_dir, samples_path):
    points, n_logs = parse_smoke_points(qa_dir)
    samples = load_gpu_samples(samples_path)
    rows = classify(points, samples)
    net = stats([r["tok_s"] for r in rows if r["state"] == "net"])
    cont = stats([r["tok_s"] for r in rows if r["state"] == "contended"])
    ratio = round(cont["mean"] / net["mean"], 2) if (net["mean"] and cont["mean"]) else None
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_logs": n_logs, "n_points": len(rows),
        "n_no_sample": sum(1 for r in rows if r["state"] == "no_sample"),
        "threshold_mem_mib": NET_MEM_THRESHOLD_MIB, "join_window_min": JOIN_WINDOW_MIN,
        "rule": "mem>7500 OR (hot AND tok<50) — BC-P-15 dual-criterion (E36 preaudit rule 3)",
        "hot_util_pct": HOT_UTIL_PCT, "hot_power_w": HOT_POWER_W, "degraded_tok_s": DEGRADED_TOK_S,
        "net": net, "contended": cont, "contended_to_net_ratio": ratio,
        "span": [rows[0]["ts"].strftime("%Y-%m-%d %H:%M"), rows[-1]["ts"].strftime("%Y-%m-%d %H:%M")] if rows else None,
        "points": [{"ts": r["ts"].strftime("%Y-%m-%d %H:%M"), "tok_s": r["tok_s"],
                    "state": r["state"], "mem_mib": r["mem_mib"], "log": r["log"]} for r in rows],
    }


def cmd_series(_args):
    rep = build(QA_DIR, GPU_SAMPLES)
    out = OUT_DIR / ("serve-sla-baseline-" + datetime.now().strftime("%Y%m%d-%H%M") + ".json")
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    n, c = rep["net"], rep["contended"]
    print(f"serve SLA baseline (logs={rep['n_logs']} points={rep['n_points']} "
          f"no_sample={rep['n_no_sample']} span={rep['span']})")
    print(f"net-window : n={n['n']} mean={n['mean']} tok/s CV={n['cv_pct']}% "
          f"P10={n['p10']} min={n['min']} max={n['max']}")
    print(f"contended  : n={c['n']} mean={c['mean']} tok/s min={c['min']} max={c['max']}"
          + (f" ({rep['contended_to_net_ratio']}x net)" if rep['contended_to_net_ratio'] else ""))
    print("verdict    : " + (
        f"净窗基线 {n['mean']} tok/s · CV {n['cv_pct']}%（BC-P-12 双态承诺结构数据前置·"
        f"月末归集 S5 产能窗基线）· 竞争窗 {rep['contended_to_net_ratio']}x=降级免责条款实证"
        if n["n"] and rep["contended_to_net_ratio"] else "样本不足·如实"))
    print("json       : " + str(out))
    return 0


def _write_fixture(d):
    qa = d / "qa"
    qa.mkdir()
    logs = {
        "smoke-20260928-1301.log": ("BigCompute QA smoke test 20260928-1301 (orders L254 / charter v1)\n",
                                    "answer='ok' eval_count=8 60.99 tok/s\n"),
        "smoke-20260928-1402.log": ("BigCompute QA smoke test 20260928-1402 (orders L254 / charter v1)\n",
                                     "answer='ok' eval_count=8 29.69 tok/s\n"),
        "smoke-20260929-0002.log": ("BigCompute QA smoke test 20260929-0002 (orders L254 / charter v1)\n",
                                    "answer='ok' eval_count=8 95.10 tok/s\n"),
        "smoke-20260929-1106.log": ("BigCompute QA smoke test 20260929-1106 (orders L254 / charter v1)\n",
                                    "answer='ok' eval_count=8 93.90 tok/s\n"),
        "smoke-20260929-1420.log": ("BigCompute QA smoke test 20260929-1420 (orders L254 / charter v1)\n",
                                    "answer='ok' eval_count=8 25.16 tok/s\n"),
        "smoke-20260929-1500.log": ("BigCompute QA smoke test 20260929-1500 (orders L254 / charter v1)\n",
                                    "answer='ok' eval_count=8 103.07 tok/s\n"),
        "smoke-20260929-1330.log": ("BigCompute QA smoke test 20260929-1330 (orders L254 / charter v1)\n", None),
    }
    for name, (head, toks) in logs.items():
        body = head + (toks or "answer missing\n")
        (qa / name).write_text(body, encoding="utf-8")
    gpu = d / "state" / "gpu-util"
    gpu.mkdir(parents=True)
    rows = [
        {"ts": "2026-09-28T13:03:00", "util_pct": 100.0, "mem_used_mib": 9500.0, "power_w": 199.8},
        {"ts": "2026-09-28T14:05:00", "util_pct": 98.0, "mem_used_mib": 8400.0, "power_w": 180.0},
        {"ts": "2026-09-29T00:04:00", "util_pct": 4.0, "mem_used_mib": 6632.0, "power_w": 33.9},
        {"ts": "2026-09-29T11:08:00", "util_pct": 5.0, "mem_used_mib": 6652.0, "power_w": 35.0},
        {"ts": "2026-09-29T14:20:37", "util_pct": 60.0, "mem_used_mib": 4776.0, "power_w": 164.55},
        {"ts": "2026-09-29T15:02:00", "util_pct": 100.0, "mem_used_mib": 6600.0, "power_w": 60.43},
    ]
    (gpu / "samples.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return d


def cmd_selftest(_args):
    ok = []
    with tempfile.TemporaryDirectory() as td:
        d = _write_fixture(Path(td))
        qa_dir, smp = d / "qa", d / "state" / "gpu-util" / "samples.jsonl"
        pts, n_logs = parse_smoke_points(qa_dir)
        ok.append(("parse: 6 points / 7 logs (1 missing tok/s skipped)",
                   len(pts) == 6 and n_logs == 7))
        rows = classify(pts, load_gpu_samples(smp))
        by = {(r["log"], r["state"]) for r in rows}
        ok.append(("classify: contended 3 (mem>7500 ×2 + 双证翻类 ×1) + net 3",
                   by == {("smoke-20260928-1301.log", "contended"),
                          ("smoke-20260928-1402.log", "contended"),
                          ("smoke-20260929-1420.log", "contended"),
                          ("smoke-20260929-0002.log", "net"),
                          ("smoke-20260929-1106.log", "net"),
                          ("smoke-20260929-1500.log", "net")}))
        # 双证翻类：14:20 漂移点 mem=4776 不越显存线，但 util=60/power=164.55 热+tok=25.16 降级 → 翻竞争
        r1420 = next(r for r in rows if r["log"] == "smoke-20260929-1420.log")
        ok.append(("dual-evidence: 14:20 计算型压制点翻竞争（mem 单维盲区补上）",
                   r1420["state"] == "contended"))
        # 伪阳性保护：15:00 自探针采样伪影 util=100 但吞吐 103.07 正常 → 维持净窗不翻
        r1500 = next(r for r in rows if r["log"] == "smoke-20260929-1500.log")
        ok.append(("false-positive guard: 热但吞吐正常不翻（E36 三伪同律）",
                   r1500["state"] == "net"))
        # 远样本豁免：+30min 无样本点 → no_sample
        far = classify([(datetime(2026, 9, 29, 23, 0), 50.0, "far.log")], load_gpu_samples(smp))
        ok.append(("no_sample: outside 15min join window excluded", far[0]["state"] == "no_sample"))
        net = stats([95.10, 93.90, 103.07])
        ok.append(("math: net mean=97.36 CV=4.2% (n=3 P10=min)", net["mean"] == 97.36 and net["cv_pct"] == 4.2))
        r1 = build(qa_dir, smp)
        ok.append(("determinism: double build identical",
                   json.dumps(r1, sort_keys=True) == json.dumps(build(qa_dir, smp), sort_keys=True)))
        ok.append(("rule meta: BC-P-15 双判据字段入报告 JSON",
                   r1["hot_util_pct"] == 30.0 and r1["hot_power_w"] == 80.0
                   and r1["degraded_tok_s"] == 50.0 and "dual-criterion" in r1["rule"]))
        ok.append(("ratio: contended mean 38.61 = 0.4x net",
                   r1["contended_to_net_ratio"] == 0.4))
    for name, passed in ok:
        print(("[PASS] " if passed else "[FAIL] ") + name)
    return 0 if all(p for _, p in ok) else 1


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("series", help="解析烟测×GPU 样本序列 → 双态 SLA 基线报告+JSON")
    sub.add_parser("selftest", help="合成夹具断言（分类/数学/豁免/确定性）")
    args = ap.parse_args()
    return cmd_selftest(args) if args.cmd == "selftest" else cmd_series(args)


if __name__ == "__main__":
    sys.exit(main())
