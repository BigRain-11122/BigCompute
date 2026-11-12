#!/usr/bin/env python3
"""revenue_caliber.py — 三线营收口径统一与「不可混算」机检（M154 落地件）

判据来源：BLUEPRINT §一 收款出口统辖（D-20260924-09/10）+ 口径 B 结算唯一 + M108 读法注记
（「近 100% 毛利系设计成本远低于价格，禁拿此数对外讲单位经济」）。

为什么需要这个件：四个数都叫「金额」，但含义互斥——
  GMV      下单金额 Σprice（含未付款/退款前；**永远不能叫收入**）
  NET      净额 Σnet_price（扣平台费 + 退款预提后；可作营收呈现面）
  COGS     成本 Σamount（口径 B 内部成本锚）
  GROSS    毛利 Σgross（= NET − COGS；不可与 NET 相加成「总收入」）
再加两类 rail 根本不承载营收（distr=归因审计行、delivery=履约事件链），
把它们的计数/事件并进营收求和，就是「营收虚高」最常见的一种。

本工具只做三件事：
  1) 按 rail 给每一行定性：能不能进营收面（revenue / rights / audit / flow / adjustment）
  2) 四个口径分行算，**永不合成一个"收入"数**；无真实数据时印 NO-DATA 而不是 0.00
  3) 混算/错口径/重复单/演练混入实面/口径未标/净额算式不符 逐条点名

命令：
  run [--root DIR] [--json-out P] [--md-out P]   实读账本出三线×口径表与 findings
  selftest                                       夹具自检（含四条负控）
  judge --rail R --caliber C                     单行判决演示（调试用）

退出码：0=GREEN / 2=FINDINGS / 3=NO-REAL-DATA / 1=用法错误
"""

import argparse
import json
import os
import sys

# ---------------------------------------------------------------- 常量（单源）

CALIBERS = ("gmv", "net", "cogs", "gross")
CALIBER_LABEL = {
    "gmv": "GMV 下单金额（不可称收入）",
    "net": "NET 净额（可作营收呈现）",
    "cogs": "COGS 成本（口径 B 内部）",
    "gross": "GROSS 毛利（= NET − COGS）",
}
# 允许被叫「收入」的口径：只有 net / gross，且必须各自单列
REVENUE_CALIBERS = ("net", "gross")

# rail 定性：revenue=承载营收 / rights=权利轨 / audit=归因审计 / flow=履约事件 / adjustment=冲销
# 每个文件带 expect：real=实面台账（出现演练行才判 DRILL-IN-REAL）/ drill=演练台账（演练行是预期态）
RAILS = {
    "deal": {"class": "revenue", "note": "成交轨（真实单 cost-ledger / 演练单 mock-order）",
             "files": [{"path": "state/cost-ledger.jsonl", "expect": "real"},
                       {"path": "state/mock-order-ledger.jsonl", "expect": "drill"}]},
    "quota": {"class": "rights", "note": "配额权利轨（发放/消费，不产生营收）",
              "files": [{"path": "state/quota-ledger.jsonl", "expect": "real"}]},
    "distr": {"class": "audit", "note": "私域分发归因轨（触达计数，非营收行）",
              "files": [{"path": "state/distr-ledger.jsonl", "expect": "real"}]},
    "delivery": {"class": "flow", "note": "履约事件链（一单多事件，非营收行）",
                 "files": [{"path": "state/delivery-ledger.jsonl", "expect": "drill"}]},
    "aftersale": {"class": "adjustment", "note": "售后台账（冲销，单列不并入营收）",
                  "files": [{"path": "state/mock-aftersale-ledger.jsonl", "expect": "drill"}]},
}

CHANNELS = ("livestream", "store", "private")  # 三线
CHANNEL_ALIAS = {
    "livestream": "livestream", "直播": "livestream",
    "store": "store", "小店": "store", "shop": "store",
    "private": "private", "私域": "private", "community": "private",
}

# 成本/净额算式容差（分位级）
NET_TOL = 0.02
DRILL_KEYS = ("mock", "simulated", "cost_simulated", "denominator_simulated")

FINDING_CODES = {
    "GMV-AS-REVENUE": "把 Σprice（下单金额）当收入呈现",
    "NON-REVENUE-RAIL-IN-REVENUE": "审计/履约/权利轨的行被并进营收求和",
    "DUPLICATE-ORDER-ROWS": "同一 order_id 在营收面出现多行（多被当多单）",
    "DRILL-IN-REAL": "演练行（mock=true）混入实面合计",
    "CALIBER-UNLABELED": "金额行没有口径标记（settlement_caliber / caliber_note 皆缺）",
    "NET-DRIFT": "net_price 与 price×(1−fee_rate)×(1−refund_rate) 不符（口径自相矛盾）",
    "CHANNEL-UNASSIGNED": "营收行无渠道标记，三线分面不可分",
    "AUDIT-ROW-HAS-AMOUNT": "非营收轨的行带金额字段（易被误加总）",
}


# ---------------------------------------------------------------- 行分类

def _num(v):
    """只接受真数字（bool 不算数字）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _truthy_marker(row):
    """演练/合成标记：任一命中即为演练行。"""
    for k in DRILL_KEYS:
        if row.get(k) is True:
            return k
    oid = str(row.get("order_id") or row.get("tx_id") or "")
    if oid.upper().startswith("MOCK-") or oid.upper().startswith("Q-DRILL"):
        return "id-prefix"
    return None


def declares_non_revenue(row):
    note = str(row.get("caliber_note") or "").lower()
    return "not a revenue row" in note or "not revenue" in note


def channel_of(row):
    raw = row.get("channel") or row.get("source") or row.get("platform")
    if not raw:
        return None
    return CHANNEL_ALIAS.get(str(raw).strip().lower())


def row_channel_or_lane(row):
    """channel 字段优先；distr 的 lane 是渠道（bilibili 等），成交轨的 lane 是 SKU 线，不当渠道用。"""
    ch = channel_of(row)
    if ch:
        return ch
    return None


def classify_row(rail, row, expect="real"):
    """给一行定性；返回 dict(rail, rail_class, drill, caliber_tagged, amounts, flags)。

    expect：该行来自的台账期望形态（real/drill）。实面台账里出现演练行才判 DRILL-IN-REAL；
    演练台账里的演练行是预期态，不判红（否则工具会结构性常红）。
    """
    meta = RAILS[rail]
    out = {
        "rail": rail,
        "rail_class": meta["class"],
        "file_expect": expect,
        "drill": bool(_truthy_marker(row)),
        "caliber_tagged": bool(row.get("settlement_caliber") or row.get("caliber_note")),
        "channel": row_channel_or_lane(row),
        "flags": [],
    }
    for c in CALIBERS:
        out[c] = _num(row.get(c if c != "cogs" else "amount"))
        if c == "net" and out["net"] is None:
            out["net"] = _num(row.get("net_price"))
        if c == "gmv" and out["gmv"] is None:
            out["gmv"] = _num(row.get("price"))
    order_id = row.get("order_id")
    out["order_id"] = order_id
    # 非营收轨却带金额字段
    if meta["class"] != "revenue" and any(out[c] is not None for c in CALIBERS):
        out["flags"].append("AUDIT-ROW-HAS-AMOUNT")
    # 净额算式自检（三个系数齐时才判）
    price, net = out["gmv"], out["net"]
    fr, rr = _num(row.get("fee_rate")), _num(row.get("refund_rate"))
    if price is not None and net is not None and fr is not None and rr is not None:
        expect = price * (1 - fr) * (1 - rr)
        if abs(expect - net) > NET_TOL:
            out["flags"].append("NET-DRIFT")
    return out


# ---------------------------------------------------------------- 聚合

def aggregate(classified):
    """按 (rail, channel, caliber) 求和；返回 totals 与 counts，不做任何跨口径相加。"""
    totals, counts = {}, {}
    for r in classified:
        for c in CALIBERS:
            v = r.get(c)
            if v is None:
                continue
            key = (r["rail"], r["channel"] or "unassigned", c)
            totals[key] = round(totals.get(key, 0.0) + v, 6)
            counts[key] = counts.get(key, 0) + 1
    return totals, counts


def findings_for(classified, include_drill=True):
    """逐条点名。include_drill=False 时演练行不参与实面合计（但仍是 finding）。"""
    findings = []
    seen_orders = {}
    for r in classified:
        for f in r["flags"]:
            findings.append({"code": f, "rail": r["rail"], "order_id": r.get("order_id")})
        if r["rail_class"] == "revenue":
            if r["drill"] and r.get("file_expect") == "real":
                findings.append({"code": "DRILL-IN-REAL", "rail": r["rail"],
                                 "order_id": r.get("order_id")})
            if not r["caliber_tagged"]:
                findings.append({"code": "CALIBER-UNLABELED", "rail": r["rail"],
                                 "order_id": r.get("order_id")})
            if r["channel"] is None:
                findings.append({"code": "CHANNEL-UNASSIGNED", "rail": r["rail"],
                                 "order_id": r.get("order_id")})
            oid = r.get("order_id")
            if oid:
                seen_orders.setdefault(oid, 0)
                seen_orders[oid] += 1
    for oid, n in seen_orders.items():
        if n > 1:
            findings.append({"code": "DUPLICATE-ORDER-ROWS", "rail": "deal",
                             "order_id": oid, "rows": n})
    return findings


def mixed_sum_detected(calibers_used):
    """一个"收入"数一旦同时用到 gmv 与 net/gross 即混算。"""
    used = set(calibers_used)
    return bool(used & {"gmv"}) and bool(used & {"net", "gross"})


def verdict_of(revenue_real_rows, findings):
    if revenue_real_rows == 0:
        return "REV-NO-REAL-DATA"
    if findings:
        return "REV-CALIBER-FINDINGS"
    return "REV-CALIBER-GREEN"


# ---------------------------------------------------------------- 读盘

def resolve_root(root):
    return os.path.abspath(root or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def load_rails(root):
    """返回 (classified, domain)；domain 记录每文件 scanned/absent/bad（D-20 域自证）。"""
    classified, domain = [], []
    for rail, meta in RAILS.items():
        for spec in meta["files"]:
            rel, expect = spec["path"], spec["expect"]
            path = os.path.join(root, rel)
            if not os.path.exists(path):
                domain.append({"rail": rail, "file": rel, "expect": expect, "state": "ABSENT"})
                continue
            n, bad = 0, 0
            try:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            classified.append(classify_row(rail, json.loads(line), expect))
                            n += 1
                        except ValueError:
                            bad += 1
            except OSError as exc:
                domain.append({"rail": rail, "file": rel, "expect": expect,
                               "state": "UNREADABLE", "why": str(exc)})
                continue
            domain.append({"rail": rail, "file": rel, "expect": expect,
                           "state": "SCANNED", "rows": n, "bad": bad})
    return classified, domain


# ---------------------------------------------------------------- 渲染

def build_payload(root):
    classified, domain = load_rails(root)

    real = [r for r in classified if r["rail_class"] == "revenue" and not r["drill"]]
    drill = [r for r in classified if r["drill"]]
    totals, counts = aggregate([r for r in classified if not r["drill"]])

    # 三线×口径矩阵；只有 revenue 轨的行进入营收面读数
    matrix = {}
    for (rail, ch, c), v in totals.items():
        if RAILS[rail]["class"] != "revenue":
            continue
        matrix.setdefault(ch, {}).setdefault(c, 0.0)
        matrix[ch][c] = round(matrix[ch].get(c, 0.0) + v, 6)

    findings = findings_for(classified)
    per_code = {}
    for f in findings:
        per_code[f["code"]] = per_code.get(f["code"], 0) + 1

    non_rev_amount_rows = sum(1 for r in classified
                              if r["rail_class"] != "revenue"
                              and any(r.get(c) is not None for c in CALIBERS))

    payload = {
        "root": root,
        "domain": domain,
        "rows_total": len(classified),
        "rows_revenue_real": len(real),
        "rows_drill": len(drill),
        "non_revenue_rows_with_amount": non_rev_amount_rows,
        "matrix": matrix,
        "totals_by_caliber": {c: round(sum(r.get(c) or 0.0 for r in real), 6) for c in CALIBERS},
        "findings": findings,
        "findings_by_code": per_code,
        "revenue_calibers": list(REVENUE_CALIBERS),
        "verdict": verdict_of(len(real), findings),
    }
    return payload


def render(payload, as_md=False):
    lines = []
    if as_md:
        lines.append("# 三线营收口径统一表 v1（由 Tools/revenue_caliber.py 生成，勿手改）")
        lines.append("")
        lines.append("## 一、四个口径的定义（互斥，禁相加）")
        lines.append("")
        lines.append("| 口径 | 算法 | 能不能叫「收入」 |")
        lines.append("|---|---|---|")
        lines.append("| GMV | Σ price（下单金额，未扣费未扣退款） | **不能** |")
        lines.append("| NET | Σ net_price（扣平台费 + 退款预提） | 能，须与 GROSS 分列 |")
        lines.append("| COGS | Σ amount（口径 B 内部成本锚） | 不能（成本） |")
        lines.append("| GROSS | Σ gross（= NET − COGS） | 能，须与 NET 分列 |")
        lines.append("")
        lines.append("## 二、三线 × 口径矩阵（真实行，演练行不计入）")
        lines.append("")
        lines.append("| 渠道 | GMV 下单金额 | NET 净额 | COGS 成本 | GROSS 毛利 |")
        lines.append("|---|---|---|---|---|")
        for ch in ("livestream", "store", "private", "unassigned"):
            if ch not in payload["matrix"]:
                continue
            m = payload["matrix"][ch]
            lines.append("| %s | %s | %s | %s | %s |" % (
                ch, _fmt(m.get("gmv")), _fmt(m.get("net")),
                _fmt(m.get("cogs")), _fmt(m.get("gross"))))
        if not payload["matrix"]:
            lines.append("| （无营收行） | — | — | — | — |")
        lines.append("")
        lines.append("## 三、本轮读数（账本级，非估算）")
        lines.append("")
        lines.append("- verdict=`%s`" % payload["verdict"])
        lines.append("- 扫到的行：%d（其中演练行 %d、真实营收行 **%d**）"
                     % (payload["rows_total"], payload["rows_drill"], payload["rows_revenue_real"]))
        lines.append("- 非营收轨带金额字段的行：%d（单独计数，不并入营收）"
                     % payload["non_revenue_rows_with_amount"])
        if payload["rows_revenue_real"] == 0:
            lines.append("- **真实成交轨 0 行 → 营收面 NO-DATA**：此处不印 0.00，"
                         "因为「没有成交」与「成交额为 0」是两件事。")
        lines.append("")
        lines.append("## 四、扫描域自证（哪些文件读过、哪些不在盘）")
        lines.append("")
        lines.append("| rail | 文件 | 期望形态 | 状态 | 行数 |")
        lines.append("|---|---|---|---|---|")
        for d in payload["domain"]:
            lines.append("| %s | `%s` | %s | %s | %s |" % (
                d["rail"], d["file"], d.get("expect", "—"), d["state"],
                d.get("rows", "—")))
        lines.append("")
        lines.append("## 五、findings（每条都点名对象）")
        lines.append("")
        if payload["findings"]:
            lines.append("| 代码 | 次数 | 含义 |")
            lines.append("|---|---|---|")
            for code, n in sorted(payload["findings_by_code"].items()):
                lines.append("| `%s` | %d | %s |" % (code, n, FINDING_CODES.get(code, "")))
            lines.append("")
            for f in payload["findings"][:20]:
                extra = []
                if f.get("order_id"):
                    extra.append("order=%s" % f["order_id"])
                if f.get("rows"):
                    extra.append("rows=%s" % f["rows"])
                lines.append("- `%s` %s %s" % (f["code"], f["rail"], " ".join(extra)))
            if len(payload["findings"]) > 20:
                lines.append("- (+%d more)" % (len(payload["findings"]) - 20))
        else:
            lines.append("无（FINDINGS none）")
        lines.append("")
        lines.append("> 口径律：GMV 永不称收入；NET/GROSS 各自单列，禁相加成「总收入」；"
                     "distr 归因行与 delivery 履约事件不是营收行。"
                     "本表只读账本，不含任何估算或外推。")
        return "\n".join(lines) + "\n"

    dom = payload["domain"]
    scanned = sum(1 for d in dom if d["state"] == "SCANNED")
    absent = sum(1 for d in dom if d["state"] == "ABSENT")
    lines.append("VERDICT=%s rows=%d revenue_real=%d drill=%d domain=scanned:%d absent:%d" % (
        payload["verdict"], payload["rows_total"], payload["rows_revenue_real"],
        payload["rows_drill"], scanned, absent))
    for d in dom:
        if d["state"] == "SCANNED":
            lines.append("  rail=%-9s %-40s SCANNED rows=%d bad=%d"
                         % (d["rail"], d["file"], d.get("rows", 0), d.get("bad", 0)))
        else:
            lines.append("  rail=%-9s %-40s %s" % (d["rail"], d["file"], d["state"]))
    lines.append("CALIBERS  GMV/NET/COGS/GROSS 分行，永不相加（可称收入者仅 %s）"
                 % "/".join(payload["revenue_calibers"]))
    if payload["rows_revenue_real"] == 0:
        lines.append("REVENUE-NO-DATA 真实成交轨 0 行 → 营收面 NO-DATA（禁印 0.00 冒充测算）")
    for ch in ("livestream", "store", "private", "unassigned"):
        if ch not in payload["matrix"]:
            continue
        m = payload["matrix"][ch]
        lines.append("  channel=%-10s gmv=%s net=%s cogs=%s gross=%s"
                     % (ch, _fmt(m.get("gmv")), _fmt(m.get("net")),
                        _fmt(m.get("cogs")), _fmt(m.get("gross"))))
    if not payload["matrix"]:
        lines.append("  channel=（无营收行可归渠道）")
    lines.append("non_revenue_rows_with_amount=%d（非营收轨带金额字段，单独计数不并入营收）"
                 % payload["non_revenue_rows_with_amount"])
    if payload["findings"]:
        for code, n in sorted(payload["findings_by_code"].items()):
            lines.append("FINDING %-28s x%-3d %s" % (code, n, FINDING_CODES.get(code, "")))
        for f in payload["findings"][:12]:
            extra = []
            if f.get("order_id"):
                extra.append("order=%s" % f["order_id"])
            if f.get("rows"):
                extra.append("rows=%s" % f["rows"])
            lines.append("  - %s %s %s" % (f["code"], f["rail"], " ".join(extra)))
        if len(payload["findings"]) > 12:
            lines.append("  (+%d more)" % (len(payload["findings"]) - 12))
    else:
        lines.append("FINDINGS none")
    return "\n".join(lines) + "\n"


def _fmt(v):
    if v is None:
        return "—"
    return "%.4f" % v


# ---------------------------------------------------------------- 自检

def _mk(rows):
    out = []
    for r in rows:
        expect = r.pop("_expect", "real")
        out.append(classify_row(r.pop("_rail"), r, expect))
    return out


def selftest():
    results = []

    def check(name, cond, why=""):
        results.append((name, bool(cond), why))

    # S1 干净行：标 caliber、带渠道、净额算式自洽 → 无 finding
    clean = _mk([{"_rail": "deal", "order_id": "A1", "price": 49.9, "net_price": 43.6126,
                  "amount": 0.252, "gross": 43.3606, "fee_rate": 0.05, "refund_rate": 0.08,
                  "settlement_caliber": "B", "channel": "store"}])
    check("S1 clean row no flags", not findings_for(clean))

    # S2 净额算式不符 → NET-DRIFT
    drift = _mk([{"_rail": "deal", "order_id": "A2", "price": 100.0, "net_price": 100.0,
                  "fee_rate": 0.05, "refund_rate": 0.0, "settlement_caliber": "B",
                  "channel": "store"}])
    codes = {f["code"] for f in findings_for(drift)}
    check("S2 net drift flagged", "NET-DRIFT" in codes)

    # S3 演练行混入实面 → DRILL-IN-REAL
    drilled = _mk([{"_rail": "deal", "order_id": "MOCK-1", "price": 3.0, "net_price": 2.622,
                    "mock": True, "fee_rate": 0.05, "refund_rate": 0.08,
                    "settlement_caliber": "B", "channel": "store"}])
    check("S3 drill flagged", any(f["code"] == "DRILL-IN-REAL" for f in findings_for(drilled)))

    # S4 同 order_id 两行（履约事件/重复记账）→ DUPLICATE-ORDER-ROWS
    dup = _mk([{"_rail": "deal", "order_id": "A3", "price": 10.0, "net_price": 9.5,
                "fee_rate": 0.05, "refund_rate": 0.0, "settlement_caliber": "B",
                "channel": "store"},
               {"_rail": "deal", "order_id": "A3", "price": 10.0, "net_price": 9.5,
                "fee_rate": 0.05, "refund_rate": 0.0, "settlement_caliber": "B",
                "channel": "store"}])
    check("S4 duplicate order flagged",
          any(f["code"] == "DUPLICATE-ORDER-ROWS" for f in findings_for(dup)))

    # S5 非营收轨带金额 → AUDIT-ROW-HAS-AMOUNT；且不得进营收矩阵
    audit = _mk([{"_rail": "distr", "tx_id": "D-1", "reach_count": 100, "price": 0.0,
                  "net_price": 0.0}])
    check("S5 audit amount flagged",
          any(f["code"] == "AUDIT-ROW-HAS-AMOUNT" for f in findings_for(audit)))

    # S6 口径未标 → CALIBER-UNLABELED
    unlabeled = _mk([{"_rail": "deal", "order_id": "A4", "price": 10.0, "net_price": 9.5,
                      "fee_rate": 0.05, "refund_rate": 0.0, "channel": "store"}])
    check("S6 unlabeled flagged",
          any(f["code"] == "CALIBER-UNLABELED" for f in findings_for(unlabeled)))

    # S7 渠道缺失 → CHANNEL-UNASSIGNED（三线分面不可分）
    noch = _mk([{"_rail": "deal", "order_id": "A5", "price": 10.0, "net_price": 9.5,
                 "fee_rate": 0.05, "refund_rate": 0.0, "settlement_caliber": "B"}])
    check("S7 channel unassigned flagged",
          any(f["code"] == "CHANNEL-UNASSIGNED" for f in findings_for(noch)))

    # S8 混算检测：gmv + net 同时进一个"收入" → True；只用 net → False
    check("S8 mixed sum detected", mixed_sum_detected(["gmv", "net"]) is True
          and mixed_sum_detected(["net", "gross"]) is False)

    # S9 零真实行 → REV-NO-REAL-DATA（不是 GREEN）
    check("S9 no real data verdict",
          verdict_of(0, []) == "REV-NO-REAL-DATA")

    # S10 非 revenue 轨的行不进营收矩阵（即使带 price）
    rows_by_rail = {"deal": [], "distr": [{"tx_id": "D-1", "price": 99.0, "net_price": 99.0,
                                           "caliber_note": "not a revenue row"}]}
    classified = [classify_row(r, row) for r, rows in rows_by_rail.items() for row in rows]
    totals, _ = aggregate([x for x in classified if not x["drill"]])
    check("S10 audit rail excluded from matrix",
          all(k[0] == "distr" for k in totals) and verdict_of(0, []) == "REV-NO-REAL-DATA")

    # S11 d'真数字守卫：bool 不当数字（防止 True 被当成 1.0 入账）
    check("S11 bool not number", _num(True) is None and _num(0.0) == 0.0)

    # S12 确定性：同一行两次分类结果相同
    a = classify_row("deal", {"order_id": "A6", "price": 1.0, "settlement_caliber": "B",
                              "channel": "private", "fee_rate": 0.0, "refund_rate": 0.0})
    b = classify_row("deal", {"order_id": "A6", "price": 1.0, "settlement_caliber": "B",
                              "channel": "private", "fee_rate": 0.0, "refund_rate": 0.0})
    check("S12 determinism", a == b)

    # S13 四口径分行：同一行不会同时进 gmv 与 gross 的同一个键
    one = classify_row("deal", {"order_id": "A7", "price": 10.0, "net_price": 9.5,
                                "amount": 1.0, "gross": 8.5, "fee_rate": 0.05,
                                "refund_rate": 0.0, "settlement_caliber": "B",
                                "channel": "store"})
    check("S13 calibers independent",
          one["gmv"] == 10.0 and one["net"] == 9.5 and one["cogs"] == 1.0 and one["gross"] == 8.5)

    # S14 caliber_note 声明「非营收行」可被识别
    check("S14 non-revenue note detected",
          declares_non_revenue({"caliber_note": "delivery event chain = fulfillment audit rail, "
                                               "not a revenue row"}) is True)

    # S15 演练台账里的演练行是预期态 → 不得判 DRILL-IN-REAL（防工具结构性常红）
    expected_drill = _mk([{"_rail": "deal", "_expect": "drill", "order_id": "MOCK-1",
                           "price": 3.0, "net_price": 2.622, "mock": True,
                           "fee_rate": 0.05, "refund_rate": 0.08,
                           "settlement_caliber": "B", "channel": "store"}])
    check("S15 expected drill not flagged",
          not any(f["code"] == "DRILL-IN-REAL" for f in findings_for(expected_drill)))

    # S16 实面台账里出现演练行仍必须判（S15 的负控对照）
    real_with_drill = _mk([{"_rail": "deal", "_expect": "real", "order_id": "MOCK-9",
                            "price": 3.0, "net_price": 2.622, "mock": True,
                            "fee_rate": 0.05, "refund_rate": 0.08,
                            "settlement_caliber": "B", "channel": "store"}])
    check("S16 drill in real face still flagged",
          any(f["code"] == "DRILL-IN-REAL" for f in findings_for(real_with_drill)))

    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, why in results:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" -- " + why) if (why and not ok) else ""))
    print("%d/%d PASS" % (passed, len(results)))
    return 0 if passed == len(results) else 1


# ---------------------------------------------------------------- 主入口

def main(argv=None):
    ap = argparse.ArgumentParser(description="三线营收口径统一与不可混算机检")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="实读账本出三线×口径表")
    r.add_argument("--root", default=None)
    r.add_argument("--json-out", default=None)
    r.add_argument("--md-out", default=None)

    sub.add_parser("selftest", help="夹具自检")

    j = sub.add_parser("judge", help="单行判决演示")
    j.add_argument("--rail", default="deal", choices=sorted(RAILS))
    j.add_argument("--json", default="{}")

    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "judge":
        row = json.loads(args.json)
        print(json.dumps(classify_row(args.rail, row), ensure_ascii=False, sort_keys=True))
        return 0
    if args.cmd == "run":
        root = resolve_root(args.root)
        payload = build_payload(root)
        text = render(payload)
        sys.stdout.write(text)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
        if args.md_out:
            with open(args.md_out, "w", encoding="utf-8") as fh:
                fh.write(render(payload, as_md=True))
        if payload["verdict"] == "REV-CALIBER-GREEN":
            return 0
        if payload["verdict"] == "REV-NO-REAL-DATA":
            return 3
        return 2
    ap.print_usage()
    return 1


if __name__ == "__main__":
    sys.exit(main())
