#!/usr/bin/env python3
"""status_face_freshness.py - 实况面新鲜度三把尺子的单源（M194·11-11 立）。

背景（M194 原文）：同一件事——CEO 实况面 docs/status-export.json 有没有
按时刷新——此前由两处各自判：

  写侧 round_append.J11：比「面 updated_at」与「墙钟 now」，上限 30 分钟
  读侧 qa_smoke no.14 -> status_export_check：比「面」与「rounds.log 最新轮」
        （上限 2h），另加面整体年龄上限 48h

两条各自都说得通，但比较对象、上限、基准点分散在两个文件里，于是存在
错位窗：20:00 compose 后轮拖到 21:00 落账，J11 在 60min 处已拒写，而
no.14 只看台账仍是绿（台账尚未写，面相对台账并不落后）；反向亦然。

本模块把三要素（比谁 / 上限 / 基准点）收在一处，两个调用点 import 同一
常量与同一判决函数；每份读数里写死 bench=，读者不必反推「这条红是拿
什么跟什么比的」。

三把尺子（名字即判据）：
  WRITE_LAG_MIN  = 30    面 vs now          写侧守门：本轮没刷面就别落账
  LEDGER_LAG_MIN = 120   面 vs 台账最新轮    读侧体检：面没跟上活
  MAX_AGE_MIN    = 2880  面 vs now          读侧体检：面彻底老化

边界面（如实）：
  - 时标不可解析 -> fail-closed（判不了不等于新鲜），本模块只给 ok=False
    + reason，verdict 文本由调用点决定。
  - 台账最新轮缺省（None）时 ledger 尺子记 n/a，不判绿也不判红——空台账
    不是「面落后」的证据；调用点须把 ledger=n/a 打进读数。
  - 本模块不读盘、不发 IO，纯函数（便于两侧共用同一夹具集）。

CLI: selftest / judge <updated_at> [--now ...] [--latest-round ...]
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys

WRITE_LAG_MIN = 30.0
LEDGER_LAG_MIN = 120.0
MAX_AGE_MIN = 2880.0

STAMP_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")


def parse_stamp(text):
    """解析实况面 updated_at。不可解析返回 None（调用点 fail-closed）。"""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    for fmt in STAMP_FORMATS:
        try:
            return datetime.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _minutes(later, earlier):
    return (later - earlier).total_seconds() / 60.0


def judge_write(stamp_text, now=None):
    """写侧尺子：面 updated_at 距本轮落账墙钟。

    返回 {ok, age_min, cap_min, bench, reason}。
    """
    now = now or datetime.datetime.now()
    ts = parse_stamp(stamp_text)
    if ts is None:
        return {"ok": False, "age_min": None, "cap_min": WRITE_LAG_MIN,
                "bench": "now", "reason": "updated_at 不可解析: %r" % (stamp_text,)}
    age = _minutes(now, ts)
    ok = age <= WRITE_LAG_MIN
    reason = "" if ok else ("面比本轮早 %.0f 分钟（上限 %.0f·bench=now）"
                            % (age, WRITE_LAG_MIN))
    return {"ok": ok, "age_min": age, "cap_min": WRITE_LAG_MIN,
            "bench": "now", "reason": reason}


def judge_read(stamp_text, now=None, latest_round_ts=None):
    """读侧尺子：面整体年龄 + 面落后台账最新轮。

    latest_round_ts 为 None 时 ledger 尺子记 n/a（ok 由 age 尺子决定）。
    返回 {ok, age_min, max_age_min, ledger_lag_min, ledger_state, flags,
          bench}。
    """
    now = now or datetime.datetime.now()
    ts = parse_stamp(stamp_text)
    if ts is None:
        return {"ok": False, "age_min": None, "max_age_min": MAX_AGE_MIN,
                "ledger_lag_min": None, "ledger_state": "n/a",
                "flags": ["STAMP-UNPARSABLE"], "bench": "now+latest_round"}
    age = _minutes(now, ts)
    flags = []
    if age > MAX_AGE_MIN:
        flags.append("AGE-CAP")
    if latest_round_ts is None:
        ledger_lag = None
        ledger_state = "n/a"
    else:
        ledger_lag = _minutes(latest_round_ts, ts)
        ledger_state = "lag"
        if ledger_lag > LEDGER_LAG_MIN:
            flags.append("LEDGER-LAG")
    return {"ok": not flags, "age_min": age, "max_age_min": MAX_AGE_MIN,
            "ledger_lag_min": ledger_lag, "ledger_state": ledger_state,
            "flags": flags, "bench": "now+latest_round"}


def selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append(ok)
        print(("PASS " if ok else "FAIL ") + name + ("  [" + detail + "]" if detail else ""))

    t = datetime.datetime(2026, 11, 11, 21, 0)

    # S1-S3 写侧边界：新鲜放行 / 恰好 30min 放行 / 超过即拒
    w = judge_write("2026-11-11 20:40", now=t)
    run("S1 写侧 20min 新鲜放行", w["ok"] and w["bench"] == "now", str(w))
    w = judge_write("2026-11-11 20:30", now=t)
    run("S2 写侧恰好 30min 放行", w["ok"] and abs(w["age_min"] - 30.0) < 1e-9, str(w))
    w = judge_write("2026-11-11 20:29", now=t)
    run("S3 写侧 31min 拒写", not w["ok"] and "上限" in w["reason"], w["reason"])

    # S4 读侧全绿：面 1h 龄、台账最新轮比面旧（面在台账之上）
    r = judge_read("2026-11-11 20:00", now=t,
                   latest_round_ts=datetime.datetime(2026, 11, 11, 19, 0))
    run("S4 读侧全绿", r["ok"] and r["ledger_lag_min"] == -60.0, str(r))

    # S5 错位窗 A（原 J11 严、no.14 宽）：面 20:00、now 21:00、台账最新轮 19:00
    #    写侧 60min > 30 拒写；读侧 age 60 < 2880 且 lag=-60 -> 绿。
    w = judge_write("2026-11-11 20:00", now=t)
    r = judge_read("2026-11-11 20:00", now=t,
                   latest_round_ts=datetime.datetime(2026, 11, 11, 19, 0))
    run("S5 错位窗A：写侧拒/读侧绿（J11 严于 no.14）",
        (not w["ok"]) and r["ok"] and w["cap_min"] != r["max_age_min"], str(w) + " | " + str(r))

    # S6 错位窗 B（原 J11 宽、no.14 严）：面 20:00、now 20:20、台账最新轮 22:30
    #    写侧 20min 放行；读侧 lag=150 > 120 -> 红。他窗写了轮但面没跟上。
    t2 = datetime.datetime(2026, 11, 11, 20, 20)
    w = judge_write("2026-11-11 20:00", now=t2)
    r = judge_read("2026-11-11 20:00", now=t2,
                   latest_round_ts=datetime.datetime(2026, 11, 11, 22, 30))
    run("S6 错位窗B：写侧绿/读侧红（no.14 严于 J11）",
        w["ok"] and (not r["ok"]) and "LEDGER-LAG" in r["flags"], str(w) + " | " + str(r))

    # S7 时标不可解析：两侧都 fail-closed，不静默当新鲜
    w = judge_write("not-a-stamp", now=t)
    r = judge_read("", now=t, latest_round_ts=t)
    run("S7 坏时标两侧 fail-closed",
        (not w["ok"]) and (not r["ok"]) and "STAMP-UNPARSABLE" in r["flags"],
        str(w["reason"]) + " | " + str(r["flags"]))

    # S8 台账缺失：ledger 记 n/a 不判红，ok 只由 age 尺子决定
    r = judge_read("2026-11-11 20:00", now=t, latest_round_ts=None)
    run("S8 台账缺省 ledger=n/a 不判红",
        r["ok"] and r["ledger_state"] == "n/a" and r["ledger_lag_min"] is None, str(r))

    # S9 年龄超 48h：读侧红且点名 AGE-CAP
    r = judge_read("2026-11-09 20:00", now=t,
                   latest_round_ts=datetime.datetime(2026, 11, 9, 19, 0))
    run("S9 年龄超 48h 判 AGE-CAP", (not r["ok"]) and "AGE-CAP" in r["flags"], str(r))

    # S10 确定性：同输入两次输出逐字相同
    a = judge_read("2026-11-11 20:00", now=t,
                   latest_round_ts=datetime.datetime(2026, 11, 11, 19, 0))
    b = judge_read("2026-11-11 20:00", now=t,
                   latest_round_ts=datetime.datetime(2026, 11, 11, 19, 0))
    run("S10 确定性", json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))

    n = sum(1 for c in checks if c)
    print("SELFTEST %s %d/%d" % ("PASS" if n == len(checks) else "FAIL", n, len(checks)))
    return 0 if n == len(checks) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="实况面新鲜度三把尺子单源（M194）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest", help="跑夹具")
    j = sub.add_parser("judge", help="判一个面时标（读数含 bench=）")
    j.add_argument("updated_at")
    j.add_argument("--now", default=None, help="基准墙钟（YYYY-MM-DD HH:MM）")
    j.add_argument("--latest-round", default=None, help="台账最新轮（YYYY-MM-DD HH:MM）")
    a = ap.parse_args(argv)

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if a.cmd == "selftest":
        return selftest()

    now = parse_stamp(a.now) if a.now else None
    latest = parse_stamp(a.latest_round) if a.latest_round else None
    out = {
        "write": judge_write(a.updated_at, now=now),
        "read": judge_read(a.updated_at, now=now, latest_round_ts=latest),
        "caps": {"write_lag_min": WRITE_LAG_MIN,
                 "ledger_lag_min": LEDGER_LAG_MIN,
                 "max_age_min": MAX_AGE_MIN},
        "bench": "write=now / read=now+latest_round",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
