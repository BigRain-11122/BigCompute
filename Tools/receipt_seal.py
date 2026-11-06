#!/usr/bin/env python
"""回执产物指纹封存面（tech T192 规格面·2026-11-07）。

读侧闸（cpu_effective_hours 的 sha_mismatch / sha_coverage）只有在回执声明了产物
指纹时才生效，而现役回执 declared=0——闸一直在等一个永远不会出现的字段。
写侧属 @HQ / lane agent 域，本司禁跨仓写，因此本件只做三件事：

  1. audit  把「本机能算到的产物指纹」离线算出来，出四态读数与覆盖率；
  2. write  把可封存的行落成本司封存册（state/receipt-seals-<day>.json），
            供写侧 owner 采纳为回执的 sha256 字段——不写对方仓、不改对方文件；
  3. seal   单件封存（人工点名某条回执）。

四态判据（缺一不得混计）：
  SEALABLE        产物在本机且可读 → 给出 sha256:size
  ABSENT          回执声明了产物、本机确实没有（指纹问题之外的另一件事）
  REMOTE          他机回执 → 本机结构上算不出，禁臆造
  NO-DELIVERABLE  回执不含产物字段（lane 回执），本不给指纹义务

用法：
  python Tools/receipt_seal.py audit [--days N] [--write] [--json-out PATH]
  python Tools/receipt_seal.py seal --receipt NAME [--out PATH]
  python Tools/receipt_seal.py selftest
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cpu_effective_hours as ceh  # noqa: E402  解析/取指/他机判据单源复用

STATES = ("SEALABLE", "ABSENT", "REMOTE", "NO-DELIVERABLE")
STATE_DIR = os.path.abspath(os.path.join(HERE, "..", "state"))


def _out(text):
    sys.stdout.write(text + "\n")


def default_out(day=None):
    day = day or datetime.now().strftime("%Y%m%d")
    return os.path.join(STATE_DIR, "receipt-seals-%s.json" % day)


def classify(rec, root, host):
    """单条回执 → 封存四态。任何异常都归 ABSENT，不把「读不到」当「已封存」。"""
    row = {
        "receipt": rec.get("receipt") or "",
        "id": rec.get("id") or "",
        "node": rec.get("node") or "",
        "deliverable": rec.get("deliverable") or "",
        "ts": rec["ts"].isoformat(sep=" ") if rec.get("ts") else "",
        "state": "",
        "sha": "",
        "size": None,
        "declared": rec.get("declared_sha") or "",
        "compare": "UNDECLARED",
    }
    if not row["deliverable"]:
        row["state"] = "NO-DELIVERABLE"
        return row
    if ceh.is_foreign(rec, host):
        row["state"] = "REMOTE"
        return row
    path = ceh.local_path(root, row["deliverable"])
    if not path or not os.path.isfile(path):
        row["state"] = "ABSENT"
        return row
    try:
        row["sha"] = ceh._sha(path)
        row["size"] = os.path.getsize(path)
    except OSError:
        row["state"] = "ABSENT"
        return row
    row["state"] = "SEALABLE"
    if row["declared"]:
        actual = row["sha"].split(":")[0]
        # 与读侧闸同一比较规则：允许截断前缀互认，禁凭长度宽容成「都算过」
        same = (actual.startswith(row["declared"][:len(actual)])
                or row["declared"].startswith(actual))
        row["compare"] = "MATCH" if same else "MISMATCH"
    return row


def audit(root, host, days=None, now=None):
    now = now or datetime.now()
    rdir = os.path.join(root, ceh.RECEIPT_REL)
    recs, bad, other = ceh.load_receipts(rdir)
    cutoff = now - timedelta(days=days) if days else None
    rows = []
    for rec in recs:
        if cutoff and rec.get("ts") and rec["ts"] < cutoff:
            continue
        rows.append(classify(rec, root, host))

    counts = {k: 0 for k in STATES}
    for row in rows:
        counts[row["state"]] += 1
    sealable = [r for r in rows if r["state"] == "SEALABLE"]
    payload = {
        "verdict": "",
        "generated_at": now.isoformat(timespec="seconds"),
        "root": os.path.abspath(root),
        "host": host,
        "window_days": days,
        "counts": counts,
        "scanned": {"receipts": len(recs), "bad_json": len(bad), "other": len(other)},
        "coverage": {
            "sealable": len(sealable),
            "declared": sum(1 for r in sealable if r["declared"]),
            "matched": sum(1 for r in sealable if r["compare"] == "MATCH"),
            "mismatched": sum(1 for r in sealable if r["compare"] == "MISMATCH"),
            "undeclared": sum(1 for r in sealable if r["compare"] == "UNDECLARED"),
        },
        "rows": rows,
    }
    cov = payload["coverage"]
    if not recs:
        payload["verdict"] = "SEAL-AUDIT-NO-DATA"
    elif cov["mismatched"]:
        payload["verdict"] = "SEAL-AUDIT-MISMATCH"
    elif cov["declared"] == 0:
        payload["verdict"] = "SEAL-AUDIT-SHA-COVERAGE-NONE"
    else:
        payload["verdict"] = "SEAL-AUDIT-OK"
    return payload


def merge_seals(rows, out_path, keep_all=False):
    """只收 SEALABLE 行；(receipt, sha) 去重，幂等。返回 (新增数, 总行数)。"""
    want = [r for r in rows if r["state"] == "SEALABLE" or (keep_all and r["state"])]
    existing = []
    if os.path.isfile(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            existing = doc.get("seals") or []
        except (OSError, ValueError):
            existing = []
    index = {(r.get("receipt"), r.get("sha")): r for r in existing}
    added = 0
    for row in want:
        key = (row["receipt"], row["sha"])
        if key in index:
            continue
        index[key] = {
            "receipt": row["receipt"],
            "id": row["id"],
            "node": row["node"],
            "deliverable": row["deliverable"],
            "sha": row["sha"],
            "size": row["size"],
            "declared": row["declared"],
            "compare": row["compare"],
        }
        added += 1
    seals = [index[k] for k in sorted(index)]
    doc = {
        "schema": "receipt-seals/v1",
        "note": "本司离线封存册：仅供写侧 owner 采纳为回执 sha256，不改对方仓",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "seals": seals,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return added, len(seals)


def render(payload):
    c = payload["counts"]
    cov = payload["coverage"]
    _out("VERDICT=%s host=%s window=%s"
         % (payload["verdict"], payload["host"],
            payload["window_days"] if payload["window_days"] else "all"))
    _out("receipts scanned=%d bad_json=%d other=%d"
         % (payload["scanned"]["receipts"], payload["scanned"]["bad_json"],
            payload["scanned"]["other"]))
    _out("states %s" % " ".join("%s=%d" % (k, c[k]) for k in STATES))
    _out("coverage sealable=%d declared=%d matched=%d mismatched=%d undeclared=%d"
         % (cov["sealable"], cov["declared"], cov["matched"],
            cov["mismatched"], cov["undeclared"]))
    for row in payload["rows"]:
        if row["state"] == "SEALABLE" and row["compare"] == "MISMATCH":
            _out("  MISMATCH %s id=%s declared=%s actual=%s"
                 % (row["receipt"], row["id"], row["declared"][:16],
                    row["sha"].split(":")[0]))


def selftest():
    checks = []
    failed = set()

    def want(name, ok):
        checks.append(name)
        if not ok:
            failed.add(name)

    tmp = tempfile.mkdtemp(prefix="rcpt-seal-")
    try:
        root = os.path.join(tmp, "root")
        rdir = os.path.join(root, "cph4", "fleet", "queue-receipts")
        os.makedirs(rdir)
        outdir = os.path.join(root, "out")
        os.makedirs(outdir)
        good = os.path.join(outdir, "a.txt")
        with open(good, "w", encoding="utf-8") as fh:
            fh.write("deliverable payload\n")
        actual = ceh._sha(good)
        actual_hex = actual.split(":")[0]

        def put(name, doc):
            with open(os.path.join(rdir, name), "w", encoding="utf-8") as fh:
                json.dump(doc, fh)

        put("R1.json", {"id": "J-1", "node": "bm-a", "ts": "2026-11-07T00:00:00",
                        "seconds": 60, "rc": 0, "deliverable": "out/a.txt"})
        put("R2.json", {"id": "J-2", "node": "bm-a", "ts": "2026-11-07T00:01:00",
                        "seconds": 60, "rc": 0, "deliverable": "out/a.txt",
                        "sha256": actual_hex})
        put("R3.json", {"id": "J-3", "node": "bm-a", "ts": "2026-11-07T00:02:00",
                        "seconds": 60, "rc": 0, "deliverable": "out/a.txt",
                        "sha256": "deadbeefdeadbeef"})
        put("R4.json", {"id": "J-4", "node": "bm-a", "ts": "2026-11-07T00:03:00",
                        "seconds": 60, "rc": 0, "deliverable": "out/none.txt"})
        put("R5.json", {"id": "J-5", "node": "bm-b", "ts": "2026-11-07T00:04:00",
                        "seconds": 60, "rc": 0, "deliverable": "out/a.txt"})
        put("R6.json", {"id": "J-6", "node": "bm-a", "seconds": 5})
        put("R7.json", {"hello": "world"})

        payload = audit(root, "bm-a")
        by_id = {r["id"]: r for r in payload["rows"]}

        want("S1 本机产物在盘 → SEALABLE 且给出 sha:size",
             by_id["J-1"]["state"] == "SEALABLE"
             and by_id["J-1"]["sha"].startswith(actual_hex)
             and by_id["J-1"]["size"] == os.path.getsize(good))
        want("S2 声明的指纹三态互斥（未声明/一致/不一致）",
             by_id["J-1"]["compare"] == "UNDECLARED"
             and by_id["J-2"]["compare"] == "MATCH"
             and by_id["J-3"]["compare"] == "MISMATCH")
        want("S3 本机缺件 → ABSENT（不许当成已封存）",
             by_id["J-4"]["state"] == "ABSENT" and by_id["J-4"]["sha"] == "")
        want("S4 他机回执 → REMOTE（本机结构上算不出）",
             by_id["J-5"]["state"] == "REMOTE")
        want("S5 无产物字段 → NO-DELIVERABLE（不给指纹义务）",
             by_id["J-6"]["state"] == "NO-DELIVERABLE")
        want("S6 非回执件不进分母、读数分列",
             payload["scanned"]["receipts"] == 6
             and payload["scanned"]["other"] == 1
             and len(payload["rows"]) == 6)
        want("S7 覆盖率显式出数（sealable=3 declared=2 matched=1 mismatched=1）",
             payload["coverage"]["sealable"] == 3
             and payload["coverage"]["declared"] == 2
             and payload["coverage"]["matched"] == 1
             and payload["coverage"]["mismatched"] == 1
             and payload["coverage"]["undeclared"] == 1)
        want("S8 有不一致即判 MISMATCH（不许读成 OK）",
             payload["verdict"] == "SEAL-AUDIT-MISMATCH")

        out_path = os.path.join(tmp, "seals", "receipt-seals.json")
        added1, total1 = merge_seals(payload["rows"], out_path)
        added2, total2 = merge_seals(payload["rows"], out_path)
        want("S9 落册只收 SEALABLE 且幂等（二跑新增 0）",
             added1 == 3 and total1 == 3 and added2 == 0 and total2 == 3)
        with open(out_path, "r", encoding="utf-8") as fh:
            seals = json.load(fh)["seals"]
        want("S10 封存册不含他机/缺件行",
             all(s["id"] in ("J-1", "J-2", "J-3") for s in seals))

        before = ceh._sha(os.path.join(rdir, "R1.json"))
        merge_seals(payload["rows"], out_path)
        after = ceh._sha(os.path.join(rdir, "R1.json"))
        want("S11 写册不触碰对方回执文件（字节指纹前后一致）", before == after)

        payload2 = audit(root, "bm-a")
        strip = lambda p: {k: v for k, v in p.items() if k != "generated_at"}
        want("S12 双跑确定性（除生成时刻外逐字节一致）",
             json.dumps(strip(payload), sort_keys=True)
             == json.dumps(strip(payload2), sort_keys=True))

        clean = os.path.join(tmp, "clean")
        os.makedirs(os.path.join(clean, "cph4", "fleet", "queue-receipts"))
        want("S13 零回执 → NO-DATA（不假绿）",
             audit(clean, "bm-a")["verdict"] == "SEAL-AUDIT-NO-DATA")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for name in checks:
        _out("  %s %s" % ("PASS" if name not in failed else "FAIL", name))
    _out("selftest %d/%d PASS" % (len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="回执产物指纹封存面（本司只读+本仓落册）")
    ap.add_argument("cmd", choices=["audit", "seal", "selftest"])
    ap.add_argument("--root", default=ceh.DEFAULT_ROOT)
    ap.add_argument("--host", default=ceh.HOST_NODE)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--receipt", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()

    payload = audit(args.root, args.host, days=args.days)
    if args.cmd == "seal":
        if not args.receipt:
            _out("seal 需要 --receipt <文件名>")
            return 4
        rows = [r for r in payload["rows"] if r["receipt"] == args.receipt]
        if not rows:
            _out("seal: 回执未在册 name=%s" % args.receipt)
            return 4
        payload["rows"] = rows
        payload["counts"] = {k: 0 for k in STATES}
        for row in rows:
            payload["counts"][row["state"]] += 1
        payload["coverage"] = {
            "sealable": sum(1 for r in rows if r["state"] == "SEALABLE"),
            "declared": sum(1 for r in rows if r["declared"]),
            "matched": sum(1 for r in rows if r["compare"] == "MATCH"),
            "mismatched": sum(1 for r in rows if r["compare"] == "MISMATCH"),
            "undeclared": sum(1 for r in rows if r["compare"] == "UNDECLARED"),
        }

    render(payload)
    out_path = args.out or default_out()
    if args.write or args.cmd == "seal":
        added, total = merge_seals(payload["rows"], out_path)
        _out("  seals added=%d total=%d out=%s" % (added, total, out_path))
    if args.json_out:
        out = os.path.abspath(args.json_out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        _out("json_out=%s" % out)

    if payload["verdict"] == "SEAL-AUDIT-NO-DATA":
        return 3
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())