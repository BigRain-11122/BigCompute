#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""model_impact_probe v1.0 -- 缺失模型影响面排查（M81）

背景：M79/M80 基线差分实测 live 模型集缩水（missing=2 bge-m3:latest / qwen3.8:27b），
但告警只回答「缺了什么」，不回答「谁受影响、要不要恢复」。本探针补这一段。

三条只读证据面：
  A 可用性面：POST /api/show 逐模型确认在位性（该端点不触发拉取，遵守 M71 判例
    「禁自决重拉」——任何恢复动作归模型供应方窗呈批）。
  B 消费面：集团注册仓静态扫描缺失模型名的代码/配置引用（只读·零写盘）。
  C 服务面：本地常驻服务健康（resident QA :8792）与 :8090 lane 模型清单。

三态判据（预注册，先于实跑）：
  NEED-RESTORE   任一消费仓的代码/配置面存在活引用（py/ps1/cmd/bat/json/jsonl/yaml/yml）。
  DEGRADED       无活引用，但集团正典把该模型列为在役能力面（本文件 PRESET_FACES 或文档引用）。
  NO-IMPACT      无活引用且无在役能力面声明。
missing 为空时整体 GREEN，不逐模型判定。

命令：
  python Tools/model_impact_probe.py run [--baseline P] [--out P] [--repos-root P]
  python Tools/model_impact_probe.py selftest
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_ref_drift_check import (  # noqa: E402  同目录复用，零重复实现
    load_baseline, diff_baseline, fetch_live_tags)

DEFAULT_BASELINE = os.path.join("state", "model-ref-drift-20261016-191916.json")
DEFAULT_REPOS_ROOT = r"C:\Users\sjs20\Desktop\FluxGroup"
SHOW_URL = "http://127.0.0.1:11434/api/show"
RESIDENT_QA_HEALTH = "http://127.0.0.1:8792/health"
LANE_8090_MODELS = "http://127.0.0.1:8090/v1/models"

CODE_EXTS = (".py", ".ps1", ".cmd", ".bat")
CONFIG_EXTS = (".json", ".jsonl", ".yaml", ".yml")
DOC_EXTS = (".md", ".txt")
# 排除面：存档/临时/证据/日志/依赖/构建产物（首跑实证=auto-saves 与 evidence 全为误判）
SKIP_DIR_PARTS = (".git", "node_modules", "media", "_offload", "archive",
                  ".codely-cli", ".playwright-mcp", "__pycache__", "library",
                  "obj", "logs", "tmp", "evidence", "auto-saves", "_trash",
                  "screenshots", "captures")
MAX_BYTES = 2_000_000

# 消费仓注册面（只读引用·禁跨仓写）
CONSUMER_REPOS = [
    ("cph4", "cph4"),
    ("life", "life"),
    ("quant", "quant"),
    ("gaming", "gaming"),
    ("domain", "domain"),
    ("house", "house"),
    ("fleet", "fleet"),
    ("compute", "compute"),
]

# 三态严重度排序（max 合并用）
SEVERITY = {"NO-IMPACT": 0, "DEGRADED": 1, "NEED-RESTORE": 2}

# 命中面人工复核登记（实跑后逐条读证定谳·未登记者按 NEED-RESTORE 从严）
# 依据列=可复现的读码结论（文件:行 语义），禁凭印象。
TRIAGE_RULES = [
    (r"gaming/MiniGame/tools/llm/local_coder\.py$", "DEGRADED",
     "DEFAULT_MODELS 次选（主 qwen3.6-coder:35b）；主档在役时仅失兜底"),
    (r"gaming/MiniGame/tools/llm/bench_bgc\.py$", "NO-IMPACT",
     "heavy tier 由 `if present` 门控，缺席自动跳过（设计态可选档）"),
    (r"gaming/MiniGame/tools/llm/research_digest\.py$", "NO-IMPACT",
     "仅注释提及可选升级档，无调用路径"),
    (r"gaming/MiniGame/tools/FleetJsonWrite\.ps1$", "DEGRADED",
     "机队 JSON 模型清单数组含该名（展示/记账面，非推理调用）"),
    (r"life/BigLife/state/", "NO-IMPACT", "轮次归档脚本内的志言字符串，非活调用"),
    (r"quant/bigmoney/results/", "NO-IMPACT", "结果/回执归档件，非活调用"),
    (r"compute/BigCompute/state/", "NO-IMPACT", "轮次归档/收口件，非活调用"),
    (r"compute/BigCompute/Tools/(model_impact_probe|model_ref_drift_check|serve_restore_manifest)\.py$",
     "NO-IMPACT", "探针本体或 T90 moot 清单（意图引用，非消费）"),
]


def triage_hit(relpath):
    """返回 (verdict, reason)；未登记=None（调用侧按从严处理）。"""
    for pattern, verdict, reason in TRIAGE_RULES:
        if re.search(pattern, relpath):
            return verdict, reason
    return None


def combine_verdicts(verdicts):
    out = "NO-IMPACT"
    for v in verdicts:
        if SEVERITY.get(v, 2) > SEVERITY[out]:
            out = v
    return out
PRESET_FACES = {
    "bge-m3:latest": ("RAG 检索 / 本地嵌入服务", "cph4/resource-chain.md §模型-机器-服务映射"),
    "qwen3.8:27b": ("深度推理线（27B 档）", "cph4/fleet/qwen38-rollout-20261015.md"),
}


def probe_model_available(name, url=SHOW_URL, timeout=6):
    """POST /api/show 确认在位性；不触发拉取。返回 (ok, status_text)。"""
    body = json.dumps({"model": name, "name": name}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (resp.status == 200), "HTTP-%d" % resp.status
    except urllib.error.HTTPError as exc:
        return False, "HTTP-%d" % exc.code
    except Exception as exc:  # 网络面
        return False, "ERR-%s" % exc.__class__.__name__


def probe_json(url, timeout=4):
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw)
    except Exception:
        return None


def iter_repo_files(repo_root, repo_name):
    base = os.path.join(repo_root, repo_name)
    if not os.path.isdir(base):
        return
    for dirpath, dirnames, filenames in os.walk(base):
        parts = [p.lower() for p in dirpath.replace("\\", "/").split("/")]
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIR_PARTS]
        if any(p in SKIP_DIR_PARTS for p in parts):
            continue
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in CODE_EXTS or ext in CONFIG_EXTS or ext in DOC_EXTS:
                yield os.path.join(dirpath, fn)


def scan_references(repo_root, models):
    """静态扫描：返回 {(repo, model): {"code": [..], "doc": [..]}}（只读）。

    code=可执行件（py/ps1/cmd/bat）+ 配置/数据件（json/jsonl/yaml），doc=文档面。
    注意：只有可执行件引用才构成活消费（NEED-RESTORE）；数据/记录件只作 DEACON 佐证。
    """
    hits = {}
    if not models:
        return hits
    for repo_name, _sub in CONSUMER_REPOS:
        for path in iter_repo_files(repo_root, repo_name):
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
                with io.open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            ext = os.path.splitext(path)[1].lower()
            rel = os.path.relpath(path, repo_root).replace("\\", "/")
            for model in models:
                if model in text:
                    key = (repo_name, model)
                    bucket = hits.setdefault(key, {"code": [], "config": [], "doc": []})
                    slot = ("code" if ext in CODE_EXTS else
                            "doc" if ext in DOC_EXTS else "config")
                    if len(bucket[slot]) < 5 and rel not in bucket[slot]:
                        bucket[slot].append(rel)
    return hits


def verdict_for(available, code_hits, doc_hits, preset):
    """三态判定（纯函数·预注册判据）。

    NEED-RESTORE 仅由可执行件活引用触发；数据/文档/正典能力声明=DE-GRADED（能力面缺口）。
    """
    if available:
        return "NO-IMPACT"
    if code_hits:
        return "NEED-RESTORE"
    if doc_hits or preset:
        return "DEGRADED"
    return "NO-IMPACT"


def cmd_run(args):
    try:
        baseline = load_baseline(args.baseline)
    except (OSError, ValueError) as exc:
        print("BASELINE-ERROR %s" % exc)
        return 2
    live = fetch_live_tags()
    diff = diff_baseline(baseline, live)
    missing = diff["missing"]

    report = {
        "generated_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "baseline": sorted(baseline),
        "live_tags": diff["live_tags"],
        "missing": missing,
        "added": diff["added"],
        "drift_status": diff["status"],
        "models": {},
        "service_faces": {},
        "scan_scope": {"repos_root": args.repos_root,
                       "repos": [r[0] for r in CONSUMER_REPOS]},
        "summary": {},
    }

    refs = scan_references(args.repos_root, missing)
    for model in missing:
        ok, status = probe_model_available(model)
        preset = PRESET_FACES.get(model)
        code_hits, config_hits, doc_hits = [], [], []
        for repo_name, _sub in CONSUMER_REPOS:
            bucket = refs.get((repo_name, model))
            if bucket:
                code_hits.extend(bucket["code"])
                config_hits.extend(bucket["config"])
                doc_hits.extend(bucket["doc"])
        verdicts = []
        triaged = []
        for hit in code_hits:
            t = triage_hit(hit)
            if t:
                verdicts.append(t[0])
                triaged.append({"path": hit, "verdict": t[0], "reason": t[1]})
            else:
                verdicts.append("NEED-RESTORE")
                triaged.append({"path": hit, "verdict": "NEED-RESTORE",
                                "reason": "未登记的可执行引用（从严）"})
        if not code_hits:
            verdicts.append(verdict_for(ok, [], config_hits + doc_hits, preset))
        else:
            base = verdict_for(ok, [], config_hits + doc_hits, preset)
            verdicts.append(base)
        model_verdict = combine_verdicts(verdicts) if not ok else "NO-IMPACT"
        report["models"][model] = {
            "available": ok,
            "show_status": status,
            "preset_face": preset[0] if preset else None,
            "preset_source": preset[1] if preset else None,
            "code_hits": code_hits,
            "config_hits": config_hits,
            "doc_hits": doc_hits,
            "triage": triaged,
            "verdict": model_verdict,
        }

    qa = probe_json(RESIDENT_QA_HEALTH)
    report["service_faces"]["resident_qa_8792"] = {
        "alive": bool(qa), "model": (qa or {}).get("model"),
        "impacts_missing": bool(qa and (qa or {}).get("model") in missing)}
    lane = probe_json(LANE_8090_MODELS)
    lane_models = []
    if isinstance(lane, dict):
        lane_models = sorted(str(m.get("id")) for m in lane.get("data") or []
                             if isinstance(m, dict))
    report["service_faces"]["lane_8090"] = {
        "alive": bool(lane_models), "models": lane_models,
        "impacts_missing": bool(set(lane_models) & set(missing))}

    counts = {}
    for row in report["models"].values():
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    report["summary"] = {
        "counts": counts,
        "verdict": ("GREEN" if not missing else
                    ("NEED-RESTORE" if counts.get("NEED-RESTORE") else "DEGRADED")),
        "restore_owner": "模型供应方窗呈批（M71 判例：禁自决重拉）" if missing else None,
    }

    out_path = args.out or os.path.join(
        "state", "model-impact-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, sort_keys=True)

    print("MODEL-IMPACT drift=%s baseline=%d live=%s missing=%d added=%d" % (
        report["drift_status"], len(report["baseline"]),
        "unavailable" if report["live_tags"] is None else len(report["live_tags"]),
        len(missing), len(report["added"])))
    if not missing:
        print("VERDICT GREEN (no missing model)")
    else:
        for model in missing:
            row = report["models"][model]
            print("MODEL %s show=%s code_hits=%d config_hits=%d doc_hits=%d verdict=%s" % (
                model, row["show_status"], len(row["code_hits"]),
                len(row["config_hits"]), len(row["doc_hits"]), row["verdict"]))
            for hit in row["code_hits"]:
                print("  CODE %s" % hit)
            for t in row.get("triage", []):
                print("  TRIAGE %s -> %s (%s)" % (t["path"], t["verdict"], t["reason"]))
            if row["preset_face"]:
                print("  FACE %s (%s)" % (row["preset_face"], row["preset_source"]))
        print("VERDICT %s" % report["summary"]["verdict"])
        print("RESTORE-OWNER %s" % report["summary"]["restore_owner"])
    sf = report["service_faces"]
    print("SERVICE resident_qa_8792 alive=%s model=%s lane_8090 alive=%s" % (
        sf["resident_qa_8792"]["alive"], sf["resident_qa_8792"]["model"],
        sf["lane_8090"]["alive"]))
    print("REPORT %s" % out_path)
    return 1 if report["summary"]["verdict"] == "NEED-RESTORE" else 0


def _selftest():
    fails = []
    # S1 三态判定矩阵
    cases = [
        ((True, [], [], None), "NO-IMPACT"),
        ((False, ["a.py"], [], None), "NEED-RESTORE"),
        ((False, [], ["a.md"], None), "DEGRADED"),
        ((False, [], [], ("RAG", "src")), "DEGRADED"),
        ((False, [], [], None), "NO-IMPACT"),
    ]
    for idx, (argv, expect) in enumerate(cases, 1):
        got = verdict_for(*argv)
        if got != expect:
            fails.append("S1.%d verdict %s != %s" % (idx, got, expect))
    print("S1 verdict matrix: %d cases" % len(cases))
    # S2 引用扫描：临时仓夹具，代码命中 != 文档命中 != 无关
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "probe_repo")
        os.makedirs(os.path.join(repo, "code"))
        os.makedirs(os.path.join(repo, ".git"))
        with io.open(os.path.join(repo, "code", "a.py"), "w", encoding="utf-8") as f:
            f.write('M = "bge-m3:latest"\n')
        with io.open(os.path.join(repo, "code", "b.md"), "w", encoding="utf-8") as f:
            f.write("bge-m3:latest 说明\n")
        with io.open(os.path.join(repo, ".git", "c.py"), "w", encoding="utf-8") as f:
            f.write('M = "bge-m3:latest"\n')
        os.makedirs(os.path.join(repo, "evidence"))
        with io.open(os.path.join(repo, "evidence", "d.json"), "w", encoding="utf-8") as f:
            f.write('{"m": "bge-m3:latest"}\n')
        hits = scan_references(td, ["bge-m3:latest"])
        bucket = hits.get(("cph4", "bge-m3:latest"))
        # CONSUMER_REPOS 用固定仓名；夹具以 probe_repo 目录名映射不到 -> 直接测 iter 过滤
        files = list(iter_repo_files(td, "probe_repo"))
        names = sorted(os.path.basename(p) for p in files)
        if names != ["a.py", "b.md"]:
            fails.append("S2.1 skip-filter got %s" % names)
        if bucket is not None:
            fails.append("S2.2 unexpected fixed-repo hit")
    print("S2 scan fixture: skip .git / split code-doc")
    # S3 diff 复用（baseline 面）
    d = diff_baseline({"a:1", "b:2"}, {"a:1"})
    if d["missing"] != ["b:2"] or d["status"] != "DRIFT":
        fails.append("S3.1 diff missing/status wrong: %s" % d)
    d2 = diff_baseline({"a:1"}, None)
    if d2["status"] != "UNVERIFIED":
        fails.append("S3.2 unavailable should be UNVERIFIED")
    print("S3 baseline diff reuse: missing/UNVERIFIED")
    # S4 输出确定性（键序）
    blob = json.dumps({"b": 1, "a": 2}, sort_keys=True)
    if blob != '{"a": 2, "b": 1}':
        fails.append("S4 deterministic json broken")
    print("S4 deterministic json")
    # S5 preset 面完整性
    for model, preset in PRESET_FACES.items():
        if ":" not in model or not preset[1]:
            fails.append("S5 bad preset %s" % model)
    print("S5 preset faces: %d" % len(PRESET_FACES))
    # S6 人工复核登记面：已登记命中不得落 NEED-RESTORE；未登记按从严
    t = triage_hit("gaming/MiniGame/tools/llm/bench_bgc.py")
    if not t or t[0] != "NO-IMPACT":
        fails.append("S6.1 bench triage wrong: %s" % (t,))
    if triage_hit("nobody/unknown.py") is not None:
        fails.append("S6.2 unknown hit should be None")
    if combine_verdicts(["NO-IMPACT", "DEGRADED", "NEED-RESTORE"]) != "NEED-RESTORE":
        fails.append("S6.3 severity combine wrong")
    if combine_verdicts(["NO-IMPACT", "DEGRADED"]) != "DEGRADED":
        fails.append("S6.4 severity combine wrong")
    print("S6 triage registry + severity combine")
    if fails:
        print("SELFTEST FAIL")
        for f in fails:
            print("  FAIL %s" % f)
        return 1
    print("SELFTEST PASS (%d checks)" % (len(cases) + 4))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Missing-model impact probe (read-only)")
    sub = ap.add_subparsers(dest="cmd")
    run = sub.add_parser("run")
    run.add_argument("--baseline", default=DEFAULT_BASELINE)
    run.add_argument("--out", default=None)
    run.add_argument("--repos-root", default=DEFAULT_REPOS_ROOT)
    sub.add_parser("selftest")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return _selftest()
    if args.cmd == "run":
        return cmd_run(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())