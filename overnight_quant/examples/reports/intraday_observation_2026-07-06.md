# Intraday Observation Report

trade_date: 2026-07-06
run_time: 2026-07-06T10:05:00+08:00
mode: demo
status: DEMO_INTRADAY_OBSERVATION
session_state: MORNING_SESSION
intraday_window: PRIMARY_BUY
candidate_source: demo_synthetic_watchlist
valid_for_trading_observation: DEMO_ONLY
market_gate: PASS
market_reasons: index_not_weak, northbound_not_extreme_outflow, limit_down_count_controlled
market_reject_reasons: 
signal_count: 2
buy_point_a_count: 1
buy_point_b_count: 0
buy_watch_count: 1

## Signals

| code | name | signal | score | price | vwap | vwap_gap_pct | buy_zone | reasons | invalid_conditions |
|---|---|---|---:|---:|---:|---:|---|---|---|
| 300001 | Demo Robotics | BUY_POINT_A | 113.8 | 19.02 | 18.9 | 0.63 | 18.94-19.13 | market_gate_pass/buy_window:PRIMARY_BUY/after_close_category_a/price_above_vwap/not_far_above_vwap/vwap_pullback_reclaim/intraday_lows_lifting/rebound_volume_expansion/range_position_ok |  |
| 002005 | Demo Outflow | BUY_WATCH | 80.4 | 15.03 | 15.0 | 0.2 | 15.03-15.18 | market_gate_pass/buy_window:PRIMARY_BUY/after_close_category_b/price_above_vwap/not_far_above_vwap/intraday_lows_lifting | vwap_reclaim_not_confirmed/range_position_weak |

## Data Quality

- source_status: none recorded
- warnings: none

Risk warning: Observation only; no automated orders; not investment advice.
