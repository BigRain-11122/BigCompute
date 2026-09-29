#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""City3D 室内烘焙批预备性探针（O-20260929-029 机队并行令 @BigCompute 切片=室内烘焙承接）

只读探针：核验重构案 R2 硬依赖「室内四包」（AD-002/008/021/035·FluxVerse City3D 项目内）
资产在位性+关键室内件族计数+体量对账——为批卡 B-CITY3D-01（室内 AO+光照烘焙批）
提供烘焙管线预备性证据。零写入消费方仓（只读扫描）；报告落 state/。
判据来源：lowpoly3d-city-rebuild-plan.md §一（模块壳拼装·件名账实锚）+§四（@BigCompute=室内件烘焙批）。

用法：
  python Tools/city3d_bake_readiness_probe.py probe [--city-root DIR] [--out FILE]
  python Tools/city3d_bake_readiness_probe.py selftest
"""
import argparse
import sys
import tempfile
import time
from pathlib import Path

# 默认只读面：FluxVerse City3D 项目室内四包所在目录（只读引用·禁跨仓写）
DEFAULT_CITY_ROOT = Path(r"C:\Users\sjs20\Desktop\FluxGroup\gaming\FluxVerse\City3D\Assets\lowpoly\01_现代城市生活")

# 室内四包（重构案 §一 资产系统表 + §五.1 扩包进城批 A）
PACK_KEYS = ["AD-002", "AD-008", "AD-021", "AD-035"]

# 关键室内件族（重构案 §一 件名账实锚名·烘焙批直接对象面：
# 壳=门洞外墙/内墙分隔；光池=Ceiling_Panel_Light 自发光灯板；大空间=Base_Floor/Base_Wall）
# 注：AD-008 壳族件名账口径「Base_Buildings」在包内实名=SM_Bld_Base_* 系列（首轮实测命名差·非缺件）
CRITICAL_FAMILIES = {
    "AD-002": ["Base_Floor", "Base_Wall", "Wall_Door", "Ceiling"],
    "AD-008": ["SM_Bld_Base", "Wall_Door"],
    "AD-021": ["ExteriorWall_GroundFloor", "InteriorWall", "House_Door", "Stairs"],
    "AD-035": ["Ceiling_Panel_Light"],
}
EXT_TRACK = [".prefab", ".fbx", ".mat", ".png", ".asset", ".tga"]


def gb(nbytes: float) -> str:
    return f"{nbytes / (1024 ** 3):.2f}GB"


def find_pack_dir(root: Path, key: str):
    if not root.is_dir():
        return None
    for d in sorted(root.iterdir()):
        if d.is_dir() and d.name.startswith(key):
            return d
    return None


def scan_pack(pack_dir: Path):
    files = [p for p in pack_dir.rglob("*") if p.is_file()]
    by_ext = {}
    total = 0
    for p in files:
        total += p.stat().st_size
        by_ext[p.suffix.lower()] = by_ext.get(p.suffix.lower(), 0) + 1
    fam = {}
    for name in CRITICAL_FAMILIES.get(pack_dir.name.split("_")[0], []):
        fam[name] = sum(1 for p in files if p.suffix.lower() == ".prefab" and name.lower() in p.name.lower())
    return {"dir": pack_dir.name, "files": len(files), "bytes": total, "by_ext": by_ext, "families": fam}


def probe(root: Path):
    rows, missing_packs, missing_families = [], [], []
    for key in PACK_KEYS:
        d = find_pack_dir(root, key)
        if d is None:
            missing_packs.append(key)
            continue
        info = scan_pack(d)
        rows.append(info)
        for fname, n in info["families"].items():
            if n < 1:
                missing_families.append(f"{key}:{fname}")
    if missing_packs or not rows:
        verdict = "MISSING"
    elif missing_families:
        verdict = "PARTIAL"
    else:
        verdict = "ASSETS_READY_FOR_BAKE_SPIKE"
    return rows, missing_packs, missing_families, verdict


def render(rows, missing_packs, missing_families, verdict):
    out = ["City3D 室内烘焙批预备性探针（只读）", "=" * 46]
    for r in rows:
        out.append(f"[{r['dir']}] files={r['files']} size={gb(r['bytes'])}")
        exts = " ".join(f"{e}={r['by_ext'][e]}" for e in EXT_TRACK if e in r["by_ext"])
        out.append(f"  ext: {exts}")
        fams = " ".join(f"{k}={v}" for k, v in r["families"].items())
        out.append(f"  critical-families: {fams}")
    if missing_packs:
        out.append(f"MISSING-PACKS: {','.join(missing_packs)}")
    if missing_families:
        out.append(f"MISSING-FAMILIES: {','.join(missing_families)}")
    out.append(f"VERDICT={verdict}")
    return "\n".join(out)


def cmd_probe(args):
    root = Path(args.city_root) if args.city_root else DEFAULT_CITY_ROOT
    rows, mp, mf, verdict = probe(root)
    text = render(rows, mp, mf, verdict)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0 if verdict == "ASSETS_READY_FOR_BAKE_SPIKE" else 1


def make_fixture(base: Path, complete: bool = True):
    base.mkdir(parents=True, exist_ok=True)
    specs = {
        "AD-002_Scene ShopsPack": ["Base_Floor_01.prefab", "Base_Wall_A.prefab", "Wall_Door.prefab", "Ceiling.prefab"],
        "AD-008_Scene Nightclub": ["SM_Bld_Base_Floor_01.prefab", "Wall_Door_B.prefab"],
        "AD-021_Scene TownPack": ["ExteriorWall_GroundFloor.prefab", "InteriorWall_01.prefab",
                                  "House_Door_A.prefab", "Stairs_01.prefab"],
        "AD-035_Scene OfficePack": ["Ceiling_Panel_Light.prefab"],
    }
    for dname, files in specs.items():
        d = base / dname
        d.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(files):
            (d / f).write_bytes(b"x" * 100)
        (d / "tex.png").write_bytes(b"x" * 50)
    if not complete:
        # 抽走 AD-035 关键件族 → 判 PARTIAL
        for p in (base / "AD-035_Scene OfficePack").glob("Ceiling_Panel_Light.prefab"):
            p.unlink()


def cmd_selftest(_args):
    checks = []
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        fx = tdp / "full"
        make_fixture(fx, complete=True)
        rows, mp, mf, v = probe(fx)
        checks.append(("complete→READY", v == "ASSETS_READY_FOR_BAKE_SPIKE" and len(rows) == 4 and not mp and not mf))
        checks.append(("family-count", rows[2]["families"]["ExteriorWall_GroundFloor"] == 1
                       and rows[2]["families"]["InteriorWall"] == 1))
        fx2 = tdp / "partial"
        make_fixture(fx2, complete=False)
        _, _, mf2, v2 = probe(fx2)
        checks.append(("partial→PARTIAL", v2 == "PARTIAL" and "AD-035:Ceiling_Panel_Light" in mf2))
        _, mp3, _, v3 = probe(tdp / "empty")
        checks.append(("empty→MISSING", v3 == "MISSING" and set(mp3) == set(PACK_KEYS)))
    ok = all(c[1] for c in checks)
    for name, passed in checks:
        print(f"selftest {name}: {'PASS' if passed else 'FAIL'}")
    print(f"selftest {'PASS' if ok else 'FAIL'} ({len(checks)} checks)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="City3D interior-bake readiness probe (read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--city-root", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_probe)
    s = sub.add_parser("selftest")
    s.set_defaults(fn=cmd_selftest)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
