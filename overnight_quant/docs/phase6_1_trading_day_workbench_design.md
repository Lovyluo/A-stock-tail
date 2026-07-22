# Phase 6.1 Trading Day Workbench Design

## Product workflow

The v0.3 workflow is: project health check -> pre-market news briefing -> call-auction observation -> intraday VWAP attack/defence observation -> existing tail scan -> after-close observation.

The project health check is a maintenance function. It verifies configuration, writable runtime directories, demo workflows, report parsing, and optional external data-source connectivity. It is not a market-timing gate and is hidden from the normal action area.

## Time windows

| Function | Live window | Demo behavior |
|---|---|---|
| News briefing | Previous likely trading day 15:00 to current day 09:25 | Any time |
| Call-auction observation | Trading day 09:25-09:30 | Any time |
| Intraday workbench | Existing intraday observation windows | Any time |
| Tail scan | Existing trading-day 14:25-14:55 window | Any time for demo/dry-run |
| After-close observation | Trading day from 14:50, plus after-close replay | Any time |

The tail scan keeps its original strategy and 14:25-14:55 session gate. The after-close observation uses its own scoring and watchlist workflow; only its start time is moved to 14:50. After 15:00 it remains available as an after-close review/recovery run. Before 14:50, a live after-close run returns `NOT_AFTER_CLOSE`. The weekday calendar remains a documented proxy and does not claim exchange-holiday precision.

## Data-source priority

1. Tencent quote for stock/index auction and current price fields.
2. mootdx quote as a quote fallback.
3. Eastmoney push2 for fund-flow and industry direction proxies.
4. CLS telegraph and Eastmoney global news for market news.
5. Eastmoney stock news and CNInfo announcements for candidate-specific context.
6. Existing local records and reports for holdings, prior tail picks, watchlists, and auction results.

## Candidate flow

Candidates are merged by normalized six-digit code. Source buckets are retained in `source_buckets`; an open holding receives priority and keeps its cost/stop context. The universe includes holdings, prior tail signals, the latest watchlist, and the latest auction observation.

## Degradation

Every external source is optional. A failed source is recorded with its error and contributes no positive evidence. Missing auction quotes return `AUCTION_DATA_UNAVAILABLE`; missing per-stock context produces `avoid` or `observe`, never an aggressive conclusion. News reports list unavailable sources. Intraday VWAP continues to use the existing quote proxy only as watch-level evidence.

## Dashboard policy

The normal workspace contains Today, News, Auction, Intraday, Tail Strategy, After-Close Watchlist, Positions/Sell Plan, and Audit/Maintenance sections. Formal tail scan and after-close observation are separate actions. Live dry-run and project health check are excluded from the normal action selector. They may appear only inside Audit/Maintenance when developer actions are enabled by configuration.

## Safety boundary

All output is research observation, attack/defence planning, and risk context. It is not investment advice and does not guarantee returns. The workbench does not submit orders, call brokerage trading interfaces, control brokerage software, or bypass session/risk gates.
