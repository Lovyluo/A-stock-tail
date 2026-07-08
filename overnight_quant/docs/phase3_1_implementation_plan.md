# Phase 3.1 Sample Exact Backtest Implementation Plan

> Executed in the current session with test-driven development. Completed steps remain checked below for release traceability.

**Goal:** Add a deterministic offline `sample_exact` backtest that validates chronology, exits, costs, metrics, and report output without polluting trading state.

**Architecture:** Committed historical fixture files feed a point-in-time provider. A chronological engine reuses existing market/filter/scoring/risk rules for selection and a dedicated next-day exit simulator; metrics and reporting remain separate modules.

**Tech Stack:** Python standard library, existing overnight strategy modules, `pytest`, CSV and YAML fixtures.

---

### Task 1: Express The Backtest Contract In Tests

**Files:**
- Create: `overnight_quant/tests/test_phase31_backtest.py`

- [x] Write tests that import `SampleHistoricalDataProvider`, `BacktestEngine`, `simulate_exit`, `calculate_metrics`, and `run_backtest`, asserting no future fields, limit-up rejection, profit/stop/conflict/limit-down exits, costs, metrics, deterministic files, state isolation, and prohibited-code absence.
- [x] Run `python -m pytest overnight_quant/tests/test_phase31_backtest.py -q`.
- [x] Verify it fails during collection because `overnight_quant.backtest` does not yet exist.

### Task 2: Add Deterministic Point-In-Time Fixtures And Provider

**Files:**
- Create: `overnight_quant/examples/historical/daily_bars.csv`
- Create: `overnight_quant/examples/historical/selection_snapshots.csv`
- Create: `overnight_quant/examples/historical/market_snapshots.csv`
- Create: `overnight_quant/examples/historical/benchmark_bars.csv`
- Create: `overnight_quant/examples/historical/dataset_manifest.yaml`
- Create: `overnight_quant/backtest/__init__.py`
- Create: `overnight_quant/backtest/historical_data.py`

- [x] Store sample rows for take-profit, stop-loss, market-fail, limit-up rejection, both-hit conservative exit, limit-down carry, missing optional features, and benchmark comparison.
- [x] Implement `SampleHistoricalDataProvider` methods:

```python
def trading_dates(self) -> list[str]: ...
def market_snapshot_asof(self, trade_date: str, as_of: str = "14:50") -> dict: ...
def candidates_asof(self, trade_date: str, as_of: str = "14:50") -> list[dict]: ...
def bars_until(self, code: str, trade_date: str) -> list[dict]: ...
def exit_bar(self, code: str, trade_date: str) -> dict | None: ...
def benchmark_bars(self) -> list[dict]: ...
def quality_manifest(self) -> dict: ...
```

- [x] Strip `next_day_*` columns in `candidates_asof` and filter `bars_until` to the selection date.

### Task 3: Implement Chronological Engine And Exit Simulator

**Files:**
- Create: `overnight_quant/backtest/backtest_engine.py`
- Modify: `overnight_quant/config.yaml`
- Modify: `overnight_quant/strategy/yang_yongxing_overnight.py`

- [x] Add a `backtest` config section with initial capital, output directory, sample data directory, and default intraday assumption.
- [x] Implement `BacktestEngine.run()` to exit carried positions before selection, apply existing strategy gates to as-of snapshots, buy one selected candidate at close proxy, and record skipped days.
- [x] Implement:

```python
def simulate_exit(position: dict, bar: dict, config: dict, intraday_assumption: str) -> dict | None: ...
def calculate_trade_costs(buy_price: float, sell_price: float, qty: int, config: dict) -> dict: ...
```

- [x] Ensure a limit-down bar returns a carry event and that simultaneous stop/profit hits choose stop in conservative mode.

### Task 4: Compute Metrics And Write Reports

**Files:**
- Create: `overnight_quant/backtest/backtest_metrics.py`
- Create: `overnight_quant/backtest/backtest_report.py`
- Create: `overnight_quant/backtest_outputs/.gitignore`

- [x] Implement metrics for total and annualized returns, maximum drawdown, win rate, profit/loss ratio, average return and holding days, counts, losses, month/year return tables, and benchmark return.
- [x] Write the seven requested CSV/Markdown artifacts beneath a supplied run directory.
- [x] Include manifest disclosure and the exact wording that sample results validate engine/event order only and cannot prove strategy profitability.

### Task 5: Add CLI, Documentation, And Verification

**Files:**
- Create: `overnight_quant/scripts/run_backtest.py`
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [x] Implement arguments:

```text
--dataset sample
--fidelity sample_exact
--intraday-assumption conservative|optimistic|close_based
--run-id <optional deterministic output id>
```

- [x] Return `NOT_IMPLEMENTED_IN_PHASE_3_1` for unsupported dataset/fidelity combinations.
- [x] Document execution, outputs, fidelity limits, and isolation from real/example transaction state.
- [x] Run:

```text
python -m pytest overnight_quant/tests -q
python overnight_quant/scripts/run_backtest.py --dataset sample --fidelity sample_exact
python overnight_quant/scripts/run_scan.py --mode demo
python overnight_quant/scripts/run_sell_plan.py --mode live
```

- [x] Verify generated backtest artifacts remain ignored and real trading state has no unintended committed output.
