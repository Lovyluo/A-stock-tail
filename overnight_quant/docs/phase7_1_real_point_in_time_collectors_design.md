# v0.4.1 真实时点采集器设计

## 1. 目标与范围

v0.4.1 将 v0.4 的手工/回放时点记录入口扩展为真实来源 provider。当前阶段只完成：

- 真实来源连通性验证；
- P0 provider 接口；
- 时点、来源版本、请求哈希和原始响应哈希；
- 主备来源选择和来源状态；
- 自动测试与无输出门禁。

本阶段不修改策略评分权重或阈值，不连接券商，不下单，不点击证券软件。来源验证和
快照采集不会创建候选、票据或订单。

## 2. 数据流

```text
真实 HTTP 来源
  -> RequestsTransport（超时、限频、有限重试）
  -> RealPointInTimeCollectors（解析、字段单位、来源版本）
  -> ProviderBatch（records + 声明的数据类型 + raw hash）
  -> CloseWindowCollector（provider 实际完成时间）
  -> 时点完整性验证
  -> 不可变 14:50 快照
```

`run_real_source_validation.py` 只验证来源，不写快照。正式快照入口仍为
`run_close_snapshot_collector.py`，增加 `--live --codes` 后使用真实 provider，且没有
demo fallback。

## 3. P0 provider

| 数据类型 | 正式主来源 | 备用/审计来源 |
|---|---|---|
| 交易日历 | 腾讯上证指数日线日期集合 | 东财指数日线仅作验证候选，当前不稳定 |
| 市场指数与宽度 | 腾讯三指数 + 东财指数上涨/下跌家数 | 无满足宽度合同的已验证备用 |
| 个股报价 | 腾讯报价 + 东财行业一级映射 | mootdx 尚未完成本轮 SLA 验证 |
| 分钟线 | 东财 `trends2` | mootdx 尚未证明 14:50 可用性 |
| 60 日前复权日线 | 腾讯 `qfq` 请求且响应键为 `qfqday` | 东财 `fqt=1` 当前间歇断连 |
| 行业及行业宽度 | 东财 F10 行业映射 + 东财板块快照 | 百度映射实测错误且不含宽度 |
| 个股资金流 | 东财分钟资金流 | 新浪当前资金快照 |
| 新闻 | 东财全球/个股新闻 + 巨潮公告 | 财联社旧接口失败；NewsNow 无逐条发布时间 |

资金流正式 provider 只返回一条选择链：东财成功时不读取新浪；东财失败时使用新浪
并在 payload 记录 `fallback_from` 和 `fallback_reason`，禁止主备数据重复计量。

## 4. 时间字段

- `event_time`：优先使用来源事件时间。腾讯报价使用来源时间戳；分钟线使用分钟字段；
  新闻使用发布时间；完成日线使用交易日 15:00。
- `observed_at`：本项目开始调用 provider 的时间。
- `available_at`：provider 返回且完成解析的真实时间。`CloseWindowCollector` 会再次
  保证该值不早于外层 provider 完成时间。
- `decision_cutoff`：当日 14:50。
- 无来源时间的快照型字段只能以本项目解析完成时间作为事件时间，并在字段说明中标明。
- 无逐条发布时间的新闻不得生成 news record。

14:50 后完成的 record 和 source status 只能进入拒绝审计，不能改变冻结快照。

## 5. 前复权证明

腾讯日线 provider 同时要求：

1. 请求参数为 `qfq`；
2. 返回对象存在 `qfqday`；
3. 每条记录写入 `adjustment=qfq`；
4. 每条记录写入
   `adjustment_evidence=request_param=qfq;response_key=qfqday`。

任一步缺失即抛出 `qfq_response_not_proven`，不得使用未复权 `day` 数据替代。当前
交易日的部分日线始终排除，只保留决策日前已完成日期。

## 6. 哈希

- 每个 record 的 `request_hash` 覆盖 URL 语义参数；
- `raw_hash` 为原始 HTTP 响应字节的 SHA-256，组合来源使用各响应哈希的稳定组合；
- `ProviderBatch.raw_hash` 审计整个 provider 批次；
- `ingest_hash` 保留原始采集顺序；
- `snapshot_hash` 只覆盖规范化后的有效 records、有效 source status、合同版本和
  决策时间。

原始采集哈希和决策快照哈希互不替代。

## 7. 失败、空数据与重试

- 默认连接/读取超时为 4/12 秒；
- 每个请求最多 2 次，指数退避从 0.4 秒开始；
- 同一主机最短请求间隔 0.25 秒；
- 429、断连、超时、JSON 错误和字段合同错误均不得回退到 demo；
- `ProviderBatch(records=[], data_types=["news"])` 表示来源成功但零新闻；
- provider 异常表示来源失败，`ProviderSpec` 仍保留预期数据类型和来源版本；
- 任一关键数据缺失时 `data_ready=false`，不生成评分、候选、票据或订单。

## 8. 运行入口

只验证真实来源，不写文件：

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_real_source_validation.py --codes 000001
```

在采集窗口使用真实 provider：

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_close_snapshot_collector.py --live --codes 000001
```

`--live --freeze` 被禁止。冻结必须使用已采集并经过审计的显式输入，避免在冻结时临时
联网并把截止后数据误写成当日有效数据。

## 9. 当前阻断

2026-07-30 的真实验证在盘后执行，不能证明来源在 14:50 前完成。东财 `push2`
曾成功返回市场宽度和 241 根分钟线（含 14:50），随后连续请求出现断连；板块和资金
接口也出现相同问题。

更重要的是，当前合同同时要求“存在完整的 14:50 事件分钟”和
`available_at <= 14:50:00`。对于需要在 14:50 分钟结束后才能形成的完整分钟 K 线，
这两个条件可能无法由真实 HTTP 来源同时满足。v0.4.1 不伪造提前可用时间，也不放宽
截止时间。正式影子验收前必须在真实交易时段测量来源事件语义，并由 PM 决定采用：

- 14:50 时点快照/逐笔累计值；或
- 完整 14:49 分钟 + 14:50 实时报价；或
- 将完整 14:50 分钟决策定义为 14:51 可用。

在该决定和真实 14:40-14:50 SLA 验证完成前，不得宣称数据已经就绪，也不得开始正式
60 日影子验收。
