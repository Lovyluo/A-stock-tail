# Phase 4.1 After-Close Analysis And Next-Morning Watchlist Design

## Status And Purpose

This specification defines a new read-only research flow:

```text
after-close market/candidate data
  -> conservative session and data-quality gate
  -> A/B/C observation classification
  -> next-morning watch conditions
  -> Markdown analysis report + watchlist CSV
```

The feature generates an observation plan only. It does not issue a buy
recommendation, create a manual order ticket, record an order, or interact
with brokerage software.

The intended commands are:

```text
python overnight_quant/scripts/run_after_close_analysis.py --mode demo
python overnight_quant/scripts/run_after_close_analysis.py --mode live
```

## Non-Goals And Prohibitions

Phase 4.1 does not implement or modify:

- `run_scan.py`, `run_sell_plan.py`, `run_record_order.py`,
  `run_trade_review.py`, or `run_backtest.py`;
- manual tickets, order recording, position tracking, or trade execution;
- automatic orders, automatic clicks, broker API integration, or a GUI;
- a historical backtest, machine-learning model, or parameter optimization;
- use of demo-fallback candidates as a formal live observation pool.

The report must state that it is an observation aid only, is not investment
advice, and does not execute trades.

## Selected Architecture

### Decision

Implement an independent after-close pipeline:

```text
run_after_close_analysis.py
  -> config_for_mode(mode)
  -> AStockClient(mode)
  -> AfterCloseAnalyzer
  -> after_close_report writers
```

Proposed additions:

```text
overnight_quant/strategy/after_close_analysis.py
overnight_quant/reports/after_close_report.py
overnight_quant/scripts/run_after_close_analysis.py
overnight_quant/tests/test_phase41_after_close_analysis.py
```

Optional configuration additions:

```yaml
after_close:
  max_a_count: 5
  max_b_count: 10
  max_c_count: 10
  min_a_score: 80
  min_b_score: 70
  min_c_score: 60
```

### Isolation From The Trading Workflow

`AfterCloseAnalyzer` must not call
`YangYongxingOvernightStrategy.scan()`. The scan workflow is a tail-session
buy-ticket flow, while after-close analysis is a next-morning observation
flow. Sharing its transaction-oriented control path would conflate two
different safety decisions.

The new pipeline must not import or call:

```text
build_manual_ticket
save_manual_ticket
order_recorder
position_tracker
```

It may reuse read-only data and representation capabilities:

- `AStockClient.get_market_snapshot()`;
- `AStockClient.get_candidate_quotes()`;
- `AStockClient.get_daily_kline()`;
- `LiveDataQualityReport` and existing source/freshness metadata;
- `config_for_mode()` for output path isolation;
- existing scoring concepts where they are recalculated under the distinct
  after-close weight formula.

## Output Path Isolation

Mode controls output state through existing `config_for_mode()` behavior:

| Mode | Reports Directory | Records Directory | Validity Label |
| --- | --- | --- | --- |
| `demo` | `overnight_quant/examples/reports/` | `overnight_quant/examples/records/` | `DEMO_ONLY` |
| `live` | `overnight_quant/reports/` | `overnight_quant/records/` | `YES` or `NO` according to gates |

Files:

```text
after_close_analysis_YYYY-MM-DD.md
next_morning_watchlist_YYYY-MM-DD.csv
```

Demo analysis must never write real `records/` or `reports/`. Live analysis
must never read example output or demo records as formal observation state.

## Session And Validity Gate

The analyzer performs its own after-close session decision using
`market_calendar.get_session_state()` and
`is_likely_cn_trade_day()`. It must not apply the scan flow's tail-session
permission to this workflow: `AFTER_CLOSE` is the valid session for live
after-close analysis.

| Condition | Status | `valid_for_trading_observation` | Watchlist CSV |
| --- | --- | --- | --- |
| `mode=demo` | `DEMO_ANALYSIS` | `DEMO_ONLY` | May contain demonstration A/B/C rows |
| Live non-trading day | `NOT_TRADING_DAY` | `NO` | Header only |
| Live trade day before `AFTER_CLOSE` | `NOT_AFTER_CLOSE` | `NO` | Header only |
| Live after close, candidate source falls back to demo | `DATA_FALLBACK_DEMO` | `NO` | Header only |
| Live after close, critical freshness/safety quality blocks analysis | `DATA_QUALITY_BLOCKED` | `NO` | Header only |
| Live after close, reliable data and classified rows exist | `WATCHLIST_READY` | `YES` | A/B/C rows |
| Live after close, reliable data but no qualified rows | `NO_WATCHLIST` | `YES` | Header only |

For `NOT_AFTER_CLOSE`, the report includes the actual session state:

```text
PRE_MARKET
CALL_AUCTION
MORNING_SESSION
LUNCH_BREAK
AFTERNOON_SESSION
TAIL_SESSION
```

For live fallback:

```text
candidate_source: demo_fallback
valid_for_trading_observation: NO
```

Fallback data may be described as a rehearsal/data-quality result in the
Markdown report but must not appear as a formal live A/B/C CSV row.

## Data Inputs And Quality Semantics

### Market View

Use available index and market snapshot fields to summarize:

- index changes and market state;
- tail stability when available;
- hot-theme count and market risk;
- northbound flow when available.

Missing optional market narrative fields are reported rather than invented.
Leading-theme tables are derived from available candidate `theme_tags` and
their strong-stock counts. Industry ranking is shown only when an existing
data source supplies it; otherwise the report states
`industry_rank_unavailable` rather than manufacturing a ranking.

### Candidate View

For each candidate, consume available read-only fields:

```text
code, name, price/close_price, change_pct, turnover_pct, amount_wan,
vol_ratio, float_mcap_yi, limit_up, limit_down, is_limit_up,
is_st, is_suspended, is_new_stock, is_bj_stock,
theme_tags, theme_rank, same_theme_strong_count,
main_net, big_order_net, fund_flow_source,
tail_pullback_pct, range_position, upper_shadow_ratio
```

Daily K-line data is used only for observation scoring and technical
description, such as MA alignment. It does not produce an order action.

### Data Quality Flags

Each candidate can retain the following flags:

```text
theme_missing
capital_missing
estimated_capital_flow
freshness_unknown
quote_stale
safety_field_unknown
```

If `main_net` is estimated, reports and CSV must identify:

```text
main_net_source
estimated_capital_flow: true
```

The report language must say the capital item is estimated and is used only
as an auxiliary observation score, not as confirmed capital inflow.

Critical live quality blockers for a formal watchlist are:

```text
candidate_source == demo_fallback
quote_stale
freshness_unknown
safety_field_unknown on otherwise selectable A/B candidates
```

## Observation Filtering And Classification

### Basic Candidate Requirements

The first pass favors candidates with:

```text
change_pct >= 3 and change_pct <= 10
amount_wan >= 15000
turnover_pct >= 3
vol_ratio >= 1
price >= 3
```

Preferred observation ranges are:

```text
change_pct: 3% to 7%
turnover_pct: 5% to 15%
vol_ratio: >= 1.2
amount_wan: >= 20000
range_position: close near the intraday high
```

### Hard Exclusions From A/B

The following conditions cannot enter A or B:

```text
st_stock
suspended
bj_stock
new_stock
price_below_min
amount_below_min
tail_pullback_too_large
quote_stale
freshness_unknown
safety_field_unknown
```

When useful for risk education, strong-looking rows with a known risk, such
as a limit-up chase risk or capital outflow, may appear in C only. Suspended,
ST, new-stock, or safety-unknown rows may be either omitted or shown in C
solely as excluded-risk examples, never with an actionable watch plan.

### Observation Score

The after-close score is separate from the overnight buy-ticket score:

```text
total_score =
    market_score * 0.10
  + theme_score * 0.25
  + price_volume_score * 0.25
  + trend_score * 0.20
  + capital_score * 0.10
  + risk_score * 0.10
```

It may reuse the meaning of existing sub-scores but must calculate the new
weighting within `after_close_analysis.py`. It must not alter the formula or
thresholds used by `run_scan.py`.

### Category Limits

| Category | Rule | Maximum Rows | Semantics |
| --- | --- | ---: | --- |
| A | `total_score >= 80`, no A/B hard exclusion | 5 | Priority observation |
| B | `70 <= total_score < 80`, no A/B hard exclusion | 10 | Backup observation |
| C | `60 <= total_score < 70`, or clearly explainable chase/risk observation | 10 | Watch only; do not chase |
| Excluded | score below 60 or not meaningful for review | none | Omitted or summarized in rejections |

C category is explicitly not a buy shortlist. The report section title must
state `C Class Risk Observation / Do Not Chase`.

## Next-Morning Watch Plan Templates

Only A and B rows receive detailed next-morning observation conditions.
Templates are selected deterministically using existing candidate features.

### Strong Trend

Applies to orderly, non-limit-up leadership or high-scoring trend candidates.

```text
Watch:
- opening gap should not exceed +5%;
- first 10 minutes should hold the prior close;
- volume should remain supportive;
- related theme leaders should remain firm.

Invalid:
- opens below -3%;
- breaks the prior close and cannot recover;
- theme leaders weaken materially.
```

### Limit-Up Or Near-Limit Risk

Applies as risk observation, normally C only.

```text
Watch:
- do not chase a one-price limit-up opening;
- observe acceptance only after a tradable open with support.

Invalid:
- high-open reversal;
- fast high-volume selloff;
- severe theme divergence.
```

### Volume Breakout

```text
Watch:
- opening should not immediately break the breakout level;
- first five minutes should retain price/volume alignment;
- pullback should hold the prior breakout area.

Invalid:
- low-open decline;
- falls back into the former range;
- turnover contracts materially.
```

### Theme Follower

```text
Watch:
- observe only while the theme leader remains strong;
- the follower must not weaken before the leader.

Invalid:
- theme leader opens weak or deteriorates;
- theme breadth contracts sharply;
- high-open reversal in the candidate.
```

C rows contain `reason` and `invalid_conditions`, but no instruction that
could be mistaken for a buy trigger.

## Report Format

`after_close_analysis_YYYY-MM-DD.md` includes:

```text
# After-Close Analysis Report

date:
mode:
status:
session_state:
candidate_source:
valid_for_trading_observation:
final_view:

## 1. Market Environment
## 2. Leading Themes
## 3. A Class Priority Observation
## 4. B Class Backup Observation
## 5. C Class Risk Observation / Do Not Chase
## 6. Next-Morning Overall Playbook
## 7. Data Quality
## Risk Warning
```

When the session or data-quality gate fails, sections 3 and 4 explicitly
state that no formal observation rows were generated and explain why.

The risk warning is mandatory:

```text
This report is for observation planning only, is not investment advice,
and does not place or automate orders.
```

## CSV Contract

`next_morning_watchlist_YYYY-MM-DD.csv` uses this fixed header:

```text
trade_date
next_trade_date
code
name
category
score
theme_tags
close_price
change_pct
turnover_pct
amount_wan
vol_ratio
main_net
main_net_source
estimated_capital_flow
reason
risk_flags
tomorrow_watch_plan
invalid_conditions
data_quality_flags
```

`NOT_TRADING_DAY`, `NOT_AFTER_CLOSE`, `DATA_FALLBACK_DEMO`, and
`DATA_QUALITY_BLOCKED` write this header with no stock rows. Demo mode may
write deterministic example rows under `overnight_quant/examples/`.

For the first version, `next_trade_date` uses the next likely China trading
weekday available through the existing weekday-based market calendar. The
report must disclose this as `next_trade_date_calendar: weekday_proxy` until
a holiday-aware calendar is implemented.

## Error Handling And Observability

- A source failure is recorded in the report data-quality section.
- A live candidate fallback is never silently treated as a live watchlist.
- Missing enhancement fields lower or flag a candidate; they do not crash the
  process.
- Missing safety/freshness certainty prevents a formal live A/B list.
- Writing report/CSV should fail clearly if its configured output directory
  cannot be created or written.

## Test Plan

The implementation phase must add tests that demonstrate:

1. Demo mode writes the report and watchlist beneath `examples/` and labels
   validity `DEMO_ONLY`.
2. Live non-trading-day analysis writes a report and header-only CSV with
   `NOT_TRADING_DAY`.
3. Live pre-close analysis writes a report and header-only CSV with
   `NOT_AFTER_CLOSE` and the concrete session state.
4. Live candidate fallback marks `candidate_source: demo_fallback`,
   `valid_for_trading_observation: NO`, and writes no formal CSV rows.
5. Live after-close reliable data can produce A/B/C categories within the
   configured caps.
6. A/B hard exclusion conditions prevent a candidate from entering A/B.
7. A/B candidates receive deterministic watch plans and invalid conditions.
8. C rows are labeled as risk observation/do-not-chase and do not present a
   buy-trigger plan.
9. Estimated capital flow is labeled; missing theme/capital values appear in
   `data_quality_flags`.
10. Neither demo nor live analysis creates a `manual_order_ticket` file.
11. The new production modules contain no automatic order, automatic click,
    broker execution, or GUI code.
12. Existing scan/sell/record/review/backtest tests remain passing without
    behavioral changes.

## Implementation Sequence For A Later Plan

After this design is approved for implementation, the implementation plan
should proceed in narrowly scoped TDD steps:

```text
session/path/output contract tests
  -> independent analyzer skeleton and empty gated output
  -> demo A/B/C deterministic classification
  -> live fallback/data-quality gates
  -> report and CSV quality labels
  -> isolation/no-ticket/no-automation regression checks
```

No implementation work is authorized by this design document alone.
