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

## 9. 四阶段时间合同

v0.4.1 不再把市场事件、网络完成、决策和模拟成交压在同一个 14:50 时点：

1. `feature_event_cutoff=14:50:00`；
2. `collection_deadline` 限制所有网络响应完成和解析时间；
3. `decision_time` 在采集截止后生成冻结决策；
4. `execution_not_before` 限制事件回测和纯模拟的最早成交。

过滤器没有放宽 `available_at`，而是分别执行：

```text
event_time <= feature_event_cutoff
available_at <= collection_deadline
decision generated at decision_time
fill event_time >= execution_not_before
```

分钟标签待验证时使用保守时间轴：

```text
feature_event_cutoff  14:50:00
collection_deadline   14:51:05
decision_time         14:51:10
execution_not_before  14:52:00
```

但 `minute_label_semantics=unverified` 会使 `data_ready=false`。保守时间轴不是
供应商语义已经验证的声明。

## 10. 四时点采样

`run_minute_label_probe.py` 在真实交易日采样：

- 14:49:55；
- 14:50:05；
- 14:50:30；
- 14:51:05。

脚本比较同一批高流动性股票的 14:50 OHLCV 哈希。14:50 记录若在该分钟内变化并在
14:51 后稳定，且 14:49:55 尚不存在该行，按分钟开始标签处理。分钟结束标签必须在
14:49:55 已存在且四个时点保持稳定。14:49:55 不存在而后续不变、任一点迟到/失败、
逐股覆盖不足或变化不明确时均保持 `unverified`。脚本记录请求起止、耗时、逐股
存在性、原始响应哈希、来源版本、采样交易日和 `probe_evidence_hash`，但不写策略
快照，不产生任何候选、票据或订单。即使脚本输出 VERIFIED，也必须先人工复核，不能
直接启用验证开关。

## 11. 资金语义、并发和全局 deadline

- 东财分钟资金流：`is_proxy=false`、`eligible_for_hard_gate=true`；
- 新浪当前资金快照：`is_proxy=true`、`eligible_for_hard_gate=false`；
- 东财成功时不调用新浪；东财失败时只返回一份新浪 proxy，禁止重复计数；
- proxy 可进入审计和 Dashboard 说明，但不进入正式筹码评分或 `decision_hash`。

独立 provider 使用受控并发；同一主机仍由传输层按 0.25 秒间隔排队。全局 deadline
到达后不再启动新请求，transport 保持取消状态，provider 股票循环和重试循环均协作
退出。collector 等全部 worker 结束后才清除 deadline 和释放 executor，因此采集函数
返回后不会继续发请求、重试或修改指标。后到结果只进入审计，provider 结果统一按
来源和规范化记录排序，线程完成顺序不影响 readiness、`snapshot_hash` 或
`decision_hash`。

Provider 调度优先级固定为：

1. 尾盘正式行情：报价、分钟线、市场、行业、正式资金流；
2. 前置静态数据：交易日历、60 日前复权日线、行业映射；
3. 新闻和公告；
4. 审计及代理来源。

`tail_provider_map()` 只包含第一组时效敏感来源；`prewarm_provider_map()` 提供静态
数据、行业映射和历史新闻预热入口。调度器应尽量在 14:40 前完成预热，尾盘窗口优先
刷新第一组。预热缓存不能绕过最终来源时间、完整性和不可变快照校验。

压力输出使用 `provider_success_ratio`，并单独输出
`formal_coverage_by_type`、`formal_complete_stock_count`、
`formal_complete_stock_ratio`、`proxy_coverage_by_type`、
`not_started_provider_count` 和 `late_provider_count`。来源调用成功率不得替代正式
股票完整率。

## 12. 当前阻断

2026-07-30 的压力测试在盘后执行，因此不能证明 14:50 SLA。2026-07-31 已在真实
交易日完成一次四时点分钟标签采样，但 14:49:55 和 14:50:30 两个必需时点因上游
连接失败没有覆盖。14:50:05 和 14:51:05 均覆盖全部 5 只股票，且 14:50 OHLCV
哈希稳定；该证据仍不足以区分分钟开始或分钟结束标签，因此结论为
`MINUTE_LABEL_INCONCLUSIVE`，不启用验证开关。

东财 `push2` 行情、分钟、行业主源及 a-stock-data Skill 提供的行业列表备用端点在
本机代理链路上仍出现断连；行业备用源未通过真实连通验证。完成一组无缺失的四时点
采样、盘中 deadline 压测和行业宽度备用源连通验证前，不得宣称数据已就绪，也不得
开始正式 60 日影子验收。
