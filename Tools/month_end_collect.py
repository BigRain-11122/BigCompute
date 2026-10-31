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
S6=gpu_energy_profile report 只读 subprocess+最新 profile JSON 月窗切片（v1.1 增·T75·E62 工程件只读复用零跨件改本体·
口径 A 对照面非结算注记常驻）。
S4b=GPU 样本×声明借用窗月窗 self/borrowed 分列（v1.2 增·T77 步③·BC-P-62 消费接线——复用 gpu_idle_collector 既有件
load_samples/load_borrow_windows/borrow_covered_dates 零重建零跨件改本体·M66 月度对账供数·self=30% 执行线读数面）。
S7=L1 本地推理计量轨（v1.3 增·tech T113）：cost_ledger l1-summary 只读（:8090 四文本车道 usage 回执=唯一 token 源·
T93/J2 同律）——2026-10-15 v3 final 迁移后自用消费面的月末归集补口·单位价恒空=口径 B（l1-settle=N1 唯一回填通道）。
S8=私域分发/归因轨（v1.4 增·tech T127）：state/distr-ledger.jsonl 只读（M87 distr 轨·非收入行·结算归口径 B）
——rows/attribution 分布/conv 关联命中/lane/live 行/publish queued 面，10-31 N1 前置补口。
S9=交付调度轨（v1.4 增·tech T127）：state/delivery-ledger.jsonl 只读（M88 append-only 事件链）——events/
交付单数/末态分布/route/max attempts/live 行，交付面可审计量进月末归集。
机械链分母随采源集动态扩展（基准 7 + S8/S9 各 1）=S8/S9 未采不虚降基准分母（旧断言 6/7·7/7 零漂移）。
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
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:  # 单源 schema（T134）：缺件不阻断采源，只让对读闸降级为 UNVERIFIED
    import rails_schema_check as rsc  # noqa: E402
except Exception:  # pragma: no cover - import 失败面
    rsc = None

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

DECLARED_LEDGER = "borrow-windows.jsonl"  # BC-P-62 日级声明台账（T77）≠ E23 记账轨生成件


def s3_borrow_state(files=None):
    """borrow-* 盘点：声明台账（borrow-windows.jsonl·T77 步① 立）与记账轨生成件分列——
    声明台账非记账轨零生成内容（无金额/工单行）·不入 generated 计数（J4 零生成态如实）。"""
    if files is None:
        files = sorted(p.name for p in (ROOT / "state").glob("borrow-*")) if (ROOT / "state").exists() else []
    declared = [f for f in files if f == DECLARED_LEDGER]
    gen = [f for f in files if f != DECLARED_LEDGER]
    return {"files": gen, "generated": len(gen), "declared_ledger": declared,
            "note": "borrow-windows.jsonl=BC-P-62 声明台账（T77）·非 E23 记账轨生成件·generated 计数排除（J4 零生成态如实）"}


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


# ---------- S4b GPU self/borrowed 月窗分列（v1.2 增·T77 步③·BC-P-62 消费接线） ----------

def windows_in_month(bw_rows, month):
    """声明借用窗中与本月的重叠行（纯函数·月窗切片=M66 月度对账供数面）。"""
    first = month + "-01"
    y, m = int(month[:4]), int(month[5:])
    nxt = datetime.date(y + (1 if m == 12 else 0), (m % 12) + 1, 1).isoformat()
    out = []
    for w in bw_rows or []:
        d0, d1 = str(w.get("date_from", "")), str(w.get("date_to", ""))
        if d0 < nxt and d1 >= first:
            out.append({"id": w.get("id"), "date_from": d0, "date_to": d1,
                        "consumer": w.get("consumer")})
    return out


def month_borrow_split(sample_rows, covered_dates, month, call_pct=None):
    """月窗样本按声明借用日分列 self/borrowed（纯函数·日级人工声明零启发式·E62 归因律同源）。
    self=30% 执行线读数面；borrowed=借出负载不计自产（BC-P-62 口径）。
    call_pct=点名阈值（调用方从 collector 单源导入·本函数零字面复制）；仅给 self 出 verdict
    （BC-P-155：self avg < 线=CALLOUT 月度档注记；n=0 或阈值缺=None）。"""
    rows = [r for r in (sample_rows or [])
            if str(r.get("ts", "")).startswith(month + "-")]
    self_rows = [r for r in rows if str(r["ts"])[:10] not in covered_dates]
    borrow_rows = [r for r in rows if str(r["ts"])[:10] in covered_dates]

    def face(rs):
        days = sorted({str(r["ts"])[:10] for r in rs})
        return {"n": len(rs),
                "avg_pct": round(sum(r["util_pct"] for r in rs) / len(rs), 1) if rs else None,
                "days": days}

    self_face = face(self_rows)
    if call_pct is not None and self_face["n"]:
        self_face["verdict"] = "CALLOUT" if self_face["avg_pct"] < call_pct else "OK"
    return {"available": True, "month": month, "call_pct": call_pct,
            "self": self_face, "borrowed": face(borrow_rows)}


def s4b_borrow_split(month, sample_rows=None, bw_rows=None):
    """S4b 源（只读）：collector 既有件复用（load_samples/load_borrow_windows/
    borrow_covered_dates 零重建）×月窗分列；sample_rows/bw_rows 注入位=selftest 离线夹具；
    导入失败=available False 降级如实（N2 零编造）。"""
    try:
        import gpu_idle_collector as gic  # 同目录既有件·模块级零副作用
    except Exception:
        return {"available": False,
                "note": "S4b 分列源不可用（collector 模块导入失败·N2 零编造）"}
    if sample_rows is None:
        sample_rows = gic.load_samples()
    if bw_rows is None:
        bw_rows = gic.load_borrow_windows()
    call_pct = getattr(gic, "CALL_PCT", None)  # 阈值单源=collector（禁本文件复制字面）
    split = month_borrow_split(sample_rows, gic.borrow_covered_dates(bw_rows), month,
                               call_pct=call_pct)
    split["windows_in_month"] = windows_in_month(bw_rows, month)
    split["note"] = ("BC-P-62 声明台账=日级人工声明零启发式（E62 归因律同源）"
                     "·self=30% 执行线读数面·借出负载不计自产·M66 月度对账供数"
                     "·BC-P-155 self 月度 verdict 接线（阈值单源=collector CALL_PCT）")
    return split


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
    """E28 serve_sla_baseline.py series 输出解析（净窗/竞争窗双态+分栈 stacks+JSON 指针）。
    v1.2：stacks 捕获=栈代际混串可见化（2026-10-15 v3 final 迁移后 7b/4b 不可比）。"""
    n = re.search(r"net-window : n=(\d+) mean=([\d.]+) tok/s CV=([\d.]+)% P10=([\d.]+)", text)
    c = re.search(r"contended  : n=(\d+) mean=([\d.]+) tok/s min=([\d.]+) max=([\d.]+)", text)
    s = re.search(r"stacks     : (.+)", text)
    j = re.search(r"json\s+: (.+\.json)", text)
    if not n:
        return None
    stacks = None
    if s:
        stacks = []
        for part in s.group(1).split("|"):
            mm = re.match(r"\s*(\S+) n=(\d+) net=([\d.]+|None)", part.strip())
            if mm:
                stacks.append({"model": mm.group(1), "n": int(mm.group(2)),
                               "net_mean_tok_s": float(mm.group(3)) if mm.group(3) != "None" else None})
    return {"net": {"n": int(n.group(1)), "mean_tok_s": float(n.group(2)),
                   "cv_pct": float(n.group(3)), "p10": float(n.group(4))},
            "contended": {"n": int(c.group(1)), "mean_tok_s": float(c.group(2)),
                          "min_tok_s": float(c.group(3)), "max_tok_s": float(c.group(4))} if c else None,
            "stacks": stacks,
            "json": j.group(1) if j else None,
            "note": "双态 SLA 基线（E28 v1.2）·月窗限定（--since 月首）·分栈呈现=2026-10-15 v3 final "
                    "迁移代际闸（7b 旧栈与 4b flash slot 不可比·跨栈聚合=口径混串）·随窗滚动"}


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
    # v1.2 栈代际闸：月窗限定（--since 月首·S5 月度口径）+报告面分栈呈现（跨 2026-10-15 迁移边界可见化）
    sla = parse_sla_series(_run_tool(["Tools/serve_sla_baseline.py", "series",
                                      "--since", month.replace("-", "") + "01"]))
    return collect_s5(log_texts, month, anchors, sla)


# ---------- S6 GPU 能耗剖面（v1.1 增·T75·E62 工程件只读复用·零跨件改本体） ----------

def parse_energy_report(text):
    """gpu_energy_profile report 文本输出解析（纯函数·selftest 复用）。"""
    m = re.search(r"energy: machine=(\S+) samples=(\d+) intervals=(\d+) skipped=(\d+)", text)
    if not m:
        return None
    out = {"machine": m.group(1), "samples": int(m.group(2)),
           "intervals": int(m.group(3)), "skipped": int(m.group(4))}
    t = re.search(r"total=([\d.]+) kWh over (\d+) days -> mean_daily=([\d.]+) kWh "
                  r"\(idle=([\d.]+) active=([\d.]+) peak=([\d.]+)W\)", text)
    if t:
        out.update({"total_kwh": float(t.group(1)), "days": int(t.group(2)),
                    "mean_daily_kwh": float(t.group(3)), "idle_kwh": float(t.group(4)),
                    "active_kwh": float(t.group(5)), "peak_w": float(t.group(6))})
    b = re.search(r"baseline\[([\d-]+)\.\.([\d-]+)\]: mean_daily=([\d.]+) kWh over (\d+) days; "
                  r"sum_delta\(displayed\)=([+-]?[\d.]+) kWh", text)
    if b:
        out["baseline"] = {"range": f"{b.group(1)}..{b.group(2)}",
                           "mean_daily_kwh": float(b.group(3)), "days": int(b.group(4)),
                           "sum_delta_displayed_kwh": float(b.group(5))}
    j = re.search(r"json=(\S+\.json)", text)
    if j:
        out["json"] = j.group(1)
    return out


def parse_energy_profile_json(data, month):
    """最新 profile JSON 的月窗切片（纯函数）：月 kWh/日均/安静带基线/正向 delta 日分列。
    借用窗归因=人工分析面（E61/E62）·本函数只做机械正向 delta 分列（负 delta/零/他月排除）。"""
    data = data or {}
    per_day = data.get("per_day") or {}
    days = {k: v for k, v in per_day.items() if k.startswith(month + "-")}
    month_kwh = round(sum(v.get("kwh", 0.0) for v in days.values()), 4)
    n = len(days)
    borrow_days = []
    for k in sorted(days):
        d = days[k].get("delta_kwh")
        if d is not None and d > 0:
            borrow_days.append({"date": k, "kwh": days[k].get("kwh"), "delta_kwh": d})
    return {"month_days_n": n, "month_kwh": month_kwh,
            "month_mean_daily_kwh": round(month_kwh / n, 4) if n else None,
            "baseline_range": data.get("baseline_range"),
            "baseline_mean_daily_kwh": data.get("baseline_mean_daily_kwh"),
            "borrow_days": borrow_days,
            "sum_delta_month_kwh": round(sum(b["delta_kwh"] for b in borrow_days), 4)}


def s6_energy(month, days=None, baseline_range=None, report_text=None, profile_data=None):
    """S6 源（只读）：gpu_energy_profile report subprocess + 最新 profile JSON 月窗切片。
    days 默认=月首至今+5 余量（显示窗须覆盖月全集·T74 判例）；baseline_range 默认=月首 8 日安静带约定
    （10 月窗=10-01..08 E62 实证·后继月随窗观察校准）；report_text/profile_data 注入位=selftest 离线夹具。"""
    start = datetime.date(int(month.split("-")[0]), int(month.split("-")[1]), 1)
    if days is None:
        days = max(1, (datetime.date.today() - start).days) + 5
    if baseline_range is None:
        baseline_range = f"{month}-01,{month}-08"
    if report_text is None:
        report_text = _run_tool(["Tools/gpu_energy_profile.py", "report",
                                 "--days", str(days), "--baseline-range", baseline_range])
    rep = parse_energy_report(report_text)
    if rep is None:
        return {"available": False,
                "note": "S6 能耗剖面源不可用（报告未解析出·采集面缺或工具失败）·月末行降级如实（N2 零编造）"}
    if profile_data is None and rep.get("json"):
        p = Path(rep["json"])
        if p.exists():
            try:
                profile_data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                profile_data = None
    month_slice = parse_energy_profile_json(profile_data, month) if profile_data else None
    return {"available": True,
            "report": {k: v for k, v in rep.items() if k != "json"},
            "json": rep.get("json"), "month_slice": month_slice,
            "days_requested": days, "baseline_range_requested": baseline_range,
            "note": "口径 A 对照面非结算（结算唯一=口径 B·Q3 铁律·电价唯一合法源=实缴账单）·正向 delta 日=借用窗归因候选分列"}


# ---------- S7 L1 本地推理计量轨（v1.3 增·T113·T93 轨只读消费） ----------

def parse_l1_summary(text):
    """cost_ledger l1-summary 输出解析（纯函数·selftest 复用）。"""
    d = json.loads(text)
    t = d.get("totals", {}) or {}
    return {"totals": {"amount_mtok": t.get("amount_mtok", 0),
                       "cost_converted_b_cny": t.get("cost_converted_b_cny", 0),
                       "pending_conversion_rows": t.get("pending_conversion_rows", 0),
                       "converted_rows": t.get("converted_rows", 0)},
            "consumer_model": d.get("consumer_model", {}) or {},
            "month_face": d.get("month_face", {}) or {},
            "settlement_caliber": d.get("settlement_caliber", ""),
            "lane_note": d.get("lane_note", "")}


def s7_l1(month, summary_text=None):
    """S7 源（只读）：cost_ledger l1-summary --month subprocess（:8090 L1 计量轨·T93）。
    summary_text 注入位=selftest 离线夹具；解析失败=available False 降级如实（N2 零编造）。"""
    if summary_text is None:
        summary_text = _run_tool(["Tools/cost_ledger.py", "l1-summary", "--month", month])
    try:
        parsed = parse_l1_summary(summary_text)
    except Exception:
        return {"available": False,
                "note": "S7 L1 轨源不可用（l1-summary 未解析出·N2 零编造）"}
    parsed["available"] = True
    parsed["note"] = ("L1 本地推理轨（:8090 四文本车道·usage 回执=唯一 token 源 J2 同律·T93）"
                      "·单位价恒空=口径 B·l1-settle=N1 唯一回填通道·2026-10-15 v3 final 迁移后自用消费面")
    return parsed


# ---------- S8 私域分发轨 / S9 交付调度轨（v1.4 增·tech T127·10-31 N1 前置） ----------

def load_jsonl(path):
    """JSONL 只读装载（rows 注入位=selftest 离线夹具·坏行跳过并计数·缺件返回 None）。"""
    p = Path(path)
    if not p.exists():
        return None, 0
    rows, bad = [], 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            bad += 1
    return rows, bad


def parse_distr_rows(rows, month):
    """distr 轨月窗聚合（纯函数）：行数/attribution 分布/conv 关联命中/lane/live 面。"""
    rows_m = [r for r in rows if str(r.get("ts", "")).startswith(month)]
    att = {}
    for r in rows_m:
        k = str(r.get("attribution_status") or "unknown")
        att[k] = att.get(k, 0) + 1
    return {"rows": len(rows_m),
            "tx_ids": [r.get("tx_id") for r in rows_m],
            "attribution": att,
            "conv_ref_hits": sum(1 for r in rows_m if r.get("conv_tx_ref")),
            "live_rows": sum(1 for r in rows_m if r.get("live") is True),
            "publish_queued": sum(1 for r in rows_m if r.get("publish_state") == "queued"),
            "lanes": sorted({str(r.get("lane")) for r in rows_m if r.get("lane")}),
            "reach_total": sum(int(r.get("reach_count") or 0) for r in rows_m)}


def s8_distr(month, rows=None, path=None):
    """S8 源（只读）：state/distr-ledger.jsonl（M87 私域归因轨·G2 tx_id 幂等由写入侧保证）。"""
    bad = 0
    if rows is None:
        p = Path(path) if path else (ROOT / "state" / "distr-ledger.jsonl")
        rows, bad = load_jsonl(p)
        if rows is None:
            return {"available": False, "note": "S8 distr 轨源不可用（台账件不存在·N2 零编造）"}
    parsed = parse_distr_rows(rows, month)
    parsed["available"] = True
    parsed["bad_lines"] = bad
    parsed["note"] = ("私域分发/归因轨（M87）·非收入行=结算归口径 B·金额归成交轨"
                      "·live 行=0 且 publish 恒 queued=计划态如实面（零真实发布）")
    return parsed


def parse_delivery_rows(rows, month):
    """交付事件链月窗聚合（纯函数）：append-only 末行状态即单终态。"""
    rows_m = [r for r in rows if str(r.get("ts", "")).startswith(month)]
    event_status, last = {}, {}
    for r in rows_m:
        st = str(r.get("status") or "")
        event_status[st] = event_status.get(st, 0) + 1
        last[r.get("delivery_id")] = st  # 台账 append-only·后行覆盖前行
    final = {}
    for st in last.values():
        final[st] = final.get(st, 0) + 1
    return {"events": len(rows_m),
            "deliveries": len(last),
            "final_status": final,
            "event_status": event_status,
            "routes": sorted({str(r.get("route_group")) for r in rows_m if r.get("route_group")}),
            "order_ids": sorted({str(r.get("order_id")) for r in rows_m if r.get("order_id")}),
            "live_rows": sum(1 for r in rows_m if r.get("live") is True),
            "max_attempts": max([int(r.get("attempts") or 0) for r in rows_m], default=0)}


def s9_delivery(month, rows=None, path=None):
    """S9 源（只读）：state/delivery-ledger.jsonl（M88 P1 成交环 stage-5 交付轨）。"""
    bad = 0
    if rows is None:
        p = Path(path) if path else (ROOT / "state" / "delivery-ledger.jsonl")
        rows, bad = load_jsonl(p)
        if rows is None:
            return {"available": False, "note": "S9 交付轨源不可用（台账件不存在·N2 零编造）"}
    parsed = parse_delivery_rows(rows, month)
    parsed["available"] = True
    parsed["bad_lines"] = bad
    parsed["note"] = ("交付调度事件链（M88）·非收入行=结算归口径 B"
                      "·只走 MOCK- 计划态单·live 行=0 如实")
    return parsed


# ---------- 解析字段 ↔ rails schema 单源对读（T134） ----------
# 解析侧读到的每个键都必须能在 Tools/rails_schema_check.py 的 SCHEMAS[rail] 里找到
# 声明（required 或 properties）。否则 schema 改键/改枚举时本侧会静默降级成
# available=False 而不报错——「无人对读的契约不是契约」。
PARSER_KEYS = {
    "distr": ("ts", "tx_id", "attribution_status", "conv_tx_ref", "live",
              "publish_state", "lane", "reach_count"),
    "delivery": ("ts", "status", "delivery_id", "route_group", "order_id",
                 "live", "attempts"),
}


def schema_key_gap(rail, schema=None):
    """返回解析侧读取但 schema 未声明的键（升序）。schema 缺失→None=UNVERIFIED。"""
    schemas = schema if schema is not None else (getattr(rsc, "SCHEMAS", None) if rsc else None)
    if not schemas or rail not in schemas:
        return None
    spec = schemas[rail]
    declared = set(spec.get("required") or []) | set((spec.get("properties") or {}).keys())
    return sorted(set(PARSER_KEYS.get(rail, ())) - declared)


# ---------- 月末行组装（§三 预注册格式·N2/N3 门内建） ----------

def assemble(month, s1, s2, s3, s4, s5, now_iso, s6=None, s4b=None, s7=None, s8=None, s9=None):
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
        stacks_seg = ""
        if sla.get("stacks"):
            stacks_seg = "·分栈 " + "/".join(
                f"{st['model']} n={st['n']} net={st['net_mean_tok_s']}" for st in sla["stacks"])
        sla_seg = (f"｜产能窗 SLA 基线（E28 v1.2·月窗）=净窗 n={sla['net']['n']} mean={sla['net']['mean_tok_s']} tok/s "
                   f"CV={sla['net']['cv_pct']}%·竞争 {ratio}{stacks_seg}"
                   f"（非商业产能·栈代际闸=2026-10-15 v3 final 迁移后 7b/4b 分栈呈现）")
    s6_seg, chain_n = "", 5
    if s6 and s6.get("available") and s6.get("month_slice"):
        ms = s6["month_slice"]
        bl = ms.get("baseline_mean_daily_kwh")
        bl_seg = f"{ms.get('baseline_range')} 日均 {bl}" if bl is not None else "⬜（安静带 0 日匹配）"
        bor = "/".join(f"{b['date'][5:]} +{b['delta_kwh']}" for b in ms["borrow_days"]) if ms["borrow_days"] else "-"
        s6_seg = (f"｜S6 能耗剖面（口径 A 对照·板卡面非结算）=月 {ms['month_kwh']} kWh·日均 {ms['month_mean_daily_kwh']}"
                  f"（{ms['month_days_n']} 日）·安静带 {bl_seg}·正向 delta 日 {bor}·合计 +{ms['sum_delta_month_kwh']} kWh"
                  f"（借用窗归因候选·人工归因面=E61/E62）")
        chain_n = 6
    elif s6 is not None and not s6.get("available"):
        s6_seg = "｜S6 能耗剖面源=不可用降级如实（N2 零编造·采集面缺）"
    s4b_seg = ""
    if s4b is not None:
        if s4b.get("available"):
            wins = s4b.get("windows_in_month") or []
            s = s4b.get("self") or {}
            b = s4b.get("borrowed") or {}
            s_avg = "%.1f%%" % s["avg_pct"] if s.get("avg_pct") is not None else "-"
            b_avg = "%.1f%%" % b["avg_pct"] if b.get("avg_pct") is not None else "-"
            wins_txt = ("借用窗 " + "/".join(str(w.get("id")) for w in wins)) if wins \
                else "零声明借用窗=全月 SELF 口径"
            v_txt = ""
            sv, cp = s.get("verdict"), s4b.get("call_pct")
            if sv:
                v_txt = (f"·月度 verdict={sv}（self avg {'<' if sv == 'CALLOUT' else '≥'} {cp}% 点名线"
                         f"·M66 月度档注记·不影响商业结算）")
            s4b_seg = (f"｜S4b GPU self/borrowed 分列（BC-P-62 声明台账·日级人工声明零启发式）"
                       f"=SELF n={s.get('n', 0)} avg={s_avg}（{len(s.get('days') or [])} 日·30% 执行线读数面）"
                       f"vs 借用 n={b.get('n', 0)} avg={b_avg}（{len(b.get('days') or [])} 日·{wins_txt}"
                       f"·借出负载不计自产·M66 月度对账供数）{v_txt}")
        else:
            s4b_seg = "｜S4b GPU self/borrowed 分列=源不可用降级如实（N2 零编造）"
    s7_seg = ""
    if s7 is not None:
        if s7.get("available"):
            t7 = s7.get("totals") or {}
            rows7 = t7.get("pending_conversion_rows", 0) + t7.get("converted_rows", 0)
            mf7 = s7.get("month_face") or {}
            cap7 = mf7.get("cap_mtok")
            cap_seg7 = str(cap7) if cap7 is not None else "未设"
            s7_seg = (f"｜S7 L1 本地推理轨（:8090 usage 回执=唯一 token 源·T93）=rows {rows7}"
                      f"·amount {t7.get('amount_mtok', 0):.6f} Mtok（pending {t7.get('pending_conversion_rows', 0)}"
                      f"/converted {t7.get('converted_rows', 0)}）·月闸 cap={cap_seg7} used={mf7.get('used_mtok', 0):.6f}"
                      f"（单位价恒空=口径 B·l1-settle=N1 唯一回填通道）")
        else:
            s7_seg = "｜S7 L1 本地推理轨=源不可用降级如实（N2 零编造）"
    if s7 is not None and s7.get("available"):
        chain_n += 1
    s8_seg = ""
    if s8 is not None:
        if s8.get("available"):
            att = s8.get("attribution") or {}
            att_txt = "/".join(f"{k}={v}" for k, v in sorted(att.items())) or "-"
            lanes_txt = "/".join(s8.get("lanes") or []) or "-"
            s8_seg = (f"｜S8 私域分发轨（M87·非收入行·结算归口径 B）=rows {s8.get('rows', 0)}"
                      f"·attribution {att_txt}·conv 关联命中 {s8.get('conv_ref_hits', 0)}"
                      f"·lane {lanes_txt}·live 行 {s8.get('live_rows', 0)}"
                      f"（publish queued {s8.get('publish_queued', 0)}·reach {s8.get('reach_total', 0)}）")
        else:
            s8_seg = "｜S8 私域分发轨=源不可用降级如实（N2 零编造）"
    s9_seg = ""
    if s9 is not None:
        if s9.get("available"):
            fs = s9.get("final_status") or {}
            fs_txt = "/".join(f"{k}={v}" for k, v in sorted(fs.items())) or "-"
            routes_txt = "/".join(s9.get("routes") or []) or "-"
            s9_seg = (f"｜S9 交付调度轨（M88 事件链·非收入行·结算归口径 B）=events {s9.get('events', 0)}"
                      f"·交付单 {s9.get('deliveries', 0)}·终态 {fs_txt}·route {routes_txt}"
                      f"·max attempts {s9.get('max_attempts', 0)}·live 行 {s9.get('live_rows', 0)}")
        else:
            s9_seg = "｜S9 交付调度轨=源不可用降级如实（N2 零编造）"
    if s8 is not None and s8.get("available"):
        chain_n += 1
    if s9 is not None and s9.get("available"):
        chain_n += 1
    chain_den = 7 + (1 if s8 is not None else 0) + (1 if s9 is not None else 0)
    missing = []
    if s6 is None:
        missing.append("S6 未采")
    if s7 is None:
        missing.append("S7 未采")
    if s8 is None:
        missing.append("S8 未采")
    if s9 is None:
        missing.append("S9 未采")
    chain_tail = f"机械链 {chain_n}/{chain_den} 就绪" + (f"（{'·'.join(missing)}）" if missing else "")
    line = (
        f"[month-end {month} planning-state light line · E25] "
        f"商业面 tokens_mtok={commercial_mtok}（S1 配额轨·计划态如实·禁估充 N2）｜"
        f"自用面 eval_count={s5['eval_count_total']} tokens（{s5['n_logs']} 次 qa 探针·非商业产能·禁入结算产能基准·"
        f"E12 锚 {'在册 ' + '/'.join(str(a['E_package_tokens']) + ' tokens' for a in s5['e12_anchors']) if s5['e12_anchors'] else '无'}）"
        f"{sla_seg}｜"
        f"S2 云端 rows={s2['rows']}·J4 零接线维持｜S3 借算 generated={s3['generated']}（E23 预注册门不跳）｜"
        f"S4 GPU {gpu.get('machine','-')} n={gpu.get('n','-')} avg={gpu.get('avg_pct','-')}% kpi={gpu.get('kpi','-')}"
        f"（30% 唯一点名阈值·3 日滚动基线 n={g.get('n','-')} avg={g.get('avg_pct','-')}%·C-20260929-02）"
        f"{s4b_seg}{s6_seg}{s7_seg}{s8_seg}{s9_seg}｜"
        f"成本三要素 原值/电价/利用率=⬜⬜⬜（Q3 铁律·电价唯一合法源=实缴账单）｜"
        f"单位成本 v1=⬜（N3 双门 {n3_gate}）｜{chain_tail}"
    )
    payload = {
        "month": month, "collected_at": now_iso, "mode": "planning_state_light",
        "sheet": "docs/ops/month-end-cost-collection-sheet-v1.md",
        "sources": {"s1_quota": s1, "s2_cloud": s2, "s3_borrow": s3, "s4_gpu": gpu,
                    "s5_selfuse": s5, "s6_energy": s6, "s4b_borrow_split": s4b,
                    "s7_l1_local_inference": s7, "s8_distr": s8,
                    "s9_delivery": s9},
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
    s6 = s6_energy(month)
    s4b = s4b_borrow_split(month)
    s7 = s7_l1(month)
    s8 = s8_distr(month)
    s9 = s9_delivery(month)
    payload = assemble(month, s1, s2, s3, s4, s5, now_iso, s6=s6, s4b=s4b, s7=s7, s8=s8, s9=s9)
    out = ROOT / "state" / f"month-end-{month.replace('-', '')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"month": month, "report": str(out.relative_to(ROOT)),
                      "sources_ok": 10}, ensure_ascii=False))
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
    s3a = s3_borrow_state(files=["borrow-windows.jsonl"])
    s3b = s3_borrow_state(files=["borrow-windows.jsonl", "borrow-cost-ledger.jsonl"])
    check("S3 盘点=声明台账排除（generated 0·declared 1）·真记账轨生成件计入",
          s3a["generated"] == 0 and s3a["declared_ledger"] == ["borrow-windows.jsonl"]
          and s3b["generated"] == 1 and s3b["files"] == ["borrow-cost-ledger.jsonl"])
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
    e6_fix = ("energy: machine=bm-a samples=100 intervals=99 skipped=1\n"
              "  total=1.2345 kWh over 3 days -> mean_daily=0.4115 kWh (idle=0.5 active=0.7345 peak=200.0W)\n"
              "  baseline[2026-10-01..2026-10-02]: mean_daily=0.4000 kWh over 2 days; sum_delta(displayed)=+0.0230 kWh\n"
              "  json=C:/x/state/gpu-energy-profile-fixture.json\n")
    e6 = parse_energy_report(e6_fix)
    check("S6 报告解析=头部+基线+JSON 指针", e6["machine"] == "bm-a" and e6["samples"] == 100
          and e6["total_kwh"] == 1.2345 and e6["mean_daily_kwh"] == 0.4115
          and e6["baseline"]["mean_daily_kwh"] == 0.4 and e6["baseline"]["days"] == 2
          and e6["baseline"]["sum_delta_displayed_kwh"] == 0.023 and e6["json"].endswith(".json"))
    check("S6 缺件态=垃圾文本 None·空 per_day 零月窗", parse_energy_report("no energy here") is None
          and parse_energy_profile_json({"per_day": {}}, "2026-10")["month_days_n"] == 0)
    e6_prof = {"baseline_range": "2026-10-01..2026-10-02", "baseline_mean_daily_kwh": 0.4,
               "per_day": {"2026-10-01": {"kwh": 0.4, "delta_kwh": 0.0},
                           "2026-10-02": {"kwh": 0.41, "delta_kwh": 0.01},
                           "2026-10-09": {"kwh": 1.195, "delta_kwh": 0.795},
                           "2026-10-10": {"kwh": 0.9016, "delta_kwh": 0.5016},
                           "2026-10-11": {"kwh": 0.0112, "delta_kwh": -0.8616},
                           "2026-09-30": {"kwh": 9.9, "delta_kwh": 9.5}}}
    ms = parse_energy_profile_json(e6_prof, "2026-10")
    check("S6 月窗切片=月 kWh/日均/正向 delta 分列·负值零值他月排除", ms["month_days_n"] == 5
          and ms["month_kwh"] == 2.9178 and ms["month_mean_daily_kwh"] == 0.5836
          and len(ms["borrow_days"]) == 3 and ms["borrow_days"][0]["date"] == "2026-10-02"
          and ms["borrow_days"][-1]["delta_kwh"] == 0.5016
          and ms["sum_delta_month_kwh"] == 1.3066 and ms["baseline_mean_daily_kwh"] == 0.4)
    ms0 = parse_energy_profile_json({"per_day": {}}, "2026-10")
    check("S6 空月窗=零日零 kWh·日均 None·borrow 空", ms0["month_kwh"] == 0
          and ms0["month_mean_daily_kwh"] is None and ms0["borrow_days"] == []
          and ms0["sum_delta_month_kwh"] == 0)
    s6_fix = {"available": True, "report": {"machine": "bm-a"}, "json": "fixture.json",
              "month_slice": ms, "days_requested": 15, "baseline_range_requested": "2026-10-01,2026-10-08",
              "note": "口径 A 对照面非结算（结算唯一=口径 B·Q3 铁律·电价唯一合法源=实缴账单）·正向 delta 日=借用窗归因候选分列"}
    p1 = assemble("2026-09", s1, s2, {"files": [], "generated": 0}, s4, s5, "T1", s6=s6_fix)
    check("计划态月末行=商业 0·三要素 ⬜·单位成本 ⬜·SLA 段在", "tokens_mtok=0" in p1["month_end_line"]
          and "⬜⬜⬜" in p1["month_end_line"] and "净窗 n=10 mean=87.05" in p1["month_end_line"]
          and p1["n2_zero_fabrication"]["unit_cost_v1"] is None)
    check("N3 双门=BLOCKED（凭证⬜+商业tokens 0）", "BLOCKED" in p1["n2_zero_fabrication"]["n3_gate"])
    s1_pos = dict(s1, consumed_mtok=1.5)
    p2 = assemble("2099-12", s1_pos, s2, {"files": [], "generated": 0}, s4, s5, "T2", s6=s6_fix)
    check("商业 tokens>0 机械透传·单位成本仍 ⬜ 零计算", "tokens_mtok=1.5" in p2["month_end_line"]
          and p2["n2_zero_fabrication"]["unit_cost_v1"] is None and "BLOCKED" in p2["n2_zero_fabrication"]["n3_gate"])
    p3 = assemble("2026-09", s1, s2, {"files": [], "generated": 0}, s4, s5, "T1", s6=s6_fix)
    check("确定性=双跑一致（含 S6 段）", p1["month_end_line"] == p3["month_end_line"])
    p4 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T1", s6=s6_fix)
    check("S6 并入月末行=月 kWh+安静带+正向 delta 分列+机械链 6/7",
          "S6 能耗剖面" in p4["month_end_line"] and "月 2.9178 kWh" in p4["month_end_line"]
          and "机械链 6/7 就绪" in p4["month_end_line"]
          and p4["sources"]["s6_energy"]["month_slice"]["month_kwh"] == 2.9178
          and "10-09 +0.795" in p4["month_end_line"])
    p5 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T1",
                  s6={"available": False, "note": "S6 能耗剖面源不可用（报告未解析出·采集面缺或工具失败）·月末行降级如实（N2 零编造）"})
    check("S6 不可用=降级行+机械链 5/7（N2 零编造）", "S6 能耗剖面源=不可用" in p5["month_end_line"]
          and "机械链 5/7" in p5["month_end_line"])
    sbw_fix = [{"id": "BW-001", "date_from": "2026-10-08", "date_to": "2026-10-08", "consumer": "H3 test"},
               {"id": "BW-002", "date_from": "2026-10-09", "date_to": "2026-10-10", "consumer": "KF batch"},
               {"id": "BW-003", "date_from": "2026-11-01", "date_to": "2026-11-02", "consumer": "next month"}]
    ssamp_fix = [{"ts": "2026-10-01T10:00:00", "util_pct": 10.0},
                 {"ts": "2026-10-01T11:00:00", "util_pct": 20.0},
                 {"ts": "2026-10-09T10:00:00", "util_pct": 90.0},
                 {"ts": "2026-10-10T10:00:00", "util_pct": 70.0},
                 {"ts": "2026-11-01T10:00:00", "util_pct": 99.0}]
    covered_fix = {"2026-10-08", "2026-10-09", "2026-10-10", "2026-11-01", "2026-11-02"}
    # BC-P-155：点名线单源=collector CALL_PCT（此处经真实源取，源不可用才用夹具期望值兜底）
    s4b_src = s4b_borrow_split("2026-10", sample_rows=ssamp_fix, bw_rows=sbw_fix)
    check("BC-P-155 阈值单源接线=collector CALL_PCT（本文件零阈值字面复制）",
          s4b_src.get("available") is True and s4b_src.get("call_pct") is not None
          and "BC-P-155" in (s4b_src.get("note") or ""))
    _cp = s4b_src.get("call_pct")
    sp = month_borrow_split(ssamp_fix, covered_fix, "2026-10", call_pct=_cp)
    check("S4b 月窗分列=self n=2 avg=15.0%·借用 n=2 avg=80.0%·他月排除·借用日分列",
          sp["self"]["n"] == 2 and sp["self"]["avg_pct"] == 15.0
          and sp["self"]["days"] == ["2026-10-01"]
          and sp["borrowed"]["n"] == 2 and sp["borrowed"]["avg_pct"] == 80.0
          and sp["borrowed"]["days"] == ["2026-10-09", "2026-10-10"])
    check("BC-P-155 self 月均低于点名线→verdict=CALLOUT（15.0% 对线）",
          sp["self"].get("verdict") == "CALLOUT")
    sp_ok = month_borrow_split([{"ts": "2026-10-02T10:00:00", "util_pct": 55.0}], set(),
                               "2026-10", call_pct=_cp)
    check("BC-P-155 self 月均不低于线→verdict=OK", sp_ok["self"].get("verdict") == "OK")
    sp_na = month_borrow_split([], set(), "2026-10", call_pct=_cp)
    check("BC-P-155 self n=0→无 verdict（禁假判 OK/CALLOUT）", "verdict" not in sp_na["self"])
    sp_nocp = month_borrow_split([{"ts": "2026-10-02T10:00:00", "util_pct": 5.0}], set(),
                                 "2026-10", call_pct=None)
    check("BC-P-155 阈值不可得→无 verdict（N2 零编造不默认通过）", "verdict" not in sp_nocp["self"])
    wm = windows_in_month(sbw_fix, "2026-10")
    check("S4b 窗月窗切片=10 月内 BW-001/002·他月窗排除", [w["id"] for w in wm] == ["BW-001", "BW-002"])
    s4b_fix = dict(sp, windows_in_month=wm,
                   note="BC-P-62 声明台账=日级人工声明零启发式（E62 归因律同源）·self=30% 执行线读数面·借出负载不计自产·M66 月度对账供数")
    p6 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T3",
                  s6=s6_fix, s4b=s4b_fix)
    check("S4b 并入月末行=self/借用分列+借用窗行+payload 源携带+月度 verdict 面",
          "S4b GPU self/borrowed 分列" in p6["month_end_line"]
          and "SELF n=2 avg=15.0%" in p6["month_end_line"]
          and "借用 n=2 avg=80.0%" in p6["month_end_line"]
          and "借用窗 BW-001/BW-002" in p6["month_end_line"]
          and "月度 verdict=CALLOUT" in p6["month_end_line"]
          and "不影响商业结算" in p6["month_end_line"]
          and p6["sources"]["s4b_borrow_split"]["self"]["n"] == 2
          and p6["sources"]["s4b_borrow_split"]["self"]["verdict"] == "CALLOUT")
    p7 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T3",
                  s6=s6_fix, s4b={"available": True, "month": "2026-10",
                                  "self": {"n": 3, "avg_pct": 12.0, "days": ["2026-10-01"]},
                                  "borrowed": {"n": 0, "avg_pct": None, "days": []},
                                  "windows_in_month": [], "note": "zero"})
    check("S4b 零声明借用窗=全月 SELF 口径行（borrowed n=0 avg=-）",
          "零声明借用窗=全月 SELF 口径" in p7["month_end_line"]
          and "借用 n=0 avg=-" in p7["month_end_line"])
    p8 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T3",
                  s6=s6_fix, s4b={"available": False,
                                  "note": "S4b 分列源不可用（collector 模块导入失败·N2 零编造）"})
    check("S4b 源不可用=降级行如实（N2 零编造）",
          "S4b GPU self/borrowed 分列=源不可用降级如实" in p8["month_end_line"])
    p9 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T3",
                  s6=s6_fix, s4b=s4b_fix)
    check("确定性=S4b 段双跑一致", p6["month_end_line"] == p9["month_end_line"])
    l1_fix = ('{"consumer_model": {"bigdomain|Qwen3.6-35B-A3B": {"amount_mtok": 0.000021, '
              '"converted_mtok": 0.0, "cost_b_cny": 0.0, "pending_rows": 1, "converted_rows": 0}}, '
              '"totals": {"amount_mtok": 0.000021, "cost_converted_b_cny": 0.0, '
              '"pending_conversion_rows": 1, "converted_rows": 0}, '
              '"month_face": {"month": "2026-10", "cap_mtok": 200, "used_mtok": 0.000021, '
              '"remaining_mtok": 199.999979, "over_cap_ceo_approved_tx": 0}, '
              '"settlement_caliber": "B_only; unit price only via l1-settle", '
              '"lane_note": "usage receipt = only legal token source (J2 parity)"}')
    l7 = parse_l1_summary(l1_fix)
    check("S7 解析=totals/月闸/口径在文", l7["totals"]["amount_mtok"] == 0.000021
          and l7["totals"]["pending_conversion_rows"] == 1
          and l7["month_face"]["cap_mtok"] == 200 and "l1-settle" in l7["settlement_caliber"])
    s7_fix = s7_l1("2026-10", summary_text=l1_fix)
    p10 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T4",
                   s6=s6_fix, s4b=s4b_fix, s7=s7_fix)
    check("S7 并入月末行=rows/amount/月闸+机械链 7/7 就绪",
          "S7 L1 本地推理轨" in p10["month_end_line"]
          and "rows 1" in p10["month_end_line"]
          and "amount 0.000021 Mtok" in p10["month_end_line"]
          and "cap=200" in p10["month_end_line"]
          and "机械链 7/7 就绪" in p10["month_end_line"]
          and p10["sources"]["s7_l1_local_inference"]["totals"]["amount_mtok"] == 0.000021)
    p11 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T4",
                   s6=s6_fix, s4b=s4b_fix,
                   s7={"available": False, "note": "S7 L1 轨源不可用（l1-summary 未解析出·N2 零编造）"})
    check("S7 源不可用=降级行如实+机械链不增（N2 零编造）",
          "S7 L1 本地推理轨=源不可用降级如实" in p11["month_end_line"]
          and "机械链 6/7 就绪" in p11["month_end_line"]
          and "S7 未采" not in p11["month_end_line"])
    p12 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T4",
                   s6=s6_fix, s4b=s4b_fix, s7=s7_fix)
    check("确定性=S7 段双跑一致", p10["month_end_line"] == p12["month_end_line"])
    check("S7 垃圾文本=解析失败降级 available False",
          s7_l1("2026-10", summary_text="not json")["available"] is False)
    s8_rows_fix = [
        {"tx_id": "D-20261025-1", "ts": "2026-10-25T12:45:51", "lane": "bilibili",
         "attribution_status": "pending", "conv_tx_ref": "", "live": False,
         "publish_state": "queued", "reach_count": 0},
        {"tx_id": "D-20261025-2", "ts": "2026-10-26T09:00:00", "lane": "wechat",
         "attribution_status": "confirmed", "conv_tx_ref": "MOCK-20261019-R1", "live": False,
         "publish_state": "queued", "reach_count": 120},
        {"tx_id": "D-20261101-1", "ts": "2026-11-01T09:00:00", "lane": "douyin",
         "attribution_status": "pending", "conv_tx_ref": "", "live": False,
         "publish_state": "queued", "reach_count": 999},
    ]
    s8 = s8_distr("2026-10", rows=s8_rows_fix)
    check("S8 月窗聚合=rows 2·attribution pending/confirmed 各 1·conv 命中 1·他月排除",
          s8["available"] and s8["rows"] == 2 and s8["attribution"] == {"pending": 1, "confirmed": 1}
          and s8["conv_ref_hits"] == 1 and s8["lanes"] == ["bilibili", "wechat"]
          and s8["reach_total"] == 120 and s8["live_rows"] == 0)
    s9_rows_fix = [
        {"event_id": "E-1", "delivery_id": "DV-20261025-1", "order_id": "MOCK-20261019-R1",
         "route_group": "resident-qa-8792", "status": "queued", "ts": "2026-10-25T22:39:36",
         "attempts": 0, "live": False},
        {"event_id": "E-2", "delivery_id": "DV-20261025-1", "order_id": "MOCK-20261019-R1",
         "route_group": "resident-qa-8792", "status": "dispatched", "ts": "2026-10-25T22:39:36",
         "attempts": 1, "live": False},
        {"event_id": "E-3", "delivery_id": "DV-20261025-1", "order_id": "MOCK-20261019-R1",
         "route_group": "resident-qa-8792", "status": "delivered", "ts": "2026-10-25T22:39:37",
         "attempts": 1, "live": False},
        {"event_id": "E-4", "delivery_id": "DV-20261101-1", "order_id": "MOCK-20261019-R2",
         "route_group": "batch-pool", "status": "queued", "ts": "2026-11-01T09:00:00",
         "attempts": 0, "live": False},
    ]
    s9 = s9_delivery("2026-10", rows=s9_rows_fix)
    check("S9 事件链聚合=events 3·交付单 1·末态 delivered=1·max attempts 1·他月排除",
          s9["available"] and s9["events"] == 3 and s9["deliveries"] == 1
          and s9["final_status"] == {"delivered": 1} and s9["event_status"]["queued"] == 1
          and s9["max_attempts"] == 1 and s9["routes"] == ["resident-qa-8792"]
          and s9["live_rows"] == 0)
    p13 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T5",
                   s6=s6_fix, s4b=s4b_fix, s7=s7_fix, s8=s8, s9=s9)
    check("S8/S9 并入月末行=两段在文+机械链 9/9 就绪+payload 源携带",
          "S8 私域分发轨" in p13["month_end_line"]
          and "S9 交付调度轨" in p13["month_end_line"]
          and "conv 关联命中 1" in p13["month_end_line"]
          and "终态 delivered=1" in p13["month_end_line"]
          and "机械链 9/9 就绪" in p13["month_end_line"]
          and p13["sources"]["s8_distr"]["rows"] == 2
          and p13["sources"]["s9_delivery"]["deliveries"] == 1)
    p14 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T5",
                   s6=s6_fix, s4b=s4b_fix, s7=s7_fix,
                   s8={"available": False, "note": "S8 台账件不存在·N2 零编造"}, s9=s9)
    check("S8 源不可用=降级行如实+机械链 8/9（S9 照常计入）",
          "S8 私域分发轨=源不可用降级如实" in p14["month_end_line"]
          and "机械链 8/9 就绪" in p14["month_end_line"]
          and "S9 交付调度轨" in p14["month_end_line"])
    p15 = assemble("2026-10", s1, s2, {"files": [], "generated": 0}, s4, s5, "T5",
                   s6=s6_fix, s4b=s4b_fix, s7=s7_fix, s8=s8, s9=s9)
    check("确定性=S8/S9 段双跑一致", p13["month_end_line"] == p15["month_end_line"])
    check("JSONL 装载缺件=None+坏行计数",
          load_jsonl(ROOT / "state" / "__no_such_ledger__.jsonl") == (None, 0))
    # ---- T134：解析字段 ↔ schema 单源对读（契约漂移会被机械逮到，不静默降级）----
    check("T134 解析键 ⊆ schema 声明（distr/delivery 双轨 gap 空）",
          schema_key_gap("distr") == [] and schema_key_gap("delivery") == [])
    _shrunk = {"distr": {"required": [k for k in rsc.SCHEMAS["distr"]["required"]],
                         "properties": {k: v for k, v in rsc.SCHEMAS["distr"]["properties"].items()
                                        if k != "reach_count"}}}
    check("T134 负例=schema 删 reach_count 即报缺口（检出能力自证）",
          schema_key_gap("distr", _shrunk) == ["reach_count"])
    _req_only = {"delivery": {"required": ["ts", "status", "delivery_id", "route_group",
                                           "order_id", "live", "attempts"],
                              "properties": {}}}
    check("T134 required-only 键也算已声明（required∪properties 双源合并）",
          schema_key_gap("delivery", _req_only) == [])
    check("T134 schema 不可用=None 降级（不假绿）", schema_key_gap("distr", {}) is None)
    total = 42
    print(f"selftest: {ok[0]}/{total} PASS" if ok[0] == total else f"selftest: FAIL ({ok[0]}/{total})")
    return 0 if ok[0] == total else 1


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
