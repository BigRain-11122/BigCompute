#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quota_design.py — 订阅档月配额数值设计计算器（口径 B 参数化·M16 高阶档扩测/M12 同法）

法源链：C-20260927-01 票档归档（议题③ 选 A·2026-09-29 12:00）→ M16 解锁 24h 落值窗
        ｜BC-F-20260927-03 配额设计律（价值感 ≥1.5×19.9 单包）｜R-20260928-compute-cost-economics Q5（v1=月末归集回溯口径·结算唯一）

核心式：Q_max = P × (1 − g) ÷ v1    P=档位月价（元）·g=目标毛利率·v1=口径 B 单位成本（元/1M token·⬜ 待首月实测）
价值感线：Q_min = 1.5 × E_单包      （实测锚 E=243 tokens·state/e12-anchor-20260928-2243.json·SKU 终稿复测）
单包等效：N = Q ÷ E_单包            （营销可感口径=每月 N 次生成）

零定价动作：本工具输出=数值设计测算面；配额落值权随 CEO 定价确认批（C-20260927-01 注册·定价确认权=CEO 保留面）。
"""
import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

V1_BAND = [0.09, 0.15, 0.30, 0.50, 0.80]  # v1 设计扫描带（元/1M token·月末归集回溯唯一口径）
MARGINS = [0.70, 0.75, 0.80]               # 目标毛利率三档（g=70%=稳健下档·设计值 ≥70%）
E_PACKAGE_TOKENS = 243                    # E_单包 dry-run 首单实测锚（两调用面口径·v0）
VALUE_FEEL_MULT = 1.5                      # 配额设计律系数


def q_max(price, margin, v1):
    return price * (1.0 - margin) / v1


def q_min_mtok():
    return VALUE_FEEL_MULT * E_PACKAGE_TOKENS / 1e6


def n_equiv(q_mtok):
    return q_mtok * 1e6 / E_PACKAGE_TOKENS


def q_from_n(n):
    return n * E_PACKAGE_TOKENS / 1e6


def cmd_table(args):
    qmin = q_min_mtok()
    print("== 订阅档月配额数值设计（口径 B 参数化·零定价动作）==")
    print(f"Q_min 价值感线 = {VALUE_FEEL_MULT} × {E_PACKAGE_TOKENS} = {VALUE_FEEL_MULT * E_PACKAGE_TOKENS} tokens/月"
          f" = {qmin} Mtok/月（M12 发布值 365/0.000365=舍入口径·BC-F-20260927-03 配额设计律·实测锚）")
    for price in args.price:
        print()
        print("-- 档位 ¥%.2f/月：Q_max = %.2f×(1−g)÷v1（Mtok/月）--" % (price, price))
        header = "| v1(元/1M) | " + " | ".join("g=%d%% → %.2f÷v1" % (g * 100, price * (1 - g)) for g in MARGINS) + " |"
        print(header)
        print("|---" * (len(MARGINS) + 1) + "|")
        for v1 in V1_BAND:
            cells = " | ".join("%.1f" % q_max(price, g, v1) for g in MARGINS)
            print("| %.2f | %s |" % (v1, cells))
        worst = min(q_max(price, MARGINS[0], v1) for v1 in V1_BAND)  # M12 语义：g=70% 稳健下档列内最保守 v1 行
        print(f"最保守行（v1=0.80/g=70%）→ Q_max={worst:.1f} Mtok ≈ Q_min 的 {worst / qmin:.0f}× → 价值感线退化为非约束·约束面=毛利线闸唯一")
        if args.n_window:
            lo, hi = args.n_window
            qlo, qhi = q_from_n(lo), q_from_n(hi)
            print("建议设计窗 N=%d~%d 次/月 → Q=%.4f~%.4f Mtok/月（对最保守行余 %.0f×·E=%d 锚·建议级·落值随 CEO 批）"
                  % (lo, hi, qlo, qhi, worst / qhi, E_PACKAGE_TOKENS))


def cmd_selftest(args):
    fails = []

    def eq(name, got, want, tol=0.051):
        if abs(got - want) > tol:
            fails.append("%s: got %.4f want %.4f" % (name, got, want))

    # M12 已发布表回归（quota-design-29.9-v1.md §一·29.9 档 15 格抽全带）
    m12 = {(0.09, 0.70): 99.7, (0.15, 0.70): 59.8, (0.30, 0.70): 29.9, (0.50, 0.70): 17.9, (0.80, 0.70): 11.2,
           (0.09, 0.75): 83.1, (0.15, 0.75): 49.8, (0.30, 0.75): 24.9, (0.50, 0.75): 15.0, (0.80, 0.75): 9.3,
           (0.09, 0.80): 66.4, (0.15, 0.80): 39.9, (0.30, 0.80): 19.9, (0.50, 0.80): 12.0, (0.80, 0.80): 7.5}
    for (v1, g), want in m12.items():
        eq("M12回归 29.9 v1=%.2f g=%.2f" % (v1, g), q_max(29.9, g, v1), want)

    # M16 高阶档抽验（手算对照）
    eq("49.9 v1=0.30 g=70%", q_max(49.9, 0.70, 0.30), 49.9)
    eq("49.9 v1=0.80 g=70%", q_max(49.9, 0.70, 0.80), 18.7)
    eq("49.9 v1=0.15 g=75%", q_max(49.9, 0.75, 0.15), 83.2)
    eq("99   v1=0.30 g=70%", q_max(99.0, 0.70, 0.30), 99.0)
    eq("99   v1=0.80 g=70%", q_max(99.0, 0.70, 0.80), 37.1)
    eq("99   v1=0.09 g=80%", q_max(99.0, 0.80, 0.09), 220.0)

    # 价值感线与单包等效（M12 §二/§三 口径）
    eq("Q_min(Mtok)", q_min_mtok(), 0.0003645, tol=1e-9)
    eq("N=30 → Q(Mtok)", q_from_n(30), 0.00729, tol=1e-6)
    eq("Q=0.0073 → N", n_equiv(0.00729), 30.0, tol=0.05)

    # 价值感线退化判定（全带最保守行仍 ≫ Q_min）
    for price in (29.9, 49.9, 99.0):
        worst = min(q_max(price, g, v1) for g in MARGINS for v1 in V1_BAND)
        if worst <= q_min_mtok():
            fails.append("价格 %.1f 最保守行 <= Q_min（价值感线成约束=口径异常）" % price)

    if fails:
        print("SELFTEST FAIL:")
        for f in fails:
            print("  - " + f)
        return 1
    print("SELFTEST PASS: M12 回归 15 格+高阶档 6 抽验+Q_min/N 数学+价值感线退化判定 全过")
    return 0


def main():
    ap = argparse.ArgumentParser(description="订阅档月配额数值设计计算器（M16/M12 同法·零定价动作）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    tp = sub.add_parser("table", help="输出档位 Q_max 参数化表+价值感线+建议窗")
    tp.add_argument("--price", type=float, action="append", required=True, help="档位月价（可多次）")
    tp.add_argument("--n-window", type=int, nargs=2, metavar=("LO", "HI"), default=None, help="建议设计窗 N 次数（单包等效）")
    sub.add_parser("selftest", help="回归断言（M12 已发布表+高阶档手算对照）")
    args = ap.parse_args()
    if args.cmd == "table":
        cmd_table(args)
        return 0
    return cmd_selftest(args)


if __name__ == "__main__":
    sys.exit(main())
