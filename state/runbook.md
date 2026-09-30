# BigCompute runbook v0.7（<2KB·超线即压缩）

## 启动读序（只读本件·禁重读历史日清）
哨兵轮=双台账增量定位新行·排程轮=mtime 快检零新行即过（疑滞后=尾行复核）；队列步=tasks/TASKS.md+state/queue/ 现役行；收尾=heartbeat 刻痕+rounds.log〔含 push 结果字段=D-15 外推可核·成功/被拒/错误码〕+QA 烟测+定向 commit·日清 ≤3 行·append 一律=round_append.py。

## 当前优先
- 13:1x 毕=M28 计分器 v1.2 工程面；17:3x 毕=M29 结缘费映射=BigHouse 收款出口双翼全收口；**22:43 N1 必跑=month_end_append run（W2 幂等）+夜报满载行=gpu_idle_collector loadline 入轮账本（O-015③·P-32 数据源）**；夜窗候选=M25/T26 烘焙实弹（净窗 5 连 BLOCKED·开则 run_bake 双路→bake_accept_check）；BC-P-15 N1 窗后解冻；00:00 常务轮=C-04 七席投票（席位4 立场行 M30 已备）+云端 token 首报（T37 JSON 已备）；10-05 复核=GPU 观察+_trash 删+批池呈批+净窗频率证据；开店物理件待 CEO；A 档硬门=M21 表。
- open=三队列 9（main M4/M25+tech T2/T9/T15/T26+explore E4/E6/E17·M25/T26 净窗事件·余 blocked 禁造活）；10-02 12:00=验证集独立标注窗（本司禁自标·窗到未标续催）；E17/Q8_0 真窗顺延同窗；keep_alive=-1 即载在役。

## 常设判据/红线速查
- GPU=DRY-RUN 至 10-05（点名=30% 唯一阈值×3 日滚动·70%=方向参考·分机行=bm-a 实测+他机转述·bm-a=派活黑名单）。
- 批池=batch-pool-stock-v1.jsonl+batch_pool.py validate 唯一验收口（POOL_GREEN·DRY-RUN 至 10-05·consumer_plan 全指名）。
- 口径 B 结算唯一｜E0 直呈 CEO｜禁跨仓写｜禁 add -A｜云端三径闸=attribution 必填·周轮云端行=api 聚合｜计分器=与 XL-7 并排看禁单点〔D-17③·验证集窗 10-02〕。
- 每轮=qa_smoke 四条→qa/*.log+png｜三队列常备 ≥10·每窗 ≥1 提案｜SLA=serve_sla_baseline.py series｜轮预算 ≤25min·commit 尾标 [via BigCompute-OSLoop]｜刻痕=审核/队列步毕必刻。

## 关键指针
正典=BLUEPRINT｜任务=tasks/TASKS.md｜mandate=iteration_prompt.txt v2.7+v2.8｜风险=risk-register.md｜回执=HQ-FEEDBACK｜账本=rounds.log｜心跳=heartbeat.txt
