# v0.5 真实记录离线桥接交付页

基线 `589b3ea74a7113e9ce7580ab901d0e2dee1885a5`；分支 `codex/v050-real-record-bridge`。
仅连接已有合同，不是来源资格批准、真实选股、绩效验证或生产激活。
需求沿用[新策略数据需求表](v050_noflow_research_design.md#数据需求与剩余接入差距)，不重做行业取证，不恢复资金流搜索。

## 已接通与真实覆盖

| 模块 | 已实现的离线转换 | 本轮真实旧记录兼容覆盖 / 仍有边界 |
| --- | --- | --- |
| 报价 | 腾讯原生CNY价格/昨收、percent涨幅、原生CNY涨跌停价推导限制标志；不补行业名称 | 10/19 session 5条，事件14:20:45，不是14:50；停牌/可交易性未被原始报价证明，保持UNPROVEN |
| 已完成日历 | 保留最新已完成日15:00原生事件及历史日期，排序唯一、60日源合同、目标日排除 | 10/19原件1条，最新10/16；不造目标日或weekday生产日历 |
| 市场 | 原生上证f170、全A三市场计数、单位；源池版本字符串保留，另生成确定性版本摘要 | 10/9原件1条，仅字段兼容；candidate/unqualified，不移到新日期/cutoff |
| 行业 | 证券身份→精确BK、原生成分计数及fltt=1强度；逐子请求hash/时间保留并重放强度 | 10/19同轮原件5条/4BK；BK1239 invalid_count=2保留；14:40候选证据不升级为14:50资格 |
| 新闻/公告 | 按原来源及交易所分区，校验请求代码/URL/hash/HTTP200；保留逐条发布时间与精度；成功零条可表达 | 本轮只做合成Provider行为回归，未复采。真实风险审核仍UNPROVEN；无精度证据不猜datetime，同日date-only拒绝 |
| 分钟 | test-only原生minute_start生成60秒区间，保留标签及最终可用证据；连续12区间截至14:50 | 真实路径明确返回 `MINUTE_SOURCE_INTERVAL_SEMANTICS_UNPROVEN`。已有minute_end资格不能改标成minute_start；份额/成交额区间口径及最终性须独立证明 |
| 资金流 | 准入、特征与决策hash前排除，原始输入仅影响ingest hash | 不求和、不差分、不授增量语义；旧策略门禁未改 |

上表真实材料为**4份原件的12条独立字段投影**，日期/cutoff不同，未组成真实完整输入。
它们的文件SHA核对前后不变。新闻/公告成功不等于CLEAR风险，报价涨跌停标志不等于可交易性已证明。

## 数据与准入边界

- 输入 `noflow_captured_provider_batches_v1`：策略、交易日、批次、原四阶段time_contract及batches。每批含data_type、capability、原生来源三元组、精确provider_key、同批身份和原始records；风险批次另含原始response receipts及已校验scope_code。
- receipts保留method/URL/params/data/json_body、HTTP状态、原始bytes的base64与raw hash、request hash、observed/available；不是仅有一个“成功”布尔值。空结果独立重放原Provider的空/截止后过滤，不把请求失败当零新闻。
- 只消费显式捕获记录的Provider envelope；复用生产Adapter审计及共享PIT。candidate、未知组合版本、test-only及未资格来源仍不可生产路由。合成下游演练与Adapter真实授权分开，测试选择hash及硬门禁false均保留。
- 新策略readiness只接受绑定到实际转换行摘要的运行时桥接上下文，JSON里的qualified、eligible或_bridge不能自授权限。真实单批有任何转换/授权错误，则外层及嵌套研究排序均空，其他独立模块只保留audit投影。
- 原Static/market/交易所Provider中decision_cutoff是feature过滤时点；仅已知版本可映射为目标decision_time，原值/含义留trace。未知版本、旧14:40合同、跨日/跨批拒绝；不以改名接受未来事件。
- 日历及历史新闻保留原生历史event；date精度须严格早于目标交易日，而非人为制造的状态事件日。非新闻published_at=null仅为本桥接的显式字段合同，不改共享PIT。
- 桥接版本 `noflow_real_record_bridge_v1` 独立hash/缓存命名空间；原需求/公式/参数及正式14:50/14:51:05/14:51:10/14:52合同未放宽。真实分钟profile为空，不接受自报VERIFIED或is_final冒充证明。

## 命令与验收

默认离线，缺输入或外部SHA拒绝；不接受network/live/order参数，不构造默认网络Provider：

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_noflow_research.py --strategy close_confirmation_noflow_v1 --provider-capture --input <captured-provider-batches.json> --expected-input-sha256 <64位外部SHA> --output-dir overnight_quant/data/cache/noflow_bridge
```

合成材料必须有 `input_scope=synthetic_provider_capture_test_only`、逐记录synthetic_test_only来源标记及test_only=true，并显式使用 `--test-only`；不自动生成demo。实际CLI双重放、外部锚定、不可覆盖写入及38项Phase106通过；另有新旧策略/公告173项回归及先前Registry等专项，未把重叠数量相加，未跑本地全量。最终提交/唯一全量CI见PR检查及仓库外交付索引。

## 未接通与最小下一步

1. **代码/证据交界**：补齐已批准且可核验的真实分钟标签、区间量额、最终性profile；真实可交易性与风险审核/发布时间精度仍缺有效合同证据，不自动CLEAR。
2. **资格证据**：市场、行业、公告必要资格及绑定、正式14:50同批完整覆盖仍未通过；新行业组合只有候选审计范围。本轮不取得这些资格。
3. **待PM批准**：上述缺口补齐并经审核后，才安排固定五股真实只读全链路验证；不重做已完成BK取证，不重启资金流调查，不回测或模拟成交。

完整hash、原件引用、模块兼容报告、保护核对及恢复索引：`D:\Projects\A-stock-tail\local-archive\recovery\v050-real-record-bridge-20261020`。
正式qualification=0/3、data_ready=false、hard_gate_authorized=false，records/candidates/tickets/orders=[]；Draft PR不合并、不发布、不激活来源。
