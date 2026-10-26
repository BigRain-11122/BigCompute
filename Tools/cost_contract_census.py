"""Cost-rail field contract census (T137).

Why: Tools/cost_ledger.py writes five rails (cost / quota / cloud / borrow /
l1) with 30+ fields across eight write commands, and Tools/month_end_collect.py
reads the summary faces of those rails. Until now nothing mechanically linked
the two sides: a summary could stop emitting a field that month_end_collect
still reads (it would silently fall back to 0 via dict.get), or a writer could
keep writing a field that no consumer ever sees. This tool builds that link:

  layer 1 writer  -> raw row keys emitted by each write command (runtime probe,
                     temp dirs only, zero touch of live ledgers)
  layer 2 reader  -> row keys actually touched by the summary aggregators
                     (traced dict subclass around cost_ledger._load_rows)
  layer 3 contract-> summary-face keys read by month_end_collect parse_*
                     (AST extraction of .get("k") on d/t containers)
  layer 4 gap     -> reader keys missing from the live summary face = silent
                     default risk; writer keys nobody reads = registered as
                     internal-only (printed, not failed)

Commands:
  python Tools/cost_contract_census.py census      # verdict + JSON face
  python Tools/cost_contract_census.py selftest    # fixtures
"""
import argparse
import ast
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import cost_ledger as cl  # noqa: E402

MONTH_END = os.path.join(HERE, "month_end_collect.py")


def _cost_faces(args, ledger=None, budget=None):
    """Cost rail has no month_end summary consumer; its readers are the
    operator faces `summary` and `fund_status` (M7 wiring predesign)."""
    out = cl.summary(args, path=ledger or cl.LEDGER)
    fund = cl.fund_status(args, path=ledger or cl.LEDGER)
    return {"totals": out, "fund": fund}


def _ns(**kw):
    ns = argparse.Namespace()
    ns.month = kw.pop("month", None)
    ns.consumer = kw.pop("consumer", None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _cost_ns():
    return _ns(order_id="C-1", date="2026-10-26", sku="resident-card",
               tier="L2", cost_item="inference", price=29.9, fee_rate=0.006,
               refund_rate=0.0, amount=3.2, tokens_local=1200, tokens_api=0,
               api_reason="-", fund_accrual=10.0)


def _quota_issue_ns():
    return _ns(tx_id="Q-1", date="2026-10-26", consumer="bigdomain",
               amount_mtok=1.5, unit_cost_b_cny=2.0, reason="grant",
               ref="R-1", ceo_approved=False)


def _quota_consume_ns():
    return _ns(tx_id="Q-2", date="2026-10-26", consumer="bigdomain",
               amount_mtok=0.5, unit_cost_b_cny=2.0, reason="use",
               ref="R-2", tokens_local=500, tokens_api=0, api_reason="-")


def _cloud_ns():
    return _ns(tx_id="CL-1", date="2026-10-26", consumer="bigstream",
               lane="h3-context-ir", task_ref="MV-0001", amount_mtok=0.8,
               reason="i2v", ceo_approved=True, tokens_local=0,
               tokens_api=800, api_reason="cloud i2v batch")


def _borrow_ns():
    return _ns(tx_id="WO-1", date="2026-10-26", lender_dept="biglife",
               borrower_dept="bigcompute", machine_id="bm-a",
               work_type="batch_inference", tokens_mtok=1.5,
               quota_debit_mtok=1.5, timeout_rule="24h",
               return_rule="no-residual", verdict_source="idlewatch-log-L1",
               reason="borrow", ceo_approved=False)


def _l1_ns():
    return _ns(tx_id="L1-1", date="2026-10-26", consumer="bigdomain",
               model="qwen3:4b", endpoint="http://127.0.0.1:11434/v1",
               prompt_tokens=800, completion_tokens=1200, reason="serve",
               ceo_approved=False)


# rail -> ordered (command name, callable, namespace builder, ledger filename)
PROBES = {
    "cost": [("add_row", cl.add_row, _cost_ns, "cost-ledger.jsonl")],
    "quota": [("quota_issue", cl.quota_issue, _quota_issue_ns,
               "quota-ledger.jsonl"),
              ("quota_consume", cl.quota_consume, _quota_consume_ns,
               "quota-ledger.jsonl")],
    "cloud": [("cloud_entry", cl.cloud_entry, _cloud_ns,
               "cloud-cost-ledger.jsonl")],
    "borrow": [("borrow_entry", cl.borrow_entry, _borrow_ns,
                "borrow-cost-ledger.jsonl")],
    "l1": [("l1_record", cl.l1_record, _l1_ns, "cost-l1-ledger.jsonl")],
}

SUMMARYS = {
    "cost": (lambda args, **kw: _cost_faces(args, **kw),
             lambda ns: _ns(month=None, fund_target=3500.0)),
    "quota": (lambda args, **kw: cl.quota_summary(args, **kw),
              lambda ns: _ns(month=None, consumer=None)),
    "cloud": (cl.cloud_summary, lambda ns: _ns(month=None)),
    "borrow": (cl.borrow_summary, lambda ns: _ns(month=None)),
    "l1": (cl.l1_summary, lambda ns: _ns(month=None)),
}


class _TraceDict(dict):
    """dict that records every key the caller actually touches."""

    def __init__(self, data, sink):
        super().__init__(data)
        self._sink = sink

    def get(self, key, default=None):
        self._sink.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self._sink.add(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self._sink.add(key)
        return super().__contains__(key)


def _call(fn, ns, ledger, budget):
    """Route to the real parameter name: the cost rail uses path=, the four
    metering rails use ledger=/budget= (per-rail J-gate budget file)."""
    import inspect
    params = inspect.signature(fn).parameters
    kwargs = {}
    kwargs["ledger" if "ledger" in params else "path"] = ledger
    if "budget" in params:
        kwargs["budget"] = budget
    return fn(ns, **kwargs)


def probe_writers():
    """Return {rail: {command: sorted keys}} from isolated temp ledgers."""
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for rail, probes in PROBES.items():
            per_cmd = {}
            for cmd, fn, build_ns, fname in probes:
                path = os.path.join(tmp, fname)
                # borrow/l1 settle paths are not probes: entry is the only
                # key-creating call; settles flip receipt_status in place.
                _call(fn, build_ns(), path, os.path.join(tmp, "b.json"))
                # second call exercises the idempotency gate (reads tx_id/order_id)
                _call(fn, build_ns(), path, os.path.join(tmp, "b.json"))
                rows = cl._load_rows(path)
                keys = set()
                for r in rows:
                    keys.update(r.keys())
                per_cmd[cmd] = sorted(keys)
            out[rail] = per_cmd
    return out


def trace_reader_rows(writers):
    """Row keys actually read by each rail's summary face."""
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for rail, probes in PROBES.items():
            fname = probes[0][3]
            path = os.path.join(tmp, fname)
            seen = set()
            orig = cl._load_rows

            def spliced(p, _seen=seen, _orig=orig):
                return [_TraceDict(r, _seen) for r in _orig(p)]

            cl._load_rows = spliced
            try:
                for cmd, fn, build_ns, _f in probes:
                    _call(fn, build_ns(), path, os.path.join(tmp, "b.json"))
                # second call exercises the idempotency gate (reads tx_id/order_id)
                _call(fn, build_ns(), path, os.path.join(tmp, "b.json"))
                summary_fn, build_sum = SUMMARYS[rail]
                summary_fn(build_sum(None), ledger=path,
                           budget=os.path.join(tmp, "b.json"))
            finally:
                cl._load_rows = orig
            out[rail] = sorted(seen)
    return out


def reader_keys_from_month_end(path=MONTH_END):
    """{function: {"top": [...], "totals": [...]}} via AST on parse_* faces."""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    wanted = {"parse_quota_summary": "quota",
              "parse_cloud_summary": "cloud",
              "parse_l1_summary": "l1"}
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in wanted:
            continue
        top, totals = set(), set()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "get"):
                continue
            if not sub.args or not isinstance(sub.args[0], ast.Constant):
                continue
            key = sub.args[0].value
            if not isinstance(key, str):
                continue
            base = fn.value
            if not isinstance(base, ast.Name):
                continue
            if base.id == "d":
                top.add(key)
            elif base.id == "t":
                totals.add(key)
        out[wanted[node.name]] = {"top": sorted(top), "totals": sorted(totals)}
    return out


def summary_faces():
    """{rail: flatten(real summary output)} from the probe ledgers."""
    faces = {}
    with tempfile.TemporaryDirectory() as tmp:
        for rail, probes in PROBES.items():
            path = os.path.join(tmp, probes[0][3])
            for cmd, fn, build_ns, _f in probes:
                _call(fn, build_ns(), path, os.path.join(tmp, "b.json"))
                # second call exercises the idempotency gate (reads tx_id/order_id)
                _call(fn, build_ns(), path, os.path.join(tmp, "b.json"))
            summary_fn, build_sum = SUMMARYS[rail]
            face = summary_fn(build_sum(None), ledger=path,
                              budget=os.path.join(tmp, "b.json"))
            flat = {"top": sorted(face.keys()),
                    "totals": sorted(face.get("totals", {}).keys())}
            faces[rail] = flat
    return faces


def census():
    writers = probe_writers()
    read_rows = trace_reader_rows(writers)
    ast_keys = reader_keys_from_month_end()
    faces = summary_faces()

    gaps = []          # reader key absent from producer face => silent 0
    for rail, spec in ast_keys.items():
        face = faces.get(rail, {"top": [], "totals": []})
        for scope in ("top", "totals"):
            missing = sorted(set(spec[scope]) - set(face[scope]))
            if missing:
                gaps.append({"rail": rail, "scope": scope,
                             "missing_keys": missing})

    unwritten = []     # writer keys no summary path reads (occlusion)
    for rail, per_cmd in writers.items():
        written = set()
        for keys in per_cmd.values():
            written.update(keys)
        unseen = sorted(written - set(read_rows.get(rail, [])))
        unwritten.append({"rail": rail, "unread_keys": unseen,
                          "written": len(written),
                          "read": len(read_rows.get(rail, []))})
    unwritten.sort(key=lambda x: (-len(x["unread_keys"]), x["rail"]))

    verdict = "COST-CONTRACT-GREEN" if not gaps else "COST-CONTRACT-GAP"
    return {
        "verdict": verdict,
        "rails": sorted(writers.keys()),
        "writer_keys": writers,
        "reader_row_keys": read_rows,
        "month_end_reader_keys": ast_keys,
        "summary_faces": faces,
        "gaps": gaps,
        "occlusion": unwritten,
    }


def selftest():
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    writers = probe_writers()
    check("F1 all five rails probed",
          sorted(writers.keys()) == ["borrow", "cloud", "cost", "l1", "quota"],
          str(sorted(writers.keys())))
    check("F2 cost rail writer key count = 15",
          len(writers["cost"]["add_row"]) == 15,
          str(len(writers["cost"]["add_row"])))
    check("F3 quota issue/consume share one row contract",
          writers["quota"]["quota_issue"] == writers["quota"]["quota_consume"])
    check("F4 borrow writer has J2 null fields declared",
          {"cost_b_cny", "receipt_status"} <= set(
              writers["borrow"]["borrow_entry"]))

    read_rows = trace_reader_rows(writers)
    check("F5 quota summary reads tx_id (trace works)",
          "tx_id" in read_rows["quota"], str(read_rows["quota"][:5]))
    check("F6 l1 summary reads amount_mtok",
          "amount_mtok" in read_rows["l1"])
    check("F6b l1 raw receipt tokens are occlusion (unread by summary)",
          "prompt_tokens" not in read_rows["l1"])

    ast_keys = reader_keys_from_month_end()
    check("F7 month_end reader extraction covers 3 rails",
          sorted(ast_keys.keys()) == ["cloud", "l1", "quota"],
          str(sorted(ast_keys.keys())))
    check("F8 quota totals reader keys >= 4",
          len(ast_keys["quota"]["totals"]) >= 4,
          str(ast_keys["quota"]["totals"]))

    faces = summary_faces()
    check("F9 live summary faces expose totals",
          all(set(f["totals"]) for f in faces.values()))
    check("F10 cloud totals carry cost_billed_b_cny",
          "cost_billed_b_cny" in faces["cloud"]["totals"])

    # negative fixture: a reader key that the face does not have must be
    # reported as a gap (detector capability self-proof, not just green).
    fake = {"quota": {"top": ["rails"], "totals": ["issued_mtok", "ghost_key"]}}
    gaps = []
    for rail, spec in fake.items():
        for scope in ("top", "totals"):
            missing = sorted(set(spec[scope]) - set(faces[rail][scope]))
            if missing:
                gaps.append(missing)
    check("F11 negative fixture DETECT-OK",
          gaps == [["ghost_key"]], str(gaps))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(("PASS " if ok else "FAIL ") + name + (" :: " + detail if detail else ""))
    print("SELFTEST %d/%d %s" % (len(checks) - len(failed), len(checks),
                                 "PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


def main():
    ap = argparse.ArgumentParser(description="cost rail field contract census")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("census")
    sub.add_parser("selftest")
    args = ap.parse_args()
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "census":
        print(json.dumps(census(), ensure_ascii=True, indent=2, sort_keys=True))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())