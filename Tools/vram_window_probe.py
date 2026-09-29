#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VRAM 真窗探针——T15 子项 2（Q8_0 三指标对比）/E17（Bonsai ngl99 全档重验）的真窗判据机械评估件。

边界与红线（本件只读，零风险面）：
- 只读探针：nvidia-smi + ollama /api/ps 直读；零显存分配、零模型加载、零派活、零 schtasks
  （DRY-RUN 观察期至 2026-10-05 合规·按需跑）。
- 与 gpu_idle_collector 分工：彼=15min tick 利用率 KPI 采集（30% 点名阈值面·samples.jsonl）；
  本件=两项预注册测试窗的判据评估（free_after_unload 数学）+历史证据累积，零重复采集面。
- 让路律：他司产线活载非本司可让面；7b 卸载路径仅「7b 维护窗」声明后可动用——W_ngl99_unload
  只报 OPEN_GATED 信息位，本件永不自动执行任何卸载/加载。
- 判据预注册（沿 T15/E17 既有口径·MiB）：
  W_q8_0      Q8_0 对比窗（7b 卸载路径）    need = 8397(8.2GiB 权重)+512(KV/ctx)+1536(1.5GiB headroom) = 10445，对 free_after_unload
  W_ngl99_r   Bonsai ngl99（7b 常驻路径）   need = 5667(5.95GB 权重·字节锚 5,946,648,928B)+512+1536 = 7715，对 free
  W_ngl99_u   Bonsai ngl99（维护窗卸载路径）同 7715 对 free_after_unload，输出=gated 信息位
用法：python Tools/vram_window_probe.py check|report [days]|selftest
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "state", "vram-window-log.jsonl")

Q8_WEIGHTS_MIB = 8397          # 8.2GiB（T15 既有口径）
BONSAI_WEIGHTS_MIB = 5667      # 5,946,648,928B（E17/T-31 字节锚）
KV_CTX_MIB = 512
HEADROOM_MIB = 1536            # 1.5GiB（T-31 J2 headroom 判据）
NEED_Q8 = Q8_WEIGHTS_MIB + KV_CTX_MIB + HEADROOM_MIB
NEED_NGL99 = BONSAI_WEIGHTS_MIB + KV_CTX_MIB + HEADROOM_MIB


def read_nvidia():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=20)
    parts = [p.strip() for p in out.stdout.strip().split(",")]
    return int(parts[0]), int(parts[1])


def read_ollama_resident():
    """返回 (resident_7b_mib, names)；/api/ps JSON 直读，失败=零驻留如实。"""
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return 0, ["<api/ps unreachable>"]
    names, mib = [], 0
    for m in data.get("models", []):
        names.append(m.get("name", "?"))
        if "7b" in m.get("name", ""):
            mib += int(m.get("size_vram", 0)) // (1024 * 1024)
    return mib, names


def build_record(used, total, res7):
    free = total - used
    free_after = free + (res7 if res7 else 0)
    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "used_mib": used, "total_mib": total, "free_mib": free,
        "resident_7b_mib": res7, "free_after_unload_mib": free_after,
        "w_q8_0": "OPEN" if free_after >= NEED_Q8 else "BLOCKED",
        "w_ngl99_resident": "OPEN" if free >= NEED_NGL99 else "BLOCKED",
        "w_ngl99_unload": ("OPEN_GATED" if free_after >= NEED_NGL99 else "BLOCKED"),
    }


def verdict_line(rec):
    q8 = rec["w_q8_0"]
    q8_note = ("need %d, short %d" % (NEED_Q8, NEED_Q8 - rec["free_after_unload_mib"])) if q8 == "BLOCKED" else "OPEN"
    nr = rec["w_ngl99_resident"]
    nr_note = ("need %d, short %d" % (NEED_NGL99, NEED_NGL99 - rec["free_mib"])) if nr == "BLOCKED" else "OPEN"
    nu = rec["w_ngl99_unload"]
    nu_note = nu + "(gated 7b-maint-window)" if nu == "OPEN_GATED" else ("need %d, short %d" % (NEED_NGL99, NEED_NGL99 - rec["free_after_unload_mib"]))
    return ("VRAM probe %s: used=%d/%d free=%d 7b_res=%d free_after_unload=%d | "
            "W_q8_0=%s(%s) W_ngl99_resident=%s(%s) W_ngl99_unload=%s" %
            (rec["ts"], rec["used_mib"], rec["total_mib"], rec["free_mib"],
             rec["resident_7b_mib"], rec["free_after_unload_mib"], q8, q8_note, nr, nr_note, nu_note))


def cmd_check():
    used, total = read_nvidia()
    res7, names = read_ollama_resident()
    rec = build_record(used, total, res7)
    rec["resident_models"] = ",".join(names)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(verdict_line(rec))
    return 0


def cmd_report(days=7):
    if not os.path.exists(LOG):
        print("report: log not found (run check first)")
        return 0
    since = datetime.now() - timedelta(days=days)
    recs = []
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                    if datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S") >= since:
                        recs.append(r)
                except Exception:
                    continue
    if not recs:
        print("report: no records in last %dd" % days)
        return 0
    span = "%s..%s" % (recs[0]["ts"], recs[-1]["ts"])
    print("VRAM window report n=%d span=%s (last %dd)" % (len(recs), span, days))
    for key, label in (("w_q8_0", "W_q8_0 Q8_0 对比窗"),
                       ("w_ngl99_resident", "W_ngl99_resident ngl99 常驻路径"),
                       ("w_ngl99_unload", "W_ngl99_unload ngl99 维护窗路径")):
        opens = [r for r in recs if str(r.get(key, "")).startswith("OPEN")]
        last = opens[-1]["ts"] if opens else "-"
        print("  %s: open=%d/%d last_open=%s" % (label, len(opens), len(recs), last))
    print("  free_after_unload max=%d MiB min=%d MiB" %
          (max(r["free_after_unload_mib"] for r in recs), min(r["free_after_unload_mib"] for r in recs)))
    return 0


def cmd_selftest():
    fails = []
    # S1 判据数学边界（10445 开/10444 关·7715 同律）
    r = build_record(12282 - 10445 + 5222, 12282, 5222)
    if r["w_q8_0"] != "OPEN":
        fails.append("S1 open boundary")
    r = build_record(7060, 12282, 5222)  # free=5222 → free_after=10444
    if r["w_q8_0"] != "BLOCKED":
        fails.append("S1 block boundary")
    # S2 驻留缺失=free_after==free
    r = build_record(7456, 12282, 0)
    if r["free_after_unload_mib"] != r["free_mib"] or r["w_ngl99_unload"] != "BLOCKED":
        fails.append("S2 no-resident")
    # S3 常驻路径对 free（非 free_after）
    r = build_record(12282 - 7716, 12282, 5222)
    if r["w_ngl99_resident"] != "OPEN":
        fails.append("S3 resident-path math")
    # S4 确定性：同输入同记录
    a = build_record(7456, 12282, 5222)
    a["ts"] = "X"
    b = build_record(7456, 12282, 5222)
    b["ts"] = "X"
    if a != b:
        fails.append("S4 determinism")
    # S5 report 聚合（临时日志零污染）
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "log.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(build_record(7456, 12282, 5222)) + "\n")
            rec_open = build_record(100, 12282, 5222)
            rec_open["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(json.dumps(rec_open) + "\n")
        saved = LOG
        globals()["LOG"] = p
        import io
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        cmd_report(7)
        sys.stdout = old
        globals()["LOG"] = saved
        out = buf.getvalue()
        if "n=2" not in out or "open=1/2" not in out or "open=2/2" not in out:
            fails.append("S5 report aggregation")
    print("selftest: %s (%s)" % ("PASS" if not fails else "FAIL " + ";".join(fails),
                                "check/report/selftest·needs q8=%d ngl99=%d" % (NEED_Q8, NEED_NGL99)))
    return 0 if not fails else 1


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        return cmd_check()
    if cmd == "report":
        return cmd_report(int(sys.argv[2]) if len(sys.argv) > 2 else 7)
    if cmd == "selftest":
        return cmd_selftest()
    print("usage: vram_window_probe.py check|report [days]|selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
