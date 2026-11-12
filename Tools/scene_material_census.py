"""Scene -> material reference census (read-only).

Why: B-CITY3D-02 rendered 5/6 magenta frames. The static material census counted
51/51 materials on the built-in Standard shader, so "shader pipeline mismatch"
looked like the answer. It was not: the two closure slabs (FloorSlab/CeilSlab)
point at material guid 31321ba1... which exists nowhere in the project. Unity
draws a magenta fallback for a dangling material reference, and the offline census
could never see it because it only walked .mat assets, never the scene's references.

Magenta luma is 0.2126*255 + 0.0722*255 = 72.6, which is exactly the frame_05
"blank" reading - the top-down view was 100% magenta floor with zero variance.
One dangling reference, two symptoms.

This tool answers, before spending an editor launch:
  "does every material reference in this scene resolve to something on disk?"

Two kinds of "not under Assets" must not be merged (M199):
  - PACKAGE-ONLY: the guid lives in Library/PackageCache (a package asset, e.g.
    URP's Lit.mat). The reference resolves; the fix is a shader/pipeline change,
    never "re-create the missing asset".
  - DANGLING: nothing on disk anywhere. That is the magenta fallback case.
Reading the first as the second sends you off to build something that already
exists; reading the second as the first hides a real defect. So they are counted
separately, and CLEAN is decided by DANGLING only.

Usage:
  python Tools/scene_material_census.py run [--root <project dir>] [--json-out <path>]
  python Tools/scene_material_census.py selftest

Exit codes: 0 CLEAN / 2 UNRESOLVED references / 3 nothing scanned (census void)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime

BUILTIN_GUIDS = {
    "0000000000000000f000000000000000",  # Unity built-in resources (shaders, default material)
    "0000000000000000e000000000000000",  # built-in extra
}

SCENE_SPLIT = re.compile(r"^--- !u!(\d+) &(-?\d+)", re.M)
NAME_RE = re.compile(r"^  m_Name:\s*(.*)$", re.M)
MAT_BLOCK_RE = re.compile(r"m_Materials:\s*\n((?:[ \t]*-[ \t]*\{[^}]*\}[ \t]*\n)+)")
REF_RE = re.compile(r"fileID:\s*(-?\d+),\s*guid:\s*([0-9a-fA-F]{32})")
GUID_LINE_RE = re.compile(r"^guid:\s*([0-9a-fA-F]{32})\s*$", re.M)


def _read(path: str) -> str:
    with open(path, "rb") as fh:
        # 统一换行：CRLF 场景会让按行锚定的块正则整体落空（夹具首版即栽在这里）
        return fh.read().decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def build_guid_index(root: str) -> dict:
    """guid -> relative asset path, for every .meta under root/Assets."""
    assets = os.path.join(root, "Assets")
    index = {}
    for dirpath, _, names in os.walk(assets):
        for n in names:
            if not n.endswith(".meta"):
                continue
            fp = os.path.join(dirpath, n)
            try:
                m = GUID_LINE_RE.search(_read(fp))
            except OSError:
                continue
            if m:
                index[m.group(1).lower()] = os.path.relpath(fp[:-5], root)
    return index


def build_package_index(root: str):
    """guid -> relative path for every .meta under Library/PackageCache.

    Returns (index, base_dir_or_None). Missing cache is not an error: it means this
    machine has never resolved packages, so package-only refs cannot be told apart
    from dangling ones and the reading must say so instead of guessing.
    """
    base = os.path.join(root, "Library", "PackageCache")
    if not os.path.isdir(base):
        return {}, None
    index = {}
    for dirpath, _, names in os.walk(base):
        for n in names:
            if not n.endswith(".meta"):
                continue
            fp = os.path.join(dirpath, n)
            try:
                # guid is always in the head of a .meta; reading whole trees of
                # package metadata would cost more than the answer is worth
                with open(fp, "rb") as fh:
                    head = fh.read(4096).decode("utf-8", "replace")
            except OSError:
                continue
            m = GUID_LINE_RE.search(head.replace("\r\n", "\n"))
            if m:
                index[m.group(1).lower()] = os.path.relpath(fp[:-5], root).replace("\\", "/")
    return index, base


def parse_scene(path: str):
    """Yield (gameobject_name, [(fileID, guid)]) for every renderer material slot."""
    text = _read(path)
    parts = SCENE_SPLIT.split(text)
    # parts = [preamble, cls, anchor, body, cls, anchor, body, ...]
    docs = []
    for i in range(1, len(parts) - 2, 3):
        docs.append((parts[i], parts[i + 1], parts[i + 2]))

    names = {}
    for cls, anchor, body in docs:
        if cls == "1":
            m = NAME_RE.search(body)
            if m:
                names[anchor] = m.group(1).strip()

    # GameObject <- component link: component doc carries m_GameObject {fileID: N}
    owner = {}
    for cls, anchor, body in docs:
        m = re.search(r"m_GameObject:\s*\{?\s*fileID:\s*(-?\d+)", body)
        if m:
            owner[anchor] = m.group(1)

    for cls, anchor, body in docs:
        if "m_Materials:" not in body:
            continue
        block = MAT_BLOCK_RE.search(body)
        if not block:
            continue
        refs = []
        for line in block.group(1).splitlines():
            r = REF_RE.search(line)
            if not r:
                refs.append((None, None))  # empty slot {fileID: 0}
                continue
            refs.append((r.group(1), r.group(2).lower()))
        go = owner.get(anchor, "?")
        yield names.get(go, "GameObject#%s" % go), refs


def census(root: str) -> dict:
    guid_index = build_guid_index(root)
    pkg_index, pkg_base = build_package_index(root)
    assets = os.path.join(root, "Assets")
    scenes = []
    for dirpath, _, names in os.walk(assets):
        for n in names:
            if n.endswith(".unity"):
                scenes.append(os.path.join(dirpath, n))
    scenes.sort()

    unresolved = []
    package_only = []
    resolved = 0
    empty = 0
    scanned_scenes = 0
    for scene in scenes:
        scanned_scenes += 1
        rel = os.path.relpath(scene, root).replace("\\", "/")
        for obj, refs in parse_scene(scene):
            for fileid, guid in refs:
                if guid is None or fileid == "0":
                    empty += 1
                    continue
                if guid in BUILTIN_GUIDS:
                    # built-in default material: resolves inside the engine, not on disk
                    resolved += 1
                    continue
                target = guid_index.get(guid)
                if target:
                    resolved += 1
                    continue
                pkg = pkg_index.get(guid)
                if pkg:
                    # resolves, but to a package asset - different fix than dangling
                    package_only.append({"scene": rel, "object": obj, "guid": guid, "in": pkg})
                else:
                    unresolved.append({"scene": rel, "object": obj, "guid": guid})

    if scanned_scenes == 0:
        verdict = "SCENE-MATERIAL-NO-SCENE"
        rc = 3
    elif unresolved:
        verdict = "SCENE-MATERIAL-UNRESOLVED"
        rc = 2
    else:
        verdict = "SCENE-MATERIAL-CLEAN"
        rc = 0
    return {
        "verdict": verdict,
        "rc": rc,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "root": root,
        "scenes_scanned": scanned_scenes,
        "slots_resolved": resolved,
        "slots_empty": empty,
        "slots_package_only": len(package_only),
        "package_only": package_only,
        "package_only_total": len(package_only),
        "package_index": len(pkg_index),
        "package_cache_present": pkg_base is not None,
        "package_cache": None if pkg_base is None else os.path.relpath(pkg_base, root).replace("\\", "/"),
        "unresolved": unresolved,
        "unresolved_total": len(unresolved),
        "objects": sorted({u["scene"] + ":" + u["object"] for u in unresolved}),
        "package_objects": sorted({u["scene"] + ":" + u["object"] for u in package_only}),
    }


def render(payload: dict) -> str:
    lines = [
        "VERDICT=%s scenes=%d slots_resolved=%d slots_empty=%d package_only=%d unresolved=%d pkg_index=%s as_of=%s"
        % (
            payload["verdict"],
            payload["scenes_scanned"],
            payload["slots_resolved"],
            payload["slots_empty"],
            payload["slots_package_only"],
            payload["unresolved_total"],
            payload["package_index"] if payload["package_cache_present"] else "absent",
            payload["as_of"],
        )
    ]
    if not payload["package_cache_present"]:
        # Absence of the cache is not evidence of dangling refs; say so out loud.
        lines.append(
            "  NOTE package_cache=absent: package-level refs cannot be told apart from "
            "dangling ones on this machine (no Library/PackageCache)"
        )
    for u in payload["package_only"][:10]:
        lines.append("  PACKAGE-ONLY %s : %s -> guid %s (%s)" % (u["scene"], u["object"], u["guid"], u["in"]))
    if payload["package_only_total"] > 10:
        lines.append("  ... +%d more package-only" % (payload["package_only_total"] - 10))
    for u in payload["unresolved"][:20]:
        lines.append("  UNRESOLVED %s : %s -> guid %s" % (u["scene"], u["object"], u["guid"]))
    if payload["unresolved_total"] > 20:
        lines.append("  ... +%d more" % (payload["unresolved_total"] - 20))
    return "\n".join(lines)


# ---------------------------------------------------------------- selftest
_META = """fileFormatVersion: 2
guid: %s
Material:
  serializedVersion: 6
"""

_SCENE_TMPL = """%%YAML 1.1
--- !u!29 &1
OcclusionCullingSettings:
  m_Name: 
--- !u!1 &100
GameObject:
  m_Name: FloorSlab
--- !u!4 &101
Transform:
  m_GameObject: {fileID: 100}
--- !u!23 &102
MeshRenderer:
  m_GameObject: {fileID: 100}
  m_Materials:
%s--- !u!1 &200
GameObject:
  m_Name: Wall
--- !u!23 &202
MeshRenderer:
  m_GameObject: {fileID: 200}
  m_Materials:
  - {fileID: 2100000, guid: %s, type: 2}
"""


def _mk_project(material_guid: str, slot_guid: str, write_mat: bool, pkg_guids=()) -> str:
    root = tempfile.mkdtemp(prefix="scenecensus-")
    assets = os.path.join(root, "Assets")
    os.makedirs(assets, exist_ok=True)
    if write_mat:
        with open(os.path.join(assets, "Slab.mat"), "w", encoding="utf-8") as fh:
            fh.write("fileFormatVersion: 2\n")
        with open(os.path.join(assets, "Slab.mat.meta"), "w", encoding="utf-8") as fh:
            fh.write(_META % material_guid)
    if pkg_guids:
        pkg = os.path.join(root, "Library", "PackageCache",
                           "com.unity.render-pipelines.universal@14.2.0-t1", "Runtime", "Materials")
        os.makedirs(pkg, exist_ok=True)
        for i, g in enumerate(pkg_guids):
            fp = os.path.join(pkg, "PkgMat%d.mat" % i)
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write("fileFormatVersion: 2\n")
            with open(fp + ".meta", "w", encoding="utf-8") as fh:
                fh.write(_META % g)
    slots = "  - {fileID: 2100000, guid: %s, type: 2}\n" % slot_guid
    with open(os.path.join(assets, "Room.unity"), "w", encoding="utf-8") as fh:
        fh.write(_SCENE_TMPL % (slots, material_guid))
    return root


def selftest() -> int:
    ok = 0
    total = 0

    def check(name, cond):
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print("  FAIL %s" % name)

    # S1 dangling guid -> UNRESOLVED and names the object
    root = _mk_project("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", False)
    p = census(root)
    check("S1 unresolved detected", p["verdict"] == "SCENE-MATERIAL-UNRESOLVED" and p["rc"] == 2)
    check("S2 object named", p["objects"] == ["Assets/Room.unity:FloorSlab", "Assets/Room.unity:Wall"])

    # S3 guid present in .meta -> CLEAN
    root2 = _mk_project("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", True)
    p2 = census(root2)
    check("S3 resolved -> clean", p2["verdict"] == "SCENE-MATERIAL-CLEAN" and p2["rc"] == 0)
    check("S4 resolved counted", p2["slots_resolved"] == 2)

    # S5 empty slot {fileID: 0} is not a defect (两处槽位置空：夹具里两个渲染器同一 guid)
    root3 = _mk_project("cccccccccccccccccccccccccccccccc", "cccccccccccccccccccccccccccccccc", True)
    scene = os.path.join(root3, "Assets", "Room.unity")
    txt = _read(scene).replace(
        "  - {fileID: 2100000, guid: cccccccccccccccccccccccccccccccc, type: 2}",
        "  - {fileID: 0}",
    )
    with open(scene, "w", encoding="utf-8") as fh:
        fh.write(txt)
    p3 = census(root3)
    check("S5 empty slot ignored", p3["verdict"] == "SCENE-MATERIAL-CLEAN" and p3["slots_empty"] == 2)

    # S6 built-in zero guid resolves in engine, not on disk
    root4 = _mk_project("dddddddddddddddddddddddddddddddd", "0000000000000000f000000000000000", True)
    p4 = census(root4)
    check("S6 builtin guid not a defect", p4["verdict"] == "SCENE-MATERIAL-CLEAN")

    # S7 no scene at all -> census void, must not read as clean
    root5 = tempfile.mkdtemp(prefix="scenecensus-")
    os.makedirs(os.path.join(root5, "Assets"), exist_ok=True)
    p5 = census(root5)
    check("S7 no scene -> rc 3", p5["verdict"] == "SCENE-MATERIAL-NO-SCENE" and p5["rc"] == 3)

    # S8 both slabs pointing at the same dead guid -> both named separately
    root6 = _mk_project("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", False)
    scene6 = os.path.join(root6, "Assets", "Room.unity")
    txt6 = _read(scene6).replace("m_Name: Wall", "m_Name: CeilSlab")
    with open(scene6, "w", encoding="utf-8") as fh:
        fh.write(txt6)
    p6 = census(root6)
    check("S8 two objects named", p6["objects"] == ["Assets/Room.unity:CeilSlab", "Assets/Room.unity:FloorSlab"])

    # S9 determinism
    p6b = census(root6)
    check("S9 deterministic", p6b["unresolved"] == p6["unresolved"])

    # S10 guid only inside Library/PackageCache -> PACKAGE-ONLY, not dangling, still CLEAN
    root7 = _mk_project("11111111111111111111111111111111", "11111111111111111111111111111111", False,
                        pkg_guids=["11111111111111111111111111111111"])
    p7 = census(root7)
    check("S10 package-only not dangling",
          p7["verdict"] == "SCENE-MATERIAL-CLEAN" and p7["rc"] == 0
          and p7["unresolved_total"] == 0 and p7["slots_package_only"] == 2)
    check("S10b package path named",
          all(u["in"].startswith("Library/PackageCache/") for u in p7["package_only"]))

    # S11 cache present but this guid absent -> real DANGLING, package_only stays 0
    root8 = _mk_project("22222222222222222222222222222222", "22222222222222222222222222222222", False,
                        pkg_guids=["33333333333333333333333333333333"])
    p8 = census(root8)
    check("S11 dangling with cache present",
          p8["verdict"] == "SCENE-MATERIAL-UNRESOLVED" and p8["rc"] == 2
          and p8["package_only_total"] == 0 and p8["package_index"] == 1)

    # S12 no cache at all -> flag it instead of silently calling refs dangling
    root9 = _mk_project("44444444444444444444444444444444", "44444444444444444444444444444444", False)
    p9 = census(root9)
    check("S12 cache absent flagged", p9["package_cache_present"] is False and p9["package_index"] == 0)
    check("S12b absent cache still reports dangling", p9["verdict"] == "SCENE-MATERIAL-UNRESOLVED")
    check("S12c render prints the caveat", "package_cache=absent" in render(p9))

    # S13 built-in guid must not be counted as package-only
    root10 = _mk_project("55555555555555555555555555555555", "0000000000000000f000000000000000", True,
                         pkg_guids=["55555555555555555555555555555555"])
    p10 = census(root10)
    check("S13 builtin not package-only",
          p10["verdict"] == "SCENE-MATERIAL-CLEAN" and p10["slots_package_only"] == 0
          and p10["slots_resolved"] == 2)

    # S14 package-only detail is deterministic
    p7b = census(root7)
    check("S14 package-only deterministic", p7b["package_only"] == p7["package_only"])

    print("scene_material_census selftest %d/%d PASS" % (ok, total))
    return 0 if ok == total else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "selftest"])
    ap.add_argument("--root", default=None)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()

    root = args.root or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".codely-cli", "labbench",
                     "city3d-bake-spike", "BakeSpike")
    )
    payload = census(root)
    print(render(payload))
    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    return payload["rc"]


if __name__ == "__main__":
    sys.exit(main())