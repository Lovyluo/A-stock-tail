# Phase 4.1.2 全市场盘后观察池引擎 Design Spec

## 目标

把当前“热点候选池驱动”的盘后观察池，升级为“00/60 全市场盘后观察池引擎”。

本阶段只解决盘后观察池入口与筛选层级错误，不改变以下边界：

- 不生成 manual ticket
- 不记录人工订单
- 不自动下单
- 不自动点击证券软件
- 不接入券商 API
- 不改动尾盘正式 live scan 的交易逻辑

## 当前问题

当前盘后观察池入口来自 `AStockClient.get_candidate_quotes()`，而这个入口本质上是：

1. 先取同花顺热点强势股；
2. 取不到时回退东财候选；
3. 最多截取前 30 只；
4. 再做盘后评分和 A/B/C 分类。

这个入口适合“热点强势候选复核”，不适合“全市场盘后观察池引擎”。

它会带来几个结构性偏差：

- 候选天然偏向涨停、接近涨停、题材高潮股；
- 很难符合“涨幅 3%–7% 更优、量价适中、可供次日观察”的原始目标；
- 漏掉不在热点池前排、但收盘形态优良的 00/60 股票；
- A/B/C 分类结果会被热点源排序强烈影响。

## 本阶段设计结论

采用方案一：

```text
00/60 全市场基础快照
→ 第一层基础过滤
→ 对入围股票补题材 / 资金 / 日线趋势细节
→ 盘后评分
→ A/B/C 分类
→ 输出次日早盘观察池
```

这是一个“先全市场基础扫描，再局部增强”的分层设计，而不是“全市场逐只补全全部字段”。

## Universe 定义

第一版全市场盘后观察池 universe 严格限定为：

- 仅 `00xxxx`
- 仅 `60xxxx`

明确排除：

- `30xxxx`
- `68xxxx`
- 北交所代码
- ETF
- 指数

目标是先把主板 / 中小板观察池做对，不在第一版扩大到创业板、科创板和 ETF。

## 新旧入口分离

### 保持不变

以下链路继续使用当前候选池逻辑，不在本阶段修改策略行为：

- `run_scan.py`
- `YangYongxingOvernightStrategy.scan()`
- `AStockClient.get_candidate_quotes()`

### 新增独立入口

盘后观察池新增独立入口，例如：

- `AStockClient.get_after_close_universe_quotes()`

或等价命名的只读接口。

这个接口只服务于：

- `AfterCloseAnalyzer`
- `run_after_close_analysis.py`

不得反向影响：

- 尾盘 live 扫描
- 干跑 dry-run
- 买入票据
- 卖出计划

## 分层扫描设计

### 第一层：全市场基础快照

目标：

- 拿到 00/60 股票的全市场基础 universe；
- 只依赖相对稳定、可批量获取的字段；
- 先做硬过滤，尽量减少后续增强成本。

基础字段至少包括：

- `code`
- `name`
- `price`
- `change_pct`
- `turnover_pct`
- `amount_wan`
- `vol_ratio`
- `high`
- `low`
- `limit_up`
- `limit_down`
- `is_limit_up`
- `is_st`
- `is_suspended`
- `float_mcap_yi`（若可稳定获取）

这一步的设计重点是“先广覆盖，再粗过滤”，而不是一开始就补题材和资金。

### 第二层：基础过滤

对第一层 universe 先做硬过滤，剔除明显不该进入观察池的股票。

第一版基础过滤规则：

- 代码前缀必须是 `00` / `60`
- 非 ST / `*ST`
- 非停牌
- 非新股（上市不足 60 日）
- 股价 `>= 3`
- 成交额 `>= 15000` 万
- 换手率 `>= 3`
- 涨幅在 `3% ~ 10%`
- 量比 `>= 1`
- 非涨停不可追
- 非明显尾盘跳水（若此时可先由日线近似或后置到增强层）

这一步的原则是：

> 只要在你的原策略里“原则上不该观察”，就尽量在这一层排掉。

### 第三层：增强字段补全

只对第二层过滤后的股票补齐增强字段。

增强字段包括：

- `theme_tags`
- `theme_rank`
- `same_theme_strong_count`
- `main_net`
- `big_order_net`
- `main_net_source`
- `fund_flow_source`
- `estimated_capital_flow`
- `daily_kline`
- `range_position`
- `tail_pullback_pct`
- `upper_shadow_ratio`
- `ma5 / ma10 / ma20`

这一层的原则是：

- 不对全市场逐只补全；
- 只对基础过滤后仍有观察价值的股票做增强；
- 速度与质量优先于“字段全量覆盖”。

## 评分设计

保留 `AfterCloseAnalyzer` 的独立评分模型，不复用 `YangYongxingOvernightStrategy.scan()`。

总分结构继续保留：

```text
total_score =
    market_score * 0.10
  + theme_score * 0.25
  + price_volume_score * 0.25
  + trend_score * 0.20
  + capital_score * 0.10
  + risk_score * 0.10
```

但评分对象将从“热点前 30”改为“全市场基础过滤后的入围股票”。

这意味着：

- 不改评分哲学；
- 只改候选入口和筛选顺序；
- 让评分真正作用于你原先想观察的股票类型。

## A/B/C 分类语义

### A / B 正式观察池

只有满足以下条件的股票才允许进入 A/B：

- 无硬排除
- 数据质量足够
- 安全字段可确认
- 分数达到阈值

### C 类风险观察 / 不建议追

C 类允许保留以下类型：

- 当天强势但风险明显偏大
- 已过热
- 涨停追高风险
- 量价不协调
- 资金明显流出
- 仅作为“提醒不要追”的观察对象

但 C 类的产品语义必须继续明确：

- 不是推荐
- 不是候选买入
- 只用于风险提示

## 数据质量与保守阻断

live 模式下，如果正式观察池所需候选出现以下关键问题，则继续阻断正式观察池：

- `quote_stale`
- `freshness_unknown`
- `safety_field_unknown`

如果 live 数据 fallback 到 demo：

- `candidate_source: demo_fallback`
- `valid_for_trading_observation: NO`
- 不输出正式 A/B 观察池

这条规则不改。

## 输出语义

### 正式 CSV

`next_morning_watchlist_YYYY-MM-DD.csv` 继续只输出：

- A 类
- B 类

### 报告

`after_close_analysis_YYYY-MM-DD.md` 继续输出：

- 市场环境
- 题材摘要
- A 类
- B 类
- C 类风险观察 / 不建议追
- 数据质量

## 预期行为变化

本阶段完成后，盘后观察池的结果应该从：

```text
热点前排 / 涨停强势股复核
```

转变为：

```text
00/60 全市场收盘后筛选出的次日观察池
```

预期改善：

- A/B 会明显减少“本来就不能追”的极端强势股；
- 更容易出现涨幅 3%–7%、成交额足、换手合理、收盘位置好、但不是涨停的候选；
- C 类仍可保留高风险强势股，但只作为“只看不碰”。

## 不做的内容

本阶段明确不做：

- 扩展到 `30xxxx`
- 扩展到 `68xxxx`
- 扩展到北交所
- ETF / 指数纳入观察池
- 新增交易功能
- 新增正式出票功能
- 新增 manual order 记录
- 新增自动交易 / 自动点击
- 改动尾盘 live scan 逻辑

## 涉及文件

预计主要涉及：

- `overnight_quant/data/astock_client.py`
- `overnight_quant/strategy/after_close_analysis.py`
- `overnight_quant/scripts/run_after_close_analysis.py`
- `overnight_quant/reports/after_close_report.py`
- `overnight_quant/tests/test_phase41_after_close_analysis.py`

可能需要新增：

- 针对全市场基础 universe 的数据辅助函数
- 针对 after-close 全市场筛选的专门测试夹具

## 测试方向

至少需要覆盖：

1. 盘后观察池不再依赖 `get_candidate_quotes()` 热点前排截断；
2. 仅 `00` / `60` 股票进入全市场盘后 universe；
3. ST / 新股 / 停牌 / 涨停追高风险不能进入 A/B；
4. 3%–7% 区间股票在相同条件下应优于极端涨停股；
5. C 类仍可保留风险强势股，但不进入正式 CSV；
6. fallback demo / stale / safety unknown 阻断逻辑保持成立；
7. 不影响现有 `run_scan.py`、`run_sell_plan.py`、`run_backtest.py`。

## 设计结论

本阶段不是“调参数”，而是“纠正盘后观察池入口层级错误”。

根本修正点只有一个：

> 盘后观察池必须从“热点候选池复核”改成“00/60 全市场基础扫描后再增强评分”。

只有这样，它才真正符合你要的：

- 全市场盘后观察池引擎
- 次日早盘观察用途
- 偏好 3%–7% 强势但未过热的股票
- 不把涨停/ST/明显不合规股票混进正式观察池
