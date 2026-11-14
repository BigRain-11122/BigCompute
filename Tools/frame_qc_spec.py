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
import re

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

# The two sides that must *import* these constants.  Guarded by name, per
# value, so adding a sixth constant cannot leave the guard behind (T241).
CONSUMER_FILES = ("pool_igniter.py", "render_contact_sheet.py")

_ASSIGN_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")
_ALIAS_RE = re.compile(r"^[A-Za-z_][\w.]*\.([A-Z][A-Z0-9_]*)$")


def _is_alias(lhs, rhs):
    """`NAME = <spec module>.NAME` — a reference to the same value.

    An alias keeps one origin: mutate the spec and the consumer moves with it.
    `NAME = <spec module>.<other name>` is *not* an alias — it pins the wrong
    value under a right-sounding name, which reads green and measures another
    threshold.
    """
    m = _ALIAS_RE.match(rhs)
    return bool(m) and m.group(1) == lhs


def scan_source(name, text):
    """Scan one consumer source. Returns `(duplicates, aliases)` as `file:line`.

    A duplicate is any assignment to a spec constant whose right-hand side is
    not a spec-module reference — that is the second copy which lets the two
    sides drift together.
    """
    duplicates, aliases = [], []
    for lineno, line in enumerate(text.splitlines(), 1):
        body = line.split("#", 1)[0].strip()
        m = _ASSIGN_RE.match(body)
        if not m or m.group(1) not in _SPEC_FIELDS:
            continue
        where = "%s:%d" % (name, lineno)
        (aliases if _is_alias(m.group(1), m.group(2).strip())
         else duplicates).append(where)
    return duplicates, aliases


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

    # S10-S14: the consumers must not re-declare the constants (that is the
    # whole point); a copy-pasted literal would silently defeat the single
    # source.  The guard list is derived from `_SPEC_FIELDS` so a new constant
    # is covered on the day it is added (T241: `CLEAR_RGB_DEFAULT` was the
    # fifth constant and the old hard-coded four-name list never saw it).
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    dupes, seen_aliases = [], []
    scanned = []
    for name in CONSUMER_FILES:
        path = os.path.join(here, name)
        if not os.path.exists(path):
            continue
        scanned.append(name)
        with open(path, encoding="utf-8", errors="replace") as fh:
            d, a = scan_source(name, fh.read())
        dupes.extend(d)
        seen_aliases.extend(a)
    chk("S10 consumers hold no second copy of the constants",
        dupes == [], dupes)
    chk("S10b both consumer files were actually scanned",
        sorted(scanned) == sorted(CONSUMER_FILES), scanned)

    # S11: every spec field is guarded — proven per name, not by trusting the
    # list.  A field the scanner does not see would pass this test only if the
    # test were written against a second copy of the same list.
    missed = []
    for field in _SPEC_FIELDS:
        d, _a = scan_source("probe.py", "%s = 123\n" % field)
        if not d:
            missed.append(field)
    chk("S11 guard covers every _SPEC_FIELDS name", missed == [], missed)

    # S12/S13: alias vs literal must split, and a mis-alias is a duplicate.
    alias_src = "CLEAR_RGB_DEFAULT = qc_spec.CLEAR_RGB_DEFAULT\n"
    d_alias, a_alias = scan_source("alias.py", alias_src)
    chk("S12 alias form is not a duplicate", d_alias == [] and a_alias == ["alias.py:1"],
        (d_alias, a_alias))
    d_mis, _a_mis = scan_source("mis.py", "CLEAR_RGB_DEFAULT = qc_spec.BG_TOL\n")
    chk("S13 alias pointing at a different constant is a duplicate",
        d_mis == ["mis.py:1"], d_mis)

    # S14: the fifth constant is caught in literal form — the exact gap T241
    # was opened for.  `(13, 13, 15)` pasted into a consumer is a second copy.
    d_lit, _a_lit = scan_source("lit.py", "CLEAR_RGB_DEFAULT = (13, 13, 15)\n")
    chk("S14 CLEAR_RGB_DEFAULT literal is a duplicate",
        d_lit == ["lit.py:1"], d_lit)

    for name, cond, detail in checks:
        print(("PASS " if cond else "FAIL ") + name +
              ("" if cond else "  <%r>" % (detail,)))
    print("SELFTEST %d/%d PASS  %s" % (ok, len(checks), sid))
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
