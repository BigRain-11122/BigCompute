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
import re
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
    # T-20261108-4: judgment-frame batch render (O-20260929-007's second
    # 3D-offline-render card).  Same labbench project: the bake card builds
    # the room, the render card shoots fixed six-view judgment frames.
    "B-CITY3D-02": "render_spike",
}

# What a payload must find on disk before it counts as runnable.  Declaring a
# runner is not evidence the runner can execute: on 2026-11-08 the labbench
# bake project existed as an empty Assets/ tree (no City3DBakeSpike entry
# script), so B-CITY3D-01 read `runnable` while every ignition would have
# launched Tuanjie into `-executeMethod` with nothing to call.  Readiness is
# checked here, not assumed.
BAKE_ENTRY_MARKER = "City3DBakeSpike"

# runner -> (project root, entry-script marker).  Readiness is per payload, not
# per project: two runners may live in one labbench project but only one of
# them may have had its entry script deployed.
PAYLOAD_SPECS = {
    "bake_spike": ("BAKE_PROJECT", "City3DBakeSpike"),
    "render_spike": ("BAKE_PROJECT", "City3DBatchRender"),
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
    # live_on_stale: the producer writes a reading a few times per day, so a
    # 15-minute watcher would otherwise read STALE on almost every tick and
    # never fire.  Re-measure instead of mistaking silence for a busy lane.
    gate, _src = lane_gate.run(live_on_stale=True)
    state = gate.get("%s_lane" % lane) or {}
    return lane, "%s/%s" % (state.get("verdict", "?"),
                            state.get("reason", gate.get("verdict", "?")))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# A failed editor launch used to leave an empty artifacts list and nothing else:
# on 2026-11-08 six ignitions failed with exit=1 while the actual cause
# (`error CS1061`) sat unread in tuanjie-bake.log.  A failure receipt has to
# name the reason it failed, not just the fact that it did.
LOG_ERROR_PATTERNS = (
    re.compile(r"error CS\d+"),           # C# compiler diagnostic
    re.compile(r"^\s*(?:Error|ERROR)\b"),  # editor / engine fatal line
    re.compile(r"\bException\b"),
    re.compile(r"^error:"),
)
CS_ERROR_RE = LOG_ERROR_PATTERNS[0]
LOG_ERROR_MAX = 6
LOG_ERROR_WIDTH = 300


def extract_log_errors(logfile, limit=LOG_ERROR_MAX):
    """Pull the error lines out of an editor log, or say why none were found.

    Returns dict(present, lines, note, bytes).  `lines=[]` is never silent:
    the note distinguishes "no log at all" from "log clean of error lines",
    which are different facts about a failed run.
    """
    out = {"present": os.path.exists(logfile), "lines": [], "note": "",
           "bytes": 0}
    if not out["present"]:
        out["note"] = "log-missing"
        return out
    try:
        out["bytes"] = os.path.getsize(logfile)
        with open(logfile, encoding="utf-8", errors="replace") as f:
            raw = f.readlines()
    except OSError as e:
        out["note"] = "log-unreadable:%s" % e.__class__.__name__
        return out
    hits = []
    for line in raw:
        text = line.rstrip("\r\n")
        if len(text) > LOG_ERROR_WIDTH:
            text = text[:LOG_ERROR_WIDTH] + "..."
        if any(p.search(text) for p in LOG_ERROR_PATTERNS):
            hits.append(text.strip())
    # keep the tail: the last lines are the ones that ended the run
    out["lines"] = hits[-limit:]
    out["note"] = "ok" if out["lines"] else "no-error-line-in-log"
    if len(hits) > limit:
        out["note"] = "ok(%d of %d shown)" % (limit, len(hits))
    return out


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
        "log": os.path.relpath(logfile, ROOT).replace("\\", "/"),
        "error": None if ok else extract_log_errors(logfile),
        "metrics": metrics_doc,
        "artifacts": artifacts,
    }


def run_render_spike(outdir):
    """B-CITY3D-02 payload: fixed six-view judgment frames + frame-check record.

    The card's deliverable is "PNG 序列 + 帧检记录", so `ok` requires the
    metrics file to carry render_ok from the editor side: frame count reached,
    no blank frame (luminance std floor), no duplicate frame (sha collision).
    A run that produced zero PNGs must never read as ok.
    """
    os.makedirs(outdir, exist_ok=True)
    frames_dir = os.path.join(outdir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    metrics = os.path.join(outdir, "render-metrics.json")
    logfile = os.path.join(outdir, "tuanjie-render.log")
    exe = TUANJIE if os.path.exists(TUANJIE) else "Tuanjie.exe"
    env = dict(os.environ)
    env["RENDER_OUT"] = metrics
    env["RENDER_DIR"] = frames_dir
    env["RENDER_W"] = env.get("RENDER_W", "1280")
    env["RENDER_H"] = env.get("RENDER_H", "720")
    env["RENDER_VIEWS"] = env.get("RENDER_VIEWS", "6")
    cmd = [exe, "-batchmode", "-projectPath", BAKE_PROJECT,
           "-executeMethod", "City3DBatchRender.Entry.Run", "-logFile", logfile]
    started = time.time()
    with open(os.path.join(outdir, "runner.txt"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write("cmd: %s\nenv: RENDER_OUT=%s RENDER_DIR=%s RENDER_W=%s RENDER_H=%s\n"
                "project: %s\nstarted: %s\n"
                % (" ".join(cmd), metrics, frames_dir, env["RENDER_W"],
                   env["RENDER_H"], BAKE_PROJECT,
                   now().strftime("%Y-%m-%d %H:%M:%S")))
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
    pngs = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
    artifacts = []
    for p in [metrics] + pngs:
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
    ok = (code == 0 and bool(metrics_doc.get("render_ok"))
          and int(metrics_doc.get("frame_count") or 0) > 0
          and len(pngs) == int(metrics_doc.get("frame_count") or -1))
    return {
        "kind": "render_spike",
        "exit": code,
        "seconds": elapsed,
        "ok": ok,
        "log": os.path.relpath(logfile, ROOT).replace("\\", "/"),
        "error": None if ok else extract_log_errors(logfile),
        "metrics": metrics_doc,
        "artifacts": artifacts,
    }


RUNNER_FUNCS = {
    "bake_spike": run_bake_spike,
    "render_spike": run_render_spike,
}


def ignite(batch_id, force=False):
    """Gate, claim, run, receipt.  Returns (ok, receipt dict)."""
    if batch_id not in RUNNERS:
        return False, {"batch_id": batch_id, "skipped": "no-local-payload"}
    _name, ready, reason = payload_readiness(batch_id)
    if not ready and not force:
        log("ignite refused %s payload-%s" % (batch_id, reason))
        return False, {"batch_id": batch_id, "skipped": "payload-not-ready:" + reason}
    if not force:
        allowed, why_compile = compile_stale_guard(batch_id)
        if not allowed:
            log("ignite refused %s %s" % (batch_id, why_compile))
            return False, {"batch_id": batch_id, "skipped": why_compile}
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
    result = RUNNER_FUNCS[RUNNERS[batch_id]](outdir)
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
        _name, ready, reason = payload_readiness(batch_id)
        if not ready:
            tried.append("%s:payload-%s" % (batch_id, reason))
            continue
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


def find_entry_script(project_root, marker=BAKE_ENTRY_MARKER, max_files=4000):
    """Path of the first .cs under Assets/ that declares `marker`, or None."""
    assets = os.path.join(project_root, "Assets")
    if not os.path.isdir(assets):
        return None
    seen = 0
    for base, _dirs, files in os.walk(assets):
        for fn in files:
            if not fn.lower().endswith(".cs"):
                continue
            seen += 1
            if seen > max_files:
                return None
            path = os.path.join(base, fn)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    if marker in f.read(200000):
                        return path
            except OSError:
                continue
    return None


def payload_readiness(batch_id, project_root=None):
    """(payload name|None, ready: bool, reason) - readiness verified on disk.

    Reasons are the vocabulary the CEO face and the log use, so a gap card is
    always explainable: no-local-payload / project-missing /
    entry-script-missing / runner-unknown / ok.
    """
    name = payload_of(batch_id)
    if name is None:
        return None, False, "no-local-payload"
    spec = PAYLOAD_SPECS.get(name)
    if spec is None:
        return name, False, "runner-unknown:%s" % name
    root = project_root or BAKE_PROJECT
    if not os.path.isdir(root):
        return name, False, "project-missing"
    if find_entry_script(root, marker=spec[1]) is None:
        return name, False, "entry-script-missing"
    return name, True, "ok"


def render_card(c):
    """One card line; a declared-but-unready payload must never look green."""
    payload = c.get("payload") or "none"
    if c.get("payload") and not c.get("payload_ready"):
        payload = "%s[NOT-READY:%s]" % (c["payload"], c.get("payload_reason"))
    return "%s lane=%s gate=%s payload=%s" % (
        c.get("batch_id"), c.get("lane"), c.get("gate"), payload)


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


def last_receipt(batch_id, rows=None, ok_only=False):
    """Most recent receipt for one card (ok_only=False keeps failures)."""
    for r in reversed(rows if rows is not None else receipt_rows()):
        if r.get("batch_id") != batch_id:
            continue
        if ok_only and not r.get("ok"):
            continue
        return r
    return None


def compile_stale_guard(batch_id, project_root=None, rows=None):
    """(allowed: bool, reason) - don't burn another editor launch on the same CS error.

    A compile failure is only worth re-attempting if a source file changed since
    that failed run.  Repeating the identical launch turns a 30-second answer
    into a 50-second non-answer six times in a row (2026-11-08).
    """
    rec = last_receipt(batch_id, rows=rows, ok_only=False)
    if rec is None:
        return True, "no-prior-run"
    if rec.get("ok"):
        return True, "last-run-ok"
    err = ((rec.get("runner") or {}).get("error") or {})
    cs_lines = [ln for ln in (err.get("lines") or []) if CS_ERROR_RE.search(ln)]
    if not cs_lines:
        return True, "no-compile-error"
    root = project_root or BAKE_PROJECT
    log_rel = (rec.get("runner") or {}).get("log") or ""
    log_path = os.path.join(ROOT, log_rel.replace("/", os.sep)) if log_rel else ""
    try:
        log_mtime = os.path.getmtime(log_path) if log_path else 0
    except OSError:
        log_mtime = 0
    newest, cs_count = 0, 0
    assets = os.path.join(root, "Assets")
    for base, _dirs, files in os.walk(assets):
        for fn in files:
            if not fn.lower().endswith(".cs"):
                continue
            cs_count += 1
            try:
                newest = max(newest, os.path.getmtime(os.path.join(base, fn)))
            except OSError:
                continue
    if log_mtime and newest <= log_mtime:
        return False, ("last-run-compile-error-unchanged(%d cs; %s)"
                       % (cs_count, cs_lines[0][:120]))
    return True, "compile-error-but-sources-changed"


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


def last_failure(receipts):
    """Latest failed ignition receipt, flattened for a CEO-facing line.

    `receipts=N ok=M` cannot tell a chain that fails every tick from one that
    never fires: both look like a small M.  The reason of the newest failure is
    named here; None means "no failure in the ledger", which status prints as
    `last_failure=none` instead of omitting the field so "no failure" and "not
    measured" stay distinguishable.
    """
    for r in reversed(receipts):
        if r.get("ok"):
            continue
        runner = r.get("runner") or {}
        err = runner.get("error")
        if err is None:
            # Receipts written before T222 carry no error envelope at all; say
            # that instead of "no error text", which would read as a clean log.
            reason = "not-captured(writer-predates-error-capture)"
        else:
            lines = err.get("lines") or []
            reason = lines[0] if lines else (err.get("note") or "no-error-text")
        return {
            "batch_id": r.get("batch_id"),
            "ts": r.get("ts"),
            "exit": runner.get("exit"),
            "error": reason,
            "lane_gate": r.get("lane_gate"),
        }
    return None


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
        name, ready, reason = payload_readiness(bid)
        cards.append({
            "batch_id": bid,
            "lane": lane,
            "gate": gate,
            "payload": name,
            "payload_ready": ready,
            "payload_reason": reason,
            "consumer_named": r.get("consumer_named"),
        })
    runnable = [c["batch_id"] for c in cards if c["payload_ready"]]
    unready = [{"batch_id": c["batch_id"], "reason": c["payload_reason"]}
               for c in cards if c["payload"] is not None and not c["payload_ready"]]
    dispatchable = review.get("dispatchable")
    receipts = receipt_rows()
    return {
        "ts": now().strftime("%Y-%m-%d %H:%M:%S"),
        "dispatchable": dispatchable,
        "dispatchable_cards": review.get("dispatchable_cards"),
        "runnable": len(runnable),
        "runnable_cards": runnable,
        "declared_unready": unready,
        "not_runnable": [c["batch_id"] for c in cards
                         if c["payload"] is None],
        "gap": (dispatchable or 0) - len(runnable),
        "igniter": {
            "last_tick_age_min": last_tick_age_min(),
            "receipts": len(receipts),
            "receipts_ok": sum(1 for r in receipts if r.get("ok")),
            "last_receipt": receipts[-1]["ts"] if receipts else None,
            "last_failure": last_failure(receipts),
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
        print(render_card(c))
    ign = rd["igniter"]
    print("dispatchable=%s runnable=%s gap=%s receipts=%s ok=%s "
          "last_tick_age_min=%s"
          % (rd["dispatchable"], rd["runnable"], rd["gap"],
             ign["receipts"], ign["receipts_ok"],
             ign["last_tick_age_min"]))
    for u in rd["declared_unready"]:
        print("unready %s reason=%s" % (u["batch_id"], u["reason"]))
    lf = ign["last_failure"]
    if lf:
        print("last_failure ts=%s batch=%s exit=%s error=%s"
              % (lf["ts"], lf["batch_id"], lf["exit"], lf["error"]))
    else:
        print("last_failure=none")
    return 0


def selftest():
    """Fixtures for the readiness rule - a declared runner is not a runnable one."""
    import tempfile
    import shutil
    checks = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    tmp = tempfile.mkdtemp(prefix="pool-igniter-selftest-")
    try:
        empty = os.path.join(tmp, "empty")
        os.makedirs(os.path.join(empty, "Assets"), exist_ok=True)
        filled = os.path.join(tmp, "filled")
        os.makedirs(os.path.join(filled, "Assets", "Editor"), exist_ok=True)
        with open(os.path.join(filled, "Assets", "Editor", "Entry.cs"),
                  "w", encoding="utf-8") as f:
            f.write("public static class Entry { public static void Run() {} }\n"
                    "// namespace City3DBakeSpike.Entry\n")
        decoy = os.path.join(tmp, "decoy")
        os.makedirs(os.path.join(decoy, "Assets"), exist_ok=True)
        with open(os.path.join(decoy, "Assets", "Other.cs"),
                  "w", encoding="utf-8") as f:
            f.write("public class Other {}\n")

        n, r, why = payload_readiness("B-UNKNOWN-99")
        chk("S1 unknown card -> no-local-payload", n is None and not r
            and why == "no-local-payload", why)

        n, r, why = payload_readiness("B-CITY3D-01", os.path.join(tmp, "nope"))
        chk("S2 missing project -> project-missing",
            n == "bake_spike" and not r and why == "project-missing", why)

        n, r, why = payload_readiness("B-CITY3D-01", empty)
        chk("S3 empty Assets -> entry-script-missing",
            n == "bake_spike" and not r and why == "entry-script-missing", why)

        n, r, why = payload_readiness("B-CITY3D-01", decoy)
        chk("S4 .cs without marker stays entry-script-missing",
            not r and why == "entry-script-missing", why)

        n, r, why = payload_readiness("B-CITY3D-01", filled)
        chk("S5 entry script present -> ready",
            n == "bake_spike" and r and why == "ok", why)

        # per-payload readiness: the same labbench project can satisfy the bake
        # card while the render card has no entry script yet (T-20261108-4).
        n, r, why = payload_readiness("B-CITY3D-02", filled)
        chk("S9 render card in bake-only project -> entry-script-missing",
            n == "render_spike" and not r and why == "entry-script-missing", why)

        with open(os.path.join(filled, "Assets", "Editor", "City3DBatchRender.cs"),
                  "w", encoding="utf-8") as f:
            f.write("namespace City3DBatchRender { public static class Entry {} }\n")
        n, r, why = payload_readiness("B-CITY3D-02", filled)
        chk("S10 render entry deployed -> ready",
            n == "render_spike" and r and why == "ok", why)

        n, r, why = payload_readiness("B-CITY3D-02", os.path.join(tmp, "nope"))
        chk("S11 render card project missing -> project-missing",
            not r and why == "project-missing", why)

        missing = [b for b, run in RUNNERS.items()
                   if run not in PAYLOAD_SPECS or run not in RUNNER_FUNCS]
        chk("S12 every declared runner has spec + function", not missing, missing)

        line = render_card({"batch_id": "B-X", "lane": "cpu", "gate": "OPEN",
                            "payload": "bake_spike", "payload_ready": False,
                            "payload_reason": "entry-script-missing"})
        chk("S6 unready card line carries NOT-READY reason",
            "NOT-READY:entry-script-missing" in line, line)

        line = render_card({"batch_id": "B-Y", "lane": "cpu", "gate": "OPEN",
                            "payload": "bake_spike", "payload_ready": True,
                            "payload_reason": "ok"})
        chk("S7 ready card line has no NOT-READY", "NOT-READY" not in line, line)

        line = render_card({"batch_id": "B-Z", "lane": None, "gate": "x",
                            "payload": None, "payload_ready": False,
                            "payload_reason": "no-local-payload"})
        chk("S8 payload-less card renders as none", "payload=none" in line, line)

        # --- T222: a failure receipt must name the error line -------------
        log_ok = os.path.join(tmp, "with-error.log")
        with open(log_ok, "w", encoding="utf-8") as f:
            f.write("Initialize engine version: 2022.3.62t15\n"
                    "Assets/Editor/BakeSpike.cs(169,17): error CS1061: "
                    "'LightingSettings' does not contain 'aoEnabled'\n"
                    "Aborting batchmode due to failure\n")
        got = extract_log_errors(log_ok)
        chk("S13 CS error line is quoted into the receipt",
            got["present"] and len(got["lines"]) == 1
            and "error CS1061" in got["lines"][0], got)

        log_clean = os.path.join(tmp, "clean.log")
        with open(log_clean, "w", encoding="utf-8") as f:
            f.write("Initialize engine version\nBatchmode quit with code 1\n")
        got = extract_log_errors(log_clean)
        chk("S14 log without error lines says so instead of staying silent",
            got["present"] and got["lines"] == []
            and got["note"] == "no-error-line-in-log", got)

        got = extract_log_errors(os.path.join(tmp, "absent.log"))
        chk("S15 missing log is named, not reported as clean",
            not got["present"] and got["note"] == "log-missing", got)

        long_log = os.path.join(tmp, "many.log")
        with open(long_log, "w", encoding="utf-8") as f:
            for i in range(10):
                f.write("error CS%04d: fake %d\n" % (i, i))
        got = extract_log_errors(long_log)
        chk("S16 only the tail of a long error list is kept",
            len(got["lines"]) == LOG_ERROR_MAX
            and "CS0009" in got["lines"][-1], got["note"])

        # --- compile-stale guard: same error + same sources = don't relaunch --
        old = (now() - datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        guarded = os.path.join(tmp, "guarded.log")
        with open(guarded, "w", encoding="utf-8") as f:
            f.write("error CS1061: nope\n")
        os.utime(guarded, (time.time() - 7200, time.time() - 7200))
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, "Assets", "Editor"), exist_ok=True)
        cs = os.path.join(proj, "Assets", "Editor", "Spike.cs")
        with open(cs, "w", encoding="utf-8") as f:
            f.write("// spike\n")
        os.utime(cs, (time.time() - 10800, time.time() - 10800))
        fail_row = {"batch_id": "B-T", "ok": False, "ts": old,
                    "runner": {"log": os.path.relpath(guarded, ROOT),
                               "error": extract_log_errors(guarded)}}
        allowed, why = compile_stale_guard("B-T", project_root=proj,
                                           rows=[fail_row])
        chk("S17 unchanged sources after a CS failure -> refuse relaunch",
            not allowed and why.startswith("last-run-compile-error-unchanged"),
            why)

        os.utime(cs, (time.time(), time.time()))
        allowed, why = compile_stale_guard("B-T", project_root=proj,
                                           rows=[fail_row])
        chk("S18 edited source -> relaunch allowed", allowed
            and why == "compile-error-but-sources-changed", why)

        allowed, why = compile_stale_guard("B-T", project_root=proj, rows=[])
        chk("S19 no prior run -> allowed", allowed and why == "no-prior-run", why)

        ok_row = {"batch_id": "B-T", "ok": True, "ts": old, "runner": {}}
        allowed, why = compile_stale_guard("B-T", project_root=proj,
                                           rows=[ok_row])
        chk("S20 last run ok -> allowed", allowed and why == "last-run-ok", why)

        no_cs_row = {"batch_id": "B-T", "ok": False, "ts": old,
                     "runner": {"log": "", "error": {"lines": ["something else"]}}}
        allowed, why = compile_stale_guard("B-T", project_root=proj,
                                           rows=[no_cs_row])
        chk("S21 non-compile failure is not blocked by this guard",
            allowed and why == "no-compile-error", why)

        # --- T223: the newest failure must be readable from the readout ------
        old_fail = {"batch_id": "B-OLD", "ok": False, "ts": "2026-11-08 10:00:00",
                    "lane_gate": "OPEN/x",
                    "runner": {"exit": 1,
                               "error": {"lines": ["error CS1061: old"]}}}
        new_fail = {"batch_id": "B-NEW", "ok": False, "ts": "2026-11-08 11:00:00",
                    "lane_gate": "CLOSED/y",
                    "runner": {"exit": 1,
                               "error": {"lines": ["error CS0246: new"]}}}
        ok_after = {"batch_id": "B-NEW", "ok": True, "ts": "2026-11-08 12:00:00",
                    "runner": {}}
        lf = last_failure([old_fail, new_fail, ok_after])
        chk("S22 last_failure picks the newest failure, not the newest receipt",
            lf and lf["batch_id"] == "B-NEW" and "CS0246" in lf["error"], lf)

        silent = {"batch_id": "B-SILENT", "ok": False, "ts": "2026-11-08 09:00:00",
                  "runner": {"exit": 2, "error": {"lines": [],
                                                  "note": "log-missing"}}}
        lf = last_failure([silent])
        chk("S23 failure without error lines still names a reason",
            lf and lf["error"] == "log-missing" and lf["exit"] == 2, lf)

        chk("S24 no failure -> None (status prints last_failure=none)",
            last_failure([ok_after]) is None and last_failure([]) is None, None)

        legacy = {"batch_id": "B-LEGACY", "ok": False, "ts": "2026-11-07 01:00:00",
                  "runner": {"exit": 1}}
        lf = last_failure([legacy])
        chk("S25 receipt without error envelope says not-captured, not clean",
            lf and lf["error"].startswith("not-captured"), lf)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           "" if ok else " :: " + str(detail)))
    print("%d/%d PASS" % (len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="gate-then-run pool igniter")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tick")
    sub.add_parser("selftest")
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
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "status":
        return status(as_json=args.json, out=args.out)
    ok, receipt = ignite(args.batch_id, force=args.force)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())