#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""month_end_append.py — N1 月末行「一命令」落账微件（BC-P-22 工程面·E25 N1 机械面收口）

背景（提案判据）：runbook 承诺「N1=一命令」，现状=两命令+人工转写——month_end_collect run
只写 state/month-end-*.json+打印月末行，行入 rounds.log 须人工复制转写进 round_append 的
--line 参数（长行中文手工转写=损行/错引风险面·T24 判例同源）。本件=subprocess 跑
month_end_collect run→读报告 month_end_line 字段→in-process 调 round_append.append_line
（utf-8 唯一路径+J1 strict 门+J2 幂等护栏全继承）→「一命令」真语义。

三闸（真 run 禁提前律的机械执法·W2 先于 W1=已落账幂等快路径零副作用）：
  W1 月份闭合门：目标月未到末日（today < last-day）→ exit 4 EARLY（N1 窗=月末轮·漏跑补跑自次日合法）
  W2 月行在账门：rounds.log 已有 [month-end <month> 行 → exit 2 ALREADY-PRESENT（月一行幂等·重复落账拒）
  W3 报告新鲜门：collect 后报告 mtime < 子进程起点-1s → exit 5 STALE（防静默失败读旧件）

amend 修正行命令（09-30 N1 轮发现缺口收口：午班提前 run 落账→月末行滞留午间快照·
run W2 幂等拒追加=修正无路径——amend=append-only 修正行，不删不改来源行·审计链完整）：
  WA 修正幂等门：已有 [month-end-amend <month> 行 → exit 2 ALREADY-AMENDED（月一修正行）
  WB 来源行在账门：无 [month-end <month> 来源行 → exit 6 NOTHING-TO-AMEND（无源不修·用 run）
  W1/W3 与 run 同律（月份闭合+collect 报告新鲜）；修正行=collect 权威行前缀改写+AMEND 注释后缀

用法：python Tools/month_end_append.py run|amend [--month YYYY-MM] | python Tools/month_end_append.py selftest
退出码：0=APPENDED / 2=ALREADY（run=PRESENT·amend=AMENDED 幂等） / 3=CORRUPT（strict 门·按 T24 流程修复） / 4=EARLY / 5=COLLECT-FAILED / 6=NOTHING-TO-AMEND
"""
import argparse
import datetime
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import round_append  # 同目录 utf-8 唯一路径件（J1/J2/J3 全继承）

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

LEDGER = ROOT / "state" / "rounds.log"


def last_day_of_month(year, month):
    nxt = datetime.date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    return nxt - datetime.timedelta(days=1)


def guard_month_closed(month, today=None):
    """W1：返回 (ok, message)。today>=末日=闭合（含漏跑补跑）。"""
    y, m = int(month[:4]), int(month[5:7])
    today = today or datetime.date.today()
    last = last_day_of_month(y, m)
    if today >= last:
        return True, f"month {month} closed (last day {last}·today {today}·N1 窗=月末轮)"
    return False, f"EARLY: month {month} 未到末日（last day {last}·today {today}）——真 run 禁提前·漏跑补跑自次日合法"


def extract_line(payload, month):
    """报告月行提取+一致性门：month 匹配+前缀格式+非空，否则 ValueError。"""
    line = payload.get("month_end_line")
    if not isinstance(line, str) or not line.strip():
        raise ValueError("month_end_line 字段缺失/空")
    if payload.get("month") != month:
        raise ValueError(f"报告 month={payload.get('month')} 与请求 --month={month} 不一致")
    if not line.startswith(f"[month-end {month} "):
        raise ValueError("month_end_line 前缀格式不符（期望 [month-end <month> …）")
    return line


def month_line_present(text, month):
    """W2 判据：账内任意行以 [month-end <month> 开头=已落账。"""
    return any(ln.startswith(f"[month-end {month} ") for ln in text.splitlines())


def amend_line_present(text, month):
    """WA 判据：账内任意行以 [month-end-amend <month> 开头=修正行已落。"""
    return any(ln.startswith(f"[month-end-amend {month} ") for ln in text.splitlines())


AMEND_SUFFIX = "｜AMEND 修正行：来源行=同日提前落账（预注册 N1 正窗违例·保留不删）·本行=N1 正窗 collect 权威值"


def build_amend_line(line, month):
    """来源权威行 → 修正行：前缀改写 [month-end→[month-end-amend + AMEND 注释后缀。"""
    return line.replace(f"[month-end {month} ", f"[month-end-amend {month} ", 1) + AMEND_SUFFIX


def ledger_state(ledger_path):
    """返回 (code, text)：code 3=既有损行（J1 同源 strict 读）。"""
    _, text = round_append._read_strict(ledger_path)
    return (3, None) if text is None else (0, text)


def append_month_end(ledger_path, month, line):
    """W2+append 合成（selftest 夹具入口）：返回 (exit, msg)。"""
    code, text = ledger_state(ledger_path)
    if code:
        return 3, "CORRUPT-PREFLIGHT: 账本非 strict UTF-8——拒落账，先按 T24 流程修复"
    if month_line_present(text, month):
        return 2, f"ALREADY-PRESENT: [{month}] 月末行已在账（月一行幂等·零追加）"
    return round_append.append_line(str(ledger_path), line)


def append_amend(ledger_path, month, line):
    """WA+WB+append 合成（selftest 夹具入口）：返回 (exit, msg)。"""
    code, text = ledger_state(ledger_path)
    if code:
        return 3, "CORRUPT-PREFLIGHT: 账本非 strict UTF-8——拒落账，先按 T24 流程修复"
    if amend_line_present(text, month):
        return 2, f"ALREADY-AMENDED: [{month}] 月末修正行已在账（月一修正行幂等·零追加）"
    if not month_line_present(text, month):
        return 6, f"NOTHING-TO-AMEND: [{month}] 来源月末行不在账——无源不修（先 run 落来源行）"
    return round_append.append_line(str(ledger_path), line)


def _collect_fresh(month):
    """W3 采集流（run/amend 共用）：collect 子进程→新鲜门→月行提取。返回 (exit, line)。"""
    print(f"[collect] month_end_collect run --month {month}")
    t0 = time.time()
    p = subprocess.run([sys.executable, str(ROOT / "Tools" / "month_end_collect.py"),
                        "run", "--month", month], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        tail = "\n".join(((p.stdout or "") + (p.stderr or "")).splitlines()[-5:])
        print(f"COLLECT-FAILED: month_end_collect exit {p.returncode}\n{tail}")
        return 5, None
    report = ROOT / "state" / f"month-end-{month.replace('-', '')}.json"
    if not report.exists() or report.stat().st_mtime < t0 - 1:
        print("STALE-REPORT: 报告缺失或非本次 collect 产物（W3 新鲜门）——拒落账")
        return 5, None
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
        line = extract_line(payload, month)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"BAD-REPORT: {e}")
        return 5, None
    return 0, line


def cmd_run(month):
    code, text = ledger_state(LEDGER)
    if code:
        print("CORRUPT-PREFLIGHT: rounds.log 非 strict UTF-8——拒落账，先按 T24 流程修复")
        return 3
    if month_line_present(text, month):
        print(f"ALREADY-PRESENT: [{month}] 月末行已在账（月一行幂等·零追加零报告刷新）")
        return 2
    ok, gmsg = guard_month_closed(month)
    if not ok:
        print(gmsg)
        return 4
    c, line = _collect_fresh(month)
    if c:
        return c
    print("MONTH-END LINE:")
    print(line)
    code, msg = round_append.append_line(str(LEDGER), line)
    print(("APPEND " if code == 0 else "") + msg)
    return code


def cmd_amend(month):
    code, text = ledger_state(LEDGER)
    if code:
        print("CORRUPT-PREFLIGHT: rounds.log 非 strict UTF-8——拒落账，先按 T24 流程修复")
        return 3
    if amend_line_present(text, month):
        print(f"ALREADY-AMENDED: [{month}] 月末修正行已在账（月一修正行幂等·零追加）")
        return 2
    if not month_line_present(text, month):
        print(f"NOTHING-TO-AMEND: [{month}] 来源月末行不在账——无源不修（先 run 落来源行）")
        return 6
    ok, gmsg = guard_month_closed(month)
    if not ok:
        print(gmsg)
        return 4
    c, line = _collect_fresh(month)
    if c:
        return c
    aline = build_amend_line(line, month)
    print("MONTH-END AMEND LINE:")
    print(aline)
    code, msg = append_amend(LEDGER, month, aline)
    print(("APPEND " if code == 0 else "") + msg)
    return code


def _selftest():
    ok = [0]

    def check(name, cond):
        ok[0] += 1 if cond else 0
        print(("PASS" if cond else "FAIL") + f" {name}")
        return cond

    # W1 月份闭合门
    check("S1 W1 门=未到末日拒跑（真 run 禁提前）", guard_month_closed("2026-09", datetime.date(2026, 9, 29))[0] is False)
    g2a, _ = guard_month_closed("2026-09", datetime.date(2026, 9, 30))
    g2b, _ = guard_month_closed("2026-09", datetime.date(2026, 10, 5))
    check("S2 W1 门=末日即闭合+漏跑补跑合法", g2a and g2b)
    check("S3 W1 门=未来月拒跑（跨月防误）", guard_month_closed("2026-12", datetime.date(2026, 9, 30))[0] is False)
    # 提取门
    fix_payload = {"month": "2026-09", "month_end_line": "[month-end 2026-09 planning-state light line · E25] 商业面 tokens_mtok=0 测试行"}
    check("S4 提取=一致月+前缀格式通过", extract_line(fix_payload, "2026-09").startswith("[month-end 2026-09 "))
    bad = [({"month": "2026-08", "month_end_line": "[month-end 2026-08 x"}, "2026-09"),
           ({"month": "2026-09", "month_end_line": ""}, "2026-09"),
           ({"month": "2026-09", "month_end_line": "无前缀行"}, "2026-09")]
    check("S5 提取门=月不匹配/空行/坏前缀三拒", all(_raises(ValueError, extract_line, b, m) for b, m in bad))
    # W2+append 端到端（temp 夹具·零 state/ 污染）
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "rounds.log"
        c1, m1 = append_month_end(led, "2026-09", fix_payload["month_end_line"])
        raw1 = led.read_bytes()
        check("S6 落账=append ok+无BOM+strict+行在", c1 == 0 and not raw1.startswith(b"\xef\xbb\xbf")
              and month_line_present(led.read_text(encoding="utf-8"), "2026-09"))
        c2, m2 = append_month_end(led, "2026-09", fix_payload["month_end_line"])
        check("S7 幂等=再跑 ALREADY-PRESENT exit2 零追加", c2 == 2 and led.read_text(encoding="utf-8").count("[month-end") == 1)
        led2 = Path(td) / "bad.log"
        led2.write_bytes("正常行\n".encode("utf-8") + "中文损行".encode("gbk"))
        c3, _ = append_month_end(led2, "2026-09", "x")
        check("S8 损行前置门=exit3（J1 继承）", c3 == 3)
    # amend 修正行（同日提前落账缺口收口面）
    fix_origin = "[month-end 2026-09 planning-state light line · E25] 商业面 tokens_mtok=0 午间快照行"
    fix_amend = build_amend_line(fix_origin, "2026-09")
    check("S9 amend 行构造=前缀改写+AMEND 后缀+来源行原文不动",
          fix_amend.startswith("[month-end-amend 2026-09 ") and "AMEND" in fix_amend
          and fix_origin.startswith("[month-end 2026-09 "))
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "rounds.log"
        led.write_text("普通轮行\n" + fix_origin + "\n", encoding="utf-8")
        c4, _ = append_amend(led, "2026-09", fix_amend)
        txt = led.read_text(encoding="utf-8")
        check("S10 amend 落账=修正行入账+来源行原文零改（两行并存）",
              c4 == 0 and txt.count("[month-end") == 2 and fix_origin in txt)
        c5, _ = append_amend(led, "2026-09", fix_amend)
        check("S11 amend 幂等=再修 exit2 仍两行（月一修正行）",
              c5 == 2 and led.read_text(encoding="utf-8").count("[month-end") == 2)
        led3 = Path(td) / "empty.log"
        led3.write_text("普通轮行\n", encoding="utf-8")
        c6, _ = append_amend(led3, "2026-09", fix_amend)
        check("S12 WB 无源不修=exit6 零追加", c6 == 6 and led3.read_text(encoding="utf-8") == "普通轮行\n")
    total = 12
    print(f"selftest: {ok[0]}/{total} PASS" if ok[0] == total else f"selftest: FAIL ({ok[0]}/{total})")
    return 0 if ok[0] == total else 1


def _raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
        return False
    except exc:
        return True


def main():
    ap = argparse.ArgumentParser(description="N1 月末行一命令落账微件（BC-P-22·E25）")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="月末轮一命令：collect→读报告→落账 rounds.log")
    r.add_argument("--month", default=datetime.date.today().strftime("%Y-%m"))
    a = sub.add_parser("amend", help="月末修正行：来源行在账+数据滞后→N1 正窗 collect 权威修正行（append-only 不删不改来源行）")
    a.add_argument("--month", default=datetime.date.today().strftime("%Y-%m"))
    sub.add_parser("selftest", help="自测（temp 夹具·零真 run 零 state/ 污染）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        return _selftest()
    try:
        y, m = int(args.month[:4]), int(args.month[5:7])
        datetime.date(y, m, 1)
    except (ValueError, IndexError):
        print("invalid --month, expect YYYY-MM")
        return 1
    if args.cmd == "amend":
        return cmd_amend(args.month)
    return cmd_run(args.month)


if __name__ == "__main__":
    sys.exit(main())
