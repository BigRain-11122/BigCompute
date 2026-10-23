#!/usr/bin/env python3
"""C1-C4 巡检串跑器（T121 工程件·10-26 P1 实装窗前置）.

一命令顺序跑四巡检，聚合 verdict 与证据 JSON：
  C1 rails_schema_check.py c1        （rails schema + ledger continuity）
  C2 fulfillment/test_pipeline.py    （履约管线）
  C3 compliance_gate_check.py check  （合规闸）
  C4 cost_ledger.py selftest         （成本账本）

用法:
  python Tools/c1c4_patrol.py run [--only C1,C3] [--timeout 300]
  python Tools/c1c4_patrol.py selftest

退出码: 0=全绿 / 1=任一 FAIL / 2=用法错误
证据: state/c1c4-<YYYYmmdd-HHMMSS>.json（只读巡检，不产生台账行）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"

CHECKS = {
    "C1": ("rails-schema+ledger-continuity", ["Tools/rails_schema_check.py", "c1"]),
    "C2": ("fulfillment-pipeline", ["Tools/fulfillment/test_pipeline.py"]),
    "C3": ("compliance-gate", ["Tools/compliance_gate_check.py", "check"]),
    "C4": ("cost-ledger-selftest", ["Tools/cost_ledger.py", "selftest"]),
}

# 各工具的 verdict 形态不一，按优先级取首个命中的末位标记
VERDICT_PATTERNS = (
    re.compile(r"VERDICT=([A-Z0-9_\-]+)"),
    re.compile(r"RESULT:\s*(PASS|FAIL)"),
    re.compile(r"selftest[^\n]*->\s*(True|False|[A-Z0-9_\-]+)"),
    re.compile(r"\b(GREEN|RED)\b"),
)


def extract_verdict(stdout: str) -> str:
    """取最后一段非空行里最强的 verdict 标记，取不到则回退末行."""
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    for line in reversed(lines):
        for pat in VERDICT_PATTERNS:
            m = pat.search(line)
            if m:
                return m.group(1)
    return lines[-1][:120] if lines else "NO-OUTPUT"


def summarize(results: list[dict]) -> dict:
    failed = [r["id"] for r in results if r["rc"] != 0 or r["timed_out"]]
    return {
        "verdict": "C1C4-GREEN" if not failed else "C1C4-RED",
        "failed": failed,
        "checked": [r["id"] for r in results],
    }


def run_one(cid: str, timeout: int) -> dict:
    label, argv = CHECKS[cid]
    started = datetime.now()
    try:
        proc = subprocess.run(
            [sys.executable, *argv],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        out, rc, timed_out = proc.stdout or "", proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        rc, timed_out = 124, True
    return {
        "id": cid,
        "label": label,
        "cmd": " ".join(argv),
        "rc": rc,
        "timed_out": timed_out,
        "verdict": extract_verdict(out),
        "seconds": round((datetime.now() - started).total_seconds(), 2),
        "tail": [ln for ln in out.strip().splitlines() if ln.strip()][-6:],
    }


def cmd_run(args) -> int:
    ids = [c.strip().upper() for c in args.only.split(",")] if args.only else list(CHECKS)
    unknown = [c for c in ids if c not in CHECKS]
    if unknown:
        print(f"unknown check id(s): {', '.join(unknown)}")
        return 2

    results = [run_one(cid, args.timeout) for cid in ids]
    summary = summarize(results)

    for r in results:
        flag = "PASS" if r["rc"] == 0 and not r["timed_out"] else "FAIL"
        print(f"{flag} {r['id']} {r['label']} rc={r['rc']} {r['seconds']}s verdict={r['verdict']}")
        if flag == "FAIL":
            for line in r["tail"]:
                print(f"    | {line}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    STATE.mkdir(exist_ok=True)
    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               **summary, "results": results}
    out_path = STATE / f"c1c4-{stamp}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"VERDICT={summary['verdict']} failed={len(summary['failed'])} "
          f"evidence={out_path.relative_to(ROOT).as_posix()}")
    return 0 if summary["verdict"] == "C1C4-GREEN" else 1


def cmd_selftest() -> int:
    checks = []

    checks.append(("verdict: VERDICT token", extract_verdict("x\nVERDICT=C1-GREEN ok") == "C1-GREEN"))
    checks.append(("verdict: RESULT line", extract_verdict("a\nRESULT: PASS (44/44)") == "PASS"))
    checks.append(("verdict: selftest arrow", extract_verdict("selftest: math=ok -> True") == "True"))
    checks.append(("verdict: fallback tail", extract_verdict("hello\nweird line") == "weird line"))
    checks.append(("verdict: empty", extract_verdict("   \n\n") == "NO-OUTPUT"))

    green = [{"id": "C1", "rc": 0, "timed_out": False}, {"id": "C3", "rc": 0, "timed_out": False}]
    s = summarize(green)
    checks.append(("summary: all green", s["verdict"] == "C1C4-GREEN" and s["failed"] == []))

    red = green + [{"id": "C2", "rc": 1, "timed_out": False}]
    s = summarize(red)
    checks.append(("summary: rc fail flags RED", s["verdict"] == "C1C4-RED" and s["failed"] == ["C2"]))

    timeout = [{"id": "C4", "rc": 124, "timed_out": True}]
    s = summarize(timeout)
    checks.append(("summary: timeout flags RED", s["verdict"] == "C1C4-RED" and s["failed"] == ["C4"]))

    checks.append(("checks registry: four ids",
                   list(CHECKS) == ["C1", "C2", "C3", "C4"] and all(CHECKS[k][1] for k in CHECKS)))
    checks.append(("every cmd path exists", all((ROOT / CHECKS[k][1][0]).exists() for k in CHECKS)))

    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    failed = sum(1 for _, ok in checks if not ok)
    print(f"selftest {len(checks) - failed}/{len(checks)} PASS")
    return 0 if failed == 0 else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="C1-C4 patrol runner")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "selftest"])
    ap.add_argument("--only", default="", help="逗号分隔子集，如 C1,C3")
    ap.add_argument("--timeout", type=int, default=300, help="单检超时秒")
    args = ap.parse_args(argv[1:])
    if args.command == "selftest":
        return cmd_selftest()
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))