# -*- coding: utf-8 -*-
"""row_loss_census.py - shared jsonl ledger row-loss census (read-only).

Why this exists (2026-11-03, T197 live evidence):
  Three C-INFRA-AUDIT cards were seen in cph4/fleet/PRODUCTION-QUEUE.jsonl at 20:41
  and were gone at 20:46. They were never committed: a concurrent writer committed
  the whole file from its own snapshot ("state: node state snapshot before rebase"),
  overwriting rows another host had just appended. Row-level writes are lost by
  whole-file overwrite; nobody reports it because every individual commit is valid.

What it measures (one line):
  For each consecutive pair of commits touching a tracked jsonl file, ids present in
  the older blob and absent from the newer blob are row losses. A loss of a row whose
  state was terminal (DONE/ARCHIVED/CANCELLED) is expected pruning and is counted
  separately; any other state is a suspicious loss and fails the census.

Read-only by construction: git show / git log only, never writes to the target repo.

Usage:
  python Tools/row_loss_census.py run [--repo PATH] [--target REL]... [--window N]
                                      [--json-out PATH]
  python Tools/row_loss_census.py selftest
Exit: 0 ROW-LOSS-FREE / 2 ROW-LOSS-FOUND / 3 UNVERIFIED (no git, untracked, unreadable)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_TARGETS = (
    "cph4/fleet/PRODUCTION-QUEUE.jsonl",
    "cph4/fleet/PRODUCTION-BACKLOG.jsonl",
    "cph4/fleet/LANE-REGISTRY.jsonl",
)
TERMINAL_STATES = ("DONE", "ARCHIVED", "CANCELLED", "COMPLETE", "CLOSED")
DEFAULT_WINDOW = 40


def _git(repo, *args):
    """Run git read-only; return (rc, bytes)."""
    try:
        p = subprocess.run(["git", "-C", repo] + list(args),
                           capture_output=True, timeout=120)
        return p.returncode, p.stdout
    except Exception:
        return 127, b""


def parse_ids(text):
    """Return (id -> state map, bad_line_count). A row without id is not indexable."""
    rows = {}
    bad = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            bad += 1
            continue
        if not isinstance(row, dict):
            bad += 1
            continue
        rid = row.get("id")
        if rid is None:
            bad += 1
            continue
        rows[str(rid)] = str(row.get("state") or "").upper()
    return rows, bad


def read_blob(repo, sha, path):
    """Return (text or None, why)."""
    rc, out = _git(repo, "show", "%s:%s" % (sha, path))
    if rc != 0:
        return None, "git-show-rc=%d" % rc
    try:
        return out.decode("utf-8"), None
    except Exception:
        return None, "utf8-decode-failed"


def commit_series(repo, path, window):
    """Commits touching path, newest first, capped at window."""
    rc, out = _git(repo, "log", "--format=%H", "-n", str(window), "--", path)
    if rc != 0:
        return []
    return [l.strip() for l in out.decode("utf-8", "replace").splitlines() if l.strip()]


def classify_loss(lost_ids, older_rows):
    """Split lost ids into expected pruning and suspicious loss."""
    expected, suspicious = [], []
    for rid in sorted(lost_ids):
        state = older_rows.get(rid, "")
        item = {"id": rid, "state": state or "<none>"}
        (expected if state in TERMINAL_STATES else suspicious).append(item)
    return expected, suspicious


def census_target(repo, path, window):
    """Census one target file. Returns a dict (never raises on data problems)."""
    result = {
        "target": path,
        "tracked": False,
        "commits_scanned": 0,
        "not_scanned_older": 0,
        "unreadable": [],
        "transitions": 0,
        "removed_terminal": 0,
        "lost_alive": 0,
        "lost_transient": 0,
        "lost_permanent": 0,
        "lost_alive_ids": [],
        "bad_lines": 0,
        "verdict": "UNVERIFIED",
        "why": "",
    }
    rc, _ = _git(repo, "ls-files", "--error-unmatch", path)
    if rc != 0:
        rc2, _ = _git(repo, "cat-file", "-e", "HEAD:%s" % path)
        if rc2 != 0:
            result["why"] = "not-in-git"
            return result
    result["tracked"] = True
    shas = commit_series(repo, path, window)
    if not shas:
        result["why"] = "no-commits-in-window"
        return result
    result["commits_scanned"] = len(shas)
    rc, out = _git(repo, "rev-list", "--count", "HEAD", "--", path)
    total = int(out.decode("utf-8", "replace").strip() or 0) if rc == 0 else 0
    result["not_scanned_older"] = max(0, total - len(shas))

    # older -> newer pairs: shas[i+1] is older than shas[i]
    for newer, older in zip(shas[:-1], shas[1:]):
        old_text, why_old = read_blob(repo, older, path)
        new_text, why_new = read_blob(repo, newer, path)
        if old_text is None or new_text is None:
            result["unreadable"].append({
                "older": older[:8], "newer": newer[:8],
                "why": why_old or why_new})
            continue
        old_rows, bad_old = parse_ids(old_text)
        new_rows, bad_new = parse_ids(new_text)
        result["bad_lines"] += bad_old + bad_new
        result["transitions"] += 1
        lost = set(old_rows) - set(new_rows)
        if not lost:
            continue
        expected, suspicious = classify_loss(lost, old_rows)
        result["removed_terminal"] += len(expected)
        if suspicious:
            result["lost_alive"] += len(suspicious)
            for item in suspicious:
                item["older_commit"] = older[:8]
                item["newer_commit"] = newer[:8]
                result["lost_alive_ids"].append(item)

    if result["transitions"] == 0 and not result["unreadable"]:
        result["why"] = "no-comparable-pairs"
        return result
    if result["unreadable"] and result["transitions"] == 0:
        result["why"] = "all-pairs-unreadable"
        return result

    # A row that disappears and comes back is a transient write hazard (a concurrent
    # reader saw a hole), not a permanent loss. Keep them apart: overstating a
    # transient as permanent is the same class of error as hiding the loss.
    newest_text, why_newest = read_blob(repo, shas[0], path)
    newest_rows = parse_ids(newest_text)[0] if newest_text is not None else {}
    for item in result["lost_alive_ids"]:
        item["readded"] = item["id"] in newest_rows
    result["lost_transient"] = sum(1 for i in result["lost_alive_ids"] if i["readded"])
    result["lost_permanent"] = result["lost_alive"] - result["lost_transient"]
    if result["lost_permanent"]:
        result["verdict"] = "ROW-LOSS-FOUND"
    elif result["lost_transient"]:
        result["verdict"] = "ROW-LOSS-TRANSIENT"
    else:
        result["verdict"] = "ROW-LOSS-FREE"
    return result


def census(repo, targets, window):
    per = [census_target(repo, t, window) for t in targets]
    agg_permanent = sum(r["lost_permanent"] for r in per)
    agg_transient = sum(r["lost_transient"] for r in per)
    agg_lost = sum(r["lost_alive"] for r in per)
    scanned = [r["target"] for r in per if r["verdict"] != "UNVERIFIED"]
    unverified = [r["target"] for r in per if r["verdict"] == "UNVERIFIED"]
    if agg_permanent:
        verdict = "ROW-LOSS-FOUND"
    elif agg_transient:
        verdict = "ROW-LOSS-TRANSIENT"
    elif unverified and not scanned:
        verdict = "UNVERIFIED"
    elif unverified:
        verdict = "PARTIAL"
    else:
        verdict = "ROW-LOSS-FREE"
    return {
        "verdict": verdict,
        "window": window,
        "repo": repo,
        "domain": "targets=%d scanned=%d unverified=%d" % (len(per), len(scanned), len(unverified)),
        "not_scanned": "commits older than window=%d are outside this judgement" % window,
        "scope_note": "row loss = id present in older commit, absent from newer commit; terminal-state removal is counted separately; a row that returns in the newest scanned commit is LOST-TRANSIENT (hole visible to concurrent readers), otherwise LOST-PERMANENT",
        "lost_alive_total": agg_lost,
        "lost_transient_total": agg_transient,
        "lost_permanent_total": agg_permanent,
        "targets": per,
    }


def render(report, limit=12):
    lines = []
    lines.append("VERDICT=%s window=%d %s" % (report["verdict"], report["window"], report["domain"]))
    lines.append("not_scanned=%s" % report["not_scanned"])
    for r in report["targets"]:
        lines.append("[%s] %s commits=%d transitions=%d removed_terminal=%d lost_alive=%d (permanent=%d transient=%d) bad_lines=%d %s" % (
            r["verdict"], r["target"], r["commits_scanned"], r["transitions"],
            r["removed_terminal"], r["lost_alive"], r["lost_permanent"], r["lost_transient"],
            r["bad_lines"], ("why=" + r["why"]) if r["why"] else ""))
        for item in r["lost_alive_ids"][:limit]:
            lines.append("  %s id=%s state=%s %s->%s" % (
                "LOST-TRANSIENT" if item.get("readded") else "LOST-PERMANENT",
                item["id"], item["state"], item["older_commit"], item["newer_commit"]))
        if len(r["lost_alive_ids"]) > limit:
            lines.append("  ... %d more" % (len(r["lost_alive_ids"]) - limit))
        for u in r["unreadable"][:limit]:
            lines.append("  UNREADABLE %s->%s %s" % (u["older"], u["newer"], u["why"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- selftest

def _gitw(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args),
                                                  p.stderr.decode("utf-8", "replace")))
    return p.stdout.decode("utf-8", "replace")


FIX = [
    # (label, rows) committed in order
    ("c1", [{"id": "A", "state": "RUNNING"}, {"id": "B", "state": "DONE"},
            {"id": "C", "state": "RUNNING"}]),
    # c2: legit pruning of terminal B only -> no loss
    ("c2", [{"id": "A", "state": "RUNNING"}, {"id": "C", "state": "RUNNING"}]),
    # c3: whole-file overwrite drops live C -> LOSS
    ("c3", [{"id": "A", "state": "DONE"}]),
]


def cmd_selftest():
    checks, fails = 0, []

    def ok(name, cond, detail=""):
        nonlocal checks
        checks += 1
        if not cond:
            fails.append("%s %s" % (name, detail))

    tmp = tempfile.mkdtemp(prefix="rowloss-")
    try:
        repo = os.path.join(tmp, "r")
        os.makedirs(repo)
        _gitw(repo, "init", "-q")
        _gitw(repo, "config", "user.name", "selftest")
        _gitw(repo, "config", "user.email", "selftest@local")
        target = "q.jsonl"
        path = os.path.join(repo, target)
        shas = []
        for label, rows in FIX:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _gitw(repo, "add", target)
            _gitw(repo, "commit", "-q", "-m", label)
            shas.append(_gitw(repo, "rev-parse", "HEAD").strip())

        # S1: parse_ids counts ids and skips bad lines
        rows, bad = parse_ids('{"id":"X","state":"done"}\nnot json\n{"state":"DONE"}\n')
        ok("S1 parse", rows == {"X": "DONE"} and bad == 2, str((rows, bad)))

        # S2: classify_loss splits terminal from alive
        exp, sus = classify_loss({"B", "C"}, {"B": "DONE", "C": "RUNNING"})
        ok("S2 classify", [i["id"] for i in exp] == ["B"] and [i["id"] for i in sus] == ["C"],
           str((exp, sus)))

        # S3: whole-history census finds exactly C (terminal B not counted)
        rep = census(repo, [target], 10)
        t = rep["targets"][0]
        ok("S3 verdict", rep["verdict"] == "ROW-LOSS-FOUND", rep["verdict"])
        ok("S3 lost", [i["id"] for i in t["lost_alive_ids"]] == ["C"], str(t["lost_alive_ids"]))
        ok("S3 terminal", t["removed_terminal"] == 1, str(t["removed_terminal"]))
        ok("S3 pairs", t["transitions"] == 2, str(t["transitions"]))

        # S4: window=1 has no comparable pair -> UNVERIFIED, not a clean pass
        rep1 = census(repo, [target], 1)
        ok("S4 window1", rep1["verdict"] == "UNVERIFIED", rep1["verdict"])

        # S5: window truncation is declared, not silent
        ok("S5 not_scanned", rep["targets"][0]["not_scanned_older"] == 0,
           str(rep["targets"][0]["not_scanned_older"]))
        rep2 = census(repo, [target], 2)
        ok("S5 truncation", rep2["targets"][0]["not_scanned_older"] == 1,
           str(rep2["targets"][0]["not_scanned_older"]))

        # S6: untracked file -> UNVERIFIED with reason, never ROW-LOSS-FREE
        with open(os.path.join(repo, "ghost.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"id":"G","state":"RUNNING"}\n')
        rg = census(repo, ["ghost.jsonl"], 10)
        ok("S6 untracked", rg["verdict"] == "UNVERIFIED" and rg["targets"][0]["why"] == "not-in-git",
           str(rg["verdict"]))

        # S7: mixed domain reports PARTIAL, not a full green
        rm = census(repo, [target, "ghost.jsonl"], 10)
        ok("S7 partial", rm["verdict"] == "ROW-LOSS-FOUND", rm["verdict"])

        # S8: non-terminal state change (RUNNING -> DONE) is not a loss
        base = os.path.join(tmp, "r2")
        os.makedirs(base)
        _gitw(base, "init", "-q")
        _gitw(base, "config", "user.name", "selftest")
        _gitw(base, "config", "user.email", "selftest@local")
        p2 = os.path.join(base, "q.jsonl")
        for state in ("RUNNING", "DONE"):
            with open(p2, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"id": "A", "state": state}) + "\n")
            _gitw(base, "add", "q.jsonl")
            _gitw(base, "commit", "-q", "-m", state)
        r8 = census(base, ["q.jsonl"], 10)
        ok("S8 state change", r8["verdict"] == "ROW-LOSS-FREE" and r8["targets"][0]["lost_alive"] == 0,
           r8["verdict"])

        # S9: determinism (same input, same result)
        ok("S9 determinism", census(repo, [target], 10)["lost_alive_total"] == rep["lost_alive_total"])

        # S10: render mentions the reason for unverified targets
        txt = render(rg)
        ok("S10 render", "UNVERIFIED" in txt and "why=not-in-git" in txt, txt.splitlines()[0])

        # S11: a row that vanishes and returns is TRANSIENT, not a clean pass and not permanent
        base3 = os.path.join(tmp, "r3")
        os.makedirs(base3)
        _gitw(base3, "init", "-q")
        _gitw(base3, "config", "user.name", "selftest")
        _gitw(base3, "config", "user.email", "selftest@local")
        p3 = os.path.join(base3, "q.jsonl")
        for rows in ([{"id": "A", "state": "RUNNING"}], [],
                     [{"id": "A", "state": "RUNNING"}]):
            with open(p3, "w", encoding="utf-8", newline="\n") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _gitw(base3, "add", "q.jsonl")
            _gitw(base3, "commit", "-q", "-m", "c%d" % len(rows))
        r11 = census(base3, ["q.jsonl"], 10)
        ok("S11 transient verdict", r11["verdict"] == "ROW-LOSS-TRANSIENT", r11["verdict"])
        ok("S11 transient tags",
           r11["targets"][0]["lost_transient"] == 1 and r11["targets"][0]["lost_permanent"] == 0,
           str((r11["targets"][0]["lost_transient"], r11["targets"][0]["lost_permanent"])))
        ok("S11 permanent tag", rep["targets"][0]["lost_permanent"] == 1,
           str(rep["targets"][0]["lost_permanent"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for f in fails:
        print("FAIL " + f)
    print("SELFTEST row_loss_census %d/%d PASS" % (checks - len(fails), checks))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description="shared jsonl ledger row-loss census")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--repo", default=None)
    r.add_argument("--target", action="append", default=None)
    r.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    r.add_argument("--json-out", default=None)
    sub.add_parser("selftest")
    args = ap.parse_args()

    if args.cmd == "selftest":
        return cmd_selftest()
    if args.cmd != "run":
        ap.print_help()
        return 3

    repo = args.repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo = os.path.abspath(repo)
    targets = tuple(args.target) if args.target else DEFAULT_TARGETS
    report = census(repo, targets, args.window)
    print(render(report))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("JSON out: %s" % args.json_out)
    if report["verdict"] == "ROW-LOSS-FOUND":
        return 2
    if report["verdict"] in ("UNVERIFIED", "PARTIAL"):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())