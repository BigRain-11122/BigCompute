"""pool_igniter.py - gate-then-run igniter for stocked batch pool cards.

Why this exists (D-20261108-09): the pool read `stock_dispatchable=4` while
`pool_effective=0` for days.  Four cards sat STOCKED because the lane gate was
closed every time a round looked at them (GPU busy with an editor session, RAM
under the CPU-lane floor), and a round can only look once.  Watching is not
dispatching: this tool waits for a lane to actually open, takes the claim
*before* running (BC-P-144), runs the card's workload, and writes a receipt
with artifact hashes.  Nothing is bypassed - a closed lane means a quiet no-op.

Contract:
  * gate first, in-process, via dispatch_precheck.evaluate (single source of
    thresholds; no re-implementation here)
  * one card at a time, one run per tick, lock file prevents overlap
  * a run writes media/pool-ignite/<batch_id>/<stamp>/ + appends one line to
    state/pool-ignitions.jsonl (append-only receipt: exit code, seconds,
    artifact paths + sha256) - that line is the pool's "effective" evidence
  * silent by construction: no console output on the no-op path, log to
    state/pool-igniter.log

Usage:
  python Tools/pool_igniter.py tick          # one watch tick (task entry)
  python Tools/pool_igniter.py run --batch-id B-CITY3D-01   # force one ignition
  python Tools/pool_igniter.py status        # receipts + lane readings
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

LOG = os.path.join(ROOT, "state", "pool-igniter.log")
LOCK = os.path.join(ROOT, "state", "pool-igniter.lock")
RECEIPTS = os.path.join(ROOT, "state", "pool-ignitions.jsonl")
OUT_ROOT = os.path.join(ROOT, "media", "pool-ignite")
TUANJIE = r"C:\Program Files\Tuanjie\Hub\Editor\2022.3.62t15\Editor\Tuanjie.exe"
BAKE_PROJECT = os.path.abspath(os.path.join(
    os.path.dirname(ROOT), "..", ".codely-cli", "labbench",
    "city3d-bake-spike", "BakeSpike"))
EDITOR_TIMEOUT_S = 1800

# Only cards with a payload that actually exists on this box are runnable.
# Everything else stays a named gate reason instead of a fake ignition.
RUNNERS = {
    "B-CITY3D-01": "bake_spike",
}


def now():
    return datetime.datetime.now()


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8", newline="\n") as f:
            f.write("%s %s\n" % (now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def card(batch_id):
    import batch_pool
    for row in batch_pool.load_rows(batch_pool.REGISTRY):
        if row.get("batch_id") == batch_id:
            return row
    return None


def lane_verdict(batch_id):
    """Read the lane gate for one card without writing a snapshot.

    A snapshot per tick per card would bury the audit log in refusals; the
    claim snapshot is written only when a run actually proceeds.
    """
    import dispatch_precheck
    c = card(batch_id)
    if c is None:
        return None, "card-not-found"
    lane, source = dispatch_precheck.lane_for_card(c)
    if lane is None:
        return None, "lane-unresolved"
    import lane_gate
    gate, _src = lane_gate.run()
    state = gate.get("%s_lane" % lane) or {}
    return lane, "%s/%s" % (state.get("verdict", "?"),
                            state.get("reason", gate.get("verdict", "?")))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_bake_spike(outdir):
    """B-CITY3D-01 payload: CPU lightmapper bake in the labbench spike project."""
    os.makedirs(outdir, exist_ok=True)
    metrics = os.path.join(outdir, "bake-metrics.json")
    logfile = os.path.join(outdir, "tuanjie-bake.log")
    exe = TUANJIE if os.path.exists(TUANJIE) else "Tuanjie.exe"
    env = dict(os.environ)
    env["BAKE_SPIKE_LIGHTMAPPER"] = "ProgressiveCPU"
    env["BAKE_SPIKE_OUT"] = metrics
    cmd = [exe, "-batchmode", "-projectPath", BAKE_PROJECT,
           "-executeMethod", "City3DBakeSpike.Entry.Run", "-logFile", logfile]
    started = time.time()
    with open(os.path.join(outdir, "runner.txt"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write("cmd: %s\nenv: BAKE_SPIKE_LIGHTMAPPER=ProgressiveCPU\n"
                "project: %s\nstarted: %s\n"
                % (" ".join(cmd), BAKE_PROJECT, now().strftime("%Y-%m-%d %H:%M:%S")))
    try:
        proc = subprocess.run(cmd, env=env, cwd=outdir,
                              timeout=EDITOR_TIMEOUT_S,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.STDOUT)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = 124
    except OSError as e:
        code = 125
        with open(os.path.join(outdir, "runner.txt"), "a", encoding="utf-8",
                  newline="\n") as f:
            f.write("spawn-error: %s\n" % e)
    elapsed = round(time.time() - started, 1)
    artifacts = []
    for p in [metrics] + sorted(glob.glob(os.path.join(
            BAKE_PROJECT, "Assets", "**", "Lightmap-*.exr"), recursive=True)):
        if os.path.exists(p):
            artifacts.append({"path": os.path.relpath(p, ROOT).replace("\\", "/"),
                              "bytes": os.path.getsize(p),
                              "sha256": sha256(p)})
    metrics_doc = {}
    if os.path.exists(metrics):
        try:
            with open(metrics, encoding="utf-8") as f:
                metrics_doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            metrics_doc = {}
    ok = (code == 0 and bool(metrics_doc.get("bake_ok"))
          and int(metrics_doc.get("lightmap_files") or 0) > 0)
    return {
        "kind": "bake_spike",
        "exit": code,
        "seconds": elapsed,
        "ok": ok,
        "metrics": metrics_doc,
        "artifacts": artifacts,
    }


def ignite(batch_id, force=False):
    """Gate, claim, run, receipt.  Returns (ok, receipt dict)."""
    if batch_id not in RUNNERS:
        return False, {"batch_id": batch_id, "skipped": "no-local-payload"}
    lane, state = lane_verdict(batch_id)
    if lane is None:
        return False, {"batch_id": batch_id, "skipped": state}
    import batch_pool
    if not force and not state.startswith("OPEN"):
        return False, {"batch_id": batch_id, "skipped": "lane-closed:" + state}
    # claim before dispatch: batch_pool.cmd_gate appends the snapshot itself.
    gate_code = batch_pool.cmd_gate(batch_pool.REGISTRY, batch_id)
    if gate_code != 0 and not force:
        log("claim refused %s code=%s" % (batch_id, gate_code))
        return False, {"batch_id": batch_id, "skipped": "claim-refused:%s" % gate_code}
    stamp = now().strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(OUT_ROOT, batch_id, stamp)
    result = run_bake_spike(outdir) if RUNNERS[batch_id] == "bake_spike" else {}
    receipt = {
        "ts": now().strftime("%Y-%m-%d %H:%M:%S"),
        "batch_id": batch_id,
        "lane": lane,
        "lane_gate": state,
        "claim_exit": gate_code,
        "outdir": os.path.relpath(outdir, ROOT).replace("\\", "/"),
        "ok": bool(result.get("ok")),
        "runner": result,
    }
    os.makedirs(os.path.dirname(RECEIPTS), exist_ok=True)
    with open(RECEIPTS, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    log("ignite %s lane=%s claim=%s ok=%s exit=%s sec=%s"
        % (batch_id, lane, gate_code, receipt["ok"],
           result.get("exit"), result.get("seconds")))
    return receipt["ok"], receipt


def tick():
    """One watch tick: try each runnable card once, stop after the first run."""
    if os.path.exists(LOCK):
        try:
            age = time.time() - os.path.getmtime(LOCK)
        except OSError:
            age = 0
        if age < EDITOR_TIMEOUT_S + 300:
            return 0                              # a run is in flight
        os.remove(LOCK)
    tried = []
    for batch_id in sorted(RUNNERS):
        lane, state = lane_verdict(batch_id)
        if lane is None or not state.startswith("OPEN"):
            tried.append("%s:%s" % (batch_id, state))
            continue
        with open(LOCK, "w", encoding="utf-8", newline="\n") as f:
            f.write(str(os.getpid()))
        try:
            ok, receipt = ignite(batch_id)
            return 0 if ok else 1
        finally:
            try:
                os.remove(LOCK)
            except OSError:
                pass
    log("tick idle: " + "; ".join(tried))
    return 0


def payload_of(batch_id):
    """Local executable payload for a card, or None when this box cannot run it.

    A card can be dispatchable (named consumer, unlocked, not in flight) and
    still be non-runnable here - no payload on this box means the ignition
    chain has nothing to execute, and that must be a named reading rather than
    a silent skip inside tick().
    """
    return RUNNERS.get(batch_id)


def receipt_rows():
    if not os.path.exists(RECEIPTS):
        return []
    rows = []
    with open(RECEIPTS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"ok": False, "ts": "-", "corrupt": True})
    return rows


def last_tick_age_min():
    """Minutes since the last tick() line.  None when the log is unreadable.

    The watcher is only useful if it is actually ticking; a dead watcher and a
    permanently closed lane look identical from the pool's `effective=0`.
    """
    if not os.path.exists(LOG):
        return None
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            lines = [x for x in f.read().splitlines() if x.strip()]
    except OSError:
        return None
    if not lines:
        return None
    parts = lines[-1].split(" ", 2)
    if len(parts) < 2:
        return None
    try:
        ts = datetime.datetime.strptime(parts[0] + " " + parts[1],
                                       "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return round((now() - ts).total_seconds() / 60.0, 1)


def readout():
    """Two-pool honesty readout for the ignition chain.

    `dispatchable` comes from batch_pool's own rule (single source); `runnable`
    counts cards with a payload on this box.  The gap is the part of the pool
    that no watcher can ever ignite, however long the window stays open.
    """
    import batch_pool
    rows = batch_pool.load_rows(batch_pool.REGISTRY)
    review = batch_pool.build_review(rows)
    cards = []
    for r in rows:
        bid = r.get("batch_id")
        lane, gate = lane_verdict(bid)
        cards.append({
            "batch_id": bid,
            "lane": lane,
            "gate": gate,
            "payload": payload_of(bid),
            "consumer_named": r.get("consumer_named"),
        })
    runnable = [c["batch_id"] for c in cards if c["payload"]]
    dispatchable = review.get("dispatchable")
    receipts = receipt_rows()
    return {
        "ts": now().strftime("%Y-%m-%d %H:%M:%S"),
        "dispatchable": dispatchable,
        "dispatchable_cards": review.get("dispatchable_cards"),
        "runnable": len(runnable),
        "runnable_cards": runnable,
        "not_runnable": [c["batch_id"] for c in cards
                         if c["payload"] is None],
        "gap": (dispatchable or 0) - len(runnable),
        "igniter": {
            "last_tick_age_min": last_tick_age_min(),
            "receipts": len(receipts),
            "receipts_ok": sum(1 for r in receipts if r.get("ok")),
            "last_receipt": receipts[-1]["ts"] if receipts else None,
        },
        "cards": cards,
    }


def status(as_json=False, out=None):
    rd = readout()
    if as_json or out:
        doc = json.dumps(rd, ensure_ascii=False, indent=2, sort_keys=True)
        if out:
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            with open(out, "w", encoding="utf-8", newline="\n") as f:
                f.write(doc + "\n")
        if as_json:
            print(doc)
        return 0
    for c in rd["cards"]:
        print("%s lane=%s gate=%s payload=%s"
              % (c["batch_id"], c["lane"], c["gate"],
                 c["payload"] or "none"))
    ign = rd["igniter"]
    print("dispatchable=%s runnable=%s gap=%s receipts=%s ok=%s "
          "last_tick_age_min=%s"
          % (rd["dispatchable"], rd["runnable"], rd["gap"],
             ign["receipts"], ign["receipts_ok"],
             ign["last_tick_age_min"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="gate-then-run pool igniter")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tick")
    p_s = sub.add_parser("status")
    p_s.add_argument("--json", action="store_true", help="machine-readable")
    p_s.add_argument("--out", help="write the JSON readout to this path")
    p_r = sub.add_parser("run")
    p_r.add_argument("--batch-id", required=True)
    p_r.add_argument("--force", action="store_true",
                     help="run even with the lane closed (manual override)")
    args = ap.parse_args(argv)
    if args.cmd == "tick":
        return tick()
    if args.cmd == "status":
        return status(as_json=args.json, out=args.out)
    ok, receipt = ignite(args.batch_id, force=args.force)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())