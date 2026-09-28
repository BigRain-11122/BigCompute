#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""banned_words_check.py — 发布面禁用词逐词过检（0 命中判据·话术/文案稿）

用法:
  python Tools/banned_words_check.py <file> [<file2> ...]

扫描目标文件中「」引号内文本（=口播/发布面），逐词检索禁用词总表：
  0 命中        -> exit 0 (PASS)
  有命中        -> exit 1 (逐行报告·发布面禁定稿)
  未发现话术引号 -> exit 2 (WARN·人工确认检查面)

词源（2026-09-28 机械镜像·改表须留指针）:
  docs/ops/merchandise-listing-v0.md §三禁用词行（财务/资产承诺面+绝对化用语）
  docs/ops/livestream-plan-v1.md §4.4 打赏权益暗示禁词 + §五 D 话术禁区
豁免面: 否定式合规提示（非投顾声明）先剥离再扫描（ALLOW_PHRASES）。"""
import re
import sys

BANNED = [
    # 财务/资产承诺面（merchandise-listing §三）
    "升值", "增值", "保值", "投资", "收益", "回报", "保本", "回购", "原始股",
    "空投", "挖矿", "错过不再", "强制稀缺", "稀缺", "收藏价值", "传世", "涨", "翻倍",
    # 绝对化用语（同源）
    "国家级", "最高级", "最佳", "第一", "顶级", "全网最",
    # 打赏权益暗示面（livestream-plan §4.4）
    "解锁", "特权", "返现", "上榜有奖",
    # 话术禁区（livestream-plan §五 D）
    "运势", "算命", "预测", "八字五行",
]

# 否定式合规提示豁免短语：命中其内的禁词=法定提示句式非违禁（剥离后扫描）
ALLOW_PHRASES = [
    "不构成任何投资建议",
    "不构成投资建议",
    "非投资建议",
]

QUOTE_RE = re.compile(r"「([^」]*)」")


def check_file(path):
    hits, quotes_scanned = [], 0
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            for quote in QUOTE_RE.findall(line):
                quotes_scanned += 1
                cleaned = quote
                for allow in ALLOW_PHRASES:
                    cleaned = cleaned.replace(allow, "")
                for word in BANNED:
                    if word in cleaned:
                        hits.append(f"  L{lineno}: 「{quote}」 -> 命中 [{word}]")
    return hits, quotes_scanned


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows GBK 控制台防 ¥ 等字符编码崩溃
        sys.stdout.reconfigure(errors="backslashreplace")
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    total_hits, total_quotes, files_scanned = [], 0, 0
    for path in sys.argv[1:]:
        hits, n = check_file(path)
        total_hits.extend(hits)
        total_quotes += n
        files_scanned += 1
        print(f"[banned-words] {path}: quotes={n} hits={len(hits)}")
    if total_quotes == 0:
        print("[banned-words] WARN: 未发现话术引号「」——请人工确认检查面 exit=2")
        return 2
    if total_hits:
        print(f"[banned-words] FAIL: {len(total_hits)} 处命中（发布面禁定稿）")
        print("\n".join(total_hits))
        return 1
    print(f"[banned-words] PASS: files={files_scanned} quotes={total_quotes} hits=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
