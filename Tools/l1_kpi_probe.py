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
DEFAULT_PORT = 8090
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
        return DEFAULT_HOST, DEFAULT_PORT, False
    raw = raw.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        rest = raw.split("://", 1)[1].split("/", 1)[0]
        host, _, port = rest.partition(":")
        return (host or DEFAULT_HOST), int(port or (443 if raw.startswith("https") else 80)), True
    if re.fullmatch(r":?\d{2,5}", raw):
        return DEFAULT_HOST, int(raw.lstrip(":")), True
    host, _, port = raw.partition(":")
    return (host or DEFAULT_HOST), int(port or DEFAULT_PORT), True


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
    """FALLBACK-OK rows in this repo, via the census tool (read-only)."""
    census = _load_census()
    roots = [HERE]
    sibling = os.path.abspath(os.path.join(REPO, "..", "..", "Tools"))
    if os.path.isdir(sibling):
        roots.append(sibling)
    rep = census.scan(roots, port, ours=REPO)
    return [r for r in rep["refs"]
            if r["status"] == "FALLBACK-OK" and r.get("domain", "OWN") == "OWN"]


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


def evaluate_fallback(rows, port=DEFAULT_PORT, prober=probe, scope_fn=scope_ports):
    """KPI-B over census rows. prober/scope_fn injectable for selftest."""
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
            state = prober(host, p, explicit)
            cand.append({"port": p, "host": host, "state": state})
        live = any(c["state"] == "UP" for c in cand)
        states.append(live)
        detail.append({"file": row["file"], "line": row["line"],
                       "steering": row.get("steering", ""),
                       "candidates": cand, "live": live})
    rate, verdict = steady_from_prober(states)
    return {"port": port, "rows_total": len(rows),
            "rows_live": sum(1 for s in states if s),
            "rate": rate, "verdict": verdict, "rows": detail}


def build_report(port=DEFAULT_PORT):
    host, eport, explicit = parse_endpoint()
    kpi_a = {"endpoint": "%s:%d" % (host, eport), "explicit": explicit,
             "state": probe(host, eport, explicit)}
    rows = census_rows(port)
    kpi_b = evaluate_fallback(rows, port=port)
    return {"tool": "l1_kpi_probe", "version": TOOL_VERSION,
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "machine": os.environ.get("COMPUTERNAME", "unknown"),
            "kpi_a_reachability": kpi_a, "kpi_b_fallback": kpi_b}


def cmd_reach(a):
    host, port, explicit = parse_endpoint(a.endpoint)
    state = probe(host, port, explicit)
    print("KPI-A reach %s:%d explicit=%s state=%s" % (host, port, explicit, state))
    return {"UP": 0, "DOWN": 3, "ABSENT": 4}[state]


def cmd_fallback(a):
    rows = census_rows(a.port)
    rep = evaluate_fallback(rows, port=a.port)
    print("KPI-B fallback rate=%s verdict=%s live=%d/%d port=%d" % (
        "N/A" if rep["rate"] is None else rep["rate"], rep["verdict"],
        rep["rows_live"], rep["rows_total"], a.port))
    for d in rep["rows"]:
        print("  %s:%s %s -> %s" % (
            d["file"], d["line"], d["steering"] or "-",
            ",".join("%s:%s" % (c["port"], c["state"]) for c in d["candidates"])))
    return 0 if rep["verdict"] in ("GREEN", "N/A-NO-SCOPE") else 3


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
                and parse_endpoint("") == (DEFAULT_HOST, DEFAULT_PORT, False))
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