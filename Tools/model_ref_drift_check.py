#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""model_ref_drift_check.py — Tools 模型引用悬空面复扫探针（tech T100·BC-P-77 批活转化件）

背景（T99 判例）：CEO Qwen v3 final 切换令删除旧 qwen2.5 家族后，qa_smoke.py OLLAMA_MODEL
硬编码悬空（满探针路径 /api/generate 缺模自动拉副作用=违清除令面）——同类面防复发探针：
本仓 Tools/**/*.py 递归面（含 resident_qa_server.py owner-face·只读不碰 T98 退避律；T102
v1.2 由单层 Tools/*.py 扩为递归——Tools/fulfillment/*.py L2 轻量契约族原为盲区）模型名引用
字面量 vs live /api/tags 清单对照 → 悬空引用报告。

用法：python Tools/model_ref_drift_check.py check [--out <json>]
      python Tools/model_ref_drift_check.py baseline-diff [--baseline <json>] [--strict]
      python Tools/model_ref_drift_check.py selftest

判据（预注册）：
  J1 纯只读：扫描面零写零生成调用（/api/tags 唯一网络面·2s 超时·零自动拉模副作用）
  J2 fail-open：API 不可达 → 引用标 UNVERIFIED 照常出报告（探针坏不阻塞轮·T44 同律）
  J3 悬空判定：引用名 ∉ live tags → DANGLING；EXEMPT_HISTORICAL 文件集（T101② moot
     清单/历史试验探针）→ EXEMPT-HISTORICAL informational 态不计 dangling（在役面判据
     =dangling 清零·豁免面单列）
  J4 owner-face 只报告不触碰（T98 同仓单执行体退避律）
  J5 基线漂移（BC-P-102/M79）：baseline-diff 以基线件 live_tags 为参照，live 缺模 →
     一行 WARN MODEL-DRIFT missing=<n>（fail-open：API 不可达=UNVERIFIED 不告警不退出
     非零；默认 advisory 退出码 0，--strict 时 DRIFT 退 1）；新增模型仅 informational。
局限（v1.1 注记）：自件跳过（self fixtures 不自扫）；字符串字面量面（f-string/变量
拼接/注释外引用不扫）；bare 名按已知家族前缀集匹配+双形态归一（bare 名 ↔ live tag 基名）；
llama-server :8090 车道模型名非 ollama tags 域 → LANE-8090 informational 态（消费侧注记）。
"""
import argparse
import io
import json
import glob
import os
import re
import sys
import tempfile
import time

OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
TOOLS_GLOB = os.path.join("Tools", "**", "*.py")  # T102 v1.2：递归面（fulfillment 等子目录入扫）

# TAGGED_RE：带 tag 引用（"qwen2.5:7b-instruct" 形）；前缀黑名单=MIME/scheme 非模型形
TAGGED_RE = re.compile(r'["\']([a-z][a-z0-9._+\-]*:[a-z0-9._+\-]+)["\']')
PREFIX_BLOCKLIST = {"localhost", "text", "application", "image", "audio", "video",
                    "multipart", "message", "http", "https", "ftp", "file", "ws",
                    "wss", "utf", "gbk", "ascii", "sqlite", "content", "accept",
                    "sha256"}
# BARE_FAMILIES：无 tag 裸名家族前缀（bge-m3 等）
BARE_FAMILIES = ("qwen", "bge-m3", "llama", "glm", "deepseek", "minicpm",
                 "gemma", "nomic", "mistral", "codellama", "all-minilm")
BARE_RE = re.compile(r'["\']((?:%s)[a-z0-9._+\-]*)["\']' % "|".join(BARE_FAMILIES))
# 提取期黑名单：进程/可执行/JSON 键形假阳性类（非模型引用·v1.1 实扫发现）
BARE_BLOCKLIST = {"llama-server", "llama-server.exe", "llama.cpp",
                 "qwen", "qwen3", "qwen3.context_length"}
# 非 ollama 车道模型（llama-server :8090 面·v3 final 迁移正身）→ LANE-8090 态
NON_OLLAMA_LANES = {"qwen3.6-35b"}
# EXEMPT_HISTORICAL（T101②）：意图性/历史试验件豁免集——文件级（整件为 moot 清单或
# 已收线历史试验探针·引用删除模型=对象正身非悬空缺陷·消费侧注记面）：
#   serve_restore_manifest = T90 moot 正身（CEO v3 final 切换令=恢复 apply 永久停用·清单留库备用）
#   其余五件 = 历史试验探针（对象=已删旧档·试验已收线归档·live 改写无意义）
EXEMPT_HISTORICAL = {
    "serve_restore_manifest.py": "T90 moot master list (intentional deleted-model registry)",
    "bonsai_fleet_trial_probe.py": "historical trial probe (M13 bonsai trial)",
    "e12_package_anchor_probe.py": "historical trial probe (E12 package anchor)",
    "inference_energy_anchor.py": "historical trial probe (E39/E46 energy anchor)",
    "q8_trial_probe.py": "historical trial probe (T15 Q8 tier)",
    "serve_concurrency_probe.py": "historical trial probe (E31 concurrency baseline)",
}


def extract_refs(path):
    """单文件提取 → [(name, kind, lineno)]；kind=tagged|bare。J1 只读。"""
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    refs = []
    seen = set()
    for m in TAGGED_RE.finditer(text):
        name = m.group(1)
        if name.split(":", 1)[0] in PREFIX_BLOCKLIST:
            continue
        lineno = text.count("\n", 0, m.start()) + 1
        key = (name, lineno)
        if key not in seen:
            seen.add(key)
            refs.append((name, "tagged", lineno))
    for m in BARE_RE.finditer(text):
        name = m.group(1)
        if name in BARE_BLOCKLIST:
            continue
        lineno = text.count("\n", 0, m.start()) + 1
        key = (name, lineno)
        if key not in seen:
            seen.add(key)
            refs.append((name, "bare", lineno))
    return refs


def scan_tools(tools_glob=TOOLS_GLOB, base_dir="Tools"):
    """Tools/**/*.py 递归全扫 → {relpath（含子目录·posix 分隔）: [(name, kind, lineno), ...]}。"""
    out = {}
    for path in sorted(glob.glob(tools_glob, recursive=True)):
        if not os.path.isfile(path):
            continue
        if os.path.basename(path) == os.path.basename(__file__):
            continue  # v1.1：自件跳过（self fixtures 不自扫）
        refs = extract_refs(path)
        if refs:
            key = os.path.relpath(path, base_dir).replace(os.sep, "/")
            out[key] = refs
    return out


def fetch_live_tags(url=OLLAMA_TAGS_URL, timeout=2):
    """live /api/tags → set(names)；失败 → None（J2 fail-open）。"""
    try:
        import urllib.request
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return set(m.get("name", "") for m in data.get("models", []))
    except Exception:
        return None


def classify(scan, live_tags):
    """J3 分类 → report dict（api 不可达 → UNVERIFIED 面·EXEMPT-HISTORICAL 不计 dangling）。"""
    report = {"generated_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "api_available": live_tags is not None,
              "live_tags": sorted(live_tags) if live_tags is not None else None,
              "files": {}, "totals": {"refs": 0, "live": 0, "dangling": 0,
                                      "unverified": 0, "lane-8090": 0,
                                      "exempt-historical": 0}}
    for fname, refs in scan.items():
        rows = []
        for name, kind, lineno in refs:
            if live_tags is None:
                status = "UNVERIFIED"
            elif fname in EXEMPT_HISTORICAL or os.path.basename(fname) in EXEMPT_HISTORICAL:
                status = "EXEMPT-HISTORICAL"  # v1.2：子目录键 basename 归一（T102）
            elif name in NON_OLLAMA_LANES:
                status = "LANE-8090"
            elif name in live_tags or (":" not in name and any(
                    t.split(":", 1)[0] == name for t in live_tags)):
                status = "LIVE"
            else:
                status = "DANGLING"
            report["totals"][status.lower()] += 1
            report["totals"]["refs"] += 1
            rows.append({"name": name, "kind": kind, "line": lineno, "status": status})
        report["files"][fname] = rows
    return report


def load_baseline(path):
    """读基线件 live_tags → set；无 live_tags 或坏件 → ValueError（J5·不静默吞）"""
    with io.open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tags = data.get("live_tags")
    if not isinstance(tags, list) or not tags:
        raise ValueError("baseline missing non-empty live_tags: %s" % path)
    return set(tags)


def diff_baseline(baseline_tags, live_tags):
    """J5 基线对账 → dict（api 不可达=UNVERIFIED·缺模=DRIFT·否则 GREEN）"""
    result = {"generated_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "baseline": sorted(baseline_tags),
              "live_tags": sorted(live_tags) if live_tags is not None else None,
              "api_available": live_tags is not None,
              "missing": [], "added": [], "status": "UNVERIFIED"}
    if live_tags is None:
        return result
    result["missing"] = sorted(baseline_tags - live_tags)
    result["added"] = sorted(live_tags - baseline_tags)
    result["status"] = "DRIFT" if result["missing"] else "GREEN"
    return result


def cmd_baseline_diff(args):
    try:
        baseline = load_baseline(args.baseline)
    except (OSError, ValueError) as exc:
        print("BASELINE-ERROR %s" % exc)
        return 2
    live = fetch_live_tags()
    result = diff_baseline(baseline, live)
    out_path = args.out or os.path.join(
        "state", "model-ref-baseline-diff-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("BASELINE-DIFF status=%s baseline=%d live=%s missing=%d added=%d" % (
        result["status"], len(result["baseline"]),
        "unavailable" if result["live_tags"] is None else len(result["live_tags"]),
        len(result["missing"]), len(result["added"])))
    if result["status"] == "UNVERIFIED":
        print("API-UNAVAILABLE: fail-open J5 -> UNVERIFIED (no warning)")
    if result["missing"]:
        print("WARN MODEL-DRIFT missing=%d %s" % (
            len(result["missing"]), ",".join(result["missing"])))
    if result["added"]:
        print("INFO MODEL-DRIFT-ADDED added=%d %s" % (
            len(result["added"]), ",".join(result["added"])))
    print("REPORT %s" % out_path)
    if args.strict and result["status"] == "DRIFT":
        return 1
    return 0


def cmd_check(args):
    scan = scan_tools(args.tools_glob if args.tools_glob else TOOLS_GLOB)
    live = fetch_live_tags()
    report = classify(scan, live)
    out_path = args.out or os.path.join(
        "state", "model-ref-drift-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, sort_keys=True)
    t = report["totals"]
    print("MODEL-REF-DRIFT files=%d refs=%d live=%d dangling=%d unverified=%d lane-8090=%d exempt-historical=%d" % (
        len(report["files"]), t["refs"], t["live"], t["dangling"], t["unverified"],
        t["lane-8090"], t["exempt-historical"]))
    if live is None:
        print("API-UNAVAILABLE: fail-open J2 -> UNVERIFIED report written")
    else:
        for fname, rows in sorted(report["files"].items()):
            for r in rows:
                if r["status"] == "DANGLING":
                    print("DANGLING %s:%d %s (%s)" % (fname, r["line"], r["name"], r["kind"]))
    print("REPORT %s" % out_path)
    if live is not None and t["dangling"] > 0:
        return 1
    return 0


def _selftest():
    """S1-S8 合成夹具离线断言（零网络·J 式确定性）。"""
    ok = [0]

    def check(cond, label):
        ok[0] += 1
        print("S%d %s %s" % (ok[0], label, "PASS" if cond else "FAIL"))
        if not cond:
            raise SystemExit(1)

    with tempfile.TemporaryDirectory() as td:
        fx = os.path.join(td, "fixture.py")
        content = (
            'MODEL = "qwen2.5:7b-instruct"\n'          # tagged real-model shape
            'EMB = "bge-m3"\n'                          # bare known family
            'URL = "http://127.0.0.1:11434"\n'         # url must not match
            'LOCAL = "localhost:11434"\n'               # blocked prefix
            'MIME = "text/plain"\n'                     # blocked prefix
            'PORT = "127.0.0.1:8792"\n'                 # digit-start must not match
            'ENC = "utf-8"\n'                           # bare but not family
            'NEW = "qwen3:4b-instruct"\n'               # tagged second model
            'PROC = "llama-server.exe"\n'               # process name -> BARE_BLOCKLIST
            'CTXKEY = "qwen3.context_length"\n'         # json key shape -> BARE_BLOCKLIST
            'LANE = "qwen3.6-35b"\n')                   # :8090 lane model -> LANE-8090
        with io.open(fx, "w", encoding="utf-8") as f:
            f.write(content)
        before = open(fx, "rb").read()
        refs = extract_refs(fx)
        names = sorted(r[0] for r in refs)
        # S1 extraction shape（含 BARE_BLOCKLIST 拦截面）
        check(names == ["bge-m3", "qwen2.5:7b-instruct", "qwen3.6-35b",
                        "qwen3:4b-instruct"], "extraction tagged+bare blocklist")
        # S2 read-only (J1)
        check(open(fx, "rb").read() == before, "read-only bytes unchanged")
        # S3 classification live/dangling/lane（双形态归一：bge-m3 ↔ bge-m3:latest）
        rep = classify({os.path.basename(fx): refs},
                       {"bge-m3:latest", "qwen3:4b-instruct"})
        row_status = {r["name"]: r["status"] for r in rep["files"][os.path.basename(fx)]}
        check(row_status["qwen2.5:7b-instruct"] == "DANGLING"
              and row_status["qwen3:4b-instruct"] == "LIVE"
              and row_status["bge-m3"] == "LIVE"
              and row_status["qwen3.6-35b"] == "LANE-8090",
              "classify live/dangling/lane normalize")
        # S4 fail-open unverified (J2)
        rep2 = classify({os.path.basename(fx): refs}, None)
        check(all(r["status"] == "UNVERIFIED" for r in rep2["files"][os.path.basename(fx)])
              and rep2["api_available"] is False, "fail-open unverified")
        # S5 determinism
        rep3 = classify({os.path.basename(fx): refs},
                        {"bge-m3:latest", "qwen3:4b-instruct"})
        stripped = lambda r: {k: v for k, v in r.items() if k != "generated_ts"}
        check(stripped(rep3) == stripped(rep), "determinism double-run")
        # S6 exempt-historical（T101②：豁免文件引用删除模型=EXEMPT-HISTORICAL 非 dangling）
        exempt_name = "serve_restore_manifest.py"
        rep4 = classify({exempt_name: [("qwen2.5:7b-instruct", "tagged", 45)]},
                        {"bge-m3:latest", "qwen3:4b-instruct"})
        check(rep4["files"][exempt_name][0]["status"] == "EXEMPT-HISTORICAL"
              and rep4["totals"]["dangling"] == 0
              and rep4["totals"]["exempt-historical"] == 1,
              "exempt-historical not counted as dangling")
        # S7 递归子目录覆盖（T102 v1.2：预注册 fulfillment 假模型引用夹具——单层 glob 盲区面）
        sub = os.path.join(td, "fulfillment", "fixture_fulfillment.py")
        os.makedirs(os.path.dirname(sub), exist_ok=True)
        with io.open(sub, "w", encoding="utf-8") as f:
            f.write('L2_STALE = "qwen2.5:7b-instruct"  # pre-registered fulfillment stale ref\n'
                    'FLASH = "qwen3:4b-instruct"\n')
        scan = scan_tools(os.path.join(td, "**", "*.py"), base_dir=td)
        sub_key = "fulfillment/fixture_fulfillment.py"
        check(sub_key in scan and scan.get("fixture.py") is not None
              and sorted(r[0] for r in scan[sub_key]) == ["qwen2.5:7b-instruct", "qwen3:4b-instruct"],
              "recursive subdir fulfillment fixture coverage (posix subpath key)")
        # S8 子目录豁免 basename 归一（子路径键 vs EXEMPT_HISTORICAL 单层键名互认）
        sub_exempt_key = "nested/serve_restore_manifest.py"
        rep5 = classify({sub_exempt_key: [("qwen2.5:7b-instruct", "tagged", 3)]},
                        {"qwen3:4b-instruct"})
        check(rep5["files"][sub_exempt_key][0]["status"] == "EXEMPT-HISTORICAL"
              and rep5["totals"]["dangling"] == 0,
              "exempt-historical basename normalize in subdir key")
        # S9 baseline 读取（J5：真结构件 -> set）
        base_path = os.path.join(td, "baseline.json")
        with io.open(base_path, "w", encoding="utf-8") as f:
            json.dump({"live_tags": ["bge-m3:latest", "qwen3:4b-instruct"]}, f)
        base = load_baseline(base_path)
        check(base == {"bge-m3:latest", "qwen3:4b-instruct"}, "baseline load live_tags")
        # S10 缺模漂移 → DRIFT + missing 清单
        d1 = diff_baseline(base, {"bge-m3:latest"})
        check(d1["status"] == "DRIFT"
              and d1["missing"] == ["qwen3:4b-instruct"]
              and d1["added"] == []
              and d1["api_available"] is True,
              "baseline drift missing model")
        # S11 全在当前 → GREEN（新增仅 informational 不影响 status）
        d2 = diff_baseline(base, {"bge-m3:latest", "qwen3:4b-instruct", "qwen3:8b"})
        check(d2["status"] == "GREEN" and d2["missing"] == []
              and d2["added"] == ["qwen3:8b"], "baseline green with added info")
        # S12 fail-open（J5）：API 不可达 → UNVERIFIED·无 missing·不告警
        d3 = diff_baseline(base, None)
        check(d3["status"] == "UNVERIFIED" and d3["missing"] == []
              and d3["api_available"] is False, "baseline fail-open unverified")
        # S13 坏基线件 → ValueError（不静默吞）
        bad_path = os.path.join(td, "bad_baseline.json")
        with io.open(bad_path, "w", encoding="utf-8") as f:
            json.dump({"totals": {}}, f)
        try:
            load_baseline(bad_path)
            raised = False
        except ValueError:
            raised = True
        check(raised, "baseline invalid rejected")
    print("SELFTEST %d/%d PASS" % (ok[0], ok[0]))
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Tools model reference drift check (T100)")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="scan Tools/**/*.py vs live /api/tags")
    c.add_argument("--out", default=None)
    c.add_argument("--tools-glob", default=None)
    b = sub.add_parser("baseline-diff", help="compare live /api/tags against baseline live_tags")
    b.add_argument("--baseline", default=os.path.join(
        "state", "model-ref-drift-20261016-191916.json"))
    b.add_argument("--out", default=None)
    b.add_argument("--strict", action="store_true", help="exit 1 when drift detected")
    sub.add_parser("selftest", help="offline synthetic fixtures")
    args = p.parse_args()
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "baseline-diff":
        return cmd_baseline_diff(args)
    return _selftest()


if __name__ == "__main__":
    raise SystemExit(main())
