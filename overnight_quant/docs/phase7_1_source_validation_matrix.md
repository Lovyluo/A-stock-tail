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
| 行业及强度 | 东财 F10 行业映射 + 东财板块快照 / a-stock-data Skill 的东财行业列表候选 | 行业名或稳定代码、板块涨跌 `%`、上涨/下跌/平盘家数、宽度 `0-1`、相对市场强度 `%` | 来源事件时间和解析完成时间均必须存在 | `emweb_core+push2_board_v2026-07-30` / `push2_clist_m90_t2_v2026-07-30` | 行业映射约 306ms 成功，识别“银行”；板块主源后续断连；对 Skill 候选端点分别验证系统代理、禁用环境代理、HTTP 和不同 push2 主机，均连接失败 | 主备均未满足，保持 `industry` 未就绪 |
| 个股资金流 | 东财分钟资金 / 新浪当前资金审计代理 | 主力、大单等，单位 `元`；另含 `semantic_class`、`timestamp_quality`、`is_proxy`、`eligible_for_hard_gate`、`field_definition_version` | 东财使用分钟事件时间；新浪只有本项目观察和解析完成时间 | `push2_fflow_kline_v2026-07-30` / `sina_moneyflow_current_v2026-07-30` | 东财本轮连续断连；新浪可返回当前资金快照，但字段等价性和分钟语义未证明 | 新浪仅作 proxy，不满足正式 `fund_flow` 门禁 |
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
本轮新增结果还记录 `feature_event_cutoff`、`collection_deadline`、`decision_time`
和 `execution_not_before`。四个时间字段不得合并成同一个截止点。

## 3. 失败状态示例

```text
eastmoney_market: FAILED, push2 connection closed
eastmoney_minute_bar: FAILED, push2 connection closed
eastmoney_fund_flow: FAILED, push2 connection closed
eastmoney_industry: FAILED, board quote connection closed
newsnow_cls_audit: FAILED, newsnow_cls_items_missing_published_at
sina_fund_flow_backup: SUCCESS_PROXY, eligible_for_hard_gate=false
```

来源成功但合法新闻为零时记录为 `AVAILABLE_EMPTY`；网络失败或缺少逐条发布时间记录为
`FAILED`。两者不得互换。

## 4. 时间合同与分钟标签

当前使用的保守合同为：

| 字段 | 时间 | 含义 |
|---|---:|---|
| `feature_event_cutoff` | 14:50:00 | 特征允许使用的最晚市场事件时间 |
| `collection_deadline` | 14:51:05 | 网络结果完成并解析的最晚时间 |
| `decision_time` | 14:51:10 | 冻结并生成影子决策 |
| `execution_not_before` | 14:52:00 | 事件回测和纯模拟最早成交时间 |

必须在真实交易日分别于 14:49:55、14:50:05、14:50:30 和 14:51:05
采样同一批高流动性股票，比较来源标为 14:50 的 OHLCV。2026-07-30 本轮执行时已在
盘后，未伪造该实验结论，因此 `minute_label_semantics=unverified`，且完整性门禁保持
失败。保守合同只用于防止未来数据进入评分，不代表供应商标签语义已经获得证明。

## 5. 频率、并发与 deadline

- 连接超时 4 秒，读取超时 12 秒；
- 最多 2 次请求，0.4 秒起指数退避；
- 同主机至少间隔 0.25 秒；
- 独立来源受控并发，同主机继续限频；
- 全局 deadline 到达后停止启动新请求，未完成来源记录 `DEADLINE_EXCEEDED`；
- 截止后完成的数据只进入审计，不参与 readiness、评分或哈希；
- 并发结果按来源和规范化记录稳定排序；
- 不无限重试，不在 14:50 后把成功状态回填为当日可用；
- 1/10/30/50 股票测试均必须单独记录，盘后结果不得冒充 14:50 SLA。

## 6. 盘后压力测试

以下结果来自 2026-07-30 盘后、全局 deadline 为 8 秒的真实联网测试。它只验证调度、
超时和失败语义，`sla_claim=false`，不能证明交易日 14:50 前的服务水平。

| 股票数 | 总耗时 ms | provider P50/P95/最大 ms | 请求/重试/失败 | 截止前成功比例 | deadline 触发 |
|---:|---:|---:|---:|---:|---:|
| 1 | 3094 | 188 / 2360 / 2625 | 21 / 5 / 4 | 63.64% | 0 |
| 10 | 4969 | 1719 / 2508 / 2562 | 57 / 5 / 4 | 63.64% | 0 |
| 30 | 7969 | 1562 / 7047 / 7531 | 114 / 5 / 4 | 36.36% | 6 |
| 50 | 8000 | 1852 / 6081 / 6531 | 115 / 5 / 4 | 18.18% | 9 |

30 和 50 股票时，慢来源在全局 deadline 下被正确截断；所有规模均为
`data_ready=false`，且 candidates、tickets、orders 均为空。新浪成功时只计入
`fund_flow_proxy` 覆盖，不计入正式 `fund_flow` 覆盖。

## 7. 验证结论

真实 provider 接口和失败门禁已经具备，但 P0 数据合同尚未全部满足：

1. 东财 `push2` 在本机代理链路下间歇断连；
2. a-stock-data Skill 的行业列表候选端点在本机亦断连，行业宽度没有已验证备用源；
3. 财联社备用缺逐条发布时间；
4. 所有自动验证均发生在盘后，不能证明 14:50 SLA；
5. 14:50 分钟标签的开始/结束语义尚未完成四时点实测；
6. 30/50 股票在 8 秒全局 deadline 下存在明显覆盖不足。

因此当前不得生成策略评分或开始正式 60 日影子验收。
