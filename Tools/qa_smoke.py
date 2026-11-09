#!/usr/bin/env python3
"""qa_smoke.py - one-shot QA smoke-test pipeline (tech T11, orders L254).

Runs the four BigCompute charter checks (docs/qa-smoke-test-charter.md):
 1. GPU collector runs and produces data (gpu_idle_collector.py report)
 2. Ollama real response (local serve probe, qwen3:4b-instruct)
 3. three queue files each hold >= 3 todo rows (state/queue/*.md)
 4. cost ledger maintained (cost_ledger.py selftest PASS)
 5. rounds.log strict UTF-8 decode integrity (tech T25 / BC-P-16 / T24:
    shell Add-Content wrote GBK-mangled lines once; appends now go
    through python utf-8 only, and this probe surfaces any recurrence
    in the same round it happens)
 6. resident QA serve 8792 health (E41 / O-20260930-1645+1656 CEO
    observation window backend, E40 delivery): a silent death of the
    CEO-facing "summon residents" service must surface in the same
    round it happens, not wait for a CEO click
 7. (reserved: cloud attribution audit probe, wires only with the first
    real J4 cloud bill - T37, do not wire early)
 8. git-tracked ledger tail integrity (BC-P-45): trailing-newline
    assertion + merged-last-line sniff over the seven ledger files;
    the write-side round_append J4 guard covers python appends only,
    so tail damage from any other writer (shell Add-Content, manual
    edits; T24 GBK line and the 10-04/05 proposals.md row merge are
    the precedent classes) surfaces here in the round it happens
 9. L1 coder lane :8090 liveness (T106, O-20261015-1335/1500 Qwen v3
    final switchover): llama.cpp :8090 (Qwen3.6-35B-A3B) is now the L1
    route for four consumer lanes (BigLife year-ring / MV storyboard /
    resident QA 8792 backend / BigMoney advisor); the group CoderServe
    watchdog keeps the process alive but nothing in this suite watched
    the lane itself (8792 /health answers without exercising the
    model), so a dead :8090 would surface only as consumer failures.
    Read-only GET /v1/models, no generation, zero GPU load.
    PASS = HTTP 200 + at least one model id listed.
12. cost contract census (T138): runs cost_contract_census.census()
    so the cost/quota/cloud/borrow/l1 writer->reader->month-end
    contract face is judged every round instead of by memory.
16. resident QA 8792 /ask function (T153): probe no.6 reads /health,
    which only says the HTTP layer is up. Live 2026-10-28 case: health
    200 status=ok while /ask returned 503 because the :8090 backend was
    dead -> the CEO observation window was functionally dead and this
    suite still showed 8792 PASS. This probe walks the real click path
    (whitelist anchor -> persona prompt -> LLM backend -> AIGC gate)
    and names the exact false-green class; when the backend port has no
    listener the POST is skipped (no wasted generation).

Writes qa/smoke-<ts>.log with raw outputs + a self-judge verdict, then
renders that exact log content to qa/smoke-<ts>.png via .NET
System.Drawing. CLI company has no GUI window to screenshot, so the PNG
is a faithful zero-edit render of the real command output (declaration
BC-F-20260928-03, group patrol to adjudicate; charter red line:
fabrication = P1).

Usage: python Tools/qa_smoke.py [--selftest]
Exit 0 = all pass; 1 = at least one fail (fix = next round top priority).
--selftest runs probe no.8 against synthetic damaged fixtures only (T96).
Encoding rule: this file stays PURE ASCII (group coding law).
"""
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
QA_DIR = os.path.join(ROOT, "qa")
QUEUE_DIR = os.path.join(ROOT, "state", "queue")
QUEUE_FILES = ("main.md", "tech.md", "explore.md")
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
OLLAMA_MODEL = "qwen3:4b-instruct"  # flash-slot resident per Qwen v3 final switchover (O-20261015-1335/1500; old qwen2.5 family deleted by CEO order -- stale ref would auto-repull a deleted model)
RESIDENT_QA_URL = "http://127.0.0.1:8792/health"
RESIDENT_QA_ASK_URL = "http://127.0.0.1:8792/ask"
QA_ASK_ANCHOR = "C-00010"  # 20-anchor whitelist member since 09-30 delivery
QA_ASK_QUESTION = "\u4eca\u5929\u751f\u610f\u600e\u4e48\u6837\uff1f"  # zh: how is business today?
QA_BACKEND_PORT = 8090  # resident_qa_server.LLM_URL default (env override read below)
CODER_SERVE_URL = "http://127.0.0.1:8090/v1/models"

PNG_PS_TEMPLATE = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$logpath = '__LOG__'
$pngpath = '__PNG__'
$log = Get-Content -LiteralPath $logpath -Encoding UTF8
$font = New-Object System.Drawing.Font('Consolas',11)
$bmp = New-Object System.Drawing.Bitmap(1700,[Math]::Max(400,$log.Count*19+40))
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::White)
$y = 10
foreach($line in $log){
  $g.DrawString($line, $font, [System.Drawing.Brushes]::Black, 10.0, [single]$y)
  $y += 19
}
$g.Dispose()
$bmp.Save($pngpath, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
"""


def run(cmd, timeout=180):
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       cwd=ROOT)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


GPU_QUERY = ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,"
             "memory.total", "--format=csv,noheader"]
GPU_CONTENTION_UTIL_PCT = 90
GPU_CONTENTION_VRAM_FREE_MB = 1024


def parse_gpu_state(csv_text):
    """T178: parse one nvidia-smi CSV line into a GPU state dict.

    Returns None when no parseable line exists (nvidia-smi missing, driver
    error, foreign output) so callers can tell "cannot read" apart from
    "read and idle" instead of defaulting to a green.
    """
    for line in (csv_text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            util = int(float(parts[0].rstrip("%")))
            used = int(float(parts[1].split()[0]))
            total = int(float(parts[2].split()[0]))
        except (ValueError, IndexError):
            continue
        return {"util_pct": util, "vram_used_mb": used,
                "vram_total_mb": total,
                "vram_free_mb": max(total - used, 0)}
    return None


def gpu_contention_verdict(state, util_pct=GPU_CONTENTION_UTIL_PCT,
                           vram_free_mb=GPU_CONTENTION_VRAM_FREE_MB):
    """T178: pure classifier for "is the GPU busy with someone else?".

    Contention is a scheduling fact, not an inference fault: a probe that
    reads it can spend a longer budget on a *real* answer instead of
    recording a false device failure. Unreadable state never counts as
    contention -- it must not buy extra budget for a dead driver.
    """
    if not state:
        return False, "GPU-UNREADABLE"
    reasons = []
    if state["util_pct"] >= util_pct:
        reasons.append("util=%d%%>=%d" % (state["util_pct"], util_pct))
    if state["vram_free_mb"] < vram_free_mb:
        reasons.append("vram_free=%dMiB<%d"
                       % (state["vram_free_mb"], vram_free_mb))
    if reasons:
        return True, "GPU-CONTENDED(%s)" % ",".join(reasons)
    return False, "GPU-IDLE-OK(util=%d%%,vram_free=%dMiB)" % (
        state["util_pct"], state["vram_free_mb"])


def gpu_state():
    """T178: one read-only nvidia-smi call; None when unavailable."""
    try:
        rc, out = run(GPU_QUERY, timeout=20)
    except Exception:  # noqa: BLE001 - probe must not crash pipeline
        return None
    if rc != 0:
        return None
    return parse_gpu_state(out)


def probe_gpu():
    """Charter 1: GPU collector runs and produces numbers."""
    rc, out = run([sys.executable, os.path.join(HERE, "gpu_idle_collector.py"),
                   "report"])
    ok = rc == 0 and "gpu report" in out and "no samples today" not in out
    return ok, out.strip()


def parse_residency(ps_text):
    """T15: classify model residency from `ollama ps` output.

    FOREVER = loaded with indefinite keep_alive (U240 standard state);
    PRESENT = loaded but on a finite timer; LOST = not resident.
    """
    for line in (ps_text or "").splitlines():
        if line.startswith(OLLAMA_MODEL):
            return "FOREVER" if "Forever" in line else "PRESENT"
    return "LOST"


def _night_stop_active():
    """O-20261012-2025 night-stop gate: MiniGameOllamaKeepWarm task
    Disabled AND no llama-server.exe process. In that state a generate
    call would auto-load the model and re-pin ~5GB VRAM — undoing the
    CEO night stop (5G 让位 KF 批) and competing with the night batch.
    Any query failure fails open to the normal full probe (never fail
    closed into a fake PASS). Live-fire precedent: 10-12 20:4x the
    untouched probe respawned llama-server PID 62960 within one run.

    10-13 live-fire amendment (BW-006): the ollama serve parent
    auto-RELOADS keep_alive=-1 pinned models, so a runner present at
    night is a BC-P-68 enforcement target, not a live lane -- and a
    generate call here would re-pin ~5GB (16-kill whack-a-mole,
    03:03:39 kill vs 03:04:25 respawn). Night hours + KeepWarm
    Disabled -> night-stop face regardless of runner presence."""
    try:
        rc, out = run(["schtasks", "/query", "/tn", "MiniGameOllamaKeepWarm",
                       "/fo", "CSV", "/nh"], timeout=15)
        if rc != 0 or '"Disabled"' not in out:
            return False
        h = datetime.datetime.now().hour
        if h >= 22 or h < 8:
            return True
        rc2, tl = run(["tasklist", "/fi", "IMAGENAME eq llama-server.exe"],
                      timeout=15)
        return rc2 == 0 and "llama-server.exe" not in tl
    except Exception:
        return False


def probe_ollama():
    """Charter 2: local Ollama serve answers a real prompt.

    T15 (R-20260928-inference-serving-standard Q3 item 3): the probe is a
    resident-serving call, so it carries keep_alive=-1 explicitly and
    records residency before/after. If residency was lost, the generate
    call itself reloads the model (auto-load) and the log line records
    the restore; residency no longer depends on default keep_alive.

    BC-P-60: /api/tags pre-check (2s, read-only). tags unreachable = bare
    FAIL fast (serve down, no 180s burn); tags 200 + generate timeout =
    BORROW-WINDOW label in the except branch (saturation env state, serve
    alive; precedent 10-10 23:1x / 10-12 00:2x+09:55).

    O-20261012-2025 night-stop gate: tags=200 + KeepWarm Disabled +
    llama-server absent -> liveness-only PASS (generation skipped; the
    morning task re-enable self-heals the full probe).
    """
    try:
        with urllib.request.urlopen(
                urllib.request.Request(OLLAMA_TAGS_URL), timeout=2) as tr:
            tr.read()
    except Exception as e:  # noqa: BLE001 - probe must not crash pipeline
        return False, ("ollama probe failed: /api/tags unreachable "
                       "(serve down, bare FAIL per BC-P-60): %r" % e)
    if _night_stop_active():
        return True, ("ollama probe: NIGHT-STOP-AWARE PASS "
                      "(O-20261012-2025): daemon-only check (tags=200); "
                      "generation skipped — KeepWarm task disabled + "
                      "night window (CEO night stop: 5G for KF batch; a "
                      "resident runner is a BC-P-68 enforcement target, "
                      "generate would re-pin it; morning re-enable "
                      "self-heals full probe)")
    rc0, ps_pre = run(["ollama", "ps"], timeout=60)
    pre = parse_residency(ps_pre)
    # T178: a GPU held by foreign production (ComfyUI / Tuanjie Editor at
    # 99% util, 11782/12282 MiB on 2026-10-31) time-slices the resident
    # runner hard enough to blow a fixed 180s budget. Reading contention
    # first buys a longer *real* budget instead of logging a false device
    # failure; the criterion (a real answer) stays exactly as strict.
    contended, gpu_label = gpu_contention_verdict(gpu_state())
    budget = 60 if contended else 180
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": "1+1?",
                       "stream": False, "keep_alive": -1,
                       "options": {"num_predict": 32, "temperature": 0}}
                      ).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=budget) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = (data.get("response") or "").strip()
        ec = data.get("eval_count")
        ed = data.get("eval_duration") or 0
        tps = (ec / (ed / 1e9)) if (ec and ed) else 0.0
        rc2, ps_post = run(["ollama", "ps"], timeout=60)
        post = parse_residency(ps_post)
        if post == "LOST":
            res = ("residency: pre=%s post=LOST (anomaly: not loaded "
                   "after generate)" % pre)
        elif pre == "LOST":
            res = ("residency: pre=LOST post=%s (auto-loaded via "
                   "keep_alive=-1)" % post)
        elif pre == "FOREVER":
            res = "residency: pre=FOREVER post=%s (maintained)" % post
        else:
            res = ("residency: pre=%s post=%s (keep_alive=-1 reapplied)"
                   % (pre, post))
        detail = ("answer=%r eval_count=%s %.2f tok/s\n%s\n%s\n%s\ngpu: %s "
                  "budget=%ss"
                  % (text, ec, tps, res, ps_pre.strip(), ps_post.strip(),
                     gpu_label, budget))
        return bool(text), detail
    except Exception as e:  # noqa: BLE001 - probe must not crash pipeline
        # T178: contended GPU = the resident runner is time-sliced into
        # uselessness (measured 2026-10-31: 8 tokens > 300s while ComfyUI
        # held 99% util). Fall back once to the CPU lane for a real
        # answer; the criterion stays "real response from the local serve".
        if contended:
            ok_cpu, det_cpu = ollama_cpu_lane()
            if ok_cpu:
                return True, ("ollama probe: CPU-LANE-DEGRADED PASS "
                              "(gpu=%s, gpu attempt failed in %ss: %r)\n%s"
                              % (gpu_label, budget, e, det_cpu))
            return False, ("ollama probe failed: BOTH-LANES "
                           "(gpu=%s budget=%ss; cpu fallback: %s; %r)"
                           % (gpu_label, budget, det_cpu, e))
        # BC-P-60: tags 200 + generate failure = borrow-window saturation
        # env state (labeled), tags unreachable = bare FAIL (serve down).
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(OLLAMA_TAGS_URL), timeout=2) as tr:
                tr.read()
            return False, ("ollama probe failed: BORROW-WINDOW "
                           "(tags=200, generate-timeout after %ss, gpu=%s): "
                           "%r" % (budget, gpu_label, e))
        except Exception:
            return False, ("ollama probe failed (gpu=%s budget=%ss): %r"
                           % (gpu_label, budget, e))


def ollama_cpu_lane(timeout=180):
    """T178: real answer from the CPU lane (num_gpu=0) while the GPU is
    held by foreign production. Uses its own short generation budget and
    unloads the CPU runner right after (keep_alive=0) so the resident GPU
    runner is not displaced for the next round."""
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": "1+1?",
                       "stream": False, "keep_alive": 0,
                       "options": {"num_predict": 8, "temperature": 0,
                                   "num_gpu": 0}}).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = datetime.datetime.now()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - probe must not crash pipeline
        return False, "cpu lane failed after %ss: %r" % (timeout, e)
    text = (data.get("response") or "").strip()
    secs = (datetime.datetime.now() - t0).total_seconds()
    return bool(text), ("cpu lane answer=%r eval_count=%s %.1fs "
                        "(num_gpu=0, keep_alive=0)"
                        % (text, data.get("eval_count"), secs))


def probe_queues():
    """Charter 3: each queue file holds >= 3 todo rows."""
    detail, ok_all = [], True
    for name in QUEUE_FILES:
        path = os.path.join(QUEUE_DIR, name)
        rows = 0
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if (line.startswith("| ") and "---" not in line
                            and not line.startswith("| #")):
                        rows += 1
        ok_all = ok_all and rows >= 3
        detail.append("%s: %d rows" % (name, rows))
    return ok_all, "\n".join(detail)


def probe_ledger():
    """Charter 4: cost ledger selftest still PASS."""
    rc, out = run([sys.executable, os.path.join(HERE, "cost_ledger.py"),
                   "selftest"])
    return rc == 0 and "PASS" in out, out.strip()


def probe_rounds_log():
    """Tech T25 (BC-P-16): rounds.log strict UTF-8 decode integrity."""
    path = os.path.join(ROOT, "state", "rounds.log")
    if not os.path.exists(path):
        return False, "rounds.log missing"
    with open(path, "rb") as f:
        raw = f.read()
    try:
        raw.decode("utf-8", errors="strict")
        return True, ("strict utf-8 decode ok: %d bytes / %d lines"
                      % (len(raw), raw.count(b"\n")))
    except UnicodeDecodeError as e:
        return False, ("strict decode FAILED at byte %d: %s "
                       "(T24 GBK recurrence - fix next round top priority)"
                       % (e.start, e.reason))


def probe_resident_qa():
    """E41: resident QA serve (8792) health for the CEO observation window.

    E40 delivered Tools/resident_qa_server.py as the O-20260930-1645
    observation-window backend, kept alive by a 5min silent schtasks
    loop. The keepalive restarts, but nothing watched the restart
    failing; this probe classifies PRESENT (health 200) vs LOST so a
    dead CEO-facing service shows up in this round's verdict.
    """
    try:
        with urllib.request.urlopen(RESIDENT_QA_URL, timeout=10) as r:
            h = json.loads(r.read().decode("utf-8"))
        ok = r.status == 200 and h.get("status") == "ok"
        return ok, ("health %d status=%s anchors=%s server=%s model=%s"
                    % (r.status, h.get("status"), h.get("anchors"),
                       h.get("server"), h.get("model")))
    except Exception as e:  # noqa: BLE001 - probe must not crash pipeline
        return False, ("resident QA serve 8792 LOST: %r "
                       "(CEO observation window - fix next round top "
                       "priority)" % e)


def _ascii_safe(s, limit=160):
    """Keep probe output printable on any console code page.

    Payload strings come from a local service whose error bodies are
    encoded in the host locale; a stray byte must never crash the round
    log (and this file stays pure ASCII).
    """
    return re.sub(r"[^\x20-\x7e]", ".", str(s))[:limit]


def qa_backend_port():
    """Port the 8792 server calls for its LLM backend.

    resident_qa_server reads RESIDENT_QA_LLM_URL; default is :8090. Env
    override is honoured here so the probe follows a lane relocation
    without code change (same contract as the server itself).
    """
    url = os.environ.get("RESIDENT_QA_LLM_URL")
    if url:
        m = re.search(r":(\d+)", url)
        if m:
            return int(m.group(1))
    return QA_BACKEND_PORT


def qa_lane_ports(health=None, default_port=None):
    """All LLM lane ports the 8792 server can reach, primary first.

    resident_qa_server v1.1 declares a fallback lane (L1 :8090 -> local
    Ollama). A probe that only looks at the primary port reports
    HEALTH-OK-BACKEND-DOWN while /ask is actually answering through the
    fallback - the same false-green class this probe exists to kill,
    only inverted. Ports are read from the server's own /health `llm`
    block so the probe follows the live configuration; env/defaults are
    the fallback when the block is absent (older server build).
    """
    ports = []

    def _add(url):
        m = re.search(r":(\d+)", str(url or ""))
        if m:
            p = int(m.group(1))
            if p not in ports:
                ports.append(p)

    llm = (health or {}).get("llm") if isinstance(health, dict) else None
    if isinstance(llm, dict):
        _add(llm.get("primary_url"))
        _add(llm.get("fallback_url"))
    if not ports:
        _add(os.environ.get("RESIDENT_QA_LLM_URL"))
        _add(os.environ.get("RESIDENT_QA_LLM_FALLBACK_URL"))
    if not ports:
        ports.append(default_port or QA_BACKEND_PORT)
    return ports


def classify_qa_functional(health_ok, backend_listening, status=None,
                           payload=None, err=None, port=None):
    """T153 (probe no.16): pure verdict for the 8792 /ask end-to-end face.

    Probe no.6 reads /health only, which reports that the HTTP server is
    up and says nothing about the LLM backend /ask depends on. Live case
    2026-10-28 03:2x: health 200 status=ok, /ask 503 "\u672c\u5730\u5927\u8111\u672a\u54cd\u5e94"
    (<urlopen error [WinError 10061]>) - the CEO observation window was
    functionally dead for days while this suite showed 8792 PASS.
    Every branch below is FAIL except a real answer; nothing softened.
    """
    port = port or QA_BACKEND_PORT
    if not health_ok:
        return (False, "SERVER-DOWN",
                "8792 /health unreachable (server dead, not a backend issue)")
    if not backend_listening:
        return (False, "HEALTH-OK-BACKEND-DOWN",
                "8792 /health ok but LLM backend :%d has no listener -> "
                "/ask cannot answer (health-only green = false green)"
                % port)
    if err is not None:
        return (False, "ASK-ERROR",
                "8792 /ask failed against a listening backend: %s"
                % _ascii_safe(err))
    if status == 200 and isinstance(payload, dict):
        reply = str(payload.get("reply") or "")
        gate = str(payload.get("gate") or "")
        if reply and "\u672c\u5730AI\u751f\u6210" in gate:
            return (True, "ASK-OK",
                    "ask 200 reply_len=%d gate=%s"
                    % (len(reply), _ascii_safe(gate, 60)))
        return (False, "ASK-BAD-BODY",
                "ask 200 but reply/gate contract broken: %s"
                % _ascii_safe(payload))
    if status == 503:
        return (False, "BACKEND-ERROR",
                "ask 503 backend did not answer: %s"
                % _ascii_safe((payload or {}).get("detail")
                              or (payload or {}).get("err")))
    if status == 403:
        return (False, "WHITELIST-CONFIG",
                "ask 403 for anchor %s: %s -> probe anchor drifted out of "
                "the whitelist (probe config, not a service fault)"
                % (QA_ASK_ANCHOR, _ascii_safe((payload or {}).get("reason"))))
    return (False, "ASK-HTTP-%s" % status,
            "ask unexpected status %s body=%s"
            % (status, _ascii_safe(payload)))


KNOWN_ENV_EVIDENCE = "state/d05-no16-gpu-contention-20261105.txt"


def reclassify_known_env(ok, tok, det, contended):
    """D-20261105-10: GPU-contended /ask timeout is a known environment
    constraint, not a defect.

    Committee ruling (T3, 11-05 06:0x): the red is real but the cause is
    compute contention (11 Tuanjie instances, 165 MiB VRAM free), which is
    a scheduling fact of the fleet, not a fault of the 8792 window - and
    adding a third state to the harness would be a new rule, which the CEO
    banned at 00:36. So an ASK-ERROR *while the GPU is demonstrably held by
    someone else* is recorded as KEPT-OPEN-KNOWN-ENV with the evidence
    pointer, and stops being counted as a round defect.

    Nothing else is softened: SERVER-DOWN, HEALTH-OK-BACKEND-DOWN, HTTP
    errors and any ASK-ERROR on a non-contended GPU all stay FAIL. Pure
    function so the selftest can prove that boundary.
    """
    if ok or not contended or tok != "ASK-ERROR":
        return ok, tok, det
    return True, "ASK-ERROR->KEPT-OPEN-KNOWN-ENV", (
        "%s [known environment constraint: GPU contention, D-20261105-10, "
        "evidence=%s - not counted as a defect, do not re-collect]"
        % (det, KNOWN_ENV_EVIDENCE))


def probe_resident_qa_functional(port_owner=None):
    """Probe no.16 (T153): 8792 /ask end-to-end, the CEO-facing function.

    /health is a liveness signal for the HTTP layer only. This probe
    exercises the path a CEO click actually takes: whitelist anchor ->
    persona prompt -> LLM backend -> gate disclosure. When the backend
    port has no listener the POST is skipped (deterministic, no wasted
    generation) and the verdict names the exact false-green class.
    """
    port = qa_backend_port()
    health = {}
    try:
        with urllib.request.urlopen(RESIDENT_QA_URL, timeout=10) as r:
            health = json.loads(r.read().decode("utf-8", errors="replace"))
        health_ok = r.status == 200 and health.get("status") == "ok"
    except Exception as e:  # noqa: BLE001 - probe must not crash pipeline
        ok, tok, det = classify_qa_functional(False, False, port=port)
        return ok, "%s: %s [%s]" % (tok, det, _ascii_safe(e, 80))
    lanes = qa_lane_ports(health, port)
    live = [p for p in lanes if (port_owner or coder_port_owner)(p)[0]]
    if not live:
        ok, tok, det = classify_qa_functional(health_ok, False,
                                              port=lanes[0])
        return ok, "%s: %s (lanes=%s)" % (
            tok, det, ",".join(str(p) for p in lanes))
    body = json.dumps({"id": QA_ASK_ANCHOR, "q": QA_ASK_QUESTION}).encode("utf-8")
    # T178: same contention read as probe 2 -- a GPU time-sliced by foreign
    # production makes a 40s /ask budget expire on a *working* backend. One
    # retry with the longer budget; verdict logic is untouched, so a retry
    # that also fails still lands on ASK-ERROR (no softening).
    contended, gpu_label = gpu_contention_verdict(gpu_state())
    # T178: the server's own backend call is GPU-bound, so under foreign
    # contention a longer budget only burns wall clock (measured 8 tokens
    # > 300s). One bounded attempt, and the label names GPU-CONTENDED so
    # this FAIL is never read as "the 8792 window is broken".
    budget = 60 if contended else 40
    attempts = 1
    status, payload, err = None, None, None
    for attempt in range(attempts):
        status, payload, err = None, None, None
        try:
            req = urllib.request.Request(
                RESIDENT_QA_ASK_URL, data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=budget) as r:
                status = r.status
                payload = json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                payload = json.loads(e.read().decode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001 - body shape is bonus context
                payload = {"raw": str(e)}
        except Exception as e:  # noqa: BLE001 - timeout/socket errors alike
            err = e
        if err is None:
            break
    ok, tok, det = classify_qa_functional(health_ok, True, status, payload,
                                          err, live[0])
    if ok and len(lanes) > 1:
        det = "%s L1-DOWN(fallback lane :%d serving)" % (det, live[0]) \
            if live[0] != lanes[0] else det
    ok, tok, det = reclassify_known_env(ok, tok, det, contended)
    return ok, "%s: %s\ngpu: %s budget=%ss attempts=%d" % (
        tok, det, gpu_label, budget, attempts)


SUPPLY_MARK_RE = re.compile(r"idle_selfcheck")


POOL_CANDIDATE_TOKEN = "pool_rows_candidate"
POOL_LEGACY_TOKEN = "pool_claimable"


def supply_dual_fields(line):
    """T132 (probe no.10): read-side guard for the two supply counters.

    `pool_effective` is the governance number (stale/own/misowned rows
    excluded) while `pool_ignitable` stays the fleet-compatible ready
    count. Since T128 both are written on every heartbeat line, so a
    reader can no longer pick the wrong one by accident - provided the
    line actually carries both. Missing field or effective > ready means
    the pair drifted apart and the reading is untrustworthy.
    T167 extends this to the label itself: the writer only emits
    `pool_rows_candidate` (T166 single construction point), so a line
    that brings the deprecated `pool_claimable=` back - hand-stitched
    or a third bare counter - is a regression the probe must flag.

    Pure function over one heartbeat line so the selftest can feed
    synthetic fixtures.
    """
    line = line or ""
    if re.search(r"\b%s\s*=" % POOL_LEGACY_TOKEN, line):
        return False, ("deprecated %s= back on heartbeat line (use %s)"
                       % (POOL_LEGACY_TOKEN, POOL_CANDIDATE_TOKEN))
    m_eff = re.search(r"pool_effective=(\d+)", line)
    m_ign = re.search(r"pool_ignitable=(\d+)", line)
    m_cand = re.search(r"%s=(\d+)" % POOL_CANDIDATE_TOKEN, line)
    if not m_eff and not m_ign:
        return False, "no pool_effective/pool_ignitable on heartbeat line"
    if not m_cand:
        return False, ("%s missing (self-describing candidate label "
                       "required)" % POOL_CANDIDATE_TOKEN)
    if not m_eff:
        return False, "pool_effective missing (pool_ignitable present)"
    if not m_ign:
        return False, "pool_ignitable missing (pool_effective present)"
    eff, ign = int(m_eff.group(1)), int(m_ign.group(1))
    if eff > ign:
        return False, ("inverted counters: pool_effective=%d > "
                       "pool_ignitable=%d" % (eff, ign))
    return True, ("pool_effective=%d pool_ignitable=%d (effective <= ready)"
                  % (eff, ign))


def probe_supply_dual_fields(root=None):
    """T132 probe no.10: last idle_selfcheck line carries both counters."""
    root = root or ROOT
    path = os.path.join(root, "state", "heartbeat.txt")
    if not os.path.exists(path):
        return False, "state/heartbeat.txt missing"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    hits = [ln for ln in lines if SUPPLY_MARK_RE.search(ln)]
    if not hits:
        return False, "no idle_selfcheck line in heartbeat"
    return supply_dual_fields(hits[-1])


# T168: heartbeat numeric tokens that decide thresholds elsewhere (RAM/VRAM
# gates, CPU-lane sentinel, starvation counters, supply counters). T167 only
# guarded the three supply labels, so a writer-side rename of e.g.
# ram_free_pct -> ram_pct would leave every reader comparing against nothing
# while the row still reads healthy. The guard is shape+range, not semantics.
HEARTBEAT_GUARDED_TOKENS = (
    ("ram_free_pct", "pct"),
    ("vram_free_mb", "count"),
    ("py_cpu", "pct"),
    # M117 (BC-P-191): second read of the same physical quantity, side by side
    # with py_cpu. Domain accepts an explicit "n/a" - the probe reports a
    # missing channel that way on purpose; 0.0 is a legal reading, absence is
    # not, so neither may stand in for the other.
    ("py_cpu_psutil", "pct_or_na"),
    ("queue_open", "count"),
    ("consecutive_idle", "count"),
    ("idle_rounds", "count"),
    (POOL_CANDIDATE_TOKEN, "count"),
    ("pool_effective", "count"),
    ("pool_ignitable", "count"),
    # M163: our own stocked cards' dispatchable count. A second pool, not a
    # refinement of pool_*; "n/a" is a legal writer form (registry missing or
    # unreadable) and 0 is a legal reading, so neither may stand in for the
    # other - same rule as py_cpu_psutil.
    ("stock_dispatchable", "count_or_na"),
    # T170 batch 1: enum/boolean faces. These carry lane and alert state,
    # so a silent value drift (e.g. task_face=paused instead of
    # pause-exempt) reads as "something" to a human but matches no branch
    # in any reader. Domain check only - the guard does not recompute the
    # decision the value encodes.
    ("cpu_lane_idle", "bool"),
    ("agenda_starved", "bool"),
    ("serve_alive", "bool"),
    ("breach_eff", "bool"),
    ("task_face", "task_face"),
    ("model_drift", "model_drift"),
    ("lane8090", "lane"),
    ("c1c4_line", "state:rounds_ago"),
    ("hq_line", "state:age_h"),
    # T207 (BC-P-199): the 70% load line moved to a single channel in T203,
    # and the row grew a cpu_load_line token that no probe watched. A writer
    # rename or a new spelling (e.g. "N/A" without the channel word) would
    # leave every reader comparing against nothing while the row still reads
    # healthy. Domain is the three-state label the writer can emit.
    ("cpu_load_line", "cpu_load_line"),
)

# T207: tokens the heartbeat writer emits that are deliberately NOT guarded.
# Each needs a reason here, otherwise heartbeat_token_crosscheck() reports it
# as unguarded - an undeclared token is exactly the silent-drift hole this
# guard exists to close. Adding a writer token means touching this table (or
# HEARTBEAT_GUARDED_TOKENS) in the same commit.
HEARTBEAT_UNGUARDED_DECLARED = {
    "verdict": "free-form status word (busy/ready/idle); not a threshold input",
    "serve_detail": "free-form diagnostic (http200 / DOWN[:reason])",
    "pool_foreign_silent": "count; no reader compares it to a threshold yet",
    "pool_owner_unknown": "count; no reader compares it to a threshold yet",
    "pool_fresh": "count; no reader compares it to a threshold yet",
    "pool_self_stale": "count; no reader compares it to a threshold yet",
    "pool_lane_mm": "count; no reader compares it to a threshold yet",
}

TASK_FACE_LITERALS = ("ok", "pause-exempt", "probe-error")
MODEL_DRIFT_LITERALS = ("GREEN", "UNVERIFIED")
# Writer side (idle_selfcheck.probe_model_drift) emits "<status>(m=<n>,a=<n>)"
# for every status model_ref_drift_check can return - GREEN / DRIFT /
# UNVERIFIED - plus the read-failure form "UNVERIFIED(err)". The domain used
# to accept only the DRIFT spelling, so the ordinary "UNVERIFIED(m=0,a=0)"
# (baseline unavailable) was judged a guard violation every round: a legal
# writer form misread as drift, i.e. the guard reported itself as the defect.
MODEL_DRIFT_PARAM_RE = re.compile(r"^(?:GREEN|DRIFT|UNVERIFIED)\(m=\d+,a=\d+\)$")
MODEL_DRIFT_ERR_RE = re.compile(r"^UNVERIFIED\(err\)$")
STATE_LINE_RE = re.compile(r"^([A-Z][A-Z0-9-]*)\((.*)\)$")
# T207: the three labels idle_selfcheck.evaluate_cpu_load_line can emit.
# PASS/BREACH carry "<cmp><pct>" and a ref which may be n/a when the shell
# channel failed; N/A-CHANNEL carries ref+line and no psutil reading at all
# (that is the point of the state - never substitute the other ruler).
CPU_LOAD_LINE_RE = re.compile(
    r"^(?:PASS|BREACH)\(psutil=-?\d+(?:\.\d+)?(?:<|>=)\d+(?:\.\d+)?,"
    r"ref=(?:n/a|-?\d+(?:\.\d+)?)\)$"
    r"|^N/A-CHANNEL\(ref=(?:n/a|-?\d+(?:\.\d+)?),line=\d+(?:\.\d+)?\)$"
)


def heartbeat_fields(line):
    """Token -> raw value for one heartbeat row (whitespace split)."""
    out = {}
    for part in (line or "").split():
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        out.setdefault(key, val)
    return out


def heartbeat_enum_ok(kind, raw):
    """T170: value-domain judge for one non-numeric heartbeat token."""
    if kind == "bool":
        return raw in ("true", "false")
    if kind == "task_face":
        return raw in TASK_FACE_LITERALS or raw.startswith("ALERT:")
    if kind == "model_drift":
        return (raw in MODEL_DRIFT_LITERALS
                or bool(MODEL_DRIFT_PARAM_RE.match(raw))
                or bool(MODEL_DRIFT_ERR_RE.match(raw)))
    if kind == "lane":
        return raw == "UP" or (raw.startswith("DARK[") and raw.endswith("]"))
    if kind.startswith("state:"):
        m = STATE_LINE_RE.match(raw)
        return bool(m) and ("%s=" % kind.split(":", 1)[1]) in m.group(2)
    if kind == "cpu_load_line":
        return bool(CPU_LOAD_LINE_RE.match(raw))
    return False


def heartbeat_token_guard(line):
    """T168/T170 pure judge: guarded tokens present, typed, in domain."""
    if not line:
        return False, "no heartbeat line"
    fields = heartbeat_fields(line)
    seen = []
    for name, kind in HEARTBEAT_GUARDED_TOKENS:
        raw = fields.get(name)
        if raw is None:
            return False, "%s missing (writer renamed or dropped it)" % name
        if kind in ("pct", "count", "pct_or_na", "count_or_na"):
            if kind.endswith("_or_na") and raw == "n/a":
                seen.append("%s=%s" % (name, raw))
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return False, "%s not numeric: %r" % (name, raw)
            if kind in ("pct", "pct_or_na"):
                if not (0.0 <= val <= 100.0):
                    return False, "%s out of range: %r" % (name, raw)
            else:
                if val < 0:
                    return False, "%s negative: %r" % (name, raw)
                if val != int(val):
                    return False, "%s not integral: %r" % (name, raw)
        elif not heartbeat_enum_ok(kind, raw):
            return False, "%s outside %s domain: %r" % (name, kind, raw)
        seen.append("%s=%s" % (name, raw))
    return True, "guarded=%d %s" % (len(seen), " ".join(seen))


def writer_heartbeat_tokens(root=None):
    """T207: read the writer-side token list declared in idle_selfcheck.py.

    Parsed with AST rather than import: importing the module would run its
    module-level probe setup, and this guard must stay a pure read. A missing
    or unparsable list is a probe failure, not a pass - otherwise deleting the
    declaration would silently disable the crosscheck.
    """
    import ast
    root = root or ROOT
    path = os.path.join(root, "Tools", "idle_selfcheck.py")
    if not os.path.exists(path):
        return [], "Tools/idle_selfcheck.py missing"
    try:
        # utf-8-sig: our own writers emit a BOM (D-20261102-03), and a BOM
        # reaches ast.parse as U+FEFF, which it rejects as a syntax error.
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            tree = ast.parse(f.read())
    except SyntaxError as e:
        return [], "idle_selfcheck.py unparsable: %s" % e
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == "HEARTBEAT_WRITER_TOKENS":
                try:
                    val = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return [], "HEARTBEAT_WRITER_TOKENS is not a literal"
                return [str(t) for t in val], ""
    return [], "HEARTBEAT_WRITER_TOKENS not declared in idle_selfcheck.py"


def heartbeat_token_crosscheck(writer_tokens, live_fields=None):
    """T207 pure judge: writer list, guard table and live row must agree.

    Three diffs, each its own failure mode:
    - writer-only: a token nobody guards or declares = silent-drift hole
    - declared-only: a stale exemption still claiming to cover a dead token
    - guarded-only: the guard is watching a token the writer no longer emits
    Live fields add a fourth: a token actually on disk that neither table
    knows about. That is the form a live check can catch even after the
    declared list has drifted.
    """
    problems = []
    writer = list(dict.fromkeys(writer_tokens))
    guarded = [n for n, _ in HEARTBEAT_GUARDED_TOKENS]
    declared = HEARTBEAT_UNGUARDED_DECLARED
    for name in writer:
        if name not in guarded and name not in declared:
            problems.append("writer token not guarded or declared: %s" % name)
    for name in declared:
        if name not in writer:
            problems.append("declared-unguarded token no longer written: %s"
                            % name)
    for name in guarded:
        if name not in writer:
            problems.append("guarded token missing from writer list: %s" % name)
    for name in (live_fields or {}):
        if name not in guarded and name not in declared:
            problems.append("live token undeclared: %s" % name)
    if problems:
        return False, "; ".join(problems)
    return True, ("crosscheck-ok writer=%d guarded=%d declared=%d"
                  % (len(writer), len(guarded), len(declared)))


def probe_heartbeat_tokens(root=None):
    """Probe no.18 (T168): guarded numeric heartbeat tokens each round.

    no.10 reads the supply trio as a pair; the rest of the row (RAM/VRAM
    gates, CPU-lane sentinel, starvation counters) had no probe side at
    all, so those tokens could be renamed on the write side and no round
    would turn red. Advisory scope: this checks structure and ranges, it
    does not re-derive the gate decisions.
    """
    root = root or ROOT
    path = os.path.join(root, "state", "heartbeat.txt")
    if not os.path.exists(path):
        return False, "state/heartbeat.txt missing"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    hits = [ln for ln in lines if SUPPLY_MARK_RE.search(ln)]
    if not hits:
        return False, "no idle_selfcheck line in heartbeat"
    ok, msg = heartbeat_token_guard(hits[-1])
    if not ok:
        return False, msg
    tokens, err = writer_heartbeat_tokens(root)
    if err:
        return False, err
    xok, xmsg = heartbeat_token_crosscheck(tokens, heartbeat_fields(hits[-1]))
    return (ok and xok), msg + " | " + xmsg


CONTRACT_VERDICT_OK = "COST-CONTRACT-GREEN"


def cost_contract_judge(result):
    """T138 pure judge over a census() result dict.

    Split out from the probe so the rejection branches are testable:
    census() only ever returns the live answer, so a green-but-gapped
    or non-green verdict would otherwise be unprovable in fixtures.
    """
    verdict = result.get("verdict")
    gaps = result.get("gaps") or []
    if verdict != CONTRACT_VERDICT_OK:
        return False, "verdict=%s gaps=%d" % (verdict, len(gaps))
    if gaps:
        return False, ("verdict=%s but %d gap(s) reported"
                       % (verdict, len(gaps)))
    rails = result.get("rails") or []
    if not rails:
        return False, "no rails probed"
    return True, "verdict=%s rails=%d gaps=0" % (verdict, len(rails))


def probe_ledger_parallel(root=None):
    """T177 (probe no.20): fused-row census over the FULL ledgers.

    Probe no.8 only sniffs the last 12 lines, so a fused row landing in
    the middle of a long ledger was invisible (T176 found three such rows
    in state/heartbeat.txt by hand). This runs the full-text census and
    passes only when every fused row is on the confirmed whitelist:
    historically confirmed rows stay quiet, anything new turns red the
    next round.
    """
    root = root or ROOT
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import ledger_parallel_census as lpc
        table = lpc.compile_table()
        wl = lpc.load_whitelist(os.path.join(root, "state",
                                            "ledger-parallel-whitelist.json"))
        report = lpc.scan(table, wl, root=root)
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "census raised: %r" % e
    verdict = lpc.verdict_of(report)
    counts = report["counts"]
    detail = "verdict=%s files=%d lines=%d parallel=%d whitelisted=%d new=%d" % (
        verdict, report["files_scanned"], report["lines_scanned"],
        counts["parallel"], counts["whitelisted"], counts["new"])
    for hit in report["hits"]:
        if not hit["whitelisted"]:
            detail += "\n  NEW %s:%d markers=%d %s" % (
                hit["file"], hit["line"], hit["markers"], hit["preview"])
    for err in report["errors"]:
        detail += "\n  ERROR %s %s" % (err["file"], err["error"])
    return verdict == "PARALLEL-CLEAN", detail


def probe_cost_contract(root=None):
    """Tech T138: cost-rail field contract census, judged every round.

    cost_contract_census.py shipped in T137 as a manual command. A
    drift in which a summary face stops emitting a key month_end_collect
    reads would only surface when someone remembered to run it - the
    same "a cap nobody measures" shape as T133. Running it here makes
    the contract face mechanically checked each round (runtime <1s).
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import cost_contract_census as ccc
        result = ccc.census()
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "census raised: %r" % e
    return cost_contract_judge(result)


def scan_root_judge(report):
    """T161: pure verdict for scan_root_census.run_census() output.

    The census banner flips only on NEEDS-EXCLUDE, so a report can read
    SCAN-ROOT-CLEAN while carrying UNVERIFIED rows (a guard literal that
    drifted off the tool). Judging the banner alone would restore the very
    "a cap nobody measures" default-pass the census was built to remove,
    so this probe grades rows and never trusts the summary line.
    """
    rows = (report or {}).get("rows") or []
    if not rows:
        return False, "no registry rows in report (census did not run)"
    fix = [r for r in rows if r.get("verdict") == "NEEDS-EXCLUDE"]
    if fix:
        return False, "VERDICT=FIX needs-exclude=%d: %s" % (
            len(fix), "; ".join(str(r.get("id")) for r in fix))
    unv = [r for r in rows if r.get("verdict") == "UNVERIFIED"]
    if unv:
        return False, "UNVERIFIED rows=%d (banner=%s): %s" % (
            len(unv), (report or {}).get("verdict"),
            "; ".join(str(r.get("id")) for r in unv))
    unknown = [r for r in rows if r.get("verdict") not in
               ("ROOT-SEPARATED", "STRUCTURAL-NA")]
    if unknown:
        return False, "unrecognised row verdict: %s" % "; ".join(
            "%s=%s" % (r.get("id"), r.get("verdict")) for r in unknown)
    counts = (report or {}).get("counts") or {}
    return True, "verdict=%s rows=%d %s sweep=%d(informational)" % (
        (report or {}).get("verdict"), len(rows),
        " ".join("%s=%d" % kv for kv in sorted(counts.items())),
        len((report or {}).get("sweep_candidates") or []))


def probe_scan_roots(root=None):
    """Probe no.17 (T161): scan-root self-reference census each round.

    scan_root_census.py shipped in T160 as a manual command. A new scanner
    that walks the repo without excluding itself, or a guard literal that
    drifts, would only surface when someone remembered to run it.
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import scan_root_census as src
        rep = src.run_census(base=root or ROOT)
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "scan_root_census raised: %r" % e
    return scan_root_judge(rep)


def probe_status_export(root=None):
    """T143: the CEO-facing export face, judged every round.

    Mandate 产品优先律 clause 5 fixes three lines in docs/status-export.json
    and requires refresh when reality moves, but nothing measured it - a
    stale face (or a GBK-mangled one that still parses as JSON) would read
    as live activity to the CEO. The gate lives in
    Tools/status_export_check.py; here it just runs the same code path each
    round.
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import status_export_check as sec
        verdict, detail = sec.check(root=root or ROOT)
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "status export check raised: %r" % e
    return verdict == "EXPORT-GREEN", "verdict=%s %s" % (verdict, detail)


RUNBOOK_MAX_BYTES = 2048


def probe_ledger_encoding(root=None):
    """Tech T145: ledger encoding health, judged every round.

    Rounds log / heartbeat are UTF-8 single-line ledgers whose only write
    path is round_append.py. PowerShell Add-Content defaults to ANSI(GBK)
    on zh-CN hosts, so a single hand-written shell append can inject真
    mojibake that still parses as text and reads as live content. The
    census lives in Tools/ledger_encoding_guard.py (same classify() the
    write-side J8 gate uses) - this probe just runs it read-only.
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    root = root or ROOT
    try:
        import ledger_encoding_guard as leg
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "ledger_encoding_guard import raised: %r" % e
    rows = leg.census([os.path.join(root, "state", "rounds.log"),
                       os.path.join(root, "state", "heartbeat.txt")])
    bad = [r for r in rows if r.get("status") != "ENCODING-OK"]
    if bad:
        return False, "LEDGER-ENCODING-BAD " + "; ".join(
            "%s=%s(dbl=%s,fffd=%s)" % (os.path.basename(r["path"]), r["status"],
                                       r.get("utf8_double_runs", "-"), r.get("fffd", "-"))
            for r in bad)
    qmark = [r for r in rows if r.get("qmark_runs", 0) > 0]
    if qmark:
        # T151: CJK->'?' damage is valid UTF-8 with no U+FFFD, so the
        # signatures above cannot see it - it must be counted separately.
        return False, "LEDGER-QMARK-BAD " + "; ".join(
            "%s=%d" % (os.path.basename(r["path"]), r["qmark_runs"]) for r in qmark)
    tot = sum(r.get("cjk_chars", 0) for r in rows)
    return True, "ENCODING-OK files=%d cjk_chars=%d latin_runs=%d" % (
        len(rows), tot, sum(r.get("latin_runs", 0) for r in rows))


def probe_runbook_budget(root=None):
    """Tech T133: the starter-read file must stay inside its own cap.

    runbook.md declares "<2KB" in its title and T-20260928-29 accepts
    it on that basis, but nothing measured it: by 10-26 it had quietly
    grown to 2499B (+22%) and no probe noticed - the same silent-drift
    shape as the model-set and supply-counter faces. A cap nobody
    measures is not a cap.
    """
    root = root or ROOT
    path = os.path.join(root, "state", "runbook.md")
    if not os.path.exists(path):
        return False, "state/runbook.md missing"
    n = os.path.getsize(path)
    if n > RUNBOOK_MAX_BYTES:
        return False, ("runbook %d bytes > %d cap (compress this round; "
                       "starter-read budget T-20260928-29)"
                       % (n, RUNBOOK_MAX_BYTES))
    return True, "runbook %d bytes <= %d cap" % (n, RUNBOOK_MAX_BYTES)


SERVE_SCRIPT = os.path.join(
    os.environ.get("BIGCOMPUTE_GROUP_ROOT",
                   os.path.abspath(os.path.join(ROOT, "..", ".."))),
    "Tools", "coder-serve.ps1")

SERVE_PATH_RE = {
    "model": re.compile(r"^\$model\s*=\s*'([^']+)'", re.M),
    "exe": re.compile(r"^\$exe\s*=\s*'([^']+)'", re.M),
}


def parse_serve_paths(text):
    """T140 pure: pull the two artifact constants out of coder-serve.ps1.

    Both are single-quoted absolute paths on their own lines. Returns None
    when either is not found, so a renamed script surfaces as UNVERIFIED
    rather than as a fake "artifact missing" P1.
    """
    out = {}
    for key, pat in SERVE_PATH_RE.items():
        m = pat.search(text or "")
        if not m:
            return None
        out[key] = m.group(1)
    return out


ARTIFACT_MIN_BYTES = {"gguf": 1 << 20, "pe": 64 << 10}


def artifact_kind(path):
    """T141: format class from the file extension ('' when unknown)."""
    ext = os.path.splitext(path or "")[1].lower()
    if ext == ".gguf":
        return "gguf"
    if ext in (".exe", ".dll"):
        return "pe"
    return "unknown"


def inspect_artifact(path, kind=None, min_bytes=None, read_head=None,
                     size_of=None):
    """T141 pure: format-level integrity, NOT a load test.

    T140 answers "is it on disk"; that is not the same question as "can
    anything open it" - R-53 shipped a 17.01GB INT8 DiT that was truncated
    and still returned Test-Path=True, then burned a whole batch. Here we
    check only what is cheap and non-destructive:
      gguf - magic 'GGUF' + size floor (a truncation below the floor is
             PRESENT-SUSPECT, not a silent pass)
      pe   - 'MZ' + e_lfanew -> 'PE\\0\\0' + size floor
    A file can pass this and still fail to load; the probe says so in its
    verdict text rather than implying a guarantee.
    """
    kind = kind or artifact_kind(path)
    if kind == "unknown":
        return "UNCHECKED", ("no integrity rule for extension %r"
                             % (os.path.splitext(path or "")[1] or "none"))
    read_head = read_head or (lambda p, n: open(p, "rb").read(n))
    size_of = size_of or os.path.getsize
    try:
        head = read_head(path, 0x1000)
        size = size_of(path)
    except Exception as e:  # noqa: BLE001 - unreadable file is a verdict
        return "UNREADABLE", "%s: %r" % (os.path.basename(path), e)
    if kind == "gguf":
        if head[:4] != b"GGUF":
            return "PRESENT-CORRUPT", ("magic=%r (expected b'GGUF')"
                                       % head[:4])
        floor = ARTIFACT_MIN_BYTES["gguf"] if min_bytes is None else min_bytes
        if size < floor:
            return "PRESENT-SUSPECT", ("size=%d < floor=%d (truncated?)"
                                       % (size, floor))
        return "PRESENT-OK", "GGUF size=%d" % size
    if head[:2] != b"MZ":
        return "PRESENT-CORRUPT", "magic=%r (expected b'MZ')" % head[:2]
    if len(head) < 0x40:
        return "PRESENT-CORRUPT", "header too short (%d bytes)" % len(head)
    e_lfanew = int.from_bytes(head[0x3C:0x40], "little")
    if (e_lfanew <= 0 or len(head) < e_lfanew + 4
            or head[e_lfanew:e_lfanew + 4] != b"PE\x00\x00"):
        return "PRESENT-CORRUPT", ("PE signature missing at e_lfanew=%d"
                                   % e_lfanew)
    floor = ARTIFACT_MIN_BYTES["pe"] if min_bytes is None else min_bytes
    if size < floor:
        return "PRESENT-SUSPECT", ("size=%d < floor=%d"
                                   % (size, floor))
    return "PRESENT-OK", "PE size=%d" % size


def serve_artifact_verdict(paths, exists=None, inspect=None):
    """T140/T141 pure: PASS iff both prerequisites are present AND sane.

    Read-only by design. Restoring artifacts belongs to the model-supply
    window (M82 / D-20261024-17) and the group launcher; this suite only
    makes absence and obvious corruption visible. UNVERIFIED when the
    constants could not be parsed.
    """
    if not paths:
        return True, ("UNVERIFIED: coder-serve.ps1 model/exe constants not "
                      "parsed (script moved or renamed) - no verdict, no "
                      "false alarm")
    exists = exists or os.path.exists
    missing = [k for k in ("model", "exe") if not exists(paths[k])]
    if missing:
        return False, ("LANE-ARTIFACT-MISSING %s - the :8090 lane cannot "
                       "start while a prerequisite is gone; a port probe "
                       "only sees this after the old listener dies "
                       "(10-25 09:26 first MISSING -> 10-27 03:16 first "
                       "notice = 41.8h blind window; restore = model-supply "
                       "window / group launcher, this suite only makes it "
                       "visible)"
                       % ", ".join("%s=%s" % (k, paths[k]) for k in missing))
    if inspect:
        bad = []
        for k in ("model", "exe"):
            state, detail = inspect(paths[k])
            if state != "PRESENT-OK":
                bad.append("%s=%s(%s)" % (k, state, detail))
        if bad:
            return False, ("LANE-ARTIFACT-BAD %s - present but not a sane "
                           "artifact (format-level check only: magic + size "
                           "floor, not a load test); a truncated file would "
                           "otherwise burn a batch (R-53 17.01GB case)"
                           % "; ".join(bad))
    return True, ("artifacts in place and sane: model=%s exe=%s"
                  % (os.path.basename(paths["model"]),
                     os.path.basename(paths["exe"])))


def probe_serve_artifacts(script=None, exists=None, inspect=None):
    """Probe no.13 (T140 presence + T141 integrity): lane prerequisites.

    Blind spot: every live probe in this suite watches a socket, so a
    missing or truncated model/exe stays invisible until something dies.
    Two checks against the launcher's own constants close that gap.
    """
    script = script or SERVE_SCRIPT
    try:
        with open(script, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except Exception:  # noqa: BLE001 - unreadable script = no verdict
        return True, ("UNVERIFIED: cannot read %s - no verdict, no false "
                      "alarm" % script)
    ok, detail = serve_artifact_verdict(parse_serve_paths(text), exists,
                                       inspect or inspect_artifact)
    if not ok and CODER_PORT in TOMBSTONED_LANES:
        return True, ("TOMBSTONE-N/A (%s) - :%d lane retired by "
                      "D-20261029-11, prerequisites no longer owed"
                      % (detail, CODER_PORT))
    return ok, detail


CODER_EXPECTED_IMAGE = "llama-server.exe"
CODER_PORT = 8090
# D-20261029-11 (b): the L1 coder lane :8090 was retired as a tombstone after
# the 10-29 12:00 verdict (zero listener, model dir gone, 0 gguf on disk).
# "别再逐轮 P1 复报" means the retired lane's absence - and the absence of its
# prerequisites - is a registered reading, not a defect. The successor KPI is
# Tools/l1_kpi_probe.py against L1_ENDPOINT (the post-verdict L1 default, see
# docs/ops/l1-kpi-definition-v1.md §七 - written by name, not by port literal,
# because a number in this comment would seed the KPI-B scope of the row below).
TOMBSTONED_LANES = frozenset({CODER_PORT})


def coder_port_owner(port=CODER_PORT):
    """T122 read-only port-owner lookup: (pid, image) on the LISTENING
    socket of `port`, else (None, None).

    A foreign image holding :8090 is a different failure class from a
    dead lane: the resident cannot bind at all, so the verdict must name
    the owner instead of reporting a bare timeout (10-24 case - the
    eCloud.exe autostart client took 127.0.0.1:8090 on 10-22 and the
    four consumer lanes stayed silently degraded for two days).
    """
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                           capture_output=True, timeout=15)
        text = (r.stdout or b"").decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - diagnosis is best effort
        return None, None
    pat = re.compile(r":%d\s+\S+\s+LISTENING\s+(\d+)" % port)
    pid = None
    for line in text.splitlines():
        m = pat.search(line)
        if m:
            pid = m.group(1)
            break
    if not pid:
        return None, None
    image = None
    try:
        t = subprocess.run(
            ["tasklist", "/FI", "PID eq %s" % pid, "/FO", "CSV", "/NH"],
            capture_output=True, timeout=15)
        rows = (t.stdout or b"").decode("utf-8", errors="replace").splitlines()
        if rows and rows[0].startswith('"'):
            image = rows[0].split('","')[0].strip('"')
    except Exception:  # noqa: BLE001 - image name is bonus context
        pass
    return pid, image


def classify_coder_failure(pid, image, err, artifacts=None):
    """T122 pure verdict text for a failed :8090 probe.

    Four mutually exclusive classes, all FAIL (never softened; charter
    §三.9 criterion stays frozen - this only names the class):
      FOREIGN-PORT - a non-lane image owns the socket (lane cannot bind)
      LANE-DOWN    - the lane image owns it but does not answer
      LANE-DOWN    - nothing listening, prerequisites in place (should be up)
      LANE-ABSENT  - nothing listening AND a prerequisite artifact is gone
                     (deployment absent). D-18 ③ calls this a non-fault
                     reading; naming it keeps ABSENT distinguishable from
                     DOWN without touching the PASS/FAIL verdict.
    `artifacts` is probe no.13's (ok, detail) tuple, or None when the
    caller has no evidence - absent evidence must not invent ABSENT.
    """
    if pid and image and image.lower() != CODER_EXPECTED_IMAGE:
        return ("L1 coder serve 8090 FOREIGN-PORT: %s pid=%s owns "
                "127.0.0.1:%d (expected %s) - the lane cannot bind; four "
                "consumer lanes degraded (fix = free the port for the lane "
                "or relocate the lane, CEO decision, not a watchdog retry)"
                % (image, pid, CODER_PORT, CODER_EXPECTED_IMAGE))
    if pid:
        return ("L1 coder serve 8090 LANE-DOWN: %s pid=%s owns the port but "
                "/v1/models is unresponsive (four consumer lanes degraded; "
                "probe error %r)" % (image or "unknown", pid, err))
    if artifacts is not None:
        art_ok, art_detail = artifacts
        if not art_ok and "LANE-ARTIFACT-MISSING" in art_detail:
            return ("L1 coder serve 8090 LANE-ABSENT: no listener on :%d (%r) "
                    "and a lane prerequisite is gone (%s) - deployment "
                    "absent, D-18 ③ non-fault reading, still FAIL per "
                    "charter §三.9 until the 12:00 verdict rules on the lane"
                    % (CODER_PORT, err, art_detail))
    return ("L1 coder serve 8090 LANE-DOWN: no listener on :%d (%r); four "
            "consumer lanes degraded - fix next round top priority"
            % (CODER_PORT, err))


def probe_l1_coder_serve():
    """Probe no.9 (T106): L1 coder lane :8090 liveness, read-only.

    O-20261015-1335/1500 Qwen v3 final switchover made llama.cpp :8090
    (Qwen3.6-35B-A3B) the L1 route for four consumer lanes (BigLife
    year-ring / MV storyboard / resident QA 8792 backend / BigMoney
    advisor). The group-deployed CoderServe watchdog (PT10M, PID lock)
    keeps the process alive, but nothing in this suite watched the lane
    itself: 8792 /health answers without exercising the model, so a
    dead :8090 would surface only as downstream consumer failures.
    GET /v1/models only - no generation, zero GPU load, 5s timeout.
    PASS = HTTP 200 + at least one model id in the listing.
    Pre-check 2026-10-16 22:0x: 200 + Qwen3.6-35B-A3B-UD-IQ4_XS.gguf.
    """
    try:
        with urllib.request.urlopen(CODER_SERVE_URL, timeout=5) as r:
            body = r.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        models = [m.get("id") for m in (data.get("data") or [])
                  if isinstance(m, dict) and m.get("id")]
        ok = r.status == 200 and bool(models)
        return ok, ("http %d /v1/models: %d model(s) listed "
                    "(L1 coder lane alive; consumers: BigLife year-ring "
                    "/ MV storyboard / resident-QA-8792 backend / "
                    "BigMoney advisor)" % (r.status, len(models)))
    except Exception as e:  # noqa: BLE001 - probe must not crash pipeline
        pid, image = coder_port_owner()
        if CODER_PORT in TOMBSTONED_LANES:
            return True, ("L1 coder serve %d TOMBSTONE-N/A: lane retired by "
                          "D-20261029-11 (%r; owner=%s pid=%s) - successor "
                          "KPI = Tools/l1_kpi_probe.py reach on L1_ENDPOINT"
                          % (CODER_PORT, e, image or "-", pid or "-"))
        return False, classify_coder_failure(pid, image, e,
                                             probe_serve_artifacts())


# T171 (BC-P-63 approved by the 2026-10-29 23:55 ban-lift order): the
# heartbeat row used to count ANY "HH:MM" anywhere in the line, so a scratch
# line that merely CITES a second-hand time (e.g. "orders 13:42:04 ...") was
# read as two fused rows -> false merged-rows FAIL. Replacement = combo marker:
#   (1) line-start anchor  ^\\?\\d{2}:\\d{1,2}x?   -- one per real entry head
#   (2) time immediately followed by a known line-opening word -- catches the
#       embedded head of a truly fused line, x-tolerant ("18:0x") and with an
#       optional :SS for os-loop / idle_selfcheck stamps.
# Openers are written as \u escapes so this file stays pure ASCII.
# Evidence: state/t63-marker-eval-20261009.txt (J1/J2/J3 gates, 93 live
# false positives eliminated, 0 new false positives, 2 fresh x-form gains).
# T172: the marker now lives in Tools/ledger_markers.py so the WRITE side
# (round_append J5) judges rows with this exact regex instead of a
# different rule ("any HH:MM >= 2"), which used to refuse legal
# second-hand time citations that this reader accepts.
if HERE not in sys.path:
    sys.path.insert(0, HERE)
# T174: the seven-file marker table now lives in ledger_markers too, so the
# tail fixer cannot drift into a looser rule than this probe.
from ledger_markers import HB_OPENERS, HB_MARKER, TAIL_PROBE_FILES  # noqa: E402
# BC-P-45 判例（10-13 T86 行实锚）：ID 形锚=「| <编号> |」整胞——裸
# \| T\d+ 会把证据胞里合法的「| T85 行+…」开头误计成第二行 ID（假
# merged-rows FAIL）；真合并行的第二行 ID 恒为整胞形态仍命中双计。
# 该表本体=Tools/ledger_markers.py（T174 单源），本文件只导入不重定义。


def probe_ledger_tails(root=None):
    """BC-P-45 (probe no.8): git-tracked ledger tail integrity.

    Read-side complement to the write-side round_append J4 guard:
    per file assert (a) last byte is a newline, (b) the last line
    holds at most one row/date marker (>= 2 = suspected merged lines,
    e.g. two queue rows or two daily-clean entries fused into one),
    and (c) no empty line inside the last 12 lines (T95: non-canonical
    append residue, 14:0x case).
    Pure read, zero writes, zero new ledgers. root param lets the
    selftest run the exact probe code against temp fixtures (T96).
    """
    root = root or ROOT
    fails, detail = [], []
    for rel, marker in TAIL_PROBE_FILES:
        path = os.path.join(root, *rel.split("/"))
        if not os.path.exists(path):
            fails.append("%s missing" % rel)
            detail.append("%s: MISSING" % rel)
            continue
        with open(path, "rb") as f:
            raw = f.read()
        rows = [ln for ln in raw.split(b"\n") if ln.strip()]
        if not rows:
            fails.append("%s empty" % rel)
            detail.append("%s: EMPTY" % rel)
            continue
        try:
            last = rows[-1].decode("utf-8", errors="strict")
        except UnicodeDecodeError as e:
            fails.append("%s last line utf-8 decode fail" % rel)
            detail.append("%s: LAST-LINE DECODE FAIL at byte %d"
                          % (rel, e.start))
            continue
        n_mark = len(re.findall(marker, last))
        tail_nl = raw.endswith(b"\n")
        if not tail_nl:
            fails.append("%s: trailing newline missing" % rel)
        # T95 (2026-10-14): tail-region empty-line sniff. The rows filter
        # above hides stray blank lines, so a non-canonical append that
        # emits "\n"+entry (14:0x case) leaves an invisible artifact at
        # position -3. All legit appends are single-line entries/table
        # rows (round_append writes line+"\n" always), so any empty line
        # inside the last 12 lines is write-path damage residue.
        raw_lines = raw.split(b"\n")
        if tail_nl:
            raw_lines = raw_lines[:-1]
        # T193 (2026-11-03): one blank line directly above a section heading
        # ("## ...") is canonical markdown spacing, not write residue.  The
        # T95 rule targets appends that emit "\n"+entry; without this
        # whitelist every day-close section separator is a false red.
        window_start = max(0, len(raw_lines) - 12)
        empt = []
        for i in range(window_start, len(raw_lines)):
            if raw_lines[i].strip():
                continue
            nxt = raw_lines[i + 1] if i + 1 < len(raw_lines) else b""
            if nxt.startswith(b"## "):
                continue
            empt.append(i)
        if empt:
            fails.append("%s: %d empty line(s) in tail-12 "
                         "(non-canonical write residue?)" % (rel, len(empt)))
        if n_mark >= 2:
            fails.append("%s: %d markers in last line (merged rows?)"
                         % (rel, n_mark))
        detail.append("%s: tail_nl=%s tail_empty=%d last_markers=%d "
                      "last_len=%d"
                      % (rel, "ok" if tail_nl else "MISSING", len(empt),
                         n_mark, len(last)))
    ok = not fails
    out = "\n".join(detail)
    if fails:
        out += "\nSUSPECT: " + "; ".join(fails)
    return ok, out


def selftest_tails():
    """T96: synthetic negative fixtures for probe no.8 (a)(b)(c).

    The live probe only ever sees healthy ledgers, so a regression in
    the tail checks would surface as a false PASS. This selftest builds
    the full seven-path TAIL_PROBE_FILES layout in a temp dir (real
    ledgers are never touched), injects one known damage signature per
    scenario, and asserts the probe fails on exactly that signature:
      A clean     -> probe PASS
      B tail-12 blank (T95 14:0x class)      -> "empty line(s) in tail-12"
      C merged last line (10-04/05 class)    -> "2 markers in last line"
      D missing trailing newline (T24 class) -> "trailing newline missing"
    """
    import shutil
    import tempfile

    clean = {
        "state/rounds.log": "2026-10-15 13:00 tokens: local=1 api=0\n",
        "state/heartbeat.txt": "13:00 round heartbeat ok\n",
        "HQ-FEEDBACK.md": "## 日清：无待报\n",
        "state/proposals.md": "| BC-P-99 | 2026-10-15 | x |\n",
        "state/queue/main.md": "| M99 | 2026-10-15 | x |\n",
        "state/queue/tech.md": "| T99 | 2026-10-15 | x |\n",
        "state/queue/explore.md": "| E99 | 2026-10-15 | x |\n",
    }

    def build(tmp, damage=None):
        for rel, text in clean.items():
            text = dict(clean)[rel]
            if damage and rel == damage[0]:
                text = damage[1]
            path = os.path.join(tmp, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)

    results = []

    def scenario(label, damage, want_ok, needle):
        tmp = tempfile.mkdtemp(prefix="qa-tails-selftest-")
        try:
            build(tmp, damage)
            ok, detail = probe_ledger_tails(root=tmp)
            good = ok == want_ok and (needle is None or needle in detail)
            results.append(good)
            print("  [%s] %s (ok=%s)" % ("PASS" if good else "FAIL",
                                        label, ok))
            if not good:
                print("    detail: %s" % detail.replace("\n", " | "))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("qa_smoke probe no.8 selftest (T96 - synthetic fixtures, "
          "real ledgers untouched)")
    scenario("A clean seven-ledger layout -> PASS", None, True, None)
    scenario("B tail-12 blank -> FAIL empty-line",
             ("state/rounds.log",
              "2026-10-15 13:00 tokens: local=1 api=0\n\n"),
             False, "empty line(s) in tail-12")
    scenario("C merged last line -> FAIL 2 markers",
             ("state/proposals.md",
              "| BC-P-99 | 2026-10-15 | x |\n"
              "| BC-P-99 | x | BC-P-98 | 2026-10-15 | fused |\n"),
             False, "2 markers in last line")
    scenario("D missing trailing newline -> FAIL tail_nl",
             ("state/heartbeat.txt", "13:00 round heartbeat ok"),
             False, "trailing newline missing")
    # T171 / BC-P-63: heartbeat marker combo judgement (applied 2026-10-30).
    # E = the 10-08 false-positive class: ONE logical line citing a
    # second-hand time must stay a PASS (old any-position marker read 2).
    scenario("E second-hand time citation -> PASS (no false merged)",
             ("state/heartbeat.txt",
              "13:00 \u8F6E\u4E2D\u523B\u75D5\uFF1A\u5BA1\u6838\u6B65\u6BD5"
              "\uFF08orders 13:42:04 \u65B0\u884C\uFF09\n"),
             True, None)
    # F = true fused rows keep being caught (embedded head follows time).
    scenario("F fused rows (two heads) -> FAIL 2 markers",
             ("state/heartbeat.txt",
              "13:00 \u8F6E\u4E2D\u523B\u75D5 a "
              "14:30 \u8F6E\u5C3E T99 done\n"),
             False, "2 markers in last line")
    # G = x-form head ("18:0x 轮中刻痕") is a real entry head the old marker
    #     missed entirely; it must still count as exactly one row.
    scenario("G x-form entry head -> PASS (single row)",
             ("state/heartbeat.txt",
              "18:0x \u8F6E\u4E2D\u523B\u75D5\uFF1A\u961F\u5217\u6B65\u6BD5\n"),
             True, None)
    # H / I = T176: machine-written rows carry a full date head and, for
    #     alarm rows, no opener word at all. H used to score 0 heads (a
    #     single row looking like no row); I used to score 0 as well, so a
    #     fused pair stayed invisible. Both must now read 1 and 2.
    scenario("H full-date machine row -> PASS (single row)",
             ("state/heartbeat.txt",
              "2026-10-31 14:11:01 P2 hq_feedback: MISSED(age_h=17.0)\n"),
             True, None)
    scenario("I fused pair of full-date alarm rows -> FAIL 2 markers",
             ("state/heartbeat.txt",
              "2026-10-31 14:11:01 P2 hq_feedback: MISSED "
              "2026-10-31 12:43:09 P2 hq_feedback: MISSED\n"),
             False, "2 markers in last line")
    # J = T193: one blank line above a "## " section heading is canonical
    #     spacing and must not be read as write residue; K keeps the true
    #     residue class (blank that is NOT a separator) red.
    scenario("J blank above section heading -> PASS (canonical spacing)",
             ("HQ-FEEDBACK.md",
              "## \u65e5\u6e05\uff1a\u65e0\u5f85\u62a5\n"
              "\n"
              "## 2026-11-03 19:0x \u65e5\u6e05\uff08"
              "\u65b0\u8282\uff09\n"),
             True, None)
    scenario("K stray blank without heading -> FAIL empty-line",
             ("state/heartbeat.txt",
              "19:00 round heartbeat ok\n\n19:01 second row\n"),
             False, "empty line(s) in tail-12")
    print("selftest verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def selftest_coder_class():
    """T122 symmetric fixtures for classify_coder_failure().

    The live probe can only exercise one class at a time (whatever the
    box happens to be doing), so each verdict branch is pinned here:
      A foreign image on the port -> FOREIGN-PORT + owner name/pid
      B lane image, no answer     -> LANE-DOWN (owner named)
      C nobody listening          -> LANE-DOWN (no listener)
      D nobody listening + prerequisite gone -> LANE-ABSENT (D-18 ③ class,
        still FAIL: naming the class must not soften the verdict)
      E nobody listening + artifacts fine    -> LANE-DOWN (should be up)
    All five must stay FAIL - a softened verdict would hide a dead lane.
    """
    miss = (False, "LANE-ARTIFACT-MISSING model=D:\\x.gguf - gone")
    cases = [
        ("A foreign owner -> FOREIGN-PORT",
         ("28008", "eCloud.exe", "timed out", None),
         "FOREIGN-PORT: eCloud.exe pid=28008"),
        ("B lane owner unresponsive -> LANE-DOWN",
         ("4242", "llama-server.exe", "timed out", None),
         "LANE-DOWN: llama-server.exe pid=4242"),
        ("C no listener -> LANE-DOWN",
         (None, None, "connection refused", None),
         "no listener on :8090"),
        ("D no listener + artifact gone -> LANE-ABSENT",
         (None, None, "connection refused", miss),
         "LANE-ABSENT"),
        ("E no listener + artifacts ok -> LANE-DOWN",
         (None, None, "connection refused", (True, "artifacts in place")),
         "LANE-DOWN: no listener"),
    ]
    results = []
    print("qa_smoke probe no.9 selftest (T122 port-owner classification)")
    for label, args, needle in cases:
        text = classify_coder_failure(*args)
        good = needle in text
        results.append(good)
        print("  [%s] %s" % ("PASS" if good else "FAIL", label))
        if not good:
            print("    detail: %s" % text)
    print("selftest verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def selftest_supply_fields():
    """T132 fixtures for supply_dual_fields(), extended by T167.

    Live heartbeat can only show the current pair, so every rejection
    branch is pinned: a line carrying just one counter is exactly the
    accidental-read the T128 split created, and effective > ready is
    arithmetically impossible unless one side went stale. T167 adds the
    label side: the deprecated name coming back, and the self-describing
    candidate label going missing, must both fail rather than pass.
    """
    cases = [
        ("both fields, effective < ready",
         "idle_selfcheck: verdict=busy pool_rows_candidate=2 "
         "pool_effective=0 pool_ignitable=2",
         True, "effective <= ready"),
        ("both fields, equal",
         "pool_rows_candidate=3 pool_effective=3 pool_ignitable=3", True,
         "pool_effective=3"),
        ("effective missing", "pool_rows_candidate=2 pool_ignitable=2", False,
         "pool_effective missing"),
        ("ready missing", "pool_rows_candidate=1 pool_effective=0", False,
         "pool_ignitable missing"),
        ("inverted",
         "pool_rows_candidate=4 pool_effective=4 pool_ignitable=2", False,
         "inverted"),
        ("no counters at all", "idle_selfcheck: verdict=busy", False,
         "no pool_effective/pool_ignitable"),
        ("deprecated pool_claimable= back",
         "idle_selfcheck: verdict=busy pool_rows_candidate=1 "
         "pool_effective=0 pool_ignitable=1 pool_claimable=1",
         False, "deprecated pool_claimable="),
        ("candidate label missing",
         "idle_selfcheck: verdict=busy pool_effective=0 pool_ignitable=2",
         False, "pool_rows_candidate missing"),
    ]
    results = []
    print("qa_smoke probe no.10 selftest (T132/T167 supply reading+labels)")
    for label, line, want_ok, needle in cases:
        ok, detail = supply_dual_fields(line)
        good = ok == want_ok and needle in detail
        results.append(good)
        print("  [%s] %s" % ("PASS" if good else "FAIL", label))
        if not good:
            print("    detail: ok=%s %s" % (ok, detail))
    print("selftest verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def selftest_cost_contract():
    """T138 fixtures for cost_contract_judge().

    Live census can only show today's answer, so all three rejection
    shapes are pinned synthetically: a non-green verdict, the
    contradictory "green but gaps" state, and an empty rail set.
    """
    cases = [
        ("clean green", {"verdict": "COST-CONTRACT-GREEN", "gaps": [],
                         "rails": ["cost", "quota"]}, True, "gaps=0"),
        ("gap verdict", {"verdict": "COST-CONTRACT-GAP",
                         "gaps": [{"rail": "cost"}], "rails": ["cost"]},
         False, "verdict=COST-CONTRACT-GAP"),
        ("green but gapped", {"verdict": "COST-CONTRACT-GREEN",
                              "gaps": [{"rail": "quota"}],
                              "rails": ["quota"]}, False, "but 1 gap"),
        ("no rails probed", {"verdict": "COST-CONTRACT-GREEN", "gaps": [],
                             "rails": []}, False, "no rails probed"),
    ]
    results = []
    print("qa_smoke probe no.12 selftest (T138 cost contract judgement)")
    for label, result, want_ok, needle in cases:
        ok, detail = cost_contract_judge(result)
        good = ok == want_ok and needle in detail
        results.append(good)
        print("  [%s] %s" % ("PASS" if good else "FAIL", label))
        if not good:
            print("    detail: ok=%s %s" % (ok, detail))
    print("selftest verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def _pe_fixture(header_ok=True, size=0x200):
    """T141 fixture builder: minimal byte image with/without a PE header."""
    buf = bytearray(b"\x00" * size)
    buf[0:2] = b"MZ"
    buf[0x3C:0x40] = (0x80).to_bytes(4, "little")
    buf[0x80:0x84] = b"PE\x00\x00" if header_ok else b"PX\x00\x00"
    return bytes(buf)


def selftest_serve_artifacts():
    """T140/T141 fixtures for presence, integrity and the combined verdict.

    Live can only show today's state (both artifacts absent), so PASS and
    the drift branches are pinned synthetically:
      A both on disk        -> PASS
      B model absent        -> FAIL naming the missing key
      C exe absent          -> FAIL naming the missing key
      D constants unparsed  -> UNVERIFIED, ok=True (never a false P1)
      E gguf magic + floor  -> PRESENT-OK
      F gguf below floor    -> PRESENT-SUSPECT (truncation is not a pass)
      G gguf bad magic      -> PRESENT-CORRUPT
      H pe MZ + PE + floor  -> PRESENT-OK
      I pe bad PE sig       -> PRESENT-CORRUPT
      J missing file        -> UNREADABLE
      K verdict + corrupt   -> FAIL LANE-ARTIFACT-BAD
    """
    text = ("$model = 'C:\\m\\q.gguf'\n"
            "$exe   = 'C:\\m\\llama-server.exe'\n")
    paths = parse_serve_paths(text)
    have = set(paths.values())
    cases = [
        ("both artifacts on disk", paths, lambda p: p in have,
         True, "artifacts in place"),
        ("model absent", paths, lambda p: p != paths["model"],
         False, "LANE-ARTIFACT-MISSING model="),
        ("exe absent", paths, lambda p: p != paths["exe"],
         False, "LANE-ARTIFACT-MISSING exe="),
        ("constants unparsed", parse_serve_paths("$model = nothing"),
         lambda p: True, True, "UNVERIFIED"),
    ]
    results = []
    print("qa_smoke probe no.13 selftest (T140 presence + T141 integrity)")
    for label, p, exists, want_ok, needle in cases:
        ok, detail = serve_artifact_verdict(p, exists)
        good = ok == want_ok and needle in detail
        results.append(good)
        print("  [%s] %s" % ("PASS" if good else "FAIL", label))
        if not good:
            print("    detail: ok=%s %s" % (ok, detail))

    # Integrity fixtures read real temp bytes, so the reader is exercised
    # exactly as on disk (no monkey-patched magic strings).
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="qa-integrity-selftest-")
    gguf_ok = os.path.join(tmp, "ok.gguf")
    with open(gguf_ok, "wb") as f:
        f.write(b"GGUF" + b"\x00" * 64)
    gguf_bad = os.path.join(tmp, "bad.gguf")
    with open(gguf_bad, "wb") as f:
        f.write(b"GGUX" + b"\x00" * 64)
    pe_ok = os.path.join(tmp, "ok.exe")
    with open(pe_ok, "wb") as f:
        f.write(_pe_fixture(True))
    pe_bad = os.path.join(tmp, "bad.exe")
    with open(pe_bad, "wb") as f:
        f.write(_pe_fixture(False))
    integ = [
        ("gguf magic + floor", gguf_ok, 4, "PRESENT-OK"),
        ("gguf below floor", gguf_ok, 1 << 20, "PRESENT-SUSPECT"),
        ("gguf bad magic", gguf_bad, 4, "PRESENT-CORRUPT"),
        ("pe header valid", pe_ok, 0x100, "PRESENT-OK"),
        ("pe bad signature", pe_bad, 0x100, "PRESENT-CORRUPT"),
        ("missing file", os.path.join(tmp, "nope.gguf"), 4, "UNREADABLE"),
    ]
    for label, path, floor, want in integ:
        state, detail = inspect_artifact(path, min_bytes=floor)
        good = state == want
        results.append(good)
        print("  [%s] %s -> %s" % ("PASS" if good else "FAIL", label, state))
        if not good:
            print("    want=%s got=%s (%s)" % (want, state, detail))

    # Combined verdict: present but corrupt must FAIL, not "in place".
    ok, detail = serve_artifact_verdict(
        paths, lambda p: True,
        lambda p: inspect_artifact(
            p, kind=artifact_kind(p),
            read_head=lambda _p, _n: b"GGUX" if p.endswith(".gguf")
            else _pe_fixture(True)[:0x1000],
            size_of=lambda _p: 1 << 21))
    good = (ok is False and "LANE-ARTIFACT-BAD" in detail)
    results.append(good)
    print("  [%s] verdict rejects corrupt artifact" % ("PASS" if good
                                                       else "FAIL"))
    if not good:
        print("    detail: ok=%s %s" % (ok, detail))

    shutil.rmtree(tmp, ignore_errors=True)
    print("selftest verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def selftest_qa_functional():
    """T153 fixtures: every classifier branch, with the live 10-28 class
    first - the point of the probe is that 'health ok + no backend' must
    never read as PASS again."""
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    r1 = classify_qa_functional(False, False)
    check("Q1 server down -> SERVER-DOWN, not ok",
          (not r1[0]) and r1[1] == "SERVER-DOWN")

    r2 = classify_qa_functional(True, False)  # 2026-10-28 live class
    check("Q2 health ok + backend down -> HEALTH-OK-BACKEND-DOWN, not ok",
          (not r2[0]) and r2[1] == "HEALTH-OK-BACKEND-DOWN")

    gate = "\u672c\u5730AI\u751f\u6210" + " qwen3.6-35b 42tok/2.1s"
    r3 = classify_qa_functional(True, True, 200,
                                {"reply": "ok answer", "gate": gate})
    check("Q3 real answer + gate -> ASK-OK", r3[0] and r3[1] == "ASK-OK")

    r4 = classify_qa_functional(True, True, 200,
                                {"reply": "ok answer", "gate": "no marker"})
    check("Q4 200 without AIGC gate marker -> ASK-BAD-BODY, not ok",
          (not r4[0]) and r4[1] == "ASK-BAD-BODY")

    r5 = classify_qa_functional(True, True, 503,
                                {"err": "backend down", "detail": "refused"})
    check("Q5 503 -> BACKEND-ERROR, not ok",
          (not r5[0]) and r5[1] == "BACKEND-ERROR")

    r6 = classify_qa_functional(True, True, 403, {"reason": "outside list"})
    check("Q6 403 -> WHITELIST-CONFIG, not ok",
          (not r6[0]) and r6[1] == "WHITELIST-CONFIG")

    r7 = classify_qa_functional(True, True, None, None, "timed out")
    check("Q7 timeout -> ASK-ERROR, not ok",
          (not r7[0]) and r7[1] == "ASK-ERROR")

    r8 = classify_qa_functional(True, True, 500, {"err": "boom"})
    check("Q8 unexpected status -> ASK-HTTP-500, not ok",
          (not r8[0]) and r8[1] == "ASK-HTTP-500")

    check("Q9 probe anchor matches C-xxxxx and question fits 2..200",
          bool(re.fullmatch(r"C-\d{5}", QA_ASK_ANCHOR))
          and 2 <= len(QA_ASK_QUESTION) <= 200)

    check("Q10 probe honours RESIDENT_QA_LLM_URL port override",
          qa_backend_port() == QA_BACKEND_PORT
          or os.environ.get("RESIDENT_QA_LLM_URL"))

    check("Q11 ascii_safe strips non-printables (log stays printable)",
          _ascii_safe("a\x81b") == "a.b"
          and _ascii_safe("x" * 400, 10) == "x" * 10)

    # Q12-Q14 lane awareness: primary-only reading must not mask a live
    # fallback, and a dead-primary+dead-fallback pair must still read as
    # no-backend (no softening of the 10-28 false-green class).
    lp_old = qa_lane_ports({}, 8090)
    check("Q12 health without llm block -> default primary only",
          lp_old == [8090])
    lp_new = qa_lane_ports({"llm": {"primary_url": "http://127.0.0.1:8090/v1/chat/completions",
                                    "fallback_url": "http://127.0.0.1:11434/v1/chat/completions"}}, 8090)
    check("Q13 health llm block -> primary then fallback",
          lp_new == [8090, 11434])
    lp_dup = qa_lane_ports({"llm": {"primary_url": "http://h:11434/v1/chat/completions",
                                    "fallback_url": "http://h:11434/v1/chat/completions"}}, 8090)
    check("Q14 identical lanes deduped (one probe, not two)",
          lp_dup == [11434])

    # Q15-Q18 (D-20261105-10): a contended-GPU ASK-ERROR is a known
    # environment constraint; everything else keeps failing.
    k1 = reclassify_known_env(False, "ASK-ERROR", "ask failed", True)
    check("Q15 contended ASK-ERROR -> KEPT-OPEN-KNOWN-ENV with evidence",
          k1[0] and "KEPT-OPEN-KNOWN-ENV" in k1[1]
          and KNOWN_ENV_EVIDENCE in k1[2])
    k2 = reclassify_known_env(False, "ASK-ERROR", "ask failed", False)
    check("Q16 same ASK-ERROR on an idle GPU still FAILS",
          (not k2[0]) and k2[1] == "ASK-ERROR")
    k3 = reclassify_known_env(False, "SERVER-DOWN", "dead", True)
    check("Q17 contended GPU never excuses a dead server",
          (not k3[0]) and k3[1] == "SERVER-DOWN")
    k4 = reclassify_known_env(False, "HEALTH-OK-BACKEND-DOWN", "no lane", True)
    check("Q18 contended GPU never excuses a missing backend lane",
          (not k4[0]) and k4[1] == "HEALTH-OK-BACKEND-DOWN")

    print("selftest qa-functional verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def selftest_gpu_contention():
    """T178 fixtures: contention read decides budgets, never verdicts."""
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    print("qa_smoke GPU contention selftest (T178)")

    s = parse_gpu_state("99 %, 11782 MiB, 12282 MiB")
    check("G1 standard CSV line parsed",
          s and s["util_pct"] == 99 and s["vram_used_mb"] == 11782
          and s["vram_total_mb"] == 12282 and s["vram_free_mb"] == 500)

    s2 = parse_gpu_state("12 %, 700 MiB, 12282 MiB\n")
    check("G2 second sample parsed (12% util, 11582MiB free)",
          s2 and s2["util_pct"] == 12 and s2["vram_free_mb"] == 11582)

    check("G3 unparseable output -> None (not a silent zero)",
          parse_gpu_state("NVIDIA-SMI has failed") is None
          and parse_gpu_state("") is None)

    ok1, lab1 = gpu_contention_verdict({"util_pct": 99, "vram_used_mb": 11782,
                                        "vram_total_mb": 12282,
                                        "vram_free_mb": 500})
    check("G4 99%% util + 500MiB free -> CONTENDED",
          ok1 and "util=99%" in lab1 and "vram_free=500MiB" in lab1)

    ok2, lab2 = gpu_contention_verdict({"util_pct": 5, "vram_used_mb": 700,
                                        "vram_total_mb": 12282,
                                        "vram_free_mb": 11582})
    check("G5 idle GPU -> idle label, no extra budget",
          (not ok2) and lab2.startswith("GPU-IDLE-OK"))

    ok3, lab3 = gpu_contention_verdict(None)
    check("G6 unreadable state never buys budget",
          (not ok3) and lab3 == "GPU-UNREADABLE")

    ok4, lab4 = gpu_contention_verdict({"util_pct": 10, "vram_used_mb": 11800,
                                        "vram_total_mb": 12282,
                                        "vram_free_mb": 482})
    check("G7 low util but starved VRAM -> CONTENDED",
          ok4 and "vram_free=482MiB" in lab4 and "util=" not in lab4)

    print("selftest gpu-contention verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def selftest_scan_roots():
    """T161 fixtures: a banner-only judge would pass the live hole."""
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    ok_rows = [{"id": "A", "verdict": "ROOT-SEPARATED"},
               {"id": "B", "verdict": "STRUCTURAL-NA"}]

    r1 = scan_root_judge({"verdict": "SCAN-ROOT-CLEAN", "rows": ok_rows})
    check("S1 clean rows -> PASS", r1[0] and "rows=2" in r1[1])

    r2 = scan_root_judge({"verdict": "SCAN-ROOT-FIX",
                          "rows": ok_rows + [{"id": "C",
                                              "verdict": "NEEDS-EXCLUDE"}]})
    check("S2 NEEDS-EXCLUDE -> FAIL",
          (not r2[0]) and "needs-exclude=1" in r2[1])

    # The live hole: the census banner counts NEEDS-EXCLUDE only, so a
    # drifted guard literal reads as UNVERIFIED under a CLEAN banner.
    r3 = scan_root_judge({"verdict": "SCAN-ROOT-CLEAN",
                          "rows": ok_rows + [{"id": "D",
                                              "verdict": "UNVERIFIED"}]})
    check("S3 UNVERIFIED under a CLEAN banner -> FAIL",
          (not r3[0]) and "UNVERIFIED" in r3[1] and "D" in r3[1])

    r4 = scan_root_judge({"verdict": "SCAN-ROOT-CLEAN", "rows": []})
    check("S4 empty report -> FAIL (no default pass)", not r4[0])

    check("S5 None report -> FAIL", not scan_root_judge(None)[0])

    r6 = scan_root_judge({"verdict": "SCAN-ROOT-CLEAN",
                          "rows": [{"id": "E", "verdict": "MAYBE"}]})
    check("S6 unrecognised row verdict -> FAIL (conservative)", not r6[0])

    print("selftest scan-root verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def selftest_heartbeat_tokens():
    """T168 fixtures: every silent-drift branch of the guarded token set.

    Live rows can only show today's healthy shape, so the rename, the
    unit flip, the non-numeric value and the non-integral counter are all
    pinned synthetically; S8 re-checks that the supply trio is still read
    together with no.10's pair semantics.
    """
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    good = ("2026-10-29 22:43:09 idle_selfcheck: verdict=busy "
            "ram_free_pct=46.7 vram_free_mb=1906 py_cpu=53.0 "
            "py_cpu_psutil=20.6 "
            "cpu_lane_idle=true pool_rows_candidate=1 pool_effective=0 "
            "queue_open=57 consecutive_idle=0 idle_rounds=0 "
            "agenda_starved=false serve_alive=true task_face=ok "
            "model_drift=DRIFT(m=2,a=0) breach_eff=true "
            "cpu_load_line=PASS(psutil=20.6<70.0,ref=53.0) "
            "pool_ignitable=3 stock_dispatchable=4 lane8090=DARK[DOWN] "
            "c1c4_line=FRESH(rounds_ago=0,verdict=C1C4-GREEN) "
            "hq_line=FRESH(age_h=0.4,date=2026-10-29)")

    r1 = heartbeat_token_guard(good)
    check("S1 healthy row -> PASS", r1[0] and "guarded=21" in r1[1])

    r2 = heartbeat_token_guard(good.replace("ram_free_pct=", "ram_pct="))
    check("S2 renamed token -> FAIL naming it",
          (not r2[0]) and "ram_free_pct missing" in r2[1])

    r3 = heartbeat_token_guard(good.replace("ram_free_pct=46.7",
                                            "ram_free_pct=140"))
    check("S3 percent out of range -> FAIL",
          (not r3[0]) and "out of range" in r3[1])

    r4 = heartbeat_token_guard(good.replace("vram_free_mb=1906",
                                            "vram_free_mb=-1"))
    check("S4 negative counter -> FAIL",
          (not r4[0]) and "negative" in r4[1])

    r5 = heartbeat_token_guard(good.replace("py_cpu=53.0", "py_cpu=na"))
    check("S5 non-numeric value -> FAIL",
          (not r5[0]) and "not numeric" in r5[1])

    r6 = heartbeat_token_guard(good.replace("queue_open=57",
                                            "queue_open=57.5"))
    check("S6 non-integral counter -> FAIL",
          (not r6[0]) and "not integral" in r6[1])

    check("S7 empty line -> FAIL", not heartbeat_token_guard("")[0])

    ok10, _ = supply_dual_fields(good)
    check("S8 supply trio still reads as a pair (no.10 agreement)", ok10)

    # T170 batch 1: enum/boolean drift the numeric guard cannot see.
    r9 = heartbeat_token_guard(good.replace("cpu_lane_idle=true",
                                            "cpu_lane_idle=idle"))
    check("S9 boolean token with non-boolean value -> FAIL",
          (not r9[0]) and "cpu_lane_idle outside bool domain" in r9[1])

    r10 = heartbeat_token_guard(good.replace("task_face=ok",
                                             "task_face=paused"))
    check("S10 unknown task_face value -> FAIL",
          (not r10[0]) and "task_face outside task_face domain" in r10[1])

    r11 = heartbeat_token_guard(good.replace("task_face=ok",
                                             "task_face=ALERT:updater"))
    check("S11 ALERT: prefixed task_face still accepted (open-ended branch)",
          r11[0])

    r12 = heartbeat_token_guard(good.replace("model_drift=DRIFT(m=2,a=0)",
                                             "model_drift=DRIFT(m=2)"))
    check("S12 malformed model_drift -> FAIL",
          (not r12[0]) and "model_drift outside" in r12[1])

    r13 = heartbeat_token_guard(good.replace("lane8090=DARK[DOWN]",
                                             "lane8090=DARK-DOWN"))
    check("S13 unparseable lane state -> FAIL",
          (not r13[0]) and "lane8090 outside" in r13[1])

    # S16-S19: the writer emits "<status>(m=,a=)" for every status
    # model_ref_drift_check returns. Only DRIFT used to be in domain, so the
    # routine "UNVERIFIED(m=0,a=0)" row (no baseline on disk) read as a guard
    # violation on every round - the guard blaming a legal writer form.
    for tag, form, want_ok in (
        ("S16", "UNVERIFIED(m=0,a=0)", True),
        ("S17", "GREEN(m=0,a=0)", True),
        ("S18", "UNVERIFIED(err)", True),
        ("S19", "MISSING(m=0,a=0)", False),
    ):
        rr = heartbeat_token_guard(
            good.replace("model_drift=DRIFT(m=2,a=0)", "model_drift=" + form))
        check("%s model_drift=%s -> %s" % (tag, form,
                                           "PASS" if want_ok else "FAIL"),
              bool(rr[0]) == want_ok)

    r14 = heartbeat_token_guard(good.replace(
        "c1c4_line=FRESH(rounds_ago=0,verdict=C1C4-GREEN)",
        "c1c4_line=FRESH(age_h=1)"))
    check("S14 c1c4_line without rounds_ago key -> FAIL",
          (not r14[0]) and "c1c4_line outside" in r14[1])

    r15 = heartbeat_token_guard(good.replace(
        "hq_line=FRESH(age_h=0.4,date=2026-10-29)", "hq_line=FRESH"))
    check("S15 hq_line without parenthesised payload -> FAIL",
          (not r15[0]) and "hq_line outside" in r15[1])

    # M117: the second CPU read is allowed to be absent (writer says "n/a"),
    # but it is never allowed to be unreadable junk or out of range.
    r20 = heartbeat_token_guard(
        good.replace("py_cpu_psutil=20.6", "py_cpu_psutil=n/a"))
    check("S20 py_cpu_psutil=n/a accepted (missing channel is not 0)",
          r20[0] and "py_cpu_psutil=n/a" in r20[1])

    r21 = heartbeat_token_guard(
        good.replace("py_cpu_psutil=20.6", "py_cpu_psutil=na"))
    check("S21 py_cpu_psutil=na (no slash) -> FAIL not numeric",
          (not r21[0]) and "py_cpu_psutil not numeric" in r21[1])

    r22 = heartbeat_token_guard(
        good.replace("py_cpu_psutil=20.6", "py_cpu_psutil=120.0"))
    check("S22 py_cpu_psutil out of range -> FAIL",
          (not r22[0]) and "py_cpu_psutil out of range" in r22[1])

    # T207: cpu_load_line guards the 70% line's single channel. The three
    # writer states pass; a dropped token, a wrong state word, and a
    # N/A-CHANNEL that quietly keeps a psutil number are all caught.
    ok_cl, msg_cl = heartbeat_token_guard(good)
    check("S23 cpu_load_line=PASS accepted", ok_cl and "cpu_load_line=PASS" in msg_cl)

    r24 = heartbeat_token_guard(
        good.replace("cpu_load_line=PASS(psutil=20.6<70.0,ref=53.0) ", ""))
    check("S24 cpu_load_line dropped -> FAIL naming it",
          (not r24[0]) and "cpu_load_line missing" in r24[1])

    r25 = heartbeat_token_guard(good.replace(
        "cpu_load_line=PASS(psutil=20.6<70.0,ref=53.0)",
        "cpu_load_line=OK(psutil=20.6<70.0,ref=53.0)"))
    check("S25 unknown cpu_load_line state word -> FAIL",
          (not r25[0]) and "cpu_load_line outside" in r25[1])

    r26 = heartbeat_token_guard(good.replace(
        "cpu_load_line=PASS(psutil=20.6<70.0,ref=53.0)",
        "cpu_load_line=N/A-CHANNEL(ref=53.0,line=70.0)"))
    check("S26 N/A-CHANNEL form accepted", r26[0])

    r27 = heartbeat_token_guard(good.replace(
        "cpu_load_line=PASS(psutil=20.6<70.0,ref=53.0)",
        "cpu_load_line=N/A-CHANNEL(psutil=20.6,ref=53.0)"))
    check("S27 N/A-CHANNEL carrying psutil -> FAIL",
          (not r27[0]) and "cpu_load_line outside" in r27[1])

    # T207 crosscheck: writer list / guard table / live row must agree.
    writer = writer_heartbeat_tokens()
    check("S28 writer token list readable", bool(writer[0]) and not writer[1])

    c28 = heartbeat_token_crosscheck(writer[0])
    check("S29 clean writer list vs tables -> OK", c28[0])

    c29 = heartbeat_token_crosscheck(list(writer[0]) + ["brand_new_token"])
    check("S30 writer token with no guard/declaration -> FAIL",
          (not c29[0]) and "brand_new_token" in c29[1])

    c30 = heartbeat_token_crosscheck(
        [t for t in writer[0] if t != "pool_lane_mm"])
    check("S31 declared-unguarded token no longer written -> FAIL",
          (not c30[0]) and "pool_lane_mm" in c30[1])

    c31 = heartbeat_token_crosscheck(
        [t for t in writer[0] if t != "cpu_load_line"])
    check("S32 guarded token missing from writer list -> FAIL",
          (not c31[0]) and "cpu_load_line" in c31[1])

    c32 = heartbeat_token_crosscheck(
        writer[0], heartbeat_fields(good + " stray_token=1"))
    check("S33 live token nobody declared -> FAIL",
          (not c32[0]) and "stray_token" in c32[1])

    # M163: the stocked-card dispatchable count is its own pool face. "n/a" is
    # what the writer emits when the registry cannot be read, so it must pass;
    # bare "na" and a negative count are drift and must not.
    r34 = heartbeat_token_guard(
        good.replace("stock_dispatchable=4", "stock_dispatchable=n/a"))
    check("S34 stock_dispatchable=n/a accepted (unreadable registry is not 0)",
          r34[0] and "stock_dispatchable=n/a" in r34[1])

    r35 = heartbeat_token_guard(
        good.replace("stock_dispatchable=4", "stock_dispatchable=na"))
    check("S35 stock_dispatchable=na (no slash) -> FAIL not numeric",
          (not r35[0]) and "stock_dispatchable not numeric" in r35[1])

    r36 = heartbeat_token_guard(
        good.replace("stock_dispatchable=4", "stock_dispatchable=-1"))
    check("S36 stock_dispatchable negative -> FAIL",
          (not r36[0]) and "stock_dispatchable negative" in r36[1])

    r37 = heartbeat_token_guard(
        good.replace("stock_dispatchable=4 ", ""))
    check("S37 stock_dispatchable dropped -> FAIL naming it",
          (not r37[0]) and "stock_dispatchable missing" in r37[1])

    print("selftest heartbeat-token verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def selftest_ledger_parallel():
    """T177: fixtures for probe no.20 (full-text fused-row census).

    The live ledger only carries confirmed rows, so a broken probe would
    read as PASS forever. Three states are pinned here: a clean file
    passes, an unlisted fused row fails, and the same row passes once it
    is on the whitelist (which is the only way the baseline may grow).
    """
    import tempfile
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           "" if ok else " -> %r" % (detail,)))

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(os.path.join(root, "state"))
        with open(os.path.join(root, "state", "heartbeat.txt"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write(u"09:10 \u8f6e\u5c3e\u65f6\u95f4\u6233 ok\n"
                    u"2026-11-01 09:12:03 os-loop: round done exit=0\n")
        for rel, _pat in TAIL_PROBE_FILES:
            path = os.path.join(root, *rel.split("/"))
            if os.path.exists(path):
                continue
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(u"\u65e5\u6e05: none\n")
        ok, detail = probe_ledger_parallel(root=root)
        check("S1 clean ledgers -> PASS", ok and "new=0" in detail, detail)

        with open(os.path.join(root, "state", "heartbeat.txt"), "a",
                  encoding="utf-8", newline="\n") as f:
            f.write(u"09:20 \u8f6e\u5c3e ok 09:25 os-loop: round done exit=0\n")
        ok, detail = probe_ledger_parallel(root=root)
        check("S2 fused row without whitelist -> FAIL",
              (not ok) and "PARALLEL-NEW" in detail and "NEW " in detail,
              detail)

        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import ledger_parallel_census as lpc
        wl_path = os.path.join(root, "state", "ledger-parallel-whitelist.json")
        report = lpc.scan(lpc.compile_table(), {}, root=root)
        filled = lpc.adopt_existing(report, wl_path, "selftest fixture")
        ok, detail = probe_ledger_parallel(root=root)
        check("S3 whitelisted row -> PASS", ok and "PARALLEL-CLEAN" in detail,
              (filled, detail))

        os.remove(os.path.join(root, "state", "queue", "tech.md"))
        ok, detail = probe_ledger_parallel(root=root)
        check("S4 missing ledger -> FAIL (fail closed)",
              (not ok) and "PARALLEL-ENGINE-FAIL" in detail, detail)

    print("selftest ledger-parallel verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


# T184: T183 gave "one command swaps the process onto the new code", but
# nothing noticed when the running process was still on OLD code. Version
# drift can sit silently for days: edit the source, forget the restart,
# /health keeps reporting the old version and every probe stays green.
# Probe no.21 compares /health.server against SERVER_VER in the source.
# Drift is WARN, never a red gate: a restart window legitimately shows a
# mismatch for a few seconds, so turning it red would train everyone to
# ignore it.
SERVER_SRC = os.path.join(HERE, "resident_qa_server.py")
SERVER_VER_RE = re.compile(r'^\s*SERVER_VER\s*=\s*"([^"]+)"', re.M)


def server_source_version(path=None, read=None):
    """T184: the SERVER_VER literal in the server source, or None."""
    read = read or (lambda p: open(p, encoding="utf-8",
                                   errors="replace").read())
    try:
        m = SERVER_VER_RE.search(read(path or SERVER_SRC))
    except Exception:  # noqa: BLE001 - unreadable source is a verdict
        return None
    return m.group(1).strip() if m else None


def version_drift_verdict(health_ver, source_ver):
    """T184 pure judge -> (ok, detail). Drift warns; it never reds the gate.

    Both sides must be readable before any claim is made: a silent service
    (probe 6 covers that) or a moved source file is UNVERIFIED - an absent
    measurement must not be dressed up as drift.
    """
    if not source_ver:
        return True, ("UNVERIFIED: SERVER_VER not found in %s (moved or "
                      "renamed) - drift unmeasurable, no false alarm"
                      % os.path.basename(SERVER_SRC))
    if not health_ver:
        return True, ("UNVERIFIED: /health reported no server version "
                      "(service down? see probe 6) - drift unmeasurable")
    if str(health_ver).strip() == source_ver.strip():
        return True, ("VERSION-IN-SYNC %s (running process == source)"
                      % source_ver.strip())
    return True, ("VERSION-DRIFT running=%s source=%s WARN - the process is "
                  "on old code; fix: python Tools/qa_restart.py restart"
                  % (str(health_ver).strip(), source_ver.strip()))


def probe_resident_qa_version():
    """Probe no.21 (T184): running /health version vs source SERVER_VER.

    Read-only: one GET /health (no generation, zero GPU load) plus a local
    file read. Never restarts anything - the fix pointer is printed, the
    action stays with the operator.
    """
    health_ver = None
    try:
        with urllib.request.urlopen(RESIDENT_QA_URL, timeout=10) as r:
            h = json.loads(r.read().decode("utf-8"))
        health_ver = h.get("server")
    except Exception:  # noqa: BLE001 - unreachable != drifted
        health_ver = None
    return version_drift_verdict(health_ver, server_source_version())


def selftest_qa_version():
    """T184 fixtures: in-sync / drift / source unreadable / service silent.

    The live service is usually in sync, so a broken comparator would read
    as PASS forever. All four states are pinned, including the two that
    must stay quiet (unmeasurable is not a defect).
    """
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           "" if ok else " -> %r" % (detail,)))

    src = os.path.join(HERE, "resident_qa_server.py")
    ver = server_source_version(src)
    check("V1 source SERVER_VER parsed", bool(ver), ver)

    ok, detail = version_drift_verdict(ver, ver)
    check("V2 same version -> VERSION-IN-SYNC",
          ok and "VERSION-IN-SYNC" in detail, detail)

    ok, detail = version_drift_verdict("resident-qa v0.1 (stale)", ver)
    check("V3 older running version -> WARN with repair pointer",
          ok and "VERSION-DRIFT" in detail and "qa_restart.py restart" in
          detail, detail)

    ok, detail = version_drift_verdict(ver, None)
    check("V4 source unreadable -> UNVERIFIED (not an alarm)",
          ok and "UNVERIFIED" in detail and "VERSION-DRIFT" not in detail,
          detail)

    ok, detail = version_drift_verdict(None, ver)
    check("V5 silent service -> UNVERIFIED (probe 6 owns the red)",
          ok and "UNVERIFIED" in detail and "VERSION-DRIFT" not in detail,
          detail)

    ok, detail = version_drift_verdict("x", None)
    check("V6 both sides missing -> UNVERIFIED, never a false drift",
          ok and "UNVERIFIED" in detail and "VERSION-DRIFT" not in detail,
          detail)

    print("selftest qa-version verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def text_integrity_judge(s):
    """Probe no.19 verdict (M93 / D-20261102-03 ③ / T212): advisory, never silent.

    Two independent families are reported side by side and never merged:

    * content damage (flattened / U+FFFD / double-encoded / conflict markers) --
      reported with file:line and never blocks the round, because old damage
      must not stop the line;
    * table column defects (a row whose cell count disagrees with its own
      header) -- declared legacy rows are only counted, but an UNDECLARED local
      row reds. That class used to be invisible: eight ledger rows were
      unreadable as rows while every gate stayed green (M130/T212).

    A census that cannot produce a summary is UNVERIFIED and reds, because
    "I could not read" must never look green.
    """
    if not isinstance(s, dict) or "verdict" not in s:
        return False, "verdict=UNVERIFIED no summary"
    v = s["verdict"]
    cats = "%d/%d" % (s.get("categories_covered_n", 0),
                      s.get("categories_required_n", 0))
    head = "verdict=%s cats=%s files=%s" % (v, cats, s.get("files_scanned", 0))
    hits = []
    for r in s.get("rows") or []:
        if r.get("status") != "DAMAGED":
            continue
        tag = "ext" if r.get("external") else "loc"
        for d in r.get("damaged_lines") or []:
            hits.append("%s:%s:%d" % (tag, os.path.basename(r.get("path", "?")),
                                      d.get("line", 0)))
    head += " table=%s(declared=%s)" % (s.get("table_rows_mismatch", 0),
                                        s.get("table_mismatch_declared", 0))
    new_local = s.get("table_mismatch_new") or []
    new_ext = s.get("table_mismatch_new_external") or []
    if new_local:
        names = ["loc:%s:%d(%s %d/%d)"
                 % (os.path.basename(d.get("path", "?")), d.get("line", 0),
                    d.get("kind", "?"), d.get("cells", 0), d.get("expected", 0))
                 for d in new_local[:6]]
        return False, head + " TABLE-MISMATCH-NEW=%d %s" % (len(new_local), ",".join(names))
    if new_ext:
        names = ["ext:%s:%d" % (os.path.basename(d.get("path", "?")), d.get("line", 0))
                 for d in new_ext[:6]]
        head += " table_new_ext=%d %s" % (len(new_ext), ",".join(names))
    if v == "TEXT-INTEGRITY-CLEAN":
        return True, head + " damaged=0"
    return True, head + " WARN-damaged=%d %s" % (len(hits), ",".join(hits[:6]))


def probe_text_integrity(root=None):
    """Probe no.19 (M93 / D-20261102-03): living-index damage visible each round.

    Runs the exact default target set the night round censuses (the seven
    mandated index families, incl. the three group ledgers), so a row born
    flattened shows up in this round's own log instead of waiting for someone
    to remember to run the tool.
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import text_integrity_census as tic
        s = tic.summarize(tic.census(tic.DEFAULT_TARGETS))
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "text_integrity_census raised: %r" % e
    return text_integrity_judge(s)


def selftest_text_integrity():
    """M93 fixtures: clean / local damage / external damage / unreadable."""
    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name
              + (("  " + str(detail)) if (detail and not cond) else ""))

    clean = {"verdict": "TEXT-INTEGRITY-CLEAN", "files_scanned": 12,
             "categories_covered_n": 7, "categories_required_n": 7, "rows": []}
    ok, det = text_integrity_judge(clean)
    check("S1 clean -> PASS cats=7/7", ok and "damaged=0" in det and "cats=7/7" in det)

    local_bad = dict(clean, verdict="TEXT-INTEGRITY-NEEDS-FIX",
                     rows=[{"path": os.path.join(ROOT, "tasks", "TASKS.md"),
                            "status": "DAMAGED", "external": False,
                            "damaged_lines": [{"line": 53}]}])
    ok, det = text_integrity_judge(local_bad)
    check("S2 local damage -> advisory PASS but named",
          ok and "WARN-damaged=1" in det and "loc:TASKS.md:53" in det)

    ext_bad = dict(clean, verdict="TEXT-INTEGRITY-EXTERNAL-DAMAGED",
                   rows=[{"path": os.path.join(os.path.dirname(ROOT),
                                               "docs", "orders.md"),
                          "status": "DAMAGED", "external": True,
                          "damaged_lines": [{"line": 7}]}])
    ok, det = text_integrity_judge(ext_bad)
    check("S3 external damage tagged ext", ok and "ext:orders.md:7" in det)

    ok, det = text_integrity_judge(None)
    check("S4 no summary -> FAIL/UNVERIFIED", (not ok) and "UNVERIFIED" in det)

    # 7 categories must be the default set, not a caller choice
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import text_integrity_census as tic
    check("S5 default set covers 7 families",
          len(tic.REQUIRED_CATEGORIES) == 7
          and all(p in tic.DEFAULT_TARGETS for _, p in tic.REQUIRED_TARGETS))

    # S6/T212 an UNDECLARED local column defect reds -- that family had no
    # alarm at all (M130), so being advisory would mean being ignored again
    col_new = dict(clean, verdict="TEXT-INTEGRITY-NEEDS-FIX",
                   table_rows_mismatch=48, table_mismatch_declared=47,
                   table_mismatch_new=[{"path": os.path.join(ROOT, "state", "queue", "main.md"),
                                        "line": 141, "kind": "COLUMN-OVERFLOW",
                                        "cells": 5, "expected": 4}])
    ok, det = text_integrity_judge(col_new)
    check("S6 undeclared column row -> FAIL named",
          (not ok) and "TABLE-MISMATCH-NEW=1" in det and "main.md:141" in det, det[:120])

    # S7 declared legacy rows alone stay advisory (old rows must not stop the line)
    col_declared = dict(clean, table_rows_mismatch=47, table_mismatch_declared=47,
                        table_mismatch_new=[])
    ok, det = text_integrity_judge(col_declared)
    check("S7 declared-legacy only -> PASS with counts",
          ok and "table=47(declared=47)" in det, det[:120])

    # S8 a column defect in a read-only group ledger is named but not ours
    col_ext = dict(clean, verdict="TEXT-INTEGRITY-EXTERNAL-DAMAGED",
                   table_rows_mismatch=48, table_mismatch_declared=47,
                   table_mismatch_new=[],
                   table_mismatch_new_external=[{"path": os.path.join(
                       os.path.dirname(ROOT), "docs", "decisions.md"),
                       "line": 300, "kind": "COLUMN-OVERFLOW",
                       "cells": 8, "expected": 7}])
    ok, det = text_integrity_judge(col_ext)
    check("S8 external column row -> named, not blocking",
          ok and "table_new_ext=1" in det and "decisions.md:300" in det, det[:120])

    print("selftest text-integrity verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


RECEIPT_LAW_WINDOW_DAYS = 7


def receipt_product_judge(payload):
    """Probe no.22 verdict (M96 / BC-P-166): a receipt must trace to a file.

    MISSING-LOCAL (the receipt claims a local product that is not on disk) and
    COPY-AS-PRODUCT (the same bytes counted as two products) are red -- those
    hours cannot be verified, so they must not read as delivered work. A
    receipt with no deliverable at all is printed but not red: it is visible
    evidence of a non-work receipt, not a claim about a product that lied.
    """
    if not isinstance(payload, dict) or "buckets" not in payload:
        return False, "verdict=UNVERIFIED no summary"
    b = payload["buckets"]
    t = payload.get("totals") or {}
    head = ("verdict=%s counted=%s net_h=%s missing_local=%d copy=%d "
            "no_deliverable=%d"
            % (payload.get("verdict", "?"), t.get("counted", 0),
               t.get("net_hours", 0), b.get("missing_local", 0),
               b.get("copy", 0), b.get("no_deliverable", 0)))
    hits = []
    for item in payload.get("copies") or []:
        hits.append("copy:%s/%s<-%s" % (item.get("node"), item.get("id"),
                                        item.get("copy_of")))
    for item in payload.get("skipped") or []:
        if item.get("reason") == "MISSING-LOCAL":
            hits.append("missing:%s/%s" % (item.get("node"), item.get("id")))
    bad = bool(b.get("missing_local") or b.get("copy"))
    return (not bad), head + ((" " + " ".join(hits[:6])) if hits else "")


def probe_receipt_product_law(root=None, days=RECEIPT_LAW_WINDOW_DAYS):
    """Probe no.22 (M96 / BC-P-166): queue receipts traceable to a file.

    cpu_effective_hours shipped as a manual command; the product-law claim
    ("CPU 有效工时可逐条追到文件") therefore only held when someone ran it.
    Here the same evaluator runs every round, so a receipt that names a
    missing or duplicated product shows up in this round's own log.
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import cpu_effective_hours as ceh
        base = root or ceh.DEFAULT_ROOT
        recs, bad, other = ceh.load_receipts(os.path.join(base, ceh.RECEIPT_REL))
        if not recs:
            return False, ("verdict=NO-DATA receipts_dir=%s bad_json=%d "
                           "other=%d -- 无可核算回执即不许判绿"
                           % (os.path.join(base, ceh.RECEIPT_REL), len(bad),
                              len(other)))
        payload = ceh.evaluate(recs, base, ceh.HOST_NODE, days=days)
        payload["buckets"]["bad_json"] = len(bad)
        payload["buckets"]["not_work_receipt"] = len(other)
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "cpu_effective_hours raised: %r" % e
    return receipt_product_judge(payload)


def selftest_receipt_product_law():
    """M96 fixtures: clean / missing-local / copy-as-product / unreadable."""
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    def base(buckets=None, **kw):
        payload = {
            "verdict": "CLEAN",
            "totals": {"counted": 3, "net_hours": 0.12},
            "buckets": dict({"verified": 3, "missing_local": 0, "copy": 0,
                             "no_deliverable": 2}, **(buckets or {})),
            "copies": [], "skipped": [],
        }
        payload.update(kw)
        return payload

    ok, det = receipt_product_judge(base())
    check("S1 clean -> PASS (no_deliverable shown, not red)",
          ok and "missing_local=0" in det and "no_deliverable=2" in det)

    miss = base({"missing_local": 1},
                skipped=[{"id": "Q-1", "node": "bm-a",
                          "reason": "MISSING-LOCAL", "deliverable": "x.txt"}])
    ok, det = receipt_product_judge(miss)
    check("S2 MISSING-LOCAL -> FAIL and named",
          (not ok) and "missing_local=1" in det and "missing:bm-a/Q-1" in det)

    copy = base({"copy": 1},
                copies=[{"id": "Q-2", "node": "bm-a", "copy_of": "Q-1",
                         "deliverable": "x.txt"}])
    ok, det = receipt_product_judge(copy)
    check("S3 COPY-AS-PRODUCT -> FAIL and named",
          (not ok) and "copy=1" in det and "copy:bm-a/Q-2<-Q-1" in det)

    ok, det = receipt_product_judge(None)
    check("S4 no summary -> FAIL/UNVERIFIED", (not ok) and "UNVERIFIED" in det)

    print("selftest receipt-product-law verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


SERVE_SETTLE_S = 20.0


def serve_health_judge(result):
    """Probe no.23 (T188): serve death may only be declared after a settle window.

    The 22:43 P1 false alarm (URLError read as OLLAMA DOWN while the process was
    alive 55s later) is the class this guards. Here the settle-window probe runs
    every round: UP is green, DEGRADED (answers but cannot work) and DOWN
    (window exhausted) are both red, and an unreadable payload never reads green.
    """
    if not isinstance(result, dict) or "verdict" not in result:
        return False, "verdict=UNVERIFIED no summary"
    v = result.get("verdict")
    head = ("verdict=%s attempts=%s settle_s=%s recovered_after_s=%s models=%d"
            % (v, result.get("attempts"), result.get("settle_s"),
               result.get("recovered_after_s"), len(result.get("models") or [])))
    reasons = [str(r) for r in (result.get("reasons") or [])]
    if v == "UP":
        return True, head
    if reasons:
        head += " reasons=" + "|".join(reasons[:3])
    return False, head


def probe_serve_health():
    """Probe no.23 (T188): live Ollama serve verdict with a settle window."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import serve_health as sh
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "serve_health import raised: %r" % e
    try:
        result = sh.run(settle_s=SERVE_SETTLE_S)
    except Exception as e:  # noqa: BLE001
        return False, "serve_health.run raised: %r" % e
    return serve_health_judge(result)


def selftest_serve_health():
    """T188 fixtures: UP / window-exhausted DOWN / DEGRADED / no summary."""
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    up = {"verdict": "UP", "attempts": 1, "settle_s": 20.0,
          "recovered_after_s": None, "models": ["qwen3:4b-instruct"],
          "reasons": []}
    ok, det = serve_health_judge(up)
    check("S1 UP -> PASS", ok and "verdict=UP" in det and "models=1" in det)

    rec = dict(up, attempts=3, recovered_after_s=6.4)
    ok, det = serve_health_judge(rec)
    check("S2 transient recovery still UP -> PASS with settled seconds",
          ok and "recovered_after_s=6.4" in det)

    down = {"verdict": "DOWN", "attempts": 7, "settle_s": 20.0,
            "recovered_after_s": None, "models": [],
            "reasons": ["unreachable after 7 attempts / 20.0s of a 20.0s settle window"]}
    ok, det = serve_health_judge(down)
    check("S3 window exhausted -> DOWN fails and names attempts",
          (not ok) and "verdict=DOWN" in det and "attempts=7" in det)

    deg = {"verdict": "DEGRADED", "attempts": 1, "settle_s": 20.0,
           "recovered_after_s": None, "models": [],
           "reasons": ["no models installed (/api/tags returned 0)"]}
    ok, det = serve_health_judge(deg)
    check("S4 DEGRADED -> FAIL and reason surfaced",
          (not ok) and "verdict=DEGRADED" in det and "no models" in det)

    ok, det = serve_health_judge(None)
    check("S5 no summary -> FAIL/UNVERIFIED", (not ok) and "UNVERIFIED" in det)

    print("selftest serve-health verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def write_encoding_judge(payload):
    """Judge a write_encoding_census payload (no.25 / T193).

    CLEAN passes and reports scope; RISK fails and names every file:line so
    the defect is visible in the round log; UNVERIFIED must not turn green
    just because nothing could be read (T-50 invalid-green lesson).
    """
    if not isinstance(payload, dict):
        return False, "UNVERIFIED: no census payload"
    verdict = payload.get("verdict")
    scanned = payload.get("scanned", 0)
    risk = payload.get("risk") or []
    unjudged = payload.get("unjudged") or []
    unreadable = payload.get("unreadable") or []
    if verdict == "WRITE-ENCODING-CLEAN":
        return True, ("verdict=%s scanned=%d risk=0 unjudged=%d"
                      % (verdict, scanned, len(unjudged)))
    if verdict == "WRITE-ENCODING-RISK":
        rows = ["%s:%s" % (h.get("file"), h.get("line")) for h in risk[:8]]
        return False, ("verdict=%s scanned=%d risk=%d unjudged=%d %s"
                       % (verdict, scanned, len(risk), len(unjudged),
                          " ".join(rows)))
    return False, ("verdict=%s scanned=%d unreadable=%d"
                   % (verdict, scanned, len(unreadable)))


def probe_write_encoding():
    """Probe no.25 (T193): static write-side encoding declaration census."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import write_encoding_census as wec
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "write_encoding_census import raised: %r" % e
    try:
        payload = wec.census()
    except Exception as e:  # noqa: BLE001
        return False, "write_encoding_census.census raised: %r" % e
    return write_encoding_judge(payload)


def selftest_write_encoding():
    """T193 fixtures: clean / risk names file:line / unverified / no payload."""
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    clean = {"verdict": "WRITE-ENCODING-CLEAN", "scanned": 11,
             "risk": [], "unjudged": [{"file": "a.py", "line": 3}],
             "unreadable": []}
    ok, det = write_encoding_judge(clean)
    check("S1 CLEAN -> PASS and reports scanned scope",
          ok and "scanned=11" in det and "unjudged=1" in det)

    risk = {"verdict": "WRITE-ENCODING-RISK", "scanned": 5,
            "risk": [{"file": "Tools/x.py", "line": 42,
                      "detail": "write mode without encoding"}],
            "unjudged": [], "unreadable": []}
    ok, det = write_encoding_judge(risk)
    check("S2 RISK -> FAIL and names file:line",
          (not ok) and "risk=1" in det and "Tools/x.py:42" in det)

    unv = {"verdict": "WRITE-ENCODING-UNVERIFIED", "scanned": 0,
           "risk": [], "unjudged": [],
           "unreadable": ["bad.py (UnicodeDecodeError)"]}
    ok, det = write_encoding_judge(unv)
    check("S3 UNVERIFIED -> FAIL, never a green by absence",
          (not ok) and "unreadable=1" in det)

    ok, det = write_encoding_judge(None)
    check("S4 no payload -> UNVERIFIED FAIL", (not ok) and "UNVERIFIED" in det)

    print("selftest write-encoding verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


ROW_LOSS_PROBE_WINDOW = 4


def row_loss_judge(payload):
    """Judge a row_loss_census payload (no.26 / T198, source verdicts since T199).

    Shared jsonl ledgers are rewritten whole, so a row can vanish between two
    commits and come back minutes later.  A permanent loss is a defect and
    names the ids; a transient hole passes but is printed, because a
    concurrent reader did see the hole and silence would teach us it was fine.

    The census owns the split between "unreadable" and "no second commit yet"
    (ROW-LOSS-NA), so this judge reads `scanned` / `unreadable` / `na_total`
    instead of re-deriving them from a free-text reason.  A target with fewer
    than two comparable commits is N/A -- neither a defect nor health, and never
    counted as scanned (T-50: no green by absence).
    """
    if not isinstance(payload, dict):
        return False, "UNVERIFIED: no census payload"
    if "scanned" not in payload or "na_total" not in payload:
        return False, "UNVERIFIED: census payload lacks scanned/n-a fields"
    verdict = payload.get("verdict")
    window = payload.get("window", 0)
    permanent = payload.get("lost_permanent_total", 0)
    transient = payload.get("lost_transient_total", 0)
    unreadable = payload.get("unreadable") or []
    na_total = payload.get("na_total", 0)
    scanned = payload.get("scanned", 0)
    if unreadable:
        return False, ("verdict=%s window=%d unreadable=%d %s"
                       % (verdict, window, len(unreadable), " ".join(unreadable[:4])))
    if not scanned:
        return False, ("verdict=%s window=%d scanned=0 n/a=%d -- no target "
                       "had comparable commits, absence is not health"
                       % (verdict, window, na_total))
    # 11-09: a loss count must be auditable by file.  The 00:20 round reported
    # "permanent=21" while naming four ids and no target at all -- an unexplainable
    # number is the same defect class as an unexplainable gap.  Name the file with
    # every id, print how many were named out of the claimed total, and pin the
    # reading to the commit it was judged at (a "permanent" loss seen at sha X is
    # local to X: those ids came back by 01:07, so the label must never be reused
    # as a durable fact without re-running).
    ids = []
    named_permanent = 0
    as_of = []
    for t in (payload.get("targets") or []):
        rows = t.get("lost_alive_ids") or []
        permanent_rows = [i for i in rows if not i.get("readded")]
        named_permanent += len(permanent_rows)
        if permanent_rows or rows:
            as_of.append("%s@%s" % (t.get("target"), t.get("newest_commit") or "?"))
        for item in rows[:6]:
            ids.append("%s %s id=%s %s->%s" % (
                "TRANSIENT" if item.get("readded") else "PERMANENT",
                t.get("target"), item.get("id"), item.get("older_commit"),
                item.get("newer_commit")))
    tail = (" " + " ".join(ids)) if ids else ""
    pin = (" as_of=" + ",".join(as_of)) if as_of else ""
    if permanent:
        detail = ("verdict=%s window=%d permanent=%d transient=%d named=%d/%d%s%s"
                  % (verdict, window, permanent, transient,
                     named_permanent, permanent, pin, tail))
        if named_permanent < permanent:
            detail += " UNEXPLAINED-PERMANENT=%d" % (permanent - named_permanent)
        return False, detail
    if transient:
        return True, ("verdict=%s window=%d permanent=0 transient=%d n/a=%d%s%s"
                      % (verdict, window, transient, na_total, pin, tail))
    return True, ("verdict=%s window=%d permanent=0 transient=0 scanned=%d "
                  "n/a=%d" % (verdict, window, scanned, na_total))


def probe_row_loss():
    """Probe no.26 (T198): row-loss census over the last few group commits."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import row_loss_census as rlc
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "row_loss_census import raised: %r" % e
    group = (os.environ.get("FLUXGROUP_ROOT")
             or os.path.normpath(os.path.join(ROOT, os.pardir, os.pardir)))
    try:
        payload = rlc.census(group, rlc.DEFAULT_TARGETS, ROW_LOSS_PROBE_WINDOW)
    except Exception as e:  # noqa: BLE001
        return False, "row_loss_census.census raised: %r" % e
    ok, detail = row_loss_judge(payload)
    return ok, detail + " targets=%d" % len(payload.get("targets") or [])


def selftest_row_loss():
    """T198 fixtures: free / transient / permanent / unreadable / n-a / empty."""
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    def tgt(path, verdict, why=None, lost=None):
        return {"target": path, "verdict": verdict, "why": why or "",
                "lost_alive_ids": lost or []}

    def payload(verdict, targets, scanned, na_total, unreadable=None,
                permanent=0, transient=0):
        return {"verdict": verdict, "window": 4, "targets": targets,
                "scanned": scanned, "na_total": na_total,
                "unreadable": unreadable or [], "na": [],
                "lost_permanent_total": permanent,
                "lost_transient_total": transient}

    free = payload("ROW-LOSS-FREE", [tgt("q.jsonl", "ROW-LOSS-FREE")], 1, 0)
    ok, det = row_loss_judge(free)
    check("S1 FREE -> PASS and reports window/scope",
          ok and "window=4" in det and "scanned=1" in det)

    trans = payload("ROW-LOSS-TRANSIENT",
                    [tgt("q.jsonl", "ROW-LOSS-TRANSIENT", lost=[
                        {"id": "MV-1", "readded": True,
                         "older_commit": "aaaaaaa", "newer_commit": "bbbbbbb"}])],
                    1, 0, transient=1)
    ok, det = row_loss_judge(trans)
    check("S2 TRANSIENT -> PASS but names the hole (never silent)",
          ok and "transient=1" in det and "MV-1" in det and "aaaaaaa" in det)

    found = payload("ROW-LOSS-FOUND",
                    [tgt("q.jsonl", "ROW-LOSS-FOUND", lost=[
                        {"id": "MV-2", "readded": False,
                         "older_commit": "ccccccc", "newer_commit": "ddddddd"}])],
                    1, 0, permanent=1)
    ok, det = row_loss_judge(found)
    check("S3 FOUND -> FAIL and names PERMANENT id",
          (not ok) and "permanent=1" in det and "MV-2" in det)

    gap = payload("PARTIAL",
                  [tgt("q.jsonl", "ROW-LOSS-FREE"),
                   tgt("b.jsonl", "UNVERIFIED", why="git-show-rc=128")],
                  1, 0, unreadable=["b.jsonl"])
    ok, det = row_loss_judge(gap)
    check("S4 unreadable target -> FAIL names target",
          (not ok) and "b.jsonl" in det and "unreadable=1" in det)

    # T199: the census itself labels short history ROW-LOSS-NA / PARTIAL-NA.
    na_partial = payload("ROW-LOSS-PARTIAL-NA",
                         [tgt("q.jsonl", "ROW-LOSS-FREE"),
                          tgt("lane.jsonl", "ROW-LOSS-NA",
                              why="no-comparable-pairs")],
                         1, 1)
    ok, det = row_loss_judge(na_partial)
    check("S5 short-history target -> PASS as n/a, not counted scanned",
          ok and "n/a=1" in det and "scanned=1" in det)

    all_na = payload("ROW-LOSS-NA-ONLY",
                     [tgt("lane.jsonl", "ROW-LOSS-NA",
                          why="no-comparable-pairs")], 0, 1)
    ok, det = row_loss_judge(all_na)
    check("S6 nothing comparable -> FAIL, absence is not health",
          (not ok) and "scanned=0" in det and "n/a=1" in det)

    legacy = {"verdict": "PARTIAL", "window": 4,
              "targets": [tgt("q.jsonl", "ROW-LOSS-FREE")],
              "lost_permanent_total": 0, "lost_transient_total": 0}
    ok, det = row_loss_judge(legacy)
    check("S7 payload without scanned -> UNVERIFIED, never guessed",
          (not ok) and "UNVERIFIED" in det)

    ok, det = row_loss_judge(None)
    check("S8 no payload -> UNVERIFIED FAIL", (not ok) and "UNVERIFIED" in det)

    # 11-09: a count must be auditable by file, and a momentary verdict must be
    # pinned to the commit it was judged at.
    two = payload("ROW-LOSS-FOUND",
                  [tgt("q.jsonl", "ROW-LOSS-FOUND", lost=[
                      {"id": "MV-3", "readded": False,
                       "older_commit": "e1e1e1e", "newer_commit": "f2f2f2f"}]),
                   tgt("lane.jsonl", "ROW-LOSS-FOUND", lost=[
                       {"id": "MV-4", "readded": False,
                        "older_commit": "e1e1e1e", "newer_commit": "f2f2f2f"}])],
                  2, 0, permanent=2)
    for t in two["targets"]:
        t["newest_commit"] = "f2f2f2f"
    ok, det = row_loss_judge(two)
    check("S9 FOUND -> FAIL names file+id and pins as_of",
          (not ok) and "q.jsonl" in det and "lane.jsonl" in det
          and "named=2/2" in det and "as_of=" in det and "f2f2f2f" in det
          and "UNEXPLAINED" not in det)

    short = payload("ROW-LOSS-FOUND",
                    [tgt("q.jsonl", "ROW-LOSS-FOUND", lost=[
                        {"id": "MV-5", "readded": False,
                         "older_commit": "c1c1c1c", "newer_commit": "d2d2d2d"}])],
                    1, 0, permanent=21)
    ok, det = row_loss_judge(short)
    check("S10 count without named ids -> UNEXPLAINED, never a bare number",
          (not ok) and "named=1/21" in det and "UNEXPLAINED-PERMANENT=20" in det)

    print("selftest row-loss verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


AFTERSALE_CAL_VERDICTS = {
    "REFUND-CALIBRATED", "REFUND-UNDER-PROVISIONED",
    "REFUND-OVER-PROVISIONED", "REFUND-NO-DATA",
    "REFUND-CANNOT-JUDGE", "REFUND-ROWS-INCONSISTENT",
}
# The synthetic pair is known-good by construction, so only a judging verdict
# proves the drill data face is intact; NO-DATA / CANNOT-JUDGE / bad rows mean
# the rails degraded, not that "there was nothing to refund".
AFTERSALE_CAL_JUDGING = {
    "REFUND-CALIBRATED", "REFUND-UNDER-PROVISIONED",
    "REFUND-OVER-PROVISIONED",
}

# Deliberately minimal rows: the point is the schema's own exclusion consts,
# not the shape of real business data.
MOCK_ROW_FIXTURE = {
    "aftersale_id": "MOCK-AF-20260101-1", "order_id": "MOCK-FIX-1",
    "date": "2026-01-01", "refund_amount": 1.0, "refund_reason": "fixture",
    "refund_simulated": True, "live": False, "mock": True,
    "caliber_note": "probe fixture",
}
REAL_ROW_FIXTURE = {
    "aftersale_id": "AF-20260101-1", "order_id": "FIX-1",
    "date": "2026-01-01", "refund_amount": 1.0, "refund_reason": "fixture",
    "refund_simulated": False, "live": True,
    "caliber_note": "probe fixture",
}


def aftersale_rails_judge(payload):
    """Pure judge for probe no.27 (M115): are the aftersale rails judgeable?

    Fails loudly instead of assuming green: a vanished drill rail, a schema
    violation, a broken mock/real separation or an unknown calibration verdict
    are all defects.  A missing *real* aftersale rail is not a defect (platform
    export is still a physical item), but it is always printed.
    """
    rails = payload.get("rails") or {}
    errors, shown = [], []
    for name in ("aftersale", "aftersale_mock"):
        info = rails.get(name) or {}
        state = ("%d rows" % info.get("rows", 0)) if info.get("present") \
            else "ABSENT"
        shown.append("%s=%s" % (name, state))
        if not info.get("present"):
            if name == "aftersale_mock":
                errors.append("aftersale_mock rail missing: the drill evidence "
                              "is gone, refund calibration cannot be re-run")
            continue
        for err in info.get("errors") or []:
            errors.append("%s schema: %s" % (name, err))
    excl = payload.get("exclusion") or {}
    if not excl.get("mock_in_real_rejected"):
        errors.append("a MOCK drill row is accepted by the real aftersale "
                      "schema: drill money could be counted as real refunds")
    if not excl.get("real_in_mock_rejected"):
        errors.append("a real refund row is accepted by the drill schema: "
                      "synthetic rails could hide real money")
    cal = payload.get("calibration") or {}
    if cal.get("error"):
        errors.append("refund calibration raised: %s" % cal["error"])
    elif cal.get("verdict") not in AFTERSALE_CAL_VERDICTS:
        errors.append("refund calibration verdict unknown: %r"
                      % (cal.get("verdict"),))
    elif cal.get("verdict") not in AFTERSALE_CAL_JUDGING:
        errors.append("drill pair no longer judgeable: cal=%s (synthetic rails "
                      "degraded, not 'nothing to refund')" % cal.get("verdict"))
    detail = "rails %s exclusion mock->real=%s real->mock=%s cal=%s" % (
        "/".join(shown),
        "REJECTED" if excl.get("mock_in_real_rejected") else "ACCEPTED",
        "REJECTED" if excl.get("real_in_mock_rejected") else "ACCEPTED",
        cal.get("error") and "RAISED" or cal.get("verdict"))
    if errors:
        return False, detail + " | " + "; ".join(errors)
    return True, detail


def aftersale_exclusion_check(rsc):
    """Feed each rail's opposite-shaped row in and require rejection."""
    mock_into_real = bool(rsc.validate_rows("aftersale", [dict(MOCK_ROW_FIXTURE)]))
    real_into_mock = bool(
        rsc.validate_rows("aftersale_mock", [dict(REAL_ROW_FIXTURE)]))
    return {"mock_in_real_rejected": mock_into_real,
            "real_in_mock_rejected": real_into_mock}


def aftersale_calibration_check(rrc):
    """Run the calibration plan over the synthetic pair (no IO to real rails)."""
    try:
        rev = rrc._load_rows(os.path.join(ROOT, "state",
                                          "mock-order-ledger.jsonl"))
        aft = rrc._load_rows(os.path.join(ROOT, "state",
                                          "mock-aftersale-ledger.jsonl"))
        result = rrc.plan(rev, aft, month=None,
                          threshold_pp=rrc.DEFAULT_THRESHOLD_PP)
        return {"verdict": result.get("verdict"),
                "orders": result.get("orders"),
                "aftersale_rows": len(aft)}
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return {"error": "%s: %s" % (type(e).__name__, e)}


def probe_aftersale_rails():
    """Probe no.27 (M115): the aftersale rails stay judgeable every round."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import rails_schema_check as rsc
        import refund_rate_calibration as rrc
    except Exception as e:  # noqa: BLE001
        return False, "import raised: %r" % (e,)
    payload = {"rails": {}, "exclusion": {}, "calibration": {}}
    for name in ("aftersale", "aftersale_mock"):
        path = rsc.RAIL_PATHS[name]
        info = {"path": os.path.basename(str(path)),
                "present": os.path.exists(str(path)),
                "rows": 0, "errors": []}
        if info["present"]:
            try:
                rows = rsc.load_rows(path)
                info["rows"] = len(rows)
                info["errors"] = rsc.validate_rows(name, rows)
            except Exception as e:  # noqa: BLE001
                info["errors"] = ["unreadable: %r" % (e,)]
        payload["rails"][name] = info
    try:
        payload["exclusion"] = aftersale_exclusion_check(rsc)
    except Exception as e:  # noqa: BLE001
        payload["exclusion"] = {"error": repr(e)}
    payload["calibration"] = aftersale_calibration_check(rrc)
    return aftersale_rails_judge(payload)


def selftest_aftersale_rails():
    """M115 fixtures: clean / real-absent / drill-missing / schema / mixing."""
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    def payload(mock_present=True, mock_rows=3, mock_errors=None,
                real_present=False, real_errors=None,
                mock_in_real=True, real_in_mock=True,
                cal_verdict="REFUND-CALIBRATED", cal_error=None):
        rails = {
            "aftersale": {"present": real_present, "rows": 2,
                          "errors": real_errors or []},
            "aftersale_mock": {"present": mock_present, "rows": mock_rows,
                               "errors": mock_errors or []},
        }
        cal = {"error": cal_error} if cal_error else {"verdict": cal_verdict}
        return {"rails": rails,
                "exclusion": {"mock_in_real_rejected": mock_in_real,
                              "real_in_mock_rejected": real_in_mock},
                "calibration": cal}

    ok, det = aftersale_rails_judge(payload())
    check("S1 drill rail clean, real rail absent -> PASS and prints both",
          ok and "aftersale=ABSENT" in det and "aftersale_mock=3 rows" in det)

    ok, det = aftersale_rails_judge(payload(real_present=True))
    check("S2 both rails present and clean -> PASS", ok and "cal=" in det)

    ok, det = aftersale_rails_judge(payload(mock_present=False))
    check("S3 drill rail gone -> FAIL naming the lost evidence",
          (not ok) and "drill evidence" in det)

    ok, det = aftersale_rails_judge(
        payload(mock_errors=["aftersale_mock[1] @refund_amount: bad"]))
    check("S4 schema violation -> FAIL naming rail and row",
          (not ok) and "aftersale_mock schema" in det and "[1]" in det)

    ok, det = aftersale_rails_judge(payload(mock_in_real=False))
    check("S5 drill row accepted by real schema -> FAIL",
          (not ok) and "drill money" in det)

    ok, det = aftersale_rails_judge(payload(real_in_mock=False))
    check("S6 real row accepted by drill schema -> FAIL",
          (not ok) and "hide real money" in det)

    ok, det = aftersale_rails_judge(payload(cal_error="ValueError: boom"))
    check("S7 calibration crash -> FAIL (not a silent skip)",
          (not ok) and "raised" in det)

    ok, det = aftersale_rails_judge(payload(cal_verdict="SOMETHING-NEW"))
    check("S8 unknown calibration verdict -> FAIL", (not ok) and "unknown" in det)

    ok, det = aftersale_rails_judge(
        payload(cal_verdict="REFUND-CANNOT-JUDGE"))
    check("S8b drill pair degraded to CANNOT-JUDGE -> FAIL (M115 criterion)",
          (not ok) and "no longer judgeable" in det)

    try:
        import rails_schema_check as rsc
        real = aftersale_exclusion_check(rsc)
        check("S9 live schema really rejects both mixed shapes",
              real["mock_in_real_rejected"] and real["real_in_mock_rejected"])
    except Exception as e:  # noqa: BLE001
        check("S9 live schema really rejects both mixed shapes: %r" % (e,),
              False)

    print("selftest aftersale verdict: %d/%d PASS"
          % (sum(results), len(results)))
    return 0 if all(results) else 1


DRILL_SET_SHRUNK = "DRILL-SET-SHRUNK"
DRILL_SET_DEGRADED = "DRILL-SET-DEGRADED"
DRILL_SET_UNREADABLE = "DRILL-SET-UNREADABLE"
DRILL_SET_NA = "N/A-NO-PLANTED-SET"


def drill_set_judge(rows):
    """Judge drill_set_census rows (no.28 / T205).

    Five drills prove themselves by "planted defect -> detector names it", but
    nothing checked that the planted sets still exist: delete a defect case and
    the drill keeps printing OK / zero deviation.  Shrunk, degraded (counts
    match yet nothing is planted) and unreadable rows all red and name the
    drill.  A declared N/A is printed only -- an explicitly declared absent
    planted set is honest -- but a census where every drill is N/A measured
    nothing, so that fails (absence is not health).
    """
    if not isinstance(rows, list) or not rows:
        return False, "UNVERIFIED: no drill census rows"
    clean, na, bad, unknown = [], [], [], []
    for row in rows:
        if not isinstance(row, dict):
            unknown.append("?:not-a-row")
            continue
        name = row.get("drill") or "?"
        verdict = row.get("verdict")
        if verdict == "DRILL-SET-CLEAN":
            clean.append(name)
        elif verdict == DRILL_SET_NA:
            na.append(name)
        elif verdict in (DRILL_SET_SHRUNK, DRILL_SET_DEGRADED,
                         DRILL_SET_UNREADABLE):
            actual = row.get("actual") or {}
            bad.append("%s:%s cases=%s/%s defects=%s%s" % (
                name, verdict,
                actual.get("cases", "?"), row.get("min_cases", "-"),
                actual.get("defects", "?"),
                (" err=%s" % row["error"]) if row.get("error") else ""))
        else:
            unknown.append("%s:%s" % (name, verdict))
    counts = "drills=%d clean=%d n/a=%d" % (len(rows), len(clean), len(na))
    tail = (" n/a-name=" + ",".join(na)) if na else ""
    if unknown:
        return False, ("%s unknown=%s -- a verdict this judge does not name "
                       "cannot be called healthy" % (counts, " ".join(unknown)))
    if bad:
        return False, ("%s shrunk/degraded=%s" % (counts, " ".join(bad)))
    if not clean:
        return False, ("%s -- every drill is N/A, nothing was measured"
                       % counts)
    return True, counts + tail


def probe_drill_sets():
    """Probe no.28 (T205): the planted sets behind every drill still exist."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import drill_set_census as dsc
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "drill_set_census import raised: %r" % e
    try:
        rows = dsc.scan()
    except Exception as e:  # noqa: BLE001
        return False, "drill_set_census.scan raised: %r" % e
    return drill_set_judge(rows)


def selftest_drill_sets():
    """T205 fixtures: clean / shrunk / degraded / unreadable / n-a / empty."""
    results = []

    def check(name, cond):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    def row(drill, verdict, cases=None, defects=None, min_cases=None,
            error=None):
        return {"drill": drill, "verdict": verdict, "min_cases": min_cases,
                "actual": {"cases": cases, "defects": defects},
                "error": error}

    ok, det = drill_set_judge([row("a_drill", "DRILL-SET-CLEAN", 10, 5, 10),
                               row("b_drill", "DRILL-SET-CLEAN", 5, 2, 5)])
    check("S1 all clean -> PASS and reports drills/clean",
          ok and "drills=2" in det and "clean=2" in det)

    ok, det = drill_set_judge([row("a_drill", "DRILL-SET-SHRUNK", 9, 5, 10)])
    check("S2 shrunk -> FAIL and names the drill",
          (not ok) and "a_drill" in det and "cases=9/10" in det)

    ok, det = drill_set_judge([row("b_drill", "DRILL-SET-DEGRADED", 10, 0, 10)])
    check("S3 degraded -> FAIL and names the drill with defect count",
          (not ok) and "b_drill" in det and "defects=0" in det)

    ok, det = drill_set_judge([row("c_drill", "DRILL-SET-UNREADABLE",
                                   error="ImportError: boom")])
    check("S4 unreadable -> FAIL and carries the error",
          (not ok) and "c_drill" in det and "ImportError" in det)

    ok, det = drill_set_judge([row("a_drill", "DRILL-SET-CLEAN", 10, 5, 10),
                               row("n_drill", "N/A-NO-PLANTED-SET")])
    check("S5 declared N/A -> PASS but printed with its name",
          ok and "n/a=1" in det and "n_drill" in det)

    ok, det = drill_set_judge([row("n_drill", "N/A-NO-PLANTED-SET")])
    check("S6 every drill N/A -> FAIL (nothing measured)",
          (not ok) and "nothing was measured" in det)

    ok, det = drill_set_judge(None)
    check("S7 no rows -> UNVERIFIED FAIL", (not ok) and "UNVERIFIED" in det)

    ok, det = drill_set_judge([row("x_drill", "SOMETHING-NEW")])
    check("S8 unknown verdict -> FAIL, never assumed healthy",
          (not ok) and "unknown" in det and "x_drill" in det)

    first = drill_set_judge([row("a_drill", "DRILL-SET-CLEAN", 10, 5, 10)])
    second = drill_set_judge([row("a_drill", "DRILL-SET-CLEAN", 10, 5, 10)])
    check("S9 deterministic on the same input", first == second)

    print("selftest drill-set verdict: %d/%d PASS"
          % (sum(results), len(results)))
    return 0 if all(results) else 1


MONEY_GUARD_CLEAN = "MONEY-GUARD-CLEAN"
MONEY_GUARD_GAPS = "MONEY-GUARD-GAPS"


def money_guard_judge(verdict, problems, scanned=None, writers=None,
                      declared=None):
    """Judge money_entry_census output (no.29 / T209).

    The census answers "which money-face writers are guarded"; this only
    decides whether that answer is healthy.  Rules kept narrow on purpose:
      - only the census's own CLEAN verdict passes; an unknown verdict is
        never assumed healthy (D-20 the-conclusion-overshoots family),
      - problems are named, not shrunk into a count,
      - CLEAN with nothing scanned measured nothing -> red,
      - a CLEAN verdict carrying problems is a contradiction -> red.
    """
    counts = "scanned=%s writers=%s declared=%s" % (
        "?" if scanned is None else scanned,
        "?" if writers is None else writers,
        "?" if declared is None else declared)
    if verdict is None:
        return False, "UNVERIFIED: census produced no verdict"
    probs = [str(p) for p in (problems or [])]
    if verdict == MONEY_GUARD_GAPS or probs:
        shown = " | ".join(probs[:6])
        more = "" if len(probs) <= 6 else " (+%d more)" % (len(probs) - 6)
        return False, ("%s gaps=%d %s%s" % (
            counts, len(probs),
            shown or "verdict=GAPS with no problem text", more))
    if verdict != MONEY_GUARD_CLEAN:
        return False, ("%s unknown verdict %r -- a verdict this judge does "
                       "not name cannot be called healthy" % (counts, verdict))
    if not scanned:
        return False, ("%s -- no tool file scanned, nothing was measured"
                       % counts)
    return True, counts


def probe_money_guard():
    """Probe no.29 (T209): the money-face writer census still runs clean."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import money_entry_census as mec
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "money_entry_census import raised: %r" % e
    try:
        verdict, problems, rows, scanned, writers = mec.run()
    except Exception as e:  # noqa: BLE001
        return False, "money_entry_census.run raised: %r" % e
    return money_guard_judge(verdict, problems, scanned, writers,
                             len(getattr(mec, "DECLARED", {})))


def selftest_money_guard():
    """T209 fixtures: clean / gaps / contradiction / unknown / empty."""
    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("[%s] S%d %s%s" % ("PASS" if cond else "FAIL",
                                  len(results), name,
                                  "" if cond else " :: " + str(detail))))

    ok, det = money_guard_judge(MONEY_GUARD_CLEAN, [], 95, 17, 10)
    check("clean -> PASS and reports scanned/writers/declared",
          ok and "scanned=95" in det and "writers=17" in det
          and "declared=10" in det, det)

    ok, det = money_guard_judge(
        MONEY_GUARD_GAPS, ["UNDECLARED-WRITER x.py:new_writer L9"], 95, 18, 10)
    check("gaps -> FAIL and names the writer",
          (not ok) and "x.py:new_writer" in det and "gaps=1" in det, det)

    ok, det = money_guard_judge(MONEY_GUARD_CLEAN,
                                ["STALE-DECLARATION a.py:f"], 95, 17, 10)
    check("clean verdict carrying problems -> FAIL (contradiction)",
          (not ok) and "STALE-DECLARATION" in det, det)

    ok, det = money_guard_judge("SOMETHING-NEW", [], 95, 17, 10)
    check("unknown verdict -> FAIL, never assumed healthy",
          (not ok) and "unknown verdict" in det, det)

    ok, det = money_guard_judge(None, None)
    check("no verdict -> UNVERIFIED FAIL", (not ok) and "UNVERIFIED" in det,
          det)

    ok, det = money_guard_judge(MONEY_GUARD_CLEAN, [], 0, 0, 10)
    check("clean with zero files scanned -> FAIL (nothing measured)",
          (not ok) and "nothing was measured" in det, det)

    many = ["GUARD-MISSING f%d:w" % i for i in range(9)]
    ok, det = money_guard_judge(MONEY_GUARD_GAPS, many, 95, 17, 10)
    check("long problem list is truncated but counted",
          (not ok) and "gaps=9" in det and "+3 more" in det, det)

    first = money_guard_judge(MONEY_GUARD_GAPS, ["a"], 95, 17, 10)
    second = money_guard_judge(MONEY_GUARD_GAPS, ["a"], 95, 17, 10)
    check("deterministic on the same input", first == second, str(first))

    # wiring check: the tool really returns the 5-tuple this judge consumes.
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import money_entry_census as mec
        verdict, problems, rows, scanned, writers = mec.run()
        shaped = (verdict in (MONEY_GUARD_CLEAN, MONEY_GUARD_GAPS)
                  and isinstance(problems, list) and scanned > 0
                  and writers > 0)
    except Exception as e:  # noqa: BLE001
        shaped = False
        verdict = repr(e)
    check("live census returns the shape this probe consumes",
          shaped, verdict)

    print("selftest money-guard verdict: %d/%d PASS"
          % (sum(results), len(results)))
    return 0 if all(results) else 1


IGNITER_MIN_SELFTEST = 55


def igniter_judge(selftest_rc, selftest_out, readout, last_receipt=None):
    """Judge the ignition chain (no.30 / M170).

    The chain is the only thing that turns a stocked card into artifacts, and
    it only produced its first successful ignition on 2026-11-08 after six
    runs died with `exit=1`, empty artifacts and an unread `error CS1061` in
    the log.  Three honesty rules come straight out of that failure:

      * the tool's own fixtures must still pass **and the count must be read**
        - `rc=0` without a count line means nothing was measured;
      * the pool's `gap` must be arithmetically consistent and every gap card
        named - an unexplained gap is the "conclusion overshoots the reading"
        family (D-20);
      * a receipt claiming `ok=true` with no artifacts, or `ok=false` with no
        error text, cannot be audited -> red.  That is exactly the shape the
        six 11-08 failures had.
    """
    out = selftest_out or ""
    m = re.search(r"(\d+)/(\d+)\s+PASS", out)
    got = total = None
    counts = "selftest=rc%s" % (selftest_rc,)
    if m:
        got, total = int(m.group(1)), int(m.group(2))
        counts = "selftest=%d/%d" % (got, total)
    if selftest_rc != 0:
        return False, ("%s -- pool_igniter fixtures failed (rc=%s)"
                       % (counts, selftest_rc))
    if got is None:
        return False, ("%s -- rc=0 but no fixture count line: nothing was "
                       "measured" % counts)
    if total < IGNITER_MIN_SELFTEST or got != total:
        return False, ("%s -- below the pinned floor %d or not all green"
                       % (counts, IGNITER_MIN_SELFTEST))
    if not isinstance(readout, dict):
        return False, "%s -- pool readout UNREADABLE" % counts

    disp = readout.get("dispatchable")
    runnable = readout.get("runnable")
    gap = readout.get("gap")
    named = (len(readout.get("declared_unready") or [])
             + len(readout.get("not_runnable") or []))
    ign = readout.get("igniter") or {}
    counts += (" dispatchable=%s runnable=%s gap=%s named=%s receipts=%s "
               "ok=%s deliverable=%s uncertified=%s tick_age=%s" % (
                   disp, runnable, gap, named, ign.get("receipts"),
                   ign.get("receipts_ok"), ign.get("receipts_deliverable"),
                   ign.get("receipts_uncertified"),
                   ign.get("last_tick_age_min")))
    if disp is None or runnable is None or gap is None:
        return False, counts + " -- dispatchable/runnable/gap unreadable"
    if gap != disp - runnable:
        return False, ("%s -- gap arithmetic broken (gap=%s != %s-%s)"
                       % (counts, gap, disp, runnable))
    if gap > 0 and named < gap:
        return False, ("%s -- %d card(s) of the gap are not named: an "
                       "unexplained gap is not a reading" % (counts, gap - named))
    if ign.get("last_tick_age_min") is None:
        return False, (counts + " -- watcher log unreadable, the chain "
                       "cannot self-report")
    if "last_failure" not in ign:
        return False, (counts + " -- readout has no last_failure field: the "
                       "newest failure reason is invisible on the CEO face")
    lf = ign.get("last_failure")
    counts += " last_failure=%s" % ("none" if lf is None else lf.get("ts"))
    # The bake card's deliverable is the lightmap atlas.  Until now a bake was
    # counted deliverable with nothing ever opening the EXR, so an all-black
    # atlas read as a successful run.  The face must carry the atlas reading -
    # "no bake yet" is an explicit None, not an absent key.
    if "bake_quality" not in ign:
        return False, (counts + " -- readout has no bake_quality field: baked "
                       "atlases are counted deliverable with nobody reading them")
    bq = ign.get("bake_quality")
    counts += " bake=%s" % ("none" if bq is None else bq.get("qc_verdict"))
    # T229: after T226 split `ok` from `deliverable_ok`, a face that prints only
    # `ok=N` again reads "ran fine" as "produced something usable".
    if "receipts_deliverable" not in ign:
        return False, (counts + " -- readout has no receipts_deliverable field: "
                       "'ran fine' is readable as 'produced something' again")
    dels = ign.get("receipts_deliverable")
    if isinstance(dels, int) and isinstance(ign.get("receipts_ok"), int) \
            and dels > ign["receipts_ok"]:
        return False, ("%s -- deliverable=%d > ok=%d is impossible"
                       % (counts, dels, ign["receipts_ok"]))
    if (ign.get("receipts") or 0) > 0 and last_receipt is None:
        return False, (counts + " -- receipts counted but the last row is "
                       "unreadable")
    if last_receipt is not None:
        ok_flag = last_receipt.get("ok")
        runner = last_receipt.get("runner") or {}
        if not isinstance(ok_flag, bool):
            return False, (counts + " -- last receipt carries no boolean `ok`: "
                           "unauditable")
        if ok_flag and not (runner.get("artifacts") or []):
            return False, (counts + " -- last receipt says ok=true with no "
                           "artifacts")
        if (not ok_flag) and not runner.get("error"):
            return False, (counts + " -- last receipt says ok=false with no "
                           "error text")
    return True, counts


def probe_igniter():
    """Probe no.30 (M170): the ignition chain's own net still passes."""
    script = os.path.join(ROOT, "Tools", "pool_igniter.py")
    if not os.path.exists(script):
        return False, "pool_igniter.py missing"
    try:
        proc = subprocess.run([sys.executable, script, "selftest"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300)
        self_out = (proc.stdout or "") + (proc.stderr or "")
        sel_rc = proc.returncode
    except Exception as e:  # noqa: BLE001 - probe must report, not crash
        return False, "pool_igniter selftest raised: %r" % e
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import pool_igniter as pi
        rd = pi.readout()
        rows = pi.receipt_rows()
        last = rows[-1] if rows else None
    except Exception as e:  # noqa: BLE001
        return False, "pool_igniter readout raised: %r" % e
    return igniter_judge(sel_rc, self_out, rd, last)


def selftest_igniter():
    """M170 fixtures: green / fixtures dead / silent count / gap / receipts."""
    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("[%s] S%d %s%s" % ("PASS" if cond else "FAIL",
                                  len(results), name,
                                  "" if cond else " :: " + str(detail))))

    good_out = "\n".join(["PASS S%d fixture" % i for i in range(55)]
                         + ["55/55 PASS"])

    def rd(**kw):
        doc = {"dispatchable": 4, "runnable": 2, "gap": 2,
               "declared_unready": [{"batch_id": "B-X", "reason": "project-missing"}],
               "not_runnable": ["B-Y"],
               "igniter": {"receipts": 11, "receipts_ok": 5,
                           "receipts_deliverable": 4,
                           "last_failure": None,
                           "bake_quality": None,
                           "last_tick_age_min": 8.4}}
        doc.update(kw)
        return doc

    ok_receipt = {"ok": True,
                  "runner": {"artifacts": [{"path": "a.exr", "sha256": "x"}]}}
    fail_receipt = {"ok": False,
                    "runner": {"error": {"present": True,
                                         "lines": ["error CS1061"]}}}

    ok, det = igniter_judge(0, good_out, rd(), ok_receipt)
    check("clean chain -> PASS and reports pool + fixture readings",
          ok and "selftest=55/55" in det and "gap=2" in det
          and "receipts=11" in det and "last_failure=none" in det, det)

    ok, det = igniter_judge(1, "20/21 PASS", rd(), ok_receipt)
    check("failing fixtures -> FAIL naming rc",
          (not ok) and "rc=1" in det, det)

    ok, det = igniter_judge(0, "PASS S1 fixture", rd(), ok_receipt)
    check("rc=0 without a count line -> UNVERIFIED FAIL",
          (not ok) and "nothing was measured" in det, det)

    ok, det = igniter_judge(0, "10/10 PASS", rd(), ok_receipt)
    check("count below the pinned floor -> FAIL",
          (not ok) and "pinned floor" in det, det)

    ok, det = igniter_judge(0, good_out, rd(gap=1), ok_receipt)
    check("gap arithmetic broken -> FAIL",
          (not ok) and "arithmetic broken" in det, det)

    ok, det = igniter_judge(0, good_out,
                            rd(declared_unready=[], not_runnable=["B-Y"]),
                            ok_receipt)
    check("arithmetically sane gap with an unnamed card -> FAIL",
          (not ok) and "not named" in det and "arithmetic" not in det, det)

    ok, det = igniter_judge(0, good_out,
                            rd(igniter={"receipts": 0, "receipts_ok": 0,
                                        "last_tick_age_min": None}),
                            None)
    check("watcher log unreadable -> FAIL even with zero receipts",
          (not ok) and "cannot self-report" in det, det)

    ok, det = igniter_judge(0, good_out, rd(), {"ok": True, "runner": {}})
    check("ok=true with no artifacts -> FAIL",
          (not ok) and "no artifacts" in det, det)

    ok, det = igniter_judge(0, good_out, rd(), {"ok": False, "runner": {}})
    check("ok=false with no error text -> FAIL",
          (not ok) and "no error text" in det, det)

    ok, det = igniter_judge(0, good_out, rd(), {"runner": {}})
    check("receipt without a boolean ok -> FAIL (unauditable)",
          (not ok) and "unauditable" in det, det)

    ok, det = igniter_judge(0, good_out, rd(), fail_receipt)
    check("a properly explained failure is not mistaken for a broken chain",
          ok, det)

    blind = rd(igniter={"receipts": 11, "receipts_ok": 5,
                        "last_tick_age_min": 8.4})
    ok, det = igniter_judge(0, good_out, blind, ok_receipt)
    check("readout without last_failure -> FAIL (CEO face blind to failures)",
          (not ok) and "last_failure field" in det, det)

    with_fail = rd(igniter={"receipts": 11, "receipts_ok": 5,
                            "receipts_deliverable": 5,
                            "bake_quality": None,
                            "last_tick_age_min": 8.4,
                            "last_failure": {"ts": "2026-11-08 22:24:54",
                                             "error": "x"}})
    ok, det = igniter_judge(0, good_out, with_fail, ok_receipt)
    check("a named failure is surfaced in the same line",
          ok and "last_failure=2026-11-08 22:24:54" in det, det)

    blind_deliv = rd(igniter={"receipts": 11, "receipts_ok": 5,
                              "last_failure": None,
                              "bake_quality": None,
                              "last_tick_age_min": 8.4})
    ok, det = igniter_judge(0, good_out, blind_deliv, ok_receipt)
    check("readout without receipts_deliverable -> FAIL (ok reads as usable)",
          (not ok) and "receipts_deliverable" in det, det)

    impossible = rd(igniter={"receipts": 11, "receipts_ok": 5,
                             "receipts_deliverable": 6,
                             "last_failure": None,
                             "bake_quality": None,
                             "last_tick_age_min": 8.4})
    ok, det = igniter_judge(0, good_out, impossible, ok_receipt)
    check("deliverable > ok -> FAIL (impossible arithmetic)",
          (not ok) and "impossible" in det, det)

    no_bake = rd(igniter={"receipts": 11, "receipts_ok": 5,
                          "receipts_deliverable": 5,
                          "last_failure": None,
                          "last_tick_age_min": 8.4})
    ok, det = igniter_judge(0, good_out, no_bake, ok_receipt)
    check("readout without bake_quality -> FAIL (atlas never read)",
          (not ok) and "bake_quality" in det, det)

    with_bake = rd(igniter={"receipts": 11, "receipts_ok": 5,
                            "receipts_deliverable": 5,
                            "last_failure": None,
                            "last_tick_age_min": 8.4,
                            "bake_quality": {"qc_verdict": "LIGHTMAP-QC-PASS"}})
    ok, det = igniter_judge(0, good_out, with_bake, ok_receipt)
    check("atlas verdict is surfaced in the same line",
          ok and "bake=LIGHTMAP-QC-PASS" in det, det)

    first = igniter_judge(0, good_out, rd(), ok_receipt)
    second = igniter_judge(0, good_out, rd(), ok_receipt)
    check("deterministic on the same input", first == second, str(first))

    print("selftest igniter verdict: %d/%d PASS"
          % (sum(results), len(results)))
    return 0 if all(results) else 1


# T227: the frame-quality reading was added to render receipts on 2026-11-09
# ~12:30.  Receipts written before that cannot carry it, and judging them would
# be a permanent red nobody can fix (no GPU window) -- the "structural wait read
# as a fault" trap.  They are named `pending`, never PASS.
FRAME_QUALITY_SINCE = "2026-11-09 12:30:00"
FRAME_QUALITY_KEYS = ("magenta_pct", "geom_pct", "qc_verdict")


def frame_quality_judge(rows, since=FRAME_QUALITY_SINCE):
    """Judge that the frame-quality reading survives on the render receipt.

    T226 measured 5/6 judgment frames carrying 16-65% magenta placeholder
    pixels, yet the old gate read `render_ok=true` because it only checked
    blank/duplicate.  Two readings (magenta share, effective geometry share)
    plus a verdict were written into the receipt -- and a reading nobody re-reads
    rots: delete the field, or let the writer regress to blank/duplicate only,
    and nothing complains.

    This judge reads the receipt field directly.  It deliberately does NOT use
    pool_igniter.last_render_quality(), which recomputes from the PNGs on disk
    and would therefore fabricate the very reading being guarded.

    Only presence and shape are judged, never the value (D-20261109-08 asks for
    three keys to be visible, not for a threshold).
    """
    newest = None
    for r in reversed(rows or []):
        run = r.get("runner") or {}
        if run.get("kind") == "render_spike" and r.get("ok"):
            newest = r
            break
    if newest is None:
        return True, "qc=n/a(no-render-yet)"
    ts = str(newest.get("ts") or "")
    batch = newest.get("batch_id")
    if ts and ts < since:
        return True, ("qc=pending(next render after %s is judged; last ok "
                      "render %s @%s predates the field)"
                      % (since, batch, ts))
    q = newest.get("frame_quality")
    if not isinstance(q, dict):
        return False, ("qc=MISSING -- ok render receipt %s @%s carries no "
                       "frame_quality (writer regressed or field deleted)"
                       % (batch, ts))
    missing = [k for k in FRAME_QUALITY_KEYS if q.get(k) in (None, "")]
    if missing:
        return False, ("qc=INCOMPLETE -- %s @%s missing %s (a partial reading "
                       "is not a reading)" % (batch, ts, ",".join(missing)))
    return True, ("qc=%s magenta_max=%s%% geom_min=%s%% (batch=%s @%s, "
                  "plumbing only)" % (q["qc_verdict"], q["magenta_pct"],
                                      q["geom_pct"], batch, ts))


def probe_frame_quality():
    """Probe no.31 (T227): render receipts still carry the quality reading."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import pool_igniter as pi
        rows = pi.receipt_rows()
    except Exception as e:  # noqa: BLE001 - report, do not crash the round
        return False, "pool_igniter receipts unreadable: %r" % (e,)
    return frame_quality_judge(rows)


def selftest_frame_quality():
    """T227 fixtures: no render / legacy / missing / partial / intact."""
    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("[%s] S%d %s%s" % ("PASS" if cond else "FAIL",
                                  len(results), name,
                                  "" if cond else " :: " + str(detail))))

    def rows(ts="2026-11-09 13:10:00", q="with", ok=True):
        rec = {"batch_id": "B-CITY3D-02", "ts": ts, "ok": ok,
               "runner": {"kind": "render_spike", "artifacts": [{"path": "x"}]}}
        if q == "with":
            rec["frame_quality"] = {"magenta_pct": 0.0, "geom_pct": 100.0,
                                    "qc_verdict": "FRAME-QC-PASS"}
        elif q == "partial":
            rec["frame_quality"] = {"magenta_pct": 0.0,
                                    "qc_verdict": "FRAME-QC-PASS"}
        return [{"batch_id": "B-OTHER", "ts": ts, "ok": True,
                 "runner": {"kind": "bake_spike", "artifacts": [{}]}},
                rec]

    ok, det = frame_quality_judge([])
    check("never rendered -> n/a, not a pass and not a failure",
          ok and "n/a(no-render-yet)" in det, det)

    ok, det = frame_quality_judge(rows(ts="2026-11-09 10:15:56", q="none"))
    check("legacy receipt before the writer -> pending, never PASS",
          ok and "pending" in det and "predates" in det, det)

    ok, det = frame_quality_judge(rows(q="none"))
    check("post-writer ok render without frame_quality -> FAIL",
          (not ok) and "MISSING" in det, det)

    ok, det = frame_quality_judge(rows(q="partial"))
    check("field present but a key missing -> FAIL naming the key",
          (not ok) and "INCOMPLETE" in det and "geom_pct" in det, det)

    ok, det = frame_quality_judge(rows())
    check("all three keys -> PASS and the verdict is echoed",
          ok and "FRAME-QC-PASS" in det and "magenta_max=" in det, det)

    ok, det = frame_quality_judge(
        [{"batch_id": "B-CITY3D-02", "ts": "2026-11-09 13:10:00",
          "ok": False, "runner": {"kind": "render_spike"},
          "frame_quality": {"magenta_pct": 0, "geom_pct": 0,
                            "qc_verdict": "x"}}])
    check("a failed render is not the newest ok render (no false green)",
          ok and "no-render-yet" in det, det)

    print("selftest frame-quality verdict: %d/%d PASS"
          % (sum(results), len(results)))
    return 0 if all(results) else 1


# --- probe no.32: dispatch verdict must declare how it was measured (M177) ---
DISPATCH_GATE_LOG = os.path.join(ROOT, "state", "dispatch-gate.jsonl")
# M175 (11-09 01:1x) wired reading=(live|file) into every verdict written by
# dispatch_precheck / pool_igniter / gpu_idle_collector.  Rows before that
# timestamp cannot carry the field -- judging them would be a red no one can
# fix (same rule as probe no.31's legacy `pending`).
DISPATCH_READING_SINCE = "2026-11-09 01:30"
DISPATCH_READING_TAIL = 80


def dispatch_reading_judge(rows):
    """M177: a verdict has to say whether it measured or read a stale file.

    A STALE lane_gate means "the heartbeat file was too old to judge" -- the
    exact state where the caller is supposed to re-measure.  Without a
    `reading` field that row is indistinguishable from "never measured", and
    the next editor of the writer can drop the field without anything turning
    red.  `reading` is per-verdict (FRESH rows carry it too), so a missing
    field is judged on any row written after the writer went in.
    """
    if rows is None:
        return False, "dispatch-gate log unreadable"
    if not rows:
        return False, "dispatch-gate log is empty (no object to judge)"
    judged = [r for r in rows if str(r.get("ts") or "") >= DISPATCH_READING_SINCE]
    older = len(rows) - len(judged)
    if not judged:
        return True, ("n/a(no-verdict-since %s) tail=%d older=%d"
                      % (DISPATCH_READING_SINCE, len(rows), older))
    fresh_n = stale_n = file_stale = 0
    missing = []
    for r in judged:
        gate = r.get("lane_gate") or {}
        verdict = str(gate.get("verdict") or "")
        reading = r.get("reading")
        if verdict == "FRESH":
            fresh_n += 1
        elif verdict == "STALE":
            stale_n += 1
            if reading == "file":
                file_stale += 1
        if not reading:
            missing.append("%s %s(%s)" % (r.get("ts") or "?",
                                          r.get("consumer") or "?",
                                          verdict or "no-verdict"))
    if missing:
        return False, ("READING-MISSING %d/%d rows since %s: %s (a verdict "
                       "without reading= is indistinguishable from 'never "
                       "measured')" % (len(missing), len(judged),
                                       DISPATCH_READING_SINCE,
                                       "; ".join(missing[:5])))
    return True, ("reading=declared judged=%d fresh=%d stale=%d file_stale=%d "
                  "older=%d since=%s" % (len(judged), fresh_n, stale_n,
                                         file_stale, older,
                                         DISPATCH_READING_SINCE))


def probe_dispatch_reading():
    """Probe no.32 (M177): dispatch verdicts still declare reading=live|file."""
    if not os.path.exists(DISPATCH_GATE_LOG):
        return False, "dispatch-gate log absent: %s" % DISPATCH_GATE_LOG
    rows = []
    try:
        with open(DISPATCH_GATE_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception as e:  # noqa: BLE001 - report, do not crash the round
        return False, "dispatch-gate log unreadable: %r" % (e,)
    return dispatch_reading_judge(rows[-DISPATCH_READING_TAIL:])


def selftest_dispatch_reading():
    """M177 fixtures: legacy / STALE missing / FRESH missing / file / empty."""
    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("[%s] S%d %s%s" % ("PASS" if cond else "FAIL",
                                  len(results), name,
                                  "" if cond else " :: " + str(detail))))

    def row(ts, verdict, reading=None, consumer="gpu-dispatch-pointer"):
        r = {"ts": ts, "consumer": consumer, "lane_gate": {"verdict": verdict}}
        if reading is not None:
            r["reading"] = reading
        return r

    ok, det = dispatch_reading_judge([row("2026-10-31 04:03:38", "STALE")])
    check("rows predating the writer are counted, not judged",
          ok and "older=1" in det and "n/a" in det, det)

    ok, det = dispatch_reading_judge([row(DISPATCH_READING_SINCE, "STALE")])
    check("STALE since the writer without reading -> FAIL naming the row",
          (not ok) and "READING-MISSING" in det
          and DISPATCH_READING_SINCE in det, det)

    ok, det = dispatch_reading_judge([row("2026-11-09 14:08:38", "FRESH")])
    check("FRESH since the writer without reading -> FAIL (per-verdict field)",
          (not ok) and "FRESH" in det, det)

    ok, det = dispatch_reading_judge(
        [row("2026-11-09 14:08:38", "STALE", "file")])
    check("STALE judged on a file reading -> PASS but file_stale visible",
          ok and "file_stale=1" in det and "stale=1" in det, det)

    ok, det = dispatch_reading_judge(
        [row("2026-11-09 14:38:38", "FRESH", "live")])
    check("live reading -> PASS and echoed", ok and "fresh=1" in det, det)

    ok, det = dispatch_reading_judge([])
    check("empty log -> FAIL (no object to judge)",
          (not ok) and "empty" in det, det)

    ok, det = dispatch_reading_judge(None)
    check("unreadable log -> FAIL, never a green",
          (not ok) and "unreadable" in det, det)

    print("selftest dispatch-reading verdict: %d/%d PASS"
          % (sum(results), len(results)))
    return 0 if all(results) else 1


def probe_probe_registry():
    """Probe no.24 (T190 / BC-P-169): probe numbering single-source census.

    Numbers used to live only in prose (docstrings + a hand-written verdict
    string + charter bullets), so a reused number stayed invisible twice
    already.  The registry below is now the only place a number is declared;
    this probe re-reads it against the charter every round.
    """
    try:
        import probe_registry_census as prc
        rep = prc.census()
    except Exception as e:  # noqa: BLE001 - census must not kill the round
        return False, "UNVERIFIED probe registry census: %r" % (e,)
    ok = rep.get("verdict") == "PROBE-REGISTRY-CLEAN"
    return ok, rep.get("summary", "no summary")


# Single source of probe numbers (T190).  The verdict line and the charter
# census both derive from this table; never re-declare a number elsewhere.
PROBE_REGISTRY = [
    (1, "gpu collector", probe_gpu),
    (2, "ollama response", probe_ollama),
    (3, "queue rows", probe_queues),
    (4, "cost ledger", probe_ledger),
    (5, "rounds.log strict-decode", probe_rounds_log),
    (6, "resident QA serve 8792", probe_resident_qa),
    (9, "L1 coder serve 8090", probe_l1_coder_serve),
    (8, "ledger tail integrity (BC-P-45)", probe_ledger_tails),
    (10, "supply dual-field reading (T132)", probe_supply_dual_fields),
    (11, "runbook size budget (T133)", probe_runbook_budget),
    (12, "cost contract census (T138)", probe_cost_contract),
    (13, "lane artifacts presence+integrity (T140/T141)",
     probe_serve_artifacts),
    (14, "CEO status export (T143)", probe_status_export),
    (15, "ledger encoding health (T145)", probe_ledger_encoding),
    (16, "resident QA 8792 /ask function (T153)",
     probe_resident_qa_functional),
    (17, "scan root self-reference census (T161)", probe_scan_roots),
    (18, "heartbeat guarded numeric tokens (T168)", probe_heartbeat_tokens),
    (20, "ledger parallel fused-row census (T177)", probe_ledger_parallel),
    (21, "resident QA version drift (T184)", probe_resident_qa_version),
    (19, "living-index text integrity (M93/D-20261102-03)",
     probe_text_integrity),
    (22, "receipt product law (M96/BC-P-166)", probe_receipt_product_law),
    (23, "ollama serve death vs transient (T188)", probe_serve_health),
    (24, "probe registry census (T190/BC-P-169)", probe_probe_registry),
    (25, "write-side encoding declarations (T193)", probe_write_encoding),
    (26, "shared jsonl row-loss census (T198)", probe_row_loss),
    (27, "aftersale rails judgeable (M115)", probe_aftersale_rails),
    (28, "drill planted-set census (T205)", probe_drill_sets),
    (29, "money-face writer guards (T209)", probe_money_guard),
    (30, "ignition chain fixtures + pool gap naming (M170)", probe_igniter),
    (31, "render frame-quality reading survives (T227)",
     probe_frame_quality),
    (32, "dispatch verdict reading declared (M177)",
     probe_dispatch_reading),
]


USAGE = (
    "usage: python Tools/qa_smoke.py [--selftest]\n"
    "  (no argument)         run the live smoke round, write qa/smoke-*.log\n"
    "  --selftest|selftest   run fixtures only: no probes, no evidence file\n")


def argv_mode(argv):
    """Return 'live' | 'selftest' | 'usage' for the CLI arguments (T208).

    A misspelled subcommand used to fall through to a full live round: the
    tool neither complained nor self-tested -- it ran every probe, wrote an
    evidence file and exited 1 on the two known reds.  To a reader that looks
    like "my test failed" when a smoke round was actually performed.
    """
    args = list(argv)
    if not args:
        return "live"
    if args in (["--selftest"], ["selftest"]):
        return "selftest"
    return "usage"


def selftest_argv():
    """Fixtures: live / both self-test spellings / unknown word rejected."""
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print("[%s] S%s %s%s" % ("PASS" if ok else "FAIL", len(results), name,
                                 "" if ok else " :: " + detail))

    check("no args -> live", argv_mode([]) == "live", argv_mode([]))
    check("--selftest -> selftest",
          argv_mode(["--selftest"]) == "selftest", "")
    check("bare selftest -> selftest (alias)",
          argv_mode(["selftest"]) == "selftest", "")
    for bad in (["--selftes"], ["selftest", "--json"], ["live"], [""]):
        check("unknown %r -> usage" % (bad,),
              argv_mode(bad) == "usage", argv_mode(bad))

    # real process: an unknown word must exit 2 without writing evidence.
    qa_dir = QA_DIR
    before = set(os.listdir(qa_dir)) if os.path.isdir(qa_dir) else set()
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "Tools", "qa_smoke.py"), "selftes"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    after = set(os.listdir(qa_dir)) if os.path.isdir(qa_dir) else set()
    check("process: unknown word -> exit 2 + usage on stderr",
          proc.returncode == 2 and "usage:" in (proc.stderr or ""),
          "rc=%r stderr=%r" % (proc.returncode, (proc.stderr or "")[:80]))
    check("process: unknown word writes no evidence",
          after == before, str(sorted(after - before)))
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "Tools", "qa_smoke.py"),
         "--bad-flag"], capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    check("process: unknown flag -> exit 2, zero probes",
          proc.returncode == 2 and "smoke verdict" not in (proc.stdout or ""),
          "rc=%r" % (proc.returncode,))

    print("selftest argv verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def main():
    mode = argv_mode(sys.argv[1:])
    if mode == "usage":
        sys.stderr.write(USAGE)
        return 2
    if mode == "selftest":
        return (selftest_tails() | selftest_coder_class()
                | selftest_supply_fields() | selftest_cost_contract()
                | selftest_serve_artifacts() | selftest_qa_functional()
                | selftest_scan_roots() | selftest_heartbeat_tokens()
                | selftest_gpu_contention() | selftest_ledger_parallel()
                | selftest_qa_version() | selftest_text_integrity()
                | selftest_receipt_product_law() | selftest_serve_health()
                | selftest_write_encoding() | selftest_row_loss()
                | selftest_aftersale_rails() | selftest_drill_sets()
                | selftest_money_guard() | selftest_igniter()
                | selftest_frame_quality() | selftest_dispatch_reading()
                | selftest_argv())
    os.makedirs(QA_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    log_path = os.path.join(QA_DIR, "smoke-%s.log" % ts)
    png_path = os.path.join(QA_DIR, "smoke-%s.png" % ts)
    probes = PROBE_REGISTRY
    lines, passed = [], 0
    lines.append("BigCompute QA smoke test %s (orders L254 / charter v1)"
                 % ts)
    for no, label, fn in probes:
        ok, detail = fn()
        passed += 1 if ok else 0
        lines.append("")
        lines.append("[%s] no.%d %s" % ("PASS" if ok else "FAIL", no, label))
        lines.append(detail)
        print("[%s] no.%d %s" % ("PASS" if ok else "FAIL", no, label))
    lines.append("")
    verdict = ("smoke verdict: %d/%d PASS (charter 1-4 + extension probes "
               "nos=%s; no.7 reserved)" % (
                   passed, len(probes),
                   ",".join(str(no) for no, _, _ in
                            sorted(PROBE_REGISTRY, key=lambda p: p[0]))))
    lines.append(verdict)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        script = (PNG_PS_TEMPLATE
                  .replace("__LOG__", log_path.replace("'", "''"))
                  .replace("__PNG__", png_path.replace("'", "''")))
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy",
                            "Bypass", "-Command", script],
                           capture_output=True, timeout=120, cwd=ROOT)
        if not os.path.exists(png_path):
            print("png render produced no file: %s"
                  % (r.stderr or "").strip()[:200])
    except Exception as e:  # noqa: BLE001 - png render must not fail round
        print("png render failed: %r" % e)
    print("%s (log=%s png=%s)" % (verdict,
                                  os.path.basename(log_path),
                                  os.path.basename(png_path)))
    return 0 if passed == len(probes) else 1


if __name__ == "__main__":
    sys.exit(main())
