# Phase 5.1 Chip Volume Confidence Design

## Scope

This module adds a conservative "chip proxy + volume confidence" layer for:

- after-close watchlist review
- tail-session dry-run review
- dashboard display

It is a research and observation aid only. It does not place orders, does not call broker APIs, and does not click or control any securities trading software.

## Data Sources

Primary inputs reuse existing project data paths:

- Daily K-line bars from the existing `get_daily_kline` client method.
- Current quote fields already attached to a candidate row, such as `price`, `close_price`, `change_pct`, `vol_ratio`, `turnover_pct`, `tail_pullback_pct`, and `upper_shadow_ratio`.
- Fund-flow proxy fields already attached to a candidate row, such as `main_net`, `big_order_net`, `fund_flow_source`, and optional fund-flow rows returned by Eastmoney minute or daily fund-flow adapters.

No new broker source is introduced. No order execution source is introduced.

## Chip Proxy Definition

The project does not know the real holder cost distribution. The module therefore builds only a proxy:

- `chip_avg_cost_20d`: 20-day volume-weighted typical price.
- `chip_avg_cost_60d`: 60-day volume-weighted typical price.
- `build_price_volume_profile`: buckets recent typical prices by a configurable percentage step and sums volume per bucket.
- `overhead_pressure_ratio`: share of profile volume above current price.
- `downside_support_ratio`: share of profile volume below or equal to current price.

These values are not true chip-cost data and must not be described as actual holder cost.

## Volume Confirmation Rules

Default rules:

- `prev_day_high_volume`: T-1 volume is higher than the prior `high_volume_prev_days` sessions.
- `today_volume_confirm`: T volume is at least `volume_confirm_ratio` times the average of the prior three sessions.
- `volume_expansion_ratio_3d`: T volume divided by the prior three-session average.

The signal is downgraded to `volume_data_missing` when there are not enough bars or valid volume fields.

## Peak Classification

`peak_type` can be one of:

- `accumulation`: price is near the proxy cost area, prior or current volume is active, main-force proxy is not negative, and overhead pressure is not dominant.
- `washout`: T-1 high volume is followed by weaker confirmation, price remains near the proxy cost area, and the structure still has support.
- `markup`: current volume confirms, price is above the 20-day and 60-day proxy costs, main-force proxy is positive, and overhead pressure is moderate.
- `distribution`: volume expands while price is extended or intraday/tail structure weakens, especially with negative main-force proxy or high overhead pressure.
- `neutral`: no reliable peak pattern.

The labels are descriptive pattern names only. They are not a prediction.

## Confidence Delta

The module outputs `confidence_delta`, clamped by config:

- default maximum bonus: `+8`
- default maximum penalty: `-10`

The delta is intentionally small. It can slightly adjust observation confidence but must not bypass session gates, risk gates, safety gates, or formal ticket rules.

## Missing Data Downgrade

If daily bars or usable volume are missing, the module returns:

- `peak_type: neutral`
- `volume_signal: volume_data_missing`
- `confidence_delta: 0`
- `reasons` containing `chip_volume_data_missing`

If fund-flow rows are missing but candidate-level `main_net` or `big_order_net` exists, the module uses those fields as a degraded main-force proxy and records the degraded reason.

## Risk Boundary

This module is not investment advice, does not guarantee returns, and does not represent real position cost. It only provides explainable proxy indicators for manual observation and review.
