#!/usr/bin/env python3
"""qa_smoke.py - one-shot QA smoke-test pipeline (tech T11, orders L254).

Runs the four BigCompute charter checks (docs/qa-smoke-test-charter.md):
 1. GPU collector runs and produces data (gpu_idle_collector.py report)
 2. Ollama real response (local serve probe, qwen2.5:7b-instruct)
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

Writes qa/smoke-<ts>.log with raw outputs + a self-judge verdict, then
renders that exact log content to qa/smoke-<ts>.png via .NET
System.Drawing. CLI company has no GUI window to screenshot, so the PNG
is a faithful zero-edit render of the real command output (declaration
BC-F-20260928-03, group patrol to adjudicate; charter red line:
fabrication = P1).

Usage: python Tools/qa_smoke.py
Exit 0 = all pass; 1 = at least one fail (fix = next round top priority).
Encoding rule: this file stays PURE ASCII (group coding law).
"""
import datetime
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
QA_DIR = os.path.join(ROOT, "qa")
QUEUE_DIR = os.path.join(ROOT, "state", "queue")
QUEUE_FILES = ("main.md", "tech.md", "explore.md")
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
RESIDENT_QA_URL = "http://127.0.0.1:8792/health"

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


def probe_ollama():
    """Charter 2: local Ollama serve answers a real prompt.

    T15 (R-20260928-inference-serving-standard Q3 item 3): the probe is a
    resident-serving call, so it carries keep_alive=-1 explicitly and
    records residency before/after. If residency was lost, the generate
    call itself reloads the model (auto-load) and the log line records
    the restore; residency no longer depends on default keep_alive.
    """
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


def main():
    os.makedirs(QA_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    log_path = os.path.join(QA_DIR, "smoke-%s.log" % ts)
    png_path = os.path.join(QA_DIR, "smoke-%s.png" % ts)
    probes = [("gpu collector", probe_gpu), ("ollama response", probe_ollama),
              ("queue rows", probe_queues), ("cost ledger", probe_ledger),
              ("rounds.log strict-decode", probe_rounds_log),
              ("resident QA serve 8792", probe_resident_qa)]
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
    verdict = "smoke verdict: %d/%d PASS (charter 4 + tech T25 + E41 probes)" % (
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
