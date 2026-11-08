#!/usr/bin/env python3
"""M150 - cost daily reconciliation (caliber B single source, read-only).

Why: the month-end collector answers "what happened this month". Nobody could
answer "what does today's book look like, and which line is wrong" without
re-running three tools and reading three JSONs. This emits ONE page with three
lines (quota consumption / unit economics / anomaly rows), each carrying the
file it came from, so a wrong number can always be traced back.

Two rules that decide every judgement here:

  * An empty rail is NO-DATA, not zero. Printing 0.00 for a rail with no rows
    is how "we never sold anything" and "we never recorded it" become the same
    statement -- exactly the confusion the ledger exists to prevent.
  * A drill row sitting in a real rail is a defect, not data. The mock rails
    (mock-order, and anything whose tx/consumer/order id carries a drill or
    MOCK marker) may be *reported* but never summed into real totals.

Read-only: no rail is ever written. Exit 0 = no critical anomaly, 2 = critical.
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATHS = {
    "revenue": os.path.join(ROOT, "state", "cost-ledger.jsonl"),
    "revenue_drill": os.path.join(ROOT, "state", "mock-order-ledger.jsonl"),
    "quota": os.path.join(ROOT, "state", "quota-ledger.jsonl"),
    "l1": os.path.join(ROOT, "state", "cost-l1-ledger.jsonl"),
}
DRILL_TOKENS = ("drill", "mock", "MOCK-", "Q-DRILL")


def is_drill(row):
    for key in ("tx_id", "consumer", "order_id", "sku", "sku_id"):
        val = str(row.get(key) or "")
        if not val:
            continue
        for tok in DRILL_TOKENS:
            if tok.lower() in val.lower():
                return True
    return bool(row.get("mock")) or bool(row.get("cost_simulated")) \
        or bool(row.get("denominator_simulated"))


def load(path):
    """-> (rows, status). status in OK / ABSENT / BAD-LINES(n)."""
    if not os.path.exists(path):
        return [], "ABSENT"
    rows, bad = [], 0
    for raw in io.open(path, encoding="utf-8"):
        if not raw.strip():
            continue
        try:
            rows.append(json.loads(raw))
        except ValueError:
            bad += 1
    return rows, ("OK" if not bad else "BAD-LINES(%d)" % bad)


def money(rows, key):
    total = 0.0
    for r in rows:
        try:
            total += float(r.get(key) or 0)
        except (TypeError, ValueError):
            pass
    return round(total, 4)


def reconcile(paths=None):
    paths = dict(DEFAULT_PATHS, **(paths or {}))
    out = {"sections": [], "anomalies": []}

    # --- 1. quota consumption (real rail only; drill rows are named) --------
    qrows, qstat = load(paths["quota"])
    real_q = [r for r in qrows if not is_drill(r)]
    drill_q = [r for r in qrows if is_drill(r)]
    issued = money(real_q, "amount_mtok")
    consumed = money(real_q, "consumed_mtok")
    out["sections"].append({
        "id": "quota",
        "source": paths["quota"],
        "status": qstat,
        "rows": len(qrows),
        "real_rows": len(real_q),
        "drill_rows": len(drill_q),
        "issued_mtok": issued,
        "consumed_mtok": consumed,
        "balance_mtok": round(issued - consumed, 6),
        "verdict": "NO-DATA" if not real_q else "DATA",
    })
    if drill_q:
        # mixed = a real total could be computed next to drill rows (CRITICAL);
        # drill-only = the rail holds nothing real yet (WARN, still readable).
        mixed = bool(real_q)
        out["anomalies"].append({
            "level": "CRITICAL" if mixed else "WARN",
            "code": "DRILL-IN-REAL-QUOTA",
            "detail": "real quota rail carries %d drill row(s) alongside %d real row(s), "
                      "e.g. %r" % (len(drill_q), len(real_q),
                                   (drill_q[0].get("tx_id") or "?")[:40]),
            "source": paths["quota"]})

    # --- 2. unit economics (real revenue rail only) ------------------------
    rrows, rstat = load(paths["revenue"])
    real_r = [r for r in rrows if not is_drill(r)]
    drill_r = [r for r in rrows if is_drill(r)]
    net = money(real_r, "net")
    cost = money(real_r, "cost")
    gross = round(net - cost, 4)
    out["sections"].append({
        "id": "unit-economics",
        "source": paths["revenue"],
        "status": rstat,
        "rows": len(rrows),
        "real_rows": len(real_r),
        "drill_rows": len(drill_r),
        "net": net, "cost": cost, "gross": gross,
        "margin": (round(gross / net, 4) if net else None),
        "verdict": "NO-DATA" if not real_r else "DATA",
        "note": ("0 行 = 计划态未入账，禁把 NO-DATA 读成 0.00" if not real_r else ""),
    })
    if real_r and net == 0:
        out["anomalies"].append({
            "level": "CRITICAL", "code": "ROWS-WITHOUT-NET",
            "detail": "%d real revenue row(s) sum to net=0 -- rows exist but "
                      "carry no money, check the write path" % len(real_r),
            "source": paths["revenue"]})

    # --- 3. row-level anomaly scan across real rails ----------------------
    for name in ("revenue", "quota", "l1"):
        rows, _ = load(paths[name])
        seen = {}
        for i, r in enumerate(rows, 1):
            if is_drill(r):
                continue
            tx = str(r.get("tx_id") or r.get("order_id") or "")
            if not tx:
                out["anomalies"].append({
                    "level": "WARN", "code": "MISSING-ID",
                    "detail": "%s row %d has no tx_id/order_id" % (name, i),
                    "source": paths[name]})
            elif tx in seen:
                out["anomalies"].append({
                    "level": "CRITICAL", "code": "DUPLICATE-ID",
                    "detail": "%s tx %r appears at rows %d and %d"
                              % (name, tx[:40], seen[tx], i),
                    "source": paths[name]})
            else:
                seen[tx] = i
            for key in ("amount", "net", "cost", "amount_mtok", "consumed_mtok"):
                try:
                    if float(r.get(key)) < 0:
                        out["anomalies"].append({
                            "level": "CRITICAL", "code": "NEGATIVE-AMOUNT",
                            "detail": "%s row %d has %s<0" % (name, i, key),
                            "source": paths[name]})
                except (TypeError, ValueError):
                    pass
            if not (r.get("date") or r.get("month")):
                out["anomalies"].append({
                    "level": "WARN", "code": "MISSING-DATE",
                    "detail": "%s row %d has neither date nor month" % (name, i),
                    "source": paths[name]})

    lrows, lstat = load(paths["l1"])
    out["sections"].append({
        "id": "l1-self-use",
        "source": paths["l1"], "status": lstat, "rows": len(lrows),
        "amount_mtok": money(lrows, "amount_mtok"),
        "note": "本司自用计量，不入商业结算（口径 B：自用分母排他）",
        "verdict": "NO-DATA" if not lrows else "DATA",
    })
    dr, dstat = load(paths["revenue_drill"])
    out["sections"].append({
        "id": "drill-rail",
        "source": paths["revenue_drill"], "status": dstat, "rows": len(dr),
        "note": "演练轨：只读展示，永不计入真实合计",
        "verdict": "NO-DATA" if not dr else "DRILL-ONLY",
    })

    crit = [a for a in out["anomalies"] if a["level"] == "CRITICAL"]
    out["verdict"] = "RECONCILE-CRITICAL" if crit else "RECONCILE-OK"
    out["counts"] = {"critical": len(crit),
                     "warn": len(out["anomalies"]) - len(crit)}
    return out


def render(res):
    out = ["[cost-daily] VERDICT=%s critical=%d warn=%d"
           % (res["verdict"], res["counts"]["critical"], res["counts"]["warn"])]
    for s in res["sections"]:
        if s["id"] == "quota":
            out.append("  quota   rows=%d real=%d drill=%d issued=%.6f consumed=%.6f "
                       "balance=%.6f %s  <- %s"
                       % (s["rows"], s["real_rows"], s["drill_rows"], s["issued_mtok"],
                          s["consumed_mtok"], s["balance_mtok"], s["verdict"],
                          os.path.basename(s["source"])))
        elif s["id"] == "unit-economics":
            if s["verdict"] == "NO-DATA":
                out.append("  money   NO-DATA (rows=%d real=0) 禁读成 0.00  <- %s"
                           % (s["rows"], os.path.basename(s["source"])))
            else:
                out.append("  money   rows=%d net=%.4f cost=%.4f gross=%.4f margin=%s  <- %s"
                           % (s["real_rows"], s["net"], s["cost"], s["gross"],
                              ("%.2f%%" % (s["margin"] * 100)) if s["margin"] is not None else "n/a",
                              os.path.basename(s["source"])))
        elif s["id"] == "l1-self-use":
            out.append("  l1      rows=%d mtok=%.6f %s（自用不入结算）  <- %s"
                       % (s["rows"], s["amount_mtok"], s["verdict"],
                          os.path.basename(s["source"])))
        elif s["id"] == "drill-rail":
            out.append("  drill   rows=%d %s（只读·不入真实合计）  <- %s"
                       % (s["rows"], s["verdict"], os.path.basename(s["source"])))
    for a in res["anomalies"]:
        out.append("  %-8s %-20s %s" % (a["level"], a["code"], a["detail"]))
    return "\n".join(out)


def selftest():
    import tempfile
    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("[%s] %s %s" % ("PASS" if cond else "FAIL", name, detail)).rstrip())

    def rail(rows):
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        with io.open(fd, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return p

    def paths(**kw):
        base = {"revenue": "", "revenue_drill": "", "quota": "", "l1": ""}
        base.update(kw)
        return base

    empty = rail([])
    # S1 clean real rails
    rev = rail([{"tx_id": "T1", "date": "2026-11-08", "net": 100.0, "cost": 20.0}])
    q = rail([{"tx_id": "Q1", "date": "2026-11-08", "amount_mtok": 0.5, "consumed_mtok": 0.2}])
    r = reconcile(paths(revenue=rev, quota=q, l1=empty, revenue_drill=empty))
    check("S1 clean -> RECONCILE-OK with computed totals",
          r["verdict"] == "RECONCILE-OK"
          and r["sections"][1]["gross"] == 80.0
          and r["sections"][0]["balance_mtok"] == 0.3,
          "%s gross=%s" % (r["verdict"], r["sections"][1]["gross"]))

    # S2 empty revenue rail must be NO-DATA, never a zero
    r = reconcile(paths(revenue=empty, quota=q, l1=empty, revenue_drill=empty))
    check("S2 empty revenue -> NO-DATA (no 0.00)",
          r["sections"][1]["verdict"] == "NO-DATA"
          and r["sections"][1]["margin"] is None,
          r["sections"][1]["verdict"])

    # S3 absent rail is reported as ABSENT, still NO-DATA
    r = reconcile(paths(revenue="/nonexistent.jsonl", quota=q, l1=empty,
                        revenue_drill=empty))
    check("S3 absent rail -> ABSENT + NO-DATA",
          r["sections"][1]["status"] == "ABSENT"
          and r["sections"][1]["verdict"] == "NO-DATA")

    # S4 drill row in the real quota rail is a CRITICAL anomaly
    qd = rail([{"tx_id": "Q-DRILL-1", "consumer": "anon-drill-001", "date": "2026-11-08",
                "amount_mtok": 0.42, "consumed_mtok": 0.1},
               {"tx_id": "Q2", "date": "2026-11-08", "amount_mtok": 1.0, "consumed_mtok": 0.0}])
    r = reconcile(paths(revenue=rev, quota=qd, l1=empty, revenue_drill=empty))
    codes = [a["code"] for a in r["anomalies"]]
    check("S4 drill row in real rail -> CRITICAL, excluded from totals",
          "DRILL-IN-REAL-QUOTA" in codes
          and r["sections"][0]["issued_mtok"] == 1.0
          and r["sections"][0]["drill_rows"] == 1,
          "issued=%s codes=%s" % (r["sections"][0]["issued_mtok"], codes))

    # S5 duplicate tx id
    revd = rail([{"tx_id": "T1", "date": "2026-11-08", "net": 1.0},
                 {"tx_id": "T1", "date": "2026-11-08", "net": 1.0}])
    r = reconcile(paths(revenue=revd, quota=q, l1=empty, revenue_drill=empty))
    check("S5 duplicate id -> CRITICAL",
          "DUPLICATE-ID" in [a["code"] for a in r["anomalies"]])

    # S6 negative amount
    revn = rail([{"tx_id": "T9", "date": "2026-11-08", "net": -5.0}])
    r = reconcile(paths(revenue=revn, quota=q, l1=empty, revenue_drill=empty))
    check("S6 negative amount -> CRITICAL",
          "NEGATIVE-AMOUNT" in [a["code"] for a in r["anomalies"]])

    # S7 rows exist but net sums to zero
    revz = rail([{"tx_id": "T3", "date": "2026-11-08", "net": 0.0}])
    r = reconcile(paths(revenue=revz, quota=q, l1=empty, revenue_drill=empty))
    check("S7 rows without money -> CRITICAL (not silent zero)",
          "ROWS-WITHOUT-NET" in [a["code"] for a in r["anomalies"]])

    # S8 missing date is a WARN, not a failure
    revm = rail([{"tx_id": "T4", "net": 10.0}])
    r = reconcile(paths(revenue=revm, quota=q, l1=empty, revenue_drill=empty))
    check("S8 missing date -> WARN only, verdict stays OK",
          "MISSING-DATE" in [a["code"] for a in r["anomalies"]]
          and r["verdict"] == "RECONCILE-OK")

    # S9 drill rail is displayed but never added to real totals
    dr = rail([{"order_id": "MOCK-1", "price": 999.0, "net": 900.0}])
    r = reconcile(paths(revenue=rev, quota=q, l1=empty, revenue_drill=dr))
    check("S9 drill rail excluded from real money",
          r["sections"][1]["net"] == 100.0
          and r["sections"][3]["verdict"] == "DRILL-ONLY",
          "net=%s" % r["sections"][1]["net"])

    # S10 determinism
    a = json.dumps(reconcile(paths(revenue=rev, quota=q, l1=empty, revenue_drill=empty)),
                   sort_keys=True, ensure_ascii=True)
    b = json.dumps(reconcile(paths(revenue=rev, quota=q, l1=empty, revenue_drill=empty)),
                   sort_keys=True, ensure_ascii=True)
    check("S10 determinism", a == b)

    # S11 a rail holding only drill rows: named as WARN, never counted as data
    qonly = rail([{"tx_id": "Q-DRILL-2", "consumer": "anon-drill-001",
                   "date": "2026-11-08", "amount_mtok": 0.42, "consumed_mtok": 0.1}])
    r = reconcile(paths(revenue=empty, quota=qonly, l1=empty, revenue_drill=empty))
    drill_anom = [a for a in r["anomalies"] if a["code"] == "DRILL-IN-REAL-QUOTA"]
    check("S11 drill-only rail -> WARN (not CRITICAL, still named)",
          len(drill_anom) == 1 and drill_anom[0]["level"] == "WARN"
          and r["sections"][0]["verdict"] == "NO-DATA"
          and r["verdict"] == "RECONCILE-OK",
          "%s/%s" % (drill_anom[0]["level"] if drill_anom else "none",
                     r["sections"][0]["verdict"]))

    for p in (empty, rev, q, qd, revd, revn, revz, revm, dr):
        try:
            os.unlink(p)
        except OSError:
            pass
    print("cost-daily selftest: %d/%d PASS"
          % (len([r for r in results if r]), len(results)))
    return 0 if all(results) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "selftest"])
    ap.add_argument("--json-out", default="")
    ap.add_argument("--txt-out", default="")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        return selftest()
    res = reconcile()
    text = render(res)
    if a.txt_out:
        io.open(a.txt_out, "w", encoding="utf-8", newline="\n").write(text + "\n")
    if a.json_out:
        io.open(a.json_out, "w", encoding="utf-8", newline="\n").write(
            json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    print(text)
    return 0 if res["verdict"] == "RECONCILE-OK" else 2


if __name__ == "__main__":
    sys.exit(main())