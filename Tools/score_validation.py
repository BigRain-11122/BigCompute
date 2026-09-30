#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score_validation.py — 计分器人工标注验证集工具（D-20260930-17 ①·D-20260930-23 ②·v1.2 验收硬判据）

律源：D-20260930-17 ①「随机抽 30 个 commit 由人（或独立于工具作者的一司）标注真实档位，
算出工具的一致率与混淆矩阵，一致率 <85% 不得上判负面」；D-20260930-23 ②判据复述。
本件=抽样导出+一致率计算双面机械件；**标注本身须独立于工具作者**（CPH4 外审面或
他司承接）——本司只出集与算法、不出标签（防「自己给自己打分」套利面·D-17 ④）。

用法：
  python Tools/score_validation.py sample --n 30 --out state/score-validation-set-20260930.jsonl [--days 7]
  python Tools/score_validation.py agreement --set state/score-validation-set-20260930.jsonl
  python Tools/score_validation.py selftest
（sample=七司池种子抽样〔seed=20260930·复跑同集〕·git -C 只读零跨仓写；
  agreement=已标注行→一致率+3×3 混淆矩阵；0 标注行=exit 2 PENDING；
  一致率 <85%=exit 1〔<85% 禁用于点名·D-23 ②〕·≥85%=exit 0）
"""
import argparse
import json
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from round_score import commit_score  # 复用 v1.2 分档口径（零重复判据）

GROUP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REPOS = {  # 七司仓（集团根相对路径·只读）
    "BigCompute": "compute/BigCompute",
    "BigDomain": "domain/BigDomain",
    "BigLife": "life/BigLife",
    "BigMoney": "quant/bigmoney",
    "BigStream": "media/BigStream",
    "Biggame": "gaming/MiniGame",
    "FluxVerse": "gaming/FluxVerse",
}
SEED = 20260930
THRESHOLD = 0.85


def _repo_commits(repo_path, days):
    out = subprocess.run(
        ["git", "-C", repo_path, "log", "--since", "%d days ago" % days,
         "--pretty=format:@@%h%x09%ad%x09%s", "--date=iso", "--name-only"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        return []
    commits, cur = [], None
    for line in out.stdout.splitlines():
        if line.startswith("@@"):
            if cur:
                commits.append(cur)
            parts = line[2:].split("\t", 2)
            cur = {"short": parts[0], "date": parts[1] if len(parts) > 1 else "",
                   "subject": parts[2] if len(parts) > 2 else "", "paths": []}
        elif line.strip() and cur is not None:
            cur["paths"].append(line.strip())
    if cur:
        commits.append(cur)
    for c in commits:
        c["score"] = commit_score(c["paths"], c["short"], repo_path, subject=c["subject"])
    return commits


def do_sample(n, days, out_path):
    pooled = []
    for name, rel in REPOS.items():
        cs = _repo_commits(os.path.join(GROUP_ROOT, rel), days)
        for c in cs:
            c["repo"] = name
        pooled.extend(cs)
        print("pool %-11s n=%d" % (name, len(cs)))
    if not pooled:
        print("VERDICT: EMPTY-POOL（七司 %d 日窗零 commit=信息态）" % days)
        return 3
    picks = random.Random(SEED).sample(pooled, min(n, len(pooled)))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for c in picks:
            f.write(json.dumps({
                "repo": c["repo"], "short": c["short"], "date": c["date"],
                "subject": c["subject"][:200], "paths": c["paths"][:20],
                "score": c["score"], "label": None,  # 标注位=独立面回填（本司禁自标）
            }, ensure_ascii=False) + "\n")
    dist = {}
    for c in picks:
        dist[c["score"]] = dist.get(c["score"], 0) + 1
    print("VERDICT: SAMPLED n=%d/%d pool=%d seed=%d dist=%s" % (len(picks), n, len(pooled), SEED, dist))
    print("SET: " + out_path)
    print("NEXT: 独立标注面（CPH4 外审/他司）回填 label∈{0,1,2} → agreement 收口（<85% 禁上判负面）")
    return 0


def compute_agreement(rows):
    """返回 dict 或 None（零已标注行）。非法档位行=跳过计数并单列报告（诚实面）。"""
    labeled = [r for r in rows if r.get("label") is not None]
    matrix = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    agree, bad = 0, 0
    for r in labeled:
        t, l = r.get("score"), r.get("label")
        if t not in (0, 1, 2) or l not in (0, 1, 2):
            bad += 1
            continue
        matrix[t][l] += 1
        if t == l:
            agree += 1
    n_valid = len(labeled) - bad
    if n_valid == 0:
        return {"n_labeled": len(labeled), "n_valid": 0, "bad": bad, "pending": True}
    return {"n_labeled": len(labeled), "n_valid": n_valid, "bad": bad,
            "agree": agree, "agreement": agree / n_valid, "matrix": matrix, "pending": False}


def do_agreement(set_path, json_path=None):
    with open(set_path, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    res = compute_agreement(rows)
    total = len(rows)
    if res["pending"]:
        print("VERDICT: PENDING-LABELS（%d/%d 已标注=等待独立标注面·D-20260930-17 ①）"
              % (res["n_labeled"], total))
        return 2
    print("混淆矩阵 [tool][label]  label=0 1 2")
    for t in (0, 1, 2):
        print("  tool=%d            %6d %3d %3d" % (t, *res["matrix"][t]))
    pct = res["agreement"] * 100
    if res["bad"]:
        print("WARN 非法档位行跳过 %d（诚实报告）" % res["bad"])
    if res["agreement"] >= THRESHOLD:
        print("VERDICT: AGREEMENT-OK %.1f%% (%d/%d) ≥85% 判负面可用（D-20260930-23 ②）"
              % (pct, res["agree"], res["n_valid"]))
        code = 0
    else:
        print("VERDICT: AGREEMENT-FAIL %.1f%% (%d/%d) <85% 禁用于点名（D-20260930-23 ②）"
              % (pct, res["agree"], res["n_valid"]))
        code = 1
    if json_path:
        with open(json_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"set": set_path, "total": total, **res}, f, ensure_ascii=False, indent=1)
        print("JSON: " + json_path)
    return code


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append((name, ok, detail))

    # S1 一致率数学：27/30 同档=90%
    rows = [{"score": 1, "label": 1}] * 27 + [{"score": 2, "label": 1}, {"score": 0, "label": 2}, {"score": 2, "label": 0}]
    res = compute_agreement(rows)
    run("S1 一致率 27/30=90%", not res["pending"] and abs(res["agreement"] - 0.9) < 1e-9 and res["agree"] == 27)
    # S2 混淆矩阵计数：matrix[1][1]=27·matrix[2][1]=1·matrix[0][2]=1·matrix[2][0]=1
    m = res["matrix"]
    run("S2 混淆矩阵四格", m[1][1] == 27 and m[2][1] == 1 and m[0][2] == 1 and m[2][0] == 1)
    # S3 <85% 判负面：24/30=80% → FAIL 面
    rows2 = [{"score": 1, "label": 1}] * 24 + [{"score": 2, "label": 1}] * 6
    res2 = compute_agreement(rows2)
    run("S3 80%<85% 判负面", abs(res2["agreement"] - 0.8) < 1e-9 and res2["agreement"] < THRESHOLD)
    # S4 零标注=PENDING
    run("S4 零标注 pending", compute_agreement([{"score": 2, "label": None}] * 30)["pending"] is True)
    # S5 非法档位跳过并单列：label=7 计 bad
    res5 = compute_agreement([{"score": 2, "label": 7}, {"score": 1, "label": 1}])
    run("S5 非法档位跳过", res5["bad"] == 1 and res5["n_valid"] == 1 and res5["agree"] == 1)
    # S6 种子确定性：同池同 seed 两抽=同集
    pool = list(range(100))
    run("S6 种子确定性", random.Random(SEED).sample(pool, 30) == random.Random(SEED).sample(pool, 30))
    # S7 工具分面：过程件 commit 降档承接（v1.2 口径一致性）
    run("S7 过程件分=1", commit_score(["results/_r449_resolve.py"], subject="S0 storm rescue r449") == 1)
    # S8 JSONL 往返：写读一致率等值
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "s.jsonl")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(p, "r", encoding="utf-8") as f:
            back = [json.loads(x) for x in f if x.strip()]
        res8 = compute_agreement(back)
        run("S8 JSONL 往返等值", res8["agreement"] == res["agreement"] and res8["matrix"] == res["matrix"])

    n_pass = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(("PASS " if ok else "FAIL ") + name + ("  [" + detail + "]" if detail else ""))
    print("SELFTEST %s %d/%d" % ("PASS" if n_pass == len(checks) else "FAIL", n_pass, len(checks)))
    return 0 if n_pass == len(checks) else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="计分器人工标注验证集工具（D-20260930-17 ①/D-23 ②）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="七司池种子抽样 N commit→JSONL 验证集（label 留空）")
    s.add_argument("--n", type=int, default=30)
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--out", required=True)
    a = sub.add_parser("agreement", help="已标注集→一致率+混淆矩阵（一致率<85%%=exit 1 禁上判负面）")
    a.add_argument("--set", required=True)
    a.add_argument("--json", default=None)
    sub.add_parser("selftest", help="自测（合成夹具零 git 依赖）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        sys.exit(_selftest())
    if args.cmd == "sample":
        sys.exit(do_sample(args.n, args.days, args.out))
    sys.exit(do_agreement(args.set, args.json))


if __name__ == "__main__":
    main()
