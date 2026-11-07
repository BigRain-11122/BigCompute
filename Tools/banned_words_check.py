#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""banned_words_check.py — 发布面禁用词逐词过检（0 命中判据·话术/文案稿）

用法:
  python Tools/banned_words_check.py <file> [<file2> ...]   话术面过检（默认）
  python Tools/banned_words_check.py census [--doc PATH] [--txt-out PATH]
                                                            词表漂移普查（扫描域自证）
  python Tools/banned_words_check.py selftest                夹具自检

扫描目标文件中「」引号内文本（=口播/发布面），逐词检索禁用词总表：
  0 命中        -> exit 0 (PASS)
  有命中        -> exit 1 (逐行报告·发布面禁定稿)
  未发现话术引号 -> exit 2 (WARN·人工确认检查面)

census（词表漂移普查）:
  本工具内的 BANNED 是人工镜像的常量，正典在文档 §四 禁用词过检总表。
  改文档不改代码（或反之）会静默失配——普查把两侧逐词比对并逻辑点名：
  MISSING-IN-TOOL  = 文档声明了但工具不检（= 声明了却没执行）
  UNDECLARED-IN-TOOL= 工具检了但文档没声明（= 执行了却没据可依）
  语义类条目（如「永不说成财务回报」「真实商标/真人」）无词形不可机检，
  单独计数并列出，禁默认为「已过检」。
  同时打印扫描域（doc/files/words/semantic）——只报结论不算自证（D-20 同族）。

词源（2026-09-28 机械镜像·改表须留指针）:
  docs/ops/merchandise-listing-v0.md §三禁用词行（财务/资产承诺面+绝对化用语）
  docs/ops/livestream-plan-v1.md §4.4 打赏权益暗示禁词 + §五 D 话术禁区
豁免面: 否定式合规提示（非投顾声明）先剥离再扫描（ALLOW_PHRASES）。
"""
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

# 词表正典（census 用）：§四 禁用词过检总表所在文档
DEFAULT_DOC = "docs/ops/livestream-cart-scripts-v1.md"
# §四 四个分组行首标识
GROUP_PREFIXES = ("- 财务", "- 绝对化", "- 打赏", "- 话术禁区")
# 纯词形：中文/字母/数字，长度 ≤8——超长或含引号、斜杠、句读者=语义类条目
WORD_TOKEN_RE = re.compile(r"^[0-9A-Za-z\u4e00-\u9fff]{1,8}$")


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


def parse_doc_words(path):
    """从文档 §四 四行分组里抽出词形；返回 (words, semantic_segments, groups_seen)。

    只收纯词形 token——语义类条目（含引号/斜杠/句读/超长）单列，不冒充已检。
    """
    words, semantic, groups = [], [], []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            raw = line.strip()
            if not any(raw.startswith(p) for p in GROUP_PREFIXES):
                continue
            groups.append(f"L{lineno}:{raw.split('：')[0]}")
            body = raw.split("：", 1)[1] if "：" in raw else raw
            for seg in re.split(r"[｜；]", body):
                seg = seg.strip()
                if not seg:
                    continue
                if WORD_TOKEN_RE.match(seg):
                    words.append(seg)
                else:
                    semantic.append(f"  L{lineno}: {seg}")
    return words, semantic, groups


def census(doc=DEFAULT_DOC):
    """返回 (verdict, payload)。verdict ∈ CENSUS-GREEN/CENSUS-DRIFT/CENSUS-UNREADABLE。"""
    try:
        doc_words, semantic, groups = parse_doc_words(doc)
    except OSError as exc:
        return "CENSUS-UNREADABLE", {
            "doc": doc, "error": str(exc), "doc_words": [], "semantic": [],
            "groups": [], "missing_in_tool": [], "undeclared_in_tool": [],
        }
    doc_set, tool_set = set(doc_words), set(BANNED)
    payload = {
        "doc": doc,
        "groups": groups,
        "doc_words": doc_words,
        "tool_words": list(BANNED),
        "semantic": semantic,
        "missing_in_tool": sorted(doc_set - tool_set),
        "undeclared_in_tool": sorted(tool_set - doc_set),
    }
    ok = not payload["missing_in_tool"] and not payload["undeclared_in_tool"] and groups
    if not groups:  # 一行分组都没读到=没量，绝不判绿
        return "CENSUS-UNREADABLE", payload
    return ("CENSUS-GREEN" if ok else "CENSUS-DRIFT"), payload


def render_census(verdict, p):
    out = [f"[banned-census] VERDICT={verdict} doc={p['doc']}"]
    out.append(f"[banned-census] domain: groups={len(p['groups'])} "
               f"doc_words={len(p['doc_words'])} tool_words={len(p.get('tool_words', []))} "
               f"semantic_not_machine_checkable={len(p['semantic'])}")
    for g in p["groups"]:
        out.append(f"  group {g}")
    if p["missing_in_tool"]:
        out.append(f"  MISSING-IN-TOOL ({len(p['missing_in_tool'])}): "
                   + "、".join(p["missing_in_tool"]))
    if p["undeclared_in_tool"]:
        out.append(f"  UNDECLARED-IN-TOOL ({len(p['undeclared_in_tool'])}): "
                   + "、".join(p["undeclared_in_tool"]))
    if p["semantic"]:
        out.append("  语义类条目（禁默认为已过检·须人审闸）:")
        out.extend(p["semantic"])
    if "error" in p:
        out.append(f"  error: {p['error']}")
    return "\n".join(out)


def selftest():
    import os
    import tempfile

    cases = []

    def run_case(name, body, want):
        tmpdir = tempfile.mkdtemp(prefix="bwc-")
        path = os.path.join(tmpdir, "doc.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        verdict, payload = census(path)
        ok = verdict == want
        cases.append((ok, name, verdict, want))
        return payload

    # S1 文档与工具常量完全一致 -> GREEN
    body_ok = ("## 四\n"
               "- 财务/资产承诺面：升值｜增值\n"
               "- 绝对化用语：最佳｜第一\n"
               "- 打赏权益暗示面：解锁｜特权\n"
               "- 话术禁区：运势｜算命\n")
    # 工具常量是全量 32 词，故 S1 用全量拼一份文档
    full = ("## 四\n"
            "- 财务/资产承诺面（同源）：" + "｜".join(BANNED[:18]) + "\n"
            "- 绝对化用语（同源）：" + "｜".join(BANNED[18:24]) + "\n"
            "- 打赏权益暗示面：" + "｜".join(BANNED[24:28]) + "\n"
            "- 话术禁区：" + "｜".join(BANNED[28:32]) + "\n")
    run_case("S1 全量对齐->GREEN", full, "CENSUS-GREEN")

    # S2 文档少一词 = 工具多检 -> UNDECLARED-IN-TOOL
    p = run_case("S2 工具多检一词->DRIFT", full.replace("｜翻倍", ""), "CENSUS-DRIFT")
    ok = p["undeclared_in_tool"] == ["翻倍"] and not p["missing_in_tool"]
    cases.append((ok, "S2b 差集指出 翻倍 为未声明", p["undeclared_in_tool"], ["翻倍"]))

    # S3 文档多一词（工具不检）-> MISSING-IN-TOOL
    p = run_case("S3 文档声明未执行->DRIFT",
                 full.replace("｜翻倍", "｜翻倍｜保收益"), "CENSUS-DRIFT")
    ok = p["missing_in_tool"] == ["保收益"] and not p["undeclared_in_tool"]
    cases.append((ok, "S3b 差集指出 保收益 未入工具", p["missing_in_tool"], ["保收益"]))

    # S4 语义类条目只计数、不混入词表
    p = run_case("S4 语义类单列->GREEN",
                 full.replace("- 话术禁区：", "- 话术禁区：「让城长大」永不说成财务回报；真实商标/真人；"),
                 "CENSUS-GREEN")
    ok = len(p["semantic"]) == 2 and not p["missing_in_tool"]
    cases.append((ok, "S4b 语义两条计入 semantic", len(p["semantic"]), 2))

    # S5 一行分组都没有 = 没量，不得判绿
    run_case("S5 无分组行->UNREADABLE", "## 四\n正文随便写\n", "CENSUS-UNREADABLE")

    # S6 文档缺失 -> UNREADABLE 且带 error
    verdict, payload = census(os.path.join(tempfile.gettempdir(), "no-such-bwc-doc.md"))
    cases.append((verdict == "CENSUS-UNREADABLE" and "error" in payload,
                  "S6 文档缺失->UNREADABLE", verdict, "CENSUS-UNREADABLE"))

    # S7 渲染含扫描域三行（结论不自证）
    verdict, payload = census(DEFAULT_DOC) if os.path.exists(DEFAULT_DOC) else ("CENSUS-UNREADABLE", {"doc": DEFAULT_DOC, "doc_words": [], "semantic": [], "groups": [], "missing_in_tool": [], "undeclared_in_tool": []})
    text = render_census(verdict, payload)
    ok = "domain:" in text and "tool_words=" in text
    cases.append((ok, "S7 渲染含扫描域行", "domain:" in text, True))

    failed = 0
    for ok, name, got, want in cases:
        print(f"[selftest] {'PASS' if ok else 'FAIL'} {name} got={got!r} want={want!r}")
        failed += 0 if ok else 1
    print(f"[selftest] {len(cases) - failed}/{len(cases)} PASS")
    return 0 if failed == 0 else 1


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows GBK 控制台防 ¥ 等字符编码崩溃
        sys.stdout.reconfigure(errors="backslashreplace")
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    if args[0] == "selftest":
        return selftest()

    if args[0] == "census":
        doc, txt_out = DEFAULT_DOC, None
        rest = args[1:]
        i = 0
        while i < len(rest):
            if rest[i] == "--doc" and i + 1 < len(rest):
                doc = rest[i + 1]
                i += 2
            elif rest[i] == "--txt-out" and i + 1 < len(rest):
                txt_out = rest[i + 1]
                i += 2
            else:
                print(f"[banned-census] 未知参数 {rest[i]!r}")
                return 2
        verdict, payload = census(doc)
        text = render_census(verdict, payload)
        print(text)
        if txt_out:
            with open(txt_out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text + "\n")
            print(f"[banned-census] txt-out={txt_out}")
        return 0 if verdict == "CENSUS-GREEN" else 1

    total_hits, total_quotes, files_scanned = [], 0, 0
    for path in args:
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