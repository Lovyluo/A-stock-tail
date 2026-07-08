# overnight_quant 实盘使用手册与输出解读

> 本文基于解压后的 `a-stock-data/overnight_quant` 项目静态检查整理。项目定位是 A 股隔夜策略研究与人工执行辅助系统，不是自动交易系统。

## 1. 项目定位

`overnight_quant` 是一个独立的 A 股隔夜交易辅助模块，核心策略名为 `yang_yongxing_overnight_v1`。它完成以下工作：

- 盘前/盘中数据源预检。
- 尾盘 live dry-run 或正式扫描。
- 在严格风控通过时生成人工买入票据。
- 人工记录 BUY / SELL 成交。
- 次日生成卖出计划。
- 生成单笔交易复盘。
- 盘后生成次日早盘观察池。
- 做样例回测、本地 daily_proxy 回测、受控真实历史数据准备。

它不会：

- 自动下单。
- 自动点击证券软件。
- 调用券商交易 API。
- 自动记录成交。
- 承诺收益。

## 2. 目录结构重点

```text
overnight_quant/
  config.yaml                         # 策略、风控、成本、路径配置
  data/                               # 行情数据与交易日/时段判断
  strategy/                           # 策略扫描、评分、盘后分析
  risk/                               # 风控 gate
  execution/                          # 人工成交记录、持仓状态、状态隔离
  reports/                            # 报告生成器与 live 实际报告目录
  records/                            # live 实际记录目录
  examples/                           # demo 示例记录和报告
  scripts/                            # 所有命令入口
  backtest/                           # 回测与历史数据准备
  backtest_data/                      # 历史数据 raw/processed/cache/manifests，运行产物忽略
  backtest_outputs/                   # 回测输出，运行产物忽略
```

## 3. 实盘前一次性检查

在项目根目录运行：

```bash
python -m pytest overnight_quant/tests -q
```

当前项目验收记录为 207 passed。实盘前如果测试不通过，不应继续使用 live 流程。

检查真实状态是否干净：

```bash
python overnight_quant/scripts/run_reset_state.py --dry-run
```

只在确认要清理真实运行产物时使用：

```bash
python overnight_quant/scripts/run_reset_state.py --yes
```

`run_reset_state.py` 只清理 real `records/` 和 `reports/` 的运行产物，不会删除源码、测试、配置和 examples。

## 4. 每个交易日推荐流程

### 4.1 盘前/盘中预检

```bash
python overnight_quant/scripts/run_preflight.py
```

用途：检查今天是否交易日、当前 session、配置和目录是否可写、主要数据源是否可用。

常见输出：

```text
OUTSIDE_TAIL_SESSION
session_state: PRE_MARKET
Preflight Report: overnight_quant\reports\preflight_YYYY-MM-DD.md
```

解释：

- `READY_FOR_LIVE_SCAN`：交易日、尾盘窗口、数据源基本可用。
- `OUTSIDE_TAIL_SESSION`：不是尾盘窗口，不能正式出票。
- `NON_TRADING_DAY`：非交易日，不能交易。
- `DATA_SOURCE_DEGRADED`：数据源有失败，继续观察但谨慎。
- `CONFIG_ERROR`：配置或目录权限异常，需要先修。

### 4.2 盘中/尾盘 dry-run

盘中任意时间可运行演练：

```bash
python overnight_quant/scripts/run_scan.py --mode live --dry-run
```

尾盘 14:25–14:55 再运行：

```bash
python overnight_quant/scripts/run_preflight.py
python overnight_quant/scripts/run_scan.py --mode live --dry-run
```

注意：dry-run 永远不应生成正式人工买入票据。

重点看控制台：

```text
Mode: live
Dry Run: YES
Market Gate: FAIL/PASS
Candidate Count: 30
Candidate Source: live/demo_fallback
Final Advice: DRY_RUN_ONLY
Signals CSV: ...
Live Data Quality Report: ...
Dry Run Report: ...
```

解读：

- `Candidate Source: live`：候选来自真实 live 数据。
- `Candidate Source: demo_fallback`：live 数据失败，使用 demo 演练候选，不能当真实观察。
- `Final Advice: DRY_RUN_ONLY`：只是演练，不是交易建议。
- `ticket_generated: NO`：dry-run 下正确行为。

### 4.3 尾盘正式扫描，仅在经过 dry-run 观察稳定后使用

正式命令：

```bash
python overnight_quant/scripts/run_scan.py --mode live
```

只应在 14:25–14:55 使用。不要随便加 `--allow-outside-session`。正式扫描如果所有风控通过，可能生成人工买入票据；如果没有票据，则不交易。

### 4.4 人工买入

如果生成 `manual_order_ticket_YYYY-MM-DD_CODE.md`，人工核对：

- 股票代码和名称。
- 建议价格。
- 最高可接受价。
- 建议数量。
- 止损价。
- 风险等级。

然后手动打开证券软件下单。系统不会下单。

记录人工 BUY：

```bash
python overnight_quant/scripts/run_record_order.py \
  --code 300001 \
  --price 18.50 \
  --qty 200 \
  --side BUY \
  --trade-time "2026-05-25 14:52:00"
```

### 4.5 次日卖出计划

次日 09:25 后：

```bash
python overnight_quant/scripts/run_sell_plan.py --mode live
```

如果无持仓，输出 `NO_OPEN_POSITION`。如果有持仓，生成 `sell_plan_YYYY-MM-DD.md`。

人工卖出后记录 SELL：

```bash
python overnight_quant/scripts/run_record_order.py \
  --code 300001 \
  --price 19.02 \
  --qty 200 \
  --side SELL \
  --trade-time "2026-05-26 09:45:00"
```

生成交易复盘：

```bash
python overnight_quant/scripts/run_trade_review.py --code 300001
```

### 4.6 盘后生成次日观察池

收盘后运行：

```bash
python overnight_quant/scripts/run_after_close_analysis.py --mode live
```

如果未收盘，会输出 `NOT_AFTER_CLOSE`，并生成空表头 CSV。只有收盘后且数据可靠，才可能生成 A/B/C 观察池。

## 5. 主要输出文件解读

### 5.1 `preflight_YYYY-MM-DD.md`

用途：实盘扫描前的系统体检。

字段：

- `status`：预检状态。
- `trade_date`：交易日期。
- `run_time`：运行时间。
- `session_state`：当前交易时段，例如 `PRE_MARKET`、`TAIL_SESSION`、`AFTER_CLOSE`。
- `is_trade_day`：是否交易日。
- `config_ok`：配置文件是否可读。
- `records_writable` / `reports_writable`：真实记录/报告目录是否可写。
- `Data Sources`：各数据源可用性。

实盘判断：

- 只有 `READY_FOR_LIVE_SCAN` 才接近正式尾盘扫描条件。
- `OUTSIDE_TAIL_SESSION` 表示不能出票。
- 数据源 FAIL 越多，越不应实盘。

### 5.2 `signals.csv`

用途：每次扫描后保存候选股评分和拒绝原因。

主要字段：

- `time`：扫描时间。
- `code/name`：股票代码与名称。
- `decision`：`BUY_CANDIDATE` 或 `REJECT`。
- `total_score`：综合评分。
- `price/change_pct/vol_ratio/turnover_pct/amount_wan/float_mcap_yi`：量价与市值字段。
- `theme_tags`：题材标签。
- `risk_flags`：风险标记。
- `main_net_source`：资金字段来源。
- `capital_score_source`：资金评分来源。
- `estimated_capital_flow`：资金是否为估算。
- `reasons`：筛选、评分和风控理由。

解读：

- `REJECT` 不代表股票一定差，只表示不满足本策略/当前风控。
- `estimated_capital_flow=True` 时资金只能辅助参考，不能当“主力确认”。
- `limit_up_unavailable`、`quote_stale`、`safety_field_unknown` 是需要高度重视的安全类原因。

### 5.3 `live_data_quality_YYYY-MM-DD.md`

用途：解释 live 数据是否可靠。

重点字段：

- `Fallback to demo`：是否使用 demo 候选兜底。
- `Source Status`：每个数据源成功/失败。
- `Candidate Counts`：raw、normalized、dropped、scored 数量。
- `Freshness`：fresh/stale/unknown 数量。
- `Field Coverage Improvement`：上市日期、新股、资金字段等覆盖率。
- `Warnings`：超时、502、fallback、stale 等警告。

实盘判断：

- `Fallback to demo: YES`：不能实盘，候选不是 live 真实候选。
- `stale` 很多：行情过期，不能出票。
- `safety field unknown count > 0`：安全字段不确定，必须保守。

### 5.4 `dry_run_scan_YYYY-MM-DD.md`

用途：dry-run 扫描报告，不生成票据。

字段：

- `DRY RUN ONLY`：演练声明。
- `session_state`：运行时段。
- `market_gate`：市场门禁是否通过。
- `candidate_source`：`live` 或 `demo_fallback`。
- `live_candidate_count` / `demo_candidate_count`：真实候选/演练候选数量。
- `valid_for_trading_observation`：是否可作为实盘观察。
- `reject_reason_top_list`：主要拒绝原因排行。
- `ticket_generated`：dry-run 应为 NO。

解读：

- `candidate_source=demo_fallback` 且 `demo_candidate_count>0`：只是演练，不可交易。
- `valid_for_trading_observation=NO`：不能用于实盘观察，通常因为非尾盘、stale、fallback 等。

### 5.5 `live_scan_summary_YYYY-MM-DD.md`

用途：正式 live 扫描摘要。

解读类似 dry-run，但 `final_advice` 可能是：

- `BUY`：生成了人工票据。
- `NO_TRADE`：无交易。

即使 `BUY`，也只是人工票据，不是自动下单。

### 5.6 `manual_order_ticket_YYYY-MM-DD_CODE.md`

用途：人工买入票据。

字段：

- `Strategy`：策略名。
- `Direction`：方向，通常 BUY。
- `Suggested Price`：建议参考价。
- `Max Acceptable Price`：最高可接受价，超过不应追。
- `Suggested Amount`：建议金额。
- `Suggested Quantity`：建议数量，按整手计算。
- `Stop Loss`：止损价。
- `Next-Day Plan`：次日卖出思路。
- `Risk Level`：风险等级。
- `Total Score`：策略综合分。
- `Confirmations`：人工确认清单。

使用规则：

- 票据不是订单。
- 只有人工确认后才可手动下单。
- 不得高于 `Max Acceptable Price` 追高。

### 5.7 `manual_orders.csv`

用途：记录你实际手动成交的 BUY/SELL。

常见字段：

- `order_id`：成交记录 ID。
- `ticket_id`：关联票据。
- `trade_date/trade_time`：成交日期/时间。
- `code/name`：股票。
- `side`：BUY 或 SELL。
- `price/qty/amount`：成交价、数量、金额。
- `max_acceptable_price/stop_loss_price`：买入票据约束。
- `status`：记录状态。

校验规则：

- BUY 价格不能高于票据最高可接受价。
- 数量必须为正，A 股默认整百股。
- SELL 必须有对应未平仓 BUY。
- SELL 数量不能超过持仓。

### 5.8 `manual_order_record_YYYY-MM-DD_CODE_SIDE.md`

用途：每次人工成交记录的结果报告。

字段：

- `status: RECORDED`：记录成功。
- `reasons: PASS`：校验通过。
- 如果失败，会写明拒绝原因。

### 5.9 `sell_plan_YYYY-MM-DD.md`

用途：次日卖出计划。

字段：

- `status`：`SELL_PLAN_READY` 或 `NO_OPEN_POSITION`。
- `Buy Price`：买入价。
- `Current Price`：当前价。
- `PnL Percent`：浮动盈亏。
- `Action`：建议动作。
- `Level`：动作等级。
- `Reason`：动作理由。
- `Stop Loss`：止损价。
- `Force Exit Before`：强制卖出参考时间。
- `Past Force Exit Time`：是否已过强制卖出时间。

Action 解读：

- `SELL_NOW`：计划上倾向立即卖出。
- `WAIT_10_MIN`：先观察 10 分钟承接。
- `TAKE_PROFIT`：达到止盈逻辑。
- `STOP_LOSS`：触发止损。
- `LIMIT_UP_WATCH`：涨停观察。
- `LIMIT_DOWN_RISK`：跌停/流动性风险。

### 5.10 `trade_lifecycle_YYYY-MM-DD.md`

用途：记录交易生命周期。

状态：

- `TICKET_ONLY`：只有票据，未记录 BUY。
- `BOUGHT_OPEN`：已买入，未卖出。
- `SELL_PLAN_READY`：卖出计划已生成。
- `CLOSED`：已卖出并平仓。
- `ERROR`：状态异常。

字段：

- 买入票据路径。
- 是否已记录 BUY。
- 买入价、数量、金额。
- 当前持仓数。
- 卖出计划路径。
- 是否已记录 SELL。
- 已实现盈亏。
- 收益率。
- 复盘报告路径。

### 5.11 `trade_review_YYYY-MM-DD_CODE.md`

用途：单笔交易复盘。

内容：

- 买入计划。
- 实际 BUY。
- 卖出计划。
- 实际 SELL。
- 毛利润、手续费、印花税、净利润、收益率。
- 执行纪律。
- 结论。

结论解读：

- `EXECUTION_OK`：执行基本符合计划。
- `BUY_VIOLATION`：买入违反票据约束，如追高。
- `SELL_VIOLATION`：卖出未按计划处理。
- `STOP_LOSS_VIOLATION`：止损纪律问题。
- `INCOMPLETE_TRADE`：交易未闭环。

### 5.12 `after_close_analysis_YYYY-MM-DD.md`

用途：盘后生成明日早盘观察计划。

关键字段：

- `status`：盘后状态。
- `session_state`：运行时段。
- `candidate_source`：候选来源。
- `valid_for_trading_observation`：是否可作为正式观察池。
- `final_view`：总结观点。
- `next_trade_date_calendar: weekday_proxy`：下一交易日用工作日近似，不含节假日校准。

状态解读：

- `DEMO_ANALYSIS`：demo 演示。
- `NOT_TRADING_DAY`：非交易日，空观察池。
- `NOT_AFTER_CLOSE`：未收盘，空观察池。
- `DATA_FALLBACK_DEMO`：live 失败 fallback demo，空观察池。
- `DATA_QUALITY_BLOCKED`：关键数据质量阻断，空观察池。
- `WATCHLIST_READY`：正式观察池已生成。
- `NO_WATCHLIST`：数据可用但无合格观察对象。

A/B/C：

- A：重点观察，分数高且无硬排除。
- B：备选观察。
- C：风险观察/只看不追。

### 5.13 `next_morning_watchlist_YYYY-MM-DD.csv`

用途：次日早盘观察池表格。

字段：

- `trade_date`：盘后分析日期。
- `next_trade_date`：预计下一交易日。
- `code/name`：股票。
- `category`：A/B/C。
- `score`：盘后观察分。
- `theme_tags`：题材。
- `close_price/change_pct/turnover_pct/amount_wan/vol_ratio`：收盘量价状态。
- `main_net/main_net_source/estimated_capital_flow`：资金字段及来源。
- `reason`：入选/风险原因。
- `risk_flags`：风险标记。
- `tomorrow_watch_plan`：明日观察条件。
- `invalid_conditions`：失效条件。
- `data_quality_flags`：数据质量标记。

使用规则：

- A/B 是明日早盘优先观察，不是买入指令。
- C 是只看不碰/风险复盘。
- 如果 CSV 只有表头，说明当天没有正式观察池。

## 6. 回测与历史数据研究

### sample_exact

```bash
python overnight_quant/scripts/run_backtest.py --dataset sample --fidelity sample_exact
```

作用：验证回测引擎逻辑，不证明策略有效。

### local daily_proxy

```bash
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed
```

作用：用本地 CSV 做日线代理研究。`daily_proxy` 不等于严格历史验证。

### 准备 daily_proxy 数据

sample：

```bash
python overnight_quant/scripts/prepare_backtest_data.py --source sample --codes 300201 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
```

positive sample：

```bash
python overnight_quant/scripts/prepare_backtest_data.py --source sample --sample-profile positive --codes 300201 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
```

local raw：

```bash
python overnight_quant/scripts/prepare_backtest_data.py --source local-raw --codes-file overnight_quant/backtest_data/raw/codes.txt --raw-dir overnight_quant/backtest_data/raw --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
```

真实历史数据准备，minimal：

```bash
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --codes 600519 --start 2025-01-02 --end 2025-01-10 --out-dir overnight_quant/backtest_data/processed --max-codes 1 --sleep 0.5 --overwrite
```

真实历史数据准备，expanded：

```bash
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --real-request-scope expanded --codes 600519,300750,510300 --start 2025-01-02 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --max-codes 3 --sleep 0.5 --overwrite
```

注意：真实历史准备结果仍是 `DAILY_PROXY`，不是 `strict_historical`。

## 7. 实盘使用纪律

建议连续 5–10 个交易日只 dry-run，不实盘。观察稳定后才可考虑 1000–3000 元小资金人工验证一次。

绝对不要：

- 因为 dry-run 出现高分就直接买。
- 在非尾盘使用正式 live scan 出票。
- 使用 `--allow-outside-session` 做实盘依据。
- 在 `Fallback to demo: YES` 时交易。
- 在行情 `quote_stale` 或 `freshness_unknown` 严重时交易。
- 让系统替你自动下单。

## 8. 最推荐的实盘日流程清单

1. 盘前：`run_preflight.py`
2. 盘前：`run_scan.py --mode live --dry-run`
3. 尾盘：`run_preflight.py`
4. 尾盘：`run_scan.py --mode live --dry-run`
5. 连续观察稳定后，尾盘才考虑：`run_scan.py --mode live`
6. 如果有票据，人工核对并手动下单。
7. 成交后：`run_record_order.py --side BUY ...`
8. 次日 09:25 后：`run_sell_plan.py --mode live`
9. 人工卖出后：`run_record_order.py --side SELL ...`
10. 复盘：`run_trade_review.py --code ...`
11. 收盘后：`run_after_close_analysis.py --mode live`

