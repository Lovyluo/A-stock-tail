# v0.4.1 真实数据源验证矩阵

验证设备：Windows，Asia/Shanghai。验证日期：2026-07-30。目标股票：`000001`。
验证发生在收盘后，因此“14:50 前完成”只能标记为未证明，不能从盘后响应速度倒推。
下列内容为脱敏摘要，不包含原始响应、密钥、持仓或运行缓存。

## 1. P0 矩阵

| 数据类型 | 主来源 / 备用来源 | 实际字段与单位 | 时间字段 | 版本与哈希 | 实测结果 | 14:50 结论 |
|---|---|---|---|---|---|---|
| 交易日历 | 腾讯 `sh000001` 日线日期集合 / 东财指数日线候选 | 交易日期，`date` | 最新已完成交易日 15:00；可用时间为解析完成 | `ifzq_fqkline_day_v2026-07-30`；记录 64 位 raw hash | 腾讯成功，生成 1 条日历合同；东财历史接口间歇断连 | 未证明 |
| 市场状态 | 腾讯上证/沪深300/创业板 + 东财上证/深证宽度 | 指数涨跌幅 `%`；上涨/下跌/平盘家数；宽度 `0-1` | 指数使用来源时间；宽度使用解析完成时间 | `push2_index_breadth_v2026-07-30` | 首次成功约 1.11 秒，上证宽度字段 `f104/f105/f106=897/1396/58`；随后断连 | 未证明，且无合格宽度备用 |
| 个股报价 | 腾讯 / mootdx 待验证 | 价格 `元/股`；成交额 `万元`；换手率 `%`；委买卖量为来源手数；停牌/涨跌停布尔值 | 腾讯字段 30 为事件时间 | `qt.gtimg.cn~88_fields_v2026-07-30`；成功样例 raw hash 前缀 `7759ba3860676ac6` | 成功，单次约 75ms | 未证明 |
| 14:50 分钟线 | 东财 `trends2` / mootdx 待验证 | OHLC `元/股`；成交量来源手数；成交额 `元`；VWAP `元/股` | 行中时间为 `event_time` | `push2_trends2_fields_v2026-07-30`；成功样例 hash 前缀 `0660b233372416c5` | 首次成功约 1.25 秒，241 条，09:30-15:00，明确包含 14:50；后续断连 | 存在 14:50 历史样例，但截止前可用性未证明 |
| 60 日前复权日线 | 腾讯 `qfqday` / 东财 `fqt=1` 候选 | OHLC `元/股`；成交量来源手数；`adjustment=qfq` | 完成日 15:00；排除决策当日 | `ifzq_fqkline_qfqday_v2026-07-30`；qfq 响应 hash 前缀 `a6ac47462482e969` | 腾讯成功；同标的 `qfqday` 与未复权 `day` 响应键和哈希均不同；东财间歇断连 | 历史数据可用，盘中 SLA 未证明 |
| 行业及强度 | 东财 F10 行业映射 + 东财板块快照 / 百度失败 | 行业名、板块涨跌 `%`、上涨/下跌家数、宽度 `0-1`、相对市场强度 `%` | 快照事件时间使用解析完成时间 | `emweb_core+push2_board_v2026-07-30` | 行业映射约 306ms 成功，识别“银行”；板块 `push2` 后续断连；旧串行实现曾耗时约 18.4 秒；百度返回 `ResultCode=10003` | 不满足 |
| 个股资金流 | 东财分钟资金 / 新浪当前资金 | 主力、大单等，单位 `元` | 东财使用分钟事件时间；新浪仅有本项目观察快照时间 | `push2_fflow_kline_v2026-07-30` / `sina_moneyflow_current_v2026-07-30` | 东财本轮连续断连；新浪成功约 1.08 秒，`000001` 有数值 | 备用可用，但东财分钟主源未满足 |
| 全球新闻 | 东财 7x24 | 标题、摘要、发布时间、URL | 来源发布时间 | `np_weblist_724_v2026-07-30` | 成功，约 235ms；正式 provider 返回 80 条带发布时间记录 | 未证明 |
| 个股新闻 | 东财搜索 | 代码、标题、摘要、发布时间、URL | 来源发布时间 | `search_api_cms_old_v2026-07-30` | 成功；正式 provider 返回 10 条 | 未证明 |
| 公告 | 巨潮 | 代码、公告标题、公告时间、附件 URL | `announcementTime` | `cninfo_query_v2026-07-30` | 成功，约 391ms；正式 provider 返回 10 条 | 未证明 |
| 财联社备用 | CLS 旧接口 / NewsNow CLS | 标题、URL | 必须有逐条发布时间 | `newsnow_cls_hot_v2026-07-30` | CLS `nodeapi/telegraphList` 返回 404；NewsNow 可连通但条目无逐条发布时间，合同失败 | 不可评分，只能失败审计 |

## 2. 成功状态示例

```text
tencent_quote: SUCCESS, records=1
tencent_trading_calendar: SUCCESS, records=1
tencent_qfq_daily: SUCCESS, records=60, qfq_proven=true
sina_fund_flow_backup: SUCCESS, records=1
eastmoney_global_news: SUCCESS, records=80
eastmoney_stock_news: SUCCESS, records=10
cninfo_announcements: SUCCESS, records=10
```

每个成功 record 均包含 `event_time`、`observed_at`、`available_at`、
`decision_cutoff`、`source`、`source_version`、`request_hash` 和 `raw_hash`。

## 3. 失败状态示例

```text
eastmoney_market: FAILED, push2 connection closed
eastmoney_minute_bar: FAILED, push2 connection closed
eastmoney_fund_flow: FAILED, push2 connection closed
eastmoney_industry: FAILED, board quote connection closed
newsnow_cls_audit: FAILED, newsnow_cls_items_missing_published_at
```

来源成功但合法新闻为零时记录为 `AVAILABLE_EMPTY`；网络失败或缺少逐条发布时间记录为
`FAILED`。两者不得互换。

## 4. 频率与重试

- 连接超时 4 秒，读取超时 12 秒；
- 最多 2 次请求，0.4 秒起指数退避；
- 同主机至少间隔 0.25 秒；
- 不无限重试，不在 14:50 后把成功状态回填为当日可用；
- 当前只验证单股票，尚未证明 50 股票候选池可在截止前完成。

## 5. 验证结论

真实 provider 接口和失败门禁已经具备，但 P0 数据合同尚未全部满足：

1. 东财 `push2` 在本机代理链路下间歇断连；
2. 行业宽度没有已验证备用源；
3. 财联社备用缺逐条发布时间；
4. 所有自动验证均发生在盘后，不能证明 14:50 SLA；
5. 完整 14:50 分钟 K 线与严格 `available_at <= 14:50:00` 可能存在定义冲突；
6. 仅完成单股票验证，未完成候选池规模压力测试。

因此当前不得生成策略评分或开始正式 60 日影子验收。
