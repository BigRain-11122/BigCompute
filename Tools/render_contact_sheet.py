#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a labelled contact sheet from a pool-ignite render run directory.

Purpose: a render run leaves six fixed-camera PNGs plus frame-quality.json.
Reading six files one by one is slow and hides the whole set; this tool tiles
them into one image with per-frame numbers (magenta% / geom%) so a human can
accept or reject the batch in one look.

Read-only on the run directory; writes only the output PNG.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

FRAME_ORDER = [f"frame_{i:02d}.png" for i in range(1, 7)]
COLS = 3
CELL_W = 640
LABEL_H = 22
PAD = 6
BG = (24, 24, 28)
FG = (235, 235, 235)


def load_quality(run_dir):
    path = os.path.join(run_dir, "frame-quality.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    out = {}
    for fr in data.get("frames") or []:
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


def build(run_dir, out_path):
    names = find_frames(run_dir)
    if not names:
        raise SystemExit(f"no frames under {run_dir}/frames")
    quality = load_quality(run_dir)
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
        if q:
            label = f"{name}  magenta={q.get('magenta_pct', '?'):.2f}%  geom={q.get('geom_pct', '?'):.2f}%"
        else:
            label = f"{name}  (no frame-quality entry)"
        draw.text((x + 2, y + cell_h + 3), label, fill=FG, font=font)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    sheet.save(out_path)
    return out_path, len(names), (sheet_w, sheet_h)


def main(argv=None):
    ap = argparse.ArgumentParser(description="render run -> labelled contact sheet")
    ap.add_argument("run_dir")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args(argv)
    out = args.out or os.path.join(args.run_dir, "contact-sheet.png")
    path, n, size = build(args.run_dir, out)
    print(f"contact-sheet: frames={n} size={size[0]}x{size[1]} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
