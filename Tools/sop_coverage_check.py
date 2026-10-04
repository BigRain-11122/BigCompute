#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigCompute SOP 覆盖三级对照机检件（O-2026-0930-027 §① 承接·M49 步③ G2·tech 面自驱轨）

O-027 §①=业务线/司内部门/开发模块三级盘点；本工具=盘点清单（docs/ops/sop-inventory-gap-list-v1.md）
的三级覆盖矩阵机检面（防漂移：存在性+轻量闸+注册表计数可复跑·10-07 治理日委员会聚合审数据源）：
  F1 三级矩阵路径存在性（L1 业务线×L2 部门×L3 开发模块注册面逐件 in-tree）
  F2 轻量闸（SOP md 件 ≤200 行/25KB·超线已申报两件=DECLARED-PENDING 呈 10-07 聚合审·非违例）
  F3 Tools 注册表漂移（Tools 根 py 计数 vs 盘点 D 面申报值）
  F4 覆盖汇总（三级各=covered/missing 计数）
只读零台面变更（唯一写=JSON 证据落 state/）；零跨仓动作。
"""
import argparse
import glob
import json
import os
import sys
import tempfile
import time

LINE_GATE = 200
BYTE_GATE = 25600  # 25KB
# 超线已申报件（盘点 §C ⚠ 注记·legal 是否适用单件闸=呈 10-07 聚合审定谳·本轮只报不判）
ALLOWLIST_OVERSIZE = [
    "docs/legal/user-service-agreement.md",
    "docs/legal/data-report-desensitization-standard.md",
]
LEGAL_12 = [
    "docs/legal/user-service-agreement.md", "docs/legal/data-report-desensitization-standard.md",
    "docs/legal/blind-box-gacha-compliance.md", "docs/legal/service-continuity-terms.md",
    "docs/legal/tax-category-confirmation-brief-v1.md",
    "docs/legal/pre-final-review-manual-verification-checklist.md",
    "docs/legal/official-source-render-index.md", "docs/legal/purchase-checkbox-copy.md",
    "docs/legal/data-processing-agreement.md", "docs/legal/b2b-no-label-delivery.md",
    "docs/legal/tipping-declaration.md", "docs/legal/privacy-policy.md",
]
# 三级覆盖注册面（L1 业务线×L2 司内部门×L3 开发模块·O-027 §①·与盘点清单 v0.2 §二互为正副本）
REGISTER = {
    "L1-业务线": {
        "抖音小店电商": ["docs/ops/store-opening-checklist-v1.md", "docs/ops/merchandise-listing-v0.md",
                      "docs/ops/fulfillment-sku-mapping-v1.md", "docs/ops/refund-disputes-sop.md"],
        "直播带货": ["docs/ops/livestream-plan-v1.md", "docs/ops/livestream-cart-scripts-v1.md",
                   "docs/ops/livestream-cart-mount-checklist-v1.md", "docs/ops/promotion-packaging-sop-v1.md"],
        "B端数据年报": ["docs/ops/b2b-playbook-v1.md", "docs/ops/b2b-custom-report-pricing-band-v1.md"],
        "私域订阅": ["docs/ops/private-domain-plan-v1.md"],
        "会员权益": ["docs/ops/membership-benefits-v1.md"],
        "算力成本商业化": ["docs/ops/month-end-cost-collection-sheet-v1.md", "docs/ops/quota-design-29.9-v1.md",
                     "docs/ops/quota-design-49.9-99-v1.md", "docs/ops/pricing-confirmation-package-29.9-v1.md"],
    },
    "L2-司内部门": {
        "外部成交通道部": ["docs/ops/store-opening-checklist-v1.md", "docs/ops/merchandise-listing-v0.md",
                     "docs/ops/fulfillment-sku-mapping-v1.md", "docs/ops/store-ledger-wiring-predesign-v1.md"],
        "定价与算力成本核算部": ["docs/ops/quota-design-29.9-v1.md", "docs/ops/quota-design-49.9-99-v1.md",
                          "docs/ops/pricing-confirmation-package-29.9-v1.md",
                          "docs/legal/tax-category-confirmation-brief-v1.md",
                          "docs/ops/month-end-cost-collection-sheet-v1.md",
                          "docs/ops/b2b-custom-report-pricing-band-v1.md"],
        "直播带货运营部": ["docs/ops/livestream-plan-v1.md", "docs/ops/livestream-cart-scripts-v1.md",
                     "docs/ops/livestream-cart-mount-checklist-v1.md", "docs/ops/promotion-packaging-sop-v1.md"],
        "风控法务部": LEGAL_12 + ["docs/ops/paypoint-compliance-map-v1.md", "docs/ops/paypoint-alignment-matrix-v1.md"],
    },
    "L3-开发模块": {
        "治理循环": ["Tools/iteration_prompt.txt", "state/runbook.md", "docs/research-dept-charter.md",
                  "docs/qa-smoke-test-charter.md"],
        "QA巡检": ["Tools/qa_smoke.py", "Tools/holiday_readiness_check.py", "docs/qa-smoke-test-charter.md"],
        "数据采集": ["Tools/gpu_idle_collector.py", "Tools/gpu_energy_profile.py", "Tools/vram_window_probe.py"],
        "法务文本": LEGAL_12,
        "批池烘焙": ["Tools/batch_pool.py", "Tools/bake_accept_check.py", "docs/ops/batch-pool-stock-v1.jsonl"],
        "履约管线": ["Tools/fulfillment/test_pipeline.py"],
    },
}
DECLARED_TOOLS = 38  # Tools 根 py 申报数（盘点 §D·sop_coverage_check 入册后）


def _measure(path):
    # 行数口径=盘点证据口径（PowerShell Measure-Object -Line·非空行）
    with open(path, "rb") as f:
        data = f.read()
    lines = sum(1 for ln in data.decode("utf-8", errors="replace").splitlines() if ln.strip())
    return lines, len(data)


def audit(repo, register=None, allowlist=None, declared_tools=DECLARED_TOOLS):
    register = REGISTER if register is None else register
    allowlist = ALLOWLIST_OVERSIZE if allowlist is None else allowlist
    f1_missing, f2_over, f2_pending, summary = [], [], [], {}
    gate_seen = set()
    for level, modules in register.items():
        lv_covered = 0
        for mod, paths in modules.items():
            missing = [p for p in paths if not os.path.exists(os.path.join(repo, p))]
            f1_missing.extend("%s/%s:%s" % (level, mod, p) for p in missing)
            if not missing:
                lv_covered += 1
            for p in paths:
                full = os.path.join(repo, p)
                if not os.path.exists(full) or not p.endswith(".md") or p in gate_seen:
                    continue
                gate_seen.add(p)
                lines, nbytes = _measure(full)
                if lines > LINE_GATE or nbytes > BYTE_GATE:
                    (f2_pending if p in allowlist else f2_over).append(
                        {"path": p, "lines": lines, "bytes": nbytes})
        summary[level] = {"covered": lv_covered, "total": len(register[level])}
    actual_tools = len(glob.glob(os.path.join(repo, "Tools", "*.py")))
    f3_drift = None if actual_tools == declared_tools else {"actual": actual_tools, "declared": declared_tools}
    viol = len(f1_missing) + len(f2_over) + (1 if f3_drift else 0)
    return {
        "tool": "sop_coverage_check v1.0", "order": "O-2026-0930-027", "repo": repo,
        "verdict": "SOP-COVERAGE-GREEN" if viol == 0 else "SOP-COVERAGE-RED",
        "violations_total": viol,
        "f1_missing": f1_missing, "f2_oversize_violation": f2_over,
        "f2_declared_pending_1007": f2_pending, "f3_tools_drift": f3_drift,
        "f4_level_summary": summary,
    }


def cmd_check(args):
    res = audit(os.path.abspath(args.repo))
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(args.repo, "state", "sop-coverage-%s.json" % ts)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("verdict=%s violations=%d" % (res["verdict"], res["violations_total"]))
    for lv, s in res["f4_level_summary"].items():
        print("  %s covered=%d/%d" % (lv, s["covered"], s["total"]))
    for item in res["f1_missing"]:
        print("  missing:", item)
    for item in res["f2_oversize_violation"]:
        print("  oversize:", item["path"], item["lines"], "lines", item["bytes"], "bytes")
    for item in res["f2_declared_pending_1007"]:
        print("  declared-pending-1007:", item["path"])
    if res["f3_tools_drift"]:
        print("  tools-drift:", res["f3_tools_drift"])
    print("evidence=%s" % out_path)
    return 0 if res["verdict"] == "SOP-COVERAGE-GREEN" else 1


def _fixture(tmp):
    repo = os.path.join(tmp, "fx")
    reg = {"L1-业务线": {"b1": ["docs/ops/a.md", "docs/ops/b.md"]},
           "L2-司内部门": {"d1": ["docs/ops/a.md"]},
           "L3-开发模块": {"m1": ["Tools/x.py"]}}
    for rel in ("docs/ops/a.md", "docs/ops/b.md", "Tools/x.py"):
        p = os.path.join(repo, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("ok\n")
    return repo, reg


def cmd_selftest(args):
    ok = [0]

    def chk(name, cond):
        ok[0] += 1 if cond else 0
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    tmp = tempfile.mkdtemp(prefix="scc-fx-")
    repo, reg = _fixture(tmp)
    res = audit(repo, register=reg, allowlist=["docs/ops/big.md"], declared_tools=1)
    chk("S1 基线 GREEN（存在性全过+工具计数吻合）", res["verdict"] == "SOP-COVERAGE-GREEN")
    chk("S2 三级覆盖汇总=1/1×3", all(s["covered"] == 1 and s["total"] == 1 for s in res["f4_level_summary"].values()))
    os.remove(os.path.join(repo, "docs", "ops", "b.md"))
    res = audit(repo, register=reg, allowlist=["docs/ops/big.md"], declared_tools=1)
    chk("S3 缺件→RED+F1 定位 L1/b1", res["verdict"] == "SOP-COVERAGE-RED" and res["f1_missing"] == ["L1-业务线/b1:docs/ops/b.md"])
    with open(os.path.join(repo, "docs", "ops", "b.md"), "w", encoding="utf-8") as f:
        f.write("x\n" * 250)
    res = audit(repo, register={"L1-业务线": {"b1": ["docs/ops/b.md"]}}, allowlist=[], declared_tools=1)
    chk("S4 超线未申报→RED 违例", res["verdict"] == "SOP-COVERAGE-RED" and len(res["f2_oversize_violation"]) == 1)
    res = audit(repo, register={"L1-业务线": {"b1": ["docs/ops/b.md"]}}, allowlist=["docs/ops/b.md"], declared_tools=1)
    chk("S5 超线已申报→DECLARED-PENDING 非违例", res["verdict"] == "SOP-COVERAGE-GREEN" and len(res["f2_declared_pending_1007"]) == 1)
    with open(os.path.join(repo, "docs", "ops", "b.md"), "w", encoding="utf-8") as f:
        f.write("ok\n")
    res = audit(repo, register=reg, allowlist=["docs/ops/big.md"], declared_tools=2)
    chk("S6 工具计数漂移→RED", res["verdict"] == "SOP-COVERAGE-RED" and res["f3_tools_drift"]["actual"] == 1)
    chk("S7 非md 件不入轻量闸（x.py 超线免测）", res["violations_total"] == 1)
    print("selftest %d/7 %s" % (ok[0], "PASS" if ok[0] == 7 else "FAIL"))
    return 0 if ok[0] == 7 else 1


def main():
    ap = argparse.ArgumentParser(description="SOP 三级覆盖对照机检（O-2026-0930-027 承接）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--repo", default="."); c.set_defaults(func=cmd_check)
    s = sub.add_parser("selftest"); s.set_defaults(func=cmd_selftest)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
