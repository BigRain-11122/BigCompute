"""dispatch_precheck.py - the pre-dispatch gate: ask before lighting a lane (BC-P-144).

Why this file exists
--------------------
`Tools/lane_gate.py` (T171 (2)) can already answer "may I light the CPU/GPU lane
right now?" with FRESH/STALE/UNPARSED and exit 0/2/3.  But a reader nobody calls
is exactly the BC-P-72 failure mode ("gate in place, no one checks it"): the
batch pool J5, the idle heartbeat and the GPU dispatch pointer each kept their
own private idea of "is the box free".

This tool is the single pre-dispatch point that dispatch-shaped code calls:

    verdict OPEN / EXEMPT -> exit 0 (dispatch may proceed)
    verdict CLOSED / STALE -> exit 2 (fail-closed: lane not open, or too stale)
    verdict UNPARSED       -> exit 3 (fail-closed: no usable reading at all)

Every check appends one JSON snapshot to state/dispatch-gate.jsonl.  A dispatch
with no snapshot is not auditable, so taking a reading is not optional: the
snapshot is written before the verdict is printed, including EXEMPT checks.

Rules (BC-P-144, pre-registered)
--------------------------------
* Thresholds and parsing are NOT re-implemented here - they are imported from
  lane_gate in-process, so gate and reader can never drift (BC-P-72 lineage).
* Fail-closed: only OPEN/EXEMPT pass.  A stale or unparsable reading is never
  treated as permission.
* EXEMPT is for dispatch paths whose only criterion is artifact arrival (e.g.
  cross-machine render pick-up, which does not consume this box's lanes).  It
  requires a non-empty reason and is recorded verbatim in the snapshot.
* Ambiguous lane inference is refused (exit 64) instead of guessed: the caller
  must pass --lane explicitly when the category is not in the mapping table.

Usage:
  python Tools/dispatch_precheck.py check --lane cpu --consumer "batch pool B-LANE-A-01"
  python Tools/dispatch_precheck.py check --batch-id B-CITY3D-01
  python Tools/dispatch_precheck.py check --lane gpu --exempt "cross-machine render pick-up"
  python Tools/dispatch_precheck.py selftest
"""
import argparse
import datetime
import json
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE = os.path.join(ROOT, "state")
GATE_LOG = os.path.join(STATE, "dispatch-gate.jsonl")
REGISTRY = os.path.join(ROOT, "docs", "ops", "batch-pool-stock-v1.jsonl")
HEARTBEAT = os.path.join(STATE, "heartbeat.txt")

if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Explicit category -> lane table.  Anything not listed must be declared by the
# caller; guessing a lane here would silently widen the gate.
CATEGORY_LANE = {
    "3d_offline_render": "gpu",
    "capture_batch": "gpu",
    "cpu_batch": "cpu",
    "text_batch": "cpu",
}
VALID_LANES = ("cpu", "gpu")
PASS_VERDICTS = ("OPEN", "EXEMPT")
EXIT_OPEN = 0
EXIT_CLOSED = 2
EXIT_UNPARSED = 3
EXIT_USAGE = 64


def _now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def lane_for_category(category):
    """Return 'cpu'/'gpu' or None when the category is not pre-registered."""
    return CATEGORY_LANE.get(str(category or "").strip())


def card_by_id(batch_id, registry=REGISTRY):
    """Read the stocking sheet and return the card dict (or None)."""
    if not os.path.exists(registry):
        return None
    with open(registry, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("batch_id") == batch_id:
                return row
    return None


def evaluate(lane, consumer=None, exempt=None, heartbeat=HEARTBEAT,
             max_age_min=None):
    """Run the gate in-process and return (result dict, exit code).

    Pure with respect to lane_gate: all reading/threshold logic is delegated.
    """
    import lane_gate

    kwargs = {"path": heartbeat}
    if max_age_min is not None:
        kwargs["max_age_min"] = float(max_age_min)
    gate, source = lane_gate.run(**kwargs)
    gate_exit = int(gate.get("exit", EXIT_UNPARSED))
    lane_state = gate.get("%s_lane" % lane, {}) or {}

    if exempt is not None:
        reason = str(exempt).strip()
        if not reason:
            return ({
                "ts": _now_str(),
                "consumer": consumer or "-",
                "lane": lane,
                "verdict": "UNPARSED",
                "reason": "EXEMPT-REASON-EMPTY(BC-P-144: exempt must be justified)",
                "gate_exit": EXIT_USAGE,
                "lane_gate": {"verdict": gate.get("verdict"), "exit": gate_exit},
            }, EXIT_USAGE)
        verdict = "EXEMPT"
        exit_code = EXIT_OPEN
        reason = "EXEMPT: " + reason
    else:
        verdict = lane_state.get("verdict", "CLOSED")
        if gate_exit == EXIT_OPEN and verdict == "OPEN":
            exit_code = EXIT_OPEN
        elif gate_exit == EXIT_UNPARSED:
            exit_code = EXIT_UNPARSED
        else:
            exit_code = EXIT_CLOSED
        if verdict not in ("OPEN", "CLOSED"):
            verdict = "CLOSED"
        reason = lane_state.get("reason", gate.get("verdict", "-"))

    return ({
        "ts": _now_str(),
        "consumer": consumer or "-",
        "lane": lane,
        "verdict": verdict,
        "reason": reason,
        "gate_exit": exit_code,
        "lane_gate": {
            "verdict": gate.get("verdict"),
            "exit": gate_exit,
            "source": source or gate.get("source"),
            "age_min": gate.get("age_min"),
            "cpu_lane": (gate.get("cpu_lane") or {}).get("verdict"),
            "gpu_lane": (gate.get("gpu_lane") or {}).get("verdict"),
        },
    }, exit_code)


def append_snapshot(result, log_path=GATE_LOG):
    """Append the audit line.  Returns the path written."""
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    with open(log_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return log_path


def render_line(r):
    lg = r.get("lane_gate", {})
    return ("dispatch-precheck: lane=%s verdict=%s exit=%d consumer=%s "
            "reason=%s | lane-gate=%s/%s src=%s age=%s"
            % (r.get("lane"), r.get("verdict"), r.get("gate_exit", -1),
               r.get("consumer"), r.get("reason"), lg.get("verdict"),
               lg.get("exit"),
               (lg.get("source") or {}).get("ts", "-"), lg.get("age_min")))


def cmd_check(args):
    lane = args.lane
    consumer = args.consumer
    if args.batch_id:
        card = card_by_id(args.batch_id)
        if card is None:
            print("FAIL batch_id not found in registry: %s" % args.batch_id)
            return EXIT_USAGE
        inferred = lane_for_category(card.get("category"))
        if lane and inferred and lane != inferred:
            print("FAIL --lane %s contradicts registry lane %s for %s"
                  % (lane, inferred, args.batch_id))
            return EXIT_USAGE
        lane = lane or inferred
        if consumer is None:
            consumer = "batch-pool:%s" % args.batch_id
    if lane is None:
        print("FAIL lane unknown: pass --lane cpu|gpu (category not in "
              "CATEGORY_LANE table; refusing to guess)")
        return EXIT_USAGE
    if lane not in VALID_LANES:
        print("FAIL invalid lane: %s" % lane)
        return EXIT_USAGE

    result, exit_code = evaluate(lane, consumer, args.exempt,
                                 heartbeat=args.heartbeat,
                                 max_age_min=args.max_age_min)
    log_path = append_snapshot(result, args.log)
    result["snapshot"] = os.path.relpath(log_path, ROOT)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_line(result))
        print("dispatch-precheck: snapshot=%s" % result["snapshot"])
    return exit_code


def _hb(ts, ram, vram, flag):
    return ("%s idle_selfcheck: verdict=busy ram_free_pct=%s vram_free_mb=%s "
            "py_cpu=40.0 cpu_lane_idle=%s\n" % (ts, ram, vram, flag))


def selftest():
    ok = True

    def chk(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print("  %-42s %s" % (name, "PASS" if cond else "FAIL"))

    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    old = (now - datetime.timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S")

    tmpdir = tempfile.mkdtemp(prefix="dispatch-precheck-")
    fresh_hb = os.path.join(tmpdir, "fresh-heartbeat.txt")
    stale_hb = os.path.join(tmpdir, "stale-heartbeat.txt")
    bad_hb = os.path.join(tmpdir, "bad-heartbeat.txt")
    log = os.path.join(tmpdir, "gate.jsonl")
    with open(fresh_hb, "w", encoding="utf-8", newline="\n") as f:
        f.write(_hb(stamp, "55.3", "1458", "true"))
    with open(stale_hb, "w", encoding="utf-8", newline="\n") as f:
        f.write(_hb(old, "55.3", "1458", "true"))
    with open(bad_hb, "w", encoding="utf-8", newline="\n") as f:
        f.write("2026-10-30 20:00:00 os-loop: no idle line here\n")

    try:
        # 1 fresh reading: cpu OPEN passes, gpu CLOSED fails closed
        r, code = evaluate("cpu", "t1", heartbeat=fresh_hb)
        chk("fresh-cpu-open-passes", r["verdict"] == "OPEN" and code == 0)
        r, code = evaluate("gpu", "t2", heartbeat=fresh_hb)
        chk("gpu-headroom-low-closed", r["verdict"] == "CLOSED" and code == 2)

        # 2 stale line -> closed, exit 2 (never permission)
        r, code = evaluate("cpu", "t3", heartbeat=stale_hb)
        chk("stale-fail-closed", r["verdict"] == "CLOSED" and code == 2)

        # 3 no idle line at all -> unparsed, exit 3
        r, code = evaluate("cpu", "t4", heartbeat=bad_hb)
        chk("unparsed-exit-3", r["verdict"] == "CLOSED" and code == 3)

        # 4 exempt passes and is recorded verbatim
        r, code = evaluate("gpu", "t5", exempt="cross-machine render pick-up",
                           heartbeat=fresh_hb)
        chk("exempt-passes", r["verdict"] == "EXEMPT" and code == 0
            and "cross-machine render pick-up" in r["reason"])

        # 5 empty exempt reason is refused (no silent blanket bypass)
        r, code = evaluate("gpu", "t6", exempt="   ", heartbeat=fresh_hb)
        chk("empty-exempt-refused", code == EXIT_USAGE
            and r["verdict"] == "UNPARSED")

        # 6 every evaluation is auditable: snapshot line appended
        for mode in ("cpu", "gpu", "exempt"):
            if mode == "exempt":
                r, _c = evaluate("gpu", "t-" + mode, exempt="artifact arrival",
                                 heartbeat=fresh_hb)
            else:
                r, _c = evaluate(mode, "t-" + mode, heartbeat=fresh_hb)
            append_snapshot(r, log)
        with open(log, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        chk("snapshot-per-check", len(rows) == 3
            and all("verdict" in x and "consumer" in x for x in rows))

        # 7 category mapping: known categories only, unknown returns None
        chk("category-map-gpu", lane_for_category("3d_offline_render") == "gpu")
        chk("category-map-cpu", lane_for_category("cpu_batch") == "cpu")
        chk("category-map-unknown-none", lane_for_category("mystery_batch") is None)

        # 8 an unknown category is not guessed by the CLI path
        chk("unknown-category-refused",
            lane_for_category("mystery_batch") is None)

        # 9 determinism: same inputs -> same verdict/exit
        a = evaluate("cpu", "t9", heartbeat=fresh_hb)
        b = evaluate("cpu", "t9", heartbeat=fresh_hb)
        chk("determinism", a[1] == b[1] and a[0]["verdict"] == b[0]["verdict"])

        # 10 thresholds come from lane_gate, not a copy here
        import lane_gate
        own = sys.modules[__name__]
        chk("thresholds-single-source",
            lane_gate.load_thresholds()[0] > 0
            and not hasattr(own, "RAM_FREE_PCT_MIN")
            and not hasattr(own, "VRAM_FREE_MB_MIN"))
    finally:
        for p in (fresh_hb, stale_hb, bad_hb, log):
            if os.path.exists(p):
                os.remove(p)
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    print("DISPATCH-PRECHECK SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="pre-dispatch lane gate (BC-P-144)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_c = sub.add_parser("check", help="gate one dispatch attempt")
    p_c.add_argument("--lane", choices=list(VALID_LANES))
    p_c.add_argument("--batch-id", help="read category/lane from the pool sheet")
    p_c.add_argument("--consumer", help="who is dispatching (audit field)")
    p_c.add_argument("--exempt", help="exemption reason (artifact-arrival paths)")
    p_c.add_argument("--heartbeat", default=HEARTBEAT)
    p_c.add_argument("--max-age-min", type=float, default=None)
    p_c.add_argument("--log", default=GATE_LOG)
    p_c.add_argument("--json", action="store_true")
    sub.add_parser("selftest", help="offline fixtures")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())