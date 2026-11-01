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

追加面（CEO 2026-11-01 10:4x 模型与执行政策令 ⑤「四信号上报（hb/agent上报/claim/
receipt），缺一不得称健康」）：
    `signals` 子命令把四条信号各自绑到一个**真实存在的**证据面，逐条判 FRESH/STALE/
    ABSENT/N-A/UNREADABLE，并按字面执行「缺一不得称健康」——只有 4/4 全鲜才给
    SIGNALS-GREEN，其余一律 INCOMPLETE（点名缺哪条），不拿「差不多」冒充健康。
    映射（每条都指到生产代码的写入行，禁凭空造信号名）：
        hb        KV `state.<node>`                ← fleet_hb.py 心跳发布
        agent上报 KV `lane.result.<node>`          ← lane_agent.py 执行链自报
        claim     本机 `queue-receipts/_last_lane_<node>.json` 的 job 字段
        receipt   本机 `queue-receipts/<job>-<node>.json` 按单回执
    后两条是**机器本地面**：查他机时不存在属预期（n/a），不能算「缺信号」，也不能
    因为查不到就判健康——故他机最优结论只能是 INCOMPLETE（本地两条 n/a）。

Usage:
    python Tools/executor_liveness.py run --node bm-a
    python Tools/executor_liveness.py run --json-out state/executor-liveness.json
    python Tools/executor_liveness.py signals --node bm-a
    python Tools/executor_liveness.py signals --nodes bm-a,bm-b,bm-c
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
    """解析证据时间戳，统一折算到本机时区后返回 naive datetime。

    实读三形态（11-01 全机取证）：`2026-11-01T12:45:13.030`（本机 naive）/
    `2026-11-01T04:45:34.133Z`（bm-m UTC 带 Z）/ `2026-10-31T22:43+08:00`（带偏移）。
    后两种若被当成本机时间直接比，会把主控判成陈旧 8 小时、或直接 TypeError 崩掉
    ——「读表读成假死」正是本工具要防的那类自欺，故时间戳一律先归一到本地再判。
    """
    if not value:
        return None
    text = str(value).strip()
    try:
        iso = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.datetime.strptime(text[:len(fmt) + 6].strip()[:19]
                                              if fmt.endswith("%S") else text, fmt)
        except ValueError:
            continue
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


# ------------------------------------------------- 四信号（CEO 2026-11-01 令 ⑤）

SIGNAL_ORDER = ("hb", "agent", "claim", "receipt")

# 每条信号指向的真实写入点（改这里前先回读生产代码，禁凭印象改映射）
SIGNAL_SOURCE = {
    "hb": "KV state.<node> (fleet_hb.py 心跳发布)",
    "agent": "KV lane.result.<node> (lane_agent.py 执行链自报)",
    "claim": "queue-receipts/_last_lane_<node>.json job 字段 (lane_agent.py write_last)",
    "receipt": "queue-receipts/<job>-<node>.json 按单回执 (lane_agent.py run_job)",
}


def _kv_get(key, kv_reader=None):
    """读一个 KV 键。『键不存在』与『读不到』分开：前者是没报过，后者是我不知道。"""
    try:
        if kv_reader is None:
            sys.path.insert(0, os.path.join(GROUP_DEFAULT, "Tools", "fleet"))
            import fleet_lane as fl  # noqa: E402
            kv_reader = fl.kv_get
        got = kv_reader(key)
    except Exception as e:  # noqa: BLE001 - 读不到不冒认
        return {"ok": False, "record": {}, "error": str(e)[:160]}
    if not got:
        return {"ok": True, "applicable": False, "record": {}, "absent": "kv-key-absent"}
    return {"ok": True, "record": got}


def read_claim_surface(node, root):
    """claim 面：本机 _last_lane_<node>.json。有 job 字段才算真的领到活。

    lane_agent 每轮都会写这个文件，IDLE 轮也写——所以「文件在」不等于「领到活」，
    必须看 job 非空。把 IDLE 轮的写盘当成领取证据，就是又一次「活的看起来像死的」
    的镜像错误。
    """
    path = os.path.join(root, "cph4", "fleet", "queue-receipts",
                        "_last_lane_%s.json" % node)
    if not os.path.exists(path):
        return {"ok": True, "applicable": False, "record": {},
                "absent": "last-lane-not-on-this-box"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "record": {}, "error": "%s: %s"
                % (type(e).__name__, str(e)[:120])}
    if not rec.get("job"):
        return {"ok": True, "applicable": False, "record": rec,
                "absent": "no-job-claimed (state=%s)" % (rec.get("state") or "-")}
    return {"ok": True, "record": rec}


def read_receipt_file_surface(node, root):
    """receipt 面：按单回执 <job>-<node>.json，取最新一枚。"""
    d = os.path.join(root, "cph4", "fleet", "queue-receipts")
    if not os.path.isdir(d):
        return {"ok": True, "applicable": False, "record": {},
                "absent": "receipts-dir-absent"}
    best = None
    try:
        names = [n for n in os.listdir(d)
                 if n.endswith("-%s.json" % node) and not n.startswith("_")]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "record": {}, "error": "%s: %s"
                % (type(e).__name__, str(e)[:120])}
    for name in names:
        path = os.path.join(d, name)
        try:
            # utf-8-sig：实读多枚回执带 BOM（OB-20261031-*-bm-b.json 等），用纯 utf-8
            # 读会整枚被判「不可解」而静默丢掉真实证据。
            with open(path, "r", encoding="utf-8-sig") as f:
                rec = json.load(f)
        except Exception:  # noqa: BLE001 - 单枚坏件不拖垮整条信号
            continue
        rec.setdefault("id", name[: -len("-%s.json" % node)])
        ts = _parse_ts(rec.get("ts"))
        if ts is None:
            # 没有 ts 的回执（如卡面/对齐类件）用落盘 mtime 兜底，并标明来源，
            # 不让「有份回执但没有时间字段」变成零证据。
            try:
                ts = datetime.datetime.fromtimestamp(os.path.getmtime(path))
                rec["_ts_source"] = "file-mtime"
            except Exception:  # noqa: BLE001
                continue
        else:
            rec["_ts_source"] = "record-ts"
        if best is None or ts > best[0]:
            best = (ts, rec)
    if best is None:
        return {"ok": True, "applicable": False, "record": {},
                "absent": "no-job-receipt-on-this-box" if not names
                else "receipts-present-but-no-parseable-ts"}
    rec = dict(best[1])
    if not rec.get("ts"):
        rec["ts"] = best[0].strftime("%Y-%m-%dT%H:%M:%S")
    return {"ok": True, "record": rec}


def _signal_row(got, now, max_age_min):
    rec = got.get("record") or {}
    age = _age_min(rec.get("ts"), now)
    absent = got.get("absent")
    # N-A（本机不适用）与 ABSENT（该有却没有）必须分开：
    #   他机查不到 claim/receipt、本机 IDLE 轮没有 job —— 是不适用，不是缺失；
    #   钥匙（kv key）不存在、回执目录有但零可解件 —— 是缺失。
    na_reasons = ("last-lane-not-on-this-box", "no-job-claimed")
    if not got.get("ok"):
        state = "UNREADABLE"
    elif absent and absent.startswith(na_reasons):
        state = "N-A"
    elif age is None:
        state = "ABSENT"
    else:
        state = "FRESH" if age <= max_age_min else "STALE"
    return {"state": state, "ts": rec.get("ts"), "age_min": age,
            "absent": absent, "error": got.get("error"),
            "job": rec.get("job") or rec.get("id"), "rc": rec.get("rc")}


def evaluate_signals(node, root=None, max_age_min=DEFAULT_MAX_AGE_MIN, now=None,
                     kv_reader=None, hard_dead_min=None):
    """四条信号逐条判，并字面执行「缺一不得称健康」。

    SIGNALS-GREEN       4/4 FRESH（唯一的「健康」）
    SIGNALS-INCOMPLETE  有鲜证据但不足四条（点名缺哪条 + 是过期/无证据/本机不适用）
    SIGNALS-STALE       四条都有证据但全部过期（仍在硬死线内）
    SIGNALS-DEAD        最新证据已过硬死线
    SIGNALS-UNKNOWN     一条都读不到（且没有可读的空证据面）
    """
    root = root or GROUP_DEFAULT
    now = now or datetime.datetime.now()
    hard_dead_min = (hard_dead_min if hard_dead_min is not None
                     else max_age_min * 6)
    got = {
        "hb": _kv_get("state.%s" % node, kv_reader),
        "agent": _kv_get("lane.result.%s" % node, kv_reader),
        "claim": read_claim_surface(node, root),
        "receipt": read_receipt_file_surface(node, root),
    }
    rows = {name: _signal_row(got[name], now, max_age_min) for name in SIGNAL_ORDER}
    fresh = [n for n in SIGNAL_ORDER if rows[n]["state"] == "FRESH"]
    stale = [n for n in SIGNAL_ORDER if rows[n]["state"] == "STALE"]
    unreadable = [n for n in SIGNAL_ORDER if rows[n]["state"] == "UNREADABLE"]
    aged = [rows[n]["age_min"] for n in SIGNAL_ORDER
            if rows[n]["age_min"] is not None]

    if len(fresh) == len(SIGNAL_ORDER):
        verdict = "SIGNALS-GREEN"
    elif fresh:
        verdict = "SIGNALS-INCOMPLETE"
    elif aged:
        verdict = "SIGNALS-STALE" if min(aged) <= hard_dead_min else "SIGNALS-DEAD"
    elif unreadable and len(unreadable) == len(SIGNAL_ORDER):
        verdict = "SIGNALS-UNKNOWN"
    elif unreadable:
        verdict = "SIGNALS-UNKNOWN"
    else:
        verdict = "SIGNALS-INCOMPLETE"   # 可读但零证据 = 缺四，不是死

    return {
        "check": "executor-signals",
        "node": node,
        "verdict": verdict,
        "healthy": verdict == "SIGNALS-GREEN",
        "max_age_min": max_age_min,
        "hard_dead_min": hard_dead_min,
        "now": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "ok_count": len(fresh),
        "missing": [n for n in SIGNAL_ORDER if n not in fresh],
        "fresh_signals": fresh,
        "signals": rows,
        "sources": SIGNAL_SOURCE,
        "note": ("四信号=CEO 2026-11-01 令 ⑤；claim/receipt 是机器本地面，"
                 "查他机恒为 N-A，故他机最优只能到 INCOMPLETE"),
    }


def signals_one_line(res):
    def _cell(r):
        if r["age_min"] is not None:
            return "%s=%.1fm" % (r["state"][:6], r["age_min"])
        return "%s" % r["state"]

    cells = " ".join("%s:%s" % (n, _cell(res["signals"][n])) for n in SIGNAL_ORDER)
    miss = ",".join(res["missing"]) or "-"
    return ("SIGNALS %s node=%s ok=%d/4 missing=%s [%s]"
            % (res["verdict"], res["node"], res["ok_count"], miss, cells))


# ---------------------------------------------------------------- selftest

def selftest_signals():
    """四信号判读夹具：既钉死「缺一不得称健康」，也钉死「查不到≠死」。"""
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
        rcpt = os.path.join(root, "cph4", "fleet", "queue-receipts")
        os.makedirs(rcpt)

        def kv_all(hb_min, agent_min):
            vals = {}
            if hb_min is not None:
                vals["state.bm-a"] = {"ts": ts(hb_min), "node": "bm-a"}
            if agent_min is not None:
                vals["lane.result.bm-a"] = {"ts": ts(agent_min), "state": "RESULT",
                                            "job": "J-1"}
            return lambda key: vals.get(key)

        def write_last(job=None, state="IDLE", minutes_ago=1):
            rec = {"ts": ts(minutes_ago), "node": "bm-a", "state": state}
            if job:
                rec["job"] = job
            with open(os.path.join(rcpt, "_last_lane_bm-a.json"), "w",
                      encoding="utf-8") as f:
                json.dump(rec, f)

        def write_receipt(minutes_ago=1, jid="J-1"):
            with open(os.path.join(rcpt, "%s-bm-a.json" % jid), "w",
                      encoding="utf-8") as f:
                json.dump({"id": jid, "node": "bm-a", "rc": 0,
                           "ts": ts(minutes_ago)}, f)

        # S12 四信号全鲜 = 唯一健康态
        write_last("J-1", "RESULT", 1)
        write_receipt(1)
        res = evaluate_signals("bm-a", root=root, now=now,
                               kv_reader=kv_all(1, 2))
        check("S12 four fresh signals -> SIGNALS-GREEN (only healthy state)",
              res["verdict"] == "SIGNALS-GREEN" and res["ok_count"] == 4
              and res["healthy"] is True, res["verdict"])

        # S13 缺回执 = 不得称健康（CEO「缺一不得称健康」的字面执行）
        os.remove(os.path.join(rcpt, "J-1-bm-a.json"))
        res = evaluate_signals("bm-a", root=root, now=now,
                               kv_reader=kv_all(1, 2))
        check("S13 missing receipt -> INCOMPLETE, never GREEN",
              res["verdict"] == "SIGNALS-INCOMPLETE" and res["healthy"] is False
              and res["missing"] == ["receipt"], (res["verdict"], res["missing"]))

        # S14 IDLE 轮的 _last_lane 写盘不算领取证据
        write_receipt(1)
        write_last(None, "IDLE", 1)
        res = evaluate_signals("bm-a", root=root, now=now,
                               kv_reader=kv_all(1, 2))
        check("S14 idle write is not a claim",
              res["signals"]["claim"]["state"] == "N-A"
              and "no-job-claimed" in (res["signals"]["claim"]["absent"] or "")
              and res["verdict"] == "SIGNALS-INCOMPLETE",
              res["signals"]["claim"])

        # S15 他机：本地两面查不到，不许判绿也不许判死。
        #     注意诚实边界：本机上「文件不在」与「他机文件不在这台盘」不可区分，
        #     故 receipt 落 ABSENT、claim 落 N-A（后者 absent 语义明确），结论同为
        #     INCOMPLETE —— 缺信号时**只**能说缺，不能顺势推健康或死亡。
        os.remove(os.path.join(rcpt, "_last_lane_bm-a.json"))
        os.remove(os.path.join(rcpt, "J-1-bm-a.json"))
        res = evaluate_signals("bm-a", root=root, now=now,
                               kv_reader=kv_all(1, 2))
        check("S15 local-only surfaces absent -> INCOMPLETE (never GREEN, never DEAD)",
              res["verdict"] == "SIGNALS-INCOMPLETE"
              and res["signals"]["claim"]["state"] == "N-A"
              and res["signals"]["receipt"]["state"] in ("N-A", "ABSENT"),
              (res["verdict"], res["signals"]["claim"]["state"],
               res["signals"]["receipt"]["state"]))

        # S16 可读但零证据 = 缺四，不是死
        res = evaluate_signals("bm-a", root=root, now=now,
                               kv_reader=lambda key: None)
        check("S16 readable but no evidence -> INCOMPLETE (not DEAD)",
              res["verdict"] == "SIGNALS-INCOMPLETE" and res["ok_count"] == 0,
              res["verdict"])

        # S17 KV 两条都读不到且本地无件 = UNKNOWN（不冒认 DEAD）
        def kv_boom(key):
            raise OSError("hub unreachable")

        res = evaluate_signals("bm-a", root=root, now=now, kv_reader=kv_boom)
        check("S17 all unreadable -> SIGNALS-UNKNOWN (not DEAD)",
              res["verdict"] == "SIGNALS-UNKNOWN"
              and res["signals"]["hb"]["state"] == "UNREADABLE", res["verdict"])

        # S18 四条全过期 -> STALE；过硬死线 -> DEAD（真死要能点名）
        os.makedirs(rcpt, exist_ok=True)
        write_last("J-9", "RESULT", 90)
        write_receipt(100, jid="J-9")
        res = evaluate_signals("bm-a", root=root, now=now, max_age_min=30.0,
                               kv_reader=kv_all(120, 150))
        stale_ok = res["verdict"] == "SIGNALS-STALE"
        write_last("J-9", "RESULT", 400)
        write_receipt(500, jid="J-9")
        res2 = evaluate_signals("bm-a", root=root, now=now, max_age_min=30.0,
                                kv_reader=kv_all(400, 450))
        check("S18 all stale -> STALE, past hard line -> DEAD",
              stale_ok and res2["verdict"] == "SIGNALS-DEAD",
              (res["verdict"], res2["verdict"]))

        # S19 单行渲染带 ok=n/4 与缺失点名
        write_last("J-1", "RESULT", 1)
        write_receipt(1)
        res = evaluate_signals("bm-a", root=root, now=now, kv_reader=kv_all(1, 1))
        line = signals_one_line(res)
        check("S19 one-line carries ok=n/4 and missing list",
              line.startswith("SIGNALS SIGNALS-GREEN node=bm-a ok=4/4")
              and "missing=-" in line, line)

    # S20 实读三形态时间戳：naive / Z（bm-m UTC）/ +08:00 偏移都要能解析，
        #    且必须归一到本地后再比——否则主控被判陈旧 8h，或直接 TypeError 崩掉。
        p_naive = _parse_ts("2026-11-01T12:45:13.030")
        p_z = _parse_ts("2026-11-01T04:45:34.133Z")
        p_off = _parse_ts("2026-10-31T22:43+08:00")
        p_utc = _parse_ts("2026-11-01T04:45:34.133+00:00")
        check("S20 Z / offset / naive ts all parse to local-naive (no crash, no 8h fake staleness)",
              p_naive is not None and p_z is not None and p_off is not None
              and p_z.tzinfo is None and p_off.tzinfo is None
              and p_z == p_utc, (p_naive, p_z, p_off))

        # S21 带 BOM 的回执不能被静默丢掉（实读 OB-20261031-*-bm-b.json 即带 BOM）
        with open(os.path.join(rcpt, "BOM-JOB-bm-a.json"), "wb") as f:
            f.write(b"\xef\xbb\xbf" + json.dumps(
                {"id": "BOM-JOB", "node": "bm-a", "rc": 0,
                 "ts": ts(2)}, ensure_ascii=False).encode("utf-8"))
        got = read_receipt_file_surface("bm-a", root)
        check("S21 BOM receipt is readable (utf-8-sig)",
              got["ok"] and got["record"].get("rc") == 0
              and got["record"].get("_ts_source") == "record-ts", got)

        # S22 有回执但无 ts 字段（卡面/对齐类件）→ 落盘 mtime 兜底并标明来源
        os.remove(os.path.join(rcpt, "BOM-JOB-bm-a.json"))
        with open(os.path.join(rcpt, "NO-TS-bm-a.json"), "w", encoding="utf-8") as f:
            json.dump({"card": "POOL-0020", "node": "bm-a"}, f)
        got2 = read_receipt_file_surface("bm-a", root)
        check("S22 ts-less receipt falls back to mtime (evidence kept, source labelled)",
              got2["ok"] and got2["record"].get("_ts_source") == "file-mtime"
              and got2["record"].get("ts"), got2)

    print("selftest executor-signals verdict: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


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
    ap.add_argument("sub", choices=["run", "signals", "selftest"])
    ap.add_argument("--node", default="bm-a")
    ap.add_argument("--nodes", default=None,
                    help="signals 子命令批量节点，逗号分隔（如 bm-a,bm-b,bm-c）")
    ap.add_argument("--root", default=None)
    ap.add_argument("--max-age-min", type=float, default=DEFAULT_MAX_AGE_MIN)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()
    if a.sub == "selftest":
        rc1 = selftest()
        rc2 = selftest_signals()
        return 0 if (rc1 == 0 and rc2 == 0) else 1

    nodes = ([n.strip() for n in a.nodes.split(",") if n.strip()]
             if a.nodes else [a.node])

    def _dump(path, payload):
        path = path if os.path.isabs(path) else os.path.join(REPO, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print("json -> %s" % path)

    if a.sub == "signals":
        out = []
        worst = None
        for node in nodes:
            res = evaluate_signals(node, root=a.root, max_age_min=a.max_age_min)
            out.append(res)
            print(signals_one_line(res))
            for name in SIGNAL_ORDER:
                r = res["signals"][name]
                if r["state"] != "FRESH":
                    why = (r["absent"] or r["error"]
                           or ("age=%.1fm" % r["age_min"] if r["age_min"] is not None
                               else "-"))
                    print("  %-7s %-10s %s" % (name, r["state"], why))
            # 批量输出时取最差：GREEN 只在全部 GREEN 时出现
            if worst is None or out[-1]["ok_count"] < worst["ok_count"]:
                worst = res
        if a.json_out:
            _dump(a.json_out, out[0] if len(out) == 1 else
                  {"check": "executor-signals-batch", "nodes": nodes, "results": out,
                   "all_green": all(r["verdict"] == "SIGNALS-GREEN" for r in out)})
        # exit 语义：GREEN=0 / INCOMPLETE=2 / STALE=3 / DEAD=4 / UNKNOWN=5
        return {"SIGNALS-GREEN": 0, "SIGNALS-INCOMPLETE": 2, "SIGNALS-STALE": 3,
                "SIGNALS-DEAD": 4, "SIGNALS-UNKNOWN": 5}[worst["verdict"]]

    res = evaluate(a.node, root=a.root, max_age_min=a.max_age_min)
    print(one_line(res))
    if a.json_out:
        _dump(a.json_out, res)
    # exit 语义：ALIVE=0 / STALE=2 / DEAD=3 / UNKNOWN=4 / NO-EVIDENCE=5
    return {"ALIVE": 0, "STALE": 2, "DEAD": 3, "UNKNOWN": 4,
            "NO-EVIDENCE": 5}[res["verdict"]]


if __name__ == "__main__":
    sys.exit(main())