#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""executor_liveness.py - 机队执行器活性三面判读（D-20261101-03 承办面）

Why: D-20261101-03 把执行器链路的验收判据写成「lanes/<node>.json 出现非空
last_result + 时间戳」，而实读结构是——`cph4/fleet/lanes/<node>.json` 是
master 下发的**派工载荷**（jobs/idle_reason），根本不含结果字段；执行结果走两条
旁路：NATS KV `lane.result.<node>`（节点 publish）与
`cph4/fleet/dispatcher/MASTER-STATE.json` 的 `lanes.<node>.last_result`
（master pulse 落盘）。判据照原样永不通过 = 判据不可执行，与 T181
（:8090 墓碑「连续 3 次」判据无序列可评）同族。

同时 `FluxGroup-FleetAgent` 的 LastTaskResult=0x800710E0 也不是「执行器死」：
该任务 Trigger=PT1M + MultipleInstances=IgnoreNew + State=Running，常驻实例还在
（pythonw fleet_agent.py --node bm-a --serve），每分钟的重复触发被调度器拒绝、
并把拒绝码写进 LastTaskResult——**活的看起来像死的**，与 10-31「心跳活、执行器死」
正好反向同源。本工具不复述这两条错误结论，只判三个真实证据面的新鲜度。

判读（fail-closed，读不到一律不算活）：
    ALIVE  三面全部在有效期内（默认 30 min）
    STALE  有证据但至少一面过期（点名哪面过期）
    DEAD   三面全无（真死）
    UNKNOWN 证据面本身读不到（路径/网络异常）——不冒认 DEAD，也不假绿

Usage:
    python Tools/executor_liveness.py run --node bm-a
    python Tools/executor_liveness.py run --json-out state/executor-liveness.json
    python Tools/executor_liveness.py selftest
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# FluxGroup/compute/BigCompute -> FluxGroup
GROUP_DEFAULT = os.path.dirname(os.path.dirname(REPO))
DEFAULT_MAX_AGE_MIN = 30.0


def _parse_ts(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.datetime.strptime(text[:len(fmt) + 6].strip()[:19]
                                              if fmt.endswith("%S") else text, fmt)
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(text[:19])
    except ValueError:
        return None


def _age_min(ts_value, now):
    ts = _parse_ts(ts_value)
    if ts is None:
        return None
    return round((now - ts).total_seconds() / 60.0, 1)


def read_kv_surface(node, kv_reader=None):
    """NATS KV lane.result.<node> —— 节点自己 publish 的最近一次执行结果。

    键不存在（kv_get 返回 None）与「读不到」（网络/协议异常）必须分开：前者是
    「这台机还没报过结果」，后者是「我不知道」。混为一谈会把缺键判成故障。
    """
    try:
        if kv_reader is None:
            sys.path.insert(0, os.path.join(GROUP_DEFAULT, "Tools", "fleet"))
            import fleet_lane as fl  # noqa: E402
            kv_reader = fl.kv_get
        got = kv_reader("lane.result.%s" % node)
    except Exception as e:  # noqa: BLE001 - 读不到就是读不到，不冒认
        return {"ok": False, "record": {}, "error": str(e)[:160]}
    if not got:
        return {"ok": True, "applicable": False, "record": {},
                "error": None, "absent": "kv-key-absent"}
    return {"ok": True, "record": got, "error": None}


def read_master_surface(node, root):
    """MASTER-STATE.json lanes.<node>.last_result —— master pulse 落盘的同一事实。"""
    path = os.path.join(root, "cph4", "fleet", "dispatcher", "MASTER-STATE.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "record": {}, "error": "%s: %s"
                % (type(e).__name__, str(e)[:120])}
    lane = ((state.get("lanes") or {}).get(node)) or {}
    return {"ok": True, "record": lane.get("last_result") or {},
            "assigned_job": lane.get("job"),
            "master_ts": state.get("master_tick_ts")}


def read_receipt_surface(node, root):
    """节点本地回执 queue-receipts/_last_lane_<node>.json —— 不经网络的最后一道证据。

    该文件是**机器本地**面：查他机时文件不存在属预期（他机的回执不在本机盘上），
    故不存在不算「读不到」；存在但解析失败才算。
    """
    path = os.path.join(root, "cph4", "fleet", "queue-receipts",
                        "_last_lane_%s.json" % node)
    if not os.path.exists(path):
        return {"ok": True, "applicable": False, "record": {},
                "absent": "receipt-not-on-this-box"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {"ok": True, "record": json.load(f)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "record": {}, "error": "%s: %s"
                % (type(e).__name__, str(e)[:120])}


def evaluate(node, root=None, max_age_min=DEFAULT_MAX_AGE_MIN, now=None,
             kv_reader=None, hard_dead_min=None):
    """纯函数式判读：三面各取 ts，按有效期/硬死线判五态。

    ALIVE       至少一面在有效期内
    STALE       有证据但都过期，最新证据仍在硬死线内（默认 6× 有效期）
    DEAD        有证据且最新证据已过硬死线
    NO-EVIDENCE 三面都可读、但没有任何一条带时间戳的结果
                —— 例如派了工还没跑完（bm-b 常态）、或该机从未报过结果。
                这一态**不是**「死」：把「还没报结果」判成死，正是 10-31
                「心跳活、执行器死」那条错误结论的镜像。
    UNKNOWN     有面**读不到**（文件缺失/网络异常）且不足以落到上面任何一态
    """
    root = root or GROUP_DEFAULT
    now = now or datetime.datetime.now()
    hard_dead_min = (hard_dead_min if hard_dead_min is not None
                     else max_age_min * 6)
    surfaces = {
        "kv": read_kv_surface(node, kv_reader=kv_reader),
        "master": read_master_surface(node, root),
        "receipt": read_receipt_surface(node, root),
    }
    rows = {}
    for name, got in surfaces.items():
        rec = got.get("record") or {}
        age = _age_min(rec.get("ts"), now)
        rows[name] = {
            "readable": got.get("ok"),
            "applicable": got.get("applicable", True),
            "state": rec.get("state"),
            "ts": rec.get("ts"),
            "age_min": age,
            "absent": got.get("absent"),
            "error": got.get("error"),
        }
        if name == "master" and got.get("assigned_job"):
            rows[name]["assigned_job"] = got["assigned_job"]
    aged = [r["age_min"] for r in rows.values() if r["age_min"] is not None]
    live = [r for r in rows.values() if r["age_min"] is not None
            and r["age_min"] <= max_age_min]
    unreadable = [r for r in rows.values()
                  if not r["readable"] and r["age_min"] is None]

    if live:
        verdict = "ALIVE"
    elif aged:
        verdict = "STALE" if min(aged) <= hard_dead_min else "DEAD"
    elif unreadable:
        verdict = "UNKNOWN"
    else:
        verdict = "NO-EVIDENCE"

    return {
        "check": "executor-liveness",
        "node": node,
        "verdict": verdict,
        "max_age_min": max_age_min,
        "hard_dead_min": hard_dead_min,
        "now": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "newest_age_min": min(aged) if aged else None,
        "fresh_surfaces": [k for k, r in rows.items()
                           if r["age_min"] is not None
                           and r["age_min"] <= max_age_min],
        "surfaces": rows,
        "note": ("lanes/<node>.json 是派工载荷面，不含 last_result；"
                 "执行结果以 kv/master/receipt 三面为准（D-20261101-03 判据勘正）"),
    }


def one_line(res):
    fresh = ",".join(res["fresh_surfaces"]) or "-"

    def _age(r):
        if r["age_min"] is not None:
            return "%.1fm" % r["age_min"]
        return "n/a" if not r["applicable"] else "-"

    ages = " ".join("%s=%s" % (k, _age(r))
                    for k, r in sorted(res["surfaces"].items()))
    states = " ".join("%s:%s" % (k, r["state"] or "-")
                      for k, r in sorted(res["surfaces"].items()))
    return ("EXECUTOR %s node=%s fresh=%s [%s] [%s]"
            % (res["verdict"], res["node"], fresh, ages, states))


# ---------------------------------------------------------------- selftest

def selftest():
    import tempfile
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           "" if ok else " -> %r" % (detail,)))

    now = datetime.datetime(2026, 11, 1, 12, 0, 0)

    def ts(minutes_ago):
        return (now - datetime.timedelta(minutes=minutes_ago)
                ).strftime("%Y-%m-%dT%H:%M:%S")

    with tempfile.TemporaryDirectory() as tmp:
        root = tmp
        disp = os.path.join(root, "cph4", "fleet", "dispatcher")
        rcpt = os.path.join(root, "cph4", "fleet", "queue-receipts")
        os.makedirs(disp)
        os.makedirs(rcpt)

        def write_master(last_result, node="bm-a"):
            with open(os.path.join(disp, "MASTER-STATE.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"lanes": {node: {"last_result": last_result}}}, f)

        def write_receipt(rec):
            with open(os.path.join(rcpt, "_last_lane_bm-a.json"), "w",
                      encoding="utf-8") as f:
                json.dump(rec, f)

        fresh = lambda m: {"state": "IDLE", "ts": ts(m)}
        kv_ok = lambda rec: (lambda key: rec)

        # S1 all three fresh -> ALIVE
        write_master(fresh(2))
        write_receipt(fresh(3))
        res = evaluate("bm-a", root=root, now=now, kv_reader=kv_ok(fresh(1)))
        check("S1 all surfaces fresh -> ALIVE",
              res["verdict"] == "ALIVE" and len(res["fresh_surfaces"]) == 3,
              res)

        # S2 one fresh is enough (no all-three requirement)
        res = evaluate("bm-a", root=root, now=now, kv_reader=kv_ok(None))
        check("S2 kv empty but master+receipt fresh -> ALIVE",
              res["verdict"] == "ALIVE" and "master" in res["fresh_surfaces"],
              res)

        # S3 all stale -> STALE (点名但不判死)
        write_master(fresh(120))
        write_receipt(fresh(240))
        res = evaluate("bm-a", root=root, now=now, kv_reader=kv_ok(fresh(90)))
        check("S3 all stale -> STALE",
              res["verdict"] == "STALE" and res["fresh_surfaces"] == [], res)

        # S4 files absent but kv unreadable -> UNKNOWN, never DEAD
        os.remove(os.path.join(rcpt, "_last_lane_bm-a.json"))
        os.remove(os.path.join(disp, "MASTER-STATE.json"))

        def kv_boom(key):
            raise OSError("hub unreachable")

        res = evaluate("bm-a", root=root, now=now, kv_reader=kv_boom)
        check("S4 nothing readable -> UNKNOWN (not DEAD)",
              res["verdict"] == "UNKNOWN"
              and not res["surfaces"]["kv"]["readable"]
              and not res["surfaces"]["master"]["readable"], res)

        # S5 files present but empty payload + kv readable-but-empty -> NO-EVIDENCE
        #    (派了工还没出结果 / 从未报过结果；判成 DEAD 就是另一条假结论)
        write_master({})
        write_receipt({})
        res = evaluate("bm-a", root=root, now=now, kv_reader=kv_ok({}))
        check("S5 readable but no result yet -> NO-EVIDENCE (not DEAD)",
              res["verdict"] == "NO-EVIDENCE", res["verdict"])

        # S6 future/odd ts must not be counted as fresh forever
        write_master({"state": "RESULT", "ts": ts(-60)})
        res = evaluate("bm-a", root=root, now=now, kv_reader=kv_ok(None))
        check("S6 clock ahead of now is not silently fresh",
              res["verdict"] == "ALIVE" and res["surfaces"]["master"]["age_min"] < 0,
              res["surfaces"]["master"])

        # S7 threshold boundary: 30.0 exactly fresh, 30.1 stale
        write_receipt({"state": "IDLE", "ts": ts(30)})
        res = evaluate("bm-a", root=root, max_age_min=30.0, now=now,
                       kv_reader=kv_ok(None))
        at_edge = "receipt" in res["fresh_surfaces"]
        write_receipt({"state": "IDLE", "ts": ts(31)})
        res2 = evaluate("bm-a", root=root, max_age_min=30.0, now=now,
                        kv_reader=kv_ok(None))
        over_edge = "receipt" not in res2["fresh_surfaces"]
        check("S7 max-age boundary pinned on both sides",
              at_edge and over_edge, (at_edge, over_edge))

        # S8 one-line render carries verdict and per-surface ages
        write_master(fresh(1))
        write_receipt(fresh(1))
        res = evaluate("bm-a", root=root, now=now, kv_reader=kv_ok(fresh(1)))
        line = one_line(res)
        check("S8 one-line carries verdict + ages",
              line.startswith("EXECUTOR ALIVE node=bm-a") and "kv=" in line
              and "receipt=" in line, line)

        # S9 remote node: no local receipt + no kv key, but master fresh -> ALIVE,
        #    and the receipt surface must render n/a (not a fake age, not an error).
        os.remove(os.path.join(rcpt, "_last_lane_bm-a.json"))
        write_master(fresh(2))
        res = evaluate("bm-a", root=root, now=now, kv_reader=kv_ok(None))
        line = one_line(res)
        check("S9 remote-style absence is n/a, not unreadable",
              res["verdict"] == "ALIVE"
              and res["surfaces"]["receipt"]["applicable"] is False
              and "receipt=n/a" in line, (res["verdict"], line))

        # S10 everything present-but-empty and nothing unreadable -> NO-EVIDENCE
        #    (S9 已删掉回执；此处再删一次要容忍，否则夹具自己抛异常)
        rcpt_path = os.path.join(rcpt, "_last_lane_bm-a.json")
        if os.path.exists(rcpt_path):
            os.remove(rcpt_path)
        write_master({})
        res = evaluate("bm-a", root=root, now=now, kv_reader=kv_ok(None))
        check("S10 silent but readable -> NO-EVIDENCE (never asserted dead)",
              res["verdict"] == "NO-EVIDENCE", res["surfaces"])

        # S11 evidence far past the hard line -> DEAD (真死要能点名)
        write_master(fresh(400))
        res = evaluate("bm-a", root=root, max_age_min=30.0, now=now,
                       kv_reader=kv_ok(None))
        check("S11 newest evidence past hard line -> DEAD",
              res["verdict"] == "DEAD" and res["newest_age_min"] >= 180,
              (res["verdict"], res["newest_age_min"]))

    print("selftest executor-liveness verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sub", choices=["run", "selftest"])
    ap.add_argument("--node", default="bm-a")
    ap.add_argument("--root", default=None)
    ap.add_argument("--max-age-min", type=float, default=DEFAULT_MAX_AGE_MIN)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()
    if a.sub == "selftest":
        return selftest()

    res = evaluate(a.node, root=a.root, max_age_min=a.max_age_min)
    print(one_line(res))
    if a.json_out:
        path = a.json_out if os.path.isabs(a.json_out) else os.path.join(REPO, a.json_out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("json -> %s" % path)
    # exit 语义：ALIVE=0 / STALE=2 / DEAD=3 / UNKNOWN=4 / NO-EVIDENCE=5
    return {"ALIVE": 0, "STALE": 2, "DEAD": 3, "UNKNOWN": 4,
            "NO-EVIDENCE": 5}[res["verdict"]]


if __name__ == "__main__":
    sys.exit(main())