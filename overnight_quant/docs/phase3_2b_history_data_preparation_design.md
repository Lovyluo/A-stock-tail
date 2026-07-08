# Phase 3.2b Historical Data Preparation Design

## Purpose

Phase 3.2b designs an offline, bounded, and auditable preparation pipeline for
`daily_proxy` research. The pipeline transforms deterministic sample inputs or
local raw historical files into the exact dataset contract already consumed by
`LocalCsvHistoricalDataProvider` in Phase 3.2a:

```text
overnight_quant/backtest_data/processed/<dataset_name>/
  daily_bars.csv
  selection_snapshots.csv
  market_snapshots.csv
  benchmark_bars.csv
  dataset_manifest.yaml
```

Preparation is a separate activity from backtesting. The backtest loop remains
offline and reads only prepared local files. This design does not claim that
daily data reconstructs the original tail-session strategy, and it does not
permit current live values to fill missing historical fields.

## Scope Decision

Three implementation approaches were considered:

| Approach | Description | Trade-off |
| --- | --- | --- |
| A. Deterministic raw-first pipeline | Implement `sample` and `local-raw` preparation first; add bounded `a-stock-data` ingestion later | Safest and easiest to verify end to end; chosen approach |
| B. Network-source-first pipeline | Begin by requesting historical data from live source adapters | Exercises real sources earlier, but makes normalization and failure behavior harder to verify |
| C. Broad market ingestion | Build a full-market collector immediately | Produces volume quickly, but exceeds scope and increases rate-limit and audit risks |

The chosen approach is **A**. Phase 3.2b-1 makes deterministic preparation and
provider compatibility trustworthy. Phase 3.2b-2 may add a small-code,
small-date-range `a-stock-data` adapter with retries, pacing, and source-error
reporting. It will not perform full-market harvesting.

## Non-Goals And Safety Boundary

Phase 3.2b does not implement:

- automated order placement or broker-software clicking;
- brokerage API trading integration;
- GUI features or machine learning;
- parameter brute-force optimization;
- `strict_historical`;
- networking inside the backtest loop;
- replacement of missing historical theme, capital, or intraday values with
  current live data;
- an unattended whole-market data download.

Outputs are research data only and remain isolated from
`overnight_quant/records/`, `overnight_quant/reports/`, and
`overnight_quant/examples/records/`.

## Command Contract

The planned entry point is:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --codes-file overnight_quant/backtest_data/raw/codes.txt \
  --start 2025-01-01 \
  --end 2025-12-31 \
  --out-dir overnight_quant/backtest_data/processed/my_dataset \
  --source local-raw \
  --max-codes 50 \
  --sleep 0.2
```

Supported arguments are:

| Argument | Meaning | Conservative Behavior |
| --- | --- | --- |
| `--codes CODE1,CODE2` | Inline security-code list | Mutually exclusive with `--codes-file` |
| `--codes-file PATH` | Newline- or comma-delimited code input | Blank/comment-only files are rejected |
| `--start YYYY-MM-DD` | Inclusive first historical date | Required |
| `--end YYYY-MM-DD` | Inclusive last historical date | Required and cannot precede `--start` |
| `--out-dir PATH` | Target prepared dataset directory | Refuses existing prepared files unless `--overwrite` |
| `--source a-stock-data\|local-raw\|sample` | Preparation source adapter | `sample` and `local-raw` first; `a-stock-data` follows in 3.2b-2 |
| `--max-codes N` | Maximum processed codes | Defaults to `50`; requested inputs beyond it are truncated with disclosure |
| `--sleep SECONDS` | Delay between remote requests | Relevant only for later `a-stock-data` ingestion; default `0.2` |
| `--overwrite` | Replace target prepared files | Must be explicitly supplied |
| `--dry-run` | Validate and display the preparation plan | Writes no processed files or manifest reports |

For Phase 3.2b-1, `--source a-stock-data` returns
`SOURCE_NOT_IMPLEMENTED` rather than silently acting as another source. The
script never implicitly expands an input list into an entire market universe.

## Architecture

The preparation flow is an offline ETL boundary before the existing provider:

```mermaid
flowchart LR
    A["Codes, date range, source arguments"] --> B["PreparationRequest validation"]
    B --> C{"Source adapter"}
    C -->|sample| D["DeterministicSampleSource"]
    C -->|local-raw| E["LocalRawSource"]
    C -. Phase 3.2b-2 .-> F["AStockHistoricalSource"]
    D --> G["Normalizer and proxy calculator"]
    E --> G
    F --> G
    G --> H["Coverage and source-error collector"]
    H --> I["Processed CSV dataset and dataset_manifest.yaml"]
    H --> J["Prepare report, field coverage, source errors"]
    I --> K["LocalCsvHistoricalDataProvider"]
    K --> L["daily_proxy backtest offline only"]
```

### Planned Components

| Component | Responsibility | Depends On |
| --- | --- | --- |
| `scripts/prepare_backtest_data.py` | CLI parsing, request validation, status output, and orchestration | Preparation service |
| `backtest/data_preparation.py` | Request/result models, normalization, proxy derivation, output writing, coverage and error aggregation | CSV/YAML utilities and source protocol |
| `backtest/preparation_sources.py` | `sample`, `local-raw`, and later bounded `a-stock-data` source adapters | Raw files or source-specific client |
| `backtest/historical_data.py` | Existing processed-dataset consumer; unchanged contract | Prepared CSV/YAML only |

The exact module split may be adjusted during the implementation plan to
match local file sizes, but the source, transform, reporting, and consumption
boundaries must remain separate.

## Directory And Isolation Contract

The preparation directories remain:

```text
overnight_quant/backtest_data/
  raw/
  processed/
  manifests/
```

- `raw/` contains user-provided raw CSV inputs and optional `codes.txt`.
- `processed/<dataset_name>/` contains a prepared dataset loadable by
  `LocalCsvHistoricalDataProvider`.
- `manifests/` contains preparation audit outputs, not trading reports.
- Real raw data, prepared datasets, and generated audit outputs remain
  git-ignored.
- Only directory documentation and ignore rules may be committed.

The recommended `--out-dir` includes a dataset subdirectory rather than
writing directly to `processed/`, so multiple controlled datasets can coexist
without accidental overwrites. The requested direct form
`--out-dir overnight_quant/backtest_data/processed` remains valid when the
caller deliberately wants one dataset at the root of that directory.

## Source Adapter Design

### Phase 3.2b-1: `sample`

`sample` consumes a committed, deterministic fixture created specifically for
preparation tests. It demonstrates normalization, proxy calculations, missing
field declarations, and compatibility with a downstream `daily_proxy`
backtest. It is not a market sample and cannot support performance claims.
All sources, including `sample`, still require explicit `--codes` or
`--codes-file`; fixture codes outside the requested set are not prepared.

### Phase 3.2b-1: `local-raw`

`local-raw` reads historical CSV files from a user-specified or documented raw
dataset location. It performs no network calls. The minimal required raw
content is individual daily OHLCV data plus code and name; fields not present
in raw input are either safely derived under the rules below or left missing.
Unless later configuration explicitly changes it, the source resolves raw
input beneath `overnight_quant/backtest_data/raw/`; `--out-dir` controls output
only and is never used as an implicit raw source.

An individual code with malformed or unusable raw data is recorded as a source
error; other valid codes may still be prepared. If no usable daily bars remain,
the command terminates with `NO_DAILY_BARS_FETCHED`.

### Phase 3.2b-2: `a-stock-data`

The later network adapter is deliberately bounded:

- it receives only the user-supplied code set, capped by `--max-codes`;
- it requests only the user-specified date interval;
- it uses `--sleep` pacing and bounded retry rules;
- daily bars and benchmark bars may use historical K-line capabilities exposed
  by the toolkit;
- listing metadata may come from Eastmoney stock information when it is
  historically stable for a code;
- historical theme, intraday tail strength, and capital fields are included
  only when the historical source returns point-in-time values for the target
  date, otherwise they remain unavailable;
- it writes per-code source errors and never runs from inside
  `run_backtest.py`.

### Historical Capability Mapping For The Later Adapter

The existing live adapter identifies useful toolkit endpoints, but its current
values cannot be treated as history. A future `a-stock-data` preparation
adapter may reuse only historically dated capabilities:

| Prepared Data | Candidate Historical Capability | Allowed Use In Phase 3.2b-2 |
| --- | --- | --- |
| Individual daily OHLCV and MA values | Baidu K-line or mootdx K-line | Allowed when response dates cover the requested interval |
| Benchmark OHLCV | Historical index K-line capability | Allowed with benchmark symbol and source in manifest |
| `list_date` | Eastmoney stock information metadata | Allowed as stable instrument metadata with source disclosure |
| Historical daily capital flow | Eastmoney `push2his` daily fund flow | Optional only when each value is dated to the historical row |
| Historical theme tags | THS dated hot-reason endpoint | Optional only for the requested historical date and disclosed coverage |
| True tail-session state | Historical minute K-line source | Optional only when available; otherwise unavailable |
| Tencent quote/current live enrichments | Tencent real-time quote | Never used to fill historical rows |

No capability listed here is fetched in Phase 3.2b-1.

## Processed Dataset Contract

### `daily_bars.csv`

This is the core table and must contain one row per `(trade_date, code)`.

| Field | Origin Or Safe Derivation | If Unavailable |
| --- | --- | --- |
| `trade_date`, `code`, `name` | Source identity | Row discarded and reported |
| `open`, `high`, `low`, `close`, `volume`, `amount` | Historical daily bar source | Row discarded and reported |
| `turnover_pct` | Historical source only in first implementation | Blank; candidate may not meet filters |
| `change_pct` | Source, or `(close / prior_close - 1) * 100` using same code's earlier date | Blank if prior bar is unavailable |
| `limit_up`, `limit_down` | Historical source, or explicitly classified price-limit proxy | Blank if rule cannot be established safely |
| `is_st` | Historical source only unless input records the status for that date | Blank; safety rejection |
| `is_suspended` | Historical source or a documented zero-trading historical marker | Blank; safety rejection |
| `list_date` | Source metadata with source disclosure | Blank; safety rejection |
| `is_bj_stock` | Code-prefix derivation | Derived and marked proxy |
| `float_mcap_yi` | Historical point-in-time source only | Blank; filtering may reject |

`is_new_stock` is not written as a source truth. The existing daily-proxy
policy derives listing age from `list_date` at each `trade_date`.

### `selection_snapshots.csv`

This table contains only information eligible for candidate scoring as of the
same `trade_date`; it may be sparse.

| Field | Rule | Manifest Marking |
| --- | --- | --- |
| `trade_date`, `code` | Link to `daily_bars` | Source field |
| `vol_ratio` | May be approximated as current daily volume divided by preceding five available daily volumes | `volume_ratio_proxy` |
| `range_position` | May be calculated as `(close - low) / (high - low)` when range is positive | `daily_range_position_proxy` |
| `tail_pullback_pct` | Requires point-in-time intraday data; not inferred from daily bar | `unavailable` unless true historical intraday source exists |
| `theme_tags`, `theme_rank` | Historical date-specific source only | `unavailable` when absent |
| `main_net`, `big_order_net` | Historical date-specific fund-flow source only | `unavailable` when absent |
| `source_quality` | Preparation-generated field describing source/proxy level | Always written |

No column beginning with `next_day_` is generated or consumed for selection.

### `market_snapshots.csv`

When a complete historical market gate is not available, preparation may emit a
benchmark-direction proxy:

| Field | Rule |
| --- | --- |
| `trade_date` | Historical benchmark date |
| `market_gate` | `PASS` only when the configured benchmark direction proxy is positive; otherwise `FAIL` |
| `index_change_pct` | Calculated benchmark daily direction percentage |
| `market_reason` | `benchmark_direction_proxy` or `benchmark_direction_proxy_non_positive` |
| `market_proxy_used` | `true` whenever generated from daily benchmark bars |

If neither historical market data nor benchmark bars are available, a market
snapshot is not invented. The downstream policy will keep that day in cash
with `market_data_unavailable`.

### `benchmark_bars.csv`

The first supported benchmark is the configured CSI 300 or SSE Composite
series. It contains:

```text
trade_date,open,high,low,close
```

The chosen symbol and data source must be named in the manifest.

## Price-Limit And Safety Derivation

Safety fields receive stricter treatment than scoring enhancements:

- `is_bj_stock` can be safely inferred from the code-prefix rule and marked as
  `code_prefix_proxy`.
- `change_pct`, `range_position`, and `vol_ratio` can be calculated from
  historical daily rows and marked as proxies.
- `limit_up` and `limit_down` can be estimated only when the preparation source
  reliably identifies the dated trading-board and ST treatment needed for the
  applicable rule. Main-board, ChiNext/STAR, ST, and ETF rules are not
  interchangeable. When this classification is absent, price-limit fields
  stay blank so the existing safety gate produces `limit_price_unknown`.
- `is_st`, `is_suspended`, and `list_date` remain blank unless a historical or
  stable metadata source supports the value. Missing values must continue to
  cause conservative BUY rejection through Phase 3.2a policy reasons.

The preparation report lists every safety field derived as a proxy and every
safety field left unknown.

## Manifest Contract

Each processed dataset must contain `dataset_manifest.yaml` with the following
shape:

```yaml
dataset:
  name: local_raw_2025_research
  created_at: "2026-05-27T14:00:00+08:00"
  start: "2025-01-01"
  end: "2025-12-31"
  codes_count: 2
  trade_dates_count: 240
  source: local-raw
  fidelity: daily_proxy

sources:
  daily_bars:
    source: local_raw_daily_bars
    fields: [trade_date, code, name, open, high, low, close, volume, amount]
    proxy_fields: [change_pct, is_bj_stock]
    unavailable_fields: [limit_up, limit_down]
  selection_snapshots:
    source: daily_bar_derivations
    fields: [trade_date, code, source_quality]
    proxy_fields: [vol_ratio, range_position]
    unavailable_fields: [tail_pullback_pct, theme_tags, theme_rank, main_net, big_order_net]
  market_snapshots:
    source: benchmark_direction_proxy
    fields: [trade_date, market_gate, index_change_pct, market_reason, market_proxy_used]
    proxy_fields: [market_gate]
    unavailable_fields: []
  benchmark_bars:
    source: local_raw_benchmark
    fields: [trade_date, open, high, low, close]

warnings:
  - DAILY_PROXY_ONLY
  - NOT_STRICT_HISTORICAL
  - NO_INTRADAY_TAIL_DATA_IF_APPLICABLE
  - NO_HISTORICAL_THEME_IF_APPLICABLE
  - NO_HISTORICAL_FUND_FLOW_IF_APPLICABLE
```

The values are calculated from actual prepared output. Blank historical
enhancement columns must be listed under `unavailable_fields`; they cannot be
omitted merely because the backtest tolerates them.

## Audit Output Contract

Each non-dry preparation attempt, including an argument or source validation
failure when the manifests directory is writable, writes a timestamped
`prepare_report` beneath
`overnight_quant/backtest_data/manifests/`:

```text
prepare_report_YYYYMMDD_HHMMSS.md
field_coverage_YYYYMMDD_HHMMSS.csv
source_errors_YYYYMMDD_HHMMSS.csv
```

`field_coverage.csv` is emitted when row coverage can be measured;
`source_errors.csv` is emitted for source or row failures. Early failures such
as `CODES_REQUIRED` may have only the report plus stdout status because no
source rows exist to measure or attribute.

`prepare_report` contains:

1. source type, input paths, date range, code-input mode, requested code count,
   applied cap, `sleep`, overwrite flag, and target directory;
2. actual code count and actual trade-date range prepared;
3. per-source success and failure counts;
4. field coverage with special emphasis on safety-field coverage;
5. proxy fields and their formulas or derivation basis;
6. unavailable historical fields;
7. discarded rows and their reasons;
8. an error-code summary referencing `source_errors.csv`;
9. whether the result is loadable for `daily_proxy`;
10. an explicit statement that it is not suitable for
    `strict_historical`.

`field_coverage.csv` has columns:

```text
table,field,present_rows,total_rows,coverage_pct,classification,source_or_formula
```

`source_errors.csv` has columns:

```text
source,code,trade_date,error_code,detail,recoverable
```

For partial success, usable processed files and reports may be emitted while
the terminal status is `PARTIAL_DATA_PREPARED`.

## Validation And Status Codes

Validation occurs before any processed file is written. Except for
`--dry-run`, validation failures are reported in stdout and in a preparation
report when its audit destination is writable:

| Condition | Status |
| --- | --- |
| Neither `--codes` nor usable `--codes-file` supplied | `CODES_REQUIRED` |
| `--start` or `--end` omitted, invalid, or reversed | `DATE_RANGE_REQUIRED` |
| Target already contains prepared outputs without `--overwrite` | `DATA_DIR_EXISTS_WITHOUT_OVERWRITE` |
| Requested source is recognized but deferred in the active sub-phase | `SOURCE_NOT_IMPLEMENTED` |
| No valid daily bars survive input and normalization | `NO_DAILY_BARS_FETCHED` |
| Some requested codes or rows fail, but a loadable dataset is emitted | `PARTIAL_DATA_PREPARED` |
| Required processed files and manifest are emitted successfully | `PREPARE_COMPLETED` |

`--dry-run` validates arguments, resolves and caps codes, lists expected
sources and target files, and prints a dry-run outcome; it writes neither
processed data nor audit reports.

## Data Flow And Chronology Guardrails

1. Resolve codes and date range, applying `--max-codes` before source access.
2. Validate output overwrite policy and source availability.
3. Read deterministic raw inputs or, in the later network phase, fetch bounded
   historical rows outside of any backtest.
4. Normalize only rows within the requested interval.
5. Calculate daily proxies using the current date and earlier rows for the same
   code; for example the rolling volume denominator contains no future dates.
6. Leave point-in-time historical fields blank when they cannot be established.
7. Write standardized files and an audit manifest.
8. Optionally verify the emitted dataset by loading it through
   `LocalCsvHistoricalDataProvider`; this is preparation validation, not a
   trading result.
9. Run `daily_proxy` later as a separate offline command.

The pipeline does not read `next_day_*` source columns into selection output,
does not call `AStockClient(mode="live")` for historical backfill during a
backtest, and does not mutate real or example trading state.

## Testing Design

Phase 3.2b-1 tests should cover:

1. `--dry-run` resolves a bounded plan and writes no processed or manifest
   outputs.
2. Missing code input returns `CODES_REQUIRED`.
3. Missing or invalid date range returns `DATE_RANGE_REQUIRED`.
4. Existing prepared output refuses replacement without `--overwrite`.
5. Deterministic `sample` or `local-raw` input produces all five processed
   dataset files.
6. `dataset_manifest.yaml` records `daily_proxy`, all proxy derivations, and
   unavailable historical fields.
7. `field_coverage.csv` reports exact counts for complete, proxy, and missing
   fields.
8. A failed code is written to `source_errors.csv` while successful codes can
   yield `PARTIAL_DATA_PREPARED`.
9. The generated processed dataset loads through
   `LocalCsvHistoricalDataProvider` and runs in `daily_proxy` mode.
10. Rolling proxy calculations use only current and earlier dated rows and do
    not consume `next_day_*` input.
11. Preparation writes only beneath `backtest_data/`; a downstream backtest
    writes only beneath `backtest_outputs/`, never real/example trading
    records or reports.
12. Production code contains no automatic order or broker-click capability.

Phase 3.2b-2 adds tests for bounded a-stock-data source failures, sleep/retry
behavior, source error reporting, and no full-universe expansion.

## Delivery Sequence

### Phase 3.2b-1: Deterministic Preparation

- Add the preparation CLI and bounded validation model.
- Add `sample` and/or `local-raw` adapters with deterministic fixture coverage.
- Generate processed CSV files, manifest, coverage report, and source-error
  report.
- Prove generated output is readable by the already accepted `daily_proxy`
  engine.

### Phase 3.2b-2: Bounded Historical Source Adapter

- Add `--source a-stock-data` for small explicit code lists and date ranges.
- Use historical K-line and stable metadata capabilities only where their
  historical meaning is documented.
- Add pacing, bounded retries, and granular source errors.
- Keep unavailable point-in-time theme, capital, and tail fields empty unless
  historically returned for the requested date.

No phase in this design performs automated trading, broker clicks, full-market
collection, or strict-historical claims.

## Acceptance Criteria For This Design

Implementation may begin only after this design is reviewed and approved.
When Phase 3.2b-1 is later implemented, it must demonstrate that:

- deterministic preparation produces a provider-loadable `daily_proxy`
  dataset;
- data and reports stay in ignored research directories;
- unknown safety data remains rejectable rather than being guessed;
- proxy and unavailable data are fully disclosed;
- the backtest remains offline and free of current/live historical backfill;
- output wording continues to state that `DAILY_PROXY` is research reference,
  not complete historical validation of `yang_yongxing_overnight_v1`.
