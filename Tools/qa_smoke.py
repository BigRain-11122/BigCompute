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
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": "1+1?",
                       "stream": False, "keep_alive": -1,
                       "options": {"num_predict": 32, "temperature": 0}}
                      ).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
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
        detail = ("answer=%r eval_count=%s %.2f tok/s\n%s\n%s\n%s"
                  % (text, ec, tps, res, ps_pre.strip(), ps_post.strip()))
        return bool(text), detail
    except Exception as e:  # noqa: BLE001 - probe must not crash pipeline
        # BC-P-60: tags 200 + generate failure = borrow-window saturation
        # env state (labeled), tags unreachable = bare FAIL (serve down).
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(OLLAMA_TAGS_URL), timeout=2) as tr:
                tr.read()
            return False, ("ollama probe failed: BORROW-WINDOW "
                           "(tags=200, generate-timeout): %r" % e)
        except Exception:
            return False, "ollama probe failed: %r" % e


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
    status, payload, err = None, None, None
    try:
        req = urllib.request.Request(
            RESIDENT_QA_ASK_URL, data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
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
    ok, tok, det = classify_qa_functional(health_ok, True, status, payload,
                                          err, live[0])
    if ok and len(lanes) > 1:
        det = "%s L1-DOWN(fallback lane :%d serving)" % (det, live[0]) \
            if live[0] != lanes[0] else det
    return ok, "%s: %s" % (tok, det)


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
    ("queue_open", "count"),
    ("consecutive_idle", "count"),
    ("idle_rounds", "count"),
    (POOL_CANDIDATE_TOKEN, "count"),
    ("pool_effective", "count"),
    ("pool_ignitable", "count"),
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
)

TASK_FACE_LITERALS = ("ok", "pause-exempt", "probe-error")
MODEL_DRIFT_LITERALS = ("GREEN", "UNVERIFIED")
MODEL_DRIFT_DRIFT_RE = re.compile(r"^DRIFT\(m=\d+,a=\d+\)$")
STATE_LINE_RE = re.compile(r"^([A-Z][A-Z0-9-]*)\((.*)\)$")


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
        return raw in MODEL_DRIFT_LITERALS or bool(
            MODEL_DRIFT_DRIFT_RE.match(raw))
    if kind == "lane":
        return raw == "UP" or (raw.startswith("DARK[") and raw.endswith("]"))
    if kind.startswith("state:"):
        m = STATE_LINE_RE.match(raw)
        return bool(m) and ("%s=" % kind.split(":", 1)[1]) in m.group(2)
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
        if kind in ("pct", "count"):
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return False, "%s not numeric: %r" % (name, raw)
            if kind == "pct":
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
    return heartbeat_token_guard(hits[-1])


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


TAIL_PROBE_FILES = (
    ("state/rounds.log", r"\d{4}-\d{2}-\d{2}( [\d:.x]+)? tokens"),
    ("state/heartbeat.txt", r"\d{2}:\d{2}"),
    ("HQ-FEEDBACK.md", "\u65e5\u6e05[:\uff1a]"),
    # BC-P-45 判例（10-13 T86 行实锚）：ID 形锚=「| <编号> |」整胞——裸
    # \| T\d+ 会把证据胞里合法的「| T85 行+…」开头误计成第二行 ID（假
    # merged-rows FAIL）；真合并行的第二行 ID 恒为整胞形态仍命中双计。
    ("state/proposals.md", r"\| BC-P-\d+ \|"),
    ("state/queue/main.md", r"\| M\d+ \|"),
    ("state/queue/tech.md", r"\| T\d+ \|"),
    ("state/queue/explore.md", r"\| E\d+ \|"),
)


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
        empt = [i for i, ln in enumerate(raw_lines[-12:]) if not ln.strip()]
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

    print("selftest qa-functional verdict: %d/%d PASS"
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
            "cpu_lane_idle=true pool_rows_candidate=1 pool_effective=0 "
            "queue_open=57 consecutive_idle=0 idle_rounds=0 "
            "agenda_starved=false serve_alive=true task_face=ok "
            "model_drift=DRIFT(m=2,a=0) breach_eff=true "
            "pool_ignitable=3 lane8090=DARK[DOWN] "
            "c1c4_line=FRESH(rounds_ago=0,verdict=C1C4-GREEN) "
            "hq_line=FRESH(age_h=0.4,date=2026-10-29)")

    r1 = heartbeat_token_guard(good)
    check("S1 healthy row -> PASS", r1[0] and "guarded=18" in r1[1])

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

    r14 = heartbeat_token_guard(good.replace(
        "c1c4_line=FRESH(rounds_ago=0,verdict=C1C4-GREEN)",
        "c1c4_line=FRESH(age_h=1)"))
    check("S14 c1c4_line without rounds_ago key -> FAIL",
          (not r14[0]) and "c1c4_line outside" in r14[1])

    r15 = heartbeat_token_guard(good.replace(
        "hq_line=FRESH(age_h=0.4,date=2026-10-29)", "hq_line=FRESH"))
    check("S15 hq_line without parenthesised payload -> FAIL",
          (not r15[0]) and "hq_line outside" in r15[1])

    print("selftest heartbeat-token verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return (selftest_tails() | selftest_coder_class()
                | selftest_supply_fields() | selftest_cost_contract()
                | selftest_serve_artifacts() | selftest_qa_functional()
                | selftest_scan_roots() | selftest_heartbeat_tokens())
    os.makedirs(QA_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    log_path = os.path.join(QA_DIR, "smoke-%s.log" % ts)
    png_path = os.path.join(QA_DIR, "smoke-%s.png" % ts)
    probes = [("gpu collector", probe_gpu), ("ollama response", probe_ollama),
              ("queue rows", probe_queues), ("cost ledger", probe_ledger),
              ("rounds.log strict-decode", probe_rounds_log),
              ("resident QA serve 8792", probe_resident_qa),
              ("L1 coder serve 8090", probe_l1_coder_serve),
              ("ledger tail integrity (BC-P-45)", probe_ledger_tails),
              ("supply dual-field reading (T132)",
               probe_supply_dual_fields),
              ("runbook size budget (T133)", probe_runbook_budget),
              ("cost contract census (T138)", probe_cost_contract),
              ("lane artifacts presence+integrity (T140/T141)",
               probe_serve_artifacts),
              ("CEO status export (T143)", probe_status_export),
              ("ledger encoding health (T145)", probe_ledger_encoding),
              ("resident QA 8792 /ask function (T153)",
               probe_resident_qa_functional),
              ("scan root self-reference census (T161)",
               probe_scan_roots),
              ("heartbeat guarded numeric tokens (T168)",
               probe_heartbeat_tokens)]
    lines, passed = [], 0
    lines.append("BigCompute QA smoke test %s (orders L254 / charter v1)"
                 % ts)
    for label, fn in probes:
        ok, detail = fn()
        passed += 1 if ok else 0
        lines.append("")
        lines.append("[%s] %s -> %s" % (label, "PASS" if ok else "FAIL",
                                         label))
        lines.append(detail)
        print("[%s] %s" % ("PASS" if ok else "FAIL", label))
    lines.append("")
    verdict = "smoke verdict: %d/%d PASS (charter 4 + T25 + E41 + BC-P-45 no.8 + T122 no.9 + T132 no.10 + T133 no.11 + T138 no.12 + T140 no.13 + T143 no.14 + T145 no.15 + T153 no.16 + T161 no.17 + T168 no.18 probes)" % (
        passed, len(probes))
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
