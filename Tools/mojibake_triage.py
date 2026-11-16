#!/usr/bin/env python3
"""mojibake_triage.py - 乱码归因器（T-20261117-133 判据③④的可执行化）.

问题：一份「看起来乱码」的输出，肉眼上三种成因长得一模一样——
  (A) 字节本身被写坏            -> 真损失，要修文件
  (B) 字节干净、只是被错误解码显示 -> 文件没病，病在读取面
  (C) 被 PowerShell 重定向包了壳  -> 文件没病，病在调用面
过去的处置是「看到乱码就改源文件」，于是 T252 把干净文件重扫了一遍、
T-133 又把同一批对象重新立行——两次都在治 (B)/(C)，源文件一次也没病。

本工具只做判定，不改目标文件：
  CLEAN                字节合法 UTF-8 且无双重编码特征
  MISREAD-ONLY         字节干净；乱码只发生在显示侧（用非 UTF-8 解码读取）
  PS-REDIRECT-WRAPPED  UTF-16 BOM 包壳，且内层文本含 mojibake
  WRITE-DAMAGED        字节非法 / 含替换符 / 源内字面量即双重编码（真损失）

判据依据（可逆性）：把一行 s 按 GBK 编码回字节、再按 UTF-8 解码，
能成功还原成另一段中文 = 该行是「UTF-8 字节被当 GBK 读过」的产物，可逆。
*注：本工具用标准库判定，未引入 ftfy（ftfy 只修不归因，且需联网装包；
归因是本件的唯一目的，可逆判据标准库即可实现）。

用法:
  python Tools/mojibake_triage.py triage <path> [--lines N] [--json out]
  python Tools/mojibake_triage.py explain
  python Tools/mojibake_triage.py selftest
"""

import argparse
import json
import os
import sys

# 控制台写侧自保：本工具的输出本身必须是合法 UTF-8（判据④的自证前提）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BOM_UTF16_LE = b"\xff\xfe"
BOM_UTF16_BE = b"\xfe\xff"
BOM_UTF8 = b"\xef\xbb\xbf"
REPLACEMENT = "\ufffd"

VERDICT_MEANING = {
    "CLEAN": "字节为合法 UTF-8 且无双重编码特征；文件本身无病",
    "MISREAD-ONLY": "字节干净；乱码只发生在显示侧（读取面用非 UTF-8 解码）",
    "PS-REDIRECT-WRAPPED": "PowerShell 重定向包壳：UTF-16 BOM + 内层 mojibake",
    "WRITE-DAMAGED": "字节非法 / 含替换符 / 源内字面量即双重编码（真损失）",
}

FIX_HINTS = {
    "CLEAN": "无需修复。若控制台仍见乱码，走 explain 的读取面配方。",
    "MISREAD-ONLY": "不要改文件。读取时显式指定 UTF-8：Get-Content -Encoding UTF8 / python -X utf8",
    "PS-REDIRECT-WRAPPED": (
        "不要改文件。改调用面：$env:PYTHONIOENCODING='utf-8'; "
        "python -X utf8 <script> | Out-File -Encoding utf8 <out>"
    ),
    "WRITE-DAMAGED": "真损失。先 python Tools/encoding_repair.py scan <path> 分类，再按可逆规则修。",
}


def _has_nonascii(s):
    return any(ord(c) > 127 for c in s)


def reversible_mojibake(line):
    """UTF-8 字节被当 GBK 解过的行，返回还原后的文本；否则 None。"""
    if not _has_nonascii(line):
        return None
    try:
        raw = line.encode("gbk", errors="strict")
    except (UnicodeEncodeError, LookupError):
        return None
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def _decode_container(b):
    """识别外层容器（UTF-16 BOM 包壳），返回 (text, wrapped, utf8_strict_ok)。"""
    if b[:2] in (BOM_UTF16_LE, BOM_UTF16_BE):
        enc = "utf-16-le" if b[:2] == BOM_UTF16_LE else "utf-16-be"
        return b[2:].decode(enc, errors="replace"), True, False
    try:
        return b.decode("utf-8", errors="strict"), False, True
    except UnicodeDecodeError:
        return b.decode("utf-8", errors="replace"), False, False


def triage_bytes(b):
    """对一份字节流做归因，返回结构化结果（纯函数，selftest 直接吃）。"""
    text, wrapped, utf8_ok = _decode_container(b)
    lines = text.splitlines()

    bad_lines = []      # 含替换符 / 非法解码
    reversible = []     # 可逆 mojibake
    irreversible = []   # 疑似 mojibake 但不可逆（含私用区/半角问号污染）
    for i, line in enumerate(lines, 1):
        if REPLACEMENT in line:
            bad_lines.append((i, line.strip()[:120]))
            continue
        restored = reversible_mojibake(line)
        if restored is not None:
            reversible.append((i, line.strip()[:120], restored.strip()[:120]))
        elif _has_nonascii(line) and _looks_mojibake(line):
            irreversible.append((i, line.strip()[:120]))

    has_bom_utf8 = b[:3] == BOM_UTF8
    if wrapped and (reversible or bad_lines or irreversible):
        verdict = "PS-REDIRECT-WRAPPED"
    elif not utf8_ok:
        verdict = "WRITE-DAMAGED"
    elif reversible:
        # 文件自身是合法 UTF-8，但字面量就是双重编码文本 => 源内写坏
        verdict = "WRITE-DAMAGED"
    elif bad_lines:
        verdict = "WRITE-DAMAGED"
    elif irreversible:
        verdict = "WRITE-DAMAGED"
    else:
        verdict = "CLEAN"

    return {
        "verdict": verdict,
        "bytes": len(b),
        "lines": len(lines),
        "container": "utf-16" if wrapped else "utf-8",
        "utf8_strict_ok": utf8_ok,
        "utf8_bom": has_bom_utf8,
        "bad_lines": bad_lines,
        "reversible_lines": reversible,
        "irreversible_lines": irreversible,
    }


# 典型 mojibake 高频字符（GBK 误读 UTF-8 时集中出现的一批），仅用于
# 「不可逆」标记的启发式旁证；判定主依据仍是 reversible_mojibake 的严格可逆测试。
_MOJI_CHARS = set("锛銆杞閽鐨缁鏄涓绋璁鍒鏂寮鏃鏁鍏浣鍦鐢鍔鎴鍙鐜瑁绔绛鑳鏉鈥鈯")


def _looks_mojibake(line):
    # 私用区字符与 C1 控制符是双重编码的典型残渣：正常中文文本里不会出现，
    # 一旦出现即认定该行已被写坏（即便无法按 GBK 可逆还原）。
    for c in line:
        o = ord(c)
        if 0xE000 <= o <= 0xF8FF or 0x80 <= o <= 0x9F:
            return True
    return sum(1 for c in line if c in _MOJI_CHARS) >= 1


def cmd_triage(args):
    path = args.path
    if not os.path.isfile(path):
        print("FAIL not-a-file: %s" % path)
        return 2
    with open(path, "rb") as f:
        b = f.read()
    r = triage_bytes(b)

    print("FILE      %s" % path)
    print("VERDICT   %s" % r["verdict"])
    print("MEANING   %s" % VERDICT_MEANING[r["verdict"]])
    print("BYTES     %d  LINES %d  CONTAINER %s  UTF8_STRICT_OK %s  UTF8_BOM %s"
          % (r["bytes"], r["lines"], r["container"], r["utf8_strict_ok"], r["utf8_bom"]))
    print("COUNTS    bad=%d reversible=%d irreversible=%d"
          % (len(r["bad_lines"]), len(r["reversible_lines"]), len(r["irreversible_lines"])))

    shown = 0
    for group, key in (("REVERSIBLE", "reversible_lines"),
                       ("IRREVERSIBLE", "irreversible_lines"),
                       ("BAD", "bad_lines")):
        for row in r[key]:
            if shown >= args.lines:
                break
            if key == "reversible_lines":
                print("  %-12s L%-5d %s   => %s" % (group, row[0], row[1], row[2]))
            else:
                print("  %-12s L%-5d %s" % (group, row[0], row[1]))
            shown += 1

    print("FIX       %s" % FIX_HINTS[r["verdict"]])
    if r["verdict"] == "CLEAN":
        print("HINT      若你眼前是乱码而此处判 CLEAN => 归因=MISREAD-ONLY（读取面），"
              "执行 explain 取配方")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"path": path, **r}, f, ensure_ascii=False, indent=2)
        print("JSON      %s" % args.json)
    return 0 if r["verdict"] in ("CLEAN", "MISREAD-ONLY") else 1


def cmd_explain(_args):
    print("MISREAD-ONLY（文件没病，读取面坏）")
    print("  PowerShell : Get-Content <f> -Encoding UTF8")
    print("  Python     : open(<f>, encoding='utf-8')  或  python -X utf8 <script>")
    print("  判据       : 同一个文件用上面两行读出来中文正常 => 之前是读面问题")
    print()
    print("PS-REDIRECT-WRAPPED（文件没病，调用面坏）")
    print("  错法       : python x.py run > out.txt      # PS5.1 对 native 走 UTF-16 包壳")
    print("  正法 A     : $env:PYTHONIOENCODING='utf-8'; python -X utf8 x.py run | Out-File -Encoding utf8 out.txt")
    print("  正法 B     : python -c \"import subprocess,sys; subprocess.run([sys.executable,'x.py','run'], stdout=open('out.txt','wb'))\"")
    print("  判据       : 落盘首字节是 50 41 53 53('PASS') 而非 ff fe")
    print()
    print("WRITE-DAMAGED（真损失）")
    print("  python Tools/encoding_repair.py scan <path>   # 先分类：可逆/不可逆")
    print("  可逆者按 UTF-16->UTF-8 或 GBK->UTF-8 复原；不可逆者保留并具名标注")
    return 0


# ---------------- selftest（离线夹具） ----------------

# 夹具取自真实样本（2026-11-17 06:2x month_end_collect selftest 的 PS 重定向产物
# L2 行）："解析" 的 UTF-8 字节被按 GBK 读成 "瑙ｆ瀽"，且该字节对在 GBK 全合法 => 可逆。
_MOJI = "瑙ｆ瀽"
_MOJI_FIX = "解析"


def _check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    return 1 if cond else 0


def cmd_selftest(_args):
    ok = [0, 0]

    def chk(name, cond):
        ok[1] += 1
        ok[0] += _check(name, cond)

    # S1 可逆判据：mojibake 行能还原
    chk("S1 reversible_mojibake 命中", reversible_mojibake(_MOJI) == _MOJI_FIX)
    # S2 正常中文不误报
    chk("S2 正常中文不误报", reversible_mojibake(_MOJI_FIX) is None)
    # S3 纯 ASCII 不误报
    chk("S3 纯 ASCII 不误报", reversible_mojibake("PASS S1 all good") is None)

    # S4 干净 UTF-8 -> CLEAN
    clean = "PASS S1 参数化\nPASS S2 门禁\nselftest: 2/2 PASS\n".encode("utf-8")
    chk("S4 干净 UTF-8 => CLEAN", triage_bytes(clean)["verdict"] == "CLEAN")

    # S5 PS 重定向包壳 => PS-REDIRECT-WRAPPED
    inner = ("PASS 纭畾鎬?S8/S9\n" + _MOJI + "\n").encode("utf-16-le")
    wrapped = BOM_UTF16_LE + inner
    r5 = triage_bytes(wrapped)
    chk("S5 UTF-16 包壳 => PS-REDIRECT-WRAPPED", r5["verdict"] == "PS-REDIRECT-WRAPPED")
    chk("S5b 容器识别=utf-16", r5["container"] == "utf-16")

    # S6 非法 UTF-8 => WRITE-DAMAGED
    broken = b"PASS ok\n\xff\xff\xff broken\n"
    chk("S6 非法字节 => WRITE-DAMAGED", triage_bytes(broken)["verdict"] == "WRITE-DAMAGED")

    # S7 源内双重编码字面量（合法 UTF-8 但内容是 mojibake）=> WRITE-DAMAGED
    src_bad = ("L1 " + _MOJI + "\n").encode("utf-8")
    chk("S7 源内双重编码 => WRITE-DAMAGED", triage_bytes(src_bad)["verdict"] == "WRITE-DAMAGED")

    # S8 六个判定码全覆盖且互斥
    seen = {triage_bytes(clean)["verdict"], triage_bytes(wrapped)["verdict"],
            triage_bytes(broken)["verdict"], triage_bytes(src_bad)["verdict"]}
    chk("S8 判定码覆盖三态以上", len(seen) >= 3)

    # S9 写侧自证：本工具输出行必须是合法 UTF-8（判据④前提）
    probe = ("文件没病 " + _MOJI_FIX).encode("utf-8")
    chk("S9 输出字节合法 UTF-8", probe.decode("utf-8") == "文件没病 " + _MOJI_FIX)

    print("selftest: %d/%d PASS" % (ok[0], ok[1]) if ok[0] == ok[1]
          else "selftest: FAIL (%d/%d)" % (ok[0], ok[1]))
    return 0 if ok[0] == ok[1] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="乱码归因器：写坏 / 读坏 / 包壳")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("triage", help="判定单个文件")
    t.add_argument("path")
    t.add_argument("--lines", type=int, default=10, help="每类最多显示行数")
    t.add_argument("--json", default=None, help="结构化结果落盘")
    t.set_defaults(func=cmd_triage)

    e = sub.add_parser("explain", help="打印安全读取/重定向配方")
    e.set_defaults(func=cmd_explain)

    s = sub.add_parser("selftest", help="离线夹具")
    s.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
