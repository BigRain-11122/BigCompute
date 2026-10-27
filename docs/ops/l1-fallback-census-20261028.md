# L1 车道引用点回退面普查（port 8090）

> 产出=Tools/l1_ref_census.py 静态普查（只读·零服务调用）·数据源=roots Tools, ..\..\Tools
> 判据：DOC-REF=注释/文档串；MESSAGE-REF=端口只出现在日志/字典键/错误文案（点名而非调用）；DIAGNOSTIC=只读探针（车道死=预期发现，无需回退）；FALLBACK-OK=**该引用所在作用域**可改指（行内 env／同函数或模块块 env／同域多端点／显式回退关键字）；UNKNOWN=文件内虽有 env 或多端点但不在本调用点作用域（可能永远到不了这个调用）；NO-FALLBACK=客户端裸写且作用域内无任何改指手段。

verdict: **ATTENTION** · files_scanned=285 · refs=71 · DIAGNOSTIC=7 DOC-REF=34 FALLBACK-OK=7 FIXTURE=7 MESSAGE-REF=14 NO-FALLBACK=1 SERVER-SIDE=1

| 文件 | 行 | 判定 | 可改指 | 域 | 片段 |
|---|---|---|---|---|---|
| ..\..\Tools\bench_coder.py | 7 | NO-FALLBACK | - | EXTERNAL | URL = "http://127.0.0.1:8090/v1/chat/completions" |
| ..\..\Tools\coder-serve.ps1 | 11 | DIAGNOSTIC | - | EXTERNAL | try { $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8090/health' -TimeoutSec 3; exit 0 } catch { } |
| ..\..\Tools\coder-serve.ps1 | 34 | SERVER-SIDE | - | EXTERNAL | $argList = '-m "{0}" --alias qwen3.6-35b-a3b --n-cpu-moe 999 -ngl 999 -c 65536 -np 1 --host 0.0.0.0 --port 8090 --jinja -t 24 -fa auto --no-webui' -f $model |
| ..\..\Tools\l1-client.py | 3 | DOC-REF | - | EXTERNAL | """l1-client.py - minimal OpenAI-compatible client for the local 35B lane (bm-a :8090). |
| ..\..\Tools\l1-client.py | 13 | DOC-REF | - | EXTERNAL | Env: L1_ENDPOINT (default http://127.0.0.1:8090) |
| ..\..\Tools\l1-client.py | 23 | FALLBACK-OK | ENV:L1_ENDPOINT(INLINE) | EXTERNAL | ENDPOINT = os.environ.get("L1_ENDPOINT", "http://127.0.0.1:8090") |
| ..\..\Tools\local-only-mode.ps1 | 4 | DOC-REF | - | EXTERNAL | # repointed to the local 35B (llama.cpp OpenAI-compatible, 127.0.0.1:8090) and |
| ..\..\Tools\local-only-mode.ps1 | 100 | FALLBACK-OK | ENV:USERPROFILE(SCOPE) | EXTERNAL | if ($Backend -eq 'local' -and -not $BaseUrl) { $BaseUrl = 'http://127.0.0.1:8090/v1' } |
| ..\..\Tools\memory-budget-gate.py | 98 | MESSAGE-REF | - | EXTERNAL | "fleetlink", "8090", "bm-b", "bm-c", "调度", "自领", "idlewatch", "主agent", "master-agent", |
| ..\..\Tools\model-lane-smoke.py | 9 | DOC-REF | - | EXTERNAL | A brain     : BRAIN_URL (默认 http://127.0.0.1:8090/v1/chat/completions) 年轮短生成+思考泄漏检查 |
| ..\..\Tools\model-lane-smoke.py | 21 | FALLBACK-OK | ENV:BRAIN_URL(INLINE) | EXTERNAL | BRAIN = os.environ.get("BRAIN_URL", "http://127.0.0.1:8090/v1/chat/completions") |
| ..\..\Tools\openclaw-model-switch.ps1 | 2 | DOC-REF | - | EXTERNAL | # Night (19:07): prefer bm-a CoderServe 35B (:8090) when it is actually serving (AutoYield resume). |
| ..\..\Tools\openclaw-model-switch.ps1 | 12 | DIAGNOSTIC | - | EXTERNAL | try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8090/health' -TimeoutSec 4; $alive = $true } catch { } |
| ..\..\Tools\patrol-fleet-carrier.ps1 | 181 | MESSAGE-REF | - | EXTERNAL | $prompt = ('【巡视席·' + $dom.company + '·域=' + $Code + '·载体触发】你是集团 ' + $dom.company + ' 巡视席（PATROL-FLEET·正典=cph4/fleet/dispatcher/PATROL-FLEET/CHARTERS.md ' + $dom.charter_section + ' |
| Tools\borrow_enforce_report.py | 23 | DOC-REF | - | OWN | :8090 coder 8 笔）的执法账消费面；报告级非破坏·账行零改 |
| Tools\borrow_enforce_report.py | 171 | FIXTURE | - | OWN | {"ts": "2026-10-14T23:18:00", "line": "borrow-window runner enforce: NO-KILL coder-exempt (only coder-face llama-server present, coder-exempt=2; :8090 resident exempt per CEO order |
| Tools\cost_ledger.py | 68 | DOC-REF | - | OWN | expansion; endpoint = bm-a :8090 llama.cpp OpenAI-compatible face, lane |
| Tools\cost_ledger.py | 696 | DOC-REF | - | OWN | # L1 metering rail (tech T93): :8090 llama.cpp OpenAI-compatible face. |
| Tools\cost_ledger.py | 1127 | FIXTURE | - | OWN | qq.endpoint, qq.reason, qq.ceo_approved = "127.0.0.1:8090", "selftest", approved |
| Tools\cost_ledger.py | 1206 | FIXTURE | - | OWN | tl.endpoint, tl.reason, tl.ceo_approved = "127.0.0.1:8090", "t94", False |
| Tools\cost_ledger.py | 1429 | FALLBACK-OK | ARGV | OWN | l1r.add_argument("--endpoint", default="127.0.0.1:8090") |
| Tools\fulfillment\pipeline.py | 343 | DOC-REF | - | OWN | blueprint lane = :8090 Qwen3.6-35B, CEO Qwen v3 final switch), L3 |
| Tools\gpu_idle_collector.py | 243 | DOC-REF | - | OWN | """T114 pure helper: True iff the exe path is the :8090 coder |
| Tools\gpu_idle_collector.py | 258 | DOC-REF | - | OWN | a blind name-kill could cross-fire the :8090 coder resident).""" |
| Tools\gpu_idle_collector.py | 299 | DOC-REF | - | OWN | # T114 10-17: the :8090 35B coder resident (CEO order 7ba65da, exe |
| Tools\gpu_idle_collector.py | 337 | DOC-REF | - | OWN | fired the :8090 coder resident 8x while its own watchdog kept |
| Tools\gpu_idle_collector.py | 403 | DOC-REF | - | OWN | rows; the :8090 coder resident (llamacpp-b11429 exe, CEO order |
| Tools\gpu_idle_collector.py | 426 | MESSAGE-REF | - | OWN | "unreadable; a name-kill would cross-fire the :8090 " |
| Tools\gpu_idle_collector.py | 432 | MESSAGE-REF | - | OWN | ":8090 resident exempt per CEO order 7ba65da; ollama-face " |
| Tools\idle_selfcheck.py | 203 | DIAGNOSTIC | - | OWN | LANE8090_URL = "http://127.0.0.1:8090/v1/models"  # 35B 车道（resident_qa_server 默认 LLM_URL） |
| Tools\idle_selfcheck.py | 207 | DOC-REF | - | OWN | """J13（T89）：:8090 车道实况——只看不说谎，三态 token。 |
| Tools\idle_selfcheck.py | 210 | DOC-REF | - | OWN | 背景（2026-10-25 12:1x 实读）：:8090 被外部进程 eCloud.exe 占住（pid 28008 实读）， |
| Tools\idle_selfcheck.py | 219 | DOC-REF | - | OWN | 注：若 RESIDENT_QA_LLM_URL 已切到 bm-d（O-20261015-1335 env 覆盖），本地 8090 |
| Tools\idle_selfcheck.py | 232 | DIAGNOSTIC | - | OWN | s.connect(("127.0.0.1", 8090)) |
| Tools\model_impact_probe.py | 12 | DOC-REF | - | OWN | C 服务面：本地常驻服务健康（resident QA :8792）与 :8090 lane 模型清单。 |
| Tools\model_impact_probe.py | 41 | DIAGNOSTIC | - | OWN | LANE_8090_MODELS = "http://127.0.0.1:8090/v1/models" |
| Tools\model_ref_drift_check.py | 27 | DOC-REF | - | OWN | llama-server :8090 车道模型名非 ollama tags 域 → LANE-8090 informational 态（消费侧注记）。 |
| Tools\model_ref_drift_check.py | 55 | DOC-REF | - | OWN | # 非 ollama 车道模型（llama-server :8090 面·v3 final 迁移正身）→ LANE-8090 态 |
| Tools\model_ref_drift_check.py | 134 | MESSAGE-REF | - | OWN | "unverified": 0, "lane-8090": 0, |
| Tools\model_ref_drift_check.py | 144 | MESSAGE-REF | - | OWN | status = "LANE-8090" |
| Tools\model_ref_drift_check.py | 234 | MESSAGE-REF | - | OWN | print("MODEL-REF-DRIFT files=%d refs=%d live=%d dangling=%d unverified=%d lane-8090=%d exempt-historical=%d" % ( |
| Tools\model_ref_drift_check.py | 236 | MESSAGE-REF | - | OWN | t["lane-8090"], t["exempt-historical"])) |
| Tools\model_ref_drift_check.py | 273 | FIXTURE | - | OWN | 'LANE = "qwen3.6-35b"\n')                   # :8090 lane model -> LANE-8090 |
| Tools\model_ref_drift_check.py | 291 | FIXTURE | - | OWN | and row_status["qwen3.6-35b"] == "LANE-8090", |
| Tools\month_end_collect.py | 15 | DOC-REF | - | OWN | S7=L1 本地推理计量轨（v1.3 增·tech T113）：cost_ledger l1-summary 只读（:8090 四文本车道 usage 回执=唯一 token 源· |
| Tools\month_end_collect.py | 340 | DOC-REF | - | OWN | """S7 源（只读）：cost_ledger l1-summary --month subprocess（:8090 L1 计量轨·T93）。 |
| Tools\month_end_collect.py | 350 | MESSAGE-REF | - | OWN | parsed["note"] = ("L1 本地推理轨（:8090 四文本车道·usage 回执=唯一 token 源 J2 同律·T93）" |
| Tools\month_end_collect.py | 525 | MESSAGE-REF | - | OWN | s7_seg = (f"｜S7 L1 本地推理轨（:8090 usage 回执=唯一 token 源·T93）=rows {rows7}" |
| Tools\p0_moe_measure.py | 4 | DOC-REF | - | OWN | Target: live 35B coder serve (:8090, llama.cpp --n-cpu-moe, CEO-deployed 2026-10-14 12:5x). |
| Tools\p0_moe_measure.py | 10 | DOC-REF | - | OWN | python Tools/p0_moe_measure.py [--base-url http://127.0.0.1:8090] [--out state/v100-p0-night-measure-20261014.json] |
| Tools\p0_moe_measure.py | 35 | FALLBACK-OK | ARGV | OWN | ap.add_argument("--base-url", default="http://127.0.0.1:8090") |
| Tools\qa_smoke.py | 25 | DOC-REF | - | OWN | 9. L1 coder lane :8090 liveness (T106, O-20261015-1335/1500 Qwen v3 |
| Tools\qa_smoke.py | 26 | DOC-REF | - | OWN | final switchover): llama.cpp :8090 (Qwen3.6-35B-A3B) is now the L1 |
| Tools\qa_smoke.py | 31 | DOC-REF | - | OWN | model), so a dead :8090 would surface only as consumer failures. |
| Tools\qa_smoke.py | 67 | DIAGNOSTIC | - | OWN | CODER_SERVE_URL = "http://127.0.0.1:8090/v1/models" |
| Tools\qa_smoke.py | 559 | MESSAGE-REF | - | OWN | return False, ("LANE-ARTIFACT-MISSING %s - the :8090 lane cannot " |
| Tools\qa_smoke.py | 603 | FALLBACK-OK | ENV:BIGCOMPUTE_GROUP_ROOT(SCOPE) | OWN | CODER_PORT = 8090 |
| Tools\qa_smoke.py | 610 | DOC-REF | - | OWN | A foreign image holding :8090 is a different failure class from a |
| Tools\qa_smoke.py | 613 | DOC-REF | - | OWN | eCloud.exe autostart client took 127.0.0.1:8090 on 10-22 and the |
| Tools\qa_smoke.py | 645 | DOC-REF | - | OWN | """T122 pure verdict text for a failed :8090 probe. |
| Tools\qa_smoke.py | 653 | MESSAGE-REF | - | OWN | return ("L1 coder serve 8090 FOREIGN-PORT: %s pid=%s owns " |
| Tools\qa_smoke.py | 659 | MESSAGE-REF | - | OWN | return ("L1 coder serve 8090 LANE-DOWN: %s pid=%s owns the port but " |
| Tools\qa_smoke.py | 662 | MESSAGE-REF | - | OWN | return ("L1 coder serve 8090 LANE-DOWN: no listener on :%d (%r); four " |
| Tools\qa_smoke.py | 668 | DOC-REF | - | OWN | """Probe no.9 (T106): L1 coder lane :8090 liveness, read-only. |
| Tools\qa_smoke.py | 670 | DOC-REF | - | OWN | O-20261015-1335/1500 Qwen v3 final switchover made llama.cpp :8090 |
| Tools\qa_smoke.py | 676 | DOC-REF | - | OWN | dead :8090 would surface only as downstream consumer failures. |
| Tools\qa_smoke.py | 867 | FIXTURE | - | OWN | "no listener on :8090"), |
| Tools\qa_smoke.py | 1069 | DIAGNOSTIC | - | OWN | ("L1 coder serve 8090", probe_l1_coder_serve), |
| Tools\resident_qa_server.py | 36 | FALLBACK-OK | ENV:RESIDENT_QA_LLM_URL(INLINE) | OWN | LLM_URL = os.environ.get("RESIDENT_QA_LLM_URL", "http://127.0.0.1:8090/v1/chat/completions")  # O-20261015-1335 fleet brain lane（bm-d 切换=改 env 零改码·O-20261015-1505 ⑤） |
| Tools\resident_qa_server.py | 81 | DOC-REF | - | OWN | """真实推理调用（:8090 35B-A3B 舰队脑·服务器常驻免钉·O-20261015-1335）。返回 (reply, eval_count)。""" |
| Tools\round_append.py | 255 | FIXTURE | - | OWN | c, m = append_line(p3, "2026-10-28 round done: is the lane 8090 dark?", allow_ascii_qmark=True) |

## 声明出处（人工复核·2026-10-28）

- 本仓 BLUEPRINT.md 对 `8090` 零命中——降级链声明不在本司仓，出处=`cph4/fleet/dispatcher/MASTER-AGENT/BLUEPRINT.md:24`：「降级链：:8090 不可达〔pause/让路/整卡回收〕→直升云 L2+晨检补记」。
- 读法校正：该句是**运维策略**（pause／让路／整卡回收→改走云端），不是「代码内自动回退」。普查实证：代码面零「自动改走云端」实现，最接近的转向手段=env 覆盖（L1_ENDPOINT／RESIDENT_QA_LLM_URL）与 CLI 参数。
- 因此「声明 vs 实装差集」的准确表述=策略存在、自动化不存在；无回退件按 owner 一行改处理，不下「声明造假」结论。

## 可下派的题清单（NO-FALLBACK → owner 一行改）

- 【外部域·登记转 owner，本司不改写】`..\..\Tools\bench_coder.py:7` — 同上。片段：`URL = "http://127.0.0.1:8090/v1/chat/completions"`

## UNKNOWN 待人工读清单（env/端点在本文件但不在本调用点作用域）

- 无

## 诚实注记

- 静态启发式分类：探针判定按关键词（probe/alive/health/socket/connect/v1/models）——措辞变化的探针行可能被计入客户端面，反之亦然；NO-FALLBACK 行须人眼复核后再动 owner 代码。
- 作用域判定按 AST 的 def 范围：同一函数（或模块顶层块）内的 env 读才算可改指；文件其他地方有 env 一律降 UNKNOWN，不冒充绿灯（T150 收紧）。
- 作用域内可改指仍属静态证据：env 是否真的被该调用点消费需逐点读码确认，属改动前一步。
- 本件不改任何 L1 面声明、不改 owner 代码；修复动作按行归各 owner 一行改；外部域行只登记派单（禁跨仓写）。
