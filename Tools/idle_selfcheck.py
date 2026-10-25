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
  J8 py_cpu 实读（O-20261011-0012 ⅴ 承接·tech T76）：Win32_Processor
     LoadPercentage 单发只读快照（locale 无关·审计面报 py_cpu 字段）——满载线
     执法判据面在 BigMoney GM 窗（本司=审计报数字段）·失败=-1.0 不判忙闲
  J9 cpu-lane 闲判（T85·O-20261013-2215 ① 承接链）：CPU 车道=RAM 单闸
     （classify_cpu_lane_idle）·GREEN-IDLE（GPU 车道·§8.2 预注册双闸）不动
     ——10-10 18:26 判例「vram_free 闸误挡纯 CPU 批」触发面修复·饥饿计数闸
     随 CPU 面常开（O-2215）收窄为 RAM 单闸
  J11 stale-m 守卫（T105·BC-P-78 批活化·T98 承继·J10 位保留给 BC-P-75）：
     他方会话工作面陈旧 M 态探针——git status --short 工作树 M 件 × mtime 龄
     >24h → P2 心跳行（日清承载）；自产 M 件按轮收尾定向 commit 纪律必为新鲜
     态=年龄闸即「非本轮产出」判别器；日粒度锁存去重（同文件同日一警防 tick
     风暴）·恢复清锁存静默·正常零追加·探针坏=缺省行零阻塞（fail-open）
  J14 收尾 C1C4 行机械校验（T125·10-26 承接）：rounds.log 轮行须含 `C1C4:`
     段且与最新 state/c1c4-*.json 证据票一致——「收尾是否真跑了 C1-C4」从
     自觉改为可查。缺行=MISSING／停滞 >3 轮=STALE／票与证据不一致=MISMATCH／
     证据缺=NOEVID → P2 advisory 心跳行（不阻断轮·读不出=UNVERIFIED fail-open）
  J15 日清面新鲜度（T129·10-26 承接 D-20261026-03）：HQ-FEEDBACK.md 日清区按
     **内容日期**判鲜（mtime 会被无 diff 触碰刷新——10-25 22:44 判例：mtime 新
     而内容停在昨笔/前笔），并与 rounds.log 最新轮行日期对表：轮已更新而日清
     未更=漏写 MISSED（比单纯 STALE 更确定）。FRESH／STALE(>24h)／MISSED／
     MISSING(零日清行)／UNVERIFIED → P2 advisory 心跳行·只出读数不改日清文本

用法：
  python Tools/idle_selfcheck.py check       # 每轮自检步（iteration_loop.ps1 内建）
  python Tools/idle_selfcheck.py night_watch  # T62 tick 哨（静默·异常才写心跳）
  python Tools/idle_selfcheck.py selftest
"""
import ctypes
import datetime
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
# J13（D-20261024-14 承接）：供给地板可点火口径——fleet 机器状态源与阈值
MACHINES_DIR = os.path.abspath(os.path.join(
    PROJECT, "..", "..", "quant", "BigMoney", "fleet", "machines"))
SELF_MACHINE = "bm-a"
IGNITABLE_SILENT_MIN = 120.0   # 与集团口径同源（owner_silent_min 120）
SUPPLY_FLOOR_IGNITABLE = 3     # 与 bigmoney SUPPLY_FLOOR_READY 同值
CLAIM_RE = re.compile(r"claimed@([A-Za-z0-9_\-]+)@")
# J13b（M86·10-24 夜轮）：本机自 claim 陈旧诊断——ready_ignitable 把「本机
# 17 天前 claim 后零交付」也算可点火，breach 面因此虚高。此处只加诊断列，
# 不改 ready_ignitable/breach 的集团同源语义（对齐律优先），另出 effective 面。
CLAIM_TS_RE = re.compile(r"claimed@([A-Za-z0-9_\-]+)@([0-9T:+.\- ]{4,32})")
SELF_STALE_DAYS = 3.0  # 预注册：本机 claim 超 3 日未 done = 陈旧自占用（非新供给）
# J13c（T124·BC-P-105）：供给池 lane 归属闸——池行首【司标签】=交付司；机队机器
# 皆为集团中枢资产（cph4/resource-chain §9.1 三车道无子公司归属），故 claim 机默认
# 司=HQ。claim 机司 ≠ 交付司标签 = lane_mismatch：该行账面可领但非本车道新供给，
# 不计入 effective_ignitable（ready_ignitable 维持集团同源语义，诊断列不删）。
DEPT_TAG_RE = re.compile(r"【([^】]{1,24})】")
MACHINE_DEPT = {"bm-a": "HQ", "bm-b": "HQ", "bm-c": "HQ", "bm-d": "HQ"}
DEPT_ALIAS = {"集团": "HQ", "FluxGroup": "HQ", "fluxgroup": "HQ", "HQ": "HQ"}
# J11（T105·BC-P-78）：陈旧 M 态守卫常量——真实 git 路径优先（PATH git=
# gitsilent 转发器吞 stdout·10-08 判例·探针零输出=假净面）
STALE_M_LATCH = os.path.join(PROJECT, "state", "stale-m.latch.json")
STALE_M_AGE_HOURS = 24.0  # BC-P-78 预注册阈值（自产 M 必新鲜=判别器）
GIT_REAL = os.environ.get("GIT_REAL", r"C:\Program Files\Git\cmd\git.exe")
# J14（T125·10-26）：收尾 C1C4 行机械校验——rounds.log 轮行锚=「 tokens: 」段；
# 停滞口径按轮行数（非墙钟）计：暂停窗/让路窗不产轮行，按墙钟会虚报停滞。
ROUNDS_LOG = os.path.join(PROJECT, "state", "rounds.log")
C1C4_MAX_AGE_ROUNDS = 3  # T125 预注册：连续 >3 轮无 C1C4 行=停滞告警
C1C4_RE = re.compile(r"C1C4:([A-Z0-9][A-Z0-9\-]*)")
# J15（T129·10-26）：日清面新鲜度校验——mtime 会被无 diff 触碰刷新（10-25 22:44
# 判例：文件 mtime 新而内容停在 10-24 尾笔），故按**内容日期**判鲜；并与 rounds.log
# 最新轮行日期对表：轮已更新而日清偿未更=漏写（MISSED·比单纯陈旧更确定）。
HQ_FEEDBACK = os.path.join(PROJECT, "HQ-FEEDBACK.md")
HQ_MAX_AGE_HOURS = 24.0  # C-20261009-02 派单① 快速件 SLA 12h 之上的宽容告警线
HQ_LINE_RE = re.compile(
    r"日清\s+(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2})"
    r"(?:\s+(\d{1,2}):([0-9xX]{2}))?")
ROUND_ROW_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) .* tokens: ")

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


def probe_py_cpu():
    """J8（T76·O-20261011-0012 ⅴ）：CPU 利用率% 实读——Win32_Processor
    LoadPercentage 单发快照（多槽取均值·locale 无关）。失败=-1.0（不判忙闲）。"""
    ps = ("Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | "
          "Measure-Object -Property LoadPercentage -Average | "
          "ForEach-Object { $_.Average }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=10,
                             encoding="utf-8", errors="replace")
        val = float(out.stdout.strip())
        return round(val, 1)
    except Exception:
        return -1.0


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


LANE8090_URL = "http://127.0.0.1:8090/v1/models"  # 35B 车道（resident_qa_server 默认 LLM_URL）


def probe_lane8090(url=LANE8090_URL, timeout=2, _get=None, _port_open=None):
    """J13（T89）：:8090 车道实况——只看不说谎，三态 token。

    该端口=coder serve 车道（resident_qa_server 默认 LLM_URL 亦指此端口）。
    背景（2026-10-25 12:1x 实读）：:8090 被外部进程 eCloud.exe 占住（pid 28008 实读），
    车道无法 bind → /ask 实测挂死 40s，而 J5 只探 11434，实况面照样报
    serve_alive=true=假绿、QA smoke 只按 12h 一次才暴露。本探针把它显式化：

      UP               = HTTP 200（车道在服）且非本司进程占位时仍如实报 UP
      DARK[OCCUPIED]   = 端口可连但 HTTP 失败（他人占位/无 HTTP 服务）
      DARK[DOWN]       = 端口不可连（无监听）

    informational·fail-open（探针自身异常不改变 verdict，只落 token）。
    注：若 RESIDENT_QA_LLM_URL 已切到 bm-d（O-20261015-1335 env 覆盖），本地 8090
    无服属预期——消费方须同时看该 env，勿单凭本 token 判故障。
    """
    def _default_get():
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status

    def _default_port_open():
        import socket
        s = socket.socket()
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", 8090))
            return True
        except Exception:
            return False
        finally:
            s.close()

    get = _get or _default_get
    port_open = _port_open or _default_port_open
    try:
        status = get()
        return "UP" if status == 200 else "DARK[http%s]" % status
    except Exception:
        return "DARK[OCCUPIED]" if port_open() else "DARK[DOWN]"


def probe_model_drift(baseline_path=None, _diff=None):
    """J12（BC-P-103 ③）：模型集基线漂移 informational 读数。

    复用 Tools/model_ref_drift_check（baseline-diff 同源·禁漂移）：读基线
    live_tags vs 现役 /api/tags → "DRIFT(m=2,a=0)" / "GREEN(m=0,a=0)" /
    "UNVERIFIED(m=0,a=0)"。纯观测零断链：任何异常 → "UNVERIFIED(err)"
    （fail-open·不阻断自检·不触发告警——告警面归 baseline-diff 显式命令）。
    """
    path = baseline_path or os.path.join(
        PROJECT, "state", "model-ref-drift-20261016-191916.json")
    try:
        sys.path.insert(0, os.path.join(PROJECT, "Tools"))
        import model_ref_drift_check as mrd
        diff_fn = _diff or (lambda b, l: mrd.diff_baseline(b, l))
        caps = mrd.load_baseline(path)
        live = None if _diff else mrd.fetch_live_tags()  # 注入面=离线自测
        res = diff_fn(caps, live)
        return "%s(m=%d,a=%d)" % (res["status"], len(res["missing"]),
                                  len(res["added"]))
    except Exception:
        return "UNVERIFIED(err)"


def latest_c1c4_evidence(evidence_dir=None):
    """J14（T125）：最新 C1C4 证据票——state/c1c4-<yyyymmdd>-<hhmmss>.json
    文件名时间可排序故取末件；零件/读坏 → None（fail-open·不阻断自检）。"""
    d = evidence_dir or os.path.join(PROJECT, "state")
    try:
        names = sorted(n for n in os.listdir(d)
                       if n.startswith("c1c4-") and n.endswith(".json"))
        if not names:
            return None
        with open(os.path.join(d, names[-1]), encoding="utf-8") as f:
            ev = json.load(f)
        return {"file": names[-1], "verdict": str(ev.get("verdict", "")),
                "generated_at": str(ev.get("generated_at", ""))}
    except Exception:
        return None


def c1c4_line_age(rounds_path=None, evidence_dir=None,
                  max_age=C1C4_MAX_AGE_ROUNDS, _lines=None, _evidence=None):
    """J14（T125）：收尾 C1C4 行机械校验（advisory·零阻断）。

    判据：自尾向前在轮行里找首条 `C1C4:` 段 → rounds_ago=其后轮行数。
    state ∈ FRESH（≤max_age 且票与证据一致）／STALE（停滞 >max_age）／
    MISMATCH（行内票 ≠ 最新证据票=收尾演出与证据脱钩）／NOEVID（证据缺或坏）／
    MISSING（全史零 C1C4 段）／UNVERIFIED（rounds.log 读不出）。
    返回 dict：{state, rounds_ago, verdict, evidence, token}。
    """
    try:
        if _lines is None:
            with open(rounds_path or ROUNDS_LOG, encoding="utf-8",
                      errors="replace") as f:
                lines = f.read().splitlines()
        else:
            lines = list(_lines)
    except Exception:
        return {"state": "UNVERIFIED", "rounds_ago": None, "verdict": None,
                "evidence": None, "token": "UNVERIFIED(rounds-read-err)"}
    rows = [ln for ln in lines if " tokens: " in ln]
    ago, verdict = None, None
    for i in range(len(rows) - 1, -1, -1):
        m = C1C4_RE.search(rows[i])
        if m:
            ago, verdict = len(rows) - 1 - i, m.group(1)
            break
    ev = _evidence if _evidence is not None else latest_c1c4_evidence(evidence_dir)
    ev_verdict = (ev or {}).get("verdict") or None
    if ago is None:
        state = "MISSING" if rows else "UNVERIFIED"
    elif ago > max_age:
        state = "STALE"
    elif not ev_verdict:
        state = "NOEVID"
    elif ev_verdict != verdict:
        state = "MISMATCH"
    else:
        state = "FRESH"
    token = "%s(rounds_ago=%s,verdict=%s,ev=%s)" % (
        state, "n/a" if ago is None else ago, verdict or "-", ev_verdict or "-")
    return {"state": state, "rounds_ago": ago, "verdict": verdict,
            "evidence": ev_verdict, "token": token}


def parse_hq_line(line, today=None):
    """J15（T129）：日清单行内容时点解析——宽容上界法（宁晚勿早·不虚报陈旧）。

    · 时分在场则用之（'4x' 掩码形按 …49 取上界：22:4x→22:49）；
    · 时分缺席 → 当日 23:59（该行最晚可能落笔时刻）；
    · 省略年份的 MM-DD 以当年推，推得日期晚于明日则回退上一年（跨年窗）。
    返回 datetime 或 None。
    """
    m = HQ_LINE_RE.search(line or "")
    if not m:
        return None
    today = today or datetime.date.today()
    raw = m.group(1)
    try:
        if raw.count("-") == 2:
            y, mo, d = (int(x) for x in raw.split("-"))
        else:
            mo, d = (int(x) for x in raw.split("-"))
            y = today.year
        dt = datetime.datetime(y, mo, d, 23, 59)
        if raw.count("-") == 1 and (dt.date() - today).days > 1:
            dt = dt.replace(year=y - 1)  # 跨年窗（1 月读上年 12 月行）
    except Exception:
        return None
    if m.group(2) is not None:
        try:
            dt = dt.replace(hour=int(m.group(2)),
                            minute=int(str(m.group(3)).replace("x", "9")
                                       .replace("X", "9")))
        except Exception:
            return None
    return dt


def hq_feedback_latest(hq_path=None, today=None, _lines=None):
    """J15（T129）：取日清区最新一行内容时点 → (datetime|None, 原文行|None)。"""
    try:
        if _lines is None:
            with open(hq_path or HQ_FEEDBACK, encoding="utf-8",
                      errors="replace") as f:
                lines = f.read().splitlines()
        else:
            lines = list(_lines)
    except Exception:
        return None, None
    for ln in reversed(lines):
        dt = parse_hq_line(ln, today=today)
        if dt is not None:
            return dt, ln
    return None, None


def latest_round_date(rounds_path=None, _lines=None):
    """J15：rounds.log 最新轮行的日期（轮行锚=「 tokens: 」段）。"""
    try:
        if _lines is None:
            with open(rounds_path or ROUNDS_LOG, encoding="utf-8",
                      errors="replace") as f:
                lines = f.read().splitlines()
        else:
            lines = list(_lines)
    except Exception:
        return None
    for ln in reversed(lines):
        m = ROUND_ROW_RE.match(ln)
        if m:
            try:
                return datetime.date(int(m.group(1)), int(m.group(2)),
                                     int(m.group(3)))
            except Exception:
                return None
    return None


def hq_line_age(hq_path=None, rounds_path=None, now=None,
                max_age_h=HQ_MAX_AGE_HOURS, _lines=None, _rounds=None):
    """J15（T129）：日清面新鲜度机检（advisory·零阻断·只出读数不改文本）。

    state ∈ FRESH（内容龄 ≤ max_age_h）／STALE（内容龄 > max_age_h）／
    MISSED（rounds.log 最新轮行日期晚于日清最新行=轮更了日清没写）／
    MISSING（全文件零「日清 …」行）／UNVERIFIED（文件读不出）。
    返回 dict：{state, age_h, date, token}。上界法可能略晚于实际写入时刻，
    故负龄按 0 计（age_h 不为负）。
    """
    now = now or datetime.datetime.now()
    try:
        if _lines is None:
            with open(hq_path or HQ_FEEDBACK, encoding="utf-8",
                      errors="replace") as f:
                lines = f.read().splitlines()
        else:
            lines = list(_lines)
    except Exception:
        return {"state": "UNVERIFIED", "age_h": None, "date": None,
                "token": "UNVERIFIED(hq-read-err)"}
    dt, _raw = hq_feedback_latest(today=now.date(), _lines=lines)
    if dt is None:
        return {"state": "MISSING", "age_h": None, "date": None,
                "token": "MISSING(no-日清行)"}
    age_h = round(max(0.0, (now - dt).total_seconds() / 3600.0), 1)
    rdate = latest_round_date(rounds_path, _rounds)
    if rdate is not None and rdate > dt.date():
        state = "MISSED"
    elif age_h > max_age_h:
        state = "STALE"
    else:
        state = "FRESH"
    token = "%s(age_h=%s,date=%s)" % (state, age_h, dt.strftime("%Y-%m-%d"))
    return {"state": state, "age_h": age_h,
            "date": dt.strftime("%Y-%m-%d"), "token": token}


def probe_task_states(names, _run=None, attempts=2, retry_wait=3.0):
    """J6（T61/BC-P-57）：schtasks 态只读探——Get-ScheduledTask State 枚举
    （locale 无关）。返回 {name: state}；缺失任务/查询失败 → Absent/ProbeError。
    瞬时失败重试一次（10-12 11:14 判例：py_cpu=83% 争用把 Get-ScheduledTask
    拖过 timeout=15s→假 P1「检测面失明」）；重试尽仍败 → ProbeError（真失明
    面维持 P1 不豁免）。_run=selftest 注入面。"""
    run_fn = _run or subprocess.run
    ps = ("Get-ScheduledTask -TaskName %s -ErrorAction SilentlyContinue | "
          "ForEach-Object { $_.TaskName + '=' + $_.State }"
          % ",".join("'%s'" % n for n in names))
    for attempt in range(attempts):
        try:
            out = run_fn(["powershell", "-NoProfile", "-Command", ps],
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
            if attempt + 1 >= attempts:
                break
            time.sleep(retry_wait)
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


def classify_cpu_lane_idle(ram_pct, ram_min=RAM_FREE_PCT_MIN):
    """J9（T85·O-20261013-2215 ① 承接链）：CPU 车道闲判=RAM 单闸·VRAM 无关。

    10-10 18:26 判例修复面：纯 CPU 批的 idle 触发不应被 vram_free 闸误挡——
    O-2215 后 CPU 面=三机 24/7 常开（工作日白天解禁），VRAM 被 GPU 生产面/CEO
    用机占满时 CPU 车道仍应达「闲」档领活。GREEN-IDLE（§8.2 预注册）维持
    RAM+VRAM 双闸=GPU 车道判据不动；本函数=CPU 车道判据（RAM 单闸·阈值复用
    预注册值零新调参）。车道级 lane 路由（fleet-nodes.json lane 字段）=集团
    工具面在役（10-12 定岗令 IdleWatch v2），本字段=机级 lane 感知闲判供
    消费面读取。
    """
    return ram_pct >= ram_min


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


def machine_silent_minutes(machine, machines_dir=None):
    """J13：机器心跳静默分钟数（clock_read 时戳龄；缺字段回退文件 mtime）。

    不可读→None：fail-open，不把「探不到」当「已死」（D-20261024-14 ②：
    接管须独立活性佐证，禁以心跳静默单条判死）。
    """
    d = machines_dir or MACHINES_DIR
    p = os.path.join(d, "%s.json" % machine)
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            ts = (json.load(f) or {}).get("clock_read") or ""
        if ts:
            t = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
            return (datetime.datetime.now() - t).total_seconds() / 60.0
    except Exception:
        pass
    try:
        return (time.time() - os.path.getmtime(p)) / 60.0
    except Exception:
        return None


def _claim_age_days(ts, now=None):
    """J13b：claim 时戳龄（天）。支持 ISO `2026-10-07T22:33:00+08:00` 与
    池台账常见的 `10-07 22:33`（缺年份=按当年补；跨年陈旧行会低估，注明）。"""
    now = now or datetime.datetime.now()
    s = (ts or "").strip().rstrip("）)").strip()
    for fmt, cut in (("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%dT%H:%M", 16),
                     ("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            t = datetime.datetime.strptime(s[:cut], fmt)
        except Exception:
            continue
        return (now - t).total_seconds() / 86400.0
    for fmt, cut in (("%m-%d %H:%M:%S", 14), ("%m-%d %H:%M", 11)):
        try:  # 缺年份：显式补当年（避开 strptime 默认 1900 的闰日告警）
            t = datetime.datetime.strptime("%d-%s" % (now.year, s[:cut]),
                                           "%Y-" + fmt)
        except Exception:
            continue
        return (now - t).total_seconds() / 86400.0
    return None


def _row_id(line):
    m = re.match(r"\s*(\d+)\.", line)
    return "backlog#%s" % m.group(1) if m else "backlog#?"


def _row_dept(line):
    """池行交付司标签（行首【X】）；无标签返回 None（不判归属）。"""
    m = DEPT_TAG_RE.search(line)
    return DEPT_ALIAS.get(m.group(1).strip(), m.group(1).strip()) if m else None


def _dept_of(machine):
    """claim 机归属司；未登记机返回 None（不判归属·保守不误杀）。"""
    return MACHINE_DEPT.get(machine)


def ignitable_face(backlog_path=BACKLOG, machines_dir=None,
                   self_machine=SELF_MACHINE,
                   silent_min=IGNITABLE_SILENT_MIN,
                   floor=SUPPLY_FLOOR_IGNITABLE):
    """J13（D-20261024-14 ③）：供给地板按 ready_ignitable 判 breach。

    与集团 compute_audit.ignitable_face 同语义（本司副本对齐面）：
    - ready_total = 池台账「可领」且未 done@ 的行（诊断列，保留不删）；
    - 可点火 = 无 claim（owner ANY）或 claim 者为本机；
    - 他机 claim：活性可读且静默 ≤ silent_min → foreign_alive（有人在干，
      非本机可领）；静默超阈 → foreign_silent（只列 takeover 候选，且须
      独立活性佐证才可接管）；活性不可读 → owner_unknown（不判死）。
    breach = ready_ignitable < floor。结构等待≠可点火活（SLI-RECONCILE 先例）。

    J13b（M86）：本机自 claim 再拆 fresh/active/stale 三列（诊断，不改上式语义）——
    effective_ignitable = ready_fresh + ready_self_active，breach_effective 用
    该值判。陈旧自占用=账面上「可领」但本机超 SELF_STALE_DAYS 未交付，不是新供给。

    J13c（T124·BC-P-105）：claim 行再判 lane 归属——行首【司标签】=交付司，
    claim 机归属司（MACHINE_DEPT）≠ 交付司 → lane_mismatch，同样不计入
    effective_ignitable（外司池活被跨司机占用=本车道无新供给）。
    """
    face = {"ready_total": 0, "ready_ignitable": 0, "ready_foreign_alive": 0,
            "ready_foreign_silent": 0, "ready_owner_unknown": 0,
            "ready_fresh": 0, "ready_self_active": 0, "ready_self_stale": 0,
            "self_stale_ids": [], "self_age_unknown": 0,
            "effective_ignitable": 0,
            "lane_mismatch": 0, "lane_mismatch_ids": [],
            "takeover_candidate_ids": [], "silent_owners": {},
            "unknown_owners": [], "floor": floor, "breach": False,
            "breach_effective": False, "self_stale_days": SELF_STALE_DAYS,
            "readable": True, "takeover_needs_corroboration": True,
            "self": self_machine}
    if not os.path.exists(backlog_path):
        face["readable"] = False
        return face
    cache = {}
    eff = 0
    with open(backlog_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "可领" not in line or "done@" in line:
                continue
            face["ready_total"] += 1
            claims = CLAIM_RE.findall(line)
            if not claims:
                face["ready_ignitable"] += 1
                face["ready_fresh"] += 1
                eff += 1
                continue
            owner = claims[-1]
            lane_ok = True
            if owner == self_machine:
                face["ready_ignitable"] += 1
                row_dept, claimer_dept = _row_dept(line), _dept_of(owner)
                lane_ok = not (row_dept and claimer_dept
                               and row_dept != claimer_dept)
                if not lane_ok:
                    face["lane_mismatch"] += 1
                    face["lane_mismatch_ids"].append(_row_id(line))
                ts = ""
                for m_owner, m_ts in CLAIM_TS_RE.findall(line):
                    if m_owner == self_machine:
                        ts = m_ts
                age_d = _claim_age_days(ts) if ts else None
                stale = age_d is not None and age_d > SELF_STALE_DAYS
                if age_d is None:
                    face["self_age_unknown"] += 1
                    face["ready_self_active"] += 1
                elif stale:
                    face["ready_self_stale"] += 1
                    face["self_stale_ids"].append(_row_id(line))
                else:
                    face["ready_self_active"] += 1
                if lane_ok and not stale:
                    eff += 1
                continue
            if owner not in cache:
                cache[owner] = machine_silent_minutes(owner, machines_dir)
            age = cache[owner]
            if age is None:
                face["ready_owner_unknown"] += 1
                face["unknown_owners"].append(owner)
            elif age > silent_min:
                face["ready_foreign_silent"] += 1
                face["silent_owners"][owner] = round(age, 1)
                face["takeover_candidate_ids"].append(_row_id(line))
            else:
                face["ready_foreign_alive"] += 1
    face["breach"] = face["ready_ignitable"] < floor
    face["effective_ignitable"] = eff
    face["breach_effective"] = face["effective_ignitable"] < floor
    return face


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


def _git_status_short(repo_root=None, _run=None):
    """J11 辅件：git status --short 只读采集——真实 git 路径优先（PATH git=
    gitsilent 转发器吞 stdout·10-08 判例·零输出=假净面）；失败/超时=None。"""
    if repo_root is None:
        repo_root = PROJECT
    git = GIT_REAL if os.path.exists(GIT_REAL) else "git"
    runner = _run or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=10, cwd=repo_root))
    try:
        r = runner([git, "status", "--short"])
        if r.returncode != 0:
            return None
        return (r.stdout or "")
    except Exception:
        return None


def stale_m_watch(repo_root=None, age_hours=STALE_M_AGE_HOURS,
                  latch_path=STALE_M_LATCH, recorder=None,
                  _run=None, _stat=None):
    """J11（T105·BC-P-78 批活化·T98 承继）：他方会话工作面陈旧 M 态守卫。
    判据=git status --short 工作树 M 件（XY 第二列=M）× mtime 龄 >age_hours
    → P2 心跳行（日清承载）。自产 M 件按轮收尾定向 commit 纪律必为新鲜态
    （龄 <24h）=年龄闸即「非本轮产出」判别器（BC-P-78 判据原文）。日粒度
    锁存去重：同文件同日只告警一次（防 tick 风暴）；恢复（离开陈旧 M 态）=
    锁存清空静默；正常=零追加（预注册「正常静默」）；探针坏=缺省行零阻塞
    （fail-open）。返回 1=有告警，0=静默/无/探针坏。pythonw 安全零 print。"""
    if repo_root is None:
        repo_root = PROJECT
    out = _git_status_short(repo_root, _run)
    if out is None:
        return 0  # fail-open：探针坏=缺省行零阻塞（BC-P-78 判据原文）
    getmtime = _stat or os.path.getmtime
    stale = {}
    for ln in out.splitlines():
        if len(ln) < 4 or ln[1] != "M":  # ' M'/'MM'/'AM'=工作树 M；??/A /R 不计
            continue
        path = ln[3:].strip().strip('"')
        if not path:
            continue
        try:
            mt = getmtime(os.path.join(repo_root, path))
        except Exception:
            continue  # 单件失明=跳过不阻塞（fail-open 同精神）
        age_h = (time.time() - mt) / 3600.0
        if age_h > age_hours:
            stale[path] = age_h
    prev = {}
    if os.path.exists(latch_path):
        try:
            with open(latch_path, encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = ""
    if isinstance(prev, str):  # 旧态/坏态容错
        prev = {}
    today = time.strftime("%Y-%m-%d")
    fresh = [(p, stale[p]) for p in sorted(stale) if prev.get(p) != today]
    new_latch = {p: d for p, d in prev.items() if p in stale}
    for p in stale:
        new_latch[p] = today  # 仍在陈旧态→锁存续鲜（同日去重·次日再警）
    try:
        if new_latch:
            with open(latch_path, "w", encoding="utf-8") as f:
                json.dump(new_latch, f)
        elif os.path.exists(latch_path):
            os.unlink(latch_path)  # 恢复=清锁存静默（J7 同型）
    except Exception:
        pass
    if not fresh:
        return 0
    sig = "、".join("%s(龄%.0fh)" % (p, a) for p, a in fresh)
    line = ("%s P2 stale-m: %s — 他方会话在飞件滞留工作树超 %gh（owner "
            "commit 未落·BC-P-78 守卫·日粒度去重·T98 承继）·修复面=执笔窗"
            "收口或日清承载〔J11〕"
            % (time.strftime("%Y-%m-%d %H:%M:%S"), sig, age_hours))
    if recorder is not None:
        recorder(line)  # selftest 注入面（零真实 heartbeat 副作用）
    else:
        append_heartbeat(line)
    return 1


def supply_gate_value(face, pool):
    """T128：供给读数（饥饿判定/心跳同源）——effective 优先，面不可读回退池行数。"""
    return face["effective_ignitable"] if face["readable"] else pool


def check():
    ram_pct = probe_ram_free_pct()
    vram_mb = probe_vram_free_mb()
    py_cpu = probe_py_cpu()
    green_idle = (ram_pct >= RAM_FREE_PCT_MIN and vram_mb >= VRAM_FREE_MB_MIN)
    cpu_idle = classify_cpu_lane_idle(ram_pct)
    pool = count_pool_claimable()
    face = ignitable_face()
    # D-20261024-14 ① + T128：饥饿判定/心跳一律走 effective 口径（本机陈旧自占
    # 与跨司机错占不算新供给，否则「无单可领」面假绿）；读数不可得时回退池行数
    # （fail-open，不因探针坏而误报供给缺口）。ready_ignitable 仍作诊断列输出。
    pool_ign = supply_gate_value(face, pool)
    q_open = count_queue_open()
    serve_alive, serve_detail = probe_serve_liveness()
    lane8090 = probe_lane8090()
    model_drift = probe_model_drift()
    c1c4_line = c1c4_line_age()
    hq_line = hq_line_age()
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
    # T85（O-2215 ①）：饥饿面闸=cpu_idle（RAM 单闸·CPU 车道常开）——green_idle
    # ⊂ cpu_idle（GPU 车道闲 ⊆ CPU 车道闲），VRAM 忙而 RAM 闲时纯 CPU 面照算
    # 饥饿计数（10-10 18:26 判例：VRAM 闸误挡纯 CPU 批的触发面修复）。
    if cpu_idle and pool_ign == 0 and q_open == 0:
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
                       "py_cpu": py_cpu, "cpu_lane_idle": cpu_idle,
                       "pool_claimable": pool, "queue_open": q_open,
                       # T128 切换期双字段并存：pool_ignitable 保集团同源口径
                       # （诊断/兼容），pool_effective 为饥饿与心跳的治理口径。
                       "pool_effective": pool_ign,
                       "pool_ignitable": (face["ready_ignitable"]
                                          if face["readable"] else pool),
                       "supply_floor": face,
                       "serve_alive": serve_alive, "serve_detail": serve_detail,
                       "lane8090": lane8090,
                       "model_drift": model_drift,
                       "c1c4_line": c1c4_line,
                       "hq_line": hq_line,
                       "task_face": task_face,
                       "task_states": {n: tstates.get(n) for n in
                                       TASK_LIVENESS_PROBE}}
    save_state(st)

    line = ("%s idle_selfcheck: verdict=%s ram_free_pct=%s vram_free_mb=%s "
            "py_cpu=%s cpu_lane_idle=%s "
            "pool_claimable=%d queue_open=%d consecutive_idle=%d "
            "idle_rounds=%d agenda_starved=%s serve_alive=%s serve_detail=%s"
            " task_face=%s model_drift=%s"
            " pool_effective=%d pool_ignitable=%d"
            " pool_foreign_silent=%d pool_owner_unknown=%d"
            " pool_fresh=%d pool_self_stale=%d pool_lane_mm=%d"
            " breach_eff=%s lane8090=%s c1c4_line=%s hq_line=%s"
            % (time.strftime("%Y-%m-%d %H:%M:%S"), verdict, ram_pct,
               vram_mb, py_cpu, str(cpu_idle).lower(), pool, q_open,
               st["consecutive_idle"], st["idle_rounds"], str(starved).lower(),
               str(serve_alive).lower(), serve_detail, task_face,
               model_drift, pool_ign,
               (face["ready_ignitable"] if face["readable"] else pool),
               face["ready_foreign_silent"],
               face["ready_owner_unknown"], face["ready_fresh"],
               face["ready_self_stale"], face["lane_mismatch"],
               str(face["breach_effective"]).lower(),
               lane8090, c1c4_line["token"], hq_line["token"]))
    code, msg = append_heartbeat(line)
    print(line)
    print("append: code=%d %s" % (code, msg))
    if not serve_alive and not pause_mode:
        # BC-P-55：serve 死亡 → P1 轮报告行（共租户受损预警·修复面=净重启托盘链
        # 配方在案 M57·自动执行随批非自决）。pause 律性豁免（10-12 11:1x 判例：
        # machine-state pause 必杀 llama-server/Ollama=合法下线非事故·观测行
        # serve_alive/detail 常驻保留·只豁免假 P1）。
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
    # J11（T105·BC-P-78·T98 承继）：陈旧 M 态守卫——非本轮产出 M 件龄 >24h
    # → P2 行（日粒度去重·fail-open 缺省行零阻塞·自产 M 必新鲜不触发）。
    try:
        if stale_m_watch() == 1:
            print("J11 stale-m: P2 alert appended (see heartbeat)")
    except Exception:
        pass
    # J14（T125）：收尾 C1C4 行机械校验——缺行/停滞/票脱钩均 advisory P2 行
    #（不阻断轮；FRESH 常态零追加·证据缺=NOEVID 同报警不当合格）。
    if c1c4_line["state"] != "FRESH":
        p2c = ("%s P2 c1c4_line: %s — 收尾 C1-C4 未可证"
               "（T125 机械校验·advisory 不阻断）"
               % (time.strftime("%Y-%m-%d %H:%M:%S"), c1c4_line["token"]))
        code4, msg4 = append_heartbeat(p2c)
        print(p2c)
        print("append-p2c: code=%d %s" % (code4, msg4))
    # J15（T129）：日清面新鲜度——陈旧/漏写均 advisory P2 行（不改日清文本）。
    if hq_line["state"] != "FRESH":
        p2h = ("%s P2 hq_feedback: %s — 日清面按内容日期判鲜"
               "（T129·mtime 不可信·advisory 不阻断）"
               % (time.strftime("%Y-%m-%d %H:%M:%S"), hq_line["token"]))
        code5, msg5 = append_heartbeat(p2h)
        print(p2h)
        print("append-p2h: code=%d %s" % (code5, msg5))
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

    # J13（D-20261024-14）：可点火口径四桶 + breach 数学（合成夹具·零真实池）
    with tempfile.TemporaryDirectory() as td:
        md = os.path.join(td, "machines")
        os.makedirs(md)
        now = datetime.datetime.now()
        with open(os.path.join(md, "bm-b.json"), "w", encoding="utf-8") as f:
            json.dump({"clock_read":
                       now.strftime("%Y-%m-%dT%H:%M:%S+08:00")}, f)
        with open(os.path.join(md, "bm-c.json"), "w", encoding="utf-8") as f:
            json.dump({"clock_read": (now - datetime.timedelta(hours=30))
                       .strftime("%Y-%m-%dT%H:%M:%S+08:00")}, f)
        bl = os.path.join(td, "backlog.md")
        with open(bl, "w", encoding="utf-8") as f:
            f.write("1. 【HQ】无主——可领\n"
                    "2. 【HQ】本机——可领（claimed@bm-a@10-07 22:33）\n"
                    "3. 【BigMoney】活他机——可领（claimed@bm-b@10-24 17:00）\n"
                    "4. 【BigMoney】死他机——可领（claimed@bm-c@10-21 08:00）\n"
                    "5. 【BigMoney】已完——可领（claimed@bm-b@10-20）done@10-21\n"
                    "6. 【BigMoney】无状态件——可领（claimed@bm-d@10-24）\n"
                    "7. 【FluxVerse】跨司机占——可领（claimed@bm-a@10-24 12:00）\n")
        fc = ignitable_face(bl, machines_dir=md, floor=4)
        fc2 = ignitable_face(bl, machines_dir=md, floor=3)
        age_c = machine_silent_minutes("bm-c", md)
        print("J13 ignitable: total=%d ign=%d alive=%d silent=%d unknown=%d "
              "fresh=%d self_active=%d self_stale=%d eff=%d lane_mm=%d "
              "breach=%s breach@3=%s breach_eff=%s take=%s silent_owners=%s"
              % (fc["ready_total"], fc["ready_ignitable"],
                 fc["ready_foreign_alive"], fc["ready_foreign_silent"],
                 fc["ready_owner_unknown"], fc["ready_fresh"],
                 fc["ready_self_active"], fc["ready_self_stale"],
                 fc["effective_ignitable"], fc["lane_mismatch"],
                 fc["breach"], fc2["breach"],
                 fc["breach_effective"], fc["takeover_candidate_ids"],
                 fc["silent_owners"]))
        ok &= fc["readable"] and fc["ready_total"] == 6
        ok &= fc["ready_ignitable"] == 3          # 无主 + 本机 ×2
        ok &= fc["ready_foreign_alive"] == 1
        ok &= fc["ready_foreign_silent"] == 1 and "bm-c" in fc["silent_owners"]
        ok &= fc["ready_owner_unknown"] == 1      # bm-d 无状态件=不判死
        ok &= fc["takeover_candidate_ids"] == ["backlog#4"]
        ok &= fc["breach"] is True and fc2["breach"] is False  # floor 数学
        # J13b（M86）：本机陈旧自占用拆列——row#2 的 10-07 claim 已 17 天
        ok &= fc["ready_fresh"] == 1
        ok &= fc["ready_self_stale"] == 1 and fc["ready_self_active"] == 1
        ok &= fc["self_stale_ids"] == ["backlog#2"]
        # J13c（T124）：交付司标签≠claim 机司 → lane_mismatch，不计入 effective
        # （只计本机持有行：他机持有行本就不入 effective，另在其桶内诊断）
        ok &= fc["lane_mismatch"] == 1
        ok &= fc["lane_mismatch_ids"] == ["backlog#7"]
        ok &= fc["effective_ignitable"] == 1      # fresh 1 + active(row7 跨司机占剔除)
        ok &= fc["breach_effective"] is True and fc["self_stale_days"] == 3.0
        ok &= _row_dept("8. 【集团】x") == "HQ" and _row_dept("9. 无标签") is None
        ok &= _dept_of("bm-zzz") is None
        ok &= (_claim_age_days("2026-10-07T22:33:00+08:00") or 0) > 3.0
        ok &= _claim_age_days("garbage") is None
        ok &= age_c is not None and age_c > IGNITABLE_SILENT_MIN
        ok &= ignitable_face(os.path.join(td, "nope.md"))["readable"] is False
        # T128：供给读数口径切 effective——ready=3 而 eff=1，旧口径会假绿；
        # 面不可读时回退池行数（fail-open）。
        ok &= supply_gate_value(fc, 99) == 1
        ok &= supply_gate_value(fc, 99) != fc["ready_ignitable"]
        ok &= supply_gate_value(ignitable_face(os.path.join(td, "nope.md")),
                                7) == 7
        print("J13d supply gate: eff=%d ready_diag=%d fallback=%d"
              % (supply_gate_value(fc, 99), fc["ready_ignitable"],
                 supply_gate_value(ignitable_face(os.path.join(td, "nope.md")),
                                   7)))

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

    # J6r probe retry（10-12 11:14 判例）：瞬时失败一次→重试救回零 ProbeError；
    # 持续失败→ProbeError（真失明面不豁免）。_run 注入零真实副作用。
    class _SimTO(Exception):
        pass

    def _flaky_run(*a, **k):
        _flaky_run.n += 1
        if _flaky_run.n == 1:
            raise _SimTO("simulated transient stall")

        class _O:
            stdout = "\n".join("%s=Ready" % n for n in MACHINE_PAUSE_SET)
        return _O()
    _flaky_run.n = 0

    def _dead_run(*a, **k):
        raise _SimTO("simulated persistent failure")

    st_r = probe_task_states(MACHINE_PAUSE_SET, _run=_flaky_run,
                             attempts=2, retry_wait=0)
    st_d = probe_task_states(MACHINE_PAUSE_SET, _run=_dead_run,
                             attempts=2, retry_wait=0)
    j6r_rec = all(st_r.get(n) == "Ready" for n in MACHINE_PAUSE_SET)
    j6r_dead = all(st_d.get(n) == "ProbeError" for n in MACHINE_PAUSE_SET)
    print("J6r probe retry: transient-recovered=%s calls=%d persistent-blind=%s"
          % (j6r_rec, _flaky_run.n, j6r_dead))
    ok &= j6r_rec and (_flaky_run.n == 2) and j6r_dead

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

    # J8 py_cpu（T76·O-20261011-0012 ⅴ）：只读真跑——float 且 [-1.0 或 0..100]
    cpu = probe_py_cpu()
    print("J8 py_cpu probe: %s" % cpu)
    ok &= isinstance(cpu, float) and (cpu == -1.0 or 0.0 <= cpu <= 100.0)

    # J9 cpu-lane idle（T85·O-2215 ① 承接链·10-10 18:26 判例）：RAM 单闸夹具——
    # ①阈值上下沿；②10-10 误挡案复现=RAM 闲+VRAM 忙→GREEN-IDLE 假 busy 而
    # CPU 车道达闲档（触发不再被 VRAM 闸挡）。
    c_hi, c_lo = classify_cpu_lane_idle(50.0), classify_cpu_lane_idle(30.0)
    vr_busy_gate = (50.0 >= RAM_FREE_PCT_MIN and 1024 < VRAM_FREE_MB_MIN)
    ok &= (c_hi is True) and (c_lo is False) and (vr_busy_gate is True)
    print("J9 cpu-lane idle: ram50=%s ram30=%s vram-busy-unblock=%s "
          "(GREEN-IDLE gate unchanged: RAM>=%s%% AND VRAM>=%dMiB)"
          % (c_hi, c_lo, vr_busy_gate, RAM_FREE_PCT_MIN, VRAM_FREE_MB_MIN))

    # J11 stale-m（T105·BC-P-78·T98 承继）：六路径夹具——工作树 M 解析（?? /A
    # 不计）/陈旧告警/同日去重/新鲜不告警+恢复清锁存/探针坏 fail-open。temp
    # 锁存+注入 _run/_stat/recorder·零真实 heartbeat/latch 副作用。
    class _RO:
        def __init__(self, out, rc=0):
            self.stdout, self.returncode = out, rc

    def _f_old(cmd):
        return _RO(" M Tools/owner_face.py\n?? new.txt\nA  staged.txt\n")

    def _f_fresh(cmd):
        return _RO(" M Tools/young.py\n")

    def _f_fail(cmd):
        raise RuntimeError("simulated probe failure")

    _old_mt = time.time() - 48 * 3600
    with tempfile.TemporaryDirectory() as td:
        lat2 = os.path.join(td, "slatch")
        rec2 = []
        a1 = stale_m_watch(td, latch_path=lat2, recorder=rec2.append,
                           _run=_f_old, _stat=lambda p: _old_mt)
        a2 = stale_m_watch(td, latch_path=lat2, recorder=rec2.append,
                           _run=_f_old, _stat=lambda p: _old_mt)
        a3 = stale_m_watch(td, latch_path=lat2, recorder=rec2.append,
                           _run=_f_fresh, _stat=lambda p: time.time())
        a4 = stale_m_watch(td, latch_path=lat2, recorder=rec2.append,
                           _run=_f_fail, _stat=lambda p: _old_mt)
        ok &= (a1, a2, a3, a4) == (1, 0, 0, 0)
        ok &= (len(rec2) == 1) and ("stale-m" in rec2[0]) and \
              ("owner_face" in rec2[0]) and ("P2" in rec2[0])
        ok &= not os.path.exists(lat2)  # 恢复=清锁存（a3 后零陈旧）
        print("J11 stale-m: stale=%d dedup=%d fresh-recover=%d failopen=%d "
              "alerts=%d latch-cleared=%s"
              % (a1, a2, a3, a4, len(rec2), not os.path.exists(lat2)))
    # J12 模型基线漂移 informational（BC-P-103 ③·离线夹具零网络）
    with tempfile.TemporaryDirectory() as td:
        bp = os.path.join(td, "baseline.json")
        with open(bp, "w", encoding="utf-8") as f:
            json.dump({"live_tags": ["a:latest", "b:latest"]}, f)
        drift = {"status": "DRIFT", "missing": ["b:latest"], "added": []}
        got_drift = probe_model_drift(bp, _diff=lambda caps, live: drift)
        got_fail = probe_model_drift(bp, _diff=lambda caps, live: 1 / 0)
        got_missing = probe_model_drift(os.path.join(td, "nope.json"))
        ok &= got_drift == "DRIFT(m=1,a=0)"
        ok &= got_fail == "UNVERIFIED(err)" and got_missing == "UNVERIFIED(err)"
        print("J12 model-drift: drift=%s failopen=%s missing=%s"
              % (got_drift, got_fail, got_missing))

    # J13 35B 车道实况（T89·三态判定·注入式确定性测）
    lane_up = probe_lane8090(_get=lambda: 200, _port_open=lambda: False)
    lane_occ = probe_lane8090(_get=lambda: 1 / 0, _port_open=lambda: True)
    lane_dn = probe_lane8090(_get=lambda: 1 / 0, _port_open=lambda: False)
    lane_500 = probe_lane8090(_get=lambda: 500, _port_open=lambda: True)
    print("J13 lane8090: up=%s occupied=%s down=%s http500=%s"
          % (lane_up, lane_occ, lane_dn, lane_500))
    ok &= (lane_up == "UP" and lane_occ == "DARK[OCCUPIED]"
           and lane_dn == "DARK[DOWN]" and lane_500 == "DARK[http500]")

    # J14 收尾 C1C4 行校验（T125）：六路径夹具——fresh/stale/mismatch/missing/
    # noevid/读不出·注入行与证据，零真实 rounds.log/state 副作用。
    r_ok = ("2026-10-25 22:4x tokens: local=0 api=0 api_reason=- | "
            "C1C4:C1C4-GREEN 4/4 C1=C1-GREEN|C2=PASS\n")
    r_plain = "2026-10-25 12:4x tokens: local=0 api=0 api_reason=- | QA 7/8\n"
    j14 = {
        "fresh": c1c4_line_age(_lines=[r_plain, r_ok],
                               _evidence={"verdict": "C1C4-GREEN"}),
        "stale": c1c4_line_age(_lines=[r_ok] + [r_plain] * 4,
                               _evidence={"verdict": "C1C4-GREEN"}),
        "mismatch": c1c4_line_age(_lines=[r_ok],
                                  _evidence={"verdict": "C1C4-RED"}),
        "missing": c1c4_line_age(_lines=[r_plain, r_plain], _evidence={}),
        "noevid": c1c4_line_age(_lines=[r_ok], _evidence={}),
    }
    with tempfile.TemporaryDirectory() as td3:
        j14["readerr"] = c1c4_line_age(
            rounds_path=os.path.join(td3, "nope.log"), _evidence={})
    ok &= j14["fresh"]["state"] == "FRESH" and j14["fresh"]["rounds_ago"] == 0
    ok &= j14["stale"]["state"] == "STALE" and j14["stale"]["rounds_ago"] == 4
    ok &= j14["mismatch"]["state"] == "MISMATCH"
    ok &= j14["missing"]["state"] == "MISSING"
    ok &= j14["noevid"]["state"] == "NOEVID"
    ok &= j14["readerr"]["state"] == "UNVERIFIED"
    print("J14 c1c4-line: %s | live=%s"
          % (" ".join("%s=%s" % (k, v["token"]) for k, v in j14.items()),
             c1c4_line_age()["token"]))

    # J15 日清面新鲜度（T129）：五路径夹具——fresh/stale/missed/missing/读不出。
    # now 固定注入 → 确定性；零真实 HQ-FEEDBACK/rounds.log 副作用。
    now15 = datetime.datetime(2026, 10, 26, 3, 20)
    hq_today = "日清 10-26 03:1x（哨兵段轮）：无 P0/P1\n"
    hq_old = "日清 10-23 12:4x 午班轮：无待报\n"
    hq_yest = "日清 10-25 22:4x（夜班排程轮）：无待报\n"
    r_today = "2026-10-26 03:1x tokens: local=0 api=0 api_reason=- | QA 8/8\n"
    j15 = {
        "fresh": hq_line_age(now=now15, _lines=[hq_old, hq_today],
                             _rounds=[r_today]),
        "stale": hq_line_age(now=now15, _lines=[hq_old], _rounds=[]),
        "missed": hq_line_age(now=now15, _lines=[hq_yest],
                              _rounds=[r_today]),
        "missing": hq_line_age(now=now15, _lines=["# 无关行\n"], _rounds=[]),
    }
    with tempfile.TemporaryDirectory() as td4:
        j15["readerr"] = hq_line_age(hq_path=os.path.join(td4, "nope.md"),
                                     now=now15, _rounds=[])
    ok &= j15["fresh"]["state"] == "FRESH" and j15["fresh"]["date"] == "2026-10-26"
    ok &= j15["stale"]["state"] == "STALE" and j15["stale"]["age_h"] > 24
    ok &= j15["missed"]["state"] == "MISSED"
    ok &= j15["missing"]["state"] == "MISSING"
    ok &= j15["readerr"]["state"] == "UNVERIFIED"
    ok &= parse_hq_line("日清 2026-10-25（哨兵轮）", today=now15.date()
                        ).strftime("%Y-%m-%d") == "2026-10-25"
    ok &= parse_hq_line("日清 12-31 23:2x", today=datetime.date(2027, 1, 1)
                        ).year == 2026
    print("J15 hq-line: %s | live=%s"
          % (" ".join("%s=%s" % (k, v["token"]) for k, v in j15.items()),
             hq_line_age()["token"]))
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
