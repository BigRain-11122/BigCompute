#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""round_budget.py — OSLoop 轮预算沙漏探针（BC-P-18 工程面·纯只读零写）

背景：09-28 六连 25min 看门狗超时击杀 ≈2.5h 轮预算蒸发（被杀轮产出全丢）。
本件=超时击杀**事前**预警（BC-P-09 刻痕律=被杀轮**事后**定位的姊妹互补位）：
读 logs/os-loop/round.lock mtime=轮起锚 → elapsed/remaining（25min 预算）一行直出，
remaining<8min 即收尾预警行。轮内步界自查面（prompt 侧自愿调用·零引擎改动）。

用法：
  python Tools/round_budget.py check [--lock <path>] [--budget 25] [--warn 8]
  python Tools/round_budget.py selftest

判据（预注册·提案 BC-P-18）：
  J1 elapsed/remaining 数学=lock mtime 锚（remaining<--warn 分钟=收尾预警行 exit 4）
  J2 lock 缺位=NO-LOCK exit 5 信息态（手工/排程外触发轮零锚合法·零崩溃）
  J3 remaining<0 → WARN-OVERTIME 行（超预算面·exit 4 同预警面）
  J4 纯只读零写（lock/台账零触碰）·判负路径预注册=lock mtime 与真实轮起偏差>5min 致误导→判负留痕合法
"""
import argparse
import os
import sys
import tempfile
import time

DEFAULT_LOCK = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "logs", "os-loop", "round.lock"))
DEFAULT_BUDGET = 25  # 分钟（轮预算 ≤25min 律）
DEFAULT_WARN = 8     # 分钟（remaining<8min 即收尾预警·提案预注册值）


def _fmt(sec):
    sec = int(sec)
    return "%dm%02ds" % (sec // 60, sec % 60)


def _state(age_sec, budget_min, warn_min):
    """纯数学：返回 (remaining_sec, exit_code)。0=OK / 4=WARN。"""
    remaining = budget_min * 60 - age_sec
    code = 4 if remaining < warn_min * 60 else 0
    return remaining, code


def check(lock_path, budget_min, warn_min):
    """返回 (exit_code, lines)。exit 0=BUDGET-OK / 4=BUDGET-WARN / 5=NO-LOCK。"""
    if not os.path.exists(lock_path):
        return 5, ["NO-LOCK: %s 不在位（手工/排程外触发轮零锚——elapsed 不可测，如实跳过）" % lock_path]
    age = time.time() - os.path.getmtime(lock_path)
    if age < 0:
        age = 0.0  # 时钟回拨保护
    remaining, code = _state(age, budget_min, warn_min)
    lines = ["round budget: elapsed=%s remaining=%s (cap %dmin·lock=%s)"
             % (_fmt(age), _fmt(max(remaining, 0)), budget_min, lock_path)]
    if code == 4:
        if remaining < 0:
            lines.append("WARN-OVERTIME: 超 %dmin 预算 %s——看门狗击杀风险高位，立即收尾"
                         % (budget_min, _fmt(-remaining)))
        else:
            lines.append("WARN: remaining<%dmin——下一动作前自查「剩余预算够不够」，不够即收尾（刻痕+commit 保产出）"
                         % warn_min)
    return code, lines


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append((name, ok, detail))

    # S1 数学边界（纯函数·确定性）
    r, c = _state(0, 25, 8)
    run("S1a 数学 新轮满预算", r == 1500 and c == 0)
    r, c = _state(25 * 60 - 481, 25, 8)
    run("S1b 数学 预警线外 1s=OK", r == 481 and c == 0)
    r, c = _state(25 * 60 - 480, 25, 8)
    run("S1c 数学 预警线=OK（严格小于才预警）", r == 480 and c == 0)
    r, c = _state(25 * 60 - 479, 25, 8)
    run("S1d 数学 预警线内 1s=WARN", r == 479 and c == 4)
    r, c = _state(25 * 60 + 60, 25, 8)
    run("S1e 数学 OVERTIME 态", r == -60 and c == 4)

    with tempfile.TemporaryDirectory() as td:
        lock = os.path.join(td, "round.lock")
        with open(lock, "w") as f:
            f.write("12345")
        # S2 新轮端到端：age≈0 → OK 无预警
        os.utime(lock, (time.time(), time.time()))
        code, lines = check(lock, 25, 8)
        run("S2 新轮端到端 exit0", code == 0 and "elapsed=" in lines[0] and "remaining=" in lines[0]
            and not any(l.startswith("WARN") for l in lines[1:]))
        # S3 age 20min → 预警
        os.utime(lock, (time.time() - 1200, time.time() - 1200))
        code, lines = check(lock, 25, 8)
        run("S3 20min 轮 exit4+WARN", code == 4 and any(l.startswith("WARN:") for l in lines))
        # S4 age 30min → OVERTIME
        os.utime(lock, (time.time() - 1800, time.time() - 1800))
        code, lines = check(lock, 25, 8)
        run("S4 30min 轮 OVERTIME 行", code == 4 and any(l.startswith("WARN-OVERTIME") for l in lines))
        # S5 lock 缺位 → NO-LOCK exit5
        code, lines = check(os.path.join(td, "nope.lock"), 25, 8)
        run("S5 缺锁 NO-LOCK exit5", code == 5 and lines[0].startswith("NO-LOCK"))
        # S6 纯只读：check 前后 lock mtime 不变（J4）
        os.utime(lock, (time.time() - 300, time.time() - 300))
        before = os.path.getmtime(lock)
        check(lock, 25, 8)
        run("S6 纯只读 mtime 不变", os.path.getmtime(lock) == before)

    n_pass = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(("PASS " if ok else "FAIL ") + name + ("  [" + detail + "]" if detail else ""))
    print("SELFTEST %s %d/%d" % ("PASS" if n_pass == len(checks) else "FAIL", n_pass, len(checks)))
    return 0 if n_pass == len(checks) else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="轮预算沙漏探针（25min 可视化·纯只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="读 lock mtime → elapsed/remaining 一行直出")
    c.add_argument("--lock", default=DEFAULT_LOCK)
    c.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    c.add_argument("--warn", type=int, default=DEFAULT_WARN)
    sub.add_parser("selftest", help="自测（temp 夹具零污染）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        sys.exit(_selftest())
    code, lines = check(args.lock, args.budget, args.warn)
    for l in lines:
        print(l)
    sys.exit(code)


if __name__ == "__main__":
    main()
