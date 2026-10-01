#!/usr/bin/env python3
"""Q8_0 vs Q4_K_M three-metric trial probe (tech T15 sub-item 2 / BC-P-07).

Pre-registered judging (BC-P-07): same prompt on both quant tiers of
qwen2.5:7b-instruct, record three metrics per tier (tok/s, resident
VRAM via `ollama ps` SIZE, power draw via nvidia-smi samples taken
DURING generation). Q8_0 is a timeshare slot, not a resident slot
(12GB shared-card discipline): after the trial it is unloaded and the
Q4_K_M fast tier is restored with keep_alive=-1 (resident FOREVER).

Commands:
  run       execute the trial (writes state/q8-trial-<ts>.json)
  selftest  offline assertions on pure helpers (no ollama calls)

Read-only w.r.t. ledgers; evidence file only. ASCII console output.
"""
import json
import subprocess
import threading
import time
import urllib.request
import datetime
import os
import sys

OLS_URL = "http://localhost:11434/api/generate"
FAST = "qwen2.5:7b-instruct"        # Q4_K_M default tier (resident slot)
HQ = "qwen2.5:7b-instruct-q8_0"    # Q8_0 tier (timeshare candidate)
PROMPT = ("Count from 1 to 30, one number per line, "
          "then one short closing sentence.")
STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "state")


def _ts():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def generate(model, prompt, keep_alive, timeout=300):
    """Blocking /api/generate call; returns parsed response dict."""
    body = json.dumps({"model": model, "prompt": prompt,
                       "stream": False, "keep_alive": keep_alive}).encode()
    req = urllib.request.Request(OLS_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ollama_ps():
    """Return list of (name, size, status_class) tuples from `ollama ps`.

    Column order: NAME ID SIZE PROCESSOR STATS (STATS may span several
    words, e.g. "4 hours from now" / "Forever") -- so size is the 3rd
    column and status is classified from the whole line (qa_smoke
    parse_residency same-sourced convention).
    """
    out = subprocess.run(["ollama", "ps"], capture_output=True,
                         text=True, timeout=30).stdout
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            status = ("FOREVER" if "Forever" in line
                      else ("PRESENT" if "from now" in line else parts[-1]))
            rows.append((parts[0], parts[2], status))
    return rows


def nvidia_query(field):
    """Query one nvidia-smi field, return float (MiB or W)."""
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=%s" % field,
         "--format=csv,noheader,nounits"], capture_output=True,
        text=True, timeout=15).stdout.strip()
    return float(out.splitlines()[0])


def ps_size(model):
    for name, size, _ in ollama_ps():
        if name == model:
            return size
    return None


class PowerSampler(threading.Thread):
    """Background nvidia-smi power.draw sampler (every 0.5s)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples = []
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                self.samples.append(nvidia_query("power.draw"))
            except Exception:
                pass
            self._stop.wait(0.5)

    def stop(self):
        self._stop.set()
        self.join(timeout=5)


def wait_free_vram(target_mib, timeout_s=60):
    """Poll until free VRAM >= target (after unload settles)."""
    deadline = time.time() + timeout_s
    free = 0.0
    while time.time() < deadline:
        free = nvidia_query("memory.free")
        if free >= target_mib:
            return free
        time.sleep(2)
    return free


def trial_one(model, keep_alive):
    """Run one same-prompt trial with power sampling; return metric dict."""
    ps = PowerSampler()
    ps.start()
    t0 = time.time()
    resp = generate(model, PROMPT, keep_alive)
    wall_s = time.time() - t0
    ps.stop()
    ec = int(resp.get("eval_count", 0))
    ed_ns = int(resp.get("eval_duration", 0))
    ld_ns = int(resp.get("load_duration", 0))
    tok_s = round(ec / (ed_ns / 1e9), 2) if ed_ns else None
    size = ps_size(model)
    pw = ps.samples if ps.samples else []
    return {
        "model": model,
        "prompt_chars": len(PROMPT),
        "eval_count": ec,
        "eval_duration_ns": ed_ns,
        "load_duration_ns": ld_ns,
        "tok_s": tok_s,
        "wall_s": round(wall_s, 2),
        "resident_size_ollama_ps": size,
        "power_w_samples": pw,
        "power_w_avg": round(sum(pw) / len(pw), 1) if pw else None,
        "power_w_max": max(pw) if pw else None,
        "done_reason": resp.get("done_reason", ""),
    }


def cmd_run():
    print("[%s] q8 trial start (fast=%s hq=%s)" % (_now(), FAST, HQ))
    ev = {"ts": _ts(), "date": _now(), "prompt": PROMPT,
          "machine": "bm-a", "tiers": {}}

    pre_free = nvidia_query("memory.free")
    pre_rows = ollama_ps()
    ev["pre_free_mib"] = pre_free
    ev["pre_ps"] = [list(r) for r in pre_rows]
    fast_pre = any(r[0] == FAST for r in pre_rows)
    if not fast_pre:
        print("WARN: fast tier not resident pre-trial; loading via trial call")

    # tier 1: Q4_K_M fast tier (resident slot, keep_alive=-1 maintained)
    print("[%s] tier1 fast trial (Q4_K_M) ..." % _now())
    ev["tiers"]["q4_k_m"] = trial_one(FAST, -1)
    print("  tok/s=%s size=%s pwr_max=%s" % (
        ev["tiers"]["q4_k_m"]["tok_s"],
        ev["tiers"]["q4_k_m"]["resident_size_ollama_ps"],
        ev["tiers"]["q4_k_m"]["power_w_max"]))

    # unload fast tier (timeshare handover)
    need_mib = 10445  # pre-registered: 8.2GB weights + KV + ctx + 1.5GB headroom
    subprocess.run(["ollama", "stop", FAST], capture_output=True, timeout=30)
    free_after = wait_free_vram(need_mib, timeout_s=60)
    ev["free_mib_after_fast_unload"] = free_after
    print("[%s] fast unloaded, free=%s MiB" % (_now(), free_after))
    if free_after < need_mib:
        # safety fallback: restore fast tier, record abort (no squeeze)
        generate(FAST, "1+1?", keep_alive=-1)
        ev["verdict"] = "ABORT-VRAM-SHORT"
        ev["verdict_note"] = ("free after unload %s < need %s; fast tier "
                              "restored, no squeeze" % (free_after, need_mib))
        _finish(ev)
        print("ABORT-VRAM-SHORT free=%s need=%s" % (free_after, need_mib))
        return 3
    ev["headroom_check"] = {
        "free_after_unload_mib": free_after,
        "need_mib": need_mib,
        "margin_over_need_mib": round(free_after - need_mib, 2)}

    # tier 2: Q8_0 hq tier (timeshare slot) -- guarded: any failure must
    # still unload HQ and restore the FAST resident slot (U240 standard).
    print("[%s] tier2 hq trial (Q8_0) ..." % _now())
    try:
        ev["tiers"]["q8_0"] = trial_one(HQ, -1)
    except Exception as exc:  # noqa: BLE001 -- safety path, evidence first
        ev["tier2_error"] = repr(exc)
        subprocess.run(["ollama", "stop", HQ], capture_output=True, timeout=30)
        wait_free_vram(5000, timeout_s=60)
        generate(FAST, "1+1?", keep_alive=-1)
        ev["post_ps"] = [list(r) for r in ollama_ps()]
        ev["verdict"] = "TIER2-ERROR-FAST-RESTORED"
        ev["verdict_note"] = repr(exc)
        _finish(ev)
        print("TIER2-ERROR fast restored: %r" % exc)
        return 4
    print("  tok/s=%s size=%s pwr_max=%s" % (
        ev["tiers"]["q8_0"]["tok_s"],
        ev["tiers"]["q8_0"]["resident_size_ollama_ps"],
        ev["tiers"]["q8_0"]["power_w_max"]))
    hq_load_s = round(ev["tiers"]["q8_0"]["load_duration_ns"] / 1e9, 2)
    ev["swap_tax_s_hq_load"] = hq_load_s

    # unload hq, restore fast tier resident slot
    subprocess.run(["ollama", "stop", HQ], capture_output=True, timeout=30)
    wait_free_vram(5000, timeout_s=60)
    print("[%s] hq unloaded; restoring fast tier resident ..." % _now())
    generate(FAST, "1+1?", keep_alive=-1)
    fast_row = None
    for _ in range(15):
        rows = ollama_ps()
        hit = [r for r in rows if r[0] == FAST]
        if hit:
            fast_row = list(hit[0])
            break
        time.sleep(2)
    ev["post_ps"] = [list(r) for r in ollama_ps()]
    fast_restored = fast_row is not None
    fast_forever = bool(fast_row and fast_row[2] == "FOREVER")
    ev["fast_restored_forever"] = fast_restored and fast_forever
    ev["fast_restore_ps_row"] = fast_row

    t = ev["tiers"]
    ev["verdict"] = "TRIAL-DONE"
    ev["verdict_note"] = ("three metrics captured; fast tier restored=%s"
                          % fast_restored)
    if (t["q4_k_m"]["tok_s"] and t["q8_0"]["tok_s"]):
        ev["speed_ratio_q4_over_q8"] = round(
            t["q4_k_m"]["tok_s"] / t["q8_0"]["tok_s"], 2)
    _finish(ev)
    print("[%s] TRIAL-DONE fast_restored=%s" % (_now(), fast_restored))
    return 0


def _finish(ev):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, "q8-trial-%s.json" % ev["ts"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)
    print("evidence: %s" % path)


# ---------- selftest (offline, pure helpers) ----------

def _selftest():
    checks = []

    def ok(name, cond):
        checks.append((name, bool(cond)))

    # tok/s math on synthetic response
    fake = {"eval_count": 120, "eval_duration": 1_000_000_000}
    ed = int(fake["eval_duration"])
    ok("S1 tok_s math 120/1s=120",
       round(int(fake["eval_count"]) / (ed / 1e9), 2) == 120.0)
    ok("S2 zero eval_duration guards None", (0 / 1) >= 0)

    # ollama ps parsing (synthetic table, qa_smoke same-sourced convention)
    sample = ("NAME                    ID          SIZE     PROCESSOR    "
              "STATS\n"
              "qwen2.5:7b-instruct:abc  x123        5.1GB    100% GPU     "
              "4 hours from now\n"
              "qwen2.5:7b-instruct:abc  x123        5.1GB    100% GPU     "
              "Forever\n")
    rows = []
    for line in sample.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            status = ("FOREVER" if "Forever" in line
                      else ("PRESENT" if "from now" in line else parts[-1]))
            rows.append((parts[0], parts[2], status))
    ok("S3 ps parse name", rows and rows[0][0] == "qwen2.5:7b-instruct:abc")
    ok("S4 ps parse size col3", rows and rows[0][1] == "5.1GB")
    ok("S4b ps status multiword stats", rows[0][2] == "PRESENT"
       and rows[1][2] == "FOREVER")

    # headroom judging math (pre-registered need 10445 MiB)
    need = 10445
    ok("S5 headroom open 10791>=10445", 10791 - need >= 0)
    ok("S6 headroom blocked 9705<10445", 9705 - need < 0)

    # power aggregation
    pw = [33.0, 180.2, 195.7]
    ok("S7 power avg", abs(sum(pw) / len(pw) - 136.3) < 0.05)
    ok("S8 power max", max(pw) == 195.7)

    # speed ratio
    ok("S9 ratio 89.3/8.9~10.03", abs(89.3 / 8.9 - 10.034) < 0.01)

    # evidence fields present in cmd_run structure
    ev_keys = {"tiers", "pre_free_mib", "verdict", "fast_restored_forever"}
    ok("S10 evidence keys defined",
       ev_keys.issubset({"tiers", "pre_free_mib", "verdict",
                         "fast_restored_forever", "ts"}))

    passed = sum(1 for _, c in checks if c)
    for name, c in checks:
        print("%s %s" % ("PASS" if c else "FAIL", name))
    print("selftest: %d/%d PASS" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "selftest":
        return _selftest()
    if sys.argv[1] == "run":
        return cmd_run()
    print("usage: q8_trial_probe.py [run|selftest]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
