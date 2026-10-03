#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vat_break_even.py — 小店主体增值税负临界点测算微件（BC-P-36 批活转化·M43·quota_design 同模式）

法源链（官方原文级四锚·证据=state/m41-taxrate-official-20261003.txt + state/m42-vatlaw-official-20261003.txt）：
- 《增值税法》第十条（三）销售服务、无形资产税率为 6%（一般纳税人档）｜第十一条 征收率 3%｜第九条 年应征销售额 500 万线
- 《实施条例》（国务院令第 826 号）第三十六条 登记为一般纳税人后不得转为小规模纳税人｜第三十七条（一）购买方为自然人不得开专票
- 2026 年第 10 号公告（衔接件·窗至 2027-12-31）第一条 起征点月销售额 10 万元（季 30 万）｜第三条（三）第 6 目 小规模 3% 减按 1%
- 销售额口径＝含税销售额÷（1＋规定征收率）〔10 号公告第三条（四）3 目〕

核心式（S=月销售额·万元·不含税；r=可抵扣进项税额÷不含税销售额）：
  小规模线　　S<10 免征；S≥10 应纳=S×征收率（起征点语义=未达免征·达到全额计征；简易计税·无进项抵扣面）
  一般纳税人线 应纳=max(0, S×6%−S×r)（负值=当期留抵结转·现金税负 0）
  临界进项占比 r*=6%−征收率（应纳相等的代数解·与 S 无关）：1% 线=5%｜3% 线（窗后回归档）=3%

零真实交易·零定价动作：测算=本司立场非税务结论；品目归类与主体身份落定权随 P7 书面确认+CEO 定价确认批。
"""
import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 官方锚参数（selftest A 组回归保护·改动须对账证据件）
RATE_GENERAL = 0.06      # 增值税法第十条（三）
LEVY_SMALL_STD = 0.03     # 增值税法第十一条
LEVY_SMALL_RED = 0.01     # 2026 年第 10 号公告第三条（三）第 6 目（窗 2026-01-01→2027-12-31）
THRESHOLD_WAN = 10.0      # 10 号公告第一条（一）月口径起征点（季=30 万同条）
ANNUAL_LINE_WAN = 500.0   # 增值税法第九条
DEFAULT_RATIO_BAND = [0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08]
DISCLAIMER = "测算=本司立场非税务结论（四品目建议归类=docs/legal/tax-category-confirmation-brief-v1.md §三·P7 书面确认窗随开店）"


def small_vat(sales_wan, levy=LEVY_SMALL_RED):
    """小规模线（万元）：未达起征点免征；达到起征点全额计征（起征点语义·非免征额）。"""
    return 0.0 if sales_wan < THRESHOLD_WAN else sales_wan * levy


def general_vat(sales_wan, input_ratio):
    """一般纳税人线（万元）：销项 6%−可抵扣进项；负值=留抵结转（当期现金税负 0）。"""
    return max(0.0, sales_wan * (RATE_GENERAL - input_ratio))


def break_even_ratio(levy=LEVY_SMALL_RED):
    """临界进项占比 r*=6%−征收率（与销售额代数无关）。"""
    return RATE_GENERAL - levy


def needs_general(monthly_sales_wan):
    """年化超 500 万线→须登记一般纳税人（第九条·小规模身份不可选）。"""
    return monthly_sales_wan * 12 > ANNUAL_LINE_WAN


def verdict(sales_wan, input_ratio, levy=LEVY_SMALL_RED):
    s, g = small_vat(sales_wan, levy), general_vat(sales_wan, input_ratio)
    if abs(s - g) < 1e-9:
        return "等值"
    return "小规模优" if s < g else "一般纳税人优"


def render(sales_list, ratio_list):
    """组装表行列表（cmd_table 打印·selftest 确定性面复用）。"""
    lines = []
    ap = lines.append
    ap("== 小店主体增值税负临界点测算（BC-P-36·M43）==")
    ap("口径：S=月销售额·万元·不含税〔销售额=含税÷(1+规定征收率)·10 号公告三（四）3 目〕｜税额列=元/月")
    ap("小规模线=起征点 10 万内免征·达到起征点全额计征（起征点语义）；超线 3% 减按 1%（窗至 2027-12-31·窗后回归 3%）｜一般纳税人线=6% 销项−可抵扣进项")
    ap("常驻标注：" + DISCLAIMER)
    for sales in sales_list:
        annual = sales * 12
        head = "-- S=%.2f 万元/月（年化 %.2f 万）" % (sales, annual)
        if needs_general(sales):
            head += "  ⚠ 年化超 500 万线〔增值税法第九条〕→须登记一般纳税人·小规模列仅对照"
        elif sales < THRESHOLD_WAN:
            head += "  〔未达起征点·小规模免征〕"
        ap("")
        ap(head)
        ap("| 进项占比 r | 小规模@1%(窗内) | 小规模@3%(窗后) | 一般纳税人@r | 判定@1% | 判定@3% |")
        ap("|---|---|---|---|---|---|")
        s1 = small_vat(sales, LEVY_SMALL_RED)
        s3 = small_vat(sales, LEVY_SMALL_STD)
        for r in ratio_list:
            g = general_vat(sales, r)
            ap("| %.0f%% | %d | %d | %d | %s | %s |" % (
                r * 100, round(s1 * 10000), round(s3 * 10000), round(g * 10000),
                verdict(sales, r, LEVY_SMALL_RED), verdict(sales, r, LEVY_SMALL_STD)))
    ap("")
    ap("临界进项占比 r*：1%% 线=%.0f%%｜3%% 线（窗后）=%.0f%%——r>r* 则一般纳税人线增值税负更低（S≥10 万域；S<10 万小规模免征恒优）"
       % (break_even_ratio(LEVY_SMALL_RED) * 100, break_even_ratio(LEVY_SMALL_STD) * 100))
    ap("注记：登记为一般纳税人后不得转为小规模纳税人〔实施条例第三十六条〕=身份决策单向·临界点两侧切换成本不对称，登记窗决策须前置；")
    ap("　　　购买方为自然人不得开增值税专用发票〔实施条例第三十七条（一）〕=小店 C 端四品目仅普票·专票面仅 B 端直签场景；")
    ap("　　　本测算=增值税应纳税额面·不含附加税费与企业所得税（小微 5%/六税两费减半=另面·12 号公告）；减按 1% 窗后口径以届时有效公告为准；零定价动作·落值随 CEO 定价确认批。")
    return lines


def cmd_selftest():
    fails = []

    def ck(name, ok):
        if not ok:
            fails.append(name)

    # A 官方锚参数回归（m41/m42 证据件逐源对照·防漂移）
    ck("A1 锚 6%〔增值税法第十条（三）〕", RATE_GENERAL == 0.06)
    ck("A2 锚 3%〔第十一条〕", LEVY_SMALL_STD == 0.03)
    ck("A3 锚 1%〔10 号公告三（三）6 目〕", LEVY_SMALL_RED == 0.01)
    ck("A4 锚 10 万〔10 号公告第一条〕", THRESHOLD_WAN == 10.0)
    ck("A5 锚 500 万〔第九条〕", ANNUAL_LINE_WAN == 500.0)
    # B 起征点边界（未达免征·达到全额计征）
    ck("B1 未达免征", small_vat(9.999) == 0.0)
    ck("B2 达到全额@1%", abs(small_vat(10.0) - 0.1) < 1e-9)
    ck("B3 达到全额@3%", abs(small_vat(10.0, LEVY_SMALL_STD) - 0.3) < 1e-9)
    # C 双线数学
    ck("C1 小规模 20@1%", abs(small_vat(20.0) - 0.2) < 1e-9)
    ck("C2 小规模 20@3%", abs(small_vat(20.0, LEVY_SMALL_STD) - 0.6) < 1e-9)
    ck("C3 一般零进项", abs(general_vat(20.0, 0.0) - 1.2) < 1e-9)
    ck("C4 一般 r=2%", abs(general_vat(20.0, 0.02) - 0.8) < 1e-9)
    ck("C5 留抵结转=现金 0", general_vat(20.0, 0.08) == 0.0)
    # D 临界点代数解（与 S 无关）
    ck("D1 r*@1%=5%", abs(break_even_ratio() - 0.05) < 1e-12)
    ck("D2 r*@3%=3%", abs(break_even_ratio(LEVY_SMALL_STD) - 0.03) < 1e-12)
    for s in (10.0, 20.0, 41.66):
        ck("D3 等值代数 S=%.2f" % s, abs(general_vat(s, 0.05) - small_vat(s)) < 1e-9)
    # E 500 万线
    ck("E1 线下 41.66", not needs_general(41.66))
    ck("E2 线上 41.67", needs_general(41.67))
    # F 判定逻辑
    ck("F1 超线小规模优", verdict(12.0, 0.02) == "小规模优")
    ck("F2 高进项一般优", verdict(12.0, 0.06) == "一般纳税人优")
    ck("F3 免征域小规模恒优", verdict(5.0, 0.0) == "小规模优")
    ck("F4 临界等值", verdict(20.0, 0.05) == "等值")
    # G 确定性（双跑渲染一致）
    l1 = render([10.0, 20.0, 41.67], DEFAULT_RATIO_BAND)
    l2 = render([10.0, 20.0, 41.67], DEFAULT_RATIO_BAND)
    ck("G1 渲染确定性", l1 == l2 and len(l1) > 20)

    if fails:
        print("SELFTEST FAIL:")
        for f in fails:
            print("  - " + f)
        return 1
    print("SELFTEST PASS: 锚参数回归 5+起征点边界 3+双线数学 5+临界代数 5+500 万线 2+判定 4+确定性 1 = 25 项全过")
    return 0


def main():
    ap = argparse.ArgumentParser(description="小店主体增值税负临界点测算微件（BC-P-36·M43·零定价动作）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    tp = sub.add_parser("table", help="双线税负对比表+临界点（--sales 万元/月·可多次）")
    tp.add_argument("--sales", type=float, action="append", required=True, help="月销售额（万元·不含税）")
    tp.add_argument("--input-ratio", type=float, action="append", default=None, help="可抵扣进项占销售额比（缺省=扫描带）")
    sub.add_parser("selftest", help="锚参数回归+数学断言+确定性（25 项）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        return cmd_selftest()
    for line in render(args.sales, args.input_ratio if args.input_ratio else DEFAULT_RATIO_BAND):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
