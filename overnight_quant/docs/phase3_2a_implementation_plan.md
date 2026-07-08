# Phase 3.2a Daily Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline local-CSV `daily_proxy` backtest route that discloses approximations and conservatively rejects candidates with unknown safety status.

**Architecture:** A new `LocalCsvHistoricalDataProvider` supplies date-bounded local bars and optional snapshots. `DailyProxyPolicy` transforms those rows into safe candidate and market views while retaining missing/proxy metadata. The existing engine, exit simulator, cost model, and metrics are reused, with reports extended for fidelity-specific quality output.

**Tech Stack:** Python standard library (`csv`, `datetime`, `pathlib`), optional pandas parquet reader, existing `overnight_quant` strategy modules, `pytest`.

---

### Task 1: Express The Daily Proxy Contract In Failing Tests

**Files:**
- Create: `overnight_quant/tests/test_phase32_daily_proxy.py`

- [ ] Add a temporary local-dataset helper that writes `daily_bars.csv`, optional snapshots, benchmark bars, and `dataset_manifest.yaml` below `tmp_path`.
- [ ] Write tests importing the desired APIs:

```python
from overnight_quant.backtest.historical_data import LocalCsvHistoricalDataProvider
from overnight_quant.scripts.run_backtest import run_backtest

def test_local_csv_provider_loads_deterministic_data(tmp_path):
    data_dir = write_complete_dataset(tmp_path)
    provider = LocalCsvHistoricalDataProvider(data_dir)
    assert provider.trading_dates() == ["2026-04-01", "2026-04-02"]
    assert provider.candidates_asof("2026-04-01")[0]["code"] == "300201"

def test_missing_data_dir_returns_clear_error(tmp_path):
    result = run_backtest(dataset="local", fidelity="daily_proxy", data_dir=str(tmp_path / "missing"))
    assert result["error"] == "BACKTEST_DATA_DIR_NOT_FOUND"

def test_missing_manifest_returns_clear_error(tmp_path):
    data_dir = write_complete_dataset(tmp_path, include_manifest=False)
    result = run_backtest(dataset="local", fidelity="daily_proxy", data_dir=str(data_dir))
    assert result["error"] == "DATASET_MANIFEST_REQUIRED"
```

- [ ] Add tests for safety-field rejection, optional-field disclosures, benchmark market proxy, market-data-unavailable cash days, `daily_close_proxy`, report warnings, output isolation, no-future-field stripping, and prohibited automatic-trading code.
- [ ] Run:

```text
python -m pytest overnight_quant/tests/test_phase32_daily_proxy.py -q
```

Expected: collection fails because `LocalCsvHistoricalDataProvider` and `DailyProxyPolicy` do not yet exist.

### Task 2: Add The Offline Local Data Provider

**Files:**
- Modify: `overnight_quant/backtest/historical_data.py`
- Test: `overnight_quant/tests/test_phase32_daily_proxy.py`

- [ ] Implement explicit provider errors:

```python
class HistoricalDataError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail
```

- [ ] Implement `LocalCsvHistoricalDataProvider` with these public methods:

```python
def trading_dates(self) -> list[str]: ...
def market_snapshot_asof(self, trade_date: str, as_of: str = "daily_close_proxy") -> dict: ...
def candidates_asof(self, trade_date: str, as_of: str = "daily_close_proxy") -> list[dict]: ...
def bars_until(self, code: str, trade_date: str) -> list[dict]: ...
def exit_bar(self, code: str, trade_date: str) -> dict | None: ...
def benchmark_bars(self) -> list[dict]: ...
def quality_manifest(self) -> dict: ...
def quality_summary(self) -> dict: ...
```

- [ ] Validate `data_dir`, `daily_bars.csv`/`.parquet`, and `dataset_manifest.yaml`; strip `next_day_*` fields; reject duplicate `(trade_date, code)` bar keys.
- [ ] For `.parquet`, attempt an optional pandas reader and translate missing reader dependency to `PARQUET_ENGINE_UNAVAILABLE`.
- [ ] Run:

```text
python -m pytest overnight_quant/tests/test_phase32_daily_proxy.py -q
```

Expected: provider-loading and clear-error tests pass; policy/engine/report tests remain failing.

### Task 3: Map Proxy Data Safely With `DailyProxyPolicy`

**Files:**
- Create: `overnight_quant/backtest/fidelity_policy.py`
- Modify: `overnight_quant/backtest/historical_data.py`
- Test: `overnight_quant/tests/test_phase32_daily_proxy.py`

- [ ] Implement candidate mapping:

```python
class DailyProxyPolicy:
    selection_as_of = "daily_close_proxy"

    def candidate_view(self, daily_bar: dict, selection_row: dict | None, trade_date: str) -> dict:
        ...

    def market_view(self, trade_date: str, market_row: dict | None, benchmark_bar: dict | None) -> dict:
        ...
```

- [ ] Map `close` to `price`, convert `amount` yuan to `amount_wan`, derive listing-age risk from `list_date`, and map `is_bj_stock` to `is_bj`.
- [ ] Populate `_risk_unknown_reasons` using exactly:

```python
{
    "limit_up": "limit_price_unknown",
    "limit_down": "limit_price_unknown",
    "is_st": "st_status_unknown",
    "is_suspended": "suspended_status_unknown",
    "list_date": "list_date_missing",
    "is_bj_stock": "bj_status_unknown",
}
```

- [ ] Populate unavailable disclosures exactly:

```text
theme_unavailable
capital_unavailable
vol_ratio_unavailable
range_position_unavailable
tail_pullback_unavailable
```

- [ ] Implement market behavior: explicit market snapshot override first; otherwise positive benchmark direction as a disclosed `market_proxy_used` gate; otherwise reject the day with `market_data_unavailable`.
- [ ] Run the focused tests and retain no live/network import in provider or policy.

### Task 4: Extend The Existing Engine And Reports Without Changing `sample_exact`

**Files:**
- Modify: `overnight_quant/backtest/backtest_engine.py`
- Modify: `overnight_quant/backtest/backtest_report.py`
- Test: `overnight_quant/tests/test_phase31_backtest.py`
- Test: `overnight_quant/tests/test_phase32_daily_proxy.py`

- [ ] Permit a provider to supply a gate override and a fidelity-specific `selection_as_of`; preserve the existing sample provider path unchanged.
- [ ] Append `proxy_fields` and `market_proxy_used` to trade output, and include dataset quality summaries in the result.
- [ ] Write `rejections.csv` and `field_coverage.csv` for all runs while retaining the Phase 3.1 seven-file output contract.
- [ ] For `daily_proxy`, emit this required summary warning:

```text
Report Fidelity: DAILY_PROXY
本报告不等同于原策略完整历史验证。
题材、资金、尾盘字段缺失可能显著影响结果。
结果仅用于研究参考。
strict_historical 尚未实现。
```

- [ ] Extend `data_quality.md` with fidelity, field coverage, proxy/unavailable fields, safety rejection count, market proxy usage, and no-live-backfill declaration.
- [ ] Run:

```text
python -m pytest overnight_quant/tests/test_phase31_backtest.py overnight_quant/tests/test_phase32_daily_proxy.py -q
```

Expected: both Phase 3.1 and Phase 3.2 tests pass.

### Task 5: Route The CLI And Create Ignored Data Directories

**Files:**
- Modify: `overnight_quant/scripts/run_backtest.py`
- Create: `overnight_quant/backtest_data/README.md`
- Create: `overnight_quant/backtest_data/.gitignore`
- Create: `overnight_quant/backtest_data/raw/.gitignore`
- Create: `overnight_quant/backtest_data/processed/.gitignore`
- Create: `overnight_quant/backtest_data/manifests/.gitignore`
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [ ] Accept `--data-dir` and route only supported pairs:

```python
if (dataset, fidelity) == ("sample", "sample_exact"):
    provider = SampleHistoricalDataProvider(sample_dir)
elif (dataset, fidelity) == ("local", "daily_proxy"):
    provider = LocalCsvHistoricalDataProvider(data_dir)
else:
    return {"error": "NOT_IMPLEMENTED_IN_PHASE_3_2A"}
```

- [ ] Catch `HistoricalDataError` and print stable CLI error codes without tracebacks for expected input failures.
- [ ] Commit no historical rows under `backtest_data/`; document where users place processed CSV data and that backtest runs never access live APIs.
- [ ] Document `daily_proxy` limitations and command use without changing demo/live trading-state routing.

### Task 6: Verification And Scoped Commit

**Files:**
- Modify: `overnight_quant/docs/phase3_2a_implementation_plan.md`

- [ ] Run:

```text
python -m pytest overnight_quant/tests -q
python overnight_quant/scripts/run_backtest.py --dataset sample --fidelity sample_exact
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/examples/historical
```

- [ ] Verify:

```text
git diff --stat
git status --short --ignored
```

Expected: backtest runtime outputs are ignored; only intended `overnight_quant/` source, docs, tests, and ignore/readme files are stageable; existing unrelated untracked files remain unstaged.

- [ ] Search production code for prohibited automation dependencies:

```text
rg -n -i "pyautogui|selenium|broker api|auto_order|place_order" overnight_quant --glob "*.py" --glob "!**/tests/**"
```

Expected: no production match.

- [ ] Stage exact Phase 3.2a files only and commit:

```text
git commit -m "Add local daily proxy backtest support"
```
