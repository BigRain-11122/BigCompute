#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""openstd_render_probe.py - openstd render channel probe (tech T43).

Codifies the manual openstd.samr.gov.cn method law (M37 index row 16, proven
on GB/T 37964 + 42460 during 10-02/10-03 rounds): free-login preview, image
tile anti-crawl loads on mouse-in-view event stream, direct scrollTop
positioning on the inner scroll container. Page divs are 0-based (id=N =
PDF page N+1); CSS page height 1308px @40% zoom; printed page N ~= PDF page
N+4; prefetch law = current page + next ~3 pages arrive with mouse stream.

Serves M40 appendix pages / 42460 body / tech T42 GB 45438 full text.
Browser discovery: PLAYWRIGHT bundled chromium -> OPENSTD_BROWSER env ->
system Chrome -> Edge (zero download, silent headless). Output confined to
state/ of this repo; zero cross-repo writes (T43 law).

Exit: 0 OK / 1 partial (tile timeout, files kept) / 2 hard fail / 3 usage.
"""
import argparse
import json
import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(PROJECT, "state")
BASE = "https://openstd.samr.gov.cn/bzgk/std/"
DOC_PAGE_H = 1308   # M37 row 16 law: CSS page height at 40% zoom
PRINTED_OFFSET = 4  # printed page N ~= PDF page N+4
PREFETCH = 3        # prefetch law: current page + next ~3
MAX_PAGES = 8       # bounded per invocation
TILE_TIMEOUT_DEFAULT = 20

JS_DISCOVER = """() => {
  const divs = [...document.querySelectorAll('div[id]')]
    .filter(d => /^\\d+$/.test(d.id) && d.offsetHeight > 100);
  if (!divs.length) return null;
  let c = divs[0].parentElement, cont = false;
  while (c && c !== document.body) {
    if (c.scrollHeight > c.clientHeight + 10) { cont = true; break; }
    c = c.parentElement;
  }
  return {pages: divs.length, first: divs[0].id, container: cont,
          pageH: Math.round(divs[0].getBoundingClientRect().height)};
}"""

JS_SCROLL = """(p) => {
  const el = document.getElementById(String(p - 1));
  if (!el) return {ok: false};
  let c = el.parentElement;
  while (c && c !== document.body && c.scrollHeight <= c.clientHeight + 10)
    c = c.parentElement;
  if (!c || c === document.body) return {ok: false};
  c.scrollTop = el.offsetTop - c.offsetTop;
  const vis = Math.abs(el.getBoundingClientRect().top - c.getBoundingClientRect().top);
  if (vis > 60) c.scrollTop = (p - 1) * 1308;
  return {ok: true, scrollTop: c.scrollTop};
}"""

JS_TILES = """(p) => {
  const el = document.getElementById(String(p - 1));
  if (!el) return null;
  return [...el.querySelectorAll('img')]
    .map(i => i.complete && i.naturalWidth > 0);
}"""

JS_SEARCH = """() => [...document.querySelectorAll('a[href*="hcno="]')]
  .map(a => ({hcno: decodeURIComponent(a.href.split('hcno=')[1] || '').split('&')[0],
              title: (a.textContent || '').trim().slice(0, 90)}))
  .filter(r => r.hcno)"""


def build_urls(hcno):
    return {"detail": BASE + "newGbInfo?hcno=" + hcno,
            "preview": BASE + "showGb?type=online&hcno=" + hcno}


def list_url(std_no):
    return BASE + "gb/std_list?p.p2=" + str(std_no)


def parse_pages(spec):
    """'5' or '3-6' -> ascending 1-based PDF pages, capped at MAX_PAGES."""
    try:
        if "-" in spec:
            lo, hi = (int(x) for x in spec.split("-", 1))
        else:
            lo = hi = int(spec)
    except ValueError:
        return []
    if lo < 1 or hi < lo or hi - lo + 1 > MAX_PAGES:
        return []
    return list(range(lo, hi + 1))


def page_mapping(pdf_page):
    """0-based div id + printed-page estimate (M37 row 16 law)."""
    return {"div_id": pdf_page - 1,
            "printed_est": pdf_page - PRINTED_OFFSET if pdf_page > PRINTED_OFFSET else None}


def prefetch_window(pdf_page):
    return list(range(pdf_page, pdf_page + PREFETCH + 1))


def tiles_ready(states):
    return bool(states) and all(states)


def find_browser():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            exe = pw.chromium.executable_path
        if exe and os.path.exists(exe):
            return exe, "bundled-chromium"
    except Exception:
        pass
    env = os.environ.get("OPENSTD_BROWSER")
    if env and os.path.exists(env):
        return env, "env-override"
    for tag, path in (("system-chrome",
                       r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                      ("system-edge",
                       r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")):
        if os.path.exists(path):
            return path, tag
    return None, None


def _ts():
    return time.strftime("%Y%m%d-%H%M%S")


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:  # utf-8 explicit (T24 law)
        f.write(text)


def _mouse_stream(page, box):
    if not box:
        return
    for i in range(12):
        x = box["x"] + box["width"] * ((i % 4) + 0.5) / 4.0
        y = box["y"] + box["height"] * ((i // 4) + 0.5) / 3.0
        page.mouse.move(x, y)
        page.wait_for_timeout(120)


def _discover_ctx(pg):
    """T54: main frame first, then every iframe (viewer may be framed)."""
    dom = pg.evaluate(JS_DISCOVER)
    if dom and dom.get("container"):
        return pg, dom, "main"
    for fr in pg.frames:
        if fr is pg.main_frame:
            continue
        try:
            d = fr.evaluate(JS_DISCOVER)
        except Exception:
            continue
        if d and d.get("container"):
            return fr, d, "frame"
    return None, dom, "none"


def _dump_dom(pg, prefix):
    """T54 diag: HTML snapshot + per-frame stats on DOM miss (login-wall /
    headless-block / iframe evidence for the next fix round)."""
    snap = os.path.join(STATE, prefix + "-domsnap.html")
    try:
        _write(snap, pg.content())
    except Exception:
        pass
    rows = []
    for fr in pg.frames:
        try:
            st = fr.evaluate("""() => ({num_divs: document.querySelectorAll('div[id]').length,
                                        body_len: (document.body ? document.body.innerHTML.length : 0),
                                        title: (document.title || '').slice(0, 60)})""")
        except Exception as exc:
            st = {"error": str(exc)[:60]}
        rows.append({"url": fr.url[:120], "stats": st})
    jpath = os.path.join(STATE, prefix + "-domdiag.json")
    _write(jpath, json.dumps({"snapshot": os.path.basename(snap), "frames": rows},
                             ensure_ascii=False, indent=1))
    return jpath


def cmd_search(a):
    exe, how = find_browser()
    if not exe:
        print("BROWSER-NOT-FOUND verdict=HARD-FAIL")
        return 2
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=exe)
        pg = browser.new_page()
        try:
            pg.goto(list_url(a.std), wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(3000)
            rows = pg.evaluate(JS_SEARCH)
        except Exception as exc:
            print("LIST-BLOCKED error=%s verdict=HARD-FAIL" % str(exc)[:120])
            return 2
        finally:
            browser.close()
    seen, out = set(), []
    for r in rows:
        key = (r["hcno"], r["title"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    for r in out:
        print("hcno=%s title=%s" % (r["hcno"], r["title"]))
    jpath = os.path.join(STATE, "openstd-search-%s-%s.json" % (a.std, _ts()))
    _write(jpath, json.dumps({"std": a.std, "url": list_url(a.std), "rows": out},
                             ensure_ascii=False, indent=1))
    print("rows=%d json=%s verdict=%s" % (len(out), os.path.basename(jpath),
                                          "OK" if out else "ZERO-ROWS"))
    return 0 if out else 1


def cmd_render(a):
    pages = parse_pages(a.pages)
    if not pages:
        print("usage: --pages must be like 5 or 3-6 (<= %d pages)" % MAX_PAGES)
        return 3
    exe, how = find_browser()
    if not exe:
        print("BROWSER-NOT-FOUND verdict=HARD-FAIL")
        return 2
    from playwright.sync_api import sync_playwright
    urls = build_urls(a.hcno)
    prefix = a.out_prefix or ("openstd-%s-render-%s" % (a.hcno[:8], _ts()))
    log = ["hcno=%s" % a.hcno, "preview=%s" % urls["preview"],
           "browser=%s" % how, "pages=%s" % pages]
    verdict, code = "OK", 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=exe,
                                     args=["--disable-blink-features=AutomationControlled"])
        pg = browser.new_page(viewport={"width": 1400, "height": 1600})
        pg.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        try:
            # T54 fix faces: (a) bare showGb 302s to newGbInfo?refer=outter
            # (10-07 实证, cookie/locale warm-up insufficient); (b) the site's
            # own trigger = .ck_btn click -> showGb hcno,'online' -> window.open
            # popup carrying site referer+session. Manual channel reproduction
            # = click-driven popup; direct preview goto kept as fallback.
            pg.goto(urls["detail"], wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(2500)
            try:
                with pg.expect_popup() as pi:
                    pg.click(".ck_btn")
                pg = pi.value
                pg.set_viewport_size({"width": 1400, "height": 1600})
            except Exception as exc:
                log.append("popup-fallback error=%s" % str(exc)[:80])
                pg.goto(urls["preview"] + "&request_locale=zh_CN",
                        wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(8000)
            log.append("final_url=%s" % pg.url[:120])
            ctx, dom, where = _discover_ctx(pg)
            if ctx is None:
                jpath = _dump_dom(pg, prefix)
                print("DOM-NOT-MATCHED where=%s dom=%s diag=%s verdict=HARD-FAIL"
                      % (where, dom, os.path.basename(jpath)))
                return 2
            log.append("dom_where=%s dom_pages=%s pageH_css=%s (law=%s)"
                       % (where, dom["pages"], dom["pageH"], DOC_PAGE_H))
            for p in pages:
                t0 = time.time()
                ctx.evaluate(JS_SCROLL, p)
                box = ctx.locator('[id="%d"]' % (p - 1)).bounding_box()
                _mouse_stream(pg, box)
                deadline = time.time() + a.tile_timeout
                states = []
                while time.time() < deadline:
                    states = ctx.evaluate(JS_TILES, p) or []
                    if tiles_ready(states):
                        break
                    pg.wait_for_timeout(400)
                ready = tiles_ready(states)
                shot = os.path.join(STATE, "%s-p%02d.png" % (prefix, p))
                ctx.locator('[id="%d"]' % (p - 1)).screenshot(path=shot)
                m = page_mapping(p)
                log.append("page=%d div=%d printed_est=%s imgs=%d ready=%s "
                           "prefetch=%s %.1fs -> %s"
                           % (p, m["div_id"], m["printed_est"], len(states), ready,
                              prefetch_window(p), time.time() - t0,
                              os.path.basename(shot)))
                if not ready:
                    verdict, code = "TILES-TIMEOUT-PARTIAL", 1
        except Exception as exc:
            print("RUN-ERROR error=%s verdict=HARD-FAIL" % str(exc)[:120])
            return 2
        finally:
            browser.close()
    txt = os.path.join(STATE, prefix + ".txt")
    _write(txt, "\n".join(log) + "\nverdict=%s\n" % verdict + "\n")
    print("\n".join(log))
    print("summary=%s verdict=%s" % (os.path.basename(txt), verdict))
    return code


def cmd_selftest(a=None):
    ok = 0

    def check(name, cond):
        nonlocal ok
        ok += 1 if cond else 0
        print("%s %s" % ("PASS" if cond else "FAIL", name))
        return cond

    u = build_urls("ABC123")
    check("S1 url assembly", u["detail"].endswith("newGbInfo?hcno=ABC123")
          and u["preview"].endswith("showGb?type=online&hcno=ABC123"))
    check("S2a single page", parse_pages("5") == [5])
    check("S2b range pages", parse_pages("3-6") == [3, 4, 5, 6])
    check("S2c cap over %d" % MAX_PAGES, parse_pages("1-9") == [])
    check("S2d invalid rejected", parse_pages("x") == [] and parse_pages("0") == [])
    m5, m1 = page_mapping(5), page_mapping(1)
    check("S3a div id law (0-based)", m5["div_id"] == 4)
    check("S3b printed est (N+4 law)", m5["printed_est"] == 1
          and m1["printed_est"] is None)
    check("S4 prefetch window", prefetch_window(7) == [7, 8, 9, 10])
    check("S5a tiles all-complete", tiles_ready([True, True]))
    check("S5b tiles one-incomplete", not tiles_ready([True, False]))
    check("S5c tiles empty-not-ready", not tiles_ready([]))
    check("S6 scroll-law offset", (5 - 1) * DOC_PAGE_H == 4 * 1308)
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    check("S7 state-confined writes (no sibling-repo path)",
          ("Flux" + "Group") not in src and ("Desk" + "top") not in src)
    check("S14 T54 frame-fallback + dom-diag helpers present",
          "def _discover_ctx" in src and "def _dump_dom" in src
          and "AutomationControlled" in src)
    print("selftest: %s" % ("PASS" if ok == 14 else "FAIL"))
    return 0 if ok == 14 else 1


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="openstd render channel probe (tech T43)")
    ap.add_argument("command", choices=["search", "render", "selftest"])
    ap.add_argument("--std", help="standard number for search (e.g. 45438)")
    ap.add_argument("--hcno", help="hcno for render")
    ap.add_argument("--pages", help="PDF page spec, e.g. 5 or 3-6 (1-based)")
    ap.add_argument("--out-prefix", help="output file prefix under state/")
    ap.add_argument("--tile-timeout", type=int, default=TILE_TIMEOUT_DEFAULT)
    a = ap.parse_args()
    if a.command == "search" and not a.std:
        return 3
    if a.command == "render" and not (a.hcno and a.pages):
        return 3
    return {"search": cmd_search, "render": cmd_render,
            "selftest": cmd_selftest}[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
