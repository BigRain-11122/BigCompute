#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""round_append.py — 轮账本/心跳安全 append 微工具（BC-P-16 工程面·T24 防再发收口）

背景（T24 判例）：PowerShell Add-Content 默认 ANSI(GBK) 编码会把中文行写损
（rounds.log L70 实损实证·修复须 raw 备份+转码+strict 全验三步）。本件=python
utf-8 显式编码唯一写入路径——轮账本/心跳 append 一律走本件（runbook 速查在册）。

用法：
  python Tools/round_append.py append --file state/rounds.log --line "<本轮行>"
  python Tools/round_append.py selftest

判据（预注册）：
  J1 写入前后全文件 strict UTF-8 decode 全过（前置门=既有损行拒绝追加 exit 3）
  J2 幂等护栏：--line 与文件末非空行完全一致时拒绝追加 exit 2（防轮内双写）
  J3 目标不存在时创建（utf-8 无 BOM·LF 行尾）
"""
import argparse
import io
import os
import sys
import tempfile

BUDGET_NOTE = "append 唯一路径：本工具（python utf-8）——shell Add-Content 禁用"


def _read_strict(path):
    """返回 (raw_bytes, text_or_None)；既有损行=None。"""
    if not os.path.exists(path):
        return b"", ""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return raw, None


def append_line(path, line):
    """返回 (exit_code, message)。exit 0=APPENDED / 2=DUPLICATE / 3=CORRUPT。"""
    raw, text = _read_strict(path)
    if text is None:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            return 3, "CORRUPT-PREFLIGHT: 既有文件非 strict UTF-8（首坏字节位 %d）——拒追加，先按 T24 流程修复" % e.start
    if text:
        last = [l for l in text.splitlines() if l.strip()]
        if last and last[-1] == line:
            return 2, "DUPLICATE-SKIP: --line 与末非空行一致（幂等护栏，零追加）"
    with io.open(path, "a", encoding="utf-8", newline="") as f:
        f.write(line + "\n")
    _, after = _read_strict(path)
    if after is None:
        return 3, "CORRUPT-POST: 写入后 strict decode 失败（异常态，即查）"
    return 0, "APPENDED ok bytes_total=%d lines_total=%d" % (len(after.encode("utf-8")), len(after.splitlines()))


def _selftest():
    checks = []

    def run(name, ok, detail=""):
        checks.append((name, ok, detail))

    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.log")
        # S1 新建文件首 append（J3）+ strict 全过（J1）
        c, m = append_line(p, "2026-09-30 05:xx tokens: local=1 api=0 中文行验证")
        raw = open(p, "rb").read()
        run("S1 新建追加+无BOM+strict", c == 0 and not raw.startswith(b"\xef\xbb\xbf") and raw.decode("utf-8").endswith("\n"), m)
        # S4 追加不损既有行
        c, m = append_line(p, "第二行·保留验证")
        text = open(p, "rb").read().decode("utf-8")
        run("S4 既有行保全", c == 0 and "中文行验证" in text and text.count("\n") == 2, m)
        # S2 幂等护栏
        c, m = append_line(p, "第二行·保留验证")
        run("S2 幂等拒绝 exit2", c == 2, m)
        # S3 既有 GBK 损行前置门
        p2 = os.path.join(td, "bad.log")
        with open(p2, "wb") as f:
            f.write("正常行\n".encode("utf-8") + "中文损行".encode("gbk"))
        c, m = append_line(p2, "新行")
        run("S3 损行前置门 exit3", c == 3, m)

    n_pass = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(("PASS " if ok else "FAIL ") + name + ("  [" + detail + "]" if detail else ""))
    print("SELFTEST %s %d/%d" % ("PASS" if n_pass == len(checks) else "FAIL", n_pass, len(checks)))
    return 0 if n_pass == len(checks) else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="轮账本/心跳安全 append 微工具（utf-8 唯一路径）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append", help="追加一行（utf-8 显式编码）")
    a.add_argument("--file", required=True)
    a.add_argument("--line", required=True)
    sub.add_parser("selftest", help="自测（temp 夹具零污染）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        sys.exit(_selftest())
    code, msg = append_line(args.file, args.line)
    print(("APPEND " if code == 0 else "") + msg)
    if code == 0:
        print("NOTE: " + BUDGET_NOTE)
    sys.exit(code)


if __name__ == "__main__":
    main()
