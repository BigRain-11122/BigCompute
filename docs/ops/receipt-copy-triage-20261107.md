# 回执产物「复制当产物」13 条逐条 triage（2026-11-07 令牌轮 · tech T215 收口）

> 口径：`Tools/cpu_effective_hours.py --days 7` 实测 `copy=13`（11-03 时为 4，四日涨 9）。
> 判定法：读 13 条回执原文（id/node/ts/rc/cmd/deliverable）+ 现盘逐件 sha256 + 产物自述字段，
> 逐条给三态之一（确定性重跑／同路径覆盖／真重复交付）+ 证据。零跨仓写、零改判据。

## 一、先说结论（三句话）

1. **13 条里没有一条能证明「谁抄了谁」**——每一组的字节相同都能被「同一确定性脚本 + 同一输入」解释，
   且回执 `declared_sha` 全量为 0（`SHA-COVERAGE-NONE`），没有任何写时指纹可作对证。
2. **5 组（7 条）的根因是读数方式，不是回执行为**：两条回执指向**同一个产物路径**，而工具在读时
   才按**当前文件**算指纹——同路径被后一次运行覆盖后，两条回执必然算出同一个 sha，
   于是「后者」被判 copy 是结构性假阳，与「复制」无关。
3. 真正需要动手的只有一处小缺口：**`mv_full_audit` 的产物不自述审计了哪一版视频**。

## 二、13 条逐条

| # | 回执 id | node | ts | deliverable | 组内 sha256（前 16） | 判定 |
|---|---|---|---|---|---|---|
| 1 | C-QUANT-NOBOND-20261103 | bm-a | 11-04 16:55:43 | quant/bigmoney/results/etf_nobond_vt.json | 0da7d210dfa7ae6a | 确定性重跑 |
| 2 | bigmoney-backtest-2026-11-04 | bm-a | 11-04 20:30:33 | 同上（同一路径） | 0da7d210dfa7ae6a | 确定性重跑 |
| 3 | bigmoney-backtest-2026-11-03 | bm-a | 11-03 12:25:25 | quant/bigmoney/results/etf_composite_pf.json | 64b62465eaacf5fe | 确定性重跑 |
| 4 | R-PLAN-BIGGAME-20261104 | bm-a | 11-04 19:35:06 | queue-receipts/triage-biggame-20261104.md | d111f0dc3f7bb7c3 | 同路径覆盖 |
| 5 | Q-TRIAGE-FLUXVERSE-20261102 | bm-a | 11-02 10:29:34 | queue-receipts/triage-fluxverse-20261102.md | 18cfb8be0a5c3b6a | 同路径覆盖 |
| 6 | Q-TRIAGE-FLUXVERSE-20261105 | bm-a | 11-05 11:10:08 | queue-receipts/triage-fluxverse-20261105.md | 84147b73de9a3bd8 | 同路径覆盖 |
| 7 | Q-TRIAGE-RECON-20261105 | bm-a | 11-05 11:20:06 | queue-receipts/triage-recon-20261105.md | b65059a5e42d75e0 | 同路径覆盖 |
| 8 | R-MV-V718-STILL-5273 | bm-a | 11-02 13:25:35 | queue-receipts/mv-v718-still-5273.json | c22c2699dffec842 | 确定性重跑＋换名 |
| 9 | R-MV-GATE-1D-20261102 | bm-a | 11-02 19:40:44 | queue-receipts/mv-gate-latest.json | c22c2699dffec842 | 确定性重跑＋换名 |
| 10 | R-NEXT-BIGSTREAMM-AUDIT-20261102 | bm-a | 11-02 01:09:56 | queue-receipts/mv-full-audit-20261102.json | 62c8eedc7516c92c | 确定性重跑 |
| 11 | R-NEXT-BIGSTREAMM-AUDIT-20261104 | bm-a | 11-04 11:50:34 | queue-receipts/mv-full-audit-20261104.json | 628b71c092ceb1dd | 确定性重跑 |
| 12 | R-NEXT-BIGSTREAMM-AUDIT-20261105 | bm-a | 11-05 02:40:48 | queue-receipts/mv-full-audit-20261105.json | 628b71c092ceb1dd | 确定性重跑 |
| 13 | R-TRIAGE-BIGGAME-BMM-20261105 | bm-a | 11-05 10:40:06 | queue-receipts/triage-biggame-20261105.md | 925e326d3adf6ec2 | 同路径覆盖 |

### A. 确定性重跑（7 条 · #1/2/3/8/9/10/11/12 中除换名说明外均属此类）

- **etf**（#1/#2/#3）：`cmd` 逐字同为 `python quant/bigmoney/scripts/etf_nobond_vt.py`（#3 为 `etf_composite_pf.py`），
  同机同脚本同输入，回测输出是确定性 JSON → 重跑字节必然相同。两次相隔 3.5h 与 1 天，各有独立 ts，不是搬运。
  注：`bigmoney-backtest-2026-11-03-v3` 在 bm-b 也跑过一次（rc=0，路径 `C:/Fluxgroup/...`）——跨机同名件已被
  `is_foreign` 归 REMOTE-UNVERIF 排除，这条排除是正确的。
- **mv 闸**（#8/#9）：`cmd` 逐字同为对 `full_mv_v718.mp4` 的同一次 `mv_delivery_gate` 调用（含相同的两条 card 区间）。
  产物内容自述 `"file": ".../full_mv_v718.mp4"`、`verdict: PASS`、`frames 5623 / shots 83`——同输入同结论，
  三次运行（13:10 / 13:25 / 19:40）各自写了**不同文件名**（`mv-gate-r2-20261102.json` / `mv-v718-still-5273.json` /
  `mv-gate-latest.json`），三件字节完全相同 `c22c2699dffec842`。
  **换名 ≠ 新工作**：净工时只计一次是对的，但三条回执各自声称了一次独立验收——建议回执注明「同结论重述」。
- **mv 全片审计**（#10/#11/#12）：11-04 与 11-05 批的是 `full_mv_v721.mp4`，两件字节相同 `628b71c0…`
  属同输入确定性重跑；11-02 批 `full_mv_v717.mp4`，字节不同（`62c8eedc…`）——**说明审计输出确实随输入变化**，
  这反过来坐实了下面那条缺口。

### B. 同路径覆盖（5 条 · #4/#5/#6/#7/#13）——根因在读数方式

五组同构：一份 `triage-<线>-<日期>.md` 被**两个不同 job id** 认领
（`R-PLAN-BIGGAME-20261104` + `Q-TRIAGE-BIGGAME-20261104`、`R-PLAN-FLUXVERSE-20261102` + `Q-TRIAGE-FLUXVERSE-20261102`，
余类推）。两边 ts 相隔 0.5–3h，`cmd` 同为 `python Tools/fleet/line_triage.py --line <线>`。

关键在于产物内容带自身时标——`triage-biggame-20261105.md` 首行是「2026-11-05 10:40」，即最后那次运行的时刻；
07:40 那次写成的是「07:40」。也就是说**同一路径被覆盖，前一份产物已不存在**。
而工具是在扫描时对**当前文件**算指纹，两条回执因此必然得到同一个 sha → 必然判「后者 copy」。
这不是回执在搬运，是**仪器把「同路径两次运行」读成了「两份产物内容相同」**。

## 三、由本轮取证得到的两处真实缺口（可查、可改、不需新机制）

1. **`copy` 判定的证据面不可证**：判据 = 扫描时点对当前文件算的 sha，而非写时指纹；
   `sha_coverage.declared=0` 意味着整批回执没有任何一条能自证「我产出的那一件长什么样」。
   后果：同路径覆盖必假阳、真搬运也检不出——两个方向同时失效。
   **不改判据**（改就是重新标定，须单独一轮），只登记给 BC-P-173/T192 的写侧面。
2. **`mv_full_audit` 产物不自述源文件**：`mv-full-audit-*.json` 的键只有
   `frames/fps/shots/epoch_thr/groups/duplicates/...`，**没有 `file`/`source` 字段**。
   而同类工具 `mv_delivery_gate` 的产物第一键就是 `"file": "...v718.mp4"`。
   后果：拿到一份审计 JSON 无法回答「审的是 v717 还是 v721」。本轮的 11-04/11-05 两件字节相同，
   只能靠**回执的 cmd** 推断输入是 v721——产物自身零自证。该工具属集团 `Tools/mv_full_audit.py`（非本司域），
   **本司只登记不修改**，建议转 @HQ / BigStream 线在产物里补 `file`（一行字段，与 mv_delivery_gate 对齐）。

## 四、对净工时的处置

- 7 条确定性重跑中有 4 条被扣（#1/#2/#3/#11/#12 归组后 4 条计 copy），5 条同路径覆盖扣 4 条，
  换名 2 条扣 2 条——合计 13 条不计净工时。
- **本司判读：扣得对，理由要换**。不是「它们抄了」，而是「同一结论的重复推导不是新增工时」。
  两条判据应当分开写在 `cpu_effective_hours` 的输出里（当前统一叫 `copy`，语义过载），
  此项列入 tech T215 的后续注记，不在本轮改工具。

## 五、证据

- 工时器机读件：`state/cpu-eff-hours-7d-20261103.json`（历史）与本轮 `--list --json-out` 实跑（`.codely-cli/tmp/ceh7.json`，一次性）
- 逐件 sha256：本轮实测（表列前 16 位，完整值见下表）

```
quant/bigmoney/results/etf_nobond_vt.json                0da7d210dfa7ae6a size=1824
quant/bigmoney/results/etf_composite_pf.json             64b62465eaacf5fe size=4337
cph4/fleet/queue-receipts/triage-biggame-20261104.md     d111f0dc3f7bb7c3 size=618
cph4/fleet/queue-receipts/triage-biggame-20261105.md     925e326d3adf6ec2 size=618
cph4/fleet/queue-receipts/triage-fluxverse-20261102.md   18cfb8be0a5c3b6a size=538
cph4/fleet/queue-receipts/triage-fluxverse-20261105.md   84147b73de9a3bd8 size=670
cph4/fleet/queue-receipts/triage-recon-20261105.md       b65059a5e42d75e0 size=691
cph4/fleet/queue-receipts/mv-v718-still-5273.json        c22c2699dffec842 size=355
cph4/fleet/queue-receipts/mv-gate-latest.json            c22c2699dffec842 size=355
cph4/fleet/queue-receipts/mv-full-audit-20261102.json    62c8eedc7516c92c size=24045
cph4/fleet/queue-receipts/mv-full-audit-20261104.json    628b71c092ceb1dd size=24305
cph4/fleet/queue-receipts/mv-full-audit-20261105.json    628b71c092ceb1dd size=24305
```

源视频（同批实测，三版互不相同，排除「同一文件换名」）：
`full_mv_v717.mp4 f0a7dce27b9da172 649MB` / `v718 7dac8d4c948e3cee 660MB` / `v721 129fa1a04ba4abe2 663MB`

## 六、如实边界

- 只读现盘 + 回执原文，**没有**任何一次重跑复现（复现会新增一次 sha 相同的产物，把问题扩大）。
- 「确定性」是从 `cmd` 逐字相同 + 输出随输入变化的实证（v717 vs v721 字节不同）推出，非逐脚本读源码确认。
- 13 条全部归属同一台机 bm-a，不跨机外推。
- 本轮**零新增工具、零改判据、零跨仓写**。