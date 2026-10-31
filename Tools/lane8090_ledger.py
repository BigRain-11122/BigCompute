#!/usr/bin/env python3
"""lane8090_ledger.py - persistent probe history for the :8090 tombstone (T181).

Why: D-20261031-05 (committee, 18:0x) kept the D-20261029-11 tombstone and
pre-registered the only way out -- "3 consecutive probes UP spanning >= 24h",
after which BigCompute itself writes the "exit tombstone" row on the STATUS
face and waits for committee verification. state/heartbeat.txt already carries
a lane8090=<token> reading per round, but a heartbeat line is a snapshot, not
an evaluable series: nothing proves the 3 probes were consecutive, and nothing
measures the span. This file is the missing series.

Design (advisory by construction):
  * append-only JSONL (state/lane8090-probe-log.jsonl), one row per probe:
        {"ts": "2026-10-31T18:15:03", "token": "DARK[DOWN]"}
  * token vocabulary is owned by Tools/idle_selfcheck.probe_lane8090
    (UP / DARK[OCCUPIED] / DARK[DOWN] / DARK[httpN]) -- imported, not copied.
  * the verdict is computed from the trailing run of consecutive UP rows only:
    a single later DOWN resets the run (that is what "consecutive" costs).
  * duplicate guard: the same token within 15 minutes is not appended twice, so
    a round that probes twice cannot fake three probes.

CLI:
  python Tools/lane8090_ledger.py probe        # probe once, append, print token
  python Tools/lane8090_ledger.py status       # verdict + evidence summary
  python Tools/lane8090_ledger.py status --json
  python Tools/lane8090_ledger.py selftest

Exit codes: probe/status always 0 (advisory, fail-open); selftest 0 only when
every assertion holds (non-zero = broken, fix before the 11-02 03:07 point).
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "state" / "lane8090-probe-log.jsonl"
NEED_RUN = 3          # D-20261031-05 (2): 3 consecutive UP probes
NEED_SPAN_H = 24.0    # D-20261031-05 (2): span >= 24h
DUP_WINDOW_S = 900    # same token inside 15 min is not a new probe
TS_FMT = "%Y-%m-%dT%H:%M:%S"


def load_entries(path=None):
    """Read the JSONL ledger; malformed rows are skipped (never crash a round)."""
    p = Path(path or LEDGER)
    out = []
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                ts = datetime.strptime(row["ts"], TS_FMT)
                token = str(row["token"])
            except Exception:
                continue
            out.append({"ts": ts, "token": token})
    return out


def evaluate(entries):
    """Verdict for the pre-registered resurrection criteria.

    Only the trailing consecutive-UP run counts. verdict=MET requires both
    run_len >= 3 and (last_ts - first_ts_of_run) >= 24h.
    """
    run = []
    for row in reversed(entries):
        if row["token"] == "UP":
            run.append(row)
        else:
            break
    run.reverse()
    if not run:
        reason = "no trailing UP probe (last=%s)" % (
            entries[-1]["token"] if entries else "none")
        return {"verdict": "NOT-MET", "reason": reason, "run_len": 0,
                "span_h": 0.0, "last": entries[-1]["ts"] if entries else None}
    span_h = (run[-1]["ts"] - run[0]["ts"]).total_seconds() / 3600.0
    if len(run) < NEED_RUN:
        reason = "run_len=%d < %d" % (len(run), NEED_RUN)
    elif span_h < NEED_SPAN_H:
        reason = "span=%.2fh < %.0fh" % (span_h, NEED_SPAN_H)
    else:
        reason = "run_len=%d span=%.2fh" % (len(run), span_h)
    verdict = "MET" if (len(run) >= NEED_RUN and span_h >= NEED_SPAN_H) else "NOT-MET"
    return {"verdict": verdict, "reason": reason, "run_len": len(run),
            "span_h": round(span_h, 2), "last": run[-1]["ts"]}


def append_probe(token, path=None, now=None):
    """Append one probe row; returns (appended, reason)."""
    p = Path(path or LEDGER)
    now = now or datetime.now()
    entries = load_entries(p)
    if entries and entries[-1]["token"] == token:
        age = (now - entries[-1]["ts"]).total_seconds()
        if 0 <= age < DUP_WINDOW_S:
            return False, "duplicate %s within %ds (not appended)" % (token, int(age))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now.strftime(TS_FMT), "token": token},
                            ensure_ascii=False) + "\n")
    return True, "appended"


def _live_probe():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from idle_selfcheck import probe_lane8090  # single source of the token vocabulary
    return probe_lane8090()


def cmd_probe():
    token = _live_probe()
    appended, why = append_probe(token)
    print("lane8090=%s %s" % (token, why))
    return 0


def cmd_status(as_json=False):
    entries = load_entries()
    res = evaluate(entries)
    res["probes_total"] = len(entries)
    res["criteria"] = ">=%d consecutive UP with span >= %gh" % (NEED_RUN, NEED_SPAN_H)
    res["ledger"] = str(LEDGER)
    if as_json:
        print(json.dumps(res, ensure_ascii=False, default=str, indent=2))
    else:
        print("verdict=%s reason=%s run_len=%d span_h=%.2f probes=%d"
              % (res["verdict"], res["reason"], res["run_len"],
                 res["span_h"], len(entries)))
        print("criteria=%s" % res["criteria"])
        tail = entries[-3:]
        print("tail=%s" % ", ".join("%s:%s" % (e["ts"].strftime(TS_FMT), e["token"])
                                    for e in tail) if tail else "tail=empty")
        if res["verdict"] == "MET":
            print("ACTION: write 'exit tombstone' row on STATUS face, then wait "
                  "for committee verification (D-20261031-05 (2)).")
    return 0


def selftest():
    fails = []
    base = datetime(2026, 10, 30, 3, 7, 0)

    def rows(spec):
        return [{"ts": base + timedelta(hours=h), "token": t} for h, t in spec]

    # 1) three UP over 25h -> MET
    r = evaluate(rows([(0, "UP"), (12, "UP"), (25, "UP")]))
    if r["verdict"] != "MET":
        fails.append("3UP/25h expected MET got %s" % r)
    # 2) three UP inside 2h -> NOT-MET (span)
    r = evaluate(rows([(0, "UP"), (1, "UP"), (2, "UP")]))
    if r["verdict"] != "NOT-MET" or "span" not in r["reason"]:
        fails.append("3UP/2h expected NOT-MET(span) got %s" % r)
    # 3) a later DOWN breaks the run even with 3 old UPs
    r = evaluate(rows([(0, "UP"), (12, "UP"), (24, "UP"), (30, "DARK[DOWN]")]))
    if r["verdict"] != "NOT-MET" or r["run_len"] != 0:
        fails.append("UPx3 then DOWN expected run_len=0 got %s" % r)
    # 4) only two UP -> NOT-MET (count)
    r = evaluate(rows([(0, "UP"), (25, "UP")]))
    if r["verdict"] != "NOT-MET" or "run_len" not in r["reason"]:
        fails.append("2UP expected NOT-MET(count) got %s" % r)
    # 5) empty ledger -> NOT-MET, no crash
    r = evaluate([])
    if r["verdict"] != "NOT-MET":
        fails.append("empty expected NOT-MET got %s" % r)
    # 6) malformed rows skipped by loader, valid row survives
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "l.jsonl"
        p.write_text('{"ts":"2026-10-31T18:00:00","token":"UP"}\n'
                     'not json\n'
                     '{"ts":"bad","token":"UP"}\n', encoding="utf-8")
        if len(load_entries(p)) != 1:
            fails.append("loader kept %d rows, expected 1" % len(load_entries(p)))
        # 7) duplicate guard: same token twice inside the window appends once
        p2 = Path(td) / "dup.jsonl"
        ok1, _ = append_probe("UP", p2, now=datetime(2026, 10, 31, 18, 5, 0))
        ok2, why2 = append_probe("UP", p2, now=datetime(2026, 10, 31, 18, 10, 0))
        if not ok1 or ok2 or "duplicate" not in why2:
            fails.append("dup guard failed: ok1=%s ok2=%s why=%s" % (ok1, ok2, why2))
        # 8) different token breaks the guard (a DOWN is always real news)
        ok3, _ = append_probe("DARK[DOWN]", p2, now=datetime(2026, 10, 31, 18, 21, 0))
        if not ok3:
            fails.append("token change was suppressed by dup guard")

    if fails:
        for f in fails:
            print("SELFTEST FAIL: %s" % f)
        print("SELFTEST FAILED (%d)" % len(fails))
        return 1
    print("SELFTEST PASS (8/8)")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "probe":
        return cmd_probe()
    if cmd == "status":
        return cmd_status(as_json="--json" in argv[2:])
    if cmd == "selftest":
        return selftest()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))