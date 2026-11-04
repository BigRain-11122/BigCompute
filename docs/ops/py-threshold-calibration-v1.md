# py 阈值标定件 v1（M118 / BC-P-191 承接）

> 2026-11-04 夜班轮落地。回答一个问题：既然 `py_cpu`（shell/WMI `LoadPercentage`）与
> `py_cpu_psutil`（psutil 全核平均）在同机同刻差 30-43pp，那 70% 验收线到底挂在谁身上，
> 两通道能不能映射成一个数。
>
> 结论先行：**不映射**。70% 阈值只点名 `py_cpu_psutil` 一个通道引用；`py_cpu` 降级为
> 独立参考读数，禁与阈值比较。理由是三条实测（下方 §二），任何一条都足以否掉映射。

## 一、两通道是什么（差异在第一性上就存在）

| 通道 | 读什么 | 语义 | 窗口 |
|---|---|---|---|
| `py_cpu` | `Get-CimInstance Win32_Processor → LoadPercentage`，每轮 spawn 一个 `powershell.exe` | WMI 瞬时值 | 一次采样一个点，且被 spawn 进程本身占据 |
| `py_cpu_psutil` | `psutil.cpu_percent(interval=1.0)`，同进程直读内核计数器 | 全逻辑核平均 | 1.0s 区间均值 |

两者不是同一个物理量的两种写法：一个是瞬时、一个是区间均值；一个是 WMI 上报口径、
一个是内核计数器差分。**没有先验理由认为它们线性可比**，所以「差多少」必须先实测，
才谈得上标定。

## 二、三条实测（bm-a，2026-11-04，32 逻辑核，共租户在跑）

### 臂 1：n=30 同帧采样 → DIVERGENT

```
verdict=DIVERGENT  shell_median=51.0  psutil_median=18.75  delta=32.25pp  speedup_x=1.4
```

证据：`state/py-calibration-n30-20261104.json`

### 臂 2a：静置 vs 单核满载 → INCONCLUSIVE（不可分辨）

`ncpu=32`，1 核合成负载理论上只值 `100/32≈3.1pp`，而机器环境底噪已 >14pp，
两通道在满载臂上差 18.85pp < 可分辨闸 20pp。**数据没有分辨力，就不判语义**。

证据：`state/py-calibration-semantic-20261104.json`

### 臂 2b：k=4 vs k=16 运动量臂（本臂判据跑之前登记）→ INCONCLUSIVE，且抓出硬矛盾

```
shell_motion=+9.0pp   psutil_motion=+0.2pp   expected_scale=37.5pp
```

加了 12 条满载线程，shell 动了 9pp，而 psutil 只动 0.2pp——`psutil.cpu_percent` 在
这台机器上没有把线程级负载记进来。这不是「差一点」，是两通道之一在共租户环境下
**不作数**。既然不知道哪个通道在说谎，任何「映射系数」都是把噪声固化成公式。

证据：`state/py-calibration-semantic2-20261104.json`

### 臂 3：spawn 自干扰 → MATERIAL（同帧采样本身不成立）

```
median_delta_pp=18.1   rounds=12   spawn_median_s=1.3669
```

在 spawn 前后各读一次 psutil，读数被自己的 spawn 抬了 18.1pp（中位数）。
即：**「同帧 A/B 采样」这个设计在结构上就被污染**——采样动作本身就是负载。
臂 1 的 psutil 读数因此系统性偏高（真实底噪只会更低）。

证据：`state/py-calibration-perturb-20261104.json`

## 三、判据与决策

| 预注册判据 | 值 | 来源 |
|---|---|---|
| 一致性判定带 | 15.0pp | 既有 `DIVERGE_PP` |
| spawn 干扰材料线 | 5.0pp | 本轮登记 `SPAWN_PERTURB_PP` |
| 语义命中容差 | 8.0pp | 本轮登记 `SEMANTIC_TOL_PP` |
| 可分辨距离 | 20.0pp | 本轮登记 `SEP_PP`（两臂共用） |

**决策：不映射（NO MAPPING）。**

1. 语义不可判（臂 2a/2b 均 INCONCLUSIVE）→ 无映射依据；
2. 采样自干扰 18.1pp 已过材料线 → 同帧比较的设计不成立，任何拟合系数都会随机器负载漂移；
3. 因此不产出换算公式，只在引用侧钉死通道名——**这是唯一不会撒谎的标定**。

## 四、阈值引用底线（本条是执行面，可直接被机检）

> **70% CPU 验收线只对 `py_cpu_psutil`（psutil 全核平均、1s 区间）生效。**
> `py_cpu`（shell / WMI `LoadPercentage`）保留为独立参考读数，**禁与 70% 线比较**，
> 禁用于任何「满载/空闲」判决；两字段并列打印，缺一则标 `n/a`，不得互相填充。

落地位置：`Tools/idle_selfcheck.py` 的 `py_cpu_pair_label()` 已并列两字段；
本轮只下裁定不改代码（改判据调用点属另案，见 §五）。

## 五、余项（如实，不假装收口）

- **谁在说谎未定**：臂 2b 的 psutil 不动是真缺陷还是共租户下的调度假象，需要在
  净窗（无 Tuanjie 实例）复跑一次臂 2b；本件不臆断。
- **判据调用点未改**：现役 J8 门槛仍读 `py_cpu`(shell)。按 §四 该读数不该上阈值，
  改调用点=行为变更，转 `state/queue/tech.md` 另案（BC-P-191 后续席）。
- **跨机未做**：bm-b/bm-c/bm-d 未采样（本轮窗内只够 bm-a）。本件判据对跨机不作断言。

## 六、复跑（一条命令可复现本件的每个数）

```bash
python Tools/cpu_read_ab_probe.py run       --rounds 30 --json-out state/py-calibration-n30-<date>.json
python Tools/cpu_read_ab_probe.py semantic  --rounds 8  --busy-cores 1  --json-out state/...json
python Tools/cpu_read_ab_probe.py semantic2 --rounds 6  --busy-low 4 --busy-high 16 --json-out state/...json
python Tools/cpu_read_ab_probe.py perturb   --rounds 12 --json-out state/py-calibration-perturb-<date>.json
python Tools/cpu_read_ab_probe.py selftest   # 19/19 PASS（判据纯函数夹具，不跑真实负载）
```