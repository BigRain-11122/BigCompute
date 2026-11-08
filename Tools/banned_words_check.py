#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""banned_words_check.py — 发布面禁用词逐词过检（0 命中判据·话术/文案稿）

用法:
  python Tools/banned_words_check.py <file> [<file2> ...]   话术面过检（默认）
  python Tools/banned_words_check.py census [--doc PATH] [--txt-out PATH]
                                                            词表漂移普查（扫描域自证）
  python Tools/banned_words_check.py coverage [--txt-out PATH] [--json-out PATH]
                                                            引号覆盖率普查（量化漏检面）
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
import os
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

# 纪律自述豁免（M159）：`禁运势/算命/预测/八字（…）` 这类「禁令列出被禁词」
# 的纪律行，禁词本身就是规则内容而非违禁用法。判据=禁词落在否定词后、句读前的
# 同一段内才算豁免；否定段在标点处终止，故「禁止赌博，这里保证升值」仍判命中。
DISCIPLINE_MARKERS = (
    "禁止", "严禁", "禁用", "杜绝", "避免", "拒绝",
    "不得", "不许", "不能", "不可", "不做", "不要", "不准", "勿", "禁",
)
DISCIPLINE_TERMINATORS = "。；！？，,;!?：:（）()「」【】\n"
DISCIPLINE_RE = re.compile("|".join(DISCIPLINE_MARKERS))


def _discipline_spans(text):
    """返回「否定词之后、句读之前」的字符区间列表（半开区间）。"""
    spans = []
    for m in DISCIPLINE_RE.finditer(text):
        i = m.end()
        if i >= len(text) or text[i] in DISCIPLINE_TERMINATORS:
            continue  # 否定词后紧跟句读=不是列举禁用词
        j = i
        while j < len(text) and text[j] not in DISCIPLINE_TERMINATORS:
            j += 1
        spans.append((i, j))
    return spans


def _in_spans(pos, spans):
    return any(a <= pos < b for a, b in spans)

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


# ---- M156 口播引号覆盖率普查（漏检面量化·只读·复用本工具词表） ----
PLAN_DOC = "docs/ops/livestream-plan-v1.md"
# (path, selector)：selector 首位是数字=按编号取节（4.1 不吃 4.10），否则按标题子串取节
COVERAGE_TARGETS = [
    (PLAN_DOC, "4.1"),
    (PLAN_DOC, "4.2"),
    # M169（11-08）：原列 "4.6"，实测 §四 只有 4.1-4.4，缺节把整份 VERDICT 恒拉成
    # PARSE-SUSPECT、真信号被遮蔽；按事实改为 4.3＝§四 四节全覆盖。缺节负控仍由 S12 夹具把守。
    (PLAN_DOC, "4.3"),
    (PLAN_DOC, "4.4"),
    (DEFAULT_DOC, "二、十类挂载讲解词"),
    (DEFAULT_DOC, "三、禁挂两类口播位"),
]
HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")
MARKUP_RE = re.compile(r"[>*`「」]")


def _sections(path):
    """按标题切节，返回 [(level, title, lineno, [(lineno, line)])]。"""
    out, cur = [], None
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            m = HEADING_RE.match(line)
            if m:
                if cur:
                    out.append(cur)
                cur = [len(m.group(1)), m.group(2).strip(), lineno, []]
            elif cur is not None:
                cur[3].append((lineno, line))
    if cur:
        out.append(cur)
    return [tuple(s) for s in out]


def _pick_section(sections, selector):
    """返回 (title, lineno, lines)；取不到= (None, 0, [])。"""
    key = str(selector).strip()
    for idx, (level, title, lineno, lines) in enumerate(sections):
        if key[0].isdigit():
            if not re.match(r"^" + re.escape(key) + r"(?![\d.])", title):
                continue
        elif key not in title:
            continue
        block = list(lines)
        for level2, title2, lineno2, lines2 in sections[idx + 1:]:
            if level2 <= level:
                break
            block.extend(lines2)
        return title, lineno, block
    return None, 0, []


def _speech_lines(lines):
    """口播候选行=引用块行（>）或含「」的行；表格/清单勾选/标题/分隔线不算口播。"""
    out = []
    for lineno, raw in lines:
        s = raw.strip()
        if not s or s.startswith("|") or s.startswith("#") or s.startswith("---") or s.startswith("- ["):
            continue
        body = s
        while body.startswith(">"):
            body = body[1:].lstrip()
        if not body:
            continue
        if s.startswith(">") or "「" in body:
            out.append((lineno, body))
    return out


def _plain(text):
    return re.sub(r"\s+", "", MARKUP_RE.sub("", text))


def _scan_quotes(speech):
    """按「」深度配对逐字扫描（跨行状态连续）。

    正则 `「(.*?)」` 遇到嵌套引号（如 4.1 披露语里的「本产品」）会提前闭合，
    把整段引用误判成引号外——这里用深度计数消除该偏差。
    返回 (chars, quoted, unquoted, per_line, remainder_text)。
    """
    depth = 0
    chars = quoted = unquoted = 0
    per_line, rem = [], []
    for lineno, body in speech:
        in_q = un_q = 0
        bare_chars = []
        for ch in re.sub(r"[>*`]", "", body):
            if ch.isspace():
                continue
            if ch == "「":
                depth += 1
                continue
            if ch == "」":
                depth = max(0, depth - 1)
                continue
            chars += 1
            if depth > 0:
                quoted += 1
                in_q += 1
            else:
                unquoted += 1
                un_q += 1
                bare_chars.append(ch)
                rem.append(ch)
        per_line.append((lineno, in_q, un_q, "".join(bare_chars)))
    return chars, quoted, unquoted, per_line, "".join(rem)


def coverage(targets=None, min_chars=50):
    """量化「引号外口播」：口播候选行里有多少字不在「」内，以及这些字里有没有禁词。

    min_chars=真实文档的下限（哑夹具可放宽），低于它=没量够，禁判 FULL/GAP。
    每行三态：quoted（整行在引号内）/ partial（有引号但行内仍有引号外文本）/
    bare（整行无引号 = 纯文本口播候选，真漏检面）。
    """
    targets = COVERAGE_TARGETS if targets is None else targets
    rows, missing, total_chars, total_quoted = [], [], 0, 0
    hits, samples = [], []
    bare_lines_total = 0
    discipline_exempt_total = 0
    # M170: 空节（有标题无口播文本）与缺节是两件事——缺节让 verdict 变
    # PARSE-SUSPECT，空节什么都不影响，却会把 sections=n/n 读成「覆盖齐全」。
    # 这里只让它可见（判据不动）。
    empty_sections = []
    for path, selector in targets:
        try:
            sections = _sections(path)
        except OSError as exc:
            rows.append({"path": path, "selector": selector, "error": str(exc)})
            missing.append("%s §%s (文件不可读)" % (path, selector))
            continue
        title, lineno, block = _pick_section(sections, selector)
        if title is None:
            missing.append("%s §%s (节不存在)" % (path, selector))
            continue
        speech = _speech_lines(block)
        chars, qchars, uchars, per_line, rem = _scan_quotes(speech)
        sec_hits, sec_exempt = [], 0
        for lineno2, in_q, un_q, bare_txt in per_line:
            cleaned = bare_txt
            for allow in ALLOW_PHRASES:
                cleaned = cleaned.replace(allow, "")
            spans = _discipline_spans(cleaned)
            for w in BANNED:
                occ = [m.start() for m in re.finditer(re.escape(w), cleaned)]
                if not occ:
                    continue
                live = [p for p in occ if not _in_spans(p, spans)]
                sec_exempt += len(occ) - len(live)
                if live:
                    sec_hits.append({"word": w, "line": lineno2, "text": bare_txt[:48]})
                    hits.append("%s §%s L%d: 引号外命中 [%s] 上下文=%s"
                                % (os.path.basename(path), selector, lineno2, w, bare_txt[:40]))
        discipline_exempt_total += sec_exempt
        bare = [p for p in per_line if p[2] and not p[1]]
        partial = [p for p in per_line if p[2] and p[1]]
        for lineno2, in_q, un_q, bare_txt in (bare + partial)[:12]:
            if len(samples) < 12:
                samples.append("L%d %s %s…" % (lineno2,
                                               "BARE" if (un_q and not in_q) else "PARTIAL",
                                               bare_txt[:36]))
        rows.append({
            "path": path, "selector": selector, "title": title, "line": lineno,
            "speech_lines": len(speech), "chars": chars,
            "quoted_chars": qchars, "unquoted_chars": uchars,
            "bare_lines": len(bare), "partial_lines": len(partial),
            "hits": sec_hits, "discipline_exempt": sec_exempt,
            "empty": chars == 0,
        })
        if chars == 0:
            empty_sections.append("%s §%s (空节·不计字数)" % (path, selector))
        total_chars += chars
        total_quoted += qchars
        bare_lines_total += len(bare)
    if missing or total_chars < min_chars:
        verdict = "QUOTE-COVERAGE-PARSE-SUSPECT"
    elif hits:
        verdict = "QUOTE-COVERAGE-GAP-HIT"
    elif total_chars == total_quoted:
        verdict = "QUOTE-COVERAGE-FULL"
    else:
        verdict = "QUOTE-COVERAGE-GAP"
    unq = max(0, total_chars - total_quoted)
    # 已解析各节的独立判定：缺节不该把整份文档的读数一并作废（缺节另计）
    if total_chars < min_chars:
        resolved = "QUOTE-COVERAGE-PARSE-SUSPECT"
    elif hits:
        resolved = "QUOTE-COVERAGE-GAP-HIT"
    elif unq == 0:
        resolved = "QUOTE-COVERAGE-FULL"
    else:
        resolved = "QUOTE-COVERAGE-GAP"
    payload = {
        "verdict": verdict, "resolved_verdict": resolved,
        "rows": rows, "missing_sections": missing,
        "speech_chars": total_chars, "quoted_chars": total_quoted,
        "unquoted_chars": unq,
        "unquoted_rate": round(unq / total_chars, 4) if total_chars else 0.0,
        "bare_lines": bare_lines_total,
        "hits": hits, "samples": samples,
        "discipline_exempt": discipline_exempt_total,
        "empty_sections": empty_sections,
    }
    return verdict, payload


def render_coverage(verdict, p):
    out = ["[quote-coverage] VERDICT=%s" % verdict]
    out.append("[quote-coverage] resolved-only=%s（缺节不影响已解析节的读数·缺节另计）"
               % p.get("resolved_verdict", "-"))
    out.append("[quote-coverage] domain: speech_chars=%d quoted=%d unquoted=%d rate=%.1f%% bare_lines=%d discipline_exempt=%d empty_sections=%d sections=%d/%d"
               % (p["speech_chars"], p["quoted_chars"], p["unquoted_chars"],
                  p["unquoted_rate"] * 100, p.get("bare_lines", 0),
                  p.get("discipline_exempt", 0), len(p.get("empty_sections") or []),
                  len(p["rows"]),
                  len(p["rows"]) + len(p["missing_sections"])))
    for r in p["rows"]:
        if "error" in r:
            out.append("  %s §%s ERROR %s" % (r["path"], r["selector"], r["error"]))
            continue
        out.append("  %s §%s L%d lines=%d chars=%d quoted=%d unquoted=%d bare=%d partial=%d hits=%d exempt=%d%s"
                   % (r["path"], r["selector"], r["line"], r["speech_lines"],
                      r["chars"], r["quoted_chars"], r["unquoted_chars"],
                      r["bare_lines"], r["partial_lines"], len(r["hits"]),
                      r.get("discipline_exempt", 0),
                      " EMPTY" if r.get("empty") else ""))
    for m in p["missing_sections"]:
        out.append("  MISSING-SECTION %s" % m)
    for m in (p.get("empty_sections") or []):
        out.append("  EMPTY-SECTION %s" % m)
    if p["hits"]:
        out.append("  引号外命中（真漏检·禁判 0 命中）:")
        out.extend("    " + h for h in p["hits"])
    if p["samples"]:
        out.append("  引号外文本样本（前 12 行，取自话术面）:")
        out.extend("    " + s for s in p["samples"])
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

    # ---- coverage 夹具（S8-S12）----
    def cov_case(name, body, want, selector="4.1", extra=None):
        tmpdir = tempfile.mkdtemp(prefix="bwc-cov-")
        path = os.path.join(tmpdir, "plan.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        targets = [(path, selector)] + (extra or [])
        verdict, payload = coverage(targets, min_chars=1)
        cases.append((verdict == want, name, verdict, want))
        return payload

    sec = "### 4.1 开场白\n\n> 「{0}」\n{1}\n### 4.2 下一节\n\n> 「别段」\n"
    # S8 口播全在引号内 -> FULL
    cov_case("S8 全引号->FULL", sec.format("欢迎来到直播间，本场全程真人值守请大家安心观看", ""),
             "QUOTE-COVERAGE-FULL")
    # S9 有引号外裸文本 -> GAP（且未越界到下一节：裸文本在 4.1 块内）
    p = cov_case("S9 引号外裸文本->GAP",
                 sec.format("欢迎来到直播间", "> 这段没有引号的话直接上屏"),
                 "QUOTE-COVERAGE-GAP")
    cases.append((p["unquoted_chars"] > 0 and p["speech_chars"] > p["quoted_chars"],
                  "S9b 量化出引号外字符", p["unquoted_chars"], ">0"))
    # S10 引号外命中禁词 -> GAP-HIT（0 命中判据不成立）
    cov_case("S10 引号外命中->GAP-HIT",
             sec.format("欢迎来到直播间", "> 这款藏品保证升值翻倍"),
             "QUOTE-COVERAGE-GAP-HIT")
    # S11 引号内命中不算漏检（覆盖率口径只量表外，词检另跑）
    p = cov_case("S11 引号内命中不走覆盖口径",
                 sec.format("这款藏品保证升值", ""), "QUOTE-COVERAGE-FULL")
    cases.append((p["hits"] == [] and p["unquoted_chars"] == 0,
                  "S11b 引号内文本不计入引号外命中", p["hits"], []))
    # S12 节号不存在 -> PARSE-SUSPECT（禁静默跳过）
    cov_case("S12 缺节->PARSE-SUSPECT", sec.format("欢迎来到直播间", ""),
             "QUOTE-COVERAGE-PARSE-SUSPECT", selector="4.6")
    # S13 纪律自述行（禁令列举被禁词）-> 不判命中，且豁免计数>0（M159）
    p = cov_case("S13 纪律自述->豁免不判命中",
                 sec.format("欢迎来到直播间", "> 禁运势/算命/预测/八字（封号重灾区）"),
                 "QUOTE-COVERAGE-GAP")
    cases.append((p["hits"] == [] and p["discipline_exempt"] == 3,
                  "S13b 纪律自述豁免计数=3", p["discipline_exempt"], 3))
    # S14 同一句里禁令段之后仍有真违禁 -> 仍 GAP-HIT（豁免不越句读）
    cov_case("S14 禁令后真违禁->GAP-HIT",
             sec.format("欢迎来到直播间", "> 禁止赌博，这款保证升值"),
             "QUOTE-COVERAGE-GAP-HIT")
    # S15 S10 的禁词前无否定词 -> 豁免计数必须为 0（负控：豁免不能变成静音）
    p = cov_case("S15 无否定词不豁免",
                 sec.format("欢迎来到直播间", "> 这款藏品保证升值翻倍"),
                 "QUOTE-COVERAGE-GAP-HIT")
    cases.append((p["discipline_exempt"] == 0,
                  "S15b 无否定词时豁免计数=0", p["discipline_exempt"], 0))

    # S16/S17 空节 ≠ 缺节 ≠ 成绩（M170）：真实文档里 §4.3 是占位节（有标题无
    # 口播），它把 sections 撑成 n/n 却不贡献一个字；而缺节仍必须 PARSE-SUSPECT。
    tmpdir = tempfile.mkdtemp(prefix="bwc-cov-")
    path = os.path.join(tmpdir, "plan.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("### 4.1 开场白\n\n> 「欢迎来到直播间」\n"
                 "### 4.3 商品讲解话术（占位）\n\n"
                 "### 4.4 反诈\n\n> 「别转账」\n")
    v16, p16 = coverage([(path, "4.1"), (path, "4.3")], min_chars=1)
    ok = (v16 == "QUOTE-COVERAGE-FULL"
          and len(p16.get("empty_sections") or []) == 1
          and p16["missing_sections"] == []
          and p16["speech_chars"] > 0)
    cases.append((ok, "S16 空节点名但不改判据（≠缺节≠成绩）",
                  "%s empty=%d missing=%d"
                  % (v16, len(p16.get("empty_sections") or []),
                     len(p16["missing_sections"])),
                  "FULL empty=1 missing=0"))
    v17, p17 = coverage([(path, "4.1"), (path, "4.9")], min_chars=1)
    ok = (v17 == "QUOTE-COVERAGE-PARSE-SUSPECT"
          and (p17.get("empty_sections") or []) == []
          and len(p17["missing_sections"]) == 1)
    cases.append((ok, "S17 缺节仍 PARSE-SUSPECT 且不被读成空节",
                  "%s empty=%d missing=%d"
                  % (v17, len(p17.get("empty_sections") or []),
                     len(p17["missing_sections"])),
                  "SUSPECT empty=0 missing=1"))

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

    if args[0] == "coverage":
        txt_out = json_out = None
        rest = args[1:]
        i = 0
        while i < len(rest):
            if rest[i] == "--txt-out" and i + 1 < len(rest):
                txt_out = rest[i + 1]
                i += 2
            elif rest[i] == "--json-out" and i + 1 < len(rest):
                json_out = rest[i + 1]
                i += 2
            else:
                print(f"[quote-coverage] 未知参数 {rest[i]!r}")
                return 2
        verdict, payload = coverage()
        text = render_coverage(verdict, payload)
        print(text)
        if txt_out:
            with open(txt_out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text + "\n")
        if json_out:
            import json
            with open(json_out, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
        if verdict == "QUOTE-COVERAGE-PARSE-SUSPECT":
            return 3
        return 0 if verdict == "QUOTE-COVERAGE-FULL" else 1

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