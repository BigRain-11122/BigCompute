#!/usr/bin/env python3
"""Batch token estimator (tech T4 / BC-P-01 work-source face).

Estimates token volume for a directory of text files BEFORE accepting
batch inference jobs from group companies (BC-P-01): per-file tokens +
bytes + totals, in the same estimation caliber as
``Tools/cost_ledger.py count`` (openai/tiktoken BPE, offline cache at
state/tiktoken-cache; exact Qwen counting stays with Ollama eval_count).
Totals feed caliber-B pricing rails (1M-token quota face, R-20260928
Q5). Read-only over inputs; writes only the optional --json output.

Usage:
  python Tools/batch_token_estimate.py --dir docs/legal
  python Tools/batch_token_estimate.py --dir docs --ext .md,.txt --json out.json
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TIKTOKEN_CACHE = os.path.join(HERE, "..", "state", "tiktoken-cache")
NOTE = ("estimation caliber: openai BPE != qwen vocab; "
        "exact qwen counting = ollama eval_count")
MAX_FILES_DEFAULT = 500


def main():
    ap = argparse.ArgumentParser(description="batch token estimator")
    ap.add_argument("--dir", required=True, help="directory to scan recursively")
    ap.add_argument("--ext", default=".md,.txt", help="comma-separated extensions")
    ap.add_argument("--encoding", default="cl100k_base")
    ap.add_argument("--json", help="optional path to dump the JSON result")
    ap.add_argument("--max-files", type=int, default=MAX_FILES_DEFAULT)
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        print(json.dumps({"status": "error_no_dir", "dir": args.dir}))
        return 2

    os.makedirs(TIKTOKEN_CACHE, exist_ok=True)
    os.environ["TIKTOKEN_CACHE_DIR"] = TIKTOKEN_CACHE
    try:
        import tiktoken  # adopted dep (MIT, OH-20260927-bigcompute)
        enc = tiktoken.get_encoding(args.encoding)
    except ImportError:
        print(json.dumps({"status": "error_unavailable",
                          "note": "tiktoken not installed: pip install tiktoken"}))
        return 2

    exts = [e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower()
            for e in args.ext.split(",") if e.strip()]
    rows, skipped = [], 0
    for root, _dirs, files in os.walk(args.dir):
        for name in sorted(files):
            if not any(name.lower().endswith(e) for e in exts):
                continue
            if len(rows) >= args.max_files:
                skipped += 1
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                rows.append({"file": os.path.relpath(path, args.dir),
                             "bytes": os.path.getsize(path),
                             "tokens": len(enc.encode(text))})
            except OSError:
                skipped += 1

    total_tokens = sum(r["tokens"] for r in rows)
    total_bytes = sum(r["bytes"] for r in rows)
    out = {
        "status": "ok",
        "dir": args.dir,
        "encoding": args.encoding,
        "counter": "openai/tiktoken",
        "files": len(rows),
        "skipped": skipped,
        "total_bytes": total_bytes,
        "total_tokens": total_tokens,
        "total_mtok": round(total_tokens / 1000000.0, 6),
        "note": NOTE,
        "cache_dir": "state/tiktoken-cache",
        "rows": rows,
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=True, indent=2)

    print("file".ljust(46), "bytes".rjust(9), "tokens".rjust(10))
    for r in rows:
        print(r["file"][:45].ljust(46), str(r["bytes"]).rjust(9), str(r["tokens"]).rjust(10))
    print("-" * 66)
    print("TOTAL files=%d tokens=%d (%.6f Mtok) skipped=%d"
          % (out["files"], total_tokens, out["total_mtok"], skipped))
    print("note: " + NOTE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
