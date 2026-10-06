#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger_tail_fix.py — 台账尾行修复器（BC-P-46 批活转化·tech T52）

qa_smoke 第 8 探针（BC-P-45·T51）只读检出后的机械修复面：
  --dry      只读扫描七件 git 追踪台账，列「尾字节缺换行」文件（默认）
  --apply    对缺尾换行件二进制追加一个换行字节（纯 append·零内容改写）
  --selftest 合成夹具自测

判据（提案三问冻结·BC-P-46）：
  J1 修复面仅限尾换行字节：'ab' 模式 append b"\\n"，永不改写既有内容
  J2 幂等：已有尾换行零动作（字节级 no-op）
  J3 空文件不判损（探针 EMPTY 面非本件职责）
  J4 末行合并嗅探（行标 ≥2）只报 MANUAL-REVIEW 不自动改（内容感知修复越权）
  J5 尾行解码损（GBK 损行类）报 DECODE-WARN，仍可安全补尾字节
退出码：0=干净/修复完成；1=--dry 检出缺尾件；2=用法错误。
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 七件清单与行标 regex 镜像 qa_smoke.TAIL_PROBE_FILES（T51）
SEVEN_FILES = (
    ("state/rounds.log", r"\d{4}-\d{2}-\d{2}"),
    ("state/heartbeat.txt", r"\d{2}:\d{2}"),
    ("HQ-FEEDBACK.md", "日清[:：]"),
    ("state/proposals.md", r"\| BC-P-\d+"),
    ("state/queue/main.md", r"\| M\d+"),
    ("state/queue/tech.md", r"\| T\d+"),
    ("state/queue/explore.md", r"\| E\d+"),
)


def _path(base, rel):
    return os.path.join(base, *rel.split("/"))


def scan(base, files):
    """返回 [(rel, status, note)]：status in {ok, damaged, empty, missing}。"""
    out = []
    for rel, marker in files:
        path = _path(base, rel)
        if not os.path.exists(path):
            out.append((rel, "missing", "file not found"))
            continue
        with open(path, "rb") as f:
            raw = f.read()
        if not raw:
            out.append((rel, "empty", "zero bytes"))
            continue
        status = "ok" if raw.endswith(b"\n") else "damaged"
        note = ("tail newline present" if status == "ok"
                else "trailing newline missing")
        lines = [ln for ln in raw.split(b"\n") if ln.strip()]
        if lines:  # 嗅探注记只报不改（J4/J5）
            last = lines[-1]
            try:
                text = last.decode("utf-8", errors="strict")
            except UnicodeDecodeError as e:
                note += " | DECODE-WARN last line utf-8 fail at byte %d" % e.start
            else:
                if len(re.findall(marker, text)) >= 2:
                    note += (" | MANUAL-REVIEW %d markers in last line "
                             "(merged rows?)" % len(re.findall(marker, text)))
        out.append((rel, status, note))
    return out


def apply_fix(base, rel):
    """J1/J2：二进制纯 append 尾换行。返回追加字节数（0=幂等零动作）。"""
    path = _path(base, rel)
    with open(path, "rb") as f:
        raw = f.read()
    if not raw or raw.endswith(b"\n"):
        return 0
    with open(path, "ab") as f:
        f.write(b"\n")
    return 1


def run_dry(base, files):
    rows = scan(base, files)
    for rel, status, note in rows:
        print("%s: %s (%s)" % (rel, status.upper(), note))
    n = sum(1 for _, s, _ in rows if s == "damaged")
    print("TAIL-DRY damaged=%d/%d" % (n, len(rows)))
    return 1 if n else 0


def run_apply(base, files):
    rows = scan(base, files)
    fixed = 0
    for rel, status, note in rows:
        if status == "damaged":
            n = apply_fix(base, rel)
            fixed += n
            print("FIXED %s +%dB (binary append only)" % (rel, n))
        elif status == "empty":
            print("SKIP %s (empty, not fixer scope)" % rel)
        if "MANUAL-REVIEW" in note or "DECODE-WARN" in note:
            print("WARN %s: %s" % (rel, note))
    rows2 = scan(base, files)
    n2 = sum(1 for _, s, _ in rows2 if s == "damaged")
    print("TAIL-APPLY fixed=%d residual_damaged=%d" % (fixed, n2))
    return 0 if n2 == 0 else 1


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append(ok)
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           (" | " + detail) if detail and not ok else ""))

    tmp = tempfile.mkdtemp(prefix="tailfix-t52-")
    files = (("a.md", r"\| M\d+"),)
    fa = os.path.join(tmp, "a.md")
    with open(fa, "wb") as f:
        f.write(b"| M1 done row")  # S1 缺尾
    before = open(fa, "rb").read()
    rows = scan(tmp, files)
    run("S1 缺尾判损", rows[0][1] == "damaged", repr(rows))
    n = apply_fix(tmp, "a.md")
    after = open(fa, "rb").read()
    run("S1 修复+1B 且前缀零改", n == 1 and after == before + b"\n",
        "n=%d after=%r" % (n, after))
    run("S1 复扫转净", scan(tmp, files)[0][1] == "ok")
    run("S4 幂等双跑零动作", apply_fix(tmp, "a.md") == 0)

    fb = os.path.join(tmp, "b.md")
    payload = b"| M2 ok tail\n"
    with open(fb, "wb") as f:
        f.write(payload)
    run("S2 已有尾零动作", apply_fix(tmp, "b.md") == 0
        and open(fb, "rb").read() == payload)

    fc = os.path.join(tmp, "c.md")
    open(fc, "wb").close()
    rows = scan(tmp, (("c.md", r"\| M\d+"),))
    run("S3 空文件不判损", rows[0][1] == "empty" and apply_fix(tmp, "c.md") == 0)

    fd = os.path.join(tmp, "d.md")
    gbk = b"| M3 \xd0\xd0"  # GBK 尾字节（utf-8 不可解码）且缺尾换行
    with open(fd, "wb") as f:
        f.write(gbk)
    n = apply_fix(tmp, "d.md")
    run("S5 解码损尾仍可安全补尾", n == 1 and open(fd, "rb").read() == gbk + b"\n")
    rows = scan(tmp, (("d.md", r"\| M\d+"),))
    run("S5 DECODE-WARN 报面在位", "DECODE-WARN" in rows[0][2], rows[0][2])

    fe = os.path.join(tmp, "e.md")
    merged = b"| M4 x | M5 y\n"  # 末行两行标=疑似合并·尾换行正常
    with open(fe, "wb") as f:
        f.write(merged)
    rows = scan(tmp, (("e.md", r"\| M\d+"),))
    run("S6 合并行嗅探只报不改", rows[0][1] == "ok"
        and "MANUAL-REVIEW" in rows[0][2]
        and apply_fix(tmp, "e.md") == 0
        and open(fe, "rb").read() == merged, repr(rows))

    ok = all(checks)
    print("SELFTEST %d/%d PASS" % (sum(checks), len(checks)))
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        sys.exit(_selftest())
    if "--apply" in args:
        sys.exit(run_apply(ROOT, SEVEN_FILES))
    if args and args[0] != "--dry":
        print("usage: ledger_tail_fix.py [--dry|--apply|--selftest]")
        sys.exit(2)
    sys.exit(run_dry(ROOT, SEVEN_FILES))


if __name__ == "__main__":
    main()
