#!/usr/bin/env python
"""回执签名普查与派生面（tech T218·2026-11-07）。

T217 复盘实证：`game-packs-*.json` 无 node/id，`model-radar-*.json` 有 node 缺 id。
回执缺签名时，消费侧（cpu_effective_hours triage）只能靠文件内容比对，永远拿不到
CONFIRMED，只能停在 PARTIAL。写侧属 @HQ / lane agent 域（本司禁跨仓写），因此本件：

  1. census  扫回执目录，逐件判签名完整度，并把「可从文件名/内容/节点词表派生」的
             字段连同来源与置信度一起列出——不臆造，派生不出就明说 UNDETERMINED；
  2. overlay 把可派生签名落成本司 overlay 册（state/receipt-sig-overlay-<day>.json），
             供消费侧读取；不改对方回执文件一个字节；
  3. resolve 单件查签名（含派生链），供人工点名核对。

判据（缺一不得混计）：
  SIGNED           node 与 id 都在（内容自带）
  ID-MISSING       有 node，无 id
  NODE-MISSING     有 id，无 node
  BOTH-MISSING     两者皆无
派生置信度：
  STRONG   内容字段 / 文件名节点 token / 全目录唯一文件名主干
  WEAK     节点词表命中（可能是别的节点名片段）
  NONE     UNDETERMINED

用法：
  python Tools/receipt_signer.py census [--days N] [--write] [--json-out PATH]
  python Tools/receipt_signer.py resolve --receipt NAME [--json]
  python Tools/receipt_signer.py selftest
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cpu_effective_hours as ceh  # noqa: E402  根/回执目录/时间解析单源复用

STATE_DIR = os.path.abspath(os.path.join(HERE, "..", "state"))
SIG_STATES = ("SIGNED", "ID-MISSING", "NODE-MISSING", "BOTH-MISSING")
# 文件名里的节点 token：-bm-a / -bm-c / bm-a- 两种位置都认
NODE_TOKEN_RE = re.compile(r"(?<![a-z0-9])(bm-[a-z]{1,2})(?![a-z0-9])", re.I)


def _out(text):
    sys.stdout.write(text + "\n")


def default_out(day=None):
    day = day or datetime.now().strftime("%Y%m%d")
    return os.path.join(STATE_DIR, "receipt-sig-overlay-%s.json" % day)


def node_vocabulary(docs):
    """节点词表只从「文件自己声明的 node」里长出来，不从外部猜。"""
    vocab = set()
    for _, doc in docs:
        if isinstance(doc, dict):
            node = str(doc.get("node") or "").strip()
            if node:
                vocab.add(node)
    return sorted(vocab)


def derive_node(doc, stem, vocab):
    """返回 (node, src, confidence)。优先级=内容 > 文件名 token > 词表命中。"""
    if isinstance(doc, dict):
        node = str(doc.get("node") or "").strip()
        if node:
            return node, "CONTENT", "STRONG"
    m = NODE_TOKEN_RE.search(stem)
    if m:
        return m.group(1).lower(), "FILENAME", "STRONG"
    hits = [v for v in vocab if v and v.lower() in stem.lower()]
    if len(hits) == 1:
        return hits[0], "VOCAB", "WEAK"
    return "", "UNDETERMINED", "NONE"


def derive_id(doc, stem, stem_counts):
    if isinstance(doc, dict):
        rid = str(doc.get("id") or doc.get("job") or "").strip()
        if rid:
            return rid, "CONTENT", "STRONG"
    if stem and stem_counts.get(stem, 0) == 1:
        return stem, "STEM", "STRONG"
    return "", "UNDETERMINED", "NONE"


def sig_state(has_node, has_id):
    if has_node and has_id:
        return "SIGNED"
    if has_node:
        return "ID-MISSING"
    if has_id:
        return "NODE-MISSING"
    return "BOTH-MISSING"


def is_machine_node(value):
    """`bm-a`/`bm-b`… 才是机队节点；`node` 位塞任务名或占位（DASHENG/unknown）不是。

    这类行若照常入节点维度统计，会把「某台机器」和「某个任务」并成一列，
    结果静默错位而不是报错——所以单列 ODD-NODE，禁入分母。
    """
    text = str(value or "").strip()
    if not text:
        return False
    return text.startswith("bm-") and text.replace("-", "").isalnum()


def collect(root, days=None, now=None):
    """读回执目录原始 JSON（不经 normalize——签名普查看的是原始字段）。"""
    now = now or datetime.now()
    rdir = os.path.join(root, ceh.RECEIPT_REL)
    items, bad = [], []
    if not os.path.isdir(rdir):
        return items, bad, rdir
    for name in sorted(os.listdir(rdir)):
        path = os.path.join(rdir, name)
        if not os.path.isfile(path) or not name.lower().endswith(".json"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            bad.append(name)
            continue
        ts = None
        if isinstance(doc, dict):
            ts = ceh.parse_ts(doc.get("ts"))
        if days is not None and ts is not None:
            if ts < now - timedelta(days=days):
                continue
        items.append((name, doc, ts))
    return items, bad, rdir


def census(root, days=None, now=None):
    items, bad, rdir = collect(root, days=days, now=now)
    docs = [(n, d) for n, d, _ in items]
    vocab = node_vocabulary(docs)
    stems = [os.path.splitext(n)[0] for n, _, _ in items]
    stem_counts = {}
    for s in stems:
        stem_counts[s] = stem_counts.get(s, 0) + 1

    rows = []
    for (name, doc, ts), stem in zip(items, stems):
        has_node = bool(isinstance(doc, dict) and str(doc.get("node") or "").strip())
        has_id = bool(isinstance(doc, dict)
                      and str(doc.get("id") or doc.get("job") or "").strip())
        node, node_src, node_conf = derive_node(doc, stem, vocab)
        rid, id_src, id_conf = derive_id(doc, stem, stem_counts)
        state = sig_state(has_node, has_id)
        row = {
            "receipt": name,
            "ts": ts.isoformat(sep=" ") if ts else "",
            "sig_state": state,
            "node": node,
            "node_src": node_src,
            "node_conf": node_conf,
            "id": rid,
            "id_src": id_src,
            "id_conf": id_conf,
            # 派生后是否「可用于消费侧的节点+内部 id 双要素」
            "resolvable": bool(node) and bool(rid),
            "confidence": "STRONG" if node and rid and node_conf == "STRONG"
            and id_conf == "STRONG" else ("WEAK" if node and rid else "NONE"),
            # node 位不是机队节点词（任务名/占位）→ 单列，禁入节点维度分母
            "odd_node": bool(node) and not is_machine_node(node),
        }
        rows.append(row)

    counts = {k: 0 for k in SIG_STATES}
    for row in rows:
        counts[row["sig_state"]] += 1
    resolvable = sum(1 for r in rows if r["resolvable"])
    strong = sum(1 for r in rows if r["confidence"] == "STRONG")
    unresolvable = [r["receipt"] for r in rows if not r["resolvable"]]

    payload = {
        "verdict": "",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": os.path.abspath(root),
        "receipt_dir": rdir,
        "window_days": days,
        "counts": counts,
        "scanned": {"receipts": len(rows), "bad_json": len(bad)},
        "coverage": {
            "signed": counts["SIGNED"],
            "unsigned": len(rows) - counts["SIGNED"],
            "resolvable": resolvable,
            "strong": strong,
            "weak": sum(1 for r in rows if r["confidence"] == "WEAK"),
            "unresolvable": len(unresolvable),
            "odd_node": sum(1 for r in rows if r["odd_node"]),
        },
        "node_vocabulary": vocab,
        "unresolvable": unresolvable[:50],
        "odd_nodes": [{"receipt": r["receipt"], "node": r["node"]}
                      for r in rows if r["odd_node"]],
        "rows": rows,
    }
    if not rows:
        payload["verdict"] = "SIGN-CENSUS-NO-DATA"
    elif unresolvable:
        payload["verdict"] = "SIGN-CENSUS-GAPS"
    elif counts["SIGNED"] == len(rows):
        payload["verdict"] = "SIGN-CENSUS-CLEAN"
    else:
        payload["verdict"] = "SIGN-CENSUS-DERIVED"
    return payload


def merge_overlay(payload, out_path):
    """只收可解析行；按 receipt 幂等去重。返回 (新增, 总数)。"""
    existing = {}
    if os.path.isfile(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            for e in doc.get("entries") or []:
                existing[e.get("receipt")] = e
        except (OSError, ValueError):
            existing = {}
    added = 0
    for row in payload["rows"]:
        if not row["resolvable"] or row["receipt"] in existing:
            continue
        existing[row["receipt"]] = {
            "receipt": row["receipt"],
            "node": row["node"],
            "node_src": row["node_src"],
            "node_conf": row["node_conf"],
            "id": row["id"],
            "id_src": row["id_src"],
            "id_conf": row["id_conf"],
            "confidence": row["confidence"],
            "sig_state": row["sig_state"],
        }
        added += 1
    doc = {
        "schema": "receipt-sig-overlay/v1",
        "note": "本司离线派生签名册：仅供消费侧读，禁当作回执原件字段",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "entries": [existing[k] for k in sorted(existing)],
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return added, len(doc["entries"])


def render(payload):
    c = payload["counts"]
    cov = payload["coverage"]
    _out("VERDICT=%s window=%s receipts=%d bad_json=%d"
         % (payload["verdict"],
            payload["window_days"] if payload["window_days"] else "all",
            payload["scanned"]["receipts"], payload["scanned"]["bad_json"]))
    _out("states %s" % " ".join("%s=%d" % (k, c[k]) for k in SIG_STATES))
    _out("coverage unsigned=%d resolvable=%d strong=%d weak=%d unresolvable=%d odd_node=%d"
         % (cov["unsigned"], cov["resolvable"], cov["strong"], cov["weak"],
            cov["unresolvable"], cov.get("odd_node", 0)))
    if payload["node_vocabulary"]:
        _out("node_vocabulary %s" % ",".join(payload["node_vocabulary"]))
    for row in payload["rows"]:
        if row["sig_state"] != "SIGNED" and row["resolvable"]:
            _out("  DERIVED %s state=%s node=%s(%s) id=%s(%s) conf=%s"
                 % (row["receipt"], row["sig_state"], row["node"],
                    row["node_src"], row["id"], row["id_src"],
                    row["confidence"]))
    for name in payload["unresolvable"]:
        _out("  UNRESOLVABLE %s" % name)


def selftest():
    checks = []
    failed = set()

    def want(name, ok):
        checks.append(name)
        if not ok:
            failed.add(name)

    tmp = tempfile.mkdtemp(prefix="rcpt-sign-")
    try:
        root = os.path.join(tmp, "root")
        rdir = os.path.join(root, "cph4", "fleet", "queue-receipts")
        os.makedirs(rdir)

        def put(name, doc):
            with open(os.path.join(rdir, name), "w", encoding="utf-8") as fh:
                json.dump(doc, fh)

        put("S1-bm-a.json", {"id": "J-1", "node": "bm-a", "ts": "2026-11-07T00:00:00"})
        put("S2-bm-a.json", {"id": "J-2", "ts": "2026-11-07T00:01:00"})
        put("S3-bm-c.json", {"node": "bm-c", "ts": "2026-11-07T00:02:00"})
        put("game-packs-2026-11-03.json",
            {"ts": "2026-11-03T10:47:11", "status": "OK", "packs": []})
        put("model-radar-bm-b-20261103.json",
            {"node": "bm-b", "ts": "2026-11-03T23:39:06", "verdict": "OK"})
        put("dup.json", {"ts": "2026-11-07T00:03:00"})
        put("dup-bm-m.json", {"ts": "2026-11-07T00:04:00"})
        with open(os.path.join(rdir, "broken.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with open(os.path.join(rdir, "note.txt"), "w", encoding="utf-8") as fh:
            fh.write("not a receipt")

        payload = census(root)
        by = {r["receipt"]: r for r in payload["rows"]}

        want("S1 内容自带 node+id → SIGNED 且不解派生",
             by["S1-bm-a.json"]["sig_state"] == "SIGNED"
             and by["S1-bm-a.json"]["node_src"] == "CONTENT"
             and by["S1-bm-a.json"]["id_src"] == "CONTENT")
        want("S2 缺 node 但文件名 bm-a → NODE-MISSING 且 FILENAME/STRONG",
             by["S2-bm-a.json"]["sig_state"] == "NODE-MISSING"
             and by["S2-bm-a.json"]["node"] == "bm-a"
             and by["S2-bm-a.json"]["node_src"] == "FILENAME"
             and by["S2-bm-a.json"]["node_conf"] == "STRONG")
        want("S3 有 node 缺 id → ID-MISSING，id 取唯一主干",
             by["S3-bm-c.json"]["sig_state"] == "ID-MISSING"
             and by["S3-bm-c.json"]["id"] == "S3-bm-c"
             and by["S3-bm-c.json"]["id_src"] == "STEM")
        want("S4 game-packs 两缺 → BOTH-MISSING 且可凭主干解析",
             by["game-packs-2026-11-03.json"]["sig_state"] == "BOTH-MISSING"
             and by["game-packs-2026-11-03.json"]["id"] == "game-packs-2026-11-03"
             and by["game-packs-2026-11-03.json"]["id_src"] == "STEM")
        want("S5 model-radar 有 node 缺 id → ID-MISSING 且 node 来自内容",
             by["model-radar-bm-b-20261103.json"]["sig_state"] == "ID-MISSING"
             and by["model-radar-bm-b-20261103.json"]["node_src"] == "CONTENT")
        want("S6 节点词表只由声明 node 长成（不凭空造）",
             payload["node_vocabulary"] == ["bm-a", "bm-b", "bm-c"])
        want("S7 坏 JSON 记 bad_json 且不进分母",
             payload["scanned"]["bad_json"] == 1
             and payload["scanned"]["receipts"] == 7
             and "broken.json" not in by)
        want("S8 非 .json 文件不入普查", "note.txt" not in by)
        want("S9 无 node 且文件名无节点 token → node UNDETERMINED、整件不可解析",
             by["dup.json"]["node"] == ""
             and by["dup.json"]["node_src"] == "UNDETERMINED"
             and by["dup.json"]["id_src"] == "STEM"
             and not by["dup.json"]["resolvable"])
        want("S10 有冲突即判 GAPS（不许读成 CLEAN）",
             payload["verdict"] == "SIGN-CENSUS-GAPS"
             and "dup.json" in payload["unresolvable"])

        out_path = os.path.join(tmp, "overlay", "sig-overlay.json")
        before = ceh._sha(os.path.join(rdir, "S2-bm-a.json"))
        added1, total1 = merge_overlay(payload, out_path)
        added2, total2 = merge_overlay(payload, out_path)
        after = ceh._sha(os.path.join(rdir, "S2-bm-a.json"))
        want("S11 overlay 只收可解析行且幂等",
             added1 == total1 and added1 == 5 and added2 == 0 and total2 == 5)
        want("S12 overlay 不触碰对方回执字节", before == after)
        with open(out_path, "r", encoding="utf-8") as fh:
            ents = json.load(fh)["entries"]
        want("S13 overlay 不含不可解析件",
             all(e["receipt"] != "dup.json" for e in ents))

        strip = lambda p: {k: v for k, v in p.items() if k != "generated_at"}
        want("S14 双跑确定性（除生成时刻外逐字节一致）",
             json.dumps(strip(payload), sort_keys=True)
             == json.dumps(strip(census(root)), sort_keys=True))

        clean = os.path.join(tmp, "clean")
        os.makedirs(os.path.join(clean, "cph4", "fleet", "queue-receipts"))
        want("S15 零回执 → NO-DATA（不假绿）",
             census(clean)["verdict"] == "SIGN-CENSUS-NO-DATA")

        only_signed = os.path.join(tmp, "signed")
        os.makedirs(os.path.join(only_signed, "cph4", "fleet", "queue-receipts"))
        put2 = os.path.join(only_signed, "cph4", "fleet", "queue-receipts", "a.json")
        with open(put2, "w", encoding="utf-8") as fh:
            json.dump({"id": "J", "node": "bm-a"}, fh)
        want("S16 全签名 → CLEAN", census(only_signed)["verdict"] == "SIGN-CENSUS-CLEAN")

        # ---- T220：node 位不是机器节点词 → 单列 ODD-NODE ----
        odd = os.path.join(tmp, "odd")
        odir = os.path.join(odd, "cph4", "fleet", "queue-receipts")
        os.makedirs(odir)
        for nm, doc in (("good-bm-a.json", {"id": "J-OK", "node": "bm-a"}),
                        ("clock-resync.json", {"id": "J-ODD", "node": "DASHENG"}),
                        ("unknown-node.json", {"id": "J-UNK", "node": "unknown"})):
            with open(os.path.join(odir, nm), "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
        op = census(odd)
        ob = {r["receipt"]: r for r in op["rows"]}
        want("S17 node 位塞任务名/占位 → odd_node=True 并单列点名",
             ob["clock-resync.json"]["odd_node"] is True
             and ob["unknown-node.json"]["odd_node"] is True
             and op["coverage"]["odd_node"] == 2
             and {x["receipt"] for x in op["odd_nodes"]}
             == {"clock-resync.json", "unknown-node.json"})
        want("S18 bm-a 是真机器节点不入 ODD-NODE（判据不误伤）",
             ob["good-bm-a.json"]["odd_node"] is False)
        want("S19 ODD-NODE 不改签名判据（三件都 SIGNED，verdict 不受影响）",
             op["counts"]["SIGNED"] == 3
             and op["verdict"] == "SIGN-CENSUS-CLEAN")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for name in checks:
        _out("  %s %s" % ("PASS" if name not in failed else "FAIL", name))
    _out("selftest %d/%d PASS" % (len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="回执签名普查与派生面（本司只读+落 overlay）")
    ap.add_argument("cmd", choices=["census", "resolve", "selftest"])
    ap.add_argument("--root", default=ceh.DEFAULT_ROOT)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--receipt", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()

    payload = census(args.root, days=args.days)
    if args.cmd == "resolve":
        if not args.receipt:
            _out("resolve 需要 --receipt <文件名>")
            return 4
        rows = [r for r in payload["rows"] if r["receipt"] == args.receipt]
        if not rows:
            _out("resolve: 未在普查册 name=%s" % args.receipt)
            return 4
        if args.json:
            _out(json.dumps(rows[0], ensure_ascii=False, indent=1))
        else:
            r = rows[0]
            _out("%s state=%s node=%s(%s/%s) id=%s(%s/%s) resolvable=%s"
                 % (r["receipt"], r["sig_state"], r["node"], r["node_src"],
                    r["node_conf"], r["id"], r["id_src"], r["id_conf"],
                    r["resolvable"]))
        return 0

    render(payload)
    if args.write:
        out_path = args.out or default_out()
        added, total = merge_overlay(payload, out_path)
        _out("  overlay added=%d total=%d out=%s" % (added, total, out_path))
    if args.json_out:
        out = os.path.abspath(args.json_out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        _out("json_out=%s" % out)

    if payload["verdict"] == "SIGN-CENSUS-NO-DATA":
        return 3
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())