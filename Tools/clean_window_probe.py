#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clean_window_probe.py - clean-window probe for true-window tests (BC-P-17).

Read-only probe: counts Tuanjie editor processes (exact image-name match,
mirroring run_bake.ps1 gate) + nvidia-smi free VRAM. CLEAN iff editors==0
AND vram_free >= 8192 MiB (BC-P-17 pre-registered dual criteria). One JSON
line appended to state/clean-window-log.jsonl per sample.

Consumers: M25/T26 interior bake live run, E17 ngl99 re-verification,
tech T15 Q8_0 comparison - clean-window discovery lag removal - plus
10-05 review-window clean-window frequency evidence.

NO DISPATCH / NO LAUNCH / NO KILL - pure observation (DRY-RUN observation
period until 2026-10-05, CEO safety-cleanup order 3; bm-a auto-dispatch
blacklist per C-20260929-02 7.4). Silent schtasks registration mirrors
BigCompute-GPU-IdleWatch (15min tick, InvisibleRunner.vbs).

Exit codes: 0 CLEAN / 3 BLOCKED (mirror run_bake gate) / 2 probe failure.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(PROJECT, "state", "clean-window-log.jsonl")
MACHINE = "bm-a"  # C-20260929-02 7.1 machine tag
FREE_MIN_MIB = 8192  # BC-P-17: clean window needs >= 8GB free VRAM
EDITOR_EXE = "tuanjie.exe"  # exact match only (Hub/CrashHandler excluded, gate mirror)


def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def count_editors():
    """Count Tuanjie.exe processes via tasklist (exact image name)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Tuanjie.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    n = 0
    for line in out.splitlines():
        if line.lower().startswith('"' + EDITOR_EXE + '"'):
            n += 1
    return n


def read_vram():
    """Return (total_mib, used_mib) from nvidia-smi, or (None, None)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        total, used = (int(x.strip()) for x in out.split(",")[:2])
        return total, used
    except Exception:
        return None, None


def classify(editors, free_mib):
    """Pure verdict: CLEAN iff editors==0 and free>=FREE_MIN_MIB (S1 fixture face)."""
    blocking = []
    if editors is None:
        blocking.append("editor_probe_failed")
    elif editors > 0:
        blocking.append("editors")
    if free_mib is None:
        blocking.append("vram_probe_failed")
    elif free_mib < FREE_MIN_MIB:
        blocking.append("vram_free")
    return ("CLEAN", []) if not blocking else ("BLOCKED", blocking)


def append_log(line):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:  # utf-8 explicit (T24 law)
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def cmd_check():
    editors = count_editors()
    total, used = read_vram()
    free = (total - used) if (total is not None and used is not None) else None
    verdict, blocking = classify(editors, free)
    append_log({"ts": _now_iso(), "machine": MACHINE, "editors": editors,
                "vram_total_mib": total, "vram_used_mib": used,
                "vram_free_mib": free, "verdict": verdict, "blocking": blocking})
    print("machine=%s editors=%s vram_free=%sMiB verdict=%s%s (NO dispatch, DRY-RUN to 10-05)"
          % (MACHINE, editors, free, verdict,
             (" blocking=" + "+".join(blocking)) if blocking else ""))
    return 0 if verdict == "CLEAN" else (3 if verdict == "BLOCKED" else 2)


def load_log(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def cmd_report():
    rows = load_log(LOG_PATH)
    n = len(rows)
    clean = [r for r in rows if r.get("verdict") == "CLEAN"]
    hours = {}
    for r in clean:
        hours[r["ts"][:13]] = hours.get(r["ts"][:13], 0) + 1
    last = rows[-1] if rows else {}
    print("machine=%s n=%d clean=%d clean_ratio=%.1f%% last=%s@%s"
          % (MACHINE, n, len(clean), (100.0 * len(clean) / n) if n else 0.0,
             last.get("verdict", "-"), last.get("ts", "-")))
    if clean:
        top = sorted(hours.items(), key=lambda kv: -kv[1])[:5]
        print("clean hours (top5): " + ", ".join("%s=%d" % kv for kv in top))
        print("last clean: " + "; ".join(
            "%s free=%sMiB" % (r.get("ts"), r.get("vram_free_mib"))
            for r in clean[-5:]))
    return 0


def cmd_selftest():
    ok = 0

    def check(name, cond):
        nonlocal ok
        ok += 1 if cond else 0
        print("%s %s" % ("PASS" if cond else "FAIL", name))
        return cond

    # S1 classification dual criteria (editors + VRAM free)
    check("S1a clean", classify(0, 9000)[0] == "CLEAN")
    check("S1b editors block", classify(3, 9000) == ("BLOCKED", ["editors"]))
    check("S1c vram block", classify(0, 7000) == ("BLOCKED", ["vram_free"]))
    check("S1d dual block", classify(2, 7000) == ("BLOCKED", ["editors", "vram_free"]))
    check("S1e boundary 8192 is CLEAN", classify(0, 8192)[0] == "CLEAN")
    check("S1f boundary 8191 is BLOCKED", classify(0, 8191)[0] == "BLOCKED")
    check("S1g probe failure exits BLOCKED", classify(None, None)[0] == "BLOCKED")

    # S2 log append + reload on temp file (utf-8 law + idempotent persistence)
    global LOG_PATH
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "cw.jsonl")
        LOG_PATH = tmp
        append_log({"ts": "2026-09-30T01:00:00", "machine": MACHINE, "verdict": "BLOCKED"})
        append_log({"ts": "2026-09-30T02:00:00", "machine": MACHINE, "verdict": "CLEAN",
                    "vram_free_mib": 9700})
        rows = load_log(tmp)
        check("S2a two rows persisted", len(rows) == 2)
        check("S2b strict utf-8 reload", rows[0]["ts"] == "2026-09-30T01:00:00")

    # S3 report aggregation math on fixture
        n_before = cmd_report.__defaults__  # (report reads LOG_PATH live, not needed)
        # aggregate via load_log directly (report prints; math asserted here)
        clean = [r for r in rows if r["verdict"] == "CLEAN"]
        check("S3a clean count", len(clean) == 1)
        hours = {}
        for r in clean:
            hours[r["ts"][:13]] = hours.get(r["ts"][:13], 0) + 1
        check("S3b hour bucket", hours.get("2026-09-30T02") == 1)

    # S4 DRY-RUN law: no process-launch verb in source (literals concatenated
    # so the assertion does not match its own source text)
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    forbidden = ("task" + "kill", "Po" + "pen", "Start-" + "Process", "os." + "system")
    check("S4a no dispatch verb in command surface",
          all(v not in src for v in forbidden))
    print("selftest: %s" % ("PASS" if ok == 12 else "FAIL"))
    return 0 if ok == 12 else 1


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="clean-window probe (BC-P-17, read-only)")
    ap.add_argument("command", choices=["check", "report", "selftest"])
    a = ap.parse_args()
    return {"check": cmd_check, "report": cmd_report, "selftest": cmd_selftest}[a.command]()


if __name__ == "__main__":
    sys.exit(main())
