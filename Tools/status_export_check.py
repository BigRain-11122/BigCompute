#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""status_export_check.py -- CEO-facing status export mechanical gate (tech T143).

Mandate 产品优先律 clause 5 requires docs/status-export.json to carry three
lines (current activity / latest artifact / next milestone) and to be
refreshed whenever reality moves. Nothing measured that file: a stale or
half-written export still *renders* as a live face while the company is
actually idle, and a GBK-mangled write (the rounds.log T24 precedent) would
ship to the CEO as unreadable text with the JSON still parsing fine.

This gate answers four questions mechanically, read-only, no network:
  1. bytes decode as strict UTF-8 and parse as JSON (no mojibake/truncation)
  2. the three required keys exist, are non-empty strings, and carry no
     U+FFFD replacement characters
  3. updated_at parses and is within MAX_AGE_H hours of now
  4. the export is not behind the ledger: when rounds.log holds a round
     newer than updated_at by more than LAG_H hours, the face was not
     refreshed while the company did work -> STALE. Same MISSED shape that
     T129 found for HQ-FEEDBACK.md, applied to the CEO-facing export.

Verdicts: EXPORT-GREEN / EXPORT-STALE / EXPORT-MISSING / EXPORT-BAD.

T144 adds `compose`: the three lines are generated mechanically from the
ledgers (last round's queue segment, newest artifact mtime, first open
queue row) instead of being hand-written every round. A hand-written face
drifts silently; a composed one cannot claim work the ledgers do not show.
`docs/status-export-overrides.json` is an append-only supplement layer --
it may add text after a generated line, never replace it.

T169 adds the write-side half of the same defect: the read gate could see
a BOM but nobody asserted the *writer* produced none. An editor save or a
PowerShell `Out-File` aimed at this file re-introduces the BOM and the face
breaks again with no warning at the moment of writing. `compose --write`
now verifies the bytes it just put on disk (BOM-free, byte-identical,
strict-UTF-8 parseable) and refuses the write loudly when they are not;
a BOM'd overrides file is reported instead of being silently dropped.

Commands:
    python Tools/status_export_check.py check [--now "YYYY-MM-DD HH:MM"]
    python Tools/status_export_check.py compose [--write] [--now "..."]
    python Tools/status_export_check.py selftest
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_PATH = os.path.join("docs", "status-export.json")
ROUNDS_PATH = os.path.join("state", "rounds.log")

REQUIRED_KEYS = ("current_activity", "latest_artifact", "next_milestone")
MAX_AGE_H = 48.0
LAG_H = 2.0

TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2})(?::(\d{2}))?")
PLACEHOLDER_TOKENS = ("tbd", "todo", "n/a", "...", "-")
# T169: a UTF-8 BOM makes strict `json.load(open(..., encoding='utf-8'))`
# raise, so a BOM'd export is broken for every consumer even though the
# text looks fine in an editor.
BOM = b"\xef\xbb\xbf"


def parse_ts(text):
    """Parse 'YYYY-MM-DD HH:MM' and the rounds.log 'HHx' hour mask.

    rounds.log writes hours like '06x' (the round log uses an hour mask
    rather than minutes). The mask is read as the *earliest* time the
    round could have started, so a masked hour never makes the export
    look fresher than it is.
    """
    if not isinstance(text, str):
        return None
    m = TS_RE.match(text.strip())
    if not m:
        return None
    y, mo, d, h = (int(m.group(i)) for i in range(1, 5))
    mi = int(m.group(5)) if m.group(5) else 0
    try:
        return datetime.datetime(y, mo, d, h, mi)
    except ValueError:
        return None


def latest_round_ts(text):
    """Newest timestamp in state/rounds.log (None when the log is empty)."""
    newest = None
    for line in (text or "").splitlines():
        ts = parse_ts(line)
        if ts and (newest is None or ts > newest):
            newest = ts
    return newest


def _bad_value(key, value):
    if not isinstance(value, str) or not value.strip():
        return "%s empty/non-string" % key
    if "\ufffd" in value:
        return "%s contains U+FFFD (mojibake)" % key
    if value.strip().lower() in PLACEHOLDER_TOKENS:
        return "%s is a placeholder (%r)" % (key, value.strip())
    return None


def export_verdict(raw_bytes, now=None, rounds_text="", exists=True):
    """Pure: verdict + detail for the export face.

    raw_bytes is None only when exists is False. Every failure mode gets
    its own verdict so a reader can tell "nobody refreshed it" from
    "somebody wrote garbage".
    """
    now = now or datetime.datetime.now()
    if not exists:
        return "EXPORT-MISSING", "%s does not exist" % EXPORT_PATH
    if isinstance(raw_bytes, (bytes, bytearray)) and bytes(raw_bytes[:3]) == BOM:
        # Named explicitly: the generic JSON error ("Unexpected UTF-8 BOM")
        # is easy to misread as a content problem instead of an encoding one.
        return "EXPORT-BAD", ("first 3 bytes are EF BB BF (UTF-8 BOM); strict "
                              "utf-8 readers reject this file")
    try:
        text = raw_bytes.decode("utf-8")
    except (UnicodeDecodeError, AttributeError) as e:
        return "EXPORT-BAD", "not strict UTF-8: %r" % e
    try:
        payload = json.loads(text)
    except ValueError as e:
        return "EXPORT-BAD", "JSON parse failed: %s" % e
    if not isinstance(payload, dict):
        return "EXPORT-BAD", "top level is %s, expected object" % type(payload).__name__
    problems = []
    for key in REQUIRED_KEYS:
        if key not in payload:
            problems.append("%s missing" % key)
            continue
        bad = _bad_value(key, payload.get(key))
        if bad:
            problems.append(bad)
    if problems:
        return "EXPORT-BAD", "; ".join(problems)
    updated = parse_ts(payload.get("updated_at"))
    if updated is None:
        return "EXPORT-BAD", "updated_at unparsable: %r" % (payload.get("updated_at"),)
    age_h = (now - updated).total_seconds() / 3600.0
    if age_h > MAX_AGE_H:
        return "EXPORT-STALE", ("updated_at=%s age=%.1fh > %.0fh cap"
                                % (updated.strftime("%Y-%m-%d %H:%M"), age_h, MAX_AGE_H))
    latest = latest_round_ts(rounds_text)
    if latest is not None:
        lag_h = (latest - updated).total_seconds() / 3600.0
        if lag_h > LAG_H:
            return "EXPORT-STALE", ("round %s is %.1fh newer than export %s "
                                    "(face not refreshed while work happened)"
                                    % (latest.strftime("%Y-%m-%d %H:%M"), lag_h,
                                       updated.strftime("%Y-%m-%d %H:%M")))
    return "EXPORT-GREEN", ("three lines present, updated_at=%s age=%.1fh"
                            % (updated.strftime("%Y-%m-%d %H:%M"), age_h))


def check(root=None, now=None):
    """Live read of the export face against the round ledger."""
    root = root or ROOT
    path = os.path.join(root, *EXPORT_PATH.split("/"))
    rounds = os.path.join(root, *ROUNDS_PATH.split("/"))
    rounds_text = ""
    if os.path.exists(rounds):
        try:
            with open(rounds, "rb") as f:
                rounds_text = f.read().decode("utf-8", "replace")
        except OSError:
            rounds_text = ""
    if not os.path.exists(path):
        return export_verdict(None, now=now, rounds_text=rounds_text, exists=False)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return "EXPORT-BAD", "unreadable: %r" % e
    return export_verdict(raw, now=now, rounds_text=rounds_text)


# --- T144: compose the three lines from the ledgers ---------------------

ARTIFACT_DIRS = ("state", "qa", "docs", "Tools")
# Bookkeeping files are not artifacts: the mandate scores them zero, so
# they must never become the "latest artifact" line.
ARTIFACT_EXCLUDE = {"state/rounds.log", "state/heartbeat.txt",
                    "docs/status-export.json", "docs/status-export-overrides.json",
                    "state/proposals.md", "HQ-FEEDBACK.md"}
# Queue files are planning bookkeeping too: score-zero by the mandate,
# so they can never be "the latest artifact".
ARTIFACT_SKIP_PREFIX = ("state/queue/",)
# Bytecode caches are build droppings, not artifacts.
ARTIFACT_SKIP_EXT = (".pyc", ".pyo")
QUEUE_PATH = os.path.join("state", "queue", "main.md")
OVERRIDES_PATH = os.path.join("docs", "status-export-overrides.json")
QUEUE_ID_RE = re.compile(r"^(M|T|E)\d+$")


READABLE_RE = re.compile(r"[\x20-\x7e\u3000-\u303f\u4e00-\u9fff\uff00-\uffef\u2000-\u2bff]+")
# Below this share of readable characters a chunk is mojibake, not text.
READABLE_MIN = 0.6


def _readable_ratio(text):
    if not text:
        return 0.0
    return len("".join(READABLE_RE.findall(text))) / float(len(text))


def _clean(text, limit):
    """One-line, mojibake-free, length-capped text ("" when unusable).

    rounds.log carries fixed-in mojibake from an earlier GBK round trip
    (the T24 precedent): the bytes are valid UTF-8 but the Chinese was
    already destroyed, and some of it is Latin-1 noise rather than U+FFFD
    -- so the gate must reject by readability, not just by "has U+FFFD".
    Keep the longest readable chunk and drop the damaged one.
    """
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    if "\ufffd" in text or _readable_ratio(text) < READABLE_MIN:
        text = re.sub(r"\ufffd+", " ", text)
        runs = [m.group(0).strip() for m in READABLE_RE.finditer(text)]
        runs = [r for r in runs if r]
        text = max(runs, key=len) if runs else ""
    return text[:limit]


def compose_activity(rounds_text):
    """Last round's queue segment, with its timestamp."""
    lines = [l for l in (rounds_text or "").splitlines() if l.strip()]
    if not lines:
        return "state/rounds.log 无轮记录"
    last = lines[-1]
    ts = parse_ts(last)
    stamp = ts.strftime("%Y-%m-%d %H:%M") if ts else "未知轮次"
    seg = ""
    # Newer rounds use " | " section separators; older ones put queue:
    # inline after the tokens fields, so both shapes are accepted.
    for part in last.split(" | "):
        part = part.strip()
        if part.startswith("queue:"):
            seg = _clean(part[len("queue:"):], 220)
            break
    if not seg:
        m = re.search(r"queue:(.*)", last)
        if m:
            seg = _clean(m.group(1), 220)
    if not seg:
        seg = "账本行摘要不可用（非 UTF-8 或空，原行见 state/rounds.log）"
    return "%s 轮：%s" % (stamp, seg)


def compose_artifact(files):
    """Newest artifact by mtime; bookkeeping files are already excluded."""
    if not files:
        return "未发现实物文件（state/qa/docs/Tools 扫描为空）"
    rel, mtime = max(files, key=lambda p: p[1])
    ts = datetime.datetime.fromtimestamp(mtime)
    return "%s（mtime %s）" % (rel, ts.strftime("%Y-%m-%d %H:%M"))


def compose_milestone(queue_text):
    """First not-done row of the P1 queue, in file order."""
    for line in (queue_text or "").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not QUEUE_ID_RE.match(cells[0]):
            continue
        status = cells[-1]
        # Status cells are written as "**done**（...）" as often as "open",
        # so the markdown emphasis has to come off before testing.
        low = status.replace("*", "").strip().lower()
        if low.startswith("done") or low.startswith("常备"):
            continue
        title = _clean(cells[1].replace("**", ""), 150)
        if not title:
            continue
        return "%s %s（队列状态=%s）" % (cells[0], title, _clean(status, 40) or "open")
    return "队列无未闭合行（state/queue/main.md）"


def compose_lines(rounds_text, queue_text, files, now=None, overrides=None):
    """Pure: three machine lines + overrides appended, never replacing."""
    now = now or datetime.datetime.now()
    lines = {
        "company": "BigCompute",
        "updated_at": now.strftime("%Y-%m-%d %H:%M"),
        "current_activity": compose_activity(rounds_text),
        "latest_artifact": compose_artifact(files),
        "next_milestone": compose_milestone(queue_text),
    }
    for key, value in (overrides or {}).items():
        if not isinstance(value, str) or not value.strip():
            continue
        if key in REQUIRED_KEYS:
            extra = _clean(value, 240)
            if extra:
                lines[key] = lines[key] + " ｜ " + extra
            continue
        lines[key] = value
    return lines


def collect_files(root=None):
    """[(relpath, mtime)] over the artifact dirs, bookkeeping excluded."""
    root = root or ROOT
    out = []
    for d in ARTIFACT_DIRS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames
                           if not x.startswith(".") and x != "__pycache__"]
            for fn in filenames:
                if fn.startswith(".") or fn.lower().endswith(ARTIFACT_SKIP_EXT):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                if rel in ARTIFACT_EXCLUDE or rel.startswith(ARTIFACT_SKIP_PREFIX):
                    continue
                try:
                    out.append((rel, os.path.getmtime(full)))
                except OSError:
                    continue
    return out


def _read_text(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _print_safe(text):
    """Composed lines can carry characters the console codec lacks (⬜ under
    GBK); printing them must not abort a write that is otherwise fine."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, "replace").decode(enc, "replace"))


def verify_written(on_disk, data):
    """Pure: problems with the bytes that landed, given the payload intended."""
    problems = []
    if on_disk.startswith(BOM):
        problems.append("first 3 bytes are EF BB BF (UTF-8 BOM)")
    if on_disk != data:
        problems.append("bytes on disk differ from the payload written")
    try:
        json.loads(on_disk.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        problems.append("post-write strict utf-8 read failed: %r" % e)
    return problems


def guarded_write(path, payload_lines):
    """Write UTF-8 (no BOM) and then verify the bytes actually on disk.

    Return (problems, first3). An empty `problems` list means the file we
    will hand to the CEO is byte-identical to what we meant to write and
    strictly decodes as UTF-8 JSON. Anything else is a refusal, not a
    warning: a half-wrong CEO-facing face is worse than a red write.
    """
    data = (json.dumps(payload_lines, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "wb") as f:
        f.write(data)
    with open(path, "rb") as f:
        on_disk = f.read()
    return verify_written(on_disk, data), on_disk[:3]


def _read_overrides(path):
    """(dict, warning) -- BOM tolerated on read, but never silently dropped."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return {}, None
    has_bom = raw.startswith(BOM)
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except ValueError as e:
        return {}, "%s unparsable: %s" % (OVERRIDES_PATH, e)
    if not isinstance(payload, dict):
        return {}, "%s top level is not an object" % OVERRIDES_PATH
    warn = None
    if has_bom:
        warn = ("%s carries a UTF-8 BOM (tolerated on read; the writer must "
                "save it BOM-free)" % OVERRIDES_PATH)
    return payload, warn


def compose(root=None, now=None, write=False):
    """Live compose; --write also gates the result through check()."""
    root = root or ROOT
    rounds_text = _read_text(os.path.join(root, *ROUNDS_PATH.split("/")))
    queue_text = _read_text(os.path.join(root, *QUEUE_PATH.split("/")))
    overrides = {}
    ov_path = os.path.join(root, *OVERRIDES_PATH.split("/"))
    if os.path.exists(ov_path):
        overrides, warn = _read_overrides(ov_path)
        if warn:
            _print_safe("NOTE %s" % warn)
    lines = compose_lines(rounds_text, queue_text, collect_files(root),
                          now=now, overrides=overrides)
    if write:
        path = os.path.join(root, *EXPORT_PATH.split("/"))
        problems, first3 = guarded_write(path, lines)
        if problems:
            _print_safe("WRITE-REJECTED %s: %s" % (EXPORT_PATH, "; ".join(problems)))
            return 3
        verdict, detail = check(root=root, now=now)
        _print_safe("WROTE %s first3=%s -> VERDICT=%s %s"
                    % (EXPORT_PATH, first3.hex(), verdict, detail))
        return 0 if verdict == "EXPORT-GREEN" else 1
    _print_safe(json.dumps(lines, ensure_ascii=False, indent=1))
    return 0


def selftest():
    """Six fixtures: one clean, five refusal branches (all must be red)."""
    now = datetime.datetime(2026, 10, 27, 21, 0)
    good = {
        "company": "BigCompute",
        "updated_at": "2026-10-27 20:30",
        "current_activity": "round in progress",
        "latest_artifact": "state/x.txt (2026-10-27 20:29)",
        "next_milestone": "N1 月末归集 10-31 22:43",
    }
    rounds_old = "2026-10-27 19x tokens: local=0 api=0\n"

    def enc(obj):
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    cases = []

    def case(label, raw, want, needle, rounds_text=rounds_old, exists=True):
        verdict, detail = export_verdict(raw, now=now, rounds_text=rounds_text,
                                         exists=exists)
        ok = verdict == want and (needle is None or needle in detail)
        cases.append(ok)
        print("  [%s] %s -> %s (%s)" % ("PASS" if ok else "FAIL", label,
                                        verdict, detail))

    case("A clean fresh export -> GREEN", enc(good), "EXPORT-GREEN", None)

    old = dict(good, updated_at="2026-10-24 12:00")
    case("B age 81h -> STALE", enc(old), "EXPORT-STALE", "cap")

    behind = dict(good, updated_at="2026-10-27 12:00")
    case("C round 19x newer than 12:00 export -> STALE",
         enc(behind), "EXPORT-STALE", "newer than export",
         rounds_text="2026-10-27 19x tokens: local=0\n")

    missing = dict(good)
    missing.pop("next_milestone")
    case("D missing next_milestone -> BAD", enc(missing), "EXPORT-BAD",
         "next_milestone missing")

    mojibake = dict(good, current_activity="轮：CEO 自检\ufffd回执")
    case("E U+FFFD in a line -> BAD", enc(mojibake), "EXPORT-BAD", "U+FFFD")

    gbk = json.dumps(dict(good, current_activity="轮中：中文"), ensure_ascii=False).encode("gbk")
    case("F GBK-encoded bytes -> BAD", gbk, "EXPORT-BAD", "UTF-8")

    case("G absent file -> MISSING", None, "EXPORT-MISSING", "does not exist",
         exists=False)

    # H guards the minute arm: a regex that only took the hour reported a
    # 20:30 export as 20:00, understating lag/age by up to 59 minutes.
    h_ok = (parse_ts("2026-10-27 20:30") == datetime.datetime(2026, 10, 27, 20, 30)
            and parse_ts("2026-10-27 06x") == datetime.datetime(2026, 10, 27, 6, 0))
    cases.append(h_ok)
    print("  [%s] H minutes preserved + hour mask floors to :00"
          % ("PASS" if h_ok else "FAIL"))

    # T144: compose must follow the ledgers, not a frozen string. Each
    # drift fixture changes exactly one input and expects only that line
    # to move -- a generator that ignores its input would pass the shape
    # check above and still ship a fiction.
    q_one = "| M4 | 接产对齐 | x | open |\n| M25 | 净窗烘焙 | y | done |\n"
    q_two = "| M4 | 接产对齐 | x | done |\n| M25 | 净窗烘焙 | y | open |\n"
    base_round = "2026-10-27 20x tokens: local=0 | queue:T143 done 实况面闸 | C1C4:GREEN\n"
    next_round = "2026-10-27 21x tokens: local=0 | queue:T144 compose 首跑 | C1C4:GREEN\n"
    files_a = [("state/a.txt", 1000000000.0)]
    files_b = [("state/a.txt", 1000000000.0), ("qa/smoke.log", 1100000000.0)]
    c0 = compose_lines(base_round, q_one, files_a, now=now)
    c1 = compose_lines(next_round, q_one, files_a, now=now)
    c2 = compose_lines(base_round, q_two, files_a, now=now)
    c3 = compose_lines(base_round, q_one, files_b, now=now)
    drift = (c0["current_activity"] != c1["current_activity"]
             and "T143" in c0["current_activity"] and "T144" in c1["current_activity"]
             and c0["next_milestone"] != c2["next_milestone"]
             and "M4" in c0["next_milestone"] and "M25" in c2["next_milestone"]
             and c0["latest_artifact"] != c3["latest_artifact"]
             and "qa/smoke.log" in c3["latest_artifact"])
    cases.append(drift)
    print("  [%s] I compose tracks round/queue/file drift" % ("PASS" if drift else "FAIL"))

    moj = compose_lines("2026-10-27 21x | queue:轮\ufffd乱码 | C1C4:GREEN\n",
                        q_one, files_a, now=now)
    clean = bool("U+FFFD" not in json.dumps(moj, ensure_ascii=False)
                 and moj["current_activity"].strip())
    cases.append(clean)
    print("  [%s] J mojibake ledger line never reaches the export"
          % ("PASS" if clean else "FAIL"))

    ov = compose_lines(base_round, q_one, files_a, now=now,
                       overrides={"next_milestone": "窗口 10-31 22:43",
                                  "current_activity": "覆盖尝试"})
    append_only = (ov["next_milestone"].startswith(c0["next_milestone"])
                   and "10-31 22:43" in ov["next_milestone"]
                   and ov["current_activity"].startswith(c0["current_activity"]))
    cases.append(append_only)
    print("  [%s] K overrides append, never replace the three lines"
          % ("PASS" if append_only else "FAIL"))

    empty = compose_lines("", "", [], now=now)
    empty_ok = all(isinstance(empty[k], str) and empty[k].strip()
                   for k in REQUIRED_KEYS + ("updated_at",))
    cases.append(empty_ok)
    print("  [%s] L all lines non-empty even on an empty tree"
          % ("PASS" if empty_ok else "FAIL"))

    # M: a .pyc mtime must never win "latest artifact" -- bytecode is a
    # build dropping (this fixture caught the first live compose doing it).
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "state", "__pycache__"))
        for rel in ("state/a.txt", "state/__pycache__/x.pyc"):
            with open(os.path.join(td, *rel.split("/")), "w") as f:
                f.write("")
        got = sorted(r for r, _ in collect_files(td))
    cache_ok = got == ["state/a.txt"]
    cases.append(cache_ok)
    print("  [%s] M __pycache__/.pyc never counted as an artifact"
          % ("PASS" if cache_ok else "FAIL"))

    # N: Latin-1 mojibake (no U+FFFD at all) must still be rejected.
    moj2 = compose_lines("2026-10-27 21x | queue:T143 done ʵ�����еբ\n",
                         q_one, files_a, now=now)
    latin_ok = "ʵ" not in moj2["current_activity"] and "T143" in moj2["current_activity"]
    cases.append(latin_ok)
    print("  [%s] N Latin-1 mojibake rejected by readability, ASCII kept"
          % ("PASS" if latin_ok else "FAIL"))

    # T169 O: a BOM'd export must be named as an encoding fault, not
    # reported as a generic content problem.
    bom_raw = BOM + enc(good)
    v, detail = export_verdict(bom_raw, now=now, rounds_text=rounds_old)
    o_ok = v == "EXPORT-BAD" and "BOM" in detail
    cases.append(o_ok)
    print("  [%s] O UTF-8 BOM export -> BAD named as BOM (%s)"
          % ("PASS" if o_ok else "FAIL", v))

    # T169 P: write-side assertion. The writer's own verify must accept a
    # clean write and refuse a BOM'd or altered one -- this is the half
    # that was missing: the reader could see it, the writer never checked.
    data_clean = enc(good)
    p_ok = (verify_written(data_clean, data_clean) == []
            and bool(verify_written(BOM + data_clean, data_clean))
            and bool(verify_written(data_clean + b" ", data_clean)))
    cases.append(p_ok)
    print("  [%s] P write-side verify: clean ok, BOM/altered refused"
          % ("PASS" if p_ok else "FAIL"))

    # T169 Q: a real write into a scratch root must land BOM-free and read
    # back GREEN (end-to-end, not just the pure helper).
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, *EXPORT_PATH.split("/"))
        problems, first3 = guarded_write(path, good)
        with open(path, "rb") as f:
            landed = f.read()
        v_live, _ = export_verdict(landed, now=now, rounds_text=rounds_old)
    q_ok = (not problems and first3 != BOM and v_live == "EXPORT-GREEN")
    cases.append(q_ok)
    print("  [%s] Q guarded write lands BOM-free (first3=%s) and reads GREEN"
          % ("PASS" if q_ok else "FAIL", first3.hex()))

    print("status_export_check selftest: %d/%d PASS" % (sum(cases), len(cases)))
    return 0 if all(cases) else 1


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "selftest":
        return selftest()
    if cmd in ("check", "compose"):
        now = None
        if "--now" in argv:
            now = parse_ts(argv[argv.index("--now") + 1])
            if now is None:
                print("bad --now value")
                return 2
        if cmd == "compose":
            return compose(now=now, write="--write" in argv)
        verdict, detail = check(now=now)
        print("VERDICT=%s %s" % (verdict, detail))
        return 0 if verdict == "EXPORT-GREEN" else 1
    print("usage: %s [check | compose [--write] | selftest] [--now 'YYYY-MM-DD HH:MM']"
          % argv[0])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))