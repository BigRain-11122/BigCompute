#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigCompute 知识共通律本司面审计探针（O-20260930-1556 ③④ 承接·explore E38）

知识共通律（O-20260930-1556）③=知识件产出批内必落 git 同步正典面（盘面-only 收口=违例）·
④=盘面零同步知识载体盘点（@决策委员会+CPH4 方案过会）。本工具=本司域自审计证据件：
  J1 未同步知识件扫（git status 知识域过滤·批内在飞信息位）
  J2 知识载体 git 追踪门（本司知识正典清单逐件 tracked 判定）
  J3 技能源面（.codely-cli/skills 安装件必有 Tools/skills git 源）
  J4 调研件面（docs/research/*.md 全 tracked）
  R1 豁免声明面（台账/心跳/证据 JSON/logs/_trash/workspace=运行时区·报告位非违例）
只读零台面变更（唯一写=JSON 证据落 state/）；零跨仓动作。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

# 本司知识正典载体清单（O-1556 ③「机制/规则/判例/SOP/提案/队列」面）
KNOWLEDGE_CARRIERS = [
    "state/runbook.md", "state/proposals.md",
    "state/queue/main.md", "state/queue/tech.md", "state/queue/explore.md",
    "Tools/iteration_prompt.txt", "CODELY.md", "BLUEPRINT.md",
    "docs/research-dept-charter.md", "HQ-FEEDBACK.md", "tasks/TASKS.md",
]
# 知识域前缀（J1 git status 过滤）
KNOWLEDGE_PREFIXES = ("docs/", "Tools/", "tasks/", "qa/", "orders/",
                      "state/runbook.md", "state/proposals.md", "state/queue/")
# R1 运行时豁免区（报告位·非违例：台账/心跳/证据数据面）
RUNTIME_EXEMPT = ["state/ (rounds.log+heartbeat+*.json 证据台账面)", "logs/",
                  "docs/_trash/ (7 天观察至 10-05)", ".codely-cli/ (workspace 安装面·源在 Tools/skills/)"]


def _git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def tracked_set(repo, paths):
    if not paths:
        return set()
    rc, out = _git(repo, "ls-files", "--", *paths)
    return set(out.split()) if rc == 0 else set()


def audit(repo, carriers=None):
    carriers = KNOWLEDGE_CARRIERS if carriers is None else carriers
    exist = [p for p in carriers if os.path.exists(os.path.join(repo, p))]
    j2_missing = sorted(p for p in exist if p not in tracked_set(repo, exist))
    # J3 技能源面（git 口径一律正斜杠）
    inst_dir = os.path.join(repo, ".codely-cli", "skills")
    installed = sorted(os.listdir(inst_dir)) if os.path.isdir(inst_dir) else []
    src_paths = ["Tools/skills/%s/SKILL.md" % s for s in installed]
    tracked_srcs = tracked_set(repo, src_paths)
    j3_missing = [s for s, p in zip(installed, src_paths) if p not in tracked_srcs]
    # J4 调研件面
    res_dir = os.path.join(repo, "docs", "research")
    on_disk = sorted(os.listdir(res_dir)) if os.path.isdir(res_dir) else []
    res_paths = ["docs/research/%s" % f for f in on_disk if f.endswith(".md")]
    tracked_res = tracked_set(repo, res_paths)
    j4_untracked = [f for f, p in zip([f for f in on_disk if f.endswith(".md")], res_paths) if p not in tracked_res]
    # J1 未同步知识件（批内在飞信息位）
    rc, out = _git(repo, "status", "--porcelain")
    j1_pending = []
    if rc == 0:
        for line in out.splitlines():
            path = line[3:].strip('"')
            if path.startswith(KNOWLEDGE_PREFIXES):
                j1_pending.append({"status": line[:2].strip(), "path": path})
    violations = ({"face": "J2-carriers-untracked", "items": j2_missing},
                  {"face": "J3-skill-source-missing", "items": j3_missing},
                  {"face": "J4-research-untracked", "items": j4_untracked})
    viol_total = sum(len(v["items"]) for v in violations)
    return {
        "tool": "knowledge_sync_audit v1.0", "order": "O-20260930-1556", "repo": repo,
        "verdict": "KNOWLEDGE-SYNC-GREEN" if viol_total == 0 else "KNOWLEDGE-SYNC-RED",
        "violations_total": viol_total,
        "j1_pending_sync_inflight": j1_pending,
        "j2_carriers_checked": len(exist), "j2_missing": j2_missing,
        "j3_installed_skills": installed, "j3_missing_source": j3_missing,
        "j4_research_on_disk": len(on_disk), "j4_untracked": j4_untracked,
        "r1_runtime_exempt_declared": RUNTIME_EXEMPT,
    }


def cmd_check(args):
    res = audit(os.path.abspath(args.repo))
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(args.repo, "state", "knowledge-sync-audit-%s.json" % ts)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("verdict=%s violations=%d" % (res["verdict"], res["violations_total"]))
    for v in (res["j2_missing"], res["j3_missing_source"], res["j4_untracked"]):
        for item in v:
            print("  violation:", item)
    print("pending_inflight=%d evidence=%s" % (len(res["j1_pending_sync_inflight"]), out_path))
    return 0 if res["verdict"] == "KNOWLEDGE-SYNC-GREEN" else 1


def _fixture_repo(tmp):
    repo = os.path.join(tmp, "fx")
    os.makedirs(repo)
    for d in ("state/queue", "docs/research", "Tools/skills/src-skill", ".codely-cli/skills/src-skill", ".codely-cli/skills/ghost"):
        os.makedirs(os.path.join(repo, *d.split("/")), exist_ok=True)
    files = {"state/runbook.md": "rb", "state/proposals.md": "pp", "state/queue/main.md": "qm",
             "docs/research/r1.md": "r1", "docs/research/r2.md": "r2",
             "Tools/skills/src-skill/SKILL.md": "sk", ".codely-cli/skills/src-skill/SKILL.md": "sk", ".codely-cli/skills/ghost/SKILL.md": "gh"}
    for rel, txt in files.items():
        with open(os.path.join(repo, *rel.split("/")), "w", encoding="utf-8") as f:
            f.write(txt)
    for a in ("init", "config user.email fx@fx.local", "config user.name fx",
              "add state/runbook.md state/queue/main.md docs/research/r1.md Tools/skills/src-skill/SKILL.md", "commit -m fx"):
        subprocess.run(["git", "-C", repo] + a.split(), capture_output=True)
    return repo


def cmd_selftest(args):
    ok = [0]

    def chk(name, cond):
        ok[0] += 1 if cond else 0
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    tmp = tempfile.mkdtemp(prefix="ksa-fx-")
    repo = _fixture_repo(tmp)
    carriers = ["state/runbook.md", "state/proposals.md", "state/queue/main.md"]
    res = audit(repo, carriers=carriers)
    chk("S1 fixture baseline RED (proposals 未追踪)", res["verdict"] == "KNOWLEDGE-SYNC-RED")
    chk("S2 J2 唯一缺口=state/proposals.md", res["j2_missing"] == ["state/proposals.md"])
    chk("S3 J3 唯一缺口=ghost（无 git 源）", res["j3_missing_source"] == ["ghost"])
    chk("S4 J4 唯一缺口=r2.md", res["j4_untracked"] == ["r2.md"])
    chk("S5 违例计数=3", res["violations_total"] == 3)
    subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "fx2"], capture_output=True)
    os.makedirs(os.path.join(repo, "Tools", "skills", "ghost"), exist_ok=True)
    with open(os.path.join(repo, "Tools", "skills", "ghost", "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("gh")
    subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "fx3"], capture_output=True)
    res2 = audit(repo, carriers=carriers)
    chk("S6 修复后 GREEN", res2["verdict"] == "KNOWLEDGE-SYNC-GREEN" and res2["violations_total"] == 0)
    chk("S7 豁免声明面在档", len(res["r1_runtime_exempt_declared"]) >= 4)
    print("selftest %d/7 %s" % (ok[0], "PASS" if ok[0] == 7 else "FAIL"))
    return 0 if ok[0] == 7 else 1


def main():
    ap = argparse.ArgumentParser(description="知识共通律本司面审计（O-20260930-1556 承接）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--repo", default="."); c.set_defaults(func=cmd_check)
    s = sub.add_parser("selftest"); s.set_defaults(func=cmd_selftest)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
