# 机队组网（Tailscale）落地后本司接线预设计件 v1.0

> 溯源：议程滚动件 2026-09-29 12:4x 窗（三轮循环·**族 C 增量**）+O-20260928-1815 ④（跨机远程通道=Tailscale 组网·CEO 装机授权本令生效·bm-a MSI 暂存 %TEMP%\tscale.msi 待提权·bm-b/bm-c 照单自装）+O-20260928-1855 ③⑤（状态面 v2=心跳+池状态 10s 级同步随 Tailscale 落地+「不建中央调度器」律）+R-20260929-fleet-inference-serving-expansion §三/§六（K1-K4 门）+风险台账 R-37（tailnet 端口暴露面）+tech T9/model-manifest-predesign-v1（L2 直拷+manifest 先行律）+E23 borrow-compute-lane-predesign-v1（阶段 2 记账轨）。本件=**本司侧接线就绪度执行单**——组网装机/执行=BigMoney GM 域（零越权零跨仓写）·落地前本司零网络动作（J4）。

## §一 缺口定谳

- **物理件在途**（集团 orders L128 原文实读）：CEO 装机授权已生效·三机各装（bm-a 静默装服务需提权·CEO 一行命令已备；bm-b/bm-c 循环侧照单自装+`tailscale up` 登录 URL 呈 CEO 一次点击）——**落地日可随时到**。
- **本司侧接线需求散在五件以上**：K1 三机连通探针+K4 端口安全门（R- §六）+阶段 1 三机服务发现（R- §三）+首批分发 L2 直拷窗（model-manifest-predesign J1）+借算工单轨阶段 2（borrow-predesign §六）+O-1855 ③ 心跳 10s 同步对接——无单张执行单=落地日碎片化重定向+多窗竞走风险→本件收口为一张步序单。
- 判负路径预注册：三机连通探针窗内不过或 K4 门不过→接线冻结亮牌=合法等待（O-1820 意义性律同源·禁造活）。

## §二 落地触发判据（物理件状态门）

- **信号源=集团侧**（GM 呈报/orders 新行/fleet-protocol 心跳 GREEN 转态）——本司零自测越权·轮审核步只读对账即可见。
- 组网落地判据=三机 tailscale 互通（GM 域验收面）；本司侧接线启动判据=W2 探针 PASS（K1·非门禁类 INFO 信号同 fleet-protocol verdict 语义）。

## §三 接线步序清单（W1-W6·落地日起一窗一步分轮推进）

| 步 | 面域 | 动作 | 判据/门 | 指针 |
|---|---|---|---|---|
| W1 | 服务面 | serve 端口绑定核验：OLLAMA_HOST 绑定 tailnet 接口（非 0.0.0.0 全开·本机环回维持） | **K4 安全门·不过=全线冻结（J2）** | R-37/R- §六 K4 |
| W2 | 联通面 | tailnet 内三机 health ok 只读探针 | K1 PASS=阶段 1 接线启动 | R- §六 K1 |
| W3 | 同步面 | serve 健康行入既有心跳面（零新建机制）·对齐 O-1855 ③ 状态面 v2（10s 级·心跳各自写全队读·verdict=INFO 非门禁） | 心跳行字段合 fleet-protocol §一.1 最低字段 | fleet-protocol §一.1 |
| W4 | 分发面 | manifest v1 正式立件先行（分发日先行律 J1）→按 blob 清单 L2 直拷（qwen2.5 层共享 dedup）·**禁 git 通道维持** | manifest 先行律+SHA256 校验双通道 | T9/model-manifest-predesign §三 |
| W5 | 记账面 | borrow-entry/settle/summary 工程接线（selftest 扩测前置） | 首笔真实借算工单双门（E23 J4） | borrow-predesign §六 |
| W6 | 消费面 | 跨机推理消费记账试点（tx_id=工单号幂等×口径 B） | BC-P-11 批+K1-K4 门全过 | BC-P-11/R- §三 |

- 依赖注记：W1-W3=阶段 1 串行；W4=分发窗独立；W5-W6=阶段 2（W5 前置于 W6）·各自独立门禁·禁跨门并跑。

## §四 边界与红线

- **零冲突声明**：状态面=同步层非调度器（O-1855 ⑤）·本司零中央调度器·零跨机写心跳/池状态（只读消费派生视图·O-1855 ② 单写者律同源）·组网执行权=GM 域。
- bm-a=自动派活黑名单维持（C-20260929-02 7.4 毕业呈批制）+派活 DRY-RUN 观察期至 10-05——本件零派活面零自动执行。
- 身份文件 machine.json=本机私有永不入库（fleet-protocol §一.1 X104/#48 同律）。

## §五 判据 J1-J4（预注册）

- **J1 步序律**：落地日起接线按本单 W1-W6 一窗一步分轮推进·禁一口气大改（轮预算 ≤25min 同律）。
- **J2 K4 冻结律**：端口安全门不过=全部接线冻结·亮牌合法等待·禁绕门。
- **J3 只读律**：集团/他司机队心跳、池状态、协调文件零写（本司侧仅消费·引用不复制）。
- **J4 零动作律**：组网物理件落地前=本司零网络动作零 tailscale 安装零端口变更——本件纯预设计·落地判据以集团侧信号为准。

## §六 验证声明

- 本件=纯文档产出：零代码变更/零网络动作/零跨仓写。
- 引用面实读：集团 orders L128（O-1815 ④）/L134（O-1855）原文实读+cph4/fleet-protocol.md §一.1 心跳条款实读（心跳各自写全队读·verdict=INFO 非门禁·machine.json 私有律）；本司件 R-20260929-fleet-inference-serving-expansion §三/§六+model-manifest-predesign-v1 §三+borrow-compute-lane-predesign-v1 §六+风险台账 R-37 行实读。
- 他机数值/状态全=台账转述非自测（bm-b/bm-c 未自测如实）。
