#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""idle_selfcheck.py — §9.6 闲置硬触发闭环·每轮自检步（O-20261007-2315 承接·BC-P-54 批活）
+ serve_liveness 探活扩展（BC-P-55·M57④ 10-08 serve 无声死亡 ~20min 事故实证派生——
探针+轮报告 P1 行本窗落地·净重启自动动作随批非自决维持）

构造化执法（§9.6.1-2）：读本机资源面 → GREEN-IDLE 达档〔§8.2：RAM 空闲≥40% 且
VRAM≥6GB〕→ 领池检（fleet/backlog 可领行计数）→ 池空拉本司三队列议程计数 →
两空写 agenda_starved 旗；连续 ≥2 轮达档未领单 → idle_rounds +1（机内自动·不等夜轮）。
心跳双字段经 round_append.py 唯一路径落 state/heartbeat.txt（禁 shell Add-Content·T24）。

判据（预注册·回访 10-13/14 并窗）：
  J1 资源读数面：RAM 空闲% = ctypes GlobalMemoryStatusEx 实读；VRAM free MiB =
     nvidia-smi 单源直读（O-20261006-2358 face 2·WMI 已知 bug 不用）
  J2 计数面：pool_claimable = fleet/backlog.md 含「可领」且无 claimed@ 的行数；
     queue_open = state/queue/{main,tech,explore}.md 含 open 的任务行数
  J3 写路面：双字段行经 round_append.py append 落 state/heartbeat.txt；
     轮间状态件 state/idle_selfcheck.json（consecutive_idle/idle_rounds 持久化）
  J4 selftest：RAM/VRAM 探针数值断言 + backlog 解析夹具断言 + 状态件 temp 往返
     + round_append 在位断言——零真实状态文件副作用
  J5 serve 探活（BC-P-55）：/api/tags 2s 超时只读 GET·alive=http200；
     死亡（连接拒/超时/非 200）→ check 落 P1 行（共租户受损预警面）·自动重启随批
  J6 task-liveness 探针（T61·BC-P-57 批活化·R-51 缓解②·10-08 13:18 GPU-IdleWatch
     静默失能 ~3h 事故派生）：schtasks 态只读探三任务（GPU-IdleWatch/OrderSentinel/
     OSLoop）——非 Ready/Running 即心跳告警行；pause 律性失能豁免=OSLoop 锚+集群
     辅证双条件（machine-state.ps1 -Mode pause 必整集 Disable 含 OSLoop→OSLoop
     Disabled 且暂停集 Disabled ≥4=律性态零告警；OSLoop 单体失能≠pause=轮死最高
     警级·禁误豁免）·任务缺失（Absent）=事故态告警
  J7 night-watch 哨（T62·R-51 缓解③·探针频次升窗）：OrderSentinel 15min tick 内建
     夜盲窗任务活性哨（pythonw 静默 fire-and-forget·夜窗检测延迟轮频 ~14h→≤15min
     达预注册 ≤30min 预算）——锁存去重（同异常签名只告警一次防 tick 风暴）·恢复=
     清锁存静默·正常零追加（预注册「正常静默」）·pause 豁免同 J6 双条件·
     OrderSentinel 自身失能=自探盲区如实记（其态由轮频探针 J6 覆盖）

用法：
  python Tools/idle_selfcheck.py check       # 每轮自检步（iteration_loop.ps1 内建）
  python Tools/idle_selfcheck.py night_watch  # T62 tick 哨（静默·异常才写心跳）
  python Tools/idle_selfcheck.py selftest
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEARTBEAT = os.path.join(PROJECT, "state", "heartbeat.txt")
STATE_FILE = os.path.join(PROJECT, "state", "idle_selfcheck.json")
LATCH_FILE = os.path.join(PROJECT, "state", "sentinel-taskface.latch")
NIGHT_LOG = os.path.join(PROJECT, "state", "sentinel-nightwatch.log")
ROUND_APPEND = os.path.join(PROJECT, "Tools", "round_append.py")
BACKLOG = os.path.abspath(os.path.join(
    PROJECT, "..", "..", "quant", "BigMoney", "fleet", "backlog.md"))
QUEUE_DIR = os.path.join(PROJECT, "state", "queue")

# §8.2 旗标判据（resource-chain L101·预注册·禁调参不经立法）
RAM_FREE_PCT_MIN = 40.0
VRAM_FREE_MB_MIN = 6 * 1024
CONSECUTIVE_TRIGGER = 2  # §9.6.2 两读触发
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"  # BC-P-55 探活端点（只读）
# J6（T61/BC-P-57）：任务态探针三任务（非 Ready/Running 即告警）
TASK_LIVENESS_PROBE = ("BigCompute-GPU-IdleWatch", "BigCompute-OrderSentinel",
                       "BigCompute-OSLoop")
# machine-state.ps1 -Mode pause 暂停集（bm-a LOCALIZE-1 全集）——pause 律性态判定源
MACHINE_PAUSE_SET = ("BigCompute-OSLoop", "BigCompute-OSLoop-PM",
                     "BigCompute-GPU-IdleWatch", "BigCompute-CleanWindowProbe",
                     "BigCompute-OrderSentinel", "BigCompute-ResidentQA",
                     "MiniGameOllamaKeepWarm", "MiniGameOllamaServe")
ALIVE_STATES = ("Ready", "Running")
PAUSE_CORROBORATE_MIN = 4  # pause 必整集 Disable；辅证下限防 OSLoop 单体失能误豁免


class MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def probe_ram_free_pct():
    """J1：RAM 空闲% 实读（GlobalMemoryStatusEx）。失败=-1.0（不达标面）。"""
    try:
        s = MemoryStatusEx()
        s.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):
            return round(100.0 * s.ullAvailPhys / s.ullTotalPhys, 1)
    except Exception:
        pass
    return -1.0


def probe_vram_free_mb():
    """J1：VRAM free MiB 实读（nvidia-smi 单源·O-20261006-2358 face 2）。失败=-1。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        vals = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
        return min(vals) if vals else -1
    except Exception:
        return -1


def probe_serve_liveness(url=OLLAMA_TAGS_URL, timeout=2):
    """J5（BC-P-55）：serve 探活——/api/tags 只读 GET·2s 超时。

    返回 (alive, detail)：alive=http200；死亡=连接拒/超时/非 200，
    detail=异常类名或 http<code>（心跳行诊断位·app.log 事故面 10-08 在案）。
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status == 200:
                return True, "http200"
            return False, "http%d" % r.status
    except Exception as e:
        return False, type(e).__name__


def probe_task_states(names):
    """J6（T61/BC-P-57）：schtasks 态只读探——Get-ScheduledTask State 枚举
    （locale 无关）。返回 {name: state}；缺失任务/查询失败 → Absent/ProbeError。"""
    ps = ("Get-ScheduledTask -TaskName %s -ErrorAction SilentlyContinue | "
          "ForEach-Object { $_.TaskName + '=' + $_.State }"
          % ",".join("'%s'" % n for n in names))
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=15,
                             encoding="utf-8", errors="replace")
        states = {}
        for line in out.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                states[k.strip()] = v.strip()
        for n in names:
            states.setdefault(n, "Absent")
        return states
    except Exception:
        return {n: "ProbeError" for n in names}


def classify_task_face(states):
    """J6 两态判定（BC-P-57 判负路径豁免面）：pause 律性态=OSLoop 锚+暂停集
    Disabled ≥4 辅证（machine-state pause 必整集 Disable）→全探针豁免；
    集群在活而探针任务单体非 Ready/Running=事故态告警（10-08 13:18 判例）。"""
    disabled_n = sum(1 for n in MACHINE_PAUSE_SET
                     if states.get(n) == "Disabled")
    pause_mode = (states.get("BigCompute-OSLoop") == "Disabled"
                  and disabled_n >= PAUSE_CORROBORATE_MIN)
    if pause_mode:
        return True, []
    alerts = [n for n in TASK_LIVENESS_PROBE
              if states.get(n) not in ALIVE_STATES]
    return False, alerts


def count_pool_claimable(backlog_path=BACKLOG):
    """J2：备货池可领行数（含「可领」且未 claimed@——池律原文行尾记账）。"""
    if not os.path.exists(backlog_path):
        return 0
    n = 0
    with open(backlog_path, encoding="utf-8") as f:
        for line in f:
            if "claimed@" in line:
                continue
            if "可领" in line:
                n += 1
    return n


def count_queue_open(queue_dir=QUEUE_DIR):
    """J2：本司三队列 open 任务行数（行含 open 且非表头/分隔行）。"""
    n = 0
    for name in ("main.md", "tech.md", "explore.md"):
        p = os.path.join(queue_dir, name)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.lstrip().startswith("|"):
                    continue
                if "---" in line:
                    continue
                if "open" in line.lower():
                    n += 1
    return n


def load_state(path=STATE_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"consecutive_idle": 0, "idle_rounds": 0,
                "last_verdict": "-", "last_check": "-"}


def save_state(st, path=STATE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def append_heartbeat(line, heart=HEARTBEAT):
    """J3：round_append 唯一路径（幂等护栏由其内置）。返回 (code, msg)。"""
    if not os.path.exists(ROUND_APPEND):
        return 4, "round_append.py missing"
    r = subprocess.run(
        [sys.executable, ROUND_APPEND, "append", "--file", heart,
         "--line", line], capture_output=True, text=True, timeout=15,
        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or r.stderr).strip()[:120]


def night_task_watch(states=None, latch_path=LATCH_FILE, recorder=None):
    """J7（T62·R-51 缓解③）：夜盲窗任务活性哨——OrderSentinel 15min tick 内建
    （pythonw 静默 fire-and-forget·宿主接线=order_sentinel.ps1）。判据=延迟预算
    ≤30min（tick 15min 达标）vs 改动量（本函数+tick 接线块）。锁存去重：同一
    异常签名只告警一次（持续异常零追加·防 15min tick 风暴）；恢复=清锁存静默；
    正常=零追加（预注册「正常静默零追加」）。pause 律性豁免同 J6 双条件。
    pythonw 无 stdout——本函数零 print（崩溃痕唯一出口=NIGHT_LOG·main 面）。"""
    if states is None:
        states = probe_task_states(MACHINE_PAUSE_SET)
    probe_error = all(v == "ProbeError" for v in states.values())
    if probe_error:
        alerts = []
        sig = "probe-error"
    else:
        pause_mode, alerts = classify_task_face(states)
        if pause_mode:
            alerts = []
        sig = "|".join(sorted(alerts))
    prev = ""
    if os.path.exists(latch_path):
        try:
            with open(latch_path, encoding="utf-8") as f:
                prev = f.read().strip()
        except Exception:
            prev = ""
    if not sig:
        # 正常态（含 pause 豁免）：恢复即清锁存·静默零追加
        if os.path.exists(latch_path):
            try:
                os.unlink(latch_path)
            except Exception:
                pass
        return 0
    if prev == sig:
        return 0  # 同签名已告警：锁存去重（持续异常零追加·防 tick 风暴）
    try:
        with open(latch_path, "w", encoding="utf-8") as f:
            f.write(sig)
    except Exception:
        pass
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if sig == "probe-error":
        line = ("%s P1 task_liveness night-watch: probe-error — schtasks "
                "态查询失败（夜盲窗检测面失明·修复=探针链自查）〔T62·R-51 缓解③·"
                "锁存 sig=probe-error〕" % now)
    else:
        line = ("%s P1 task_liveness night-watch: %s 非活态（%s）— T62 夜盲窗哨"
                "〔OrderSentinel 15min tick·R-51 缓解③·锁存去重 sig=%s〕·修复面="
                "schtasks enable 复活·pause 集签名未达=非律性失能禁豁免"
                % (now, ",".join(alerts),
                   ";".join("%s=%s" % (n, states.get(n)) for n in alerts), sig))
    if recorder is not None:
        recorder(line)  # selftest 注入面（零真实 heartbeat 副作用）
    else:
        append_heartbeat(line)
    return 1


def check():
    ram_pct = probe_ram_free_pct()
    vram_mb = probe_vram_free_mb()
    green_idle = (ram_pct >= RAM_FREE_PCT_MIN and vram_mb >= VRAM_FREE_MB_MIN)
    pool = count_pool_claimable()
    q_open = count_queue_open()
    serve_alive, serve_detail = probe_serve_liveness()
    tstates = probe_task_states(MACHINE_PAUSE_SET)
    probe_error = all(v == "ProbeError" for v in tstates.values())
    if probe_error:
        pause_mode, task_alerts, task_face = False, [], "probe-error"
    else:
        pause_mode, task_alerts = classify_task_face(tstates)
        task_face = ("pause-exempt" if pause_mode else
                     ("ALERT:" + ",".join(task_alerts) if task_alerts
                      else "ok"))

    st = load_state()
    if green_idle and pool == 0 and q_open == 0:
        # 达档且无单可领无议程可拉 → 饥荒面：连击计数与 idle_rounds 递增（§9.6.2）
        st["consecutive_idle"] = st.get("consecutive_idle", 0) + 1
        if st["consecutive_idle"] >= CONSECUTIVE_TRIGGER:
            st["idle_rounds"] = st.get("idle_rounds", 0) + 1
        starved = True
    else:
        # 忙面/有单/有议程 → 连击清零（RED 消费仍看 idle_rounds 累计值）
        st["consecutive_idle"] = 0
        starved = False
    verdict = "green-idle" if green_idle else "busy"
    st["last_verdict"] = verdict
    st["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
    st["last_read"] = {"ram_free_pct": ram_pct, "vram_free_mb": vram_mb,
                       "pool_claimable": pool, "queue_open": q_open,
                       "serve_alive": serve_alive, "serve_detail": serve_detail,
                       "task_face": task_face,
                       "task_states": {n: tstates.get(n) for n in
                                       TASK_LIVENESS_PROBE}}
    save_state(st)

    line = ("%s idle_selfcheck: verdict=%s ram_free_pct=%s vram_free_mb=%s "
            "pool_claimable=%d queue_open=%d consecutive_idle=%d "
            "idle_rounds=%d agenda_starved=%s serve_alive=%s serve_detail=%s"
            " task_face=%s"
            % (time.strftime("%Y-%m-%d %H:%M:%S"), verdict, ram_pct,
               vram_mb, pool, q_open, st["consecutive_idle"],
               st["idle_rounds"], str(starved).lower(),
               str(serve_alive).lower(), serve_detail, task_face))
    code, msg = append_heartbeat(line)
    print(line)
    print("append: code=%d %s" % (code, msg))
    if not serve_alive:
        # BC-P-55：serve 死亡 → P1 轮报告行（共租户受损预警·修复面=净重启托盘链
        # 配方在案 M57·自动执行随批非自决）
        p1 = ("%s P1 serve_liveness: OLLAMA DOWN detail=%s — 共租户"
              "（BigMoney L1/BigLife QA/bge-m3）受损预警·修复=净重启托盘链"
              "（M57 配方）·自动重启随批（BC-P-55）"
              % (time.strftime("%Y-%m-%d %H:%M:%S"), serve_detail))
        code2, msg2 = append_heartbeat(p1)
        print(p1)
        print("append-p1: code=%d %s" % (code2, msg2))
    if probe_error:
        # J6 探针自身故障=检测面失明（P1·与任务告警分面）
        p1e = ("%s P1 task_liveness: probe-error — schtasks 态查询失败"
               "（检测面失明·修复=探针链自查）" % time.strftime(
                   "%Y-%m-%d %H:%M:%S"))
        code3, msg3 = append_heartbeat(p1e)
        print(p1e)
        print("append-p1e: code=%d %s" % (code3, msg3))
    elif task_alerts and not pause_mode:
        # J6（T61/BC-P-57·R-51 缓解②）：任务单体非 Ready=事故态告警
        #（10-08 13:18 GPU-IdleWatch 判例同型·修复面=enable 复活·pause 未达=非律性）
        p1t = ("%s P1 task_liveness: %s 非活态（%s）— R-51 事故态告警"
               "（13:18 判例同型）·修复面=schtasks enable 复活·pause 集签名"
               "未达=非律性失能禁豁免" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                ",".join(task_alerts),
                ";".join("%s=%s" % (n, tstates.get(n))
                         for n in task_alerts)))
        code3, msg3 = append_heartbeat(p1t)
        print(p1t)
        print("append-p1t: code=%d %s" % (code3, msg3))
    return 0 if code in (0, 2) else 1


def selftest():
    """J4：四断言干跑——探针/解析/状态往返/写路在位。零真实状态文件副作用。"""
    ok = True
    ram = probe_ram_free_pct()
    vram = probe_vram_free_mb()
    print("J1 probe: ram_free_pct=%s vram_free_mb=%s" % (ram, vram))
    ok &= isinstance(ram, float) and ram > 0
    ok &= isinstance(vram, int) and vram > 0

    fx = tempfile.NamedTemporaryFile("w", suffix=".md",
                                      encoding="utf-8", delete=False)
    fx.write("# fixture\n1. 【A】可领\n2. 【B】可领（claimed@bm-a@10-07 22:33）\n"
             "3. 【C】claimed@bm-c@10-01 → done@10-02\n4. 【D】可领\n")
    fx.close()
    n = count_pool_claimable(fx.name)
    print("J2 backlog parse fixture: claimable=%d (expect 2)" % n)
    ok &= (n == 2)
    os.unlink(fx.name)

    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "st.json")
        save_state({"consecutive_idle": 1, "idle_rounds": 0}, p)
        st = load_state(p)
        print("J3 state round-trip: %s" % st)
        ok &= (st["consecutive_idle"] == 1)
    print("J4 round_append present: %s" % os.path.exists(ROUND_APPEND))
    ok &= os.path.exists(ROUND_APPEND)

    alive, detail = probe_serve_liveness()
    alive_dn, detail_dn = probe_serve_liveness(
        "http://127.0.0.1:1/api/tags")  # 闭合端口=死亡路径夹具（零副作用）
    print("J5 serve liveness: live=(%s,%s) down=(%s,%s)"
          % (alive, detail, alive_dn, detail_dn))
    ok &= isinstance(alive, bool)
    ok &= (alive_dn is False and bool(detail_dn))

    # J6 task-liveness（T61/BC-P-57）：两态判定纯函数夹具 + 只读探真跑
    base = {n: "Ready" for n in MACHINE_PAUSE_SET}
    inc = dict(base, **{"BigCompute-GPU-IdleWatch": "Disabled"})  # 13:18 判例
    pau = {n: "Disabled" for n in MACHINE_PAUSE_SET}              # pause 整集
    solo = dict(base, **{"BigCompute-OSLoop": "Disabled"})        # OSLoop 单体
    run = dict(base, **{"BigCompute-OrderSentinel": "Running"})   # Running 活
    c_inc, c_pau, c_solo, c_run = (classify_task_face(inc),
                                  classify_task_face(pau),
                                  classify_task_face(solo),
                                  classify_task_face(run))
    live_t = probe_task_states(MACHINE_PAUSE_SET)  # 只读真跑
    print("J6 task-liveness: incident=%s pause=%s osloop-solo=%s "
          "running=%s live3=%s" % (c_inc, c_pau, c_solo, c_run,
          {n: live_t.get(n) for n in TASK_LIVENESS_PROBE}))
    ok &= (c_inc == (False, ["BigCompute-GPU-IdleWatch"]))
    ok &= (c_pau == (True, []))
    ok &= (c_solo == (False, ["BigCompute-OSLoop"]))
    ok &= (c_run == (False, []))
    ok &= all(live_t.get(n) for n in TASK_LIVENESS_PROBE)

    # J7 night-watch（T62·R-51 缓解③）：锁存去重哨夹具——temp 锁存+注入
    # recorder·零真实 heartbeat/latch 副作用（五路径：新告警/去重/签名变更/
    # 恢复清锁存/pause 豁免）
    with tempfile.TemporaryDirectory() as td:
        lat = os.path.join(td, "latch")
        rec = []
        n_all = {n: "Ready" for n in MACHINE_PAUSE_SET}
        n_inc = dict(n_all, **{"BigCompute-GPU-IdleWatch": "Disabled"})
        n_pau = {n: "Disabled" for n in MACHINE_PAUSE_SET}
        n_err = {n: "ProbeError" for n in MACHINE_PAUSE_SET}
        r1 = night_task_watch(n_inc, lat, rec.append)   # 新事故→告警 1
        r2 = night_task_watch(n_inc, lat, rec.append)   # 同签名→去重 0
        r3 = night_task_watch(n_err, lat, rec.append)   # 签名变更→再告警 1
        r4 = night_task_watch(n_all, lat, rec.append)   # 恢复→清锁存 0
        r5 = night_task_watch(n_pau, lat, rec.append)   # pause 豁免→零追加 0
        ok &= (r1, r2, r3, r4, r5) == (1, 0, 1, 0, 0)
        ok &= (len(rec) == 2) and (not os.path.exists(lat))
        ok &= ("night-watch" in rec[0]) and ("probe-error" in rec[1])
        print("J7 night-watch: alert=%d dedup=%d resig=%d recover=%d "
              "pause=%d alerts=%d latch-cleared=%s"
              % (r1, r2, r3, r4, r5, len(rec), not os.path.exists(lat)))
    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "night_watch":
        # T62 tick 哨：pythonw 无 stdout——零 print·崩溃痕唯一出口=NIGHT_LOG
        try:
            sys.exit(night_task_watch())
        except Exception as e:
            try:
                with open(NIGHT_LOG, "a", encoding="utf-8") as f:
                    f.write("%s night_watch crash: %r\n"
                            % (time.strftime("%Y-%m-%d %H:%M:%S"), e))
            except Exception:
                pass
            sys.exit(3)
    sys.exit(check() if cmd == "check" else selftest())
