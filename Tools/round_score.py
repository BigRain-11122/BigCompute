#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""round_score.py — 产品优先律轮计分器（D-20260929-07 ②③·P-2026-09-29-07·BC-P-24/T33 工程面）

计分律（Executive Protocol v1.1·CEO 原话锚「产出落地很少…结果早点出」）：
  2 分=能跑/能看/能用实物（可跑脚本/真实出数报告/可看账本）
  1 分=实际文件改动（非 0 类亦非 2 类的实改面）
  0 分=纯 md 文档与纯记账（心跳/日清/簿记行/export 刷新）+例行验证证据（qa 烟测=每轮机制例行产出
      非产品增量，入 0 类防全员 2 分稀释判负面）
  连续 24h 全部 commit 均 0 分=空转判负（值守轮点名面·D-20260929-07 ③）

机械分类法（文件路径 → 类）：
  2 类：Tools/**/*.(py|ps1|cs)=可跑脚本｜docs/ops/*.jsonl=可看账本｜state/*.(json|txt) 非 0 类名单=真实出数报告
  0 类：**/*.md｜state/{heartbeat.txt,rounds.log,runbook.md,queue/*,proposals.md}｜docs/status-export.json
      ｜qa/*｜Tools/iteration_prompt.txt｜Tools/skills/**
  1 类：其余实改
commit 分=其变更文件类的 max；日/窗聚合=24h 滚动窗内 commit 分分布。

用法：
  python Tools/round_score.py score [--hours 24] [--json state/round-score-<ts>.json]
  python Tools/round_score.py selftest
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

ZERO_EXACT = {
    "state/heartbeat.txt",
    "state/rounds.log",
    "state/runbook.md",
    "state/proposals.md",
    "docs/status-export.json",
    "tools/iteration_prompt.txt",
}
ZERO_PREFIX = ("state/queue/", "qa/", "tools/skills/")


def classify_path(path):
    """返回 0/1/2（文件类·判据见模块 docstring）。"""
    p = path.replace("\\", "/").lower()
    if p.endswith(".md") or p in ZERO_EXACT or p.startswith(ZERO_PREFIX):
        return 0
    if p.startswith("tools/") and p.endswith((".py", ".ps1", ".cs")):
        return 2
    if p.startswith("docs/ops/") and p.endswith(".jsonl"):
        return 2
    if p.startswith("state/") and p.endswith((".json", ".txt")):
        return 2
    return 1


def commit_score(paths):
    """commit 分=变更文件类 max（空变更集=0·merge 类如实）。"""
    return max((classify_path(p) for p in paths), default=0)


def _git_log(hours):
    since = "%d hours ago" % hours
    out = subprocess.run(
        ["git", "log", "--since", since, "--pretty=format:@@%h%x09%ad%x09%s", "--date=iso", "--name-only"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    commits, cur = [], None
    for line in out.splitlines():
        if line.startswith("@@"):
            if cur:
                commits.append(cur)
            parts = line[2:].split("\t", 2)  # 空首部 subject 兼容（pad 法）
            short = parts[0]
            date = parts[1] if len(parts) > 1 else ""
            subject = parts[2] if len(parts) > 2 else ""
            cur = {"short": short, "date": date, "subject": subject, "paths": []}
        elif line.strip() and cur is not None:
            cur["paths"].append(line.strip())
    if cur:
        commits.append(cur)
    for c in commits:
        c["score"] = commit_score(c["paths"])
    return commits


def score(hours, json_path=None):
    commits = _git_log(hours)
    n = len(commits)
    dist = {0: 0, 1: 0, 2: 0}
    for c in commits:
        dist[c["score"]] += 1
        top = sorted(set(classify_path(p) for p in c["paths"]), reverse=True)
        print("%s score=%d classes=%s %s" % (c["short"], c["score"], top, c["subject"][:48]))
    if n == 0:
        verdict = "NO-COMMITS（%dh 窗零 commit=信息态·非判负）" % hours
        code = 2
    elif dist[1] == 0 and dist[2] == 0:
        verdict = "IDLE-ALL-ZERO（连续 %dh 全部 commit 均 0 分=空转判负点名面·D-20260929-07 ③）" % hours
        code = 1
    else:
        verdict = "PRODUCT-%dh max=%d n=%d n2=%d n1=%d n0=%d" % (hours, max(dist), n, dist[2], dist[1], dist[0])
        code = 0
    print("VERDICT: " + verdict)
    if json_path:
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        with open(json_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"hours": hours, "verdict": verdict, "dist": dist, "commits": commits},
                      f, ensure_ascii=False, indent=1)
        print("JSON: " + json_path)
    return code, verdict, dist, commits


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append((name, ok, detail))

    # S1 分类面九代表位
    cases = [
        ("Tools/round_score.py", 2), ("tools/bake_spike_assets/run_bake.ps1", 2),
        ("Tools/bake_spike_assets/BakeSpike.cs", 2), ("docs/ops/batch-pool-stock-v1.jsonl", 2),
        ("state/round-score-20260930.json", 2), ("state/compliance-gate-20260930-1219.txt", 2),
        ("docs/ops/paypoint-compliance-map-v1.md", 0), ("state/heartbeat.txt", 0),
        ("state/rounds.log", 0), ("state/runbook.md", 0), ("state/queue/main.md", 0),
        ("state/proposals.md", 0), ("docs/status-export.json", 0),
        ("qa/smoke-20260930-1259.log", 0), ("qa/page-20260930.png", 0),
        ("Tools/iteration_prompt.txt", 0), ("Tools/skills/x/SKILL.md", 0),
        ("assets/misc.bin", 1),
    ]
    for p, want in cases:
        got = classify_path(p)
        run("S1 分类 %s=%d" % (p, want), got == want, "got=%d" % got)
    # S2 commit 分=max（2+0 混合=2）
    run("S2 commit max 混合", commit_score(["state/heartbeat.txt", "Tools/x.py"]) == 2)
    # S3 空变更集=0
    run("S3 空集=0", commit_score([]) == 0)
    # S4 判负面：全 0 commit 列表→IDLE-ALL-ZERO 判定面（纯函数判定·不碰 git）
    zero_only = [{"short": "aaa", "date": "d", "subject": "s", "paths": ["state/heartbeat.txt"], "score": 0}]
    run("S4 全零=判负面", (lambda d: d[1] == 0 and d[2] == 0)(  # dist[1]==dist[2]==0 → 判负分支
        {0: 1, 1: 0, 2: 0}) and all(c["score"] == 0 for c in zero_only))
    # S5 单 0 commit 与混合 commit 的聚合分布数学
    mixed = [{"score": 0}, {"score": 2}, {"score": 1}]
    dist = {0: 0, 1: 0, 2: 0}
    for c in mixed:
        dist[c["score"]] += 1
    run("S5 分布数学", dist == {0: 1, 1: 1, 2: 1})

    n_pass = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(("PASS " if ok else "FAIL ") + name + ("  [" + detail + "]" if detail else ""))
    print("SELFTEST %s %d/%d" % ("PASS" if n_pass == len(checks) else "FAIL", n_pass, len(checks)))
    return 0 if n_pass == len(checks) else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="产品优先律轮计分器（2/1/0 机械计分+24h 空转判负）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="近 N 小时 commit 计分+24h 判负面")
    s.add_argument("--hours", type=int, default=24)
    s.add_argument("--json", default=None)
    sub.add_parser("selftest", help="自测（纯函数夹具零 git 依赖）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        sys.exit(_selftest())
    code, _, _, _ = score(args.hours, args.json)
    sys.exit(code)


if __name__ == "__main__":
    main()
