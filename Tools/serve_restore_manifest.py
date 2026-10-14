#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve 模型恢复清单件（M71 步② 夜窗恢复预备·10-14 12:2x 哨兵轮落盘）。

事故锚：10-14 12:02:49-50 九模型族被 9× /api/delete 本地连发删除仅剩 bge-m3
（E65 自滚动基线 10 模型对照）→ 四兄弟司 codely 循环 /v1 404 风暴+8792 受损。
处置律（12:1x 轮 M71 步①定谳）：白窗零重拉（CEO 工作窗让路律·删除为人工面
意图不明），恢复=夜窗按消费方优先序。

本件=夜窗一命令恢复正身：
  manifest（默认 dry-run）= 基线(E65 自滚动 JSON) vs 现场(/api/tags 只读) 差集
    → 消费方优先序恢复清单 + 逐条 pull 命令 + 证据 JSON 落 state/；
  apply = 夜窗执行（本地钟 22:00-08:59 之外拒绝·--force 不得用于白窗让路面），
    串行 ollama pull（带宽礼貌+逐模型验活），逐条 /api/tags 复核后进位。

消费方优先序依据（型号归因通道缺口如实：server.log 不落 404 请求型号，
本表=在册实锚而非风暴客户端直证）：
  1 qwen2.5:7b-instruct  = 工作马（KeepWarm pin+U240 SLA 面·M71 行首拉条款）
  2 qwen2.5:14b-8k      = 10-10 在用观测实锚（heartbeat 85% CPU/GPU 用后 Stopping）
  3 qwen3:8b            = 基线在册（qwen3 系四司循环族·夜窗复核后可调序）
  4 qwen3-8b-ud:q4_k_xl = 同上
  5 qwen3.5:9b-16k      = 同上
  6 qwen3.5:9b          = 同上
  7 qwen3-vl:4b         = 同上
  8 qwen2.5:14b         = 同上（14b-8k 派生底模）
  9 qwen3.8:27b-8k      = 同上
恢复前前置（12:2x 勘正行条款）：先核删除是否为某窗受令清理批——若为受令清理，
本清单暂停呈批复序，禁机械重拉。
"""
import argparse
import glob
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
TAGS_URL = "http://127.0.0.1:11434/api/tags"

PRIORITY = [
    "qwen2.5:7b-instruct",
    "qwen2.5:14b-8k",
    "qwen3:8b",
    "qwen3-8b-ud:q4_k_xl",
    "qwen3.5:9b-16k",
    "qwen3.5:9b",
    "qwen3-vl:4b",
    "qwen2.5:14b",
    "qwen3.8:27b-8k",
]

NIGHT_WINDOW = (22, 9)  # 允许 apply 的本地钟区间：>=22:00 或 <09:00


def _eprint(*a):
    sys.stderr.write(" ".join(str(x) for x in a) + "\n")


def load_baseline(path=None):
    """基线清单=E65 自滚动基线件（serve-contract-audit-*.json 的 inventory）。"""
    if path is None:
        cands = sorted(glob.glob(os.path.join(STATE, "serve-contract-audit-*.json")),
                       key=os.path.getmtime)
        if not cands:
            raise SystemExit("no baseline file: state/serve-contract-audit-*.json")
        path = cands[-1]
    with io.open(path, "r", encoding="utf-8") as f:
        j = json.load(f)
    inv = j.get("inventory") or []
    return path, [m for m in inv if isinstance(m, str) and m]


def live_tags(url=TAGS_URL, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8"))
        return True, [m.get("name", "") for m in (j.get("models") or [])]
    except Exception as e:  # noqa: BLE001 - 探针面如实出状态
        return False, "tags unreachable: %s" % e


def ordered_missing(baseline, current):
    cur = set(current)
    missing = [m for m in baseline if m not in cur]
    def key(m):
        try:
            return (0, PRIORITY.index(m))
        except ValueError:
            return (1, 0)
    return sorted(missing, key=key)


def night_ok(now=None):
    h = (now or time.localtime()).tm_hour
    return h >= NIGHT_WINDOW[0] or h < NIGHT_WINDOW[1]


def cmd_manifest(args):
    bpath, baseline = load_baseline(args.baseline)
    ok, cur = live_tags()
    if not ok:
        _eprint("FAIL live /api/tags: %s" % cur)
        return 3
    missing = ordered_missing(baseline, cur)
    extra = [m for m in cur if m not in set(baseline)]
    ts = time.strftime("%Y%m%d-%H%M%S")
    payload = {
        "ts": ts,
        "baseline_file": os.path.relpath(bpath, ROOT),
        "baseline_n": len(baseline),
        "live_n": len(cur),
        "live": cur,
        "missing_ordered": missing,
        "extra_not_in_baseline": extra,
        "pull_commands": ["ollama pull %s" % m for m in missing],
        "night_window_now": night_ok(),
        "guard": "apply 仅夜窗（22:00-08:59）·恢复前先核删除是否受令清理批",
    }
    out = os.path.join(STATE, "serve-restore-manifest-%s.json" % ts)
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("RESTORE-MANIFEST baseline=%d live=%d missing=%d -> %s"
          % (len(baseline), len(cur), len(missing), os.path.relpath(out, ROOT)))
    for m in missing:
        print("  pull-order %s" % m)
    if extra:
        print("  extra-not-in-baseline: %s" % ",".join(extra))
    print("night_window_now=%s (apply %s)" % (night_ok(), "ALLOWED" if night_ok() else "REFUSED"))
    return 0


def cmd_apply(args):
    if not night_ok():
        _eprint("REFUSED: 白窗让路面（夜窗 22:00-08:59 外禁 apply·M71 步①处置律）")
        return 4
    bpath, baseline = load_baseline(args.baseline)
    ok, cur = live_tags()
    if not ok:
        _eprint("FAIL live /api/tags: %s" % cur)
        return 3
    missing = ordered_missing(baseline, cur)
    if not missing:
        print("ALREADY-RESTORED nothing missing")
        return 0
    ts = time.strftime("%Y%m%d-%H%M%S")
    logf = os.path.join(STATE, "serve-restore-apply-%s.log" % ts)
    done, failed = [], []
    with io.open(logf, "w", encoding="utf-8") as lf:
        lf.write("apply start ts=%s missing=%d\n" % (ts, len(missing)))
        for m in missing:
            lf.write("pull %s ...\n" % m)
            lf.flush()
            p = subprocess.run(["ollama", "pull", m], capture_output=True, text=True)
            lf.write("pull %s exit=%d\n" % (m, p.returncode))
            lf.flush()
            ok2, cur2 = live_tags()
            verified = ok2 and m in set(cur2)
            lf.write("verify %s -> %s\n" % (m, "OK" if verified else "MISSING"))
            (done if verified else failed).append(m)
    print("APPLY done=%d failed=%d -> %s" % (len(done), len(failed), os.path.relpath(logf, ROOT)))
    return 0 if not failed else 5


def _selftest():
    fails = []
    # J1 基线解析夹具
    tmp = os.path.join(STATE, "_fixture-baseline.json")
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump({"inventory": ["bge-m3:latest", "qwen2.5:7b-instruct", "zz-other:1b"]}, f)
    _, b1 = load_baseline(tmp)
    if b1 != ["bge-m3:latest", "qwen2.5:7b-instruct", "zz-other:1b"]:
        fails.append("J1 baseline parse")
    # J2 差集+优先序（工作马首拉·未知模型垫底·字典序稳定）
    cur = ["bge-m3:latest"]
    miss = ordered_missing(b1, cur)
    if miss != ["qwen2.5:7b-instruct", "zz-other:1b"]:
        fails.append("J2 ordered missing: %s" % miss)
    # J3 优先序表健康（零重复+工作马居首）
    if len(PRIORITY) != len(set(PRIORITY)):
        fails.append("J3a PRIORITY 重复项")
    if PRIORITY[0] != "qwen2.5:7b-instruct":
        fails.append("J3b 工作马非首拉")
    # J4 夜窗闸四时点（22:00-08:59 区间含 08:59·09:00 起拒绝）
    import datetime as dt
    if night_ok(dt.datetime(2026, 10, 14, 23, 0).timetuple()) is not True:
        fails.append("J4a 23:00 应放行")
    if night_ok(dt.datetime(2026, 10, 14, 12, 30).timetuple()) is not False:
        fails.append("J4b 12:30 应拒绝")
    if night_ok(dt.datetime(2026, 10, 15, 9, 0).timetuple()) is not False:
        fails.append("J4c 09:00 应拒绝")
    if night_ok(dt.datetime(2026, 10, 15, 0, 5).timetuple()) is not True:
        fails.append("J4d 00:05 应放行")
    # J5 live_tags 坏 URL 如实出 False
    ok3, msg3 = live_tags(url="http://127.0.0.1:1/api/tags", timeout=1)
    if ok3 or "unreachable" not in str(msg3):
        fails.append("J5 live_tags fail-path")
    os.remove(tmp)
    if fails:
        _eprint("SELFTEST-FAIL %s" % "; ".join(fails))
        return 1
    print("SELFTEST-PASS 5/5 (J1 parse/J2 order/J4 night-gate x3/J5 fail-path)")
    return 0


def main():
    ap = argparse.ArgumentParser(description="serve 模型恢复清单件（M71 步②）")
    ap.add_argument("cmd", nargs="?", default="manifest",
                    choices=["manifest", "apply", "selftest"])
    ap.add_argument("--baseline", default=None,
                    help="基线 JSON 路径（默认=state/ 最新 serve-contract-audit-*.json）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        return _selftest()
    if args.cmd == "manifest":
        return cmd_manifest(args)
    return cmd_apply(args)


if __name__ == "__main__":
    sys.exit(main())
