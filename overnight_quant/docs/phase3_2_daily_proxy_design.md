# Phase 3.2 Local Historical Data And Daily Proxy Design

## Purpose

Phase 3.2 extends the offline backtest harness with local historical CSV data
and a `daily_proxy` fidelity mode. It supports research on broader local
datasets while preserving the event order, exit rules, fee model, and metrics
validated by Phase 3.1.

`daily_proxy` is not a complete historical verification of
`yang_yongxing_overnight_v1`. Daily bars cannot fully reconstruct the strategy's
14:50 state, theme momentum, capital flow, or intraday order-book strength.

## Chosen Architecture

The implementation extends the existing engine rather than creating a second
backtest engine:

- `LocalCsvHistoricalDataProvider` loads and validates local offline datasets.
- `DailyProxyPolicy` maps local fields into an as-of selection view, records
  proxy and unavailable fields, and enforces conservative safety rejection.
- `BacktestEngine` continues to own chronological processing, buying, exiting,
  fees, and equity events.
- `backtest_report.py` emits fidelity-specific disclosures and data-quality
  output.

This design prevents local daily-bar research from being mislabeled as
`sample_exact` and avoids duplicated trading-event logic.

## Implementation Scope

Phase 3.2a supports:

```text
python overnight_quant/scripts/run_backtest.py \
  --dataset local \
  --fidelity daily_proxy \
  --data-dir overnight_quant/backtest_data/processed
```

Phase 3.2a implements stable local CSV operation. Parquet input may be
recognized, but when the required reader engine is not available it must fail
clearly with `PARQUET_ENGINE_UNAVAILABLE`.

Phase 3.2a does not implement history downloads, `prepare_backtest_data.py`,
`strict_historical`, parameter optimization, machine learning, GUI features,
broker integration, automated orders, or automated broker-software clicks.

## Storage And State Isolation

Local historical research data uses:

```text
overnight_quant/backtest_data/
  raw/
  processed/
  manifests/
```

Real data under these directories is git-ignored. Only `.gitignore` files and
directory documentation are committed. Backtest runtime artifacts continue to
be written only to:

```text
overnight_quant/backtest_outputs/<run_id>/
```

The provider and engine do not read or write `records/`, `reports/`, or
`examples/records` during local backtests.

## Local Dataset Contract

### Required File: `daily_bars.csv`

| Field | Role | Missing Behavior |
| --- | --- | --- |
| `trade_date`, `code`, `name` | Row identity | Invalid row or dataset validation error |
| `open`, `high`, `low`, `close` | Buy/exit price proxies | Invalid row or dataset validation error |
| `volume`, `amount`, `turnover_pct`, `change_pct`, `float_mcap_yi` | Candidate filtering | Candidate rejected as insufficient data |
| `limit_up`, `limit_down` | Price-limit safety | BUY rejected with `limit_price_unknown` |
| `is_st` | ST safety | BUY rejected with `st_status_unknown` |
| `is_suspended` | Suspension safety | BUY rejected with `suspended_status_unknown` |
| `list_date` | Listing-age safety | BUY rejected with `list_date_missing` |
| `is_bj_stock` | Beijing exchange safety | BUY rejected with `bj_status_unknown` |

### Optional File: `selection_snapshots.csv`

The selection file augments each `(trade_date, code)` row with:

- `vol_ratio`
- `range_position`
- `tail_pullback_pct`
- `theme_tags`
- `theme_rank`
- `main_net`
- `big_order_net`
- `source_quality`

Missing optional fields do not crash a backtest. They lower or constrain
scoring and appear in `unavailable_fields` and field-coverage reporting.

### Optional File: `market_snapshots.csv`

Preferred columns:

- `trade_date`
- `market_gate`
- `index_change_pct`
- `market_reason`

If a market snapshot is absent but benchmark data exists, the policy may use
`benchmark_direction_proxy` and must set `market_proxy_used: true`. If neither
market snapshot nor benchmark is available, the day remains in cash with
reason `market_data_unavailable`.

### Optional File: `benchmark_bars.csv`

Columns:

- `trade_date`
- `open`
- `high`
- `low`
- `close`

Missing benchmark data does not invalidate trades, except when no market
snapshot exists and no market proxy can be computed.

### Required File: `dataset_manifest.yaml`

The manifest records:

- data source and date range;
- field coverage and missing fields;
- fields treated as proxies;
- whether `strict_historical` is supported;
- any known limitations of the source data.

## Provider Interface

`LocalCsvHistoricalDataProvider` conforms to the event engine's existing data
boundary:

```python
def trading_dates(self) -> list[str]: ...
def market_snapshot_asof(self, trade_date: str, as_of: str = "daily_close_proxy") -> dict: ...
def candidates_asof(self, trade_date: str, as_of: str = "daily_close_proxy") -> list[dict]: ...
def bars_until(self, code: str, trade_date: str) -> list[dict]: ...
def exit_bar(self, code: str, trade_date: str) -> dict | None: ...
def benchmark_bars(self) -> list[dict]: ...
def quality_manifest(self) -> dict: ...
def quality_summary(self) -> dict: ...
```

Dataset-level errors are explicit:

| Condition | Result |
| --- | --- |
| Missing `--data-dir` path | `BACKTEST_DATA_DIR_NOT_FOUND` |
| Missing `daily_bars` input | `DAILY_BARS_REQUIRED` |
| Missing dataset manifest | `DATASET_MANIFEST_REQUIRED` |
| Unreadable CSV/parquet input | `BACKTEST_DATA_READ_ERROR` |
| Parquet reader dependency unavailable | `PARQUET_ENGINE_UNAVAILABLE` |
| Duplicate keys or invalid required value | `BACKTEST_DATA_VALIDATION_FAILED` |

## Daily Proxy Policy

`DailyProxyPolicy` merges daily bars with optional selection data without
claiming that daily values reproduce the 14:50 signal state.

For each candidate it sets:

```text
data_fidelity: daily_proxy
selection_as_of: daily_close_proxy
proxy_fields: ...
unavailable_fields: ...
_risk_unknown_reasons: ...
```

Enhancement-field degradation:

| Missing Input | Recorded Disclosure |
| --- | --- |
| `theme_tags` or `theme_rank` | `theme_unavailable` |
| `main_net` or `big_order_net` | `capital_unavailable` |
| `vol_ratio` | `vol_ratio_unavailable` |
| `range_position` | `range_position_unavailable` |
| `tail_pullback_pct` | `tail_pullback_unavailable` |

No missing historical field is filled from live adapters or current market
information.

## Conservative Safety Gate

Safety uncertainty is not a scoring penalty; it is a BUY rejection:

| Missing Safety Data | Risk Reason |
| --- | --- |
| `limit_up` or `limit_down` | `limit_price_unknown` |
| `is_st` | `st_status_unknown` |
| `is_suspended` | `suspended_status_unknown` |
| `list_date` | `list_date_missing` |
| `is_bj_stock` | `bj_status_unknown` |

`is_new_stock` is derived from `list_date` in daily-proxy mode and is
identified as a listing-age proxy. A candidate with insufficient listing
history is rejected; unknown listing date is never treated as seasoned.

## Market Gate Proxy

When a `market_snapshots` row supplies `market_gate`, the policy uses that
historical statement and reports its source.

When it is missing but a benchmark row exists, the policy computes a
`benchmark_direction_proxy` from the benchmark bar. Only a positive benchmark
direction may pass the simplified gate; the report records
`market_proxy_used: true`.

When both inputs are absent, the engine records an empty day with
`market_data_unavailable`.

## Chronology And Future-Data Protection

- Selection uses daily or snapshot rows dated no later than the current
  `trade_date`.
- Candidate views drop fields beginning with `next_day_`.
- Exit OHLC rows are accessed only after a position exists and only for exit
  simulation.
- `daily_proxy` trades record `selection_as_of=daily_close_proxy`, never
  `14:50`.
- No live data source is called during a backtest.
- Missing historical theme, capital, or tail fields remain missing and are
  reported as such.

## Outputs And Disclosures

The existing output set remains:

- `trades.csv`
- `equity_curve.csv`
- `monthly_returns.csv`
- `yearly_returns.csv`
- `skipped_days.csv`
- `data_quality.md`
- `backtest_summary.md`

Phase 3.2a adds:

- `rejections.csv`
- `field_coverage.csv`

For `daily_proxy`, `backtest_summary.md` must state:

```text
Report Fidelity: DAILY_PROXY
本报告不等同于原策略完整历史验证。
题材、资金、尾盘字段缺失可能显著影响结果。
结果仅用于研究参考。
strict_historical 尚未实现。
```

`data_quality.md` records source files, date range, field coverage,
unavailable and proxy fields, safety-unknown rejection counts, market proxy
usage, and a declaration that no future or current/live values were used to
fill missing historical fields.

## Test Contract

Phase 3.2a tests cover:

1. Local CSV data loads deterministically.
2. A missing data directory returns `BACKTEST_DATA_DIR_NOT_FOUND`.
3. A missing manifest returns `DATASET_MANIFEST_REQUIRED`.
4. Missing safety fields reject BUY candidates with exact risk reasons.
5. Missing theme and capital fields do not crash and appear in quality output.
6. Missing market snapshots use benchmark proxy when possible, or create a
   `market_data_unavailable` cash day otherwise.
7. DAILY_PROXY summaries include all research limitations.
8. Existing `sample_exact` output remains deterministic.
9. Candidate selection never consumes future dates or `next_day_*` columns.
10. Output writes remain isolated to `backtest_outputs/`.
11. No automatic trading or clicking code is introduced.

## Later Steps

- Phase 3.2b may add `prepare_backtest_data.py`, offline dataset preparation,
  source ingestion, and automatic manifest/coverage generation.
- Phase 3.2c may compare broader time ranges and group outcomes by data
  coverage, without brute-force parameter optimization.
- `strict_historical` remains a future capability requiring complete
  point-in-time historical fields.
