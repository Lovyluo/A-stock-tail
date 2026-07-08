# Local Backtest Data

This directory is reserved for offline historical research datasets. Real
market data is deliberately git-ignored.

## Directories

- `raw/`: original local CSV or parquet inputs.
- `processed/`: standardized input directories used with `--data-dir`.
- `manifests/`: supporting source notes or coverage reports.
- `cache/`: runtime-only historical-source response cache, ignored by Git.

For Phase 3.2a, place a dataset directory beneath `processed/` containing:

- `daily_bars.csv` and `dataset_manifest.yaml` (required);
- `selection_snapshots.csv`, `market_snapshots.csv`, and
  `benchmark_bars.csv` (optional).

Example invocation:

```text
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed/my_dataset
```

The backtest loop reads local files only. It does not call live adapters or
populate missing historical fields with current data.

## Offline Preparation

Phase 3.2b-1 can prepare a deterministic research dataset without network
access:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source sample --codes 300201 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
```

For user-provided raw CSV data:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source local-raw --codes-file overnight_quant/backtest_data/raw/codes.txt --raw-dir overnight_quant/backtest_data/raw --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
```

`local-raw` expects `daily_bars.csv` under `raw/`, with identity and daily
OHLCV columns plus any historically available safety fields. An optional
`benchmark_bars.csv` supplies benchmark-direction market proxies. Unknown
safety fields remain empty; historical theme, capital flow, and intraday-tail
fields remain unavailable unless a future historical source can establish
them at that date.

Preparation writes five ignored processed files and three ignored audit files
under `manifests/`. It never writes to real or example trading state. Prepared
datasets are `DAILY_PROXY` research inputs only, not `strict_historical`
validation.

## Mocked Historical Source Skeleton

Phase 3.2b-2b adds the bounded `--source a-stock-data` contract for testing
with an injected mock client only. It does not include an enabled real data
client and does not make HTTP or TCP requests.

The CLI can validate a request without fetching anything:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --codes 300001,600519 --start 2025-01-01 --end 2025-01-31 --max-codes 10 --sleep 0.5 --dry-run
```

The hard maximum is 10 codes. Inputs above the effective maximum are rejected
with `MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT` and are never silently truncated.
A non-dry CLI invocation without an injected test client returns
`REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B`.

Mock-driven processed files and cache entries exist only as test/runtime
artifacts beneath ignored `processed/`, `manifests/`, and `cache/`
directories. The generated dataset remains `DAILY_PROXY`, with unavailable
historical theme, capital, and intraday-tail fields disclosed rather than
filled from live data.

## Fake-Real Validation Gate

Phase 3.2b-2c adds `--enable-real-astock-request` validation only. It permits
tests to inject a fake-real dated-history client, but it contained no real
HTTP or TCP implementation in that gate phase.

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --codes 600519 --start 2025-01-01 --end 2025-01-10 --out-dir overnight_quant/backtest_data/processed --max-codes 1 --sleep 0.5 --dry-run
```

Enabled gate requests are capped at 3 codes and 31 natural days, with no
silent truncation. An unenabled invocation returns
`REAL_NETWORK_NOT_ENABLED`. Any fake-real prepared output is still
`DAILY_PROXY` only, not `strict_historical` verification.

## Minimal Real First-Run Preparation

Phase 3.2b-2d implements a narrower explicitly enabled first-run route:

- one supplied code maximum;
- ten natural days maximum;
- Baidu dated daily K-line retrieval only for historical bars;
- Eastmoney stable metadata retrieval only for `name` and `list_date`;
- raw response cache under `cache/a_stock_data/`, which remains ignored.

Requests beyond the first-run boundary fail with
`REAL_FIRST_RUN_SCOPE_TOO_LARGE`. This route does not request benchmark,
theme, fund-flow, Tencent current-quote, or live-adapter data. Unknown
historical safety fields remain missing for downstream conservative rejection.

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --codes 600519 --start 2025-01-02 --end 2025-01-10 --out-dir overnight_quant/backtest_data/processed --max-codes 1 --sleep 0.5 --overwrite
```

Prepared results can be consumed only as offline `DAILY_PROXY` research input;
they are not `strict_historical` verification or evidence of profitability.

## Real Cache Replay Audit

The retained real first-run raw cache is the replay baseline. A repeated
request for the identical one-code/ten-day scope must read both allowed
endpoint responses from `cache/a_stock_data/` and disclose:

```text
cache_enabled: true
cache_hits: 2
cache_writes: 0
cache_read_failures: 0
network_requests_made: 0
```

Malformed-cache recovery is tested only against disposable pytest cache
directories with an injected fake transport. Do not corrupt or delete the
accepted ignored real raw cache during routine replay verification. The
`cache_enabled` field is audit metadata introduced with replay validation, so
it is excluded alongside runtime counters when comparing a pre-2e cold
manifest with a replay manifest.

## Expanded Real Request Scope

Phase 3.2b-2f keeps `minimal` as the default real preparation scope
(`1` code / `10` natural days) and adds explicit `expanded` preparation
(`3` codes / `31` natural days):

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --real-request-scope expanded --codes 600519,300750,510300 --start 2025-01-02 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --max-codes 3 --sleep 0.5 --dry-run
```

The `expanded` scope does not enable a new endpoint or weaken safety-field
handling. It uses the same ignored raw cache and audit boundary; exact repeated
requests can be cache replayed, while a different date range intentionally has
a different cache key. Reports expose per-code daily-bars/metadata status,
`partial_success`, `failed_codes`, and cache/network counters. Resulting data
remains offline `DAILY_PROXY` input only, never `strict_historical` evidence.
