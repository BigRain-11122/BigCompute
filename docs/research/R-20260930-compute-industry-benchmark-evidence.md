# R-20260930 算力对标行业基准取证件（E42·D-20260930-36 R-C1~C3 阈值校准承接）

> 承接=D-20260930-36 全球头部对标与规律检查规则（docs/audits/global-benchmark-and-check-rules-20260930.md §7 R-C1~C3·L128「R-C1 利用率阈值（头部未披露）→下一轮补取证」）+E35 三尺并排 70% 方向参考线校准数据源。任务板 explore E42。09-30 17:5x 哨兵轮产出。

## 一、立项三问（P-65 判据先立）

1. **消费方是谁**：D-36 审计会话阈值校准面（R-C1~C3 三行阈值回填候选·呈审计件派生链）+本司 GPU 点名判据校准依据面（30% 唯一点名阈值×3 日滚动+70% 方向性参考=C-20260929-02 席2/席7 附款）。
2. **单位经济**：头部毛利结构（折旧主成本占比）直接校验本司口径 B 成本锚三要素方法论（R-20260928-compute-cost-economics 折旧=主成本设计值）——头部官方数若证实折旧主导，则本司成本锚结构方向正确。
3. **合规**：全部数字=SEC EDGAR 官方渠道（官方原文全文+data.sec.gov 官方 XBRL 数据接口）·**禁记忆充当来源=D-36 原文铁律**·零跨仓写·派生计算口径显式注记（非公司列报值不冒充）。

## 二、取证源与判读口径

- **源 1=CoreWeave, Inc.**（Nasdaq: CRWV·CIK 0001769628·SIC 7372）FY2025 Form 10-K（主档 crwv-20251231.htm·accession 0001769628-26-000104·2,792,032B）——双通道取证：①官方原文下载本地全文检索（utilization 26 命中逐条过筛）②data.sec.gov companyconcept XBRL 官方数据接口（us-gaap 概念级数值·非人工转录）。
- **源 2=Lambda**（私有公司·lambdalabs.com）——官方渠道盘点：无财报/招股书申报义务·官方面=站点/博客/定价页（定价结构已由审计件 R-C2 回填·事实·11/12/15·不重复取证）。
- **判读律**：引句=HTML 标签剥离后逐字（原文级）·数值=XBRL 官方标签值（概念名+accession 在案可复验）·百分比派生=显式标注「派生计算」。

## 三、CoreWeave 毛利结构尺（官方源级数字锚）

**XBRL 官方值表**（data.sec.gov/api/xbrl/companyconcept/CIK0001769628/us-gaap/<概念>.json·accn 0001769628-26-000104·10-K 年度值）：

| us-gaap 概念 | FY2023 | FY2024 | FY2025 |
|---|---|---|---|
| Revenue（RevenueFromContractWithCustomerExcludingAssessedTax） | $229M | $1,915M | $5,131M |
| Cost of revenue（CostOfGoodsAndServicesSold） | $69M | $493M | $1,453M |
| 折旧摊销（DepreciationDepletionAndAmortization） | $103M | $863M | $2,454M |

**两口径毛利结构**（派生计算·注记）：

- **表观毛利**（公司列报结构·成本行不含 D&A——10-K 无 GrossProfit 列报概念·companyconcept GrossProfit 接口 404 实证）：FY2023 **69.9%**／FY2024 **74.3%**／FY2025 **71.7%**。
- **含全量 D&A 下界毛利**（派生计算 (Rev−CoR−D&A)/Rev·下界因 D&A 行含非 COGS 折旧不拆分）：FY2023 **24.9%**／FY2024 **29.2%**／FY2025 **23.9%**。
- **D&A/收入比 = 45.0%／45.1%／47.8%**——**折旧=算力生意第一成本实证**（收入近半被折旧吃掉·表观高毛利=成本行剔除 D&A 的列报结构产物）→ 本司口径 B「折旧主成本锚」结构方向获头部官方数校准 ✓（数值面本司仍随物理凭证 ⬜ 不编造）。

## 四、利用率尺——官方披露判负+替代机制锚

**判负（如实）**：10-K 全文 26 处 "utiliz" 命中**全为定性表述**（如 NVIDIA Exemplar Cloud MFU 基准「our infrastructure enables superior utilization compared to reference targets」·产品面「increasing utilization at runtime」）·**零利用率百分比披露**——头部上市公司财报级源**不披露运行时利用率**→ R-C1「≥70% 目标」在官方源层面**维持【推断】级**。

**替代机制锚（原文级三引句·利用率风险的官方呈现形态=合约消纳结构）**：

1. **RPO（长约消纳存量）**：「As of December 31, 2025, we had $60.7 billion of remaining performance obligations ("RPO"), compared to $15.1 billion of RPO as of December 31, 2024. As of December 31, 2025, our committed contracts had a weighted-average contract duration of approximately five years.」——RPO/当期收入=11.8×（FY2025）·加权合约期 ~5 年。
2. **合约结构（take-or-pay 占比）**：「We currently sell access to our platform either through committed contracts, which are take-or-pay, or on-demand, which is pay-as-you-go. For the years ended December 31, 2025, 2024, and 2023, committed contracts accounted for over 98%, 96% and 88% of our revenue, respectively.」——committed 占比 88%→96%→**98%** 逐年上升。
3. **客户集中度（真订单极端形态）**：「We recognized an aggregate of approximately 67% of our revenue from our top customer, Microsoft, for the year ended December 31, 2025. We recognized an aggregate of approximately 77% of our revenue from our top two customers for the year ended December 31, 2024. We recognized an aggregate of approximately 73% of our revenue for the year ended December 31, 2023, from our top three customers.」

**判读**：头部「利用率」的可对标机制=**利用率风险经 take-or-pay 长约结构转移给客户**（非自己承担运行时波动）——「哪条机制让他们赢」=消纳结构先行·采购前已有长约覆盖（与 R-C4 长约消纳律同构）。

## 五、Lambda 官方面盘点（判负如实）

私有公司无申报义务：利用率/毛利**零官方披露**→维持 ⬜ 判负不造数。E42 靶向「CoreWeave/Lambda 级」中，**CoreWeave=唯一具备财报级官方源的可取证头部**（私有头部 Lambda/Together/Vast 同判负结构·其定价结构锚审计件 R-C2 已事实回填·不重复）。70% 方向参考线的「行业头部 70%+」表述在官方源层面**无百分比锚**——该数源于行业转述非官方披露·方向参考维持但加注（§六）。

## 六、R-C1~C3 阈值校准建议（呈 D-36 审计会话·建议级·冻结权在 CEO/委员会）

- **R-C1**：70% 阈值维持推断级+**建议判据表述升级**——头部运行时利用率不可对标（无官方披露）·可对标面改=**消纳结构三指标**：committed 收入占比 ≥90%／RPO/年收入 ≥3×／加权合约期 ≥3 年（头部实证 98%／11.8×／~5 年）。本司 14.0%/17.6% vs 头部的诚实口径=「未消纳产能结构差」非「运行时百分比差」。
- **R-C2**：维持已回填（事实·11/12/15）——本件新增官方佐证引句：头部双轨=committed(take-or-pay)/on-demand(pay-as-you-go) 同构（§四引句 2）。
- **R-C3**：判据不变——集中度引句作头部对照注记（微软 67%=「真订单」极端形态·头部亦承受集中度风险=本律不放松）。
- **R-C4**：**官方机制佐证入档**——头部 RPO $60.7B+5 年加权合约期+take-or-pay 98%=「采购前须有长约消纳方」正典同构实证（审计件「事实·17」行增源可选）。
- **本司 30% 唯一点名阈值（C-20260929-02）**：不动（已收口）；70% 方向性参考线加注「无官方源级百分比锚·方向参考维持·本司可测代理=消费方消纳结构」。

## 七、应用表

| 应用面 | 判据/用途 | 数据/证据 | 状态 |
|---|---|---|---|
| D-36 R-C1 校准 | 利用率阈值表述升级候选 | 10-K 26 命中零百分比+消纳结构三指标（§四） | 观察位（呈审计会话） |
| 口径 B 成本锚校验 | 折旧主成本方向验证 | D&A/收入 45-47.8%（§三·XBRL 官方值） | ✓ 方向校准通过 |
| R-C4 长约消纳律 | 采购前置消纳方机制佐证 | RPO 60.7B+~5 年+take-or-pay 98%（§四引句） | 观察位（呈审计会话） |
| 本司 GPU 点名判据 | 70% 方向参考加注 | 头部无百分比披露（§五） | 建议级注记 |
| 月末成本归集（E25） | 毛利线对照外部锚 | 两口径毛利 71.7%/23.9%（FY2025） | 观察位（N1 后并排） |
| 提案面回流 | 头部机制=先消纳后采购 | §六 R-C1 消纳结构三指标 | 候选（BC-P 面待立） |

## 八、验证声明

- **外证（全 URL·官方渠道）**：EDGAR 公司页（sec.gov/cgi-bin/browse-edgar?action=getcompany&company=coreweave·CIK 0001769628 实证）·10-K 主档 sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm（本地全文下载 2,792,141B·26 处 utiliz 逐条过筛）·XBRL 接口 data.sec.gov/api/xbrl/companyconcept/CIK0001769628/us-gaap/{RevenueFromContractWithCustomerExcludingAssessedTax,CostOfGoodsAndServicesSold,DepreciationDepletionAndAmortization}.json（GrossProfit 接口 404=无此列报概念·CoGS 404 同判）·EDGAR FTS efts.sec.gov/LATEST/search-index?q="utilization"&ciks=0001769628（38 命中定位）。引句=HTML 标签剥离逐字（含 RPO/take-or-pay/集中度三段全文）。
- **内知**：本件无——全部数字/引句来自上列官方源当日抓取（2026-09-30 17:4x-17:5x·复抓日志=本声明在案即验）。
- **派生计算注记**：两口径毛利/D&A 占比/RPO 倍数=本件显式公式计算（§三§四标注「派生计算」）·非公司列报值·下界口径因 D&A 行不拆 COGS/Opex 显式声明。
- **局限如实**：头部利用率百分比判负维持（26 命中定性零数字）·Lambda 面判负维持（私有零披露）·阈值建议=建议级（冻结权在 CEO/委员会）·本件零跨仓写零真实交易。

（派生链临时件：$env:TEMP/crwv-ftsearch/——10-K 与 8-K ex992 官方原文缓存·不入仓不提交）

## 九、E43 增量·次级头部 Nebius+超大规模三源并排（09-30 18:1x 哨兵轮·explore E43）

> E42 判负承接（§八 局限行「次级头部补证窗」）·全部数值=data.sec.gov SEC 官方 XBRL 接口当日抓取（2026-09-30 18:0x-18:1x·declared UA）·禁记忆充当来源维持。

**源 3=Nebius Group N.V.**（Nasdaq: NBIS·CIK 0001513845·data.sec.gov submissions 接口官方确认 name/ticker）FY2025 20-F/A（accession 0001104659-26-065681·nbis-20251231x20fa.htm·2026-05-22 filed·period 2025-12-31）XBRL 官方值：

| us-gaap 概念 | FY2024 | FY2025 |
|---|---|---|
| Revenues | $117.5M | $529.8M |
| CostOfRevenue | $73.4M | $166.2M |
| Depreciation | ⬜未取 | $411.0M |
| DepreciationAndAmortization | ⬜未取 | $417.9M |

- **列报结构数学证明**：CoR($166.2M)＜Depreciation($411.0M)→**CoR 必然排除折旧**（与 CoreWeave 同构·§三）→表观毛利=**68.6%**（FY2024 37.5%·派生计算）。
- **含全量 D&A 下界毛利**（派生计算·同 §三 下界口径）=(529.8−166.2−417.9)/529.8=**−10.3%**——次级头部建设期极端形态（产能爬坡·折旧前置）。
- **D&A/收入=78.9%**（vs CoreWeave FY2025 47.8%）——折旧第一成本在纯 AI infra 次级源面**更极端成立**。

**源 4=Microsoft**（CIK 0000789019·10-K FY2026〔2025-07-01→2026-06-30〕）／**源 5=Amazon**（CIK 0001018724·10-K FY2025）超大规模对照（XBRL 官方值）：

| 源 | Rev | CoR/CoGS | D&A（口径注记） | 表观毛利（派生） | D&A/Rev |
|---|---|---|---|---|---|
| Microsoft FY2026 | $331,839M | $106,374M（CostOfGoodsAndServicesSold） | $34,300M（Depreciation） | 67.9% | 10.3% |
| Amazon FY2025 | $716,924M | $356,414M（CostOfGoodsAndServicesSold） | $65,756M（DepreciationDepletionAndAmortization）·Dep $41,860M | 50.3% | 9.2% |

- **四源并排结构判读**：纯 AI infra（CoreWeave 47.8%／Nebius 78.9%）vs 超大规模（MSFT 10.3%〔Dep 口径〕／AMZN 9.2%）——**折旧占比梯度=纯算力≫超大规模**（多元业务稀释+成熟资产基座）；**口径 B「折旧主成本锚」四源方向全成立（4/4）**；表观毛利带 50-72% 全部依赖「成本行剔除/低置 D&A」列报结构。
- **列报口径注记 ⬜**：MSFT/AMZN CoGS 是否含折旧=全文核受阻未断言（见验证声明墙注）；排除折旧证仅 Nebius（数学）/CoreWeave（原文+GrossProfit 404）两源在案。

**利用率尺（E43 靶面）如实**：Nebius 20-F/A 全文 utiliz 逐条过筛 **⬜ 顺延**（www.sec.gov 通道墙·冷却窗重试承接 tech T36）；MSFT/AMZN 10-K 同 ⬜——**本窗不可宣称「双头部零披露」结论加固**（CoreWeave 26 命中零百分比单源在案·其余源待筛）→ R-C1 利用率百分比维持【推断】级不动；毛利结构尺升级为**四源官方并排**（本节）呈 D-36 R-C1~C3 校准面。

**验证声明（E43 增量·复抓日志）**：
- 外证全通（declared UA·data.sec.gov）：submissions/CIK0001513845.json×2（name/ticker/20-F/6-K 档案清单）+companyconcept×3（Nebius Revenues/CostOfRevenue/DepreciationDepletionAndAmortization——末者仅 Yandex 期旧值→companyfacts 全枚举定位现役 tag=Depreciation/DepreciationAndAmortization）+companyfacts×3（Nebius/MSFT/AMZN·AMZN 重复键 Python 容错解析）。
- **通道墙如实**：www.sec.gov **4 抓 403 止损**（browse-edgar×2+Archives 20-F/A 主档×2·declared UA 不豁免·IP 级「undeclared automated tool」拦截页）——**触墙根因=本机 fetch_content httpx 无 UA 首抓**→「SEC 域先声明后访问」纪律新行（tech T36 承接·§九本声明=复抓日志在案即验）。
- 局限如实：Nebius FY2024 D&A 未取（FY2025 单年够用不扩抓省请求）；三源 utilization 全文筛 ⬜；全部百分比=本件显式公式派生·非公司列报值。
- 临时件：$env:TEMP/e43_amzn.py（AMZN companyfacts 重复键解析脚本·非源缓存）；nbis-20fa.htm 下载失败零缓存。

## §十 T36 SEC 通道纪律件落地+efts 单试判负实录（09-30 18:2x 轮）

- **纪律件落位**：技能 `bigcompute-verify-official-source` SKILL.md 增 §4「SEC 官方域通道纪律」——先声明后访问律（通道序=web_fetch 原生 UA→fetch_content `backend="curl"` TLS 指纹→**禁 httpx 裸通道首抓 SEC 域**）+触墙止损（冷却 ≥10min+同通道复抓 ≤1·再负=本窗判负）+data.sec.gov XBRL 三接口备用通道 SOP（不受墙·§九全数实证）+efts.sec.gov FTS 全文筛单试通道+判负=「不披露」结论加固实证面如实记录。
- **efts 单试判负（复抓 ≤1 已用尽）**：`web_fetch` efts.sec.gov/LATEST/search-index（q="data center utilization"·ciks=0001513845 Nebius 靶）→ **403**；同通道复抓 `fetch_content backend="curl"` → **curl_cffi 未装**（duckduckgo-mcp-server[browser] extra 缺装·通道序第二位现不可用·装件评估挂维护窗非本轮）→ **efts 通道本窗判负**。
- **www 通道维持判负**（E43 §九 4×403 已用尽本窗额度）→ **三源利用率全文筛=SEC 双通道本窗全判负**，⬜ 顺延下窗；候选通道（下窗单试评估）=①curl_cffi 装件后 efts 复试②公司 IR 官网自托管申报件（nebius.com / microsoft.com/investor / ir.aboutamazon.com·官方源级但非 SEC 域·适用 §1 铁律同律逐字引用）。
- **诚实律维持**：「双头部零披露」结论加固本窗仍不宣称（CoreWeave 单源 26 命中零百分比在案·其余三源待筛）——纪律执行本身即本窗产出（止损面=省盲抓轮预算·§2.5 收严实证）。
