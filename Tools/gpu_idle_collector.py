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
  D-20260930-36 R-C1 add-on: benchmark GAP line = 50% evaluated on the
  7-day rolling average (head-firm compare; <50% = structural failure).
  Gap line is NOT an enforcement line: the sole call-out threshold stays
  30% (C-20260929-02), and the 70% target stays a directional reference
  pending threshold calibration R- receipts (D-20260930-36).
- loadline: O-2026-0930-015 item 3 night-ledger FULL-LOAD line for the
  fleet report (util time-share + in-flight batches + GREEN-IDLE
  call-out) plus P-32 data-source fields (cpu_util_pct / total_ram_gb /
  prod_lanes). The bm-a heartbeat WRITER itself belongs to the Biggame
  A-machine window (O-2026-0930-010 item 3, NO_TS + P-32 debt); this
  tool only PROVIDES machine metrics - no writer edit, no cross-repo
  write.
- selftest: offline window/dispatch math checks (no nvidia-smi needed).

Usage (run from repo root):
  python Tools/gpu_idle_collector.py sample
  python Tools/gpu_idle_collector.py report
  python Tools/gpu_idle_collector.py loadline
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
BENCH_DAYS = 7     # D-20260930-36 R-C1: benchmark horizon (head-firm compare)
BENCH_PCT = 50.0   # D-20260930-36 R-C1: 7-day avg <50% = structural failure
MACHINE = "bm-a"   # local machine tag -> weekly per-machine ledger row
BUSY_PCT = 30.0   # loadline time-share busy line (= sole call-out line)
QUIET_PCT = 10.0  # loadline time-share quiet line
BATCH_POOL = os.path.join(HERE, "..", "docs", "ops",
                          "batch-pool-stock-v1.jsonl")
RESIDENT_LANES = [("serve-ollama", "http://127.0.0.1:11434/"),
                  ("resident-qa-8792", "http://127.0.0.1:8792/health")]
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


def roll_dates(n_days, at=None):
    """Calendar dates covered by an n-day rolling window (today + n-1 back)."""
    at = at or now()
    return tuple((at.date() - datetime.timedelta(days=i)).isoformat()
                 for i in range(n_days))


def roll3_dates(at=None):
    """Calendar dates covered by the rolling baseline (today + N-1 back)."""
    return roll_dates(ROLL_DAYS, at)


def callout_verdict(avg):
    """C-20260929-02 seat-2/seat-7: sole call-out gate = 30% measured on
    the pre-registered 3-day rolling baseline (not a same-day snapshot)."""
    return ("PASS" if avg >= CALL_PCT
            else "FLAG(<30%% call-out threshold on %d-day rolling "
                 "baseline, C-20260929-02)" % ROLL_DAYS)


def bench_verdict(avg):
    """D-20260930-36 R-C1: benchmark gap line (CoreWeave/Lambda head-firm
    compare). Gap line only -- the sole call-out threshold stays 30%."""
    return ("BELOW-BENCH(<50%% structural-failure line on %d-day avg, "
            "R-C1 D-20260930-36)" % BENCH_DAYS if avg < BENCH_PCT
            else "AT-OR-ABOVE-BENCH(R-C1 50%% line, D-20260930-36)")


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
    b7_days = roll_dates(BENCH_DAYS)
    b7 = [r for r in rows if r["ts"].startswith(b7_days)]
    b7avg = (sum(r["util_pct"] for r in b7) / len(b7)) if b7 else avg
    print("%d-day benchmark (D-20260930-36 R-C1, head-firm gap line): "
          "%s..%s n=%d avg=%.1f%% -> %s (gap line only; sole call-out "
          "stays 30%%; 70%% target = directional ref pending R- "
          "threshold-calibration receipts)"
          % (BENCH_DAYS, b7_days[-1], b7_days[0], len(b7), b7avg,
             bench_verdict(b7avg)))
    inwin, complete = window(rows)
    if complete:
        wavg = sum(r["util_pct"] for r in inwin) / len(inwin)
        print("rolling %d-min window: n=%d avg=%.1f%% -> %s"
              % (WINDOW_MIN, len(inwin), wavg,
                 "IDLE" if wavg < IDLE_PCT else "busy"))
    else:
        print("rolling window incomplete (%d sample(s))" % len(inwin))
    return 0


def time_share(day):
    """O-2026-0930-015 item 3: util time-share over the day's samples."""
    n = len(day)
    busy = sum(1 for r in day if r["util_pct"] >= BUSY_PCT)
    quiet = sum(1 for r in day if r["util_pct"] < QUIET_PCT)
    return busy * 100.0 / n, quiet * 100.0 / n


def pool_status(path=BATCH_POOL):
    """Batch pool census: (cards, in_flight, standby_lanes) per J5 gate."""
    cards, in_flight, lanes = 0, 0, set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                cards += 1
                if str(row.get("status", "")).upper() != "STOCKED":
                    in_flight += 1
                lane = row.get("lane")
                if lane:
                    lanes.add(lane)
    return cards, in_flight, len(lanes)


def lane_alive(url, timeout=1.5):
    """Read-only localhost liveness probe for one resident lane."""
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def cmd_loadline():
    rows = load_samples()
    today = now().date().isoformat()
    day = [r for r in rows if r["ts"].startswith(today)]
    if day:
        busy_pct, quiet_pct = time_share(day)
        avg = sum(r["util_pct"] for r in day) / len(day)
        share = ("gpu_ts_share busy>=30%%=%.1f%% quiet<10%%=%.1f%% "
                 "n=%d avg=%.1f%%" % (busy_pct, quiet_pct, len(day), avg))
    else:
        share = "gpu_ts_share no-samples-today"
    r3_days = roll3_dates()
    r3 = [r for r in rows if r["ts"].startswith(r3_days)]
    r3avg = (sum(r["util_pct"] for r in r3) / len(r3)) if r3 else 0.0
    kpi = callout_verdict(r3avg)
    cards, in_flight, lanes = pool_status()
    alive = sum(1 for _n, u in RESIDENT_LANES if lane_alive(u))
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=1)
        ram = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        cpu, ram = None, None
    p32 = "p32 cpu_util_pct=%s total_ram_gb=%s prod_lanes=%d " \
          "(alive=%d resident + %d standby pool lanes)" % (
              ("%.0f" % cpu) if cpu is not None else "n/a",
              ("%.1f" % ram) if ram is not None else "n/a",
              alive + lanes, alive, lanes)
    print("loadline %s machine=%s: %s | in_flight=%d/%d cards | "
          "green_idle_callout=%s (3-day %s..%s n=%d avg=%.1f%%, sole "
          "line 30%%) | %s"
          % (today, MACHINE, share, in_flight, cards, kpi,
             r3_days[-1], r3_days[0], len(r3), r3avg, p32))
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
    # D-20260930-36 R-C1: 7-day benchmark gap line (not an enforcement line)
    b7_days = roll_dates(BENCH_DAYS, at)
    assert b7_days[0] == "2026-09-28" and b7_days[-1] == "2026-09-22", \
        "benchmark spans today + 6 prior calendar days"
    assert roll_dates(ROLL_DAYS, at) == roll3_dates(at), \
        "generic roller must reproduce the 3-day baseline exactly"
    assert BENCH_PCT > CALL_PCT, "R-C1 gap line sits above the call-out line"
    week = [{"ts": (at - datetime.timedelta(days=d, minutes=5)
                    ).isoformat(timespec="seconds"),
             "util_pct": 46.0, "mem_used_mib": 0.0, "power_w": 0.0}
            for d in range(BENCH_DAYS)]
    in7 = [r for r in week if r["ts"].startswith(b7_days)]
    assert len(in7) == BENCH_DAYS, "benchmark window includes all 7 days"
    older7 = {"ts": (at - datetime.timedelta(days=BENCH_DAYS, minutes=5)
                     ).isoformat(timespec="seconds"),
              "util_pct": 100.0, "mem_used_mib": 0.0, "power_w": 0.0}
    assert not older7["ts"].startswith(b7_days), "day-7-old sample excluded"
    w7avg = sum(r["util_pct"] for r in in7) / len(in7)
    assert abs(w7avg - 46.0) < 1e-9, "7-day average math"
    assert bench_verdict(w7avg).startswith("BELOW-BENCH"), \
        "46% 7-day avg must read as below the 50% line"
    assert bench_verdict(50.0).startswith("AT-OR-ABOVE-BENCH"), \
        "50% exactly = at the line, not below it"
    assert bench_verdict(51.0).startswith("AT-OR-ABOVE-BENCH"), \
        "51% 7-day avg clears the gap line"
    # O-2026-0930-015 item 3: loadline time-share + pool gate + probe
    share_rows = [{"util_pct": v} for v in (35.0, 80.0, 5.0, 25.0, 2.0)]
    b_pct, q_pct = time_share(share_rows)
    assert abs(b_pct - 40.0) < 1e-9 and abs(q_pct - 40.0) < 1e-9, \
        "time-share math: 2/5 busy (>=30), 2/5 quiet (<10)"
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(json.dumps({"lane": "L1", "status": "STOCKED"}) + "\n")
        tf.write(json.dumps({"lane": "L1", "status": "STOCKED"}) + "\n")
        tf.write(json.dumps({"lane": "L2", "status": "IN_FLIGHT"}) + "\n")
        tmp_path = tf.name
    try:
        c, inf, ln = pool_status(tmp_path)
        assert (c, inf, ln) == (3, 1, 2), \
            "pool census: 3 cards, 1 in-flight, 2 distinct lanes"
    finally:
        os.unlink(tmp_path)
    assert not lane_alive("http://127.0.0.1:1/", timeout=0.5), \
        "closed port must probe dead (probe guard)"
    print("selftest PASS: window completeness/exclusion/average/"
          "idle-vs-busy verdicts/call-out threshold/3-day rolling "
          "baseline/7-day R-C1 benchmark line/dispatch-ts parse/"
          "O-015 loadline time-share+pool census+probe guard ok")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample", help="take one nvidia-smi sample + maybe dispatch")
    sub.add_parser("report", help="same-day stats + daily KPI line")
    sub.add_parser("loadline",
                   help="O-015 night-ledger full-load line + P-32 fields")
    sub.add_parser("selftest", help="offline window/dispatch math checks")
    a = p.parse_args()
    return {"sample": cmd_sample, "report": cmd_report,
            "loadline": cmd_loadline, "selftest": cmd_selftest}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
