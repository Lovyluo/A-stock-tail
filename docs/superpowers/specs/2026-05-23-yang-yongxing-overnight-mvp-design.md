# Yang Yongxing Overnight MVP Design

## Goal

Build an independent `overnight_quant/` Python package for a manual A-share overnight trading assistant. The MVP must run end-to-end in demo mode without live market access:

```bash
python overnight_quant/scripts/run_scan.py --mode demo
```

The system only generates research signals, risk decisions, manual order tickets, and next-day sell plans. It must not place orders, click trading software, or imply guaranteed profit.

## Scope

Included in MVP:

- Demo-first data client with optional live adapters and automatic fallback to demo data.
- Eight-step overnight strategy pipeline with explicit pass/reject reasons.
- Risk gate before ticket generation.
- Manual buy ticket generation and CSV signal persistence.
- Demo/manual-order based sell plan generation.
- Focused tests for filters, scoring, and risk.
- README with usage and risk disclosures.

Not included in MVP:

- Automatic trading or brokerage integration.
- Full historical backtesting.
- Full review report generation.
- Complex dragon-tiger-board analysis.
- Complex theme NLP.
- Multi-account support.

## Package Layout

```text
overnight_quant/
+-- README.md
+-- config.yaml
+-- data/
|   +-- __init__.py
|   +-- astock_client.py
|   +-- demo_data.py
+-- strategy/
|   +-- __init__.py
|   +-- filters.py
|   +-- scoring.py
|   +-- sell_rules.py
|   +-- yang_yongxing_overnight.py
+-- risk/
|   +-- __init__.py
|   +-- risk_manager.py
+-- execution/
|   +-- __init__.py
|   +-- manual_ticket.py
|   +-- trade_recorder.py
+-- records/
+-- reports/
+-- scripts/
|   +-- run_scan.py
|   +-- run_sell_plan.py
+-- tests/
    +-- test_filters.py
    +-- test_scoring.py
    +-- test_risk.py
```

## Data Design

`AStockClient` accepts `mode`:

- `demo`: always returns deterministic demo data.
- `live`: tries live endpoints adapted from `a-stock-data`; on any fetch failure, logs the failure and falls back to demo.

MVP quote records must include:

- `code`, `name`, `price`, `change_pct`, `vol_ratio`, `turnover_pct`, `amount_wan`, `float_mcap_yi`
- risk flags: `is_st`, `is_suspended`, `is_new_stock`, `is_limit_up`, `is_bj`
- optional scoring fields: `theme_tags`, `theme_rank`, `same_theme_strong_count`, `big_order_net`, `main_net`, `tail_pullback_pct`

Demo data must cover:

- qualified strong stock
- limit-up unavailable stock
- tail-diving stock
- no-theme stock
- capital-outflow stock
- ST stock
- suspended stock
- new stock

Live adapters in MVP may be partial. They should prefer Tencent quote data for real-time quote fields and use generated neutral demo K-lines when live K-line fetch is unavailable.

## Strategy Pipeline

`YangYongxingOvernightStrategy.scan()` returns a structured result:

- market gate result
- candidate count
- rejected records with reasons
- scored candidates
- risk-approved final picks
- generated ticket paths

Eight-step MVP mapping:

1. Market gate: demo/live index mood, fail blocks all tickets.
2. Initial pool: remove ST, suspended, new, BJ, price/liquidity/range failures.
3. Price-volume score: vol ratio, turnover, amount, range position, tail pullback.
4. Trend score: MA5/MA10/MA20, recent gain, upper shadow.
5. Theme score: simple `theme_tags` and `theme_rank`.
6. Capital score: simple `big_order_net`, `main_net`, tail flow fields.
7. Weighted total score and ranking.
8. Buy-ticket and sell-plan rules, manual only.

Every filter and score should include human-readable reasons, not just booleans.

## Risk Gate

`RiskManager.evaluate_buy()` must run before manual ticket creation and reject when:

- market gate is FAIL
- score is below configured threshold
- stock is ST, suspended, new, BJ, or limit-up unavailable
- single order amount exceeds `risk.max_order_value`
- daily order count exceeds `risk.max_daily_trades`

Return format:

```python
{
    "allow": bool,
    "reason": "market gate failed",
    "risk_level": "LOW|MEDIUM|HIGH",
    "max_loss_amount": float,
    "reasons": ["market_gate_fail"]
}
```

## Outputs

`run_scan.py --mode demo` must save:

- `overnight_quant/records/signals.csv`
- one or more manual ticket markdown files under `overnight_quant/reports/`

Manual tickets include strategy name, date, code, name, suggested price, max acceptable price, amount, quantity, stop loss, next-day plan, risk level, and manual-confirmation checklist.

`run_sell_plan.py --mode demo` reads `manual_orders.csv` if present. If absent, it creates/uses a deterministic demo manual order so the sell-plan path is testable.

## Testing

Tests focus on pure logic:

- filters reject known bad demo samples and keep the qualified sample.
- scoring ranks the qualified strong stock above weak/no-theme/outflow samples.
- risk rejects market fail, limit-up, low-score, and max-order violations.

## Risks And Assumptions

- This is a research assistant, not investment advice.
- Demo data is deterministic and intentionally artificial.
- Live data source failures must not crash demo workflows.
- Missing live fields are neutral or mildly penalized, with reasons logged for later improvement.
