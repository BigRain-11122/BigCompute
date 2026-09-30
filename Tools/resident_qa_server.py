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
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen2.5:7b-instruct"
SERVER_VER = "resident-qa v1.0 (BigCompute MaaS consumer face)"


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


def ollama_chat(messages, timeout=60):
    """真实推理调用（qa 探针同源·keep_alive=-1 U240 常驻标准）。返回 (reply, eval_count)。"""
    payload = json.dumps({
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": -1,
        "options": {"num_predict": 160, "temperature": 0.8},
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8"))
    return (j.get("message") or {}).get("content", "").strip(), j.get("eval_count", 0)


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
            reply, ev = srv.chat_fn([
                {"role": "system", "content": persona_prompt(a)},
                {"role": "user", "content": q},
            ])
        except Exception as e:
            self._json(503, {"err": "本地大脑未响应", "detail": str(e)[:80]})
            return
        dur = round(time.time() - t0, 2)
        gate = f"本地AI生成·{MODEL}·白名单锚民·{ev}tok/{dur}s"
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
    _p(f"LISTEN {args.host}:{args.port} anchors={len(anchors)} model={MODEL} ({SERVER_VER})")
    srv.serve_forever()


def probe(args):
    """一次性真跑验证：真实锚民名单+真实 Ollama 一问+证据 JSON 落 state/。"""
    anchors = load_anchors(args.data)
    a = anchors[0]
    print(f"ANCHORS n={len(anchors)} first={a['id']}·{a['name']} ({a.get('pro')})")
    q = "城主召见：今天铺子里生意怎么样？"
    t0 = time.time()
    reply, ev = ollama_chat([
        {"role": "system", "content": persona_prompt(a)},
        {"role": "user", "content": q},
    ])
    dur = round(time.time() - t0, 2)
    gate = f"本地AI生成·{MODEL}·白名单锚民·{ev}tok/{dur}s"
    print(f"ASK {a['id']} q={q}")
    print(f"REPLY {reply}")
    print(f"GATE {gate}")
    out = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "id": a["id"], "name": a["name"], "q": q, "reply": reply,
        "gate": gate, "eval_count": ev, "dur_s": dur, "anchors_n": len(anchors),
        "model": MODEL, "server_ver": SERVER_VER,
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
    srv.shutdown()
    print(f"SELFTEST {'PASS' if ok[0] == 10 else 'FAIL'} {ok[0]}/10")
    return 0 if ok[0] == 10 else 1


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
