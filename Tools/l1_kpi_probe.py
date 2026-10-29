#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""l1_kpi_probe.py - successor KPI probe for the L1 lane (D-20261028-18 ③).

Why: D-20261028-18 retired "L1 consumption rate >=20%" (its denominator is
science-free while the lane is DARK) and named two successor KPIs owned by
@BigCompute: (A) L1_ENDPOINT reachability in three states, (B) fallback success
rate over the FALLBACK-OK scope. This tool is the measuring side of that
definition: it probes live sockets and reads the static census, it never
starts, stops or re-points a service.

KPI-A  L1_ENDPOINT reachability (tri-state, D-18 ③ "UP/DOWN/ABSENT 三态读数"):
  UP      TCP accept + GET /v1/models -> 200 carrying >=1 model id
  DOWN    an endpoint is *expected* here (explicit env/user value, or something
          does listen) but the model API does not answer
  ABSENT  no explicit endpoint configured and nothing listens on the code
          default port -> the lane is not deployed on this host. This is not a
          failure reading; it is the honest "no lane" state.

KPI-B  fallback success rate. Scope = census rows with status FALLBACK-OK and
  domain OWN (FIXTURE / DIAGNOSTIC / MESSAGE-REF / DOC-REF are already excluded
  upstream by Tools/l1_ref_census.py):
      rate = live-capable rows / FALLBACK-OK rows
  A row is live-capable when at least one endpoint reachable from *its own
  scope* answers UP: its ENV override value when the variable is set, otherwise
  the code default, plus any distinct port literal declared inside the
  enclosing function (that is the MULTI-ENDPOINT / FAILOVER-KW steering).
  Denominators and verdicts: total==0 -> N/A-NO-SCOPE (never reported as 0%),
  all live -> GREEN, none live -> RED, mixed -> PARTIAL. Per D-18 ②, a row whose
  candidates are all ABSENT stays in the denominator and counts as not-live:
  "no live fallback target" is a real finding, not a missing measurement.

Usage:
  python Tools/l1_kpi_probe.py selftest
  python Tools/l1_kpi_probe.py reach    [--endpoint 8090]
  python Tools/l1_kpi_probe.py fallback [--port 8090]
  python Tools/l1_kpi_probe.py report   [--port 8090] [--out state/l1-kpi-probe.json]

Exit: reach 0=UP/3=DOWN/4=ABSENT; fallback+report 0=GREEN-or-N/A/3=PARTIAL-or-RED;
2=usage or scan error. selftest 0=PASS.
"""
import argparse
import datetime
import http.client
import json
import os
import re
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENV_NAME = "L1_ENDPOINT"
DEFAULT_HOST = "127.0.0.1"
# D-20261029-11 (b): the L1 coder lane at :8090 is retired as a tombstone and
# the default endpoint moves to the local ollama lane. Two ports, two jobs -
# conflating them would seed the consumer rows with a live port that none of
# them actually names, i.e. fake-green KPI-B by construction.
L1_DEFAULT_PORT = 11434
TOMBSTONED_PORTS = (8090,)
CENSUS_SEED_PORT = 8090
DEFAULT_PORT = CENSUS_SEED_PORT        # historical alias: census/scope seed only
REGISTRY_PATH = os.path.join(REPO, "docs", "ops", "l1-no-fallback-registry.json")
MODELS_PATH = "/v1/models"
TIMEOUT = 1.5
# Only literals that *look like* a port: "host:8090", port=8090, or a bare quoted "8090".
# A bare 4-digit number is usually a year/count and must not become a probe target.
PORT_SHAPED_RES = (
    re.compile(r":(\d{4,5})(?![\d])"),
    re.compile(r"(?:port|PORT|addr|ADDR)\D{0,12}(\d{4,5})(?![\d])"),
    re.compile(r"['\"](\d{4,5})['\"]"),
)
TOOL_VERSION = "1.0"

STATES = ("UP", "DOWN", "ABSENT")


def parse_endpoint(spec=None):
    """Return (host, port, explicit). explicit=True means someone named it."""
    raw = spec if spec is not None else os.environ.get(ENV_NAME)
    if not raw:
        return DEFAULT_HOST, L1_DEFAULT_PORT, False
    raw = raw.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        rest = raw.split("://", 1)[1].split("/", 1)[0]
        host, _, port = rest.partition(":")
        return (host or DEFAULT_HOST), int(port or (443 if raw.startswith("https") else 80)), True
    if re.fullmatch(r":?\d{2,5}", raw):
        return DEFAULT_HOST, int(raw.lstrip(":")), True
    host, _, port = raw.partition(":")
    return (host or DEFAULT_HOST), int(port or L1_DEFAULT_PORT), True


def _http_get(host, port, path, timeout):
    """Return (status, text). status 0 = transport error (text = reason)."""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.read().decode("utf-8", "replace")
        finally:
            conn.close()
    except (OSError, http.client.HTTPException) as exc:
        return 0, str(exc)[:120]


def count_models(text):
    """Model ids in a /v1/models body. 0 also covers 'not a model payload'."""
    try:
        doc = json.loads(text)
    except ValueError:
        return 0
    if not isinstance(doc, dict):
        return 0
    for key in ("data", "models"):
        items = doc.get(key)
        if isinstance(items, list):
            return sum(1 for it in items if isinstance(it, dict) and (it.get("id") or it.get("name")))
    return 0


def probe(host, port, explicit, models_path=MODELS_PATH, timeout=TIMEOUT, http_get=None):
    """KPI-A for one endpoint. Pure read."""
    if port in TOMBSTONED_PORTS:
        # D-20261029-11 (b): a retired lane is registered, not measured. Calling
        # it DARK/DOWN again would re-open the P1 that the verdict closed.
        return "TOMBSTONE"
    getter = http_get or _http_get
    try:
        with socket.create_connection((host, port), timeout):
            listening = True
    except OSError:
        listening = False
    if not listening:
        # Nothing listens: expected-here means DOWN, unconfigured default means ABSENT.
        return "DOWN" if explicit else "ABSENT"
    status, text = getter(host, port, models_path, timeout)
    if status != 200:
        return "DOWN"
    return "UP" if count_models(text) else "DOWN"


def _load_census():
    sys.path.insert(0, HERE)
    import l1_ref_census  # noqa: E402  (sibling tool, same repo)
    return l1_ref_census


def census_rows(port=DEFAULT_PORT):
    """FALLBACK-OK rows in this repo, via the census tool (read-only).

    Rows from this file are dropped: KPI-B measures *consumers* of the lane,
    and the measuring instrument is not one. Without this the probe's own
    `DEFAULT_PORT = 8090` constant shows up as a consumer row whenever the
    module body contains failover keywords - the row count then changes
    whenever anyone edits this tool's comments (observed 2026-10-28: an edit
    adding "fallback" to the role-table reasons turned 5 rows into 6). A
    metric that reacts to its own source text is not a metric.
    """
    census = _load_census()
    roots = [HERE]
    sibling = os.path.abspath(os.path.join(REPO, "..", "..", "Tools"))
    if os.path.isdir(sibling):
        roots.append(sibling)
    rep = census.scan(roots, port, ours=REPO)
    me = os.path.abspath(__file__)
    return [r for r in rep["refs"]
            if r["status"] == "FALLBACK-OK" and r.get("domain", "OWN") == "OWN"
            and os.path.abspath(r.get("file", "")) != me]


def scope_ports(row, default_port):
    """Ports declared inside the row's enclosing function (steering candidates)."""
    try:
        with open(row["file"], "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return [default_port]
    census = _load_census()
    lo, hi = 1, len(lines)
    if row["file"].lower().endswith(".py"):
        funcs = census._function_ranges("\n".join(lines))          # same-tool reuse
        spans = [(hi - start, start, hi) for _name, start, hi in funcs
                 if start <= row["line"] <= hi]
        if spans:
            _span, lo, hi = max(spans)                             # innermost def
        else:
            lo, hi = max(1, row["line"] - 40), min(len(lines), row["line"] + 40)
    else:
        lo, hi = max(1, row["line"] - 40), min(len(lines), row["line"] + 40)
    ports = [default_port]
    for text in lines[lo - 1:hi]:
        for pat in PORT_SHAPED_RES:
            for lit in pat.findall(text):
                p = int(lit)
                if 80 < p < 65536 and p not in ports:
                    ports.append(p)
    return ports


def steady_from_prober(row_states):
    """Pure verdict math: list[bool] -> (rate, verdict). Used by selftest."""
    total = len(row_states)
    if total == 0:
        return None, "N/A-NO-SCOPE"
    live = sum(1 for s in row_states if s)
    rate = round(live / float(total), 4)
    if live == total:
        return rate, "GREEN"
    if live == 0:
        return rate, "RED"
    return rate, "PARTIAL"


DEFAULT_ROLE = "CONSUMPTION"

# FALLBACK-OK rows whose port literal is not an outbound consumption call.
# Declarative on purpose, and guarded twice: an entry without a reason is a
# defect, and an entry that no longer matches a census row is stale (same
# lesson as EXEMPT_HISTORICAL in model_ref_drift_check / OUT_OF_CENSUS in
# rails_schema_check - an unguarded exemption table rots into a hole).
#
# Why segment at all (2026-10-28 T158 pre-work): none of the three dead rows
# in KPI-B is a call site that *could* fall back. cost_ledger writes the lane
# as ledger metadata; p0_moe_measure and qa_smoke name a lane as the subject
# of a measurement. Handing a probe a fallback would make it measure another
# lane and mask the outage - the exact false-green class the probes exist to
# kill. The primary rate stays un-segmented until the 10-29 12:00 tombstone /
# restore verdict (D-20261028-18); this table only adds a second reading.
ROW_ROLES = [
    {"file": "cost_ledger.py", "line": 1429, "role": "RECORD-FIELD",
     "anchor": 'l1r.add_argument("--endpoint"',
     "reason": "l1-record --endpoint default is stored in the ledger row; no "
               "request is issued from this literal"},
    {"file": "p0_moe_measure.py", "line": 35, "role": "DIAGNOSTIC-TARGET",
     "anchor": 'ap.add_argument("--base-url"',
     "reason": "V100 P0 benchmark harness: --base-url names the lane under "
               "measurement, a fallback would silently benchmark another lane"},
    {"file": "qa_smoke.py", "line": 844, "role": "DIAGNOSTIC-TARGET",
     "anchor": "CODER_PORT = 8090",
     "reason": "CODER_PORT is the lane the liveness probe reports on; falling "
               "back would hide the outage the probe exists to find"},
]



QA_SMOKE_SRC = os.path.join(HERE, "qa_smoke.py")


def coder_port_line():
    """Line of qa_smoke's CODER_PORT constant - computed, never hard-coded.

    Fixtures that pin a line number break on every unrelated edit above it
    (observed three times on 2026-10-29 alone). Scan instead.
    """
    try:
        with open(QA_SMOKE_SRC, "r", encoding="utf-8") as f:
            for i, t in enumerate(f.read().splitlines()):
                if t.strip() == "CODER_PORT = 8090":
                    return i + 1
    except OSError:
        pass
    return 0


def row_source_line(row):
    """The source line a census row points at (None when unreadable)."""
    try:
        with open(row["file"], "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        return lines[int(row["line"]) - 1]
    except (OSError, IndexError, KeyError, TypeError, ValueError):
        return None


def entry_matches(row, entry):
    """Anchor-first entry matching.

    Line numbers drift every time the file grows - this table has already been
    wrong twice (qa_smoke CODER_PORT moved 772 -> 839 -> 844 inside one day,
    once by an unrelated edit and once by the tombstone patch itself). An
    anchor is the literal the row is about, so it survives that churn; the
    line number stays in the table for humans only.
    """
    if os.path.basename(row.get("file", "")) != entry.get("file"):
        return False
    anchor = (entry.get("anchor") or "").strip()
    if anchor:
        text = row_source_line(row)
        return text is not None and anchor in text
    return row.get("line") == entry.get("line")


def role_for(row, roles=None):
    """(role, reason) for a census row / detail row; unlisted = CONSUMPTION."""
    roles = ROW_ROLES if roles is None else roles
    for e in roles:
        if entry_matches(row, e):
            return e.get("role", DEFAULT_ROLE), e.get("reason", "")
    return DEFAULT_ROLE, ""


def role_issues(rows, roles=None):
    """Table hygiene: entries lacking a reason, or no longer matching a row."""
    roles = ROW_ROLES if roles is None else roles
    out = []
    for e in roles:
        if not (e.get("reason") or "").strip():
            out.append({"file": e.get("file"), "line": e.get("line"),
                        "problem": "NO-REASON"})
        elif not any(entry_matches(r, e) for r in rows):
            out.append({"file": e.get("file"), "line": e.get("line"),
                        "problem": "STALE"})
    return out


def load_registry(path=None):
    """Rows explicitly registered as 'no fallback by design' (D-20261029-11 (b)).

    The (b) branch of the 10-29 verdict forbids silence: a FALLBACK-OK row that
    cannot name a live target must either name one or say why not, on the record.
    The registry is that record, in JSON so probes can read it. Missing file is
    an empty registry, not an error - a clone without the fallback rows is normal.
    """
    path = path or REGISTRY_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return []
    entries = doc.get("entries") if isinstance(doc, dict) else doc
    return [e for e in (entries or []) if isinstance(e, dict)]


def registry_issues(rows, entries):
    """Hygiene for the no-fallback registry: reason required, entry must match a row."""
    out = []
    for e in entries:
        if not (e.get("reason") or "").strip():
            out.append({"file": e.get("file"), "line": e.get("line"),
                        "problem": "NO-REASON"})
        elif not any(entry_matches(r, e) for r in rows):
            out.append({"file": e.get("file"), "line": e.get("line"),
                        "problem": "STALE"})
    return out


def registered_keys(rows, entries):
    """Row identities covered by the registry (anchor-first, same as roles)."""
    return set((os.path.basename(r.get("file", "")), r.get("line"))
               for r in rows if any(entry_matches(r, e) for e in entries))


def registered_no_fallback(rows, entries=None):
    """(registered row entries, issues) for the given census rows."""
    entries = load_registry() if entries is None else entries
    hits = [e for e in entries if any(entry_matches(r, e) for r in rows)]
    return hits, registry_issues(rows, entries)


def segment_states(detail, roles=None):
    """Split per-row live flags into (consumption states, excluded rows)."""
    roles = ROW_ROLES if roles is None else roles
    consumption, excluded = [], []
    for d in detail:
        role, reason = role_for(d, roles)
        if role == DEFAULT_ROLE:
            consumption.append(bool(d.get("live")))
        else:
            excluded.append({"file": d["file"], "line": d["line"],
                             "role": role, "reason": reason,
                             "live": bool(d.get("live"))})
    return consumption, excluded


def evaluate_fallback(rows, port=DEFAULT_PORT, prober=probe, scope_fn=scope_ports,
                      registry=None):
    """KPI-B over census rows. prober/scope_fn/registry injectable for selftest."""
    detail = []
    states = []
    for row in rows:
        ports = scope_fn(row, port)
        cand = []
        for p in ports:
            host = DEFAULT_HOST
            explicit = p != port
            spec = os.environ.get(ENV_NAME)
            if spec:
                ehost, eport, _ = parse_endpoint(spec)
                if eport == p:
                    host = ehost
                    explicit = True
            state = ("TOMBSTONE" if p in TOMBSTONED_PORTS
                     else prober(host, p, explicit))
            cand.append({"port": p, "host": host, "state": state})
        live = any(c["state"] == "UP" for c in cand)
        states.append(live)
        detail.append({"file": row["file"], "line": row["line"],
                       "steering": row.get("steering", ""),
                       "candidates": cand, "live": live})
    rate, verdict = steady_from_prober(states)
    live_n = sum(1 for s in states if s)
    cons, excluded = segment_states(detail)
    c_rate, c_verdict = steady_from_prober(cons)
    reg, reg_issues = registered_no_fallback(rows, registry)
    reg_keys = registered_keys(rows, reg)
    res_states = [s for row, s in zip(rows, states)
                  if (os.path.basename(row.get("file", "")), row.get("line")) not in reg_keys]
    r_rate, r_verdict = steady_from_prober(res_states)
    return {"port": port, "rows_total": len(rows),
            "rows_live": live_n,
            "rate": rate, "verdict": verdict, "rows": detail,
            "tombstoned_ports": list(TOMBSTONED_PORTS),
            "segments": {
                "primary": {"basis": "every FALLBACK-OK row (unchanged math)",
                            "live": live_n, "total": len(states),
                            "rate": rate, "verdict": verdict},
                "consumption": {"basis": "rows whose literal is an outbound call",
                                "live": sum(1 for s in cons if s),
                                "total": len(cons),
                                "rate": c_rate, "verdict": c_verdict},
                "residual": {"basis": "rows still owing a fallback (registered "
                                      "no-fallback rows excluded)",
                             "live": sum(1 for s in res_states if s),
                             "total": len(res_states),
                             "rate": r_rate, "verdict": r_verdict},
                "excluded": excluded,
                "no_fallback_registered": reg,
                "registry_issues": reg_issues,
                "table_issues": role_issues(rows),
            }}


def build_report(port=DEFAULT_PORT):
    host, eport, explicit = parse_endpoint()
    kpi_a = {"endpoint": "%s:%d" % (host, eport), "explicit": explicit,
             "state": probe(host, eport, explicit),
             "tombstoned": eport in TOMBSTONED_PORTS}
    rows = census_rows(port)
    kpi_b = evaluate_fallback(rows, port=port)
    return {"tool": "l1_kpi_probe", "version": TOOL_VERSION,
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "machine": os.environ.get("COMPUTERNAME", "unknown"),
            "policy": "D-20261029-11", "tombstoned_ports": list(TOMBSTONED_PORTS),
            "kpi_a_reachability": kpi_a, "kpi_b_fallback": kpi_b}


def cmd_reach(a):
    host, port, explicit = parse_endpoint(a.endpoint)
    state = probe(host, port, explicit)
    note = " (tombstoned: retired by D-20261029-11, not a defect)" \
        if port in TOMBSTONED_PORTS else ""
    print("KPI-A reach %s:%d explicit=%s state=%s%s" % (host, port, explicit, state, note))
    if state == "TOMBSTONE":
        return 0
    return {"UP": 0, "DOWN": 3, "ABSENT": 4}.get(state, 4)


def cmd_fallback(a):
    rows = census_rows(a.port)
    rep = evaluate_fallback(rows, port=a.port)
    seg = rep["segments"]
    print("KPI-B fallback rate=%s verdict=%s live=%d/%d port=%d" % (
        "N/A" if rep["rate"] is None else rep["rate"], rep["verdict"],
        rep["rows_live"], rep["rows_total"], a.port))
    for d in rep["rows"]:
        print("  %s:%s %s -> %s" % (
            d["file"], d["line"], d["steering"] or "-",
            ",".join("%s:%s" % (c["port"], c["state"]) for c in d["candidates"])))
    c_ = seg["consumption"]
    print("KPI-B segments consumption rate=%s verdict=%s live=%d/%d excluded=%d" % (
        "N/A" if c_["rate"] is None else c_["rate"], c_["verdict"],
        c_["live"], c_["total"], len(seg["excluded"])))
    for e in seg["excluded"]:
        print("  EXCLUDED %s:%s %s live=%s - %s" % (
            e["file"], e["line"], e["role"], e["live"], e["reason"]))
    for i in seg["table_issues"]:
        print("  ROLE-TABLE %s %s:%s" % (i["problem"], i["file"], i["line"]))
    res = seg["residual"]
    print("KPI-B segments residual rate=%s verdict=%s live=%d/%d registered=%d" % (
        "N/A" if res["rate"] is None else res["rate"], res["verdict"],
        res["live"], res["total"], len(seg["no_fallback_registered"])))
    for e in seg["no_fallback_registered"]:
        print("  NO-FALLBACK %s:%s %s - %s" % (
            e["file"], e["line"], e.get("role", "-"), e.get("reason", "")))
    for i in seg["registry_issues"]:
        print("  REGISTRY %s %s:%s" % (i["problem"], i["file"], i["line"]))
    face = c_ if getattr(a, "segmented", False) else rep
    return 0 if face["verdict"] in ("GREEN", "N/A-NO-SCOPE") else 3


def cmd_report(a):
    rep = build_report(a.port)
    out = a.out or os.path.join(REPO, "state", "l1-kpi-probe-%s.json" %
                                datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    a_ = rep["kpi_a_reachability"]
    b_ = rep["kpi_b_fallback"]
    print("KPI-A %s state=%s | KPI-B rate=%s verdict=%s (%d/%d) | out=%s" % (
        a_["endpoint"], a_["state"], "N/A" if b_["rate"] is None else b_["rate"],
        b_["verdict"], b_["rows_live"], b_["rows_total"], out))
    c_ = b_.get("segments", {}).get("consumption", {})
    if c_:
        print("KPI-B segments consumption rate=%s verdict=%s (%d/%d) excluded=%d" % (
            "N/A" if c_["rate"] is None else c_["rate"], c_["verdict"],
            c_["live"], c_["total"], len(b_["segments"]["excluded"])))
    return 0 if b_["verdict"] in ("GREEN", "N/A-NO-SCOPE") else 3


def cmd_selftest(_a):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    checks = []

    def stub(payload):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, srv.server_address[1]

    # S1 live stub with a model id -> UP
    srv, p1 = stub({"data": [{"id": "stub-model"}]})
    checks.append(("S1 stub /v1/models -> UP", probe("127.0.0.1", p1, True) == "UP"))
    # S2 same port after shutdown, expected -> DOWN
    srv.shutdown()
    srv.server_close()
    checks.append(("S2 dead-but-expected -> DOWN", probe("127.0.0.1", p1, True) == "DOWN"))
    # S3 unconfigured closed port -> ABSENT, then explicit -> DOWN
    probe_sock = socket.socket()
    probe_sock.bind(("127.0.0.1", 0))
    free_port = probe_sock.getsockname()[1]
    probe_sock.close()
    checks.append(("S3 closed+unconfigured -> ABSENT", probe("127.0.0.1", free_port, False) == "ABSENT"))
    checks.append(("S4 closed+explicit -> DOWN", probe("127.0.0.1", free_port, True) == "DOWN"))
    # S5 200 with no model ids is not UP
    srv2, p2 = stub({"object": "list", "data": []})
    checks.append(("S5 empty model list -> DOWN", probe("127.0.0.1", p2, True) == "DOWN"))
    srv2.shutdown()
    srv2.server_close()
    # S6 verdict math
    math_ok = (steady_from_prober([True] * 3) == (1.0, "GREEN")
               and steady_from_prober([True, True, False]) == (0.6667, "PARTIAL")
               and steady_from_prober([False, False]) == (0.0, "RED")
               and steady_from_prober([]) == (None, "N/A-NO-SCOPE"))
    checks.append(("S6 rate math incl. N/A-NO-SCOPE", math_ok))
    # S7 endpoint parsing
    parse_ok = (parse_endpoint("8090") == (DEFAULT_HOST, 8090, True)
                and parse_endpoint(":8792") == (DEFAULT_HOST, 8792, True)
                and parse_endpoint("http://127.0.0.1:11434/v1") == ("127.0.0.1", 11434, True)
                and parse_endpoint("") == (DEFAULT_HOST, L1_DEFAULT_PORT, False))
    checks.append(("S7 endpoint parsing", parse_ok))
    # S8 census integration: rows have the fields KPI-B needs
    rows = census_rows(DEFAULT_PORT)
    checks.append(("S8 census rows usable (%d FALLBACK-OK)" % len(rows),
                   all({"file", "line", "status"} <= set(r) for r in rows)))
    # S9 scope ports always include the default, never dedupe it away
    if rows:
        ports = scope_ports(rows[0], DEFAULT_PORT)
        checks.append(("S9 scope ports include default", DEFAULT_PORT in ports))
    else:
        checks.append(("S9 scope ports include default (no rows: vacuous)", True))
    # S10 port-shaped extraction: a bare year is not a probe target
    sample = "started 2026-10-28 boundary=1335 url http://h:8792/v1 port 11434 '8090'"
    picked = set()
    for pat in PORT_SHAPED_RES:
        picked.update(int(x) for x in pat.findall(sample))
    checks.append(("S10 no year/noise as port, real ports picked",
                   2026 not in picked and 1335 not in picked
                   and {8792, 11434, 8090} <= picked))

    # S11-S16 role segmentation (T158 pre-work). Segmentation is an additive
    # second reading: the primary rate/verdict must be untouched by the table,
    # and the table itself is guarded (reason required, stale entries flagged).
    checks.append(("S11 unlisted row defaults to CONSUMPTION",
                   role_for({"file": "some_tool.py", "line": 1}, ROW_ROLES)
                   == ("CONSUMPTION", "")))
    checks.append(("S12 declared row carries role + reason",
                   role_for({"file": os.path.join("Tools", "qa_smoke.py"),
                             "line": coder_port_line()}, ROW_ROLES)[0] == "DIAGNOSTIC-TARGET"))
    no_reason = [{"file": "a.py", "line": 2, "role": "RECORD-FIELD", "reason": " "}]
    checks.append(("S13 reason-less entry flagged NO-REASON",
                   [i["problem"] for i in role_issues([{"file": "a.py", "line": 2}],
                                                      no_reason)] == ["NO-REASON"]))
    stale = role_issues([{"file": "a.py", "line": 2}], ROW_ROLES)
    checks.append(("S14 stale entries flagged (%d)" % len(stale),
                   len(stale) == len(ROW_ROLES)
                   and all(i["problem"] == "STALE" for i in stale)))
    syn = [{"file": "f1.py", "line": 1, "live": True},
           {"file": "f2.py", "line": 2, "live": True},
           {"file": os.path.join("Tools", "qa_smoke.py"), "line": coder_port_line(), "live": False}]
    cons, exc = segment_states(syn, ROW_ROLES)
    checks.append(("S15 segment math: excluded row cannot move consumption rate",
                   steady_from_prober(cons) == (1.0, "GREEN") and len(exc) == 1
                   and exc[0]["role"] == "DIAGNOSTIC-TARGET"))
    rows_syn = [{"file": "a.py", "line": 1, "status": "FALLBACK-OK"},
                {"file": os.path.join("Tools", "qa_smoke.py"), "line": coder_port_line(),
                 "status": "FALLBACK-OK"}]
    rep_syn = evaluate_fallback(rows_syn, port=DEFAULT_PORT,
                                prober=lambda *a, **k: "ABSENT",
                                scope_fn=lambda row, p: [p])
    checks.append(("S16 primary verdict untouched by the role table",
                   rep_syn["rate"] == 0.0 and rep_syn["verdict"] == "RED"
                   and rep_syn["segments"]["excluded"][0]["line"] == coder_port_line()
                   and rep_syn["segments"]["primary"]["verdict"] == "RED"))
    if rows:
        issues = role_issues(rows)
        checks.append(("S17 live role table clean (%d issues)" % len(issues),
                       not issues))
        me = os.path.abspath(__file__)
        checks.append(("S18 self rows excluded (%d rows)" % len(rows),
                       all(os.path.abspath(r.get("file", "")) != me
                           for r in rows)))

    # S19-S23 tombstone + no-fallback registry (D-20261029-11 (b))
    checks.append(("S19 tombstoned port reported as TOMBSTONE, never probed",
                   probe("127.0.0.1", TOMBSTONED_PORTS[0], True) == "TOMBSTONE"))
    rep_t = evaluate_fallback([{"file": "a.py", "line": 1, "status": "FALLBACK-OK"}],
                              port=CENSUS_SEED_PORT,
                              prober=lambda *a, **k: "UP",
                              scope_fn=lambda row, p: [TOMBSTONED_PORTS[0]])
    checks.append(("S20 a listening tombstone target cannot score KPI-B",
                   rep_t["rows_live"] == 0 and rep_t["rate"] == 0.0
                   and rep_t["rows"][0]["candidates"][0]["state"] == "TOMBSTONE"))
    hyg = registry_issues([{"file": "a.py", "line": 1}],
                          [{"file": "a.py", "line": 2, "reason": "x"},
                           {"file": "a.py", "line": 1, "reason": " "}])
    checks.append(("S21 registry hygiene: stale + no-reason flagged",
                   sorted(i["problem"] for i in hyg) == ["NO-REASON", "STALE"]))
    syn3 = [{"file": "f1.py", "line": 1, "status": "FALLBACK-OK"},
            {"file": "f2.py", "line": 2, "status": "FALLBACK-OK"},
            {"file": "f3.py", "line": 3, "status": "FALLBACK-OK"}]
    reg_syn = [{"file": "f3.py", "line": 3, "role": "RECORD-FIELD", "reason": "metadata"}]

    def _syn_probe(host, port, explicit):
        return "UP" if port != 50000 else "ABSENT"
    rep_r = evaluate_fallback(syn3, port=CENSUS_SEED_PORT, prober=_syn_probe,
                              scope_fn=lambda row, p: [50000 if row.get("line") == 3 else 11434],
                              registry=reg_syn)
    checks.append(("S22 residual reading excludes registered rows only",
                   rep_r["rate"] == 0.6667 and rep_r["verdict"] == "PARTIAL"
                   and rep_r["segments"]["residual"]["rate"] == 1.0
                   and rep_r["segments"]["residual"]["total"] == 2
                   and len(rep_r["segments"]["no_fallback_registered"]) == 1))
    if rows:
        reg_live = load_registry()
        checks.append(("S23 live no-fallback registry clean (%d entries)" % len(reg_live),
                       bool(reg_live) and not registry_issues(rows, reg_live)))

    smoke = os.path.join(HERE, "qa_smoke.py")
    with open(smoke, "r", encoding="utf-8") as _f:
        _lines = _f.read().splitlines()
    _n = next(i + 1 for i, t in enumerate(_lines) if t.strip() == "CODER_PORT = 8090")
    drift_row = {"file": smoke, "line": _n}
    stale_entry = {"file": "qa_smoke.py", "line": 1, "anchor": "CODER_PORT = 8090"}
    wrong_entry = {"file": "qa_smoke.py", "line": _n, "anchor": "zzz-not-here"}
    checks.append(("S24 anchor match survives a stale line number",
                   entry_matches(drift_row, stale_entry)
                   and not entry_matches(drift_row, wrong_entry)
                   and role_for(drift_row, ROW_ROLES)[0] == "DIAGNOSTIC-TARGET"))

    bad = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print("%s %s" % ("PASS" if ok else "FAIL", name))
    print("selftest: %d/%d PASS" % (len(checks) - len(bad), len(checks)))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="L1 lane successor KPI probe (D-20261028-18)")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("selftest")
    s.set_defaults(func=cmd_selftest)
    for name, fn in (("reach", cmd_reach), ("fallback", cmd_fallback), ("report", cmd_report)):
        p = sub.add_parser(name)
        p.add_argument("--port", type=int, default=DEFAULT_PORT)
        if name == "reach":
            p.add_argument("--endpoint", default=None)
        if name == "fallback":
            p.add_argument("--segmented", action="store_true",
                           help="judge the exit code on the consumption "
                                "segment (opt-in; default face is unchanged)")
        if name == "report":
            p.add_argument("--out", default=None)
        p.set_defaults(func=fn)
    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    try:
        return a.func(a)
    except Exception as exc:                                  # noqa: BLE001
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())