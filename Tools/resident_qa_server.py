# -*- coding: utf-8 -*-
"""resident_qa_server.py — 硅基观测窗「召见居民·城主对话台」8792 服务端 v1
（BC-P-04 MaaS lane 消费面工程件·O-20260930-1645 遗留待裁「8792 居民问答服务端缺失（前端在·后端未建）」承接
·前端契约=硅基生命元宇宙.html L786-830 只读锚：POST /ask {id,q}→{reply|answer|a|text, gate|gates}·403={reason|err}）

三律在位：
- 端口恒绑 127.0.0.1（R-37 同源纪律·零 tailnet/局域网暴露面）
- 数据件只读（MiniGame 硅基生命元宇宙-data.js parse·禁跨仓写）
- 机审门 gate 字段恒带「本地AI生成」披露（AIGC 标识诚实律·内部观测面同律）

白名单 v1：锚民 20 位（data.js meet.anchors）可召见；荣誉席 C-00001/2/3 禁聊；
其余 C-xxxxx → 403 白名单外（轮值 100/日随 BigLife 户籍真源 J2 待接·如实）。
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if sys.stdout:  # pythonw 下 stdout=None：静默护栏
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _p(msg):
    try:
        print(msg)
    except Exception:
        pass
DEFAULT_DATA = r"C:\Users\sjs20\Desktop\FluxGroup\gaming\MiniGame\硅基生命元宇宙-data.js"
HONOR_SEATS = ("C-00001", "C-00002", "C-00003")
LLM_URL = os.environ.get("RESIDENT_QA_LLM_URL", "http://127.0.0.1:8090/v1/chat/completions")  # O-20261015-1335 fleet brain lane（bm-d 切换=改 env 零改码·O-20261015-1505 ⑤）
MODEL = "qwen3.6-35b"
# 降级链（T148 R3/T149 普查正法）：L1 8090 不可达→本机 Ollama 常驻面，声明链与代码同源
FALLBACK_URL = os.environ.get("RESIDENT_QA_LLM_FALLBACK_URL", "http://127.0.0.1:11434/v1/chat/completions")
FALLBACK_MODEL = os.environ.get("RESIDENT_QA_FALLBACK_MODEL", "")  # 空=按 /v1/models 实读
# T179 CPU 降级道：L1/fallback 两道路均失败（GPU 被外部产线占满时常见）再走一次
# Ollama 原生 /api/chat + options.num_gpu=0；keep_alive=0 用完即卸，不夺常驻 GPU runner 的位置。
# 与 Tools/qa_smoke.py ollama_cpu_lane 同法（探针面与服务面同一条降级语义）。
CPU_LANE_TAG = "CPU-LANE"
CPU_LANE_URL = os.environ.get("RESIDENT_QA_CPU_LANE_URL", "")  # 空=由 FALLBACK_URL 派生原生口
CPU_LANE_MAX_TOKENS = int(os.environ.get("RESIDENT_QA_CPU_LANE_TOKENS", "120"))
CPU_LANE_TIMEOUT = int(os.environ.get("RESIDENT_QA_CPU_LANE_TIMEOUT", "120"))
SERVER_VER = "resident-qa v1.2 (BigCompute MaaS consumer face)"
_MODEL_CACHE = {}  # chat_url -> (model_id, ts)
_LAST_LANE = {"lane": None, "model": None, "primary_down": False}


def load_anchors(data_path):
    """只读 parse data.js 的 meet.anchors 数组（括号平衡截取→json.loads）。"""
    with open(data_path, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r'"anchors"\s*:\s*\[', text)
    if not m:
        raise RuntimeError("anchors not found in data file")
    depth = 0
    start = m.end() - 1  # at '['
    for i in range(start, len(text)):
        c = text[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                arr = text[start:i + 1]
                anchors = json.loads(arr)
                if not anchors or "id" not in anchors[0]:
                    raise RuntimeError("anchors parse bad shape")
                return anchors
    raise RuntimeError("anchors bracket unbalanced")


def persona_prompt(a):
    bd = (a.get("bd") or "").strip()
    hook = (a.get("hook") or "").strip()
    now = (a.get("now") or "").strip()
    ring = (a.get("ring") or "").strip()
    return (
        f"你是硅基城市里的一位真实居民：{a.get('id')}·{a.get('name')}（{a.get('age')}岁，"
        f"{a.get('district')}区，职业：{a.get('pro')}）。\n"
        f"人设档案：{bd}。{hook}\n"
        f"近况：{now}\n今天的心声（口吻参考）：{ring}\n"
        "城主在观测窗召见你，向你问了一句话。请完全用第一人称、以这位居民的口吻回答："
        "1-2 句话，口语化，自然带一点你的职业与性格色彩；只输出回答本身。"
    )


def _models_url(chat_url):
    """chat completions URL → 同源 /v1/models URL（非该形态返回空串=不探测）。"""
    u = (chat_url or "").rstrip("/")
    marker = "/chat/completions"
    return u[: -len(marker)] + "/models" if u.endswith(marker) else ""


def _native_chat_url(chat_url):
    """OpenAI 兼容 chat URL → 同源 Ollama 原生 /api/chat URL（非该形态返回空串）。

    原生口才带 options.num_gpu / keep_alive（OpenAI 兼容口不保证透传），
    故 CPU 降级道只走原生口。
    """
    u = (chat_url or "").rstrip("/")
    marker = "/v1/chat/completions"
    return u[: -len(marker)] + "/api/chat" if u.endswith(marker) else ""


def _models_url_from_native(native_url):
    """Ollama 原生 /api/chat URL → 同源 /v1/models URL（CPU 道自读模型名用）。"""
    u = (native_url or "").rstrip("/")
    marker = "/api/chat"
    return u[: -len(marker)] + "/v1/models" if u.endswith(marker) else ""


def _cpu_lane_url():
    """CPU 道目标：RESIDENT_QA_CPU_LANE_URL 优先，否则由 FALLBACK_URL 派生原生口。"""
    raw = CPU_LANE_URL or FALLBACK_URL
    return _native_chat_url(raw) or (raw or "").rstrip("/")


def _cpu_lane_model(url):
    """CPU 道模型名：显式 env 优先，其次本机 /v1/models 实读，最后退回 FALLBACK 侧解析。"""
    if FALLBACK_MODEL:
        return FALLBACK_MODEL
    mu = _models_url_from_native(url)
    if mu:
        try:
            ids = [d.get("id") for d in (_http_json(mu, timeout=5).get("data") or []) if d.get("id")]
            if ids:
                return ids[0]
        except Exception:
            pass
    return resolve_model(FALLBACK_URL, "")


def _http_json(url, payload=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_model(chat_url, hint="", ttl=300):
    """实际服务名以 /v1/models 为准（T154 诚实律：披露行禁写部署侧简写）。不可得则退回 hint。"""
    now = time.time()
    hit = _MODEL_CACHE.get(chat_url)
    if hit and now - hit[1] < ttl:
        return hit[0]
    name = hint or ""
    mu = _models_url(chat_url)
    if mu:
        try:
            ids = [d.get("id") for d in (_http_json(mu, timeout=5).get("data") or []) if d.get("id")]
            if ids:
                name = ids[0]
        except Exception:
            pass
    _MODEL_CACHE[chat_url] = (name, now)
    return name


def _chat_one(url, model, messages, timeout):
    payload = json.dumps({
        "model": model or MODEL,
        "messages": messages,
        "max_tokens": 160, "temperature": 0.8,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8"))
    ch = (j.get("choices") or [{}])[0]
    u = j.get("usage") or {}
    return (ch.get("message") or {}).get("content", "").strip(), u.get("completion_tokens", 0)


def _cpu_lane_chat(messages, timeout=None):
    """T179 CPU 降级道：本机 Ollama 原生 /api/chat，num_gpu=0 + keep_alive=0。

    短预算（CPU_LANE_MAX_TOKENS）保证超时可控；空答复视为失败（判据不放宽）。
    返回 (reply, eval_count, model)。
    """
    url = _cpu_lane_url()
    if not url:
        raise RuntimeError("no native ollama url")
    model = _cpu_lane_model(url)
    payload = {
        "model": model, "messages": messages, "stream": False,
        "keep_alive": 0,  # 用完即卸：不夺常驻 GPU runner 的位置
        "options": {"num_predict": CPU_LANE_MAX_TOKENS, "num_gpu": 0},
    }
    j = _http_json(url, payload, timeout or CPU_LANE_TIMEOUT)
    text = ((j.get("message") or {}).get("content") or "").strip()
    if not text:
        raise RuntimeError("cpu lane empty reply")
    return text, j.get("eval_count", 0), model


def ollama_chat(messages, timeout=150):
    """真实推理调用：L1 舰队脑（:8090）→ 不可达降级本机 Ollama
    → 两道路均失败再走 CPU 降级道（T179·多 GPU 被占满时的兜底）。
    返回 (reply, eval_count, meta)。"""
    lanes = [("L1", LLM_URL, MODEL)]
    if FALLBACK_URL and FALLBACK_URL != LLM_URL:
        lanes.append(("FALLBACK", FALLBACK_URL, FALLBACK_MODEL))
    errors = []
    for lane, url, hint in lanes:
        try:
            model = resolve_model(url, hint)
            reply, ev = _chat_one(url, model, messages, timeout)
            _LAST_LANE.update({"lane": lane, "model": model, "primary_down": lane != "L1"})
            return reply, ev, {"lane": lane, "model": model}
        except Exception as e:
            errors.append(f"{lane}={type(e).__name__}")
            if lane == "L1":
                _LAST_LANE["primary_down"] = True
    try:
        reply, ev, model = _cpu_lane_chat(messages)
        _LAST_LANE.update({"lane": CPU_LANE_TAG, "model": model,
                           "primary_down": True, "degraded": True})
        return reply, ev, {"lane": CPU_LANE_TAG, "model": model, "degraded": True}
    except Exception as e:
        errors.append(f"{CPU_LANE_TAG}={type(e).__name__}")
    raise RuntimeError("all lanes down: " + " ".join(errors))


class QAServer(ThreadingHTTPServer):
    def __init__(self, addr, anchors, chat_fn, started=time.time()):
        self.anchors = {a["id"]: a for a in anchors}
        self.chat_fn = chat_fn
        self.started = started
        super().__init__(addr, _Handler)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默律：零 stdout/stderr 噪声
        pass
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            srv = self.server
            self._json(200, {
                "status": "ok", "server": SERVER_VER, "model": MODEL,
                "anchors": len(srv.anchors), "whitelist": "anchors20_v1",
                "uptime_s": int(time.time() - srv.started),
                "llm": dict(_LAST_LANE, primary_url=LLM_URL, fallback_url=FALLBACK_URL),
            })
        else:
            self._json(404, {"err": "not found"})

    def do_POST(self):
        if self.path != "/ask":
            self._json(404, {"err": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            self._json(400, {"err": "bad request"})
            return
        rid = str(req.get("id") or "").strip()
        q = str(req.get("q") or "").strip()
        srv = self.server
        if not re.fullmatch(r"C-\d{5}", rid):
            self._json(403, {"reason": "居民编号格式不对（C-xxxxx）"})
            return
        if rid in HONOR_SEATS:
            self._json(403, {"reason": "荣誉席禁聊（今日不可召见）"})
            return
        a = srv.anchors.get(rid)
        if a is None:
            self._json(403, {"reason": "白名单外·今日不可召见（v1=锚民20·轮值随户籍源待接）"})
            return
        if not (2 <= len(q) <= 200):
            self._json(403, {"reason": "问话 4-60 字为宜，换个问法吧"})
            return
        t0 = time.time()
        try:
            res = srv.chat_fn([
                {"role": "system", "content": persona_prompt(a)},
                {"role": "user", "content": q},
            ])
        except Exception as e:
            self._json(503, {"err": "本地大脑未响应", "detail": str(e)[:80]})
            return
        reply, ev = res[0], res[1]
        meta = res[2] if len(res) > 2 and isinstance(res[2], dict) else None
        dur = round(time.time() - t0, 2)
        model_used = (meta or {}).get("model") or MODEL
        lane = (meta or {}).get("lane") or "L1"
        gate = f"本地AI生成·{model_used}·{lane}·白名单锚民·{ev}tok/{dur}s"
        self._json(200, {"reply": reply or "（沉吟片刻）", "gate": gate, "id": rid})


def serve(args):
    anchors = load_anchors(args.data)
    try:
        srv = QAServer((args.host, args.port), anchors, ollama_chat)
    except OSError as e:
        if e.errno in (10048, 98):  # 端口占用=单实例护栏：静默让位退出
            _p(f"PORT-IN-USE {args.port} single-instance exit")
            return 0
        raise
    _p(f"LISTEN {args.host}:{args.port} anchors={len(anchors)} model={MODEL} l1={LLM_URL} fallback={FALLBACK_URL or '-'} ({SERVER_VER})")
    srv.serve_forever()


def probe(args):
    """一次性真跑验证：真实锚民名单+真实 Ollama 一问+证据 JSON 落 state/。"""
    anchors = load_anchors(args.data)
    a = anchors[0]
    print(f"ANCHORS n={len(anchors)} first={a['id']}·{a['name']} ({a.get('pro')})")
    q = "城主召见：今天铺子里生意怎么样？"
    t0 = time.time()
    reply, ev, meta = ollama_chat([
        {"role": "system", "content": persona_prompt(a)},
        {"role": "user", "content": q},
    ])
    dur = round(time.time() - t0, 2)
    gate = f"本地AI生成·{meta['model']}·{meta['lane']}·白名单锚民·{ev}tok/{dur}s"
    print(f"ASK {a['id']} q={q}")
    print(f"REPLY {reply}")
    print(f"GATE {gate}")
    out = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "id": a["id"], "name": a["name"], "q": q, "reply": reply,
        "gate": gate, "eval_count": ev, "dur_s": dur, "anchors_n": len(anchors),
        "model": meta["model"], "lane": meta["lane"], "server_ver": SERVER_VER,
    }
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = f"state/resident-qa-probe-{stamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"EVIDENCE {path}")
    return 0


def selftest(_args):
    """离线端到端：临时 data 夹具+mock chat_fn+ephemeral 端口真 HTTP 往返。零真实 Ollama/零 state 污染。"""
    import tempfile
    ok = [0]

    def check(name, cond):
        ok[0] += 1 if cond else 0
        print(("PASS " if cond else "FAIL ") + name)
        return cond

    fixture = [
        {"id": "C-00010", "name": "顾阿凤", "district": "NS", "pro": "数据粥铺摊主",
         "age": "68", "hook": "h", "bd": "热心·口头禅「侬晓得伐」", "now": "n", "ring": "r"},
        {"id": "C-00011", "name": "朱鸿奎", "district": "NS", "pro": "时空校准师",
         "age": "74", "hook": "h2", "bd": "较真", "now": "n2", "ring": "r2"},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tf:
        tf.write('window.SILICON_DATA={"meet":{"sec":"s","anchors":' +
                 json.dumps(fixture, ensure_ascii=False) + '}}')
        fx = tf.name
    check("S1 anchors parse (fixture)", len(load_anchors(fx)) == 2)

    def mock_chat(messages, timeout=60):
        sysp = messages[0]["content"]
        assert "顾阿凤" in sysp or "朱鸿奎" in sysp
        return "侬晓得伐，今朝生意蛮好。", 42

    srv = QAServer(("127.0.0.1", 0), load_anchors(fx), mock_chat)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"

    def post(path, obj):
        r = urllib.request.Request(base + path, data=json.dumps(obj).encode("utf-8"),
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    code, body = post("/ask", {"id": "C-00010", "q": "生意怎么样？"})
    j = json.loads(body)
    check("S2 happy path 200 + reply", code == 200 and "侬晓得伐" in j.get("reply", ""))
    check("S3 gate 带 AIGC 披露+机审数据", "本地AI生成" in j.get("gate", "") and "42tok" in j.get("gate", ""))
    code, body = post("/ask", {"id": "C-00001", "q": "聊聊？"})
    check("S4 荣誉席 403", code == 403 and "荣誉席禁聊" in json.loads(body).get("reason", ""))
    code, body = post("/ask", {"id": "C-99999", "q": "聊聊？"})
    check("S5 白名单外 403", code == 403 and "白名单外" in json.loads(body).get("reason", ""))
    check("S6 恶意 id 403", post("/ask", {"id": "C-00010;rm", "q": "聊聊？"})[0] == 403)
    check("S7 空问题 403", post("/ask", {"id": "C-00010", "q": ""})[0] == 403)
    with urllib.request.urlopen(base + "/health", timeout=10) as r:
        h = json.loads(r.read().decode("utf-8"))
    check("S8 health 200 anchors=2", h.get("anchors") == 2 and h.get("status") == "ok")
    req = urllib.request.Request(base + "/ask", method="OPTIONS")
    with urllib.request.urlopen(req, timeout=10) as r:
        check("S9 OPTIONS 204 + CORS", r.status == 204 and
              r.headers.get("Access-Control-Allow-Origin") == "*")
    code, _ = post("/nope", {})
    check("S10 未知路径 404", code == 404)

    # 降级链面（T148 R3）：URL 派生 / 模型名实读失败退化 / 全道不可达显式抛错
    check("S11 _models_url 派生", _models_url("http://h:1/v1/chat/completions") == "http://h:1/v1/models"
          and _models_url("http://h:1/x") == "")
    check("S12 resolve_model 不可达退回 hint", resolve_model("http://127.0.0.1:9/v1/chat/completions", "hint-m") == "hint-m")
    g_llm, g_fb = LLM_URL, FALLBACK_URL
    try:
        globals()["LLM_URL"] = "http://127.0.0.1:9/v1/chat/completions"
        globals()["FALLBACK_URL"] = "http://127.0.0.1:9/v1/chat/completions"
        raised = False
        try:
            ollama_chat([{"role": "user", "content": "x"}], timeout=2)
        except RuntimeError as e:
            msg = str(e)
            # T179：两道路失败后必须试过 CPU 道，且仍不假绿
            raised = "all lanes down" in msg and "L1=" in msg and f"{CPU_LANE_TAG}=" in msg
        check("S13 三道全不可达显式抛错(不假绿)", raised)
    finally:
        globals()["LLM_URL"], globals()["FALLBACK_URL"] = g_llm, g_fb

    # T179 CPU 降级道：原生口派生 / 两道路失败后走 CPU 道 / 空答复不放宽 / gate 披露车道
    g_cpu_url = globals().get("CPU_LANE_URL", "")
    try:
        globals()["CPU_LANE_URL"] = ""
        derived = _cpu_lane_url()
        globals()["CPU_LANE_URL"] = "http://h:2/api/chat"
        overridden = _cpu_lane_url()
        globals()["CPU_LANE_URL"] = "http://h:3/v1/chat/completions"
        overridden_oa = _cpu_lane_url()
    finally:
        globals()["CPU_LANE_URL"] = g_cpu_url
    check("S14 原生口派生+CPU 道 URL 覆盖",
          _native_chat_url("http://h:1/v1/chat/completions") == "http://h:1/api/chat"
          and _native_chat_url("http://h:1/x") == ""
          and _models_url_from_native("http://h:2/api/chat") == "http://h:2/v1/models"
          and _models_url_from_native("http://h:2/x") == ""
          and derived.endswith("/api/chat") and overridden == "http://h:2/api/chat"
          and overridden_oa == "http://h:3/api/chat")
    real_cpu = globals()["_cpu_lane_chat"]
    try:
        globals()["LLM_URL"] = "http://127.0.0.1:9/v1/chat/completions"
        globals()["FALLBACK_URL"] = "http://127.0.0.1:9/v1/chat/completions"
        globals()["_cpu_lane_chat"] = lambda msgs, timeout=None: ("侬晓得伐，慢点也答得上。", 7, "cpu-m")
        reply, ev, meta = ollama_chat([{"role": "user", "content": "x"}], timeout=2)
        check("S15 两道路失败→CPU 道真答复+车道披露",
              "侬晓得伐" in reply and ev == 7
              and meta.get("lane") == CPU_LANE_TAG and meta.get("degraded") is True
              and _LAST_LANE.get("degraded") is True)

        def _empty_cpu(_msgs, timeout=None):
            raise RuntimeError("cpu lane empty reply")
        globals()["_cpu_lane_chat"] = _empty_cpu
        raised = False
        try:
            ollama_chat([{"role": "user", "content": "x"}], timeout=2)
        except RuntimeError as e:
            raised = "all lanes down" in str(e) and f"{CPU_LANE_TAG}=RuntimeError" in str(e)
        check("S16 CPU 道空答复仍抛错(判据不放宽)", raised)
    finally:
        globals()["LLM_URL"], globals()["FALLBACK_URL"] = g_llm, g_fb
        globals()["_cpu_lane_chat"] = real_cpu

    def cpu_mock(messages, timeout=60):
        return "降级道也能说话。", 5, {"lane": CPU_LANE_TAG, "model": "m-cpu"}

    srv2 = QAServer(("127.0.0.1", 0), load_anchors(fx), cpu_mock)
    p2 = srv2.server_address[1]
    threading.Thread(target=srv2.serve_forever, daemon=True).start()

    def post2(path, obj):
        r = urllib.request.Request(f"http://127.0.0.1:{p2}" + path,
                                   data=json.dumps(obj).encode("utf-8"),
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    code, j = post2("/ask", {"id": "C-00010", "q": "生意怎么样？"})
    check("S17 gate 带 CPU-LANE 车道披露",
          code == 200 and f"·{CPU_LANE_TAG}·" in j.get("gate", "") and "5tok" in j.get("gate", ""))
    srv2.shutdown()

    srv.shutdown()
    print(f"SELFTEST {'PASS' if ok[0] == 17 else 'FAIL'} {ok[0]}/17")
    return 0 if ok[0] == 17 else 1


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8792)
    sp.add_argument("--data", default=DEFAULT_DATA)
    sp.set_defaults(fn=serve)
    pp = sub.add_parser("probe")
    pp.add_argument("--data", default=DEFAULT_DATA)
    pp.set_defaults(fn=probe)
    st = sub.add_parser("selftest")
    st.set_defaults(fn=selftest)
    a = p.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
