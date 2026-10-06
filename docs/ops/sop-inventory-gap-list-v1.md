# BigCompute《SOP 盘点与补建清单》v1.0（O-2026-0930-027 承接件）

- 令据：O-2026-0930-027 全集团 SOP 建制令（2026-10-04 22:23 委员会通道落地·九司+CPH4 承接·盘点清单=首窗回执·10-07 治理日聚合审）。
- 双闸判据（O-027 §③）：轻量闸=单件 ≤200 行/25KB+入口窄化三问；判据前置闸=可执行判据+机器可验优先。
- 证据口径：行数/字节=2026-10-04 22:5x 全量逐件实测（PowerShell Measure-Object -Line + Length·行数=非空行口径）；缺位判定=全仓 glob+`git log --all` 双零命中。
- 版本进度：**步① 在册盘点=10-04 22:43 夜班轮落盘**；**步② G2 三级对照矩阵+机检件=10-05 00:1x 轮落盘（sop_coverage_check.py selftest 7/7+真实 check GREEN·证据 state/sop-coverage-20261005-001835.json）**；**步③ 对标双源行级补全=10-05 12:4x 午班轮落盘（提前于 10-06 排期·§三 表）**；**步④ 排期收口=G4 上册 SOP 同轮落盘（tools-onboarding-sop-v1.md·tech T47 收口）+v1.0 定稿——≤10-06 EOD 窗提前 ~1.5 天·10-07 治理日聚合审呈报面就绪**。

## 一、在册盘点（步①）

### A. 治理面（mandate/runbook/章程）
| 件 | 路径 | 行/字节 | 判据（前置冻结） | 上游指针 | 状态 |
|---|---|---|---|---|---|
| 值班夜轮 mandate v2.7+2.8 批次 | Tools/iteration_prompt.txt | 7/10.0KB | 每轮固定步+自审步+刻痕律+QA 四条收尾闸 | 集团 orders 正典族（O-20260926-2253-HQ-C/L254/省token令④） | 在册·机检=轮账本+刻痕行 |
| runbook v0.10 | state/runbook.md | 9/2.0KB | <2KB 线·启动读序 | T-20260928-29（Executive Protocol 适配） | 在册 |
| 调研部章程 v1.0 | docs/research-dept-charter.md | 13/5.0KB | 每窗 ≥1 R- 实质件·P-65 三件套 | P-2026-09-26-18 建制令 | 在册 |
| QA 自验 charter v1.0 | docs/qa-smoke-test-charter.md | 31/2919B | 四条清单+证据形态+扩展探针 5/6+执行律+引用链归一 | orders L254 | **✓ 10-04 哨兵轮落盘（G1 补建毕·6 处引用归一·R-47 缓解主动作毕）** |

### B. ops 族（28 件·类型分列）
| 件 | 行/字节 | 类型 | 判据/锚 |
|---|---|---|---|
| store-opening-checklist-v1 | 32/5.4KB | 检查表 SOP | BLUEPRINT §五.5+后台 8 项+税负身份决策行（M45 三环闭环） |
| livestream-cart-mount-checklist-v1 | 28/8.2KB | 检查表 SOP | AIGC 标识办法×2+56 号令原文级+盲盒公示三叠加（M8） |
| promotion-packaging-sop-v1 | 20/8.3KB | SOP | 56 号令+平台营销规则后台类目+监督公示（T-27 步6） |
| refund-disputes-sop | 22/3.4KB | SOP | 退款争议处理面 |
| month-end-cost-collection-sheet-v1 | 22/4.3KB | 执行单 SOP | E25 五源清单+N1-N3·N1=月末日 22:43 一命令（M26 selftest 9/9） |
| livestream-cart-scripts-v1 | 48/10.5KB | 操作模板 | M14·十类讲解词+禁用词口播位 |
| livestream-plan-v1 | 135/24.9KB | 计划正典 | 挂载面 v1.1·135 行<200 线 |
| fulfillment-sku-mapping-v1 | 21/6.6KB | 映射表 | M6·三类群×十站·演算实跑 PASS |
| paypoint-compliance-map-v1 | 26/7.5KB | 合规映射表 | M21/M29·C-20261001 附录 A 判例·机检 compliance_gate_check 59/59 |
| paypoint-alignment-matrix-v1 | 33/6.8KB | 对齐矩阵 | T-27·blocked 项随开店平台后台逐项解锁 |
| quota-design-29.9-v1 / 49.9-99-v1 | 36+43/6.4+6.5KB | 数值设计 | M12/M16·quota_design.py 机检·阁 A 档裁定后落值 |
| pricing-confirmation-package-29.9-v1 | 19/5.4KB | 定价包 | BC-F-20260927-03·口径 B 结算唯一 |
| b2b-playbook-v1 / b2b-custom-report-pricing-band-v1 | 53+21/14.2+6.4KB | 操作手册/定价规格 | 城市数据年报 298-388/年档（N7） |
| private-domain-plan-v1 | 51/15.0KB | 计划 | N2 居民成长档案订阅 |
| membership-benefits-v1 | 48/9.8KB | 权益规格 | 会员权益行·随美术侧回填 |
| merchandise-listing-v0 | 21/4.4KB | 上架预清单 | v0 计划态·随开店窗升级 |
| art-asset-dependency-spec-v1 | 25/4.2KB | 需求规格 | T-16 三池需求+授权标签 schema 七字段 |
| empowerment-catalog-v1 | 26/8.4KB | 能力清单 | T-21·四消费方注册+P-67 计价双口径 |
| bonsai-fleet-allocation-report-v1 | 18/3.6KB | 分配报告 | T-31（P-2026-09-28-07 机队分发令） |
| borrow/cloud-cost/fleet-network/model-manifest/store-ledger-wiring predesign ×5 | 36+32+22+43+45 | 预设计 | E23/E21/E26/E22/M7·各带 schema 判据（15 字段族+SHA256 双通道） |
| review-window-1005-package-v1 | 25/6.2KB | 呈批件 | M46/M47·窗开口 ≤12h 数据鲜度判据 |
| tools-onboarding-sop-v1 | 27/3.3KB | 上册门禁 SOP | **G4 缺口补建 10-05 12:4x 轮**：J1 selftest 必带+J2 注册行+J3 反重复查册+J4 qa_smoke 接线+J5 轻量闸（≤200 行/25KB）判据前置冻结·上册五步·对标双源在件 §三 |

### C. legal 操作件（12 件·全数官方原文级锚）
| 件 | 行/字节 | 判据/锚 |
|---|---|---|
| user-service-agreement | 86/**25.6KB ⚠** | 生成式 AI 办法第十二条+入驻三式（L34 用户协议） |
| data-report-desensitization-standard | 45/**29.6KB ⚠** | 个保法+数安法 21/29/30 条+GB/T 37964-2019/42460-2023 国标原文级 |
| blind-box-gacha-compliance | 36/12.4KB | 盲盒指引要点域+月消费上限强制件数值接入 |
| service-continuity-terms | 33/12.2KB | 消保法实施条例 778 自动续费+预售款 |
| tax-category-confirmation-brief-v1 | 33/13.6KB | 12/19 号公告+增值税法 826 号令实施条例双源 |
| pre-final-review-manual-verification-checklist | 30/11.6KB | W1-W8 政府官方源核验单（T-13） |
| official-source-render-index | 27/9.6KB | 核验批官方源 URL 索引（M37·15 源全录） |
| purchase-checkbox-copy | 32/7.5KB | 确认程序第二十条第二次（W1） |
| data-processing-agreement | 45/6.7KB | B 端合同数据授权条款 |
| b2b-no-label-delivery | 37/7.1KB | B 端无标识交付边界 |
| tipping-declaration | 26/6.0KB | 打赏声明 |
| privacy-policy | 51/9.6KB | 隐私政策 |

⚠=超轻量闸 25KB 线两件（合计 2/44 件）→ 瘦身复审候选（O-027 §③a 季度复审面·legal 操作件是否适用 SOP 单件闸=呈 10-07 聚合审定谳·本轮只注记零改动）。

### D. Tools 机检面（40 件·注册表）
全数带 selftest/探针子命令=机器可验判据面（各轮 rounds.log 自验在案；反重复律查册对象=集团 cph4/README 注册表只读引用，本司零重复建制）。
行数实测全录（40 件）：bake_accept_check(231) banned_words_check(65) batch_pool(207) batch_token_estimate(90) bonsai_fleet_trial_probe(133) city3d_bake_readiness_probe(142) city3d_bake_spike(127) clean_window_probe(164) cloud_attribution_audit(186) compliance_gate_check(98) cost_ledger(978) e12_package_anchor_probe(104) gpu_energy_profile(186) gpu_idle_collector(508) holiday_readiness_check(150) inference_energy_anchor(179) knowledge_sync_audit(131) ledger_tail_fix(172) model_blob_manifest(273) month_end_append(259) month_end_collect(229) q8_trial_probe(274) qa_smoke(217) queue_check(58) quota_design(87) resident_qa_server(261) round_append(87) round_budget(109) round_score(207) score_validation(185) sec_fulltext_screen(156) self_drive_metrics(119) serve_concurrency_probe(138) serve_sla_baseline(222) sla_drift_preaudit(162) sop_coverage_check(169) storm_leftover_check(143) vat_break_even(131) vram_window_probe(165) review_pkg_refresh(198)——共 40 件（10-05 00:1x 轮 +sop_coverage_check·G2 机检面=F3 注册表漂移探针防再漂移；10-05 22:4x 夜班轮 +review_pkg_refresh〔BC-P-40 批活转化·tech T49·复核窗数据面一命令刷新器·上册 SOP J1-J5 全过〕；10-06 12:4x 午班轮 +ledger_tail_fix〔BC-P-46 批活转化·tech T52 台账尾行修复器·上册 SOP J1-J5 全过〕）。

### E. 上游集团面 SOP 族（只读引用·反重复律查册对象）
集团 14+ 件 SOP 族（新公司 SOP/五闸审查/音频标准/批建造 SOP/调研协议/极简执行协议/值班夜轮 mandate 等）=cph4 注册面在册——本司新建/补建一律先对照查册，禁重复建制。

## 二、缺口补建清单（步②·G2 三级对照矩阵=10-05 00:1x 轮落盘）
| # | 缺口 | 判据前置 | 排期建议 |
|---|---|---|---|
| G1 | qa-smoke-test-charter.md 落盘（6 处引用悬空·判据已冻结于 mandate+qa_smoke.py 实装=落盘即引用归一） | 四条清单+证据形态（log+png 渲染法·R-31 承接） | **done 10-04 哨兵轮提前于排期**：docs/qa-smoke-test-charter.md v1.0 在树·≤200 行/25KB 过闸·R-47 缓解主动作毕 |
| G2 | 模块级 SOP 覆盖缺口对照（业务线/司内部门/开发模块三级对照矩阵·O-027 §①） | 每缺口=判据前置冻结+对标依据先行 | **done 10-05 00:1x 轮**：三级矩阵机检化=Tools/sop_coverage_check.py〔L1 业务线 6/L2 部门 4/L3 开发模块 6 三级注册面·F1 存在性+F2 轻量闸+F3 注册表漂移+F4 覆盖汇总·selftest 7/7〕+真实 check **GREEN violations=0**〔三级覆盖 6/6+4/4+6/6·两申报件 DECLARED-PENDING 呈 10-07〕·证据 state/sop-coverage-20261005-001835.json |

### G2 三级对照矩阵（机检注册面摘要·正副本=Tools/sop_coverage_check.py REGISTER）

| 级 | 模块 | 覆盖件（指针） | 判定 |
|---|---|---|---|
| L1 业务线 | 抖音小店电商 | store-opening-checklist+merchandise-listing-v0+fulfillment-sku-mapping+refund-disputes-sop | ✅ 计划态全链（merchandise v0 随开店物理件窗升级在册） |
| L1 业务线 | 直播带货 | livestream-plan+cart-scripts+cart-mount-checklist+promotion-packaging-sop | ✅ |
| L1 业务线 | B 端数据年报 | b2b-playbook+b2b-custom-report-pricing-band | ✅ |
| L1 业务线 | 私域订阅 | private-domain-plan | ✅ |
| L1 业务线 | 会员权益 | membership-benefits | ✅ |
| L1 业务线 | 算力成本商业化 | month-end-cost-collection+quota-design×2+pricing-package | ✅ |
| L2 部门 | 外部成交通道部 | L1 电商四件+store-ledger-wiring-predesign | ✅ |
| L2 部门 | 定价与算力成本核算部 | quota-design×2+pricing-package+tax-brief+month-end-collection+b2b-pricing-band | ✅ |
| L2 部门 | 直播带货运营部 | livestream 四件族 | ✅ |
| L2 部门 | 风控法务部 | legal 12 件+paypoint-compliance-map+alignment-matrix | ✅ |
| L3 开发模块 | 治理循环/QA 巡检/数据采集/法务文本/批池烘焙/履约管线 | §A+§C+§D 件族（iteration_prompt/runbook/两 charter/qa_smoke/collector 族/legal 12/batch_pool+bake/check/batch-pool-stock/test_pipeline） | ✅ 6/6 模块 |
| 缺口 | G3 履约执行 SOP（发货/对账/异常件操作面·现=sku-mapping 映射表+test_pipeline 44/44 机检面·无操作面件） | 判据前置=订单状态机+pipeline 断言挂接；对标=抖音电商平台发货时效规则（官方源·原文级核验待 10-06 收口面） | ⬜ 随开店物理件窗（等待态·不占当期车道） |
| 缺口 | G4 Tools 新件上册 SOP（selftest 必带+注册表行+反重复查册·现散见 mandate/runbook 无单件） | 判据前置=机检可验（selftest 子命令+D 面行数实测+qa_smoke 过）；对标=业界自验惯例（pytest/cargo test 同律）+cph4 注册表 SOP 族查册面 | **done 10-05 12:4x 午班轮提前于 10-06 排期**：docs/ops/tools-onboarding-sop-v1.md 落盘〔J1-J5 判据前置冻结+上册五步+违例处置·≤200 行/25KB 过闸·tech T47 同轮收口〕 |

## 三、对标依据指针（步③·10-05 12:4x 午班轮行级补全·每件带对标·无对标=拍脑袋违例）
口径：**源①=O-022 标杆定谳律**（Steam 爆款方法论/业界头部工程实践/官方原文级锚）；**源②=O-021 GitHub 雷达高星件**（本司 OH- 切片族四窗实存：OH-20260926/27/28+OH-20261005-bigcompute·群仓 cph4/oss-harvest/）。雷达零同域切片=如实注记不造锚（BC-P-43 扩域候选承接）。

### §A 治理面（4 件）
| 件 | 源①（定谳律/业界） | 源②（雷达/OH- 切片） |
|---|---|---|
| iteration_prompt mandate | Google SRE 值班工程惯例（runbook/看门狗/告警纪律·业界头部实践） | llmperf（方法学参考位·OH-20261005）；值守调度域零同域切片如实注记（扩雷达候选 BC-P-43） |
| runbook v0.12 | SRE on-call runbook 精简惯例（<2KB 速查律） | 同上注记（治理域雷达零同域如实） |
| research-dept-charter | 科学预注册惯例（判据前置冻结=临床试验预注册同律） | OH- 切片纪律同构（四窗实存·radar→评估→采用/判负全链=调研消费雷达产线同域） |
| qa-smoke-test-charter | 业界 CI 冒烟门惯例（pytest smoke/CI gate 同律） | optimum-benchmark+gpu-burn（QA 量具线·OH-20261005-bigcompute 切片） |

### §B ops 族（27 件·按件/族行级）
| 件（族） | 源① | 源② |
|---|---|---|
| store-opening-checklist | ruleHouse《【本地生活及虚拟】行业管理规范》2026-07-01 版原文级（M33 v1.0：入驻三式+类目树+官方授权资质） | doudian/jinritemai SDK 族 top5（OH-20260927 §三） |
| livestream-cart-mount-checklist | 56 号令+AIGC 标识办法+盲盒指引原文级三叠加（M34 W5/W8）+Steam 爆款方法论·转化纪律 | douyin 生态 top5 判负例（R-27：商业自动化仅官方开放平台 API 路线） |
| promotion-packaging-sop | 56 号令原文级（M34 步⑤·原价口径条款逐字） | 同上（促销自动化=官方 API 面同律） |
| refund-disputes-sop | 消保法 53 条+778 号令原文级（M34 W6：预收款退还+自动续费） | mcp-cn-commerce 售后 lane（OH-20260927 §三：售后列表/详情 SDK 范围同构） |
| month-end-cost-collection-sheet | 口径 B 月末归集正典（BLUEPRINT §五）+月结会计惯例 | tokencost（OH-20260926 §四切片 4：token 成本归集同域·口径 A 对照专用锚） |
| livestream-cart-scripts / livestream-plan | 平台直播规则（R-20260924-dy-live-rules）+Steam 爆款话术/发布节奏方法论（O-022） | douyin 生态 top5（合规参照位） |
| fulfillment-sku-mapping | 平台发货时效规则（G3 对标·官方源核验随开店窗） | mcp-cn-commerce 订单 lane（OH-20260927 §三：订单列表/详情=OrderSource 替换点 1 同构面） |
| paypoint-compliance-map / alignment-matrix | C-20260927-01 判例+五列官方锚族（59/59 机检） | mcp-cn-commerce doudian lane（合规通道同域·live_verified=false 采用前置缺口在册） |
| quota-design×2 / pricing-confirmation-package | P-67 计价双口径+56 号令原价口径原文级 | tokencost+genai-prices（OH-20260926 §四切片 4：token 价格估算双件·口径 A 呈现锚） |
| b2b-playbook / b2b-pricing-band | ruleHouse 行业管理规范+Steam 爆款定价带方法论 | tokencost/genai-prices（同上） |
| private-domain-plan | 778 号令自动续费原文级（M34 W6：显著提醒+≤2 步取消） | mcp-cn-commerce（通道同域参照） |
| membership-benefits | 778 号令+Steam 爆款会员权益方法论 | tiktoken（OH-20260926：token 计量=权益 token 化同域·MIT 采用件） |
| merchandise-listing-v0 | ruleHouse 类目树原文级（M33）+Steam 上架页方法论 | doudian SDK 族 top5（同上） |
| art-asset-dependency-spec | Steam 爆款美术资产管线方法论（O-022 定谳律） | 美术管线域雷达零同域切片如实注记（扩雷达候选 BC-P-43） |
| empowerment-catalog | BLUEPRINT §五+P-67 计价双口径 | llama.cpp serve 生态（OH-20260927 §四切片 2：PrismML-Eng fork 实弹消费先例） |
| bonsai-fleet-allocation-report | fleet-allocations 正典+O-015 满载律 | gpu-burn（OH-20261005-bigcompute：满载律 loadline 载具·cleared） |
| predesign ×5（borrow/cloud-cost/fleet-network/model-manifest/store-ledger-wiring） | E 系判据链+口径 B 正典（各带 schema 预注册） | gpuhunt（云 GPU 租价·cloud lane 同域·OH-20260926 §四切片 4）+tiktoken（计量面） |
| review-window-1005-package | 集团复核窗判例族（O-029 卡点梳理令=呈批面同构） | optimum-benchmark+gpu-burn（GPU 三尺量具·OH-20261005-bigcompute） |

### §C legal 12 件
对标主体=官方原文级锚自带（§C 锚列·核验批 16 项全成：90/766/778 号令+39 号指引+增值税法/实施条例+个保法/数安法+GB/T 37964/42460 国标全文）；源②=douyin 生态 top5+mcp-cn-commerce 判负例参照（R-27 合规路线）——零新增动作。

### §D Tools 机检面（38 件）
源①=业界自验惯例（pytest/cargo test selftest 同律=G4 对标·全数带 selftest 子命令）；源②=雷达切片族映射：cost_ledger/batch_token_estimate/month_end_collect→tiktoken+tokencost（计量/成本归集）·gpu_idle_collector/qa_smoke/round_budget/bake_accept_check→gpu-burn+optimum-benchmark（量具/满载律）·serve_sla_baseline/serve_concurrency_probe/resident_qa_server→llmperf（percentile 方法学）+llama.cpp（OH-20260927 §四）·model_blob_manifest→git/LFS 官方双锚（R-20260927 分发纪律·雷达零同域如实）·治理探针族（round_append/round_score/storm_leftover 等）→源①为主（雷达零同域如实注记）。

## 四、排期（步④·每司每窗 ≥1 件·夜窗/自驱轨承接不占业务车道）
- 10-04 窗：G1 charter 落盘（提前兑现）。
- 10-05 窗：**G2 三级对照矩阵+机检件落盘（00:1x 轮）+G4 上册 SOP 落盘（12:4x 午班轮·提前于 10-06 候选）+步③ 对标双源行级补全（12:4x 轮）+v1.0 定稿**——四步全毕·10-06 EOD 窗提前 ~1.5 天。
- G3 履约执行 SOP：⬜ 随开店物理件窗（等待态·不占当期车道·对标已前置=平台发货时效规则官方源）。
- **呈 10-07 治理日委员会聚合审**：本清单 v1.0（盘点+缺口+对标+排期四要件齐）+两 legal 申报件 DECLARED-PENDING 定谳面+sop_coverage_check GREEN 证据链。
