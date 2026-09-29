#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compliance_gate_check.py — 付费点×合规义务映射表机检门（M21·席7 附款硬门工程面）

法源链：C-20260927-01 席7 附款「A 档上线前未过表不得开单收款=硬门」｜U288 机检优先律（机器可验判据=唯一 PASS 依据）
锚位单元格式：✓<file>::<keyword>（表内人类可读+机器可验同源）
判定：✓ 锚=文件存在（根/docs/legal/docs/ops/Tools 四路解析）+关键词在件=PASS；任一失效=MISSING（门红·exit 1）
　　　⬜ =待定值槽（如实计数·不计失败）　—=不适用（零处理）
命令：check（默认·对 docs/ops/paypoint-compliance-map-v1.md 实跑）｜selftest（合成用例回归）
"""
import argparse
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIRS = [ROOT, ROOT / "docs" / "legal", ROOT / "docs" / "ops", ROOT / "Tools"]
DEFAULT_MAP = ROOT / "docs" / "ops" / "paypoint-compliance-map-v1.md"
ANCHOR_RE = re.compile(r"✓([\w\-./]+)::([^|＋⬜〔（〕]+)")
SLOT_RE = re.compile(r"⬜〔([^〕]+)〕")


def resolve(fname):
    for d in SEARCH_DIRS:
        p = d / fname
        if p.is_file():
            return p
    return None


def parse_map(map_path):
    """只解析映射表数据行（| 开头·表头/分隔行除外）——散文面的示例锚不入机检。"""
    anchors, slots, rows = [], [], 0
    for line in map_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        if line.startswith("| 付费点"):
            continue
        rows += 1
        anchors.extend((f, k.strip()) for f, k in ANCHOR_RE.findall(line))
        slots.extend(SLOT_RE.findall(line))
    return anchors, slots, rows


def run_check(map_path):
    anchors, slots, rows = parse_map(map_path)
    print(f"== 合规门机检（{map_path.name}）==")
    print(f"rows={rows}  anchors={len(anchors)}  slots={len(slots)}")
    missing = 0
    for fname, kw in anchors:
        p = resolve(fname)
        if p is None:
            print(f"MISSING_FILE  {fname}::{kw}")
            missing += 1
            continue
        if kw not in p.read_text(encoding="utf-8", errors="replace"):
            print(f"MISSING_KW    {fname}::{kw}")
            missing += 1
    for s in slots:
        print(f"SLOT_PENDING   {s}")
    verdict = "GATE_GREEN" if missing == 0 and len(slots) == 0 else (
        "GATE_GREEN_WITH_SLOTS" if missing == 0 else "GATE_RED")
    print(f"verdict={verdict}  pass={len(anchors) - missing}/{len(anchors)}  slots={len(slots)}")
    return 0 if missing == 0 else 1


def run_selftest():
    tmp = Path(sys.argv[0]).parent / "_selftest_gate_map.md"
    kw_absent = "零命中锚" + "词XYZ"  # 拆串防自指：确保该词不存在于本件源码
    tmp.write_text(
        "| r1 | ✓compliance_gate_check.py::ANCHOR_RE | ⬜〔合成槽〕 | — | — | — |\n"
        "| r2 | ✓no_such_file_xyz.md::关键词 | — | — | — | — |\n"
        f"| r3 | ✓compliance_gate_check.py::{kw_absent} | — | — | — | — |\n",
        encoding="utf-8")
    ok = True
    try:
        anchors, slots, rows = parse_map(tmp)
        assert len(anchors) == 3 and anchors[0] == ("compliance_gate_check.py", "ANCHOR_RE"), f"anchor parse {anchors}"
        assert rows == 3, f"row count {rows}"
        assert len(slots) == 1 and slots[0] == "合成槽", f"slot parse {slots}"
        results = []
        for fname, kw in anchors:
            p = resolve(fname)
            if p is None:
                results.append("MISSING_FILE")
            elif kw not in p.read_text(encoding="utf-8", errors="replace"):
                results.append("MISSING_KW")
            else:
                results.append("PASS")
        assert results == ["PASS", "MISSING_FILE", "MISSING_KW"], f"classify {results}"
        assert run_check(tmp) == 1, "red-gate exit"
        print("selftest: anchor_parse / slot_parse / classify_3way / red_gate_exit PASS")
    except AssertionError as e:
        print(f"selftest FAIL: {e}")
        ok = False
    finally:
        tmp.unlink(missing_ok=True)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="check", choices=["check", "selftest"])
    ap.add_argument("--map", default=str(DEFAULT_MAP))
    args = ap.parse_args()
    if args.command == "selftest":
        return run_selftest()
    return run_check(Path(args.map))


if __name__ == "__main__":
    sys.exit(main())
