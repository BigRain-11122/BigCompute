#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""borrow_window_freshness.py — 借用窗声明台账收线鲜度门（E70·T104 判例防再发件）

背景（T104 判例）：BW-007 窗毕 10-14、收线注记 10-16 补办（pause 跳轮面）——借用窗
收线依赖轮内人工记忆，跳轮即滞留。本件=state/borrow-windows.jsonl 的机械鲜度门：
窗毕日（date_to < 今日）行须带正式收线标记，缺标记=STALE-OPEN 出册（月末 S4b 归因
自洽前置：台账自载收线证据，不依赖 rounds.log 外证）。

用法：
  python Tools/borrow_window_freshness.py check [--ledger state/borrow-windows.jsonl] [--out PATH]
  python Tools/borrow_window_freshness.py selftest

判据（预注册）：
  F1 窗毕无收线标记 → STALE-OPEN（exit 1）。正式标记形态三式：
     a)「收线 2026-XX-XX」（带时点收线注记·BW-003/006/007 形）
     b)「收线回计价已毕」（BW-002 形）
     c)「收线毕」
  F2 在窗行（date_to >= 今日）→ ACTIVE 不判
  F3 条件式措辞（「收线日按…补账」/「…归因收线」）非正式标记 → 仍判 STALE
     （BW-004/005 教训面·selftest 负例断言）
  F4 损行（非法 JSON）→ BAD-JSON 计数跳过不崩（exit 1 保守门）
  F5 缺档 → EMPTY-OK exit 0（J4 零编造态如实）
  F6 纯只读：check 全程台账字节零改
  F7 确定性：同输入重复跑判定一致
  收线滞后 informational 指标：closure_lag_days = 收线注记日 − date_to（可解析时）。
控制台输出纯 ASCII（GBK 控制台防崩）；台账中文只按 id 回显。证据 JSON 走 --out。
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

# 正式收线标记三式（F1）；条件式「收线日按…/…归因收线」设计上不匹配（F3）
CLOSURE_RE = re.compile(r"收线\s*2026-\d{2}-\d{2}|收线回计价已毕|收线毕")
CLOSURE_TS_RE = re.compile(r"收线\s*(2026-\d{2}-\d{2})")
DEFAULT_LEDGER = "state/borrow-windows.jsonl"


def load_rows(path):
    """返回 (rows|None, bad_count)；缺档=None（F5）。"""
    if not os.path.exists(path):
        return None, 0
    rows, bad = [], 0
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            ln = raw.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                bad += 1
    return rows, bad


def _days(a, b):
    return (datetime.date.fromisoformat(a) - datetime.date.fromisoformat(b)).days


def classify(rows, bad, today):
    """返回 (active_ids, closed[(id,lag|None)], stale[(id,date_to,age_days)], bad)。"""
    active, closed, stale = [], [], []
    for r in rows:
        rid = str(r.get("id", "?"))
        d_to = str(r.get("date_to", ""))
        note = str(r.get("caliber_note", ""))
        if not d_to:
            bad += 1
            continue
        if d_to >= today:
            active.append(rid)  # F2
            continue
        m = CLOSURE_TS_RE.search(note)
        if m:
            closed.append((rid, _days(m.group(1), d_to)))  # F1a + lag 指标
        elif CLOSURE_RE.search(note):
            closed.append((rid, None))  # F1b/F1c（无时点·lag n/a）
        else:
            stale.append((rid, d_to, _days(today, d_to)))  # F1/F3
    return active, closed, stale, bad


def cmd_check(args):
    rows, bad = load_rows(args.ledger)
    today = datetime.date.today().isoformat()
    if rows is None:
        print("VERDICT: EMPTY-OK no ledger (J4 zero-fabrication) today=%s" % today)
        return 0  # F5
    active, closed, stale, bad = classify(rows, bad, today)
    print("borrow-window-freshness today=%s rows=%d active=%d closed=%d stale=%d bad_json=%d"
          % (today, len(rows), len(active), len(closed), len(stale), bad))
    for rid in sorted(active):
        print("  ACTIVE      %s (in-window, not judged)" % rid)
    for rid, lag in sorted(closed):
        print("  CLOSED      %s closure_lag_days=%s" % (rid, "n/a" if lag is None else lag))
    for rid, d_to, age in sorted(stale):
        print("  STALE-OPEN  %s window_ended=%s days_since_end=%d closure_marker=MISSING"
              % (rid, d_to, age))
    if bad:
        print("  BAD-JSON    lines=%d (skipped)" % bad)
    verdict = "STALE-OPEN" if stale else ("BAD-JSON" if bad else "FRESH-GREEN")
    print("VERDICT: %s" % verdict)
    if args.out:
        payload = {"today": today, "rows": len(rows), "active": active,
                   "closed": [{"id": r, "closure_lag_days": l} for r, l in closed],
                   "stale": [{"id": r, "date_to": d, "days_since_end": a} for r, d, a in stale],
                   "bad_json": bad, "verdict": verdict, "ledger": args.ledger}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True, indent=1, sort_keys=True)
        print("evidence: %s" % args.out)
    return 1 if (stale or bad) else 0


def _fixture(tmp, today, stale_note, green_note):
    """temp 台账：ACTIVE/CLOSED-date/CLOSED-variant/STALE/条件式×2/BAD-JSON 七行。"""
    d_end = (datetime.date.fromisoformat(today) - datetime.timedelta(days=2)).isoformat()
    lines = [
        json.dumps({"id": "T-ACT", "date_to": today, "caliber_note": "x"}, ensure_ascii=False),
        json.dumps({"id": "T-CLD", "date_to": d_end,
                    "caliber_note": "收线 %s 09:5x 毕（E64 一命令回计价）" % today}, ensure_ascii=False),
        json.dumps({"id": "T-CLB", "date_to": d_end, "caliber_note": "收线回计价已毕"}, ensure_ascii=False),
        json.dumps({"id": "T-STL", "date_to": d_end, "caliber_note": stale_note}, ensure_ascii=False),
        json.dumps({"id": "T-CND1", "date_to": d_end, "caliber_note": "收线日按 E62 回计价法补账"}, ensure_ascii=False),
        json.dumps({"id": "T-CND2", "date_to": d_end, "caliber_note": "按 BW-004 归因勘正先例收线"}, ensure_ascii=False),
        "{not-json",
    ]
    path = os.path.join(tmp, "ledger.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def cmd_selftest(args):
    ok = fail = 0

    def check(name, cond):
        nonlocal ok, fail
        if cond:
            ok += 1
            print("PASS %s" % name)
        else:
            fail += 1
            print("FAIL %s" % name)

    today = datetime.date.today().isoformat()
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, today, "日级人工声明·无标记", "n/a")
        before = open(path, "rb").read()
        rows, bad = load_rows(path)
        active, closed, stale, bad = classify(rows, bad, today)
        ids_a = sorted(active)
        ids_c = sorted(r for r, _ in closed)
        ids_s = sorted(r for r, _, _ in stale)
        check("S1 closed-with-date + lag math", ids_c == ["T-CLB", "T-CLD"]
              and dict((r, l) for r, l in closed)["T-CLD"] == 2)
        check("S2 stale detected", "T-STL" in ids_s)
        check("S3 active not judged", ids_a == ["T-ACT"])
        check("S4 variant-b closed lag n/a", dict((r, l) for r, l in closed)["T-CLB"] is None)
        check("S5 conditional phrasing still stale (BW-004/005 negative)",
              "T-CND1" in ids_s and "T-CND2" in ids_s)
        check("S6 bad-json counted no crash", bad == 1)
        check("S8 read-only bytes unchanged", open(path, "rb").read() == before)
        a2, c2, s2, b2 = classify(load_rows(path)[0], load_rows(path)[1], today)
        check("S7 determinism", (a2, c2, s2, b2) == (active, closed, stale, bad))
        p = subprocess.run([sys.executable, os.path.abspath(__file__), "check",
                            "--ledger", path], capture_output=True, text=True)
        check("S9 e2e exit 1 on stale fixture", p.returncode == 1 and "STALE-OPEN" in p.stdout)
        green = os.path.join(tmp, "green.jsonl")
        with open(green, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "T-G", "date_to": today, "caliber_note": "x"},
                               ensure_ascii=False) + "\n")
        p2 = subprocess.run([sys.executable, os.path.abspath(__file__), "check",
                             "--ledger", green], capture_output=True, text=True)
        check("S10 e2e exit 0 green/active-only", p2.returncode == 0 and "FRESH-GREEN" in p2.stdout)
        p3 = subprocess.run([sys.executable, os.path.abspath(__file__), "check",
                             "--ledger", os.path.join(tmp, "nope.jsonl")],
                            capture_output=True, text=True)
        check("S11 missing ledger EMPTY-OK exit 0", p3.returncode == 0 and "EMPTY-OK" in p3.stdout)
    total = ok + fail
    print("selftest: %d/%d PASS" % (ok, total))
    return 1 if fail else 0


def main():
    ap = argparse.ArgumentParser(description="borrow-window closure freshness gate (E70)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--ledger", default=DEFAULT_LEDGER)
    c.add_argument("--out", default=None)
    c.set_defaults(func=cmd_check)
    s = sub.add_parser("selftest")
    s.set_defaults(func=cmd_selftest)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
