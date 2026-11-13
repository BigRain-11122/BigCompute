#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a labelled contact sheet from a pool-ignite render run directory.

Purpose: a render run leaves six fixed-camera PNGs plus frame-quality.json.
Reading six files one by one is slow and hides the whole set; this tool tiles
them into one image with per-frame numbers (magenta% / geom%) so a human can
accept or reject the batch in one look.

Read-only on the run directory; writes only the output PNG.

`--verify` (tech T236) re-measures every frame from its own pixels with an
implementation that shares no code with the writer (`Tools/pool_igniter.py`
`frame_quality`): the writer walks raw bytes, this one builds channel masks and
counts histograms. A label copied from the receipt is only as trustworthy as
the receipt; if the writer's magenta rule ever breaks, a plain sheet would
print the wrong number more legibly. Mismatch marks that cell
`MISMATCH(live=...,receipt=...)` and exits 3.

T238 / BC-P-250: the two implementations stay independent, but the four
threshold constants now come from `Tools/frame_qc_spec.py` on both sides, and
both selftests assert a calibration frame whose magenta share is known by
construction. Two independent implementations of the same wrong threshold
would agree; an anchored one cannot drift alone.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import frame_qc_spec as qc_spec   # shared constants, not a shared implementation

FRAME_ORDER = [f"frame_{i:02d}.png" for i in range(1, 7)]
COLS = 3
CELL_W = 640
LABEL_H = 22
PAD = 6
BG = (24, 24, 28)
FG = (235, 235, 235)


def load_quality_doc(run_dir):
    path = os.path.join(run_dir, "frame-quality.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def load_quality(run_dir):
    doc = load_quality_doc(run_dir)
    out = {}
    for fr in doc.get("frames") or []:
        name = fr.get("file")
        if name:
            out[name] = fr
    return out


def find_frames(run_dir):
    frames_dir = os.path.join(run_dir, "frames")
    if not os.path.isdir(frames_dir):
        return []
    have = set(os.listdir(frames_dir))
    return [n for n in FRAME_ORDER if n in have]


def default_font(size=16):
    for name in ("consola.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _band_mask(chan, pred):
    """L channel -> 0/255 mask. `pred` is a plain int predicate."""
    return chan.point(lambda v: 255 if pred(v) else 0)


def _mask_count(mask):
    return mask.histogram()[255]


def live_readings(im, clear_rgb=None):
    """Measure one RGB image -> (magenta_pct, geom_pct, bg_rgb, bg_source).

    Independent implementation: per-channel masks multiplied together and
    counted by histogram, instead of a per-pixel byte loop.
    """
    n = im.width * im.height
    if not n:
        return None
    r, g, b = im.split()
    # Constants come from the spec module so neither reader can drift alone.
    mag = _mask_count(ImageChops.multiply(
        ImageChops.multiply(_band_mask(r, lambda v: v >= qc_spec.MAGENTA_MIN_R),
                            _band_mask(g, lambda v: v <= qc_spec.MAGENTA_MAX_G)),
        _band_mask(b, lambda v: v >= qc_spec.MAGENTA_MIN_B)))
    if clear_rgb:
        cr, cg, cb = clear_rgb
        source = "declared"
    else:
        from collections import Counter
        data = im.tobytes()
        cr, cg, cb = Counter(data[i:i + 3] for i in range(0, n * 3, 3)
                             ).most_common(1)[0][0]
        source = "modal"
    tol = qc_spec.BG_TOL
    bg = _mask_count(ImageChops.multiply(
        ImageChops.multiply(_band_mask(r, lambda v, c=cr: abs(v - c) <= tol),
                            _band_mask(g, lambda v, c=cg: abs(v - c) <= tol)),
        _band_mask(b, lambda v, c=cb: abs(v - c) <= tol)))
    return (round(100.0 * mag / n, 2), round(100.0 * (n - bg) / n, 2),
            [cr, cg, cb], source)


def verify(run_dir):
    """Re-measure every receipt frame. Returns (rows, mismatches)."""
    doc = load_quality_doc(run_dir)
    frames = doc.get("frames") or []
    declared = doc.get("clear_rgb")
    rows = []
    mismatches = []
    for fr in frames:
        name = fr.get("file") or "?"
        path = os.path.join(run_dir, "frames", name)
        if not os.path.exists(path):
            rows.append((name, None, None, fr, "MISSING-FILE"))
            mismatches.append(name)
            continue
        try:
            with Image.open(path) as im:
                # A declared clear colour is data from the receipt; only the
                # *readings* are recomputed here, never trusted.
                clear = (tuple(declared)
                         if (declared and fr.get("bg_source") == "declared")
                         else None)
                live = live_readings(im.convert("RGB"), clear)
        except Exception:
            rows.append((name, None, None, fr, "UNREADABLE"))
            mismatches.append(name)
            continue
        if live is None:
            rows.append((name, None, None, fr, "UNREADABLE"))
            mismatches.append(name)
            continue
        live_m, live_g, _bg, _src = live
        rec_m = fr.get("magenta_pct")
        rec_g = fr.get("geom_pct")
        bad = (live_m != rec_m) or (live_g != rec_g)
        if bad:
            mismatches.append(name)
        rows.append((name, (live_m, live_g), (rec_m, rec_g), fr,
                     "MISMATCH" if bad else "OK"))
    return rows, mismatches


def build(run_dir, out_path, marks=None):
    names = find_frames(run_dir)
    if not names:
        raise SystemExit(f"no frames under {run_dir}/frames")
    quality = load_quality(run_dir)
    marks = marks or {}
    rows = (len(names) + COLS - 1) // COLS

    first = Image.open(os.path.join(run_dir, "frames", names[0]))
    src_w, src_h = first.size
    first.close()
    cell_h = max(1, int(round(CELL_W * src_h / float(src_w))))
    sheet_w = COLS * CELL_W + (COLS + 1) * PAD
    sheet_h = rows * (cell_h + LABEL_H) + (rows + 1) * PAD
    sheet = Image.new("RGB", (sheet_w, sheet_h), BG)
    draw = ImageDraw.Draw(sheet)
    font = default_font(15)

    for idx, name in enumerate(names):
        r, c = divmod(idx, COLS)
        x = PAD + c * (CELL_W + PAD)
        y = PAD + r * (cell_h + LABEL_H + PAD)
        with Image.open(os.path.join(run_dir, "frames", name)) as im:
            sheet.paste(im.convert("RGB").resize((CELL_W, cell_h)), (x, y))
        q = quality.get(name) or {}
        mark = marks.get(name)
        if mark:
            label = f"{name}  {mark}"
        elif q:
            label = f"{name}  magenta={q.get('magenta_pct', '?'):.2f}%  geom={q.get('geom_pct', '?'):.2f}%"
        else:
            label = f"{name}  (no frame-quality entry)"
        draw.text((x + 2, y + cell_h + 3), label, fill=FG, font=font)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    sheet.save(out_path)
    return out_path, len(names), (sheet_w, sheet_h)


def run_selftest():
    """Two fixtures: honest receipt -> PASS/0, tampered receipt -> MISMATCH/3."""
    ok = 0
    checks = []

    def chk(name, cond, detail=""):
        nonlocal ok
        checks.append((name, bool(cond), detail))
        if cond:
            ok += 1

    clear = tuple(qc_spec.CLEAR_RGB_DEFAULT)
    tmp = tempfile.mkdtemp(prefix="contact-sheet-st-")
    run_dir = os.path.join(tmp, "run")
    os.makedirs(os.path.join(run_dir, "frames"))
    w = h = 40
    # frame_01: 160 magenta px + 160 grey px out of 1600 -> magenta=10.0 geom=20.0
    im = Image.new("RGB", (w, h), clear)
    for x in range(16):
        for y in range(10):
            im.putpixel((x, y), (255, 0, 255))
    for x in range(16):
        for y in range(10, 20):
            im.putpixel((x, y), (200, 200, 200))
    im.save(os.path.join(run_dir, "frames", "frame_01.png"))
    # frame_02: untouched clear -> magenta=0.0 geom=0.0
    Image.new("RGB", (w, h), clear).save(
        os.path.join(run_dir, "frames", "frame_02.png"))

    good = {
        "clear_rgb": list(clear), "frames_read": 2,
        "frames": [
            {"file": "frame_01.png", "magenta_pct": 10.0, "geom_pct": 20.0,
             "bg_rgb": list(clear), "bg_source": "declared"},
            {"file": "frame_02.png", "magenta_pct": 0.0, "geom_pct": 0.0,
             "bg_rgb": list(clear), "bg_source": "declared"},
        ],
    }

    def write_receipt(doc):
        with open(os.path.join(run_dir, "frame-quality.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(doc, fh)

    write_receipt(good)
    rows, bad = verify(run_dir)
    chk("S1 honest receipt verifies clean (ok=2, mismatch=0)",
        bad == [] and [r[4] for r in rows] == ["OK", "OK"], rows)
    chk("S2 live readings equal the receipt numbers",
        rows[0][1] == (10.0, 20.0) and rows[1][1] == (0.0, 0.0), rows)

    tampered = json.loads(json.dumps(good))
    tampered["frames"][0]["magenta_pct"] = 0.0
    write_receipt(tampered)
    rows, bad = verify(run_dir)
    chk("S3 tampered magenta is caught (one mismatch named)",
        bad == ["frame_01.png"], bad)
    chk("S4 mismatch keeps both numbers (live 10.0 vs receipt 0.0)",
        rows[0][1][0] == 10.0 and rows[0][2][0] == 0.0, rows[0])

    mark = {}
    for name, live, rec, _fr, state in rows:
        if state == "MISMATCH" and live and rec:
            mark[name] = "MISMATCH(live=%.2f%%,receipt=%.2f%%)" % (
                live[0], rec[0])
    out = os.path.join(run_dir, "contact-sheet.png")
    build(run_dir, out, marks=mark)
    chk("S5 sheet is written and the bad cell carries the MISMATCH mark",
        os.path.exists(out) and "frame_01.png" in mark
        and "MISMATCH(live=10.00%" in mark["frame_01.png"], mark)

    # geom tamper must be caught too (a writer could break only one reading)
    tampered2 = json.loads(json.dumps(good))
    tampered2["frames"][1]["geom_pct"] = 5.0
    write_receipt(tampered2)
    rows, bad = verify(run_dir)
    chk("S6 geom tamper is caught on its own axis", bad == ["frame_02.png"], bad)

    # modal branch: no clear_rgb -> live derives modal background
    modal = {"frames_read": 1, "frames": [
        {"file": "frame_02.png", "magenta_pct": 0.0, "geom_pct": 0.0,
         "bg_rgb": list(clear), "bg_source": "modal"}]}
    write_receipt(modal)
    rows, bad = verify(run_dir)
    chk("S7 modal receipt (no clear_rgb) verifies and is read as modal",
        bad == [] and rows[0][4] == "OK", rows)

    # missing frame file is a mismatch, not a silent skip
    os.remove(os.path.join(run_dir, "frames", "frame_01.png"))
    write_receipt(good)
    rows, bad = verify(run_dir)
    chk("S8 a receipt frame with no PNG is MISSING-FILE",
        bad == ["frame_01.png"] and rows[0][4] == "MISSING-FILE", rows)

    # T238 / BC-P-250: same calibration anchor as the writer's selftest. The
    # two implementations stay independent, but both must reproduce a share
    # that is known by construction - that is what makes a threshold edit
    # visible on both sides at once instead of only on the edited one.
    cal_im, cal_expect = qc_spec.calibration_frame()
    cal_live = live_readings(cal_im.convert("RGB"), clear)
    chk("S9 calibration frame (10% by construction) reads magenta=10.00",
        cal_expect == 10.0 and abs(cal_live[0] - cal_expect) <= 0.01, cal_live)
    saved_max_g = qc_spec.MAGENTA_MAX_G
    qc_spec.MAGENTA_MAX_G = -1            # no pixel can satisfy g <= -1
    try:
        cal_narrow = live_readings(cal_im.convert("RGB"), clear)
    finally:
        qc_spec.MAGENTA_MAX_G = saved_max_g
    chk("S10 the anchor is threshold-sensitive in this reader too",
        cal_narrow[0] == 0.0
        and live_readings(cal_im.convert("RGB"), clear)[0] == 10.0,
        cal_narrow[0])
    chk("S11 the verify summary names the constant group it judged by",
        qc_spec.spec_id().startswith("spec:"), qc_spec.spec_id())

    for name, cond, detail in checks:
        print(("PASS " if cond else "FAIL ") + name +
              ("" if cond else "  <%r>" % (detail,)))
    print("SELFTEST %d/%d PASS" % (ok, len(checks)))
    return 0 if ok == len(checks) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="render run -> labelled contact sheet")
    ap.add_argument("run_dir", nargs="?")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--verify", action="store_true",
                    help="re-measure each frame from pixels and compare to the receipt")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    if not args.run_dir:
        ap.error("run_dir is required (or use --selftest)")
    out = args.out or os.path.join(args.run_dir, "contact-sheet.png")

    marks = {}
    rc = 0
    if args.verify:
        rows, bad = verify(args.run_dir)
        if not rows:
            print(f"verify: no frames in {args.run_dir}/frame-quality.json -> FAIL")
            return 2
        for name, live, rec, _fr, state in rows:
            if state == "OK":
                print(f"verify {name}: OK live=%.2f%%/%.2f%% receipt=%.2f%%/%.2f%%"
                      % (live[0], live[1], rec[0], rec[1]))
            elif state == "MISMATCH":
                marks[name] = "MISMATCH(live=%.2f%%,receipt=%.2f%%)" % (live[0], rec[0])
                if live[1] != rec[1]:
                    marks[name] = "MISMATCH(live=%.2f%%,receipt=%.2f%%)" % (live[1], rec[1])
                print("verify %s: MISMATCH(live=%.2f%%/%.2f%%,receipt=%.2f%%/%.2f%%)"
                      % (name, live[0], live[1], rec[0], rec[1]))
            else:
                marks[name] = state
                print(f"verify {name}: {state}")
        rc = 3 if bad else 0
        print("verify: frames=%d ok=%d mismatch=%d judged_by=%s -> %s"
              % (len(rows), len(rows) - len(bad), len(bad), qc_spec.spec_id(),
                 "PASS" if rc == 0 else "FAIL"))

    path, n, size = build(args.run_dir, out, marks=marks)
    print(f"contact-sheet: frames={n} size={size[0]}x{size[1]} -> {path}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
