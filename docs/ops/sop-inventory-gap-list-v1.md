# BigCompute《SOP 盘点与补建清单》v0.2（O-2026-0930-027 承接件）

- 令据：O-2026-0930-027 全集团 SOP 建制令（2026-10-04 22:23 委员会通道落地·九司+CPH4 承接·盘点清单=首窗回执·10-07 治理日聚合审）。
- 双闸判据（O-027 §③）：轻量闸=单件 ≤200 行/25KB+入口窄化三问；判据前置闸=可执行判据+机器可验优先。
- 证据口径：行数/字节=2026-10-04 22:5x 全量逐件实测（PowerShell Measure-Object -Line + Length·行数=非空行口径）；缺位判定=全仓 glob+`git log --all` 双零命中。
- 版本进度：**步① 在册盘点=10-04 22:43 夜班轮落盘**；**步② G2 三级对照矩阵+机检件=10-05 00:1x 轮落盘（sop_coverage_check.py selftest 7/7+真实 check GREEN·证据 state/sop-coverage-20261005-001835.json）**；步③ 对标补全/步④ 排期收口=10-06 分轮（≤10-06 EOD 全件收口呈 10-07）。

## 一、在册盘点（步①）

### A. 治理面（mandate/runbook/章程）
| 件 | 路径 | 行/字节 | 判据（前置冻结） | 上游指针 | 状态 |
|---|---|---|---|---|---|
| 值班夜轮 mandate v2.7+2.8 批次 | Tools/iteration_prompt.txt | 7/10.0KB | 每轮固定步+自审步+刻痕律+QA 四条收尾闸 | 集团 orders 正典族（O-20260926-2253-HQ-C/L254/省token令④） | 在册·机检=轮账本+刻痕行 |
| runbook v0.10 | state/runbook.md | 9/2.0KB | <2KB 线·启动读序 | T-20260928-29（Executive Protocol 适配） | 在册 |
| 调研部章程 v1.0 | docs/research-dept-charter.md | 13/5.0KB | 每窗 ≥1 R- 实质件·P-65 三件套 | P-2026-09-26-18 建制令 | 在册 |
| QA 自验 charter v1.0 | docs/qa-smoke-test-charter.md | 31/2919B | 四条清单+证据形态+扩展探针 5/6+执行律+引用链归一 | orders L254 | **✓ 10-04 哨兵轮落盘（G1 补建毕·6 处引用归一·R-47 缓解主动作毕）** |

### B. ops 族（27 件·类型分列）
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

### D. Tools 机检面（37 件·注册表）
全数带 selftest/探针子命令=机器可验判据面（各轮 rounds.log 自验在案；反重复律查册对象=集团 cph4/README 注册表只读引用，本司零重复建制）。
行数实测全录（38 件）：bake_accept_check(231) banned_words_check(65) batch_pool(207) batch_token_estimate(90) bonsai_fleet_trial_probe(133) city3d_bake_readiness_probe(142) city3d_bake_spike(127) clean_window_probe(164) cloud_attribution_audit(186) compliance_gate_check(98) cost_ledger(978) e12_package_anchor_probe(104) gpu_energy_profile(186) gpu_idle_collector(508) holiday_readiness_check(150) inference_energy_anchor(179) knowledge_sync_audit(131) model_blob_manifest(273) month_end_append(259) month_end_collect(229) q8_trial_probe(274) qa_smoke(217) queue_check(58) quota_design(87) resident_qa_server(261) round_append(87) round_budget(109) round_score(207) score_validation(185) sec_fulltext_screen(156) self_drive_metrics(119) serve_concurrency_probe(138) serve_sla_baseline(222) sla_drift_preaudit(162) sop_coverage_check(169) storm_leftover_check(143) vat_break_even(131) vram_window_probe(165)——共 38 件（10-05 00:1x 轮 +sop_coverage_check·G2 机检面=F3 注册表漂移探针防再漂移）。

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
| 缺口 | G4 Tools 新件上册 SOP（selftest 必带+注册表行+反重复查册·现散见 mandate/runbook 无单件） | 判据前置=机检可验（selftest 子命令+D 面行数实测+qa_smoke 过）；对标=业界自验惯例（pytest/cargo test 同律）+cph4 注册表 SOP 族查册面 | ⬜ 10-06 自驱轨窗候选 |

## 三、对标依据指针（步③·每件带对标·无对标=拍脑袋违例）
- 已带官方原文级对标在册件：legal 12 件全数（见 §C 锚列）+ops 商业件锚=BLUEPRINT §五+CEO 方案归档（docs/plans/）。
- 步③ 补全面：每件补「顶尖对标双源」行级指针（O-022 标杆定谳律=Steam 爆款方法论+业界头部工程实践；GitHub 雷达高星件=O-021 波）——10-06 轮收口入本表各列。

## 四、排期（步④·每司每窗 ≥1 件·夜窗/自驱轨承接不占业务车道）
- 10-04 窗：G1 charter 落盘（提前兑现）。
- 10-05 窗：**G2 三级对照矩阵+机检件落盘（本窗 ≥1 件兑现·sop_coverage_check.py selftest 7/7+check GREEN）**。
- 10-06 窗：步③ 对标双源行级补全（legal/ops 各件+G3/G4 对标核验）+G4 上册 SOP（自驱轨候选）+本清单 v1.0 定稿（≤10-06 EOD）→ 呈 10-07 治理日委员会聚合审。
