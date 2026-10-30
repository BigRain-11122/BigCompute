"""Lane gate: the in-repo CONSUMER of the heartbeat lane fields (T171 (2) / BC-P-72).

Why this file exists
--------------------
`idle_selfcheck.check()` writes a machine line to state/heartbeat.txt every 10 min:

    2026-10-30 19:50:58 idle_selfcheck: verdict=busy ram_free_pct=55.3
    vram_free_mb=1458 py_cpu=40.0 cpu_lane_idle=true ...

Until now that broadcast had no consumer inside this repo (BC-P-72: "broadcast
with no reader on the wire"), so nobody could ask "may I light CPU lane work
right now?" and get a machine-readable answer. This tool is that reader.

Rules (pre-registered, no new tuning)
-------------------------------------
* Thresholds are imported from idle_selfcheck (RAM_FREE_PCT_MIN / VRAM_FREE_MB_MIN)
  so producer and consumer can never drift apart by a copied literal.
* Freshness: a line older than --max-age-min (default 30) is STALE. A stale line
  yields CLOSED for both lanes and exit 2 -- absence of a fresh reading is never
  treated as permission.
* CPU lane: OPEN requires BOTH the producer flag cpu_lane_idle=true AND a
  re-read of ram_free_pct >= RAM_FREE_PCT_MIN. Flag true with RAM below the
  threshold is reported as CONSISTENCY-MISMATCH and stays CLOSED (a stale or
  hand-edited token must not open a lane).
* GPU lane: OPEN requires vram_free_mb >= VRAM_FREE_MB_MIN (same headroom rule
  that gates GPU batch work).

Exit codes: 0 = parsed and fresh (lane verdicts valid), 2 = STALE,
3 = UNPARSED/MISSING (no idle_selfcheck line found).

Usage:
  python Tools/lane_gate.py run                 # human line
  python Tools/lane_gate.py run --json          # machine JSON to stdout
  python Tools/lane_gate.py run --json-out state/lane-gate.json
  python Tools/lane_gate.py selftest
"""
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE = os.path.join(ROOT, "state")
HEARTBEAT = os.path.join(STATE, "heartbeat.txt")

if HERE not in sys.path:
    sys.path.insert(0, HERE)

MACHINE = "bm-a"
MAX_AGE_MIN = 30.0
LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+idle_selfcheck:\s*(.+)$")
TOKEN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
TS_FMT = "%Y-%m-%d %H:%M:%S"

# Consumed heartbeat tokens -- this list IS the contract with idle_selfcheck.
CONSUMED_FIELDS = ("ram_free_pct", "vram_free_mb", "cpu_lane_idle")


def load_thresholds():
    """Import the producer's thresholds; refuse to guess if unavailable."""
    from idle_selfcheck import RAM_FREE_PCT_MIN, VRAM_FREE_MB_MIN
    return float(RAM_FREE_PCT_MIN), int(VRAM_FREE_MB_MIN)


def parse_line(line):
    """Parse one heartbeat line -> {ts, tokens} or None."""
    m = LINE_RE.match(line.strip())
    if not m:
        return None
    ts_raw, body = m.group(1), m.group(2)
    try:
        ts = datetime.datetime.strptime(ts_raw, TS_FMT)
    except ValueError:
        return None
    return {"ts": ts, "tokens": dict(TOKEN_RE.findall(body))}


def last_idle_line(path=HEARTBEAT):
    """Return (line, lineno) of the newest idle_selfcheck line, newest first."""
    if not os.path.exists(path):
        return None, 0
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    for i in range(len(lines) - 1, -1, -1):
        if "idle_selfcheck:" in lines[i]:
            return lines[i], i + 1
    return None, 0


def _to_float(tokens, key):
    try:
        return float(tokens[key])
    except (KeyError, TypeError, ValueError):
        return None


def _to_bool(tokens, key):
    v = tokens.get(key)
    if v is None:
        return None
    v = v.strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return None


def judge(tokens, age_min, ram_min, vram_min, max_age_min=MAX_AGE_MIN):
    """Pure judge over parsed tokens. Returns the verdict dict (no I/O)."""
    missing = [k for k in CONSUMED_FIELDS if k not in tokens]
    ram = _to_float(tokens, "ram_free_pct")
    vram = _to_float(tokens, "vram_free_mb")
    flag = _to_bool(tokens, "cpu_lane_idle")

    if missing or ram is None or vram is None or flag is None:
        return {
            "machine": MACHINE,
            "verdict": "UNPARSED",
            "missing": missing,
            "consumed_fields": list(CONSUMED_FIELDS),
            "cpu_lane": {"verdict": "CLOSED", "reason": "UNPARSED"},
            "gpu_lane": {"verdict": "CLOSED", "reason": "UNPARSED"},
            "exit": 3,
        }

    stale = age_min is None or age_min > max_age_min
    out = {
        "machine": MACHINE,
        "consumed_fields": list(CONSUMED_FIELDS),
        "readings": {"ram_free_pct": ram, "vram_free_mb": int(vram),
                     "cpu_lane_idle": flag},
        "thresholds": {"ram_free_pct_min": ram_min, "vram_free_mb_min": vram_min},
        "age_min": None if age_min is None else round(float(age_min), 1),
        "max_age_min": max_age_min,
        "stale": stale,
    }

    if stale:
        reason = "STALE(age>%smin)" % max_age_min
        out.update({
            "verdict": "STALE",
            "cpu_lane": {"verdict": "CLOSED", "reason": reason},
            "gpu_lane": {"verdict": "CLOSED", "reason": reason},
            "exit": 2,
        })
        return out

    if flag and ram < ram_min:
        cpu = {"verdict": "CLOSED",
               "reason": "CONSISTENCY-MISMATCH(flag=true,ram=%s<%s)" % (ram, ram_min)}
        consistency = "RAM-BELOW-IDLE-FLAG"
    elif flag:
        cpu = {"verdict": "OPEN",
               "reason": "cpu_lane_idle=true,ram=%s>=%s" % (ram, ram_min)}
        consistency = "OK"
    else:
        cpu = {"verdict": "CLOSED", "reason": "cpu_lane_idle=false,ram=%s" % ram}
        consistency = "OK"

    if vram >= vram_min:
        gpu = {"verdict": "OPEN",
               "reason": "vram_free=%d>=%d" % (int(vram), vram_min)}
    else:
        gpu = {"verdict": "CLOSED",
               "reason": "vram_free=%d<%d" % (int(vram), vram_min)}

    out.update({"verdict": "FRESH", "consistency": consistency,
                "cpu_lane": cpu, "gpu_lane": gpu, "exit": 0})
    return out


def run(path=HEARTBEAT, max_age_min=MAX_AGE_MIN, now=None):
    """Read the newest heartbeat line, judge it, return (verdict, source)."""
    ram_min, vram_min = load_thresholds()
    line, lineno = last_idle_line(path)
    if line is None:
        return {"machine": MACHINE, "verdict": "UNPARSED", "missing": ["heartbeat-line"],
                "consumed_fields": list(CONSUMED_FIELDS),
                "cpu_lane": {"verdict": "CLOSED", "reason": "NO-LINE"},
                "gpu_lane": {"verdict": "CLOSED", "reason": "NO-LINE"},
                "exit": 3}, {}
    parsed = parse_line(line)
    if parsed is None:
        return {"machine": MACHINE, "verdict": "UNPARSED", "missing": ["malformed-line"],
                "consumed_fields": list(CONSUMED_FIELDS),
                "cpu_lane": {"verdict": "CLOSED", "reason": "MALFORMED"},
                "gpu_lane": {"verdict": "CLOSED", "reason": "MALFORMED"},
                "exit": 3}, {}
    now = now or datetime.datetime.now()
    age_min = (now - parsed["ts"]).total_seconds() / 60.0
    verdict = judge(parsed["tokens"], age_min, ram_min, vram_min, max_age_min)
    verdict["source"] = {"file": os.path.relpath(path, ROOT), "line": lineno,
                         "ts": parsed["ts"].strftime(TS_FMT)}
    return verdict, verdict["source"]


def render_line(v):
    cpu, gpu = v.get("cpu_lane", {}), v.get("gpu_lane", {})
    src = v.get("source", {})
    return ("lane-gate: machine=%s verdict=%s CPU=%s(%s) GPU=%s(%s) src=%s@L%s age=%smin"
            % (v.get("machine", MACHINE), v.get("verdict"),
               cpu.get("verdict"), cpu.get("reason"),
               gpu.get("verdict"), gpu.get("reason"),
               src.get("ts", "-"), src.get("line", "-"), v.get("age_min")))


def cmd_run(args):
    path = HEARTBEAT
    max_age = MAX_AGE_MIN
    as_json = "--json" in args
    json_out = None
    for i, a in enumerate(args):
        if a == "--file" and i + 1 < len(args):
            path = args[i + 1]
        elif a == "--max-age-min" and i + 1 < len(args):
            max_age = float(args[i + 1])
        elif a == "--json-out" and i + 1 < len(args):
            json_out = args[i + 1]
    verdict, _ = run(path, max_age)
    if json_out:
        out_path = json_out if os.path.isabs(json_out) else os.path.join(ROOT, json_out)
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n")
    if as_json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print(render_line(verdict))
    return int(verdict.get("exit", 3))


def _line(ts, **tokens):
    body = " ".join("%s=%s" % (k, v) for k, v in tokens.items())
    return "%s idle_selfcheck: %s\n" % (ts, body)


def selftest():
    ok = True

    def chk(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print("  %-34s %s" % (name, "PASS" if cond else "FAIL"))

    ram_min, vram_min = load_thresholds()
    chk("thresholds-imported", ram_min > 0 and vram_min > 0)

    base = datetime.datetime(2026, 10, 30, 20, 0, 0)
    tok = {"ram_free_pct": "55.3", "vram_free_mb": "1458", "cpu_lane_idle": "true"}

    # 1 fresh / cpu open / gpu closed
    v = judge(tok, 5.0, ram_min, vram_min, 30.0)
    chk("fresh-cpu-open", v["cpu_lane"]["verdict"] == "OPEN"
        and v["gpu_lane"]["verdict"] == "CLOSED" and v["exit"] == 0)

    # 2 flag true but RAM below threshold -> closed + mismatch
    v = judge({"ram_free_pct": "20.0", "vram_free_mb": "1458",
               "cpu_lane_idle": "true"}, 5.0, ram_min, vram_min, 30.0)
    chk("flag-true-ram-low-closed", v["cpu_lane"]["verdict"] == "CLOSED"
        and v["consistency"] == "RAM-BELOW-IDLE-FLAG")

    # 3 flag false -> closed
    v = judge({"ram_free_pct": "60.0", "vram_free_mb": "1458",
               "cpu_lane_idle": "false"}, 5.0, ram_min, vram_min, 30.0)
    chk("flag-false-closed", v["cpu_lane"]["verdict"] == "CLOSED")

    # 4 vram high -> gpu open
    v = judge({"ram_free_pct": "60.0", "vram_free_mb": str(vram_min + 1),
               "cpu_lane_idle": "true"}, 5.0, ram_min, vram_min, 30.0)
    chk("gpu-headroom-open", v["gpu_lane"]["verdict"] == "OPEN")

    # 5 stale -> both closed, exit 2
    v = judge(tok, 31.0, ram_min, vram_min, 30.0)
    chk("stale-closes-both", v["verdict"] == "STALE" and v["exit"] == 2
        and v["cpu_lane"]["verdict"] == "CLOSED" and v["gpu_lane"]["verdict"] == "CLOSED")

    # 6 age boundary counts as fresh
    v = judge(tok, 30.0, ram_min, vram_min, 30.0)
    chk("age-boundary-fresh", v["verdict"] == "FRESH")

    # 7 missing token -> unparsed exit 3
    v = judge({"ram_free_pct": "55.3", "cpu_lane_idle": "true"}, 1.0,
              ram_min, vram_min, 30.0)
    chk("missing-token-unparsed", v["verdict"] == "UNPARSED" and v["exit"] == 3
        and "vram_free_mb" in v["missing"])

    # 8 malformed cpu_lane_idle token -> unparsed
    v = judge({"ram_free_pct": "55.3", "vram_free_mb": "1458",
               "cpu_lane_idle": "maybe"}, 1.0, ram_min, vram_min, 30.0)
    chk("bad-flag-value-unparsed", v["verdict"] == "UNPARSED" and v["exit"] == 3)

    # 9 line parser + newest-line selection over a synthetic file
    tmp = os.path.join(STATE, "_lane_gate_selftest.tmp")
    lines = [
        _line("2026-10-30 19:00:00", verdict="busy", ram_free_pct="11.1",
              vram_free_mb="100", py_cpu="90.0", cpu_lane_idle="false"),
        "2026-10-30 19:10:00 os-loop: round done exit=0\n",
        _line("2026-10-30 19:50:58", verdict="busy", ram_free_pct="55.3",
              vram_free_mb="1458", py_cpu="40.0", cpu_lane_idle="true"),
    ]
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    try:
        ln, no = last_idle_line(tmp)
        chk("newest-line-selected", no == 3 and "55.3" in ln)
        v, src = run(tmp, 30.0, now=datetime.datetime(2026, 10, 30, 20, 0, 0))
        chk("file-run-cpu-open", v["cpu_lane"]["verdict"] == "OPEN"
            and src["line"] == 3 and v["age_min"] == 9.0)
        v, src = run(tmp, 30.0, now=datetime.datetime(2026, 10, 30, 21, 0, 0))
        chk("file-run-stale", v["verdict"] == "STALE" and v["exit"] == 2)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    # 10 consumed-field list is the BC-P-72 contract
    chk("consumes-cpu_lane_idle", "cpu_lane_idle" in CONSUMED_FIELDS)

    print("LANE-GATE SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] == "selftest":
        return selftest()
    if args[0] == "run":
        return cmd_run(args[1:])
    print("unknown command: %s" % args[0], file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))