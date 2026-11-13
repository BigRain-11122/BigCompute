"""Frame QC spec — the single source for the judgment-frame reading constants.

Why this file exists (T238 / BC-P-250):
`pool_igniter.frame_quality()` (writer side) and
`render_contact_sheet.live_readings()` (independent re-measure) deliberately
do not share *code*, so one impl can catch the other's bug.  But until now they
shared the four limb literals by copy: `MAGENTA_MIN_R/B=200`, `MAX_G=80`,
`BG_TOL=6`.  Edit both copies the same wrong way and both verifications go
green together - the independent witness then says nothing about the threshold
itself, only about the arithmetic.

So: the constants live here, both sides import them, every reading row carries
`judged_by=spec:<hash>` so a reader can tell which constant set produced the
number, and a calibration frame with a *known* magenta share is asserted in
both selftests.  Widening or narrowing a threshold turns those anchors red
instead of quietly widening what "FRAME-QC-PASS" means.

Honest boundary: the anchor pins the arithmetic, not the physics. 200/80/6 are
calibrated to the observed Unity error-shader colour (~255,0,255); this module
does not prove they are the right thresholds for a different renderer or a
different tone-mapping chain.
"""

from __future__ import annotations

import hashlib

# Unity's missing-material magenta is ~ (255, 0, 255).  The bands are wide
# enough to survive compression/tone-mapping drift and narrow enough not to
# swallow the reds and purples that real content legitimately contains.
MAGENTA_MIN_R = 200
MAGENTA_MIN_B = 200
MAGENTA_MAX_G = 80
BG_TOL = 6                          # per-channel tolerance vs the clear colour
CLEAR_RGB_DEFAULT = (13, 13, 15)    # Color(0.05, 0.05, 0.06) at 8bpc, 0-255

_SPEC_FIELDS = (
    "MAGENTA_MIN_R", "MAGENTA_MIN_B", "MAGENTA_MAX_G",
    "BG_TOL", "CLEAR_RGB_DEFAULT",
)


def spec_id():
    """Stable short id of the constant group, e.g. `spec:1a2b3c4d5e6f`."""
    payload = "|".join("%s=%s" % (name, globals()[name])
                       for name in _SPEC_FIELDS)
    return "spec:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def is_magenta(r, g, b):
    """True when the pixel falls in the error-shader magenta band."""
    return (r >= MAGENTA_MIN_R and b >= MAGENTA_MIN_B
            and g <= MAGENTA_MAX_G)


def is_background(r, g, b, clear_rgb=None):
    """True when the pixel matches the camera clear colour within BG_TOL."""
    cr, cg, cb = clear_rgb if clear_rgb else CLEAR_RGB_DEFAULT
    return (abs(r - cr) <= BG_TOL and abs(g - cg) <= BG_TOL
            and abs(b - cb) <= BG_TOL)


def calibration_frame(size=40, magenta_pct=10.0, clear_rgb=None):
    """Synthetic frame whose magenta share is known by construction.

    Returns `(Image, expected_magenta_pct)`.  The rest of the frame is the
    clear colour, so the geometry reading is known too.  Consumers assert the
    measured number equals the constructed one: that is the anchor which makes
    a threshold edit visible.
    """
    from PIL import Image

    clear = tuple(clear_rgb or CLEAR_RGB_DEFAULT)
    n = size * size
    want = int(round(n * magenta_pct / 100.0))
    im = Image.new("RGB", (size, size), clear)
    px = im.load()
    for i in range(want):
        px[i % size, i // size] = (255, 0, 255)
    return im, round(100.0 * want / n, 2)


def selftest():
    ok = 0
    checks = []

    def chk(name, cond, detail=""):
        nonlocal ok
        checks.append((name, bool(cond), detail))
        if cond:
            ok += 1

    # S1/S2: the id is derived from the values, so an edit changes the label.
    sid = spec_id()
    chk("S1 spec_id is stable and self-describing",
        sid.startswith("spec:") and len(sid) == len("spec:") + 12
        and sid == spec_id(), sid)

    saved = globals()["BG_TOL"]
    globals()["BG_TOL"] = saved + 1
    chk("S2 changing one constant changes the id", spec_id() != sid,
        (sid, spec_id()))
    globals()["BG_TOL"] = saved
    chk("S2b restoring the constant restores the id", spec_id() == sid, sid)

    # S3-S6: band boundaries are asserted on the edges, not in the middle.
    chk("S3 pure magenta is magenta", is_magenta(255, 0, 255))
    chk("S4 r just below the band is not magenta",
        not is_magenta(MAGENTA_MIN_R - 1, 0, 255))
    chk("S5 g just above the band is not magenta",
        not is_magenta(255, MAGENTA_MAX_G + 1, 255))
    chk("S6 b just below the band is not magenta",
        not is_magenta(255, 0, MAGENTA_MIN_B - 1))

    # S7/S8: clear-colour tolerance edges.
    cr, cg, cb = CLEAR_RGB_DEFAULT
    chk("S7 exact clear colour is background", is_background(cr, cg, cb))
    chk("S8 tolerance is per-channel and inclusive on both sides",
        is_background(cr + BG_TOL, cg - BG_TOL, cb)
        and not is_background(cr + BG_TOL + 1, cg, cb))

    # S9: the calibration frame is constructed exactly as declared.
    im, expected = calibration_frame()
    data = im.tobytes()
    total = len(data) // 3
    mag = sum(1 for i in range(0, len(data), 3)
              if data[i] == 255 and data[i + 1] == 0 and data[i + 2] == 255)
    chk("S9 calibration frame carries the declared magenta share",
        total == 1600 and mag == 160 and expected == 10.0,
        (total, mag, expected))

    # S10: the consumers must not re-declare the constants (that is the whole
    # point); a copy-pasted literal would silently defeat the single source.
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    dupes = []
    for name in ("pool_igniter.py", "render_contact_sheet.py"):
        path = os.path.join(here, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                body = line.split("#", 1)[0]
                if any(body.strip().startswith(k + " =")
                       for k in ("MAGENTA_MIN_R", "MAGENTA_MIN_B",
                                 "MAGENTA_MAX_G", "BG_TOL")):
                    dupes.append("%s:%d" % (name, lineno))
    chk("S10 consumers hold no second copy of the constants", dupes == [], dupes)

    for name, cond, detail in checks:
        print(("PASS " if cond else "FAIL ") + name +
              ("" if cond else "  <%r>" % (detail,)))
    print("SELFTEST %d/%d PASS  %s" % (ok, len(checks), sid))
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
