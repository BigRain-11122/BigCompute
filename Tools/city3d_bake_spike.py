# -*- coding: utf-8 -*-
"""City3D 室内烘焙批 spike 脚手架（M25 机械链·R-20260929-city3d-interior-bake-lane §二）

labbench 隔离区零碰司仓（O-023 先例）：本工具只读引用 FluxVerse City3D 资产/配置，
生成最小 spike 工程到集团 labbench 隔离区（gitignored 区·非任何公司仓）。
实弹烘焙=run_bake.ps1（净窗窗格判据·J1 时长/J2 峰值指标窗须无他司产线活载）。

用法：
  python Tools/city3d_bake_spike.py scaffold [--dest DIR] [--source-root DIR] [--source-project DIR] [--packs AD-021,AD-035]
  python Tools/city3d_bake_spike.py selftest
"""
import argparse
import filecmp
import shutil
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
TEMPLATE_CS = HERE / "bake_spike_assets" / "BakeSpike.cs"

DEFAULT_SOURCE_ROOT = Path(r"C:\Users\sjs20\Desktop\FluxGroup\gaming\FluxVerse\City3D\Assets\lowpoly\01_现代城市生活")
DEFAULT_SOURCE_PROJECT = Path(r"C:\Users\sjs20\Desktop\FluxGroup\gaming\FluxVerse\City3D")
DEFAULT_DEST = Path(r"C:\Users\sjs20\Desktop\FluxGroup\.codely-cli\labbench\city3d-bake-spike\BakeSpike")
DEFAULT_PACKS = ["AD-021", "AD-035"]


def find_pack_dir(root: Path, key: str) -> Path:
    for d in sorted(root.iterdir()):
        if d.is_dir() and d.name.startswith(key):
            return d
    raise FileNotFoundError(f"pack dir not found for {key} under {root}")


def copy_tree(src: Path, dst: Path) -> int:
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return sum(1 for p in dst.rglob("*") if p.is_file())


def scaffold(dest: Path, source_root: Path, source_project: Path, packs) -> dict:
    proj_ver_src = source_project / "ProjectSettings" / "ProjectVersion.txt"
    manifest_src = source_project / "Packages" / "manifest.json"
    if not proj_ver_src.is_file():
        raise FileNotFoundError(f"ProjectVersion.txt missing: {proj_ver_src}")
    if not manifest_src.is_file():
        raise FileNotFoundError(f"manifest.json missing: {manifest_src}")
    if not TEMPLATE_CS.is_file():
        raise FileNotFoundError(f"BakeSpike.cs template missing: {TEMPLATE_CS}")

    (dest / "ProjectSettings").mkdir(parents=True, exist_ok=True)
    (dest / "Packages").mkdir(parents=True, exist_ok=True)
    (dest / "Assets" / "Editor").mkdir(parents=True, exist_ok=True)

    # 1) 版本/包清单与 City3D 逐字镜像（只读源）
    shutil.copyfile(proj_ver_src, dest / "ProjectSettings" / "ProjectVersion.txt")
    shutil.copyfile(manifest_src, dest / "Packages" / "manifest.json")
    # 2) 室内样板间所需资产包整体拷贝（.meta 全随包=GUID 保持·材质/预制引用链不断）
    copied = {}
    for key in packs:
        pack = find_pack_dir(source_root, key)
        n = copy_tree(pack, dest / "Assets" / pack.name)
        copied[pack.name] = n
    # 3) 批模式烘焙入口
    shutil.copyfile(TEMPLATE_CS, dest / "Assets" / "Editor" / "BakeSpike.cs")
    return {"dest": str(dest), "packs": copied,
            "editor_cs": 1, "project_version": (dest / "ProjectSettings" / "ProjectVersion.txt").read_text(encoding="utf-8").strip().splitlines()[0]}


def _make_fixture(tmp: Path):
    """合成夹具：假 City3D 源（版本文件+manifest）+假 AD-021/AD-035 包。"""
    src_proj = tmp / "City3D"
    (src_proj / "ProjectSettings").mkdir(parents=True)
    (src_proj / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.62t15\nm_EditorVersionWithRevision: 2022.3.62t15 (06f842492117)\nm_TuanjieEditorVersion: 1.10.3\n", encoding="utf-8")
    (src_proj / "Packages").mkdir(parents=True)
    (src_proj / "Packages" / "manifest.json").write_text(
        '{\n  "dependencies": {\n    "com.unity.render-pipelines.universal": "14.2.0-t1"\n  }\n}\n', encoding="utf-8")
    root = src_proj / "Assets" / "lowpoly" / "01_x"
    pack21 = root / "AD-021_Scene场景_城镇社区_TownPack"
    pack35 = root / "AD-035_Scene场景_现代办公室_OfficePack"
    for d in (pack21, pack35):
        d.mkdir(parents=True)
    for name in ("SM_Bld_House_ExteriorWall_GroundFloor_01.prefab", "SM_Bld_House_InteriorWall_A.prefab", "wall.mat"):
        (pack21 / name).write_text("fixture", encoding="utf-8")
    for name in ("Ceiling_Panel_Light_01.prefab", "panel.mat"):
        (pack35 / name).write_text("fixture", encoding="utf-8")
    return src_proj, root


def selftest() -> int:
    fails = []

    def check(name, cond):
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src_proj, root = _make_fixture(tmp)
        dest = tmp / "spike"

        # A1 版本镜像
        out = scaffold(dest, root, src_proj, ["AD-021", "AD-035"])
        check("A1 project_version mirrored", out["project_version"] == "m_EditorVersion: 2022.3.62t15")
        # A2 manifest 逐字等
        check("A2 manifest verbatim",
              filecmp.cmp(src_proj / "Packages" / "manifest.json", dest / "Packages" / "manifest.json", shallow=False))
        # A3 包内容全拷（AD-021=3 文件/AD-035=2 文件）
        check("A3 pack AD-021 files", out["packs"]["AD-021_Scene场景_城镇社区_TownPack"] == 3)
        check("A4 pack AD-035 files", out["packs"]["AD-035_Scene场景_现代办公室_OfficePack"] == 2)
        # A5 模板入口在位+关键符号
        cs = (dest / "Assets" / "Editor" / "BakeSpike.cs").read_text(encoding="utf-8")
        check("A5 entry cs copied", "namespace City3DBakeSpike" in cs and "public static void Run()" in cs)
        # A6 花括号平衡（编辑器编译前置卫生检）
        check("A6 cs braces balanced", cs.count("{") == cs.count("}"))
        # A9 光基准对齐锚（BC-P-19·City3D 官方光基准档三面在模板在位）
        check("A9 light baseline anchors",
              "shadowStrength = 0.8f" in cs and "Skybox/Procedural" in cs
              and "BakeFillLight" in cs and "city3d_official_ad022_v1" in cs)
        # A7 确定性：重跑 scaffold 后文件集合不变
        before = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
        scaffold(dest, root, src_proj, ["AD-021", "AD-035"])
        after = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
        check("A7 scaffold deterministic", before == after)
        # A8 无包外泄漏：顶层目录仅 ProjectSettings/Packages/Assets
        top = sorted(d.name for d in dest.iterdir())
        check("A8 top-level dirs only 3", top == ["Assets", "Packages", "ProjectSettings"])

    print(f"selftest: {9 - len(fails)}/9 checks, {'PASS' if not fails else 'FAIL'}")
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("scaffold")
    sp.add_argument("--dest", default=None)
    sp.add_argument("--source-root", default=None)
    sp.add_argument("--source-project", default=None)
    sp.add_argument("--packs", default=",".join(DEFAULT_PACKS))
    sub.add_parser("selftest")
    args = ap.parse_args()

    if args.cmd == "selftest":
        sys.exit(selftest())
    dest = Path(args.dest) if args.dest else DEFAULT_DEST
    source_root = Path(args.source_root) if args.source_root else DEFAULT_SOURCE_ROOT
    source_project = Path(args.source_project) if args.source_project else DEFAULT_SOURCE_PROJECT
    packs = [p.strip() for p in args.packs.split(",") if p.strip()]
    out = scaffold(dest, source_root, source_project, packs)
    print("scaffold ok:")
    for k, v in out.items():
        print(f"  {k}: {v}")
    print(f"  run_bake: powershell -File Tools/bake_spike_assets/run_bake.ps1 -Lightmapper ProgressiveCPU|ProgressiveGPU")


if __name__ == "__main__":
    main()
