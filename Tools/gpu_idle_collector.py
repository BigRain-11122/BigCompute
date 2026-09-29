#!/usr/bin/env python3
"""gpu_idle_collector.py - GPU idle monitor + dispatcher (T-20260928-28).

CEO dedicated dispatch under Self-Drive v2.0 zero-idle order
(orders.md L252 2026-09-28; spec = docs/self-drive.md sections 2/6.3 +
tasks/TASKS.md T-20260928-28 + state/proposals.md BC-P-01 batch pool).

- sample: append one nvidia-smi JSONL row to state/gpu-util/samples.jsonl
  (ts, util_pct, mem_used_mib, power_w). Then, when a full 30-min rolling
  window (>= 2 samples at 15-min tick) averages < 50% utilization, append
  one dispatch line to state/gpu-util/dispatch-log.md (dedup: max one per
  30-min cooldown).
- dispatch v1 = auditable POINTER only, no auto-execution, no cross-repo
  writes: it points at the top open todo of state/queue/ (P1 -> P2 -> P3,
  three-line queue law); batch pool BC-P-01 has zero in-flight jobs in
  plan state; marketplace listing stays blocked on CPH4 tables.
- DRY-RUN OBSERVATION MODE (CEO safety-fix order 2026-09-28, item 3):
  monitoring + observation log only, no dispatch at all; real dispatch
  activation (BC-P-01 batch pool -> local Ollama) stays deferred until
  after the one-week observation window ends 2026-10-05.
- report: same-day stats with machine tag (weekly ledger per-machine row,
  C-20260929-02 7.1) + call-out KPI verdict. Pre-registered measurement
  rule (C-20260929-02 seat-2/seat-7 amendments): sole call-out threshold
  = 30% evaluated on the 3-day rolling baseline (the 70% target stays a
  directional reference only) -> honest FLAG below 30%.
- selftest: offline window/dispatch math checks (no nvidia-smi needed).

Usage (run from repo root):
  python Tools/gpu_idle_collector.py sample
  python Tools/gpu_idle_collector.py report
  python Tools/gpu_idle_collector.py selftest

Encoding rule: this file stays PURE ASCII (group coding law).
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
UTIL_DIR = os.path.join(HERE, "..", "state", "gpu-util")
SAMPLES = os.path.join(UTIL_DIR, "samples.jsonl")
DISPATCH = os.path.join(UTIL_DIR, "dispatch-log.md")
QUEUE_DIR = os.path.join(HERE, "..", "state", "queue")

WINDOW_MIN = 30    # rolling idle window (CEO spec)
IDLE_PCT = 50.0    # rolling-window idle verdict (CEO spec, unchanged)
CALL_PCT = 30.0    # C-20260929-02 7.1: SOLE call-out threshold (see below)
ROLL_DAYS = 3      # C-20260929-02 seat-2/7: pre-registered measure basis
MACHINE = "bm-a"   # local machine tag -> weekly per-machine ledger row
DISPATCH_COOLDOWN_MIN = 30
DISPATCH_HEADER = ("# GPU idle observation log (T-20260928-28; DRY-RUN per "
                   "CEO safety-fix order 2026-09-28 item 3: no "
                   "auto-dispatch, observe 1 week to 2026-10-05)\n")


def now():
    return datetime.datetime.now()


def load_samples():
    rows = []
    if os.path.exists(SAMPLES):
        with open(SAMPLES, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    return rows


def query_gpu():
    out = subprocess.run(
        ["nvidia-smi",
         "--query-gpu=utilization.gpu,memory.used,power.draw",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        sys.stderr.write("nvidia-smi failed: %s\n" % out.stderr.strip())
        return None
    try:
        util_s, mem_s, pow_s = [x.strip() for x in
                                out.stdout.strip().splitlines()[0].split(",")]
        return {"util_pct": float(util_s), "mem_used_mib": float(mem_s),
                "power_w": float(pow_s.rstrip(" W"))}
    except (ValueError, IndexError):
        sys.stderr.write("nvidia-smi parse failed: %r\n" % out.stdout)
        return None


def window(rows, at=None):
    """Samples inside the trailing WINDOW_MIN; complete if >= 2 samples."""
    at = at or now()
    cutoff = at - datetime.timedelta(minutes=WINDOW_MIN)
    inwin = [r for r in rows
             if datetime.datetime.fromisoformat(r["ts"]) >= cutoff]
    return inwin, len(inwin) >= 2


def roll3_dates(at=None):
    """Calendar dates covered by the rolling baseline (today + N-1 back)."""
    at = at or now()
    return tuple((at.date() - datetime.timedelta(days=i)).isoformat()
                 for i in range(ROLL_DAYS))


def callout_verdict(avg):
    """C-20260929-02 seat-2/seat-7: sole call-out gate = 30% measured on
    the pre-registered 3-day rolling baseline (not a same-day snapshot)."""
    return ("PASS" if avg >= CALL_PCT
            else "FLAG(<30%% call-out threshold on %d-day rolling "
                 "baseline, C-20260929-02)" % ROLL_DAYS)


def queue_head_pointer():
    """Top open todo across P1 -> P2 -> P3 queue files (pointer only)."""
    for name in ("main.md", "tech.md", "explore.md"):
        path = os.path.join(QUEUE_DIR, name)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.startswith("| "):
                    continue
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) >= 4 and cells[-1].startswith("open"):
                    return "%s %s: %s" % (name, cells[0], cells[1][:80])
    return "queue scan: no open row found"


def _parse_last_ts(text):
    """Timestamp from one dispatch row text, or None (parse-safe)."""
    if not text.startswith("| "):
        return None
    try:
        return datetime.datetime.fromisoformat(
            text.strip().strip("|").split("|")[0].strip())
    except ValueError:
        return None


def maybe_dispatch(rows):
    inwin, complete = window(rows)
    if not complete:
        return ("window incomplete (%d sample(s) in last %d min), "
                "no dispatch" % (len(inwin), WINDOW_MIN))
    if not inwin:
        return "no samples in window"
    avg = sum(r["util_pct"] for r in inwin) / len(inwin)
    if avg >= IDLE_PCT:
        return ("window busy: avg %.1f%% >= %.0f%%, no dispatch"
                % (avg, IDLE_PCT))
    last_ts = None
    if os.path.exists(DISPATCH):
        last = ""
        with open(DISPATCH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        last_ts = _parse_last_ts(last)
    if last_ts is not None and (now() - last_ts) < datetime.timedelta(
            minutes=DISPATCH_COOLDOWN_MIN):
        return ("idle avg %.1f%% but dispatch on cooldown (last %s)"
                % (avg, last_ts.isoformat(timespec="seconds")))
    os.makedirs(UTIL_DIR, exist_ok=True)
    new_file = not os.path.exists(DISPATCH)
    with open(DISPATCH, "a", encoding="utf-8") as f:
        if new_file:
            f.write(DISPATCH_HEADER)
        f.write("| %s | IDLE window avg %.1f%% (%d samples/%d min) | %s | "
                "DRY-RUN observe only, NO dispatch (CEO safety-fix "
                "2026-09-28; activation deferred past 2026-10-05 review); "
                "batch pool BC-P-01: 0 in-flight (plan state) |\n"
                % (now().isoformat(timespec="seconds"), avg, len(inwin),
                   WINDOW_MIN, queue_head_pointer()))
    return "IDLE avg %.1f%% -> dry-run observation line appended (no dispatch)" % avg


def cmd_sample():
    m = query_gpu()
    if m is None:
        return 1
    os.makedirs(UTIL_DIR, exist_ok=True)
    row = {"ts": now().isoformat(timespec="seconds")}
    row.update(m)
    with open(SAMPLES, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print("sample ok: util=%.0f%% mem=%.0fMiB power=%.2fW"
          % (m["util_pct"], m["mem_used_mib"], m["power_w"]))
    print(maybe_dispatch(load_samples()))
    return 0


def cmd_report():
    rows = load_samples()
    today = now().date().isoformat()
    day = [r for r in rows if r["ts"].startswith(today)]
    if not day:
        print("gpu report %s machine=%s: no samples today" % (today, MACHINE))
        return 0
    avg = sum(r["util_pct"] for r in day) / len(day)
    mx = max(r["util_pct"] for r in day)
    r3_days = roll3_dates()
    r3 = [r for r in rows if r["ts"].startswith(r3_days)]
    r3avg = (sum(r["util_pct"] for r in r3) / len(r3)) if r3 else avg
    kpi = callout_verdict(r3avg)
    print("gpu report %s machine=%s: n=%d avg=%.1f%% max=%.0f%% kpi=%s"
          % (today, MACHINE, len(day), avg, mx, kpi))
    print("%d-day rolling baseline (pre-registered, C-20260929-02): "
          "%s..%s n=%d avg=%.1f%% (call-out 30%%; 70%% target = "
          "directional ref)" % (ROLL_DAYS, r3_days[-1], r3_days[0],
                                len(r3), r3avg))
    inwin, complete = window(rows)
    if complete:
        wavg = sum(r["util_pct"] for r in inwin) / len(inwin)
        print("rolling %d-min window: n=%d avg=%.1f%% -> %s"
              % (WINDOW_MIN, len(inwin), wavg,
                 "IDLE" if wavg < IDLE_PCT else "busy"))
    else:
        print("rolling window incomplete (%d sample(s))" % len(inwin))
    return 0


def cmd_selftest():
    at = datetime.datetime(2026, 9, 28, 10, 0, 0)

    def mk(mins, u):
        return {"ts": (at - datetime.timedelta(minutes=mins)
                       ).isoformat(timespec="seconds"),
                "util_pct": u, "mem_used_mib": 0.0, "power_w": 0.0}

    rows = [mk(5, 10.0)]
    w, c = window(rows, at)
    assert not c and len(w) == 1, "single-sample window must be incomplete"
    rows.append(mk(20, 20.0))
    w, c = window(rows, at)
    assert c and len(w) == 2, "two samples within 30 min complete the window"
    rows.append(mk(59, 100.0))
    w, c = window(rows, at)
    assert len(w) == 2, "59-min-old sample must fall outside the window"
    assert c, "window must stay complete after adding an old sample"
    avg = sum(r["util_pct"] for r in w) / len(w)
    assert abs(avg - 15.0) < 1e-9, "window average math"
    assert avg < IDLE_PCT, "15% avg must classify as idle"
    busy = [mk(1, 80.0), mk(16, 90.0)]
    bavg = sum(r["util_pct"] for r in busy) / len(busy)
    assert bavg >= IDLE_PCT, "85% avg must classify as busy"
    assert CALL_PCT < IDLE_PCT, "C-20260929-02: call-out threshold sanity"
    assert ("PASS" if 35.0 >= CALL_PCT else "FLAG") == "PASS", \
        "35% avg must sit above the 30% call-out line"
    # C-20260929-02 seat-2/7: 3-day rolling baseline (pre-registered rule)
    b_days = roll3_dates(at)
    assert b_days[0] == "2026-09-28" and b_days[-1] == "2026-09-26", \
        "baseline spans today + 2 prior calendar days"
    day1 = {"ts": (at - datetime.timedelta(days=1, minutes=5)
                   ).isoformat(timespec="seconds"),
            "util_pct": 40.0, "mem_used_mib": 0.0, "power_w": 0.0}
    day2 = {"ts": (at - datetime.timedelta(days=2, minutes=5)
                   ).isoformat(timespec="seconds"),
            "util_pct": 40.0, "mem_used_mib": 0.0, "power_w": 0.0}
    spread = [mk(5, 20.0), day1, day2]
    in3 = [r for r in spread if r["ts"].startswith(b_days)]
    assert len(in3) == 3, "baseline must include today + 2 prior days"
    stale = {"ts": (at - datetime.timedelta(days=ROLL_DAYS, minutes=5)
                    ).isoformat(timespec="seconds"),
             "util_pct": 100.0, "mem_used_mib": 0.0, "power_w": 0.0}
    assert not stale["ts"].startswith(b_days), "day-3-old sample excluded"
    s_avg = spread[0]["util_pct"]
    b_avg = sum(r["util_pct"] for r in in3) / len(in3)
    assert s_avg < CALL_PCT, "same-day 20% sits below the call-out line"
    assert b_avg >= CALL_PCT, "3-day avg 33.3% must clear the line"
    assert callout_verdict(s_avg).startswith("FLAG"), "20% same-day FLAGS"
    assert callout_verdict(b_avg) == "PASS", "33.3% baseline PASSes"
    assert _parse_last_ts("| 2026-09-28T09:33:28 | IDLE x | y |") == \
        datetime.datetime(2026, 9, 28, 9, 33, 28), "dispatch ts parse"
    assert _parse_last_ts("not a dispatch row") is None, "non-row -> None"
    assert _parse_last_ts("| garbage | IDLE x | y |") is None, "bad ts -> None"
    print("selftest PASS: window completeness/exclusion/average/"
          "idle-vs-busy verdicts/call-out threshold/3-day rolling "
          "baseline/dispatch-ts parse ok")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample", help="take one nvidia-smi sample + maybe dispatch")
    sub.add_parser("report", help="same-day stats + daily KPI line")
    sub.add_parser("selftest", help="offline window/dispatch math checks")
    a = p.parse_args()
    return {"sample": cmd_sample, "report": cmd_report,
            "selftest": cmd_selftest}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
