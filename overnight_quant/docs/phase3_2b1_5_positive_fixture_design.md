# Phase 3.2b-1.5 Positive Prepared Fixture Design

## Purpose

Phase 3.2b-1.5 adds a deterministic positive sample profile so the offline
preparation pipeline can be verified through one completed `daily_proxy`
trade. It complements the existing neutral sample, which remains useful for
the no-trade and unavailable-enhancement-field path.

This is a pipeline validation fixture. It is not live data, not a strict
historical dataset, and not evidence that the strategy is profitable.

## Scope

The preparation command accepts:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source sample \
  --sample-profile positive \
  --codes 300201 \
  --start 2025-01-01 \
  --end 2025-01-31 \
  --out-dir overnight_quant/backtest_data/processed \
  --overwrite
```

Profiles:

| Profile | Purpose | Enhanced Historical Sample Fields |
| --- | --- | --- |
| `neutral` | Existing load/no-trade and missing-field behavior | Theme and capital remain unavailable |
| `positive` | Prepared-data trade lifecycle validation | Deterministic `sample_fixture` theme and capital values on eligible sample dates |

`--sample-profile` is ignored for `--source local-raw`; local raw inputs
continue to control their own historical values without any sample injection.

## Data Boundary

The positive fixture lives in:

```text
overnight_quant/examples/historical_prepare_positive_raw/
  daily_bars.csv
  selection_snapshots.csv
  benchmark_bars.csv
```

The prepared output remains:

```text
overnight_quant/backtest_data/processed/
  daily_bars.csv
  selection_snapshots.csv
  market_snapshots.csv
  benchmark_bars.csv
  dataset_manifest.yaml
```

For `positive` only, preparation copies deterministic fixture values for:

- `theme_tags`
- `theme_rank`
- `same_theme_strong_count`
- `main_net`
- `big_order_net`

It also writes:

- `theme_source: sample_fixture`
- `capital_source: sample_fixture`
- `source_quality: sample_fixture`

`tail_pullback_pct` remains empty and unavailable because no minute bars are
provided. The existing daily close, volume ratio, range position, and
benchmark-direction proxy behavior remains unchanged.

## Reporting

For a positive preparation, the manifest and preparation report include:

```text
sample_profile: positive
positive profile uses deterministic sample_fixture theme/capital fields for pipeline validation
not live-filled
not strict historical
not evidence of strategy profitability
DAILY_PROXY only
```

The `daily_proxy` data-quality and summary reports carry the same disclosure
from the processed manifest. They continue to identify the run as
`DAILY_PROXY`, with `selection_as_of: daily_close_proxy` and
`data_fidelity: daily_proxy`.

## Unchanged Behavior

This phase does not change:

- `BacktestEngine`;
- scoring or risk rules;
- fee calculation;
- minimum BUY score;
- network or live adapter behavior;
- real/example trade state locations.

Runtime prepared datasets, audit reports, and backtest outputs remain ignored.

## Verification

Tests and command-line verification prove:

1. neutral preparation retains the prior no-trade behavior;
2. positive preparation writes an auditable processed dataset;
3. a prepared positive dataset yields at least one `daily_proxy` trade;
4. trade rows contain commissions, stamp tax, PnL, return percentage,
   `daily_close_proxy`, and `daily_proxy`;
5. reports retain all research limitations and source disclosures;
6. no networking, automatic order placement, or broker-software clicking is
   introduced.
