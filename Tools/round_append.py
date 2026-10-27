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
  J4 尾行守卫（T50·HQ-FEEDBACK 04/05 行合并判例）：既有文件尾字节非 \\n 时
     先补一个换行再追加——防新行并入既有尾行（合并=行账本可读性损·10-05 即修实证）
  J5 双时标 lint（T81·BC-P-45 写入侧升格·10-12 复发实锚）：目标行 HH:MM
     正则计数 ≥2 → 拒写 exit 4（与 qa_smoke no.8 读侧探针同判据镜像）——
     事故类=自身时标+二手 HH:MM 引用并线（22:5x 刻痕引 20:49:44 类）；
     改写法=二手时刻一律 x 形（20:49x / 23:2x）或去冒号。lint 自身异常
     =fail-closed 拒写。检查单→lint→脚本断言链闭环（10-13 CEO 令）。
  J6 空追加闸（T130·10-26 立）：--line 纯空白/零长或含换行 → 拒写 exit 5。
     事故类=台账 mtime 被刷新而内容零增（读者按 mtime 判续鲜会误读为已续鲜·
     D-20261026-03 实锚），本条把该路径在工具面关死。
  J7 回读断言（T130）：写入后回读末非空行，与入参不一致 → 结果带
     ALERT-READBACK-MISMATCH 告警（防编码/截断吞写；非拦截，exit 仍 0）。
  J8 mojibake 闸（T145·10-27 立）：目标行含真双子转义（UTF-8 字节被 Latin-1
     误读后再存为 UTF-8，如「ä¸­æ–‡」）或 U+FFFD → 拒写 exit 6。
     判据单源=Tools/ledger_encoding_guard.py（与 qa_smoke no.15 读侧同函数）。
     边界如实=合法排版符 §·×¥ 与西文人名重音符不触发（8 夹具钉死）。
"""
import argparse
import io
import os
import re
import sys
import tempfile

BUDGET_NOTE = "append 唯一路径：本工具（python utf-8）——shell Add-Content 禁用"

try:
    from ledger_encoding_guard import classify as _enc_classify
except Exception:  # 同目录导入（被当脚本直跑时 sys.path[0] 已是 Tools/）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ledger_encoding_guard import classify as _enc_classify

# 读侧 qa_smoke no.8（BC-P-45）heartbeat marker 同判据——写侧镜像（闸一致）
HHMM_RE = re.compile(r"\d{2}:\d{2}")
DUAL_TS_LIMIT = 2


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


def _last_nonempty(text):
    for l in reversed(text.splitlines()):
        if l.strip():
            return l
    return None


def _verify_readback(path, line):
    """J7：回读末非空行与入参比对。返回 (ok, message)。"""
    _, text = _read_strict(path)
    if text is None:
        return False, "ALERT-READBACK-MISMATCH: 写入后 strict decode 失败"
    got = _last_nonempty(text)
    if got != line:
        return False, "ALERT-READBACK-MISMATCH: 期望末行=%r 实读=%r（疑似编码/截断吞写）" % (line, got)
    return True, "readback=ok"


def append_line(path, line):
    """返回 (exit_code, message)。

    exit 0=APPENDED / 2=DUPLICATE / 3=CORRUPT / 4=DUAL-TS / 5=EMPTY-LINE
    """
    raw, text = _read_strict(path)
    if text is None:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            return 3, "CORRUPT-PREFLIGHT: 既有文件非 strict UTF-8（首坏字节位 %d）——拒追加，先按 T24 流程修复" % e.start
    # J6 空追加闸（T130）：纯空白/零长或含换行 → 拒写（触碰未写唯一合法面关死）
    if not line or not line.strip():
        return 5, "EMPTY-LINE-REJECT: --line 为空白/零长——空追加会刷新 mtime 而不增内容（J6·T130）"
    if "\n" in line or "\r" in line:
        return 5, "EMPTY-LINE-REJECT: --line 含换行符——一次只追加一行（J6·T130）"
    if text:
        if _last_nonempty(text) == line:
            return 2, "DUPLICATE-SKIP: --line 与末非空行一致（幂等护栏，零追加）"
    # J5 双时标 lint（T81·BC-P-45 写入侧升格）：HH:MM 计数 ≥2 → 拒写 fail-closed
    try:
        n_ts = len(HHMM_RE.findall(line))
    except Exception as e:  # lint 自身异常 = fail-closed 拒写（宁拒不写）
        return 4, "LINT-ERROR: 双时标 lint 自身异常——fail-closed 拒写（%s）" % e
    if n_ts >= DUAL_TS_LIMIT:
        return 4, ("DUAL-TIMESTAMP-REJECT: 目标行含 %d 处 HH:MM（≥%d）——二手时标引用面"
                   "（BC-P-45 升格·与 qa_smoke no.8 读侧同判据）·改写：二手时刻一律 x 形"
                   "（如 20:49x / 23:2x / 15:1x-22:3x）或去冒号后重试") % (n_ts, DUAL_TS_LIMIT)
    # J8 mojibake 闸（T145·判据单源=ledger_encoding_guard.classify）
    try:
        enc = _enc_classify(line)
        n_dbl, n_fffd = len(enc["utf8_double"]), enc["fffd"]
        recovered = enc["utf8_double"][0]["recovered"] if enc["utf8_double"] else ""
    except Exception as e:  # 闸自身异常 = fail-closed 拒写（宁拒不写）
        return 4, "LINT-ERROR: J8 mojibake 闸自身异常——fail-closed 拒写（%s）" % e
    if enc["status"] != "ENCODING-OK":
        hint = ("（该段应读作「%s」）" % recovered) if recovered else ""
        return 6, ("MOJIBAKE-REJECT: 目标行含编码损伤 utf8_double=%d fffd=%d——"
                   "疑似 UTF-8 被当 ANSI/Latin-1 往返（T145·BC-P-16 同族）。"
                   "正解=重打该段中文%s，或写入前先 strict UTF-8 校验"
                   % (n_dbl, n_fffd, hint))
    # J4 尾行守卫：既有尾字节非 \n 时先补换行，防新行并入既有尾行（T50 判例）
    tail_nl_fix = bool(text) and not raw.endswith(b"\n")
    with io.open(path, "a", encoding="utf-8", newline="") as f:
        if tail_nl_fix:
            f.write("\n")
        f.write(line + "\n")
    _, after = _read_strict(path)
    if after is None:
        return 3, "CORRUPT-POST: 写入后 strict decode 失败（异常态，即查）"
    msg = "APPENDED ok bytes_total=%d lines_total=%d" % (len(after.encode("utf-8")), len(after.splitlines()))
    if tail_nl_fix:
        msg += " tail_nl_fix=1（既有尾行缺换行已补——J4 守卫）"
    rb_ok, rb_msg = _verify_readback(path, line)  # J7 回读断言（非拦截·防吞写）
    if not rb_ok:
        msg = rb_msg + " | " + msg
    return 0, msg

# T95 note (2026-10-14): the J4/J5 write-side guards here cover python
# appends only; non-canonical shell writes (leading newline + no trailing
# newline, 14:0x case) are surfaced read-side by qa_smoke probe no.8
# third check (tail-12 empty-line sniff) - detectability contract.

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
        # S5 尾行守卫（J4）：既有尾字节无 \n → 补换行后追加，两行分立不合并
        p3 = os.path.join(td, "notail.log")
        with open(p3, "wb") as f:
            f.write("行A（尾无换行）".encode("utf-8"))
        c, m = append_line(p3, "行B（追加行）")
        txt3 = open(p3, "rb").read().decode("utf-8")
        run("S5 尾行守卫补换行", c == 0 and txt3 == "行A（尾无换行）\n行B（追加行）\n" and "tail_nl_fix=1" in m, m)
        # S6 正常尾（带 \n）→ 零补换行动作
        c, m = append_line(p3, "行C")
        txt4 = open(p3, "rb").read().decode("utf-8")
        run("S6 正常尾零动作", c == 0 and txt4 == "行A（尾无换行）\n行B（追加行）\n行C\n" and "tail_nl_fix" not in m, m)
        # S7 二手引用行拒写（J5·事故类正身：自身完整时标+二手 HH:MM 并线=2 处）
        before = open(p3, "rb").read()
        c, m = append_line(p3, "22:55 刻痕：第三方复活被杀（20:49:44 击发实锚）")
        run("S7 二手引用行拒写 exit4", c == 4 and "DUAL-TIMESTAMP" in m and open(p3, "rb").read() == before, m)
        # S8 融合行拒写（两行并一形态）
        c, m = append_line(p3, "22:43 排程轮行（22:50 轮尾行并入=融合形态）")
        run("S8 融合行拒写 exit4", c == 4 and open(p3, "rb").read() == before, m)
        # S9 单时标行过（自身时标唯一）
        c, m = append_line(p3, "22:55 刻痕：队列步毕·单时标验证行")
        run("S9 单时标行过", c == 0 and open(p3, "rb").read().endswith("22:55 刻痕：队列步毕·单时标验证行\n".encode("utf-8")), m)
        # S10 x 形引用过（二手时刻 x 形=纪律正写法·J5 放行面）
        c, m = append_line(p3, "23:0x 刻痕：复活实锚（20:49x 击发·22:3x 再执法）")
        run("S10 x形引用过", c == 0 and "23:0x" in open(p3, "rb").read().decode("utf-8"), m)
        # S11 空追加拒写（J6·T130）：零长不给写，文件字节零变化
        before = open(p3, "rb").read()
        c, m = append_line(p3, "")
        run("S11 空行拒写 exit5", c == 5 and "EMPTY-LINE-REJECT" in m and open(p3, "rb").read() == before, m)
        # S12 纯空白拒写（J6）：空格/制表符同样不得刷新 mtime
        c, m = append_line(p3, "   \t  ")
        run("S12 纯空白拒写 exit5", c == 5 and open(p3, "rb").read() == before, m)
        # S13 含换行拒写（J6）：一次一行不破
        c, m = append_line(p3, "行D\n行E")
        run("S13 换行拒写 exit5", c == 5 and open(p3, "rb").read() == before, m)
        # S14 回读断言正例/失配告警（J7）：断言函数对正误两态判对
        ok1, m1 = _verify_readback(p3, "23:0x 刻痕：复活实锚（20:49x 击发·22:3x 再执法）")
        ok2, m2 = _verify_readback(p3, "这行不在文件里")
        run("S14 回读断言双态", ok1 and not ok2 and "ALERT-READBACK-MISMATCH" in m2, (m1 + " / " + m2)[:120])
        # S15 真双子转义拒写（J8·T145）：UTF-8 字节被 Latin-1 误读再存 → exit 6 零污
        before = open(p3, "rb").read()
        c, m = append_line(p3, "损坏 " + "中文".encode("utf-8").decode("latin-1"))
        run("S15 双子转义拒写 exit6",
            c == 6 and "MOJIBAKE-REJECT" in m and open(p3, "rb").read() == before, m[:100])
        # S16 U+FFFD 拒写（J8）：替换符即损伤，不必等双子转义成形
        c, m = append_line(p3, "含替换符 中文\ufffd\ufffd正文")
        run("S16 替换符拒写 exit6", c == 6 and open(p3, "rb").read() == before, m[:100])
        # S17 合法排版符/西文重音不误拒（J8 边界）：§·×¥ 与 é ü 必过
        c, m = append_line(p3, "Ren\u00e9e M\u00fcller §二 · 门控 × 上限 ¥29.9")
        run("S17 合法拉丁符放行", c == 0 and "§二" in open(p3, "rb").read().decode("utf-8"), m[:100])

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
