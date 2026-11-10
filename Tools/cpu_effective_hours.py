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
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RECEIPT_REL = os.path.join("cph4", "fleet", "queue-receipts")
HOST_NODE = os.environ.get("FLUXGROUP_NODE", "bm-a")
HASH_CAP = 16 * 1024 * 1024  # 超过 16MB 只取前 1MB+size 做指纹，避免大件拖慢

BUCKETS = ("verified", "remote_unverif", "missing_local", "owner_elsewhere",
           "rc_fail", "replay", "copy", "path_reuse", "zero_seconds",
           "no_deliverable", "bad_json", "not_work_receipt", "unparsed",
           "sha_mismatch")

# 回执可能用这些键声明产物内容指纹（BC-P-173：文件名与大小都不变、内容变了时，
# 只有对账声明指纹才能区分「同一件被重新编码」与「换了另一个文件」）
SHA_KEYS = ("sha", "sha256", "deliverable_sha256", "artifact_sha256")
SHA_MIN_HEX = 7


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


def _norm_sha(value):
    """回执声明的指纹 → 小写十六进制串；不像指纹的值一律当没声明（不假红）。"""
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    text = text.split(":")[0]  # 兼容 "hex:size" 与 "sha256=hex"
    if len(text) < SHA_MIN_HEX:
        return ""
    if any(ch not in "0123456789abcdef" for ch in text):
        return ""
    return text


def declared_sha(doc):
    """取回执声明的内容指纹：顶层已知键优先，其次 deliverables[] 里同路径条目。"""
    for key in SHA_KEYS:
        norm = _norm_sha(doc.get(key))
        if norm:
            return norm
    items = doc.get("deliverables")
    if isinstance(items, list):
        want = str(doc.get("deliverable") or "")
        for item in items:
            if not isinstance(item, dict):
                continue
            if want and str(item.get("path") or "") not in ("", want):
                continue
            for key in SHA_KEYS:
                norm = _norm_sha(item.get(key))
                if norm:
                    return norm
    return ""


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


# 回执可能显式声明产物归属（M189）。实测 2026-11-11：2022 张回执 0 张带这些字段，
# 所以归属目前只能从路径反推；字段一旦出现必须压过路径推断（声明＞约定）。
OWNER_KEYS = ("owner", "product_node", "owner_node", "artifact_owner")


def _declared_owner(doc):
    for key in OWNER_KEYS:
        val = str((doc or {}).get(key) or "").strip()
        if val:
            return val, key
    return "", ""


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
        owner, owner_key = _declared_owner(doc)
        return {
            "schema": "llm",
            "id": str(doc.get("job") or ""),
            "node": str(doc.get("line") or "llm"),
            "ts": parse_ts(doc.get("ts")),
            "seconds": _num(doc.get("sec")),
            "deliverable": str(doc.get("path") or ""),
            "rc_ok": state in ("DONE", "OK", "PASS"),
            "rc": state,
            "declared_sha": declared_sha(doc),
            "owner": owner,
            "owner_key": owner_key,
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
        "declared_sha": declared_sha(doc),
        "owner": _declared_owner(doc)[0],
        "owner_key": _declared_owner(doc)[1],
        # 回执自己的成败断言（M190）：rc=0 与「产物写出来了」是两件事，
        # 11-11 实测 biggame-playable-2026-11-03-bm-a.json 即 rc=0/ok_rc=[0]
        # 却 outcome=FAIL + error_class=E-ARTIFACT + deliverable_exists=false。
        "outcome": str(doc.get("outcome") or "").upper(),
        "error_class": str(doc.get("error_class") or ""),
        "declared_missing": doc.get("deliverable_exists") is False,
    }


SELF_FAIL_OK_OUTCOMES = ("", "OK", "PASS", "DONE")


def self_declared_failure(item):
    """回执自报失败的三要素（M190/M191 单源判据）：与 rc 无关。

    rc=0 只说明命令跑完；outcome=FAIL / error_class / deliverable_exists=false
    说的是产物没写出来。分诊（triage_missing）与 QA 探针（qa_smoke no.22）都
    读这里，避免同一规则两处手写后漂移。
    """
    signals = []
    if str(item.get("outcome") or "").upper() not in SELF_FAIL_OK_OUTCOMES:
        signals.append("outcome=%s" % item.get("outcome"))
    if item.get("error_class"):
        signals.append("error_class=%s" % item.get("error_class"))
    if item.get("declared_missing"):
        signals.append("deliverable_exists=false")
    return signals


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


def _is_machine_node(name):
    """bm-a/bm-b/... 这类才是机队节点名；llm schema 的 node 实为车道名，不当机判。"""
    return bool(name) and name.replace("-", "").isalnum() and name.startswith("bm-")


def is_foreign(rec, host):
    """他机回执不得用本机磁盘判真（T192 封存面共用此判据，禁两处各写一份）。"""
    node = rec.get("node") or ""
    if not node or node == host:
        return False
    return (_is_machine_node(node)
            or rec.get("schema") == "lane"
            or str(rec.get("deliverable") or "").startswith("/"))


_NODE_TOKEN = re.compile(r"bm-[a-z0-9]+")


def owner_from_deliverable(rel):
    """产物路径的**归属机器**（M187·只认目录段这个结构证据）。

    `cph4/fleet/artifacts/bm-b/MANIFEST.jsonl` 的 bm-b 是该产物区的属主——本机
    找不到它属正常，不是「本机缺件」。而文件名里的 bm-x（`model-radar-bm-c-
    20261103.json`）是**被作业对象**：那份雷达本就该由执行机写在本机，不改变归属。
    两个不同目录段同时出现＝路径自相矛盾，返回空（不判），禁按猜归属。
    """
    parts = [p for p in re.split(r"[\\/]+", (rel or "").replace("\\", "/")) if p]
    hits = sorted({p for p in parts[:-1] if _is_machine_node(p)})
    if len(hits) != 1:
        return ""
    return hits[0]


def subject_nodes(rel, host=""):
    """文件名里出现的机队节点名（被作业对象·仅作读数可追，不作归属证据）。"""
    base = os.path.basename((rel or "").replace("\\", "/"))
    return sorted({t for t in _NODE_TOKEN.findall(base.lower())
                   if t != (host or "")})


OWNER_KEYS = ("owner", "product_node", "owner_node", "artifact_owner")


def owner_from_receipt(rec):
    """回执自己声明的产物归属（M189 读侧字段面）。

    实测 2026-11-11：2022 张回执里 **0 张**带 owner 字段——所以当前只能靠路径反推。
    字段一旦出现就必须压过路径推断：声明是断言，路径只是约定。返回 (owner, src)。
    """
    val = str((rec or {}).get("owner") or "").strip()
    if val:
        return val, "field:%s" % ((rec or {}).get("owner_key") or "owner")
    return "", ""


def evaluate(records, root, host, days=None, node=None, now=None):
    now = now or datetime.now()
    cutoff = None
    if days:
        cutoff = now - timedelta(days=days)

    buckets = {k: 0 for k in BUCKETS}
    rows, skipped = [], []
    seen_keys = {}
    by_deliverable = {}
    sha_declared = 0
    sha_match = 0
    sha_mismatch = []

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

        # 他机回执不得用本机磁盘判真：repo 相对路径在每台机上同名同构，
        # 拿它在本机算 digest 会把两台机各自真跑出来的同名产物判成「复制当产物」，
        # 也会把本机残留的旧文件当成他机产物已核销（D-20261103-02 回执产物律 triage）。
        foreign = is_foreign(rec, host)
        if foreign:
            buckets["remote_unverif"] += 1
            skipped.append((rec, "REMOTE-UNVERIF"))
            continue

        # M187：产物落在他机产物区（artifacts/bm-b/…）＝归属错轴，不是本机缺件。
        # 这个判定只认目录段（结构证据）；文件名里的 bm-x 是被作业对象，不作归属证据
        # ——把 model-radar-bm-c 当成「不归本机」会把「该在本机写却没写」洗成清白。
        owner, owner_src = owner_from_receipt(rec)
        if not owner:
            owner = owner_from_deliverable(rec["deliverable"])
            owner_src = "path" if owner else ""
        subs = subject_nodes(rec["deliverable"], host)
        if subs:
            rec["subject_nodes"] = subs
        if owner and owner != host:
            rec["owner_node"] = owner
            rec["owner_src"] = owner_src
            buckets["owner_elsewhere"] += 1
            skipped.append((rec, "OWNER-ELSEWHERE owner=%s src=%s"
                            % (owner, owner_src or "?")))
            continue

        path = local_path(root, rec["deliverable"])
        exists = bool(path) and os.path.isfile(path)
        digest = _sha(path) if exists else ""
        if not exists:
            buckets["missing_local"] += 1
            skipped.append((rec, "MISSING-LOCAL"))
            continue

        if rec["seconds"] <= 0:
            buckets["zero_seconds"] += 1
            skipped.append((rec, "zero-seconds"))
            continue

        # 回执若声明了产物指纹，必须与盘上这一件对得上：文件在、大小也一样，
        # 但内容已被重新编码/替换时，这条回执核销的不是它声称的那一件（BC-P-173）。
        actual_hex = digest.split(":")[0] if digest else ""
        declared = rec.get("declared_sha") or ""
        if declared:
            sha_declared += 1
            if not (actual_hex.startswith(declared[:len(actual_hex)])
                    or declared.startswith(actual_hex)):
                buckets["sha_mismatch"] += 1
                sha_mismatch.append(rec)
                skipped.append((rec, "SHA-MISMATCH declared=%s actual=%s"
                                % (declared[:16], actual_hex)))
                continue
            sha_match += 1

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
            "sha_declared": declared,
            "sha_match": True if declared else None,
            "receipt": rec["receipt"],
            "schema": rec["schema"],
            "state": "VERIFIED",
        }
        rows.append(row)
        by_deliverable.setdefault((rec["node"], digest), []).append(row)

    # 复制当产物：同一节点上同一内容被不同 id 当成各自产物 → 后者不计净工时
    # （不同节点各自跑出同名同内容产物属正常，不算复制——见上方 foreign 判定）
    # 「复制」必须两个路径：同一路径被后一次运行覆盖，盘上只能读到最后一次内容，
    # 两条回执的 digest 必然相同——这不是搬运证据，是确定性重跑（M215 triage 1）。
    # 只有「不同路径 + 同内容」才构成复制证据；同路径重跑照计工时，仅做可见告警。
    copy_rows = []
    reuse_rows = []
    for _node_digest, group in by_deliverable.items():
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda r: (r["ts"], r["id"]))
        ids = {r["id"] for r in group}
        if len(ids) < 2:
            continue
        first = group[0]
        seen_paths = {first["deliverable"]: first["id"]}
        for dup in group[1:]:
            if dup["deliverable"] in seen_paths:
                dup["state"] = "PATH-REUSED"
                dup["reused_with"] = seen_paths[dup["deliverable"]]
                reuse_rows.append(dup)
                continue
            seen_paths[dup["deliverable"]] = dup["id"]
            dup["state"] = "COPY-AS-PRODUCT"
            dup["copy_of"] = first["id"]
            copy_rows.append(dup)
    if copy_rows:
        copy_set = {id(r) for r in copy_rows}
        rows = [r for r in rows if id(r) not in copy_set]
        buckets["copy"] += len(copy_rows)
    buckets["path_reuse"] += len(reuse_rows)

    buckets["verified"] = len(rows)
    net_seconds = sum(r["seconds"] for r in rows)
    per_node = {}
    for row in rows:
        slot = per_node.setdefault(row["node"] or "unknown", {"count": 0, "seconds": 0.0})
        slot["count"] += 1
        slot["seconds"] = round(slot["seconds"] + row["seconds"], 1)

    verdict = "ATTENTION" if (buckets["missing_local"] or buckets["copy"]
                              or buckets["replay"] or buckets["zero_seconds"]
                              or buckets["sha_mismatch"]
                              or buckets["owner_elsewhere"]) else "CLEAN"
    # 自报失败总览（M190）：outcome/error_class/deliverable_exists 是回执自己
    # 的成败断言，与 rc 无关。产物在不在都先记数，才能答「失败到底有没有写出东西」。
    self_fail = [r for r in records
                 if str(r.get("outcome") or "").upper() not in ("", "OK", "PASS", "DONE")
                 or r.get("error_class") or r.get("declared_missing")]
    fail_no_file = 0
    for r in self_fail:
        p = local_path(root, r.get("deliverable") or "")
        if not (p and os.path.isfile(p)):
            fail_no_file += 1
    self_fail_summary = {"total": len(self_fail),
                         "with_file": len(self_fail) - fail_no_file,
                         "without_file": fail_no_file,
                         "by_error_class": {}}
    for r in self_fail:
        k = r.get("error_class") or "(none)"
        self_fail_summary["by_error_class"][k] = \
            self_fail_summary["by_error_class"].get(k, 0) + 1

    payload = {
        "tool": "cpu_effective_hours",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": root,
        "host": host,
        "generated_ts_utc_offset": None,
        "window_days": days,
        "node_filter": node,
        "verdict": verdict,
        "self_fail": self_fail_summary,
        "totals": {
            "receipts_scanned": len(rows) + len(skipped) + buckets["unparsed"],
            "counted": len(rows),
            "net_seconds": round(net_seconds, 1),
            "net_hours": round(net_seconds / 3600.0, 3),
        },
        "buckets": buckets,
        "sha_coverage": {
            "declared": sha_declared,
            "matched": sha_match,
            "mismatched": len(sha_mismatch),
            "note": ("declared=n 表示有多少条回执声明了产物内容指纹；"
                     "declared=0 说明整批回执无法区分「同一件被重新编码」与「换了另一个文件」"),
        },
        "per_node": per_node,
        "rows": rows,
        "copies": [{"id": r["id"], "node": r["node"], "copy_of": r["copy_of"],
                    "deliverable": r["deliverable"]} for r in copy_rows],
        "path_reuses": [{"id": r["id"], "node": r["node"],
                         "reused_with": r["reused_with"],
                         "deliverable": r["deliverable"]} for r in reuse_rows],
        "skipped": [{"id": r["id"], "node": r["node"], "receipt": r.get("receipt", ""),
                     "reason": why, "deliverable": r["deliverable"],
                     "owner": r.get("owner_node") or "",
                     "owner_src": r.get("owner_src") or "",
                     "subject_nodes": r.get("subject_nodes") or [],
                     "outcome": r.get("outcome") or "",
                     "error_class": r.get("error_class") or "",
                     "declared_missing": bool(r.get("declared_missing"))}
                    for r, why in skipped],
    }
    return payload


def _git_last_commit(root, relpath):
    """该路径在**任意分支**上的最近提交行（只读）。空=从未入过库。"""
    try:
        run = subprocess.run(["git", "-C", root, "log", "--all", "--oneline",
                              "-1", "--", relpath],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=20)
        return (run.stdout or "").strip()
    except Exception:  # noqa: BLE001 - triage must classify, not crash
        return ""


_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _git_reach(root, hist_line):
    """hist 行里的提交相对 HEAD 的可达性（只读·M186）。

    `git log --all` 会把旁支备份分支上的提交也算成「曾入过库」，于是两种事实
    被塞进同一个标签：主线曾有后被移出（可找回）vs 从未进过主线（旁支件本
    就不在交付面）。这里只回答一件事——那笔提交从 HEAD 能不能走到。

    返回 {"reach": "HEAD"|"OFF-HEAD"|"UNKNOWN", "refs": [...], "sha": str}。
    UNKNOWN 一律不猜（git 不可用／非仓／sha 解析不了），由调用方如实降级。
    """
    sha = (hist_line or "").split(" ")[0] if (hist_line or "").strip() else ""
    out = {"reach": "UNKNOWN", "refs": [],
           "sha": sha if _SHA_RE.match(sha) else ""}
    if not out["sha"]:
        return out
    try:
        anc = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor",
                              out["sha"], "HEAD"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=20)
    except Exception:  # noqa: BLE001 - 判不了可达性就不猜
        return out
    if anc.returncode == 0:
        out["reach"] = "HEAD"
        return out
    if anc.returncode != 1:
        return out   # 128=坏对象/非仓，不是「不可达」结论
    try:
        refs = subprocess.run(["git", "-C", root, "for-each-ref",
                               "--contains", out["sha"],
                               "--format=%(refname:short)"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=20)
        out["refs"] = [ln.strip() for ln in (refs.stdout or "").splitlines()
                       if ln.strip()]
    except Exception:  # noqa: BLE001
        return out
    out["reach"] = "OFF-HEAD"
    return out


_DATE_IN_NAME = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")


def _name_date(name):
    """文件名里的日期（YYYY-MM-DD=三态之一）。读不到=空串，不猜。"""
    m = _DATE_IN_NAME.search(os.path.splitext(name)[0])
    return "%s-%s-%s" % m.groups() if m else ""


def _json_signature(path):
    """候选件内部声明的 (node, id)。读不到/非 JSON/非对象一律空，不猜。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            obj = json.load(fh)
    except Exception:  # noqa: BLE001 - 签名读取失败只降级，不使 triage 崩
        return "", ""
    if not isinstance(obj, dict):
        return "", ""
    node = str(obj.get("node") or obj.get("host") or "")
    rid = str(obj.get("id") or "")
    return node, rid


def _confirm(expected, cand):
    """候选与缺失件是否同一次工作（三要素：node / 内部 id / 日期）。

    返回 CONFIRMED / PARTIAL / NODE-CONFLICT / DATE-DIFF / ID-DIFF / NO-STATE。
    只有 CONFIRMED 才是「可改收据路径」的候选；冲突态是明确的反证，必须挡住——
    否则同名词干的别机/别次件会被当成路径笔误，催出一次错误的改路径动作。
    """
    known = 0
    for key in ("node", "id", "date"):
        exp, got = expected.get(key) or "", cand.get(key) or ""
        if not exp or not got:
            continue
        known += 1
        if exp != got:
            return {"node": "NODE-CONFLICT", "id": "ID-DIFF",
                    "date": "DATE-DIFF"}[key]
    if known >= 2:
        return "CONFIRMED"
    return "PARTIAL" if known == 1 else "NO-STATE"


OVERLAY_GLOB = "receipt-sig-overlay-*.json"
LOCAL_STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")


def load_sig_overlay(root=None, path=None):
    """读回执签名 overlay（receipt_signer 的产出），按 receipt 名索引。

    overlay 只补「回执自己没写 node/id」的那一半，且只认 STRONG 派生——
    弱派生值一旦入判就是把猜测当证据，宁可维持 PARTIAL。
    读不到=空表（调用方继续走收据原文，禁凭空提级）。

    搜索面=--root 的 state/ 与本司仓 state/（默认 root 是集团根，overlay 却由
    本司产出；只搜一处会「有 overlay 却报 none」，把接线做成假动作）。
    """
    meta = {"path": "", "entries": 0, "strong": 0, "weak": 0, "missing": ""}
    if not path:
        dirs = []
        for base in (root or DEFAULT_ROOT,):
            dirs.append(os.path.join(base, "state"))
        if LOCAL_STATE_DIR not in dirs:
            dirs.append(LOCAL_STATE_DIR)
        cands = []
        for d in dirs:
            found = sorted(glob.glob(os.path.join(d, OVERLAY_GLOB)))
            if found:
                cands = found   # 先命中者为准（root 面优先，本司仓为兜底）
                break
        if not cands:
            meta["missing"] = "no %s under %s" % (OVERLAY_GLOB, " | ".join(dirs))
            return {}, meta
        path = cands[-1]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        meta["missing"] = "unreadable: %s" % exc
        return {}, meta
    rows = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        meta["missing"] = "no entries[] in %s" % os.path.basename(path)
        return {}, meta
    index = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("receipt") or "")
        if not name:
            continue
        index[name] = row
        if str(row.get("confidence") or "").upper() == "STRONG":
            meta["strong"] += 1
        else:
            meta["weak"] += 1
    meta["path"] = path
    meta["entries"] = len(index)
    return index, meta


def _expected_for(item, overlay):
    """缺失件的期望签名：收据原文优先，缺的那一半才由 overlay STRONG 补。

    返回 (expected, src)。src 逐字段标 receipt/overlay，输出面可查「这条结论
    是靠收据自己说的，还是靠 overlay 推的」。
    """
    node = str(item.get("node") or "")
    rid = str(item.get("id") or "")
    src = {"node": "receipt" if node else "", "id": "receipt" if rid else ""}
    ent = (overlay or {}).get(str(item.get("receipt") or "")) or {}
    if not node and ent.get("node") and str(ent.get("node_conf") or "STRONG") == "STRONG":
        node = str(ent["node"])
        src["node"] = "overlay"
    if not rid and ent.get("id") and str(ent.get("id_conf") or "STRONG") == "STRONG":
        rid = str(ent["id"])
        src["id"] = "overlay"
    return {"node": node, "id": rid}, src


def triage_missing(payload, root, hist_fn=None, overlay=None, reach_fn=None):
    """MISSING-LOCAL 逐条定性（BC-P-211·BC-P-213 签名加固·T219 overlay·M186 可达性）。

    红本身不告诉任何人该怎么办：件不在盘，可能是「曾入过库后被删」「名字漂移」
    或「从来就没有」。三类处置完全不同（找回/改收据路径/作废收据），所以把
    git 全分支历史与同目录近名件作为判据显式出数，禁用人读猜。

    近名件同理不能只凭共享词干：11-07 实测 5 件 NAME-DRIFT 里 4 件是别机/别次
    的件被同名词干吸进来。故候选必须带内部签名（node/内部 id/日期）与缺失件
    对照，只有三要素无冲突且至少两项相符（CONFIRMED）才标 actionable。

    「历史里有」也不是一个结论（M186 实测 11/20 落在 backup 旁支）：可达性三态
    HEAD→IN-HEAD-HISTORY-REMOVED／OFF-HEAD→OFF-MAINLINE(带 ref)／判不了→
    HISTORY-UNVERIFIED，禁把旁支件读成「主线删掉了」。
    """

    hist_fn = hist_fn or _git_last_commit
    items = []
    for item in payload.get("skipped") or []:
        if item.get("reason") != "MISSING-LOCAL":
            continue
        rel = item.get("deliverable") or ""
        if not rel:
            items.append({"id": item.get("id"), "node": item.get("node"),
                          "deliverable": "", "class": "NO-PATH",
                          "hist": "", "variants": [], "candidates": [],
                          "actionable": [], "reach": {}})
            continue
        hist = hist_fn(root, rel)
        d = os.path.dirname(os.path.join(root, rel))
        base = os.path.basename(rel)
        expected, sig_src = _expected_for(item, overlay)
        base_expected = {"node": str(item.get("node") or ""),
                         "id": str(item.get("id") or ""),
                         "date": _name_date(base)}
        expected["date"] = base_expected["date"]
        stem_tokens = os.path.splitext(base)[0].lower().split("-")
        candidates = []
        if os.path.isdir(d):
            cands = []
            for name in os.listdir(d):
                if name == base:
                    continue
                toks = os.path.splitext(name)[0].lower().split("-")
                shared = 0
                for a, b in zip(stem_tokens, toks):
                    if a != b:
                        break
                    shared += 1
                if not shared:
                    continue
                # 共享词干越多越像同一个件；排序只定展示先后，不做判定
                cands.append((-shared, name))
            cands.sort()
            for _s, name in cands[:5]:
                path = os.path.join(d, name)
                node, rid = _json_signature(path) if os.path.isfile(path) else ("", "")
                cand = {"name": name, "node": node, "id": rid,
                        "date": _name_date(name)}
                cand["confirm"] = _confirm(expected, cand)
                cand["confirm_base"] = _confirm(base_expected, cand)
                candidates.append(cand)
        variants = [c["name"] for c in candidates]
        actionable = [c["name"] for c in candidates
                      if c["confirm"] == "CONFIRMED"]
        reach = {"reach": "", "refs": [], "sha": ""}
        if hist:
            try:
                reach = (reach_fn or _git_reach)(root, hist) or reach
            except Exception:  # noqa: BLE001 - 可达性判不了就不猜
                reach = {"reach": "UNKNOWN", "refs": [], "sha": ""}
            state = reach.get("reach")
            if state == "HEAD":
                cls = "IN-HEAD-HISTORY-REMOVED"
            elif state == "OFF-HEAD":
                cls = "OFF-MAINLINE"
            else:
                cls = "HISTORY-UNVERIFIED"
        elif variants:
            # 「同目录有近名件」不等于名字写错（M186/M187 同族第三处口径缺陷）：
            # 11-11 实测 9 件 NAME-DRIFT 里 8 件的候选全被日期/节点反证——那次
            # 那份根本没落盘，处置是补写或作废，不是改收据路径。故标签按候选的
            # confirm 汇总，CONFIRMED 才算真漂移。
            confirms = [c["confirm"] for c in candidates]
            if "CONFIRMED" in confirms:
                cls = "NAME-DRIFT"
            elif all(c in ("DATE-DIFF", "NODE-CONFLICT", "ID-DIFF")
                     for c in confirms):
                cls = "MISSING-THAT-OCCURRENCE"
            else:
                cls = "MISSING-AMBIGUOUS"
        else:
            cls = "NEVER-EXISTED"
        # 最强的处置信号先行：回执自己已承认失败（M190）。rc=0 只说明命令跑完，
        # outcome=FAIL / error_class / deliverable_exists=false 说的是产物没写出来。
        # 这类件不是「供给缺口」而是「已申报的失败」，处置是重排或免计数，与
        # 「以为写成了却找不到」完全不同，故压过历史/近名件判定（信息仍保留在
        # reach/variants 里可追）。
        self_declared = self_declared_failure(item)
        if self_declared:
            cls = "KNOWN-FAIL"
        items.append({"id": item.get("id"), "node": item.get("node"),
                      "deliverable": rel, "class": cls, "hist": hist,
                      "reach": reach,
                      "variants": variants, "candidates": candidates,
                      "actionable": actionable,
                      "sig_src": sig_src, "expected": expected,
                      "self_declared": self_declared,
                      "upgraded": [c["name"] for c in candidates
                                   if c["confirm"] == "CONFIRMED"
                                   and c["confirm_base"] != "CONFIRMED"]})
    return items


def report_triage(items, overlay_meta=None):
    if overlay_meta:
        if overlay_meta.get("path"):
            _out("MISSING-TRIAGE overlay=%s entries=%d strong=%d weak=%d"
                 % (os.path.basename(overlay_meta["path"]),
                    overlay_meta.get("entries", 0), overlay_meta.get("strong", 0),
                    overlay_meta.get("weak", 0)))
        else:
            _out("MISSING-TRIAGE overlay=none (%s)"
                 % (overlay_meta.get("missing") or "not provided"))
    if not items:
        _out("MISSING-TRIAGE none")
        return
    tally = {}
    actionable = 0
    upgraded = 0
    for it in items:
        tally[it["class"]] = tally.get(it["class"], 0) + 1
        actionable += len(it.get("actionable") or [])
        upgraded += len(it.get("upgraded") or [])
    _out("MISSING-TRIAGE %d 件  分类=%s  可改路径候选=%d  靠 overlay 提级=%d"
         % (len(items), " ".join("%s:%d" % (k, tally[k]) for k in sorted(tally)),
            actionable, upgraded))
    actions = [("自报失败(重排或免计数)", tally.get("KNOWN-FAIL", 0)),
               ("改收据路径", tally.get("NAME-DRIFT", 0)),
               ("补写或作废(那次没落盘)", tally.get("MISSING-THAT-OCCURRENCE", 0)),
               ("补证据(证据不足)", tally.get("MISSING-AMBIGUOUS", 0)),
               ("作废收据(从未存在)", tally.get("NEVER-EXISTED", 0)),
               ("支线取回", tally.get("OFF-MAINLINE", 0)),
               ("主线找回", tally.get("IN-HEAD-HISTORY-REMOVED", 0)),
               ("可达性待查", tally.get("HISTORY-UNVERIFIED", 0)),
               ("无路径", tally.get("NO-PATH", 0))]
    live = ["%s=%d" % (k, v) for k, v in actions if v]
    _out("处置面 %s" % (" ".join(live) if live else "none"))
    for it in items:
        extra = ""
        if it["hist"]:
            rc = it.get("reach") or {}
            state = rc.get("reach") or "UNKNOWN"
            if state == "HEAD":
                where = "HEAD"
            elif state == "OFF-HEAD":
                where = ",".join(rc.get("refs") or []) or "off-head(no-ref)"
            else:
                where = "unknown"
            extra = "  hist=%s  reach=%s" % (it["hist"][:60], where)
        src = it.get("sig_src") or {}
        sig = "node=%s,id=%s" % (src.get("node") or "-", src.get("id") or "-")
        sd = it.get("self_declared") or []
        if sd:
            extra += "  why=%s" % ",".join(sd)
        _out("  %-22s %-6s %-18s %s  sig[%s]%s"
             % (it["class"], it["node"], (it["id"] or "")[:18], it["deliverable"],
                sig, extra))
        for cand in it.get("candidates") or []:
            up = "  ↑overlay" if cand.get("confirm") == "CONFIRMED" and \
                cand.get("confirm_base") != "CONFIRMED" else ""
            _out("    cand %-42s confirm=%-13s base=%-13s node=%-5s id=%-18s date=%s%s"
                 % (cand["name"], cand["confirm"], cand.get("confirm_base", "-"),
                    cand["node"] or "-", (cand["id"] or "")[:18] or "-",
                    cand["date"] or "-", up))


def report(payload, show_list=False):
    t = payload["totals"]
    b = payload["buckets"]
    _out("CPU 有效工时核算（root=%s host=%s window=%s）"
         % (payload["root"], payload["host"],
            ("%sd" % payload["window_days"]) if payload["window_days"] else "all"))
    _out("verdict=%s  counted=%d  net=%sh  (%.0fs)"
         % (payload["verdict"], t["counted"], t["net_hours"], t["net_seconds"]))
    _out("receipts_scanned=%d  verified=%d  remote_unverif=%d  missing_local=%d  "
         "owner_elsewhere=%d  "
         "rc_fail=%d  replay=%d  copy=%d  path_reuse=%d  zero_seconds=%d  "
         "no_deliverable=%d  bad_json=%d  not_work_receipt=%d"
         % (t["receipts_scanned"], b["verified"], b["remote_unverif"],
            b["missing_local"], b["owner_elsewhere"], b["rc_fail"], b["replay"],
            b["copy"],
            b["path_reuse"], b["zero_seconds"],
            b["no_deliverable"], b["bad_json"], b["not_work_receipt"]))
    cover = payload.get("sha_coverage") or {}
    _out("sha_coverage: declared=%s matched=%s mismatched=%s"
         % (cover.get("declared", "?"), cover.get("matched", "?"),
            cover.get("mismatched", "?")))
    sf = payload.get("self_fail") or {}
    if sf:
        cls = sf.get("by_error_class") or {}
        _out("self_fail(回执自报失败): total=%d with_file=%d without_file=%d  %s"
             % (sf.get("total", 0), sf.get("with_file", 0),
                sf.get("without_file", 0),
                " ".join("%s:%d" % (k, v) for k, v in sorted(cls.items()))))
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
    for item in payload.get("path_reuses") or []:
        _out("  REUSE %s/%s 同路径重跑（计工时·同 %s）%s"
             % (item["node"], item["id"], item["reused_with"], item["deliverable"]))
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


def _lane(rid, node, seconds, deliverable, ts, rc=0, **extra):
    rec = {"id": rid, "node": node, "rc": rc, "seconds": seconds,
           "cmd": "python x.py", "deliverable": deliverable,
           "deliverable_exists": True, "ts": ts}
    rec.update(extra)
    return rec


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
                           ("c.json", {"v": 3}), ("old.json", {"v": 7}),
                           ("rerun.json", {"v": 11})):
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
        # 他机回执声称的 repo 相对路径在本机同名存在（out/a.json）：
        # 这是本机 J-OK 的产物，不能被当成 bm-c 的产物核销，更不得判成复制
        _write(os.path.join(rdir, "r16.json"),
               _lane("J-REMOTE-SAMEPATH", "bm-c", 500, "out/a.json", fresh))
        # 同一路径被两次运行覆盖：盘上只留最后一次内容，两条回执 digest 必然相同。
        # 这是重跑不是搬运——工时须计、不得剔除（M215 triage 定谳）
        _write(os.path.join(rdir, "r17.json"),
               _lane("J-RERUN1", "bm-a", 200, "out/rerun.json", fresh))
        _write(os.path.join(rdir, "r18.json"),
               _lane("J-RERUN2", "bm-a", 250, "out/rerun.json", fresh))

        recs, bad, other = load_receipts(rdir)
        want("S1 坏 JSON 进 bad_json", len(bad) == 1)
        want("S2 list 形态被展开为一条", any(r["id"] == "J-LIST" for r in recs))

        p = evaluate(recs, root, "bm-a", days=1, now=now)
        want("S3 有效行计入 (J-OK/J-COPY1/llm-ok/J-LIST/J-RERUN1/J-RERUN2)",
             p["totals"]["counted"] == 6)
        if os.environ.get("CPUEFF_DEBUG"):
            _out("DEBUG totals=%s rows=%s" % (p["totals"],
                  [(r["id"], r["seconds"]) for r in p["rows"]]))
        want("S4 纯有效工时=3600+100+60+30+200+250 秒",
             abs(p["totals"]["net_seconds"] - 4240.0) < 0.01)
        want("S5 rc 失败不入账", p["buckets"]["rc_fail"] >= 1)
        want("S6 本机缺产物=missing_local 且 verdict ATTENTION",
             p["buckets"]["missing_local"] == 1 and p["verdict"] == "ATTENTION")
        want("S7 他机回执一律 remote_unverif（含本机有同名文件的两条）",
             p["buckets"]["remote_unverif"] == 2)
        want("S18 他机 repo 相对路径不得用本机文件核销、也不得判复制",
             all(r["id"] != "J-REMOTE-SAMEPATH" for r in p["rows"])
             and all(c["id"] != "J-REMOTE-SAMEPATH" for c in p["copies"])
             and all(c["id"] != "J-OK" for c in p["copies"]))
        want("S8 同 id 同产物重放只计一次", p["buckets"]["replay"] == 1)
        want("S9 复制当产物被抓(J-COPY2)",
             any(c["id"] == "J-COPY2" for c in p["copies"]))
        want("S23 同路径同内容=重跑不作复制、工时照计(J-RERUN2)",
             all(c["id"] != "J-RERUN2" for c in p["copies"])
             and any(r["id"] == "J-RERUN2" for r in p["rows"])
             and p["buckets"]["path_reuse"] == 1)
        want("S24 不同路径同内容仍判复制（copy 计数不因新规则放水）",
             p["buckets"]["copy"] == 1
             and [c["id"] for c in p["copies"]] == ["J-COPY2"])
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
             p["buckets"]["no_deliverable"] == 1 and p["totals"]["counted"] == 6)
        want("S17 QC 结果件（非执行回执）单列 not_work_receipt",
             len(other) == 1 and p["buckets"]["not_work_receipt"] == 0)

        # ---- 声明指纹对账面（BC-P-173）：隔离子树，不扰上面 17 条的计数 ----
        sha_root = os.path.join(tmp, "sha", "root")
        sdir = os.path.join(sha_root, RECEIPT_REL)
        os.makedirs(sdir, exist_ok=True)
        for name, body in (("sh1.json", {"v": "one"}), ("sh2.json", {"v": "two"}),
                           ("sh3.json", {"v": "three"})):
            _write(os.path.join(sha_root, "out", name), body)
        real1 = _sha(os.path.join(sha_root, "out", "sh1.json")).split(":")[0]
        rec_ok = _lane("J-SHA-OK", "bm-a", 60, "out/sh1.json", fresh)
        rec_ok["sha"] = real1[:8]                      # 回执声明截断前缀也算命中
        _write(os.path.join(sdir, "s1.json"), rec_ok)
        rec_bad = _lane("J-SHA-BAD", "bm-a", 120, "out/sh2.json", fresh)
        rec_bad["sha256"] = "deadbeefdeadbeef"         # 文件在、大小不变，内容不是那一件
        _write(os.path.join(sdir, "s2.json"), rec_bad)
        rec_junk = _lane("J-SHA-JUNK", "bm-a", 30, "out/sh3.json", fresh)
        rec_junk["sha"] = "not-a-hash"                 # 不像指纹 → 当没声明，不假红
        _write(os.path.join(sdir, "s3.json"), rec_junk)

        srecs, _sbad, _sother = load_receipts(sdir)
        sp = evaluate(srecs, sha_root, "bm-a", days=1, now=now)
        want("S19 声明指纹与盘上一致（含截断前缀）→ 计入",
             any(r["id"] == "J-SHA-OK" and r["sha_match"] for r in sp["rows"]))
        want("S20 声明指纹与盘上不一致 → sha_mismatch 剔除+ATTENTION",
             sp["buckets"]["sha_mismatch"] == 1
             and any("SHA-MISMATCH" in why for _r, why in
                     [(None, x["reason"]) for x in sp["skipped"]])
             and sp["verdict"] == "ATTENTION")
        want("S21 非指纹形态的声明值不假红（按未声明计）",
             any(r["id"] == "J-SHA-JUNK" for r in sp["rows"])
             and sp["totals"]["counted"] == 2)
        want("S22 指纹覆盖率显式出数（declared=2 matched=1 mismatched=1）",
             sp["sha_coverage"]["declared"] == 2
             and sp["sha_coverage"]["matched"] == 1
             and sp["sha_coverage"]["mismatched"] == 1)

        # ---- M187 归属轴：产物落在他机产物区 ≠ 本机缺件 ----
        oroot = os.path.join(tmp, "ownerroot")
        ordir = os.path.join(oroot, RECEIPT_REL)
        os.makedirs(ordir, exist_ok=True)
        _write(os.path.join(ordir, "o1.json"),
               _lane("O-OTHER", "bm-a", 60,
                     "cph4/fleet/artifacts/bm-b/MANIFEST.jsonl", fresh))
        _write(os.path.join(ordir, "o2.json"),
               _lane("O-OWN", "bm-a", 60,
                     "cph4/fleet/artifacts/bm-a/MANIFEST.jsonl", fresh))
        _write(os.path.join(ordir, "o3.json"),
               _lane("O-SUBJECT", "bm-a", 60,
                     "cph4/fleet/queue-receipts/model-radar-bm-c-20261103.json",
                     fresh))
        _write(os.path.join(ordir, "o4.json"),
               _lane("O-AMBIG", "bm-a", 60,
                     "cph4/fleet/artifacts/bm-b/bm-c/x.json", fresh))
        orecs, _obad, _ooth = load_receipts(ordir)
        op = evaluate(orecs, oroot, "bm-a", days=1, now=now)
        oslot = {s["id"]: s for s in op["skipped"]}
        want("S42 产物在他机产物区（artifacts/bm-b/…）→ OWNER-ELSEWHERE 且不计入本机缺件",
             op["buckets"]["owner_elsewhere"] == 1
             and op["buckets"]["missing_local"] == 3
             and oslot["O-OTHER"]["reason"].startswith("OWNER-ELSEWHERE owner=bm-b")
             and oslot["O-OTHER"]["owner"] == "bm-b")
        want("S43 本机自己的产物区（artifacts/bm-a/…）缺件仍判 MISSING-LOCAL",
             oslot["O-OWN"]["reason"] == "MISSING-LOCAL")
        want("S44 文件名里的他机 token 只是被作业对象 → 不重分类，仅留读数",
             oslot["O-SUBJECT"]["reason"] == "MISSING-LOCAL"
             and oslot["O-SUBJECT"]["subject_nodes"] == ["bm-c"])
        want("S45 路径含两个不同节点目录 = 自相矛盾 → 不判归属，仍记缺件",
             oslot["O-AMBIG"]["reason"] == "MISSING-LOCAL"
             and oslot["O-AMBIG"]["owner"] == "")
        only_other = [r for r in orecs if r["id"] == "O-OTHER"]
        po = evaluate(only_other, oroot, "bm-a", days=1, now=now)
        want("S46 只有归属错轴、零缺件时仍判 ATTENTION（不得读成 CLEAN）",
             po["buckets"]["owner_elsewhere"] == 1
             and po["buckets"]["missing_local"] == 0
             and po["verdict"] == "ATTENTION")
        want("S47 路径无节点段时按回执字段判归属（owner_src=field）",
             oslot["O-OTHER"]["owner_src"] == "path"
             and evaluate([normalize(_lane("O-FIELD", "bm-a", 60, "out/none.json",
                                           fresh, owner="bm-b"))],
                          oroot, "bm-a", days=1,
                          now=now)["skipped"][0]["owner_src"] == "field:owner")
        fld = evaluate([normalize(_lane("O-FIELD-OWN", "bm-a", 60,
                                        "cph4/fleet/artifacts/bm-b/x.json", fresh,
                                        owner="bm-a"))],
                       oroot, "bm-a", days=1, now=now)
        fslot = {s["id"]: s for s in fld["skipped"]}
        want("S48 回执声明 owner=本机时压过路径推断（不判 OWNER-ELSEWHERE）",
             fld["buckets"]["owner_elsewhere"] == 0
             and fslot["O-FIELD-OWN"]["reason"] == "MISSING-LOCAL")

        td = os.path.join(tmp, "triage_root")
        tdir = os.path.join(td, "cph4", "fleet", "queue-receipts")
        os.makedirs(tdir)
        with open(os.path.join(tdir, "model-radar-bm-a-20261104.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{}")
        # 签名加固夹具：同词干但内部 node 是他机 → 不是同一件，不可改路径
        with open(os.path.join(tdir, "node-radar-bm-b-20261103.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"node": "bm-b", "id": "E-CONFLICT"}, fh)
        # 三要素齐（node 同 / 同日期 / 内部 id 同）→ 唯一可改路径候选
        with open(os.path.join(tdir, "node-radar-bm-a-20261102.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"node": "bm-a", "id": "F-MATCH"}, fh)
        with open(os.path.join(tdir, "node-radar-bm-a-20261102-v2.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"node": "bm-a", "id": "F-MATCH"}, fh)
        tpay = {"skipped": [
            {"id": "A-NEVER", "node": "bm-a", "reason": "MISSING-LOCAL",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "ghost-a.json")},
            {"id": "B-DRIFT", "node": "bm-a", "reason": "MISSING-LOCAL",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "model-radar-bm-a-20261103.json")},
            {"id": "C-HIST", "node": "bm-a", "reason": "MISSING-LOCAL",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "old.json")},
            {"id": "D-REMOTE", "node": "bm-b", "reason": "REMOTE-UNVERIF",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "x.json")},
            {"id": "E-CONFLICT", "node": "bm-a", "reason": "MISSING-LOCAL",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "node-radar-bm-a-20261103.json")},
            {"id": "F-MATCH", "node": "bm-a", "reason": "MISSING-LOCAL",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "node-radar-bm-a-20261102.json")}]}
        tri = triage_missing(
            tpay, td,
            hist_fn=lambda r, p: "abc1234 add" if p.endswith("old.json") else "",
            reach_fn=lambda r, h: {"reach": "HEAD", "refs": [],
                                   "sha": "abc1234"})
        kinds = {i["id"]: i["class"] for i in tri}
        by_id = {i["id"]: i for i in tri}
        want("S25 从未入过库且无近名件 → NEVER-EXISTED",
             kinds.get("A-NEVER") == "NEVER-EXISTED")
        want("S26 同目录只有别日件（DATE-DIFF 反证）→ MISSING-THAT-OCCURRENCE"
             "（那次没落盘·不许读成名字写错），且仍列出近名件",
             kinds.get("B-DRIFT") == "MISSING-THAT-OCCURRENCE"
             and any("model-radar-bm-a-20261104.json" in i["variants"]
                     for i in tri if i["id"] == "B-DRIFT"))
        want("S27 可从 HEAD 到达 → IN-HEAD-HISTORY-REMOVED（可找回）",
             kinds.get("C-HIST") == "IN-HEAD-HISTORY-REMOVED")
        want("S28 只对 MISSING-LOCAL 定性，其余跳过项不入表",
             len(tri) == 5 and "D-REMOTE" not in kinds)
        e_cands = {c["name"]: c["confirm"]
                  for c in by_id["E-CONFLICT"]["candidates"]}
        want("S29 近名件内部 node 是他机 → NODE-CONFLICT 且不列可改路径",
             e_cands.get("node-radar-bm-b-20261103.json") == "NODE-CONFLICT"
             and by_id["E-CONFLICT"]["actionable"] == [])
        b_cands = {c["name"]: c["confirm"]
                  for c in by_id["B-DRIFT"]["candidates"]}
        want("S30 仅同词干、日期不同 → DATE-DIFF，不算可改路径",
             b_cands.get("model-radar-bm-a-20261104.json") == "DATE-DIFF"
             and by_id["B-DRIFT"]["actionable"] == [])
        f_cands = {c["name"]: c["confirm"]
                  for c in by_id["F-MATCH"]["candidates"]}
        want("S31 node+内部 id+日期三项相符 → CONFIRMED 且列入可改路径",
             f_cands.get("node-radar-bm-a-20261102-v2.json") == "CONFIRMED"
             and by_id["F-MATCH"]["actionable"]
             == ["node-radar-bm-a-20261102-v2.json"])

        # ---- M186 可达性：历史里曾有 ≠ 主线曾有 ----
        def tri_one(pid, fname, reach):
            return triage_missing(
                {"skipped": [{"id": pid, "node": "bm-a",
                              "reason": "MISSING-LOCAL",
                              "deliverable": os.path.join(
                                  "cph4", "fleet", "queue-receipts", fname)}]},
                td, hist_fn=lambda r, p: "abc1234 add",
                reach_fn=lambda r, h: reach)[0]

        off = tri_one("G-OFFBRANCH", "side.json",
                      {"reach": "OFF-HEAD", "refs": ["backup/bm-a-20261110"],
                       "sha": "abc1234"})
        unk = tri_one("H-UNVERIFIED", "norepo.json",
                      {"reach": "UNKNOWN", "refs": [], "sha": ""})
        head = tri_one("I-HEADREMOVED", "mainline.json",
                       {"reach": "HEAD", "refs": [], "sha": "abc1234"})
        want("S39 历史里有但只在旁支 → OFF-MAINLINE 并带 ref 名",
             off["class"] == "OFF-MAINLINE"
             and off["reach"]["refs"] == ["backup/bm-a-20261110"])
        want("S40 可达性判不了 → HISTORY-UNVERIFIED（不冒充主线删除/旁支）",
             unk["class"] == "HISTORY-UNVERIFIED")
        want("S41 同一路径可由 HEAD 到达 → IN-HEAD-HISTORY-REMOVED",
             head["class"] == "IN-HEAD-HISTORY-REMOVED")

        # ---- M190：近名件被反证 ≠ 名字漂移（三类处置互不相同）----
        with open(os.path.join(tdir, "amb-20261105-old.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("no signature here")
        tpay["skipped"].append(
            {"id": "J-AMBIG", "node": "bm-a", "reason": "MISSING-LOCAL",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "amb-20261105.json")})
        tri_m = triage_missing(
            tpay, td,
            hist_fn=lambda r, p: "abc1234 add" if p.endswith("old.json") else "",
            reach_fn=lambda r, h: {"reach": "HEAD", "refs": [], "sha": "abc1234"})
        mk = {i["id"]: i for i in tri_m}
        want("S49 候选全是节点/日期反证 → MISSING-THAT-OCCURRENCE 且无可改路径",
             mk["E-CONFLICT"]["class"] == "MISSING-THAT-OCCURRENCE"
             and mk["E-CONFLICT"]["actionable"] == [])
        want("S50 候选无任何可对照要素 → MISSING-AMBIGUOUS（既不判漂移也不判没写）",
             mk["J-AMBIG"]["class"] == "MISSING-AMBIGUOUS"
             and mk["J-AMBIG"]["actionable"] == [])
        want("S51 候选三要素齐（CONFIRMED）→ 仍判 NAME-DRIFT 并列出可改路径",
             mk["F-MATCH"]["class"] == "NAME-DRIFT"
             and mk["F-MATCH"]["actionable"] == ["node-radar-bm-a-20261102-v2.json"])
        mixed = [(i["id"], i["class"]) for i in tri_m
                 if (i["class"] == "NAME-DRIFT") != bool(i["actionable"])]
        want("S52 标签与动作一致：NAME-DRIFT ⟺ 有可改路径候选，其余形态不得混报",
             not mixed)

        # ---- M190 第二层：回执自报失败 ≠ 缺件（rc=0 只证明命令跑完）----
        tpay["skipped"] += [
            {"id": "K-KNOWNFAIL", "node": "bm-a", "reason": "MISSING-LOCAL",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "node-radar-bm-a-20261102.json"),
             "outcome": "FAIL", "error_class": "E-ARTIFACT"},
            {"id": "L-DECLMISS", "node": "bm-a", "reason": "MISSING-LOCAL",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "model-radar-bm-a-20261103.json"),
             "declared_missing": True}]
        tri_k = triage_missing(
            tpay, td, hist_fn=lambda r, p: "",
            reach_fn=lambda r, h: {"reach": "HEAD", "refs": [], "sha": ""})
        kk = {i["id"]: i for i in tri_k}
        want("S53 回执自报 outcome=FAIL → KNOWN-FAIL，压过近名件 CONFIRMED"
             "（已申报的失败不是供给缺口）",
             kk["K-KNOWNFAIL"]["class"] == "KNOWN-FAIL"
             and "outcome=FAIL" in kk["K-KNOWNFAIL"]["self_declared"])
        want("S54 回执自报 deliverable_exists=false → KNOWN-FAIL"
             "（产物没写出来是回执自己的陈述，不是找不到）",
             kk["L-DECLMISS"]["class"] == "KNOWN-FAIL"
             and "deliverable_exists=false" in kk["L-DECLMISS"]["self_declared"])

        # ---- T219 overlay 接线：收据缺 node/id 时由 STRONG 签名补齐 ----
        with open(os.path.join(tdir, "unsigned-20261102.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"node": "bm-a", "id": "G-OVERLAY"}, fh)
        with open(os.path.join(tdir, "weak-20261102.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"node": "bm-a", "id": "H-WEAK"}, fh)
        tpay["skipped"] += [
            {"id": "", "node": "", "receipt": "unsigned-20261102.json",
             "reason": "MISSING-LOCAL",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "unsigned-20261102-out.json")},
            {"id": "", "node": "", "receipt": "weak-20261102.json",
             "reason": "MISSING-LOCAL",
             "deliverable": os.path.join("cph4", "fleet", "queue-receipts",
                                         "weak-20261102-out.json")}]
        ovy = {"unsigned-20261102.json": {
                   "receipt": "unsigned-20261102.json", "node": "bm-a",
                   "node_conf": "STRONG", "id": "G-OVERLAY",
                   "id_conf": "STRONG", "confidence": "STRONG"},
               "weak-20261102.json": {
                   "receipt": "weak-20261102.json", "node": "bm-a",
                   "node_conf": "WEAK", "id": "H-WEAK",
                   "id_conf": "WEAK", "confidence": "WEAK"}}
        tri_o = triage_missing(
            tpay, td,
            hist_fn=lambda r, p: "abc1234 add" if p.endswith("old.json") else "",
            reach_fn=lambda r, h: {"reach": "HEAD", "refs": [], "sha": "abc1234"},
            overlay=ovy)
        by_o = {i["deliverable"].split(os.sep)[-1]: i for i in tri_o}
        u = by_o.get("unsigned-20261102-out.json") or {}
        w = by_o.get("weak-20261102-out.json") or {}
        u_cands = {c["name"]: c for c in u.get("candidates") or []}
        w_cands = {c["name"]: c for c in w.get("candidates") or []}
        want("S32 收据缺 node/id + overlay STRONG → 提级 CONFIRMED 并列出",
             u_cands.get("unsigned-20261102.json", {}).get("confirm") == "CONFIRMED"
             and u_cands.get("unsigned-20261102.json", {}).get("confirm_base") == "PARTIAL"
             and u.get("upgraded") == ["unsigned-20261102.json"]
             and (u.get("sig_src") or {}).get("node") == "overlay"
             and (u.get("sig_src") or {}).get("id") == "overlay")
        want("S33 overlay 弱派生（WEAK）不入判 → 维持 PARTIAL 不提级",
             w_cands.get("weak-20261102.json", {}).get("confirm") == "PARTIAL"
             and not w.get("actionable") and not w.get("upgraded")
             and (w.get("sig_src") or {}).get("node") == "")
        tri_n = triage_missing(
            tpay, td,
            hist_fn=lambda r, p: "abc1234 add" if p.endswith("old.json") else "",
            reach_fn=lambda r, h: {"reach": "HEAD", "refs": [], "sha": "abc1234"})
        n0 = {i["deliverable"].split(os.sep)[-1]: i for i in tri_n}
        n_cands = {c["name"]: c for c in
                   (n0.get("unsigned-20261102-out.json") or {}).get("candidates") or []}
        want("S34 无 overlay 时逐字维持现状（不得凭空提级）",
             n_cands.get("unsigned-20261102.json", {}).get("confirm") == "PARTIAL"
             and not (n0.get("unsigned-20261102-out.json") or {}).get("upgraded"))
        mixed, msrc = _expected_for(
            {"node": "bm-a", "id": "", "receipt": "unsigned-20261102.json"}, ovy)
        want("S35 收据只写了 node → 只补 id（逐字段标源，不整条改判）",
             mixed.get("node") == "bm-a" and mixed.get("id") == "G-OVERLAY"
             and msrc.get("node") == "receipt" and msrc.get("id") == "overlay")

        ostate = os.path.join(tmp, "state")
        os.makedirs(ostate, exist_ok=True)
        _write(os.path.join(ostate, "receipt-sig-overlay-20261101.json"),
               {"entries": [{"receipt": "old-a.json"}]})
        _write(os.path.join(ostate, "receipt-sig-overlay-20261107.json"),
               {"entries": [{"receipt": "a.json", "confidence": "STRONG"},
                            {"receipt": "b.json", "confidence": "WEAK"}]})
        idx, meta = load_sig_overlay(tmp)
        want("S36 overlay 自动取最新一件并按 STRONG/WEAK 分计",
             meta["entries"] == 2 and meta["strong"] == 1 and meta["weak"] == 1
             and meta["path"].endswith("receipt-sig-overlay-20261107.json")
             and set(idx) == {"a.json", "b.json"})
        idx2, meta2 = load_sig_overlay(
            tmp, path=os.path.join(tmp, "absent", "receipt-sig-overlay-x.json"))
        want("S37 overlay 显式路径读不到 → 空表且如实报 missing，不抛栈",
             idx2 == {} and meta2["entries"] == 0 and bool(meta2["missing"]))
        idx3, meta3 = load_sig_overlay(tmp)
        want("S38 未给路径时 root/state 优先命中（本司仓只作兜底）",
             meta3["path"].startswith(ostate) and len(idx3) == 2)
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
    ap.add_argument("--triage-missing", action="store_true")
    ap.add_argument("--sig-overlay", default=None,
                    help="回执签名 overlay 路径；缺省取 state/receipt-sig-overlay-*.json 最新一件")
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
    if args.triage_missing:
        overlay, overlay_meta = load_sig_overlay(args.root, args.sig_overlay)
        payload["sig_overlay"] = overlay_meta
        payload["missing_triage"] = triage_missing(payload, args.root,
                                                  overlay=overlay)
        report_triage(payload["missing_triage"], overlay_meta)

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