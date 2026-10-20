# v0.5 无资金流行业共振与量价确认研究基线

## 定位与冻结范围

- 策略 ID：`close_confirmation_noflow_v1`；生命周期 `research_only`。
- 基线：`c13549fd543371fafe0ec908858e7f53ae84ce7b`，从已核实的最新 `origin/main` 分出。
- 仅离线研究排序，不实现研究入选、模拟买卖或正式信号。无总分入选阈值，分数不是上涨概率。
- 不改 `close_confirmation_v1`、legacy、共享 readiness、生产 Registry、Dashboard 或默认策略。
- 固定五股仅用于数据链路：000001、000333、600000、600519、601318；不代表全市场收益有效性。
- PM 已接受 2026-10-19 行业同轮候选审计，范围仅限上述五股、四 BK、该来源版本和 14:40 audit 合同；不授予 14:50 来源资格。
- 旧资金流实现、UNPROVEN 冻结和证据保留；停止语义搜索和旧策略推进，不用本版本绕过旧策略门禁。

## 数据需求与剩余接入差距

本表合并此前独立接入差距核对，不再另起来源调查。所有真实准入均待验证桥接，不能由 JSON 中的 `qualified=true` 自证。

| 数据/用途 | 已有来源与证据 | 现有接入及正式资格 | 新策略要求/真实缺口 |
| --- | --- | --- | --- |
| 已完成交易日日历 | 腾讯 S1；10/19 有 completed-calendar 原件 | bound，版本/来源校验已有 | 必须排序、唯一、严格早于目标日；需要本版本可审计记录桥接，不使用 weekday proxy |
| 当日交易状态/报价 | 腾讯固定五股；10/19 session 5/5 | quote bound，显式 envelope | 14:50 同批报价、价格 CNY、涨幅 percent、停牌/涨跌停/可交易性明确；上午 session 不能当14:50报价 |
| 市场基准及宽度 | 东财三个市场计数与上证指数 f170，历史 audit | candidate、S2 0/3 | 基准 SH:000001；全 A 有效股票口径/池版本；尚无获准正式 14:50 组合批次 |
| 身份→BK→行业 | 东财 CoreConception、成分宽度、fltt=1 强度 | 新组合 candidate 未激活；10/19 单轮 audit 接受 | 同源、同批、精确 BK 关联和原生涨幅；14:40证据不可挪为14:50资格 |
| 分钟量价 | mootdx 固定端点与五股，既有3/3有限范围 | minute_bar bound；逐笔仅审计 | 12个完整连续区间，截至14:50；minute_start原生标签14:38–14:49；60秒、shares/CNY、真实成交额及可信区间/最终性证据；真实语义桥接仍缺失 |
| 个股新闻 | 东财 S1真实 evidence | bound；成功零新闻合法 | 来源成功及明确风险审阅 CLEAR；缺发布时间/来源失败/风险未知拒绝 |
| 全球新闻 | 东财 S1真实 evidence | bound；成功零新闻合法 | 与个股新闻独立状态；未知不能当无风险 |
| 公告 | 巨潮403、SSE/SZSE/BSE候选材料 | 均 candidate/unqualified | 唯一实际交易所身份、发布时间精度、风险审阅；仍是实际阻断，不以新闻替代 |
| 可交易性/流动性 | 报价标志和有效分钟量额 | 新研究派生规则，不授来源资格 | 明确非停牌、非涨跌停，可交易性已核验；12分钟成交额≥100万元为本版本未校准研究过滤，不改旧阈值 |
| 60日qfq/历史成本proxy | 腾讯 S1 daily_bar_qfq bound | 原能力和旧门禁保留 | 本策略公式不需要日线；可选成本proxy仅独立审计展示，缺失不阻断、不排名，当前不新增计算器 |
| 资金流/主力筹码/盘口 | 资金流原始值CNY，其他语义UNPROVEN | 原冻结不变；盘口未资格 | 全部移出新策略需求、特征、门禁和决策hash；仅原始审计，不构造净流入或中性盘口 |

### 准入层次与来源限制

1. `noflow_research_input_v2` 接受 records-first 显式输入，不从网络或旧缓存自动构造。旧v1文件保留，不自动迁移或重签为v2。
2. `noflow_research_record_v2` 每条携带能力、身份、版本、批次、交易日、时间合同hash、请求/raw/payload hash；分钟记录另有 `noflow_minute_interval_v1` 区间/最终性元字段。
3. 复用既有 PIT 检查、证券身份规范化、生产 Adapter 矩阵和 provenance 路由；没有修改其标准。
4. 公开真实路径先检查精确 bound/provider key 和来源资格，**仍拒绝缺少独立核验的 noflow 来源桥接**。本轮并未实现或批准真实来源准入器；即使真实标签看似合格，也返回 `verified_noflow_source_bridge_not_available`。这不是资金流阻断。
5. `--test-only` 只接受每条均为 `synthetic_test_only.noflow / synthetic_noflow_v2 / test_only` 的合成输入，不能用于真实来源提升。
6. payload hash 和外部文件 SHA 分别检查数据损坏与文件身份，不能单独证明来源/字段语义真实。真实候选 audit 原件仍交原 verifier，不由新策略重签成正式数据。

## 时间、身份与单位合同

- 正式研究目标沿用保守 minute-start 四阶段：feature_event_cutoff=14:50:00；collection_deadline=14:51:05；decision_time=14:51:10；execution_not_before=14:52:00。
- 复用 `build_close_time_contract` 生成时间形状，不在生成器中补造 VERIFIED 或 probe hash。输入必须有已核验分钟标签；本轮合成 hash 仅在 test-only fixture。
- `decision_cutoff` 必须等于 decision_time，不是 feature cutoff。事件不得晚于 feature cutoff；event≤observed≤available≤collection_deadline；同日且有时区，不截断毫秒或平移原生时点。
- 分钟准入以实际 `interval_end<=feature_cutoff` 为准，不以原生起点标签代替成交区间终点。minute_start的12个完整区间为 `[14:38,14:39)` 至 `[14:49,14:50)`，不要求纳入起点14:50的行；`[14:50,14:51)` 即使在14:51:05前已完成且is_final=true仍拒绝。
- 每条必须分别保留 `native_label`、`minute_label_semantics`、`interval_start`、`interval_end`；`event_time=native_label=interval_start`，长度精确60秒，同日、连续、无重叠。按实际终点排序后完整覆盖14:38–14:50，不平移/截断原生时间，也不改共享旧策略规则。
- `finalized_at`及最终性证据均必须存在，且 `interval_end<=finalized_at<=observed_at<=available_at<=collection_deadline`。仅is_final=true不能证明最终性；即便早1ms宣称最终可用也拒绝。
- 本轮仅合成路径接受显式 `VERIFIED_SYNTHETIC_TEST_ONLY` 区间证据，区间/最终性hash须与合成probe锚点匹配；该匹配不证明上游真实语义。公开真实路径仍要求独立桥接，不因合成通过获得资格。
- 分钟 OHLC、量、额均有效，量额为各60秒区间值而非累计值。volume=0仅在amount=0时合法，计算窗口分母必须非零。
- 报价、基准与行业涨幅在同批同日的14:50原生事件时点对齐。证券身份由代码及交易所规范化，不能把 SZ:000001 当 SH:000001。
- 股票按有证据的 `security_identity → board_code` 关联；分类固定 `eastmoney_coreconception+board_constituent_breadth_v1`；名称不用于 join、不补写腾讯报价。
- 同一BK的多股视图必须一致；完全相同重复记录去重，冲突/缺股/缺分钟/其他身份拒绝，不用“最后一条”掩盖冲突。
- up/down/flat/invalid/valid/total为非负整数；valid=up+down+flat>0；total=valid+invalid；breadth=up/valid。BK1239 invalid_count=2原样进入审计与特征，不转平盘。
- 新闻公告的每个 item 必须有发布时间及精度；同日date-only拒绝；过去date-only保留精度；晚于cutoff或观察时间拒绝。来源成功零条允许，但风险审阅仍须明确CLEAR。未实现基于关键词猜测“无风险”。

## 公式与参数

所有参数为**人工冻结、未校准的研究假设**，不是沿用旧总分阈值，不代表最优、盈利或预测概率。`parameter_hash()`、`feature_hash()`、`requirement_hash()`分别覆盖参数、公式说明和需求合同，版本互相引用。

记 C(x)=min(1,max(0,x))，涨幅单位均为百分数/百分点：1表示1%，不是0.01。

| 组 | 固定公式（组值0–1，权重均25%） |
| --- | --- |
| 市场状态 M | mean(C(0.5+上证涨幅/4), 全A breadth_ratio) |
| 行业共振 I | mean(C(0.5+原生BK涨幅/6), 同源BK breadth_ratio) |
| 相对强弱 R | mean(C(0.5+(个股涨幅-上证涨幅)/10), C(0.5+(个股涨幅-BK涨幅)/10)) |
| 量价结构 P | mean(C(0.5+VWAP位置%/3), C(0.5+VWAP斜率%/1.5), C(0.5+成交量比变化/2), C(0.5+尾段涨幅%/4)) |

- 总分=100×0.25×(M+I+R+P)；仅降序排序，同分按六位代码排序。没有入选阈值或自动重分配权重。
- VWAP=sum(amount_CNY)/sum(volume_shares)，范围为上述12个完整成交区间，**不是全天VWAP**，不以price×volume补成交额。现有特征 `window_start/window_end` 保留原生标签14:38/14:49的含义，不冒充实际区间边界；实际边界由输入和readiness规范记录中原样保留的interval字段确定。
- VWAP位置=(最后收盘价/窗口VWAP-1)×100；斜率=(12分钟VWAP/前7分钟VWAP-1)×100，即两个扩展窗口端点相隔5分钟。
- 成交量比变化=sum(最后5行volume)/sum(此前5行volume)-1；负值合法，但缺分母拒绝。
- 尾段涨幅=(最后close/最后5行首行open-1)×100。正方向表示尾段确认；不引入盘口或资金流。
- 无新行业relative_strength_pct字段；只有个股相对明确上证/BK基准的差值，双方同批时点可追溯。
- 流动性下限为窗口成交额1,000,000 CNY，属于新需求合同的未校准假设，不修改旧策略。窗口或权重更改必须版本化，不能事后选优。

## 哈希、输出和缓存隔离

- `ingest_hash`覆盖原始输入，允许随审计资金流、重复顺序变化。
- `decision_hash`只覆盖本策略、参数/需求、时间合同、readiness结果和排名特征；不含资金流/原始输入hash。`snapshot_hash`在本策略命名空间覆盖同一决策内容。
- 数据类型 fund_flow/fund_flow_audit/order_book/transaction/daily_bar/cost_proxy 在准入、评分与决策哈希前排除；根级其他审计内容同样不参与。无法通过 forged incremental/eligible 标志提升资格。
- `audit_source_index`独立保留被使用记录的请求/raw hash、版本、时点，不能回流评分。区间元字段在输入和readiness的rows/state完整保留，不通过标签改写隐藏原始区间。
- 本轮需求、输入、record、报告及feature合同升级至v2，区间准入规则参与feature/requirements hash；公式、权重、评分参数和parameter hash不变。旧v1 hash/输出不改写，v1输入不得经新准入器自动通过。
- 新requirements hash：`435b52d1ceeffdd66af9636d4ef8fbd155623a3fd0d4c3f6dbefed9712a33614`；feature hash：`e307d5aedae47fe5d09e10ea3d129032dd56fc1ce1abd53148917fa4dbe57c3d`；parameter hash保持 `3a3346720dcc8134d345bf1e4d7473c0a3b597c1ea9b940938056ccd4f023f9b`。
- 只写 `output_dir/close_confirmation_noflow_v1/{test_only|research}/<snapshot_hash>.json`，复用现有不可覆盖写入函数；原有缓存/快照/报告均不改写。外部SHA只锚定输入，不授予资格。
- 完整合成路径最多为 `SYNTHETIC_RESEARCH_RANKING / research_ready=true / test_only=true`；**正式 data_ready=false、records/candidates/tickets/orders=[]**，研究入选为空。
- 不完整或真实尚未准入路径为 `POINT_IN_TIME_DATA_INCOMPLETE / research_ready=false`，整批无排名，无自动补零、demo、fallback或按可用股重新选池。

## 离线命令与验收

公开默认离线拒绝（退出2，零联网）：

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_noflow_research.py --strategy close_confirmation_noflow_v1
```

对已有外部锚定记录回放，不接受 `--network` 或 live/order 参数：

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_noflow_research.py --strategy close_confirmation_noflow_v1 --input <records.json> --expected-input-sha256 <64位SHA> --output-dir overnight_quant/data/cache/noflow_research
```

可重复零联网端到端演练，实际CLI使用临时目录内明确合成输入，不占用任何真实路径：

```powershell
D:\A-stock\.venv\Scripts\python.exe -m pytest overnight_quant/tests/test_phase105_noflow_research.py::test_actual_cli_external_anchor_no_network_and_write_once -q
```

`--test-only`仅用于上述合成演练。不存在自动构造演示数据的 CLI 分支，也没有默认启用新策略的注册动作。

## 后续验证与放弃条件（本轮不执行）

1. **数据验收**：先独立批准真实市场/行业/公告等来源及新记录桥接、14:50同批单位和时间语义；逐项核对完整性、外部锚定和双重放。不可使用10/19的14:40audit替代；源失败日不算完整日。
2. **冻结实验协议**：获得足够覆盖的历史PIT数据后，在任何绩效计算前，书面冻结股票池、训练/验证/测试日期、费用表、滑点、参与率及阻塞规则、比较基线、指标和否决线。缺数据时不输出收益统计。
3. **时间顺序样本外**：复用 `backtest/walk_forward.py` 的36月训练/6月验证/6月测试、purge5日/embargo5日及最近12月封存设施。参数仅训练区间确定；数据不足即报告样本不足，不缩窗凑结果。
4. **研究排序验证**：预先比较等权四组、上证基准、简单个股动量、旧策略（仅其门禁通过的独立样本）。事先冻结秩相关、换手、覆盖率、分组稳定性、净超额及最大回撤指标；不能只报告最好时期。
5. **模拟交易另行协议**：本轮没有买卖/持有期实现。绩效验证前须明确冻结选股数量与排序截点、14:52后入场窗口及取消规则、T+1最早退出、固定持有期、停牌/涨跌停/一字板、费用滑点、部分成交、流动性参与限制和订单阻塞；复用 event_engine/paper设施但不能直接沿用其默认14:51时间。未冻结就不跑交易绩效。
6. **运行稳定性**：另行批准真实影子观察，测缺失/超时/迟到/重放/数据漂移；操作成功、数据资格、策略有效性分别验收。不因研究排名产生候选、票据或订单。
7. **失败/放弃**：真实单位/来源/时间不可证明则停止数据准入；充分样本外中相对预先基准无稳定净优势、成本敏感性抵消优势、依赖单行业/单时期或微小参数变化即翻转，则否决当前假设。具体数值否决线在看测试结果前冻结；不得事后改成通过。五股链路与合成数据不能证明任何收益。

## 证据索引与安全

- 10/20分钟区间修复前反例：原Head `40f793cbe6e3ba7a897342af2255855837498981` 接受截至14:51的最后区间并返回五股排序。修复后该反例拒绝，完整截至14:50的12区间通过；缺失、长度、重叠、标签、过早最终可用与资金流不变性均有专项。完整修复前后结果及hash：仓库外 `D:\Projects\A-stock-tail\local-archive\recovery\v050-minute-interval-20261020`，不是行情证据。
- 10/19被接受的原批次及完整hash：仓库外 `reports/t31-same-round-audit-20261019-c13549f-recovered-reauthorized-v1` 的 delivery/manifest；原NO_GO目录不变。详情归档至 `docs/project-history/2026-Q4.md`，正文不复制原始市场数据。
- 本版本恢复清单：`D:\Projects\A-stock-tail\local-archive\recovery\v050-noflow-20261019`；文件/diff备份、stash、保护对象核对和最终测试/CI索引均在仓库外。
- 本轮未采集、不改时间服务、不创建任务、不下载历史、不大规模调参、不安装依赖。
- 正式 qualification=0/3、data_ready=false、hard_gate_authorized=false，records/candidates/tickets/orders为空。不合并、不发布、不激活来源。
