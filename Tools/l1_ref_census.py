#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""l1_ref_census.py - static census of hard-coded lane endpoint references.

Why (tech T149): after the :8090 lane artifacts vanished (T148) we found one
consumer that hard-codes the endpoint in its default and has no failover. Before
changing anything we need the full picture: every place that names the port, and
for each one whether it can be steered elsewhere (env override / multi-endpoint)
or would just break. Read-only: this tool never touches a service.

Classification per reference line (T150 tightening: steering is judged in the
*scope of the call site*, not across the whole file):
  DOC-REF        comment line, or inside a module/class/function docstring
  MESSAGE-REF    the port only appears in a log line / dict key / error text -
                 it names the lane, it never calls it (no failover meaning)
  DIAGNOSTIC     read-only probe / health line (a dead lane is the *expected*
                 finding here; no failover required)
  FALLBACK-OK    client line whose enclosing scope can re-point it (inline env
                 read, env read in the same function/module block, a second
                 endpoint literal in that scope, or an explicit failover keyword)
  UNKNOWN        steering exists in the file but NOT in this reference's scope -
                 the env var may never reach this call. Needs a human read, and
                 is deliberately not counted as green.
  NO-FALLBACK    client line with a bare literal and nothing to steer it
                 (rows carry domain=EXTERNAL when the file lives outside this
                 repo, i.e. the fix belongs to another owner - we file it, we
                 do not edit their code)

Usage:
  python Tools/l1_ref_census.py selftest
  python Tools/l1_ref_census.py scan [--roots Tools ..\\..\\Tools] [--port 8090]
                                     [--json-out state/l1-ref-census.json]
                                     [--md-out docs/ops/l1-fallback-census.md]
Exit: 0 = GREEN (no NO-FALLBACK client refs), 3 = ATTENTION, 2 = usage/scan error.
"""
import argparse
import ast
import io
import json
import os
import re
import sys
import tokenize

SCAN_EXTS = (".py", ".ps1", ".cmd", ".bat")
KNOWN_PORTS = ("8090", "11434", "8080", "8000", "8091", "8092", "5000", "8792")
DOC_HINT = "# -*- coding"
PROBE_RE = re.compile(
    r"probe|alive|liveness|health|judge|verdict|_get\b|/v1/models|socket|connect", re.I)
ARGV_RE = re.compile(r"add_argument\(.*['\"]--(endpoint|base-?url|url|host|port)", re.I)
SERVER_RE = re.compile(r"--port\b|--host\b|argList|httplistener|\.listen\(|\bbind\(", re.I)
FIXTURE_RE = re.compile(r"selftest|_test\b|drill|fixture", re.I)
ENV_RE = re.compile(
    r"environ\.get\(\s*['\"]([A-Z0-9_]+)['\"]|getenv\(\s*['\"]([A-Z0-9_]+)['\"]")
PS_ENV_RE = re.compile(r"\$env:([A-Za-z0-9_]+)")
FAILOVER_RE = re.compile(r"fallback|回退|先试|备用|next_endpoint|endpoints\s*=\s*\[", re.I)
ENDPOINT_NAME_RE = re.compile(r"(url|endpoint|base|host|addr|uri|\bep\b)", re.I)
STRING_LIT_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def _docstring_lines(src):
    """Line numbers covered by module/class/function docstrings (best effort)."""
    covered = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return covered
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            lo = getattr(first, "lineno", 0)
            hi = getattr(first, "end_lineno", lo) or lo
            for n in range(lo, hi + 1):
                covered.add(n)
    return covered


def _function_ranges(src):
    """[(name, start_line, end_line)] for every def, so fixtures can be recognised."""
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno))
    return out


def _enclosing_func(ranges, lineno):
    """All enclosing def names, innermost last (nested helpers count as fixtures too)."""
    hits = [(hi - lo, name) for name, lo, hi in ranges if lo <= lineno <= hi]
    return [name for _span, name in sorted(hits, reverse=True)]


def _strip_trailing_comment(line):
    """Cut an inline '#' comment without tripping over URLs/schemes."""
    idx = line.find(" #")
    return line[:idx] if idx >= 0 else line


def _scope_lines(funcs, lineno, exec_lines):
    """Executable lines of the reference's scope: enclosing def, else module block.

    T150: an env read three functions away is not a steering mechanism for this
    call site, so the scope is deliberately narrow.
    """
    encl = sorted((hi - lo, lo, hi) for _name, lo, hi in funcs if lo <= lineno <= hi)
    if encl:
        _span, lo, hi = encl[0]
        return [(n, t) for n, t in exec_lines if lo <= n <= hi]
    return [(n, t) for n, t in exec_lines
            if not any(lo <= n <= hi for _name, lo, hi in funcs)]


def _env_hits(lines):
    """[(line, VAR)] env reads inside the given scope."""
    out = []
    for n, text in lines:
        m = ENV_RE.search(text) or PS_ENV_RE.search(text)
        if m:
            out.append((n, m.group(1) or m.group(2)))
    return out


def _multi_hits(lines, port):
    """[(line, port)] other known endpoint literals inside the given scope."""
    out = []
    for n, text in lines:
        for p in KNOWN_PORTS:
            if p == str(port):
                continue
            if re.search(r"(?<![\d])%s(?![\d])" % p, text):
                out.append((n, p))
    return out


PORT_BOUND_L = r"(?<![A-Za-z0-9_])"
PORT_BOUND_R = r"(?![A-Za-z0-9_])"


def _port_pattern(port):
    """Port token not glued into a longer identifier (lane8090 is a name, not a port)."""
    return re.compile(PORT_BOUND_L + re.escape(str(port)) + PORT_BOUND_R)


def _label_only(code, port):
    """True when the port occurs only inside plain label/log strings, not a URL.

    T150: message text such as 'LANE-DOWN: ... owns the port' names the lane and
    never calls it, so it must not be scored as a client reference. An
    endpoint-shaped assignee (url/endpoint/base/host/ep/addr/uri) keeps the line
    in the client family.
    """
    pat = _port_pattern(port)
    hits = [m.start() for m in pat.finditer(code)]
    if not hits:
        return False
    spans = [(m.start(), m.end()) for m in STRING_LIT_RE.finditer(code)]
    for pos in hits:
        literal = None
        for lo, hi in spans:
            if lo <= pos < hi:
                literal = code[lo:hi]
                break
        if literal is None or "://" in literal:
            return False           # bare token or URL literal -> real endpoint ref
    lhs = code.split("=", 1)[0] if "=" in code else ""
    return not ENDPOINT_NAME_RE.search(lhs)


def _is_external(path, ours):
    """True when the file lives outside this repo (fix belongs to another owner)."""
    if not ours:
        return False
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(ours)]) \
            != os.path.abspath(ours)
    except ValueError:                       # different drives on Windows
        return True


def classify_file(path, port, ours=None):
    """Return list of reference rows for one file. Pure/read-only."""
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    lines = src.splitlines()
    docs = _docstring_lines(src) if path.lower().endswith(".py") else set()
    funcs = _function_ranges(src) if path.lower().endswith(".py") else []
    pat = _port_pattern(port)

    code_lines = []
    exec_lines = []
    for n, raw in enumerate(lines, 1):
        stripped = raw.strip()
        is_comment = (stripped.startswith("#") or stripped.lower().startswith("rem ")
                      or stripped.startswith("::"))
        code_part = "" if (is_comment or n in docs) else _strip_trailing_comment(raw).strip()
        if code_part:
            exec_lines.append((n, code_part))
        if not pat.search(raw):
            continue
        code_lines.append({
            "line": n,
            "text": stripped[:180],
            "kind": "DOC" if (is_comment or n in docs) else "CODE",
            "code": code_part,
        })

    # file-level steering capability, kept only to explain an UNKNOWN verdict
    file_env = _env_hits(exec_lines)
    file_multi = _multi_hits(exec_lines, port)
    file_fail = [n for n, text in exec_lines if FAILOVER_RE.search(text)]
    domain = "EXTERNAL" if _is_external(path, ours) else "OWN"

    rows = []
    for row in code_lines:
        code = row["code"]
        fn = _enclosing_func(funcs, row["line"])
        in_fixture = any(FIXTURE_RE.search(name) for name in fn)
        scope = _scope_lines(funcs, row["line"], exec_lines)
        scope_env = _env_hits(scope)
        scope_multi = _multi_hits(scope, port)
        scope_fail = [n for n, text in scope if FAILOVER_RE.search(text)]
        inline_env = ENV_RE.search(code) or PS_ENV_RE.search(code)
        steering = ""
        if row["kind"] == "DOC":
            status = "DOC-REF"
        elif ARGV_RE.search(code):
            status, steering = "FALLBACK-OK", "ARGV"
        elif SERVER_RE.search(code):
            status = "SERVER-SIDE"
        elif in_fixture or code.startswith('{"'):
            status = "FIXTURE"
        elif PROBE_RE.search(code):
            status = "DIAGNOSTIC"
        elif _label_only(code, port):
            status = "MESSAGE-REF"       # label/log/dict-key mention, not a call site
        elif inline_env:
            status = "FALLBACK-OK"
            steering = "ENV:%s(INLINE)" % (inline_env.group(1) or inline_env.group(2))
        elif scope_env:
            status, steering = "FALLBACK-OK", "ENV:%s(SCOPE)" % scope_env[0][1]
        elif scope_multi:
            status, steering = "FALLBACK-OK", "MULTI-ENDPOINT(SCOPE)"
        elif scope_fail:
            status, steering = "FALLBACK-OK", "FAILOVER-KW(SCOPE)"
        elif file_env:
            status, steering = "UNKNOWN", "ENV-FILE-ONLY:%s" % file_env[0][1]
        elif file_multi or file_fail:
            status, steering = "UNKNOWN", "ENDPOINT-ELSEWHERE-IN-FILE"
        else:
            status = "NO-FALLBACK"
        rows.append({
            "file": path,
            "line": row["line"],
            "status": status,
            "steering": steering,
            "domain": domain,
            "text": row["text"],
        })
    return rows


def scan(roots, port, ours=None):
    out = []
    files_scanned = 0
    for root in roots:
        if not os.path.isdir(root):
            out.append({"file": root, "line": 0, "status": "ROOT-MISSING",
                        "steering": "", "domain": "OWN", "text": "root not found (skipped)"})
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in
                                 ("__pycache__", ".git", "_trash", "node_modules"))
            for name in sorted(filenames):
                if not name.lower().endswith(SCAN_EXTS):
                    continue
                if os.path.basename(name) == os.path.basename(__file__):
                    continue                       # 自查工具本身不计入普查面
                path = os.path.join(dirpath, name)
                try:
                    rows = classify_file(path, port, ours)
                except OSError as exc:                     # unreadable: report, don't crash
                    out.append({"file": path, "line": 0, "status": "UNREADABLE",
                                "steering": "", "domain": "OWN", "text": str(exc)[:120]})
                    continue
                files_scanned += 1
                out.extend(rows)
    out.sort(key=lambda r: (r["file"], r["line"]))
    counts = {}
    for r in out:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    if counts.get("NO-FALLBACK"):
        verdict = "ATTENTION"
    elif counts.get("UNKNOWN"):
        verdict = "CENSUS-GREEN-WITH-UNKNOWN"
    else:
        verdict = "CENSUS-GREEN"
    return {"port": str(port), "roots": roots, "files_scanned": files_scanned,
            "refs": out, "counts": counts, "verdict": verdict}


def render_md(rep):
    c = rep["counts"]
    L = ["# L1 车道引用点回退面普查（port %s）" % rep["port"], "",
         "> 产出=Tools/l1_ref_census.py 静态普查（只读·零服务调用）·数据源=roots %s"
         % ", ".join(rep["roots"]),
         "> 判据：DOC-REF=注释/文档串；MESSAGE-REF=端口只出现在日志/字典键/错误文案（点名而非调用）；"
         "DIAGNOSTIC=只读探针（车道死=预期发现，无需回退）；"
         "FALLBACK-OK=**该引用所在作用域**可改指（行内 env／同函数或模块块 env／同域多端点／"
         "显式回退关键字）；UNKNOWN=文件内虽有 env 或多端点但不在本调用点作用域（可能永远到不了这个调用）；"
         "NO-FALLBACK=客户端裸写且作用域内无任何改指手段。", "",
         "verdict: **%s** · files_scanned=%d · refs=%d · %s" % (
             rep["verdict"], rep["files_scanned"], len(rep["refs"]),
             " ".join("%s=%d" % (k, v) for k, v in sorted(c.items()))), "",
         "| 文件 | 行 | 判定 | 可改指 | 域 | 片段 |", "|---|---|---|---|---|---|"]
    for r in rep["refs"]:
        L.append("| %s | %s | %s | %s | %s | %s |" % (
            r["file"].replace("|", "\\|"), r["line"], r["status"], r["steering"] or "-",
            r.get("domain", "OWN"), r["text"].replace("|", "\\|")))
    L += ["", "## 声明出处（人工复核·2026-10-28）", "",
          "- 本仓 BLUEPRINT.md 对 `8090` 零命中——降级链声明不在本司仓，出处="
          "`cph4/fleet/dispatcher/MASTER-AGENT/BLUEPRINT.md:24`："
          "「降级链：:8090 不可达〔pause/让路/整卡回收〕→直升云 L2+晨检补记」。",
          "- 读法校正：该句是**运维策略**（pause／让路／整卡回收→改走云端），"
          "不是「代码内自动回退」。普查实证：代码面零「自动改走云端」实现，"
          "最接近的转向手段=env 覆盖（L1_ENDPOINT／RESIDENT_QA_LLM_URL）与 CLI 参数。",
          "- 因此「声明 vs 实装差集」的准确表述=策略存在、自动化不存在；"
          "无回退件按 owner 一行改处理，不下「声明造假」结论。", "",
          "## 可下派的题清单（NO-FALLBACK → owner 一行改）", ""]
    bare = [r for r in rep["refs"] if r["status"] == "NO-FALLBACK"]
    own = [r for r in bare if r.get("domain", "OWN") == "OWN"]
    ext = [r for r in bare if r.get("domain") == "EXTERNAL"]
    if own:
        for r in own:
            L.append("- 【本司】`%s:%d` — 裸写端口、作用域内无 env/argv 可改指；建议加一处读取"
                     "（环境变量或 CLI 参数）。片段：`%s`"
                     % (r["file"], r["line"], r["text"][:120]))
    if ext:
        for r in ext:
            L.append("- 【外部域·登记转 owner，本司不改写】`%s:%d` — 同上。片段：`%s`"
                     % (r["file"], r["line"], r["text"][:120]))
    if not bare:
        L.append("- 无（全部客户端引用均可在作用域内改指）")

    unknown = [r for r in rep["refs"] if r["status"] == "UNKNOWN"]
    L += ["", "## UNKNOWN 待人工读清单（env/端点在本文件但不在本调用点作用域）", ""]
    if unknown:
        for r in unknown:
            L.append("- `%s:%d` — %s；片段：`%s`"
                     % (r["file"], r["line"], r["steering"] or "-", r["text"][:120]))
    else:
        L.append("- 无")

    L += ["", "## 诚实注记", "",
          "- 静态启发式分类：探针判定按关键词（probe/alive/health/socket/connect/v1/models）——"
          "措辞变化的探针行可能被计入客户端面，反之亦然；NO-FALLBACK 行须人眼复核后再动 owner 代码。",
          "- 作用域判定按 AST 的 def 范围：同一函数（或模块顶层块）内的 env 读才算可改指；"
          "文件其他地方有 env 一律降 UNKNOWN，不冒充绿灯（T150 收紧）。",
          "- 作用域内可改指仍属静态证据：env 是否真的被该调用点消费需逐点读码确认，属改动前一步。",
          "- 本件不改任何 L1 面声明、不改 owner 代码；修复动作按行归各 owner 一行改；"
          "外部域行只登记派单（禁跨仓写）。"]
    return "\n".join(L) + "\n"


def cmd_scan(a):
    rep = scan(a.roots, a.port, a.ours)
    if a.json_out:
        d = os.path.dirname(a.json_out)
        if d:
            os.makedirs(d, exist_ok=True)
        with io.open(a.json_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rep, ensure_ascii=False, indent=2) + "\n")
    if a.md_out:
        d = os.path.dirname(a.md_out)
        if d:
            os.makedirs(d, exist_ok=True)
        with io.open(a.md_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(render_md(rep))
    print("L1-REF-CENSUS port=%s files=%d refs=%d %s verdict=%s" % (
        rep["port"], rep["files_scanned"], len(rep["refs"]),
        " ".join("%s=%d" % (k, v) for k, v in sorted(rep["counts"].items())),
        rep["verdict"]))
    for r in rep["refs"]:
        if r["status"] in ("NO-FALLBACK", "ROOT-MISSING", "UNREADABLE"):
            print("  %-13s %s:%s [%s] %s" % (r["status"], r["file"], r["line"],
                                             r.get("domain", "OWN"), r["text"][:90]))
    return 0 if rep["verdict"] != "ATTENTION" else 3


def cmd_selftest(_a):
    import tempfile
    tmp = tempfile.mkdtemp(prefix="l1census-")
    fixtures = {
        "client_env.py": (
            'import os\n'
            'ENDPOINT = os.environ.get("L1_ENDPOINT", "http://127.0.0.1:8090")\n'
            'def go():\n    return ENDPOINT + "/v1/chat/completions"\n'),
        "client_bare.py": (
            'import urllib.request\n'
            'URL = "http://127.0.0.1:8090/v1/chat/completions"\n'
            'def go():\n    return urllib.request.urlopen(URL)\n'),
        "probe_only.py": (
            'import socket\n'
            'def alive():\n'
            '    s = socket.socket()\n'
            '    s.connect(("127.0.0.1", 8090))\n'
            '    return True\n'),
        "doc_mention.py": (
            '"""Talks to the 8090 lane."""\n'
            '# 8090 lane is the brain\n'
            'X = 1\n'),
        "multi.py": (
            'PRIMARY = "http://127.0.0.1:8090"\n'
            'ALT = "http://127.0.0.1:11434"\n'
            'def post():\n    return PRIMARY\n'),
        "lane.ps1": (
            '$ep = $env:L1_ENDPOINT\n'
            'if (-not $ep) { $ep = "http://127.0.0.1:8090" }\n'
            'Invoke-RestMethod "$ep/v1/models"\n'),
    }
    for name, body in fixtures.items():
        with io.open(os.path.join(tmp, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)

    def statuses(rep):
        return sorted({os.path.basename(r["file"]): r["status"] for r in rep["refs"]}.items())

    S = []
    rep = scan([tmp], 8090)
    by = {}
    for r in rep["refs"]:
        by.setdefault(os.path.basename(r["file"]), set()).add(r["status"])
    S.append(("S1 env override -> FALLBACK-OK", by.get("client_env.py") == {"FALLBACK-OK"}))
    S.append(("S2 bare literal -> NO-FALLBACK", by.get("client_bare.py") == {"NO-FALLBACK"}))
    S.append(("S3 socket probe -> DIAGNOSTIC", by.get("probe_only.py") == {"DIAGNOSTIC"}))
    S.append(("S4 docstring+comment -> DOC-REF only", by.get("doc_mention.py") == {"DOC-REF"}))
    S.append(("S5 second endpoint -> FALLBACK-OK(MULTI)",
              by.get("multi.py") == {"FALLBACK-OK"}
              and all(r["steering"] == "MULTI-ENDPOINT(SCOPE)"
                      for r in rep["refs"] if os.path.basename(r["file"]) == "multi.py")))
    S.append(("S6 ps1 $env: -> FALLBACK-OK(ENV)",
              by.get("lane.ps1") == {"FALLBACK-OK"}
              and all(r["steering"].startswith("ENV:")
                      for r in rep["refs"] if os.path.basename(r["file"]) == "lane.ps1")))
    S.append(("S7 verdict ATTENTION with a bare client", rep["verdict"] == "ATTENTION"))
    S.append(("S8 counts consistent", sum(rep["counts"].values()) == len(rep["refs"])))

    os.remove(os.path.join(tmp, "client_bare.py"))
    rep2 = scan([tmp], 8090)
    S.append(("S9 bare client removed -> CENSUS-GREEN", rep2["verdict"] == "CENSUS-GREEN"))
    S.append(("S10 missing root reported not crashed",
              scan([os.path.join(tmp, "nope")], 8090)["refs"][0]["status"] == "ROOT-MISSING"))
    md = render_md(rep)
    S.append(("S11 md renders verdict + table header", "verdict: **ATTENTION**" in md
              and "| 文件 | 行 | 判定 |" in md))
    S.append(("S12 port param respected",
              scan([tmp], 11434)["counts"].get("NO-FALLBACK", 0) == 0))

    # S13-S15: role refinements (argv default / server launch line / selftest fixture)
    roles = {
        "argv.py": ('import argparse\n'
                    'ap = argparse.ArgumentParser()\n'
                    'ap.add_argument("--base-url", default="http://127.0.0.1:8090")\n'),
        "serve.ps1": '$argList = \'-m "{0}" --host 127.0.0.1 --port 8090\'\n',
        "fixture.py": ('def cmd_selftest():\n'
                       '    ep = "127.0.0.1:8090"\n'
                       '    return ep\n'),
    }
    tmp2 = tempfile.mkdtemp(prefix="l1census2-")
    for name, body in roles.items():
        with io.open(os.path.join(tmp2, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
    rep3 = scan([tmp2], 8090)
    by3 = {}
    for r in rep3["refs"]:
        by3.setdefault(os.path.basename(r["file"]), []).append(r)
    S.append(("S13 argv default -> FALLBACK-OK(ARGV)",
              [r["status"] for r in by3.get("argv.py", [])] == ["FALLBACK-OK"]
              and by3["argv.py"][0]["steering"] == "ARGV"))
    S.append(("S14 server launch line -> SERVER-SIDE",
              [r["status"] for r in by3.get("serve.ps1", [])] == ["SERVER-SIDE"]))
    S.append(("S15 selftest fixture -> FIXTURE",
              [r["status"] for r in by3.get("fixture.py", [])] == ["FIXTURE"]))
    S.append(("S16 refinements leave no NO-FALLBACK here", rep3["verdict"] == "CENSUS-GREEN"))

    # S17-S22: T150 scope tightening + domain attribution
    tmp3 = tempfile.mkdtemp(prefix="l1census3-")
    scoped = {
        "env_other_func.py": (
            'import os\n'
            'def other():\n'
            '    return os.environ.get("L1_ENDPOINT")\n'
            'def go():\n'
            '    return "http://127.0.0.1:8090/api/chat"\n'),
        "env_in_scope.py": (
            'import os\n'
            'def go():\n'
            '    ep = os.environ.get("L1_ENDPOINT", "http://127.0.0.1:8090")\n'
            '    return ep\n'),
        "multi_in_scope.py": (
            'def go():\n'
            '    primary = "http://127.0.0.1:8090/api"\n'
            '    alt = "http://127.0.0.1:11434/v1"\n'
            '    return primary\n'),
        "multi_other_func.py": (
            'ALT = "http://127.0.0.1:11434/v1"\n'
            'def go():\n'
            '    return "http://127.0.0.1:8090/api"\n'),
        "msg_only.py": (
            'def report(lane):\n'
            '    print("lane %s down on :8090 (label only)" % lane)\n'
            '    return {"lane-8090": 0}\n'),
    }
    for name, body in scoped.items():
        with io.open(os.path.join(tmp3, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
    rep4 = scan([tmp3], 8090, ours=tmp)      # tmp3 is outside "ours" -> EXTERNAL
    by4 = {}
    for r in rep4["refs"]:
        by4.setdefault(os.path.basename(r["file"]), []).append(r)
    S.append(("S17 env only in another function -> UNKNOWN",
              [r["status"] for r in by4.get("env_other_func.py", [])] == ["UNKNOWN"]
              and by4["env_other_func.py"][0]["steering"] == "ENV-FILE-ONLY:L1_ENDPOINT"))
    S.append(("S18 env consumed in own scope -> FALLBACK-OK(ENV)",
              [r["status"] for r in by4.get("env_in_scope.py", [])] == ["FALLBACK-OK"]
              and by4["env_in_scope.py"][0]["steering"].startswith("ENV:L1_ENDPOINT")))
    S.append(("S19 second endpoint in own scope -> FALLBACK-OK(MULTI)",
              [r["steering"] for r in by4.get("multi_in_scope.py", [])] == ["MULTI-ENDPOINT(SCOPE)"]))
    S.append(("S20 second endpoint elsewhere -> UNKNOWN",
              [r["status"] for r in by4.get("multi_other_func.py", [])] == ["UNKNOWN"]
              and by4["multi_other_func.py"][0]["steering"] == "ENDPOINT-ELSEWHERE-IN-FILE"))
    S.append(("S21 outside-repo rows carry domain=EXTERNAL",
              all(r.get("domain") == "EXTERNAL" for r in rep4["refs"] if r["line"])))
    S.append(("S22 UNKNOWN alone -> CENSUS-GREEN-WITH-UNKNOWN",
              rep4["verdict"] == "CENSUS-GREEN-WITH-UNKNOWN"))
    md4 = render_md(rep4)
    S.append(("S23 md renders UNKNOWN section", "## UNKNOWN 待人工读清单" in md4))
    rep5 = scan([tmp3], 8090, ours=tmp3)     # now inside "ours"
    S.append(("S24 ours=root marks rows OWN",
              all(r.get("domain") == "OWN" for r in rep5["refs"] if r["line"])))
    S.append(("S25 label/log mention -> MESSAGE-REF",
              [r["status"] for r in by4.get("msg_only.py", [])] == ["MESSAGE-REF", "MESSAGE-REF"]))
    with io.open(os.path.join(tmp3, "msg_key.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write('lane8090 = 1\nreport = {"lane8090": lane8090}\n')
    S.append(("S26 port glued in an identifier is not a reference",
              classify_file(os.path.join(tmp3, "msg_key.py"), 8090) == []))

    fails = [n for n, ok in S if not ok]
    for n, ok in S:
        print(("PASS " if ok else "FAIL ") + n)
    print("SELFTEST %d/%d %s" % (len(S) - len(fails), len(S),
                                 "PASS" if not fails else "FAIL " + ",".join(fails)))
    return 0 if not fails else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--roots", nargs="+",
                   default=["Tools", os.path.join("..", "..", "Tools")],
                   help="scan roots; default = this repo Tools + group Tools")
    s.add_argument("--port", type=int, default=8090)
    s.add_argument("--ours", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   help="this repo root; files outside it are reported as EXTERNAL")
    s.add_argument("--json-out")
    s.add_argument("--md-out")
    sub.add_parser("selftest")
    a = ap.parse_args(argv)
    return cmd_selftest(a) if a.cmd == "selftest" else cmd_scan(a)


if __name__ == "__main__":
    sys.exit(main())