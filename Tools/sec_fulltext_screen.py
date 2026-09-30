#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SEC 申报全文利用率筛件（R-20260930-compute-industry-benchmark-evidence §十 承接·E44）

通道纪律（tech T36·SKILL §4 先声明后访问律执法件）：
- data.sec.gov（submissions/XBRL 接口·官方不受墙域·§九实证）= 申报元数据唯一入口
- www.sec.gov/Archives 全文 = 本地声明 UA 单试通道（SEC fair-access 惯例：UA 声明实体+联系方式）
- efts.sec.gov FTS = 跨库反查通道（找任何一家披露官方利用率 % 的公司=R-C1 唯一剩余源级锚径）
- 触墙 403 → 判负止损（冷却 >=10min·同通道复抓 <=1·禁无 UA 裸抓·SKILL §4 同律）

用法：
  python Tools/sec_fulltext_screen.py screen --cik 0001513845 --form 20-F [--kw utiliz]
  python Tools/sec_fulltext_screen.py fts --q "data center utilization"
  python Tools/sec_fulltext_screen.py selftest
"""
import argparse
import io
import json
import re
import sys
import urllib.request
import urllib.error

UA = "FluxGroup BigCompute Research research-contact@bigcompute.local (SEC fair-access declared UA)"
KW_DEFAULT = "utiliz"
CTX = 120
TIMEOUT = 30


def _get(url: str):
    """声明 UA 单试 GET。返回 (status, bytes)。403=墙。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # 网络层异常按墙级止损处理
        print(f"NET-ERROR {type(e).__name__}: {e}")
        return -1, b""


def _strip_html(raw: bytes) -> str:
    txt = raw.decode("utf-8", errors="replace")
    txt = re.sub(r"(?is)<(script|style).*?</\1>", " ", txt)
    # 块级标签转换行边界，防跨段粘连
    txt = re.sub(r"(?i)</?(p|div|tr|td|th|li|h[1-6]|br)[^>]*>", "\n", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"&nbsp;?", " ", txt)
    txt = re.sub(r"&amp;", "&", txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    return txt


def _find_filings(sub: dict, form_prefix: str, limit: int = 3):
    rec = sub.get("filings", {}).get("recent", {})
    out = []
    for form, acc, doc, date in zip(
        rec.get("form", []), rec.get("accessionNumber", []),
        rec.get("primaryDocument", []), rec.get("filingDate", []),
    ):
        if form.upper().startswith(form_prefix.upper()):
            out.append({"form": form, "accession": acc, "primary": doc, "date": date})
            if len(out) >= limit:
                break
    return out


def screen(cik: str, form: str, kw: str, limit: int = 3) -> int:
    cik10 = cik.strip().zfill(10)
    st, body = _get(f"https://data.sec.gov/submissions/CIK{cik10}.json")
    if st != 200:
        print(f"VERDICT=META-FAIL status={st} domain=data.sec.gov")
        return 3
    sub = json.loads(body.decode("utf-8"))
    name = sub.get("name", "?")
    fils = _find_filings(sub, form, limit=limit)
    if not fils:
        print(f"VERDICT=NO-FILING cik={cik10} name={name} form~{form}")
        return 1
    print(f"TARGET name={name} filings={len(fils)} kw={kw!r}")
    cik_int = cik10.lstrip("0") or "0"
    rc, grand, pct_all = 0, 0, []
    for f0 in fils:
        acc_nd = f0["accession"].replace("-", "")
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nd}/{f0['primary']}"
        print(f"DOC form={f0['form']} date={f0['date']} accession={f0['accession']} {doc_url}")
        st, raw = _get(doc_url)
        if st != 200:
            print(f"  VERDICT=WALL status={st} domain=www.sec.gov (declared-UA single-shot judged)")
            return 3
        txt = _strip_html(raw)
        low = txt.lower()
        hits, i = [], 0
        while True:
            j = low.find(kw.lower(), i)
            if j < 0:
                break
            seg = txt[max(0, j - CTX): j + CTX].replace("\n", " ")
            pct = re.findall(r"\d{1,3}(?:\.\d+)?\s?%", seg)
            hits.append({"ctx": seg.strip(), "pct_nearby": pct})
            i = j + 1
        print(f"  SCREEN n_hits={len(hits)} doc_chars={len(txt)}")
        for h in hits[:40]:
            print(f"    - ...{h['ctx']}...")
            if h["pct_nearby"]:
                print(f"      PCT-NEARBY {h['pct_nearby']}")
                pct_all += h["pct_nearby"]
        grand += len(hits)
        if len(hits) < 40:
            continue
    print(f"VERDICT={'HITS' if grand else 'ZERO-HITS'} total_hits={grand} pct_nearby_total={len(pct_all)} {pct_all[:10]}")
    return rc


def fts(q: str) -> int:
    url = "https://efts.sec.gov/LATEST/search-index?q=" + urllib.request.quote(q) + "&startdt=2001-01-01"
    st, body = _get(url)
    if st != 200:
        print(f"VERDICT=WALL status={st} domain=efts.sec.gov (declared-UA single-shot judged)")
        return 3
    data = json.loads(body.decode("utf-8"))
    hits = data.get("hits", {}).get("hits", [])
    print(f"FTS q={q!r} total={data.get('hits', {}).get('total', {}).get('value', len(hits))}")
    for h in hits[:10]:
        s = h.get("_source", {})
        print(f"  - {s.get('file_date')} {s.get('display_names')} {s.get('adsh')}")
    print(f"VERDICT={'FTS-OK' if st == 200 else 'FTS-FAIL'}")
    return 0


def selftest() -> int:
    ok = 0
    # S1 表格配对：form 前缀匹配取最新在前假设
    sub = {"filings": {"recent": {
        "form": ["20-F/A", "10-K", "20-F"], "accessionNumber": ["A-1", "B-2", "C-3"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm"], "filingDate": ["2026-01-01", "2025-02-02", "2024-03-03"],
    }}}
    f = _find_filings(sub, "20-F")
    assert f and f[0]["form"] == "20-F/A" and len(f) == 2, f
    ok += 1
    # S2 HTML 剥离+段落边界
    t = _strip_html(b"<p>Data center <b>utilization</b> was 78%.</p><script>x()</script>")
    assert "utilization" in t and "78%" in t and "x()" not in t, t
    ok += 1
    # S3 命中与近邻百分比提取（含无百分比面）
    low = t.lower()
    j = low.find("utiliz")
    seg = t[max(0, j - CTX): j + CTX]
    assert re.findall(r"\d{1,3}(?:\.\d+)?\s?%", seg) == ["78%"], seg
    ok += 1
    # S4 访问号去杠 URL 组装
    acc_nd = "0001513845-26-000001".replace("-", "")
    assert acc_nd == "000151384526000001" and len(acc_nd) == 18
    ok += 1
    # S5 声明 UA 在位（先声明后访问律结构断言）
    assert "bigcompute" in UA.lower() and "declared" in UA.lower(), UA
    ok += 1
    print(f"SELFTEST {ok}/5 PASS")
    return 0 if ok == 5 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="screen", choices=["screen", "fts", "selftest"])
    ap.add_argument("--cik", default="0001513845")
    ap.add_argument("--form", default="20-F")
    ap.add_argument("--kw", default=KW_DEFAULT)
    ap.add_argument("--q", default='"data center utilization"')
    ap.add_argument("--limit", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "fts":
        return fts(a.q)
    return screen(a.cik, a.form, a.kw, a.limit)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.exit(main())
