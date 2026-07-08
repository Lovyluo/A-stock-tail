# Phase 3.1 Sample Exact Backtest Design

## Purpose

Phase 3.1 adds an offline, deterministic research harness for the existing `yang_yongxing_overnight_v1` decision rules. It validates event ordering, no-future-data boundaries, exit simulation, cost calculation, metrics, and output generation. It does not establish that the strategy is profitable.

## Scope Boundary

Only this command is supported:

```text
python overnight_quant/scripts/run_backtest.py --dataset sample --fidelity sample_exact
```

`local`, `daily_proxy`, and `strict_historical` return `NOT_IMPLEMENTED_IN_PHASE_3_1`. There is no brokerage integration, automated execution, GUI, machine learning, or intraday high-frequency behavior.

## Architecture

`SampleHistoricalDataProvider` reads committed fixtures from `overnight_quant/examples/historical/`. It exposes point-in-time selection snapshots at `14:50`, market snapshots, bars available up through a selection date, later exit bars, benchmark bars, and a dataset manifest.

`BacktestEngine` processes trading dates chronologically. It exits any open position before that day's selection pass, applies the existing market/filter/scoring/risk functions only to the selection view, purchases at the same-day close proxy, and attempts next-day exits with explicit assumptions. A position blocked by limit-down remains open until a later sellable bar.

`backtest_metrics.py` calculates returns and drawdowns from executed trades and the equity curve. `backtest_report.py` writes output files to `backtest_outputs/<run_id>/`, never to real or example trading-state records/reports.

## No-Future-Data Contract

- Selection input contains only fields observable at `selection_date 14:50`.
- Provider strips any fixture field whose name starts with `next_day_` from candidates before scoring.
- Technical bars passed into scoring are filtered to `date <= selection_date`.
- Exit bars are fetched only after a BUY has been recorded and are used only for exit simulation.
- Each trade records `selection_as_of=14:50` and `data_fidelity=sample_exact`.
- Missing theme or fund fields are disclosed in quality output; no current live value is substituted.

## Simulation Rules

- One position maximum, one new BUY per trade date.
- BUY price proxy is the selected bar close.
- `max_order_value` and 100-share lots control position size.
- Default intraday assumption is `conservative`.
- Gap profit at or above `+3%` exits at open; gap stop at or below `-3%` exits at open.
- When high and low touch profit and stop levels on the same exit day, `conservative` uses stop first.
- Limit-down unable-to-exit days append `limit_down_exit_risk` and carry the position forward.
- Commission, sell stamp tax, and configurable slippage are charged from `config.yaml`.

## Outputs

Generated files under `overnight_quant/backtest_outputs/<run_id>/`:

- `trades.csv`
- `equity_curve.csv`
- `monthly_returns.csv`
- `yearly_returns.csv`
- `skipped_days.csv`
- `data_quality.md`
- `backtest_summary.md`

The output directory is git-ignored. The report states that `sample_exact` demonstrates engine and event-sequence verification only, not historical profitability of the strategy.
