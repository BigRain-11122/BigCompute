#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""model_ref_drift_check.py — Tools 模型引用悬空面复扫探针（tech T100·BC-P-77 批活转化件）

背景（T99 判例）：CEO Qwen v3 final 切换令删除旧 qwen2.5 家族后，qa_smoke.py OLLAMA_MODEL
硬编码悬空（满探针路径 /api/generate 缺模自动拉副作用=违清除令面）——同类面防复发探针：
本仓 Tools/*.py（含 resident_qa_server.py owner-face·只读不碰 T98 退避律）模型名引用字面量
vs live /api/tags 清单对照 → 悬空引用报告。

用法：python Tools/model_ref_drift_check.py check [--out <json>]
      python Tools/model_ref_drift_check.py selftest

判据（预注册）：
  J1 纯只读：扫描面零写零生成调用（/api/tags 唯一网络面·2s 超时·零自动拉模副作用）
  J2 fail-open：API 不可达 → 引用标 UNVERIFIED 照常出报告（探针坏不阻塞轮·T44 同律）
  J3 悬空判定：引用名 ∉ live tags → DANGLING（restore 备用件等意图性引用由消费侧注记）
  J4 owner-face 只报告不触碰（T98 同仓单执行体退避律）
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
TOOLS_GLOB = os.path.join("Tools", "*.py")

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


def scan_tools(tools_glob=TOOLS_GLOB):
    """Tools/*.py 全扫 → {relpath: [(name, kind, lineno), ...]}。"""
    out = {}
    for path in sorted(glob.glob(tools_glob)):
        if os.path.basename(path) == os.path.basename(__file__):
            continue  # v1.1：自件跳过（self fixtures 不自扫）
        refs = extract_refs(path)
        if refs:
            out[os.path.basename(path)] = refs
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
    """J3 分类 → report dict（api 不可达 → UNVERIFIED 面）。"""
    report = {"generated_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "api_available": live_tags is not None,
              "live_tags": sorted(live_tags) if live_tags is not None else None,
              "files": {}, "totals": {"refs": 0, "live": 0, "dangling": 0,
                                      "unverified": 0, "lane-8090": 0}}
    for fname, refs in scan.items():
        rows = []
        for name, kind, lineno in refs:
            if live_tags is None:
                status = "UNVERIFIED"
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
    print("MODEL-REF-DRIFT files=%d refs=%d live=%d dangling=%d unverified=%d lane-8090=%d" % (
        len(report["files"]), t["refs"], t["live"], t["dangling"], t["unverified"],
        t["lane-8090"]))
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
    """S1-S5 合成夹具离线断言（零网络·J 式确定性）。"""
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
    print("SELFTEST %d/%d PASS" % (ok[0], ok[0]))
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Tools model reference drift check (T100)")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="scan Tools/*.py vs live /api/tags")
    c.add_argument("--out", default=None)
    c.add_argument("--tools-glob", default=None)
    sub.add_parser("selftest", help="offline synthetic fixtures")
    args = p.parse_args()
    if args.cmd == "check":
        return cmd_check(args)
    return _selftest()


if __name__ == "__main__":
    raise SystemExit(main())
