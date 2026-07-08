# Yang Yongxing Overnight MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a demo-first, independent `overnight_quant/` package that scans demo A-share candidates, scores them, applies risk gates, writes `signals.csv`, generates manual buy tickets, and produces a next-day sell plan.

**Architecture:** The package is split into data, strategy, risk, execution, scripts, records, reports, and tests. Demo data is deterministic and live mode is best-effort; live failures are logged and fall back to demo without interrupting the workflow. Pure scoring/filter/risk functions are tested directly, while scripts compose those functions into the MVP command-line flows.

**Tech Stack:** Python 3.10+, standard library, `requests`, `pandas`, `pytest`, optional `PyYAML`.

---

## File Map

- Create `overnight_quant/config.yaml`: strategy thresholds, risk limits, output paths.
- Create `overnight_quant/data/demo_data.py`: deterministic market, quote, k-line, and manual-order fixtures.
- Create `overnight_quant/data/astock_client.py`: demo/live client with fallback logging.
- Create `overnight_quant/strategy/filters.py`: market and candidate gates with reasons.
- Create `overnight_quant/strategy/scoring.py`: price-volume, trend, theme, capital, risk, and weighted total scoring.
- Create `overnight_quant/strategy/sell_rules.py`: next-day sell action decision logic.
- Create `overnight_quant/strategy/yang_yongxing_overnight.py`: end-to-end scan orchestration.
- Create `overnight_quant/risk/risk_manager.py`: pre-ticket buy risk gate.
- Create `overnight_quant/execution/manual_ticket.py`: ticket markdown and CSV persistence.
- Create `overnight_quant/execution/trade_recorder.py`: manual-order read/write helpers for sell-plan demo.
- Create `overnight_quant/scripts/run_scan.py`: CLI for demo/live scan.
- Create `overnight_quant/scripts/run_sell_plan.py`: CLI for demo/live sell plan.
- Create `overnight_quant/tests/test_filters.py`, `test_scoring.py`, `test_risk.py`.
- Create `overnight_quant/README.md`: run instructions and risk disclosure.

## Task 1: Package Skeleton And Config

**Files:**
- Create: `overnight_quant/__init__.py`
- Create: `overnight_quant/config.yaml`
- Create package `__init__.py` files under data, strategy, risk, execution.
- Create directories: `overnight_quant/records`, `overnight_quant/reports`, `overnight_quant/scripts`, `overnight_quant/tests`.

- [ ] **Step 1: Create package directories and config**

Use `New-Item -ItemType Directory -Force` for directories. Write `config.yaml` with:

```yaml
strategy:
  name: yang_yongxing_overnight_v1
  mode: conservative
  min_total_score: 75
  final_pick_count: 1
scan:
  default_mode: demo
  max_candidates: 30
filters:
  min_change_pct: 3
  max_change_pct: 7
  min_vol_ratio: 1
  min_turnover_pct: 5
  max_turnover_pct: 18
  min_amount_wan: 15000
  min_float_mcap_yi: 30
  max_float_mcap_yi: 250
  max_tail_pullback_pct: 2
trend:
  max_5d_gain_pct: 30
  max_10d_gain_pct: 45
  max_upper_shadow_ratio: 0.45
risk:
  max_position_ratio_per_stock: 0.2
  max_order_value: 5000
  max_daily_trades: 2
  hard_stop_loss_pct: -3
  disaster_stop_loss_pct: -5
  no_trade_if_market_fail: true
sell:
  take_profit_pct_1: 3
  take_profit_pct_2: 6
  stop_loss_pct: -3
  force_exit_before: "10:30"
cost:
  slippage_pct: 0.1
paths:
  records_dir: overnight_quant/records
  reports_dir: overnight_quant/reports
```

- [ ] **Step 2: Verify skeleton imports**

Run: `python -c "import overnight_quant; print('ok')"`
Expected: prints `ok`.

## Task 2: Demo Data And Client Fallback

**Files:**
- Create: `overnight_quant/data/demo_data.py`
- Create: `overnight_quant/data/astock_client.py`

- [ ] **Step 1: Add deterministic demo data**

Define functions:

```python
def demo_market_snapshot() -> dict:
    pass

def demo_quotes() -> list[dict]:
    pass

def demo_daily_kline(code: str, lookback: int = 120) -> list[dict]:
    pass

def demo_manual_order() -> dict:
    pass
```

Samples must include codes `300001`, `002002`, `600003`, `300004`, `002005`, `600006`, `300007`, `301008` covering qualified, limit-up, tail-dive, no-theme, outflow, ST, suspended, and new-stock cases.

- [ ] **Step 2: Add `AStockClient`**

Implement:

```python
class AStockClient:
    def __init__(self, mode: str = "demo", logger: logging.Logger | None = None):
        pass

    def get_market_snapshot(self) -> dict:
        pass

    def get_candidate_quotes(self) -> list[dict]:
        pass

    def get_daily_kline(self, code: str, lookback: int = 120) -> list[dict]:
        pass

    def get_current_price(self, code: str) -> dict:
        pass
```

In `live` mode, try Tencent quotes for demo quote codes. On exception or empty data, log `live data failed, fallback to demo` and use demo data.

- [ ] **Step 3: Smoke-check demo client**

Run: `python -c "from overnight_quant.data.astock_client import AStockClient; c=AStockClient('demo'); print(len(c.get_candidate_quotes()))"`
Expected: prints at least `8`.

## Task 3: Filters And Scoring

**Files:**
- Create: `overnight_quant/strategy/filters.py`
- Create: `overnight_quant/strategy/scoring.py`
- Test: `overnight_quant/tests/test_filters.py`
- Test: `overnight_quant/tests/test_scoring.py`

- [ ] **Step 1: Write filter tests**

Test that the qualified demo stock passes initial filters and ST, suspended, new, limit-up, tail-dive, no-theme, and outflow samples produce explicit reasons.

- [ ] **Step 2: Implement filters**

Expose:

```python
def evaluate_market_gate(market: dict) -> dict:
    pass

def initial_filter(stock: dict, config: dict) -> dict:
    pass

def evaluate_tail_stability(stock: dict, config: dict) -> dict:
    pass
```

Return dictionaries with `pass`, `reasons`, and `reject_reasons`.

- [ ] **Step 3: Write scoring tests**

Assert qualified stock total score is >= 75 and ranks above no-theme/outflow/tail-dive samples.

- [ ] **Step 4: Implement scoring**

Expose:

```python
def score_stock(stock: dict, kline: list[dict], market_score: float, config: dict) -> dict:
    pass

def rank_scored(scored: list[dict]) -> list[dict]:
    pass
```

Return component scores, `total_score`, `score_reasons`, `risk_flags`, and `decision`.

- [ ] **Step 5: Run tests**

Run: `python -m pytest overnight_quant/tests/test_filters.py overnight_quant/tests/test_scoring.py -q`
Expected: all tests pass.

## Task 4: Risk Gate And Manual Ticket

**Files:**
- Create: `overnight_quant/risk/risk_manager.py`
- Create: `overnight_quant/execution/manual_ticket.py`
- Test: `overnight_quant/tests/test_risk.py`

- [ ] **Step 1: Write risk tests**

Cover market fail, low score, limit-up, max-order violation, and valid qualified stock.

- [ ] **Step 2: Implement `RiskManager`**

Expose:

```python
class RiskManager:
    def __init__(self, config: dict):
        pass

    def evaluate_buy(self, stock: dict, market_gate: dict, planned_amount: float, daily_trade_count: int = 0) -> dict:
        pass
```

Return `allow`, `reason`, `reasons`, `risk_level`, and `max_loss_amount`.

- [ ] **Step 3: Implement manual ticket helpers**

Expose:

```python
def build_manual_ticket(stock: dict, risk: dict, config: dict, trade_date: str) -> dict:
    pass

def save_manual_ticket(ticket: dict, reports_dir: str) -> str:
    pass

def append_signal_csv(rows: list[dict], records_dir: str) -> str:
    pass
```

- [ ] **Step 4: Run risk tests**

Run: `python -m pytest overnight_quant/tests/test_risk.py -q`
Expected: all tests pass.

## Task 5: Scan CLI

**Files:**
- Create: `overnight_quant/strategy/yang_yongxing_overnight.py`
- Create: `overnight_quant/scripts/run_scan.py`

- [ ] **Step 1: Implement strategy orchestration**

`YangYongxingOvernightStrategy.scan()` composes client, filters, scoring, risk, ticket generation, and CSV persistence. It returns `market_gate`, `candidates`, `rejected`, `selected`, `tickets`, `signals_csv`, and `fallback_messages`.

- [ ] **Step 2: Implement `run_scan.py`**

Parse `--mode demo|live`, load config, instantiate client and strategy, run scan, print a human-readable summary and ticket paths.

- [ ] **Step 3: Verify demo scan**

Run: `python overnight_quant/scripts/run_scan.py --mode demo`
Expected: exits 0, writes `overnight_quant/records/signals.csv`, and writes at least one manual ticket markdown under `overnight_quant/reports/`.

## Task 6: Sell Plan CLI

**Files:**
- Create: `overnight_quant/strategy/sell_rules.py`
- Create: `overnight_quant/execution/trade_recorder.py`
- Create: `overnight_quant/scripts/run_sell_plan.py`

- [ ] **Step 1: Implement sell rules**

Expose:

```python
def decide_sell_action(order: dict, current: dict, config: dict) -> dict:
    pass
```

Actions: `SELL_NOW`, `WAIT_10_MIN`, `TAKE_PROFIT`, `STOP_LOSS`, `LIMIT_UP_WATCH`, `LIMIT_DOWN_RISK`.

- [ ] **Step 2: Implement manual order read/write**

If `manual_orders.csv` is absent in demo mode, write a deterministic demo order from `demo_manual_order()`.

- [ ] **Step 3: Implement `run_sell_plan.py`**

Load manual orders, get current/demo price, decide actions, save `sell_plan_YYYY-MM-DD.md`.

- [ ] **Step 4: Verify sell plan**

Run: `python overnight_quant/scripts/run_sell_plan.py --mode demo`
Expected: exits 0 and writes a sell plan markdown file.

## Task 7: README And Full Verification

**Files:**
- Create: `overnight_quant/README.md`

- [ ] **Step 1: Write README**

Document install, demo scan, live fallback, manual ticket, sell plan, tests, and risk disclosure.

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest overnight_quant/tests -q`
Expected: all tests pass.

- [ ] **Step 3: Run end-to-end demo commands**

Run:

```bash
python overnight_quant/scripts/run_scan.py --mode demo
python overnight_quant/scripts/run_sell_plan.py --mode demo
```

Expected: both exit 0 and produce records/reports artifacts.

## Self-Review

- Spec coverage: The tasks cover independent package creation, demo-first data, live fallback, filters, scoring, risk gates, ticket persistence, sell plans, tests, and README. Excluded features remain out of scope.
- Placeholder scan: No incomplete task depends on undefined future work; extension points are represented by stable function signatures.
- Type consistency: The same dictionary-based quote, gate, score, risk, ticket, and order shapes are used across tasks.
