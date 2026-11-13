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

M166: the two-pool reading (`stock_dispatchable` = this company's stocked
cards that could be dispatched, `pool_effective` = the fleet pool's
ignitable rows) now travels into `current_activity` from the heartbeat
line, not from a hand-typed sentence. They are different pools (T-20261105-96),
so the note states both numbers side by side and never subtracts them. A
missing field or a stale heartbeat renders `n/a` with the reason -- `0`
means "read as zero", never "could not read".

M167 (found while wiring M166): the overrides supplement had no freshness
gate, so an 11-03 sentence still appended itself onto an 11-08 face as
soon as anyone ran `compose --write`. Supplements older than the export's
own staleness cap are now ignored with a NOTE instead of being merged.

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
# M194：两条读侧尺子的阈值单源=status_face_freshness（禁复制字面）；
# 与写侧 round_append.J11 共用同一份定义，读数里各自打印 bench=。
# BC-P-241：面的 caps/freshness 两键也由同一模块生成，本文件只做比对。
try:
    from status_face_freshness import (LEDGER_LAG_MIN, MAX_AGE_MIN,
                                       caps_block, caps_line)
except Exception:  # 同目录导入兜底（被 qa_smoke 经 sys.path 加载时不触发）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from status_face_freshness import (LEDGER_LAG_MIN, MAX_AGE_MIN,
                                       caps_block, caps_line)

MAX_AGE_H = MAX_AGE_MIN / 60.0
LAG_H = LEDGER_LAG_MIN / 60.0

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


CAPS_KEY = "caps"
FRESHNESS_KEY = "freshness"
CAPS_FIELDS = ("write_lag_min", "ledger_lag_min", "max_age_min",
               "bench_date", "source")


def caps_problems(payload):
    """BC-P-241: 面里写的判据必须逐字等于单源常量。

    面缺这两键 = CEO 打开面看不到门在哪（这正是 M194 留下的缺口）；
    键在但值与 status_face_freshness 不符 = 面在撒谎（阈值改了、面没跟），
    比不写更糟。两种都判红，读数里点名差在哪一字段。
    """
    problems = []
    block = payload.get(CAPS_KEY)
    if not isinstance(block, dict):
        problems.append("%s missing (面未自证判据)" % CAPS_KEY)
        return problems
    expect = caps_block()
    for field in CAPS_FIELDS:
        if field not in block:
            problems.append("%s.%s missing" % (CAPS_KEY, field))
            continue
        got, want = block[field], expect[field]
        if isinstance(want, float):
            try:
                same = abs(float(got) - want) < 1e-9
            except (TypeError, ValueError):
                same = False
        else:
            same = got == want
        if not same:
            problems.append("%s.%s=%r != 单源 %r" % (CAPS_KEY, field, got, want))
    bad_line = _bad_value(FRESHNESS_KEY, payload.get(FRESHNESS_KEY))
    if bad_line:
        problems.append(bad_line)
    elif payload.get(FRESHNESS_KEY) != caps_line():
        problems.append("%s 与单源 caps_line() 不一致（判据文字已漂移）"
                        % FRESHNESS_KEY)
    return problems


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
    cap_problems = caps_problems(payload)
    if cap_problems:
        return "EXPORT-BAD", "; ".join(cap_problems)
    updated = parse_ts(payload.get("updated_at"))
    if updated is None:
        return "EXPORT-BAD", "updated_at unparsable: %r" % (payload.get("updated_at"),)
    age_h = (now - updated).total_seconds() / 3600.0
    if age_h > MAX_AGE_H:
        return "EXPORT-STALE", ("updated_at=%s age=%.1fh > %.0fh cap (bench=now)"
                                % (updated.strftime("%Y-%m-%d %H:%M"), age_h, MAX_AGE_H))
    latest = latest_round_ts(rounds_text)
    if latest is not None:
        lag_h = (latest - updated).total_seconds() / 3600.0
        if lag_h > LAG_H:
            return "EXPORT-STALE", ("round %s is %.1fh newer than export %s "
                                    "(face not refreshed while work happened; "
                                    "bench=rounds.log latest round)"
                                    % (latest.strftime("%Y-%m-%d %H:%M"), lag_h,
                                       updated.strftime("%Y-%m-%d %H:%M")))
    return "EXPORT-GREEN", ("three lines present, updated_at=%s age=%.1fh "
                            "(bench=now+rounds.log latest round)"
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
                    "state/proposals.md", "HQ-FEEDBACK.md",
                    # M202: 风险台账与里程碑声明都是维护面（行级 upkeep / 单字段声明），
                    # 不能当「最近实物」——否则加一条风险或改一次里程碑就让 CEO 面
                    # 显示「最近交付=台账/状态文件」。
                    "docs/risk-register.md", "state/milestone.json",
                    # M203：工具清单是登记面，不是交付物。注册一个新工具会让它成为
                    # mtime 最新的文件，于是 CEO 面显示「最近实物=Tools/tool_manifest.txt」，
                    # 把同批真正新增的脚本挤掉——与 M202 的台账冒充同族。
                    "Tools/tool_manifest.txt"}
# Queue files are planning bookkeeping too: score-zero by the mandate,
# so they can never be "the latest artifact".
# M181 finding: a checker's own output (patrol json, registry/probe reports,
# smoke png) is evidence that a check ran, not work product.  Letting it win
# "latest artifact" made the face say "nothing shipped" on days that shipped.
ARTIFACT_SKIP_PREFIX = ("state/queue/", "state/c1c4-",
                        "state/probe-registry-", "state/text-integrity-",
                        "state/lane-gate-", "state/dispatch-gate",
                        "state/ledger-continuity-", "qa/smoke-")
# Bytecode caches are build droppings, not artifacts.
ARTIFACT_SKIP_EXT = (".pyc", ".pyo", ".log", ".jsonl", ".lock")
# T230 finding: the tangle of *process logs* (clean-window-log.jsonl and
# friends) is bookkeeping, so it must not win "latest artifact" by being the
# most recently touched file.  Readings and reports (.json/.txt/.md) still can.
QUEUE_PATH = os.path.join("state", "queue", "main.md")
OVERRIDES_PATH = os.path.join("docs", "status-export-overrides.json")
QUEUE_ID_RE = re.compile(r"^(M|T|E)\d+$")

# M166: the heartbeat is the single source for both pool readings.
HEARTBEAT_PATH = os.path.join("state", "heartbeat.txt")
HEARTBEAT_MAX_AGE_MIN = 45.0
POOL_FIELDS = ("stock_dispatchable", "pool_effective")
POOL_FIELD_RE = re.compile(r"\b(stock_dispatchable|pool_effective)=(\S+)")
# M167: a supplement older than the export's own cap is stale by the same
# argument the export itself is -- it describes a round nobody can verify.
OVERRIDE_MAX_AGE_H = MAX_AGE_H

# M182: the queue-derived milestone can only ever name the oldest unfinished
# row, so the CEO face kept reporting M25 (opened 2026-09-29) as "next" for
# six weeks -- and M181 showed the row status text carries stray dates, so
# inference cannot be fixed by heuristics.  A round now states the target in
# state/milestone.json; the declaration wins while fresh and the face always
# prints which of the two it used.
MILESTONE_PATH = os.path.join("state", "milestone.json")
MILESTONE_MAX_AGE_H = 48.0
MILESTONE_TIMEFMT = "%Y-%m-%d %H:%M"
MILESTONE_SOURCE_DECLARED = "source=declared"
MILESTONE_SOURCE_QUEUE = "source=queue"


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
    if len(text) > limit:
        # Cutting mid-token ("…call-") reads to the CEO as a broken feed;
        # drop the trailing partial word unless that would lose most of it.
        text = text[:limit]
        cut = text.rfind(" ")
        if cut > limit * 0.6:
            text = text[:cut]
        # Chinese has no spaces, so the word-boundary rule above never fires
        # and the line used to stop mid-sentence ("…改为由").  Prefer the last
        # clause boundary, then always mark the cut so a truncated sentence is
        # never mistaken for a finished one.
        for mark in ("。", "；", "，", "、", "）", "|"):
            pos = text.rfind(mark)
            if pos > limit * 0.5:
                text = text[:pos + 1]
                break
        text = text.rstrip("，、；") + "…"
    return text


def last_heartbeat_line(text):
    """Newest `idle_selfcheck:` line carrying a timestamp, or None."""
    newest, newest_ts = None, None
    for line in (text or "").splitlines():
        if "idle_selfcheck:" not in line:
            continue
        ts = parse_ts(line)
        if ts is None:
            continue
        if newest_ts is None or ts > newest_ts:
            newest, newest_ts = line, ts
    return newest, newest_ts


def _pool_count(value):
    """Integer count, or None when the token is not a count.

    `n/a` and friends must stay unreadable: turning an unreadable pool
    into `0` is the exact confusion this note exists to remove.
    """
    text = str(value or "").strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def pool_note(heartbeat_text, now=None, max_age_min=HEARTBEAT_MAX_AGE_MIN):
    """Pure: one-line two-pool reading for the CEO face.

    Two pools are reported side by side and never combined: `stock_dispatchable`
    is this company's stocked cards (batch_pool review), `pool_effective` is the
    fleet queue's ignitable rows. T-20261105-96 established that they read
    different objects; summing or subtracting them would invent a number.
    """
    now = now or datetime.datetime.now()
    line, ts = last_heartbeat_line(heartbeat_text)
    if line is None:
        return "两池：n/a（心跳无 idle_selfcheck 行）"
    age_min = (now - ts).total_seconds() / 60.0
    if age_min > max_age_min:
        return ("两池：n/a（心跳 %.0f 分钟未更新，超 %.0f 分钟上限）"
                % (age_min, max_age_min))
    found = dict(POOL_FIELD_RE.findall(line))
    parts, missing = [], []
    for field in POOL_FIELDS:
        count = _pool_count(found.get(field))
        if count is None:
            missing.append(field)
            continue
        parts.append("%d" % count)
    if missing:
        return "两池：n/a（心跳缺 %s，不按 0 读）" % "/".join(missing)
    return ("两池：本司备货可派 %s ／ 机队可领 %s（两个不同池，本司卡未登记进机队池）"
            % (parts[0], parts[1]))


def compose_activity(rounds_text):
    """Last round's queue segment, with its timestamp.

    M202: the ledger allows a trailing "更正" line with no `queue:` segment.
    Reading `lines[-1]` blindly turned "the last line is a correction" into
    "the summary is unreadable" — the wrong line, not a broken ledger. So scan
    from the end for the newest line that actually carries a queue segment.
    """
    lines = [l for l in (rounds_text or "").splitlines() if l.strip()]
    if not lines:
        return "state/rounds.log 无轮记录"

    def _queue_seg(line):
        # Newer rounds use " | " section separators; older ones put queue:
        # inline after the tokens fields, so both shapes are accepted.
        for part in line.split(" | "):
            part = part.strip()
            if part.startswith("queue:"):
                return _clean(part[len("queue:"):], 220)
        m = re.search(r"queue:(.*)", line)
        return _clean(m.group(1), 220) if m else ""

    last, seg = lines[-1], ""
    for line in reversed(lines):
        seg = _queue_seg(line)
        if seg:
            last = line
            break
    if not seg:
        seg = "账本行摘要不可用（既有行均无 queue: 段，原行见 state/rounds.log）"
    ts = parse_ts(last)
    stamp = ts.strftime("%Y-%m-%d %H:%M") if ts else "未知轮次"
    return "%s 轮：%s" % (stamp, seg)


_CODE_EXT = (".py", ".ps1", ".psm1", ".cs", ".bat", ".cmd")


def compose_artifact(files):
    """Newest artifact by mtime; bookkeeping files are already excluded."""
    if not files:
        return "未发现实物文件（state/qa/docs/Tools 扫描为空）"
    # M181: a script is work product, but this line answers "what can the CEO
    # look at?" - when a reading/report also landed, name it instead of the
    # script that wrote it.
    reports = [p for p in files if not p[0].lower().endswith(_CODE_EXT)]
    rel, mtime = max(reports or files, key=lambda p: p[1])
    ts = datetime.datetime.fromtimestamp(mtime)
    return "%s（mtime %s）" % (rel, ts.strftime("%Y-%m-%d %H:%M"))


def compose_milestone(queue_text):
    """First not-done row of the P1 queue, in file order.

    M166 finding: this used to return the first not-done row even when it
    was BLOCKED-PHYSICAL (waiting on the CEO), so the CEO-facing "next
    milestone" could only ever name an item nobody can start. Active rows
    win; a queue with nothing active falls back to the blocked rows so the
    line never goes blank (and the status text still says blocked).

    M181 note: an attempt to demote rows by the newest date inside their
    status text was withdrawn - row statuses carry stray dates (window
    deadlines, "as of" stamps), so the demotion picked *other* stale rows
    instead of fresher work.  Naming the wrong milestone is worse than
    naming an old one; this function stays as M166 left it.
    """
    rows = []
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
        rows.append((cells[0], title, status, low))
    for rid, title, status, low in rows:
        if low.startswith("blocked"):
            continue
        return "%s %s（队列状态=%s）" % (rid, title, _clean(status, 40) or "open")
    if rows:
        rid, title, status, _ = rows[0]
        return "%s %s（队列状态=%s）" % (rid, title, _clean(status, 40) or "open")
    return "队列无未闭合行（state/queue/main.md）"


def load_declared_milestone(root=None, now=None, max_age_h=MILESTONE_MAX_AGE_H):
    """(text, note) -- the target a round declared, while it is still fresh.

    M182: "which row is really next" is not inferable from the queue text, so
    the round states it instead of the tool guessing.  A stale or unparsable
    declaration is dropped with a note rather than silently used: naming a
    3-day-old target on the CEO face is the same lie as naming a 6-week-old
    one, and a future timestamp is a typo, not a plan.
    """
    root = root or ROOT
    path = os.path.join(root, *MILESTONE_PATH.split("/"))
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None, None
    if raw.startswith(BOM):
        raw = raw[3:]
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return None, "%s unparsable: %s" % (MILESTONE_PATH, e)
    if not isinstance(payload, dict):
        return None, "%s top level is not an object" % MILESTONE_PATH
    text = payload.get("next_milestone")
    if not isinstance(text, str) or not text.strip():
        return None, "%s has no next_milestone text, queue-derived used" % MILESTONE_PATH
    stamp = payload.get("declared_at")
    try:
        declared = datetime.datetime.strptime(str(stamp), MILESTONE_TIMEFMT)
    except (ValueError, TypeError):
        return None, ("%s declared_at=%r is not 'YYYY-MM-DD HH:MM', "
                      "queue-derived used" % (MILESTONE_PATH, stamp))
    now = now or datetime.datetime.now()
    age_h = (now - declared).total_seconds() / 3600.0
    if age_h > max_age_h:
        return None, ("%s declared %s is %.1fh old (> %.0fh), queue-derived used"
                      % (MILESTONE_PATH, stamp, age_h, max_age_h))
    if age_h < -1.0:
        return None, ("%s declared_at=%s is %.1fh in the future, "
                      "queue-derived used" % (MILESTONE_PATH, stamp, -age_h))
    return text.strip(), None


def compose_lines(rounds_text, queue_text, files, now=None, overrides=None,
                  pool_line=None, declared_milestone=None):
    """Pure: three machine lines + overrides appended, never replacing.

    `pool_line` (M166) is appended to current_activity so the two-pool
    reading reaches the CEO face without being retyped each round.
    `declared_milestone` (M182) wins over the queue-derived row, and the line
    says which one it came from so a wrong target can be traced to its author.
    """
    now = now or datetime.datetime.now()
    activity = compose_activity(rounds_text)
    if pool_line:
        activity = activity + " ｜ " + pool_line
    declared = _clean(declared_milestone, 240) if declared_milestone else ""
    if declared:
        milestone = "%s（%s）" % (declared, MILESTONE_SOURCE_DECLARED)
    else:
        milestone = "%s（%s）" % (compose_milestone(queue_text),
                                 MILESTONE_SOURCE_QUEUE)
    lines = {
        "company": "BigCompute",
        "updated_at": now.strftime("%Y-%m-%d %H:%M"),
        "current_activity": activity,
        "latest_artifact": compose_artifact(files),
        "next_milestone": milestone,
        # BC-P-241：判据与定标日期写进面自身。两个键由代码独占生成，
        # 不在下面的 overrides 循环里可被覆盖——面只能投影单源，不能改写它。
        CAPS_KEY: caps_block(),
        FRESHNESS_KEY: caps_line(),
    }
    for key, value in (overrides or {}).items():
        if key in (CAPS_KEY, FRESHNESS_KEY):
            _print_safe("NOTE overrides 试图改写 %s，已忽略（判据只能来自单源）" % key)
            continue
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


def overrides_freshness(path, now=None, max_age_h=OVERRIDE_MAX_AGE_H):
    """(fresh, note) -- is the supplement layer describing the current work?

    M167: an override written nine days ago still merged into today's face
    the moment `compose --write` ran, because nothing asked "when was this
    written". No timestamp exists inside the file, so file mtime is the
    evidence available; a stale layer is dropped with a NOTE rather than
    silently merged (it would be a claim about a round nobody can verify).
    """
    now = now or datetime.datetime.now()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False, "%s unreadable, supplements ignored" % OVERRIDES_PATH
    age_h = (now - datetime.datetime.fromtimestamp(mtime)).total_seconds() / 3600.0
    if age_h > max_age_h:
        return False, ("%s is %.1fh old (> %.0fh), supplements ignored as stale"
                       % (OVERRIDES_PATH, age_h, max_age_h))
    return True, None


def compose(root=None, now=None, write=False):
    """Live compose; --write also gates the result through check()."""
    root = root or ROOT
    rounds_text = _read_text(os.path.join(root, *ROUNDS_PATH.split("/")))
    queue_text = _read_text(os.path.join(root, *QUEUE_PATH.split("/")))
    heartbeat_text = _read_text(os.path.join(root, *HEARTBEAT_PATH.split("/")))
    overrides = {}
    ov_path = os.path.join(root, *OVERRIDES_PATH.split("/"))
    if os.path.exists(ov_path):
        fresh, warn = overrides_freshness(ov_path, now=now)
        if warn:
            _print_safe("NOTE %s" % warn)
        if fresh:
            overrides, read_warn = _read_overrides(ov_path)
            if read_warn:
                _print_safe("NOTE %s" % read_warn)
    declared, ms_note = load_declared_milestone(root, now=now)
    if ms_note:
        _print_safe("NOTE %s" % ms_note)
    lines = compose_lines(rounds_text, queue_text, collect_files(root),
                          now=now, overrides=overrides,
                          pool_line=pool_note(heartbeat_text, now=now),
                          declared_milestone=declared)
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
        "caps": caps_block(),
        "freshness": caps_line(),
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
            with open(os.path.join(td, *rel.split("/")), "w",
                      encoding="utf-8") as f:
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

    # M166 R: both pool readings present -> both numbers in one line, and
    # the note never merges them into a single total.
    hb = ("2026-10-27 20:40:00 idle_selfcheck: verdict=busy "
          "python_tokens stock_dispatchable=4 pool_effective=0\n")
    note = pool_note(hb, now=datetime.datetime(2026, 10, 27, 20, 45))
    r_ok = ("4" in note and "0" in note and "n/a" not in note
            and "本司备货可派 4" in note and "机队可领 0" in note)
    cases.append(r_ok)
    print("  [%s] R two-pool note carries both numbers (%s)"
          % ("PASS" if r_ok else "FAIL", note))

    # M166 S: an absent field must read n/a, never 0 -- "could not read"
    # and "read as zero" are different claims (the T-22 no.10 precedent).
    hb_one = ("2026-10-27 20:40:00 idle_selfcheck: verdict=busy "
              "pool_effective=0\n")
    note_s = pool_note(hb_one, now=datetime.datetime(2026, 10, 27, 20, 45))
    s_ok = "n/a" in note_s and "stock_dispatchable" in note_s
    cases.append(s_ok)
    print("  [%s] S missing pool field -> n/a named, not 0 (%s)"
          % ("PASS" if s_ok else "FAIL", note_s))

    # M166 T: a stale heartbeat is not a current reading, whatever it says.
    note_t = pool_note(hb, now=datetime.datetime(2026, 10, 27, 23, 0))
    t_ok = "n/a" in note_t and "未更新" in note_t
    cases.append(t_ok)
    print("  [%s] T stale heartbeat -> n/a with age (%s)"
          % ("PASS" if t_ok else "FAIL", note_t))

    # M166 U: the note must reach the composed activity line, so a CEO who
    # only reads the export sees the two pools without running anything.
    c_pool = compose_lines(base_round, q_one, files_a, now=now, pool_line=note)
    u_ok = c_pool["current_activity"].endswith(note) and "T143" in c_pool["current_activity"]
    cases.append(u_ok)
    print("  [%s] U pool note appended to current_activity, round text kept"
          % ("PASS" if u_ok else "FAIL"))

    # M167 V: a supplement older than the cap is ignored, not merged -- the
    # 11-03 sentence must not appear on an 11-08 face.
    with tempfile.TemporaryDirectory() as td:
        ovp = os.path.join(td, "ov.json")
        with open(ovp, "w", encoding="utf-8") as f:
            f.write('{"next_milestone": "11-03 旧窗口"}')
        old_time = (now - datetime.timedelta(days=5)).timestamp()
        os.utime(ovp, (old_time, old_time))
        fresh_old, warn_old = overrides_freshness(ovp, now=now)
        os.utime(ovp, (now.timestamp(), now.timestamp()))
        fresh_new, warn_new = overrides_freshness(ovp, now=now)
    v_ok = (fresh_old is False and "stale" in (warn_old or "")
            and fresh_new is True and warn_new is None)
    cases.append(v_ok)
    print("  [%s] V stale overrides ignored, fresh overrides kept (%s)"
          % ("PASS" if v_ok else "FAIL", warn_old))

    # M166 W: a blocked row earlier in file order must not shadow an active
    # milestone -- and with nothing active the blocked row still shows.
    q_blocked = ("| M4 | 接产对齐 | x | **BLOCKED-PHYSICAL**（待 CEO） |\n"
                 "| M9 | 真活行 | y | open |\n")
    q_all_blocked = "| M4 | 接产对齐 | x | **BLOCKED-PHYSICAL**（待 CEO） |\n"
    w_ok = ("M9" in compose_milestone(q_blocked)
            and "M4" not in compose_milestone(q_blocked)
            and "M4" in compose_milestone(q_all_blocked))
    cases.append(w_ok)
    print("  [%s] W active milestone skips blocked; all-blocked falls back"
          % ("PASS" if w_ok else "FAIL"))

    # M182 X: a declared target wins over the oldest unfinished queue row,
    # and the line says which one it used (a wrong target must be traceable
    # to whoever declared it, not to the queue).
    x_declared = compose_lines(base_round, q_one, files_a, now=now,
                               declared_milestone="B-CITY3D-02 复渲验洋红")
    x_queue = compose_lines(base_round, q_one, files_a, now=now)
    x_ok = (x_declared["next_milestone"].startswith("B-CITY3D-02 复渲验洋红")
            and MILESTONE_SOURCE_DECLARED in x_declared["next_milestone"]
            and "M4" not in x_declared["next_milestone"]
            and MILESTONE_SOURCE_QUEUE in x_queue["next_milestone"]
            and "M4" in x_queue["next_milestone"])
    cases.append(x_ok)
    print("  [%s] X declared milestone wins and names its source"
          % ("PASS" if x_ok else "FAIL"))

    # M182 Y: freshness gate on the declaration -- fresh used, stale / future
    # / unparsable / empty ignored with a reason, absent file silent.
    with tempfile.TemporaryDirectory() as td:
        mp = os.path.join(td, *MILESTONE_PATH.split("/"))
        os.makedirs(os.path.dirname(mp))

        def _write_kv(payload):
            with open(mp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)

        fresh_stamp = (now - datetime.timedelta(hours=2)).strftime(MILESTONE_TIMEFMT)
        stale_stamp = (now - datetime.timedelta(hours=72)).strftime(MILESTONE_TIMEFMT)
        future_stamp = (now + datetime.timedelta(hours=6)).strftime(MILESTONE_TIMEFMT)

        _write_kv({"next_milestone": "新鲜目标", "declared_at": fresh_stamp})
        y_fresh, y_fresh_note = load_declared_milestone(td, now=now)

        _write_kv({"next_milestone": "陈旧目标", "declared_at": stale_stamp})
        y_stale, y_stale_note = load_declared_milestone(td, now=now)

        _write_kv({"next_milestone": "未来目标", "declared_at": future_stamp})
        y_future, y_future_note = load_declared_milestone(td, now=now)

        _write_kv({"next_milestone": "   ", "declared_at": fresh_stamp})
        y_empty, y_empty_note = load_declared_milestone(td, now=now)

        _write_kv({"next_milestone": "坏时标", "declared_at": "今天"})
        y_bad, y_bad_note = load_declared_milestone(td, now=now)

        with open(mp, "w", encoding="utf-8") as f:
            f.write("{not json")
        y_broken, y_broken_note = load_declared_milestone(td, now=now)

        os.remove(mp)
        y_absent, y_absent_note = load_declared_milestone(td, now=now)

    y_ok = (y_fresh == "新鲜目标" and y_fresh_note is None
            and y_stale is None and "old" in (y_stale_note or "")
            and y_future is None and "future" in (y_future_note or "")
            and y_empty is None and "no next_milestone" in (y_empty_note or "")
            and y_bad is None and "declared_at" in (y_bad_note or "")
            and y_broken is None and "unparsable" in (y_broken_note or "")
            and y_absent is None and y_absent_note is None)
    cases.append(y_ok)
    print("  [%s] Y declaration gate: fresh used, stale/future/empty/torn ignored"
          % ("PASS" if y_ok else "FAIL"))

    # BC-P-241 Z1-Z4: 面里的判据必须来自单源，且不许被补充层改写。
    no_caps = dict(good)
    no_caps.pop(CAPS_KEY)
    no_caps.pop(FRESHNESS_KEY)
    z1_v, z1_d = export_verdict(enc(no_caps), now=now, rounds_text=rounds_old)
    z1_ok = z1_v == "EXPORT-BAD" and "caps missing" in z1_d
    cases.append(z1_ok)
    print("  [%s] Z1 面无 caps 块判红并点名 (%s)" % ("PASS" if z1_ok else "FAIL", z1_d))

    drift = dict(good, caps=dict(caps_block(), max_age_min=2880.0 * 2))
    z2_v, z2_d = export_verdict(enc(drift), now=now, rounds_text=rounds_old)
    z2_ok = z2_v == "EXPORT-BAD" and "max_age_min" in z2_d and "单源" in z2_d
    cases.append(z2_ok)
    print("  [%s] Z2 caps 与单源不符判红并点名字段 (%s)" % ("PASS" if z2_ok else "FAIL", z2_d))

    text_drift = dict(good, freshness="判据：随便写的")
    z3_v, _ = export_verdict(enc(text_drift), now=now, rounds_text=rounds_old)
    z3_ok = z3_v == "EXPORT-BAD"
    cases.append(z3_ok)
    print("  [%s] Z3 freshness 文字漂移判红 (%s)" % ("PASS" if z3_ok else "FAIL", z3_v))

    z4 = compose_lines(base_round, q_one, files_a, now=now,
                       overrides={CAPS_KEY: {"max_age_min": 1},
                                  FRESHNESS_KEY: "手写判据"})
    z4_ok = (z4[CAPS_KEY] == caps_block() and z4[FRESHNESS_KEY] == caps_line())
    cases.append(z4_ok)
    print("  [%s] Z4 overrides 不得改写 caps/freshness" % ("PASS" if z4_ok else "FAIL"))

    # M202: 尾部「更正」行不得把上一条轮行的摘要读成不可用（读错行≠账坏了）
    tail_correction = ("2026-11-13 19:2x tokens: local=0 api=0 | queue:M201 done(x)\n"
                       "2026-11-13 19:3x 更正：上条轮账本基线写错\n")
    a1 = compose_activity(tail_correction)
    a1_ok = "M201 done" in a1 and "不可用" not in a1
    cases.append(a1_ok)
    print("  [%s] A1 尾部更正行不影响轮摘要 (%s)" % ("PASS" if a1_ok else "FAIL", a1))

    # M202: 全无 queue 段时仍要明说「读不到」，不许空着冒充有摘要
    a2 = compose_activity("2026-11-13 19:3x 更正：写错\n")
    a2_ok = "不可用" in a2
    cases.append(a2_ok)
    print("  [%s] A2 无任何 queue 段判不可用 (%s)" % ("PASS" if a2_ok else "FAIL", a2))

    # M202: 风险台账是维护面，不能成为「最近实物」
    a3_ok = "docs/risk-register.md" in ARTIFACT_EXCLUDE
    cases.append(a3_ok)
    print("  [%s] A3 风险台账不进最近实物" % ("PASS" if a3_ok else "FAIL"))

    # M203: 工具清单是登记面，注册新工具不该把它顶成「最近实物」
    a4_ok = "Tools/tool_manifest.txt" in ARTIFACT_EXCLUDE
    cases.append(a4_ok)
    print("  [%s] A4 工具清单不进最近实物" % ("PASS" if a4_ok else "FAIL"))

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