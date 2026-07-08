# overnight_quant

Demo-first MVP for `yang_yongxing_overnight_v1`, a manual A-share overnight signal assistant.

This package is intentionally independent from `TradingAgents-astock/`. It only reads market data, scores candidates, applies risk gates, writes signal records, generates manual order tickets, and creates next-day sell plans.

It does not place orders, click broker software, bypass broker APIs, or perform any automated trading.

## What The MVP Does

- Runs a deterministic demo scan with no live market dependency.
- Optionally tries live quote data in `--mode live`; if live data fails, it logs the failure and falls back to demo data.
- Applies market, initial-pool, price-volume, trend, theme, capital, total-score, and buy/sell rule checks.
- Emits explicit pass/reject reasons for filters, scoring, and risk.
- Blocks ticket generation when risk gates fail.
- In live mode, blocks BUY tickets on non-trading days and outside the 14:25-14:55 tail session by default.
- Saves live signals and manual trade artifacts under real `records/` and `reports/` directories.
- Routes demo scan and demo sell-plan artifacts to `examples/`.
- Generates a next-day sell plan from the selected state's `manual_orders.csv`; demo mode creates an example order if none exists.

## Install

From the repository root:

```bash
python -m pip install pytest
```

Live mode can use standard-library Tencent quote fetching. Future data adapters may use the broader `a-stock-data` endpoints and optional dependencies such as `requests`, `mootdx`, `pandas`, and `stockstats`.

After-close full-market observation can optionally use `easyquotation` as a
Sina full-market fallback when Eastmoney `clist` is unavailable:

```bash
python -m pip install easyquotation
```

The fallback is used only for after-close watchlist universe discovery. It does
not enable order placement, manual tickets, broker API access, or automated
clicking.

## Run Demo Scan

```bash
python overnight_quant/scripts/run_scan.py --mode demo
```

Expected outputs:

- `overnight_quant/examples/records/signals.csv`
- `overnight_quant/examples/records/signal_rejections.csv`
- `overnight_quant/examples/reports/manual_order_ticket_<date>_<code>.md`

Demo commands never write to real `overnight_quant/records/` or `overnight_quant/reports/`.

`signals.csv` is reserved for tail candidates that pass the strategy and risk
gate. ST stocks, limit-up unavailable stocks, overextended rows, stale rows, and
other rejected candidates are written only to `signal_rejections.csv` for audit.
Runtime CSV files are written as UTF-8 with BOM so Windows spreadsheet tools can
display Chinese names without mojibake.

The demo universe includes:

- qualified strong stock
- limit-up unavailable stock
- tail-diving stock
- no-theme stock
- capital-outflow stock
- ST stock
- suspended stock
- new stock
- Beijing Stock Exchange sample

## Run Live With Fallback

```bash
python overnight_quant/scripts/run_scan.py --mode live
```

Live mode is best-effort. It tries real candidate and enrichment sources first:

- THS hot-reason candidates
- Tencent real-time quotes
- Baidu daily K-line data
- Eastmoney stock info and fund flow
- THS northbound flow

For after-close watchlists, the full-market universe source order is:

1. Eastmoney `clist` 00/60 full-market universe.
2. Optional `easyquotation` Sina full-market snapshot.
3. Demo fallback only as a blocked rehearsal state.

Only the first two can produce a formal live after-close observation result.
Demo fallback is explicitly marked and does not generate formal A/B observation
rows.

Each normalized stock carries `_sources`, `_missing_fields`, and safety-field risk reasons. Missing scoring fields lower scores; missing safety fields such as limit prices, ST status, suspended status, list date, or Beijing Stock Exchange status conservatively block ticket generation.

Live mode caches confirmed listing dates under `overnight_quant/data/cache/list_date_cache.json` and ignores that runtime cache in git. When direct fund-flow fields are unavailable, `main_net` can be estimated from the THS big-order field and is marked with `fund_flow_source=estimated_from_big_order_net`; this affects capital scoring only and is not a safety gate.

Live reports include `trade_date`, `run_time`, `session_state`, `is_trade_day`, `freshness_summary`, stale-source details, and a `Field Coverage Improvement` section. On a non-trading day or outside the tail window, live mode may still scan and write the report, but final advice remains `NO_TRADE` unless you run explicit demo mode.

If all live candidate sources fail, the command falls back to demo data and prints/logs the fallback reason. Fallback data is still subject to the live session/risk gates, so it will not produce a live BUY ticket on a non-trading day. Every live run writes:

- `overnight_quant/reports/live_data_quality_<date>.md`

Live commands always use real `overnight_quant/records/` and `overnight_quant/reports/`; they never read demo records under `examples/`.

## Manual Order Ticket

The ticket is a human-readable markdown file. It includes:

- strategy name
- date
- code and name
- suggested price
- max acceptable price
- suggested amount and board-lot quantity
- stop loss
- next-day sell plan summary
- risk level
- manual confirmation checklist

The ticket is not an order. You must decide and execute manually in your own trading software.

## Run Sell Plan

```bash
python overnight_quant/scripts/run_sell_plan.py --mode demo
```

If `overnight_quant/examples/records/manual_orders.csv` does not exist in demo mode, the script writes a deterministic demo order, then saves:

- `overnight_quant/examples/reports/sell_plan_<date>.md`

Possible actions:

- `SELL_NOW`
- `WAIT_10_MIN`
- `TAKE_PROFIT`
- `STOP_LOSS`
- `LIMIT_UP_WATCH`
- `LIMIT_DOWN_RISK`

Committed example snapshots can be reviewed directly. When recording a new teaching-only demo fill, select example state explicitly:

```text
python overnight_quant/scripts/run_trade_review.py --state example --code 300001
```

For a newly generated example ticket, use `run_record_order.py --state example` with that ticket's code, price, quantity, and trade time. Without `--state example`, manual trade recording and trade review default to real state.

## Real Trading Day Runbook

Preflight:

```text
python overnight_quant/scripts/run_preflight.py
python overnight_quant/scripts/run_scan.py --mode live --dry-run
```

Tail session, 14:25-14:55:

```text
python overnight_quant/scripts/run_scan.py --mode live
```

If a manual ticket is generated:

1. Manually check stock code, price, quantity, stop loss, and max acceptable price.
2. Manually open your broker software.
3. Do not chase above the max acceptable price.
4. Place the order manually only if you independently accept the risk.
5. After execution, record the fill in `overnight_quant/records/manual_orders.csv`.

Record the manual fill:

```text
python overnight_quant/scripts/run_record_order.py --code 300001 --price 18.50 --qty 200 --side BUY --trade-time "2026-05-25 14:52:00"
```

The recorder validates the fill against the latest ticket, writes `manual_orders.csv`, and updates `trade_lifecycle_<date>.md`. It does not place or submit orders.

Next day after 09:25:

```text
python overnight_quant/scripts/run_sell_plan.py --mode live
```

After the manual sell is completed:

```text
python overnight_quant/scripts/run_record_order.py --code 300001 --price 19.02 --qty 200 --side SELL --trade-time "2026-05-26 09:45:00"
python overnight_quant/scripts/run_trade_review.py --code 300001
```

SELL records are checked against open manual BUY positions. Full sells close the position; partial sells leave the remaining `open_qty`; the trade review estimates commission, stamp tax, net PnL, return percentage, and execution-discipline flags.

Live dry-run and live summary reports are saved under `overnight_quant/reports/`. If a capital-flow field is estimated from big-order data, reports mark `estimated_capital_flow=true` and state that the capital item is only an auxiliary estimate.

## After-Close Next-Morning Watchlist

Generate a read-only observation plan after the market closes:

```text
python overnight_quant/scripts/run_after_close_analysis.py --mode demo
python overnight_quant/scripts/run_after_close_analysis.py --mode live
```

Demo mode writes deterministic example outputs beneath:

- `overnight_quant/examples/reports/after_close_analysis_<date>.md`
- `overnight_quant/examples/records/next_morning_watchlist_<date>.csv`

Live mode writes beneath real `reports/` and `records/`, but creates a formal
watchlist only on a likely trading day after close with usable live data.
Before close, on a non-trading day, after live-to-demo fallback, or when
critical freshness/safety fields are uncertain, it writes a report and an
empty-header CSV instead.

The markdown report groups observations into A/B/C classes. The CSV file named
`next_morning_watchlist_<date>.csv` contains only formal A/B observation rows;
C-class risk rows remain in the report as "do not chase" exclusions and are not
written into the watchlist CSV. It never generates a manual ticket, records an
order, submits an order, or clicks broker software.

If the prior evening report was missed, a live pre-market catch-up mode can
reconstruct the observation list from precisely dated prior-close data:

```text
python overnight_quant/scripts/run_after_close_analysis.py --mode live --replay-previous-close
```

This replay mode is only valid before continuous trading, during `PRE_MARKET`
or `CALL_AUCTION`. It writes separate `morning_replay_analysis_<date>.md` and
`morning_replay_watchlist_<date>.csv` artifacts, requires all replay-sensitive
data to match the previous likely trading day, and labels accepted rows as
`previous_close_replay`. It is not current intraday confirmation and still
does not create tickets, records, submitted orders, or broker clicks.

## Local Observation Dashboard

Phase 4.2 adds an optional local Streamlit dashboard for reading the latest
preflight, live dry-run, after-close, morning replay, sell-plan, lifecycle, and
review artifacts without manually opening every markdown or CSV file:

```text
pip install -r requirements-ui.txt
python overnight_quant/scripts/run_dashboard.py
```

If Streamlit is not installed, the launcher prints:

```text
UI_DEPENDENCY_MISSING: please run pip install -r requirements-ui.txt
```

The dashboard defaults to live mode and Chinese UI. Use the `中文 / English`
language switch to change the interface immediately. It now displays parsed
preflight, live dry-run, after-close, morning replay, sell-plan, lifecycle, and
review results directly in the page. The generated markdown and CSV files remain
available under audit expanders for traceability, but users no longer need to
open `reports/` or `records/` manually for normal observation.

Demo actions remain available only for testing and are explicitly labeled as
demo; live fallback to demo is shown as not valid for live reference observation.

Phase 4.2.3 upgrades the same dashboard into a premium dark financial-terminal
layout with a top status bar, hero conclusion, neon status badges, glass-style
cards, direct result summaries, A/B/C watchlist tables, and audit expanders.
This is a presentation-only upgrade: strategy logic, result parsing, command
whitelist, and manual-only trading boundaries are unchanged.

The Tail tab separates observable tail candidates from rejected audit rows.
Limit-up, ST, overextended, stale, or otherwise rejected rows are shown only
under risk exclusions and must not be treated as observation candidates.

The dashboard exposes a fixed whitelist of safe actions: preflight, live
dry-run, formal live tail scan, after-close analysis, morning replay, sell-plan
generation, demo after-close, and demo scan. `Live Dry-run` always means
`live data + --dry-run`; it never becomes formal live and never generates a
manual ticket. The formal live button runs only during the 14:25-14:55 tail
window and otherwise shows a Chinese/English warning without running the scan.
When formal live runs, the page parses the latest manual ticket and directly
shows the stock code, suggested price range, position amount, suggested
quantity, stop loss, and next-morning sell plan. It still does not record
manual trades, automate orders, click broker software, call broker trading APIs,
or accept arbitrary shell input.

For `Live Dry-run`, review:

- `overnight_quant/reports/dry_run_scan_<date>.md`
- `overnight_quant/reports/live_data_quality_<date>.md`
- `overnight_quant/records/signals.csv`
- `overnight_quant/records/signal_rejections.csv`

For formal live tail scan, review:

- `overnight_quant/reports/live_scan_summary_<date>.md`
- `overnight_quant/reports/manual_order_ticket_<date>_<code>.md`
- `overnight_quant/records/signals.csv`
- `overnight_quant/records/signal_rejections.csv`

## Runtime State And Reset

Real-run state is intentionally kept clean in:

- `overnight_quant/records/`
- `overnight_quant/reports/`

Committed demonstration snapshots and all `--mode demo` outputs use:

- `overnight_quant/examples/records/`
- `overnight_quant/examples/reports/`

Inspect or clear generated real-run artifacts before a live trading day:

```text
python overnight_quant/scripts/run_reset_state.py --dry-run
python overnight_quant/scripts/run_reset_state.py --yes
```

The reset command only removes known generated files from real-state directories. It does not remove source modules, tests, configuration, README files, or anything under `examples/`.

## Sample Historical Backtest

Phase 3.1 provides one deterministic, offline backtest command:

```text
python overnight_quant/scripts/run_backtest.py --dataset sample --fidelity sample_exact
```

It reads committed fixtures from `overnight_quant/examples/historical/` and writes each run only beneath `overnight_quant/backtest_outputs/<run_id>/`. Backtest output is ignored by Git and never reads or writes real trading records.

`sample_exact` validates the backtest engine, fee handling, event ordering, no-future-data boundary, and limit-down carry behavior. It is synthetic sample data and cannot demonstrate that `yang_yongxing_overnight_v1` is profitable or historically effective. `daily_proxy`, `strict_historical`, and local historical datasets are intentionally deferred to later phases.

## Local Daily Proxy Backtest

Phase 3.2a supports offline local CSV research input:

```text
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed/my_dataset
```

Provide `daily_bars.csv` and `dataset_manifest.yaml` in the selected directory. Optional `selection_snapshots.csv`, `market_snapshots.csv`, and `benchmark_bars.csv` improve coverage. Local market data is ignored by Git and generated results still go only to `overnight_quant/backtest_outputs/<run_id>/`.

`daily_proxy` uses the daily close as an approximate buy price and records `selection_as_of=daily_close_proxy`. It does not reconstruct a true 14:50 signal. Missing safety fields reject a buy; missing theme, capital, or tail-strength fields remain unavailable and are disclosed rather than replaced with current live data. `daily_proxy` is research reference only and is not a complete historical validation of the strategy.

## Prepare Offline Daily Proxy Data

Phase 3.2b-1 adds a bounded, offline preparation step. Its deterministic
sample fixture requires an explicit code list:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source sample --codes 300201 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed
```

The default sample profile is `neutral`; it keeps theme and capital fields
unavailable and is suitable for testing the no-trade path. To validate that a
prepared dataset can flow through one completed daily-proxy transaction, use
the committed positive profile:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source sample --sample-profile positive --codes 300201 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed
```

The positive profile uses deterministic `sample_fixture` theme and capital
fields only to validate the preparation-to-trade report pipeline. They are not
live-filled or confirmed historical signals. The output remains `DAILY_PROXY`
research, not `strict_historical` validation or evidence of profitability;
intraday tail pullback remains unavailable without minute bars.

Local raw CSV preparation is also offline:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source local-raw --codes-file overnight_quant/backtest_data/raw/codes.txt --raw-dir overnight_quant/backtest_data/raw --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
```

The preparer derives disclosed daily proxies such as rolling volume ratio,
daily range position, and moving averages from same-code historical rows only.
It leaves unavailable theme, capital-flow, and intraday-tail fields blank, and
does not invent unknown safety values. Prepared files and quality reports stay
under ignored `backtest_data/` directories and never enter real or example
trade records.

`--dry-run` writes no processed data. Existing processed data are not
overwritten without `--overwrite`. Network-backed historical collection is
not implemented in this phase.

## Mocked A-Stock-Data Preparation Contract

Phase 3.2b-2b introduces a bounded `--source a-stock-data` adapter skeleton
that is usable only through injected mock clients in tests. It deliberately
does not implement real Baidu, mootdx, Eastmoney, Tencent, THS, or other
historical requests, and it never falls back to sample/demo inputs.

The CLI supports request validation without data access:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --codes 300001,600519 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --max-codes 10 --sleep 0.5 --dry-run
```

`LIVE_PREP_HARD_MAX_CODES` is 10. Requests exceeding the effective maximum
are rejected with `MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT`, never truncated or
reordered. A valid non-dry CLI run without an injected mock client returns
`REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B`.

Mock-prepared data keep uppercase truth-level disclosure: injected dated bars
are `REAL_HISTORICAL`, daily-derived volume/range/market signals are
`DAILY_PROXY`, unavailable theme/capital/tail fields remain `UNAVAILABLE`,
and unconfirmed safety fields remain `UNKNOWN` so the safety gate rejects
them conservatively. `SAMPLE_FIXTURE` fields are prohibited from this source
path. The resulting files are still `DAILY_PROXY` research artifacts, not
`strict_historical` validation or evidence of profitability.

## Fake-Real Request Validation Gate

Phase 3.2b-2c adds opt-in request validation only:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --codes 600519 --start 2025-01-01 --end 2025-01-10 --out-dir overnight_quant/backtest_data/processed --max-codes 1 --sleep 0.5 --dry-run
```

In the Phase 3.2b-2c gate, an enabled request was limited to 3 codes and 31
natural days, while non-dry execution without a test-injected fake-real client
returned `REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C`. Phase 3.2b-2d extends
that guarded path with the still narrower real preparation workflow below.

Outputs remain `DAILY_PROXY` research artifacts. Theme, capital, and
intraday-tail fields stay unavailable unless historically established, unknown
safety fields continue to block buys, and no current/live values are used to
fill history.

## Minimal Real Historical Preparation

Phase 3.2b-2d adds an experimental, explicitly enabled real preparation route.
The first-run boundary is deliberately small: one supplied code and at most
ten natural days, in addition to the existing three-code/thirty-one-day outer
gate. Larger first-run requests are rejected with
`REAL_FIRST_RUN_SCOPE_TOO_LARGE`.

Only preparation time data access is implemented:

- Baidu dated daily K-line rows for OHLCV and amount
- Eastmoney stable metadata for `name` and `list_date`
- raw response cache and source-error audit under ignored `backtest_data/`

Benchmark history, fund flow, THS themes, Tencent current quote, and live
adapter backfills are not used. Historical price limits, ST state, and
suspension state remain unknown unless supplied by a historical source, so the
downstream safety gate may conservatively reject every trade.

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --codes 600519 --start 2025-01-02 --end 2025-01-10 --out-dir overnight_quant/backtest_data/processed --max-codes 1 --sleep 0.5 --overwrite
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed
```

`--dry-run` performs no network requests. The subsequent backtest reads local
processed files only. Output remains `DAILY_PROXY` research material, not
`strict_historical` validation or evidence of strategy profitability.

## Real Cache Replay Validation

Phase 3.2b-2e validates replay of the same bounded real preparation request
without widening its network scope. The accepted Phase 3.2b-2d cold-fill run
recorded two endpoint requests and two raw-cache writes. Re-running the same
one-code/ten-day command against those retained ignored cache entries must
report:

```text
cache_enabled: true
cache_hits: 2
cache_writes: 0
cache_read_failures: 0
network_requests_made: 0
backtest_network_access: false
```

Do not delete accepted real raw cache merely to manufacture a second cold
request. Corrupt-cache recovery is tested only with disposable test caches
and fake transports. Prepared replay output remains `DAILY_PROXY` research
input and is consumed offline by `run_backtest.py`. When comparing the
accepted pre-2e cold manifest with replay output, the new `cache_enabled`
field is treated as audit metadata alongside runtime cache/request counters.

## Expanded Real Historical Preparation Scope

Phase 3.2b-2f adds an explicit scope selector while preserving the conservative
default:

| Scope | Codes | Natural Days |
| --- | ---: | ---: |
| `minimal` (default) | 1 | 10 |
| `expanded` | 3 | 31 |

`expanded` must be explicitly selected and still requires
`--enable-real-astock-request`, including for dry-run validation. It reuses
only the existing Baidu dated daily K-line and Eastmoney stable metadata
preparation endpoints:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --real-request-scope expanded --codes 600519,300750,510300 --start 2025-01-02 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --max-codes 3 --sleep 0.5 --dry-run
```

Requests are never truncated or reordered. A repeated request for the same
codes and exact date range can be replayed from cache; widening a prior date
range creates a distinct cache key and may require new preparation requests.
Reports disclose per-code daily-bar and metadata status, failed codes, cache
counters, and network request counts. Prepared data remains `DAILY_PROXY`
only, not `strict_historical` validation or evidence of strategy
profitability, and `run_backtest.py` remains offline.

## Run Tests

```bash
python -m pytest overnight_quant/tests -q
```

The tests cover:

- filters
- scoring
- risk gates
- live trading calendar/session gates
- live freshness and quality-report fields
- manual BUY/SELL recording, position tracking, lifecycle reports, and single-trade review
- real/example state isolation and safe real-state reset
- deterministic sample-exact backtesting, no-future-data boundaries, and report disclosure
- local daily-proxy loading, conservative safety rejections, market proxy disclosure, and state isolation
- offline daily-proxy data preparation, quality audit output, and processed-to-backtest integration
- neutral and positive prepared fixtures, including positive trade-report lifecycle validation without live data
- mocked-only bounded historical-source validation, cache/audit behavior, and offline daily-proxy consumption
- fake-real request gating, three-code/thirty-one-day limits, truth-level audits, and offline consumption without network implementation
- minimal one-code/ten-day real historical preparation with raw-cache auditing and offline daily-proxy consumption
- real-first-run raw-cache replay counters, processed-input consistency, and zero-network offline consumption

## Risk Notice

This system is for research and decision support only. It is not investment advice.

Short-term overnight trading can be highly volatile. Risks include high-open fade, low open, liquidity gaps, limit-down inability to exit, data-source errors, and human execution errors.

You are responsible for all trading decisions and outcomes. The software must not be treated as a promise of profit or a substitute for independent judgment.

## Extension Points

The MVP keeps stable function boundaries for later work:

- replace demo theme fields with real `ths_hot_reason`
- replace demo capital fields with real Eastmoney fund-flow data
- add bounded historical-source preparation after offline validation
- add after-close review reports
- add dragon-tiger-board enrichment
- add richer market breadth and industry rotation gates
