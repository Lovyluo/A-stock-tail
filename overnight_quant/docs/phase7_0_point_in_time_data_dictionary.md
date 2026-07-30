# v0.4 时点数据字典

## 1. 通用时点合同

每条可评分记录必须包含以下字段：

| 字段 | 含义 |
|---|---|
| `event_time` | 市场事件或数据事实发生时间 |
| `published_at` | 发布者公开发布时间；非新闻可为空字符串 |
| `observed_at` | 本项目首次观察到该记录的时间 |
| `available_at` | 数据完成解析并可供策略读取的时间 |
| `decision_cutoff` | 该记录允许参与的最晚决策时间 |
| `source` | 数据来源标识 |
| `source_version` | 来源适配器或协议版本 |
| `request_hash` | 请求参数的稳定哈希 |
| `raw_hash` | 原始响应或规范化载荷的稳定哈希 |
| `data_type` | `market`、`industry`、`quote`、`minute_bar`、`fund_flow`、`news` 等 |
| `payload` | 业务字段 |

统一过滤条件：

```text
event_time <= decision_time
observed_at <= decision_time
available_at <= decision_time
available_at >= observed_at
decision_time <= decision_cutoff
```

新闻额外要求：

```text
published_at is not empty
published_at <= decision_time
```

## 2. 冻结快照

| 字段 | 含义 |
|---|---|
| `status` | `FROZEN_1450` |
| `trade_date` | 交易日期 |
| `decision_time` | 固定为当日 14:50 |
| `records` | 通过时点合同的记录 |
| `rejected_records` | 被拒绝记录及原因 |
| `record_count` | 可用记录数 |
| `rejected_count` | 拒绝记录数 |
| `snapshot_hash` | 可用记录集合哈希 |

同一日期的不可变快照写入后不能以不同内容覆盖。

## 3. 策略特征

| 字段 | 含义 |
|---|---|
| `market_strength` | 指数强度与市场广度合成值 |
| `industry_relative_strength` | 行业相对市场强度 |
| `industry_breadth` | 行业上涨广度 |
| `stock_relative_to_industry_pct` | 个股相对行业涨跌幅 |
| `vwap` | 截止决策时点的累计 VWAP |
| `vwap_position_pct` | 当前价相对 VWAP 百分比 |
| `vwap_slope_pct` | 近 5 分钟累计 VWAP 斜率代理 |
| `tail_support_score` | 尾盘回踩承接得分 |
| `abnormal_volume_z` | 尾盘成交量相对历史分钟的标准分 |
| `extreme_order_imbalance` | 五档买卖量不平衡代理 |
| `catalyst_score` | 有来源、有发布时间的催化质量 |
| `negative_announcement_count` | 决策前可见负面公告数量 |
| `chip_avg_cost_20d` | 20 日量价成本代理 |
| `chip_avg_cost_60d` | 60 日量价成本代理 |
| `overhead_pressure_ratio` | 当前价上方成交分布压力代理 |
| `downside_support_ratio` | 当前价下方成交分布支撑代理 |
| `main_force_chip_proxy` | 量价与资金行为的综合代理 |

## 4. 门禁与评分输出

| 字段 | 含义 |
|---|---|
| `hard_gates.market` | 市场门禁 |
| `hard_gates.industry` | 行业门禁 |
| `hard_gates.liquidity` | 流动性门禁 |
| `hard_gates.news_risk` | 消息风险门禁 |
| `hard_gates.data_quality` | 时点与数据质量门禁 |
| `components` | 六项加权评分明细 |
| `total_score` | 0-100 的影子评分 |
| `decision_hash` | 输入、门禁和评分的稳定决策哈希 |
| `demo_field_count` | 正式模式演示字段数量，必须为 0 |

`total_score` 不能绕过硬门禁，也不能单独解释为确定性结论。

## 5. 事件成交字段

| 字段 | 含义 |
|---|---|
| `event_time` | 实际模拟成交分钟 |
| `quantity` | 成交数量，100 股整数倍 |
| `price` | 分钟成交参考价加滑点和冲击成本 |
| `slippage_bps` | 滑点基点 |
| `impact_bps` | 冲击成本基点 |
| `partial` | 是否部分成交 |
| `blocked_reason` | 停牌、涨跌停等受阻原因 |
| `blocked_days` | 无法退出累计天数 |

日线收盘价不得用来补充 14:51-14:55 的成交。

## 6. 完整性与就绪状态

`execution_ok` 只表示程序正常完成；`data_ready` 只在完整输入通过统一校验后为
`true`。存在任意一条合法记录不能推导数据已经就绪。

完整输入至少要求：

- 市场快照含指数强度与市场广度；
- 至少一只股票具备完整 quote、12 个不同事件分钟，且明确存在
  `event_time=14:50` 的分钟线；
- quote 含价格、昨收、成交额、换手率、停牌及涨跌停状态；
- 个股具备匹配的行业强度与行业广度；
- 筹码代理至少有 60 根已被可信交易日历确认的有效日线及可用资金流记录；
- 新闻源明确返回成功；成功但零条新闻记为 `AVAILABLE_EMPTY`，来源失败记为
  `FAILED`。

统一输出字段：

| 字段 | 含义 |
|---|---|
| `coverage_by_type` | market、industry、quote、minute_bar、daily_bar、fund_flow、news、trading_calendar 覆盖数量 |
| `readiness_errors` | 阻断 `data_ready` 的机器可解析错误列表 |
| `critical_source_status` | 各关键数据类型的来源状态、记录数与来源名称 |
| `stock_readiness` | 逐股完整性状态、分钟数、日线数及缺失原因 |

输入不完整时状态为 `POINT_IN_TIME_DATA_INCOMPLETE`，不得生成影子候选、票据或
订单。文件不存在或无法读取时使用 `POINT_IN_TIME_DATA_UNAVAILABLE`。

## 7. 来源状态合同与冻结哈希

`source_status` 与业务记录使用相同的时点合同，必须包含 `event_time`、
`observed_at`、`available_at`、`decision_cutoff`、`source`、
`source_version` 和 `raw_hash`。14:50 后才成功的来源状态不得改变当日就绪状态，
只能写入独立审计记录。

`snapshot_hash` 覆盖决策时间、合同版本、截至决策时点有效的 records 和有效的
source status。冻结后到达的来源状态不能改变已冻结快照及其哈希。

## 8. 60 日筹码代理日线合同

- 至少 60 个不同的已完成交易日期，按日期升序规范化；
- 重复日期采用确定性去重，并在记录规范化审计或 `daily_bar_audit` 中记录拒绝原因；
- 统一使用前复权数据，逐条记录 `adjustment=qfq`；
- 复权方式未知、未复权或混用时，筹码维度不可用；
- 默认只使用决策日前已经完成的日线，当日 14:50 部分日线不得冒充收盘日线；
- 每条日线必须具备有效的 `high`、`low`、`close` 和 `volume`；
- 59 个唯一交易日、60 条同日重复记录或任何决策后可见日线均不能通过完整性门禁。

筹码成本和主力资金字段始终是代理指标，不代表真实持仓成本或真实主力仓位。

## 9. PR #5 范围

PR #5 只提供研发框架，以及手工快照和回放快照入口，尚未接入真实自动采集器。
因此本阶段不得宣称已经可以开始正式 60 个交易日影子验收。真实时点采集器计划在
独立的 v0.4.1 阶段实现。

## 10. 分钟事件与记录确定性

分钟线完整性只按 `event_time` 判断，`available_at` 只表示系统何时可以使用记录。
14:49 分钟线即使在 14:50 到达，也不能代替 14:50 事件分钟。分钟线按
`event_time` 排序，同一股票同一事件分钟采用 `available_at`、`source` 和
`raw_hash` 确定性决胜，重复项进入审计。

所有有效 records 在物化前统一排序和去重：

- quote、market 和 industry 选择决策前最新事件；
- minute_bar 按股票代码和事件分钟唯一化；
- daily_bar 按股票代码和交易日期唯一化；
- fund_flow 按事件时间稳定升序；
- news 按发布时间、可用时间、来源和哈希稳定排序；
- source_status 按数据类型、来源和完成时间稳定排序。

相同有效记录集合的输入顺序不得改变 readiness、评分、`decision_hash` 或
`snapshot_hash`。原始调用顺序如需审计，只记录在独立 `ingest_hash`，不得进入
决策快照哈希。

## 11. 可信交易日历合同

60 日筹码日线必须同时提供 `trading_calendar` 时点记录。该记录应来自交易所日历
或基准指数交易日期集合，并具备 `calendar_kind`、`calendar_name`、
`trade_dates`、`source`、`source_version` 和 `raw_hash`。

只有日历明确列出的开市日期才可计入 60 日。周末、法定休市日和日历未知日期均
不得计数；最新日线不得晚于日历确认的决策日前最后一个已完成交易日。缺少可信
日历时，筹码维度和 `data_ready` 同时失败。日历记录属于有效 records，因此纳入
`snapshot_hash`。

## 12. Provider 完成时间

手工 provider 调用记录 `started_at` 和 `completed_at`。来源状态的
`available_at` 使用 provider 返回并完成解析后的真实完成时间；provider 返回记录
的系统 `available_at` 不得早于该完成时间。14:50 后完成的来源状态和记录只能进入
拒绝审计，不能进入当日冻结快照。

`CloseWindowCollector` 支持注入 clock 以进行确定性测试。该能力不代表已接入真实
联网自动采集器；真实采集器仍属于独立 v0.4.1 范围。
