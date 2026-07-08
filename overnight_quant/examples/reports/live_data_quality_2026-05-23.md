# Live Data Quality Report

Mode: live
Date: 2026-05-23
trade_date: 2026-05-23
run_time: 2026-05-23T10:21:56.002536+08:00
session_state: NON_TRADING_DAY
is_trade_day: NO
Fallback to demo: NO

## Source Status

- tencent_indices: OK, rows=3, error=
- ths_hsgt: OK, rows=262, error=
- ths_hot_reason: OK, rows=76, error=
- tencent_quote: OK, rows=30, error=
- eastmoney_fund_flow_minute: FAIL, rows=0, error=000670: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=000670: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=603459: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=603459: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=000826: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=000826: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=603663: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=603663: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=000595: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=000595: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=688512: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=688512: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=603186: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=603186: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=603806: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=603806: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=000532: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=000532: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=000725: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=000725: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=605500: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=605500: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=000997: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=000997: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=000630: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=000630: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=600497: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=600497: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=600563: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=600563: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=000711: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=000711: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=000012: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=000012: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=600601: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=600601: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=600191: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=600191: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=601208: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=601208: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=603223: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=603223: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=600160: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=600160: Remote end closed connection without response
- eastmoney_fund_flow_minute: FAIL, rows=0, error=600416: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: FAIL, rows=0, error=600416: Remote end closed connection without response
- eastmoney_fund_flow_minute: OK, rows=100, error=
- eastmoney_fund_flow_minute: OK, rows=100, error=
- eastmoney_fund_flow_minute: OK, rows=100, error=
- eastmoney_fund_flow_minute: OK, rows=100, error=
- eastmoney_fund_flow_minute: OK, rows=100, error=
- eastmoney_fund_flow_minute: OK, rows=100, error=
- eastmoney_fund_flow_minute: OK, rows=100, error=

## Candidate Counts

- raw: 76
- normalized: 30
- dropped: 0
- scored: 30

## Freshness

freshness_summary:
- fresh: 0
- stale: 30
- unknown: 1

stale_sources:
- ths_hot_reason: data_date=2026-05-23, data_time=, stale=False, reason=non_trading_day_cache_possible
- tencent_quote: data_date=2026-05-22, data_time=16:14:54, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:18, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:21, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:15:00, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:51, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:34, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:17, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:07, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:15:00, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:51, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:05, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:42, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:33, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:20, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:25, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:15, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:03, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:11, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:29, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:12, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:16, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:15, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:00, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:28, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:45, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:22, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:28, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:27, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:24, stale=True, reason=quote_stale
- tencent_quote: data_date=2026-05-22, data_time=16:14:43, stale=True, reason=quote_stale

## Field Coverage Improvement

- list_date coverage: 30/30 (100.0%)
- is_new_stock coverage: 30/30 (100.0%)
- main_net coverage: 30/30 (100.0%)
- big_order_net coverage: 30/30 (100.0%)
- safety field unknown count: 0
- candidate rejected by safety unknown count: 0
- fund_flow_source: {'estimated_from_big_order_net': 23, 'eastmoney_fund_flow_minute': 7}
- estimated_capital_flow_count: 23
- fund_flow_error_count: 23
- top_missing_fields: {}
- source_error_summary: {'eastmoney_fund_flow_minute': 23, 'eastmoney_fund_flow_daily': 23}

## Field Coverage

| Field | Present | Missing | Coverage |
|---|---:|---:|---:|
| code | 30 | 0 | 100.0% |
| name | 30 | 0 | 100.0% |
| price | 30 | 0 | 100.0% |
| change_pct | 30 | 0 | 100.0% |
| vol_ratio | 30 | 0 | 100.0% |
| turnover_pct | 30 | 0 | 100.0% |
| amount_wan | 30 | 0 | 100.0% |
| float_mcap_yi | 30 | 0 | 100.0% |
| limit_up | 30 | 0 | 100.0% |
| limit_down | 30 | 0 | 100.0% |
| is_limit_up | 30 | 0 | 100.0% |
| is_st | 30 | 0 | 100.0% |
| is_suspended | 30 | 0 | 100.0% |
| list_date | 30 | 0 | 100.0% |
| is_new_stock | 30 | 0 | 100.0% |
| is_bj_stock | 30 | 0 | 100.0% |
| theme_tags | 30 | 0 | 100.0% |
| big_order_net | 30 | 0 | 100.0% |
| main_net | 30 | 0 | 100.0% |

## Missing Fields By Stock

- None.

## Warnings

- non_trading_day
- eastmoney_fund_flow_minute: 000670: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 000670: Remote end closed connection without response
- eastmoney_fund_flow_minute: 603459: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 603459: Remote end closed connection without response
- eastmoney_fund_flow_minute: 000826: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 000826: Remote end closed connection without response
- eastmoney_fund_flow_minute: 603663: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 603663: Remote end closed connection without response
- eastmoney_fund_flow_minute: 000595: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 000595: Remote end closed connection without response
- eastmoney_fund_flow_minute: 688512: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 688512: Remote end closed connection without response
- eastmoney_fund_flow_minute: 603186: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 603186: Remote end closed connection without response
- eastmoney_fund_flow_minute: 603806: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 603806: Remote end closed connection without response
- eastmoney_fund_flow_minute: 000532: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 000532: Remote end closed connection without response
- eastmoney_fund_flow_minute: 000725: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 000725: Remote end closed connection without response
- eastmoney_fund_flow_minute: 605500: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 605500: Remote end closed connection without response
- eastmoney_fund_flow_minute: 000997: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 000997: Remote end closed connection without response
- eastmoney_fund_flow_minute: 000630: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 000630: Remote end closed connection without response
- eastmoney_fund_flow_minute: 600497: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 600497: Remote end closed connection without response
- eastmoney_fund_flow_minute: 600563: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 600563: Remote end closed connection without response
- eastmoney_fund_flow_minute: 000711: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 000711: Remote end closed connection without response
- eastmoney_fund_flow_minute: 000012: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 000012: Remote end closed connection without response
- eastmoney_fund_flow_minute: 600601: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 600601: Remote end closed connection without response
- eastmoney_fund_flow_minute: 600191: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 600191: Remote end closed connection without response
- eastmoney_fund_flow_minute: 601208: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 601208: Remote end closed connection without response
- eastmoney_fund_flow_minute: 603223: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 603223: Remote end closed connection without response
- eastmoney_fund_flow_minute: 600160: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 600160: Remote end closed connection without response
- eastmoney_fund_flow_minute: 600416: HTTP Error 502: Bad Gateway
- eastmoney_fund_flow_daily: 600416: Remote end closed connection without response
