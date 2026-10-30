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
  J5 融合行 lint（T81 立·T172 换装）：判据单源=Tools/ledger_markers.py
     的组合 marker（与 qa_smoke no.8 读侧同一 regex，禁复制字面）——
     行首锚 ∨ 时刻+行首词（轮中刻痕/轮尾/os-loop/…）计数 ≥2 → 拒写 exit 4。
     事故类=两行并一（自身时标+第二个行头）；
     改写：真融合行拆成两行分写。纯「二手时刻引用」（如引 orders 的
     HH:MM 证据时刻）在换装后放行——读侧早已如此判，写侧此前用「任意
     HH:MM ≥2」会拒写读侧认可的行，义务不对等（T172 立行实证）。
     lint 自身异常=fail-closed 拒写。检查单→lint→脚本断言链闭环（10-13 CEO 令）。
  J6 空追加闸（T130·10-26 立）：--line 纯空白/零长或含换行 → 拒写 exit 5。
     事故类=台账 mtime 被刷新而内容零增（读者按 mtime 判续鲜会误读为已续鲜·
     D-20261026-03 实锚），本条把该路径在工具面关死。
  J7 回读断言（T130）：写入后回读末非空行，与入参不一致 → 结果带
     ALERT-READBACK-MISMATCH 告警（防编码/截断吞写；非拦截，exit 仍 0）。
  J8 mojibake 闸（T145·10-27 立）：目标行含真双子转义（UTF-8 字节被 Latin-1
     误读后再存为 UTF-8，如「ä¸­æ–‡」）或 U+FFFD → 拒写 exit 6。
     判据单源=Tools/ledger_encoding_guard.py（与 qa_smoke no.15 读侧同函数）。
     边界如实=合法排版符 §·×¥ 与西文人名重音符不触发（8 夹具钉死）。
  J9 CJK→? 闸（T151·10-28 立·J8 盲区补口）：shell stdin / --line 经 GBK 控制台
     往返时中文逐字变 ASCII 「?」，结果整行是合法 UTF-8 纯 ASCII——J8（查双子
     转义/U+FFFD）对此全盲，三次实锚（T24/T145 族·第三例=T150/T151 队列行）。
     判据两条：①整行 non-ASCII=0 且含「?」→ 疑似整行 CJK 破坏（正身形态）；
     ②任意处「??」连续 ≥2 → 破坏残留（混排行面）。命中 → 拒写 exit 7。
     逃生门=--allow-ascii-qmark（真英文问句等合法 ASCII 行，显式声明放行）。
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

# T172：J5 判据单源=ledger_markers（与 qa_smoke no.8 读侧同一 regex·禁复制字面）
try:
    from ledger_markers import count_markers, count_raw_stamps
except Exception:  # 同目录导入兜底
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ledger_markers import count_markers, count_raw_stamps

DUAL_TS_LIMIT = 2
# J9（T151）：CJK 被 GBK 控制台吞成 ASCII 问号的破坏形态
QMARK_RUN_RE = re.compile(r"\?{2,}")


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


def append_line(path, line, allow_ascii_qmark=False):
    """返回 (exit_code, message)。

    exit 0=APPENDED / 2=DUPLICATE / 3=CORRUPT / 4=DUAL-TS / 5=EMPTY-LINE
    / 6=MOJIBAKE / 7=QMARK-CORRUPT
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
    # J5 融合行 lint（T81 立·T172 换装）：组合 marker 计数 ≥2 → 拒写 fail-closed
    n_mark = count_markers(line)
    if n_mark is None:  # lint 自身异常 = fail-closed 拒写（宁拒不写）
        return 4, "LINT-ERROR: 融合行 lint 自身异常——fail-closed 拒写"
    if n_mark >= DUAL_TS_LIMIT:
        n_raw = count_raw_stamps(line) or 0
        return 4, ("FUSED-ROW-REJECT: 目标行含 %d 处行头 marker（≥%d·裸 HH:MM %d 处）——"
                   "两行并一形态（与 qa_smoke no.8 读侧同判据·T172 换装）·正解=拆成两行分写；"
                   "纯二手时刻引用（引他人证据时刻）不在此列，可直接重试"
                   % (n_mark, DUAL_TS_LIMIT, n_raw))
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
    # J9 CJK→? 闸（T151）：纯 ASCII 却带问号 / 连续问号残留 = 疑似中文被 GBK 控制台吞掉
    if not allow_ascii_qmark:
        try:
            n_nonascii = sum(1 for ch in line if ord(ch) > 0x7F)
            n_run = len(QMARK_RUN_RE.findall(line))
        except Exception as e:  # 闸自身异常 = fail-closed 拒写
            return 4, "LINT-ERROR: J9 CJK→? 闸自身异常——fail-closed 拒写（%s）" % e
        if n_nonascii == 0 and "?" in line:
            return 7, ("QMARK-CORRUPT-REJECT: 整行 non-ASCII=0 却含「?」——中文疑被 GBK "
                       "控制台逐字吞成问号（T151·J8 盲区正身）。正解=重打该行中文"
                       "（写入走 python utf-8，勿经 shell stdin 传中文），或真为英文问句时"
                       "显式加 --allow-ascii-qmark")
        if n_run:
            return 7, ("QMARK-CORRUPT-REJECT: 行内「??」连续问号 %d 处——破坏残留形态"
                       "（T151 判据②·混排行面）。重打该段中文或 --allow-ascii-qmark 放行"
                       % n_run)
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
        # S7 事故类融合行拒写（J5·T172 换装后仍拒：自身时标+轮尾行头=两处 marker）
        before = open(p3, "rb").read()
        c, m = append_line(p3, "22:55 刻痕：第三方复活被杀 20:49 轮尾行并入")
        run("S7 事故类融合行拒写 exit4", c == 4 and "FUSED-ROW" in m and open(p3, "rb").read() == before, m)
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
        # S18 整行 CJK→? 破坏拒写（J9·T151 正身：T150/T151 队列行形态）
        before = open(p3, "rb").read()
        c, m = append_line(p3, "01:5x ??? T151 ????????? T149 ?????????")
        run("S18 整行问号破坏拒写 exit7",
            c == 7 and "QMARK-CORRUPT" in m and open(p3, "rb").read() == before, m[:100])
        # S19 混排行「??」残留拒写（J9 判据②：有中文但夹问号串）
        c, m = append_line(p3, "T150 复评件：口径??未闭合，待补")
        run("S19 混排问号串拒写 exit7", c == 7 and open(p3, "rb").read() == before, m[:100])
        # S20 逃生门放行（--allow-ascii-qmark：真英文问句）
        c, m = append_line(p3, "2026-10-28 round done: is the lane 8090 dark?", allow_ascii_qmark=True)
        run("S20 逃生门放行", c == 0 and open(p3, "rb").read().decode("utf-8").endswith("dark?\n"), m[:100])
        # S21 纯 ASCII 无问号不误拒（J9 零假阳性面：英文/数字行照常写）
        c, m = append_line(p3, "2026-10-28 os-loop: round done exit=0")
        run("S21 纯ASCII无问号放行", c == 0 and "exit=0" in open(p3, "rb").read().decode("utf-8"), m[:100])
        # S22 二手时刻引用放行（J5·T172 义务对等面：读侧认的行写侧不得拒）
        c, m = append_line(p3, "22:56 刻痕：第三方复活被杀（20:49:44 击发实锚·引他人证据时刻）")
        run("S22 二手引用行放行", c == 0 and "击发实锚" in open(p3, "rb").read().decode("utf-8"), m[:120])

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
    a.add_argument("--allow-ascii-qmark", action="store_true",
                   help="放行含问号的纯 ASCII 行（J9 逃生门·真英文问句用）")
    sub.add_parser("selftest", help="自测（temp 夹具零污染）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        sys.exit(_selftest())
    code, msg = append_line(args.file, args.line, allow_ascii_qmark=args.allow_ascii_qmark)
    print(("APPEND " if code == 0 else "") + msg)
    if code == 0:
        print("NOTE: " + BUDGET_NOTE)
    sys.exit(code)


if __name__ == "__main__":
    main()
