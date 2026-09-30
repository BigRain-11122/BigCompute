#!/usr/bin/env python3
"""SLA 分类漂移双判据预验证件（BC-P-15 修复数据前置·算力议程族 B 增量·E36）

E29 轮内实锤「SLA 分类漂移」的 N1 后修复预验证：
- 根因锚（2026-09-30 15:2x 轮实读定位）：2026-09-29 14:18:37 样本 util=60% power=164.55W
  但 mem=4776MiB ≤7500 —— 竞争态为**计算型压制**（他司产线活算）非显存型：
  mem 单维判据结构性盲区（14:20 探针 25.16 tok/s 误归净窗→CV 5.8%→24.4%）。
- check   : 复用 serve_sla_baseline 同源配对分类（零口径变更·N1 冻结期纯只读）→
            净窗点逐点 util/power 面板 + 漂移嫌疑点（net 但 util/power 越嫌疑线）+
            双判据敏感性表（候选阈值组合下翻转数与净窗 mean/CV 变化）→ JSON 落盘 state/
- selftest: 合成夹具断言（漂移点识别/敏感性翻转/CV 收窄/确定性）

消费方=BC-P-15 修复轮（N1 窗后解冻·阈值选择数据在案修复零推导）·月末 S5 产能窗基线质量面。
零 GPU 占用·零状态污染（纯读 qa/ 与 state/gpu-util/）·禁 N1 冻结期改 serve_sla_baseline 口径。
"""
import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_sla_baseline as slb  # noqa: E402  同源解析/配对/统计复用（口径冻结面零复制漂移）

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "state"

# 嫌疑线：真净窗带（util 4-9%·power 33-37W·R-39/E28 实测）vs 漂移点（util 60%·power 164.55W）中分
SUSPECT_UTIL_PCT = 30.0
SUSPECT_POWER_W = 80.0
# 候选双判据阈值（敏感性扫描·修复轮择一落地·net 须同时满足各行条件）
# 第三行=「热+降级双证」：util/power 越线 **且** tok/s 落降级带（<50）方翻转——
# 实跑发现纯热判据有伪阳性模式（自探针 generate 被采样器捕 util=100·孤立 power 峰·吞吐均正常）
RULES = [("util<30 AND power<80", 30.0, 80.0, None),
         ("util<50 AND power<120", 50.0, 120.0, None),
         ("hot AND tok<50 (degraded+hot)", 30.0, 80.0, 50.0)]


def audit(qa_dir, samples_path):
    points, n_logs = slb.parse_smoke_points(qa_dir)
    rows = slb.classify(points, slb.load_gpu_samples(samples_path))
    n_no_sample = sum(1 for r in rows if r["state"] == "no_sample")
    net_rows = [r for r in rows if r["state"] == "net"]
    contended_rows = [r for r in rows if r["state"] == "contended"]

    panel = []
    for r in net_rows:
        util, power = r["util"], r["power"]
        suspect = (util is not None and util >= SUSPECT_UTIL_PCT) or \
                  (power is not None and power >= SUSPECT_POWER_W)
        panel.append({"ts": r["ts"].strftime("%Y-%m-%d %H:%M"), "tok_s": r["tok_s"],
                      "log": r["log"], "mem_mib": r["mem_mib"], "util": util,
                      "power_w": power, "drift_suspect": suspect})

    sensitivity = []
    for name, u_t, p_t, tok_lt in RULES:
        def _flip(r):
            hot = (r["util"] is not None and r["util"] >= u_t) or \
                  (r["power"] is not None and r["power"] >= p_t)
            return hot and (tok_lt is None or r["tok_s"] < tok_lt)
        flipped = [r for r in net_rows if _flip(r)]
        kept_net = [r["tok_s"] for r in net_rows if r not in flipped]
        kept_cont = [r["tok_s"] for r in contended_rows] + [r["tok_s"] for r in flipped]
        sensitivity.append({
            "rule": name, "flips": len(flipped),
            "flipped": [{"ts": r["ts"].strftime("%Y-%m-%d %H:%M"), "tok_s": r["tok_s"],
                         "log": r["log"], "util": r["util"], "power_w": r["power"],
                         "mem_mib": r["mem_mib"]} for r in flipped],
            "net_after": slb.stats(kept_net), "contended_after": slb.stats(kept_cont)})

    net_now = slb.stats([r["tok_s"] for r in net_rows])
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_logs": n_logs, "n_points": len(rows), "n_no_sample": n_no_sample,
        "net_now": net_now, "n_net": len(net_rows),
        "suspect_line": {"util_pct": SUSPECT_UTIL_PCT, "power_w": SUSPECT_POWER_W},
        "root_cause_anchor": "2026-09-29 14:18:37 sample util=60% power=164.55W mem=4776MiB "
                              "—— compute-bound contention, mem-only rule blind spot",
        "net_panel": panel, "sensitivity": sensitivity,
        "freeze_note": "read-only preaudit; classifier untouched; N1 caliber frozen until post-window"}


def cmd_check(_args):
    rep = audit(slb.QA_DIR, slb.GPU_SAMPLES)
    out = OUT_DIR / ("sla-drift-preaudit-" + datetime.now().strftime("%Y%m%d-%H%M") + ".json")
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"SLA drift preaudit (points={rep['n_points']} net={rep['n_net']} "
          f"no_sample={rep['n_no_sample']}·口径冻结纯只读)")
    print(f"net-now    : n={rep['net_now']['n']} mean={rep['net_now']['mean']} "
          f"CV={rep['net_now']['cv_pct']}% (mem-only 现行口径)")
    for p in rep["net_panel"]:
        flag = " <-- DRIFT-SUSPECT" if p["drift_suspect"] else ""
        print(f"  {p['ts']}  tok={p['tok_s']:>6}  mem={p['mem_mib']}  "
              f"util={p['util']}  power={p['power_w']}{flag}")
    for s in rep["sensitivity"]:
        n = s["net_after"]
        print(f"rule {s['rule']:<22}: flips={s['flips']} -> net n={n['n']} "
              f"mean={n['mean']} CV={n['cv_pct']}%")
    print("json       : " + str(out))
    return 0


def _fixture(d):
    qa = d / "qa"
    qa.mkdir()
    logs = {
        "smoke-20260929-1416.log": ("BigCompute QA smoke test 20260929-1416 (orders L254 / charter v1)\n",
                                    "answer='ok' eval_count=8 93.90 tok/s\n"),
        "smoke-20260929-1420.log": ("BigCompute QA smoke test 20260929-1420 (orders L254 / charter v1)\n",
                                    "answer='ok' eval_count=8 25.16 tok/s\n"),
        "smoke-20260929-1430.log": ("BigCompute QA smoke test 20260929-1430 (orders L254 / charter v1)\n",
                                    "answer='ok' eval_count=8 45.00 tok/s\n"),
    }
    for name, (head, toks) in logs.items():
        (qa / name).write_text(head + toks, encoding="utf-8")
    gpu = d / "state" / "gpu-util"
    gpu.mkdir(parents=True)
    rows = [
        {"ts": "2026-09-29T14:16:00", "util_pct": 5.0, "mem_used_mib": 6632.0, "power_w": 35.0},
        {"ts": "2026-09-29T14:20:37", "util_pct": 60.0, "mem_used_mib": 4776.0, "power_w": 164.55},
        {"ts": "2026-09-29T14:31:00", "util_pct": 95.0, "mem_used_mib": 9500.0, "power_w": 199.8},
    ]
    (gpu / "samples.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return d


def cmd_selftest(_args):
    ok = []
    with tempfile.TemporaryDirectory() as td:
        d = _fixture(Path(td))
        rep = audit(d / "qa", d / "state" / "gpu-util" / "samples.jsonl")
        ok.append(("parse+classify: 3 points, net=2 contended=1 (14:20 点 mem=4776 误归净窗复现)",
                   rep["n_points"] == 3 and rep["n_net"] == 2))
        sus = [p for p in rep["net_panel"] if p["drift_suspect"]]
        ok.append(("drift-suspect: 14:20 点被嫌疑线捕获（util 60≥30·power 164.55≥80）·真净窗 14:19 不捕",
                   len(sus) == 1 and sus[0]["ts"].endswith("14:20") and sus[0]["tok_s"] == 25.16))
        s1 = rep["sensitivity"][0]
        ok.append((f"rule util<30 AND power<80: flips=1 -> net n=1 mean=93.9",
                   s1["flips"] == 1 and s1["net_after"]["n"] == 1
                   and s1["net_after"]["mean"] == 93.9))
        s3 = rep["sensitivity"][2]
        ok.append(("rule hot AND tok<50: 降级+热双证同判 flips=1（热但吞吐正常不翻）",
                   s3["flips"] == 1 and s3["net_after"]["n"] == 1
                   and s3["net_after"]["mean"] == 93.9))
        ok.append(("CV 收窄: mem-only 含漂移点 CV=57.7% -> 双判据后 None(n=1)·contended n=2 均值 35.08",
                   rep["net_now"]["cv_pct"] == 57.7
                   and s1["contended_after"]["n"] == 2 and s1["contended_after"]["mean"] == 35.08))
        ok.append(("determinism: double audit identical",
                   json.dumps(rep, sort_keys=True) ==
                   json.dumps(audit(d / "qa", d / "state" / "gpu-util" / "samples.jsonl"),
                              sort_keys=True)))
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
    sub.add_parser("check", help="净窗点 util/power 面板+漂移嫌疑+双判据敏感性 → JSON")
    sub.add_parser("selftest", help="合成夹具断言（漂移识别/翻转/CV 收窄/确定性）")
    args = ap.parse_args()
    return cmd_selftest(args) if args.cmd == "selftest" else cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
