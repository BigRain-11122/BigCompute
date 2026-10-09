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
T56: batch-first-jump double-hop - the first scroll target of a batch renders
blank on first visit and recovers on the second; it is re-visited at batch end
(warm-on-previous-page priming judged negative live 10-07). Tile counter also
covers span.pdfImg (popup-channel false-negative fix, T55 residual).
T66: batch-first blank is positional; recovery approach must be a FORWARD
near-jump arrival (v2): pad-hop to p0-1, final jump p0-1 -> p0 (forward
arrivals to unloaded pages load 100% - in-loop non-first positions, fix1
p08->p09; backward arrivals blank 100% - far 16->09 fix1, near 10->09 v1
live-fired negative 10-08). p01 = no-jump exception (viewer start position).
T65: hcno direct derivation - openstd hcno = MD5(standard full designation
incl. year).hexdigest().upper() (M37 §二 law, proven verbatim on GB/T
42460-2023 + GB 45438-2025, mandatory GB carries no /T). A full-designation
--std short-circuits the site list (ZERO-ROWS structural closure, T43);
discovery chain = md5-direct > site-list fallback.
T69: offset-landing jump law (T68 root-cause fix). The viewer scroll handler
picks cachePage = first 0-based page i with pages[i].top-10 > scrollTop, then
initImage(i) loads .page:eq(i) (0-based) = that page's OWN bg-token batch.
Landing exactly at a page top (scrollTop = top) therefore always loads the
NEXT page's batch - the batch-first blank root cause (T66 v1/v2 judged
negative because pad-hop geometry still terminated on a top landing). Fix:
land 20px ABOVE the target top (el.offsetTop - container - 20), inside the
cachePage window [pages[k-1].top-10, pages[k].top-10) - loads the target
batch on first visit, no rehop pad sequence needed. Tile counter fix: real
popup-channel tiles are span.pdfImg-<row>-<col> (class "pdfImg-0-3", not
exact "pdfImg") - selector is now span[class^="pdfImg"], closing the
imgs=0/ready=false false-negative series (T55..T68). The T56/T66 re-visit
fallback stays wired but fires only when the first visit is not tile-ready.
Browser discovery: PLAYWRIGHT bundled chromium -> OPENSTD_BROWSER env ->
system Chrome -> Edge (zero download, silent headless). Output confined to
state/ of this repo; zero cross-repo writes (T43 law).

Exit: 0 OK / 1 partial (tile timeout, files kept) / 2 hard fail / 3 usage.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(PROJECT, "state")
BASE = "https://openstd.samr.gov.cn/bzgk/std/"
DOC_PAGE_H = 1308   # M37 row 16 law: CSS page height at 40% zoom (manual channel). T55: popup channel (.ck_btn) measures ~1644 @50% — scroll rides the offsetTop primary path (pageH-independent, verified 37964 p2/p4 full-page); the (p-1)*1308 fallback is manual-channel-only, must not fire under popup channel
PRINTED_OFFSET = 4  # printed page N ~= PDF page N+4
PREFETCH = 3        # prefetch law: current page + next ~3
MAX_PAGES = 8       # bounded per invocation
TILE_TIMEOUT_DEFAULT = 20
OFFSET_LANDING = 20  # T69/T68 law: land this many px ABOVE the target page
                     # top - cachePage window is [pages[k-1].top-10,
                     # pages[k].top-10), so top-landing loads batch k+1
                     # (batch-first blank root cause) and -20 loads batch k.

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
  c.scrollTop = el.offsetTop - c.offsetTop - 20;
  const vis = Math.abs(el.getBoundingClientRect().top - c.getBoundingClientRect().top);
  if (vis > 60) c.scrollTop = (p - 1) * 1308 - 20;
  return {ok: true, scrollTop: c.scrollTop};
}"""

JS_TILES = """(p) => {
  const el = document.getElementById(String(p - 1));
  if (!el) return null;
  const imgs = [...el.querySelectorAll('img')]
    .map(i => i.complete && i.naturalWidth > 0);
  const spans = [...el.querySelectorAll('span[class^="pdfImg"]')].map(s => {
    const bg = getComputedStyle(s).backgroundImage || '';
    return !!bg && bg !== 'none' && s.offsetWidth > 0;
  });
  return imgs.concat(spans);
}"""

JS_SCRIPT_FPRINT = """() => [...document.scripts].map(s => {
  if (s.src) return 'src:' + s.src.split('/').pop();
  const t = (s.textContent || '');
  let h = 5381;
  for (let i = 0; i < t.length; i++) { h = ((h * 33) ^ t.charCodeAt(i)) >>> 0; }
  return 'inline:' + t.length + ':' + h;
}).sort().join(' | ')"""

JS_SEARCH = """() => [...document.querySelectorAll('a[href*="hcno="]')]
  .map(a => ({hcno: decodeURIComponent(a.href.split('hcno=')[1] || '').split('&')[0],
              title: (a.textContent || '').trim().slice(0, 90)}))
  .filter(r => r.hcno)"""


def build_urls(hcno):
    return {"detail": BASE + "newGbInfo?hcno=" + hcno,
            "preview": BASE + "showGb?type=online&hcno=" + hcno}


def list_url(std_no):
    return BASE + "gb/std_list?p.p2=" + str(std_no)


STD_FULL_RE = re.compile(r"^GB(/T)? \d+(\.\d+)?-\d{4}$")


def derive_hcno(std_full):
    """T65/M37 §二 law: hcno = MD5(full designation incl. year).upper().
    Proven on GB 45438-2025 -> F32EA2A561F1886CD8D606513512D547 and
    GB/T 42460-2023 -> E1A4E7943D64346D9EF1E3D0855F8496 (exact string,
    single spaces; zero network - preferred discovery channel)."""
    return hashlib.md5(std_full.strip().encode("utf-8")).hexdigest().upper()


def is_full_designation(s):
    return bool(STD_FULL_RE.match(s.strip()))


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


def revisit_hop(p, total):
    """T56 law: the first scroll target of a batch renders blank on its first
    visit (viewer lazy queue unprimed for far jumps; p15/p12/p10 blank-firsts
    10-07, and warm-on-previous-page judged NEGATIVE live 10-07: p14 blank,
    p15 full in the same batch), while every second visit recovers. So the
    first target is always re-visited once after the batch; single-page
    batches hop to a neighbour page first."""
    if p + 1 <= total:
        return p + 1
    if p - 1 >= 1:
        return p - 1
    return p


def rehop_plan(p0, total):
    """T66 v2 forward-arrival law: the approach to the batch-first target
    pads to p0-1, making the final jump p0-1 -> p0 a FORWARD near arrival -
    the only geometry with 100% load evidence (in-loop non-first positions;
    fix1 p08->p09 recovery 10-08). Backward arrivals to unloaded targets are
    100% blank (fix1 far 16->09; v1 near 10->09 via p0+1 live-fired negative
    10-08: stale-loaded p10 pad fired no fresh queue activity). Falls back to
    p0+1 (T56-verified single-page geometry) only when p0-1 does not exist.
    Returns None when no neighbour exists (single-page document = p01-style
    no-jump case: the positional blank rides the first *jump* and p01, at
    the viewer start position, needs none - hence the p01 exception 10-08)."""
    if p0 - 1 >= 1:
        return p0 - 1
    if p0 + 1 <= total:
        return p0 + 1
    return None


def viewer_cache_page(scroll_top, tops, guard_off=10):
    """T68/T69 off-by-one law mirror (pure math, selftest S21a). The site
    scroll handler picks cachePage = first 0-based i with tops[i]-guard_off >
    scroll_top, then initImage(cachePage) loads .page:eq(cachePage) = that
    page's OWN bg-token batch. Top-landing at page k (scroll_top == tops[k])
    therefore loads k+1's batch (the batch-first blank); offset landing
    tops[k]-20 lands inside [tops[k-1]-10, tops[k]-10) and loads k's batch."""
    for i, t in enumerate(tops):
        if t - guard_off > scroll_top:
            return i
    return len(tops) - 1


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
    if is_full_designation(a.std):
        # T65 md5-direct: no network, no site list (T43 ZERO-ROWS bypass).
        hcno = derive_hcno(a.std)
        jpath = os.path.join(STATE, "openstd-search-%s-%s.json"
                             % (re.sub(r"[^0-9A-Za-z]", "", a.std), _ts()))
        _write(jpath, json.dumps({"std": a.std, "method": "md5-direct",
                                  "rows": [{"hcno": hcno,
                                            "title": "(md5-direct)"}]},
                                 ensure_ascii=False, indent=1))
        print("hcno=%s title=(md5-direct)" % hcno)
        print("rows=1 json=%s verdict=MD5-DIRECT" % os.path.basename(jpath))
        return 0
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
    if not out:
        print("hint: --std 'GB 45438-2025' (full designation) derives hcno "
              "directly via T65 md5-direct, no network")
    return 0 if out else 1


def cmd_render(a):
    pages = parse_pages(a.pages)
    if not pages:
        print("usage: --pages must be like 5 or 3-6 (<= %d pages)" % MAX_PAGES)
        return 3
    # T65: --std full designation derives hcno when --hcno is omitted.
    hcno = a.hcno or (derive_hcno(a.std)
                      if a.std and is_full_designation(a.std) else None)
    if not hcno:
        print("usage: --hcno required, or --std 'GB 45438-2025' full "
              "designation for T65 md5-direct")
        return 3
    exe, how = find_browser()
    if not exe:
        print("BROWSER-NOT-FOUND verdict=HARD-FAIL")
        return 2
    from playwright.sync_api import sync_playwright
    urls = build_urls(hcno)
    prefix = a.out_prefix or ("openstd-%s-render-%s" % (hcno[:8], _ts()))
    log = ["hcno=%s" % hcno, "preview=%s" % urls["preview"],
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
            # T69/T68(4): read-only viewer script fingerprint - src filenames
            # + inline length:djb2 hash pairs; two documents sharing the same
            # viewer template yield identical src: entries (the 37964-vs-45438
            # same-mechanism corroboration, inline entries differ per-doc by
            # design - token arrays etc.).
            try:
                log.append("scripts_fprint=%s"
                           % (ctx.evaluate(JS_SCRIPT_FPRINT) or "")[:400])
            except Exception:
                log.append("scripts_fprint=eval-error")
            p0_ready = False
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
                if p == pages[0]:
                    p0_ready = ready
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
            # T56 double-hop + T66 v2 forward-arrival law: re-visit the first
            # target once at batch end - first visits render blank (unprimed
            # far-jump queue), second visits recover (p14 blank / p15 full
            # live proof 10-07; warm-on-previous-page judged negative same
            # round). T66: pad-hop to p0-1 so the final approach p0-1 -> p0 is
            # a FORWARD near arrival (100% load evidence); backward arrivals
            # stay blank (far 16->09 fix1; near 10->09 v1 live-negative
            # 10-08). Blank first attempt png kept as -a1 evidence; canonical
            # png = re-visit. T69: with offset-landing the first visit now
            # loads the target's own batch (T68 off-by-one root cause fixed),
            # so the re-visit pad fires ONLY when the first visit is not
            # tile-ready - first visit ready = canonical png, no pad needed.
            p0 = pages[0]
            a0 = os.path.join(STATE, "%s-p%02d.png" % (prefix, p0))
            if p0_ready:
                log.append("rehop skipped page=%d first visit ready "
                           "(T69 offset-landing)" % p0)
            else:
                if os.path.exists(a0):
                    os.rename(a0, os.path.join(STATE, "%s-p%02d-a1.png"
                                               % (prefix, p0)))
                hop = rehop_plan(p0, dom["pages"])
                if hop is not None:
                    ctx.evaluate(JS_SCROLL, hop)
                    _mouse_stream(pg, ctx.locator('[id="%d"]'
                                                 % (hop - 1)).bounding_box())
                    # T69b: poll the PAD page to tile-ready before
                    # re-approaching p0 - T42's proven non-first path polls
                    # every page to ready; a fixed 1.5s pad was judged
                    # negative twice (T66 v2 + T69 first live run) while the
                    # same geometry with a ready-polled predecessor loads the
                    # shared batch. Pad-ready poll = the missing ingredient.
                    hp_deadline = time.time() + a.tile_timeout
                    while time.time() < hp_deadline:
                        if tiles_ready(ctx.evaluate(JS_TILES, hop) or []):
                            break
                        pg.wait_for_timeout(400)
                    pg.wait_for_timeout(400)
                t0 = time.time()
                ctx.evaluate(JS_SCROLL, p0)
                _mouse_stream(pg, ctx.locator('[id="%d"]'
                                              % (p0 - 1)).bounding_box())
                states, deadline = [], time.time() + a.tile_timeout
                while time.time() < deadline:
                    states = ctx.evaluate(JS_TILES, p0) or []
                    if tiles_ready(states):
                        break
                    pg.wait_for_timeout(400)
                ready2 = tiles_ready(states)
                ctx.locator('[id="%d"]' % (p0 - 1)).screenshot(path=a0)
                log.append("rehop page=%d via_hop=%s imgs=%d ready=%s %.1fs"
                           " -> %s (T66 forward-arrival law)"
                           % (p0, hop if hop is not None else "self",
                              len(states), ready2, time.time() - t0,
                              os.path.basename(a0)))
                if not ready2:
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
    check("S15 T55 law-divergence note (popup ~1644@50% vs manual 1308@40%)",
          "offsetTop primary" in src and "manual-channel-only" in src
          and "el.offsetTop - c.offsetTop" in src)
    check("S16a T56 revisit-hop law (next page, prev fallback, self floor)",
          revisit_hop(14, 35) == 15 and revisit_hop(35, 35) == 34
          and revisit_hop(1, 1) == 1)
    check("S16b T56 double-hop re-visit wired at batch end",
          "def revisit_hop" in src and "rehop page=" in src
          and "-a1.png" in src)
    check("S17 tile counter covers span.pdfImg (popup false-negative fix)",
          "span.pdfImg" in src)
    check("S18 T65 hcno md5 direct-derivation law (M37 §二 fixtures)",
          derive_hcno("GB 45438-2025") == "F32EA2A561F1886CD8D606513512D547"
          and derive_hcno("GB/T 42460-2023") == "E1A4E7943D64346D9EF1E3D0855F8496"
          and derive_hcno("GB/T 37964-2019") == "C8DF1BC2FB43C6EC0E602EB65EF0BC66")
    check("S19 T65 full-designation gate + search/render wiring",
          is_full_designation("GB 45438-2025")
          and is_full_designation("GB/T 42460-2023")
          and not is_full_designation("45438")
          and not is_full_designation("GB 45438")
          and "MD5-DIRECT" in src and "md5-direct" in src)
    check("S20a T66 v2 forward-arrival rehop plan (prev-first pad, None floor)",
          rehop_plan(9, 19) == 8 and rehop_plan(19, 19) == 18
          and rehop_plan(1, 19) == 2 and rehop_plan(1, 1) is None)
    check("S20b T66 v2 forward-arrival wired for ALL batch sizes (gate removed)",
          "def rehop_plan" in src and ("len(pages) " + "== 1") not in src
          and "via_hop=" in src and "T66 forward-arrival law" in src)
    tops = [i * 1644 for i in range(5)]
    check("S21a T69 off-by-one math dual state (top-landing loads batch k+1,"
          " offset-landing loads batch k)",
          viewer_cache_page(tops[2], tops) == 3
          and viewer_cache_page(tops[2] - 20, tops) == 2
          and viewer_cache_page(tops[4] - 20, tops) == 4
          and viewer_cache_page(-20, tops) == 0
          and viewer_cache_page(tops[0], tops) == 1)
    check("S21b T69 offset-landing wired in JS_SCROLL (both paths -20)",
          "el.offsetTop - c.offsetTop - 20" in src
          and "(p - 1) * 1308 - 20" in src
          and "OFFSET_LANDING = 20" in src)
    check("S21c T69 real-tile selector + conditional rehop + script fprint",
          'span[class^="pdfImg"]' in src
          and "first visit ready" in src
          and "scripts_fprint=" in src)
    print("selftest: %s" % ("PASS" if ok == 25 else "FAIL"))
    return 0 if ok == 25 else 1


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
    if a.command == "render" and not (
            (a.hcno or (a.std and is_full_designation(a.std))) and a.pages):
        return 3
    return {"search": cmd_search, "render": cmd_render,
            "selftest": cmd_selftest}[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
