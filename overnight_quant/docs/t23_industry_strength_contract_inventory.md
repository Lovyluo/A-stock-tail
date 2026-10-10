# T2.3 行业强度与完整字段链路盘点

调查日期：2026-10-09。固定起点：`7a20c46e11fe5f74ee5a2e666e290a5c660c9dee`。
本轮状态：`INDUSTRY_STRENGTH_NATIVE_SCHEMA_UNPROVEN`；只完成调查，不实现未经证明的字段。
不改变已通过的 10 月 9 日两模块 audit，不把本轮查询补入该批次。

## 精简合同表

三项来源都必须保留 `capability/origin_source/adapter/source/source_version`、
原生 `event_time`、真实 `observed_at/available_at`、请求开始/完成时间、
`decision_cutoff/request_hash/raw_hash`。原始响应和每个子请求必须可重放。
Adapter 只检查身份、envelope 和 provenance，不得静默补写这些字段或换算未知单位。

| 层 | market_breadth | industry_snapshot | fund_flow |
| --- | --- | --- | --- |
| Provider 字段 | 上证 `stock/get.f170` -> `index_change_pct`，单位 percent；三市场 `f104/f105/f106` -> up/down/flat/valid/invalid/total，`breadth_ratio=up/valid` | 五股 code -> 唯一 BK/名称；成分 up/down/flat/invalid/valid/total、breadth_ratio；另需同 BK 原生指数 `change_pct`，单位 percent，不能用成分均值 | 固定五股 `main_net/small_net/mid_net/large_net/super_net`，单位 CNY；累计/增量语义必须由原始接口合同证明 |
| Provider 来源及时间 | eastmoney/direct_http；三市场 f124 与上证 f86 原生时间分别保留并检查 | CoreConception 仅静态映射审计；每页成分 f124 与新增板块原生时间分别检查目标日、cutoff 和请求可用时间 | eastmoney/direct_http；分钟字符串是原生事件标签；当前实现必须恰好等于 feature_cutoff，不能冒用本机时间 |
| Adapter | 固定 provider key、capability、版本、64 位 hash、`event<=observed<=available`；candidate 公开执行拒绝 | 同左；合法 test_only envelope 仅用于合成回归，不激活生产 | 同左；新浪 proxy 不可代替；单位/数值语义仍须 Provider validator 独立检查 |
| 快照 | materialize 将 market payload 放到 stock.market，保留 provenance | 当前按 quote.industry_name 与 industry 名称关联，不按股 code/BK；需要显式同分类口径关联，不能在 Adapter 中补写 quote | 按股 code 追加 fund_flow 数组；不会替调用方作累计转增量 |
| readiness | 有 market；完整来源成功；numeric index_change_pct + ratio 供 features；其他 quote/minute/qfq/news/calendar 门槛仍保留 | 有匹配行业名称、合法 breadth_ratio，且 change_pct 或 relative_strength_pct 至少一个有效；本轮只拟补 change_pct | 至少一条非代理、eligible 的有效 main/large/super 数值；不能据此宣称上游累计/增量已证明 |
| features | 使用 index_change_pct 和 breadth_ratio，公式不改 | 优先 relative_strength_pct，否则 change_pct；个股相对行业收益明确需要 change_pct；无需新增 relative_strength_pct | last-five main_net 求和；上游值若实际累计却标成增量会有误解释风险，不能靠下游求和证明其语义 |

## 已有响应研究与本轮四次查询

10 月 9 日原始四 BK 成分响应只请求 `f2,f3,f12,f14,f124`。
其中 f12 是成分证券代码，f3 是个股涨跌幅，不能代表板块指数涨跌幅。
T1 历史板块列表只找到 BK0474/BK0475 的原生 f3 样本；BK1239/BK1277 未被旧前 100 行覆盖，
因此历史材料不能证明四板块完整合同，也绝不进入新快照。

本轮预先写入不可覆盖请求计划，使用现有 .NET transport，对以下四个精确合同各调用一次：

| BK | 方法与 URL | 参数 | 结果 |
| --- | --- | --- | --- |
| BK0474 | GET https://push2.eastmoney.com/api/qt/stock/get | secid=90.BK0474, fltt=2, invt=2 | MARKET_SOURCE_REQUEST_FAILED，无响应 |
| BK0475 | 同上 | secid=90.BK0475, fltt=2, invt=2 | MARKET_SOURCE_REQUEST_FAILED，无响应 |
| BK1239 | 同上 | secid=90.BK1239, fltt=2, invt=2 | MARKET_SOURCE_REQUEST_FAILED，无响应 |
| BK1277 | 同上 | secid=90.BK1277, fltt=2, invt=2 | MARKET_SOURCE_REQUEST_FAILED，无响应 |

字段请求统一为 `f57,f58,f86,f170,f43,f60,f169`：拟研究代码、名称、原生时间、涨跌幅，
以及价格/昨收/涨跌额的交叉关系。没有收到成功响应，不能证明其在 BK 接口中的含义或缩放，
不能宣布 f170 可用于板块，不得通过合成 fixture 代替来源验证。
四次 transport/worker 调用后停止，无 retry/fallback/换路径；无响应的具体原因未被独立证明。
失败 JSON 的 `network_requests_made=4` 是 transport dispatch 计数，不是已证明的服务端 HTTP 接收次数。
不可修改该原文件；独立补充 replay 明确 `worker_invocations=4`、`network_requests_made=null`、
`upstream_network_activity=unknown`、`failure_reason_verified=false`。

| 调查材料 | 完整 hash |
| --- | --- |
| 请求计划文件 SHA-256 | 103567f23dd16099d5a9d86cf1e3f666056e9c01d3da96d44c526694adffd261 |
| 研究 evidence hash | ab6154e8f97e1f2cc7c3587c985e7393a22b155c9bcce8adc2dbd7498399cbd9 |
| 研究原文件 SHA-256 | a86dcd59343f22425e2bf736076897e2554597dda58e9715776163e063ad429e |
| 外部锚定失败研究 replay hash | cd7477355a558d1020ae702fadfb0d5e743238e6624e54d0d2ee6750901510b0 |
| 两份 replay 文件 SHA-256 | 8174a756472818aff236204192ed72641dd00feb79c6ab2446acf0f517496134 |

材料在 ignored cache 的 `bk_strength_schema_*_2026-10-09*.json`，双 replay 字节一致。
`SCHEMA_RESEARCH_FILE_ANCHOR_VERIFIED` 仅证明外部 SHA 锚定和本地研究文件结构一致；
不是原生板块解析成功、不是独立证明网络失败原因、不是 Provider 合格。

## 一次性列明剩余接入缺口

1. 原生板块 change_pct 尚未取得响应证明。不得用 breadth_ratio、0、个股收益或成分平均替代。
2. 原生产候选 validator 未要求行业强度；future candidate 合同必须验证值、percent 单位、BK/名称身份及原始字段。
3. 原行业代码把最大成分事件分钟截断；新增强度合同必须保留全部原生秒/毫秒并逐子请求校验。
   不能只用聚合最大值隐藏旧日期、未来事件或迟到的某一页；必须逐条日期一致、native<=cutoff、completion<=deadline。
4. 当前快照仅依赖 quote 行业名称作关联。腾讯原生 quote 未提供同一 Eastmoney BK 分类；
   不能补造 quote 字段或使用旧 collector 的跨来源补充路径。需独立、显式、可审计的 code/BK 关联，
   并用实际 test_only Adapter -> normalize/materialize -> readiness -> features 回归证明，而不是只测 payload。
5. T2 records 缺少 PIT 要求存在的 `published_at` 键；非新闻可按真实合同原生声明空值，而非伪造发布时间。
   `decision_cutoff` 当前等于 feature cutoff，直接以较晚 decision_time 冻结会被 `decision_after_cutoff` 拒绝。
   feature cutoff、collection deadline、decision time 必须分别声明，不能覆盖原生 event/observed/available。
6. 当前资金流挑选 `event==feature_cutoff`，没有等待；14:37 请求不能取得未来 14:40 行。
   通用 audit override 只改变市场/行业，资金流默认仍要求 14:50。必须明确新的 audit-only as-of 行规则或
   独立调度等待目标行，保留正式 14:50 精确行门槛。未解决前不能把 20 请求组合称为“可执行”。
7. 资金流 raw 值被直接标记 minute_interval/incremental，没有差分或独立语义证明，且只返回一行。
   readiness 通过不代表 last-five feature 语义正确；需要在同一来源记录中证明单位和累计/增量，不能自动推断。
8. 当前组合仅宽度+行业，无资金流；计划 11 basic/14 maximum。新增后单页应为
   market 2 + mapping 5 + constituent 4 + board quote 4 + fund 5 = 20。
   原行业 cap=12，新增行业 basic=13，旧 cap 必须在新版本中重建，不能绕过。
   若保留旧成分页扩展量，新组合 maximum=23；23*2000ms=46s，
   14:39:30 至 14:40:10 仅 40s，不能宣称最迟启动的预算足够。
   必须事前给出精确页计划及运行开销余量：分页改变需重新预检，不能静默追加或延后截止。
9. Adapter 的新候选 key 已是 `market_source_providers.EastmoneyMarketSourceProviders.*`；
   `RealPointInTimeCollectors.*` 是保留的 legacy_provider_key，不是已激活的新生产绑定。
   旧 collector 的混源/本机时间行为不能作为新候选接入证明，也不能默认调用。
10. 完整快照仍需 quote、12 分钟、60 qfq/可信日历、news 成功等既有门禁；三项 audit 完成也不等于完整策略就绪。

## 实施与下一交易日的前置条件

- 必须先取得并证明四 BK 原生板块字段、单位和时间，再实施 source/evidence/verifier 和 registry 升级。
- 原生板块和成分宽度分别保留请求/响应 hash、时间和逐页 provenance；按 BK 精确关联。
- 新行业合同预计 v2，组合 evidence 预计 v2，最终版本名与 hash 以实现后确定性计算为准，当前不登记虚构版本。
- 同轮三模块 CLI 离线演练必须包括缺字段、错误单位、错 BK、未来事件、重签 change_pct、分页预算和资金流错窗。
- 合成 fixture 必须标记 test_only，不作为真实 data_ready 或资格证据，不激活 candidate。
- 下一日期只能由真实 completed-calendar/session 确认；不提前假定日期，不创建任务/提醒。
- 仍按 14:37 启动、最迟 14:39:30、cutoff=14:40、collection_deadline=14:40:10；
  单请求 2000ms，不以调度缺口为由放宽正式四阶段合同。
- 当前调查不通过，未完成三模块零联网 CLI 演练、完整行业集成测试、下一交易日审计或版本升级。
  不运行最终全量，不推送，不触发 CI；本轮只保存调查与治理文档。

## 代码索引

- `market_source_providers.py`: collect_market_breadth_batch / collect_fund_flow_batch / _valid_industry_record。
- `eastmoney_derived_industry_breadth.py`: collect_records / _collect_board / request_leaves / verify_evidence。
- `source_capability_adapters.py`: candidate envelope 失效关闭及 test_only selection。
- `point_in_time.py`: REQUIRED_TEMPORAL_FIELDS / records_available_at。
- `close_confirmation_readiness.py`: materialize_point_in_time_records / _stock_readiness_errors / _valid_fund_flow。
- `features.py`: _market_strength / _industry_strength / _chip_proxy_score，公式不变。
- `t23_combined_audit.py`: request_plan / precheck / _collect_module，仅旧两模块合同。

## 安全状态

qualification=0/3；data_ready=false；hard_gate_authorized=false；candidates/tickets/orders=[]。
32 entries / bound=8 / candidate=7 不变；PR #30 保持 Draft。
BK1239 历史 invalid_count=2 原样保留，所有历史 evidence 不改写，不拼入下一轮。
