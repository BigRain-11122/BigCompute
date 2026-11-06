# 回执「复制」判据结构性假阳修复（2026-11-07 令牌轮）

## 一句话

`copy` 桶此前按「扫描时点的文件内容指纹」归组，同一路径被后一次运行覆盖后两条回执必然同 sha，
于是确定性的重跑被判成「复制当产物」并**从有效工时里扣掉**——修法：同 digest 但**同路径**=重跑
（`PATH-REUSED`，工时辰照计、仅可见告警），只有**不同路径**同内容才算 `COPY-AS-PRODUCT`。

## 出生地

`Tools/cpu_effective_hours.py` 的 `by_deliverable` 归组 + 剔除逻辑。
诊断来自 11-07 00:4x 的 13 条 copy triage（`docs/ops/receipt-copy-triage-20261107.md` /
`state/receipt-copy-triage-20261107.json`）：13 条里搬运证据 0 条，全是确定性重跑/换名/同路径覆盖。

## 为什么不需要等写侧 `declared_sha`

BC-P-210 的判据前置①原写「先等 T192 写侧指纹」。本修法只用**路径同一性**这一条结构性证据：

- 两条回执指向**不同路径**且内容相同 → 必然有一次搬运（无论有没有 sha 都能判）；
- 两条回执指向**同一路径** → 盘上只可能读到最后一个版本，sha 相同是读取方式的必然结果，
  不是搬运证据。**这条结论与有没有 declared_sha 无关**。

因此这是去假阳，不是新增能力；写侧指纹面仍未落地，`sha_coverage.declared` 仍为 0（如实）。

## 前/后真数（7 日窗，bm-a）

| 指标 | 修前（11-07 00:2x 记录） | 修后（本轮实跑） |
|---|---|---|
| `copy` | 13 | **5** |
| `path_reuse` | 无此桶 | **8**（新增，不剔工时） |
| `counted` | 76 | **84** |
| `net_hours` | 1.084h | **1.119h（4029s）** |

找回的真工时 = 4029 − 3902 = **+126s（+0.035h）**，正好等于 8 条同路径重跑回执的秒数合计——
数字自洽，不是凭空放大。

修后全桶（7d）：`scanned=794 verified=84 remote_unverif=308 missing_local=8 rc_fail=141
replay=0 copy=5 path_reuse=8 zero_seconds=0 no_deliverable=27 bad_json=4 not_work_receipt=193`
verdict 仍 `ATTENTION`（missing_local=8 与 copy=5 仍真实存在）。

1 日窗：`counted=6 net=0.723h (2602s) copy=0 path_reuse=0 missing_local=2`。

## 自测

`python Tools/cpu_effective_hours.py --selftest` → **24/24 PASS**（22 → 24）：
- `S23` 同路径同内容=重跑、不作复制、工时照计（`J-RERUN2` 在 rows 内、不在 copies 内、`path_reuse=1`）
- `S24` 不同路径同内容仍判复制（`copy` 计数不因新规则放水：`copies == ["J-COPY2"]`）
- 既有 `S9`（`J-COPY2` 被抓）与 `S18`（他机同路径不判复制）保持绿。

## 残留风险（如实）

1. **同路径 + 伪造秒数**仍不可辨：一份回执声称「我重跑过」并把同一文件再挂一次，
   在无 `declared_sha`、无 `cmd` 指纹的前提下与重跑不可区分。最终仲裁仍是写侧指纹面（T192）。
2. 两次连续实跑之间读数有微漂（`rc_fail 141/142`、`not_work_receipt 193/192`）——
   回执目录正被他轮并发写入，属实况非抖动，读数一律带时点解释。
3. `copy=5` 这 5 条**不是**被推翻的假阳：它们是不同路径同内容，仍需人工看是否真搬运；
   本修法只收回「同路径」这一类。

## 证据

- `state/cpu-eff-hours-pathreuse-20261107.txt`（7d --list 全量逐条）
- `state/cpu-eff-hours-pathreuse-1d-20261107.txt`
- `state/cpu-eff-hours-7d-20261107-fix.json` / `state/cpu-eff-hours-1d-20261107-fix.json`