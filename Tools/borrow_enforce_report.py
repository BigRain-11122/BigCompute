#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""borrow_enforce_report.py — 借窗 runner 夜窗执法账 rollup 件（T83·BC-P-68 消费面）

背景（T82）：gpu_idle_collector 借窗执法腿已把每笔 ENFORCED-KILL/WARN 逐笔记入
state/borrow-runner-enforce-log.jsonl（审计账）——本件=该账的只读汇总消费面：
按夜分组（ts 日期）计数+首末笔+事件分类，供 BW 收线注记/月末 S4b/R-52 复活
节奏证据三面取数。纯只读零新台账。

用法：
  python Tools/borrow_enforce_report.py report [--days N]
  python Tools/borrow_enforce_report.py selftest

判据（预注册）：
  J1 账档缺位 = EMPTY-OK exit 0（零态如实·J4 同律不编造）
  J2 按日期分组 rollup：count/first/last/事件分类计数（确定性·同账同出）
  J3 strict UTF-8 读（损行=定位报错 exit 3·T24 前置门同律）
  J4 纯只读：目标账档字节零改（selftest 断言）
"""
import argparse
import hashlib
import io
import json
import os
import sys
import tempfile

LOG_REL = "state/borrow-runner-enforce-log.jsonl"

# 事件分类（与 gpu_idle_collector 执法腿写面同词表·前缀匹配）
EVENT_KINDS = ("ENFORCED-KILL", "KILL-FAILED", "NO-KILL", "WARN")


def _read_entries(path):
    """返回 (entries, err)。损行 err=首坏字节位。"""
    if not os.path.exists(path):
        return [], None
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, e.start
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue  # 非条目行跳过（账档=本司执法腿唯一写手·防御性宽容读）
        if isinstance(d, dict) and d.get("ts") and d.get("line"):
            out.append(d)
    return out, None


def _classify(line):
    for k in EVENT_KINDS:
        if k in line:
            return k
    return "OTHER"


def rollup(entries, days=None):
    """按日期分组 → [{date,count,first,last,kinds{...}}]（升序）。"""
    by_date = {}
    for e in entries:
        date = str(e.get("ts", ""))[:10]
        g = by_date.setdefault(date, {"date": date, "count": 0, "first": None,
                                      "last": None, "kinds": {}})
        g["count"] += 1
        if g["first"] is None or str(e["ts"]) < g["first"]:
            g["first"] = str(e["ts"])
        if g["last"] is None or str(e["ts"]) > g["last"]:
            g["last"] = str(e["ts"])
        kind = _classify(str(e.get("line", "")))
        g["kinds"][kind] = g["kinds"].get(kind, 0) + 1
    rows = sorted(by_date.values(), key=lambda r: r["date"])
    if days:
        rows = rows[-days:]
    return rows


def render_report(path, days=None):
    """返回 (exit_code, text)。"""
    entries, bad = _read_entries(path)
    if entries is None:
        return 3, "CORRUPT: 账档非 strict UTF-8（首坏字节位 %d）——先按 T24 流程修复" % bad
    if not entries:
        return 0, "EMPTY-OK: 执法账零条目（零态如实·borrow 窗未触发执法或账未起）"
    rows = rollup(entries, days)
    total = sum(r["count"] for r in rows)
    lines = ["borrow-runner enforce rollup: %d entries, %d night-group(s)" % (total, len(rows))]
    for r in rows:
        kinds = " ".join("%s=%d" % (k, v) for k, v in sorted(r["kinds"].items()))
        lines.append("  %s count=%d first=%s last=%s %s"
                     % (r["date"], r["count"], r["first"], r["last"], kinds))
    return 0, "\n".join(lines)


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append((name, ok, detail))

    with tempfile.TemporaryDirectory() as td:
        # S1 零态（账档缺位=EMPTY-OK exit 0·J1）
        c, t = render_report(os.path.join(td, "none.jsonl"))
        run("S1 缺档零态 exit0", c == 0 and "EMPTY-OK" in t, t.splitlines()[0] if t else "")
        # S2 分组 rollup+事件分类+确定性（J2）
        p = os.path.join(td, "log.jsonl")
        fixture = [
            {"ts": "2026-10-11T23:20:48", "line": "borrow-window runner enforce: ENFORCED-KILL llama-server.exe (night + borrow day)"},
            {"ts": "2026-10-11T23:24:06", "line": "borrow-window runner enforce: ENFORCED-KILL llama-server.exe (night + borrow day)"},
            {"ts": "2026-10-11T23:29:45", "line": "borrow-window runner sentinel: WARN runner PRESENT"},
            {"ts": "2026-10-12T23:01:00", "line": "borrow-window runner enforce: ENFORCED-KILL llama-server.exe (night + borrow day)"},
        ]
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            for e in fixture:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        before = open(p, "rb").read()
        c, t = render_report(p)
        ok2 = (c == 0 and "4 entries, 2 night-group" in t
               and "2026-10-11 count=3 first=2026-10-11T23:20:48 last=2026-10-11T23:29:45" in t
               and "ENFORCED-KILL=2 WARN=1" in t
               and "2026-10-12 count=1" in t)
        run("S2 分组rollup+分类", ok2, t.splitlines()[1] if len(t.splitlines()) > 1 else t)
        # J2 确定性：双跑同出
        c2, t2 = render_report(p)
        run("S3 确定性双跑同出", c2 == 0 and t2 == t, "")
        # J4 纯只读：字节零改
        run("S4 纯只读字节零改", open(p, "rb").read() == before, "")
        # S5 --days 截窗（仅末 N 夜组）
        c3, t3 = render_report(p, days=1)
        run("S5 days截窗", c3 == 0 and "1 night-group" in t3 and "2026-10-12 count=1" in t3, "")
        # S6 损行前置门（J3）
        p2 = os.path.join(td, "bad.jsonl")
        with open(p2, "wb") as f:
            f.write('{"ts": "x", "line": "ok"}\n'.encode("utf-8") + "中文损行".encode("gbk"))
        c4, t4 = render_report(p2)
        run("S6 损行前置门 exit3", c4 == 3 and "CORRUPT" in t4, "")

    n_pass = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(("PASS " if ok else "FAIL ") + name + ("  [" + detail + "]" if detail else ""))
    print("SELFTEST %s %d/%d" % ("PASS" if n_pass == len(checks) else "FAIL", n_pass, len(checks)))
    return 0 if n_pass == len(checks) else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="借窗 runner 执法账只读 rollup（BC-P-68 消费面）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="按夜分组汇总执法账")
    r.add_argument("--days", type=int, default=None, help="仅末 N 夜组")
    sub.add_parser("selftest", help="自测（temp 夹具零污染）")
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, *LOG_REL.split("/"))
    if args.cmd == "selftest":
        sys.exit(_selftest())
    code, text = render_report(path, getattr(args, "days", None))
    print(text)
    sys.exit(code)


if __name__ == "__main__":
    main()
