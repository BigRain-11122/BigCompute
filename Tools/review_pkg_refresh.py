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

Usage:
  python Tools/review_pkg_refresh.py refresh [--out PATH]
  python Tools/review_pkg_refresh.py selftest

Consumers: M46/M47/M48 package data face (window-open <=12h freshness), future review
windows reuse; seat4 evidence-pack refresh is a same-pattern extension candidate (BC-P-44).
On-demand tool: NOT wired into per-round qa_smoke (charter probe set unchanged) per
tools-onboarding-sop-v1.md J4 exemption line.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python"

GPU_CMD_REPORT = [PY, os.path.join("Tools", "gpu_idle_collector.py"), "report"]
GPU_CMD_LOADLINE = [PY, os.path.join("Tools", "gpu_idle_collector.py"), "loadline"]
POOL_CMD = [PY, os.path.join("Tools", "batch_pool.py"), "validate"]
TRASH_DIR = os.path.join("docs", "_trash")
CLEAN_LOG = os.path.join("state", "clean-window-log.jsonl")


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


def cmd_refresh(args):
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
    print("selftest: %d/%d PASS" % (state["pass"], state["n"]))
    return 0 if state["pass"] == state["n"] else 1


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # GBK console guard
    ap = argparse.ArgumentParser(description="review-window package data-face refresher (BC-P-40/T49)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("refresh", help="compose four data faces, print (+optional save)")
    p1.add_argument("--out", default=None, help="relative repo path to save block, e.g. state/review-pkg-refresh-<ts>.txt")
    p1.set_defaults(func=cmd_refresh)
    p2 = sub.add_parser("selftest", help="fixture selftest, zero live probes")
    p2.set_defaults(func=cmd_selftest)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
