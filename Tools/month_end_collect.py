#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""月末归集机械链工具（E25 执行单五源工程化·N1 一键直跑件·产品优先律 2 分件）
承接=docs/ops/month-end-cost-collection-sheet-v1.md §二 五源清单 + §三 月末行格式（预注册格式·本件零新格式发明）
+§四 计划态轻量月末行分级 + §六 N1-N3 判据（N1 月末轮必跑·N2 零编造律·N3 单位成本 v1 落值双门）。
法源链=R-20260928-compute-cost-economics「月末归集回溯口径=结算唯一·试点期回溯定价法防编造产能基准」
+M2 定价确认包「单位成本 v1 ⬜ 待首月末归集」+C-20260929-01 云端 J4 零接线 + C-20260929-02 GPU 30% 唯一点名阈值。
设计律：组合既有件零重建——S1/S2=cost_ledger 既有命令 subprocess 只读；S3=state/borrow-* 存在性检查（E23 预设计
§七零生成态）；S4=gpu_idle_collector report 只读；S5=qa 烟测日志 eval_count 解析+E12 锚件在册引用+**E28 SLA 双态基线直供**
（Tools/serve_sla_baseline.py series 产能窗基线·E28 头部声明的消费方接线·非商业产能注记常驻）。
N2 零编造律结构性落地：成本三要素（原值/电价/利用率）恒 ⬜（物理凭证前任何估算/外部锚/OSS 数据禁入）；
单位成本 v1 恒 ⬜（N3 双门=凭证齐+真实商业 tokens 归集·定价确认权=CEO·本件零计算零落值零定价动作）。
命令全只读零新台账：产出=state/month-end-<YYYYMM>.json 报告件（月末行快照·非台账·不入任何结算式）。
用法：python Tools/month_end_collect.py run [--month YYYY-MM] | python Tools/month_end_collect.py selftest
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
COST_ELEMENTS = ("original_value", "electricity_price", "utilization")  # Q3 铁律：恒 ⬜ 至物理凭证


def _run_tool(args):
    p = subprocess.run([sys.executable] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT))
    return (p.stdout or "") + (p.stderr or "")


# ---------- S1/S2 解析（纯函数·selftest 复用） ----------

def parse_quota_summary(text):
    d = json.loads(text)
    t = d.get("totals", {})
    return {
        "issued_mtok": t.get("issued_mtok", 0),
        "consumed_mtok": t.get("consumed_mtok", 0),
        "cost_issued_b_cny": t.get("cost_issued_b_cny", 0),
        "cost_consumed_b_cny": t.get("cost_consumed_b_cny", 0),
        "rails": d.get("rails", ""),
        "settlement_caliber": d.get("settlement_caliber", ""),
    }


def parse_cloud_summary(text):
    d = json.loads(text)
    t = d.get("totals", {})
    return {
        "rows": t.get("rows", 0),
        "amount_mtok": t.get("amount_mtok", 0),
        "cost_billed_b_cny": t.get("cost_billed_b_cny", 0),
        "pending_bill_rows": t.get("pending_bill_rows", 0),
        "wiring": d.get("wiring", ""),
    }


# ---------- S3 借算轨存在性（E23 预设计 §七·零生成态） ----------

def s3_borrow_state():
    files = sorted(p.name for p in (ROOT / "state").glob("borrow-*")) if (ROOT / "state").exists() else []
    return {"files": files, "generated": len(files)}


# ---------- S4 GPU report 解析（纯函数） ----------

def parse_gpu_report(text):
    m = re.search(r"gpu report ([\d-]+) machine=(\S+): n=(\d+) avg=([\d.]+)% max=(\d+)% kpi=(\w+)", text)
    r = re.search(r"3-day rolling baseline[^:]*: ([\d-]+)\.\.([\d-]+) n=(\d+) avg=([\d.]+)%", text)
    if not m:
        return None
    out = {"date": m.group(1), "machine": m.group(2), "n": int(m.group(3)),
           "avg_pct": float(m.group(4)), "max_pct": int(m.group(5)), "kpi": m.group(6)}
    if r:
        out["roll3"] = {"span": f"{r.group(1)}..{r.group(2)}", "n": int(r.group(3)), "avg_pct": float(r.group(4))}
    return out


# ---------- S5 自用面解析（纯函数·月过滤） ----------

def parse_eval_line(line):
    m = re.search(r"eval_count=(\d+)", line)
    if not m:
        return None
    t = re.search(r"([\d.]+) tok/s", line)
    return {"eval_count": int(m.group(1)), "tok_s": float(t.group(1)) if t else None}


def collect_s5(log_texts, month, anchors=None, sla=None):
    """log_texts={filename: text}；只收文件名 smoke-<month 首段日期> 的 eval 行。anchors=[{file,E_package_tokens}]
    sla=E28 serve_sla_baseline series 解析件（产能窗基线·非商业产能·随窗滚动更新）"""
    n_logs, eval_total, tok_list = 0, 0, []
    for name, text in sorted(log_texts.items()):
        m = re.match(r"smoke-(\d{4})(\d{2})(\d{2})-", name)
        if not m or f"{m.group(1)}-{m.group(2)}" != month:
            continue
        hits = [parse_eval_line(ln) for ln in text.splitlines()]
        hits = [h for h in hits if h]
        if hits:
            n_logs += 1
            eval_total += sum(h["eval_count"] for h in hits)
            tok_list += [h["tok_s"] for h in hits if h["tok_s"] is not None]
    return {"n_logs": n_logs, "eval_count_total": eval_total,
            "tok_s_n": len(tok_list),
            "e12_anchors": anchors or [], "sla_baseline": sla,
            "note": "自用面=非商业产能·禁入结算产能基准（回溯定价法同律·计量真值=Ollama eval_count L0）"}


def parse_sla_series(text):
    """E28 Tools/serve_sla_baseline.py series 输出解析（净窗/竞争窗双态+JSON 指针）。"""
    n = re.search(r"net-window : n=(\d+) mean=([\d.]+) tok/s CV=([\d.]+)% P10=([\d.]+)", text)
    c = re.search(r"contended  : n=(\d+) mean=([\d.]+) tok/s min=([\d.]+) max=([\d.]+)", text)
    j = re.search(r"json\s+: (.+\.json)", text)
    if not n:
        return None
    return {"net": {"n": int(n.group(1)), "mean_tok_s": float(n.group(2)),
                   "cv_pct": float(n.group(3)), "p10": float(n.group(4))},
            "contended": {"n": int(c.group(1)), "mean_tok_s": float(c.group(2)),
                          "min_tok_s": float(c.group(3)), "max_tok_s": float(c.group(4))} if c else None,
            "json": j.group(1) if j else None,
            "note": "双态 SLA 基线（E28）·非商业产能·分类规则 mem>7500MiB 单维=E28 续窗候选（util/power 精化）·随窗滚动"}


def _s5_real(month):
    log_texts = {p.name: p.read_text(encoding="utf-8", errors="replace")
                 for p in sorted((ROOT / "qa").glob("smoke-*.log"))} if (ROOT / "qa").exists() else {}
    anchors = []
    for p in sorted((ROOT / "state").glob("e12-anchor-*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            anchors.append({"file": p.name, "E_package_tokens": d.get("E_package_tokens")})
        except Exception:
            anchors.append({"file": p.name, "E_package_tokens": None})
    sla = parse_sla_series(_run_tool(["Tools/serve_sla_baseline.py", "series"]))
    return collect_s5(log_texts, month, anchors, sla)


# ---------- 月末行组装（§三 预注册格式·N2/N3 门内建） ----------

def assemble(month, s1, s2, s3, s4, s5, now_iso):
    commercial_mtok = s1["consumed_mtok"]  # 商业面=S1 配额轨 consumed（计划态=0·如实禁估充）
    three_elements = {k: None for k in COST_ELEMENTS}  # 恒 ⬜·Q3 铁律
    n3_credential = all(v is not None for v in three_elements.values())
    n3_commercial = commercial_mtok > 0
    n3_gate = "OPEN(凭证齐+商业tokens在册→按 M2 §二 核心式落值·定价确认权=CEO)" if (n3_credential and n3_commercial) \
        else f"BLOCKED(凭证={'⬜' if not n3_credential else '✓'}+商业tokens={commercial_mtok})"
    gpu = s4 or {}
    g = gpu.get("roll3") or {}
    sla = s5.get("sla_baseline")
    sla_seg = ""
    if sla:
        c = sla.get("contended") or {}
        ratio = f"{c['mean_tok_s'] / sla['net']['mean_tok_s']:.2f}x" if c and sla["net"]["mean_tok_s"] else "-"
        sla_seg = (f"｜产能窗 SLA 基线（E28）=净窗 n={sla['net']['n']} mean={sla['net']['mean_tok_s']} tok/s "
                   f"CV={sla['net']['cv_pct']}%·竞争 {ratio}（非商业产能·分类精化=E28 续窗候选）")
    line = (
        f"[month-end {month} planning-state light line · E25] "
        f"商业面 tokens_mtok={commercial_mtok}（S1 配额轨·计划态如实·禁估充 N2）｜"
        f"自用面 eval_count={s5['eval_count_total']} tokens（{s5['n_logs']} 次 qa 探针·非商业产能·禁入结算产能基准·"
        f"E12 锚 {'在册 ' + '/'.join(str(a['E_package_tokens']) + ' tokens' for a in s5['e12_anchors']) if s5['e12_anchors'] else '无'}）"
        f"{sla_seg}｜"
        f"S2 云端 rows={s2['rows']}·J4 零接线维持｜S3 借算 generated={s3['generated']}（E23 预注册门不跳）｜"
        f"S4 GPU {gpu.get('machine','-')} n={gpu.get('n','-')} avg={gpu.get('avg_pct','-')}% kpi={gpu.get('kpi','-')}"
        f"（30% 唯一点名阈值·3 日滚动基线 n={g.get('n','-')} avg={g.get('avg_pct','-')}%·C-20260929-02）｜"
        f"成本三要素 原值/电价/利用率=⬜⬜⬜（Q3 铁律·电价唯一合法源=实缴账单）｜"
        f"单位成本 v1=⬜（N3 双门 {n3_gate}）｜机械链 5/5 就绪"
    )
    payload = {
        "month": month, "collected_at": now_iso, "mode": "planning_state_light",
        "sheet": "docs/ops/month-end-cost-collection-sheet-v1.md",
        "sources": {"s1_quota": s1, "s2_cloud": s2, "s3_borrow": s3, "s4_gpu": gpu, "s5_selfuse": s5},
        "n2_zero_fabrication": {"cost_three_elements": three_elements,
                                "unit_cost_v1": None, "n3_gate": n3_gate,
                                "note": "N2：任何 ⬜ 不以估算/外部锚/OSS 数据回填（口径 A=呈现面专用）"},
        "month_end_line": line,
    }
    return payload


def cmd_run(month):
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    s1 = parse_quota_summary(_run_tool(["Tools/cost_ledger.py", "quota-summary"]))
    s2 = parse_cloud_summary(_run_tool(["Tools/cost_ledger.py", "cloud-summary"]))
    s3 = s3_borrow_state()
    s4 = parse_gpu_report(_run_tool(["Tools/gpu_idle_collector.py", "report"]))
    s5 = _s5_real(month)
    payload = assemble(month, s1, s2, s3, s4, s5, now_iso)
    out = ROOT / "state" / f"month-end-{month.replace('-', '')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"month": month, "report": str(out.relative_to(ROOT)),
                      "sources_ok": 5}, ensure_ascii=False))
    print("MONTH-END LINE:")
    print(payload["month_end_line"])
    return 0


def cmd_selftest():
    ok = [0]

    def check(name, cond):
        ok[0] += 1 if cond else 0
        print(("PASS" if cond else "FAIL") + f" {name}")
        return cond

    s1_fix = '{"totals":{"balance_mtok":0,"consumed_mtok":0,"cost_consumed_b_cny":0,"cost_issued_b_cny":0,"issued_mtok":0},"rails":"accounting rail only; real exchange needs 3 prerequisites","settlement_caliber":"B_only"}'
    s2_fix = '{"totals":{"amount_mtok":0,"cost_billed_b_cny":0,"pending_bill_rows":0,"rows":0},"wiring":"accounting rail only; real cloud entry waits for the first paid bill receipt (J4)"}'
    s4_fix = ("gpu report 2026-09-29 machine=bm-a: n=59 avg=8.9% max=100% kpi=FLAG(<30% call-out threshold on 3-day rolling baseline, C-20260929-02)\n"
              "3-day rolling baseline (pre-registered, C-20260929-02): 2026-09-27..2026-09-29 n=117 avg=13.0% (call-out 30%; 70% target = directional ref)\n"
              "rolling 30-min window: n=2 avg=34.5% -> IDLE\n")
    logs_fix = {
        "smoke-20260929-0012.log": "answer='1+1 equals 2.' eval_count=8 92.58 tok/s\nresidency: pre=FOREVER post=FOREVER (maintained)\n",
        "smoke-20261001-0900.log": "answer='1+1 equals 2.' eval_count=8 90.00 tok/s\n",
        "smoke-20260928-2243.log": "answer='x' eval_count=8 80.00 tok/s\nanswer='y' eval_count=12 81.00 tok/s\n",
        "smoke-20260929-bad.log": "no eval here\n",
    }
    s1 = parse_quota_summary(s1_fix)
    check("S1 解析=计划态四零", s1["consumed_mtok"] == 0 and s1["rails"].startswith("accounting rail"))
    s2 = parse_cloud_summary(s2_fix)
    check("S2 解析=rows/pending 零·J4 在文", s2["rows"] == 0 and "J4" in s2["wiring"])
    s4 = parse_gpu_report(s4_fix)
    check("S4 解析=分机行+滚动基线", s4["machine"] == "bm-a" and s4["n"] == 59 and s4["kpi"] == "FLAG"
          and s4["roll3"]["n"] == 117 and s4["roll3"]["avg_pct"] == 13.0)
    sla_fix = ("serve SLA baseline (logs=41 points=40 no_sample=0 span=['2026-09-28 10:03', '2026-09-29 14:46'])\n"
               "net-window : n=10 mean=87.05 tok/s CV=24.4% P10=25.16 min=25.16 max=103.07\n"
               "contended  : n=30 mean=77.77 tok/s min=29.69 max=101.03 (0.89x net)\n"
               "json       : C:/x/state/serve-sla-baseline-20260929-1447.json\n")
    sla = parse_sla_series(sla_fix)
    check("SLA 解析=净窗双态+JSON 指针", sla["net"]["n"] == 10 and sla["net"]["mean_tok_s"] == 87.05
          and sla["net"]["cv_pct"] == 24.4 and sla["contended"]["n"] == 30 and sla["json"].endswith(".json"))
    s5 = collect_s5(logs_fix, "2026-09", sla=sla)
    check("S5 聚合=月内 2 探针（无 eval 行不计）·eval 28·他月排除", s5["n_logs"] == 2 and s5["eval_count_total"] == 28
          and s5["sla_baseline"]["net"]["p10"] == 25.16)
    p1 = assemble("2026-09", s1, s2, {"files": [], "generated": 0}, s4, s5, "T1")
    check("计划态月末行=商业 0·三要素 ⬜·单位成本 ⬜·SLA 段在", "tokens_mtok=0" in p1["month_end_line"]
          and "⬜⬜⬜" in p1["month_end_line"] and "净窗 n=10 mean=87.05" in p1["month_end_line"]
          and p1["n2_zero_fabrication"]["unit_cost_v1"] is None)
    check("N3 双门=BLOCKED（凭证⬜+商业tokens 0）", "BLOCKED" in p1["n2_zero_fabrication"]["n3_gate"])
    s1_pos = dict(s1, consumed_mtok=1.5)
    p2 = assemble("2099-12", s1_pos, s2, {"files": [], "generated": 0}, s4, s5, "T2")
    check("商业 tokens>0 机械透传·单位成本仍 ⬜ 零计算", "tokens_mtok=1.5" in p2["month_end_line"]
          and p2["n2_zero_fabrication"]["unit_cost_v1"] is None and "BLOCKED" in p2["n2_zero_fabrication"]["n3_gate"])
    p3 = assemble("2026-09", s1, s2, {"files": [], "generated": 0}, s4, s5, "T1")
    check("确定性=双跑一致", p1["month_end_line"] == p3["month_end_line"])
    print(f"selftest: {ok[0]}/9 PASS" if ok[0] == 9 else f"selftest: FAIL ({ok[0]}/9)")
    return 0 if ok[0] == 9 else 1


def main():
    ap = argparse.ArgumentParser(description="E25 month-end cost collection mechanical chain")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--month", default=datetime.date.today().strftime("%Y-%m"))
    sub.add_parser("selftest")
    args = ap.parse_args()
    if args.cmd == "selftest":
        return cmd_selftest()
    if not MONTH_RE.match(args.month):
        print("invalid --month, expect YYYY-MM")
        return 1
    return cmd_run(args.month)


if __name__ == "__main__":
    sys.exit(main())
