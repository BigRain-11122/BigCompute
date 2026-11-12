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
import shutil
import struct
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


def preserve_lightmaps(lightmaps, outdir):
    """Copy a bake's atlases into the run directory before the receipt is written.

    Unity writes Lightmap-*.exr into the project's Assets, a path every later
    bake overwrites.  A receipt that advertises those paths stops matching its
    own sha256 as soon as the next run lands, so the earlier delivery silently
    becomes unprovable (T232: the provable set shrank 3 -> 2 after one re-bake).
    Copies live under <run>/lightmaps/ and the project path is kept as `source`
    so the origin stays readable.  A failed copy is skipped, never faked.
    """
    dest_dir = os.path.join(outdir, "lightmaps")
    kept = []
    for p in lightmaps:
        try:
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, os.path.basename(p))
            shutil.copy2(p, dest)
        except OSError:
            continue
        kept.append({"path": dest, "source": p,
                     "bytes": os.path.getsize(dest), "sha256": sha256(dest)})
    return kept


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
    lightmaps = sorted(glob.glob(os.path.join(
        BAKE_PROJECT, "Assets", "**", "Lightmap-*.exr"), recursive=True))
    # The project's Assets are shared mutable state: the next bake overwrites
    # them.  Snapshot first, then advertise only the snapshot (T232).
    preserved = preserve_lightmaps(lightmaps, outdir)
    qc_paths = [k["path"] for k in preserved] if preserved else lightmaps
    artifacts = []
    for p in [metrics] + qc_paths:
        if os.path.exists(p):
            artifacts.append({"path": os.path.relpath(p, ROOT).replace("\\", "/"),
                              "bytes": os.path.getsize(p),
                              "sha256": sha256(p)})
    for k in preserved:
        for a in artifacts:
            if a["path"] == os.path.relpath(k["path"], ROOT).replace("\\", "/"):
                a["source"] = os.path.relpath(k["source"], ROOT).replace("\\", "/")
    metrics_doc = {}
    if os.path.exists(metrics):
        try:
            with open(metrics, encoding="utf-8") as f:
                metrics_doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            metrics_doc = {}
    ok = (code == 0 and bool(metrics_doc.get("bake_ok"))
          and int(metrics_doc.get("lightmap_files") or 0) > 0)
    # The atlas is the deliverable, so "bake reported ok" and "there is light
    # in the atlas" are two claims; the second one is now measured and stored
    # beside the metrics rather than assumed.
    quality = lightmap_quality_report(qc_paths) if (ok and qc_paths) else None
    if quality:
        qpath = os.path.join(outdir, "lightmap-quality.json")
        try:
            with open(qpath, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(quality, ensure_ascii=False, indent=2,
                                   sort_keys=True) + "\n")
            artifacts.append({"path": os.path.relpath(qpath, ROOT).replace("\\", "/"),
                              "bytes": os.path.getsize(qpath),
                              "sha256": sha256(qpath)})
        except OSError:
            pass
    return {
        "kind": "bake_spike",
        "exit": code,
        "seconds": elapsed,
        "ok": ok,
        "deliverable_ok": bake_deliverable_ok(ok, quality),
        "log": os.path.relpath(logfile, ROOT).replace("\\", "/"),
        "error": None if ok else extract_log_errors(logfile),
        "metrics": metrics_doc,
        "lightmap_quality": quality,
        "lightmaps_preserved": len(preserved),
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
    # T226: two readings that blank/duplicate cannot see (magenta fallback
    # materials, near-empty framing).  Written beside the metrics as their own
    # record so the frame set stays self-describing.
    quality = frame_quality_report(frames_dir) if pngs else None
    if quality:
        qpath = os.path.join(outdir, "frame-quality.json")
        try:
            with open(qpath, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(quality, ensure_ascii=False, indent=2,
                                   sort_keys=True) + "\n")
        except OSError:
            qpath = None
    artifacts = []
    for p in [metrics] + ([qpath] if quality and qpath else []) + pngs:
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
    # D-20261109-08: the frame set is the card's deliverable, so "ran fine" and
    # "usable frames" are two different claims and get two different fields.
    qc_ok = bool(quality) and quality.get("qc_verdict") == "FRAME-QC-PASS"
    return {
        "kind": "render_spike",
        "exit": code,
        "seconds": elapsed,
        "ok": ok,
        "deliverable_ok": ok and qc_ok,
        "log": os.path.relpath(logfile, ROOT).replace("\\", "/"),
        "error": None if ok else extract_log_errors(logfile),
        "metrics": metrics_doc,
        "frame_quality": quality,
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
    fp, fp_parts = payload_fingerprint(batch_id)
    if not force:
        allowed, why_dup = duplicate_guard(batch_id, fingerprint=fp)
        if not allowed:
            log("ignite skipped %s duplicate:%s" % (batch_id, why_dup))
            return False, {"batch_id": batch_id,
                           "skipped": "duplicate:" + why_dup,
                           "fingerprint": fp}
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
        "fingerprint": fp,
        "fingerprint_parts": fp_parts,
        "outdir": os.path.relpath(outdir, ROOT).replace("\\", "/"),
        "ok": bool(result.get("ok")),
        # T229: mirrored at the top level so a reader cannot take "ran fine"
        # for "produced something usable" without opening `runner`.
        "deliverable_ok": bool(result.get("deliverable_ok",
                                         result.get("ok"))),
        "runner": result,
    }
    receipt.update(receipt_extra_fields(result))
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
        finally:
            try:
                os.remove(LOCK)
            except OSError:
                pass
        # A duplicate skip is not a failure: the card is already built, so let
        # the next card in the pool have the window instead of reporting red.
        if str((receipt or {}).get("skipped", "")).startswith("duplicate:"):
            tried.append("%s:duplicate" % batch_id)
            continue
        return 0 if ok else 1
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


def receipt_deliverable(row):
    """Is this receipt's product usable, not merely a clean run?

    The rule itself lives in batch_pool (single source, since pool_igniter
    already depends on it and the dependency must not run backwards).
    """
    import batch_pool
    return batch_pool.receipt_deliverable(row)


def receipt_state(row):
    """'yes' | 'no' | 'unknown' -- see batch_pool.receipt_deliverable_state."""
    import batch_pool
    return batch_pool.receipt_deliverable_state(row)


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


# tech T224 / R-069: on 2026-11-09 the watcher re-burned B-CITY3D-01 every 15
# minutes from 08:11 to 10:11 - ten editor launches, each producing a
# byte-identical 410-byte bake-metrics.json.  A chain that reruns identical work
# is not producing anything new; it holds the editor and the CPU hostage.  Same
# card + same payload fingerprint inside the window = SKIP, naming the run it
# duplicates.
DUPLICATE_WINDOW_H = 6.0
FINGERPRINT_SCHEME = "card+entry+cs-newest"


def payload_fingerprint(batch_id, project_root=None):
    """Stable hash of what decides one card's output, plus its components.

    Three parts, because a changed output can only come from one of them: the
    card row (what to build), the entry script bytes (where it starts), and the
    newest .cs under Assets/ with the file count (whether how-it-builds
    changed).  Components are returned so a mismatch stays explainable.
    """
    row = card(batch_id)
    if row is None:
        return None, {"reason": "card-not-found"}
    row_text = json.dumps(row, ensure_ascii=False, sort_keys=True)
    spec = PAYLOAD_SPECS.get(RUNNERS.get(batch_id) or "")
    marker = spec[1] if spec else BAKE_ENTRY_MARKER
    root = project_root or BAKE_PROJECT
    entry = find_entry_script(root, marker=marker)
    entry_sha = sha256(entry) if entry else ""
    newest, cs_count = 0, 0
    assets = os.path.join(root, "Assets")
    for base, _dirs, files in os.walk(assets):
        for fn in files:
            if not fn.lower().endswith(".cs"):
                continue
            cs_count += 1
            try:
                newest = max(newest, int(os.path.getmtime(os.path.join(base, fn))))
            except OSError:
                continue
    h = hashlib.sha256()
    for part in (FINGERPRINT_SCHEME, batch_id, row_text, entry_sha,
                 str(cs_count), str(newest)):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:16], {
        "scheme": FINGERPRINT_SCHEME,
        "card": hashlib.sha256(row_text.encode("utf-8")).hexdigest()[:16],
        "entry": entry_sha[:16] if entry_sha else "absent",
        "cs_count": cs_count,
        "cs_newest": newest,
    }


def _receipt_age_h(ts):
    try:
        return (now() - datetime.datetime.strptime(
            ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return None


def duplicate_guard(batch_id, rows=None, hours=DUPLICATE_WINDOW_H,
                    project_root=None, fingerprint=None):
    """(allowed: bool, reason) - don't rebuild a card whose payload is unchanged.

    Only an `ok=true` receipt can be a baseline: re-running after a failure is
    the retry path, not a duplicate.  A baseline written before fingerprints
    existed cannot prove anything, so it lets one more run through and says so
    instead of silently claiming the card is fresh.
    """
    rows = rows if rows is not None else receipt_rows()
    fp = fingerprint
    if fp is None:
        fp, _parts = payload_fingerprint(batch_id, project_root=project_root)
    if fp is None:
        return True, "no-card"
    for r in reversed(rows):
        if r.get("batch_id") != batch_id or not r.get("ok"):
            continue
        prev = r.get("fingerprint")
        age = _receipt_age_h(r.get("ts"))
        if not prev:
            return True, "baseline-without-fingerprint(%s)" % r.get("ts")
        if age is None:
            return True, "baseline-ts-unreadable(%s)" % r.get("ts")
        if age > hours:
            return True, "baseline-stale(%.1fh>%gh)" % (age, hours)
        if prev != fp:
            return True, "payload-changed(%s->%s)" % (prev, fp)
        return False, ("same-payload-within-%gh(last ok %s fp=%s)"
                       % (hours, r.get("ts"), fp))
    return True, "no-ok-baseline"


def delivery_audit(rows=None):
    """Runs are not deliveries: count provable payloads, name the rest.

    "ok runs" alone cannot tell a real delivery from a rebuilt one - on
    2026-11-09 B-CITY3D-01 had 24 ok runs behind a single labbench scene,
    most of them from the 15-minute re-burn chain.  Three readings per card:
      unique_payloads - distinct fingerprints among ok receipts (what this
                        box provably built once);
      pre_guard_runs  - ok receipts predating fingerprints, so their payloads
                        can never be compared (never counted as value);
      redundant_runs  - ok runs whose fingerprint repeats an earlier ok run.
    """
    rows = receipt_rows() if rows is None else rows
    cards, seen = {}, {}
    for r in rows:
        if not r.get("ok"):
            continue
        bid = r.get("batch_id") or "?"
        c = cards.setdefault(bid, {"ok_runs": 0, "unique_payloads": 0,
                                   "pre_guard_runs": 0, "redundant_runs": 0})
        c["ok_runs"] += 1
        fp = r.get("fingerprint")
        if not fp:
            c["pre_guard_runs"] += 1
            continue
        if fp in seen.setdefault(bid, set()):
            c["redundant_runs"] += 1
            continue
        seen[bid].add(fp)
        c["unique_payloads"] += 1
    totals = {"ok_runs": 0, "unique_payloads": 0, "pre_guard_runs": 0,
              "redundant_runs": 0}
    for c in cards.values():
        for k in totals:
            totals[k] += c[k]
    return {"totals": totals, "cards": cards}


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


def duplicate_skips_today():
    """How many identical re-burns the guard stopped today (0 when unreadable).

    The guard's value is invisible from `receipts=N`: a skipped card writes no
    receipt by design.  This reads the watcher log so the CEO face can show the
    editor launches that did *not* happen.
    """
    if not os.path.exists(LOG):
        return 0
    stamp = now().strftime("%Y-%m-%d")
    n = 0
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith(stamp) and "duplicate:" in line:
                    n += 1
    except OSError:
        return 0
    return n


# --- frame quality (tech T226) -------------------------------------------
# The judgment-frame check only asked "blank?" and "duplicate?", so two
# different defects read as passing frames: a frame whose materials fell back
# to Unity's magenta error shader, and a near-empty overhead frame
# (luma_std=9.63, barely over the 2.0 floor).  These two readings exist to be
# *seen*, not to gate: no threshold is introduced here before real numbers
# exist on real frames (T226 判据④).  Measured from the same PNG bytes the
# editor wrote, so no editor run is needed to read an existing frame set.
MAGENTA_MIN_R = 200      # Unity missing-material magenta ~ (255, 0, 255)
MAGENTA_MIN_B = 200
MAGENTA_MAX_G = 80
BG_TOL = 6               # per-channel tolerance when matching the clear colour
CLEAR_RGB_DEFAULT = (13, 13, 15)   # Color(0.05, 0.05, 0.06) at 8bpc, 0-255


def frame_quality(path, clear_rgb=None):
    """Read one PNG -> {magenta_pct, geom_pct, bg_rgb, bg_source} or None.

    `geom_pct` = share of pixels that are *not* the camera background, i.e.
    something was actually drawn there.  When the caller cannot declare the
    clear colour, the modal colour is used instead and labelled `modal`; the
    two estimates are never mixed silently, because on a top-down view the
    modal colour is the floor - reading it as "background" would turn a
    full-floor frame into "nothing drawn".
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(path).convert("RGB")
        data = im.tobytes()          # raw RGB triples, no per-pixel objects
    except (OSError, ValueError):
        return None
    n = len(data) // 3
    if not n:
        return None
    if clear_rgb:
        cr, cg, cb = clear_rgb
        source = "declared"
    else:
        from collections import Counter
        cr, cg, cb = Counter(data[i:i + 3] for i in range(0, n * 3, 3)
                             ).most_common(1)[0][0]
        source = "modal"
    mag = 0
    bg = 0
    for i in range(0, n * 3, 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        if r >= MAGENTA_MIN_R and b >= MAGENTA_MIN_B and g <= MAGENTA_MAX_G:
            mag += 1
        if (abs(r - cr) <= BG_TOL and abs(g - cg) <= BG_TOL
                and abs(b - cb) <= BG_TOL):
            bg += 1
    return {
        "magenta_pct": round(100.0 * mag / n, 2),
        "geom_pct": round(100.0 * (n - bg) / n, 2),
        "bg_rgb": [cr, cg, cb],
        "bg_source": source,
    }


def frame_quality_report(frames_dir, clear_rgb=CLEAR_RGB_DEFAULT):
    """Aggregate the two readings over a frame set; worst frame is named."""
    frames = []
    for p in sorted(glob.glob(os.path.join(frames_dir, "*.png"))):
        q = frame_quality(p, clear_rgb=clear_rgb)
        if q is None:
            return {"frames_read": 0, "unreadable": os.path.basename(p)}
        q["file"] = os.path.basename(p)
        frames.append(q)
    if not frames:
        return {"frames_read": 0, "unreadable": "no-frames"}
    worst_m = max(frames, key=lambda f: f["magenta_pct"])
    worst_g = min(frames, key=lambda f: f["geom_pct"])
    placeholder = [f["file"] for f in frames if f["magenta_pct"] > 0]
    # D-20261109-08 ①: an acceptance object with placeholder material may not
    # be judged PASS.  The rule is "zero error-shader pixels", not a tuned
    # percentage, and it is a reading-level verdict - the run-level `ok`
    # (count/blank/duplicate) is left exactly as it was so the ignition
    # retry/baseline contract does not silently change meaning.
    qc = "FRAME-QC-PASS" if not placeholder else \
        "FRAME-QC-INSUFFICIENT-PLACEHOLDER"
    return {
        "frames_read": len(frames),
        "clear_rgb": list(clear_rgb) if clear_rgb else None,
        "magenta_max_pct": worst_m["magenta_pct"],
        "magenta_max_frame": worst_m["file"],
        "geom_min_pct": worst_g["geom_pct"],
        "geom_min_frame": worst_g["file"],
        "placeholder_frames": len(placeholder),
        "placeholder_files": placeholder,
        "qc_verdict": qc,
        "frames": frames,
    }


def last_render_quality(rows=None):
    """Frame-quality reading for the newest ok render_spike receipt.

    Prefers numbers already stored in the receipt; falls back to reading the
    frame set on disk so receipts written before this existed are still
    readable.  Never returns a fabricated zero: "no render yet" is None.
    """
    rows = rows if rows is not None else receipt_rows()
    for r in reversed(rows):
        run = r.get("runner") or {}
        if run.get("kind") != "render_spike" or not r.get("ok"):
            continue
        q = r.get("frame_quality")
        if not q:
            outdir = r.get("outdir")
            if not outdir:
                return None
            frames_dir = os.path.join(ROOT, outdir, "frames")
            q = frame_quality_report(frames_dir)
            q["source"] = "recomputed-from-frames"
        else:
            q = dict(q)
            q["source"] = "receipt"
        q["outdir"] = r.get("outdir")
        q["ts"] = r.get("ts")
        return q
    return None


# --- lightmap QC (the bake card's deliverable) ---------------------------
# A successful bake writes Lightmap-*.exr and was counted deliverable without
# anyone ever opening one: an all-black atlas would have passed every check we
# had.  These readings answer one question only - is there light in the file -
# and report it as numbers.  No quality threshold is invented here: a bright
# atlas and a dim atlas both read CONTENT-OK, because nothing has calibrated
# "bright enough" for a City3D interior.
LIGHTMAP_BLACK_LIN = 1e-4
FFMPEG = "ffmpeg"


def exr_dimensions(path):
    """Width/height off the EXR header (dataWindow) - no image library needed."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    if not head.startswith(b"\x76\x2f\x31\x01"):
        return None  # not an EXR magic
    i = head.find(b"dataWindow\x00box2i\x00")
    if i < 0 or len(head) < i + 30:
        return None
    try:
        x0, y0, x1, y1 = struct.unpack("<4i", head[i + 21:i + 37])
    except struct.error:
        return None
    w, h = x1 - x0 + 1, y1 - y0 + 1
    if w <= 0 or h <= 0:
        return None
    return w, h


def lightmap_stats(path):
    """Linear-light readings for one baked lightmap; None when unreadable."""
    dims = exr_dimensions(path)
    if not dims:
        return None
    try:
        out = subprocess.run(
            [FFMPEG, "-v", "error", "-i", path, "-f", "rawvideo",
             "-pix_fmt", "grayf32le", "-"],
            capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout:
        return None
    import numpy as np
    a = np.frombuffer(out.stdout, dtype="<f4")
    if a.size == 0:
        return None
    lit = float((a > LIGHTMAP_BLACK_LIN).mean())
    return {
        "file": os.path.basename(path),
        "width": dims[0],
        "height": dims[1],
        "pixels": int(a.size),
        "mean_lin": round(float(a.mean()), 6),
        "std_lin": round(float(a.std()), 6),
        "max_lin": round(float(a.max()), 6),
        "p01_lin": round(float(np.percentile(a, 1)), 6),
        "p99_lin": round(float(np.percentile(a, 99)), 6),
        "lit_ratio_pct": round(lit * 100.0, 2),
    }


def judge_lightmap_stats(stats):
    """One of four readings; never a fabricated green."""
    if not stats:
        return "LIGHTMAP-UNREADABLE"
    if stats["max_lin"] <= LIGHTMAP_BLACK_LIN:
        return "LIGHTMAP-EMPTY"
    if stats["std_lin"] <= 0.0:
        return "LIGHTMAP-UNIFORM"
    return "LIGHTMAP-CONTENT-OK"


def lightmap_quality_report(paths, source=None, reader=None):
    """Read every baked lightmap and name the ones with no light in them.

    `reader` exists so fixtures can supply pixel readings without an EXR file;
    production always uses lightmap_stats (ffmpeg + numpy).
    """
    read = reader or lightmap_stats
    paths = [p for p in (paths or []) if p]
    files = []
    for p in paths:
        st = read(p)
        entry = {"path": os.path.relpath(p, ROOT).replace("\\", "/")
                 if os.path.isabs(p) else p}
        if st:
            entry.update(st)
            entry["verdict"] = judge_lightmap_stats(st)
        else:
            entry["verdict"] = "LIGHTMAP-UNREADABLE"
        files.append(entry)
    if not paths:
        return {"files_read": 0, "files": [], "unreadable": "no-lightmaps",
                "qc_verdict": "LIGHTMAP-QC-NO-INPUT",
                "source": source or "recomputed-from-lightmaps"}
    readable = [f for f in files if f["verdict"] != "LIGHTMAP-UNREADABLE"]
    bad = [f["file"] for f in readable if f["verdict"] != "LIGHTMAP-CONTENT-OK"]
    if not readable:
        qc = "LIGHTMAP-QC-UNREADABLE"
    elif bad:
        qc = "LIGHTMAP-QC-INSUFFICIENT"
    else:
        qc = "LIGHTMAP-QC-PASS"
    return {
        "files_read": len(files),
        "files": files,
        "dark_files": bad,
        "min_lit_pct": min([f["lit_ratio_pct"] for f in readable], default=None),
        "mean_lin": (round(sum(f["mean_lin"] for f in readable)
                           / len(readable), 6) if readable else None),
        "qc_verdict": qc,
        "source": source or "recomputed-from-lightmaps",
    }


def bake_deliverable_ok(ok, quality):
    """M180: a clean bake is not a usable atlas.

    `ok` says the editor exited and reported bake_ok with N lightmaps on disk;
    the atlas itself can still be uniformly dark.  Both claims are needed
    before a bake receipt may count as supply.
    """
    return bool(ok) and bool(quality) \
        and quality.get("qc_verdict") == "LIGHTMAP-QC-PASS"


def last_bake_quality(rows=None):
    """Lightmap reading for the newest ok bake receipt (receipt first)."""
    rows = rows if rows is not None else receipt_rows()
    for r in reversed(rows):
        run = r.get("runner") or {}
        if run.get("kind") != "bake_spike" or not r.get("ok"):
            continue
        q = r.get("lightmap_quality")
        if not q:
            paths = [a.get("path") for a in (run.get("artifacts") or [])
                     if str(a.get("path", "")).lower().endswith(".exr")]
            paths = [p for p in paths
                     if p and os.path.exists(os.path.join(ROOT, p))]
            if not paths:
                return None
            q = lightmap_quality_report([os.path.join(ROOT, p) for p in paths])
        else:
            q = dict(q)
            q["source"] = "receipt"
        q["outdir"] = r.get("outdir")
        q["ts"] = r.get("ts")
        return q
    return None


def receipt_extra_fields(result):
    """Mirror readings written inside `runner` up to the receipt top level.

    Consumers (status/last_bake_quality/tests) read the top level, while the
    runner functions store the reading next to the artifacts they describe.
    Without this, a receipt can carry a full QC block that no reader ever sees
    (11-09 17:58 / 19:10 receipts: `deliverable_ok=true` with the reading
    nested and therefore invisible).  Old receipts are never rewritten.
    """
    extra = {}
    q = (result or {}).get("lightmap_quality")
    if q:
        extra["lightmap_quality"] = q
    fq = (result or {}).get("frame_quality")
    if fq:
        # flat magenta_pct/geom_pct are what consumers ask for; the runner keeps
        # them as magenta_max_pct/geom_min_pct next to the per-frame detail
        flat = dict(fq)
        if "magenta_pct" not in flat and flat.get("magenta_max_pct") is not None:
            flat["magenta_pct"] = flat.get("magenta_max_pct")
        if "geom_pct" not in flat and flat.get("geom_min_pct") is not None:
            flat["geom_pct"] = flat.get("geom_min_pct")
        extra["frame_quality"] = flat
    return extra


def _file_sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _product_scope(paths, outdir):
    """M181: was this product written into its own run directory?

    The labbench bake atlas lives in the project's Assets, a path every later
    bake rewrites, so "today's bytes match" is weaker evidence for a legacy
    receipt than for one whose product sits under its own run directory (T232).
    Both can be COMPUTABLE; they must not be counted as the same number.
    """
    if not paths:
        return "no-paths"
    if not outdir:
        return "legacy-shared-path"
    base = os.path.normpath(outdir)
    for p in paths:
        rel = os.path.normpath(str(p))
        try:
            inside = os.path.commonpath([base, rel]) == base
        except ValueError:      # different drives: cannot be inside
            inside = False
        if not inside:
            return "legacy-shared-path"
    return "run-dir"


def bake_reconcile(rows=None, reader=None, exists=None, hasher=None):
    """M181: can the uncertified bake receipts be certified from disk?

    Receipts written before lightmap QC existed carry no reading, so M180 has
    to call them `unknown`.  Their atlases may still be on disk - but the
    labbench atlas path is *shared and overwritten* by every bake, so a file
    that exists today is not evidence about an older run.  Each receipt
    declares sha256 per artifact, so the only honest test is: present **and**
    byte-identical to the declared digest.  Two named lists come out; nothing
    is rewritten, and a mismatch is never read as "dark".
    """
    rows = receipt_rows() if rows is None else rows
    read = reader or lightmap_stats
    exists = exists or (lambda rel: os.path.exists(
        os.path.normpath(os.path.join(ROOT, rel))))
    hasher = hasher or (lambda rel: _file_sha256(
        os.path.normpath(os.path.join(ROOT, rel))))
    certifiable, not_certifiable, declared = [], [], []
    for r in rows:
        run = r.get("runner") or {}
        if run.get("kind") != "bake_spike" or not r.get("ok"):
            continue
        exrs = [a for a in (run.get("artifacts") or [])
                if str(a.get("path", "")).lower().endswith(".exr")]
        q_declared = r.get("lightmap_quality") or run.get("lightmap_quality")
        if q_declared:
            # The producer wrote a reading (either layer).  That is evidence in
            # its own right - no recompute needed - but record whether today's
            # disk bytes still match, because the atlas path is shared.
            states = []
            for a in exrs:
                rel = a["path"]
                if not exists(rel):
                    states.append("missing-on-disk")
                    continue
                got = hasher(rel)
                states.append("sha-match" if (not a.get("sha256") or got == a["sha256"])
                              else "sha-mismatch(overwritten)")
            declared.append({"ts": r.get("ts"), "batch_id": r.get("batch_id"),
                             "outdir": r.get("outdir"),
                             "qc_verdict": q_declared.get("qc_verdict"),
                             "min_lit_pct": q_declared.get("min_lit_pct"),
                             "reading_source": "declared",
                             "product_scope": _product_scope(
                                 [a["path"] for a in exrs], r.get("outdir")),
                             "disk_states": states})
            continue
        entry = {"ts": r.get("ts"), "batch_id": r.get("batch_id"),
                 "outdir": r.get("outdir"), "exr_declared": len(exrs),
                 "product_scope": _product_scope(
                     [a["path"] for a in exrs], r.get("outdir")),
                 "files": []}
        if not exrs:
            entry["reason"] = "no-exr-in-receipt"
            not_certifiable.append(entry)
            continue
        missing, mismatched, readable = [], [], []
        for a in exrs:
            rel = a["path"]
            item = {"path": rel, "declared_sha256": a.get("sha256")}
            if not exists(rel):
                item["state"] = "missing-on-disk"
                missing.append(rel)
            else:
                got = hasher(rel)
                item["on_disk_sha256"] = got
                if a.get("sha256") and got and got != a["sha256"]:
                    item["state"] = "sha-mismatch(overwritten)"
                    mismatched.append(rel)
                else:
                    item["state"] = "sha-match"
                    readable.append(rel)
            entry["files"].append(item)
        entry["exr_missing"] = missing
        entry["exr_overwritten"] = mismatched
        if missing:
            entry["reason"] = "exr-missing-on-disk"
        elif mismatched:
            entry["reason"] = "exr-overwritten(sha-mismatch)"
        else:
            rep = lightmap_quality_report([os.path.join(ROOT, p) for p in readable],
                                          reader=read)
            entry["qc_verdict"] = rep["qc_verdict"]
            entry["min_lit_pct"] = rep.get("min_lit_pct")
            for item, f in zip(entry["files"], rep["files"]):
                item["verdict"] = f["verdict"]
                item.update({k: f.get(k) for k in
                             ("mean_lin", "std_lin", "max_lin", "lit_ratio_pct")})
            if rep["qc_verdict"] == "LIGHTMAP-QC-PASS":
                certifiable.append(entry)
                continue
            entry["reason"] = "lightmap-not-content-ok"
        not_certifiable.append(entry)
    reasons = {}
    for e in not_certifiable:
        reasons[e.get("reason", "?")] = reasons.get(e.get("reason", "?"), 0) + 1
    legacy = [e for e in (declared + certifiable)
              if e.get("product_scope") == "legacy-shared-path"]
    return {
        "ts": now().strftime("%Y-%m-%d %H:%M:%S"),
        "unread": len(certifiable) + len(not_certifiable),
        "declared": declared,
        "declared_count": len(declared),
        "certifiable": certifiable,
        "not_certifiable": not_certifiable,
        "certifiable_count": len(certifiable),
        "not_certifiable_count": len(not_certifiable),
        "legacy_shared": legacy,
        "legacy_shared_count": len(legacy),
        "reasons": reasons,
        "note": "declared = the receipt itself carries a QC reading; "
                "certifiable = no reading, but today's disk bytes are "
                "sha-identical to the receipt so the reading is recomputable; "
                "the atlas path is shared across bakes; legacy-shared-path = "
                "the artifact does not live under its own run directory, so "
                "a byte match may be a later run's identical product "
                "(M181: counted, never merged with same-run evidence)",
    }


def render_bake_reconcile(rec):
    print("bake_reconcile declared=%s recomputable=%s unrecomputable=%s"
          % (rec["declared_count"], rec["certifiable_count"],
             rec["not_certifiable_count"]))
    print("scope legacy_shared=%s(artifact outside its own run dir; a byte "
          "match may belong to a later run)"
          % rec.get("legacy_shared_count", 0))
    if rec["reasons"]:
        print("reasons " + " ".join("%s=%s" % (k, v)
                                    for k, v in sorted(rec["reasons"].items())))
    for e in rec["declared"]:
        print("DECLARED %s %s qc=%s min_lit=%s%% disk=%s"
              % (e["ts"], e["batch_id"], e.get("qc_verdict"),
                 e.get("min_lit_pct"), ",".join(e["disk_states"]) or "-"))
    for e in rec["certifiable"]:
        shas = ",".join((f.get("on_disk_sha256") or "")[:8] for f in e["files"])
        print("CERTIFIABLE %s %s qc=%s min_lit=%s%% files=%s sha=%s"
              % (e["ts"], e["batch_id"], e.get("qc_verdict"),
                 e.get("min_lit_pct"), len(e["files"]), shas))
    for e in rec["not_certifiable"]:
        print("NOT-CERTIFIABLE %s %s reason=%s missing=%s overwritten=%s"
              % (e["ts"], e["batch_id"], e.get("reason"),
                 len(e.get("exr_missing") or []),
                 len(e.get("exr_overwritten") or [])))
    return 0


KNOWN_BAD_AGE_DAYS = 7
UNKNOWN_CONVERGE_SIDECAR = os.path.join(ROOT, "state", "unknown-converge.json")


def converge_unknown(rows=None, max_age_days=KNOWN_BAD_AGE_DAYS, reader=None,
                     exists=None, hasher=None, now_dt=None):
    """T230: `unknown` must have an exit, or it becomes quiet garbage.

    A receipt that carries no QC reading reads `unknown` - honest, but it can
    only leave that state when the card runs again.  On a lane that never opens
    it would sit there forever, counted as neither supply nor failure.  So each
    unknown receipt is classified once, from today's disk:

    * COMPUTABLE   - the product's own bytes are still byte-identical to what
                     the receipt declared, so the reading is recomputable now.
    * UNPROVABLE   - files missing/overwritten or the card has no QC concept;
                     names the reason instead of guessing.
    * KNOWN-BAD    - unprovable AND older than `max_age_days`:降级, so the face
                     stops implying "might still be good".

    Nothing is written back into the receipt log; the result is a sidecar.
    """
    rows = receipt_rows() if rows is None else rows
    now_dt = now_dt or now()
    exists = exists or (lambda rel: os.path.exists(
        os.path.normpath(os.path.join(ROOT, rel))))
    hasher = hasher or (lambda rel: _file_sha256(
        os.path.normpath(os.path.join(ROOT, rel))))
    reader = reader or lightmap_stats
    computable, unprovable = [], []
    unknown = 0
    for r in rows:
        if receipt_state(r) != "unknown":
            continue
        unknown += 1
        run = r.get("runner") or {}
        kind = run.get("kind")
        age_h = _receipt_age_h(r.get("ts"))
        entry = {"ts": r.get("ts"), "batch_id": r.get("batch_id"),
                 "outdir": r.get("outdir"), "kind": kind,
                 "age_h": None if age_h is None else round(age_h, 2),
                 "age_days": None if age_h is None else round(age_h / 24.0, 2)}
        verdict, reason = "UNPROVABLE", None
        if kind == "bake_spike":
            exr_paths = [a.get("path") for a in (run.get("artifacts") or [])
                         if str(a.get("path", "")).lower().endswith(".exr")]
            entry["product_scope"] = _product_scope(exr_paths, r.get("outdir"))
            rec = bake_reconcile(rows=[r], reader=reader, exists=exists,
                                 hasher=hasher)
            if rec["certifiable_count"] == 1:
                verdict = "COMPUTABLE"
                reason = "lightmap-recomputed-from-disk"
                entry["qc_verdict"] = rec["certifiable"][0].get("qc_verdict")
                entry["min_lit_pct"] = rec["certifiable"][0].get("min_lit_pct")
            elif rec["declared_count"] == 1:
                verdict = "COMPUTABLE"
                reason = "reading-declared-in-receipt"
                entry["qc_verdict"] = rec["declared"][0].get("qc_verdict")
            else:
                reason = (rec["not_certifiable"][0].get("reason")
                          if rec["not_certifiable"] else "no-bake-entry")
        elif kind == "render_spike":
            pngs = [a for a in (run.get("artifacts") or [])
                    if str(a.get("path", "")).lower().endswith(".png")]
            missing, mismatched = [], []
            for a in pngs:
                rel = a["path"]
                if not exists(rel):
                    missing.append(rel)
                elif a.get("sha256") and hasher(rel) != a["sha256"]:
                    mismatched.append(rel)
            entry["frames_declared"] = len(pngs)
            entry["product_scope"] = _product_scope(
                [a["path"] for a in pngs], r.get("outdir"))
            if not pngs:
                reason = "no-png-in-receipt"
            elif missing:
                reason = "frames-missing-on-disk"
                entry["frames_missing"] = len(missing)
            elif mismatched:
                reason = "frames-overwritten(sha-mismatch)"
                entry["frames_overwritten"] = len(mismatched)
            else:
                frames_dir = os.path.dirname(
                    os.path.normpath(os.path.join(ROOT, pngs[0]["path"])))
                rep = frame_quality_report(frames_dir)
                entry["qc_verdict"] = rep.get("qc_verdict")
                entry["frames_read"] = rep.get("frames_read")
                verdict = "COMPUTABLE"
                reason = "frame-quality-recomputed-from-disk"
        else:
            reason = "card-kind-has-no-qc(%s)" % (kind or "?")
        entry["verdict"] = verdict
        entry["reason"] = reason
        if verdict == "COMPUTABLE":
            qv = str(entry.get("qc_verdict") or "")
            # Computable means "the reading is now known" - it may well be a
            # failed QC.  Naming that separately stops a provably-bad batch
            # from resting in the same bucket as an unreadable one.
            entry["qc_ok"] = qv.endswith("-QC-PASS")
            computable.append(entry)
            continue
        if entry["age_days"] is not None and entry["age_days"] > max_age_days:
            entry["known_bad"] = True
            entry["known_bad_reason"] = (
                "unprovable and older than %dd" % max_age_days)
        unprovable.append(entry)
    known_bad = [e for e in unprovable if e.get("known_bad")]
    reasons = {}
    for e in unprovable:
        reasons[e["reason"] or "?"] = reasons.get(e["reason"] or "?", 0) + 1
    pass_n = sum(1 for e in computable if e.get("qc_ok"))
    legacy = [e for e in computable
              if e.get("product_scope") == "legacy-shared-path"]
    return {
        "ts": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "scanned": len(rows),
        "unknown": unknown,
        "computable": computable,
        "computable_count": len(computable),
        "computable_pass_count": pass_n,
        "computable_fail_count": len(computable) - pass_n,
        "legacy_shared": legacy,
        "legacy_shared_count": len(legacy),
        "run_dir_computable_count": len(computable) - len(legacy),
        "unprovable": unprovable,
        "unprovable_count": len(unprovable),
        "known_bad": known_bad,
        "known_bad_count": len(known_bad),
        "reasons": reasons,
        "age_policy_days": max_age_days,
        "note": "COMPUTABLE = today's bytes still match the receipt, so the "
                "reading is recomputed now; KNOWN-BAD = unprovable and older "
                "than the age policy; legacy-shared-path = the artifact lives "
                "outside its own run directory, so a byte match may belong to "
                "a later run (M181: reported separately, never merged into "
                "same-run evidence); receipts are never rewritten",
    }


def unknown_converge_reading(path=None):
    """Latest sidecar, for faces that must not re-run the recompute.

    Recomputation costs an ffmpeg read per EXR; status and review read the
    published numbers instead and say `n/a` when no sidecar exists yet, rather
    than pretending the unknown set is empty.  The reader itself lives in
    batch_pool so `pool_igniter status` and `batch_pool review` cannot drift.
    """
    import batch_pool
    return batch_pool.unknown_converge_reading(path)


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
            "receipts_deliverable": sum(1 for r in receipts
                                        if receipt_deliverable(r)),
            "receipts_uncertified": sum(1 for r in receipts
                                        if receipt_state(r) == "unknown"),
            "last_receipt": receipts[-1]["ts"] if receipts else None,
            "last_failure": last_failure(receipts),
            "duplicate_window_h": DUPLICATE_WINDOW_H,
            "duplicate_skips_today": duplicate_skips_today(),
            "frame_quality": last_render_quality(receipts),
            "bake_quality": last_bake_quality(receipts),
            "delivery_audit": delivery_audit(receipts),
            "unknown_converge": unknown_converge_reading(),
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
          "deliverable=%s uncertified=%s last_tick_age_min=%s"
          % (rd["dispatchable"], rd["runnable"], rd["gap"],
             ign["receipts"], ign["receipts_ok"],
             ign.get("receipts_deliverable", "?"),
             ign.get("receipts_uncertified", "?"),
             ign["last_tick_age_min"]))
    print("duplicate_guard=window%gh skips_today=%s"
          % (ign["duplicate_window_h"], ign["duplicate_skips_today"]))
    da = ign.get("delivery_audit") or {}
    tot = da.get("totals") or {}
    if tot:
        print("delivery_audit runs=%s unique_payloads=%s pre_guard=%s "
              "redundant=%s"
              % (tot["ok_runs"], tot["unique_payloads"],
                 tot["pre_guard_runs"], tot["redundant_runs"]))
        for bid in sorted(da.get("cards") or {}):
            c = da["cards"][bid]
            print("  %s runs=%s unique=%s pre_guard=%s redundant=%s"
                  % (bid, c["ok_runs"], c["unique_payloads"],
                     c["pre_guard_runs"], c["redundant_runs"]))
    fq = ign.get("frame_quality")
    if fq and fq.get("frames_read"):
        print("frame_quality frames=%s magenta_max=%s%%(%s) geom_min=%s%%(%s) "
              "placeholder_frames=%s qc=%s source=%s"
              % (fq["frames_read"], fq["magenta_max_pct"],
                 fq["magenta_max_frame"], fq["geom_min_pct"],
                 fq["geom_min_frame"], fq.get("placeholder_frames"),
                 fq.get("qc_verdict"), fq.get("source")))
    elif fq:
        print("frame_quality unreadable=%s" % fq.get("unreadable", "?"))
    else:
        print("frame_quality=none(no ok render yet)")
    bq = ign.get("bake_quality")
    if bq and bq.get("files_read"):
        print("bake_quality files=%s min_lit=%s%% mean_lin=%s dark=%s qc=%s source=%s"
              % (bq["files_read"], bq.get("min_lit_pct"), bq.get("mean_lin"),
                 ",".join(bq.get("dark_files") or []) or "none",
                 bq.get("qc_verdict"), bq.get("source")))
    elif bq:
        print("bake_quality %s" % bq.get("unreadable", "no-lightmaps"))
    else:
        print("bake_quality=none(no ok bake yet)")
    for u in rd["declared_unready"]:
        print("unready %s reason=%s" % (u["batch_id"], u["reason"]))
    lf = ign["last_failure"]
    if lf:
        print("last_failure ts=%s batch=%s exit=%s error=%s"
              % (lf["ts"], lf["batch_id"], lf["exit"], lf["error"]))
    else:
        print("last_failure=none")
    uc = ign.get("unknown_converge")
    if uc:
        print("unknown_converge computable=%s(pass=%s fail=%s) unprovable=%s "
              "known_bad=%s as_of=%s policy=%sd"
              % (uc["computable"], uc.get("computable_pass"),
                 uc.get("computable_fail"), uc["unprovable"],
                 uc["known_bad"], uc["as_of"], uc["age_policy_days"]))
    else:
        print("unknown_converge n/a(no-sidecar: run `unknown-converge`)")
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

        # --- duplicate-ignition guard (tech T224 / R-069) -------------------
        fp, parts = payload_fingerprint("B-CITY3D-01", project_root=proj)
        chk("S26 fingerprint is stable and names its parts",
            isinstance(fp, str) and len(fp) == 16
            and parts.get("scheme") == FINGERPRINT_SCHEME
            and parts.get("entry") == "absent", parts)

        fp2, _ = payload_fingerprint("B-CITY3D-01", project_root=proj)
        chk("S27 same payload -> same fingerprint", fp == fp2, (fp, fp2))

        fp_unknown, _ = payload_fingerprint("B-NOT-IN-REGISTRY-99")
        chk("S28 card not in registry -> no fingerprint", fp_unknown is None,
            fp_unknown)

        allowed, why = duplicate_guard("B-CITY3D-01", rows=[], fingerprint=fp)
        chk("S29 no ok baseline -> allowed", allowed and why == "no-ok-baseline",
            why)

        recent = (now() - datetime.timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S")
        same = {"batch_id": "B-CITY3D-01", "ok": True, "ts": recent,
                "fingerprint": fp, "runner": {}}
        allowed, why = duplicate_guard("B-CITY3D-01", rows=[same], fingerprint=fp)
        chk("S30 same card + same payload within window -> skip",
            not allowed and why.startswith("same-payload-within-"), why)

        moved = dict(same, fingerprint="deadbeefdeadbeef")
        allowed, why = duplicate_guard("B-CITY3D-01", rows=[moved], fingerprint=fp)
        chk("S31 payload changed -> allowed", allowed
            and why.startswith("payload-changed"), why)

        old_ok = dict(same, ts=(now() - datetime.timedelta(hours=7)).strftime(
            "%Y-%m-%d %H:%M:%S"))
        allowed, why = duplicate_guard("B-CITY3D-01", rows=[old_ok], fingerprint=fp)
        chk("S32 baseline older than the window -> allowed", allowed
            and why.startswith("baseline-stale"), why)

        failed = {"batch_id": "B-CITY3D-01", "ok": False, "ts": recent,
                  "fingerprint": fp, "runner": {"error": {"lines": ["x"]}}}
        allowed, why = duplicate_guard("B-CITY3D-01", rows=[failed],
                                       fingerprint=fp)
        chk("S33 a failure is not a baseline (retry stays allowed)",
            allowed and why == "no-ok-baseline", why)

        pre_fp = {"batch_id": "B-CITY3D-01", "ok": True, "ts": recent,
                  "runner": {}}
        allowed, why = duplicate_guard("B-CITY3D-01", rows=[pre_fp],
                                       fingerprint=fp)
        chk("S34 legacy ok receipt without fingerprint lets one run through",
            allowed and why.startswith("baseline-without-fingerprint"), why)

        # --- delivery audit: runs are not deliveries (tech T231) -----------
        da = delivery_audit([dict(same, fingerprint="aaaabbbbccccdddd"),
                             dict(same, fingerprint="aaaabbbbccccdddd"),
                             dict(failed, fingerprint="aaaabbbbccccdddd")])
        chk("S35 same payload built twice -> 1 unique + 1 redundant, "
            "failure not counted",
            da["totals"]["ok_runs"] == 2
            and da["totals"]["unique_payloads"] == 1
            and da["totals"]["redundant_runs"] == 1
            and da["totals"]["pre_guard_runs"] == 0, da)

        da = delivery_audit([pre_fp, pre_fp])
        chk("S36 pre-guard receipts are named but never counted as value",
            da["totals"]["unique_payloads"] == 0
            and da["totals"]["pre_guard_runs"] == 2
            and da["totals"]["redundant_runs"] == 0, da)

        da = delivery_audit([dict(same, fingerprint="aaaabbbbccccdddd"),
                             dict(same, fingerprint="1111222233334444")])
        chk("S37 two different payloads -> 2 unique, 0 redundant",
            da["totals"]["unique_payloads"] == 2
            and da["totals"]["redundant_runs"] == 0, da)

        da = delivery_audit([])
        chk("S38 empty ledger reads as zeros, not as absent",
            da["totals"] == {"ok_runs": 0, "unique_payloads": 0,
                             "pre_guard_runs": 0, "redundant_runs": 0},
            da["totals"])

        # --- frame quality (tech T226) ------------------------------------
        # Two synthetic frames whose defect blank/duplicate cannot see: a
        # magenta fallback-material frame and a near-empty frame that is
        # 99.7% background yet still passes the luma-std floor.
        from PIL import Image
        fq_dir = os.path.join(tmp, "frames")
        os.makedirs(fq_dir, exist_ok=True)
        mag = Image.new("RGB", (100, 100), (255, 0, 255))
        mag.save(os.path.join(fq_dir, "frame_01.png"))
        sparse = Image.new("RGB", (100, 100), (13, 13, 15))
        for x in range(10):
            for y in range(10):
                sparse.putpixel((x, y), (255, 255, 255))
        sparse.save(os.path.join(fq_dir, "frame_02.png"))
        ok_dir = os.path.join(tmp, "frames-ok")
        os.makedirs(ok_dir, exist_ok=True)
        good = Image.new("RGB", (100, 100), (13, 13, 15))
        for x in range(100):
            for y in range(20, 80):
                good.putpixel((x, y), (110, 120, 90))
        good.save(os.path.join(ok_dir, "frame_01.png"))

        q = frame_quality(os.path.join(fq_dir, "frame_01.png"))
        chk("S35 all-magenta frame reads magenta=100/geom=0",
            q["magenta_pct"] == 100.0 and q["geom_pct"] == 0.0, q)

        rep = frame_quality_report(fq_dir)
        chk("S36 magenta frame and sparse frame are named by their own metric",
            rep["magenta_max_frame"] == "frame_01.png"
            and rep["magenta_max_pct"] == 100.0
            and rep["geom_min_frame"] == "frame_02.png"
            and rep["geom_min_pct"] == 1.0, rep)

        # A magenta frame is *drawn* (100% geometry) with broken material, so
        # the two readings must be read together - geom alone would pass it.
        q_mag = frame_quality(os.path.join(fq_dir, "frame_01.png"),
                              clear_rgb=CLEAR_RGB_DEFAULT)
        chk("S36b magenta frame shows up in geom as drawn (why both are needed)",
            q_mag["geom_pct"] == 100.0 and q_mag["magenta_pct"] == 100.0, q_mag)

        q_sparse = frame_quality(os.path.join(fq_dir, "frame_02.png"),
                                 clear_rgb=CLEAR_RGB_DEFAULT)
        chk("S37 near-empty frame reads geom=1.0% against declared clear",
            q_sparse["geom_pct"] == 1.0 and q_sparse["bg_source"] == "declared",
            q_sparse)

        q_good = frame_quality(os.path.join(ok_dir, "frame_01.png"),
                               clear_rgb=CLEAR_RGB_DEFAULT)
        chk("S38 normal frame reads geom=60.0% and magenta=0",
            q_good["geom_pct"] == 60.0 and q_good["magenta_pct"] == 0.0, q_good)

        # declared vs modal must stay distinguishable: on the good frame the
        # modal colour is the clear colour anyway, so use a floor-dominated
        # frame to prove the two estimates differ and are labelled.
        floor = Image.new("RGB", (100, 100), (13, 13, 15))
        for x in range(100):
            for y in range(0, 90):
                floor.putpixel((x, y), (60, 60, 60))
        floor.save(os.path.join(fq_dir, "frame_03.png"))
        q_decl = frame_quality(os.path.join(fq_dir, "frame_03.png"),
                              clear_rgb=CLEAR_RGB_DEFAULT)
        q_modal = frame_quality(os.path.join(fq_dir, "frame_03.png"))
        chk("S39 declared vs modal background are labelled and differ",
            q_decl["bg_source"] == "declared" and q_modal["bg_source"] == "modal"
            and q_decl["geom_pct"] == 90.0 and q_modal["geom_pct"] == 10.0,
            (q_decl, q_modal))

        bad = os.path.join(tmp, "not-a-png.png")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("not a png\n")
        chk("S40 unreadable frame returns None instead of a zero reading",
            frame_quality(bad) is None)
        chk("S41 empty frame dir -> frames_read=0 and named reason",
            frame_quality_report(os.path.join(tmp, "empty-frames"))
            .get("unreadable") == "no-frames")

        # D-20261109-08: a frame set with placeholder material may not be
        # judged PASS, and the run-level ok must not be redefined silently.
        rep_bad = frame_quality_report(fq_dir)
        chk("S42 placeholder frames -> QC-INSUFFICIENT, not PASS",
            rep_bad["qc_verdict"] == "FRAME-QC-INSUFFICIENT-PLACEHOLDER"
            and rep_bad["placeholder_frames"] == 1, rep_bad["qc_verdict"])
        rep_ok = frame_quality_report(ok_dir)
        chk("S43 clean frame set -> QC-PASS with zero placeholder",
            rep_ok["qc_verdict"] == "FRAME-QC-PASS"
            and rep_ok["placeholder_frames"] == 0, rep_ok["qc_verdict"])

        # lightmap QC: the bake card's atlas was counted deliverable for weeks
        # without anything reading it.  These fixtures pin the four readings.
        good_st = {"file": "a.exr", "mean_lin": 0.21, "std_lin": 0.05,
                   "max_lin": 0.28, "lit_ratio_pct": 100.0, "pixels": 16}
        chk("S44a lit atlas -> CONTENT-OK",
            judge_lightmap_stats(good_st) == "LIGHTMAP-CONTENT-OK")
        chk("S44b black atlas -> EMPTY",
            judge_lightmap_stats({"file": "b.exr", "mean_lin": 0.0,
                                  "std_lin": 0.0, "max_lin": 0.0,
                                  "lit_ratio_pct": 0.0, "pixels": 16})
            == "LIGHTMAP-EMPTY")
        chk("S44c flat atlas -> UNIFORM",
            judge_lightmap_stats({"file": "c.exr", "mean_lin": 0.5,
                                  "std_lin": 0.0, "max_lin": 0.5,
                                  "lit_ratio_pct": 100.0, "pixels": 16})
            == "LIGHTMAP-UNIFORM")
        chk("S44d missing reading -> UNREADABLE, never green",
            judge_lightmap_stats(None) == "LIGHTMAP-UNREADABLE")

        synth = os.path.join(tmp, "synth.exr")
        with open(synth, "wb") as f:
            f.write(b"\x76\x2f\x31\x01" + b"\x00" * 10
                    + b"dataWindow\x00box2i\x00" + struct.pack("<i", 16)
                    + struct.pack("<4i", 0, 0, 1023, 1023))
        chk("S45a EXR header yields dataWindow size",
            exr_dimensions(synth) == (1024, 1024), exr_dimensions(synth))
        chk("S45b non-EXR file -> None", exr_dimensions(bad) is None)

        rep_none = lightmap_quality_report([])
        chk("S46 no lightmaps -> QC-NO-INPUT with named reason",
            rep_none["qc_verdict"] == "LIGHTMAP-QC-NO-INPUT"
            and rep_none["unreadable"] == "no-lightmaps", rep_none["qc_verdict"])

        rep_unread = lightmap_quality_report([os.path.join(tmp, "nope.exr")])
        chk("S47 unreadable atlas -> QC-UNREADABLE and named",
            rep_unread["qc_verdict"] == "LIGHTMAP-QC-UNREADABLE"
            and rep_unread["files"][0]["verdict"] == "LIGHTMAP-UNREADABLE",
            rep_unread["qc_verdict"])

        def _fake(dark):
            def _r(p):
                if "dark" in p:
                    return {"file": "dark.exr", "mean_lin": 0.0,
                            "std_lin": 0.0, "max_lin": 0.0,
                            "lit_ratio_pct": 0.0, "pixels": 16}
                return dict(good_st)
            return _r

        rep_lit = lightmap_quality_report([os.path.join(tmp, "lit-a.exr"),
                                           os.path.join(tmp, "lit-b.exr")],
                                          reader=_fake(False))
        chk("S48 two lit atlases -> QC-PASS with min_lit reported",
            rep_lit["qc_verdict"] == "LIGHTMAP-QC-PASS"
            and rep_lit["min_lit_pct"] == 100.0, rep_lit["qc_verdict"])

        rep_dark = lightmap_quality_report([os.path.join(tmp, "lit-a.exr"),
                                           os.path.join(tmp, "dark-b.exr")],
                                           reader=_fake(True))
        chk("S49 one black atlas -> QC-INSUFFICIENT and names it",
            rep_dark["qc_verdict"] == "LIGHTMAP-QC-INSUFFICIENT"
            and rep_dark["dark_files"] == ["dark.exr"], rep_dark)

        chk("S50 no bake receipt -> last_bake_quality is None, not a zero read",
            last_bake_quality([]) is None)

        chk("S51 M180: a clean bake with a black atlas is not deliverable",
            bake_deliverable_ok(True, rep_dark) is False
            and bake_deliverable_ok(True, None) is False)
        chk("S52 M180: only a lit atlas certifies the bake",
            bake_deliverable_ok(True, rep_lit) is True
            and bake_deliverable_ok(False, rep_lit) is False)

        # M181: reconciling old receipts.  The labbench atlas path is shared
        # and rewritten by every bake, so "the file is there" proves nothing;
        # only a sha256 match ties a file on disk to that receipt's run.
        def _bake(ts, exrs, quality=None, ok=True, kind="bake_spike"):
            return {"ts": ts, "batch_id": "B-CITY3D-01", "ok": ok,
                    "outdir": "media/pool-ignite/B-CITY3D-01/x",
                    "lightmap_quality": quality,
                    "runner": {"kind": kind,
                               "artifacts": [dict(path=p, sha256=s)
                                             for p, s in exrs]}}

        on_disk = {"a.exr": "aaa", "b.exr": "bbb"}
        rec_ok = bake_reconcile(
            [_bake("t1", [("a.exr", "aaa"), ("b.exr", "bbb")])],
            reader=_fake(False),
            exists=lambda rel: rel in on_disk,
            hasher=lambda rel: on_disk[rel])
        chk("S53 M181: sha-matched lit atlases -> certifiable",
            rec_ok["certifiable_count"] == 1
            and rec_ok["not_certifiable_count"] == 0
            and rec_ok["certifiable"][0]["qc_verdict"] == "LIGHTMAP-QC-PASS",
            rec_ok["reasons"])

        rec_over = bake_reconcile(
            [_bake("t2", [("a.exr", "zzz"), ("b.exr", "bbb")])],
            reader=_fake(False),
            exists=lambda rel: rel in on_disk,
            hasher=lambda rel: on_disk[rel])
        chk("S54 M181: one overwritten atlas -> not certifiable, no fake dark",
            rec_over["certifiable_count"] == 0
            and rec_over["reasons"].get("exr-overwritten(sha-mismatch)") == 1,
            rec_over["reasons"])

        rec_miss = bake_reconcile(
            [_bake("t3", [("gone.exr", "xxx")])],
            reader=_fake(False),
            exists=lambda rel: False,
            hasher=lambda rel: None)
        chk("S55 M181: atlas gone from disk -> missing, named",
            rec_miss["reasons"].get("exr-missing-on-disk") == 1,
            rec_miss["reasons"])

        rec_dark = bake_reconcile(
            [_bake("t4", [("dark.exr", "ddd")])],
            reader=_fake(True),
            exists=lambda rel: True,
            hasher=lambda rel: "ddd")
        chk("S56 M181: all-match but black atlas -> not certifiable, named",
            rec_dark["reasons"].get("lightmap-not-content-ok") == 1,
            rec_dark["reasons"])

        rec_skip = bake_reconcile(
            [_bake("t5", [("a.exr", "aaa")], quality={"qc_verdict": "x"}),
             _bake("t6", [("a.exr", "aaa")], ok=False),
             _bake("t7", [("a.exr", "aaa")], kind="render_spike")],
            reader=_fake(False),
            exists=lambda rel: True,
            hasher=lambda rel: "aaa")
        chk("S57 M181: already-declared, failed and render receipts stay out of the recompute list",
            rec_skip["unread"] == 0 and rec_skip["declared_count"] == 1,
            (rec_skip["reasons"], rec_skip["declared_count"]))

        rec_zero = bake_reconcile([], reader=_fake(False))
        chk("S58 M181: no receipts -> zero, not a fabricated verdict",
            rec_zero["unread"] == 0 and rec_zero["declared"] == []
            and rec_zero["certifiable"] == []
            and rec_zero["not_certifiable"] == [])

        # The reading lives inside `runner` while consumers read the top level;
        # mirror it or the evidence is invisible to every reader.
        nested = {"kind": "bake_spike",
                  "lightmap_quality": {"qc_verdict": "LIGHTMAP-QC-PASS"}}
        chk("S59 M181: runner-level reading is mirrored to the receipt top level",
            receipt_extra_fields(nested).get("lightmap_quality", {})
            .get("qc_verdict") == "LIGHTMAP-QC-PASS")
        chk("S60 M181: no reading -> nothing mirrored (no invented field)",
            receipt_extra_fields({"kind": "bake_spike"}) == {})

        # T230: `unknown` must have an exit.  A receipt with no QC reading can
        # only leave that state by running again - impossible on a lane that
        # never opens, so it would sit as neither supply nor failure forever.
        def _unk(ts, kind, artifacts, ok=True):
            return {"ts": ts, "batch_id": "B-CITY3D-01", "ok": ok,
                    "outdir": "media/pool-ignite/x",
                    "runner": {"kind": kind, "artifacts": artifacts}}

        fresh = now().strftime("%Y-%m-%d %H:%M:%S")
        old = (now() - datetime.timedelta(days=8)
               ).strftime("%Y-%m-%d %H:%M:%S")
        disk = {"a.exr": "aaa", "b.exr": "bbb"}
        conv_ok = converge_unknown(
            [_unk(fresh, "bake_spike",
                  [{"path": "a.exr", "sha256": "aaa"},
                   {"path": "b.exr", "sha256": "bbb"}])],
            reader=_fake(False),
            exists=lambda rel: rel in disk, hasher=lambda rel: disk[rel])
        chk("S61 T230: unknown bake with sha-identical atlas -> COMPUTABLE",
            conv_ok["unknown"] == 1 and conv_ok["computable_count"] == 1
            and conv_ok["computable"][0]["qc_verdict"] == "LIGHTMAP-QC-PASS"
            and conv_ok["known_bad_count"] == 0, conv_ok["reasons"])

        conv_bad = converge_unknown(
            [_unk(old, "bake_spike",
                  [{"path": "a.exr", "sha256": "zzz"}])],
            reader=_fake(False), exists=lambda rel: True,
            hasher=lambda rel: "aaa")
        chk("S62 T230: unprovable + older than policy -> KNOWN-BAD, reason named",
            conv_bad["computable_count"] == 0
            and conv_bad["known_bad_count"] == 1
            and conv_bad["unprovable"][0]["reason"]
            == "exr-overwritten(sha-mismatch)"
            and conv_bad["unprovable"][0].get("known_bad") is True,
            conv_bad["unprovable"])

        # A render receipt predating T226 has no QC verdict but its frames live
        # in a per-run directory (never overwritten), so the reading really is
        # recomputable today.
        frames_dir = os.path.join(tmp, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        shot = os.path.join(frames_dir, "frame_01.png")
        made_png = False
        try:
            from PIL import Image
            Image.new("RGB", (8, 8), (13, 13, 15)).save(shot)
            made_png = True
        except ImportError:
            pass
        if made_png:
            conv_render = converge_unknown(
                [_unk(fresh, "render_spike",
                      [{"path": shot, "sha256": _file_sha256(shot)}])])
            chk("S63 T230: unknown render frame set on disk -> COMPUTABLE",
                conv_render["computable_count"] == 1
                and conv_render["computable"][0]["qc_verdict"]
                == "FRAME-QC-PASS"
                and conv_render["computable_pass_count"] == 1,
                conv_render["reasons"])
            mag = os.path.join(frames_dir, "frame_02.png")
            from PIL import Image
            Image.new("RGB", (8, 8), (255, 0, 255)).save(mag)
            conv_mag = converge_unknown(
                [_unk(fresh, "render_spike",
                      [{"path": mag, "sha256": _file_sha256(mag)}])])
            chk("S68 T230: provably placeholder frames -> computable but qc fail",
                conv_mag["computable_count"] == 1
                and conv_mag["computable_fail_count"] == 1
                and conv_mag["computable"][0]["qc_ok"] is False,
                conv_mag["computable"])
            os.remove(shot)
            os.remove(mag)
        else:
            chk("S63 T230: frame recompute fixture skipped (no PIL) -> not claimed",
                True, "PIL missing")

        conv_gone = converge_unknown(
            [_unk(fresh, "render_spike",
                  [{"path": os.path.join(tmp, "gone", "f.png"),
                    "sha256": "abc"}])])
        chk("S64 T230: frames gone -> UNPROVABLE named, not counted as supply",
            conv_gone["computable_count"] == 0
            and conv_gone["unprovable"][0]["reason"] == "frames-missing-on-disk"
            and conv_gone["unprovable"][0].get("known_bad") is None,
            conv_gone["reasons"])

        side = os.path.join(tmp, "unknown-converge.json")
        with open(side, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"ts": "2026-11-10 01:00:00", "unknown": 3,
                                "computable_count": 1, "unprovable_count": 2,
                                "known_bad_count": 0,
                                "known_bad": [],
                                "age_policy_days": 7}))
        rd_side = unknown_converge_reading(side)
        chk("S65 T230: sidecar reading is published as-is",
            rd_side["computable"] == 1 and rd_side["unprovable"] == 2
            and rd_side["known_bad"] == 0, rd_side)
        chk("S66 T230: no sidecar -> None, never a fabricated empty set",
            unknown_converge_reading(os.path.join(tmp, "nope.json")) is None)
        conv_none = converge_unknown([{"ok": True, "ts": fresh,
                                      "runner": {"kind": "render_spike",
                                                 "frame_quality":
                                                 {"qc_verdict": "FRAME-QC-PASS"}}}])
        chk("S67 T230: certificated receipts stay out of the unknown set",
            conv_none["unknown"] == 0 and conv_none["computable_count"] == 0)

        # T232: the bake writes its atlases into the project's Assets, which the
        # next bake overwrites. A receipt pointing there stops matching its own
        # sha256, so the run must snapshot the bytes first.
        proj_assets = os.path.join(tmp, "proj", "Assets")
        os.makedirs(proj_assets, exist_ok=True)
        exr_a = os.path.join(proj_assets, "Lightmap-0_comp_light.exr")
        with open(exr_a, "wb") as f:
            f.write(b"EXR-A" * 16)
        run1 = os.path.join(tmp, "run1")
        kept1 = preserve_lightmaps([exr_a], run1)
        chk("S69 T232: atlas is copied into the run directory",
            len(kept1) == 1 and os.path.exists(kept1[0]["path"])
            and kept1[0]["path"].startswith(run1)
            and kept1[0]["source"] == exr_a, kept1)
        sha_before = kept1[0]["sha256"]
        chk("S70 T232: copy hash equals the source bytes at snapshot time",
            sha_before == _file_sha256(exr_a), sha_before)

        # the next bake overwrites the project atlas - the snapshot must not move
        with open(exr_a, "wb") as f:
            f.write(b"EXR-B" * 16)
        chk("S71 T232: later bake overwriting the project atlas leaves the "
            "snapshot provable",
            _file_sha256(kept1[0]["path"]) == sha_before
            and _file_sha256(exr_a) != sha_before, "snapshot drifted")

        run2 = os.path.join(tmp, "run2")
        kept2 = preserve_lightmaps([exr_a], run2)
        chk("S72 T232: two consecutive runs each keep their own provable copy",
            len(kept2) == 1 and kept2[0]["path"] != kept1[0]["path"]
            and _file_sha256(kept1[0]["path"]) == sha_before
            and _file_sha256(kept2[0]["path"]) == _file_sha256(exr_a),
            "second run invalidated the first")
        chk("S73 T232: unreadable source is skipped, not recorded as a copy",
            preserve_lightmaps([os.path.join(tmp, "nope.exr")], run2) == [])

        # M181: a byte match is only as strong as where the product lives.  A
        # legacy receipt points at the shared labbench atlas; the same bytes can
        # be a later run's identical output, so it may not be counted as
        # same-run evidence.
        run_dir_art = "media/pool-ignite/x/lightmaps/a.exr"
        disk_run = {run_dir_art: "aaa"}
        conv_run = converge_unknown(
            [_unk(fresh, "bake_spike", [{"path": run_dir_art, "sha256": "aaa"}])],
            reader=_fake(False),
            exists=lambda rel: rel in disk_run, hasher=lambda rel: disk_run[rel])
        chk("S74 M181: artifact under its own run dir -> scope=run-dir",
            conv_run["computable_count"] == 1
            and conv_run["computable"][0].get("product_scope") == "run-dir"
            and conv_run["legacy_shared_count"] == 0
            and conv_run["run_dir_computable_count"] == 1, conv_run["computable"])

        conv_legacy = converge_unknown(
            [_unk(fresh, "bake_spike", [{"path": "a.exr", "sha256": "aaa"}])],
            reader=_fake(False),
            exists=lambda rel: rel in disk, hasher=lambda rel: disk[rel])
        chk("S75 M181: artifact outside its run dir stays COMPUTABLE but is "
            "counted as legacy-shared, not same-run",
            conv_legacy["computable_count"] == 1
            and conv_legacy["computable"][0].get("product_scope")
            == "legacy-shared-path"
            and conv_legacy["legacy_shared_count"] == 1
            and conv_legacy["run_dir_computable_count"] == 0,
            conv_legacy["computable"])

        rec_legacy = bake_reconcile(
            rows=[{"ts": fresh, "batch_id": "B-CITY3D-01", "ok": True,
                   "outdir": "media/pool-ignite/x",
                   "runner": {"kind": "bake_spike",
                              "lightmap_quality": {"qc_verdict":
                                                   "LIGHTMAP-QC-PASS"},
                              "artifacts": [{"path": "a.exr", "sha256": "aaa"}]}}],
            reader=_fake(False), exists=lambda rel: rel in disk,
            hasher=lambda rel: disk[rel])
        chk("S76 M181: bake_reconcile tags the declared path too, so the "
            "reconcile face cannot read legacy as same-run",
            rec_legacy["declared_count"] == 1
            and rec_legacy["declared"][0].get("product_scope")
            == "legacy-shared-path"
            and rec_legacy["legacy_shared_count"] == 1, rec_legacy["declared"])
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
    p_c = sub.add_parser("bake-reconcile")
    p_c.add_argument("--json-out")
    p_c.add_argument("--txt-out")
    p_u = sub.add_parser("unknown-converge")
    p_u.add_argument("--json-out", default=UNKNOWN_CONVERGE_SIDECAR)
    p_u.add_argument("--txt-out")
    p_u.add_argument("--max-age-days", type=float,
                     default=KNOWN_BAD_AGE_DAYS)
    args = ap.parse_args(argv)
    if args.cmd == "tick":
        return tick()
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "status":
        return status(as_json=args.json, out=args.out)
    if args.cmd == "bake-reconcile":
        before = _file_sha256(RECEIPTS)
        rec = bake_reconcile()
        after = _file_sha256(RECEIPTS)
        rec["receipts_sha256_before"] = before
        rec["receipts_sha256_after"] = after
        rec["receipts_unchanged"] = before == after
        doc = json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True)
        if args.json_out:
            os.makedirs(os.path.dirname(os.path.abspath(args.json_out)),
                        exist_ok=True)
            with open(args.json_out, "w", encoding="utf-8", newline="\n") as f:
                f.write(doc + "\n")
        if args.txt_out:
            os.makedirs(os.path.dirname(os.path.abspath(args.txt_out)),
                        exist_ok=True)
            with open(args.txt_out, "w", encoding="utf-8", newline="\n") as f:
                f.write("bake_reconcile %s\n" % rec["ts"])
                f.write("uncertified=%s declared=%s certifiable=%s not_certifiable=%s\n"
                        % (rec["unread"], rec["declared_count"],
                           rec["certifiable_count"],
                           rec["not_certifiable_count"]))
                for k, v in sorted(rec["reasons"].items()):
                    f.write("reason %s=%s\n" % (k, v))
                for e in rec["declared"]:
                    f.write("DECLARED %s %s qc=%s min_lit=%s%% disk=%s\n"
                            % (e["ts"], e["batch_id"], e.get("qc_verdict"),
                               e.get("min_lit_pct"),
                               ",".join(e["disk_states"]) or "-"))
                for e in rec["certifiable"]:
                    f.write("CERTIFIABLE %s %s qc=%s min_lit=%s%%\n"
                            % (e["ts"], e["batch_id"], e.get("qc_verdict"),
                               e.get("min_lit_pct")))
                    for it in e["files"]:
                        f.write("  %s sha=%s verdict=%s\n"
                                % (it["path"], it.get("on_disk_sha256"),
                                   it.get("verdict")))
                for e in rec["not_certifiable"]:
                    f.write("NOT-CERTIFIABLE %s %s reason=%s\n"
                            % (e["ts"], e["batch_id"], e.get("reason")))
                    for it in e["files"]:
                        f.write("  %s state=%s declared=%s on_disk=%s\n"
                                % (it["path"], it["state"],
                                   (it.get("declared_sha256") or "")[:16],
                                   (it.get("on_disk_sha256") or "")[:16]))
                f.write("receipts_sha256 before=%s after=%s unchanged=%s\n"
                        % (before, after, rec["receipts_unchanged"]))
        return render_bake_reconcile(rec)
    if args.cmd == "unknown-converge":
        before = _file_sha256(RECEIPTS)
        rec = converge_unknown(max_age_days=args.max_age_days)
        after = _file_sha256(RECEIPTS)
        rec["receipts_sha256_before"] = before
        rec["receipts_sha256_after"] = after
        rec["receipts_unchanged"] = before == after
        doc = json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True)
        if args.json_out:
            os.makedirs(os.path.dirname(os.path.abspath(args.json_out)),
                        exist_ok=True)
            with open(args.json_out, "w", encoding="utf-8", newline="\n") as f:
                f.write(doc + "\n")
        if args.txt_out:
            os.makedirs(os.path.dirname(os.path.abspath(args.txt_out)),
                        exist_ok=True)
            with open(args.txt_out, "w", encoding="utf-8", newline="\n") as f:
                f.write("unknown_converge %s\n" % rec["ts"])
                f.write("scanned=%s unknown=%s computable=%s(pass=%s fail=%s) "
                        "unprovable=%s known_bad=%s policy=%sd\n"
                        % (rec["scanned"], rec["unknown"],
                           rec["computable_count"],
                           rec["computable_pass_count"],
                           rec["computable_fail_count"],
                           rec["unprovable_count"],
                           rec["known_bad_count"], rec["age_policy_days"]))
                for k, v in sorted(rec["reasons"].items()):
                    f.write("reason %s=%s\n" % (k, v))
                f.write("scope legacy_shared=%s run_dir=%s\n"
                        % (rec["legacy_shared_count"],
                           rec["run_dir_computable_count"]))
                for e in rec["computable"]:
                    f.write("COMPUTABLE %s %s kind=%s qc=%s scope=%s "
                            "reason=%s\n"
                            % (e["ts"], e["batch_id"], e["kind"],
                               e.get("qc_verdict"),
                               e.get("product_scope", "?"), e["reason"]))
                for e in rec["unprovable"]:
                    f.write("UNPROVABLE %s %s kind=%s age_days=%s reason=%s%s\n"
                            % (e["ts"], e["batch_id"], e["kind"],
                               e["age_days"], e["reason"],
                               " KNOWN-BAD" if e.get("known_bad") else ""))
                f.write("receipts_sha256 before=%s after=%s unchanged=%s\n"
                        % (before, after, rec["receipts_unchanged"]))
        print("scanned=%s unknown=%s computable=%s(pass=%s fail=%s) "
              "unprovable=%s known_bad=%s policy=%sd receipts_unchanged=%s"
              % (rec["scanned"], rec["unknown"], rec["computable_count"],
                 rec["computable_pass_count"], rec["computable_fail_count"],
                 rec["unprovable_count"], rec["known_bad_count"],
                 rec["age_policy_days"], rec["receipts_unchanged"]))
        print("scope legacy_shared=%s run_dir=%s(M181: not the same evidence)"
              % (rec["legacy_shared_count"],
                 rec["run_dir_computable_count"]))
        for k, v in sorted(rec["reasons"].items()):
            print("reason %s=%s" % (k, v))
        for e in rec["computable"]:
            print("COMPUTABLE %s %s kind=%s qc=%s scope=%s"
                  % (e["ts"], e["batch_id"], e["kind"], e.get("qc_verdict"),
                     e.get("product_scope", "?")))
        for e in rec["unprovable"]:
            print("UNPROVABLE %s %s age_days=%s reason=%s%s"
                  % (e["ts"], e["batch_id"], e["age_days"], e["reason"],
                     " KNOWN-BAD" if e.get("known_bad") else ""))
        return 0
    ok, receipt = ignite(args.batch_id, force=args.force)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())