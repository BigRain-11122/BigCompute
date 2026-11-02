# 回执产物红面 triage（M97 承办件）

- 生成时间：2026-11-03 00:5x
- 窗口：近 7 日（`python Tools/cpu_effective_hours.py --days 7 --list`）
- 口径修正：本轮先修仪器再 triage——他机（`bm-*`）回执一律 REMOTE-UNVERIF，不再用本机磁盘判真；
  标记（LINE）→ 修复后红面 = 5 missing_local + 4 copy（修复前 5 + 6，另 2 条系跨机误判）。
- 证据：`state/cpu-eff-triage-7d-20261103.json`、`state/cpu-eff-hours-20261103.txt`

## 一、结论先行

| 类别 | 条数 | 判读 |
|---|---|---|
| 真实产出但文件已不在（scratch 目录未受管） | 4 | 工时不可核销；根因=产物写进 `D:\FleetWorker\review`，该目录非受管 |
| 回执声称的产物与命令无因果关系（假回执） | 1 | 记账面缺陷：`deliverable_exists` 由执行器自填，未经落盘复核 |
| 同节点同内容重复交付（复制当产物） | 4 | 只计一次工时；其中 1 组同日 5 分钟内同产物重跑 |
| 跨节点同名同构误判（仪器缺陷） | 2 | 本轮已修，不计入红面 |

## 二、MISSING-LOCAL 5 条逐条

| # | 回执 | 声称产物 | 判定 | 依据 |
|---|---|---|---|---|
| 1 | bm-a/Q-SMOKE-LANE-001（10-29 10:02） | `D:\FleetWorker\review\smoke_lane.txt` | 真实产出·已消失 | cmd 是 `Path(...).write_text('ok')`，写成功才会 rc=0；现文件不存在，目录仍在 |
| 2 | bm-a/Q-TEST-LOCALLLM（10-29 10:53） | `D:\FleetWorker\review\local_llm_test.txt` | **假回执** | cmd 为 `python -c "print(1)"`，从不写该路径；`deliverable_exists:true` 是自填未复核 |
| 3 | bm-a/R-MV-V718-AIGC（10-29 10:20） | `...\v718_label\full_mv_v718_aigc.mp4` | 真实产出·已消失 | stdout 有 `bytes=180654901 out=...`，25.5s；`v718_label` 目录仍在但已空 |
| 4 | bm-a/R-MV-V718-FIDELITY（10-29 10:25） | `D:\FleetWorker\review\fidelity_v718.json` | 真实产出·已消失 | stdout 实出 SSIM 0.9888 / PSNR 40.6 / VERDICT PASS |
| 5 | bm-a/R-MV-V718-SHOTAUDIT（10-29 10:15） | `D:\FleetWorker\review\shot_audit_v718.json` | 真实产出·已消失 | stdout 实出逐镜亮度/锐度表 |

共同点：3/4/5 是同一批 v718 质检链（AIGC 标签 → 保真度 → 逐镜审计），产物全部落在
`D:\FleetWorker\review`（工作区临时目录）。M95 已确认「目录在、文件不在」；本轮补上因果：
**产物从未进入受管目录，被临时目录清理带走**，不是路径解析错误。

## 三、COPY 4 条（同节点、内容逐字节相同）

| 后者 | 前者 | 内容指纹 |
|---|---|---|
| Q-TRIAGE-FLUXVERSE-20261102（10:29:34） | R-PLAN-FLUXVERSE-20261102（10:24:35） | `triage-fluxverse-20261102.md` 18CFB8BE |
| R-MV-V718-STILL-5273（13:25:35） | R-MV-GATE-R2-20261102（13:10:37） | `C22C2699…` |
| R-MV-GATE-1D-20261102（19:40:44） | R-MV-GATE-R2-20261102 | `C22C2699…`（`mv-gate-latest.json` 亦同） |
| R-NEXT-BIGSTREAMM-AUDIT-20261102（01:09:56） | R-NEXT-BIGSTREAMM-AUDIT-20261101 | `62C8EEDC…` |

判读：
1. 前三条是**同一分析重复执行**（真跑了 3.1s / 61.5s / 71.5s），产物没有新增，按律只计一次工时。
2. 第四条是真发现：`R-NEXT-BIGSTREAMM-AUDIT-20261102` 的 cmd 审计的是 **`full_mv_v717.mp4`**，
   与 11-01 同一输入 → 输出逐字节相同。「11-02 的审计」实际是 11-01 的审计，
   **产物日期不反映输入版本**。属 @BigStream 线输入未推进，本司只呈清单不改数。

## 四、仪器缺陷（本轮已修）

修前 `bm-c/C-FIN-ETF-COMPOSITE-20261102` 与 `bm-m/MV-DIRECTOR-PACKET-20261102` 被判 COPY：
二者 deliverable 都是 repo 相对路径，本机同名文件存在 → 被当成「同内容重复交付」。
实读两条回执：bm-c stdout 落 `K:\Fluxgroup\...`、bm-m stdout 落 `/Users/junsheng.sun/...`，
**各自在本机真跑过**（120.5s / 0.1s），内容相同只因脚本与输入确定。

修法（`Tools/cpu_effective_hours.py`）：
- 他机（`bm-*` 节点，或 lane schema 他机，或 posix 绝对路径）回执**先判 REMOTE-UNVERIF，不碰本机磁盘**；
- COPY 分组键从 `digest` 改为 `(node, digest)`，跨节点不参与复制判定；
- selftest 18/18（新增 S18：他机同路径不得核销、不得判复制；S7 期望值同步 1→2）。

修复后：`remote_unverif` 56→78（把跨机读数从"假核销/假复制"归位），
`zero_seconds` 18→0（bm-m 零秒回执属他机，本机无权判缺陷），`copy` 6→4。

## 五、派生动作

1. 本司自纠：v718 三条质检产物如需追溯，只能重跑（输入 `full_mv_v718.mp4` 仍在库；
   AIGC 标签件 172MB 需重生成）——列入 tech 队列，不写他司仓。
2. 提案 BC-P-168：lane 回执的 deliverable 必须落在受管目录（repo 内或持久归档），
   scratch 目录产物记 `EPHEMERAL` 并在窗口内自动降级，不许当产能凭证。
3. 回执写入器应改「自填 deliverable_exists」为「落盘 stat 后写」，否则假回执无法从字段上分辨（BC-P-167 承接面）。