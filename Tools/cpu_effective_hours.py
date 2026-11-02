#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cpu_effective_hours.py — CPU 有效工时核算器（BM, 2026-11-03）

承接：OB-20261102-2015-CPUFULL（CEO「调动起 CPU 去干该干的事」令）中的验收面——
「验收只看 queue-receipts 里的产物回执 + 日报『CPU 有效工时』可逐条追到文件」。

本器只做三件事，不做派工：
  1) 把 cph4/fleet/queue-receipts/*.json 读成统一记录（兼容两种回执 schema）；
  2) 逐条把 deliverable 落回磁盘复核（存在性 + 内容 sha256），给出可追到文件的清单；
  3) 剔除四类"不算有效工时"的动静：rc 失败、产物缺失、重复重放、复制当产物，
     剩下的 rc=0 且产物真实在盘的 seconds 才计入「有效工时」。

为什么必须剔除而不是照单累加：令里同时写了「禁造假活（不许空转刷占用、不许重跑已 DONE 卡、
不许把原始拷贝当产物）」。只累加 seconds 会把这三类直接算成产能，等于把闸做成橡皮章。

三态（既不过度乐观也不误判他机）：
  VERIFIED        产物在本机磁盘上 → 计入
  REMOTE-UNVERIF  他机产物且本机无副本 → 不计入、不判缺陷（本机看不到≠没产出）
  MISSING-LOCAL   本机产出却找不到文件 → 不计入 + 判缺陷（exit 2）

用法：
  python Tools/cpu_effective_hours.py                # 全窗口 + 汇总
  python Tools/cpu_effective_hours.py --list         # 附逐条明细（可逐条追到文件）
  python Tools/cpu_effective_hours.py --days 1 --json-out state/cpu-eff.json
  python Tools/cpu_effective_hours.py --selftest
退出码：0 干净 / 2 有待查项 / 3 无可用回执数据
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RECEIPT_REL = os.path.join("cph4", "fleet", "queue-receipts")
HOST_NODE = os.environ.get("FLUXGROUP_NODE", "bm-a")
HASH_CAP = 16 * 1024 * 1024  # 超过 16MB 只取前 1MB+size 做指纹，避免大件拖慢

BUCKETS = ("verified", "remote_unverif", "missing_local", "rc_fail",
           "replay", "copy", "zero_seconds", "no_deliverable",
           "bad_json", "not_work_receipt", "unparsed")


def _out(text):
    sys.stdout.write(text + "\n")


def _sha(path):
    h = hashlib.sha256()
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        left = size if size <= HASH_CAP else 1024 * 1024
        while left > 0:
            chunk = fh.read(min(65536, left))
            if not chunk:
                break
            h.update(chunk)
            left -= len(chunk)
    return "%s:%d" % (h.hexdigest()[:16], size)


def parse_ts(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def normalize(doc):
    """两种 schema → 统一记录。返回 None 表示不是可核算回执。"""
    if not isinstance(doc, dict):
        return None
    if "deliverable" in doc and ("seconds" in doc or "rc" in doc):
        has_deliverable = True
    elif "id" in doc and "seconds" in doc and not ("job" in doc and "state" in doc):
        # lane 回执无产物字段：合法回执，但追不到文件（单独归类，不混成坏 JSON）
        has_deliverable = False
    elif "job" in doc and ("sec" in doc or "state" in doc):
        has_deliverable = None
    else:
        return None

    if has_deliverable is None:
        state = str(doc.get("state") or "").upper()
        return {
            "schema": "llm",
            "id": str(doc.get("job") or ""),
            "node": str(doc.get("line") or "llm"),
            "ts": parse_ts(doc.get("ts")),
            "seconds": _num(doc.get("sec")),
            "deliverable": str(doc.get("path") or ""),
            "rc_ok": state in ("DONE", "OK", "PASS"),
            "rc": state,
        }

    rc = doc.get("rc")
    ok_rc = doc.get("ok_rc")
    if isinstance(ok_rc, list) and ok_rc:
        rc_ok = rc in ok_rc
    else:
        rc_ok = (rc == 0)
    return {
        "schema": "lane",
        "id": str(doc.get("id") or ""),
        "node": str(doc.get("node") or ""),
        "ts": parse_ts(doc.get("ts")),
        "seconds": _num(doc.get("seconds")),
        "deliverable": str(doc.get("deliverable") or ""),
        "rc_ok": rc_ok,
        "rc": rc,
    }


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_receipts(receipt_dir):
    """返回 (records, bad_json, other)。

    bad_json      = 读不动/不是 JSON 的文件（真损坏，要人看）
    other         = 合法 JSON 但不是可核算的执行回执（QC 结果件/ack/claim 状态）
    """
    records, bad_json, other = [], [], []
    if not os.path.isdir(receipt_dir):
        return records, bad_json, other
    for path in sorted(glob.glob(os.path.join(receipt_dir, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            bad_json.append(path)
            continue
        if isinstance(doc, list):
            doc = doc[0] if doc and isinstance(doc[0], dict) else None
        rec = normalize(doc)
        if rec is None:
            other.append(path)
            continue
        rec["receipt"] = os.path.basename(path)
        records.append(rec)
    return records, bad_json, other


def local_path(root, rel):
    if not rel:
        return None
    cleaned = rel.replace("\\", "/")
    if len(cleaned) > 1 and cleaned[1] == ":":
        return cleaned  # 绝对 Windows 路径
    if cleaned.startswith("/"):
        return None      # 他机 posix 绝对路径：本机不可能有
    return os.path.normpath(os.path.join(root, cleaned))


def evaluate(records, root, host, days=None, node=None, now=None):
    now = now or datetime.now()
    cutoff = None
    if days:
        cutoff = now - timedelta(days=days)

    buckets = {k: 0 for k in BUCKETS}
    rows, skipped = [], []
    seen_keys = {}
    by_deliverable = {}

    for rec in records:
        if node and rec["node"] != node:
            continue
        if cutoff and rec["ts"] and rec["ts"] < cutoff:
            continue
        if not rec["rc_ok"]:
            buckets["rc_fail"] += 1
            skipped.append((rec, "rc-fail rc=%s" % (rec["rc"],)))
            continue

        if not rec["deliverable"]:
            buckets["no_deliverable"] += 1
            skipped.append((rec, "no-deliverable"))
            continue

        path = local_path(root, rec["deliverable"])
        exists = bool(path) and os.path.isfile(path)
        digest = _sha(path) if exists else ""

        if not exists:
            if rec["node"] and rec["node"] != host and not rec["deliverable"].startswith("/"):
                buckets["remote_unverif"] += 1
                state = "REMOTE-UNVERIF"
            elif rec["node"] and rec["node"] != host:
                buckets["remote_unverif"] += 1
                state = "REMOTE-UNVERIF"
            else:
                buckets["missing_local"] += 1
                state = "MISSING-LOCAL"
            skipped.append((rec, state))
            continue

        if rec["seconds"] <= 0:
            buckets["zero_seconds"] += 1
            skipped.append((rec, "zero-seconds"))
            continue

        key = (rec["id"], rec["node"], digest)
        if key in seen_keys:
            buckets["replay"] += 1
            skipped.append((rec, "replay-of %s" % seen_keys[key]))
            continue
        seen_keys[key] = rec["receipt"]

        row = {
            "id": rec["id"],
            "node": rec["node"],
            "ts": rec["ts"].isoformat(sep=" ") if rec["ts"] else "",
            "seconds": rec["seconds"],
            "hours": round(rec["seconds"] / 3600.0, 4),
            "deliverable": rec["deliverable"],
            "sha": digest,
            "receipt": rec["receipt"],
            "schema": rec["schema"],
            "state": "VERIFIED",
        }
        rows.append(row)
        by_deliverable.setdefault(digest, []).append(row)

    # 复制当产物：同一内容被不同 id 当成各自产物 → 后者不计净工时
    copy_rows = []
    for digest, group in by_deliverable.items():
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda r: (r["ts"], r["id"]))
        ids = {r["id"] for r in group}
        if len(ids) < 2:
            continue
        for dup in group[1:]:
            dup["state"] = "COPY-AS-PRODUCT"
            dup["copy_of"] = group[0]["id"]
            copy_rows.append(dup)
    if copy_rows:
        copy_set = {id(r) for r in copy_rows}
        rows = [r for r in rows if id(r) not in copy_set]
        buckets["copy"] += len(copy_rows)

    buckets["verified"] = len(rows)
    net_seconds = sum(r["seconds"] for r in rows)
    per_node = {}
    for row in rows:
        slot = per_node.setdefault(row["node"] or "unknown", {"count": 0, "seconds": 0.0})
        slot["count"] += 1
        slot["seconds"] = round(slot["seconds"] + row["seconds"], 1)

    verdict = "ATTENTION" if (buckets["missing_local"] or buckets["copy"]
                              or buckets["replay"] or buckets["zero_seconds"]) else "CLEAN"
    payload = {
        "tool": "cpu_effective_hours",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": root,
        "host": host,
        "generated_ts_utc_offset": None,
        "window_days": days,
        "node_filter": node,
        "verdict": verdict,
        "totals": {
            "receipts_scanned": len(rows) + len(skipped) + buckets["unparsed"],
            "counted": len(rows),
            "net_seconds": round(net_seconds, 1),
            "net_hours": round(net_seconds / 3600.0, 3),
        },
        "buckets": buckets,
        "per_node": per_node,
        "rows": rows,
        "copies": [{"id": r["id"], "node": r["node"], "copy_of": r["copy_of"],
                    "deliverable": r["deliverable"]} for r in copy_rows],
        "skipped": [{"id": r["id"], "node": r["node"], "receipt": r.get("receipt", ""),
                     "reason": why, "deliverable": r["deliverable"]} for r, why in skipped],
    }
    return payload


def report(payload, show_list=False):
    t = payload["totals"]
    b = payload["buckets"]
    _out("CPU 有效工时核算（root=%s host=%s window=%s）"
         % (payload["root"], payload["host"],
            ("%sd" % payload["window_days"]) if payload["window_days"] else "all"))
    _out("verdict=%s  counted=%d  net=%sh  (%.0fs)"
         % (payload["verdict"], t["counted"], t["net_hours"], t["net_seconds"]))
    _out("receipts_scanned=%d  verified=%d  remote_unverif=%d  missing_local=%d  "
         "rc_fail=%d  replay=%d  copy=%d  zero_seconds=%d  no_deliverable=%d  "
         "bad_json=%d  not_work_receipt=%d"
         % (t["receipts_scanned"], b["verified"], b["remote_unverif"],
            b["missing_local"], b["rc_fail"], b["replay"], b["copy"],
            b["zero_seconds"], b["no_deliverable"], b["bad_json"],
            b["not_work_receipt"]))
    for node in sorted(payload["per_node"]):
        slot = payload["per_node"][node]
        _out("  node=%-6s counted=%-3d effective_h=%.3f"
             % (node, slot["count"], slot["seconds"] / 3600.0))
    if show_list:
        _out("-- 逐条（可追到文件）--")
        for row in payload["rows"]:
            _out("  %s %-6s %-28s %7.1fs %s sha=%s"
                 % (row["ts"] or "?", row["node"] or "?", row["id"][:28],
                    row["seconds"], row["deliverable"], row["sha"]))
    for item in payload["copies"]:
        _out("  COPY  %s/%s ← %s" % (item["node"], item["id"], item["copy_of"]))
    for item in payload["skipped"]:
        if item["reason"].startswith("rc-fail"):
            continue
        _out("  SKIP  %s/%s %s (%s)" % (item["node"], item["id"], item["reason"],
                                        item["deliverable"] or "-"))


# --------------------------------------------------------------------------- selftest
def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)


def _lane(rid, node, seconds, deliverable, ts, rc=0):
    return {"id": rid, "node": node, "rc": rc, "seconds": seconds,
            "cmd": "python x.py", "deliverable": deliverable,
            "deliverable_exists": True, "ts": ts}


def selftest():
    checks, failed = [], []

    def want(name, cond):
        checks.append(name)
        if not cond:
            failed.append(name)

    tmp = tempfile.mkdtemp(prefix="cpueff-")
    try:
        root = os.path.join(tmp, "root")
        rdir = os.path.join(root, RECEIPT_REL)
        os.makedirs(rdir, exist_ok=True)
        now = datetime(2026, 11, 3, 12, 0, 0)
        fresh = "2026-11-03T11:00:00"
        old = "2026-10-20T11:00:00"

        # 真实产物
        for name, body in (("a.json", {"v": 1}), ("b.json", {"v": 2}),
                           ("same1.json", {"v": 9}), ("same2.json", {"v": 9}),
                           ("c.json", {"v": 3}), ("old.json", {"v": 7})):
            _write(os.path.join(root, "out", name), body)

        _write(os.path.join(rdir, "r1.json"),
               _lane("J-OK", "bm-a", 3600, "out/a.json", fresh))
        _write(os.path.join(rdir, "r2.json"),
               _lane("J-FAIL", "bm-a", 1200, "out/a.json", fresh, rc=7))
        _write(os.path.join(rdir, "r3.json"),
               _lane("J-MISS", "bm-a", 600, "out/nope.json", fresh))
        _write(os.path.join(rdir, "r4.json"),
               _lane("J-REMOTE", "bm-c", 900, "out/not-here.json", fresh))
        _write(os.path.join(rdir, "r5.json"),
               _lane("J-OK", "bm-a", 3600, "out/a.json", fresh))  # 重放
        _write(os.path.join(rdir, "r6.json"),
               _lane("J-COPY1", "bm-a", 100, "out/same1.json", fresh))
        _write(os.path.join(rdir, "r7.json"),
               _lane("J-COPY2", "bm-a", 100, "out/same2.json", fresh))
        _write(os.path.join(rdir, "r8.json"),
               {"job": "llm-ok", "line": "llm", "state": "DONE", "sec": 60,
                "path": "out/b.json", "ts": fresh, "chars": 10, "model": "m"})
        _write(os.path.join(rdir, "r9.json"),
               {"job": "llm-empty", "line": "llm", "state": "QUEUE-EMPTY",
                "sec": 0, "path": "", "ts": fresh})
        _write(os.path.join(rdir, "r10.json"),
               _lane("J-OLD", "bm-a", 7200, "out/old.json", old))
        _write(os.path.join(rdir, "r11.json"),
               _lane("J-ZERO", "bm-a", 0, "out/b.json", fresh))
        with open(os.path.join(rdir, "r12.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        _write(os.path.join(rdir, "r13.json"),
               [_lane("J-LIST", "bm-a", 30, "out/c.json", fresh)])
        _write(os.path.join(rdir, "r14.json"),
               {"id": "J-NODELIV", "node": "bm-a", "rc": 0, "seconds": 45,
                "cmd": "python scan.py", "ts": fresh})
        _write(os.path.join(rdir, "r15.json"),
               {"verdict": "PASS", "fps": 24, "frames": 100, "seams": []})

        recs, bad, other = load_receipts(rdir)
        want("S1 坏 JSON 进 bad_json", len(bad) == 1)
        want("S2 list 形态被展开为一条", any(r["id"] == "J-LIST" for r in recs))

        p = evaluate(recs, root, "bm-a", days=1, now=now)
        want("S3 有效行计入 (J-OK/J-COPY1/llm-ok/J-LIST)",
             p["totals"]["counted"] == 4)
        if os.environ.get("CPUEFF_DEBUG"):
            _out("DEBUG totals=%s rows=%s" % (p["totals"],
                  [(r["id"], r["seconds"]) for r in p["rows"]]))
        want("S4 纯有效工时=3600+100+60+30 秒",
             abs(p["totals"]["net_seconds"] - 3790.0) < 0.01)
        want("S5 rc 失败不入账", p["buckets"]["rc_fail"] >= 1)
        want("S6 本机缺产物=missing_local 且 verdict ATTENTION",
             p["buckets"]["missing_local"] == 1 and p["verdict"] == "ATTENTION")
        want("S7 他机缺产物=remote_unverif 不判缺陷", p["buckets"]["remote_unverif"] == 1)
        want("S8 同 id 同产物重放只计一次", p["buckets"]["replay"] == 1)
        want("S9 复制当产物被抓(J-COPY2)",
             any(c["id"] == "J-COPY2" for c in p["copies"]))
        want("S10 零秒被剔除", p["buckets"]["zero_seconds"] == 1)

        p1 = evaluate(recs, root, "bm-a", days=1, now=now)
        want("S11 窗口过滤掉 10-20 旧回执（J-OLD 及其重放不计）",
             all(r["id"] != "J-OLD" for r in p1["rows"]))

        pnode = evaluate(recs, root, "bm-a", node="bm-c", now=now)
        want("S12 --node 过滤生效", pnode["totals"]["counted"] == 0)

        a = evaluate(recs, root, "bm-a", now=now)
        b = evaluate(recs, root, "bm-a", now=now)
        a.pop("generated_at", None)
        b.pop("generated_at", None)
        want("S13 确定性（两次结果一致）", json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))

        empty = os.path.join(tmp, "empty")
        os.makedirs(empty, exist_ok=True)
        pe = evaluate([], empty, "bm-a", now=now)
        want("S14 空目录 → counted=0 且 verdict CLEAN",
             pe["totals"]["counted"] == 0 and pe["verdict"] == "CLEAN")

        # 绝对 posix 路径不当作本机缺失
        recs2 = [{"schema": "llm", "id": "X", "node": "bm-m", "ts": now,
                  "seconds": 5, "deliverable": "/Users/other/x.json", "rc_ok": True, "rc": "DONE"}]
        p2 = evaluate(recs2, root, "bm-a", now=now)
        want("S15 posix 绝对路径=remote 不判缺陷", p2["buckets"]["remote_unverif"] == 1)
        want("S16 无产物字段回执单列 no_deliverable（不计工时、不判缺陷）",
             p["buckets"]["no_deliverable"] == 1 and p["totals"]["counted"] == 4)
        want("S17 QC 结果件（非执行回执）单列 not_work_receipt",
             len(other) == 1 and p["buckets"]["not_work_receipt"] == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for name in checks:
        _out("  %s %s" % ("PASS" if name not in failed else "FAIL", name))
    _out("selftest %d/%d PASS" % (len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="CPU 有效工时核算器")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--host", default=HOST_NODE)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--node", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    rdir = os.path.join(args.root, RECEIPT_REL)
    recs, bad, other = load_receipts(rdir)
    if not recs:
        _out("CPU-EFF-HOURS verdict=NO-DATA receipts_dir=%s bad_json=%d other=%d"
             % (rdir, len(bad), len(other)))
        return 3

    payload = evaluate(recs, args.root, args.host, days=args.days, node=args.node)
    payload["buckets"]["bad_json"] = len(bad)
    payload["buckets"]["not_work_receipt"] = len(other)
    payload["totals"]["receipts_scanned"] = len(recs) + len(bad) + len(other)
    payload["other_receipts"] = [os.path.basename(p) for p in other]
    report(payload, show_list=args.list)

    if args.json_out:
        out = os.path.abspath(args.json_out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        _out("json_out=%s" % out)

    return 0 if payload["verdict"] == "CLEAN" else 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())