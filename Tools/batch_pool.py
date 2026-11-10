"""batch_pool.py - batch pool stocking registry validator (T-20260929-32).

CEO dispatches O-20260929-007 / O-20260929-013 / O-20260929-019 order
BigCompute to stock the batch pool (备货律: consumer_plan named, no
auto-dispatch; bm-a is on the auto-dispatch blacklist per C-20260929-02
7.4; DRY-RUN observation period runs to 2026-10-05).

Registry: docs/ops/batch-pool-stock-v1.jsonl (committed, CEO-visible).
This tool NEVER dispatches anything - it machine-checks the stocking sheet:
  J1  every card has all required fields non-empty
  J2  City3D/FluxVerse lane cards with category 3d_offline_render >= 2
      (O-20260929-007 hard criterion "3D 离线渲染批 >=2")
  J3  batch_id unique (tx_id idempotency basis)
  J4  consumer_plan non-empty on every card (备货律指名, O-1820 (3) same law)
  J5  no card is in-flight/running; the dry-run lock itself fires only when
      the window is expired AND a card has >=2 consecutive claims without
      ever leaving STOCKED (D-20261106-05 / D-20261108-03: expiry alone is a
      reminder, not a lock)
  J6  data_deps local-presence gate (BC-P-37, group precedent
      D-20261004-02 (1) BigMoney F-01 four-shard crash): if a card
      declares data_deps (file/dir list), every path must exist locally
      at validation time; missing = DATA_NOT_LOCAL red gate. Cards with
      no data_deps field are unaffected (zero forced migration).

Commands:
  list      print all cards (lane summary + per-card lines)
  validate  run J1-J5 against the registry, exit 1 on any violation
  gate      pre-dispatch check for one card: consult Tools/lane_gate.py via
            Tools/dispatch_precheck.py (BC-P-144) and fail closed unless the
            card's lane is OPEN (or an explicit EXEMPT reason is given)
  review    monthly DRY-RUN observation review (M66): machine-readable
            snapshot of validate verdict + lane/status stock + consumer_plan
            coverage + dry-run age vs DRY_RUN_END. Writes state JSON.
  selftest  offline fixtures for every check (no registry needed)

Usage:
  python Tools/batch_pool.py validate
  python Tools/batch_pool.py list
  python Tools/batch_pool.py gate --batch-id B-CITY3D-01
  python Tools/batch_pool.py review
  python Tools/batch_pool.py selftest
"""
import argparse
import datetime
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(ROOT, "docs", "ops", "batch-pool-stock-v1.jsonl")

REQUIRED_FIELDS = [
    "batch_id", "lane", "category", "consumer_plan", "order_ref",
    "workload", "resource_profile", "dispatch_mode", "status",
    "acceptance", "stocked_at",
]
IN_FLIGHT_STATUSES = {"IN_FLIGHT", "RUNNING", "DISPATCHED"}
CITY3D_LANE = "City3D/FluxVerse"
CITY3D_CATEGORY = "3d_offline_render"
CITY3D_MIN = 2  # O-20260929-007: >=2 stocked 3D offline render batches
DRY_RUN_END = "2026-10-05"  # J5 observation window end (M46 review package)
CLAIM_LOG = os.path.join(ROOT, "state", "dispatch-gate.jsonl")
# D-20261106-05 (re-issued D-20261108-03): the lock needs evidence of a card
# being claimed and never igniting.  One expiry on its own must not freeze the
# pool - that is what kept 6 STOCKED cards frozen for 31 days on a calendar
# artefact instead of a finding.
J5_LOCK_STREAK = 2

# A consumer_plan counts as "named" only when it names who takes the work.
# Placeholder forms keep J4's non-empty check satisfied while naming nobody, so
# they must never inflate the dispatchable reading (M161, BC-P-219).
CONSUMER_PLACEHOLDER_MARKERS = ("待对接", "随协作市场表建面", "待定",
                                "TBD", "tbd", "无指名")


def load_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError("line %d: bad JSON: %s" % (lineno, e))
    return rows


def check_rows(rows):
    """Return (problems, info) - problems is a list of violation strings."""
    problems = []
    info = []
    ids = []
    lanes = {}
    city3d_3d = 0
    for i, row in enumerate(rows, 1):
        rid = row.get("batch_id") or "<line %d>" % i
        for field in REQUIRED_FIELDS:
            if not str(row.get(field, "") or "").strip():
                problems.append("J1 %s: missing/empty field '%s'" % (rid, field))
        if row.get("batch_id"):
            ids.append(row["batch_id"])
        if not str(row.get("consumer_plan", "") or "").strip():
            problems.append("J4 %s: consumer_plan empty (备货律指名)" % rid)
        if str(row.get("status", "")).upper() in IN_FLIGHT_STATUSES:
            problems.append(
                "J5 %s: status=%s is in-flight (DRY-RUN to 2026-10-05)"
                % (rid, row.get("status")))
        deps = row.get("data_deps")
        if deps:
            if not isinstance(deps, list):
                problems.append("J6 %s: data_deps must be a list" % rid)
            else:
                for dep in deps:
                    dp = str(dep)
                    target = dp if os.path.isabs(dp) else os.path.join(ROOT, dp)
                    if not os.path.exists(target):
                        problems.append(
                            "J6 %s: DATA_NOT_LOCAL: %s "
                            "(claim-before-dispatch presence assertion, "
                            "D-20261004-02 (1) precedent)" % (rid, dp))
        named_type = declared_named_type(row)
        if named_type not in (None, "bool"):
            # Warning level on purpose: J4's meaning has not changed, so this
            # must not turn a stocked sheet red - but a silently dead label is
            # named rather than absorbed.
            info.append(
                "J7 %s: consumer_named is %s, not a boolean - the label reads "
                "as undeclared and the mismatch check skips this card"
                % (rid, named_type))
        lane = str(row.get("lane", "") or "?")
        lanes[lane] = lanes.get(lane, 0) + 1
        if lane == CITY3D_LANE and row.get("category") == CITY3D_CATEGORY:
            city3d_3d += 1
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    for d in dupes:
        problems.append("J3 duplicate batch_id: %s (tx_id idempotency basis)" % d)
    if city3d_3d < CITY3D_MIN:
        problems.append(
            "J2 City3D/FluxVerse 3d_offline_render cards=%d < %d "
            "(O-20260929-007 hard criterion)" % (city3d_3d, CITY3D_MIN))
    info.append("cards=%d lanes=%s city3d_3d_offline=%d"
               % (len(rows), json.dumps(lanes, ensure_ascii=False), city3d_3d))
    return problems, info


def cmd_list(path):
    rows = load_rows(path)
    print("batch pool stocking registry: %s" % os.path.relpath(path, ROOT))
    lanes = {}
    for row in rows:
        lanes.setdefault(row.get("lane", "?"), []).append(row)
    for lane, cards in lanes.items():
        print("\n[%s] %d card(s)" % (lane, len(cards)))
        for c in cards:
            print("  - %s (%s) status=%s" % (
                c.get("batch_id"), c.get("category"), c.get("status")))
            print("    consumer_plan: %s" % c.get("consumer_plan"))
            print("    order_ref: %s | dispatch_mode: %s" % (
                c.get("order_ref"), c.get("dispatch_mode")))
    print("\ntotal=%d lanes=%d" % (len(rows), len(lanes)))
    return 0


def cmd_validate(path):
    if not os.path.exists(path):
        print("FAIL registry not found: %s" % path)
        return 1
    try:
        rows = load_rows(path)
    except ValueError as exc:
        # 读不到的登记簿也要给判据面：先报 unreadable 再判红，不抛栈（D-20261101-20 判据分层）
        print("FAIL " + str(exc))
        _emit_scope("registry=%s cards=0 lanes=0" % os.path.basename(path), [], [str(exc)])
        print("VERDICT=POOL_FAIL (registry unreadable)")
        return 1
    problems, info = check_rows(rows)
    lanes, statuses = {}, {}
    for r in rows:
        lanes[r.get("lane")] = lanes.get(r.get("lane"), 0) + 1
        statuses[r.get("status")] = statuses.get(r.get("status"), 0) + 1
    # 判据分层：validate 只证「卡片台账合格且已备货」，不证「卡片现在能跑」——后者归 gate/J5
    not_gated = sorted("%s:%s" % (r.get("batch_id"), r.get("status"))
                       for r in rows if r.get("status") in IN_FLIGHT_STATUSES)
    _emit_scope(
        "registry=%s cards=%d lanes=%d status=%s"
        % (os.path.basename(path), len(rows), len(lanes),
           ",".join("%s=%d" % kv for kv in sorted(statuses.items(), key=lambda kv: str(kv[0])))),
        not_gated, [])
    print("scope_note=validate 判台账合格性；卡片可点火性归 batch-gate/J5（本闸不出该结论）")
    for line in info:
        print("INFO " + line)
    if problems:
        for p in problems:
            print("FAIL " + p)
        print("VERDICT=POOL_FAIL (%d problem(s))" % len(problems))
        return 1
    print("VERDICT=POOL_GREEN (J1 fields/J2 city3d>=2/J3 unique/J4 "
          "consumer_plan/J5 dry-run/J6 data_deps-locality all PASS)")
    return 0


def _emit_scope(domain, not_scanned=(), unreadable=()):
    try:
        import scope_proof
        scope_proof.emit(domain, not_scanned, unreadable)
    except ImportError:  # 单文件复制到机动位置时不因缺伴生模块而失判
        print("domain=%s" % domain)
        print("not_scanned=%d [%s]" % (len(not_scanned), ", ".join(map(str, list(not_scanned)[:5])) or "none"))
        print("unreadable=%d [%s]" % (len(unreadable), ", ".join(map(str, list(unreadable)[:5])) or "none"))


def cmd_gate(path, batch_id, consumer=None, exempt=None, as_json=False):
    """Pre-dispatch gate for one card (BC-P-144).

    The registry says what a card *is*, the lane gate says whether this box may
    run it *now*.  Neither answers alone: J5 only forbids in-flight status, and
    lane_gate never saw the pool.  Fail-closed on a closed/stale/unreadable
    lane, and never guess a lane for an unregistered category.
    """
    if not os.path.exists(path):
        print("FAIL registry not found: %s" % path)
        return 1
    rows = load_rows(path)
    card = next((r for r in rows if r.get("batch_id") == batch_id), None)
    if card is None:
        print("FAIL batch_id not found: %s" % batch_id)
        return 64
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import dispatch_precheck
    lane = dispatch_precheck.lane_for_card(card)[0]
    if lane is None:
        print("FAIL category '%s' has no pre-registered lane "
              "(BC-P-144: refuse to guess; pass --lane via dispatch_precheck)"
              % card.get("category"))
        return 64
    if str(card.get("status", "")).upper() in IN_FLIGHT_STATUSES:
        print("WARN %s status=%s is in-flight (J5); gate reads resources only"
              % (batch_id, card.get("status")))
    result, exit_code = dispatch_precheck.evaluate(
        lane, consumer or "batch-pool:%s" % batch_id, exempt,
        live_on_stale=True)
    # batch_id must be in the snapshot itself: without it a claim cannot be
    # attributed back to a card, and the J5 claim rule has nothing to read.
    result["batch_id"] = batch_id
    log_path = dispatch_precheck.append_snapshot(result)
    result["snapshot"] = os.path.relpath(log_path, ROOT)
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("batch-gate: %s category=%s %s"
              % (batch_id, card.get("category"),
                 dispatch_precheck.render_line(result)))
        print("batch-gate: snapshot=%s" % result["snapshot"])
    return exit_code


def load_claims(log_path=None, with_refusals=False):
    """Read dispatch-gate snapshots into per-card claim history.

    A *claim* is a snapshot where the gate actually cleared the card to run
    (gate_exit == 0, lane OPEN).  A snapshot that refused the card - lane
    CLOSED, stale heartbeat, unreadable gate - is a *refusal*, not a claim:
    nothing was taken, so it cannot count as "claimed and never ignited".
    Lines unreadable as JSON are skipped, never guessed.

    Returns {batch_id: [claim,...]}; with with_refusals=True returns
    (claims, refusals).  Cards are identified by the snapshot's ``batch_id``
    field, or - for older lines - by ``consumer`` of the form
    ``batch-pool:<batch_id>``.
    """
    log_path = log_path or CLAIM_LOG
    claims, refused = {}, {}
    if not os.path.exists(log_path):
        return (claims, refused) if with_refusals else claims
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            bid = str(row.get("batch_id") or "")
            if not bid:
                consumer = str(row.get("consumer") or "")
                if consumer.startswith("batch-pool:"):
                    bid = consumer.split(":", 1)[1]
            if not bid:
                continue
            rec = {
                "ts": row.get("ts"),
                "verdict": row.get("verdict"),
                "gate_exit": row.get("gate_exit"),
            }
            if rec["gate_exit"] == 0:
                claims.setdefault(bid, []).append(rec)
            else:
                refused.setdefault(bid, []).append(rec)
    return (claims, refused) if with_refusals else claims


def j5_lock_state(rows, claims=None, today=None, refusals=None):
    """J5 lock judgement per card (D-20261106-05, re-issued D-20261108-03).

    A card locks only when BOTH hold:
      * the dry-run window is expired (calendar fact, alone insufficient), and
      * the card carries >= J5_LOCK_STREAK *cleared* claims (gate_exit == 0)
        while still STOCKED, i.e. it was cleared to run that many times and
        never left STOCKED.
    Refused attempts (lane closed / stale gate) are reported but never lock a
    card: a card nobody could take was not "claimed and never ignited".
    A card already in a non-STOCKED status is not locked.
    """
    import datetime
    if today is None:
        today = datetime.date.today()
    claims = claims or {}
    refusals = refusals or {}
    end = datetime.date(*[int(x) for x in DRY_RUN_END.split("-")])
    age_days = (today - end).days
    overdue = age_days > 0
    per_card = {}
    locked = []
    for row in rows:
        bid = str(row.get("batch_id") or "")
        if not bid:
            continue
        status = str(row.get("status", "?")).upper() or "?"
        streak = len(claims.get(bid, []))
        is_locked = bool(overdue and status == "STOCKED"
                         and streak >= J5_LOCK_STREAK)
        per_card[bid] = {"status": status, "claims": streak,
                         "refused": len(refusals.get(bid, [])),
                         "locked": is_locked}
        if is_locked:
            locked.append(bid)
    return {
        "rule": "expired AND >=%d cleared claims unignited (still STOCKED)"
                % J5_LOCK_STREAK,
        "dry_run_end": DRY_RUN_END,
        "dry_run_age_days": age_days,
        "dry_run_overdue": overdue,
        "claims_total": sum(len(v) for v in claims.values()),
        "refusals_total": sum(len(v) for v in refusals.values()),
        "locked_cards": sorted(locked),
        "per_card": per_card,
        "blocks_activation": bool(locked),
    }


def is_named_consumer(plan):
    """True only when consumer_plan names a consumer, not merely non-empty.

    "消费对接随协作市场表建面" passes J4 but nobody is on the hook for the
    work, so it must not count toward the dispatchable reading.
    """
    text = str(plan or "").strip()
    if not text:
        return False
    return not any(m in text for m in CONSUMER_PLACEHOLDER_MARKERS)


def declared_named(row):
    """Read the card's own `consumer_named` flag: True / False / None.

    None means the card does not declare one (older cards, or a card added
    before M162). It is kept distinct from False so "we never said" cannot be
    read as "we said nobody takes it".
    """
    if "consumer_named" not in row:
        return None
    val = row.get("consumer_named")
    if isinstance(val, bool):
        return val
    return None


def declared_named_type(row):
    """Name the JSON type of `consumer_named`: 'bool' / 'string' / ... / None.

    M164: declared_named() folds every non-bool into "never declared", so a
    card that writes "false" as a string loses its label silently and the
    mismatch check never fires - the mislabel becomes indistinguishable from
    no label, at exactly the spot M162 set out to make visible. A guard needs
    the type name itself to say which card did it.
    """
    if "consumer_named" not in row:
        return None
    val = row.get("consumer_named")
    if isinstance(val, bool):
        return "bool"
    if val is None:
        return "null"
    if isinstance(val, str):
        return "string"
    if isinstance(val, (int, float)):
        return "number"
    if isinstance(val, list):
        return "list"
    if isinstance(val, dict):
        return "object"
    return type(val).__name__


IGNITION_LOG = os.path.join(ROOT, "state", "pool-ignitions.jsonl")
UNKNOWN_CONVERGE_SIDECAR = os.path.join(ROOT, "state", "unknown-converge.json")


def unknown_converge_reading(path=None):
    """T230: the published verdict on previously-unknown receipts.

    `unknown` is an honest state, not an exit: a receipt with no QC reading can
    only leave it by running again, which never happens on a lane that stays
    shut.  `pool_igniter unknown-converge` recomputes what today's disk can
    still prove and publishes three counts here; this reader is the single
    source for every face that must not pay the recompute (an ffmpeg read per
    EXR).  Missing/unreadable sidecar returns None - "not measured yet" must
    never print as "no unknown receipts".
    """
    path = path or UNKNOWN_CONVERGE_SIDECAR
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict) or "known_bad_count" not in doc:
        return None
    return {
        "as_of": doc.get("ts"),
        "unknown": doc.get("unknown"),
        "computable": doc.get("computable_count"),
        "computable_pass": doc.get("computable_pass_count"),
        "computable_fail": doc.get("computable_fail_count"),
        "unprovable": doc.get("unprovable_count"),
        "known_bad": doc.get("known_bad_count"),
        # M181: a byte match against an artifact living outside its own run
        # directory is weaker evidence - surfaced separately, never merged
        # with same-run evidence.
        "legacy_shared": doc.get("legacy_shared_count"),
        "known_bad_items": ["%s@%s" % (e.get("batch_id"), e.get("ts"))
                            for e in (doc.get("known_bad") or [])],
        "age_policy_days": doc.get("age_policy_days"),
    }


def receipt_deliverable(row):
    """Whether an ignition receipt's product is usable, not merely a clean run.

    T226 (D-20261109-08) split two claims that used to share `ok`: `ok` = the
    runner exited cleanly and wrote its artifacts; `deliverable_ok` = those
    artifacts passed frame QC.  Counting `ok` as "the pool produced something"
    would let the 10:15:56 batch (5 of 6 frames magenta placeholder) stand as
    effective supply.

    Receipts written before the split carry no `deliverable_ok`; for those the
    QC data they did carry decides, and only when there is no QC data at all do
    we fall back to `ok`.  Reading a pre-split receipt as deliverable just
    because `ok=true` would re-create exactly the false green this split exists
    to kill.

    Single source: pool_igniter.receipt_deliverable delegates here.
    """
    return receipt_deliverable_state(row) == "yes"


def receipt_deliverable_state(row):
    """'yes' | 'no' | 'unknown' for one ignition receipt (T229).

    `unknown` is a real state, not a polite yes: a render receipt written before
    T226 carries no QC verdict, so the ledger cannot say whether its frames are
    usable.  Counting it as supply is how the 10:15:56 batch (5 of 6 frames
    magenta placeholder) would keep reading as "effective".  Counting it as a
    failure would be the opposite lie -- the run did exit cleanly.
    """
    if not row.get("ok"):
        return "no"
    run = row.get("runner") or {}
    q = row.get("frame_quality") or run.get("frame_quality")
    if isinstance(q, dict) and q.get("qc_verdict"):
        return "yes" if q["qc_verdict"] == "FRAME-QC-PASS" else "no"
    if run.get("kind") == "bake_spike":
        # M180: the bake's deliverable is the lightmap atlas, and since
        # T-20261109-11 it has its own verdict.  A black atlas once read
        # "deliverable" purely because bake_ok was true; receipts written
        # before that verdict existed cannot prove either way, so they are
        # unknown rather than supply.
        lq = row.get("lightmap_quality") or run.get("lightmap_quality")
        if isinstance(lq, dict) and lq.get("qc_verdict"):
            return "yes" if lq["qc_verdict"] == "LIGHTMAP-QC-PASS" else "no"
        return "unknown"
    if run.get("kind") == "render_spike":
        # QC is a render-only concept; without its verdict nothing certifies
        # the frames.
        return "unknown"
    # Kinds that have no QC concept: the writer's own deliverability flag is
    # the strongest claim available.  Checked *after* the kind rules because
    # receipts written before a kind had QC carry `deliverable_ok=true` as a
    # plain mirror of `ok`; trusting that first let a clean run re-enter
    # supply as if its product had been inspected (11-09 17:58/19:10 bakes).
    for holder in (row, run):
        if isinstance(holder.get("deliverable_ok"), bool):
            return "yes" if holder["deliverable_ok"] else "no"
    return "yes"


def ignition_stats(log_path=None, hours=24, now=None):
    """D-20261108-09: the pool's effective face = runs that really happened.

    `dispatchable` counts cards that *look* runnable (named + not locked + not
    in flight); it never consulted the lane gate, so 4 cards read dispatchable
    for days while effective stayed 0 and the GPU was busy the whole time.
    Receipts written by pool_igniter.py are the only evidence that a card
    actually ran, so this is what "effective" must be read from.
    Unreadable lines are skipped, never guessed; a missing log is 0 receipts.
    """
    log_path = log_path or IGNITION_LOG
    now = now or datetime.datetime.now()
    cutoff = now - datetime.timedelta(hours=hours)
    stats = {"total": 0, "ok": 0, "ok_24h": 0, "last_ts": None,
             "cards_ok_24h": [], "unreadable": 0,
             # T229: "ran", "produced something usable" and "cannot be proven either
             # way" are three distinct counts.
             "deliv": 0, "deliv_24h": 0, "cards_deliv_24h": [],
             "unknown": 0, "unknown_24h": 0}
    if not os.path.exists(log_path):
        return stats
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                stats["unreadable"] += 1
                continue
            stats["total"] += 1
            if not row.get("ok"):
                continue
            stats["ok"] += 1
            state = receipt_deliverable_state(row)
            deliverable = state == "yes"
            if deliverable:
                stats["deliv"] += 1
            elif state == "unknown":
                stats["unknown"] += 1
            stats["last_ts"] = row.get("ts")
            try:
                ts = datetime.datetime.strptime(str(row.get("ts")),
                                                "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if ts >= cutoff:
                stats["ok_24h"] += 1
                bid = str(row.get("batch_id") or "?")
                if bid not in stats["cards_ok_24h"]:
                    stats["cards_ok_24h"].append(bid)
                if deliverable and bid not in stats["cards_deliv_24h"]:
                    stats["cards_deliv_24h"].append(bid)
                if deliverable:
                    stats["deliv_24h"] += 1
                elif state == "unknown":
                    stats["unknown_24h"] += 1
    return stats


def build_review(rows, today=None, claims=None, refusals=None,
                 unknown_converge=None):
    """Assemble the M66 monthly review snapshot from stocked cards.

    Pure (no I/O, no clock unless today is omitted) so selftest can pin dates.
    `today` is a date or None. `claims` is the per-card claim history returned
    by load_claims(); when omitted the review sees zero claims, which means "no
    evidence of a stuck card", not "fine". The dry-run age is the gap between
    DRY_RUN_END and today; expiring alone no longer blocks activation
    (D-20261108-03) - only cards with >= J5_LOCK_STREAK unignited claims do.
    """
    import datetime
    if today is None:
        today = datetime.date.today()
    problems, info = check_rows(rows)
    lanes, statuses, modes = {}, {}, {}
    named = 0
    for row in rows:
        lanes[str(row.get("lane", "?"))] = \
            lanes.get(str(row.get("lane", "?")), 0) + 1
        st = str(row.get("status", "?")).upper() or "?"
        statuses[st] = statuses.get(st, 0) + 1
        md = str(row.get("dispatch_mode", "?") or "?")
        modes[md] = modes.get(md, 0) + 1
        if is_named_consumer(row.get("consumer_plan")):
            named += 1
    in_flight = sum(statuses.get(s, 0) for s in IN_FLIGHT_STATUSES)
    lock = j5_lock_state(rows, claims, today, refusals)
    overdue = lock["dry_run_overdue"]
    # M161: "right now, how many cards could really be dispatched" = someone is
    # named to take it AND it is neither locked nor already in flight.
    dispatchable, placeholder = [], []
    declared_true, declared_false, named_mismatch, bad_type = [], [], [], []
    for row in rows:
        bid = str(row.get("batch_id") or "")
        dec = declared_named(row)
        named_type = declared_named_type(row)
        if named_type not in (None, "bool") and bid:
            bad_type.append("%s:%s" % (bid, named_type))
        if dec is True:
            declared_true.append(bid)
        elif dec is False:
            declared_false.append(bid)
        if dec is not None and bid and \
                dec is not is_named_consumer(row.get("consumer_plan")):
            # The card's own label and the text disagree - the label is what
            # dispatch reads, so name it instead of silently trusting either.
            named_mismatch.append(bid)
        if not bid or not str(row.get("consumer_plan", "") or "").strip():
            continue
        if not is_named_consumer(row.get("consumer_plan")):
            placeholder.append(bid)
            continue
        st = str(row.get("status", "?")).upper() or "?"
        if st in IN_FLIGHT_STATUSES or lock["per_card"].get(bid, {}).get("locked"):
            continue
        dispatchable.append(bid)
    findings = []
    if overdue and not lock["locked_cards"]:
        findings.append(
            "J5 dry-run window ended %s (%d days ago) but expiry alone does not "
            "lock (D-20261108-03); no card carries >=%d unignited claims yet, so "
            "the pool is open subject to the per-card claim rule"
            % (DRY_RUN_END, lock["dry_run_age_days"], J5_LOCK_STREAK))
    if lock["locked_cards"]:
        findings.append(
            "J5 LOCKED %d card(s) [%s]: dry-run window expired %d days ago and "
            "each carries >=%d claims while still STOCKED"
            % (len(lock["locked_cards"]), ", ".join(lock["locked_cards"]),
               lock["dry_run_age_days"], J5_LOCK_STREAK))
    if named < len(rows):
        findings.append(
            "consumer_plan named on %d/%d cards (备货律指名)" % (named, len(rows)))
    if placeholder:
        findings.append(
            "consumer_plan names nobody on %d card(s) [%s]: placeholder text "
            "keeps J4 non-empty but there is no consumer to take the work"
            % (len(placeholder), ", ".join(placeholder)))
    if named_mismatch:
        findings.append(
            "CONSUMER-NAMED-MISMATCH on %d card(s) [%s]: the card's own "
            "consumer_named flag contradicts its consumer_plan text; fix the "
            "label before dispatch reads it" % (len(named_mismatch),
                                                ", ".join(named_mismatch)))
    if bad_type:
        findings.append(
            "CONSUMER-NAMED-BAD-TYPE on %d card(s) [%s]: consumer_named is not "
            "a boolean, so it reads as 'never declared' and the mismatch check "
            "skips it - a mislabel looks exactly like no label (M164)"
            % (len(bad_type), ", ".join(bad_type)))
    if dispatchable:
        findings.append(
            "dispatchable now = %d card(s) [%s] (named + not locked + not "
            "in flight)" % (len(dispatchable), ", ".join(dispatchable)))
    if problems:
        findings.append("validate reported %d problem(s)" % len(problems))
    ign = ignition_stats()
    if dispatchable and not ign["ok_24h"]:
        findings.append(
            "effective=0 while %d card(s) read dispatchable [%s]: dispatchable "
            "ignores the lane gate; receipts in %s are the real supply face"
            % (len(dispatchable), ", ".join(dispatchable),
               os.path.relpath(IGNITION_LOG, ROOT)))
    if ign["ok_24h"] > ign["deliv_24h"]:
        findings.append(
            "%d ignition(s) in %dh ran clean but produced no usable product "
            "(ok=%d deliverable=%d): read effective_24h, not ignited_24h"
            % (ign["ok_24h"] - ign["deliv_24h"], 24,
               ign["ok_24h"], ign["deliv_24h"]))
    if ign["unknown_24h"]:
        findings.append(
            "%d clean run(s) in %dh cannot be certified either way (no QC "
            "verdict in receipt): counted as neither supply nor failure; "
            "read `pool_igniter status` frame_quality for the recomputed numbers"
            % (ign["unknown_24h"], 24))
    # T230: an unknown receipt must be able to leave that state, or it turns
    # into quiet garbage.  The sidecar holds what today's disk can still prove.
    if unknown_converge:
        if unknown_converge.get("known_bad"):
            findings.append(
                "KNOWN-BAD %d receipt(s) [%s]: unprovable from disk and older "
                "than the %sd policy, so the pool stops implying they might "
                "still be good"
                % (unknown_converge["known_bad"],
                   ", ".join(unknown_converge.get("known_bad_items") or []),
                   unknown_converge.get("age_policy_days")))
        if unknown_converge.get("computable_fail"):
            findings.append(
                "%d unknown receipt(s) are now provably unfit "
                "(recomputed QC failed): they were never supply, and the "
                "receipts themselves are left untouched"
                % unknown_converge["computable_fail"])
    return {
        "cards": len(rows),
        "lanes": lanes,
        "statuses": statuses,
        "dispatch_modes": modes,
        "consumer_plan_named": named,
        "consumer_plan_placeholder": placeholder,
        "consumer_named_declared_true": declared_true,
        "consumer_named_declared_false": declared_false,
        "consumer_named_mismatch": named_mismatch,
        "consumer_named_bad_type": bad_type,
        "dispatchable": len(dispatchable),
        "dispatchable_cards": dispatchable,
        "ignited_24h": ign["ok_24h"],
        "ignited_24h_cards": ign["cards_ok_24h"],
        # T229: supply that can actually be consumed. `ignited_24h` only says
        # a runner exited cleanly; the 11-09 10:15 batch did that and still had
        # 5 of 6 judgment frames full of magenta placeholder.
        "effective_24h": ign["deliv_24h"],
        "effective_24h_cards": ign["cards_deliv_24h"],
        "uncertified_24h": ign["unknown_24h"],
        "ignitions_total": ign["ok"],
        "deliverable_total": ign["deliv"],
        "uncertified_total": ign["unknown"],
        "unknown_converge": unknown_converge,
        "last_ignition_ts": ign["last_ts"],
        "in_flight": in_flight,
        "dry_run_end": DRY_RUN_END,
        "dry_run_age_days": lock["dry_run_age_days"],
        "dry_run_overdue": overdue,
        "j5_rule": lock["rule"],
        "j5_claims_total": lock["claims_total"],
        "j5_refusals_total": lock["refusals_total"],
        "j5_locked_cards": lock["locked_cards"],
        "j5_per_card": lock["per_card"],
        "j5_blocks_activation": lock["blocks_activation"],
        "problems": problems,
        "info": info,
        "findings": findings,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def cmd_review(path, as_json=False, write=True):
    if not os.path.exists(path):
        print("FAIL registry not found: %s" % path)
        return 1
    rows = load_rows(path)
    claims, refused = load_claims(with_refusals=True)
    review = build_review(rows, claims=claims, refusals=refused,
                          unknown_converge=unknown_converge_reading())
    review["registry"] = os.path.relpath(path, ROOT)
    review["claims_source"] = os.path.relpath(CLAIM_LOG, ROOT)
    print("REVIEW cards=%d lanes=%d named=%d placeholder=%d dispatchable=%d "
          "consumer_named=%d/%d valid=%d mismatch=%d badtype=%d "
          "in_flight=%d dry_run_end=%s overdue=%s claims=%d refused=%d "
          "locked=%d j5_blocks_activation=%s"
          % (review["cards"], len(review["lanes"]),
             review["consumer_plan_named"],
             len(review["consumer_plan_placeholder"]),
             review["dispatchable"],
             len(review["consumer_named_declared_true"]),
             # M165: the denominator is the card count, not "cards that happen
             # to declare a bool" - the old N/M silently shrank the M the
             # moment one card wrote the flag as a string, so 4/6 became 3/5
             # and the same pool looked differently sized. valid/badtype sit
             # beside it instead of moving the denominator.
             review["cards"],
             len(review["consumer_named_declared_true"])
             + len(review["consumer_named_declared_false"]),
             len(review["consumer_named_mismatch"]),
             len(review["consumer_named_bad_type"]),
             review["in_flight"],
             review["dry_run_end"], review["dry_run_overdue"],
             review["j5_claims_total"], review["j5_refusals_total"],
             len(review["j5_locked_cards"]),
             review["j5_blocks_activation"]))
    # D-20261108-09: dispatchable is a look, ignition is a fact. Print both.
    print("EFFECTIVE ignited_24h=%d deliverable_24h=%d uncertified_24h=%d "
          "cards=%s total_ok=%d total_deliverable=%d total_uncertified=%d last=%s"
          % (review["ignited_24h"], review["effective_24h"],
             review["uncertified_24h"],
             ",".join(review["effective_24h_cards"]) or "-",
             review["ignitions_total"], review["deliverable_total"],
             review["uncertified_total"],
             review["last_ignition_ts"] or "-"))
    uc = review.get("unknown_converge")
    if uc:
        print("UNKNOWN-CONVERGE unknown=%s computable=%s(pass=%s fail=%s) "
              "unprovable=%s known_bad=%s as_of=%s policy=%sd"
              % (uc["unknown"], uc["computable"], uc["computable_pass"],
                 uc["computable_fail"], uc["unprovable"], uc["known_bad"],
                 uc["as_of"], uc["age_policy_days"]))
    else:
        print("UNKNOWN-CONVERGE n/a(no-sidecar: run "
              "`pool_igniter unknown-converge`)")
    for f in review["findings"]:
        print("FINDING " + f)
    if as_json:
        print(json.dumps(review, ensure_ascii=False, indent=2))
    review_exit = 1 if review["problems"] else 0
    print("VERDICT=%s" % ("POOL-REVIEW-FAIL" if review_exit
                          else "POOL-REVIEW-GREEN"))
    if write:
        stamp = review["generated_at"].replace("-", "").replace(":", "") \
            .replace(" ", "-")
        out = os.path.join(ROOT, "state", "batch-pool-review-%s.json" % stamp)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(review, f, ensure_ascii=False, indent=2)
        print("evidence=%s" % os.path.relpath(out, ROOT))
    return review_exit


def _fixture(base=None, **over):
    row = {
        "batch_id": "B-T-01", "lane": CITY3D_LANE, "category":
        CITY3D_CATEGORY, "consumer_plan": "cp", "order_ref": "O-x",
        "workload": "w", "resource_profile": "r", "dispatch_mode":
        "ON_DEMAND", "status": "STOCKED", "acceptance": "a",
        "stocked_at": "t",
    }
    if base:
        row.update(base)
    row.update(over)
    return row


def cmd_selftest():
    # J1: missing field detected / valid fixture passes the field check
    bad = check_rows([_fixture(workload="")])[0]
    assert any("J1" in p and "workload" in p for p in bad), "J1 miss"
    # J2: one City3D 3d card fails, two pass
    assert any("J2" in p for p in check_rows([_fixture()])[0]), "J2 <2 miss"
    ok = [_fixture(), _fixture(base={"batch_id": "B-T-02"})]
    assert not any("J2" in p for p in check_rows(ok)[0]), "J2 >=2 false fail"
    # J3: duplicate id detected
    dup = check_rows([_fixture(), _fixture()])[0]
    assert any("J3" in p for p in dup), "J3 dup miss"
    # J4: empty consumer_plan detected
    bad_cp = check_rows([_fixture(consumer_plan="  ")])[0]
    assert any("J4" in p for p in bad_cp), "J4 miss"
    # J5: in-flight status detected, STOCKED passes
    bad_st = check_rows([_fixture(status="IN_FLIGHT")])[0]
    assert any("J5" in p for p in bad_st), "J5 miss"
    assert not any("J5" in p for p in check_rows([_fixture()])[0]), \
        "J5 false positive"
    # J6: no data_deps -> unaffected; existing dep passes; missing dep
    # red-gates; non-list type rejected
    pair = [_fixture(), _fixture(base={"batch_id": "B-T-02"})]
    assert not any("J6" in p for p in check_rows(pair)[0]), \
        "J6 false positive (no data_deps)"
    dep_ok = check_rows([_fixture(), _fixture(base={
        "batch_id": "B-T-02", "data_deps": ["Tools/batch_pool.py"]})])[0]
    assert not any("J6" in p for p in dep_ok), "J6 false positive (exists)"
    dep_bad = check_rows([_fixture(), _fixture(base={
        "batch_id": "B-T-02",
        "data_deps": ["state/no-such-dep-dir-xyz/"]})])[0]
    assert any("J6" in p and "DATA_NOT_LOCAL" in p for p in dep_bad), \
        "J6 miss (DATA_NOT_LOCAL)"
    dep_typ = check_rows([_fixture(), _fixture(base={
        "batch_id": "B-T-02", "data_deps": "Tools/batch_pool.py"})])[0]
    assert any("J6" in p and "list" in p for p in dep_typ), "J6 type miss"
    # determinism: same rows -> same problems
    r1 = check_rows([_fixture(), _fixture()])[0]
    r2 = check_rows([_fixture(), _fixture()])[0]
    assert r1 == r2, "determinism"
    # gate (BC-P-144): lane mapping is explicit and unknown categories are
    # refused rather than guessed
    import dispatch_precheck
    assert dispatch_precheck.lane_for_category(CITY3D_CATEGORY) == "gpu", \
        "gate lane map (gpu)"
    assert dispatch_precheck.lane_for_category("cpu_batch") == "cpu", \
        "gate lane map (cpu)"
    assert dispatch_precheck.lane_for_category("no_such_category") is None, \
        "gate unknown category must not be guessed"
    # review (M66): stock snapshot + dry-run age are pinned by fixtures
    import datetime
    pair = [_fixture(), _fixture(base={"batch_id": "B-T-02"})]
    rev = build_review(pair, today=datetime.date(2026, 10, 20))
    assert rev["cards"] == 2 and rev["consumer_plan_named"] == 2, "review counts"
    assert rev["in_flight"] == 0 and rev["statuses"].get("STOCKED") == 2, \
        "review status tally"
    assert rev["dry_run_age_days"] == 15 and rev["dry_run_overdue"], \
        "review dry-run age"
    # D-20261108-03: expiry alone is a reminder, NOT a lock.  This is the
    # regression the revision exists to prevent.
    assert not rev["j5_blocks_activation"], \
        "expiry alone must not lock the pool (old J5 rule regression)"
    assert rev["j5_locked_cards"] == [], "no claims -> no locked cards"
    assert any("J5 dry-run window ended" in f for f in rev["findings"]), \
        "review finding text (expiry reminder)"
    assert any("expiry alone does not lock" in f for f in rev["findings"]), \
        "review must state the new rule explicitly"
    # one claim is an attempt, two are a stuck card
    one = build_review(pair, today=datetime.date(2026, 10, 20),
                       claims={"B-T-01": [{"ts": "t1"}]})
    assert not one["j5_blocks_activation"], "one claim must not lock"
    two = build_review(pair, today=datetime.date(2026, 10, 20),
                       claims={"B-T-01": [{"ts": "t1"}, {"ts": "t2"}]})
    assert two["j5_blocks_activation"], "two unignited claims must lock"
    assert two["j5_locked_cards"] == ["B-T-01"], "locked card named"
    assert two["j5_per_card"]["B-T-01"]["claims"] == 2, "claim count kept"
    assert any("J5 LOCKED" in f for f in two["findings"]), "lock finding text"
    # claims before the window ends are not evidence of a stuck card
    pre = build_review(pair, today=datetime.date(2026, 10, 1),
                       claims={"B-T-01": [{"ts": "t1"}, {"ts": "t2"}]})
    assert not pre["j5_blocks_activation"], "pre-end claims must not lock"
    # a card that left STOCKED is not locked, even with two cleared claims
    moved = build_review(
        [_fixture(), _fixture(base={"batch_id": "B-T-02", "status": "RUNNING"})],
        today=datetime.date(2026, 10, 20),
        claims={"B-T-02": [{"ts": "t1"}, {"ts": "t2"}]})
    assert not moved["j5_blocks_activation"], \
        "a card in a non-STOCKED status is not locked"
    assert not moved["j5_per_card"]["B-T-02"]["locked"], \
        "in-flight card never locked"
    # refusals are visible but never lock a card (gate said no -> nothing taken)
    refused_rev = build_review(
        pair, today=datetime.date(2026, 10, 20),
        claims={}, refusals={"B-T-01": [{"ts": "t1"}, {"ts": "t2"},
                                        {"ts": "t3"}]})
    assert not refused_rev["j5_blocks_activation"], \
        "refused attempts must not lock (nothing was claimed)"
    assert refused_rev["j5_per_card"]["B-T-01"]["refused"] == 3, \
        "refusals still reported per card"
    assert refused_rev["j5_per_card"]["B-T-01"]["claims"] == 0, \
        "refusals are not claims"
    # claims are attributed per card, never shared between cards
    per = build_review(
        pair, today=datetime.date(2026, 10, 20),
        claims={"B-T-01": [{"ts": "t1"}], "B-T-02": [{"ts": "t2"}]})
    assert not per["j5_blocks_activation"], "claims must not be pooled together"
    assert per["j5_per_card"]["B-T-01"]["claims"] == 1, "per-card claim count"
    # claim log parsing: batch_id field and the legacy consumer form both count,
    # unreadable lines are skipped and never invented
    import tempfile
    tmp_log = os.path.join(tempfile.mkdtemp(), "gate.jsonl")
    with open(tmp_log, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"batch_id": "B-T-01", "gate_exit": 0,
                            "verdict": "OPEN", "ts": "t1"}) + "\n")
        f.write(json.dumps({"consumer": "batch-pool:B-T-02", "gate_exit": 0,
                            "verdict": "OPEN", "ts": "t2"}) + "\n")
        f.write(json.dumps({"consumer": "batch-pool:B-T-02", "gate_exit": 2,
                            "verdict": "CLOSED", "ts": "t3"}) + "\n")
        f.write(json.dumps({"consumer": "gpu-dispatch-pointer", "gate_exit": 2,
                            "ts": "t4"}) + "\n")
        f.write("not json\n")
    parsed = load_claims(tmp_log)
    assert sorted(parsed) == ["B-T-01", "B-T-02"], \
        "claims attributed only to named cards (legacy consumer form included)"
    assert len(parsed["B-T-01"]) == 1 and len(parsed["B-T-02"]) == 1, \
        "one cleared claim per card; the CLOSED line is a refusal"
    parsed_c, parsed_r = load_claims(tmp_log, with_refusals=True)
    assert len(parsed_r.get("B-T-02", [])) == 1, \
        "refused attempt kept separate from claims"
    assert load_claims(os.path.join(os.path.dirname(tmp_log), "absent.jsonl")) == {}, \
        "missing claim log is empty, not an error"
    early = build_review(pair, today=datetime.date(2026, 10, 1))
    assert not early["dry_run_overdue"] and not early["j5_blocks_activation"], \
        "review pre-end must not flag blocker"
    flying = build_review(
        [_fixture(), _fixture(base={"batch_id": "B-T-02", "status": "RUNNING"})],
        today=datetime.date(2026, 10, 20))
    assert flying["in_flight"] == 1 and not flying["j5_blocks_activation"], \
        "review must not claim J5 blocks when a card is already in-flight"
    assert any("J5" in p for p in flying["problems"]), "review carries validate"
    r1 = build_review(pair, today=datetime.date(2026, 10, 20))
    r2 = build_review(pair, today=datetime.date(2026, 10, 20))
    r1.pop("generated_at")
    r2.pop("generated_at")
    assert r1 == r2, "review determinism"
    c1 = build_review(pair, today=datetime.date(2026, 10, 20),
                      claims={"B-T-01": [{"ts": "t1"}, {"ts": "t2"}]})
    c2 = build_review(pair, today=datetime.date(2026, 10, 20),
                      claims={"B-T-01": [{"ts": "t1"}, {"ts": "t2"}]})
    c1.pop("generated_at")
    c2.pop("generated_at")
    assert c1 == c2, "claim-based lock determinism"
    # M161: "dispatchable" must mean somebody is actually named to take it -
    # passing J4's non-empty check is not the same thing.
    assert is_named_consumer("cp") and not is_named_consumer("  "), \
        "named basic (text vs blank)"
    assert not is_named_consumer("消费对接随协作市场表建面"), \
        "placeholder form is not a named consumer"
    assert not is_named_consumer("待对接·协作市场表"), "pending form no"
    ph = build_review(
        [_fixture(), _fixture(base={"batch_id": "B-T-02",
                                    "consumer_plan": "随协作市场表建面"})],
        today=datetime.date(2026, 10, 20))
    assert ph["consumer_plan_named"] == 1, "placeholder not counted as named"
    assert ph["consumer_plan_placeholder"] == ["B-T-02"], "placeholder listed"
    assert ph["dispatchable"] == 1 and ph["dispatchable_cards"] == ["B-T-01"], \
        "dispatchable excludes placeholder"
    assert any("names nobody" in f for f in ph["findings"]), \
        "placeholder finding text"
    locked_disp = build_review(pair, today=datetime.date(2026, 10, 20),
                               claims={"B-T-01": [{"ts": "t1"}, {"ts": "t2"}]})
    assert locked_disp["dispatchable"] == 1 and \
        locked_disp["dispatchable_cards"] == ["B-T-02"], \
        "a locked card is not dispatchable"
    busy = build_review(
        [_fixture(), _fixture(base={"batch_id": "B-T-02", "status": "RUNNING"})],
        today=datetime.date(2026, 10, 20))
    assert busy["dispatchable"] == 1 and busy["dispatchable_cards"] == ["B-T-01"], \
        "an in-flight card is not dispatchable"
    # M162: a card may declare its own consumer_named flag. Declared flag and
    # plan text must agree; disagreement is named, never silently resolved.
    assert declared_named(_fixture()) is None, "no flag -> None (not False)"
    assert declared_named(_fixture(consumer_named=False)) is False, \
        "explicit false is readable"
    assert declared_named(_fixture(consumer_named="false")) is None, \
        "a string is not a bool declaration"
    agree = build_review(
        [_fixture(consumer_named=True),
         _fixture(base={"batch_id": "B-T-02",
                        "consumer_plan": "随协作市场表建面",
                        "consumer_named": False})],
        today=datetime.date(2026, 10, 20))
    assert agree["consumer_named_mismatch"] == [], \
        "agreeing declarations raise nothing"
    assert agree["consumer_named_declared_true"] == ["B-T-01"], "true listed"
    assert agree["consumer_named_declared_false"] == ["B-T-02"], "false listed"
    mis = build_review(
        [_fixture(consumer_named=False),
         _fixture(base={"batch_id": "B-T-02",
                        "consumer_plan": "随协作市场表建面",
                        "consumer_named": True})],
        today=datetime.date(2026, 10, 20))
    assert mis["consumer_named_mismatch"] == ["B-T-01", "B-T-02"], \
        "both contradiction directions are named"
    assert any("CONSUMER-NAMED-MISMATCH" in f for f in mis["findings"]), \
        "mismatch finding text"
    assert not any("CONSUMER-NAMED-MISMATCH" in f for f in agree["findings"]), \
        "no mismatch finding when declarations agree"
    # M164: a label that is not a real bool must be named, not folded into
    # "never declared" where it looks identical to no label at all.
    assert declared_named_type(_fixture(consumer_named="false")) == "string", \
        "string label is typed as string"
    assert declared_named_type(_fixture(consumer_named=0)) == "number", \
        "numeric label is typed as number"
    typed = build_review(
        [_fixture(consumer_named="false"),
         _fixture(base={"batch_id": "B-T-02", "consumer_named": 0})],
        today=datetime.date(2026, 10, 20))
    assert typed["consumer_named_declared_true"] == [] and \
        typed["consumer_named_declared_false"] == [], \
        "bad-typed labels do not land in declared true/false"
    assert typed["consumer_named_bad_type"] == ["B-T-01:string", "B-T-02:number"], \
        "each bad-typed card is named with its type"
    assert any("CONSUMER-NAMED-BAD-TYPE" in f for f in typed["findings"]), \
        "bad-type finding text"
    assert not any("CONSUMER-NAMED-BAD-TYPE" in f for f in agree["findings"]), \
        "boolean labels raise no bad-type finding"
    # T229: "ran clean" and "produced something usable" are two counts.
    assert receipt_deliverable({"ok": True, "deliverable_ok": True}) is True
    assert receipt_deliverable({"ok": True, "deliverable_ok": False}) is False
    assert receipt_deliverable(
        {"ok": True, "runner": {"deliverable_ok": False}}) is False, \
        "runner-level flag decides when the top level is absent"
    assert receipt_deliverable(
        {"ok": True,
         "frame_quality": {"qc_verdict": "FRAME-QC-INSUFFICIENT-PLACEHOLDER"}}
    ) is False, "pre-split receipt with failed QC is not deliverable"
    assert receipt_deliverable(
        {"ok": True, "frame_quality": {"qc_verdict": "FRAME-QC-PASS"}}) is True
    assert receipt_deliverable({"ok": True}) is True, \
        "no runner kind at all -> ok decides (not a card with a quality gate)"
    assert receipt_deliverable_state(
        {"ok": True, "runner": {"kind": "bake_spike",
                                "lightmap_quality": {"qc_verdict": "LIGHTMAP-QC-INSUFFICIENT"}}}
    ) == "no", "M180: a dark atlas is not a deliverable"
    assert receipt_deliverable_state(
        {"ok": True, "runner": {"kind": "bake_spike",
                                "lightmap_quality": {"qc_verdict": "LIGHTMAP-QC-PASS"}}}
    ) == "yes", "M180: a lit atlas certifies the bake"
    assert receipt_deliverable_state(
        {"ok": True, "runner": {"kind": "bake_spike"}}) == "unknown", \
        "M180: a bake receipt written before lightmap QC proves nothing"
    assert receipt_deliverable_state(
        {"ok": True, "runner": {"kind": "render_spike"}}) == "unknown", \
        "pre-split render receipt is unknown, not supply"
    assert receipt_deliverable({"ok": True,
                                "runner": {"kind": "render_spike"}}) is False, \
        "unknown is never counted as deliverable"
    assert receipt_deliverable({"ok": False, "deliverable_ok": True}) is False, \
        "a failed run is never deliverable"
    assert receipt_deliverable_state(
        {"ok": True, "deliverable_ok": True,
         "runner": {"kind": "bake_spike"}}) == "unknown", \
        "M181: a bare deliverable_ok mirrors ok and cannot certify an atlas"
    assert receipt_deliverable_state(
        {"ok": True, "deliverable_ok": True,
         "runner": {"kind": "render_spike"}}) == "unknown", \
        "M181: same for a render receipt without frame QC"
    assert receipt_deliverable_state(
        {"ok": True, "deliverable_ok": True,
         "runner": {"kind": "other_job"}}) == "yes", \
        "kinds without a quality concept still trust the writer flag"
    # T230: an unknown receipt must be able to leave `unknown`; the sidecar is
    # where the published verdict lives, and both faces read the same numbers.
    uc = build_review(pair, today=datetime.date(2026, 10, 20),
                      unknown_converge={
                          "unknown": 3, "computable": 1, "computable_pass": 0,
                          "computable_fail": 1, "unprovable": 2,
                          "known_bad": 2, "age_policy_days": 7,
                          "known_bad_items": ["B-CITY3D-01@2026-11-01 00:00:00"],
                          "as_of": "2026-11-10 01:00:00"})
    assert any("KNOWN-BAD" in f for f in uc["findings"]), \
        "T230: stale unprovable receipts must be named KNOWN-BAD"
    assert any("provably unfit" in f for f in uc["findings"]), \
        "T230: a recomputed QC failure is named, not left as unknown"
    assert uc["unknown_converge"]["known_bad"] == 2, \
        "T230: sidecar numbers are published as-is"
    assert build_review(pair, today=datetime.date(2026, 10, 20)
                        )["unknown_converge"] is None and \
        unknown_converge_reading(os.path.join(ROOT, "state", "no-such-sidecar.json")) is None, \
        "T230: no sidecar reads as not-measured, never as an empty set"
    print("selftest PASS (J1-J7 + gate lane map + review + J5 claim rule "
          "+ claim log + determinism + dispatchable + consumer_named "
          "+ receipt deliverable tri-state + bake atlas tri-state "
          "+ QC-before-flag ordering + unknown convergence, %d checks)" % 78)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_l = sub.add_parser("list", help="print all pool cards")
    p_l.add_argument("--registry", default=REGISTRY)
    p_v = sub.add_parser("validate", help="machine-check the stocking sheet")
    p_v.add_argument("--registry", default=REGISTRY)
    p_g = sub.add_parser("gate", help="pre-dispatch lane gate for one card")
    p_g.add_argument("--batch-id", required=True)
    p_g.add_argument("--registry", default=REGISTRY)
    p_g.add_argument("--consumer", help="who is dispatching (audit field)")
    p_g.add_argument("--exempt", help="exemption reason (artifact-arrival paths)")
    p_g.add_argument("--json", action="store_true")
    p_r = sub.add_parser("review", help="M66 monthly dry-run review snapshot")
    p_r.add_argument("--registry", default=REGISTRY)
    p_r.add_argument("--json", action="store_true")
    p_r.add_argument("--no-write", action="store_true",
                     help="print only, skip state/batch-pool-review-*.json")
    sub.add_parser("selftest", help="offline fixture checks")
    args = ap.parse_args()
    if args.cmd == "list":
        return cmd_list(args.registry)
    if args.cmd == "validate":
        return cmd_validate(args.registry)
    if args.cmd == "gate":
        return cmd_gate(args.registry, args.batch_id, args.consumer,
                        args.exempt, args.json)
    if args.cmd == "review":
        return cmd_review(args.registry, args.json, not args.no_write)
    return cmd_selftest()


if __name__ == "__main__":
    sys.exit(main())
