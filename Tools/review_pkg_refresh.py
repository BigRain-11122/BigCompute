#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review_pkg_refresh.py -- 10-05 (and future) review-window package data-face one-command refresher.

BC-P-40 batch-activation conversion, tech T49 (2026-10-05 night round).
Purpose: review-window package (docs/ops/review-window-1005-package-v1.md) requires
window-open data age <= 12h (M47 pre-registered). Manual 4-command re-assembly per
refresh round was the drift-prone gap (M47 hand-assembled once; noon round skipped).
This tool composes the four data faces in one command, stdout block ready to paste.

Faces (all read-only, zero new ledgers, zero dispatch -- DRY-RUN same discipline):
  A gpu three-ruler + loadline + machine_borrowable : gpu_idle_collector.py report + loadline
  B batch pool validate                              : batch_pool.py validate
  C _trash re-count                                  : docs/_trash/ walk (files + MB)
  D clean-window tally                               : state/clean-window-log.jsonl (lines + BLOCKED)

Pre-registered criteria (BC-P-40):
  J1 four faces assembled, zero manual composition
  J2 determinism: face parsers pure; subprocess output passed through verbatim
  J3 read-only: source files hash-unchanged across refresh
  J4 selftest PASS (fixture-based; no live probes invoked in selftest)

Seat4 face (BC-P-44 batch-activation conversion, tech T53, 2026-10-06 night round):
additive `refresh --face seat4` composes the seat4-dualtrack-evidence-pack-v1.md
four data sections (M50 J1 freshness: <=12h at 10-07 governance-day window open):
  S1 local uptake  : state/rounds.log api= field aggregation (zero-cloud proof)
  S2 quality       : qa/smoke-*.log verdict tally + git daily commits since 2026-09-29
  S3 load row      : collector report + loadline (rolling 30min + dual borrow probe)
  S4 machine image : collector machine_profile re-measure
Same discipline: read-only, zero new ledgers, zero dispatch, DRY-RUN same law.

Usage:
  python Tools/review_pkg_refresh.py refresh [--face {review,seat4}] [--out PATH]
  python Tools/review_pkg_refresh.py selftest

Consumers: M46/M47/M48 package data face (window-open <=12h freshness), future review
windows reuse; seat4 face = 10-07 governance-day three-order co-window report face
(M50 ② J1). On-demand tool: NOT wired into per-round qa_smoke (charter probe set
unchanged) per tools-onboarding-sop-v1.md J4 exemption line.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python"

GPU_CMD_REPORT = [PY, os.path.join("Tools", "gpu_idle_collector.py"), "report"]
GPU_CMD_LOADLINE = [PY, os.path.join("Tools", "gpu_idle_collector.py"), "loadline"]
GPU_CMD_PROFILE = [PY, os.path.join("Tools", "gpu_idle_collector.py"), "machine_profile"]
POOL_CMD = [PY, os.path.join("Tools", "batch_pool.py"), "validate"]
TRASH_DIR = os.path.join("docs", "_trash")
CLEAN_LOG = os.path.join("state", "clean-window-log.jsonl")
ROUND_LOG = os.path.join("state", "rounds.log")
QA_DIR = os.path.join("qa")
GIT_SINCE_DATE = "2026-09-29"  # evidence pack §2 daily-commit baseline start
VERDICT_RE = re.compile(r"(?:smoke\s+verdict:|VERDICT:)\s*(\d+)\s*/\s*(\d+)\s+PASS")
API_RE = re.compile(r"\bapi=(\d+)\b")


def run_cmd(cmd):
    """Run a repo tool read-only command, return (rc, combined output). UTF-8 decode, fail-soft."""
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # fail-soft: refresh must not die on one face
        return 127, "ERROR face command failed: %r" % (e,)


def face_trash(trash_dir):
    """Count files and total MB under a directory (os.walk read-only). Returns (n, mb)."""
    n, total = 0, 0
    for dirpath, _dirs, files in os.walk(trash_dir):
        for f in files:
            n += 1
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return n, total / (1024.0 * 1024.0)


def face_clean_window(log_path):
    """Parse clean-window jsonl: returns (total_lines, blocked_lines). Substring fallback fail-soft."""
    total = blocked = 0
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    if json.loads(line).get("verdict") == "BLOCKED":
                        blocked += 1
                except ValueError:
                    if "BLOCKED" in line:
                        blocked += 1
    except FileNotFoundError:
        return 0, 0
    return total, blocked


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compose_block(ts=None):
    """Assemble the four data faces into one stdout block. Read-only (J3)."""
    ts = ts or time.strftime("%Y-%m-%d %H:%M:%S")
    lines = ["review-pkg-refresh %s (BC-P-40 / tech T49; read-only, zero dispatch)" % ts]
    rc_a, out_a = run_cmd(GPU_CMD_REPORT)
    rc_a2, out_a2 = run_cmd(GPU_CMD_LOADLINE)
    lines.append("[A gpu three-ruler + loadline + machine_borrowable] rc=%d/%d" % (rc_a, rc_a2))
    lines.extend(l for l in (out_a + out_a2).splitlines() if l.strip())
    rc_b, out_b = run_cmd(POOL_CMD)
    lines.append("[B batch-pool validate] rc=%d" % rc_b)
    lines.extend(l for l in out_b.splitlines() if l.strip())
    n, mb = face_trash(os.path.join(ROOT, TRASH_DIR))
    lines.append("[C _trash re-count] %d files %.1f MB" % (n, mb))
    total, blocked = face_clean_window(os.path.join(ROOT, CLEAN_LOG))
    lines.append("[D clean-window tally] %d/%d BLOCKED (zero-clean if equal; dual gate = editors + vram_free)" % (blocked, total))
    lines.append("[J1 four faces assembled | J3 read-only face parsers | consumer: review-window package data age <=12h]")
    return "\n".join(lines)


def face_rounds_api(log_path):
    """Seat4 S1: aggregate the api= field over rounds.log (first api= per line wins).
    Returns (total_lines, parsed, api0_rounds, apigt0_rounds, api_calls)."""
    total = parsed = api0 = apigt0 = api_calls = 0
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                total += 1
                m = API_RE.search(line)
                if not m:
                    continue
                parsed += 1
                v = int(m.group(1))
                api_calls += v
                if v == 0:
                    api0 += 1
                else:
                    apigt0 += 1
    except FileNotFoundError:
        pass
    return total, parsed, api0, apigt0, api_calls


def face_smoke_tally(qa_dir):
    """Seat4 S2a: tally smoke-*.log verdict lines (standard + early VERDICT format).
    Last verdict line in a file wins. Returns (total_files, full_pass, partial_names)."""
    try:
        names = sorted(f for f in os.listdir(qa_dir)
                       if f.startswith("smoke-") and f.endswith(".log"))
    except FileNotFoundError:
        names = []
    full = 0
    partials = []
    for name in names:
        try:
            with open(os.path.join(qa_dir, name), "r", encoding="utf-8",
                      errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        m = None
        for m2 in VERDICT_RE.finditer(text):
            m = m2
        if m is None:
            continue
        if int(m.group(1)) >= int(m.group(2)):
            full += 1
        else:
            partials.append(name)
    return len(names), full, partials


def tally_daily(dates_text):
    """Seat4 S2b: pure per-day counter over `git log --date=short` output."""
    counts = {}
    for d in dates_text.split():
        counts[d] = counts.get(d, 0) + 1
    return sorted(counts.items())


def face_git_daily(since=GIT_SINCE_DATE):
    """Seat4 S2b: daily commit counts since the evidence-pack baseline. Fail-soft []."""
    try:
        p = subprocess.run(["git", "log", "--since=%sT00:00" % since,
                            "--pretty=%ad", "--date=short"],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return tally_daily(p.stdout or "")
    except Exception:
        return []


def compose_seat4_block(ts=None):
    """Assemble the seat4 evidence-pack four data sections. Read-only (J3)."""
    ts = ts or time.strftime("%Y-%m-%d %H:%M:%S")
    lines = ["seat4-evidence-refresh %s (BC-P-44 / tech T53; read-only, zero dispatch)" % ts]
    total, parsed, api0, apigt0, api_calls = face_rounds_api(os.path.join(ROOT, ROUND_LOG))
    pct = (100.0 * api0 / parsed) if parsed else 0.0
    lines.append("[S1 local-uptake: rounds.log api aggregate] lines=%d parsed=%d "
                 "api=0 rounds=%d (%.1f%%) api>0 rounds=%d total_api_calls=%d "
                 "(zero cloud-generation track per P-09)" % (total, parsed, api0, pct,
                                                             apigt0, api_calls))
    n_files, n_full, partials = face_smoke_tally(os.path.join(ROOT, QA_DIR))
    lines.append("[S2 quality: smoke verdict tally] files=%d full_pass=%d partial=%d%s"
                 % (n_files, n_full, len(partials),
                    (" (%s; re-run same round per fix-at-once law)" % ", ".join(partials))
                    if partials else ""))
    lines.append("[S2 quality: daily commits since %s] %s" % (
        GIT_SINCE_DATE,
        " ".join("%s=%d" % (d, n) for d, n in face_git_daily()) or "none"))
    rc_l, out_l = run_cmd(GPU_CMD_LOADLINE)
    rc_r, out_r = run_cmd(GPU_CMD_REPORT)
    lines.append("[S3 load row: loadline + report (rolling 30min + dual probe)] rc=%d/%d"
                 % (rc_l, rc_r))
    lines.extend(l for l in (out_l + "\n" + out_r).splitlines() if l.strip())
    rc_p, out_p = run_cmd(GPU_CMD_PROFILE)
    lines.append("[S4 machine image: machine_profile re-measure] rc=%d" % rc_p)
    lines.extend(l for l in out_p.splitlines() if l.strip())
    lines.append("[J1 four sections assembled | J3 read-only | consumer: seat4 evidence "
                 "pack <=12h freshness at 10-07 window open (M50 J1)]")
    return "\n".join(lines)


def cmd_refresh(args):
    if getattr(args, "face", "review") == "seat4":
        block = compose_seat4_block()
    else:
        block = compose_block()
    print(block)
    if args.out:
        path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:  # utf-8 no BOM (BC-P-16 J3)
            fh.write(block + "\n")
        print("saved: %s" % os.path.relpath(path, ROOT))
    return 0


def cmd_selftest(_args):
    state = {"pass": 0, "n": 0}

    def check(name, cond):
        state["n"] += 1
        state["pass"] += 1 if cond else 0
        print("S%-2d %s %s" % (state["n"], "PASS" if cond else "FAIL", name))

    # S1 face_trash fixture
    with tempfile.TemporaryDirectory() as td:
        for name, size in (("a.bin", 1024 * 1024), ("b.bin", 2 * 1024 * 1024)):
            with open(os.path.join(td, name), "wb") as fh:
                fh.write(b"x" * size)
        n, mb = face_trash(td)
        check("face_trash counts 2 files ~3.0MB", n == 2 and abs(mb - 3.0) < 0.01)
    # S2 face_clean_window fixture (json + substring fallback)
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
        fh.write('{"verdict": "BLOCKED"}\n{"verdict": "CLEAN"}\nnot-json BLOCKED line\n\n')
        tmp = fh.name
    try:
        total, blocked = face_clean_window(tmp)
        check("clean_window 3 lines 2 BLOCKED (json+fallback)", (total, blocked) == (3, 2))
        check("clean_window missing file fail-soft", face_clean_window(tmp + ".nope") == (0, 0))
    finally:
        os.unlink(tmp)
    # S3 determinism: pure parser double-run identical
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
        fh.write('{"verdict": "BLOCKED"}\n' * 5)
        tmp = fh.name
    try:
        check("determinism parser double-run equal", face_clean_window(tmp) == face_clean_window(tmp))
    finally:
        os.unlink(tmp)
    # S4 J3 read-only on real sources (hash across pure-face read)
    log_path = os.path.join(ROOT, CLEAN_LOG)
    if os.path.exists(log_path):
        before = _sha256(log_path)
        face_clean_window(log_path)
        check("J3 clean-window log hash unchanged", _sha256(log_path) == before)
    else:
        check("J3 clean-window log absent -> skip-hash ok", True)
    # S5 canonical command registry (drift guard: faces stay pinned to charter tools)
    check("S5 canonical cmds pinned",
          GPU_CMD_REPORT[1].endswith("gpu_idle_collector.py") and GPU_CMD_LOADLINE[2] == "loadline"
          and POOL_CMD[1].endswith("batch_pool.py"))
    # S6 seat4 S1: rounds api aggregation fixture (zero/poz/unparsable/missing fail-soft)
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as fh:
        fh.write("2026-10-05 tokens: local=1 api=0 api_reason=- x\n"
                 "2026-10-05 tokens: local=1 api=2 api_reason=oss y\n"
                 "header line without api field\n\n"
                 "2026-10-05 api_reason=- is not api= (api=5 tail) z\n")
        tmp = fh.name
    try:
        got = face_rounds_api(tmp)
        check("S6 rounds api aggregate 4/3/1/2/7",
              got == (4, 3, 1, 2, 7))
        check("S6b rounds api missing file fail-soft",
              face_rounds_api(tmp + ".nope") == (0, 0, 0, 0, 0))
    finally:
        os.unlink(tmp)
    # S7 seat4 S2a: smoke verdict tally fixture (standard + early + partial + none)
    with tempfile.TemporaryDirectory() as td:
        for name, body in (("smoke-a.log", "x\nsmoke verdict: 4/4 PASS\n"),
                           ("smoke-b.log", 'VERDICT: 4/4 PASS, no missing item\n'),
                           ("smoke-c.log", "smoke verdict: 5/6 PASS (probe)\n"),
                           ("smoke-d.log", "no verdict line here\n"),
                           ("other.log", "smoke verdict: 1/1 PASS\n")):
            with open(os.path.join(td, name), "w", encoding="utf-8") as fh:
                fh.write(body)
        n_files, n_full, partials = face_smoke_tally(td)
        check("S7 smoke tally 4 files 2 full 1 partial",
              (n_files, n_full, partials) == (4, 2, ["smoke-c.log"]))
        check("S7b smoke tally missing dir fail-soft",
              face_smoke_tally(os.path.join(td, "nope")) == (0, 0, []))
    # S8 seat4 S2b: pure daily counter fixture
    check("S8 tally_daily sorted counts",
          tally_daily("2026-10-05\n2026-10-05\n2026-10-06\n")
          == [("2026-10-05", 2), ("2026-10-06", 1)])
    # S9 seat4 registry pin (profile cmd pinned; --face choices frozen)
    check("S9 seat4 cmd pinned + verdict regex spans both formats",
          GPU_CMD_PROFILE[2] == "machine_profile"
          and bool(VERDICT_RE.search("smoke verdict: 6/7 PASS x"))
          and bool(VERDICT_RE.search("VERDICT: 4/4 PASS, no missing item"))
          and not VERDICT_RE.search("4/4 pass lowercase"))
    print("selftest: %d/%d PASS" % (state["pass"], state["n"]))
    return 0 if state["pass"] == state["n"] else 1


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # GBK console guard
    ap = argparse.ArgumentParser(description="review-window package data-face refresher (BC-P-40/T49)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("refresh", help="compose data faces, print (+optional save)")
    p1.add_argument("--face", default="review", choices=["review", "seat4"],
                    help="review = M46-M48 package four faces (BC-P-40); "
                         "seat4 = evidence-pack four sections (BC-P-44/T53)")
    p1.add_argument("--out", default=None, help="relative repo path to save block, e.g. state/review-pkg-refresh-<ts>.txt")
    p1.set_defaults(func=cmd_refresh)
    p2 = sub.add_parser("selftest", help="fixture selftest, zero live probes")
    p2.set_defaults(func=cmd_selftest)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
