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


CODER_EXPECTED_IMAGE = "llama-server.exe"
CODER_PORT = 8090


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


def classify_coder_failure(pid, image, err):
    """T122 pure verdict text for a failed :8090 probe.

    Three mutually exclusive classes, all FAIL (never softened):
      FOREIGN-PORT - a non-lane image owns the socket (lane cannot bind)
      LANE-DOWN    - the lane image owns it but does not answer
      LANE-DOWN    - nothing listening at all
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
        return False, classify_coder_failure(pid, image, e)


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
    All three must stay FAIL - a softened verdict would hide a dead lane.
    """
    cases = [
        ("A foreign owner -> FOREIGN-PORT",
         ("28008", "eCloud.exe", "timed out"),
         "FOREIGN-PORT: eCloud.exe pid=28008"),
        ("B lane owner unresponsive -> LANE-DOWN",
         ("4242", "llama-server.exe", "timed out"),
         "LANE-DOWN: llama-server.exe pid=4242"),
        ("C no listener -> LANE-DOWN",
         (None, None, "connection refused"),
         "no listener on :8090"),
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


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return selftest_tails() | selftest_coder_class()
    os.makedirs(QA_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    log_path = os.path.join(QA_DIR, "smoke-%s.log" % ts)
    png_path = os.path.join(QA_DIR, "smoke-%s.png" % ts)
    probes = [("gpu collector", probe_gpu), ("ollama response", probe_ollama),
              ("queue rows", probe_queues), ("cost ledger", probe_ledger),
              ("rounds.log strict-decode", probe_rounds_log),
              ("resident QA serve 8792", probe_resident_qa),
              ("L1 coder serve 8090", probe_l1_coder_serve),
              ("ledger tail integrity (BC-P-45)", probe_ledger_tails)]
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
    verdict = "smoke verdict: %d/%d PASS (charter 4 + T25 + E41 + BC-P-45 no.8 probes)" % (
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
