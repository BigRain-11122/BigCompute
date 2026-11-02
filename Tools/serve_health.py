#!/usr/bin/env python3
"""serve_health.py - live Ollama serve verdict with a settle window.

Why: on 2026-11-02 22:43 the heartbeat wrote a P1 "OLLAMA DOWN detail=URLError"
while ollama.exe had already restarted 55s later (PID 54224 at 22:44:08) and a
real generation succeeded. A single failed read is not a death sentence - the
same rule executor_liveness.py already applies to dispatcher lanes.

This probe separates three things a bare TCP/HTTP check cannot:
  UP        /api/tags 200, >=1 model, (optional) real generation answers
  DEGRADED  serve answers but cannot do its job (no models / model missing /
            generation failed)
  DOWN      the settle window was exhausted without recovery

A transient failure that recovers inside --settle-s is reported as UP with
recovered_after_s set, so a 55s restart window never masquerades as an outage.

Exit codes: 0=UP, 2=DEGRADED, 3=DOWN, 9=selftest failed
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:11434"
DEFAULT_SETTLE_S = 45.0
DEFAULT_RETRY_EVERY_S = 3.0
DEFAULT_TIMEOUT_S = 5.0

UP, DEGRADED, DOWN = "UP", "DEGRADED", "DOWN"
EXIT = {UP: 0, DEGRADED: 2, DOWN: 3}


class Transport(Exception):
    """Any failure to reach the endpoint (DNS, refused, timeout, HTTP!=200)."""


class Malformed(Exception):
    """Endpoint answered but the payload is not usable JSON."""


def http_fetch(url, timeout):
    """Return (status, body_bytes); raise Transport on any reach failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:  # URLError, timeout, connection refused, HTTPError
        raise Transport(f"{type(e).__name__}: {e}")


def _json(body):
    try:
        return json.loads(body)
    except Exception as e:
        raise Malformed(str(e))


def probe_tags(fetch, base, timeout):
    status, body = fetch(base + "/api/tags", timeout)
    if status != 200:
        raise Transport(f"tags HTTP {status}")
    j = _json(body)
    models = [m.get("name", "?") for m in (j.get("models") or [])]
    return models


def probe_generate(fetch, base, timeout, model):
    payload = json.dumps(
        {
            "model": model,
            "prompt": "reply with the single word: ok",
            "stream": False,
            "options": {"num_predict": 16},
        }
    ).encode()
    req = urllib.request.Request(
        base + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    status, body = fetch(req, timeout)
    if status != 200:
        raise Transport(f"generate HTTP {status}")
    j = _json(body)
    if j.get("error"):
        raise Transport(f"generate error: {str(j['error'])[:120]}")
    if not j.get("done_reason"):
        raise Transport("generate returned no done_reason")
    return {
        "eval_count": j.get("eval_count"),
        "done_reason": j.get("done_reason"),
        "total_ms": round((j.get("total_duration") or 0) / 1e6),
    }


def judge(models, expect_models, generate_result):
    """Pure verdict function - no I/O. Returns (verdict, reasons)."""
    reasons = []
    if not models:
        reasons.append("no models installed (/api/tags returned 0)")
    for want in expect_models or []:
        if want not in models:
            reasons.append(f"expected model absent: {want}")
    if generate_result is not None and generate_result.get("error"):
        reasons.append("generation failed: " + str(generate_result["error"])[:120])
    if reasons:
        return DEGRADED, reasons
    return UP, []


def run(base=DEFAULT_BASE, settle_s=DEFAULT_SETTLE_S,
        retry_every_s=DEFAULT_RETRY_EVERY_S, timeout_s=DEFAULT_TIMEOUT_S,
        expect_models=None, do_generate=False, fetch=http_fetch,
        sleep=time.sleep, clock=time.monotonic):
    """Probe with a settle window. Never reports DOWN before the window ends."""
    started = clock()
    attempts = 0
    last_err = None
    recovered_after_s = None
    models = None

    while True:
        attempts += 1
        try:
            models = probe_tags(fetch, base, timeout_s)
            if attempts > 1:
                recovered_after_s = round(clock() - started, 1)
            break
        except (Transport, Malformed) as e:
            last_err = f"{type(e).__name__}: {e}"
            elapsed = clock() - started
            if elapsed + retry_every_s > settle_s:
                return {
                    "verdict": DOWN,
                    "base": base,
                    "attempts": attempts,
                    "settle_s": settle_s,
                    "recovered_after_s": None,
                    "last_error": last_err,
                    "models": [],
                    "reasons": [
                        f"unreachable after {attempts} attempts / {round(elapsed,1)}s "
                        f"of a {settle_s}s settle window"
                    ],
                    "elapsed_s": round(elapsed, 1),
                }
            sleep(retry_every_s)

    gen = None
    if do_generate:
        target = (expect_models or models or [None])[0]
        try:
            gen = probe_generate(fetch, base, max(timeout_s, 120.0), target)
        except (Transport, Malformed) as e:
            gen = {"error": f"{type(e).__name__}: {str(e)[:120]}"}

    verdict, reasons = judge(models, expect_models, gen)
    return {
        "verdict": verdict,
        "base": base,
        "attempts": attempts,
        "settle_s": settle_s,
        "recovered_after_s": recovered_after_s,
        "last_error": last_err,
        "models": models,
        "generate": gen,
        "reasons": reasons,
        "elapsed_s": round(clock() - started, 1),
    }


# ----------------------------- selftest -----------------------------

def _fake(script):
    """script: list of items consumed per call. item = ("ok", models) | ("err", msg)."""
    seq = list(script)
    calls = {"n": 0}

    def fetch(url, timeout):
        calls["n"] += 1
        if not seq:
            item = ("err", "script exhausted")
        else:
            item = seq.pop(0)
        if item[0] == "err":
            raise Transport(item[1])
        return 200, json.dumps({"models": [{"name": m} for m in item[1]]}).encode()

    fetch.calls = calls
    return fetch


def _fake_clock(step=0.0):
    t = {"v": 0.0}

    def clock():
        return t["v"]

    def sleep(s):
        t["v"] += s

    return clock, sleep, t


def selftest():
    checks = []

    def ck(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    # S1 clean UP on first attempt
    f = _fake([("ok", ["qwen3:4b-instruct"])])
    clk, slp, _ = _fake_clock()
    r = run(fetch=f, sleep=slp, clock=clk)
    ck("S1 UP on first attempt", r["verdict"] == UP and r["attempts"] == 1
       and r["recovered_after_s"] is None, r["verdict"])

    # S2 transient recovery -> UP, not DOWN (the 55s restart case)
    f = _fake([("err", "refused"), ("err", "refused"), ("ok", ["m1"])])
    clk, slp, _ = _fake_clock()
    r = run(settle_s=45.0, retry_every_s=3.0, fetch=f, sleep=slp, clock=clk)
    ck("S2 transient recovers -> UP", r["verdict"] == UP and r["attempts"] == 3,
       r["verdict"])
    ck("S2 records recovery delay", r["recovered_after_s"] is not None
       and r["recovered_after_s"] > 0, str(r["recovered_after_s"]))

    # S3 settle window exhausted -> DOWN with attempt count > 1
    f = _fake([("err", "refused")] * 50)
    clk, slp, _ = _fake_clock()
    r = run(settle_s=10.0, retry_every_s=3.0, fetch=f, sleep=slp, clock=clk)
    ck("S3 exhausted -> DOWN", r["verdict"] == DOWN and r["attempts"] > 1,
       f"{r['verdict']} attempts={r['attempts']}")

    # S4 never DOWN inside the window: one blip is not death
    f = _fake([("err", "blip"), ("ok", ["m1"])])
    clk, slp, _ = _fake_clock()
    r = run(settle_s=45.0, fetch=f, sleep=slp, clock=clk)
    ck("S4 single blip is not DOWN", r["verdict"] != DOWN, r["verdict"])

    # S5 zero models -> DEGRADED (answers but cannot serve)
    f = _fake([("ok", [])])
    clk, slp, _ = _fake_clock()
    r = run(fetch=f, sleep=slp, clock=clk)
    ck("S5 zero models -> DEGRADED", r["verdict"] == DEGRADED, r["verdict"])

    # S6 expected model missing -> DEGRADED and names the model
    f = _fake([("ok", ["qwen3:4b-instruct"])])
    clk, slp, _ = _fake_clock()
    r = run(expect_models=["qwen3.6-35b"], fetch=f, sleep=slp, clock=clk)
    ck("S6 missing expected model -> DEGRADED",
       r["verdict"] == DEGRADED and any("qwen3.6-35b" in x for x in r["reasons"]),
       r["verdict"])

    # S7 expected model present -> UP
    f = _fake([("ok", ["qwen3.6-35b", "qwen3:4b-instruct"])])
    clk, slp, _ = _fake_clock()
    r = run(expect_models=["qwen3:4b-instruct"], fetch=f, sleep=slp, clock=clk)
    ck("S7 expected model present -> UP", r["verdict"] == UP, r["verdict"])

    # S8 malformed payload -> retried, not crash; ends DOWN on exhaustion
    def bad_fetch(url, timeout):
        return 200, b"<html>not json</html>"
    clk, slp, _ = _fake_clock()
    r = run(settle_s=6.0, retry_every_s=3.0, fetch=bad_fetch, sleep=slp, clock=clk)
    ck("S8 malformed body -> DOWN not crash",
       r["verdict"] == DOWN and "Malformed" in (r["last_error"] or ""),
       str(r["last_error"])[:40])

    # S9 generate failure -> DEGRADED
    def _url_of(target):
        # probe_generate passes a urllib Request, probe_tags passes a plain str
        return target.full_url if hasattr(target, "full_url") else target

    def gen_fail_fetch(url, timeout):
        if "/api/tags" in _url_of(url):
            return 200, json.dumps({"models": [{"name": "m1"}]}).encode()
        return 200, json.dumps({"error": "model not found"}).encode()
    clk, slp, _ = _fake_clock()
    r = run(do_generate=True, fetch=gen_fail_fetch, sleep=slp, clock=clk)
    ck("S9 generation failure -> DEGRADED", r["verdict"] == DEGRADED, r["verdict"])

    # S10 generate success -> UP with metrics
    def gen_ok_fetch(url, timeout):
        if "/api/tags" in _url_of(url):
            return 200, json.dumps({"models": [{"name": "m1"}]}).encode()
        return 200, json.dumps({"eval_count": 5, "done_reason": "stop",
                                "total_duration": 46000000}).encode()
    clk, slp, _ = _fake_clock()
    r = run(do_generate=True, fetch=gen_ok_fetch, sleep=slp, clock=clk)
    ck("S10 generation success -> UP",
       r["verdict"] == UP and r["generate"]["eval_count"] == 5, r["verdict"])

    # S11 exit code mapping is total and distinct
    ck("S11 exit codes distinct",
       len({EXIT[UP], EXIT[DEGRADED], EXIT[DOWN]}) == 3 and EXIT[UP] == 0,
       str(EXIT))

    # S12 judge() is pure: same input -> same output, no I/O side effects
    a = judge(["m1"], [], None)
    b = judge(["m1"], [], None)
    ck("S12 judge deterministic", a == b, str(a))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  [{detail}]"))
    print(f"selftest {len(checks) - len(failed)}/{len(checks)} PASS")
    return 0 if not failed else 9


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    rp = sub.add_parser("run", help="probe the live serve endpoint")
    rp.add_argument("--base", default=DEFAULT_BASE)
    rp.add_argument("--settle-s", type=float, default=DEFAULT_SETTLE_S)
    rp.add_argument("--retry-every-s", type=float, default=DEFAULT_RETRY_EVERY_S)
    rp.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    rp.add_argument("--expect-model", action="append", default=[],
                    help="model that must be present (repeatable)")
    rp.add_argument("--generate", action="store_true",
                    help="also run a real generation probe")
    rp.add_argument("--json-out", default=None)
    sub.add_parser("selftest")

    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if args.cmd != "run":
        ap.print_help()
        return 1

    result = run(base=args.base, settle_s=args.settle_s,
                 retry_every_s=args.retry_every_s, timeout_s=args.timeout_s,
                 expect_models=args.expect_model, do_generate=args.generate)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    line = (f"verdict={result['verdict']} attempts={result['attempts']} "
            f"settle_s={result['settle_s']} models={len(result['models'])}")
    if result["recovered_after_s"] is not None:
        line += f" recovered_after_s={result['recovered_after_s']}"
    print(line)
    for r in result["reasons"]:
        print("  - " + r)
    return EXIT[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())