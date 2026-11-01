#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scope_proof.py - 判据分层：闸在给结论时必须自证扫描域（D-20261101-20 / D-20261102-02 承接·M92）

D-20 事故原型：lane-task-guard 打印 ALL-READY，而域外任务全黑——读数没错，结论越界。
修法不是改读数，而是让每个闸同时打印「扫了谁 / 没扫谁 / 哪条读不到」，
使 GREEN 的含义被限定在 domain 之内。

本模块只做格式化，不做判定：判定仍归各闸自己（禁在此处聚合出一个更大的绿）。
  -- 三行口径 --
  domain=<扫描面：类别 + 计数>
  not_scanned=<未纳入判定的范围：计数 + 示例>
  unreadable=<读不到的对象：计数 + 示例>   （无则 none）

selftest = 形状/确定性/截断/空集四类断言，零外部依赖。
"""
import sys

SAMPLE_CAP = 5


def _short(items, cap=SAMPLE_CAP):
    items = list(items)
    if not items:
        return "none"
    head = ", ".join(str(x) for x in items[:cap])
    if len(items) > cap:
        return "%s ...(+%d)" % (head, len(items) - cap)
    return head


def build(domain, not_scanned=(), unreadable=()):
    """返回三行字符串列表（确定性：不排序输入，调用方保证稳定顺序）。"""
    not_scanned = list(not_scanned)
    unreadable = list(unreadable)
    return [
        "domain=%s" % str(domain),
        "not_scanned=%d [%s]" % (len(not_scanned), _short(not_scanned)),
        "unreadable=%d [%s]" % (len(unreadable), _short(unreadable)),
    ]


def emit(domain, not_scanned=(), unreadable=(), out=None):
    out = sys.stdout if out is None else out
    for line in build(domain, not_scanned, unreadable):
        print(line, file=out)
    return build(domain, not_scanned, unreadable)


def coverage_line(scanned, skipped, unreadable):
    """一行覆盖率读数：扫到/跳过/不可读三面并排，防只看结论。"""
    total = scanned + skipped + unreadable
    pct = 0.0 if total == 0 else round(100.0 * scanned / total, 1)
    return "coverage=scanned:%d skipped:%d unreadable:%d ratio=%.1f%%" % (
        scanned, skipped, unreadable, pct)


def cmd_selftest():
    ok = [0]

    def chk(name, cond):
        ok[0] += 1 if cond else 0
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    lines = build("register 3/levels", ["a.md"], [])
    chk("S1 三行形状固定且顺序固定",
        len(lines) == 3 and lines[0].startswith("domain=")
        and lines[1].startswith("not_scanned=") and lines[2].startswith("unreadable="))
    chk("S2 空集显式 none 而非空白", build("x")[1] == "not_scanned=0 [none]")
    chk("S3 计数与示例一致", build("x", ["a", "b"])[1] == "not_scanned=2 [a, b]")
    many = ["f%02d" % i for i in range(9)]
    line = build("x", many)[1]
    chk("S4 超样截断带余数", line.startswith("not_scanned=9 [f00, f01, f02, f03, f04 ...(+4)]"))
    chk("S5 确定性（同输入同输出）", build("x", many) == build("x", many))
    chk("S6 unreadable 独立成行不并入 skipped", build("x", ["a"], ["b"])[2] == "unreadable=1 [b]")
    chk("S7 coverage 分母零不除零", coverage_line(0, 0, 0).endswith("ratio=0.0%"))
    chk("S8 coverage 三面并排计数", coverage_line(7, 2, 1) ==
        "coverage=scanned:7 skipped:2 unreadable:1 ratio=70.0%")
    chk("S9 非字符串项安全落地", build("x", [1, None])[1] == "not_scanned=2 [1, None]")
    chk("S10 emit 返回与打印同源", build("x")[0] == emit("x", out=_Null())[0])
    print("selftest %d/10 %s" % (ok[0], "PASS" if ok[0] == 10 else "FAIL"))
    return 0 if ok[0] == 10 else 1


class _Null(object):
    def write(self, _s):
        return 0

    def flush(self):
        return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["selftest"]:
        return cmd_selftest()
    print("usage: scope_proof.py selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())