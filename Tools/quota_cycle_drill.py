"""quota_cycle_drill — 配额发放->消费闭环演练（M104 / M98 派生）。

目的：把 cost_ledger 的 quota 三命令（quota-budget / quota-issue /
quota-consume）以**设计值**跑一次完整闭环，验证四条账本不变量：

  A1 预算内发放通过、越预算拒发（月度闸按 month 汇总，且不跨月串账）
  A2 CEO 批准通道可越预算但留痕（status=ok_ceo_approved_over_cap）
  A3 消费按余额扣减，余额不足拒付，余额永不为负
  A4 tx_id 幂等：任何重放都不新增行、不重复计成本

另有一条**反向探针**：金额必须为正。负额若被接受，等同于凭空印配额
（issue -x 会抬高余额、consume -x 也会抬高余额），本轮如实点名。

全部写盘在 tempfile 目录内，**不碰真账本**（state/cost-ledger*.jsonl）。
数值一律标 drill-design-value：这不是 CEO 定价确认批落值。

用法：
  python Tools/quota_cycle_drill.py run [--txt-out PATH] [--json-out PATH]
  python Tools/quota_cycle_drill.py selftest
"""
import argparse
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cost_ledger as CL  # noqa: E402

# 设计值（非定价批值）
CAP_MTok = 1.000
UNIT_COST_B = 0.60  # CNY / Mtok（与 unit_cost_drill 同源设计单价）
MONTH = "2026-11"
MONTH_NEXT = "2026-12"


def _ns(**kw):
    """构造 cost_ledger 命令所需的 args 命名空间。"""
    d = dict(tx_id=None, consumer=None, amount_mtok=None,
             unit_cost_b_cny=UNIT_COST_B, reason="-", ref="-",
             date=None, ceo_approved=False,
             tokens_local=0, tokens_api=0, api_reason="-",
             month=None, cap_mtok=None)
    d.update(kw)
    return argparse.Namespace(**d)


def _rows(path):
    return CL._load_rows(path)


def run_cycle(ledger, budget):
    """跑一遍闭环，返回事件序列（每步一发事实，不掩盖中间态）。"""
    events = []

    def rec(step, status, **extra):
        ev = {"step": step, "status": status}
        ev.update(extra)
        events.append(ev)
        return ev

    # 0) 月度预算（设计值）
    rec("budget", **CL.quota_budget(
        _ns(month=MONTH, cap_mtok=CAP_MTok), path=budget))

    # 1) A1 预算内发放两笔
    rec("issue.fitting.1", **CL.quota_issue(_ns(
        tx_id="Q-DRILL-1", consumer="anon-drill-a", amount_mtok=0.400,
        date="2026-11-01", reason="drill"), ledger=ledger, budget=budget))
    rec("issue.fitting.2", **CL.quota_issue(_ns(
        tx_id="Q-DRILL-2", consumer="anon-drill-b", amount_mtok=0.500,
        date="2026-11-02", reason="drill"), ledger=ledger, budget=budget))

    # 2) A1 越预算拒发（0.9 + 0.2 > 1.0）
    rec("issue.over_budget", **CL.quota_issue(_ns(
        tx_id="Q-DRILL-3", consumer="anon-drill-a", amount_mtok=0.200,
        date="2026-11-03", reason="drill"), ledger=ledger, budget=budget))

    # 3) A2 CEO 批准越预算（留痕）
    rec("issue.over_budget.approved", **CL.quota_issue(_ns(
        tx_id="Q-DRILL-3A", consumer="anon-drill-a", amount_mtok=0.200,
        date="2026-11-03", reason="drill", ceo_approved=True),
        ledger=ledger, budget=budget))

    # 4) A1 不跨月串账：次月同额应通过（月度闸按月分桶）
    rec("issue.next_month", **CL.quota_issue(_ns(
        tx_id="Q-DRILL-4", consumer="anon-drill-c", amount_mtok=0.900,
        date="2026-12-01", reason="drill"), ledger=ledger, budget=budget))

    # 5) A3 消费扣减（余额 0.6 -> 0.5）
    rec("consume.ok", **CL.quota_consume(_ns(
        tx_id="Q-DRILL-5", consumer="anon-drill-a", amount_mtok=0.100,
        date="2026-11-05", reason="drill", tokens_local=1000),
        ledger=ledger))

    # 6) A3 余额不足拒付
    rec("consume.insufficient", **CL.quota_consume(_ns(
        tx_id="Q-DRILL-6", consumer="anon-drill-a", amount_mtok=99.0,
        date="2026-11-05", reason="drill"), ledger=ledger))

    # 7) A4 幂等：issue / consume 各重放一次
    rec("issue.replay", **CL.quota_issue(_ns(
        tx_id="Q-DRILL-1", consumer="anon-drill-a", amount_mtok=0.400,
        date="2026-11-01", reason="drill"), ledger=ledger, budget=budget))
    rec("consume.replay", **CL.quota_consume(_ns(
        tx_id="Q-DRILL-5", consumer="anon-drill-a", amount_mtok=0.100,
        date="2026-11-05", reason="drill"), ledger=ledger))

    # 8) 反向探针：负额（若被接受=可凭空印配额）
    rec("issue.negative", **CL.quota_issue(_ns(
        tx_id="Q-DRILL-7", consumer="anon-drill-a", amount_mtok=-0.500,
        date="2026-11-06", reason="drill"), ledger=ledger, budget=budget))
    rec("consume.negative", **CL.quota_consume(_ns(
        tx_id="Q-DRILL-8", consumer="anon-drill-b", amount_mtok=-0.500,
        date="2026-11-06", reason="drill"), ledger=ledger))

    rows = _rows(ledger)
    per = CL._quota_state(rows)
    return {
        "events": events,
        "rows": len(rows),
        "per_consumer": per,
        "month_caps": CL._load_budget(budget),
    }


# ---------------------------------------------------------------- judgments
ALLOWED_ISSUE_OK = {"ok", "ok_ceo_approved_over_cap"}


def judge(payload):
    """从事件序列判四条不变量 + 一条反向探针。返回 (verdict, checks)。"""
    ev = {e["step"]: e for e in payload["events"]}
    checks = []

    def chk(cid, ok, detail):
        checks.append({"id": cid, "ok": bool(ok), "detail": detail})

    # A1 预算内发放
    chk("A1.issue_fitting",
        ev["issue.fitting.1"]["status"] == "ok"
        and ev["issue.fitting.2"]["status"] == "ok",
        "fitting1=%s fitting2=%s" % (ev["issue.fitting.1"]["status"],
                                     ev["issue.fitting.2"]["status"]))
    # A1 越预算拒发
    chk("A1.over_budget_blocked",
        ev["issue.over_budget"]["status"] == "blocked_over_budget",
        "status=%s issued_mtd=%s" % (ev["issue.over_budget"]["status"],
                                     ev["issue.over_budget"].get("issued_mtd_mtok")))
    # A1 月度分桶（不跨月串账）
    chk("A1.month_bucketing",
        ev["issue.next_month"]["status"] == "ok"
        and payload["month_caps"].get(MONTH) == CAP_MTok
        and payload["month_caps"].get(MONTH_NEXT) is None,
        "next_month=%s caps=%s" % (ev["issue.next_month"]["status"],
                                   payload["month_caps"]))
    # A2 批准通道留痕
    chk("A2.approved_over_cap",
        ev["issue.over_budget.approved"]["status"] == "ok_ceo_approved_over_cap"
        and ev["issue.over_budget.approved"]["row"].get("ceo_approved") is True,
        "status=%s ceo_approved=%s" % (
            ev["issue.over_budget.approved"]["status"],
            ev["issue.over_budget.approved"]["row"].get("ceo_approved")))

    # A3 扣减与拒付
    chk("A3.consume_decrement",
        ev["consume.ok"]["status"] == "ok"
        and abs(ev["consume.ok"]["balance_mtok"] - 0.500) < 1e-9,
        "balance=%s" % ev["consume.ok"].get("balance_mtok"))
    chk("A3.insufficient_blocked",
        ev["consume.insufficient"]["status"] == "insufficient_balance",
        "status=%s balance=%s" % (ev["consume.insufficient"]["status"],
                                  ev["consume.insufficient"].get("balance_mtok")))

    # A4 幂等
    chk("A4.issue_idempotent",
        ev["issue.replay"]["status"] == "duplicate",
        "status=%s" % ev["issue.replay"]["status"])
    chk("A4.consume_idempotent",
        ev["consume.replay"]["status"] == "duplicate",
        "status=%s" % ev["consume.replay"]["status"])

    # 行数守恒：1 预算不落行；发放 3 笔（1,2,3A）+ 次月 1 笔 + 消费 1 笔
    expected_rows = 5
    chk("A4.row_conservation", payload["rows"] == expected_rows,
        "rows=%s expected=%s" % (payload["rows"], expected_rows))

    # 余额永不为负
    neg = {k: v.get("balance_mtok") for k, v in payload["per_consumer"].items()
           if (v.get("balance_mtok") or 0) < 0}
    chk("A3.no_negative_balance", not neg, "negative=%s" % (neg or "none"))

    # 反向探针：负额必须被拒
    neg_issue_rejected = ev["issue.negative"]["status"] not in ALLOWED_ISSUE_OK
    neg_consume_rejected = ev["consume.negative"]["status"] != "ok"
    chk("N1.negative_issue_rejected", neg_issue_rejected,
        "status=%s" % ev["issue.negative"]["status"])
    chk("N1.negative_consume_rejected", neg_consume_rejected,
        "status=%s" % ev["consume.negative"]["status"])

    failed = [c for c in checks if not c["ok"]]
    verdict = "QUOTA-CYCLE-GREEN" if not failed else "QUOTA-CYCLE-GAPS"
    return verdict, checks


def render(payload, verdict, checks):
    out = io.StringIO()
    out.write("QUOTA CYCLE DRILL  month=%s cap=%s Mtok unit_cost_b=%s CNY/Mtok\n"
              % (MONTH, CAP_MTok, UNIT_COST_B))
    out.write("values marked: drill-design-value (NOT a CEO pricing approval)\n")
    out.write("-" * 72 + "\n")
    for e in payload["events"]:
        extra = ""
        if "balance_mtok" in e:
            extra = " balance=%s" % e["balance_mtok"]
        elif "issued_mtd_mtok" in e:
            extra = " issued_mtd=%s" % e["issued_mtd_mtok"]
        out.write("  %-26s -> %s%s\n" % (e["step"], e["status"], extra))
    out.write("-" * 72 + "\n")
    for c in checks:
        out.write("  [%s] %-26s %s\n"
                  % ("PASS" if c["ok"] else "FAIL", c["id"], c["detail"]))
    out.write("-" * 72 + "\n")
    out.write("rows=%d\n" % payload["rows"])
    for who, st in sorted(payload["per_consumer"].items()):
        out.write("  %-14s issued=%s consumed=%s balance=%s\n"
                  % (who, st["issued_mtok"], st["consumed_mtok"],
                     st["balance_mtok"]))
    out.write("VERDICT=%s failed=%d/%d\n"
              % (verdict, len([c for c in checks if not c["ok"]]), len(checks)))
    return out.getvalue()


# ------------------------------------------------------------------- runners
def _fresh():
    d = tempfile.mkdtemp(prefix="quota-drill-")
    return os.path.join(d, "quota.jsonl"), os.path.join(d, "budget.json"), d


def cmd_run(args):
    ledger, budget, tmp = _fresh()
    payload = run_cycle(ledger, budget)
    verdict, checks = judge(payload)
    text = render(payload, verdict, checks)
    sys.stdout.write(text)
    if args.txt_out:
        with open(args.txt_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    if args.json_out:
        doc = {"verdict": verdict, "checks": checks, "payload": payload,
               "design_values": {"cap_mtok": CAP_MTok,
                                 "unit_cost_b_cny": UNIT_COST_B,
                                 "marked": "drill-design-value"}}
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
    return 0 if verdict == "QUOTA-CYCLE-GREEN" else 2


def cmd_selftest(_args):
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # S1 完整闭环正向
    ledger, budget, _tmp = _fresh()
    payload = run_cycle(ledger, budget)
    verdict, sub = judge(payload)
    ck("S1.full_cycle_green", verdict == "QUOTA-CYCLE-GREEN",
       "verdict=%s fails=%s" % (verdict,
                                [c["id"] for c in sub if not c["ok"]]))

    # S2 预算闸数学：恰好到顶放行（0.4+0.6=1.0）
    ledger, budget, _tmp = _fresh()
    CL.quota_budget(_ns(month=MONTH, cap_mtok=CAP_MTok), path=budget)
    a = CL.quota_issue(_ns(tx_id="Q-S2-1", consumer="u", amount_mtok=0.400,
                           date="2026-11-01"), ledger=ledger, budget=budget)
    b = CL.quota_issue(_ns(tx_id="Q-S2-2", consumer="u", amount_mtok=0.600,
                           date="2026-11-01"), ledger=ledger, budget=budget)
    c = CL.quota_issue(_ns(tx_id="Q-S2-3", consumer="u", amount_mtok=0.0001,
                           date="2026-11-01"), ledger=ledger, budget=budget)
    ck("S2.cap_boundary", a["status"] == "ok" and b["status"] == "ok"
       and c["status"] == "blocked_over_budget",
       "a=%s b=%s c=%s" % (a["status"], b["status"], c["status"]))

    # S3 余额边界：恰好花光放行，下一笔拒
    ledger, budget, _tmp = _fresh()
    CL.quota_issue(_ns(tx_id="Q-S3-1", consumer="u", amount_mtok=0.100,
                       date="2026-11-01"), ledger=ledger, budget=budget)
    x = CL.quota_consume(_ns(tx_id="Q-S3-2", consumer="u",
                             amount_mtok=0.100, date="2026-11-02"),
                         ledger=ledger)
    y = CL.quota_consume(_ns(tx_id="Q-S3-3", consumer="u",
                             amount_mtok=0.0001, date="2026-11-02"),
                         ledger=ledger)
    ck("S3.balance_boundary",
       x["status"] == "ok" and abs(x["balance_mtok"]) < 1e-9
       and y["status"] == "insufficient_balance",
       "x=%s bal=%s y=%s" % (x["status"], x.get("balance_mtok"), y["status"]))

    # S4 未设预算月份不拦（cap None = 无闸，如实语义）
    ledger, budget, _tmp = _fresh()
    z = CL.quota_issue(_ns(tx_id="Q-S4-1", consumer="u", amount_mtok=99.0,
                           date="2026-11-01"), ledger=ledger, budget=budget)
    ck("S4.no_cap_month_passes", z["status"] == "ok", "status=%s" % z["status"])

    # S5 负额发放被拒（不当成 ok/ok_ceo_approved）
    ledger, budget, _tmp = _fresh()
    n = CL.quota_issue(_ns(tx_id="Q-S5-1", consumer="u", amount_mtok=-1.0,
                           date="2026-11-01"), ledger=ledger, budget=budget)
    ck("S5.negative_issue_rejected", n["status"] not in ALLOWED_ISSUE_OK,
       "status=%s" % n["status"])

    # S6 负额消费被拒，且余额不被抬高
    ledger, budget, _tmp = _fresh()
    CL.quota_issue(_ns(tx_id="Q-S6-1", consumer="u", amount_mtok=0.100,
                       date="2026-11-01"), ledger=ledger, budget=budget)
    m = CL.quota_consume(_ns(tx_id="Q-S6-2", consumer="u",
                             amount_mtok=-5.0, date="2026-11-02"),
                         ledger=ledger)
    bal = CL._quota_state(_rows(ledger))["u"]["balance_mtok"]
    ck("S6.negative_consume_rejected",
       m["status"] != "ok" and abs(bal - 0.100) < 1e-9,
       "status=%s balance=%s" % (m["status"], bal))

    # S7 零额被拒（零额无意义且会污染行数）
    ledger, budget, _tmp = _fresh()
    z0 = CL.quota_issue(_ns(tx_id="Q-S7-1", consumer="u", amount_mtok=0.0,
                            date="2026-11-01"), ledger=ledger, budget=budget)
    ck("S7.zero_issue_rejected", z0["status"] not in ALLOWED_ISSUE_OK,
       "status=%s" % z0["status"])

    # S8 judge 负控：把一条 status 改坏必须转红（证明不是永绿）
    ledger, budget, _tmp = _fresh()
    payload = run_cycle(ledger, budget)
    for e in payload["events"]:
        if e["step"] == "consume.insufficient":
            e["status"] = "ok"
    verdict_bad, _ = judge(payload)
    ck("S8.judge_negative_control", verdict_bad == "QUOTA-CYCLE-GAPS",
       "verdict=%s" % verdict_bad)

    # S9 真账本零触碰：run 前后 sha 不变
    real = os.path.join(HERE, "..", "state", "quota.jsonl")
    before = _sha(real)
    ledger, budget, _tmp = _fresh()
    run_cycle(ledger, budget)
    ck("S9.real_ledger_untouched", before == _sha(real),
       "sha_before=%s" % (before[:12] if before else "absent"))

    # S10 确定性：两次运行 payload 事件序列一致
    l1, b1, _ = _fresh()
    l2, b2, _ = _fresh()
    p1 = run_cycle(l1, b1)
    p2 = run_cycle(l2, b2)
    same = [(e["step"], e["status"]) for e in p1["events"]] == \
           [(e["step"], e["status"]) for e in p2["events"]]
    ck("S10.determinism", same, "steps=%d" % len(p1["events"]))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        sys.stdout.write("[%s] %-28s %s\n"
                         % ("PASS" if ok else "FAIL", name, detail))
    sys.stdout.write("SELFTEST %s %d/%d\n"
                     % ("PASS" if not failed else "FAIL",
                        len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def _sha(path):
    import hashlib
    if not os.path.exists(path):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--txt-out", dest="txt_out", default=None)
    r.add_argument("--json-out", dest="json_out", default=None)
    sub.add_parser("selftest")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    return cmd_selftest(args)


if __name__ == "__main__":
    sys.exit(main())