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
            # 回执时间戳带时区（...Z）时不能与 naive 的 now 比：先转本机 naive
            if ts is not None and ts.tzinfo is not None:
                ts = ts.astimezone().replace(tzinfo=None)
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


# ---- T221：产物侧签名普查（只测量，不建库不过滤）----
DELIV_JSON_EXT = (".json", ".jsonl")
DELIV_TEXT_EXT = (".md", ".txt", ".log", ".csv")
DELIV_MEDIA_EXT = (".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg",
                   ".gif", ".wav", ".mp3")
DATE_TOKEN_RE = re.compile(r"(?<!\d)(20\d{2})[-_]?(\d{2})[-_]?(\d{2})(?!\d)")
ID_LINE_RE = re.compile(r"(?im)^\s*(?:receipt[_-]?)?id\s*[:=]\s*\S+")
NODE_LINE_RE = re.compile(r"(?im)^\s*node\s*[:=]\s*\S+")


def deliverable_family(path):
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext in DELIV_JSON_EXT:
        return "json"
    if ext in DELIV_TEXT_EXT:
        return "text"
    if ext in DELIV_MEDIA_EXT:
        return "media"
    return "other"


def product_signature(path, family):
    """读盘上这一件，判它自身带不带 node/id。返回 (node, id, note)。

    不猜、不补、不扫全目录：产物侧签名本来就应该长在产物里，
    读不到就是没签，不是「换个地方找找」。
    """
    if family == "json":
        try:
            # utf-8-sig：他机产物带 BOM 是既成事实，读侧不容差（本仓写侧仍无 BOM）
            if str(path).lower().endswith(".jsonl"):
                # jsonl 是多条记录，整文件 json.load 必炸：只取首条非空行当签名字面
                doc = None
                with open(path, "r", encoding="utf-8-sig") as fh:
                    for line in fh:
                        if line.strip():
                            doc = json.loads(line)
                            break
            else:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    doc = json.load(fh)
        except (OSError, ValueError):
            return False, False, "UNREADABLE"
        if not isinstance(doc, dict):
            return False, False, "NON-OBJECT"
        node = str(doc.get("node") or doc.get("host") or "").strip()
        rid = str(doc.get("id") or doc.get("job") or "").strip()
        return bool(node), bool(rid), "CONTENT"
    if family == "text":
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                head = "".join(fh.readline() for _ in range(15))
        except OSError:
            return False, False, "UNREADABLE"
        has_node = bool(NODE_LINE_RE.search(head) or NODE_TOKEN_RE.search(head))
        has_id = bool(ID_LINE_RE.search(head))
        return has_node, has_id, "HEAD"
    if family == "media":
        # 媒体件只能靠文件名三要素：节点 token + 日期 + 非空主干
        name = os.path.basename(path)
        stem = os.path.splitext(name)[0]
        return (bool(NODE_TOKEN_RE.search(name)),
                bool(DATE_TOKEN_RE.search(name)) and bool(stem), "FILENAME")
    return False, False, "NO-RULE"


def receipt_deliverables(doc):
    """回执声称的产物路径（顶层 deliverable + deliverables[].path），保持原样去重。"""
    out = []
    if isinstance(doc, dict):
        primary = str(doc.get("deliverable") or "").strip()
        if primary:
            out.append(primary)
        items = doc.get("deliverables")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    rel = str(item.get("path") or "").strip()
                    if rel and rel not in out:
                        out.append(rel)
    return out


def deliverable_census(root, days=7, now=None, host=None):
    host = host or ceh.HOST_NODE
    now = now or datetime.now()
    items, bad, rdir = collect(root, days=days, now=now)

    refs, remote, no_path = {}, [], []
    for name, doc, _ts in items:
        paths = receipt_deliverables(doc)
        if not paths:
            no_path.append(name)
            continue
        rec = ceh.normalize(doc)
        # 他机产物不得用本机磁盘判真（同 ceh.evaluate 判据，禁两处各写一份）
        if rec is None or ceh.is_foreign(rec, host):
            for rel in paths:
                remote.append({"receipt": name, "deliverable": rel})
            continue
        for rel in paths:
            refs.setdefault(rel, []).append(name)

    rows = []
    for rel in sorted(refs):
        path = ceh.local_path(root, rel)
        exists = bool(path) and os.path.isfile(path)
        family = deliverable_family(rel)
        if not exists:
            rows.append({"deliverable": rel, "family": family, "state": "MISSING",
                         "node": False, "id": False, "note": "NOT-ON-DISK",
                         "receipts": refs[rel]})
            continue
        has_node, has_id, note = product_signature(path, family)
        if note in ("UNREADABLE", "NON-OBJECT"):
            state = "UNREADABLE"
        elif has_node and has_id:
            state = "SIGNED"
        elif has_node or has_id:
            state = "HALF"
        else:
            state = "UNSIGNED"
        rows.append({"deliverable": rel, "family": family, "state": state,
                     "node": has_node, "id": has_id, "note": note,
                     "receipts": refs[rel]})

    by_family = {}
    for fam in ("json", "text", "media", "other"):
        sel = [r for r in rows if r["family"] == fam and r["state"] != "MISSING"]
        by_family[fam] = {
            "eligible": len(sel),                      # 可带签名件数（在盘可检查）
            "signed": sum(1 for r in sel if r["state"] == "SIGNED"),
            "half": sum(1 for r in sel if r["state"] == "HALF"),
            "unsigned": sum(1 for r in sel if r["state"] == "UNSIGNED"),
            "unreadable": sum(1 for r in sel if r["state"] == "UNREADABLE"),
        }

    eligible = sum(1 for r in rows if r["state"] != "MISSING")
    signed = sum(1 for r in rows if r["state"] == "SIGNED")
    missing = sum(1 for r in rows if r["state"] == "MISSING")

    payload = {
        "verdict": "",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": os.path.abspath(root),
        "receipt_dir": rdir,
        "window_days": days,
        "host": host,
        "scanned": {"receipts": len(items), "bad_json": len(bad)},
        "headline": {"eligible": eligible, "signed": signed,
                     "rate": (round(float(signed) / eligible, 4) if eligible else 0.0)},
        "counts": {"deliverables_referenced": len(rows), "missing_on_disk": missing,
                   "remote_unverifiable": len(remote), "receipts_without_path": len(no_path)},
        "by_family": by_family,
        # BC-P-216 ①：先出两数，数字为 0 就到此为止，不许先建库后找证据
        "gate": {"generation_or_overlay_allowed": signed > 0,
                 "why": ("产物侧签名实测为 0：加 generation 钩子或扩 overlay 都无处可用"
                         if signed == 0 else
                         "已有产物自带签名，产物侧 overlay 才有落点")},
        "remote": remote[:30],
        "rows": rows,
    }
    if not items:
        payload["verdict"] = "DELIV-SIG-CENSUS-NO-DATA"
    elif eligible == 0:
        payload["verdict"] = "DELIV-SIG-CENSUS-NO-PRODUCT"
    elif signed == 0:
        payload["verdict"] = "DELIV-SIG-CENSUS-ABSENT"
    elif signed == eligible:
        payload["verdict"] = "DELIV-SIG-CENSUS-CLEAN"
    else:
        payload["verdict"] = "DELIV-SIG-CENSUS-PARTIAL"
    return payload


def render_deliv(payload):
    head = payload["headline"]
    c = payload["counts"]
    _out("VERDICT=%s window=%s receipts=%d products=%d"
         % (payload["verdict"], payload["window_days"],
            payload["scanned"]["receipts"], c["deliverables_referenced"]))
    _out("可带签名件数=%d 实际带签名件数=%d rate=%.1f%%"
         % (head["eligible"], head["signed"], head["rate"] * 100))
    _out("missing_on_disk=%d remote_unverifiable=%d no_path_receipts=%d"
         % (c["missing_on_disk"], c["remote_unverifiable"],
            c["receipts_without_path"]))
    for fam in ("json", "text", "media", "other"):
        f = payload["by_family"][fam]
        if not (f["eligible"] or f["half"] or f["unsigned"]):
            continue
        _out("  %-5s eligible=%d signed=%d half=%d unsigned=%d unreadable=%d"
             % (fam, f["eligible"], f["signed"], f["half"], f["unsigned"],
                f["unreadable"]))
    _out("gate %s（%s）"
         % ("ALLOW" if payload["gate"]["generation_or_overlay_allowed"] else "STOP",
            payload["gate"]["why"]))
    for row in payload["rows"]:
        if row["state"] in ("MISSING", "SIGNED"):
            continue
        _out("  %s %s family=%s" % (row["state"], row["deliverable"], row["family"]))


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

        # ---- T221：产物侧签名普查（只出两数，不建库）----
        droot = os.path.join(tmp, "deliv")
        ddir = os.path.join(droot, "cph4", "fleet", "queue-receipts")
        pdir = os.path.join(droot, "state")
        os.makedirs(ddir)
        os.makedirs(pdir)

        def put_json(rel, doc):
            with open(os.path.join(droot, rel), "w", encoding="utf-8") as fh:
                json.dump(doc, fh)

        def put_text(rel, text):
            with open(os.path.join(droot, rel), "w", encoding="utf-8") as fh:
                fh.write(text)

        put_json("state/signed-product.json", {"node": "bm-a", "id": "P-1", "v": 1})
        put_json("state/half-product.json", {"node": "bm-a", "v": 2})
        put_text("state/report.md", "# 报告\nnode: bm-a\nid: R-9\n正文\n")
        put_text("state/plain.md", "# 无签名报告\n正文\n")
        put_text("state/noisy.md", "# 报告\nbm-c 参与\nid: R-10\n")
        open(os.path.join(droot, "state", "clip-bm-a-20261107.mp4"), "wb").close()
        open(os.path.join(droot, "state", "clip.mp4"), "wb").close()
        put_text("state/broken.json", "{not json")

        def rcp(name, doc):
            with open(os.path.join(ddir, name), "w", encoding="utf-8") as fh:
                json.dump(doc, fh)

        rcp("d1.json", {"id": "J1", "node": "bm-a", "rc": 0, "seconds": 3,
                        "deliverable": "state/signed-product.json"})
        rcp("d2.json", {"id": "J2", "node": "bm-a", "rc": 0, "seconds": 3,
                        "deliverable": "state/half-product.json"})
        rcp("d3.json", {"id": "J3", "node": "bm-a", "rc": 0, "seconds": 3,
                        "deliverable": "state/report.md"})
        rcp("d4.json", {"id": "J4", "node": "bm-a", "rc": 0, "seconds": 3,
                        "deliverable": "state/plain.md"})
        rcp("d5.json", {"id": "J5", "node": "bm-a", "rc": 0, "seconds": 3,
                        "deliverable": "state/clip-bm-a-20261107.mp4"})
        rcp("d6.json", {"id": "J6", "node": "bm-a", "rc": 0, "seconds": 3,
                        "deliverable": "state/clip.mp4"})
        rcp("d7.json", {"id": "J7", "node": "bm-a", "rc": 0, "seconds": 3,
                        "deliverable": "state/gone.json"})
        rcp("d8.json", {"id": "J8", "node": "bm-c", "rc": 0, "seconds": 3,
                        "deliverable": "state/signed-product.json"})
        rcp("d9.json", {"id": "J9", "node": "bm-a", "rc": 0, "seconds": 3,
                        "deliverable": "state/signed-product.json"})

        dp = deliverable_census(droot, days=None, host="bm-a")
        drows = {r["deliverable"]: r for r in dp["rows"]}
        want("S20 json 自带 node+id → SIGNED；缺 id 只判 HALF",
             drows["state/signed-product.json"]["state"] == "SIGNED"
             and drows["state/half-product.json"]["state"] == "HALF")
        want("S21 文本件首行 node/id 行 → SIGNED；无签名 → UNSIGNED",
             drows["state/report.md"]["state"] == "SIGNED"
             and drows["state/plain.md"]["state"] == "UNSIGNED")
        want("S22 文件名三要素（节点+日期）→ SIGNED；缺节点 → UNSIGNED",
             drows["state/clip-bm-a-20261107.mp4"]["state"] == "SIGNED"
             and drows["state/clip.mp4"]["state"] == "UNSIGNED")
        want("S23 他机回执不入分母（本机不能判他机产物）",
             dp["counts"]["remote_unverifiable"] == 1
             and all("d8.json" not in r["receipts"] for r in dp["rows"])
             and drows["state/signed-product.json"]["receipts"] == ["d1.json", "d9.json"])
        want("S24 多张回执引用同一件只算一件（d1+d9 同路径）",
             dp["counts"]["deliverables_referenced"] == 7
             and drows["state/signed-product.json"]["receipts"] == ["d1.json", "d9.json"])
        want("S25 不在盘的产物单列 MISSING，不计入可带签名件数",
             drows["state/gone.json"]["state"] == "MISSING"
             and dp["counts"]["missing_on_disk"] == 1
             and dp["headline"]["eligible"] == 6)
        want("S26 两数如实（eligible=6 signed=3）且判 PARTIAL",
             dp["headline"] == {"eligible": 6, "signed": 3, "rate": 0.5}
             and dp["verdict"] == "DELIV-SIG-CENSUS-PARTIAL")
        want("S27 族分母互不串（json=2 text=2 media=2）",
             dp["by_family"]["json"]["eligible"] == 2
             and dp["by_family"]["text"]["eligible"] == 2
             and dp["by_family"]["media"]["eligible"] == 2
             and dp["by_family"]["text"]["signed"] == 1
             and dp["by_family"]["json"]["signed"] == 1)

        only_unsigned = os.path.join(tmp, "deliv-unsigned")
        odir = os.path.join(only_unsigned, "cph4", "fleet", "queue-receipts")
        os.makedirs(os.path.join(only_unsigned, "state"))
        os.makedirs(odir)
        with open(os.path.join(only_unsigned, "state", "u.md"), "w", encoding="utf-8") as fh:
            fh.write("正文\n")
        with open(os.path.join(odir, "u.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "U1", "node": "bm-a", "rc": 0, "seconds": 1,
                       "deliverable": "state/u.md"}, fh)
        up = deliverable_census(only_unsigned, days=None, host="bm-a")
        want("S28 产物侧签名实测为 0 → ABSENT 且 gate=STOP（不许先建库）",
             up["verdict"] == "DELIV-SIG-CENSUS-ABSENT"
             and up["headline"]["signed"] == 0
             and up["gate"]["generation_or_overlay_allowed"] is False)

        empty = os.path.join(tmp, "deliv-empty")
        os.makedirs(os.path.join(empty, "cph4", "fleet", "queue-receipts"))
        want("S29 零回执 → NO-DATA（不假绿）",
             deliverable_census(empty, days=None, host="bm-a")["verdict"]
             == "DELIV-SIG-CENSUS-NO-DATA")

        # ---- 带时区回执时间戳（...Z）不得让 --days 窗口崩 ----
        tz = os.path.join(tmp, "deliv-tz")
        tzdir = os.path.join(tz, "cph4", "fleet", "queue-receipts")
        os.makedirs(os.path.join(tz, "state"))
        os.makedirs(tzdir)
        with open(os.path.join(tz, "state", "z.json"), "w", encoding="utf-8") as fh:
            json.dump({"node": "bm-a", "id": "Z-1"}, fh)
        with open(os.path.join(tzdir, "z.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "Z", "node": "bm-a", "rc": 0, "seconds": 1,
                       "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "deliverable": "state/z.json"}, fh)
        tzp = deliverable_census(tz, days=1, host="bm-a")
        want("S30 回执 ts 带 Z 时 --days 窗口照常（时区不崩、当窗内件在册）",
             tzp["scanned"]["receipts"] == 1
             and tzp["headline"]["signed"] == 1
             and census(tz, days=1)["scanned"]["receipts"] == 1)

        # ---- jsonl 是多记录件，整文件 json.load 必炸（MANIFEST.jsonl 实测 UNREADABLE）----
        jl = os.path.join(tmp, "deliv-jsonl")
        jldir = os.path.join(jl, "cph4", "fleet", "queue-receipts")
        os.makedirs(os.path.join(jl, "state"))
        os.makedirs(jldir)
        with open(os.path.join(jl, "state", "MANIFEST.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"node": "bm-a", "id": "M-1"}) + "\n"
                     + json.dumps({"node": "bm-a", "id": "M-2"}) + "\n")
        with open(os.path.join(jldir, "m.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "MJ", "node": "bm-a", "rc": 0, "seconds": 1,
                       "deliverable": "state/MANIFEST.jsonl"}, fh)
        jlp = deliverable_census(jl, days=None, host="bm-a")
        want("S31 jsonl 取首条记录判签名（不判成 UNREADABLE）",
             jlp["rows"][0]["state"] == "SIGNED"
             and jlp["by_family"]["json"]["unreadable"] == 0)

        # ---- 他机产物带 BOM：读侧不容差（artifacts/bm-a/MANIFEST.jsonl 实测）----
        bom = os.path.join(tmp, "deliv-bom")
        bomdir = os.path.join(bom, "state", "m.json")
        os.makedirs(os.path.dirname(bomdir))
        with open(bomdir, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + json.dumps({"node": "bm-a"}).encode("utf-8"))
        want("S32 带 UTF-8 BOM 的 json 仍可读（判 HALF 而非 UNREADABLE）",
             product_signature(bomdir, "json") == (True, False, "CONTENT"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for name in checks:
        _out("  %s %s" % ("PASS" if name not in failed else "FAIL", name))
    _out("selftest %d/%d PASS" % (len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="回执签名普查与派生面（本司只读+落 overlay）")
    ap.add_argument("cmd",
                    choices=["census", "resolve", "deliverables", "selftest"])
    ap.add_argument("--root", default=ceh.DEFAULT_ROOT)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--host", default=ceh.HOST_NODE)
    ap.add_argument("--receipt", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()

    if args.cmd == "deliverables":
        dpayload = deliverable_census(args.root,
                                      days=args.days if args.days else 7,
                                      host=args.host)
        render_deliv(dpayload)
        if args.json_out:
            out = os.path.abspath(args.json_out)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(dpayload, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
            _out("json_out=%s" % out)
        if dpayload["verdict"] in ("DELIV-SIG-CENSUS-NO-DATA",
                                   "DELIV-SIG-CENSUS-NO-PRODUCT"):
            return 3
        return 0

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