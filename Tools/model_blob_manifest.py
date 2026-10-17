#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型分发 blob 清单工程件（议程三轮循环族 D 增量·E27·L2 直拷预演面·零分发动作）

model-manifest-predesign-v1.md §四「家族层共享 dedup 发现」的工程量化承接：
从 ollama manifests JSON 直采逐层 blob 清单（单一事实源零重算）→ 跨模型 dedup
实测 → 唯一 blob 集拷贝单（tech T9/W4 分发日即用·SHA256 双通道 J2 的清单源）。

Commands:
  list       逐模型逐层 blob 清单（digest/size/media_type/盘上实存核验）
  dedup      跨模型并集分析：逐模型拷贝量合计 vs 唯一集拷贝量+共享 blob 明细
  verify     J2 完整性门：全量 SHA256 重哈希 vs manifest digest（--quick=仅 size 面）
             → 基线 JSON 落盘（分发前完整性基线·W4/T9 分发日即用）
  preflight  BC-P-70 点火前关键权重一命令预检：--path P [--expect-bytes N]
             [--expect-sha256 HEX] [--magic gguf|safetensors]——缺档/尺寸不符/
             sha 不匹配/magic 不符=PREFLIGHT_FAIL exit 1（零点火·R-53 INT8 DiT
             截断 17.01GB 烧废首单判例防再发·烧窗/借用窗点火/机队 CDN 拉取线用）
  selftest   合成夹具断言（并集/节约数学+digest→路径映射+缺件检出+verify 四态
             +preflight 四门）

零分发动作（DRY-RUN 同律）；blobs 目录只读；无网络。
"""
import hashlib
import json
import os
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def models_root():
    return os.environ.get("OLLAMA_MODELS") or os.path.join(
        os.path.expanduser("~"), ".ollama", "models"
    )


def blob_path(root, digest):
    return os.path.join(root, "blobs", "sha256-" + digest.split(":", 1)[-1])


def short(digest):
    hex_ = digest.split(":", 1)[-1]
    return hex_[:8] + "…" + hex_[-4:]


def load_manifests(root):
    """manifests 目录实读→[{model_id, display_name, entries:[(media,digest,decl_size)]}]"""
    mdir = os.path.join(root, "manifests")
    out = []
    for dirpath, _dirs, files in os.walk(mdir):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, mdir).replace("\\", "/")
            parts = rel.split("/")
            if len(parts) < 3:
                continue
            display = parts[-2] + ":" + parts[-1]
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = []
            cfg = data.get("config", {})
            if cfg.get("digest"):
                entries.append(("config", cfg["digest"], int(cfg.get("size", 0))))
            for layer in data.get("layers", []):
                if layer.get("digest"):
                    entries.append(
                        (
                            layer.get("mediaType", "?").rsplit(".", 1)[-1],
                            layer["digest"],
                            int(layer.get("size", 0)),
                        )
                    )
            out.append({"model_id": rel, "display_name": display, "entries": entries})
    return out


def disk_size(root, digest):
    """盘上实存优先（declared size 仅缺件时回退）→ (bytes_or_None, exists)"""
    p = blob_path(root, digest)
    if os.path.exists(p):
        return os.path.getsize(p), True
    return None, False


def cmd_list(root):
    for m in sorted(load_manifests(root), key=lambda x: x["display_name"]):
        total = 0
        missing = 0
        print(f"model {m['display_name']}  ({m['model_id']})")
        for media, digest, declared in m["entries"]:
            size, exists = disk_size(root, digest)
            if exists:
                total += size
            else:
                missing += 1
            sz = f"{size}B" if exists else f"MISSING(declared {declared}B)"
            print(f"  [{media:<8}] sha256:{short(digest)}  {sz}")
        print(f"  total_on_disk={total}B  blobs={len(m['entries'])}  missing={missing}")
    return 0


def cmd_dedup(root):
    manifests = load_manifests(root)
    blob_map = {}  # digest -> {"media": set, "models": set, "bytes": int|None}
    per_model = {}
    for m in manifests:
        mt = 0
        for media, digest, _declared in m["entries"]:
            size, exists = disk_size(root, digest)
            b = blob_map.setdefault(digest, {"media": set(), "models": set(), "bytes": None})
            b["media"].add(media)
            b["models"].add(m["display_name"])
            if exists:
                b["bytes"] = size
                mt += size
        per_model[m["display_name"]] = mt
    naive = sum(per_model.values())
    unique = sum(b["bytes"] or 0 for b in blob_map.values() if b["bytes"] is not None)
    shared = {d: b for d, b in blob_map.items() if len(b["models"]) > 1}
    shared_savings = naive - unique
    print(f"models={len(manifests)}  unique_blobs={len(blob_map)}")
    print(f"per_model_copy_sum={naive}B  unique_copy_set={unique}B")
    pct = (shared_savings / naive * 100) if naive else 0.0
    print(f"dedup_savings={shared_savings}B ({pct:.4f}%)")
    print(f"shared_blobs={len(shared)}")
    for d, b in sorted(shared.items(), key=lambda kv: -(kv[1]["bytes"] or 0)):
        print(
            f"  sha256:{short(d)}  {b['bytes']}B  media={'+'.join(sorted(b['media']))}"
            f"  models={','.join(sorted(b['models']))}"
        )
    for name, t in sorted(per_model.items()):
        print(f"  per-model {name}: {t}B")
    return 0


def sha256_file(path):
    """流式 SHA256（8MiB 块·全量基线用）"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_verify(root, quick=False, out=None):
    """J2 完整性门：逐唯一 blob 重核 vs manifest digest → 基线 JSON（只读零分发）

    full=SHA256 重哈希（文件名内证法·E22 手工演示的工具化）；
    quick=声明 size vs 实际 size 面（秒级·哈希前预检）。
    """
    manifests = load_manifests(root)
    blobs = {}  # digest -> declared size（同 blob 同内容同 size·首见即真）
    for m in manifests:
        for _media, digest, declared in m["entries"]:
            blobs.setdefault(digest, declared)
    results = []
    ok = missing = bad = 0
    total = 0
    t0 = time.time()
    for digest in sorted(blobs):
        hex_ = digest.split(":", 1)[-1]
        declared = blobs[digest]
        p = blob_path(root, digest)
        if not os.path.exists(p):
            missing += 1
            results.append({"digest": "sha256:" + hex_, "status": "MISSING", "declared_b": declared})
            continue
        size = os.path.getsize(p)
        total += size
        if quick:
            if declared and size != declared:
                bad += 1
                results.append({"digest": "sha256:" + hex_, "status": "SIZE_MISMATCH", "declared_b": declared, "actual_b": size})
            else:
                ok += 1
                results.append({"digest": "sha256:" + hex_, "status": "OK_QUICK", "bytes": size})
        else:
            actual = sha256_file(p)
            if actual == hex_:
                ok += 1
                results.append({"digest": "sha256:" + hex_, "status": "OK", "bytes": size})
            else:
                bad += 1
                results.append({"digest": "sha256:" + hex_, "status": "HASH_MISMATCH", "actual_sha256": actual, "bytes": size})
    elapsed = time.time() - t0
    mode = "quick_size" if quick else "full_sha256"
    verdict = "VERIFY_GREEN" if (missing == 0 and bad == 0) else "VERIFY_FAIL"
    thru = (total / 1048576 / elapsed) if elapsed else 0.0
    print(f"mode={mode}  models={len(manifests)}  blobs={len(blobs)}  ok={ok}  missing={missing}  mismatch={bad}")
    print(f"verified_bytes={total}B  elapsed={elapsed:.1f}s  throughput={thru:.1f}MiB/s")
    print(f"verdict={verdict}")
    payload = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "machine": "bm-a",
        "mode": mode,
        "models": len(manifests),
        "blobs_total": len(blobs),
        "ok": ok,
        "missing": missing,
        "mismatch": bad,
        "bytes_verified": total,
        "elapsed_s": round(elapsed, 2),
        "throughput_mib_s": round(thru, 1),
        "verdict": verdict,
        "blobs": results,
    }
    if out is None:
        os.makedirs("state", exist_ok=True)
        out = os.path.join("state", "manifest-verify-" + time.strftime("%Y%m%d-%H%M") + ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"json={out}")
    return 0 if verdict == "VERIFY_GREEN" else 1


def _magic_check(path, magic):
    """文件头 magic 只读检查：gguf=首 4B 'GGUF'；safetensors=头 8B 小端头长合理域

    返回 (ok_or_None, detail)：None=未知 magic 名（usage error 态）。"""
    try:
        if magic == "gguf":
            with open(path, "rb") as f:
                head = f.read(4)
            return head == b"GGUF", head.hex()
        if magic == "safetensors":
            with open(path, "rb") as f:
                raw = f.read(8)
            if len(raw) != 8:
                return False, "short-header"
            hsize = int.from_bytes(raw, "little")
            ok = 2 <= hsize <= 100_000_000  # JSON 头部长度合理域（截断/错件检出）
            return ok, "header_len=%d" % hsize
        return None, "unknown-type"
    except OSError as e:
        return False, "io-error:%s" % e


def cmd_preflight(path, expect_bytes=None, expect_sha256=None, magic=None, out=None):
    """BC-P-70 点火前关键权重预检（R-53 INT8 DiT 截断烧废首单判例防再发）

    判据四门：缺档/尺寸不符/sha 不匹配/magic 不符 → PREFLIGHT_FAIL exit 1（零点火）；
    无期望参数时=exists-only 浅检（如实标注零深检·禁冒充完整门）。
    消费模式=orders 行「大小校验字节数」+源件 sha（跨机 CDN 拉取线同律）。
    """
    t0 = time.time()
    checks = []

    def mark(name, status, **kw):
        checks.append(dict(check=name, status=status, **kw))

    fail = False
    if not os.path.exists(path):
        mark("exists", "MISSING")
        fail = True
    else:
        size = os.path.getsize(path)
        mark("exists", "OK", actual_b=size)
        if expect_bytes is not None:
            if size != expect_bytes:
                mark("size", "SIZE_MISMATCH", expect_b=expect_bytes, actual_b=size)
                fail = True
            else:
                mark("size", "OK", expect_b=expect_bytes)
        if magic:
            head_ok, why = _magic_check(path, magic)
            if head_ok is None:
                print("ERROR: unknown --magic type (gguf|safetensors)")
                return 2
            mark("magic", "OK" if head_ok else "MAGIC_MISMATCH", type=magic, detail=why)
            fail = fail or (not head_ok)
        if expect_sha256:
            want = expect_sha256.lower().split(":")[-1]
            actual = sha256_file(path)
            if actual == want:
                mark("sha256", "OK", expect=want)
            else:
                mark("sha256", "HASH_MISMATCH", expect=want, actual=actual)
                fail = True
    elapsed = time.time() - t0
    verdict = "PREFLIGHT_GREEN" if not fail else "PREFLIGHT_FAIL"
    print("preflight path=%s" % path)
    for c in checks:
        print("  [%s] %s" % (c["check"], c["status"]))
    print("verdict=%s  elapsed=%.2fs" % (verdict, elapsed))
    payload = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "machine": "bm-a",
        "tool": "model_blob_manifest.py preflight",
        "path": path,
        "expect_bytes": expect_bytes,
        "expect_sha256": expect_sha256,
        "magic": magic,
        "verdict": verdict,
        "checks": checks,
        "elapsed_s": round(elapsed, 2),
    }
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print("json=%s" % out)
    return 0 if not fail else 1


def _selftest():
    import tempfile

    # 1) 并集/节约数学（合成：A 与 B 共享 S·各独占 a/b）
    A = {"display_name": "A", "entries": [("system", "sha256:S", 10), ("model", "sha256:a", 100)]}
    B = {"display_name": "B", "entries": [("system", "sha256:S", 10), ("model", "sha256:b", 200)]}
    blob_map = {}
    per_model = {}
    for m in (A, B):
        mt = 0
        for _media, digest, declared in m["entries"]:
            b = blob_map.setdefault(digest, {"media": set(), "models": set(), "bytes": declared})
            b["models"].add(m["display_name"])
            mt += declared
        per_model[m["display_name"]] = mt
    naive = sum(per_model.values())
    unique = sum(b["bytes"] for b in blob_map.values())
    assert naive == 320 and unique == 310, (naive, unique)
    assert naive - unique == 10, "shared blob 只应省一份"
    shared = {d for d, b in blob_map.items() if len(b["models"]) > 1}
    assert shared == {"sha256:S"}, shared

    # 2) digest→路径映射
    assert blob_path("R", "sha256:abc123").replace("\\", "/") == "R/blobs/sha256-abc123"

    # 3) 盘上实存/缺件检出（临时目录）
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "blobs"))
        with open(blob_path(td, "sha256:ff"), "wb") as f:
            f.write(b"x" * 5)
        s, ok = disk_size(td, "sha256:ff")
        assert ok and s == 5, (s, ok)
        _s2, ok2 = disk_size(td, "sha256:ee")
        assert not ok2, "缺件必须检出 False"

    # 4) verify 端到端四态（真实命令面·临时 root·零真实 state 污染）
    import tempfile as _tf

    with _tf.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "blobs"))
        good_hex = hashlib.sha256(b"good-content").hexdigest()
        with open(os.path.join(td, "blobs", "sha256-" + good_hex), "wb") as f:
            f.write(b"good-content")  # 12B·文件名哈希=内容哈希 → OK
        corrupt_hex = hashlib.sha256(b"planned").hexdigest()
        with open(os.path.join(td, "blobs", "sha256-" + corrupt_hex), "wb") as f:
            f.write(b"tampered!!")  # 9B·文件名=计划哈希·内容=篡改 → HASH_MISMATCH
        gone_hex = hashlib.sha256(b"gone").hexdigest()  # 不落盘 → MISSING
        mdir = os.path.join(td, "manifests", "qwen", "fake")
        os.makedirs(mdir)
        # good 声明 size=999 ≠ 实际 12 → quick 面 SIZE_MISMATCH；corrupt 声明 10=实际 10
        with open(os.path.join(mdir, "latest"), "w", encoding="utf-8") as f:
            json.dump({"config": {"digest": "sha256:" + good_hex, "size": 999},
                       "layers": [
                           {"digest": "sha256:" + corrupt_hex, "size": 10,
                            "mediaType": "application/vnd.ollama.image.model"},
                           {"digest": "sha256:" + gone_hex, "size": 4, "mediaType": "x.y"},
                       ]}, f)
        rep = os.path.join(td, "rep.json")
        rc = cmd_verify(td, quick=False, out=rep)
        assert rc == 1, "缺件+哈希坏件必须 exit 1"
        with open(rep, encoding="utf-8") as f:
            data = json.load(f)
        st = {b["digest"]: b["status"] for b in data["blobs"]}
        assert st["sha256:" + good_hex] == "OK", st
        assert st["sha256:" + corrupt_hex] == "HASH_MISMATCH", st
        assert st["sha256:" + gone_hex] == "MISSING", st
        rc2 = cmd_verify(td, quick=True, out=rep)
        assert rc2 == 1, "size 不符+缺件必须 exit 1"
        with open(rep, encoding="utf-8") as f:
            data2 = json.load(f)
        st2 = {b["digest"]: b["status"] for b in data2["blobs"]}
        assert st2["sha256:" + good_hex] == "SIZE_MISMATCH", st2
        assert st2["sha256:" + corrupt_hex] == "OK_QUICK", "quick 面只核 size——哈希坏件 size 同则不可见（如实分立）"
        assert st2["sha256:" + gone_hex] == "MISSING", st2

    # 5) preflight 四门端到端（BC-P-70·R-53 截断判例防再发）
    with tempfile.TemporaryDirectory() as td:
        pf = os.path.join(td, "weights.gguf")
        blob = b"GGUF" + b"\x00" * 20  # 24B
        with open(pf, "wb") as f:
            f.write(blob)
        real_sha = hashlib.sha256(blob).hexdigest()
        rep = os.path.join(td, "pf.json")
        # 全门过（size+sha+magic）
        assert cmd_preflight(pf, expect_bytes=24, expect_sha256=real_sha, magic="gguf", out=rep) == 0
        with open(rep, encoding="utf-8") as f:
            assert json.load(f)["verdict"] == "PREFLIGHT_GREEN"
        # 截断模拟：expected 大于 actual → SIZE_MISMATCH FAIL（R-53 病理面）
        assert cmd_preflight(pf, expect_bytes=25, out=rep) == 1
        with open(rep, encoding="utf-8") as f:
            d = json.load(f)
        assert d["verdict"] == "PREFLIGHT_FAIL"
        assert any(c["check"] == "size" and c["status"] == "SIZE_MISMATCH" for c in d["checks"])
        # sha 不符
        assert cmd_preflight(pf, expect_sha256="0" * 64, out=rep) == 1
        # 缺档
        assert cmd_preflight(os.path.join(td, "gone.gguf"), out=rep) == 1
        # magic 负例（非 GGUF 内容冒 GGUF 名）
        pf2 = os.path.join(td, "fake.gguf")
        with open(pf2, "wb") as f:
            f.write(b"NOTG" + b"\x00" * 20)
        assert cmd_preflight(pf2, magic="gguf", out=rep) == 1
        # safetensors 头正例+短头负例（截断面）
        st = os.path.join(td, "model.safetensors")
        with open(st, "wb") as f:
            f.write((4).to_bytes(8, "little") + b"{}{}")
        assert cmd_preflight(st, magic="safetensors", out=rep) == 0
        with open(st, "wb") as f:
            f.write(b"\xff" * 4)
        assert cmd_preflight(st, magic="safetensors", out=rep) == 1
        # exists-only 浅检过（无期望参数·如实标注零深检）
        assert cmd_preflight(pf, out=rep) == 0
        with open(rep, encoding="utf-8") as f:
            assert json.load(f)["verdict"] == "PREFLIGHT_GREEN"
        # 未知 magic 名=usage error exit 2
        assert cmd_preflight(pf, magic="zip", out=rep) == 2

    print("selftest PASS (union/savings math + digest->path mapping + missing-blob detect + verify 4-state + preflight 4-gate)")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "selftest":
        return _selftest()
    if cmd == "preflight":
        # BC-P-70：preflight 不依赖 ollama models root（任意路径权重文件）
        if "--path" not in argv:
            print("usage: preflight --path P [--expect-bytes N] [--expect-sha256 HEX] [--magic gguf|safetensors] [--out PATH]")
            return 1

        def arg(name, cast=None):
            if name not in argv:
                return None
            v = argv[argv.index(name) + 1]
            return cast(v) if cast else v

        return cmd_preflight(
            arg("--path"),
            expect_bytes=arg("--expect-bytes", int),
            expect_sha256=arg("--expect-sha256"),
            magic=arg("--magic"),
            out=arg("--out"),
        )
    root = models_root()
    if not os.path.isdir(root):
        print(f"ERROR: models root not found: {root}")
        return 1
    if cmd == "list":
        return cmd_list(root)
    if cmd == "dedup":
        return cmd_dedup(root)
    if cmd == "verify":
        quick = "--quick" in argv
        out = argv[argv.index("--out") + 1] if "--out" in argv else None
        return cmd_verify(root, quick=quick, out=out)
    print("usage: model_blob_manifest.py list|dedup|verify [--quick] [--out PATH]|preflight --path P [--expect-bytes N] [--expect-sha256 HEX] [--magic gguf|safetensors] [--out PATH]|selftest")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
