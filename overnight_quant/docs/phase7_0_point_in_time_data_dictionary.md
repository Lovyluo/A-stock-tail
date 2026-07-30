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
