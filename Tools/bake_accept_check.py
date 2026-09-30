# -*- coding: utf-8 -*-
"""Bake acceptance pre-check (BC-P-20 / M25 / T26) — J1/J2/J3 mechanical verdicts.

Reads the evidence produced by Tools/bake_spike_assets/run_bake.ps1:
  <dir>/metrics.json      (BakeSpike.cs WriteMetrics — bake_ok/bake_seconds/lightmap_*)
  <dir>/vram_samples.csv  (nvidia-smi 5s external sampler — J2 source)
  <dir>/summary.json      (run_bake.ps1 — vram_peak_mib fallback for J2)

Pre-registered criteria (R-20260929-city3d-interior-bake-lane §二):
  J1 = bake_ok true AND bake_seconds <= 1800 (30min single-module bay)
  J2 = VRAM peak recorded (samples > 0; headroom vs 12GB card reported, >=1.5GB discipline note)
  J3 = lightmap products present (lightmap_files>=1, lightmap_bytes>0, runtime_lightmaps>=1)
  (producer pre-check only — consumer acceptance stays with City3D line / B-CITY3D-01.)

Pure read-only on evidence dirs; selftest fixtures live in temp only. No new ledgers.
Usage:
  python Tools/bake_accept_check.py check [dir ...]   # no dir = newest 2 under state/bake-spike-*
  python Tools/bake_accept_check.py selftest
Exit codes: 0 = all runs all-PASS; 1 = any FAIL; 2 = evidence missing.
"""
import json
import os
import re
import sys
import tempfile

J1_MAX_SECONDS = 1800.0
TOTAL_VRAM_MIB = 12282  # bm-a RTX 4070S 12GB
HEADROOM_MIB = 1536     # fleet YELLOW-HEAVY co-card discipline (BC-P-06 same value)
STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")


def _stdout_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _load_metrics(run_dir):
    path = os.path.join(run_dir, "metrics.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _peak_from_csv(run_dir):
    path = os.path.join(run_dir, "vram_samples.csv")
    if not os.path.isfile(path):
        return None, 0
    peak, n = 0, 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.search(r",(\d+),\s*(\d+)\s*$", line)
            if m:
                n += 1
                mem = int(m.group(1))
                if mem > peak:
                    peak = mem
    return peak, n


def _peak_from_summary(run_dir):
    path = os.path.join(run_dir, "summary.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = json.load(f)
        v = s.get("vram_peak_mib")
        return int(v) if isinstance(v, (int, float)) and v > 0 else None
    except Exception:
        return None


def check_run(run_dir):
    """Returns dict with per-J verdicts; keys j1/j2/j3 in {PASS, FAIL}, plus fields."""
    r = {"dir": run_dir, "found": False}
    m = _load_metrics(run_dir)
    if m is None:
        return r
    r["found"] = True
    r["lightmapper"] = str(m.get("lightmapper", "?"))
    r["baseline"] = str(m.get("light_baseline", "-"))
    fatal = "fatal" in m
    bake_ok = bool(m.get("bake_ok", False))
    secs = float(m.get("bake_seconds", -1.0))
    r["bake_seconds"] = secs
    r["j1"] = "PASS" if (bake_ok and not fatal and 0 <= secs <= J1_MAX_SECONDS) else "FAIL"
    peak, n = _peak_from_csv(run_dir)
    src = "csv"
    if n == 0:
        peak = _peak_from_summary(run_dir)
        src = "summary"
        n = 1 if peak is not None else 0
    r["vram_peak_mib"] = peak if (peak is not None and peak >= 0) else None
    r["samples"] = n
    r["j2"] = "PASS" if (peak is not None and peak > 0 and n > 0) else "FAIL"
    files = int(m.get("lightmap_files", 0))
    nbytes = int(m.get("lightmap_bytes", 0))
    rt = int(m.get("runtime_lightmaps", 0))
    r["lightmap_files"], r["lightmap_bytes"], r["runtime_lightmaps"] = files, nbytes, rt
    r["j3"] = "PASS" if (files >= 1 and nbytes > 0 and rt >= 1) else "FAIL"
    r["overall"] = "PASS" if (r["j1"] == "PASS" and r["j2"] == "PASS" and r["j3"] == "PASS") else "FAIL"
    return r


def fmt_mb(nbytes):
    return round(nbytes / (1024.0 * 1024.0), 1)


def print_check(run_dirs):
    results = []
    missing = []
    for d in run_dirs:
        r = check_run(d)
        if not r["found"]:
            missing.append(d)
            print("bake_accept MISSING-EVIDENCE dir=%s (metrics.json not found)" % d)
            continue
        peak = r["vram_peak_mib"]
        head = (TOTAL_VRAM_MIB - peak) if peak is not None else None
        print("bake_accept %s: lightmapper=%s J1=%ss=%s J2=peak=%sMiB(samples=%s,headroom=%sMiB)=%s J3=%dfiles/%sMB(rt=%d)=%s overall=%s baseline=%s" % (
            os.path.basename(r["dir"]), r["lightmapper"],
            ("%.1f" % r["bake_seconds"]) if r["bake_seconds"] >= 0 else "?", r["j1"],
            peak if peak is not None else "?", r["samples"],
            head if head is not None else "?", r["j2"],
            r["lightmap_files"], fmt_mb(r["lightmap_bytes"]), r["runtime_lightmaps"], r["j3"],
            r["overall"], r["baseline"]))
        if head is not None and head < HEADROOM_MIB:
            print("  NOTE: headroom %dMiB < %dMiB co-card discipline line (BC-P-06)" % (head, HEADROOM_MIB))
        results.append(r)
    if missing:
        return 2, results
    if not results:
        return 2, results
    runs_txt = "；".join(
        "%s J1=%ss/J2=peak %sMiB/J3=%d张/%sMB%s" % (
            r["lightmapper"], ("%.1f" % r["bake_seconds"]), r["vram_peak_mib"],
            r["lightmap_files"], fmt_mb(r["lightmap_bytes"]),
            "" if r["overall"] == "PASS" else "（判负）")
        for r in results)
    print("R-§三行3 回填行（组装件·消费方终验权维持）：labbench 烘焙样板间实弹 %s —— %s" % (
        "✓ 实测" if all(x["overall"] == "PASS" for x in results) else "🟡 判负留存", runs_txt))
    return (0 if all(x["overall"] == "PASS" for x in results) else 1), results


def auto_dirs():
    root = STATE_DIR
    if not os.path.isdir(root):
        return []
    dirs = sorted(
        [os.path.join(root, d) for d in os.listdir(root) if d.startswith("bake-spike-")
         and os.path.isdir(os.path.join(root, d))])
    return dirs[-2:]


def selftest():
    ok = 0
    total = 0

    def check(name, cond):
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print("  SELFTEST-FAIL: %s" % name)

    tmp = tempfile.mkdtemp(prefix="bake-accept-st-")

    def fixture(name, metrics, csv_lines=None, summary=None):
        d = os.path.join(tmp, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "metrics.json"), "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=True)
        if csv_lines is not None:
            with open(os.path.join(d, "vram_samples.csv"), "w", encoding="utf-8") as f:
                f.write("\n".join(csv_lines) + "\n")
        if summary is not None:
            with open(os.path.join(d, "summary.json"), "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=True)
        return d

    good_metrics = {"lightmapper": "ProgressiveCPU", "bake_ok": True, "bake_seconds": 604.2,
                    "lightmap_files": 6, "lightmap_bytes": 3 * 1024 * 1024,
                    "runtime_lightmaps": 6, "light_baseline": "city3d_official_ad022_v1"}
    # S1 full PASS
    d1 = fixture("s1", good_metrics,
                 ["2026-09-30T22:45:00+08:00,7315, 92", "2026-09-30T22:45:05+08:00,11176, 100"])
    r = check_run(d1)
    check("S1 j1", r["j1"] == "PASS"); check("S1 j2 peak", r["vram_peak_mib"] == 11176 and r["samples"] == 2)
    check("S1 j3", r["j3"] == "PASS"); check("S1 overall", r["overall"] == "PASS")
    # S2 J1 over 30min
    d2 = fixture("s2", dict(good_metrics, bake_seconds=2000.0),
                 ["t,7315, 92"])
    r = check_run(d2)
    check("S2 j1 fail", r["j1"] == "FAIL" and r["j3"] == "PASS" and r["overall"] == "FAIL")
    # S3 bake_ok false + zero artifacts
    d3 = fixture("s3", dict(good_metrics, bake_ok=False, lightmap_files=0, lightmap_bytes=0,
                            runtime_lightmaps=0), ["t,7315, 92"])
    r = check_run(d3)
    check("S3 fail", r["j1"] == "FAIL" and r["j3"] == "FAIL" and r["overall"] == "FAIL")
    # S4 csv missing -> summary fallback
    d4 = fixture("s4", good_metrics, csv_lines=None, summary={"vram_peak_mib": 9020})
    r = check_run(d4)
    check("S4 summary fallback", r["vram_peak_mib"] == 9020 and r["j2"] == "PASS")
    # S5 headroom note line under discipline threshold
    d5 = fixture("s5", good_metrics, ["t,11800, 100"])
    r = check_run(d5)
    check("S5 headroom", (TOTAL_VRAM_MIB - 11800) < HEADROOM_MIB and r["j2"] == "PASS")
    # S6 missing metrics.json
    r = check_run(os.path.join(tmp, "nope"))
    check("S6 missing", r["found"] is False)
    # S7 print_check exit codes + backfill assembly (dual path)
    import io
    import contextlib
    d7 = fixture("s7g", dict(good_metrics, lightmapper="ProgressiveGPU", bake_seconds=411.9,
                              lightmap_files=6), ["t,11176, 100"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code, _ = print_check([d1, d7])
    out = buf.getvalue()
    check("S7 exit0", code == 0)
    check("S7 backfill both paths", "ProgressiveGPU" in out and "ProgressiveCPU" in out and "R-§三行3" in out)
    with contextlib.redirect_stdout(io.StringIO()):
        code_f, _ = print_check([d2])
    check("S7 fail exit1", code_f == 1)
    with contextlib.redirect_stdout(io.StringIO()):
        code_m, _ = print_check([os.path.join(tmp, "nope")])
    check("S7 missing exit2", code_m == 2)
    # S8 headroom NOTE prints
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_check([d5])
    check("S8 note", "co-card discipline" in buf.getvalue())
    print("selftest: %d/%d PASS" % (ok, total))
    return 0 if ok == total else 1


def main(argv):
    _stdout_utf8()
    if len(argv) >= 2 and argv[1] == "selftest":
        return selftest()
    if len(argv) >= 2 and argv[1] == "check":
        dirs = [os.path.abspath(d) for d in argv[2:]]
        if not dirs:
            dirs = auto_dirs()
            if not dirs:
                print("bake_accept check: no bake-spike-* evidence dir under state/ yet (T26 pending clean window)")
                return 2
        code, _ = print_check(dirs)
        return code
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
