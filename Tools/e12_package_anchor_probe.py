# -*- coding: utf-8 -*-
"""E12: 19.9 单包 dry-run 首单 token 实测锚探针（quota-design-29.9-v1.md §二 回填面）。

口径 v0（两调用面）: 19.9 算力验证服务爆款管线（一句话→策略→回测→报告页）的 LLM 消费面
= 调用1 策略蓝图生成（L2 本地 qwen2.5:7b 结构化 JSON）+ 调用2 回测报告解说。
回测引擎与报告页渲染=确定性计算零 token，不计入。
E_单包 = Ollama eval_count 累计（Qwen 精确计数口径·quota-design §二）。
模型=现役常驻 7b（keep_alive:-1 维持常驻·零新增 VRAM 占用·让路律零触碰）。
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

API = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b-instruct"
OUT = Path("state/e12-anchor-20260928-2243.json")

SYS_STRATEGY = (
    "你是策略结构化引擎。将用户的一句话交易想法转换为结构化策略 JSON 蓝图，"
    "字段：strategy_name, universe, entry_rule, exit_rule, holding_days, risk_params, assumptions。"
    "只输出 JSON，不要多余解释。"
)
USER_STRATEGY = (
    "帮我验证一个想法：指数连涨三天就买入，持有五天卖出，用沪深300指数历史数据做回测演示。"
)

SYS_REPORT = (
    "你是回测报告撰写器。根据回测结果 JSON 写一段面向买家的结果解说：3-5 句，"
    "只客观陈述统计事实，禁止任何收益承诺或投资建议措辞，结尾附一句合规提示"
    "（本服务为算力验证演示，不构成投资建议）。"
)
USER_REPORT = json.dumps(
    {
        "strategy": "MA3连涨买入持有5天",
        "backtest_period": "2021-01-01..2025-12-31",
        "trades": 47,
        "win_rate": 0.532,
        "avg_holding_days": 5,
        "max_drawdown": -0.082,
        "annualized_return": 0.041,
        "sharpe": 0.63,
    },
    ensure_ascii=False,
)


def call(system: str, user: str) -> dict:
    payload = {
        "model": MODEL,
        "prompt": user,
        "system": system,
        "stream": False,
        "keep_alive": -1,  # 常驻调用必带（Q3 标准第 3 项）
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1024},
    }
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    wall = time.time() - t0
    ev = data.get("eval_count") or 0
    ev_dur_s = (data.get("eval_duration") or 0) / 1e9
    return {
        "prompt_eval_count": data.get("prompt_eval_count") or 0,
        "eval_count": ev,
        "eval_duration_s": round(ev_dur_s, 3),
        "wall_s": round(wall, 3),
        "tok_s": round(ev / ev_dur_s, 2) if ev_dur_s > 0 else None,
        "done_reason": data.get("done_reason"),
        "output_head": (data.get("response") or "")[:120],
    }


def main() -> int:
    print("E12 dry-run first-order probe: model=%s keep_alive=-1" % MODEL)
    calls = []
    for name, sys_p, user_p in (
        ("call1_strategy_blueprint", SYS_STRATEGY, USER_STRATEGY),
        ("call2_report_narrative", SYS_REPORT, USER_REPORT),
    ):
        r = call(sys_p, user_p)
        r["call"] = name
        calls.append(r)
        print("%s: eval_count=%s prompt=%s tok/s=%s done=%s"
              % (name, r["eval_count"], r["prompt_eval_count"],
                 r["tok_s"], r["done_reason"]))

    e_pkg = sum(c["eval_count"] for c in calls)
    e_prompt = sum(c["prompt_eval_count"] for c in calls)
    result = {
        "probe": "e12_single_package_anchor",
        "date": "2026-09-28",
        "window": "22:43 night round",
        "model": MODEL,
        "caliber": "v0 two-call surface (strategy blueprint + report narrative); "
                   "backtest engine & render = deterministic, zero tokens",
        "E_package_tokens": e_pkg,
        "E_package_mtok": round(e_pkg / 1e6, 6),
        "prompt_tokens_total": e_prompt,
        "proxy_anchor_mtok": 0.028,
        "proxy_vs_measured": round(0.028 / (e_pkg / 1e6), 1) if e_pkg else None,
        "calls": calls,
        "note": "manual dry-run first order (batch pool dispatch face stays "
                "DRY-RUN to 2026-10-05); re-measure after SKU finalization",
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("E_package_tokens=%d (%.6f Mtok) vs proxy 0.028 Mtok -> %sx" % (
        e_pkg, e_pkg / 1e6, result["proxy_vs_measured"]))
    print("written: %s" % OUT)
    return 0 if e_pkg > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
