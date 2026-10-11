#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve_contract_audit — U240 常驻服务契约活体审计件（族 A 增量·R-20260929-fleet-inference-serving-expansion 配套·E65）

只读审计：常驻 serve 四面活体对照 U240 常驻标准——①residency+keep_alive（api/ps expires_at→never_expires 判定）
②num_ctx 契约（api/show model_info native context_length vs ollama ps CONTEXT 列 effective 值→静默缩窗复核〔R-20260928 §发现：4096≠32768 首例〕）
③模型在册清单漂移（api/tags vs 上一份审计 JSON 自滚动基线·无前档=首档建档）
④headroom 面（nvidia-smi free·J2 ≥1.5GB 借算门 informational）。
纯只读：零 schtasks、零派活、零写台账、零 GPU 生成动作；JSON 证据落 state/。
消费面：U240 常驻标准执法证据链 / 跨机 num_ctx 契约行（fleet 扩面 R- §三）回填 / qa_smoke ollama 探针归因（R-50 借窗态）旁证。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "http://127.0.0.1:11434"
STATE_DIR = os.path.join("state")
NEVER_YEAR = 2100  # expires_at 年份 ≥ 此值 = keep_alive -1 永不过期
NEVER_ZERO = 100   # expires_at 年份 ≤ 此值 = keep_alive -1 零时哨兵（Ollama 实发 0001-01-01）


def http_json(path, timeout=8):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def vram_free_mib():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=10).decode().strip().splitlines()
        return int(out[0].strip())
    except Exception:
        return -1


def ollama_ps_raw():
    try:
        return subprocess.check_output(["ollama", "ps"], stderr=subprocess.DEVNULL,
                                       timeout=10).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


# ---------- 判定函数（selftest 锚）----------

def keepalive_verdict(expires_at_iso):
    """expires_at ISO 串 → keep_alive 判定。far-future=never_expires(-1)；近时=expiring；空=not_loaded。"""
    if not expires_at_iso:
        return "not_loaded"
    try:
        y = int(str(expires_at_iso)[:4])
    except (ValueError, TypeError):
        return "unknown"
    if y <= NEVER_ZERO or y >= NEVER_YEAR:
        return "never_expires"
    return "expiring"


def parse_ollama_ps_context(raw, model_hint=""):
    """ollama ps 表 → {model: effective_ctx_int|None}。CONTEXT 列在（0.6+）；缺失列=None 值保字段。"""
    out = {}
    lines = [ln for ln in (raw or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return out
    header = lines[0].upper()
    has_ctx = "CONTEXT" in header
    for ln in lines[1:]:
        parts = [p.strip() for p in ln.split() if p.strip()]
        if not parts:
            continue
        name = parts[0]
        if has_ctx:
            # 末列=CONTEXT（部分版本含 EXPIRES 在 SIZE/PROCESSOR 前）——取纯数字列为 ctx 候选
            ctx = None
            for p in reversed(parts):
                if re.fullmatch(r"\d{3,9}", p):
                    ctx = int(p)
                    break
            out[name] = ctx
        else:
            out[name] = None
    if model_hint:
        for k in out:
            if k.startswith(model_hint.split(":")[0]):
                out.setdefault(model_hint, out[k])
    return out


def find_native_ctx(show_obj):
    """api/show model_info → native context_length（嵌套键 *_context_length 检取）。缺=None。"""
    mi = (show_obj or {}).get("model_info") or {}
    for k, v in mi.items():
        if k.endswith("context_length") and isinstance(v, int):
            return v
    return None


def ctx_shrink_note(native, effective):
    """native vs effective → 契约缩窗判定。任一 None=unverifiable；equal=fits；shrink=ratio。"""
    if not native or not effective:
        return {"verdict": "unverifiable", "ratio": None}
    ratio = round(effective / native, 4)
    verdict = "fits" if effective >= native else "shrunk"
    return {"verdict": verdict, "ratio": ratio}


def inventory_drift(current, previous):
    """在册清单 vs 前档 → added/removed/unchanged。previous None=首档建档。"""
    cur, prev = set(current or []), set(previous or [])
    if not previous:
        return {"baseline": "first_snapshot", "added": sorted(cur), "removed": [], "unchanged": []}
    return {"baseline": "rolling", "added": sorted(cur - prev),
            "removed": sorted(prev - cur), "unchanged": sorted(cur & prev)}


def prev_audit_baseline(state_dir):
    """最近一份审计 JSON 的 inventory（自滚动基线）。无=None。"""
    cands = sorted(glob.glob(os.path.join(state_dir, "serve-contract-audit-*.json")))
    if not cands:
        return None, None
    path = cands[-1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d.get("inventory", []), os.path.basename(path)
    except Exception:
        return None, None


# ---------- live audit ----------

def cmd_audit(args):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    rep = {"ts": ts, "api": API, "machine": "bm-a"}
    problems = []
    # ① tags → 存活 + 清单
    try:
        tags = http_json("/api/tags")
        inv = sorted(m.get("name", "") for m in tags.get("models", []))
        rep["serve_alive"] = True
        rep["inventory"] = inv
    except Exception as e:
        rep["serve_alive"] = False
        rep["inventory"] = []
        problems.append("api_tags_fail: %r" % e)
    # ② ps → residency
    try:
        ps = http_json("/api/ps")
        res = []
        for m in ps.get("models", []):
            res.append({"name": m.get("name"), "size_vram": m.get("size_vram"),
                        "expires_at": m.get("expires_at"),
                        "keepalive": keepalive_verdict(m.get("expires_at"))})
        rep["resident"] = res
    except Exception as e:
        rep["resident"] = []
        problems.append("api_ps_fail: %r" % e)
    never = [r for r in rep["resident"] if r["keepalive"] == "never_expires"]
    rep["residency_verdict"] = "PASS" if never else ("NO_RESIDENT" if rep["resident"] else "NONE_LOADED")
    # ③ native ctx（api/show·逐常驻位）+ effective ctx（ollama ps CONTEXT 列）
    raw_ps = ollama_ps_raw()
    eff_map = parse_ollama_ps_context(raw_ps)
    ctx_faces = []
    for r in rep["resident"]:
        name = r.get("name") or ""
        native = None
        try:
            # 本机 Ollama 版本 /api/show 为 POST 面（GET=405 实锚·工具面发现）
            req = urllib.request.Request(
                API + "/api/show", data=json.dumps({"model": name}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                show = json.loads(r.read().decode())
            native = find_native_ctx(show)
        except Exception:
            native = None
        effective = eff_map.get(name)
        shrink = ctx_shrink_note(native, effective)
        ctx_faces.append({"model": name, "native_ctx": native, "effective_ctx": effective,
                          "ctx_face": shrink["verdict"], "ctx_ratio": shrink["ratio"]})
    rep["ctx_contract"] = ctx_faces
    rep["ollama_ps_raw_lines"] = len([ln for ln in raw_ps.splitlines() if ln.strip()])
    # ④ 清单漂移（自滚动基线）
    prev_inv, prev_file = prev_audit_baseline(STATE_DIR)
    rep["drift"] = inventory_drift(rep["inventory"], prev_inv)
    rep["drift_baseline_file"] = prev_file
    # ⑤ headroom 面
    rep["vram_free_mib"] = vram_free_mib()
    # verdict 汇总
    rep["u240_face"] = {
        "keepalive_residency": rep["residency_verdict"],
        "ctx_contract_checked": bool(ctx_faces),
        "headroom_j2_note": "informational",
    }
    out = os.path.join(STATE_DIR, "serve-contract-audit-%s.json" % ts)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("SERVE-CONTRACT-AUDIT %s" % ts)
    print("serve_alive=%s inventory_n=%d residency=%s" % (
        rep["serve_alive"], len(rep["inventory"]), rep["residency_verdict"]))
    for c in ctx_faces:
        print("ctx: %s native=%s effective=%s face=%s ratio=%s" % (
            c["model"], c["native_ctx"], c["effective_ctx"], c["ctx_face"], c["ctx_ratio"]))
    print("drift: baseline=%s added=%s removed=%s" % (
        rep["drift"]["baseline"], rep["drift"]["added"], rep["drift"]["removed"]))
    print("vram_free_mib=%s" % rep["vram_free_mib"])
    print("problems=%s" % (problems or "none"))
    print("evidence=%s" % out)
    return 0


# ---------- selftest（J1-J7·fixture 零 live 依赖）----------

def cmd_selftest(args):
    ok = [0, 0]

    def check(name, cond):
        ok[0] += 1
        ok[1] += 1 if cond else 0
        print("J%-2d %-38s %s" % (ok[0], name, "PASS" if cond else "FAIL"))

    check("keepalive zero-sentinel", keepalive_verdict("0001-01-01T00:00:00Z") == "never_expires")
    check("keepalive far-future", keepalive_verdict("2100-01-01T00:00:00Z") == "never_expires")
    check("keepalive expiring", keepalive_verdict("2026-10-11T13:30:00Z") == "expiring")
    check("keepalive not_loaded/empty", keepalive_verdict("") == "not_loaded")
    raw_ctx = ("NAME ID SIZE PROCESSOR CONTEXT EXPIRES\n"
               "qwen3:8b abc123 7.4GB 100% GPU 8192 4 minutes from now")
    m = parse_ollama_ps_context(raw_ctx)
    check("ollama_ps ctx parse", m.get("qwen3:8b") == 8192)
    raw_noctx = "NAME ID SIZE PROCESSOR\nqwen3:8b abc123 7.4GB 100% GPU"
    m2 = parse_ollama_ps_context(raw_noctx)
    check("ollama_ps no-ctx col → None field", m2.get("qwen3:8b") is None)
    check("ollama_ps empty robust", parse_ollama_ps_context("") == {})
    show = {"model_info": {"qwen3.context_length": 40960, "general.architecture": "qwen3"}}
    check("native ctx extract", find_native_ctx(show) == 40960)
    check("native ctx missing → None", find_native_ctx({"model_info": {}}) is None)
    s1 = ctx_shrink_note(32768, 8192)
    check("shrink verdict+ratio", s1["verdict"] == "shrunk" and abs(s1["ratio"] - 0.25) < 1e-6)
    check("shrink unverifiable", ctx_shrink_note(None, 8192)["verdict"] == "unverifiable")
    check("shrink fits", ctx_shrink_note(8192, 8192)["verdict"] == "fits")
    d1 = inventory_drift(["a", "b"], None)
    check("drift first_snapshot", d1["baseline"] == "first_snapshot" and d1["added"] == ["a", "b"])
    d2 = inventory_drift(["a", "c"], ["a", "b"])
    check("drift rolling add/del", d2["added"] == ["c"] and d2["removed"] == ["b"] and d2["unchanged"] == ["a"])
    print("SELFTEST %d/%d %s" % (ok[1], ok[0], "PASS" if ok[1] == ok[0] else "FAIL"))
    return 0 if ok[1] == ok[0] else 1


def main():
    ap = argparse.ArgumentParser(description="U240 resident serve contract live audit (read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("audit", help="live read-only audit → state/ JSON evidence")
    sub.add_parser("selftest", help="fixture selftest J1-J13")
    a = ap.parse_args()
    if a.cmd == "audit":
        return cmd_audit(a)
    return cmd_selftest(a)


if __name__ == "__main__":
    sys.exit(main())
