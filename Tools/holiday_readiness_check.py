#!/usr/bin/env python3
"""holiday_readiness_check.py - 24h unattended-ops readiness probe.

Committee order O-20260930-1540 (holiday readiness check) asks every
subsidiary to prove 24h autonomous operation through the CEO absence
window (10-01..10-08). This probe bundles the five BigCompute readiness
faces into one verdict plus a P0 fix list, re-runnable by any round
through the holiday; the JSON evidence file is the readiness proof.

Faces (order wording: automation base / activity / review chain /
resource water; face 5 = O-1540 standing add-on 1):
 1. automation base  - schtasks patrol via Tools/task_check.ps1
                       (OSLoop-PM + OrderSentinel + GPU-IdleWatch +
                       CleanWindowProbe, orphan round.lock included)
 2. activity         - freshness of state/rounds.log, state/heartbeat.txt
                       and the newest qa/smoke-*.log (all < 30h old)
 3. review chain     - cost_ledger.py selftest PASS and
                       fulfillment/test_pipeline.py exit 0
 4. resource water   - free disk on the repo drive >= 20 GB, and
                       state/gpu-util/samples.jsonl mtime age < 24h
 5. win-update guard - no auto-reboot while a user is logged on
                       (AU NoAutoRebootWithLoggedOnUsers=1) OR an
                       active Windows Update pause; both absent = P0
                       (the fix is an admin one-liner, echoed in the
                       P0 line for the daily report).

Writes state/holiday-readiness-<ts>.json with raw evidence.
Exit 0 = READY (no P0); 1 = P0 present (fix = next round top priority).
Encoding rule: this file stays PURE ASCII (group coding law).
"""
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
STATE = os.path.join(ROOT, "state")

FRESH_LIMIT_H = 30.0   # activity face: rounds land in 12/22 bands + sentinel
GPU_LIMIT_H = 24.0      # gpu samples freshness (GPU-IdleWatch 15min cadence)
DISK_MIN_GB = 20.0


def run(cmd, timeout=300):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", "exception: %s" % e


def age_h(path):
    try:
        m = os.path.getmtime(path)
        return (datetime.datetime.now().timestamp() - m) / 3600.0
    except OSError:
        return None


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    p0 = []
    faces = {}

    # 1. automation base
    rc, out, err = run(["powershell", "-NoProfile", "-ExecutionPolicy",
                        "Bypass", "-File",
                        os.path.join(HERE, "task_check.ps1")], 180)
    faces["automation_base"] = {"rc": rc,
                                "output": (out + err).strip()[:2000]}
    if rc != 0:
        p0.append("task_check rc=%d (task missing/unready or orphan lock)"
                  % rc)

    # 2. activity freshness
    act = {}
    for label, path in (("rounds_log", os.path.join(STATE, "rounds.log")),
                        ("heartbeat", os.path.join(STATE, "heartbeat.txt"))):
        a = age_h(path)
        act[label + "_age_h"] = None if a is None else round(a, 2)
        if a is None or a > FRESH_LIMIT_H:
            p0.append("stale %s (age %s h)" % (label, a))
    smokes = sorted(glob.glob(os.path.join(ROOT, "qa", "smoke-*.log")))
    if smokes:
        a = age_h(smokes[-1])
        act["last_smoke"] = os.path.basename(smokes[-1])
        act["last_smoke_age_h"] = round(a, 2)
        if a > FRESH_LIMIT_H:
            p0.append("stale qa smoke (age %.1f h)" % a)
    else:
        p0.append("no qa smoke logs found")
    faces["activity"] = act

    # 3. review chain
    rc1, out1, err1 = run([sys.executable, os.path.join(HERE, "cost_ledger.py"),
                           "selftest"], 300)
    rc2, out2, err2 = run([sys.executable,
                           os.path.join(HERE, "fulfillment", "test_pipeline.py")],
                          300)
    faces["review_chain"] = {
        "ledger_selftest_rc": rc1,
        "ledger_tail": (out1 + err1).strip()[-300:],
        "pipeline_rc": rc2,
        "pipeline_tail": (out2 + err2).strip()[-300:],
    }
    if rc1 != 0:
        p0.append("cost_ledger selftest rc=%d" % rc1)
    if rc2 != 0:
        p0.append("fulfillment pipeline rc=%d" % rc2)

    # 4. resource water level
    total, used, free = shutil.disk_usage(ROOT)
    gpu_age = age_h(os.path.join(STATE, "gpu-util", "samples.jsonl"))
    faces["resource_water"] = {
        "disk_free_gb": round(free / 1e9, 1),
        "gpu_samples_age_h": None if gpu_age is None else round(gpu_age, 2),
    }
    if free / 1e9 < DISK_MIN_GB:
        p0.append("disk free %.1f GB < %.0f GB" % (free / 1e9, DISK_MIN_GB))
    if gpu_age is None or gpu_age > GPU_LIMIT_H:
        p0.append("gpu samples stale (age %s h)" % gpu_age)

    # 5. windows update auto-reboot guard (O-1540 standing add-on 1)
    guard = {}
    rc_g, out_g, _ = run(["reg", "query",
                          "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
                          "\\WindowsUpdate\\Auto Update\\AU",
                          "/v", "NoAutoRebootWithLoggedOnUsers"], 30)
    guard["no_auto_reboot_on"] = (rc_g == 0 and "0x1" in out_g.split())
    rc_p, out_p, _ = run(["reg", "query", "HKLM\\SOFTWARE\\Microsoft"
                          "\\WindowsUpdate\\UX\\Settings",
                          "/v", "PauseUpdatesExpiryTime"], 30)
    pause_until = None
    if rc_p == 0:
        for ln in out_p.splitlines():
            s = ln.strip()
            if s and not s.upper().startswith(("HKLM", "HKEY")):
                pause_until = s
    guard["pause_updates_expiry"] = pause_until
    faces["windows_update_guard"] = guard
    if not guard["no_auto_reboot_on"] and pause_until is None:
        p0.append("windows update auto-reboot guard ABSENT: neither AU "
                  "NoAutoRebootWithLoggedOnUsers=1 nor active update "
                  "pause (O-1540 1 fix: Settings -> pause updates >=1w, "
                  "or admin: reg add HKLM\\SOFTWARE\\Microsoft\\Windows\\"
                  "CurrentVersion\\WindowsUpdate\\Auto Update\\AU /v "
                  "NoAutoRebootWithLoggedOnUsers /t REG_DWORD /d 1 /f)")

    verdict = "READY" if not p0 else "P0-PRESENT"
    doc = {"ts": ts, "verdict": verdict, "p0": p0, "faces": faces,
           "order_ref": "O-20260930-1540",
           "window": "CEO absence 10-01..10-08, 24h autonomous ops"}
    out_path = os.path.join(STATE, "holiday-readiness-%s.json" % ts)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("verdict: %s" % verdict)
    for line in p0:
        print("P0: %s" % line)
    print("evidence: %s" % os.path.relpath(out_path, ROOT).replace("\\", "/"))
    return 0 if not p0 else 1


if __name__ == "__main__":
    sys.exit(main())
